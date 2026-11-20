"""LIV Residential: landlord with homes in Amsterdam, Rotterdam, Den Haag and elsewhere.

https://livresidential.nl/huurwoningen/<city> is one server-rendered page per city with every home in it (the
page for all cities shows only the first 20, so it is not used). One request per city; the page of a home gives
the bedrooms, the service costs and whether the home is "onder optie".
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://livresidential.nl"
# city slug -> the site's city page. Leiden and Delft have no page on the site.
CITY_PAGES = {"den-haag": "den-haag", "amsterdam": "amsterdam", "rotterdam": "rotterdam"}
_HREF_RE = re.compile(r"/huurwoningen/[^/?#]+/[^/?#]+/([^/?#]+)/?(?:[?#].*)?$")
_POSTCODE_RE = re.compile(r"\b(\d{4})\s?([A-Z]{2})\b")
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_AVAIL_RE = re.compile(r"Beschikbaar\s+vanaf\s+(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip() if el else ""


def _type(label: str) -> Optional[str]:
    t = label.strip().lower()
    if not t:
        return None
    if "parkeer" in t or "garage" in t or "berging" in t:
        return "parking"
    if t == "studio":
        return "studio"
    if t.startswith("kamer"):
        return "room"
    if "appartement" in t or "flat" in t or "penthouse" in t or "maisonnette" in t:
        return "apartment"
    if "woning" in t or "huis" in t:
        return "house"
    return None


def parse_page(html: str, city: Optional[str] = None) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    grid = None
    for h3 in soup.find_all("h3"):
        grid = h3.find_parent("div", class_=lambda c: bool(c) and "grid" in c)
        if grid is not None:
            break
    cards = [a for a in (grid.find_all("a", href=_HREF_RE, recursive=False) if grid is not None else [])]
    if grid is not None and len(grid.find_all("a", href=_HREF_RE)) != len(cards):
        raise SourceError("layout changed: livresidential page has home links outside its cards")
    if grid is None:
        # an honest empty page keeps its heading and filters but has no result grid at all
        # (never seen; a page that still shows an amount per month but no grid is a changed layout, not empty)
        if soup.find(string=re.compile(r"Woningtype")) and not soup.find(string=re.compile(r"per maand")):
            return []
        raise SourceError("layout changed: livresidential page has no list of homes")
    out: List[Listing] = []
    seen = set()
    for a in cards:
        m = _HREF_RE.search(a["href"])
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        title = _text(a.find("h3"))
        paras = [_text(p) for p in a.find_all("p")]
        # the line with the postcode and the town, and the line with the rent: found by what they hold, not by
        # their position (a card that lost its empty first line once read the rent as the town)
        place_line = next((p for p in paras if _POSTCODE_RE.search(p) and "€" not in p), "")
        price_line = next((p for p in paras if "€" in p), "")
        price = parse_price(price_line)
        if not title or not place_line or price is None:
            raise SourceError("layout changed: livresidential card %s has no title, town line or rent" % sid[:60])
        pm = _POSTCODE_RE.search(place_line) or _POSTCODE_RE.search(title)
        postcode = pm.group(1) + pm.group(2) if pm else None
        town = place_line[pm.end():].strip() if pm and _POSTCODE_RE.search(place_line) else place_line
        slug = city_slug(town)
        if slug is None:
            continue
        if city in CITY_PAGES and slug != city:
            continue
        address = re.split(r"\s+\d{4}\s?[A-Z]{2}\b|,\s*[A-Za-z]", title)[0].strip(" ,") or None
        area = rooms = None
        for div in a.select("div.ml-2"):
            t = _text(div)
            if re.search(r"\bm2\b|m²", t):
                area = parse_int(t)
            elif re.search(r"\bkamers?\b", t):
                rooms = parse_int(t)
        labels = [_text(s).lower() for s in a.select("span.uppercase")]
        img = a.find("img")
        src = (img.get("data-src") or img.get("src")) if img is not None else None
        out.append(Listing(
            source="livresidential",
            source_id=sid,
            url=re.split(r"[?#]", a["href"] if a["href"].startswith("http") else BASE + a["href"])[0],
            title=title or sid,
            city=slug,
            price_total=price,
            rooms=rooms,
            area_m2=area,
            address=address,
            postcode=postcode,
            image_url=src or None,
            extra={"labels": labels} if labels else {},
        ))
    return out


def _facts(soup: BeautifulSoup) -> Dict[str, str]:
    """The 'Kenmerken' block: label -> value, as the page prints them."""
    out: Dict[str, str] = {}
    for box in soup.select("div.w-full.mb-2.text-sm"):
        kids = [c for c in box.find_all("div", recursive=False)]
        if len(kids) >= 2:
            label = _text(kids[0])
            if label and label not in out:
                out[label] = _text(kids[1])
    return out


# the red banner over a home's page: "Deze woning is onder optie" is the one seen; the others are what the
# same banner would say for a home that is further along
_TAKEN_RE = re.compile(r"optie|verhuurd|gereserveerd|onder\s+bod|bezet|niet\s+(?:meer\s+)?beschikbaar"
                       r"|\brented\b|\breserved\b|under\s+(?:option|offer)|no\s+longer\s+available", re.I)


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    facts = _facts(soup)
    heading = soup.find("h2", string=re.compile(r"^\s*Apartment overview\s*$"))
    block = heading.parent if heading is not None else None
    if block is None or "Type" not in facts:
        raise SourceError("layout changed: livresidential detail page has no overview text or 'Kenmerken'")
    desc = clean_text(block_text(block), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: livresidential detail page has an empty description")
    banner = soup.find("h1", class_=lambda c: bool(c) and "bg-red-500" in c)
    if banner is not None and _text(banner):
        desc = _text(banner) + "\n" + desc
        listing.extra["site_status"] = _text(banner)
        if _TAKEN_RE.search(_text(banner)):
            listing.extra["taken"] = True         # the matcher drops it (filters.evaluate)
    listing.description = desc

    typ = _type(facts["Type"])
    if typ:
        listing.property_type = typ
    if "Oppervlakte" in facts and parse_int(facts["Oppervlakte"]) is not None:
        listing.area_m2 = parse_int(facts["Oppervlakte"])
    if "Kamers" in facts and parse_int(facts["Kamers"]) is not None:
        listing.rooms = parse_int(facts["Kamers"])
    if "Slaapkamers" in facts and parse_int(facts["Slaapkamers"]) is not None:
        listing.bedrooms = parse_int(facts["Slaapkamers"])
    m = _AVAIL_RE.search(soup.get_text(" ", strip=True))
    if m:
        listing.available_from = "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1)))
    price_el = soup.find("p", string=re.compile(r"€.*per maand"))
    if price_el is not None and listing.price_total is None:
        listing.price_total = parse_price(_text(price_el))
    svc = facts.get("Servicekosten")
    if svc and "€" in svc:
        amount = parse_price(svc)
        if amount is not None:
            listing.extra["service_costs"] = amount
            # the page prints the rent as "€ 1.950 per maand (excl.)" and the service costs apart, next to it
            listing.extra["service_costs_on_top"] = True
            listing.extra["service_costs_note"] = "Servicekosten %s" % svc
    return listing


class LivResidential(Source):
    name = "livresidential"
    label = "LIV Residential"
    homepage = "https://livresidential.nl/huurwoningen"
    cities = ("den-haag", "amsterdam", "rotterdam")
    note = "Reply via the landlord (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        page = CITY_PAGES.get(city)
        if page is None:
            return []
        return parse_page(http.get_text("%s/huurwoningen/%s" % (BASE, page)), city)

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = LivResidential()
