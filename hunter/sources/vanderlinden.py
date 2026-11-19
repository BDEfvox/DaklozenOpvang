"""Makelaardij Van der Linden: large rental agency, mostly Amsterdam/Almere/Zaandam/Diemen.

https://www.vanderlinden.nl/woning-huren/ is one server-rendered page with every available rental (~90).
One request in total (per_city=False); the runner filters listings to the configured cities.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
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
            # The card shows one amount for a whole home and for one place in a shared home alike: what the rent
            # buys is known once the listing's own page has been read (apply_details).
            extra={"label": label or None, "rent_basis_unread": True},
        ))
    return out


_PP_RE = re.compile(r"€\s?(?:\d{1,3}(?:\.\d{3})*|\d+)(?:,-|,\d{2})?\s*(?:all-in\s*)?(?:per\s+maand\s+)?per\s+persoon", re.I)
_ROOMS_RE = re.compile(r"Aantal kamers\s*(\d+)", re.I)
_BEDS_RE = re.compile(r"Aantal slaapkamers\s*(\d+)", re.I)
_AMOUNT = r"€\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,-|,\d{2})?"
_SERVICE_RE = re.compile(r"servicekosten\s*:?\s*" + _AMOUNT + r"\s*(?:per\s+maand|p/m|p\.m\.|/\s*maand)", re.I)
_BARE_RE = re.compile(r"kale\s+huur(?:prijs)?\s*:?\s*" + _AMOUNT, re.I)
_TOTAL_RE = re.compile(r"totaal\s+maandbedrag\s*:?\s*" + _AMOUNT, re.I)
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
# "Beschikbaar per 1 november 2026!" and "Per 15 november 2026 beschikbaar!"
_AVAIL_RE = re.compile(r"(?:beschikbaar\s+(?:per|vanaf)\s+(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b"
                       r"|\bper\s+(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\s+beschikbaar\b)", re.I)
_SHARED_HOME_RE = re.compile(r"woonruimte\s+in\s+een\s+gedeelde\s+2peer-woning", re.I)
_FACT_LINE_RE = re.compile(r"^[-•\s]*((?:kale huurprijs|servicekosten|totaal maandbedrag)\s*:.*?)[.;]?$", re.I)
_UNFURNISHED_RE = re.compile(r"niet\s+gemeubileerd|ongemeubileerd|niet\s+gestoffeerd", re.I)
_FURNISHED_RE = re.compile(r"meubilering\s+en\s+stoffering\s+inbegrepen|(?:woning|appartement)\s+is\s+(?:volledig\s+)?gemeubileerd", re.I)
_UPHOLSTERED_RE = re.compile(r"(?:woning|appartement)\s+is\s+(?:volledig\s+)?gestoffeerd", re.I)


def _facts(parts: List[Optional[str]]) -> Optional[str]:
    """The page's own words behind the extra keys: whole quotes only, at most 200 characters."""
    out = ""
    for part in (p.strip(" ;") for p in parts if p and p.strip(" ;")):
        joined = out + ("; " if out else "") + part
        if len(joined) <= 200:
            out = joined
        elif not out:
            out = part[:200]
    return out or None


def apply_details(listing: Listing, html: str) -> Listing:
    """Description (the listing's own text only), rooms/bedrooms and what the text states: service costs,
    availability, furnishing, studio, and the amounts of a 2Peer shared home (reported as the page
    states them: per person, never multiplied)."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "form", "nav", "footer", "header", "noscript"]):
        t.decompose()
    page = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).replace("\xa0", " ")
    heading = soup.find("h2", string=re.compile(r"^\s*Over de woning\s*$"))
    if heading is None:
        raise SourceError("layout changed: vanderlinden detail page has no 'Over de woning' section")
    block = heading.find_next_sibling("p")
    if block is None:
        raise SourceError("layout changed: vanderlinden 'Over de woning' section has no listing text")
    for junk in block.select("a.readmore"):
        junk.decompose()
    lines = [re.sub(r"\s+", " ", x.replace("\xa0", " ")).strip() for x in block_text(block).split("\n")]
    lines = [x for x in lines if x]
    desc = "\n".join(lines)
    if not desc:
        raise SourceError("layout changed: vanderlinden 'Over de woning' section is empty")
    listing.description = clean_text(desc, keep_lines=True)
    listing.extra.pop("rent_basis_unread", None)      # the page has been read: its text says what the rent buys

    m = _ROOMS_RE.search(page)
    if m:
        listing.rooms = int(m.group(1))
    m = _BEDS_RE.search(page)
    if m:
        listing.bedrooms = int(m.group(1))
    if listing.price_total is None:
        price = soup.select_one("h2.price")
        listing.price_total = parse_price(_text(price)) if price is not None else None

    facts: List[Optional[str]] = []
    if _PP_RE.search(desc):
        listing.extra["price_per_person"] = True
        for line in lines:
            m = _FACT_LINE_RE.match(line)
            if m:
                facts.append(re.split(r",\s*inclusief", m.group(1))[0])
    if _SHARED_HOME_RE.search(desc):
        listing.extra["site_housemates"] = True
        facts.append(_SHARED_HOME_RE.search(desc).group(0))
    m = _SERVICE_RE.search(desc)
    if m:
        listing.extra["service_costs"] = parse_price(m.group(1))
        if not any(f and f.lower().startswith("servicekosten") for f in facts):
            facts.append(m.group(0))
        # "Kale huurprijs: € 966 ... Servicekosten: € 195 ... Totaal maandbedrag: € 1.161": the page's own sum
        # shows that the listed rent is the bare rent and the service costs come on top of it.
        bare, total = _BARE_RE.search(desc), _TOTAL_RE.search(desc)
        if bare and total and listing.price_total is not None:
            bare_n, total_n, svc_n = parse_price(bare.group(1)), parse_price(total.group(1)), listing.extra["service_costs"]
            if bare_n == listing.price_total and svc_n and bare_n + svc_n == total_n:
                listing.extra["service_costs_on_top"] = True
                listing.extra["service_costs_note"] = re.sub(r"\s+", " ", m.group(0)).strip()
    m = _AVAIL_RE.search(desc)  # "Beschikbaar per 1 november 2026!"; without a year the page gives no date
    if m:
        day, month, year = [g for g in m.groups() if g]
        listing.available_from = "%s-%02d-%02d" % (year, _MONTHS[month.lower()], int(day))
    if _UNFURNISHED_RE.search(desc):
        listing.furnished = "unfurnished"
    elif _FURNISHED_RE.search(desc):
        listing.furnished = "furnished"
    elif _UPHOLSTERED_RE.search(desc):
        listing.furnished = "upholstered"
    if any(re.sub(r"^[•\-\s]+|[•\-\s]+$", "", x).lower() == "studio" for x in lines):
        listing.property_type = "studio"
    site_facts = _facts(facts) if listing.extra.keys() & {"price_per_person", "site_housemates", "service_costs"} else None
    if site_facts:
        listing.extra["site_facts"] = site_facts
    return listing


class VanDerLinden(Source):
    name = "vanderlinden"
    label = "Van der Linden"
    homepage = "https://www.vanderlinden.nl/woning-huren/"
    cities = ("amsterdam",)
    per_city = False
    rent_basis_from_page = True    # "€ 966 per maand" is one place in a home for two, or a whole home
    note = "Reply via the agency (free)"
    # every listing carries the same "Inkomensrichtlijnen" block: 3.5x the rent alone, 4x together
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        html = http.get_text(BASE + "/woning-huren/")
        out = parse_page(html)
        if city in CITIES:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = VanDerLinden()
