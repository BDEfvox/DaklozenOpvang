"""Small parsing helpers shared by sources."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, List, Optional

from .models import CITIES

_HAGUE_ALIASES = ("den haag", "'s-gravenhage", "s-gravenhage", "the hague", "hague", "'s gravenhage",
                  # districts of Den Haag that sites use as the "city"; Rijswijk, Voorburg, Leidschendam and
                  # Wassenaar are separate municipalities and stay out
                  "scheveningen", "loosduinen", "kijkduin", "ypenburg", "leidschenveen")


def city_slug(name: Optional[str]) -> Optional[str]:
    """'s-Gravenhage / The Hague / Den Haag -> 'den-haag'. Unknown -> None."""
    if not name:
        return None
    n = unicodedata.normalize("NFKC", name).strip().lower().replace("’", "'")
    if n in _HAGUE_ALIASES:
        return "den-haag"
    return n if n in CITIES else None


def parse_price(text: Optional[str]) -> Optional[int]:
    """'€ 1.250,- p/m' -> 1250, '€1,250.00' -> 1250, '1250' -> 1250, '1.248,55' -> 1249. No digits -> None."""
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
        euros = int(whole)
    except ValueError:
        return None
    cents = m.group(2)
    if cents and int(cents.ljust(2, "0")) >= 50:
        euros += 1                    # "1228.55" is shown as € 1.229 on the site: round, don't cut
    return euros


