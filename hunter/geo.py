"""Conservative PDOK geocoding; all I/O uses the caller's polite HTTP client.

Cache keys are compact JSON arrays [normalised street, number (including suffix),
postcode, city]. Values are {lat, lon, precision} or {not_found: ISO date}.
Misses expire after seven days; successes do not expire. No responses are saved.

Cards: absent ``geo`` means untried; a success has ``geo={lat,lon,precision,q}``;
a completed miss has ``geo=None, geo_q=key``. A partial lookup (budget exhaustion
or service error) stays untried and is never cached. Its strategies restart next
run. ``geo_q`` is only used for misses. Missing/expired cache entries are retried,
even on cards marked as misses. Title is deliberately not treated as an address:
marketing text is insufficient evidence for a street name.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from .sources.base import SourceError
from .utils import CITIES, city_slug

ENDPOINT = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
CITY_CENTRES = {
    "den-haag": (52.0705, 4.3007), "amsterdam": (52.3676, 4.9041),
    "rotterdam": (51.9244, 4.4777), "leiden": (52.1601, 4.4970),
    "delft": (52.0116, 4.3571),
}
_POSTCODE = re.compile(r"(?<!\w)([1-9]\d{3})\s*([a-z]{2})(?!\w)", re.I)
# The house number at the end, with what follows it: a letter ("12A", "12 II"), or a floor or unit after a
# hyphen or slash ("85-R", "10-1", "12/2").
_NUMBER = re.compile(r"^(.+?)\s+(\d+)(\s*[-/]\s*[a-z0-9]+|\s*[a-z]+)?$", re.I)
_POINT = re.compile(r"POINT\s*\(\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)\s*\)", re.I)


# "D.S. Lohmanlaan", "V. Zeggelenlaan": some sites abbreviate the first words of a street name to initials.
_ABBREVIATED = re.compile(r"^((?:[^\W\d_]\.\s?)+)\s*(\S.*)$")


def _abbreviation(street: str) -> Optional[Tuple[List[str], str]]:
    """(initials, the rest of the name) for a street written with initials, else None."""
    match = _ABBREVIATED.match(street.strip())
    if not match:
        return None
    initials = [c.casefold() for c in match.group(1) if c.isalpha()]
    return (initials, match.group(2).strip()) if initials else None


def _spells_out(listed: str, official: str) -> bool:
    """Is `official` ("De Savornin Lohmanlaan") the street that `listed` ("D.S. Lohmanlaan") abbreviates?
    The written-out part has to be the same, and each initial has to start one word in front of it, in order."""
    short = _abbreviation(listed)
    if short is None:
        return False
    initials, rest = short
    words = official.split()
    for i in range(1, len(words)):
        if _normalise(" ".join(words[i:])) == _normalise(rest):
            head = [_normalise(w) for w in words[:i]]
            return len(head) == len(initials) and all(w[:1] == c for w, c in zip(head, initials))
    return False


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))


def _postcode(value: Any) -> str:
    match = _POSTCODE.search(str(value or ""))
    return "".join(match.groups()).upper() if match else ""


def parse_address(address: Optional[str], postcode: Optional[str] = None,
                  city: Optional[str] = None) -> Dict[str, str]:
    """Pure parser: return street, number (suffix retained), postcode and city.

    Strip a trailing postcode/city; recognise terminal house numbers with optional
    letters, Roman floors or hyphenated suffixes. Postcode-only addresses never
    become street queries. A city name alone is also not a street.
    """
    text = str(address or "").strip()
    code = _postcode(postcode) or _postcode(text)
    slug = city_slug(city) or str(city or "").strip().lower()
    code_match = _POSTCODE.search(text)
    if code_match:
        text = text[:code_match.start()].rstrip(" ,")
    # Also accept addresses ending in a city without a postcode.
    names = list(CITIES.values()) + ["'s-Gravenhage", "’s-Gravenhage"]
    for name in names:
        text = re.sub(r"(?:,\s*|\s+)" + re.escape(name) + r"$", "", text, flags=re.I)
    text = text.strip(" ,")
    if _normalise(text) in {_normalise(n) for n in names}:
        text = ""
    match = _NUMBER.fullmatch(text)
    if match:
        street = match.group(1).strip()
        number = match.group(2) + re.sub(r"\s+", "", match.group(3) or "").upper()
    else:
        street, number = text, ""
    return {"street": street, "number": number, "postcode": code, "city": slug}


def cache_key(listing_fields: Dict[str, Any]) -> str:
    """Stable key for the actual address fields; punctuation/accents are ignored."""
    fields = parse_address(listing_fields.get("address"), listing_fields.get("postcode"),
                           listing_fields.get("city"))
    return json.dumps([_normalise(fields[k]) for k in ("street", "number", "postcode", "city")],
                      ensure_ascii=True, separators=(",", ":"))


def load_cache(path: Any) -> Dict[str, Any]:
    """Read a JSON object, returning an empty cache for missing/broken files."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def save_cache(path: Any, cache: Dict[str, Any]) -> None:
    """Atomically replace the cache using a temporary file in the same directory.

    Write/replace errors propagate, preserving the previous file and removing the
    temporary file. The caller owns creation of the destination directory.
    """
    target = Path(path)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(target.parent),
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as out:
            temp = out.name
            json.dump(cache, out, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        if temp is not None and os.path.exists(temp):
            os.unlink(temp)


def _day(now: Any) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        return now.date()
    if isinstance(now, date):
        return now
    return date.fromisoformat(str(now)[:10])


def _inside(lat: Any, lon: Any) -> bool:
    return (isinstance(lat, (int, float)) and not isinstance(lat, bool)
            and isinstance(lon, (int, float)) and not isinstance(lon, bool)
            and 50.6 <= lat <= 53.7 and 3.2 <= lon <= 7.3)


def _near_city(lat: float, lon: float, city: str) -> bool:
    centre = CITY_CENTRES.get(city)
    if centre is None:
        return True
    a, b = map(math.radians, centre)
    x, y = map(math.radians, (lat, lon))
    h = math.sin((x - a) / 2) ** 2 + math.cos(a) * math.cos(x) * math.sin((y - b) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, h))) <= 25


