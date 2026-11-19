"""Builds docs/data.json (schema in DESIGN.md) from state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

from .config import ROOT
from .filters import capacity, places_available, price_basis, rent_basis
from .geo import parse_address
from .models import CITIES
from .notify.format import FLAG_LABELS
from .state import State, atomic_write_json, iso, parse_iso

LISTING_FIELDS = ("uid", "source", "url", "title", "city", "address", "postcode", "price_total",
                  "price_includes_utilities", "bedrooms", "rooms", "area_m2", "property_type",
                  "furnished", "available_from", "available_until", "image_url")


# What kind of thing a flag says, for the page's marks: "warn" (a condition that can rule the home out),
# "check" (the listing leaves something open: ask before you count on it), "good", or "info" (a plain fact).
FLAG_KINDS: Dict[str, str] = {
    "no_sharers": "warn", "no_students": "warn", "working_only": "warn", "no_registration": "warn",
    "suspicious_price": "warn", "scam_signals": "warn", "rooms_only": "warn",
    "sharing_unclear": "check", "places_unclear": "check", "rent_unclear": "check", "age_unclear": "check",
    "bedrooms_unknown": "check", "bedrooms_unconfirmed": "check", "price_unknown": "check",
    "sharers_ok": "good", "students_ok": "good", "utilities_included": "good", "apply_together": "good",
}


def flag_kinds() -> Dict[str, str]:
    return {name: FLAG_KINDS.get(name, "info") for name in FLAG_LABELS}


def home_key(listing: Dict[str, Any], uid: str) -> str:
    """One name for a home across sites and re-listings, so that a home someone has ruled out stays ruled out
    when it comes back under another id. Only an address with a house number names a home: a street name
    alone can be several homes, and those keep the listing's own id."""
    parts = parse_address(listing.get("address"))
    # "2e De Riemerstraat" has a digit and no house number: a street, where several homes can be for rent
    if parts["street"] and parts["number"] and listing.get("city"):
        street = re.sub(r"[^a-z0-9]", "", parts["street"].lower())
        # "85-R", "85 R" and "85r" are one number; "10-1" (a floor) is not "101"
        number = re.sub(r"[^a-z0-9]+", "-", parts["number"].lower()).strip("-")
        number = re.sub(r"(?<=\d)-(?=[a-z])", "", number)
        if len(street) >= 3 and number:
            return "a:%s:%s:%s" % (listing["city"], street, number)
    return "u:" + uid


def _source_rows(state: State, sources: Dict[str, Any]) -> List[Dict[str, Any]]:
    names = set(state.health)
    rows = []
    for name in sorted(names):
        h = state.health.get(name, {})
        src = sources.get(name)
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
    sources = _safe_sources()
    labels = {s.name: s.label for s in sources.values()}
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
        item["flags"] = flags = [f for f in e.get("flags", []) if isinstance(f, str)]
        item["home_key"] = home_key(l, uid)
        # what the listed rent buys, and whether costs come on top of it (see filters.py)
        item["rent_basis"] = rent_basis(flags)
        item["price_basis"] = price_basis(flags, l.get("price_includes_utilities"))
        item["capacity"] = capacity(flags)                  # people the home houses, if the listing says
        item["places_available"] = places_available(flags)  # places that can be rented now, if it says
        geo = e.get("geo") if isinstance(e.get("geo"), dict) else {}
        ok = all(type(geo.get(k)) in (int, float) for k in ("lat", "lon"))
        item["lat"] = round(geo["lat"], 5) if ok else None
        item["lon"] = round(geo["lon"], 5) if ok else None
        item["geo"] = geo.get("precision") if ok and geo.get("precision") in ("address", "street", "postcode") else None
        # flag name -> the listing's own sentence behind it (third-party text: the page renders it as text only)
        evidence = e.get("evidence")
        item["evidence"] = {k: v for k, v in evidence.items() if isinstance(v, str)} if isinstance(evidence, dict) else {}
        listings.append(item)
    listings.sort(key=lambda x: x.get("first_seen") or "", reverse=True)
    last_run = state.meta.get("last_run") or iso(now)
    household = cfg.get("household") if isinstance(cfg.get("household"), dict) else {}
    persons = household.get("persons") if type(household.get("persons")) is int else None
    if persons is None and len(cfg.get("searches", [])) == 1:
        persons = cfg["searches"][0]["persons"]
    return {
        "schema": 2,
        "generated_at": iso(now),
        "last_run": last_run,
        "dashboard_url": cfg.get("dashboard_url", ""),
        "cities": {c: CITIES.get(c, c) for c in cfg.get("cities", [])},
        "searches": [{"name": s["name"], "persons": s["persons"], "max_total": s["max_total"],
                      "great_total": s["great_total"]} for s in cfg.get("searches", [])],
        "household": {"persons": persons},
        "flag_labels": dict(FLAG_LABELS),
        "flag_kinds": flag_kinds(),
        "reply_template": cfg.get("reply_template", ""),
        "sources": _source_rows(state, sources),
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
