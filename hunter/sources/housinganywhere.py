"""HousingAnywhere: reads the search results embedded in the server-rendered city page.

Search page used: https://housinganywhere.com/s/<City>--Netherlands/apartment-for-rent
That is the site's own "apartment for rent" collection page: it only lists whole apartments and houses (no
private/shared rooms, no studios). Plain path, no query string: robots.txt disallows `/api/*` and `/*/s/*?*`.

The listings are in `window.__staticRouterHydrationData = JSON.parse("<json string>")` (loaderData -> some
route key -> `listings`). `window.__PRELOADED_STATE__` (redux) does not contain them; it is only used as a
sanity check that this is the expected kind of page. On a detail page it holds `listing.entity`.

Results beyond page 1 (31 homes in The Hague, 23 per page): the pager is made of <button>s without a link, no
rel="next" and the canonical URL is the first page, so there is no plain-path URL for page 2. The only way is
a query string, which robots.txt disallows (`/*/s/*?*`), so only page 1 is read.
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
    "delft": "Delft--Netherlands",
    "rotterdam": "Rotterdam--Netherlands",
}
_TYPES = {"APARTMENT": "apartment", "HOUSE": "house", "STUDIO": "studio", "ROOM": "room",
          "PRIVATE_ROOM": "room", "SHARED_ROOM": "room"}
_HYDRATION_KEY = "window.__staticRouterHydrationData"
_STATE_KEY = "window.__PRELOADED_STATE__"
# facilities.tenant_status as seen next to the visible "Tenant type" line: "working professionals only" ->
# "Working professionals only", "any" -> "Students, working professionals". A students-only value was not
# seen on any saved page or in the page script, so it is ignored (no guess).
_TENANT_LABELS = {"working professionals only": "Working professionals only",
                  "any": "Students, working professionals"}


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


def _date(value: Any) -> Optional[str]:
    """'2027-02-28T00:00:00Z' -> '2027-02-28'. The site's 'no date' is 0001-01-01: that gives None."""
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value) and not value.startswith("0001-"):
        return value[:10]
    return None


def _price(item: Dict[str, Any], available_from: Optional[str]) -> Dict[str, Optional[int]]:
    """The monthly rent to show, the lowest and highest the row states, and the rent of the first bookable month.

    The site prices per month of the year. `priceEUR` is only the "flat" price; for some listings it is shown
    nowhere on the page (audit H1: 3350 where the page says "From €2650 per month" and the card "€2650 - €3999").
    The rent shown is the one the page headlines, "From €X per month" (minPrice, in euro), else priceEUR. It is the
    same on every run. The rent of the first bookable month (pricingValuesEUR.precise["MM-YYYY"]) moves with the
    calendar, so it is kept beside it and never used as the price: a price that changes by itself would be
    announced as a price drop. The range is minPrice..maxPrice, widened by that first month."""
    values = item.get("pricingValuesEUR") if isinstance(item.get("pricingValuesEUR"), dict) else {}
    precise = values.get("precise") if isinstance(values.get("precise"), dict) else {}
    first: Optional[int] = None
    if available_from:
        first = _to_int(precise.get(available_from[5:7] + "-" + available_from[:4]))
    euro = (item.get("currency") or "EUR") == "EUR"
    lo = _to_int(item.get("minPrice")) if euro else None
    hi = _to_int(item.get("maxPrice")) if euro else None
    chosen = lo or _to_int(item.get("priceEUR"))
    nums = [n for n in (lo, hi, chosen, first) if n]
    return {"price": chosen, "min": min(nums) if nums else None, "max": max(nums) if nums else None, "first": first}


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
    available_from = _date(item.get("bookableDateFromStart"))
    pr = _price(item, available_from)
    price = pr["price"]
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
    if pr["min"] is not None and pr["max"] is not None and pr["min"] != pr["max"]:
        extra["price_min"] = pr["min"]
        extra["price_max"] = pr["max"]
    if pr["first"] and pr["first"] != price:
        extra["price_first_month"] = pr["first"]
    extra["price_basis"] = "from"   # how price_total is read; the runner announces no drop when this changes
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
        available_from=available_from,
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
    if items and not any(isinstance(i, dict) and (i.get("listingPath") or i.get("unitTypePath")) for i in items):
        raise SourceError("layout changed: housinganywhere listing items have no listingPath or unitTypePath")
    return out


def _entity(html: str) -> Dict[str, Any]:
    """listing.entity of window.__PRELOADED_STATE__ on a detail page."""
    i = html.find(_STATE_KEY)
    if i < 0:
        raise SourceError("layout changed: housinganywhere detail page has no preloaded state")
    m = re.compile(r"\s*=\s*\(?\s*").match(html, i + len(_STATE_KEY))
    try:
        state, _ = json.JSONDecoder().raw_decode(html[m.end():]) if m else (None, 0)
        entity = state["listing"]["entity"]
    except (ValueError, TypeError, KeyError):
        raise SourceError("layout changed: housinganywhere preloaded state unreadable")
    if not isinstance(entity, dict):
        raise SourceError("layout changed: housinganywhere listing.entity is not an object")
    return entity


