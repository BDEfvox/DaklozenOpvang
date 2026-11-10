"""ikwilhuren.nu (MVGM): large institutional landlord/manager; many Den Haag and Amsterdam homes.

The search page can only filter per city through a CSRF-protected form plus two geocoding POSTs, which would be
4 requests per city. Instead we read the nationwide list sorted by "newest offered":
GET https://ikwilhuren.nu/aanbod/?sort=aanbodDESC[&page=N]  (10 per page, server-rendered), pages 1-3,
and keep what lies in the three cities. New listings always appear on the first pages.
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

BASE = "https://ikwilhuren.nu"
PAGES = 3
_TYPES = (
    ("kamer", "room"), ("studio", "studio"), ("parkeer", "parking"), ("garage", "parking"),
    ("eengezins", "house"), ("woonhuis", "house"), ("tussenwoning", "house"), ("hoekwoning", "house"),
    ("villa", "house"), ("herenhuis", "house"),
    ("appartement", "apartment"), ("maisonnette", "apartment"), ("benedenwoning", "apartment"),
    ("bovenwoning", "apartment"), ("penthouse", "apartment"), ("galerij", "apartment"), ("portiek", "apartment"),
)
_NOT_AVAILABLE = ("verhuurd", "optie", "gereserveerd")


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def _ptype(label: str) -> Optional[str]:
    low = label.lower()
    for key, val in _TYPES:
        if key in low:
            return val
    return "other" if low else None


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def parse_page(html: str) -> List[Listing]:
    """All listings on one page, any town (the caller filters by city)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.card-woning")
    if not cards:
        if soup.select_one("form#frmFilter") is not None and "geen" in soup.get_text(" ").lower():
            return []
        raise SourceError("layout changed: ikwilhuren page has no listing cards")
    out: List[Listing] = []
    for card in cards:
        link = card.select_one("a.stretched-link[href*='/object/']")
        if link is None:
            raise SourceError("layout changed: ikwilhuren card without /object/ link")
        href = link["href"]
        m = re.search(r"-([0-9a-f]{32})/?$", href)
        if not m:
            raise SourceError("layout changed: ikwilhuren object link has no id: %s" % href)
        badge = _text(card.select_one(".badge")).lower()
        if any(w in badge for w in _NOT_AVAILABLE):
            continue
        title = _text(link)
        place = None
        for span in card.select(".card-body > span"):
            pm = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", _text(span))
            if pm:
                place = (pm.group(1).replace(" ", ""), pm.group(2).strip())
                break
        if place is None:
            raise SourceError("layout changed: ikwilhuren card without 'postcode town' line")
        slug = city_slug(place[1])
        if slug is None:
            continue
        price = parse_price(_text(card.select_one(".dotted-spans .fw-bold")))
        area = bedrooms = None
        for span in card.select(".dotted-spans span:not(.fw-bold)"):
            txt = _text(span).lower()
            if "slaapkamer" in txt:
                bedrooms = parse_int(txt)
            elif "m" in txt and area is None and re.search(r"\d+\s*m", txt):
                area = parse_int(txt)
        ptype_label = title.split(" ")[0] if title else ""
        address = title[len(ptype_label):].strip() or None
        img = card.select_one("img")
        src = img.get("src") if img else None
        if src and src.startswith("//"):
            src = "https:" + src
        avail = _text(card.select_one(".small .d-flex"))
        out.append(Listing(
            source="ikwilhuren",
            source_id=m.group(1),
            url=BASE + href,
            title="%s, %s" % (title, place[1]),
            city=slug,
            price_total=price,
            bedrooms=bedrooms,
            area_m2=area,
            property_type=_ptype(ptype_label),
            address=address,
            postcode=place[0],
            available_from=_iso(avail),
            image_url=src,
            extra={"availability": avail or None},
        ))
    return out


class IkWilHuren(Source):
    name = "ikwilhuren"
    label = "MVGM (ikwilhuren.nu)"
    homepage = "https://ikwilhuren.nu/aanbod/"
    cities = ("den-haag", "amsterdam", "leiden")
    per_city = False
    note = "Free account on ikwilhuren.nu needed to reply"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out: List[Listing] = []
        seen = set()
        for page in range(1, PAGES + 1):
            url = BASE + "/aanbod/?sort=aanbodDESC" + ("&page=%d" % page if page > 1 else "")
            for l in parse_page(http.get_text(url)):
                if l.source_id not in seen:
                    seen.add(l.source_id)
                    out.append(l)
        if city in self.cities:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        soup = BeautifulSoup(http.get_text(listing.url), "html.parser")
        text = soup.get_text(" ", strip=True).replace("\xa0", " ")
        m = re.search(r"Omschrijving\s+(.*?)\s+(?:Gebruik van deze site|Kenmerken)", text, re.S)
        if m:
            listing.description = clean_text(m.group(1))
        sm = re.search(r"Servicekosten\s+€\s*([\d.,]+)", text)
        if sm:
            listing.extra["service_costs"] = parse_price(sm.group(1))
        if listing.bedrooms is None and listing.description:
            bm = re.search(r"(\d+)\s*slaapkamers?", listing.description, re.I)
            if bm:
                listing.bedrooms = int(bm.group(1))
        if listing.rooms is None and listing.description:
            rm = re.search(r"(\d+)[- ]kamer", listing.description, re.I)
            if rm:
                listing.rooms = int(rm.group(1))
        return listing


SOURCE = IkWilHuren()
