"""Loads config.json (keys starting with '_' are ignored)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "cities": ["den-haag", "amsterdam", "leiden"],
    "searches": [],
    "exclude_types": ["room", "studio", "parking", "other"],
    "suspicious_total_below": 700,
    "disabled_sources": [],
    "telegram": {"quiet_hours": [23, 7], "max_messages_per_run": 15, "first_run_max": 10,
                 "digest_hour": 9, "timezone": "Europe/Amsterdam"},
    "detail_fetch_max_per_run": 10,
    "health_alert_after_failures": 6,
    "keep_listings_days": 30,
    "prune_seen_days": 45,
    "heartbeat_minutes": 60,
    "dashboard_url": "",
    "reply_template": "",
    "flags": {},
    "max_persons_patterns": [],
    "couple_patterns": [],
    "single_patterns": [],
}


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


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
    env_url = os.environ.get("DASHBOARD_URL", "").strip()
    if env_url:
        cfg["dashboard_url"] = env_url
    return cfg
