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

from ..models import Listing
from ..utils import city_slug, clean_text
from .base import Source, SourceError

BASE = "https://kamernet.nl"

# Kamernet listing types (same numbers as its searchCategories): 1 room, 2 apartment, 4 studio, 8 anti-squat.
_TYPES = {1: "room", 2: "apartment", 4: "studio", 8: "other"}
_URL_PREFIX = {1: "kamer", 2: "appartement", 4: "studio", 8: "anti-kraak"}
# furnishingId: 1 "kaal" (shell), 2 "gestoffeerd" (upholstered), 4 "gemeubileerd" (furnished); 3 unknown.
_FURNISHING = {1: "unfurnished", 2: "upholstered", 4: "furnished"}

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
    if items is None:
        if resp.get("total") == 0:
            return []
        raise SourceError("layout changed: kamernet listings is null but total > 0")
    if not isinstance(items, list):
        raise SourceError("layout changed: kamernet listings is not a list")
    out: List[Listing] = []
    for it in items:
        lst = parse_listing(it, city)
        if lst is not None and lst.property_type in ("apartment", "house"):
            out.append(lst)
    if items and not out and not any(isinstance(i, dict) and i.get("listingId") for i in items):
        raise SourceError("layout changed: kamernet listing items have no listingId")
    return out


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
    desc = det.get("dutchDescription") or det.get("englishDescription")
    title = det.get("dutchTitle") or det.get("englishTitle")
    text = " ".join(x for x in (title, desc) if x)
    if text:
        listing.description = clean_text(text)
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
    persons = _to_int(det.get("suitableForNumberOfPersons"))
    if persons:
        listing.extra["max_persons"] = persons
    if det.get("isRegistrationAllowed") is False:
        listing.extra["registration_allowed"] = False
    if det.get("deposit"):
        listing.extra["deposit"] = _to_int(det.get("deposit"))
    return listing


class Kamernet(Source):
    name = "kamernet"
    label = "Kamernet"
    homepage = "https://kamernet.nl"
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
