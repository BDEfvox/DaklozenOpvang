"""Decides whether a listing matches a search, which tier, and which flags apply."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .models import Listing


@dataclass
class SearchMatch:
    search: str
    persons: int
    price_pp: Optional[int]
    tier: str  # "great" | "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {"search": self.search, "persons": self.persons,
                "price_pp": self.price_pp, "tier": self.tier}


@dataclass
class MatchResult:
    matches: List[SearchMatch] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    @property
    def best_tier(self) -> Optional[str]:
        if not self.matches:
            return None
        return "great" if any(m.tier == "great" for m in self.matches) else "ok"

    @property
    def best_price_pp(self) -> Optional[int]:
        vals = [m.price_pp for m in self.matches if m.price_pp is not None]
        return min(vals) if vals else None


def _contains_any(text: str, phrases: List[str]) -> bool:
    # Whole words only: "geen inschrijving" must not match "geen inschrijvingskosten".
    return any(re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", text) for p in phrases if p)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def detect_flags(listing: Listing, cfg: Dict[str, Any]) -> List[str]:
    text = ((listing.title or "") + " \n " + (listing.description or "")).lower()
    fl = cfg.get("flags", {})
    out: List[str] = []
    for key in ("no_sharers", "no_students", "sharers_ok", "no_registration",
                "income_requirement", "scam_signals", "rooms_only"):
        if _contains_any(text, fl.get(key, [])):
            out.append(key)
    # max persons
    nums: List[int] = []
    for pat in cfg.get("max_persons_patterns", []):
        for m in re.finditer(pat, text):
            try:
                nums.append(int(m.group(1)))
            except (IndexError, ValueError):
                pass
    for pat in cfg.get("couple_patterns", []):
        if re.search(pat, text):
            nums.append(2)
    for pat in cfg.get("single_patterns", []):
        if re.search(pat, text):
            nums.append(1)
    mp = listing.extra.get("max_persons") if isinstance(listing.extra, dict) else None
    if isinstance(mp, int) and mp > 0:  # structured value from the site (e.g. Kamernet "suitable for N persons")
        nums.append(mp)
    if nums:
        out.append("max_persons:%d" % min(nums))
    if "no_sharers" in out and "sharers_ok" in out:  # "niet geschikt voor woningdelers" contains the positive phrase
        out.remove("sharers_ok")
    # short term
    a, b = _parse_date(listing.available_from), _parse_date(listing.available_until)
    if a and b and (b - a).days < 365:
        out.append("short_term")
    if listing.price_includes_utilities:
        out.append("utilities_included")
    return out


def _effective_bedrooms(listing: Listing) -> Optional[int]:
    if listing.bedrooms is not None:
        return listing.bedrooms
    if listing.rooms is not None:
        return max(listing.rooms - 1, 0)
    return None


def evaluate(listing: Listing, cfg: Dict[str, Any]) -> MatchResult:
    flags = detect_flags(listing, cfg)
    res = MatchResult(flags=flags)
    if listing.city not in cfg.get("cities", []):
        return res
    if (listing.property_type or "") in cfg.get("exclude_types", []):
        return res
    if "rooms_only" in flags:  # "alleen nog losse kamers": you can't rent the whole home together
        return res

    price = listing.price_total
    searches = list(cfg.get("searches", []))
    if not searches:
        return res
    prefiltered = bool(listing.extra.get("site_prefiltered"))
    all_unknown = (listing.bedrooms is None and listing.rooms is None and listing.area_m2 is None)

    extra_flags: List[str] = []
    if price is None:
        if not prefiltered:
            return res
        searches = [min(searches, key=lambda s: s["max_total"])]
        extra_flags.append("price_unknown")

    eff_bed = _effective_bedrooms(listing)
    susp = cfg.get("suspicious_total_below")
    if price is not None and susp and price < susp and (eff_bed is None or eff_bed >= 2):
        flags.append("suspicious_price")

    max_persons: Optional[int] = None
    for f in flags:
        if f.startswith("max_persons:"):
            max_persons = int(f.split(":", 1)[1])
    no_sharers = "no_sharers" in flags

    used_unknown = False
    for s in searches:
        persons = int(s["persons"])
        if price is not None and price > s["max_total"]:
            continue
        if eff_bed is not None:
            if eff_bed < s["min_bedrooms"]:
                continue
        elif all_unknown and prefiltered:
            used_unknown = True
        elif listing.area_m2 is not None:
            if listing.area_m2 < s.get("min_area_if_unknown", 0):
                continue
            used_unknown = True
        else:
            continue
        if persons >= 3 and no_sharers:
            continue
        if max_persons is not None and max_persons < persons:
            continue
        pp = int(round(price / persons)) if price is not None else None
        tier = "great" if (price is not None and price <= s["great_total"]) else "ok"
        res.matches.append(SearchMatch(s["name"], persons, pp, tier))

    if res.matches:
        if used_unknown:
            extra_flags.append("bedrooms_unknown")
        for f in extra_flags:
            if f not in flags:
                flags.append(f)
    return res
