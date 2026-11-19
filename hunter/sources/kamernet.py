"""Kamernet: reads the `__NEXT_DATA__` JSON of the apartment search page.

Search page used: https://kamernet.nl/huren/appartement-<city>?radius=1
That is Kamernet's own "Appartement" category page (searchCategories=[2]); rooms (1), studios (4) and
anti-squat (8) live on other pages, so only whole apartments/houses show up. `radius=1` keeps neighbouring
towns out; the code still drops anything whose citySlug is not the requested city.

Allowed by robots.txt: /huren/... is not disallowed (only /*displayroomadvert, /SearchRooms/*, /ajax/ ...).
Detail pages (/huren/appartement-<city>/<street>/appartement-<id>) are used by fetch_details.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..models import CITIES, Listing
from ..utils import city_slug, clean_text
from .base import Source, SourceError

BASE = "https://kamernet.nl"

# Kamernet listing types (same numbers as its searchCategories): 1 room, 2 apartment, 4 studio, 8 anti-squat.
_TYPES = {1: "room", 2: "apartment", 4: "studio", 8: "other"}
_URL_PREFIX = {1: "kamer", 2: "appartement", 4: "studio", 8: "anti-kraak"}
# furnishingId: 1 "kaal" (shell), 2 "gestoffeerd" (upholstered), 4 "gemeubileerd" (furnished); 3 unknown.
_FURNISHING = {1: "unfurnished", 2: "upholstered", 4: "furnished"}

# suitableForNumberOfPersons is a bit id, not a head count. Pairs seen next to the visible "Aantal huurders"
# line on the saved detail pages: 2 -> "2 personen", 4 -> "3 personen", 8 -> "4 personen"; 1 -> 1 follows the
# same sequence but was not seen. Any other id gives no max_persons.
_PERSONS_BY_ID = {1: 1, 2: 2, 4: 3, 8: 4}
# candidateStatusId is a list of bit values; the visible "Bezigheid" line shows the matching labels.
_STATUS_LABELS = {1: "Student", 2: "Werkende student", 4: "Werkend", 8: "Werkzoekend"}
_STUDENT_KINDS = (1, 2)
# candidateGenderId: 1 -> "Man" (seen on 2417454 and 2417735, 19 Nov 2026), 2 -> "Vrouw", 8 -> "Niet belangrijk",
# null -> "Iedereen welkom".
_GENDER_MAN = 1
_GENDER_WOMAN = 2
_IDEAL_HEADING = "Verhuurders ideale huurder"
_FACT_LABELS = ("Aantal huurders", "Leeftijd", "Geslacht", "Bezigheid")

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _next_data(html: str) -> Dict[str, Any]:
    m = _NEXT_RE.search(html)
    if not m:
        raise SourceError("layout changed: kamernet has no __NEXT_DATA__")
    try:
        return json.loads(m.group(1))
    except ValueError as exc:
        raise SourceError(f"layout changed: kamernet __NEXT_DATA__ is not JSON ({exc})")


def _iso_date(value: Any) -> Optional[str]:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return None


def _to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def listing_url(item: Dict[str, Any]) -> Optional[str]:
    """The way Kamernet's own links look: /huren/<type>-<city>/<street>/<type>-<id> (never displayroomadvert)."""
    lt = item.get("listingType")
    prefix = _URL_PREFIX.get(lt)
    city, street, lid = item.get("citySlug"), item.get("streetSlug"), item.get("listingId")
    if not (prefix and city and street and lid):
        return None
    return f"{BASE}/huren/{prefix}-{city}/{street}/{prefix}-{lid}"


def parse_listing(item: Dict[str, Any], city: str) -> Optional[Listing]:
    if not isinstance(item, dict) or not item.get("listingId"):
        return None
    if city_slug(item.get("citySlug") or item.get("city")) != city:
        return None
    ptype = _TYPES.get(item.get("listingType"), "other")
    url = listing_url(item)
    if url is None:
        return None
    street = item.get("street")
    thumb = item.get("resizedFullPreviewImageUrl") or item.get("fullPreviewImageUrl") or item.get("thumbnailUrl")
    title = "{} te huur: {}".format({"apartment": "Appartement", "room": "Kamer", "studio": "Studio"}.get(ptype, "Woning"),
                                    street or item.get("city") or city)
    return Listing(
        source="kamernet",
        source_id=str(item["listingId"]),
        url=url,
        title=title,
        city=city,
        price_total=_to_int(item.get("totalRentalPrice")),
        price_includes_utilities=item.get("utilitiesIncluded") if isinstance(item.get("utilitiesIncluded"), bool) else None,
        area_m2=_to_int(item.get("surfaceArea")) or None,
        property_type=ptype,
        furnished=_FURNISHING.get(item.get("furnishingId")),
        address=street,
        available_from=_iso_date(item.get("availabilityStartDate")),
        available_until=_iso_date(item.get("availabilityEndDate")),
        image_url=thumb,
        extra={"student_house": bool(item.get("isStudentHouseAdvert"))},
    )


