// Subscriber relay for House Hunter (Cloudflare Worker, free plan).
//
//   POST /telegram            Telegram's webhook (header X-Telegram-Bot-Api-Secret-Token). Someone with the secret
//                             invite link sends /start <code> from a private chat or a group and is added to the list.
//                             /stop and /status work from the same chat. Replies go back as the webhook response
//                             itself, so this Worker never needs the bot token.
//   GET  /subscribers         The checker on GitHub pulls the list here (bearer PULL_SECRET).
//   POST /subscribers/remove  {"chat_id": "<id>"}            a chat that blocked or removed the bot (bearer)
//   POST /subscribers/migrate {"from": "<id>", "to": "<id>"}  a group that became a supergroup (bearer)
//   POST /subscribers/reset   {"confirm": "remove-all"}       the owner removes every subscriber (bearer);
//                             used with a new invite code when a link went to someone it should not have
//
// Per chat only "chat:<id>" -> {"type", "added_at"} is stored: no names, usernames, titles or message text.
// Every reply is a fixed string. Nothing a user typed is ever sent back.

import { parseCommand } from "./commands.mjs";

const PREFIX = "chat:";
const ID_PATTERN = /^-?\d{1,20}$/;
const CHAT_TYPES = ["private", "group", "supergroup"];
const MAX_BODY = 64 * 1024;
const DEFAULT_MAX_SUBSCRIBERS = 50;

const REPLY = {
  already: "This chat already gets the rental alerts. Send /stop to end them.",
  inviteOnly: "This bot is invite-only. Ask the owner for an invite link.",
  full: "The list of subscribers is full, so this chat could not be added. Ask the owner.",
  notSubscribed: "This chat is not subscribed. (A change can take a minute to show here.)",
  stopped: "Done. This chat will no longer get the alerts.",
  subscribed: "This chat is subscribed. (A change can take a minute to show here.)",
  unavailable: "Something went wrong on my side. Try again in a minute.",
};
const welcome = (dashboardUrl) =>
  "You're in! This chat now gets the rental alerts."
  + (dashboardUrl ? `\nDashboard: ${dashboardUrl}` : "")
  + "\nSend /stop to end them.";

const empty = (status) => new Response(null, { status });
const isChatId = (value) => typeof value === "string" && ID_PATTERN.test(value);

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

