"""123Wonen: rental agency (Den Haag, Amsterdam, Leiden branches). Server-rendered list, newest first.

https://www.123wonen.nl/huurwoningen/in/<city>/sort/newest  (12 listings per page, one request per city).
Rented listings stay on the page with a "Verhuurd" label; they are skipped here.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://www.123wonen.nl"

_TYPES = (
    ("kamer", "room"), ("studio", "studio"), ("parkeer", "parking"), ("garage", "parking"),
    ("eengezins", "house"), ("woonhuis", "house"), ("tussenwoning", "house"), ("hoekwoning", "house"),
    ("villa", "house"), ("bungalow", "house"), ("herenhuis", "house"), ("grachtenpand", "house"),
    ("appartement", "apartment"), ("flat", "apartment"), ("maisonnette", "apartment"),
    ("benedenwoning", "apartment"), ("bovenwoning", "apartment"), ("penthouse", "apartment"),
    ("portiek", "apartment"), ("galerij", "apartment"), ("loft", "apartment"),
)
_FURNISHED = (("gemeubileerd", "furnished"), ("gestoffeerd", "upholstered"), ("kaal", "unfurnished"))
_NOT_AVAILABLE = ("verhuurd", "optie", "voorbehoud", "gereserveerd")


def _ptype(label: Optional[str]) -> Optional[str]:
    low = (label or "").lower()
    for key, val in _TYPES:
        if key in low:
            return val
    return "other" if low else None


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def parse_page(html: str, city: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    counter = None
    for el in soup.find_all(string=re.compile(r"^\s*Gevonden\s*$")):
        nxt = el.find_next("i")
        if nxt is not None:
            counter = parse_int(nxt.get_text())
            break
    if counter is None:
        raise SourceError("layout changed: 123wonen result counter ('Gevonden') not found")
    cards = soup.select("div.pandlist-container")
    if counter == 0:
        return []
    if not cards:
        raise SourceError("layout changed: 123wonen shows %d results but no listing cards" % counter)

    out: List[Listing] = []
    for card in cards:
        classes = card.get("class") or []
        status = _text(card.select_one(".pand-status")).lower()
        if "verhuurd" in classes or any(w in status for w in _NOT_AVAILABLE):
            continue
        link = card.select_one("a[href*='/huur/']")
        href = link.get("href") if link else None
        if not href:
            m = re.search(r"location\.href='([^']+)'", card.get("onclick") or "")
            href = m.group(1) if m else None
        if not href:
            raise SourceError("layout changed: 123wonen card without detail link")
        m = re.search(r"-(\d+-\d+)/?$", href)
        if not m:
            raise SourceError("layout changed: 123wonen detail link has no id: %s" % href)
        sid = m.group(1)
        title_el = card.select_one(".pand-title")
        town = _text(title_el).split(",")[0].strip() if title_el else ""
        if city_slug(town) != city:
            continue
        address_el = card.select_one(".pand-address")
        address = _text(address_el) or None

        specs: Dict[str, str] = {}
        for li in card.select(".pand-specs li"):
            spans = li.find_all("span", recursive=False)
            if len(spans) >= 2:
                specs[_text(spans[0]).lower()] = _text(spans[1])
        price = parse_price(_text(card.select_one(".pand-price")))
        furnished = None
        for key, val in _FURNISHED:
            if key in specs.get("interieur", "").lower():
                furnished = val
        avail = specs.get("beschikbaarheid", "")
        available_from = _iso(avail)  # "Vanaf 01-12-2026"; "Per direct" -> None
        img = card.select_one(".pand-image")
        slogan = _text(card.select_one(".pand-slogan"))
        ptype = _ptype(specs.get("type"))
        out.append(Listing(
            source="123wonen",
            source_id=sid,
            url=href,
            title=("%s %s, %s" % (specs.get("type") or "Woning", address or "", town)).replace("  ", " "),
            city=city,
            price_total=price,
            bedrooms=parse_int(specs.get("slaapkamers")),
            area_m2=parse_int(specs.get("woonoppervlakte")),
            property_type=ptype,
            furnished=furnished,
            address=address,
            available_from=available_from,
            description=clean_text(slogan + ". " + specs.get("max. huurperiode", "")) if slogan else
            clean_text(specs.get("max. huurperiode")),
            image_url=img.get("data-src") if img else None,
            posted_at=_iso(specs.get("aangeboden sinds")),
            extra={"availability": avail or None},
        ))
    return out


class Wonen123(Source):
    name = "123wonen"
    label = "123Wonen"
    homepage = "https://www.123wonen.nl"
    cities = ("den-haag", "amsterdam", "leiden")
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        if city not in self.cities:
            return []
        html = http.get_text("%s/huurwoningen/in/%s/sort/newest" % (BASE, city))
        return parse_page(html, city)


SOURCE = Wonen123()