def parse_search(html: str, city: str) -> List[Listing]:
    data = _next_data(html)
    try:
        resp = data["props"]["pageProps"]["targetPageProps"]["findListingsResponse"]
    except (KeyError, TypeError):
        raise SourceError("layout changed: kamernet findListingsResponse not found")
    if not isinstance(resp, dict) or "listings" not in resp:
        raise SourceError("layout changed: kamernet listings key missing")
    items = resp["listings"]
    top = resp.get("topAdListings")
    if top is not None and not isinstance(top, list):
        raise SourceError("layout changed: kamernet topAdListings is not a list")
    if items is None:
        if resp.get("total") == 0 and not top:
            return []
        if not top:
            raise SourceError("layout changed: kamernet listings is null but total > 0")
        items = []
    if not isinstance(items, list):
        raise SourceError("layout changed: kamernet listings is not a list")
    # The paid "Uitgelichte top advertenties" are counted in `total` but are not in `listings`.
    items = list(items) + list(top or [])
    out: List[Listing] = []
    seen = set()
    for it in items:
        lst = parse_listing(it, city)
        if lst is not None and lst.property_type in ("apartment", "house") and lst.source_id not in seen:
            seen.add(lst.source_id)
            out.append(lst)
    if items and not out and not any(isinstance(i, dict) and i.get("listingId") for i in items):
        raise SourceError("layout changed: kamernet listing items have no listingId")
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _one_language(title: Any, desc: Any) -> str:
    title = clean_text(title) if isinstance(title, str) else ""
    desc = clean_text(desc, keep_lines=True) if isinstance(desc, str) else ""
    if title and desc and _norm(desc).startswith(_norm(title)):
        return desc  # the title is the opening words of the text: do not repeat it
    return "\n".join(x for x in (title, desc) if x)


def _description(det: Dict[str, Any]) -> str:
    """Dutch text first, then the English text (flag phrases may be in either), unless it is the same text."""
    nl = _one_language(det.get("dutchTitle"), det.get("dutchDescription"))
    en = _one_language(det.get("englishTitle"), det.get("englishDescription"))
    if not nl or _norm(en) == _norm(nl):
        return nl or en
    if not en or _norm(en) in _norm(nl):
        return nl
    return clean_text(nl + "\n" + en, keep_lines=True)


