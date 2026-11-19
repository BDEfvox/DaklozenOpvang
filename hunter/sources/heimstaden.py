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
from ..models import CITIES, Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError, summary_description

URL = "https://heimstaden.com/nl/huurwoningen/"
_TYPES = {"apartment": "apartment", "house": "house", "room": "room", "studio": "studio", "parking": "parking"}
_BEDROOM_WORDS = {"twee": 2, "drie": 3, "vier": 4}
_BEDROOM_NUMBER = r"\d+|" + "|".join(_BEDROOM_WORDS)
# "twee slaapkamers", "1 slaapkamer"; "een slaapkamer" is left out, because "een" is also the article
# ("een slaapkamer aan de voorzijde en een aan de achterzijde"). group(2) is set for a range.
_BEDROOMS = re.compile(
    rf"\b({_BEDROOM_NUMBER})(\s+(?:tot|t/m|tot\s+en\s+met|of|à)\s+(?:{_BEDROOM_NUMBER})|\s*[-–]\s*(?:{_BEDROOM_NUMBER}))?"
    rf"\s+slaapkamers\b|\b(\d+)\s+slaapkamer\b",
    re.I,
)


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
    cities = tuple(CITIES)
    per_city = False
    note = "Reply via Heimstaden (free)"
    # gross income at least 4x the basic rent (heimstaden.com FAQ, "Welke inkomenseisen hanteren jullie?")
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out = parse_page(http.get_text(URL))
        if city in self.cities:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        html = http.get_text(listing.url)
        soup = BeautifulSoup(html, "html.parser")
        intro = soup.select_one(".project-intro")
        if intro is not None:
            listing.description = clean_text(intro.get_text(" ", strip=True))
        else:
            summary_description(listing, html, "heimstaden")
        for tr in soup.select(".project-facts-table tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is not None and td is not None and "kamers" in _text(th).lower():
                listing.rooms = parse_int(_text(td)) or listing.rooms
        bm = _BEDROOMS.search(listing.description or "")
        # A range ("1 tot 3 slaapkamers") is a page for several homes: no single count is right, so the
        # count stays unknown and the filters fall back on the room count, marked as unconfirmed.
        if bm and not bm.group(2):
            count = (bm.group(1) or bm.group(3)).lower()
            listing.bedrooms = int(count) if count.isdecimal() else _BEDROOM_WORDS[count]
        sm = re.search(r"Servicekosten\s+(\d+)\s+euro", soup.get_text(" ", strip=True))
        if sm:
            listing.extra["service_costs"] = int(sm.group(1))
        return listing


SOURCE = Heimstaden()
