#!/usr/bin/env bash
# Let other chats subscribe to the alerts with a secret invite link.
# Puts a tiny relay on Cloudflare Workers (free). It receives the bot's webhook, keeps the list of subscribed
# chats in Workers KV (never in the public repo) and never gets the bot token. The checker on GitHub pulls
# the list on every run. Safe to re-run. Prints no token, secret or chat id. The invite links are printed
# on purpose: they are what you send to people.
#
#   ./telegram-relay/deploy.sh               set up or update the relay, then print the invite links
#   ./telegram-relay/deploy.sh --links       print the invite links again
#   ./telegram-relay/deploy.sh --new-invite  make a new invite code (the old links stop working;
#                                            people who already subscribed stay subscribed)
#   ./telegram-relay/deploy.sh --count       how many chats are subscribed (never which)
#   ./telegram-relay/deploy.sh --remove-all  remove every subscriber and make a new invite code: for when a link
#                                            reached someone it should not have. Invite the right people again.
#   ./telegram-relay/deploy.sh --off         switch it off: alerts go to your own chat only again
#
# Needs: ./go-live.sh done (.env holds the bot token and chat id), `npx wrangler login` done once, and
# this version of the checker (hunter/ and hunt.yml) pushed to GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=deploy
for arg in "$@"; do
  case "$arg" in
    --off) MODE=off ;;
    --links) MODE=links ;;
    --new-invite) MODE=new-invite ;;
    --count) MODE=count ;;
    --remove-all) MODE=remove-all ;;
    *) echo "Unknown option: $arg (use --off, --links, --new-invite, --count or --remove-all)"; exit 1 ;;
  esac
done

KV_TITLE="house-hunter-subscribers"
# A fixed wrangler version: it runs with your Cloudflare login, so never pull an unreviewed new release.
WRANGLER="wrangler@4.164.0"
export WRANGLER_SEND_METRICS=false
GH="$(command -v gh || true)"
if [ -z "$GH" ] && [ -x "$HOME/.local/bin/gh" ]; then GH="$HOME/.local/bin/gh"; fi

