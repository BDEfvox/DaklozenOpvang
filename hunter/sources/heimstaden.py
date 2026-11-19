"""Heimstaden Nederland: private landlord, a handful of vacant homes at a time across the country.

https://heimstaden.com/nl/huurwoningen/ is one server-rendered page with every vacant home (cards are
hidden with a CSS class and filtered client side). One request in total (per_city=False).
The number on a card is the number of rooms ("kamers", incl. living room), not bedrooms; the detail page
description usually mentions the bedrooms.
"""
from __future__ import annotations

import json
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


_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\.?,?\s+(\d{4})\b", re.I)
# "exclusief g/w/l", "excl. g/w/l", "inclusief g/w/l"; a "niet inclusief" counts as exclusive
_UTILITIES = re.compile(r"\b(niet\s+)?(excl(?:usief|\.)?|incl(?:usief|\.)?)\s+(?:de\s+)?g/w/l\b", re.I)


def _iso_date(text: Optional[str]) -> Optional[str]:
    """'Beschikbaar 1 december, 2026' -> '2026-12-01'. 'Direct' (or anything without a date) -> None."""
    m = _DATE.search(text or "")
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1))) if m else None


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
            available_from=_iso_date(avail),
            image_url=img.get("src") if img else None,
            extra={"availability": avail or None},
        ))
    return out


def _json_ld_postcode(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            offered = item.get("itemOffered") if isinstance(item, dict) else None
            address = offered.get("address") if isinstance(offered, dict) else None
            code = address.get("postalCode") if isinstance(address, dict) else None
            if isinstance(code, str) and re.match(r"^\d{4}\s?[A-Z]{2}$", code.strip(), re.I):
                return code.replace(" ", "").upper()
    return None


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
            if th is None or td is None:
                continue
            label = _text(th).lower()
            if "kamers" in label:
                listing.rooms = parse_int(_text(td)) or listing.rooms
            elif label.startswith("beschikbaar"):
                # "Direct" has no date. The JSON-LD availabilityStarts is not used: it is stale on "Direct" homes.
                listing.available_from = _iso_date(_text(td)) or listing.available_from
        if listing.postcode is None:
            listing.postcode = _json_ld_postcode(soup)
        um = _UTILITIES.search(listing.description or "")
        if um:
            inclusive = um.group(2).lower().startswith("incl")
            listing.price_includes_utilities = inclusive != bool(um.group(1))
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