# Costs the site lists per listing (entity.costs.costs). A bill for gas, water, electricity or internet is not a
# fixed extra: it is an estimate of what the tenant will use, and "bills included" already covers it.
_BILLS = ("electricity-bill", "gas-bill", "water-bill", "internet-bill", "utility-bills", "broadcasting-bill")


def _required_monthly(entity: Dict[str, Any]) -> "tuple[int, str]":
    """(euros, what): fixed amounts the tenant must pay every month besides the rent, e.g. a cleaning fee.
    ut1747098: cleaning-fee {value: 10000 (cents), payableBy: tenant, payableAt: monthly, required: true}."""
    costs = entity.get("costs") if isinstance(entity.get("costs"), dict) else {}
    items = costs.get("costs") if isinstance(costs.get("costs"), dict) else {}
    total, names = 0, []
    for name, c in sorted(items.items()):
        if name in _BILLS or not isinstance(c, dict):
            continue
        value = c.get("value")
        if (c.get("required") is True and c.get("payableBy") == "tenant" and c.get("payableAt") == "monthly"
                and c.get("isEstimated") is not True and isinstance(value, int) and not isinstance(value, bool)
                and value >= 100):
            total += value // 100
            names.append("%s €%d" % (str(name).replace("-", " "), value // 100))
    return total, ", ".join(names)[:100]


def _tenant_extra(listing: Listing, entity: Dict[str, Any]) -> None:
    extra = listing.extra
    facilities = entity.get("facilities") if isinstance(entity.get("facilities"), dict) else {}
    facts: List[str] = []
    status = facilities.get("tenant_status")
    if status in _TENANT_LABELS:
        facts.append("Tenant type: " + _TENANT_LABELS[status])
        if status == "working professionals only":
            extra["site_working_only"] = True
        elif status == "any":
            extra["site_students_ok"] = True
    min_age, max_age = entity.get("minAge"), entity.get("maxAge")
    has_min = isinstance(min_age, int) and not isinstance(min_age, bool) and min_age > 0
    # maxAge 0 means no limit (the page then shows "At least N" or "No preference"); ut1747098 has minAge 25,
    # maxAge 45 and shows "Age: 25 - 45".
    has_max = isinstance(max_age, int) and not isinstance(max_age, bool) and 16 <= max_age <= 99 and \
        (not has_min or max_age >= min_age)
    if has_min and has_max:
        facts.append("Age: %d - %d" % (min_age, max_age))
    elif has_min:
        facts.append("Age: At least %d" % min_age)
    elif has_max:
        facts.append("Age: Up to %d" % max_age)
    if has_min and min_age > 18:
        extra["site_min_age"] = min_age
    if has_max:
        extra["site_max_age"] = max_age
    # freePlaces 1 is shown as "Space for 1 person" (ut1698524, ut1749760, ut260681); 0 shows no such line.
    places = entity.get("freePlaces")
    if isinstance(places, int) and not isinstance(places, bool) and places >= 1:
        extra["places_free"] = places            # the places that can be rented now, not the home's size
        facts.append("Space for 1 person" if places == 1 else "Space for %d people" % places)
    registration = facilities.get("registration_possible")
    if registration == "no":
        extra["registration_possible"] = False
        facts.append("You can't register at this address (inschrijven)")
    elif registration == "yes":
        facts.append("You can register at this address (inschrijven)")
    # currentOccupancy is NOT used: it was 0 on every saved page, so whether it counts future housemates or the
    # tenants who are leaving is unknown. A facility marked "shared" is the site's own statement.
    if any(facilities.get(k) == "shared" for k in ("kitchen", "bathroom", "toilet", "living_room")):
        extra["site_housemates"] = True
    must, what = _required_monthly(entity)
    if must:
        extra["mandatory_costs"] = must
        extra["mandatory_costs_note"] = "Required every month on top of the rent: %s" % what
        facts.append("Required every month: %s" % what)
    if facts:
        extra["site_facts"] = " · ".join(facts)[:200]


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    entity = _entity(html)

    def meta(**attrs: str) -> Optional[str]:
        tag = soup.find("meta", attrs=attrs)
        return tag.get("content") if tag and tag.get("content") else None

    # The full text is listing.entity.description; twitter:description holds the same text. og:description is a
    # 46-57 character summary ("Bills included, 100 m², Space for 2 people.") and is the last resort.
    text = (entity.get("description") if isinstance(entity.get("description"), str) and entity["description"].strip()
            else None) or meta(property="twitter:description") or meta(property="og:description") \
        or meta(name="description")
    if text is None:
        raise SourceError("layout changed: housinganywhere detail page has no description")
    listing.description = clean_text(text, keep_lines=True)
    summary = meta(name="description") or ""
    m = re.search(r"(\d+)\s*bedroom", summary)
    if m and listing.bedrooms is None:
        listing.bedrooms = int(m.group(1))
    if listing.available_from is None:
        periods = entity.get("bookablePeriods")
        if isinstance(periods, list) and periods and isinstance(periods[0], dict):
            listing.available_from = _date(periods[0].get("from"))
    _tenant_extra(listing, entity)
    return listing


class HousingAnywhere(Source):
    name = "housinganywhere"
    label = "HousingAnywhere"
    homepage = "https://housinganywhere.com"
    cities = tuple(_CITY_PATHS)
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