def _ideal_tenant_rows(html: str) -> Dict[str, str]:
    """The visible 'Verhuurders ideale huurder' block: label -> value (e.g. Leeftijd -> '16 - 40 jaar')."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(lambda t: t.name in ("h4", "h5", "h6") and t.get_text(strip=True) == _IDEAL_HEADING)
    section = heading.find_parent("section") if heading else None
    rows: Dict[str, str] = {}
    if section is None:
        return rows
    for div in section.find_all("div"):
        ps = div.find_all("p", recursive=False)
        if len(ps) == 2:
            label = clean_text(ps[0].get_text(" "))
            value = clean_text(ps[1].get_text(" ")).replace(" ,", ",")
            if label and value:
                rows.setdefault(label, value)
    return rows


def _id_list(value: Any) -> Optional[List[int]]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return list(value)
    return None


def _apply_ideal_tenant(listing: Listing, det: Dict[str, Any], html: str) -> None:
    rows = _ideal_tenant_rows(html)
    extra = listing.extra
    # 1. head count: the visible text wins; the id mapping is the fallback (unknown ids give nothing)
    persons: Optional[int] = None
    m = re.match(r"(\d+)\s+person", rows.get("Aantal huurders", ""))
    if m:
        persons = int(m.group(1))
    else:
        sid = det.get("suitableForNumberOfPersons")
        if isinstance(sid, int) and not isinstance(sid, bool):
            persons = _PERSONS_BY_ID.get(sid)
    if persons:
        extra["max_persons"] = persons
    # 2. occupation: only trusted when the ids spell exactly the visible labels
    ids = _id_list(det.get("candidateStatusId"))
    labels: Optional[List[str]] = None
    if ids and all(i in _STATUS_LABELS for i in ids):
        labels = [_STATUS_LABELS[i] for i in sorted(set(ids))]
        shown = rows.get("Bezigheid")
        if shown is not None and sorted(x.strip() for x in shown.split(",")) != sorted(labels):
            labels = None  # the page says something else than the ids: believe nothing
    if labels and ids:
        kinds = set(ids)
        if kinds <= set(_STUDENT_KINDS):
            extra["site_students_only"] = True
        elif kinds == {4}:
            extra["site_working_only"] = True       # "Bezigheid: Werkend". That already says no students.
        elif not kinds & set(_STUDENT_KINDS):
            extra["site_no_students"] = True        # "Werkend, Werkzoekend"
        elif 1 in kinds:
            extra["site_students_ok"] = True        # "Student, Werkende student, Werkend": students are named too
        # "Werkende student, Werkend": a student must have a job. No mark says exactly that, so none is set.
    # 3. age: only real limits (16 and 99 are the site's defaults)
    lo, hi = det.get("candidateMinAgeId"), det.get("candidateMaxAgeId")
    age_ok = all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in (lo, hi))
    if age_ok and rows.get("Leeftijd"):
        nums = [int(x) for x in re.findall(r"\d+", rows["Leeftijd"])]
        if nums != [v for v in (lo, hi) if v is not None]:
            age_ok = False
    if age_ok:
        if lo is not None and lo > 18:
            extra["site_min_age"] = lo
        if hi is not None and 0 < hi < 99:
            extra["site_max_age"] = hi
    # 4. gender: only the proven pair 2 = Vrouw
    gender = det.get("candidateGenderId")
    if gender == _GENDER_WOMAN and rows.get("Geslacht", "Vrouw") == "Vrouw":
        extra["site_women_only"] = True
    if gender == _GENDER_MAN and rows.get("Geslacht", "Man") == "Man":
        extra["site_men_only"] = True
    # 5. the proof line: the page's own labels and values
    facts = [f"{k}: {rows[k]}" for k in _FACT_LABELS if rows.get(k)]
    if facts:
        extra["site_facts"] = " · ".join(facts)[:200]


def apply_details(listing: Listing, html: str) -> Listing:
    data = _next_data(html)
    try:
        det = data["props"]["pageProps"]["targetPageProps"]["listingDetails"]
    except (KeyError, TypeError):
        raise SourceError("layout changed: kamernet listingDetails not found")
    if not isinstance(det, dict):
        raise SourceError("layout changed: kamernet listingDetails is not an object")
    bedrooms = _to_int(det.get("numOfBedrooms"))
    rooms = _to_int(det.get("numOfRooms"))
    if bedrooms is not None:
        listing.bedrooms = bedrooms
    if rooms is not None:
        listing.rooms = rooms
    text = _description(det)
    if text:
        listing.description = text
    if det.get("postalCode") and not listing.postcode:
        listing.postcode = str(det["postalCode"]).replace(" ", "")
    if det.get("computedStreetName") and det.get("houseNumber"):
        num = str(det["houseNumber"]) + (str(det.get("houseNumberAddition") or ""))
        listing.address = f"{det['computedStreetName']} {num}"
    if listing.area_m2 is None:
        listing.area_m2 = _to_int(det.get("surfaceArea")) or None
    # Facility ids: 1 = shared, 2 = own (checked on live listings, 9 Nov 2026). A shared kitchen or
    # living room means this is a room in someone's home, even in the "appartement" category.
    if det.get("kitchenId") == 1 or det.get("livingRoomId") == 1:
        listing.property_type = "room"
    if any(det.get(k) == 1 for k in ("kitchenId", "livingRoomId", "showerId", "toiletId")):
        listing.extra["site_housemates"] = True  # the code comment above: 1 = shared
    _apply_ideal_tenant(listing, det, html)
    if det.get("isRegistrationAllowed") is False:
        listing.extra["registration_allowed"] = False
        listing.extra["registration_possible"] = False
    if det.get("deposit"):
        listing.extra["deposit"] = _to_int(det.get("deposit"))
    return listing


class Kamernet(Source):
    name = "kamernet"
    label = "Kamernet"
    homepage = "https://kamernet.nl"
    cities = tuple(CITIES)
    note = "Paid Kamernet account needed to reply"

    def search_url(self, city: str) -> str:
        return f"{BASE}/huren/appartement-{city}?radius=1"

    def fetch(self, city: str, http: "Any") -> List[Listing]:
        html = http.get_text(self.search_url(city))
        return parse_search(html, city)

    def fetch_details(self, listing: Listing, http: "Any") -> Listing:
        if "displayroomadvert" in listing.url or not listing.url.startswith(BASE + "/huren/"):
            return listing
        html = http.get_text(listing.url)
        return apply_details(listing, html)


SOURCE = Kamernet()