def _result(value: Any) -> bool:
    return (isinstance(value, dict) and _inside(value.get("lat"), value.get("lon"))
            and value.get("precision") in ("address", "street", "postcode"))


def _cached(cache: Dict[str, Any], key: str, today: date) -> Tuple[bool, Optional[dict]]:
    value = cache.get(key)
    if _result(value):
        return True, {k: value[k] for k in ("lat", "lon", "precision")}
    if isinstance(value, dict) and isinstance(value.get("not_found"), str):
        try:
            age = today - date.fromisoformat(value["not_found"])
            if timedelta(0) <= age < timedelta(days=7):
                return True, None
        except ValueError:
            pass
    return False, None


def _strategies(fields: Dict[str, str]) -> List[Tuple[str, str]]:
    street, number, code, city = (fields[k] for k in ("street", "number", "postcode", "city"))
    city = CITIES.get(city, city)
    queries = []
    if code and number:
        queries.append(("adres", code + " " + number))
    if street and number:
        queries.append(("adres", " ".join((street, number, city)).strip()))
    if street:
        queries.append(("weg", " ".join((street, city)).strip()))
        short = _abbreviation(street)
        if short is not None:      # "D.S. Lohmanlaan": ask for "Lohmanlaan" and accept the one street it abbreviates
            queries.append(("weg-initials", " ".join((short[1], city)).strip()))
    if code:
        queries.append(("postcode", code))
    return queries


def _docs(payload: Any) -> List[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise SourceError("malformed PDOK response")
    response = payload["response"]
    docs = response.get("docs")
    count = response.get("numFound")
    if (not isinstance(docs, list) or not isinstance(count, int) or isinstance(count, bool)
            or count < 0 or (count == 0 and docs) or (count > 0 and not docs)):
        raise SourceError("malformed PDOK response")
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("type"), str):
            raise SourceError("malformed PDOK document")
        if not isinstance(doc.get("centroide_ll"), str) or not _POINT.fullmatch(doc["centroide_ll"]):
            raise SourceError("malformed PDOK point")
    return docs


# The place names PDOK uses for each city ("woonplaats"), normalised. A "Schoolstraat 12" exists in Den Haag and
# in Delft, 9 km apart: the distance to the city centre cannot tell them apart, the place name can.
_PLACES = {"den-haag": ("sgravenhage", "denhaag"), "amsterdam": ("amsterdam",), "rotterdam": ("rotterdam",),
           "leiden": ("leiden",), "delft": ("delft",)}


def _in_city(doc: dict, city: str) -> bool:
    names = _PLACES.get(city)
    if names is None:
        return True
    place = doc.get("woonplaatsnaam") or str(doc.get("weergavenaam") or "").rpartition(",")[2]
    place = _normalise(place)
    # "1019AT Amsterdam", "Hoogvliet Rotterdam": the city's name is in it; "Leiderdorp" is not Leiden
    return any(place.endswith(n) for n in names)


