"""Heimstaden Nederland: private landlord, a handful of vacant homes at a time across the country.

https://heimstaden.com/nl/huurwoningen/ is one server-rendered page with every vacant home (cards are
hidden with a CSS class and filtered client side). One request in total (per_city=False).
The number on a card is the number of rooms ("kamers", incl. living room), not bedrooms; the detail page
description usually mentions the bedrooms.
"""
from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

URL = "https://heimstaden.com/nl/huurwoningen/"
_TYPES = {"apartment": "apartment", "house": "house", "room": "room", "studio": "studio", "parking": "parking"}


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def parse_page(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.select_one("#hose-object-search-result-card-list-wrapper")
    if wrapper is None:
        raise SourceError("layout changed: heimstaden card list not found")
    cards = wrapper.select("div.object-card[data-object-id]")
    if not cards:
        if soup.select_one("[data-hose-search-form]") is not None:
            return []
        raise SourceError("layout changed: heimstaden page has no cards")
    out: List[Listing] = []
    for card in cards:
        link = card.select_one("a.object-card__inner")
        loc = _text(card.select_one(".object-card__location"))
        if link is None or not link.get("href") or not loc:
            raise SourceError("layout changed: heimstaden card without link/location")
        slug = city_slug(loc.split("/")[0].strip())
        if slug is None:
            continue
        address = _text(card.select_one(".object-card__address")) or None
        avail = _text(card.select_one(".object-card__availability"))
        img = card.select_one("img.object-card__photo")
        out.append(Listing(
            source="heimstaden",
            source_id=card["data-object-id"],
            url=link["href"],
            title="%s, %s" % (address or "Woning", loc.split("/")[0].strip()),
            city=slug,
            price_total=parse_price(_text(card.select_one(".object-card__data-prize"))),
            rooms=parse_int(_text(card.select_one(".object-card__data-rooms"))),
            area_m2=parse_int(_text(card.select_one(".object-card__data-size"))),
            property_type=_TYPES.get((link.get("data-hose-tracking-object-type") or "").lower(), None),
            address=address,
            image_url=img.get("src") if img else None,
            extra={"availability": avail or None},
        ))
    return out


class Heimstaden(Source):
    name = "heimstaden"
    label = "Heimstaden"
    homepage = "https://heimstaden.com/nl/huurwoningen/"
    cities = ("den-haag", "amsterdam", "leiden")
    per_city = False
    note = "Reply via Heimstaden (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out = parse_page(http.get_text(URL))
        if city in self.cities:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        soup = BeautifulSoup(http.get_text(listing.url), "html.parser")
        intro = soup.select_one(".project-intro")
        if intro is not None:
            listing.description = clean_text(intro.get_text(" ", strip=True))
        for tr in soup.select(".project-facts-table tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is not None and td is not None and "kamers" in _text(th).lower():
                listing.rooms = parse_int(_text(td)) or listing.rooms
        if listing.description:
            bm = re.search(r"(\d+|twee|drie|vier)\s+slaapkamers", listing.description, re.I)
            if bm:
                words = {"twee": 2, "drie": 3, "vier": 4}
                v = bm.group(1).lower()
                listing.bedrooms = words.get(v) or int(v)
        sm = re.search(r"Servicekosten\s+(\d+)\s+euro", soup.get_text(" ", strip=True))
        if sm:
            listing.extra["service_costs"] = int(sm.group(1))
        return listing


SOURCE = Heimstaden()
