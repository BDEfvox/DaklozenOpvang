"""DirectWonen: large aggregator of rental listings (agencies and private landlords).

https://directwonen.nl/huurwoningen-huren/<city>  (30 tiles per page, "Nieuwste eerst" is the default order).
robots.txt only disallows /zoeken/, /searchrooms/ and account paths; this path is allowed.
Replying needs a paid account, so the tile links to a payment page; we store the plain listing URL instead.
That listing page is public and is what fetch_details reads (for the description, i.e. the bedrooms).
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://directwonen.nl"
_SLUGS = {"den-haag": "den-haag", "amsterdam": "amsterdam", "leiden": "leiden"}
_TYPES = {
    "appartement": "apartment", "woning": "house", "huis": "house", "eengezinswoning": "house",
    "studio": "studio", "kamer": "room", "parkeerplaats": "parking",
}


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def _listing_url(href: str) -> str:
    """Tiles of 'smart only' adverts link to /premiumaccountpayment?...returnUrl=<listing url>."""
    if "premiumaccountpayment" in href:
        q = parse_qs(urlparse(href).query)
        if q.get("returnUrl"):
            return q["returnUrl"][0]
    return href if href.startswith("http") else BASE + href


def parse_page(html: str, city: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".tiles-container")
    if container is None:
        raise SourceError("layout changed: directwonen tiles container not found")
    tiles = container.select("div.tile")
    if not tiles:
        # a real empty search shows no tiles but also keeps the sort dropdown
        if soup.select_one("#SortOptionsDropDown") is not None:
            return []
        raise SourceError("layout changed: directwonen page has no tiles")
    out: List[Listing] = []
    for tile in tiles:
        link = tile.select_one("a.inner-content")
        if link is None or not link.get("href") or tile.select_one(".upsell-advert") is not None:
            continue  # promo tiles ("Hoe werkt Direct Wonen") sit between the listings
        url = _listing_url(link["href"])
        m = re.search(r"-(\d+)/?$", url.split("?")[0])
        if not m:
            raise SourceError("layout changed: directwonen listing url has no id: %s" % url)
        sid = m.group(1)
        header = _text(tile.select_one(".advert-location-header")).lower()
        place = _text(tile.select_one("h3.location-text"))
        street, _, town = place.rpartition(",")
        if city_slug(town.strip()) != city:
            continue
        price = parse_price(_text(tile.select_one(".advert-location-price")))
        kale = _text(tile.select_one(".kale-huur")).lower()
        rooms = area = None
        for banner in tile.select(".small-banner"):
            classes = banner.get("class") or []
            top = _text(banner.select_one(".small-banner-top"))
            if "rooms" in classes:
                rooms = parse_int(top)
            elif "surface" in classes:
                area = parse_int(top)
        avail = None
        for row in tile.select("tr"):
            cells = row.find_all("td")
            if len(cells) == 2 and "beschikbaar" in _text(cells[0]).lower():
                avail = _iso(_text(cells[1]))
        img = tile.select_one(".advert-thumbnail img")
        age = _text(tile.select_one(".added-days-count-info"))
        out.append(Listing(
            source="directwonen",
            source_id=sid,
            url=url,
            title="%s %s" % (header.capitalize() or "Woning", place),
            city=city,
            price_total=price,
            price_includes_utilities=True if "incl" in kale else (False if "excl" in kale else None),
            rooms=rooms,
            area_m2=area,
            property_type=_TYPES.get(header, "other" if header else None),
            address=street.strip() or None,
            available_from=avail,
            image_url=img.get("src") if img else None,
            extra={"age": age or None, "smart_only": "smart only" in _text(tile).lower()},
        ))
    return out


def apply_details(listing: Listing, html: str) -> Listing:
    """The tile only gives "kamers" (rooms incl. living room). The listing page is public and its
    description usually states the bedrooms; the filter reads the count from that text."""
    soup = BeautifulSoup(html, "html.parser")
    info = soup.select_one("#advertInformation")
    if info is None:
        if soup.select_one("#RoomAdvertPrice") is not None:
            # "Smart only" adverts send you to the payment page. That is the site's choice: leave it.
            listing.extra["details_unavailable"] = True
            return listing
        raise SourceError("layout changed: directwonen listing page has no description block")
    listing.description = clean_text(info.get_text(" ", strip=True)) or listing.description
    return listing


class DirectWonen(Source):
    name = "directwonen"
    label = "DirectWonen"
    homepage = "https://directwonen.nl"
    cities = ("den-haag", "amsterdam", "leiden")
    note = "Paid account needed to reply"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        slug = _SLUGS.get(city)
        if not slug:
            return []
        return parse_page(http.get_text("%s/huurwoningen-huren/%s" % (BASE, slug)), city)

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        if not listing.url.startswith(BASE + "/huurwoningen-huren/"):
            return listing
        if listing.extra.get("smart_only"):  # the listing page is only shown to paying members
            listing.extra["details_unavailable"] = True
            return listing
        return apply_details(listing, http.get_text(listing.url))


SOURCE = DirectWonen()
