"""Rotsvast: rental agency with branches in Den Haag, Amsterdam and Leiden. Server-rendered list.

https://www.rotsvast.nl/huren/?search=<City>   (10 per page; available listings come before the rented ones,
so page 1 is enough unless it contains no rented card, then page 2 is read as well: max 2 requests per city).
The card icons are: surface, bed (= bedrooms, checked against the detail page), door (interior), clock.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://www.rotsvast.nl"
_SEARCH = {"den-haag": "Den Haag", "amsterdam": "Amsterdam", "leiden": "Leiden"}
_FURNISHED = (("gemeubileerd", "furnished"), ("gestoffeerd", "upholstered"), ("kaal", "unfurnished"))


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def parse_page(html: str, city: str):
    """Returns (listings, has_rented_card, has_next_page)."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".house-list__overview__items") is None:
        raise SourceError("layout changed: rotsvast result container not found")
    cards = [a for a in soup.select(".house-list__overview__items a.card--house")
             if re.search(r"-h\d+/?$", a.get("href") or "")]
    has_next = soup.select_one("a[href*='/huren/page/2/']") is not None
    if not cards:
        # an empty search shows a message; a page without cards and without it is suspicious
        if "geen resultaten" in soup.get_text(" ").lower() or "geen woningen" in soup.get_text(" ").lower():
            return [], False, False
        raise SourceError("layout changed: rotsvast page has no listing cards")
    out: List[Listing] = []
    any_rented = False
    for a in cards:
        label = _text(a.select_one(".card-house__label")).lower()
        if "verhuurd" in label or "onder optie" in label:
            any_rented = True
            continue
        texts = a.select(".card-house__text")
        if not texts or not _text(texts[0]):
            raise SourceError("layout changed: rotsvast card without town")
        town = _text(texts[0])
        if city_slug(town) != city:
            continue
        address = _text(a.select_one(".card-house__title")) or None
        price = parse_price(_text(texts[-1])) if len(texts) > 1 else None  # some cards have no price
        if price is not None and price < 100:
            price = None  # placeholder prices such as "EUR 10 p.m." on unfinished listings
        area = bedrooms = None
        furnished = avail_text = None
        for li in a.select(".card-house__list li"):
            icon = li.find("i")
            cls = " ".join(icon.get("class") or []) if icon else ""
            val = _text(li)
            if "icon-surface" in cls:
                area = parse_int(val)
            elif "icon-bed" in cls:
                bedrooms = parse_int(val)
            elif "icon-door" in cls:
                for key, f in _FURNISHED:
                    if key in val.lower():
                        furnished = f
            elif "icon-clock" in cls:
                avail_text = val
        href = a["href"]
        sid = re.search(r"-(h\d+)/?$", href).group(1)
        img = a.select_one("img[data-lazy-src]") or a.select_one("img")
        image = (img.get("data-lazy-src") or img.get("src")) if img else None
        if image and image.startswith("data:"):
            image = None
        out.append(Listing(
            source="rotsvast",
            source_id=sid,
            url=href,
            title="%s, %s" % (address or "Woning", town),
            city=city,
            price_total=price,
            bedrooms=bedrooms,
            area_m2=area,
            furnished=furnished,
            address=address,
            available_from=_iso(avail_text),
            image_url=image,
            extra={"availability": avail_text},
        ))
    return out, any_rented, has_next


class Rotsvast(Source):
    name = "rotsvast"
    label = "Rotsvast"
    homepage = "https://www.rotsvast.nl/huren/"
    cities = ("den-haag", "amsterdam", "leiden")
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        term = _SEARCH.get(city)
        if not term:
            return []
        q = quote_plus(term)
        listings, rented, has_next = parse_page(http.get_text("%s/huren/?search=%s" % (BASE, q)), city)
        if has_next and not rented:
            more, _, _ = parse_page(http.get_text("%s/huren/page/2/?search=%s" % (BASE, q)), city)
            listings += more
        return listings

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        soup = BeautifulSoup(http.get_text(listing.url), "html.parser")
        desc = soup.select_one(".house-description")
        if desc is None:
            return listing
        text = clean_text(desc.get_text(" ", strip=True).replace("Beschrijving", "", 1).replace("Lees verder Lees minder", ""))
        listing.description = text
        if text:
            m = re.search(r"(\d+)[- ]kamer", text, re.I)
            if m:
                listing.rooms = int(m.group(1))
            low = text.lower()
            if "excl. g/w/e" in low or "exclusief g/w/e" in low:
                listing.price_includes_utilities = False
            if "appartement" in low or "bovenwoning" in low or "benedenwoning" in low:
                listing.property_type = listing.property_type or "apartment"
            elif "woonhuis" in low or "eengezins" in low or "tussenwoning" in low or "hoekwoning" in low:
                listing.property_type = listing.property_type or "house"
        return listing


SOURCE = Rotsvast()
