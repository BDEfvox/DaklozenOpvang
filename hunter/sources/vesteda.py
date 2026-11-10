"""Vesteda: large private landlord. Data from the site's own units API (the one its search page calls).

POST https://www.vesteda.com/api/units/search/facet with the search page's filter object.
One request per city (coordinates are fixed, so no geocoding calls). robots.txt allows everything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://www.vesteda.com"
API = BASE + "/api/units/search/facet"

# city slug -> (search label, latitude, longitude, radius km)
_PLACES = {
    "den-haag": ("Den Haag, Nederland", 52.0705, 4.3007, 5),
    "amsterdam": ("Amsterdam, Nederland", 52.3676, 4.9041, 5),
    "leiden": ("Leiden, Nederland", 52.1636, 4.4802, 5),
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
    cities = ("den-haag", "amsterdam", "leiden")
    note = "Free account on hurenbij.vesteda.com needed to reply"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        place = _PLACES.get(city)
        if not place:
            return []
        label, lat, lng, radius = place
        payload = {
            "filters": [], "latitude": lat, "longitude": lng, "place": label, "placeType": 1,
            "radius": radius, "sorting": 0, "priceFrom": 500, "priceTo": 2400, "language": "nl",
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
                extra={"status": obj.get("status"), "complex": obj.get("complex")},
            ))
        return out


SOURCE = Vesteda()
