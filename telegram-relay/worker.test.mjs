// node --test telegram-relay/worker.test.mjs   (no dependencies; KV is replaced by a Map)
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseCommand } from "./commands.mjs";
import worker from "./worker.mjs";

// A Map-backed KV with the parts the Worker uses: get, put, delete and list (prefix + cursor, small pages
// so that following the cursor is really exercised).
function fakeKv(pageSize = 2) {
  const map = new Map();
  return {
    map,
    stale: null,  // a Map here makes reads answer from it, like KV's edge cache right after a change
    async get(key, options) {
      if (options !== undefined) assert.ok(options.cacheTtl >= 30, "KV refuses a cacheTtl under 30 seconds");
      const from = this.stale || map;
      return from.has(key) ? from.get(key) : null;
    },
    async put(key, value) { map.set(key, value); },
    async delete(key) { map.delete(key); },
    async list({ prefix = "", cursor } = {}) {
      const names = [...map.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const slice = names.slice(start, start + pageSize);
      const end = start + pageSize;
      return { keys: slice.map((name) => ({ name })), list_complete: end >= names.length, cursor: String(end) };
    },
  };
}

function makeEnv(extra = {}) {
  const kv = fakeKv();
  return {
    kv, map: kv.map,
    WEBHOOK_SECRET: "hook", PULL_SECRET: "pull", INVITE_CODE: "Invite_1-x", DASHBOARD_URL: "https://dash.example/",
    SUBSCRIBERS: kv, ...extra,
  };
}

const call = (req, env) => worker.fetch(req, env);

function update(body, { secret = "hook" } = {}) {
  return new Request("https://relay.example/telegram", {
    method: "POST",
    headers: secret === null ? {} : { "X-Telegram-Bot-Api-Secret-Token": secret },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}
const say = (text, { chat = 100, type = "private", ...rest } = {}, opts) =>
  update({ update_id: 1, message: { message_id: 1, chat: { id: chat, type, title: "Secret Name", username: "someone" }, text, ...rest } }, opts);

const asks = (env, text, chat = {}) => call(say(text, chat), env).then(async (r) => ({ status: r.status, body: r.status === 200 ? await r.json() : null }));

const subscribed = (env, id) => env.map.has(`chat:${id}`);
const api = (path, { method = "POST", auth = "Bearer pull", body } = {}) =>
  new Request(`https://relay.example${path}`, {
    method, headers: auth === null ? {} : { Authorization: auth },
    body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });

test("parseCommand: case-insensitive command, @bot suffix, argument kept as typed", () => {
  assert.deepEqual(parseCommand("/Start@my_bot  Abc_1 more"), { command: "start", arg: "Abc_1" });
  assert.deepEqual(parseCommand("/stop"), { command: "stop", arg: "" });
  assert.deepEqual(parseCommand("hello /stop"), { command: "", arg: "" });
  assert.deepEqual(parseCommand(undefined), { command: "", arg: "" });
  assert.deepEqual(parseCommand(42), { command: "", arg: "" });
});

test("/start with the right code subscribes a private chat; the welcome has the dashboard and /stop", async () => {
  const env = makeEnv();
  const { body } = await asks(env, "/start Invite_1-x");
  assert.equal(body.method, "sendMessage");
  assert.equal(body.chat_id, 100);
  assert.equal(body.parse_mode, undefined);
  assert.match(body.text, /https:\/\/dash\.example\//);
  assert.match(body.text, /\/stop/);
  assert.ok(subscribed(env, 100));
});

test("only the chat type and the time are stored: no names, usernames, titles or text", async () => {
  const env = makeEnv();
  await asks(env, "/start Invite_1-x", { chat: -500, type: "supergroup" });
  assert.equal(env.map.size, 1);
  const stored = JSON.parse(env.map.get("chat:-500"));
  assert.deepEqual(Object.keys(stored).sort(), ["added_at", "type"]);
  assert.equal(stored.type, "supergroup");
  assert.ok(!Number.isNaN(Date.parse(stored.added_at)));
  const raw = [...env.map.values()].join("|") + [...env.map.keys()].join("|");
  assert.doesNotMatch(raw, /Secret Name|someone|Invite_1/);
});

test("groups and supergroups can subscribe; the command may be upper case and have an @bot suffix", async () => {
  const env = makeEnv();
  await asks(env, "/START@house_bot Invite_1-x", { chat: -1, type: "group" });
  await asks(env, "/start Invite_1-x", { chat: -1002, type: "supergroup" });
  assert.ok(subscribed(env, -1) && subscribed(env, -1002));
});

test("a wrong, missing or empty code gets one fixed reply and stores nothing", async () => {
  const env = makeEnv();
  const replies = [];
  for (const text of ["/start", "/start nope", "/start invite_1-x", "/start Invite_1-xx", "/start Invite_1-"]) {
    replies.push((await asks(env, text)).body.text);
  }
  assert.equal(new Set(replies).size, 1);
  assert.match(replies[0], /invite-only/);
  assert.equal(env.map.size, 0);
});

test("an unset or empty INVITE_CODE never matches", async () => {
  for (const code of [undefined, ""]) {
    const env = makeEnv({ INVITE_CODE: code });
    for (const text of ["/start", "/start undefined", "/start ", "/start x"]) {
      assert.match((await asks(env, text)).body.text, /invite-only/);
    }
    assert.equal(env.map.size, 0);
  }
});

test("already subscribed: says so, even with a wrong code, and stays one subscriber", async () => {
  const env = makeEnv();
  await asks(env, "/start Invite_1-x");
  const before = env.map.get("chat:100");
  assert.match((await asks(env, "/start wrong")).body.text, /already gets the rental alerts/);
  assert.equal(env.map.get("chat:100"), before);
  assert.match((await asks(env, "/start Invite_1-x")).body.text, /already gets the rental alerts/);
  assert.equal(JSON.parse(env.map.get("chat:100")).type, "private");
  assert.equal(env.map.size, 1);
});

test("a read that is a minute old never leaves the list wrong", async () => {
  // KV answers reads from an edge cache for a while after a change. Whatever the read says,
  // /start with the right code must end with the chat on the list and /stop with the chat off it.
  const env = makeEnv();
  env.kv.stale = new Map();                                   // the read still says: not subscribed
  await asks(env, "/start Invite_1-x");
  assert.equal(env.map.size, 1);
  assert.match((await asks(env, "/stop")).body.text, /not subscribed/);   // says the old state...
  assert.equal(env.map.size, 0);                              // ...but the chat is really removed

  env.kv.stale = new Map([["chat:100", "{}"]]);               // the read still says: subscribed
  assert.match((await asks(env, "/start Invite_1-x")).body.text, /already gets the rental alerts/);
  assert.equal(env.map.size, 1);                              // written again, so it is true
  env.map.clear();
  assert.match((await asks(env, "/start wrong")).body.text, /already/);    // no code: nothing is written
  assert.equal(env.map.size, 0);
  assert.match((await asks(env, "/status")).body.text, /can take a minute/);
});

test("a full list refuses new chats (default 50, or MAX_SUBSCRIBERS)", async () => {
  const env = makeEnv({ MAX_SUBSCRIBERS: "3" });
  for (const chat of [1, 2, 3]) await asks(env, "/start Invite_1-x", { chat });
  assert.match((await asks(env, "/start Invite_1-x", { chat: 4 })).body.text, /full/);
  assert.equal(env.map.size, 3);
  // an already subscribed chat is still told so when the list is full
  assert.match((await asks(env, "/start Invite_1-x", { chat: 2 })).body.text, /already/);

  const dflt = makeEnv();
  for (let i = 1; i <= 50; i++) dflt.map.set(`chat:${i}`, "{}");
  assert.match((await asks(dflt, "/start Invite_1-x", { chat: 999 })).body.text, /full/);
  dflt.map.delete("chat:50");
  assert.match((await asks(dflt, "/start Invite_1-x", { chat: 999 })).body.text, /You're in/);

  const junk = makeEnv({ MAX_SUBSCRIBERS: "lots" });  // not a number: back to the default
  assert.match((await asks(junk, "/start Invite_1-x")).body.text, /You're in/);
});

test("/stop removes a subscribed chat; otherwise it says the chat is not subscribed", async () => {
  const env = makeEnv();
  assert.match((await asks(env, "/stop")).body.text, /not subscribed/);
  await asks(env, "/start Invite_1-x");
  assert.match((await asks(env, "/stop@house_bot")).body.text, /no longer get/);
  assert.equal(env.map.size, 0);
});

test("/status tells whether the chat is subscribed and never creates a subscriber", async () => {
  const env = makeEnv();
  assert.match((await asks(env, "/status", { chat: 0 })).body.text, /not subscribed/);
  assert.equal(env.map.size, 0);
  await asks(env, "/start Invite_1-x");
  assert.match((await asks(env, "/status")).body.text, /^This chat is subscribed/);
});

test("other commands, plain text, channels and bare updates are ignored without a reply", async () => {
  const env = makeEnv();
  for (const text of ["/help", "/price", "/constructor", "hello", "start Invite_1-x", ""]) {
    assert.equal((await asks(env, text)).status, 204);
  }
  assert.equal((await asks(env, "/start Invite_1-x", { type: "channel" })).status, 204);
  assert.equal((await asks(env, "/start Invite_1-x", { type: "bot" })).status, 204);
  for (const body of [{ update_id: 1 }, { update_id: 1, message: {} }, { update_id: 1, message: { chat: { id: "x", type: "private" }, text: "/start Invite_1-x" } },
                      { update_id: 1, edited_message: { chat: { id: 1, type: "private" }, text: "/start Invite_1-x" } }, [], "null", 5]) {
    assert.equal((await call(update(body), env)).status, 204);
  }
  assert.equal(env.map.size, 0);
});

test("my_chat_member left or kicked removes the chat, with no reply; other statuses keep it", async () => {
  const env = makeEnv();
  await asks(env, "/start Invite_1-x", { chat: -7, type: "group" });
  await asks(env, "/start Invite_1-x", { chat: 8 });
  const change = (chat, status, type = "group") => update({ update_id: 2, my_chat_member: { chat: { id: chat, type }, new_chat_member: { status } } });
  assert.equal((await call(change(-7, "member"), env)).status, 204);
  assert.equal((await call(change(-7, "administrator"), env)).status, 204);
  assert.ok(subscribed(env, -7));
  assert.equal((await call(change(-7, "left"), env)).status, 204);
  assert.ok(!subscribed(env, -7));
  assert.equal((await call(change(8, "kicked", "private"), env)).status, 204);
  assert.ok(!subscribed(env, 8));
  assert.equal((await call(change(9, "kicked"), env)).status, 204);  // never subscribed: nothing happens
});

test("a group that became a supergroup keeps its subscription under the new id", async () => {
  const env = makeEnv();
  await asks(env, "/start Invite_1-x", { chat: -11, type: "group" });
  const res = await call(say(undefined, { chat: -11, type: "group", migrate_to_chat_id: -100111 }), env);
  assert.equal(res.status, 204);
  assert.ok(!subscribed(env, -11) && subscribed(env, -100111));
  assert.equal(JSON.parse(env.map.get("chat:-100111")).type, "supergroup");
  // a chat that was not subscribed does not become one by migrating
  await call(say(undefined, { chat: -22, type: "group", migrate_to_chat_id: -100222 }), env);
  assert.ok(!subscribed(env, -100222));
});

test("wrong or missing webhook secret is refused, also when WEBHOOK_SECRET is not configured", async () => {
  const env = makeEnv();
  assert.equal((await call(say("/start Invite_1-x", {}, { secret: "nope" }), env)).status, 403);
  assert.equal((await call(say("/start Invite_1-x", {}, { secret: null }), env)).status, 403);
  assert.equal((await call(say("/start Invite_1-x", {}, { secret: "" }), env)).status, 403);
  for (const unset of [undefined, ""]) {
    assert.equal((await call(say("/start Invite_1-x", {}, { secret: "" }), makeEnv({ WEBHOOK_SECRET: unset }))).status, 403);
    assert.equal((await call(say("/start Invite_1-x", {}, { secret: "undefined" }), makeEnv({ WEBHOOK_SECRET: unset }))).status, 403);
  }
  assert.equal(env.map.size, 0);
});

test("malformed or oversized bodies are accepted (204) so Telegram does not retry", async () => {
  const env = makeEnv();
  assert.equal((await call(update("{not json"), env)).status, 204);
  assert.equal((await call(update(""), env)).status, 204);
  const big = { update_id: 1, message: { chat: { id: 1, type: "private" }, text: "/start Invite_1-x", pad: "x".repeat(70 * 1024) } };
  assert.equal((await call(update(big), env)).status, 204);
  const lying = new Request("https://relay.example/telegram", {
    method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "hook", "Content-Length": String(70 * 1024) }, body: "{}",
  });
  assert.equal((await call(lying, env)).status, 204);
  assert.equal(env.map.size, 0);
});

test("no reply ever contains text taken from the incoming message", async () => {
  const env = makeEnv();
  const canary = "CANARY-<b>Zq9</b>-ωx";
  const chat = { id: 5, type: "private", title: canary, username: canary, first_name: canary, last_name: canary };
  const texts = [`/start ${canary}`, `/start@${canary} ${canary}`, `/status ${canary}`, `/stop ${canary}`, `/stop`, `/start Invite_1-x`, `/start ${canary}`, `/status`, `/stop ${canary}`];
  for (const text of texts) {
    const res = await call(update({ update_id: 1, message: { chat, text, from: { first_name: canary, username: canary } } }), env);
    if (res.status === 200) {
      const raw = JSON.stringify(await res.json());
      assert.doesNotMatch(raw, /CANARY|Zq9|ωx/);
    }
  }
  assert.doesNotMatch([...env.map.values()].join(), /CANARY|Zq9/);
});

test("a storage failure answers with a fixed message (commands) or 500 (so Telegram retries)", async () => {
  const env = makeEnv();
  env.kv.list = async () => { throw new Error("kv down /start Invite_1-x"); };
  const body = await (await call(say("/start Invite_1-x"), env)).json();
  assert.match(body.text, /Try again in a minute/);
  assert.doesNotMatch(JSON.stringify(body), /kv down/);
  env.kv.delete = async () => { throw new Error("kv down"); };
  const gone = update({ update_id: 3, my_chat_member: { chat: { id: 1, type: "group" }, new_chat_member: { status: "left" } } });
  assert.equal((await call(gone, env)).status, 500);
});

// ------------------------------------------------------------------------------------ the checker's endpoints
test("GET /subscribers returns the ids as strings, following the KV cursor", async () => {
  const env = makeEnv();
  for (const id of [5, -6, 7, -100123456789, 9]) env.map.set(`chat:${id}`, "{}");
  env.map.set("other:1", "{}");
  env.map.set("chat:not-an-id", "{}");
  const res = await call(api("/subscribers", { method: "GET" }), env);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  const { chats } = await res.json();
  assert.deepEqual([...chats].sort(), ["-100123456789", "-6", "5", "7", "9"]);
  assert.ok(chats.every((c) => typeof c === "string"));
});

test("GET /subscribers on an empty list", async () => {
  assert.deepEqual(await (await call(api("/subscribers", { method: "GET" }), makeEnv())).json(), { chats: [] });
});

test("the checker's endpoints need the pull secret, also when PULL_SECRET is not configured", async () => {
  const env = makeEnv();
  env.map.set("chat:1", "{}");
  const reqs = (auth) => [api("/subscribers", { method: "GET", auth }), api("/subscribers/remove", { auth, body: { chat_id: "1" } }),
                          api("/subscribers/migrate", { auth, body: { from: "1", to: "2" } })];
  for (const auth of [null, "Bearer nope", "pull", "Bearer ", "Bearer Pull"]) {
    for (const req of reqs(auth)) assert.equal((await call(req, env)).status, 403);
  }
  for (const unset of [undefined, ""]) {
    for (const auth of [null, "Bearer ", "Bearer undefined", "Bearer"]) {
      for (const req of reqs(auth)) assert.equal((await call(req, makeEnv({ PULL_SECRET: unset }))).status, 403);
    }
  }
  assert.deepEqual([...env.map.keys()], ["chat:1"]);
});

test("POST /subscribers/remove removes a chat and answers 204 (also when it was not there)", async () => {
  const env = makeEnv();
  env.map.set("chat:-42", "{}");
  assert.equal((await call(api("/subscribers/remove", { body: { chat_id: "-42" } }), env)).status, 204);
  assert.equal(env.map.size, 0);
  assert.equal((await call(api("/subscribers/remove", { body: { chat_id: "-42" } }), env)).status, 204);
});

test("POST /subscribers/migrate moves a subscription", async () => {
  const env = makeEnv();
  env.map.set("chat:-1", JSON.stringify({ type: "group", added_at: "2026-01-01T00:00:00.000Z" }));
  assert.equal((await call(api("/subscribers/migrate", { body: { from: "-1", to: "-1001" } }), env)).status, 204);
  assert.deepEqual([...env.map.keys()], ["chat:-1001"]);
  // not subscribed: nothing is created
  assert.equal((await call(api("/subscribers/migrate", { body: { from: "-5", to: "-1005" } }), env)).status, 204);
  assert.deepEqual([...env.map.keys()], ["chat:-1001"]);
});

test("POST /subscribers/reset removes every subscriber, only with the pull secret and the confirm word", async () => {
  const env = makeEnv();
  for (const id of ["1", "-2", "-1003", "44", "55"]) env.map.set(`chat:${id}`, "{}");
  env.map.set("other:key", "kept");
  assert.equal((await call(api("/subscribers/reset", { auth: null, body: { confirm: "remove-all" } }), env)).status, 403);
  assert.equal((await call(api("/subscribers/reset", { auth: "Bearer wrong", body: { confirm: "remove-all" } }), env)).status, 403);
  assert.equal((await call(api("/subscribers/reset", { body: {} }), env)).status, 400);
  assert.equal((await call(api("/subscribers/reset", { body: { confirm: "yes" } }), env)).status, 400);
  assert.equal((await call(api("/subscribers/reset", { method: "GET" }), env)).status, 404);
  assert.equal(env.map.size, 6);
  const res = await call(api("/subscribers/reset", { body: { confirm: "remove-all" } }), env);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { removed: 5 });
  assert.deepEqual([...env.map.keys()], ["other:key"]);          // more than one KV page was followed
  assert.deepEqual(await (await call(api("/subscribers/reset", { body: { confirm: "remove-all" } }), env)).json(), { removed: 0 });
});

test("invalid ids, bodies and sizes are refused with 400 or 413", async () => {
  const env = makeEnv();
  env.map.set("chat:1", "{}");
  const bad = ["", "abc", "1.5", "--1", "1 ", "12345678901234567890123", "chat:1", "1\n2", 1, null, ["1"]];
  for (const id of bad) {
    assert.equal((await call(api("/subscribers/remove", { body: { chat_id: id } }), env)).status, 400);
    assert.equal((await call(api("/subscribers/migrate", { body: { from: id, to: "2" } }), env)).status, 400);
    assert.equal((await call(api("/subscribers/migrate", { body: { from: "1", to: id } }), env)).status, 400);
  }
  for (const body of ["{not json", "null", "[]", "5", "{}"]) {
    assert.equal((await call(api("/subscribers/remove", { body }), env)).status, 400);
  }
  assert.equal((await call(api("/subscribers/remove", { body: { chat_id: "1", pad: "x".repeat(70 * 1024) } }), env)).status, 413);
  assert.deepEqual([...env.map.keys()], ["chat:1"]);
});

test("unknown paths and wrong methods are 404", async () => {
  const env = makeEnv();
  for (const req of [
    new Request("https://relay.example/", { method: "POST" }), new Request("https://relay.example/telegram"),
    api("/subscribers", { method: "POST", body: {} }), api("/subscribers/remove", { method: "GET" }),
    api("/subscribers/migrate", { method: "GET" }), api("/subscribers/other", { body: {} }), api("/listings", { body: {} }),
  ]) {
    assert.equal((await call(req, env)).status, 404);
  }
});
