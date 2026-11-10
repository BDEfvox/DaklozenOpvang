"""Small parsing helpers shared by sources."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

_CITY_ALIASES = {
    "den-haag": ("den haag", "'s-gravenhage", "s-gravenhage", "the hague", "hague", "'s gravenhage"),
    "amsterdam": ("amsterdam",),
    "leiden": ("leiden",),
}


def city_slug(name: Optional[str]) -> Optional[str]:
    """'s-Gravenhage / The Hague / Den Haag -> 'den-haag'. Unknown -> None."""
    if not name:
        return None
    n = unicodedata.normalize("NFKC", name).strip().lower().replace("’", "'")
    for slug, aliases in _CITY_ALIASES.items():
        if n == slug or n in aliases:
            return slug
    return None


def parse_price(text: Optional[str]) -> Optional[int]:
    """'€ 1.250,- p/m' -> 1250, '€1,250.00' -> 1250, '1250' -> 1250. No digits -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(round(text))
    t = str(text).replace("\xa0", " ")
    m = re.search(r"(\d{1,3}(?:[.,\s]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)", t)
    if not m:
        return None
    whole = re.sub(r"[.,\s]", "", m.group(1))
    try:
        return int(whole)
    except ValueError:
        return None


def parse_int(text: Optional[str]) -> Optional[int]:
    """'3 kamers' -> 3, '75 m²' -> 75. No digits -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(r"\d+", str(text))
    return int(m.group()) if m else None


def clean_text(text: Optional[str], limit: int = 4000) -> Optional[str]:
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    return t[:limit] or None
