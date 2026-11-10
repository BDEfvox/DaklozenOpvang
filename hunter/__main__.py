"""CLI: python -m hunter run | check | telegram-setup | dashboard | backup"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from .config import load_config
from .envfile import ENV_PATH, load_env, write_env_value


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run
    cfg = load_config()
    only = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    return run(cfg, dry_run=args.dry_run, only=only, data_dir=args.data_dir, docs_dir=args.docs_dir)


def cmd_check(args: argparse.Namespace) -> int:
    from .filters import evaluate
    from .http import Http
    from .runner import fetch_source
    from .sources import all_sources
    cfg = load_config()
    http = Http()
    sources = all_sources()
    if not sources:
        print("No source modules found in hunter/sources/ yet.")
        return 0
    if args.source:
        if args.source not in sources:
            print("Unknown source %r. Available: %s" % (args.source, ", ".join(sources)))
            return 2
        sources = {args.source: sources[args.source]}
    table = []
    for name, src in sources.items():
        reason = src.unavailable_reason()
        if reason:
            print("== %s: disabled (%s)" % (name, reason))
            table.append((name, "disabled", 0, 0, 0.0, reason))
            continue
        t0 = time.time()
        fr = fetch_source(src, cfg, http, only_city=args.city)
        dt = time.time() - t0
        matched = [(l, evaluate(l, cfg)) for l in fr.listings]
        matched = [(l, r) for l, r in matched if r.matched]
        print("== %s: %s, %d parsed, %d matched, %.1fs" % (name, fr.status, len(fr.listings), len(matched), dt))
        for city, (n, err) in fr.per_city.items():
            print("   %s: %s" % (city, ("%d listings" % n) if err is None else "ERROR " + err))
        for l in fr.listings[:3]:
            print("   - EUR %s | %s bed / %s rooms | %s" % (l.price_total, l.bedrooms, l.rooms, (l.title or "")[:70]))
        table.append((name, fr.status, len(fr.listings), len(matched), dt, fr.error or ""))
    print("\n%-18s %-9s %7s %8s %6s  %s" % ("source", "status", "parsed", "matched", "secs", "note"))
    for name, status, n, m, dt, note in table:
        print("%-18s %-9s %7d %8d %6.1f  %s" % (name, status, n, m, dt, note[:80]))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import write_dashboard
    from .state import State
    cfg = load_config()
    path = write_dashboard(cfg, State(args.data_dir, cfg), args.docs_dir, datetime.now(timezone.utc))
    print("wrote %s" % path)
    return 0


def cmd_telegram_setup(args: argparse.Namespace) -> int:
    from .notify.telegram import api_call
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set (environment or .env). Create a bot with @BotFather first.")
        return 2
    try:
        me = api_call(token, "getMe")
    except RuntimeError as exc:
        print(exc)
        return 1
    if not me.get("ok"):
        print("Telegram rejected the token: %s" % me.get("description"))
        return 1
    bot = me["result"]["username"]
    chat_id = args.chat_id
    if not chat_id:
        print("Add @%s to your group, then send /start@%s in the group." % (bot, bot))
        print("(Bots have privacy mode on, so they only see commands.) Waiting up to %d s..." % args.wait)
        deadline = time.time() + args.wait
        offset = None
        found = None
        while time.time() < deadline and not found:
            payload = {"timeout": 10, "allowed_updates": ["message", "my_chat_member"]}
            if offset:
                payload["offset"] = offset
            try:
                data = api_call(token, "getUpdates", payload, timeout=30)
            except RuntimeError as exc:
                print(exc)
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("my_chat_member") or {}
                chat = msg.get("chat") or {}
                if chat.get("type") in ("group", "supergroup"):
                    found = chat
        if not found:
            print("No group message seen. Run the command again after sending /start@%s in the group." % bot)
            return 1
        chat_id = str(found["id"])
        print("Found group: %s" % found.get("title", chat_id))
    write_env_value("TELEGRAM_CHAT_ID", str(chat_id))
    print("Saved TELEGRAM_CHAT_ID to %s" % ENV_PATH)
    r = api_call(token, "sendMessage", {"chat_id": chat_id, "text": "✅ House Hunter is connected to this group"})
    if not r.get("ok"):
        print("Test message failed: %s" % r.get("description"))
        return 1
    print("Test message sent.")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    from .backup import run_backup, trim_log
    cfg = load_config()
    if args.log:
        trim_log(args.log)
    mode = args.mode or (cfg.get("local_backup") or {}).get("mode", "auto")
    print("[backup] %s, mode %s" % (datetime.now().strftime("%Y-%m-%d %H:%M"), mode))
    return run_backup(cfg, mode=mode, dry_run=args.dry_run)


def main(argv: Optional[List[str]] = None) -> int:
    load_env()
    p = argparse.ArgumentParser(prog="hunter")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="one hunting run")
    r.add_argument("--dry-run", action="store_true", help="print messages, write nothing")
    r.add_argument("--sources", help="comma-separated source names")
    r.add_argument("--data-dir")
    r.add_argument("--docs-dir")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check", help="live fetch, writes nothing")
    c.add_argument("--source")
    c.add_argument("--city")
    c.set_defaults(fn=cmd_check)

    t = sub.add_parser("telegram-setup", help="find the group chat id and save it to .env")
    t.add_argument("--chat-id")
    t.add_argument("--wait", type=int, default=180)
    t.set_defaults(fn=cmd_telegram_setup)

    d = sub.add_parser("dashboard", help="rebuild docs/data.json from state")
    d.add_argument("--data-dir")
    d.add_argument("--docs-dir")
    d.set_defaults(fn=cmd_dashboard)

    b = sub.add_parser("backup", help="Mac backup run: sites GitHub can't reach + GitHub watchdog")
    b.add_argument("--mode", choices=["auto", "all"], help="default: local_backup.mode in config.json")
    b.add_argument("--dry-run", action="store_true", help="print messages, write nothing")
    b.add_argument("--log", help="log file to keep small (used by the launchd job)")
    b.set_defaults(fn=cmd_backup)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
