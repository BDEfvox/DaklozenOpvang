"""Riva Rentals: Rotterdam agency (WordPress, Divi). Its offer is a Pararius feed shown as cards.

https://rivarentals.nl/aanbod/ lists 12 cards per page, newest first, sold-out ones marked by a status class;
/aanbod/page/<n>/ holds the next pages (about 5 today). Every run reads page 1 and follows the page links up to
MAX_PAGES, so a run costs at most 3 requests (up to DEEP_PAGES on its first run and once an hour). One list for all towns (per_city=False): Rotterdam, Delft and a few
towns around them; the runner keeps the configured cities.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://rivarentals.nl"
MAX_PAGES = 3
DEEP_PAGES = 8         # the whole list (5 pages on 19 Nov 2026), read on the first run and once an hour
_HREF_RE = re.compile(r"/object/([a-z0-9-]+)/?$")
_STATUS_RE = re.compile(r"\bstatus-([a-z-]+?)-forrent\b")
_AVAIL_RE = re.compile(r"Beschikbaar\s+per\s+(\d{1,2})-(\d{1,2})-(\d{4})", re.I)
_TITLE_RE = re.compile(r"^\s*(\S+)\s+.+?\s+te huur in\b", re.I)
_TYPES = {"appartement": "apartment", "apartment": "apartment", "woonhuis": "house", "house": "house",
          "parkeergelegenheid": "parking", "parking": "parking"}
_FURNISHED = {"gemeubileerd": "furnished", "gestoffeerd": "upholstered", "kaal": "unfurnished"}


def _city(name: str) -> Optional[str]:
    slug = city_slug(name.strip())
    if slug is None and name.strip().lower().endswith(" rotterdam"):
        slug = "rotterdam"          # "Hoogvliet rotterdam": a district of Rotterdam, listed as its own town
    return slug


def _last_page(soup: BeautifulSoup) -> int:
    nums = [int(a.get_text(strip=True)) for a in soup.select("a.page-numbers, span.page-numbers")
            if a.get_text(strip=True).isdigit()]
    return max(nums) if nums else 1


def parse_page(html: str) -> Tuple[List[Listing], int]:
    """(listings, number of the last page). Cards that are rented, or in other towns, are left out."""
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one("div.wtfy-pararius-grid")
    if grid is None:
        raise SourceError("layout changed: riva rentals page has no card grid (wtfy-pararius-grid)")
    cards = grid.find_all("a", href=_HREF_RE, recursive=False)
    if not cards:
        if grid.get_text(strip=True) == "" and not grid.find("a"):
            return [], 1
        raise SourceError("layout changed: riva rentals grid has no cards")
    out: List[Listing] = []
    for a in cards:
        m = _STATUS_RE.search(" ".join(a.get("class") or []))
        title_el = a.select_one("h3.wtfy-pararius-title")
        lis = [re.sub(r"\s+", " ", li.get_text(" ", strip=True).replace("\xa0", " ")) for li in a.select("ul.wtfy-pararius-meta li")]
        if m is None or title_el is None or len(lis) < 3:
            raise SourceError("layout changed: riva rentals card has an unknown structure")
        if m.group(1) != "beschikbaar":
            continue                    # status-verhuurd-forrent and anything else that is not on offer
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
        street, _, town = title.rpartition(",")
        slug = _city(town)
        if slug is None:
            continue
        area = beds = price = None
        for text in lis:
            if re.search(r"m²|m2", text):
                area = parse_int(text)
            elif "slaapkamer" in text:
                beds = parse_int(text)
            elif "€" in text:
                price = parse_price(text)
        sid = _HREF_RE.search(a["href"]).group(1)
        img = a.find("img")
        out.append(Listing(
            source="rivarentals",
            source_id=sid,
            url=a["href"] if a["href"].startswith("http") else BASE + a["href"],
            title=title,
            city=slug,
            price_total=price,
            # "0" is how the feed writes "unknown": "0 m²" always, "0 slaapkamers" on a home with an area
            # (a parking place has neither, keeps its 0 and stays out)
            bedrooms=None if (beds == 0 and area) else beds,
            area_m2=area if area else None,
            address=street.strip() or None,
            image_url=(img.get("src") if img is not None else None) or None,
        ))
    return out, _last_page(soup)


def _pairs(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for blurb in soup.select("div.et_pb_blurb_container"):
        head, val = blurb.select_one("h4"), blurb.select_one("div.et_pb_blurb_description")
        if head is not None and val is not None:
            out.setdefault(head.get_text(" ", strip=True), re.sub(r"\s+", " ", val.get_text(" ", strip=True)))
    return out


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("div.et_pb_post_content")
    facts = _pairs(soup)
    canon = soup.find("link", rel="canonical")
    if body is None or "Aantal slaapkamers" not in facts:
        raise SourceError("layout changed: riva rentals detail page has no description or details")
    if canon is not None and canon.get("href") and canon["href"].rstrip("/") != listing.url.rstrip("/"):
        raise SourceError("layout changed: riva rentals detail page is another page (%s)" % canon["href"])
    desc = clean_text(block_text(body), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: riva rentals detail description is empty")
    listing.description = desc
    title = soup.title.get_text(" ", strip=True) if soup.title is not None else ""
    m = _TITLE_RE.match(title)
    if m and m.group(1).lower() in _TYPES:
        listing.property_type = _TYPES[m.group(1).lower()]
    n = parse_int(facts.get("Aantal slaapkamers"))
    if n:                                   # "0" is "unknown" here too
        listing.bedrooms = n
    area = parse_int(facts.get("Woonoppervlak"))
    if area:
        listing.area_m2 = area
    if listing.price_total is None and "€" in facts.get("Huurprijs", ""):
        listing.price_total = parse_price(facts["Huurprijs"])
    fur = _FURNISHED.get(facts.get("Type verhuur", "").strip().lower())
    if fur:
        listing.furnished = fur
    m = _AVAIL_RE.search(desc)
    if m:
        listing.available_from = "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    return listing


class RivaRentals(Source):
    name = "rivarentals"
    label = "Riva Rentals"
    homepage = "https://rivarentals.nl/aanbod/"
    cities = ("rotterdam", "delft")
    per_city = False
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out, last = parse_page(http.get_text(BASE + "/aanbod/"))
        page = 1
        while page < min(last, self.page_limit(MAX_PAGES, DEEP_PAGES)):
            page += 1
            try:
                more, _ = parse_page(http.get_text("%s/aanbod/page/%d/" % (BASE, page)))
            except (SourceError, SourceBlocked) as exc:
                raise SourcePartial(out, exc, "riva rentals page %d" % page)
            out.extend(more)
        if city in CITIES:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = RivaRentals()
