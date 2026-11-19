"""Loads config.json (keys starting with '_' are ignored)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .models import CITIES

ROOT = Path(__file__).resolve().parent.parent
# Who is looking can be kept out of the (public) repository: the HOUSEHOLD environment variable (a JSON object,
# on GitHub a repository secret) or this git-ignored file replaces config.json's "household".
HOUSEHOLD_PATH: Optional[Path] = ROOT / "household.json"

DEFAULTS: Dict[str, Any] = {
    "cities": list(CITIES),
    "searches": [],
    "exclude_types": ["room", "studio", "parking", "other"],
    "suspicious_total_below": 700,
    "suspicious_rent_per_m2_below": 6,
    "disabled_sources": [],
    "telegram": {"quiet_hours": [23, 7], "max_messages_per_run": 15, "first_run_max": 10,
                 "digest_hour": 9, "timezone": "Europe/Amsterdam"},
    "detail_fetch_max_per_run": 10,
    "geo_lookups_per_run": 0,
    "health_alert_after_failures": 6,
    "keep_listings_days": 30,
    "prune_seen_days": 45,
    "heartbeat_minutes": 60,
    "dashboard_url": "",
    "reply_template": "",
    "flags": {},
    "max_persons_patterns": [],
    "income_patterns": [],
    "couple_patterns": [],
    "single_patterns": [],
    "tenant_rules": {},
    "household": {"persons": None, "occupation": None, "ages": [], "sharers": False},
}


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _private_household() -> Optional[Dict[str, Any]]:
    """The household from the HOUSEHOLD variable or household.json, or None when neither holds a JSON object.
    A value that cannot be read is ignored: every home is then shown, none is lost."""
    raw = os.environ.get("HOUSEHOLD", "").strip()
    if not raw and HOUSEHOLD_PATH is not None:
        try:
            raw = HOUSEHOLD_PATH.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raw = ""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return _strip(value) if isinstance(value, dict) else None


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path) if path else ROOT / "config.json"
    with open(p, encoding="utf-8") as fh:
        raw = _strip(json.load(fh))
    cfg: Dict[str, Any] = json.loads(json.dumps(DEFAULTS))
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    household = _private_household()
    if household is not None:
        merged = dict(DEFAULTS["household"])
        merged.update(household)
        cfg["household"] = merged
        cfg["household_private"] = True
    env_url = os.environ.get("DASHBOARD_URL", "").strip()
    if env_url:
        cfg["dashboard_url"] = env_url
    return cfg