def parse_int(text: Optional[str]) -> Optional[int]:
    """'3 kamers' -> 3, '75 m²' -> 75. No digits -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(r"\d+", str(text))
    return int(m.group()) if m else None


# A description as it is read for flags. Conditions ("geen woningdelers", "alleen voor studenten") often stand
# at the very end of a long text, so the reading limit is generous; what is saved is cut later (see runner.py).
TEXT_MAX = 20000


def clean_text(text: Optional[str], limit: int = TEXT_MAX, keep_lines: bool = False) -> Optional[str]:
    """One space between words. With `keep_lines` a line break in the listing's text stays a line break (one "\n"),
    so the flag rules can tell "Huisdieren: geen" / "Studenten welkom" (two lines) from "geen studenten"."""
    if not text:
        return None
    if keep_lines:
        t = re.sub(r"[^\S\n]+", " ", str(text).replace("\r\n", "\n").replace("\r", "\n"))
        t = re.sub(r" ?\n[ \n]*", "\n", t).strip()
    else:
        t = re.sub(r"\s+", " ", str(text)).strip()
    return (t[:limit].rstrip("\n") if keep_lines else t[:limit]) or None


_BLOCK_TAGS = frozenset(("p", "div", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section",
                         "article", "blockquote", "dd", "dt", "dl", "pre", "header", "footer", "td", "th"))
_SKIP_TAGS = frozenset(("script", "style", "noscript", "template"))


def block_text(node: Any) -> str:
    """The visible text of a BeautifulSoup element: its pieces joined by a space (as get_text(" ", strip=True)
    does), with a line break where the page breaks the line: at <br> and around paragraphs, list items and other
    blocks. Pass the result to clean_text(..., keep_lines=True)."""
    parts: List[str] = []

    def walk(el: Any) -> None:
        for child in getattr(el, "children", []):
            name = getattr(child, "name", None)
            if name is None:
                if type(child).__name__ in ("NavigableString", "CData"):
                    piece = str(child).strip()
                    if piece:
                        parts.append(piece)
            elif name == "br":
                parts.append("\n")
            elif name not in _SKIP_TAGS:
                block = name in _BLOCK_TAGS
                if block:
                    parts.append("\n")
                walk(child)
                if block:
                    parts.append("\n")

    walk(node)
    return " ".join(parts)


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
# "Slaapkamers: 3" (plural only; not a size such as "Slaapkamers: 4,5 x 3", not "Bedrooms: 3 rooms in total")
_BED_COLON_RE = re.compile(r"(?<![\w-])(?:slaapkamers|bedrooms)\s*[:=]\s*(\d{1,2})\b"
                           r"(?!\s*(?:[.,]\d|x\b|m\b|m2|m²|rooms?\b|kamers?\b|ruimtes?\b))", re.I)
# "3 slaapkamers", "drie ruime slaapkamers", "2-bedroom apartment"
_BED_COUNT_RE = re.compile(
    r"(?<![\w.,/-])" + _NUM + r"(?P<gap>[\s-]+(?:" + _ADJ + r"(?:\s*,\s*|\s+(?:en|and)\s+|\s+))*|-)" + _BED, re.I)
# What the home had, could have or does not have: "voorheen 2 slaapkamers", "mogelijkheid tot 2 slaapkamers",
# "geen 2 slaapkamers maar 1". Same line only: a list item above the count says nothing about it.
_NOT_THE_COUNT_RE = re.compile(
    r"(?:\b(?:voorheen|vroeger|voormalige?|oorspronkelijk|previously|formerly|originally|eventueel|evt\.?|potentieel|"
    r"potentially|mogelijkheid|mogelijkheden|optie|possibility|option|convertible|te\s+(?:maken|creëren|realiseren))\b"
    r"(?:[ \t]+[\w'’-]+){0,4}[ \t]+|\b(?:geen|niet|no|not)[ \t]+)$", re.I)
_RANGE_BEFORE_RE = re.compile(r"(?:\d|" + "|".join(_NUM_WORDS) + r"|een)\s*(?:tot|to|of|or|à|a|en/of|[-–/])\s*$", re.I)
# "tweede slaapkamer", "3e slaapkamer", "third bedroom"
_BED_ORDINAL_RE = re.compile(r"(?<![\w-])(" + "|".join(_ORDINALS) + r")\s+(?:" + _ADJ + r"\s+)?(?:slaapkamer|bedroom)\b", re.I)
# "Slaapkamer 2: ...", "bedroom 3 (8 m²)" (a numbered list of bedrooms, not a size)
_BED_INDEX_RE = re.compile(r"(?:slaapkamer|bedroom)\s+([1-6])\b(?!\s*(?:[.,]\d|x\b|m\b|m2|m²|\d))", re.I)


# "2-BR apartment", "3BR", "2 bdrm"
_BED_ABBR_RE = re.compile(r"(?<![\w.,/-])([1-9])[- ]?(?:BR|[Bb]drm?s?)\b")
# Bedrooms described one by one or floor by floor, each with its size:
# "Slaapkamer van circa. 13 m². ... Drie slaapkamers van circa. 10 m², 9 m², 6 m²." is four bedrooms.
_SIZE = r"(?:circa\.?|ca\.?|ongeveer|approx(?:imately|\.)?|about|±)?\s*\d{1,2}(?:[.,]\d)?\s*(?:m²|m2|vierkante\s+meter|sqm|square\s+met(?:er|re)s?)"
_BED_SIZED_RE = re.compile(
    r"(?<![\w-])(?:(?P<n>\d|" + "|".join(_NUM_WORDS) + r")\s+)?(?:" + _ADJ + r"\s+)?(?P<noun>slaapkamers?|bedrooms?)\s+"
    r"(?:van|of|met|with)\s+(?P<sizes>" + _SIZE + r"(?:\s*(?:,|en|and|&)\s*" + _SIZE + r")*)", re.I)


def _sized_bedrooms(t: str) -> Optional[int]:
    """Bedrooms counted from mentions that give a size. Each language is counted on its own (a text in Dutch and
    English describes every room twice), the same mention twice counts once, and a single bedroom whose size also
    stands in a list ("twee slaapkamers van 12 m² en 9 m²" ... "de slaapkamer van 12 m²") is one of that list."""
    best = 0
    for dutch in (True, False):
        seen, listed, singles = set(), set(), []
        total = 0
        for m in _BED_SIZED_RE.finditer(t):
            noun = m.group("noun").lower()
            if noun.startswith("slaap") != dutch:
                continue
            sizes = tuple(re.findall(r"(\d{1,2}(?:[.,]\d)?)\s*(?:m²|m2|vierkante|sqm|square)", m.group("sizes"), re.I))
            raw = (m.group("n") or "").lower()
            n = (int(raw) if raw.isdigit() else _NUM_WORDS.get(raw)) if raw else None
            plural = noun.endswith("s")
            if n is None:
                n = len(sizes) if plural else 1
            if (n > 1) != plural or n > 6 or (plural and len(sizes) not in (1, n)):
                continue  # "2 slaapkamer van", "2 slaapkamers van 12 m², 9 m² en 7 m²": count and sizes disagree
            if _RANGE_BEFORE_RE.search(t[max(0, m.start() - 12):m.start()]) or \
                    re.match(r"\s*(?:tot|to|-|–)\s*\d", t[m.end():m.end() + 8]):
                continue  # "1 tot 3 slaapkamers van 10 m² tot 16 m²": the homes of a building, not one home
            key = (n, sizes)
            if key in seen:
                continue
            seen.add(key)
            if plural:
                if listed.intersection(sizes):
                    continue  # "3 slaapkamers van 12, 10 en 8 m². Op de eerste verdieping 2 slaapkamers van 12 en 10 m²"
                listed.update(sizes)
                total += n
            else:
                singles.append(sizes[0])
        total += len({x for x in singles if x not in listed})
        best = max(best, total)
    return best or None


def bedrooms_from_text(text: Optional[str]) -> Optional[int]:
    """Number of bedrooms a description states, or None if it doesn't say.

    'Aantal slaapkamers: 2' -> 2, 'drie ruime slaapkamers' -> 3, '2-bedroom apartment' -> 2,
    'de tweede slaapkamer' -> 2 (at least). Ranges about a whole building ('1 tot 3 slaapkamers') are ignored.
    With several different numbers the largest wins: descriptions also list bedrooms per floor
    ('3 slaapkamers ... op de eerste verdieping twee slaapkamers'). Bedrooms that are described one by one with
    their sizes are added up ('Slaapkamer van 13 m². ... Drie slaapkamers van 10 m², 9 m², 6 m²' -> 4).
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
        if _NOT_THE_COUNT_RE.search(t[max(0, m.start() - 60):m.start()]):
            continue
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
    found += [int(m.group(1)) for m in _BED_ABBR_RE.finditer(t)]
    sized = _sized_bedrooms(t)
    if sized:
        found.append(sized)
    return max(found) if found else None


