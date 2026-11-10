"""State files under data/: seen.json, matches.json, health.json, meta.json.

Writes are atomic and only happen when something besides pure timestamps changed,
or when the last write is older than `heartbeat_minutes`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ROOT

FILES = ("seen", "matches", "health", "meta")
# Keys ignored when deciding whether state "changed".
TIMESTAMP_KEYS = {"last_seen", "last_ok", "last_error", "last_run", "last_write", "notified_at"}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in TIMESTAMP_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _signature(data: Dict[str, Any]) -> str:
    return json.dumps(_strip(data), sort_keys=True, default=str)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(str(tmp), str(path))


class State:
    def __init__(self, data_dir: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> None:
        self.dir = Path(data_dir) if data_dir else ROOT / "data"
        self.cfg = cfg or {}
        self.seen: Dict[str, Dict[str, Any]] = {}
        self.matches: Dict[str, Dict[str, Any]] = {}
        self.health: Dict[str, Dict[str, Any]] = {}
        self.meta: Dict[str, Any] = {}
        self._orig_sig = ""
        self.load()

    # ---- io
    def _read(self, name: str) -> Dict[str, Any]:
        p = self.dir / (name + ".json")
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self) -> None:
        self.seen = self._read("seen")
        self.matches = self._read("matches")
        self.health = self._read("health")
        self.meta = self._read("meta")
        self._orig_sig = self._sig()

    def _sig(self) -> str:
        return _signature({"seen": self.seen, "matches": self.matches,
                           "health": self.health, "meta": self.meta})

    def changed(self) -> bool:
        return self._sig() != self._orig_sig

    def heartbeat_due(self, now: datetime) -> bool:
        last = parse_iso(self.meta.get("last_write"))
        if last is None:
            return True
        minutes = float(self.cfg.get("heartbeat_minutes", 60))
        return now - last >= timedelta(minutes=minutes)

    def should_write(self, now: datetime) -> bool:
        return self.changed() or self.heartbeat_due(now)

    def prune(self, now: datetime) -> None:
        seen_cut = now - timedelta(days=int(self.cfg.get("prune_seen_days", 45)))
        for uid in [u for u, e in self.seen.items()
                    if (parse_iso(e.get("last_seen")) or now) < seen_cut]:
            del self.seen[uid]
        keep_cut = now - timedelta(days=int(self.cfg.get("keep_listings_days", 30)))
        for uid in [u for u, e in self.matches.items()
                    if (parse_iso(e.get("last_seen")) or now) < keep_cut]:
            del self.matches[uid]
        counters = self.meta.get("new_matches", {})
        for day in sorted(counters)[:-14]:
            del counters[day]

    def save(self, now: datetime, force: bool = False) -> bool:
        """Write all files if due. Returns True when files were written."""
        self.prune(now)
        if not (force or self.should_write(now)):
            return False
        self.meta["last_write"] = iso(now)
        for name in FILES:
            atomic_write_json(self.dir / (name + ".json"), getattr(self, name))
        self._orig_sig = self._sig()
        return True

    # ---- helpers
    def source_has_seen(self, source: str) -> bool:
        prefix = source + ":"
        return any(u.startswith(prefix) for u in self.seen)

    def count_new_match(self, day: str, city: str) -> None:
        c = self.meta.setdefault("new_matches", {}).setdefault(day, {})
        c[city] = c.get(city, 0) + 1

    def find_duplicate(self, fingerprint: str, source: str, now: datetime, days: int = 14) -> Optional[str]:
        """uid of the same property already seen via another source within `days`."""
        cut = now - timedelta(days=days)
        for uid, e in self.seen.items():
            if (e.get("fp") == fingerprint and e.get("source") != source and e.get("matched")
                    and not e.get("dup_of")):
                if (parse_iso(e.get("last_seen")) or cut) >= cut:
                    return uid
        return None