touch .env
chmod 600 .env
env_get() {  # value of KEY in .env, without surrounding quotes
  local v; v="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
env_set() {  # env_set KEY VALUE
  local tmp; tmp="$(mktemp)"
  grep -v -E "^$1=" .env > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}
env_unset() {  # env_unset KEY
  local tmp; tmp="$(mktemp)"
  grep -v -E "^$1=" .env > "$tmp" || true
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}
if [ -z "$(env_get TELEGRAM_BOT_TOKEN)" ] || [ -z "$(env_get TELEGRAM_CHAT_ID)" ]; then
  echo "No bot token or chat id in .env. Run ./go-live.sh first."; exit 1
fi

# tg METHOD [JSON]: call the Bot API, print Telegram's JSON answer. The token and the payload travel in
# environment variables, never on a command line, and errors never print the URL (it contains the token).
tg() {
  local payload="${2:-}"; [ -n "$payload" ] || payload='{}'
  TELEGRAM_BOT_TOKEN="$(env_get TELEGRAM_BOT_TOKEN)" TG_PAYLOAD="$payload" python3 -c '
import json, os, sys, urllib.error, urllib.request
url = "https://api.telegram.org/bot%s/%s" % (os.environ["TELEGRAM_BOT_TOKEN"], sys.argv[1])
req = urllib.request.Request(url, data=os.environ["TG_PAYLOAD"].encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.read().decode())
except Exception as e:
    print(json.dumps({"ok": False, "description": type(e).__name__}))
' "$1"
}
field() {  # field EXPR: evaluate a Python expression on JSON from stdin (d = the parsed JSON)
  python3 -c 'import json, sys; d = json.load(sys.stdin); v = eval(sys.argv[1]); print("" if v is None else v)' "$1"
}
valid_code() { [[ "$1" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; }  # Telegram's rule for a start parameter
newsecret() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
newcode() { python3 -c 'import secrets; print(secrets.token_urlsafe(16))'; }  # 22 characters of A-Z a-z 0-9 _ -

MENU='[{"command":"start","description":"Subscribe with an invite link"},{"command":"stop","description":"Stop the alerts in this chat"},{"command":"status","description":"Is this chat subscribed?"}]'
set_menu() {
  local r; r="$(tg setMyCommands "{\"commands\": $MENU}")"
  [ "$(printf %s "$r" | field 'd.get("ok")')" = "True" ] || echo "(could not set the command menu: $(printf %s "$r" | field 'd.get("description")'))"
}

print_links() {  # print_links CODE
  local bot; bot="$(tg getMe | field '(d.get("result") or {}).get("username")')"
  if [ -z "$bot" ]; then echo "Could not read the bot's username from Telegram."; return 1; fi
  echo "Invite links (send them only to people you want to give the alerts to):"
  echo "  Private chat with the bot:   https://t.me/$bot?start=$1"
  echo "  Add the bot to a group:      https://t.me/$bot?startgroup=$1"
  echo "A new invite link: ./telegram-relay/deploy.sh --new-invite   Remove everyone: ./telegram-relay/deploy.sh --remove-all"
  echo "Switch it off: ./telegram-relay/deploy.sh --off"
}

wr() { (cd telegram-relay && npx --yes "$WRANGLER" "$@"); }

# ---------------------------------------------------------------------------------------------- --links
if [ "$MODE" = links ]; then
  CODE="$(env_get RELAY_INVITE_CODE)"
  if ! valid_code "$CODE"; then echo "No invite code yet. Run ./telegram-relay/deploy.sh first."; exit 1; fi
  print_links "$CODE"
  exit $?
fi

# ---------------------------------------------------------------------------------------------- --count
if [ "$MODE" = count ]; then
  if [ -z "$(env_get RELAY_URL)" ] || [ -z "$(env_get RELAY_PULL_SECRET)" ]; then
    echo "The relay is not set up (no RELAY_URL / RELAY_PULL_SECRET in .env). Run ./telegram-relay/deploy.sh first."; exit 1
  fi
  RELAY_URL="$(env_get RELAY_URL)" RELAY_PULL_SECRET="$(env_get RELAY_PULL_SECRET)" python3 -c '
import json, os, urllib.error, urllib.request
req = urllib.request.Request(os.environ["RELAY_URL"].rstrip("/") + "/subscribers",
                             headers={"Authorization": "Bearer " + os.environ["RELAY_PULL_SECRET"], "User-Agent": "house-hunter"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        n = len(json.load(r)["chats"])
    print("%d subscribed chat%s (besides yours)." % (n, "" if n == 1 else "s"))
except Exception as e:
    print("Could not ask the relay: %s" % (e.code if isinstance(e, urllib.error.HTTPError) else type(e).__name__))
    raise SystemExit(1)
'
  exit $?
fi

# ---------------------------------------------------------------------------------------------- --remove-all
if [ "$MODE" = remove-all ]; then
  if [ -z "$(env_get RELAY_URL)" ] || [ -z "$(env_get RELAY_PULL_SECRET)" ]; then
    echo "The relay is not set up (no RELAY_URL / RELAY_PULL_SECRET in .env). Run ./telegram-relay/deploy.sh first."; exit 1
  fi
  # First close the door (a new invite code), then empty the room.
  CODE="$(newcode)"
  if ! printf %s "$CODE" | wr secret put INVITE_CODE >/dev/null; then
    echo "Could not set a new invite code on Cloudflare, so nobody was removed. Run: npx wrangler login   and try again."; exit 1
  fi
  env_set RELAY_INVITE_CODE "$CODE"
  RELAY_URL="$(env_get RELAY_URL)" RELAY_PULL_SECRET="$(env_get RELAY_PULL_SECRET)" python3 -c '
import json, os, urllib.error, urllib.request
req = urllib.request.Request(os.environ["RELAY_URL"].rstrip("/") + "/subscribers/reset", method="POST",
                             data=json.dumps({"confirm": "remove-all"}).encode(),
                             headers={"Authorization": "Bearer " + os.environ["RELAY_PULL_SECRET"],
                                      "Content-Type": "application/json", "User-Agent": "house-hunter"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        n = json.load(r)["removed"]
    print("Removed %d subscribed chat%s. Your own chat is not on that list and keeps getting the alerts." % (n, "" if n == 1 else "s"))
except Exception as e:
    print("Could not ask the relay: %s" % (e.code if isinstance(e, urllib.error.HTTPError) else type(e).__name__))
    print("The invite code is new, but the subscribers are still there. Run ./telegram-relay/deploy.sh (it updates")
    print("the relay), then this command again.")
    raise SystemExit(1)
' || exit 1
  echo "The old invite links no longer work. New ones:"
  print_links "$CODE"
  exit $?
fi

# ---------------------------------------------------------------------------------------------- --new-invite
if [ "$MODE" = new-invite ]; then
  if [ -z "$(env_get RELAY_URL)" ]; then echo "The relay is not set up. Run ./telegram-relay/deploy.sh first."; exit 1; fi
  CODE="$(newcode)"
  if ! printf %s "$CODE" | wr secret put INVITE_CODE >/dev/null; then
    echo "Could not set the new invite code on Cloudflare. Run: npx wrangler login   and try again."; exit 1
  fi
  env_set RELAY_INVITE_CODE "$CODE"
  echo "New invite code set. The old links no longer work; chats that already subscribed stay subscribed."
  print_links "$CODE"
  exit $?
fi

# ---------------------------------------------------------------------------------------------- --off
if [ "$MODE" = off ]; then
  R="$(tg deleteWebhook)"
  if [ "$(printf %s "$R" | field 'd.get("ok")')" != "True" ]; then
    echo "Telegram refused: $(printf %s "$R" | field 'd.get("description")')"; exit 1
  fi
  if [ -n "$GH" ]; then
    "$GH" variable delete RELAY_URL >/dev/null 2>&1 || true        # the checker stops asking for subscribers
    "$GH" secret delete RELAY_PULL_SECRET >/dev/null 2>&1 || true
  else
    echo "(gh not found: delete the RELAY_URL variable and RELAY_PULL_SECRET secret on GitHub by hand)"
  fi
  env_unset RELAY_URL
  env_unset RELAY_PULL_SECRET
  echo "Relay off. The bot's webhook is deleted, and alerts go to your own chat only. RELAY_URL and"
  echo "RELAY_PULL_SECRET are removed from GitHub and from .env (the invite code stays in .env, so deploying"
  echo "again keeps the same invite links)."
  echo "The list of subscribed chats is still stored on Cloudflare, and the Worker is left in place. To erase both:"
  echo "  (cd telegram-relay && npx $WRANGLER kv namespace delete --binding SUBSCRIBERS)"
  echo "  (cd telegram-relay && npx $WRANGLER delete)"
  exit 0
fi

# ---------------------------------------------------------------------------------------------- deploy
if [ -z "$GH" ]; then echo "GitHub CLI (gh) not found."; exit 1; fi
# account-level commands run from an empty folder: the relay config is only valid once its KV id is filled in
NEUTRAL="$(mktemp -d)"
trap 'rm -rf "$NEUTRAL"' EXIT  # also when a later step stops the script
wr_any() { (cd "$NEUTRAL" && npx --yes "$WRANGLER" "$@" </dev/null); }

# 1. Checks
echo "== 1/5 Checks"
SLUG="$("$GH" repo view --json nameWithOwner -q .nameWithOwner)"
REMOTE_WORKFLOW="$("$GH" api -H "Accept: application/vnd.github.raw+json" "repos/$SLUG/contents/.github/workflows/hunt.yml?ref=main" 2>/dev/null || true)"
if ! printf %s "$REMOTE_WORKFLOW" | grep -q "RELAY_PULL_SECRET"; then
  echo "GitHub doesn't have the subscriber version of the checker (hunt.yml) yet. Push first:"
  echo "  git pull --rebase && git push"; exit 1
fi
WHOAMI="$(wr_any whoami 2>&1 || true)"
if ! printf %s "$WHOAMI" | grep -qi "you are logged in"; then
  echo "Not signed in to Cloudflare. Run once: npx wrangler login"; exit 1
fi
OWNER="${SLUG%%/*}"; NAME="${SLUG##*/}"
DASHBOARD_URL="https://${OWNER}.github.io/${NAME}/"
echo "Repository $SLUG, Cloudflare signed in."

# 2. Storage for the subscriber list (Workers KV)
echo; echo "== 2/5 Storage"
kv_id() { { wr_any kv namespace list 2>/dev/null || true; } | python3 -c 'import json, sys
try: print(next((n["id"] for n in json.load(sys.stdin) if n.get("title") == sys.argv[1]), ""))
except Exception: print("")' "$KV_TITLE"; }
KV_ID="$(kv_id)"
if [ -z "$KV_ID" ]; then
  # from an empty folder, so wrangler doesn't try to edit the config or ask questions
  wr_any kv namespace create "$KV_TITLE" >/dev/null || true
  KV_ID="$(kv_id)"
fi
if [ -z "$KV_ID" ]; then echo "Could not create the KV namespace $KV_TITLE."; exit 1; fi
python3 - "$KV_ID" <<'PY'
import pathlib, re, sys
p = pathlib.Path("telegram-relay/wrangler.jsonc")
s = p.read_text()
new = re.sub(r'("binding": "SUBSCRIBERS", "id": ")[^"]*(")', lambda m: m.group(1) + sys.argv[1] + m.group(2), s)
if new == s and sys.argv[1] not in s:
    sys.exit("Could not find the SUBSCRIBERS binding in telegram-relay/wrangler.jsonc")
p.write_text(new)
PY
echo "KV namespace $KV_TITLE ready."
if ! git diff --quiet -- telegram-relay/wrangler.jsonc 2>/dev/null; then
  echo "(telegram-relay/wrangler.jsonc now holds its id: that is an address, not a secret. Commit that file.)"
fi

# 3. Deploy the Worker and its secrets (Cloudflare keeps them encrypted; it never gets the bot token)
echo; echo "== 3/5 Deploying the relay"
OUT="$(wr deploy 2>&1)" || { echo "$OUT"; exit 1; }
URL="$(printf %s "$OUT" | grep -oE 'https://[A-Za-z0-9.-]+\.workers\.dev' | head -1 || true)"
if [ -z "$URL" ]; then echo "$OUT"; echo "Deployed, but could not find the workers.dev address above."; exit 1; fi
echo "Relay at $URL"
WEBHOOK_SECRET="$(newsecret)"
PULL_SECRET="$(env_get RELAY_PULL_SECRET)"; [ -n "$PULL_SECRET" ] || PULL_SECRET="$(newsecret)"
INVITE_CODE="$(env_get RELAY_INVITE_CODE)"; valid_code "$INVITE_CODE" || INVITE_CODE="$(newcode)"  # a re-run keeps the code
printf %s "$PULL_SECRET" | wr secret put PULL_SECRET >/dev/null
printf %s "$INVITE_CODE" | wr secret put INVITE_CODE >/dev/null
printf %s "$DASHBOARD_URL" | wr secret put DASHBOARD_URL >/dev/null
echo "Relay secrets set."

# 4. Let the checker on GitHub (and the Mac backup) pull the list
echo; echo "== 4/5 Connecting the checker"
printf %s "$PULL_SECRET" | "$GH" secret set RELAY_PULL_SECRET >/dev/null
"$GH" variable set RELAY_URL --body "$URL" >/dev/null
echo "set RELAY_PULL_SECRET and RELAY_URL on GitHub."
env_set RELAY_URL "$URL"
env_set RELAY_PULL_SECRET "$PULL_SECRET"
env_set RELAY_INVITE_CODE "$INVITE_CODE"
echo "saved RELAY_URL, RELAY_PULL_SECRET and RELAY_INVITE_CODE in .env (not in git), so the Mac backup uses the relay too."

# 5. Point the bot at the relay, then check the whole path without subscribing anybody
echo; echo "== 5/5 Telegram webhook"
PAYLOAD="$(URL="$URL/telegram" S="$WEBHOOK_SECRET" python3 -c 'import json, os
print(json.dumps({"url": os.environ["URL"], "secret_token": os.environ["S"],
                  "allowed_updates": ["message", "my_chat_member"],
                  "drop_pending_updates": False, "max_connections": 1}))')"
# the new webhook secret goes to Cloudflare and Telegram back to back, so no update meets a mismatch for long
printf %s "$WEBHOOK_SECRET" | wr secret put WEBHOOK_SECRET >/dev/null
R="$(tg setWebhook "$PAYLOAD")"
if [ "$(printf %s "$R" | field 'd.get("ok")')" != "True" ]; then
  echo "Telegram refused the webhook: $(printf %s "$R" | field 'd.get("description")')"; exit 1
fi
set_menu

# The relay answers a webhook call with the message it wants sent. Called by this script instead of Telegram,
# that message just comes back here. /status from chat id 0 (no real chat) must not subscribe anybody.
CHECK="$(URL="$URL" S="$WEBHOOK_SECRET" P="$PULL_SECRET" python3 -c '
import json, os, time, urllib.error, urllib.request
# a named User-Agent: Cloudflare blocks the default Python one before the Worker sees it (error 1010)
UA = {"User-Agent": "house-hunter-setup"}
def call(method, path, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(os.environ["URL"] + path, data=data, method=method,
                                 headers=dict(UA, **{"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return type(e).__name__, ""
update = {"update_id": 0, "message": {"chat": {"id": 0, "type": "private"}, "text": "/status"}}
hook = {"X-Telegram-Bot-Api-Secret-Token": os.environ["S"]}
pull = {"Authorization": "Bearer " + os.environ["P"]}
result = "relay did not answer"
for _ in range(8):  # a new Worker can take a moment
    status, body = call("POST", "/telegram", hook, update)
    if status == 200 and body:
        text = json.loads(body).get("text", "")
        result = "ok" if text.startswith("This chat is not subscribed") else "unexpected reply to /status"
        break
    result = "relay answered %s to the webhook call" % status
    time.sleep(5)
if result == "ok":
    if call("POST", "/telegram", None, update)[0] != 403 or call("GET", "/subscribers")[0] != 403 \
       or call("GET", "/subscribers", {"Authorization": "Bearer wrong"})[0] != 403:
        result = "relay does not refuse calls without the right secret"
if result == "ok":
    status, body = call("GET", "/subscribers", pull)
    try:
        chats = json.loads(body)["chats"] if status == 200 else None
    except Exception:
        chats = None
    if chats is None:
        result = "the pull secret is not accepted (status %s)" % status
    elif "0" in chats:
        result = "chat id 0 was subscribed by the check"
print(result)
')"
unset WEBHOOK_SECRET PULL_SECRET
SET_URL="$(tg getWebhookInfo | field '(d.get("result") or {}).get("url")')"  # empty when Telegram refuses the call
if [ "$SET_URL" != "$URL/telegram" ]; then echo "The webhook points to '$SET_URL' instead of the relay. Re-run."; exit 1; fi
if [ "$CHECK" != "ok" ]; then
  echo "Something is off: $CHECK. Wait a minute (a new Worker can take a moment) and re-run."; exit 1
fi
echo "Webhook set. The relay answers, refuses calls without a secret, and the checker's pull secret works."
echo
echo "Done. Alerts now also go to every chat that subscribes, at the start of the next run."
print_links "$INVITE_CODE"
unset INVITE_CODE
