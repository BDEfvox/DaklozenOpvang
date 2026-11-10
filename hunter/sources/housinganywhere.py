"""HousingAnywhere: reads the search results embedded in the server-rendered city page.

Search page used: https://housinganywhere.com/s/<City>--Netherlands/apartment-for-rent
That is the site's own "apartment for rent" collection page: it only lists whole apartments and houses (no
private/shared rooms, no studios). Plain path, no query string: robots.txt disallows `/api/*` and `/*/s/*?*`.

The listings are in `window.__staticRouterHydrationData = JSON.parse("<json string>")` (loaderData -> some
route key -> `listings`). `window.__PRELOADED_STATE__` (redux) does not contain them; it is only used as a
sanity check that this is the expected kind of page.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import Listing
from ..utils import city_slug, clean_text
from .base import Source, SourceError

BASE = "https://housinganywhere.com"
_CITY_PATHS = {
    "den-haag": "The-Hague--Netherlands",
    "amsterdam": "Amsterdam--Netherlands",
    "leiden": "Leiden--Netherlands",
}
_TYPES = {"APARTMENT": "apartment", "HOUSE": "house", "STUDIO": "studio", "ROOM": "room",
          "PRIVATE_ROOM": "room", "SHARED_ROOM": "room"}
_HYDRATION_KEY = "window.__staticRouterHydrationData"


def _hydration(html: str) -> Dict[str, Any]:
    i = html.find(_HYDRATION_KEY)
    if i < 0:
        raise SourceError("layout changed: housinganywhere hydration data not found")
    m = re.compile(r"\s*=\s*JSON\.parse\(").match(html, i + len(_HYDRATION_KEY))
    if not m:
        raise SourceError("layout changed: housinganywhere hydration data has a new shape")
    try:
        literal, _ = json.JSONDecoder().raw_decode(html[m.end():])  # JS string literal == JSON string
        data = json.loads(literal)
    except (ValueError, TypeError) as exc:
        raise SourceError(f"layout changed: housinganywhere hydration data unreadable ({exc})")
    return data


def _find_listings(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    loader = data.get("loaderData") if isinstance(data, dict) else None
    if not isinstance(loader, dict):
        raise SourceError("layout changed: housinganywhere loaderData missing")
    for value in loader.values():
        if isinstance(value, dict) and isinstance(value.get("listings"), list):
            return value["listings"]
    raise SourceError("layout changed: housinganywhere listings not found in loaderData")


def _to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def parse_listing(item: Dict[str, Any], city: str) -> Optional[Listing]:
    if not isinstance(item, dict):
        return None
    path = item.get("listingPath") or item.get("unitTypePath")
    if not path:
        return None
    if city_slug(item.get("city")) != city:
        return None
    ptype = _TYPES.get(str(item.get("propertyType", "")).upper(), "other")
    if ptype not in ("apartment", "house"):
        return None
    m = re.search(r"/(ut\d+)/", path)
    sid = m.group(1) if m else (str(item.get("unitTypeInternalID") or item.get("id") or ""))
    if not sid:
        return None
    price = _to_int(item.get("priceEUR"))
    if price is None and (item.get("currency") or "EUR") == "EUR":
        price = _to_int(item.get("minPrice"))
    utilities = item.get("utilities")  # I = included, E = excluded, P = partly
    street = item.get("street")
    bedrooms = _to_int(item.get("facility_bedroom_count"))
    area = _to_int(item.get("facility_total_size")) or None
    extra: Dict[str, Any] = {
        "min_stay_months": _to_int(item.get("minimalRentalPeriod")),
        "max_stay_months": _to_int(item.get("maximumStay")),
        "landlord_type": item.get("landlordType"),
        "registration_possible": {"yes": True, "no": False}.get(str(item.get("facility_registration_possible"))),
    }
    if utilities == "P":
        extra["utilities_partly_included"] = True
    max_price = _to_int(item.get("maxPrice"))
    if max_price and price and max_price > price:
        extra["price_max"] = max_price
    kind = "Apartment" if ptype == "apartment" else "House"
    return Listing(
        source="housinganywhere",
        source_id=sid,
        url=BASE + quote(path, safe="/"),
        title="{} for rent: {}".format(kind, street or item.get("city") or city),
        city=city,
        price_total=price,
        price_includes_utilities={"I": True, "E": False}.get(utilities),
        bedrooms=bedrooms,
        area_m2=area,
        property_type=ptype,
        furnished="furnished" if item.get("facility_bedroom_furnished") == "yes" else None,
        address=street,
        image_url=item.get("previewImage") or ((item.get("photos") or [None])[0]),
        extra={k: v for k, v in extra.items() if v is not None},
    )


def parse_search(html: str, city: str) -> List[Listing]:
    items = _find_listings(_hydration(html))
    out: List[Listing] = []
    for it in items:
        lst = parse_listing(it, city)
        if lst is not None:
            out.append(lst)
    if items and not any(isinstance(i, dict) and i.get("listingPath") for i in items):
        raise SourceError("layout changed: housinganywhere listing items have no listingPath")
    return out


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")

    def meta(**attrs: str) -> Optional[str]:
        tag = soup.find("meta", attrs=attrs)
        return tag.get("content") if tag and tag.get("content") else None

    text = meta(property="twitter:description") or meta(property="og:description") or meta(name="description")
    if text is None:
        raise SourceError("layout changed: housinganywhere detail page has no description")
    listing.description = clean_text(text)
    summary = meta(name="description") or ""
    m = re.search(r"(\d+)\s*bedroom", summary)
    if m and listing.bedrooms is None:
        listing.bedrooms = int(m.group(1))
    return listing


class HousingAnywhere(Source):
    name = "housinganywhere"
    label = "HousingAnywhere"
    homepage = "https://housinganywhere.com"
    note = "Mostly furnished, mid-term; platform fee applies"

    def search_url(self, city: str) -> str:
        return f"{BASE}/s/{_CITY_PATHS[city]}/apartment-for-rent"

    def fetch(self, city: str, http: "Any") -> List[Listing]:
        if city not in _CITY_PATHS:
            return []
        return parse_search(http.get_text(self.search_url(city)), city)

    def fetch_details(self, listing: Listing, http: "Any") -> Listing:
        if not listing.url.startswith(BASE + "/room/"):
            return listing
        return apply_details(listing, http.get_text(listing.url))


SOURCE = HousingAnywhere()
