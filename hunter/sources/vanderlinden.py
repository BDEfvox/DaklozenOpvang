"""Makelaardij Van der Linden: large rental agency, mostly Amsterdam/Almere/Zaandam/Diemen.

https://www.vanderlinden.nl/woning-huren/ is one server-rendered page with every available rental (~90).
One request in total (per_city=False); we keep what lies in the three cities (in practice Amsterdam).
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

BASE = "https://www.vanderlinden.nl"
_SKIP_LABELS = ("onder optie", "verhuurd", "senioren", "onder bod")


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def parse_page(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#zoekresultaten") is None:
        raise SourceError("layout changed: vanderlinden result container (#zoekresultaten) not found")
    cards = soup.select("div.woninginfo")
    if not cards:
        raise SourceError("layout changed: vanderlinden page has no listing cards")
    out: List[Listing] = []
    seen = set()
    for card in cards:
        link = card.select_one("a[href*='/huurwoning/']")
        if link is None:
            continue
        href = link["href"]
        m = re.search(r"/huurwoning/[^/]+/(\d+)/?$", href)
        if not m:
            raise SourceError("layout changed: vanderlinden detail link has no id: %s" % href)
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        label = _text(card.select_one(".fotolabel")).lower()
        if any(w in label for w in _SKIP_LABELS) or "seniorenhuisvesting" in _text(card).lower():
            continue
        loc_el = card.select_one(".fa-location-dot")
        town = _text(loc_el.parent) if loc_el is not None else ""
        town = re.sub(r"\s+", " ", town).strip()
        slug = city_slug(town)
        if slug is None:
            continue
        address = _text(card.select_one("strong")) or None
        price = parse_price(_text(card.select_one(".mt-2")))
        area = rooms = None
        for span in card.select("span.me-2"):
            icon = span.find("span", class_=re.compile(r"kiko-"))
            cls = " ".join(icon.get("class") or []) if icon else ""
            if "kiko-square-footage" in cls and area is None:
                area = parse_int(_text(span))
            elif "kiko-bedroom" in cls:
                # Despite the icon name this is "Aantal kamers" (rooms incl. living room) on the detail page.
                rooms = parse_int(_text(span))
        img = card.select_one("img")
        src = img.get("src") if img else None
        notes = []
        if "belangstellendenlijst" in label:
            notes.append("Belangstellendenlijst: inschrijven voor een bezichtiging")
        out.append(Listing(
            source="vanderlinden",
            source_id=sid,
            url=BASE + href if href.startswith("/") else href,
            title="%s, %s" % (address or "Woning", town),
            city=slug,
            price_total=price,
            rooms=rooms,
            area_m2=area,
            property_type="studio" if "studio" in (address or "").lower() else None,
            address=address,
            image_url=(BASE + src) if src and src.startswith("/") else src,
            description=clean_text(". ".join(notes)) if notes else None,
            extra={"label": label or None},
        ))
    return out


_PP_RE = re.compile(r"€\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,-|,\d{2})?\s*(?:all-in\s*)?per persoon", re.I)
_ALLIN_RE = re.compile(r"vanaf\s*€\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,-|,\d{2})?\s*all-in", re.I)
_ROOMS_RE = re.compile(r"Aantal kamers\s*(\d+)", re.I)
_BEDS_RE = re.compile(r"Aantal slaapkamers\s*(\d+)", re.I)


def apply_details(listing: Listing, html: str) -> Listing:
    """Description, rooms/bedrooms, and per-person pricing (e.g. the "2PEER" sharer homes list the
    price per person on the overview page)."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "form", "nav", "footer", "header", "noscript"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).replace("\xa0", " ")
    i = text.find("Over de woning")
    if i < 0:
        raise SourceError("layout changed: vanderlinden detail page has no 'Over de woning' section")
    desc = text[i + len("Over de woning"):]
    listing.description = clean_text(desc)
    m = _ROOMS_RE.search(text)
    if m:
        listing.rooms = int(m.group(1))
    m = _BEDS_RE.search(text)
    if m:
        listing.bedrooms = int(m.group(1))
    pp = _PP_RE.search(desc)
    if pp:
        per_person = parse_price(pp.group(1))
        listing.extra["price_per_person"] = per_person
        allin = _ALLIN_RE.search(desc)
        if allin:
            listing.price_total = parse_price(allin.group(1))
        elif per_person:
            beds = listing.bedrooms if listing.bedrooms else max((listing.rooms or 3) - 1, 2)
            listing.price_total = per_person * beds
    return listing


class VanDerLinden(Source):
    name = "vanderlinden"
    label = "Van der Linden"
    homepage = "https://www.vanderlinden.nl/woning-huren/"
    cities = ("amsterdam",)
    per_city = False
    note = "Reply via the agency (free)"
    # every listing carries the same "Inkomensrichtlijnen" block: 3.5x the rent alone, 4x together
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        html = http.get_text(BASE + "/woning-huren/")
        out = parse_page(html)
        if city in ("den-haag", "amsterdam", "leiden"):
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = VanDerLinden()
