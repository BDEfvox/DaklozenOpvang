#!/usr/bin/env bash
# One-time setup for House Hunter. Safe to re-run. You run this yourself: it asks for secrets
# interactively and sends them straight to GitHub; nothing secret is written to the repo.
set -euo pipefail
cd "$(dirname "$0")"

# ./go-live.sh --telegram   finds the Telegram group again (e.g. after it became a "supergroup")
REDO_TELEGRAM=0
for arg in "$@"; do
  case "$arg" in
    --telegram) REDO_TELEGRAM=1 ;;
    *) echo "Unknown option: $arg (only --telegram is supported)"; exit 1 ;;
  esac
done

GH="$(command -v gh || true)"
if [ -z "$GH" ] && [ -x "$HOME/.local/bin/gh" ]; then GH="$HOME/.local/bin/gh"; fi
if [ -z "$GH" ]; then echo "GitHub CLI (gh) not found. Install it from https://cli.github.com and re-run."; exit 1; fi

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

touch .env
chmod 600 .env
env_get() {  # value of KEY in .env, without surrounding quotes
  local v; v="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
assert_no_env_staged() {  # the .env file holds your secrets: it must never reach the public repo
  git rm -q --cached --ignore-unmatch .env >/dev/null 2>&1 || true
  if git diff --cached --name-only | grep -qE '(^|/)\.env$'; then echo "Refusing to commit .env (secrets)."; exit 1; fi
}
env_set() {  # env_set KEY VALUE
  local tmp; tmp="$(mktemp)"
  grep -v -E "^$1=" .env > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}
if ! grep -qxF '.env' .gitignore 2>/dev/null; then
  [ -s .gitignore ] && [ -n "$(tail -c1 .gitignore)" ] && echo >> .gitignore
  echo '.env' >> .gitignore
fi

# 1. GitHub login
echo; echo "== 1/9 GitHub login"
if "$GH" auth status >/dev/null 2>&1; then echo "Already logged in."; else "$GH" auth login --web --git-protocol https; fi
"$GH" auth setup-git   # lets plain `git push` use this GitHub login
# Commits need a name and email. If git has none on this Mac, use your GitHub name and private noreply address.
if [ -z "$(git config user.email || true)" ]; then
  GH_LOGIN="$("$GH" api user -q .login)"; GH_ID="$("$GH" api user -q .id)"
  git config user.name "$GH_LOGIN"
  git config user.email "${GH_ID}+${GH_LOGIN}@users.noreply.github.com"
  echo "Set this repo's git identity to $GH_LOGIN (GitHub noreply email)."
fi

# 2. Telegram
echo; echo "== 2/9 Telegram"
if [ -z "$(env_get TELEGRAM_BOT_TOKEN)" ]; then
  cat <<'TXT'
Create your bot:
  1. In Telegram open @BotFather and send /newbot, then follow the questions.
  2. Copy the token it gives you (looks like 123456:ABC...).
  3. Create a group with your friends and add the bot to it.
TXT
  read -r -s -p "Paste the bot token (input hidden): " TOKEN; echo
  if [ -z "$TOKEN" ]; then echo "No token given."; exit 1; fi
  env_set TELEGRAM_BOT_TOKEN "$TOKEN"
  unset TOKEN
fi
if [ -z "$(env_get TELEGRAM_CHAT_ID)" ] || [ "$REDO_TELEGRAM" = "1" ]; then
  "$PY" -m hunter telegram-setup
fi

# 3. Optional email alerts
echo; echo "== 3/9 Email alerts (optional)"
cat <<'TXT'
Pararius, Funda and others block automated checks. As a workaround you can make a dedicated Gmail
account with an app password, subscribe to their saved-search alert emails with it, and House Hunter
will read that inbox. Skip this if you do not want that now.
TXT
read -r -p "Set up the email-alert inbox now? [y/N] " ANS || ANS=""
if [[ "${ANS:-N}" =~ ^[Yy] ]]; then
  read -r -p "IMAP host [imap.gmail.com]: " IMAP_HOST; IMAP_HOST="${IMAP_HOST:-imap.gmail.com}"
  read -r -p "IMAP user (the alert inbox address): " IMAP_USER
  read -r -s -p "IMAP password / app password (hidden): " IMAP_PASSWORD; echo
  read -r -p "IMAP folder [INBOX]: " IMAP_FOLDER; IMAP_FOLDER="${IMAP_FOLDER:-INBOX}"
  env_set IMAP_HOST "$IMAP_HOST"; env_set IMAP_USER "$IMAP_USER"
  env_set IMAP_PASSWORD "$IMAP_PASSWORD"; env_set IMAP_FOLDER "$IMAP_FOLDER"
  unset IMAP_PASSWORD
fi

# 4. Keep local test state out of the repo
echo; echo "== 4/9 Checking data/"
mkdir -p data
touch data/.gitkeep
grep -qxF 'data.local-backup/' .gitignore || echo 'data.local-backup/' >> .gitignore
MOVED=0
for f in data/* data/.[!.]*; do
  [ -e "$f" ] || continue
  [ "$(basename "$f")" = ".gitkeep" ] && continue
  mkdir -p data.local-backup
  mv "$f" "data.local-backup/$(basename "$f").$(date +%s)"
  MOVED=1
done
if [ "$MOVED" = "1" ]; then echo "Moved local test state to data.local-backup/ (not pushed)."; fi

# 5. Repository
echo; echo "== 5/9 Repository"
if ! git remote get-url origin >/dev/null 2>&1; then
  read -r -p "Repository name [house-hunter]: " REPO; REPO="${REPO:-house-hunter}"
  git add -A; assert_no_env_staged
  if ! git diff --cached --quiet; then git commit -q -m "House Hunter"; fi
  "$GH" repo create "$REPO" --public --source . --remote origin --push
else
  git add -A; assert_no_env_staged
  if ! git diff --cached --quiet; then git commit -q -m "House Hunter setup"; fi
  git push -u origin HEAD
fi
SLUG="$("$GH" repo view --json nameWithOwner -q .nameWithOwner)"
OWNER="${SLUG%%/*}"; NAME="${SLUG##*/}"
URL="https://${OWNER}.github.io/${NAME}/"

