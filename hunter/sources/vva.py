"""VVA (Vereniging Verhuurmakelaars Amsterdam): the rental offer of the association's member agents.

https://www.vva.amsterdam/woningaanbod/huur/amsterdam is server-rendered (a Hay Web portal): 16 cards a page,
a rel="next" link, and a filter that works through the URL (`pricerange.maxprice=<menu value>`; the page's own
script builds that query string). One page in normal use, at most 3. Amsterdam and its surroundings only
(per_city=False: one list for the association; the runner keeps the configured cities).

A card states the type, "kamers" and "slaapkamers" under separate tooltips, the area and the finish. Cards of
parking places, homes with an "Onder bod" (offer made) or sold/rented status, and new-build project cards with a
price range ("van € 1.445,- tot € 1.545,-", no single home) are left out. The listing's own page adds the
description and the Kenmerken rows. The id is the page's path (agency, street and number), never the query string:
that changes with the paging.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://www.vva.amsterdam"
LIST_URL = BASE + "/woningaanbod/huur/amsterdam?moveunavailablelistingstothebottom=true&orderby=9"
MAX_PAGES = 3
# The values the page's own "Tot" menu offers.
_PRICE_STEPS = (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1250, 1500, 1750, 2000, 2500, 3000, 4000, 5000, 6000)
_TAKEN = ("verhuurd", "onder bod", "onder optie", "verkocht")
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_PATH_RE = re.compile(r"^/woningaanbod/huur/([a-z0-9-]+(?:/[a-z0-9._-]+){1,3})$", re.I)
_COUNT_RE = re.compile(r"^\s*(\d[\d.]*)\s+objec(?:ten|t)\s+gevonden", re.I)
_NOT_A_HOME = ("parkeer", "garage", "berging", "opslag", "bedrijf", "kantoor", "winkel", "horeca", "praktijk",
               "loods", "grond", "box")
_HOUSE = ("woonhuis", "eengezins", "tussenwoning", "hoekwoning", "vrijstaand", "twee-onder-een", "2-onder-1",
          "herenhuis", "villa", "bungalow", "woning", "huis")
_APARTMENT = ("appartement", "penthouse", "maisonnette", "bovenwoning", "benedenwoning", "portiek", "galerij",
              "flat", "etage", "benedenhuis")


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip() if el else ""


def _property_type(kind: str) -> Optional[str]:
    k = kind.lower()
    if any(w in k for w in _NOT_A_HOME):
        return "parking" if any(w in k for w in ("parkeer", "garage", "box", "berging")) else "other"
    if k.startswith("studio"):
        return "studio"
    if k.startswith("kamer"):
        return "room"
    if any(w in k for w in _APARTMENT):
        return "apartment"
    if any(w in k for w in _HOUSE):
        return "house"
    return None


def _furnished(text: str) -> Optional[str]:
    t = text.lower()
    if "ongemeubileerd" in t or t.strip() == "kaal":
        return "unfurnished"
    if "gemeubileerd" in t:
        return "furnished"
    if "gestoffeerd" in t:
        return "upholstered"
    return None


def _price_cap(budget: int) -> int:
    for step in _PRICE_STEPS:
        if step >= budget:
            return step
    return _PRICE_STEPS[-1]


def _count(soup: BeautifulSoup) -> int:
    btn = soup.select_one(".sys-property-count")
    t = _text(btn)
    if re.match(r"^\s*geen\s+objecten\s+gevonden", t, re.I):
        return 0
    m = _COUNT_RE.match(t)
    if not m:
        raise SourceError("layout changed: vva result page has no 'N objecten gevonden' count")
    return int(m.group(1).replace(".", ""))


def parse_page(html: str) -> List[Listing]:
    return _parse(BeautifulSoup(html, "html.parser"))[0]


def _parse(soup: BeautifulSoup):
    """(listings, number of homes the page says it found)"""
    expected = _count(soup)
    box = soup.select_one(".sys-page-of-properties")
    if box is None:
        raise SourceError("layout changed: vva result container (.sys-page-of-properties) not found")
    cards = box.select("article.object")
    if expected == 0:
        return [], 0
    if not cards:
        raise SourceError("layout changed: vva says %d objects but the page has no cards" % expected)
    # every link to a home's page belongs to a card this parser reads: one outside them is a card it no longer sees
    in_cards = {id(a) for art in cards for a in art.select("a[href]")}
    for a in box.select("a[href]"):
        if id(a) not in in_cards and _PATH_RE.match((a.get("href") or "").split("?")[0].split("#")[0]) \
                and len((a.get("href") or "").split("?")[0].strip("/").split("/")) >= 5:
            raise SourceError("layout changed: vva page has a home link outside its cards: %s" % a["href"][:90])
    out: List[Listing] = []
    seen = set()
    for art in cards:
        classes = art.get("class") or []
        link = art.select_one("a.object__address-container")
        price_text = _text(art.select_one(".price"))
        if "project" in classes or re.match(r"^\s*van\s", price_text, re.I) or " tot " in price_text.lower():
            continue                                           # a new-build project: a price range, no single home
        if link is None or not link.get("href"):
            raise SourceError("layout changed: vva card has no detail link")
        path = link["href"].split("?")[0].split("#")[0]
        m = _PATH_RE.match(path)
        if not m:
            raise SourceError("layout changed: vva detail link not recognised: %s" % path[:90])
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        status = _text(art.select_one(".object__status_container")).lower()
        if any(w in status for w in _TAKEN):
            continue                                           # "Onder bod"
        kind = _text(art.select_one(".object_type .value"))
        ptype = _property_type(kind)
        if ptype in ("parking", "other"):
            continue
        town = _text(art.select_one(".object__address .locality"))
        city = city_slug(town) or city_slug(sid.split("/")[0].replace("-", " "))
        if city is None:
            continue
        street = _text(art.select_one(".object__address .street")) or None
        postcode = _text(art.select_one(".object__address .zipcode")) or None
        rooms = parse_int(_text(art.select_one(".object_rooms .number")))
        beds = _text(art.select_one(".object_bed_rooms .number"))
        area_text = _text(art.select_one(".object_sqfeet .number"))
        img = art.select_one(".object__image img")
        src = (img.get("data-src") or img.get("src")) if img is not None else None
        out.append(Listing(
            source="vva",
            source_id=sid,
            url=BASE + path,
            title="%s, %s" % (street or postcode or "Woning", town),
            city=city,
            price_total=parse_price(price_text),
            # "0" bedrooms on a home with an area is "not filled in" (a parking place keeps its 0 and stays out)
            bedrooms=(int(beds) or (None if re.search(r"[1-9]", area_text) else 0)) if beds.isdigit() else None,
            rooms=rooms,
            area_m2=parse_int(area_text) if re.search(r"\d", area_text) else None,
            property_type=ptype,
            furnished=_furnished(_text(art.select_one(".object_fitment_furnished .text"))),
            address=street,
            postcode=postcode,
            image_url=src,
        ))
    return out, expected


def next_page(html: str, price_cap: Optional[int] = None) -> Optional[str]:
    link = BeautifulSoup(html, "html.parser").find("link", rel="next")
    href = link.get("href") if link is not None else None
    if not href:
        return None
    url = urljoin(BASE, href)
    if price_cap and "pricerange.maxprice" not in url:
        url += "&pricerange.maxprice=%d" % price_cap
    return url


# --------------------------------------------------------------------------- detail page
def _facts(soup: BeautifulSoup) -> Dict[str, str]:
    facts: Dict[str, str] = {}
    for dl in soup.select("#objectfeatures dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            facts.setdefault(_text(dt).lower(), _text(dd))
    return facts


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("#objectdescription .description > div")
    if box is None or soup.find(id="objectfeatures") is None:
        raise SourceError("layout changed: vva detail page has no description or Kenmerken block")
    desc = clean_text(block_text(box), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: vva description block is empty")
    listing.description = desc
    facts = _facts(soup)
    if not facts:
        raise SourceError("layout changed: vva Kenmerken block has no rows")

    # "2 (waarvan 1 slaapkamer)": two rooms, one of them a bedroom
    m = re.match(r"^(\d+)(?:\s*\(waarvan\s+(\d+)\s+slaapkamers?\))?", facts.get("aantal kamers", ""))
    if m:
        if int(m.group(1)) > 0:
            listing.rooms = int(m.group(1))
        if m.group(2) is not None:
            listing.bedrooms = int(m.group(2))
    area = facts.get("woonoppervlakte")
    if area and re.search(r"\d", area) and listing.area_m2 is None:
        listing.area_m2 = parse_int(area)
    if listing.price_total is None:
        listing.price_total = parse_price(facts.get("huurprijs"))
    kind = facts.get("type object")
    if kind and listing.property_type is None:
        listing.property_type = _property_type(kind)
    since = _DATE_RE.search(facts.get("aangeboden sinds", ""))
    if since and listing.posted_at is None:
        listing.posted_at = "%s-%02d-%02d" % (since.group(3), _MONTHS[since.group(2).lower()], int(since.group(1)))
    accept = _DATE_RE.search(facts.get("aanvaarding", ""))
    if accept and listing.available_from is None:
        listing.available_from = "%s-%02d-%02d" % (accept.group(3), _MONTHS[accept.group(2).lower()], int(accept.group(1)))
    return listing


def _unique(listings: List[Listing]) -> List[Listing]:
    seen = set()
    return [l for l in listings if not (l.source_id in seen or seen.add(l.source_id))]


class Vva(Source):
    name = "vva"
    label = "VVA Amsterdam"
    homepage = "https://www.vva.amsterdam/woningaanbod/huur/amsterdam"
    cities = ("amsterdam",)
    per_city = False
    note = "Reply via the member agency named on the listing"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        cap = _price_cap(self.max_rent(2400))
        url: Optional[str] = "%s&pricerange.maxprice=%d" % (LIST_URL, cap)
        out: List[Listing] = []
        total: Optional[int] = None
        for page_no in range(MAX_PAGES):
            try:
                html = http.get_text(url)
                page, found = _parse(BeautifulSoup(html, "html.parser"))
                if total is None:
                    total = found
                elif found != total:
                    # the next link lost the price filter: do not mix two different searches
                    raise SourceError("layout changed: vva next page counts %d homes, the first page %d" % (found, total))
            except (SourceBlocked, SourceError) as exc:
                if page_no == 0:
                    raise
                raise SourcePartial(_unique(out), exc, "page %d" % (page_no + 1)) from exc
            out.extend(page)
            url = next_page(html, cap)
            if not url:
                break
        if city in CITIES:
            out = [l for l in out if l.city == city]
        return _unique(out)

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = Vva()
