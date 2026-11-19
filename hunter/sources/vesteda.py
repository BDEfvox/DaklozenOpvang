"""Vesteda: large private landlord. Data from the site's own units API (the one its search page calls).

POST https://www.vesteda.com/api/units/search/facet with the search page's filter object.
One request per city (coordinates are fixed, so no geocoding calls). robots.txt allows everything.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError, summary_description

BASE = "https://www.vesteda.com"
API = BASE + "/api/units/search/facet"

# city slug -> (search label, latitude, longitude, radius km)
_PLACES = {
    "den-haag": ("Den Haag, Nederland", 52.0705, 4.3007, 5),
    "amsterdam": ("Amsterdam, Nederland", 52.3676, 4.9041, 5),
    "leiden": ("Leiden, Nederland", 52.1636, 4.4802, 5),
    "delft": ("Delft, Nederland", 52.0116, 4.3571, 5),
    "rotterdam": ("Rotterdam, Nederland", 51.9244, 4.4777, 5),
}

# UnitStatus in the site's JS: ForRent=1, Rented=2, RentedWithReservation=3, Reserved=4, New=5
_OPEN_STATUS = (1, 5)

_TYPES = (
    ("eengezins", "house"), ("woonhuis", "house"), ("hoekwoning", "house"), ("tussenwoning", "house"),
    ("grondgebonden", "house"), ("villa", "house"),
    ("studio", "studio"), ("kamer", "room"), ("parkeer", "parking"), ("garage", "parking"),
    ("appartement", "apartment"), ("woning", "apartment"), ("flat", "apartment"), ("maisonnette", "apartment"),
    ("penthouse", "apartment"),
)


_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)
_AMOUNT = re.compile(r"(?:€|euro)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*euro", re.I)
# a mandatory parking place in a description: "verplichte afname van een parkeerplaats voor €157 per maand"
_PARKING_TEXT = re.compile(
    r"(?:verplichte?\s+afname\s+(?:van\s+)?(?:een\s+|de\s+)?parkeer(?:plaats|gelegenheid)|"
    r"afname\s+(?:van\s+)?(?:een\s+|de\s+)?parkeer(?:plaats|gelegenheid)\s+(?:is\s+)?verplicht)[^.]{0,80}",
    re.I,
)
FACTS_MAX = 200


def _long_date(text: Optional[str]) -> Optional[str]:
    m = _LONG_DATE.search(text or "")
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1))) if m else None


def _amount(text: str) -> Optional[int]:
    m = _AMOUNT.search(text or "")
    if not m:
        return None
    return int(float((m.group(1) or m.group(2)).replace(",", ".")))


def _add_fact(listing: Listing, fact: str) -> None:
    facts = listing.extra.get("site_facts")
    if isinstance(facts, str) and facts:
        if fact in facts:
            return
        fact = facts + "; " + fact
    listing.extra["site_facts"] = fact[:FACTS_MAX]


def _kenmerken(html: str) -> List[Tuple[str, str]]:
    """The 'Kenmerken' definition lists of the detail page, in page order: [(label, value)].
    The page leaves <dt>/<dd> unclosed, so the pairs are cut from the raw markup."""
    pairs: List[Tuple[str, str]] = []
    for dl in re.findall(r"<dl[^>]*c-definition-list[^>]*>(.*?)</dl>", html, re.S):
        for m in re.finditer(r"<dt[^>]*>(.*?)<dd[^>]*>(.*?)(?=<dt[^>]*>|$)", dl, re.S):
            label = BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
            value = BeautifulSoup(m.group(2), "html.parser").get_text(" ", strip=True)
            pairs.append((label.replace("\xa0", " "), value.replace("\xa0", " ")))
    return pairs


def _description(html: str) -> Optional[str]:
    """The home's own text: the cell after the heading 'Korte omschrijving van deze woning'."""
    soup = BeautifulSoup(html, "html.parser")
    for h in soup.find_all("h2"):
        if h.get_text(" ", strip=True).lower().startswith("korte omschrijving"):
            cell = h.find_parent(class_="o-layout__cell")
            nxt = cell.find_next_sibling(class_="o-layout__cell") if cell is not None else None
            if nxt is not None:
                for br in nxt.find_all("br"):
                    br.replace_with(" ")
                return clean_text(nxt.get_text(" ", strip=True).replace("\xa0", " "))
    return None


def _ptype(label: Optional[str]) -> str:
    low = (label or "").lower()
    for key, val in _TYPES:
        if key in low:
            return val
    return "apartment"


