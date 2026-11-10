"""Mac backup: covers the sites the GitHub checker can't reach, and warns when GitHub stops running.

Off by default; turned on with ./local-backup.sh on (a launchd job, every 10 minutes while the Mac is awake).

Modes
- auto (default): read the GitHub checker's data/health.json, meta.json and seen.json from the public repo.
  Run locally only the sites GitHub reports as blocked or broken, so nothing is alerted twice. Listings GitHub has
  already seen are known locally too. If GitHub hasn't saved anything for `stale_hours`, send one warning (and one
  "running again" message when it recovers). Never sends a daily digest: GitHub does that.
- all: run every site locally, for when you don't use GitHub at all. Don't use it while GitHub runs too, or
  you'll get every alert twice.
Local state lives in data-local/ (gitignored), separate from the GitHub checker's data/.
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from .config import ROOT
from .notify.format import e
from .notify.telegram import ConsoleNotifier, Notifier, make_notifier
from .state import State, iso, parse_iso

TAG = "💻 <i>Mac backup</i>"
LOG_MAX_BYTES = 2_000_000
LOG_KEEP_BYTES = 200_000


class TaggedNotifier(Notifier):
    """Marks every message so you can tell the Mac's alerts from GitHub's."""

    def __init__(self, inner: Notifier, tag: str = TAG) -> None:
        self.inner = inner
        self.tag = tag

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None) -> bool:
        return self.inner.send(self.tag + "\n" + text, url=url, dashboard_url=dashboard_url)


def parse_repo(url: Optional[str]) -> Optional[str]:
    """https://github.com/owner/repo(.git) or git@github.com:owner/repo.git -> 'owner/repo'."""
    m = re.search(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", (url or "").strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def detect_repo(cfg: Dict[str, Any]) -> Optional[str]:
    configured = (cfg.get("local_backup") or {}).get("github_repo")
    if configured:
        return configured
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return parse_repo(out.stdout) if out.returncode == 0 else None


def raw_url(repo: str, path: str) -> str:
    return "https://raw.githubusercontent.com/%s/main/%s" % (repo, path)


def fetch_json(url: str) -> Any:
    resp = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"})
    resp.raise_for_status()
    return resp.json()


def choose_sources(health: Dict[str, Any], available: List[str]) -> List[str]:
    """Sites the GitHub checker reports as blocked or broken (and that exist here)."""
    return sorted(n for n, h in (health or {}).items()
                  if n in available and isinstance(h, dict) and h.get("status") in ("blocked", "error"))


def github_stale(meta: Dict[str, Any], now: datetime, hours: float) -> bool:
    last = parse_iso((meta or {}).get("last_run"))
    return last is None or now - last > timedelta(hours=hours)


def seed_seen(state: State, gh_seen: Dict[str, Any], now_s: str) -> int:
    """Copy listings the GitHub checker already knows into local memory, so they aren't alerted again."""
    added = 0
    for uid, entry in (gh_seen or {}).items():
        if uid in state.seen or not isinstance(entry, dict):
            continue
        local = {k: entry[k] for k in ("first_seen", "last_seen", "last_price", "matched", "notified_at",
                                       "source", "fp", "dup_of", "details") if k in entry}
        local.setdefault("first_seen", now_s)
        local.setdefault("last_seen", now_s)
        local.setdefault("source", uid.split(":", 1)[0])
        local["from_github"] = True
        state.seen[uid] = local
        added += 1
    return added


def trim_log(path: str) -> None:
    """Keep the launchd log small: once it passes ~2 MB, keep only the last ~200 KB."""
    try:
        if os.path.getsize(path) <= LOG_MAX_BYTES:
            return
        with open(path, "rb") as fh:
            fh.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            tail = fh.read()
        with open(path, "wb") as fh:
            fh.write(b"[log trimmed]\n" + tail)
    except OSError:
        pass


def _down_message(last_run: Optional[str], repo: str) -> str:
    when = last_run or "never"
    return ("⚠️ <b>The GitHub checker hasn't saved anything since %s (UTC).</b>\n"
            "It may be switched off or failing. Open %s and look at the <b>hunt</b> workflow; "
            "if it says it was disabled, click <b>Enable workflow</b>.") % (
        e(when), e("https://github.com/%s/actions" % repo))


def run_backup(cfg: Dict[str, Any], mode: str = "auto", dry_run: bool = False, now: Optional[datetime] = None,
               fetch: Callable[[str], Any] = fetch_json, notifier: Optional[Notifier] = None,
               http: Any = None, sources: Optional[Dict[str, Any]] = None, repo: Optional[str] = None,
               data_dir: Optional[str] = None) -> int:
    from .runner import run_once

    now = now or datetime.now(timezone.utc)
    lb = cfg.get("local_backup") or {}
    data_dir = data_dir or str(ROOT / lb.get("data_dir", "data-local"))
    docs_dir = os.path.join(data_dir, "docs")
    local_cfg = copy.deepcopy(cfg)
    local_cfg.setdefault("telegram", {})["digest"] = False  # GitHub sends the daily digest
    state = State(data_dir, local_cfg)
    inner = notifier or (ConsoleNotifier() if dry_run else make_notifier(cfg))
    tagged = TaggedNotifier(inner)
    if sources is None:
        from .sources import all_sources
        sources = all_sources()
    if http is None:
        from .http import Http
        http = Http()

    only: Optional[List[str]] = None
    if mode == "auto":
        repo = repo or detect_repo(cfg)
        if not repo:
            print("[backup] no GitHub repo found yet (run ./go-live.sh first, or use --mode all)")
            return 0
        try:
            health = fetch(raw_url(repo, "data/health.json"))
            meta = fetch(raw_url(repo, "data/meta.json"))
        except Exception as exc:
            print("[backup] could not read the GitHub checker's state from %s (%s); nothing to do"
                  % (repo, type(exc).__name__))
            return 0

        if github_stale(meta, now, float(lb.get("stale_hours", 3))):
            if not state.meta.get("watchdog_alerted") and tagged.send(_down_message(meta.get("last_run"), repo)):
                state.meta["watchdog_alerted"] = True
        elif state.meta.get("watchdog_alerted"):
            if tagged.send("✅ The GitHub checker is running again."):
                state.meta["watchdog_alerted"] = False

        disabled = set(cfg.get("disabled_sources", []))
        only = choose_sources(health, [n for n in sources if n not in disabled])
        if not only:
            print("[backup] GitHub reaches every site; nothing to run here")
            if not dry_run:
                state.save(now)
            return 0
        try:
            n = seed_seen(state, fetch(raw_url(repo, "data/seen.json")), iso(now))
            print("[backup] knows %d listings from GitHub's memory" % n)
        except Exception as exc:
            print("[backup] could not read GitHub's seen.json (%s); running anyway" % type(exc).__name__)
        print("[backup] running here: %s" % ", ".join(only))
    elif mode != "all":
        print("[backup] unknown mode %r (use auto or all)" % mode)
        return 2

    return run_once(local_cfg, state, sources, tagged, http, now=now, only=only, write=not dry_run,
                    docs_dir=docs_dir)
