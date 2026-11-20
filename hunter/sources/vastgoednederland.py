"""Vastgoed Nederland: a platform that shows the rental offer of VBO estate agents all over the country.

https://www.vastgoednederland.nl/huurwoningen?q=<place>&huurprijs_tot=<max> is server-rendered, newest first,
17 cards a page, with a rel="next" link. The search form of the page itself (a plain GET form) sends `q` and
`huurprijs_tot`; /aanbod/zoeken, /api/ and "uitgebreid-zoeken" are disallowed by robots.txt and never used.
One page per city normally, a second one when the first is full; never more than 2 pages a city.

The card gives street, town, rent, area and, behind its bed icon, the number of bedrooms: on every home
checked on 20 Nov 2026 (seven, with 1, 2 and 4 bedrooms) that number is the page's own "Aantal slaapkamers".
A "0" there is a home whose page has no bedrooms row, so 0 is read as "not known". The listing's own page
("Aantal slaapkamers", "Aantal kamers") overrules the card. Cards labelled "Onder optie" or "Verhuurd onder
voorbehoud" are left out.

A card may outlive its home: the site's own link then answers 404. Such a home is marked taken when its page
is read, so that nobody is sent a dead link (three homes in Delft on 20 Nov 2026).
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://www.vastgoednederland.nl"
MAX_PAGES = 2          # per city (the source contract: one search page per city, two at most)
# The values the page's own "tot" menu offers; the search is given the smallest one at or above the budget.
_PRICE_STEPS = (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000, 6000)
_TAKEN = ("verhuurd", "onder optie", "onder bod")
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
# a rental home in a new-build project is listed among the others with a /nieuwbouw/ address (seen for Leiden)
_ID_RE = re.compile(r"/(?:huurwoningen|nieuwbouw)/([a-z0-9-]+)/woning-(\d+)(?:-[^/?#]*)?$")
_COUNT_RE = re.compile(r"^\s*([\d.]+)\s+huurwoningen\s+gevonden", re.I)
_SINCE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_NOT_A_HOME = ("parkeer", "garage", "berging", "opslag", "bedrijf", "kantoor", "winkel", "horeca", "praktijk",
               "loods", "grond", "box")
_HOUSE = ("woonhuis", "eengezins", "tussenwoning", "hoekwoning", "vrijstaand", "twee-onder-een", "2-onder-1",
          "herenhuis", "villa", "bungalow", "woning", "huis")
_APARTMENT = ("appartement", "penthouse", "maisonnette", "bovenwoning", "benedenwoning", "portiek", "galerij",
              "flat", "etage")


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


def _price_cap(budget: int) -> int:
    for step in _PRICE_STEPS:
        if step >= budget:
            return step
    return _PRICE_STEPS[-1]


def _count(soup: BeautifulSoup) -> int:
    h1 = soup.select_one("#titleWrapper h1")
    m = _COUNT_RE.match(_text(h1)) if h1 is not None else None
    if not m:
        raise SourceError("layout changed: vastgoednederland result page has no 'N huurwoningen gevonden' heading")
    return int(m.group(1).replace(".", ""))


def parse_page(html: str) -> List[Listing]:
    return _parse(BeautifulSoup(html, "html.parser"))


def _parse(soup: BeautifulSoup) -> List[Listing]:
    expected = _count(soup)
    wrapper = soup.find(id="propertiesWrapper")
    links = wrapper.select("a.propertyLink") if wrapper is not None else []
    if expected == 0:
        return []
    if not links:
        raise SourceError("layout changed: vastgoednederland says %d homes but the page has no cards" % expected)
    # every link to a home's page is a card this parser reads: one that is not is a card it no longer sees
    for a in wrapper.select("a[href]"):
        if a not in links and _ID_RE.search((a.get("href") or "").split("?")[0]):
            raise SourceError("layout changed: vastgoednederland page has a home link outside its cards: %s"
                              % a["href"][:90])
    out: List[Listing] = []
    seen = set()
    for a in links:
        href = (a.get("href") or "").split("?")[0]
        m = _ID_RE.search(href)
        if not m:
            raise SourceError("layout changed: vastgoednederland detail link not recognised: %s" % href[:90])
        sid = m.group(2)
        if sid in seen:
            continue
        seen.add(sid)
        label = _text(a.select_one(".label")).lower()
        if any(w in label for w in _TAKEN):
            continue                                           # "Onder optie", "Verhuurd onder voorbehoud"
        street = _text(a.select_one(".street"))
        town = _text(a.select_one(".city"))
        city = city_slug(town) or city_slug(m.group(1).replace("-", " "))
        if city is None:
            continue
        area = beds = None
        for li in a.select(".bottom li"):
            if li.find("span", class_=re.compile(r"icon-meter")) is not None:
                n = parse_int(_text(li))
                area = n if n else None
            elif li.find("span", class_=re.compile(r"icon-bed")) is not None:
                n = _text(li)
                beds = int(n) if n.isdigit() and 0 < int(n) < 20 else None     # "0": no bedrooms row on the page
        img = a.select_one("img")
        out.append(Listing(
            source="vastgoednederland",
            source_id=sid,
            url=urljoin(BASE, href),
            title="%s, %s" % (street, town) if street else town,
            city=city,
            price_total=parse_price(_text(a.select_one(".price"))),
            area_m2=area,
            bedrooms=beds,
            address=street or None,
            image_url=(img.get("src") if img is not None else None) or None,
            extra={"broker": _text(a.select_one(".broker")) or None},
        ))
    return out


def next_page(html: str) -> Optional[str]:
    """The rel="next" link of a result page, or None on the last one."""
    link = BeautifulSoup(html, "html.parser").find("a", rel="next")
    href = link.get("href") if link is not None else None
    return urljoin(BASE, href) if href else None


# --------------------------------------------------------------------------- detail page
def _facts(soup: BeautifulSoup) -> dict:
    """label -> value over every <dl> of the Kenmerken block (the first value wins for a repeated label)."""
    facts: dict = {}
    for dl in soup.select("#details dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            facts.setdefault(_text(dt).lower(), _text(dd))
    return facts


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("#textDescription")
    if box is None or soup.find(id="details") is None:
        raise SourceError("layout changed: vastgoednederland detail page has no description or Kenmerken block")
    desc = clean_text(block_text(box), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: vastgoednederland description block is empty")
    listing.description = desc
    facts = _facts(soup)
    if not facts:
        raise SourceError("layout changed: vastgoednederland Kenmerken block has no rows")

    rooms = facts.get("aantal kamers")
    if rooms and rooms.isdigit() and int(rooms) > 0:
        listing.rooms = int(rooms)
    beds = facts.get("aantal slaapkamers")
    if beds and beds.isdigit() and int(beds) > 0:              # "0": not filled in, as on the card
        listing.bedrooms = int(beds)
    area = facts.get("woonoppervlakte")
    if area and re.search(r"\d", area) and listing.area_m2 is None:
        listing.area_m2 = parse_int(area)
    if listing.price_total is None:
        listing.price_total = parse_price(facts.get("prijs"))
    kind = facts.get("soort appartement") or facts.get("soort")
    if kind and listing.property_type is None:
        listing.property_type = _property_type(kind)
    since = _SINCE_RE.search(facts.get("aangeboden sinds", ""))
    if since and listing.posted_at is None:
        listing.posted_at = "%s-%02d-%02d" % (since.group(3), _MONTHS[since.group(2).lower()], int(since.group(1)))

    # "Prijs € 1.650,- p/m" and "Servicekosten € 150.00 p/m" are two lines of the same table: the service costs
    # are listed next to the rent, so they come on top of it.
    service = parse_price(facts.get("servicekosten"))
    if service:
        listing.extra["service_costs"] = service
        listing.extra["service_costs_on_top"] = True
        listing.extra["service_costs_note"] = "Prijs %s; Servicekosten %s" % (facts.get("prijs", "?"), facts["servicekosten"])
    return listing


def _unique(listings: List[Listing]) -> List[Listing]:
    seen = set()
    return [l for l in listings if not (l.source_id in seen or seen.add(l.source_id))]


class VastgoedNederland(Source):
    name = "vastgoednederland"
    label = "Vastgoed Nederland"
    homepage = "https://www.vastgoednederland.nl/huurwoningen"
    cities = ("amsterdam", "den-haag", "leiden", "delft", "rotterdam")
    note = "Reply via the estate agent named on the listing"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        if city not in CITIES:
            return []
        url: Optional[str] = "%s/huurwoningen?q=%s&huurprijs_tot=%d" % (
            BASE, quote(CITIES[city]), _price_cap(self.max_rent(2400)))
        out: List[Listing] = []
        for page_no in range(MAX_PAGES):
            try:
                html = http.get_text(url)
                out.extend(l for l in parse_page(html) if l.city == city)
            except (SourceBlocked, SourceError) as exc:
                if page_no == 0:
                    raise
                raise SourcePartial(_unique(out), exc, "page %d" % (page_no + 1)) from exc
            url = next_page(html)
            if not url:
                break
        return _unique(out)

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        try:
            html = http.get_text(listing.url)
        except SourceError as exc:                 # a wall (SourceBlocked) is not a SourceError and passes on
            if "answered HTTP 404" not in str(exc) and "answered HTTP 410" not in str(exc):
                raise
            # the link is the search page's own: the home's page is gone while its card is still shown
            listing.extra["taken"] = True          # the matcher drops it (filters.evaluate)
            listing.extra["site_status"] = "page not found"
            return listing
        return apply_details(listing, html)


SOURCE = VastgoedNederland()