class Vesteda(Source):
    name = "vesteda"
    label = "Vesteda"
    homepage = "https://www.vesteda.com/nl"
    cities = tuple(_PLACES)
    note = "Free account on hurenbij.vesteda.com needed to reply"
    # 3.5x the rent alone, 4x together; "Deze norm geldt voor iedereen" (vesteda.com, inkomens-voorwaarden)
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        place = _PLACES.get(city)
        if not place:
            return []
        label, lat, lng, radius = place
        payload = {
            "filters": [], "latitude": lat, "longitude": lng, "place": label, "placeType": 1,
            "radius": radius, "sorting": 0, "priceFrom": 500, "priceTo": self.max_rent(2400), "language": "nl",
        }
        data = http.post_json(API, payload)
        return self.parse(data, city)

    def parse(self, data: Any, city: str) -> List[Listing]:
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict) or not isinstance(results.get("objects"), list):
            raise SourceError("layout changed: vesteda units API has no results.objects")
        out: List[Listing] = []
        for obj in results["objects"]:
            if obj.get("status") not in _OPEN_STATUS:
                continue  # rented / reserved
            slug = city_slug(obj.get("city"))
            if slug != city:
                continue  # radius search also returns Leiderdorp, Voorschoten, Diemen, ...
            price = parse_price(obj.get("priceUnformatted"))
            if not price or not obj.get("url") or obj.get("id") is None:
                continue
            number = f"{obj.get('houseNumber') or ''}{obj.get('houseNumberAddition') or ''}".strip()
            street = (obj.get("street") or "").strip()
            address = f"{street} {number}".strip() or None
            notes = []
            if obj.get("onlySixtyFivePlus"):
                notes.append("Alleen voor 65+")
            if obj.get("ageFrom"):
                notes.append(f"Minimale leeftijd {obj['ageFrom']} jaar")
            if obj.get("onlyMiddleRent"):
                notes.append("Middenhuur: inkomenseis")
            if obj.get("prioritizeKeyProfessions"):
                notes.append("Voorrang voor cruciale beroepen")
            if obj.get("suitedForHomeSharers"):
                notes.append("Geschikt voor woningdelers")
            if obj.get("complex"):
                notes.append(f"Complex {obj['complex']}")
            beds = obj.get("numberOfBedRooms")
            extra: Dict[str, Any] = {"status": obj.get("status"), "complex": obj.get("complex")}
            facts = []
            age_from = obj.get("ageFrom")
            if isinstance(age_from, int) and not isinstance(age_from, bool) and age_from > 18:
                extra["site_min_age"] = age_from
                facts.append("minimum age %d" % age_from)
            if obj.get("onlySixtyFivePlus") is True:
                extra["site_min_age"] = max(65, extra.get("site_min_age", 0))
                facts.append("only for people of 65 and older")
            if obj.get("suitedForHomeSharers") is True:
                extra["site_sharers_ok"] = True
                facts.append("suited for home sharers")
            if facts:
                # Vesteda's own data fields (ageFrom, onlySixtyFivePlus, suitedForHomeSharers), put in words
                extra["site_facts"] = ("Vesteda lists this home as: " + ", ".join(facts))[:FACTS_MAX]
            if notes:
                extra["api_notes"] = clean_text(". ".join(notes))
            out.append(Listing(
                source=self.name,
                source_id=str(obj["id"]),
                url=BASE + obj["url"],
                title=f"{obj.get('entitysubtypelabel') or 'Woning'} {address or ''}, {obj.get('city')}".replace("  ", " "),
                city=slug,
                price_total=price,
                price_includes_utilities=None,
                bedrooms=int(beds) if beds else None,
                area_m2=parse_int(obj.get("size")) or None,
                property_type=_ptype(obj.get("entitysubtypelabel")),
                address=address,
                postcode=obj.get("postalCode"),
                description=clean_text(". ".join(notes)) if notes else None,
                image_url=obj.get("imageBig") or obj.get("imageSmall"),
                extra=extra,
            ))
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        """One request: the detail page. Its Kenmerken and its own description fill what the API does not give.
        The page's JSON-LD offers.availability says OutOfStock on open units and is never used."""
        html = http.get_text(listing.url)
        rows = _kenmerken(html)
        if not rows:
            raise SourceError("layout changed: vesteda detail page has no Kenmerken lists")
        by = {label.lower(): value for label, value in rows}
        avail = _long_date(by.get("beschikbaar vanaf"))
        if avail:
            listing.available_from = avail
        costs = parse_price(by.get("servicekosten p.m."))
        if costs is not None:
            listing.extra["service_costs"] = costs
        beds = parse_int(by.get("aantal slaapkamers"))
        if beds is not None:
            listing.bedrooms = beds
        if listing.area_m2 is None:
            listing.area_m2 = parse_int(by.get("woonoppervlakte m²")) or None
        if "woningdelen" in by.get("geschikt voor", "").lower():
            listing.extra["site_sharers_ok"] = True
            _add_fact(listing, "Geschikt voor: %s" % by["geschikt voor"])

        text = _description(html)
        if text is None:
            listing = summary_description(listing, html, "vesteda")
            text = listing.description
        notes = listing.extra.get("api_notes")
        listing.description = clean_text("%s %s" % (text, notes) if text and notes else text or notes)

        parking = by.get("soort parkeergelegenheid", "")
        found = None  # (amount, the page's own words)
        if re.search(r"verplicht", parking, re.I) and _amount(parking) is not None:
            found = (_amount(parking), parking)
        elif text:
            pm = _PARKING_TEXT.search(text)
            if pm and _amount(pm.group(0)) is not None:
                found = (_amount(pm.group(0)), pm.group(0).strip())
        if found:
            listing.extra["mandatory_costs"] = found[0]
            listing.extra["mandatory_costs_note"] = found[1][:200]
        rm = re.search(r"(\d+)[- ]kamer", text or "", re.I)
        if rm and listing.rooms is None:
            listing.rooms = int(rm.group(1))
        return listing


SOURCE = Vesteda()
