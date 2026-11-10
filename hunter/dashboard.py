"""Builds docs/data.json (schema in DESIGN.md) from state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ROOT
from .models import CITIES
from .notify.format import FLAG_LABELS
from .state import State, atomic_write_json, iso, parse_iso

LISTING_FIELDS = ("uid", "source", "url", "title", "city", "address", "postcode", "price_total",
                  "price_includes_utilities", "bedrooms", "rooms", "area_m2", "property_type",
                  "furnished", "available_from", "available_until", "image_url")


def _source_rows(state: State) -> List[Dict[str, Any]]:
    labels: Dict[str, Any] = {}
    try:
        from .sources import all_sources
        labels = all_sources()
    except Exception:  # pragma: no cover
        pass
    names = set(state.health)
    rows = []
    for name in sorted(names):
        h = state.health.get(name, {})
        src = labels.get(name)
        rows.append({
            "name": name,
            "label": h.get("label") or (src.label if src else None) or name,
            "homepage": h.get("homepage") or (src.homepage if src else ""),
            "status": h.get("status", "disabled" if not h else "ok"),
            "last_ok": h.get("last_ok"),
            "last_error": h.get("last_error"),
            "error": h.get("error"),
            "note": h.get("note") if h.get("note") is not None else (src.note if src else ""),
            "listings_last_run": h.get("listings_last_run"),
        })
    return rows


def build_data(cfg: Dict[str, Any], state: State, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=int(cfg.get("keep_listings_days", 30)))
    labels = {s.name: s.label for s in _safe_sources().values()}
    listings = []
    for uid, e in state.matches.items():
        last = parse_iso(e.get("last_seen"))
        if last is not None and last < cut:
            continue
        l = e.get("listing", {})
        item = {k: l.get(k) for k in LISTING_FIELDS}
        item["uid"] = uid
        item["source_label"] = labels.get(l.get("source"), l.get("source"))
        item["first_seen"] = e.get("first_seen")
        item["last_seen"] = e.get("last_seen")
        item["price_drop_from"] = e.get("price_drop_from")
        item["best_tier"] = e.get("best_tier")
        item["matches"] = e.get("matches", [])
        item["flags"] = e.get("flags", [])
        listings.append(item)
    listings.sort(key=lambda x: x.get("first_seen") or "", reverse=True)
    last_run = state.meta.get("last_run") or iso(now)
    return {
        "schema": 1,
        "generated_at": iso(now),
        "last_run": last_run,
        "dashboard_url": cfg.get("dashboard_url", ""),
        "cities": {c: CITIES.get(c, c) for c in cfg.get("cities", [])},
        "searches": [{"name": s["name"], "persons": s["persons"], "max_total": s["max_total"],
                      "great_total": s["great_total"]} for s in cfg.get("searches", [])],
        "flag_labels": dict(FLAG_LABELS),
        "reply_template": cfg.get("reply_template", ""),
        "sources": _source_rows(state),
        "listings": listings,
    }


def _safe_sources() -> Dict[str, Any]:
    try:
        from .sources import all_sources
        return all_sources()
    except Exception:  # pragma: no cover
        return {}


def write_dashboard(cfg: Dict[str, Any], state: State, docs_dir: Optional[str] = None,
                    now: Optional[datetime] = None) -> Path:
    path = (Path(docs_dir) if docs_dir else ROOT / "docs") / "data.json"
    atomic_write_json(path, build_data(cfg, state, now))
    return path