// Constant time: both sides are hashed first, so neither the content nor the length leaks through timing.
// A missing (non-string or empty) expected secret never matches.
async function sameSecret(given, expected) {
  if (typeof given !== "string" || typeof expected !== "string" || !expected) return false;
  const [a, b] = await Promise.all([digest(given), digest(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

const hasBearer = (request, env) =>
  sameSecret(request.headers.get("Authorization"), env.PULL_SECRET ? `Bearer ${env.PULL_SECRET}` : "");

function reply(chatId, text) {
  return Response.json({ method: "sendMessage", chat_id: chatId, text, link_preview_options: { is_disabled: true } });
}

async function readJson(request) {
  if (Number(request.headers.get("Content-Length") ?? 0) > MAX_BODY) return { tooBig: true };
  const raw = await request.text();
  if (raw.length > MAX_BODY) return { tooBig: true };
  try {
    return { value: JSON.parse(raw) };
  } catch {
    return { bad: true };
  }
}

// ---------------------------------------------------------------------------------------------- storage
async function listIds(env) {
  const ids = [];
  let cursor;
  do {
    const page = await env.SUBSCRIBERS.list({ prefix: PREFIX, cursor });
    for (const key of page.keys) {
      const id = key.name.slice(PREFIX.length);
      if (isChatId(id)) ids.push(id);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return ids;
}

// Workers KV answers a read from a cache at the edge for a while (here 30 seconds, the shortest it allows), so
// right after a change a chat can still look subscribed or unsubscribed. That is why /start with the right code
// always writes and /stop always deletes, whatever this read says: the stored list ends up right either way.
const isSubscribed = async (env, id) => (await env.SUBSCRIBERS.get(PREFIX + id, { cacheTtl: 30 })) !== null;
const addChat = (env, id, type) =>
  env.SUBSCRIBERS.put(PREFIX + id, JSON.stringify({ type, added_at: new Date().toISOString() }));
const removeChat = (env, id) => env.SUBSCRIBERS.delete(PREFIX + id);

async function moveChat(env, from, to, type) {
  if (from === to || !(await isSubscribed(env, from))) return;
  if (!(await isSubscribed(env, to))) await addChat(env, to, type);
  await removeChat(env, from);
}

function maxSubscribers(env) {
  const n = Number(env.MAX_SUBSCRIBERS);
  return env.MAX_SUBSCRIBERS !== undefined && env.MAX_SUBSCRIBERS !== "" && Number.isInteger(n) && n >= 0
    ? n : DEFAULT_MAX_SUBSCRIBERS;
}

// ---------------------------------------------------------------------------------------------- Telegram
async function handleCommand(env, chat, command, code) {
  const id = String(chat.id);
  if (command === "start") {
    const known = await isSubscribed(env, id);
    if (!(await sameSecret(code, env.INVITE_CODE))) return reply(chat.id, known ? REPLY.already : REPLY.inviteOnly);
    if (!known && (await listIds(env)).length >= maxSubscribers(env)) return reply(chat.id, REPLY.full);
    await addChat(env, id, chat.type); // also when it looks subscribed: that read may be a minute old
    return reply(chat.id, known ? REPLY.already : welcome(env.DASHBOARD_URL));
  }
  if (command === "stop") {
    const known = await isSubscribed(env, id);
    await removeChat(env, id); // also when it looks unsubscribed: that read may be a minute old
    return reply(chat.id, known ? REPLY.stopped : REPLY.notSubscribed);
  }
  if (command === "status") {
    return reply(chat.id, (await isSubscribed(env, id)) ? REPLY.subscribed : REPLY.notSubscribed);
  }
  return empty(204);
}

async function handleUpdate(env, update) {
  const left = update.my_chat_member;
  if (left && typeof left === "object") {
    const status = left.new_chat_member?.status;
    const id = typeof left.chat?.id === "number" ? String(left.chat.id) : "";
    if ((status === "left" || status === "kicked") && isChatId(id)) await removeChat(env, id);
    return empty(204);
  }

  const message = update.message;
  const chat = message?.chat;
  if (!message || typeof chat?.id !== "number" || !CHAT_TYPES.includes(chat.type)) return empty(204);

  if (typeof message.migrate_to_chat_id === "number") {
    const to = String(message.migrate_to_chat_id);
    if (isChatId(to)) await moveChat(env, String(chat.id), to, "supergroup");
    return empty(204);
  }

  const { command, arg } = parseCommand(message.text);
  if (!command) return empty(204);
  try {
    return await handleCommand(env, chat, command, arg);
  } catch {
    return reply(chat.id, REPLY.unavailable);
  }
}

async function answerTelegram(request, env) {
  if (!(await sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.WEBHOOK_SECRET))) {
    return empty(403);
  }
  const body = await readJson(request);
  if (!body.value || typeof body.value !== "object") return empty(204); // never make Telegram retry junk
  try {
    return await handleUpdate(env, body.value);
  } catch {
    return empty(500); // a storage problem: let Telegram deliver the update again
  }
}

// ---------------------------------------------------------------------------------------------- the checker
async function pullAction(request, env, path) {
  if (!(await hasBearer(request, env))) return empty(403);
  try {
    if (request.method === "GET" && path === "/subscribers") {
      return Response.json({ chats: await listIds(env) }, { headers: { "Cache-Control": "no-store" } });
    }
    const body = await readJson(request);
    if (body.tooBig) return empty(413);
    const data = body.value;
    if (!data || typeof data !== "object") return empty(400);
    if (path === "/subscribers/remove") {
      if (!isChatId(data.chat_id)) return empty(400);
      await removeChat(env, data.chat_id);
      return empty(204);
    }
    if (path === "/subscribers/reset") {
      if (data.confirm !== "remove-all") return empty(400);
      const ids = await listIds(env);
      for (const id of ids) await removeChat(env, id);
      return Response.json({ removed: ids.length }, { headers: { "Cache-Control": "no-store" } });
    }
    if (!isChatId(data.from) || !isChatId(data.to)) return empty(400);
    await moveChat(env, data.from, data.to, "supergroup");
    return empty(204);
  } catch {
    return empty(500);
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    const post = request.method === "POST";
    if (post && path === "/telegram") return answerTelegram(request, env);
    if (request.method === "GET" && path === "/subscribers") return pullAction(request, env, path);
    if (post && ["/subscribers/remove", "/subscribers/migrate", "/subscribers/reset"].includes(path)) {
      return pullAction(request, env, path);
    }
    return empty(404);
  },
};