# --------------------------------------------------------------------------- rooms in free text
_ROOM_WORDS = {"een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6}
_HOME_NL = (r"(?:huur)?(?:appartement|woning|flat|bovenwoning|benedenwoning|maisonnette|hoekwoning|tussenwoning|penthouse|"
            r"etagewoning|portiekwoning|galerijflat|hoekappartement|stadswoning|eengezinswoning)")
# "tweekamerappartement", "3-kamerappartement", "2 kamer appartement", "3-kamer huurappartement".
# Never "slaapkamer": "2-slaapkamer appartement" counts bedrooms, which is another question.
_ROOMS_NL_RE = re.compile(r"(?<![\w-])(\d|" + "|".join(_ROOM_WORDS) + r")[\s-]?kamer[\s-]?" + _HOME_NL + r"\b", re.I)


def rooms_from_dutch_text(text: Optional[str]) -> Optional[int]:
    """Number of rooms ("kamers", living room included) a Dutch description states about the home, or None.

    'tweekamerappartement' -> 2, 'Ruim 3-kamer appartement' -> 3. Only Dutch wording counts, because there the
    meaning is fixed: a "driekamerappartement" has a living room and two bedrooms. English "two-room apartment"
    is used both ways and is left alone. Different numbers in one text (a building with several homes): None.
    """
    if not text:
        return None
    found = set()
    for m in _ROOMS_NL_RE.finditer(unicodedata.normalize("NFKC", str(text))):
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _ROOM_WORDS.get(raw)
        if n and 1 <= n <= 9:
            found.add(n)
    return found.pop() if len(found) == 1 else None
