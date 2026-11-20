"""vb&t Verhuurmakelaars: large rental agency (Eindhoven, Amsterdam, Den Haag, Rotterdam, Delft, ...).

https://vbtverhuurmakelaars.nl/woningen is a server-rendered page (Sapper/Svelte) with 12 homes of the whole
country, newest first, and a page navigation (/woningen/2, /woningen/3, ...). There is no city filter on the page
(the city pages such as /huurwoningen-denhaag are plain text pages without homes), so the source reads the first
few pages once per run (per_city=False) and keeps the homes of the five cities.
robots.txt: only /cms is disallowed.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://vbtverhuurmakelaars.nl"
MAX_PAGES = 3          # 36 homes, newest first: a new home is always on the first pages
DEEP_PAGES = 14        # the whole list (10 pages on 19 Nov 2026), read on the first run and once an hour
# statuses that mean the home is taken (the site's own words: rented, option, reserved, sold); any other status
# (beschikbaar, in verhuur, inschrijving gestart, ...) is kept and shown in extra["status"]
_SKIP_STATUS = ("verhuurd", "optie", "gereserveerd", "onder bod", "verkocht", "voorbehoud", "rented", "reserved", "sold")

_TYPES = (
    ("eengezins", "house"), ("woonhuis", "house"), ("hoekwoning", "house"), ("tussenwoning", "house"),
    ("studio", "studio"), ("kamer", "room"), ("parkeer", "parking"), ("garage", "parking"), ("berging", "parking"),
    ("bedrijf", "other"), ("kantoor", "other"), ("winkel", "other"),
    ("appartement", "apartment"), ("maisonnette", "apartment"), ("penthouse", "apartment"),
)


def _fix(text: str) -> str:
    """requests decodes this server's pages as Latin-1 (no charset in the header), which turns "€" into "â\x82¬".
    Undo that when the text is such a mis-decoded UTF-8 page; leave correct text alone."""
    if "â" in text or "Ã" in text:
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
    return text


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip() if el else ""


def _ptype(label: str) -> Optional[str]:
    low = (label or "").lower()
    for key, val in _TYPES:
        if key in low:
            return val
    return None


def _date(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", text or "")
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    months = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
              "september": 9, "oktober": 10, "november": 11, "december": 12}
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(months) + r")\s+(\d{4})\b", text or "", re.I)
    if m:
        return "%s-%02d-%02d" % (m.group(3), months[m.group(2).lower()], int(m.group(1)))
    return None


def _image(tag: Optional[Tag]) -> Optional[str]:
    style = (tag.get("style") or "") if tag is not None else ""
    m = re.search(r"url\(['\"]?([^'\")]+)", style)
    if not m:
        return None
    src = m.group(1)
    return BASE + src if src.startswith("/") else src


def parse_page(html: str) -> Tuple[List[Listing], Optional[int]]:
    """The homes of one /woningen page in the five cities, and the number of the next page (None if the page
    navigation has no further page)."""
    soup = BeautifulSoup(_fix(html), "html.parser")
    main = soup.find("main")
    h1 = main.find("h1") if main is not None else None
    if main is None or h1 is None or "woningaanbod" not in _text(h1).lower():
        raise SourceError("layout changed: vbtverhuur /woningen has no 'Ons woningaanbod' heading")
    links = main.select("a[href^='/woning/']")
    cards = main.select("div.itemviewer.houses a.property")
    if len(links) != len(cards):
        raise SourceError("layout changed: vbtverhuur /woningen has home links outside the cards")
    out: List[Listing] = []
    seen = set()
    for card in cards:
        href = card.get("href") or ""
        m = re.match(r"^/woning/([\w-]+)/?$", href)
        items = card.select_one("div.items")
        if not m or items is None:
            raise SourceError("layout changed: vbtverhuur card without a /woning/<slug> link: %s" % href)
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        status_el = card.select_one("span.status")
        status = _text(status_el).lower()
        classes = " ".join(status_el.get("class") or []).lower() if status_el is not None else ""
        if status_el is None or not status:
            raise SourceError("layout changed: vbtverhuur card %s has no status" % sid)
        if any(w in status or w in classes for w in _SKIP_STATUS):
            continue
        town_el = items.find("div", recursive=False)
        street_el = items.select_one("span.normal")
        price_el = items.select_one("div.price")
        town = _text(town_el)
        address = _text(street_el) or None
        price = parse_price(_text(price_el))
        if not town or not address or price is None:
            raise SourceError("layout changed: vbtverhuur card %s has no city, address or rent" % sid)
        rows: Dict[str, str] = {}
        usp = ""
        for tr in items.select("table tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) == 2:
                rows[_text(tds[0]).lower()] = _text(tds[1])
            elif tds and "usp" in (tds[0].get("class") or []):
                usp = _text(tds[0])
        slug = city_slug(town)
        if slug is None:
            continue
        kind = _ptype(rows.get("soort object", ""))
        if kind in ("parking", "other"):
            continue
        extra: Dict[str, object] = {"status": status}
        costs = parse_price(rows.get("servicekosten"))
        if costs is not None:
            extra["service_costs"] = costs
        if rows.get("aantal reacties"):
            extra["reactions"] = parse_int(rows["aantal reacties"])
        out.append(Listing(
            source="vbtverhuur",
            source_id=sid,
            url=BASE + href,
            title="%s %s, %s" % (rows.get("soort object") or "Woning", address, town),
            city=slug,
            price_total=price,
            rooms=parse_int(rows.get("kamers")),
            area_m2=parse_int(rows.get("woonoppervlakte")),
            property_type=kind,
            address=address,
            available_from=_date(rows.get("beschikbaar", "")),
            image_url=_image(card.select_one(".visimage")),
            description=clean_text(usp, keep_lines=True) if usp else None,
            extra=extra,
        ))
    # The page navigation: the current page is marked, and the pages are linked by number. ("Volgende" is not the
    # first arrow on a later page: there "Vorige" comes first, and reading that as the next page stopped at page 2.)
    nav = main.select_one("div.pageNav")
    nxt = None
    if nav is not None:
        numbers = set()
        for a in nav.select("a[href]"):
            mm = re.search(r"/woningen/(\d+)$", a.get("href") or "")
            if mm:
                numbers.add(int(mm.group(1)))
        cur = nav.select_one("a.currentpage")
        mm = re.search(r"/woningen/(\d+)$", (cur.get("href") if cur is not None else "") or "")
        here = int(mm.group(1)) if mm else 1
        nxt = here + 1 if here + 1 in numbers else None
    return out, nxt


def _pairs(soup: BeautifulSoup) -> Dict[str, str]:
    """The 'housedetails' block of a home's page: label div followed by value div, under category headings."""
    block = soup.select_one("div.housedetails")
    pairs: Dict[str, str] = {}
    if block is None:
        return pairs
    kids = [c for c in block.find_all("div", recursive=False)]
    i = 0
    while i < len(kids):
        if "housedetailscategory" in (kids[i].get("class") or []):
            i += 1
            continue
        if i + 1 < len(kids) and "housedetailscategory" not in (kids[i + 1].get("class") or []):
            pairs[_text(kids[i]).lower()] = _text(kids[i + 1])
            i += 2
        else:
            i += 1
    return pairs