def _trust(doc: dict, kind: str, fields: Dict[str, str]) -> Optional[dict]:
    initials = kind == "weg-initials"
    kind = "weg" if initials else kind
    if doc["type"] != kind:
        return None
    if kind in ("adres", "weg") and not _in_city(doc, fields["city"]):
        return None
    point = _POINT.fullmatch(doc["centroide_ll"])
    lon, lat = map(float, point.groups())
    if not _inside(lat, lon) or not _near_city(lat, lon, fields["city"]):
        return None
    display = str(doc.get("weergavenaam") or "").split(",")[0]
    parsed = parse_address(display)
    street = doc.get("straatnaam") or (display if kind == "weg" else parsed["street"])
    if initials:
        if not _spells_out(fields["street"], str(street)):
            return None
    elif kind in ("adres", "weg") and fields["street"]:
        if _normalise(street) != _normalise(fields["street"]):
            return None
    if kind == "adres":
        number = re.match(r"\d+", str(doc.get("huisnummer") or parsed["number"]))
        asked = re.match(r"\d+", fields["number"])
        if not number or not asked or int(number.group()) != int(asked.group()):
            return None
    # A street+number query may legitimately correct a listing's stale postcode.
    if kind == "postcode" and _postcode(doc.get("postcode") or display) != fields["postcode"]:
        return None
    return {"lat": lat, "lon": lon,
            "precision": {"adres": "address", "weg": "street", "postcode": "postcode"}[kind]}


class _BudgetExhausted(Exception):
    pass


class _Lookup:
    def __init__(self, http: Any, budget: Optional[int] = None) -> None:
        self.http, self.budget, self.requests = http, budget, 0

    def run(self, fields: Dict[str, Any], cache: Dict[str, Any], today: date) -> Optional[dict]:
        key = cache_key(fields)
        found, result = _cached(cache, key, today)
        if found:
            return result
        parsed = parse_address(fields.get("address"), fields.get("postcode"), fields.get("city"))
        for kind, query in _strategies(parsed):
            if self.budget is not None and self.requests >= self.budget:
                raise _BudgetExhausted()
            url = ENDPOINT + "?" + urlencode({
                "q": query, "fq": "type:" + ("weg" if kind == "weg-initials" else kind),
                "rows": 10 if kind == "weg-initials" else 5,
                "fl": "weergavenaam,type,centroide_ll,postcode,woonplaatsnaam,straatnaam,huisnummer,score",
            })
            self.requests += 1
            docs = _docs(self.http.get_json(url))
            if kind == "weg-initials":
                # Two streets that fit the initials ("J. Meewisstraat"): which one is meant is not known.
                fits = [(doc, _trust(doc, kind, parsed)) for doc in docs]
                names = {_normalise(doc.get("straatnaam") or str(doc.get("weergavenaam") or "").split(",")[0])
                         for doc, found in fits if found is not None}
                if len(names) == 1:
                    result = next(found for _, found in fits if found is not None)
                    cache[key] = dict(result)
                    return result
                continue
            for doc in docs:
                # For the postcode+number strategy require the postcode as well.
                if (kind == "adres" and query.startswith(parsed["postcode"] + " ")
                        and parsed["postcode"] and _postcode(doc.get("postcode")) != parsed["postcode"]):
                    continue
                result = _trust(doc, kind, parsed)
                if result is not None:
                    cache[key] = dict(result)
                    return result
        cache[key] = {"not_found": today.isoformat()}
        return None


def locate(listing_fields: Dict[str, Any], http: Any, cache: Dict[str, Any],
           now: Any = None) -> Optional[dict]:
    """Locate one address, cache first. Service errors propagate; no failed cache write.

    ``now`` optionally supplies a date/datetime/ISO date for reproducible expiry.
    Use locate_cards for bounded requests and service failure isolation.
    """
    return _Lookup(http).run(listing_fields, cache, _day(now))


def locate_cards(cards: Dict[str, Any], http: Any, cache: Dict[str, Any],
                 budget: int, now: Any) -> Tuple[int, Optional[str]]:
    """Mutate cards; return (HTTP calls attempted, None or 'service_error').

    Budget counts get_json calls, including failures, not the HTTP client's internal
    retries. Cached/native coordinates cost nothing, even at zero budget. A service
    failure stops the batch immediately; error details never expose addresses.
    Native numeric coordinates only need the Netherlands bounds check. Current
    successful cards are retained; changed addresses invalidate previous outcomes.
    """
    lookup = _Lookup(http, max(0, budget))
    today = _day(now)
    for card in cards.values():
        fields = card.get("listing") or {}
        key = cache_key(fields)
        extra = fields.get("extra") or {}
        if _inside(extra.get("lat"), extra.get("lon")):
            card["geo"] = {"lat": float(extra["lat"]), "lon": float(extra["lon"]),
                           "precision": "address", "q": key}
            card.pop("geo_q", None)
            continue
        current = card.get("geo")
        if _result(current) and current.get("q") == key:
            continue
        card.pop("geo", None)
        card.pop("geo_q", None)
        try:
            result = lookup.run(fields, cache, today)
        except _BudgetExhausted:
            # Still allow later cache hits and native coordinates with no budget.
            continue
        except Exception:
            # Http implementations may raise SourceBlocked/SourceError, requests
            # errors or JSON decoding errors. Never leak their address-bearing text.
            return lookup.requests, "service_error"
        card["geo"] = dict(result, q=key) if result is not None else None
        if result is None:
            card["geo_q"] = key
    return lookup.requests, None
