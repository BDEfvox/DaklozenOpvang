#!/usr/bin/env bash
# Mac backup for House Hunter. OFF until you turn it on; nothing is installed before that.
#
#   ./local-backup.sh on       start: runs every 10 minutes while this Mac is awake
#   ./local-backup.sh off      stop
#   ./local-backup.sh status   on or off, plus the last log lines
#   ./local-backup.sh run      one run now in this terminal (add --dry-run to send nothing)
#   ./local-backup.sh remove   stop and delete the background job
#
# What it does (mode "auto" in config.json): checks only the sites the GitHub checker reports as blocked or
# broken, so you never get the same alert twice, and warns you in Telegram if GitHub stops running.
# Its messages start with "💻 Mac backup".
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="local.house-hunter.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LOGDIR="$DIR/data-local"
LOG="$LOGDIR/backup.log"
PY="$DIR/.venv/bin/python"

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$PY</string>
    <string>-W</string>
    <string>ignore</string>
    <string>-m</string>
    <string>hunter</string>
    <string>backup</string>
    <string>--log</string>
    <string>$LOG</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
}

is_on() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

stop() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl disable "$DOMAIN/$LABEL" 2>/dev/null || true
}

case "${1:-status}" in
  on)
    if [ ! -x "$PY" ]; then echo "No .venv yet. Run ./go-live.sh first."; exit 1; fi
    if ! grep -qE '^TELEGRAM_BOT_TOKEN=.' "$DIR/.env" 2>/dev/null; then
      echo "Note: no Telegram token in .env yet, so messages only go to $LOG"
    fi
    write_plist
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl enable "$DOMAIN/$LABEL"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Mac backup is ON (every 10 minutes while this Mac is awake). Log: $LOG"
    echo "It only runs while the Mac is awake. To keep it awake when plugged in: System Settings > Battery >"
    echo "Options > 'Prevent automatic sleeping on power adapter when the display is off'."
    ;;
  off)
    stop
    echo "Mac backup is OFF."
    ;;
  status)
    if is_on; then echo "Mac backup is ON."; else echo "Mac backup is OFF."; fi
    if [ -f "$LOG" ]; then echo "--- last log lines:"; tail -n 15 "$LOG"; fi
    ;;
  run)
    shift
    cd "$DIR" && "$PY" -W ignore -m hunter backup "$@"
    ;;
  remove)
    stop
    rm -f "$PLIST"
    echo "Mac backup removed (local data in data-local/ kept)."
    ;;
  *)
    echo "Usage: $0 on | off | status | run [--dry-run] [--mode all] | remove"
    exit 1
    ;;
esac
