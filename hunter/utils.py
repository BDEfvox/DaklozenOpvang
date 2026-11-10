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


# --------------------------------------------------------------------------- bedrooms in free text
_NUM_WORDS = {
    "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
_ORDINALS = {
    "tweede": 2, "derde": 3, "vierde": 4, "vijfde": 5, "2e": 2, "3e": 3, "4e": 4, "5e": 5,
    "second": 2, "third": 3, "fourth": 4, "fifth": 5, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
}
_NUM = r"(?P<n>\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_ADJ = (r"(?:ruime|grote|royale|riante|nette|mooie|lichte|volwaardige|goede|flinke|prima|comfortabele|aparte|"
        r"separate|kleine|fijne|spacious|large|big|bright|double|good[- ]sized|generous|nice|comfortable|small)")
_BED = r"(?P<noun>slaapkamers?|bedrooms?)"
# "Aantal slaapkamers: 2", "Number of bedrooms 2"
_BED_LABEL_RE = re.compile(r"(?:aantal|number of)\s+(?:slaapkamers|bedrooms)\s*[:=]?\s*(\d{1,2})\b", re.I)
# "Slaapkamers: 3" (plural only, and not a size such as "Slaapkamers: 4,5 x 3")
_BED_COLON_RE = re.compile(r"(?<![\w-])(?:slaapkamers|bedrooms)\s*[:=]\s*(\d{1,2})\b(?!\s*(?:[.,]\d|x\b|m\b|m2|m²))", re.I)
# "3 slaapkamers", "drie ruime slaapkamers", "2-bedroom apartment"
_BED_COUNT_RE = re.compile(
    r"(?<![\w.,/-])" + _NUM + r"(?P<gap>[\s-]+(?:" + _ADJ + r"(?:\s*,\s*|\s+(?:en|and)\s+|\s+))*|-)" + _BED, re.I)
_RANGE_BEFORE_RE = re.compile(r"(?:\d|" + "|".join(_NUM_WORDS) + r"|een)\s*(?:tot|to|of|or|à|a|en/of|[-–/])\s*$", re.I)
# "tweede slaapkamer", "3e slaapkamer", "third bedroom"
_BED_ORDINAL_RE = re.compile(r"(?<![\w-])(" + "|".join(_ORDINALS) + r")\s+(?:" + _ADJ + r"\s+)?(?:slaapkamer|bedroom)\b", re.I)
# "Slaapkamer 2: ...", "bedroom 3 (8 m²)" (a numbered list of bedrooms, not a size)
_BED_INDEX_RE = re.compile(r"(?:slaapkamer|bedroom)\s+([1-6])\b(?!\s*(?:[.,]\d|x\b|m\b|m2|m²|\d))", re.I)


def bedrooms_from_text(text: Optional[str]) -> Optional[int]:
    """Number of bedrooms a description states, or None if it doesn't say.

    'Aantal slaapkamers: 2' -> 2, 'drie ruime slaapkamers' -> 3, '2-bedroom apartment' -> 2,
    'de tweede slaapkamer' -> 2 (at least). Ranges about a whole building ('1 tot 3 slaapkamers') are ignored.
    With several different numbers the largest wins: descriptions also list bedrooms per floor
    ('3 slaapkamers ... op de eerste verdieping twee slaapkamers').
    """
    if not text:
        return None
    t = unicodedata.normalize("NFKC", str(text)).replace("\xa0", " ")
    labelled = [int(m.group(1)) for m in _BED_LABEL_RE.finditer(t)] + [int(m.group(1)) for m in _BED_COLON_RE.finditer(t)]
    labelled = [n for n in labelled if 0 < n <= 10]
    if labelled:
        return max(labelled)
    found = []
    for m in _BED_COUNT_RE.finditer(t):
        raw = m.group("n").lower()
        n = _NUM_WORDS.get(raw) if not raw.isdigit() else int(raw)
        if not n or n > 10:
            continue
        if _RANGE_BEFORE_RE.search(t[max(0, m.start() - 12):m.start()]):
            continue  # "1 tot 3 slaapkamers": the homes in this building differ
        plural = m.group("noun").lower().endswith("s")
        adjacent = not re.search(r"[a-z]", m.group("gap"), re.I)
        # "2 slaapkamer appartement" / "3 bedroom house" are fine; "2 ruime slaapkamer" is not a count
        if n > 1 and not plural and not adjacent:
            continue
        if n == 1 and plural:
            continue
        found.append(n)
    found += [_ORDINALS[m.group(1).lower()] for m in _BED_ORDINAL_RE.finditer(t)]
    found += [int(m.group(1)) for m in _BED_INDEX_RE.finditer(t)]
    return max(found) if found else None