# "het huren van een parkeerplaats à € 1.200,- p/m een vereiste": the amount may hold a thousands point
_PARKING_REQ = re.compile(r"parkeerplaats.{0,80}?\b(?:een\s+vereiste|verplicht)", re.I)
_AMOUNT = re.compile(r"€\s?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,-|,\d{1,2})?")


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(_fix(html), "html.parser")
    page = soup.select_one("div.housepage")
    offer = soup.select_one("div.offerText")
    if page is None or offer is None:
        raise SourceError("layout changed: vbtverhuur detail page has no .housepage / .offerText")
    for junk in offer.select("label"):       # "Verberg volledige omschrijving..."
        junk.decompose()
    desc = clean_text(block_text(offer), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: vbtverhuur detail page has an empty description")
    by = _pairs(soup)
    if not by:
        raise SourceError("layout changed: vbtverhuur detail page has no property details block")

    usps = [_text(p) for p in soup.select("div.usps p")]
    criteria = None
    for h3 in soup.select("div.usps h3"):
        if _text(h3).lower() == "toewijzingscriteria":
            p = h3.find_next_sibling("p")
            criteria = _text(p) if p is not None else None
    if criteria and re.search(r"friends\s*woning", criteria, re.I):
        # the site's own "Toewijzingscriteria": two tenants rent together, each meeting half of the income norm
        listing.extra["site_sharers_ok"] = True
        listing.extra["site_facts"] = ("Toewijzingscriteria: " + criteria)[:200]
    notes = [x for x in usps if x]
    listing.description = clean_text("\n".join([desc] + notes), keep_lines=True)

    secondary = _text(soup.select_one("div.baseinfo .secondary"))
    m = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", secondary)
    if m:
        listing.postcode = m.group(1).replace(" ", "")
    kind = _ptype(by.get("soort object", ""))
    if kind and listing.property_type is None:
        listing.property_type = kind
    if listing.area_m2 is None:
        listing.area_m2 = parse_int(by.get("woonoppervlakte"))
    avail = _date(by.get("aanvaarding", ""))
    if avail:
        listing.available_from = avail
    costs = parse_price(by.get("servicekosten"))
    if costs is not None:
        listing.extra["service_costs"] = costs
        # the page prints "Huurprijs € 1.320,- p.m." and "Servicekosten € 60,-" as two separate amounts
        listing.extra["service_costs_on_top"] = True
        listing.extra["service_costs_note"] = "Servicekosten: %s (listed next to the rent)" % by["servicekosten"]
    rent = None
    for lab in soup.select("div.respondpanel div.infolabel"):
        if _text(lab).lower() == "huurprijs":
            val = lab.find_next_sibling("div")
            rent = parse_price(_text(val)) if val is not None else None
    if rent is not None and listing.price_total is None:
        listing.price_total = rent
    months = re.search(r"(\d+)\s*maanden", by.get("huurtermijn (min.)", ""), re.I)
    if months:
        listing.extra["min_stay_months"] = int(months.group(1))
    # a compulsory parking place with exactly one amount: "huren van een parkeerplaats à € 80,- p/m een vereiste"
    for note in usps:
        if _PARKING_REQ.search(note):
            amounts = _AMOUNT.findall(note)
            if len(amounts) == 1:
                listing.extra["mandatory_costs"] = parse_price(amounts[0])     # rounds cents, reads "1.200" whole
                listing.extra["mandatory_costs_note"] = note[:200]
    return listing


class VbtVerhuur(Source):
    name = "vbtverhuur"
    label = "vb&t Verhuurmakelaars"
    homepage = "https://vbtverhuurmakelaars.nl/woningen"
    cities = ("amsterdam", "den-haag", "delft", "leiden", "rotterdam")
    per_city = False
    note = "Reply on the agency's site (free registration)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out: List[Listing] = []
        seen = set()
        page = 1
        limit = self.page_limit(MAX_PAGES, DEEP_PAGES)
        while page <= limit:
            url = BASE + "/woningen" + ("" if page == 1 else "/%d" % page)
            try:
                found, nxt = parse_page(http.get_text(url))
            except SourceBlocked:
                raise
            except SourceError as exc:
                if page == 1:
                    raise
                raise SourcePartial(out, exc, "vbtverhuur page %d" % page)
            for l in found:
                if l.source_id not in seen:
                    seen.add(l.source_id)
                    out.append(l)
            if nxt is None or nxt <= page:
                break
            page = nxt
        if city in CITIES:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = VbtVerhuur()