# 6. Secrets and variables
echo; echo "== 6/9 Secrets"
for KEY in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID IMAP_HOST IMAP_USER IMAP_PASSWORD IMAP_FOLDER; do
  VAL="$(env_get "$KEY")"
  if [ -n "$VAL" ]; then
    printf %s "$VAL" | "$GH" secret set "$KEY" >/dev/null && echo "set $KEY"
  fi
done
unset VAL
"$GH" variable set DASHBOARD_URL --body "$URL" && echo "set DASHBOARD_URL=$URL"

# 7. Pages
echo; echo "== 7/9 GitHub Pages"
if OUT="$("$GH" api -X POST "repos/${SLUG}/pages" -f "source[branch]=main" -f "source[path]=/docs" 2>&1)"; then
  echo "Pages enabled."
elif echo "$OUT" | grep -qi "already"; then
  echo "Pages already enabled."
else
  echo "$OUT"
fi

# 8. First run
echo; echo "== 8/9 First run"
START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED=0
for _ in $(seq 1 12); do   # GitHub needs a few seconds to register a freshly pushed workflow
  "$GH" workflow enable hunt.yml >/dev/null 2>&1 || true
  if "$GH" workflow run hunt.yml >/dev/null 2>&1; then STARTED=1; break; fi
  sleep 5
done
if [ "$STARTED" != "1" ]; then echo "Could not start the workflow. Open the repo's Actions tab and run 'hunt' by hand."; exit 1; fi
RUN_ID=""
for _ in $(seq 1 30); do
  sleep 4
  RUN_ID="$("$GH" run list --workflow hunt.yml --event workflow_dispatch --limit 5 \
            --json databaseId,createdAt -q "[.[] | select(.createdAt >= \"$START\")][0].databaseId" 2>/dev/null || true)"
  if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ]; then break; fi
done
if [ -z "$RUN_ID" ] || [ "$RUN_ID" = "null" ]; then echo "Could not find the run. Check the Actions tab."; exit 1; fi
"$GH" run watch "$RUN_ID" --exit-status || echo "The run reported a problem; see the Actions tab."
git pull --rebase -q || true
echo
if [ -f data/health.json ]; then
  "$PY" - <<'PYEOF'
import json
h = json.load(open("data/health.json"))
print("%-18s %-9s %s" % ("source", "status", "note"))
for n, v in sorted(h.items()):
    print("%-18s %-9s %s" % (n, v.get("status", "?"), (v.get("error") or v.get("note") or "")[:70]))
bad = [n for n, v in h.items() if v.get("status") in ("blocked", "error")]
if bad:
    print("\n%d site(s) can't be checked from GitHub's servers: %s." % (len(bad), ", ".join(sorted(bad))))
    print("Your Mac can cover them (only those, so no double alerts):  ./local-backup.sh on")
PYEOF
else
  echo "(no data/health.json yet)"
fi

# 9. Done
echo; echo "== 9/9 Done"
echo "Dashboard: $URL  (Pages can take a minute or two to appear the first time)"
echo "First alerts should be in your Telegram group now."
