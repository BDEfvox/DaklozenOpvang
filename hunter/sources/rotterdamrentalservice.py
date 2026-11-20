"""Rotterdam Rental Service: expat-oriented agency, furnished homes in and around Rotterdam.

https://rotterdamrentalservice.com/aanbod is a SvelteKit page. Its server-rendered HTML holds six cards and, in
a `<script data-sveltekit-fetched data-url="https://cpl01.ogonline.nl/api/listings?page=1&limit=6...">` block,
the answer of the site's own listings API for those six (every field: price, bedrooms, rooms, address, status,
description). The page reads further pages by calling that same address with page=2, 3. This source reads the
embedded page 1 and follows `hasNextPage` with the page's own address, at most MAX_PAGES pages in total
(15 homes = 3 requests per run; up to DEEP_PAGES on its first run and once an hour). `?page=2` on the site's own URL is ignored by the site. The API host has no
robots.txt (404). /aanbod/verhuurd (what is rented) is never read.

The server sends `text/html` without a charset, so `requests` decodes the page as ISO-8859-1; `_fix_encoding`
undoes that.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..http import Http
from ..models import Listing
from ..utils import block_text, city_slug, clean_text
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://rotterdamrentalservice.com"
MAX_PAGES = 3                      # the site has 3 pages of 6 today: the whole list in an ordinary run
DEEP_PAGES = 6                     # read on the first run and once an hour, should the list grow
API_HOST_SUFFIX = ".ogonline.nl"
_OPEN_STATUSES = ("available", "expected")      # under_option, under_bid, rented ... are taken or gone
_API_RE = re.compile(r"/api/listings\?")


def _fix_encoding(text: str) -> str:
    """UTF-8 bytes that were decoded as ISO-8859-1 ("â\x82¬", "Ã©") back to text. Correct text is returned as is."""
    if not text or not re.search(r"[ÂÃâ][\x80-\xbf]", text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _slug(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _fetched_blocks(soup: BeautifulSoup) -> List[Any]:
    out = []
    for sc in soup.find_all("script", attrs={"data-sveltekit-fetched": True}):
        url = sc.get("data-url") or ""
        if _API_RE.search(url):
            out.append((url, sc))
    return out


def _body(sc: Any) -> Dict[str, Any]:
    try:
        wrapper = json.loads(sc.string or "")
        body = wrapper["body"]
        data = json.loads(body) if isinstance(body, str) else body
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("layout changed: rotterdamrentalservice embedded listings are not JSON: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
        raise SourceError("layout changed: rotterdamrentalservice listings have no 'docs'")
    return data


def _property_type(doc: Dict[str, Any]) -> Optional[str]:
    consumer = doc.get("consumer") or {}
    titles = " ".join(str(t.get("title", "")) for t in (doc.get("web") or {}).get("types") or []).lower()
    if "kamer" in titles.split() or titles.startswith("kamer"):
        return "room"
    if "studio" in titles:
        return "studio"
    if consumer.get("isApartment"):
        return "apartment"
    if consumer.get("isHouse"):
        return "house"
    return None


def _pos_int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _euros(value: Any) -> Optional[int]:
    """A rent in whole euros, rounded as utils.parse_price rounds (1600.75 is 1601, not 1600)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return int(value + 0.5)


def listing_from_doc(doc: Dict[str, Any], image: Optional[str] = None) -> Optional[Listing]:
    """One API record -> Listing. None for what is not an open rental home in one of the five cities."""
    # "status" decides whether the home is on offer: a record without it is a changed layout, not a taken home
    for key in ("id", "title", "address", "rentalsPrice", "consumer", "status"):
        if key not in doc:
            raise SourceError("layout changed: rotterdamrentalservice listing has no '%s'" % key)
    if doc.get("status") not in _OPEN_STATUSES or doc.get("isRentals") is False:
        return None
    addr = doc["address"] or {}
    slug = city_slug(addr.get("settlement"))
    if slug is None:
        return None
    ptype = _property_type(doc)
    if ptype is None:
        return None            # neither an apartment nor a house: not a home for living in
    price = doc["rentalsPrice"] or {}
    details = (doc["consumer"] or {}).get("details") or {}
    title = str(doc["title"]).strip()
    pc = re.sub(r"\s+", "", str(addr.get("postalCode") or "")).upper()
    furnished = None
    if price.get("isFurnished"):
        furnished = "furnished"
    elif price.get("isDecorated"):
        furnished = "upholstered"
    available = None
    acc = doc.get("acceptance") or {}
    if acc.get("term") == "date" and re.match(r"\d{4}-\d{2}-\d{2}", str(acc.get("date") or "")):
        available = str(acc["date"])[:10]
    if image is None:
        for photo in doc.get("photos") or []:
            url = ((photo or {}).get("import") or {}).get("url")
            if url:
                image = url
                break
    extra: Dict[str, Any] = {}
    if price.get("isShortStay"):
        extra["short_stay"] = True
    return Listing(
        source="rotterdamrentalservice",
        source_id=str(doc["id"]),
        url="%s/detail/%s/%s" % (BASE, _slug(title), doc["id"]),
        title=title,
        city=slug,
        price_total=_euros(price.get("amount")) if not price.get("isRange") else None,
        bedrooms=_pos_int(details.get("bedrooms")),
        rooms=_pos_int(details.get("rooms")),
        area_m2=_pos_int(details.get("livingSurface")),
        property_type=ptype,
        furnished=furnished,
        address=title,
        postcode=pc or None,
        available_from=available,
        image_url=image,
        extra=extra,
    )


def parse_page(html: str) -> "tuple":
    """The first page: (listings, api url of the page, paging dict). Raises SourceError when the page does not
    carry the embedded listings; an embedded empty list is a genuinely empty offer."""
    soup = BeautifulSoup(_fix_encoding(html), "html.parser")
    blocks = _fetched_blocks(soup)
    if not blocks:
        raise SourceError("layout changed: rotterdamrentalservice page has no embedded listings")
    url, sc = blocks[0]
    data = _body(sc)
    images: Dict[str, str] = {}
    for a in soup.find_all("a", href=re.compile(r"/detail/[^/]+/[0-9a-f]+$")):
        img = a.find("img")
        if img is not None and img.get("src"):
            images[a["href"].rsplit("/", 1)[-1]] = img["src"]
    out = []
    for doc in data["docs"]:
        listing = listing_from_doc(doc, images.get(str(doc.get("id"))))
        if listing is not None:
            out.append(listing)
    return out, url, data


def parse_api_page(data: Any) -> List[Listing]:
    if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
        raise SourceError("layout changed: rotterdamrentalservice API page has no 'docs'")
    return [l for l in (listing_from_doc(d) for d in data["docs"]) if l is not None]


def _page_url(url: str, page: int) -> str:
    parts = urlparse(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(API_HOST_SUFFIX):
        raise SourceError("layout changed: rotterdamrentalservice listings come from an unexpected address")
    if not re.search(r"([?&])page=\d+", url):
        raise SourceError("layout changed: rotterdamrentalservice listings address has no page number")
    return re.sub(r"([?&])page=\d+", r"\g<1>page=%d" % page, url, count=1)


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(_fix_encoding(html), "html.parser")
    doc = None
    for url, sc in _fetched_blocks(soup):
        if listing.source_id in url:
            docs = _body(sc)["docs"]
            doc = docs[0] if docs else None
            break
    if doc is None or str(doc.get("id")) != listing.source_id:
        raise SourceError("layout changed: rotterdamrentalservice detail page does not carry this listing")
    html_desc = (doc.get("description") or {}).get("full")
    if not html_desc:
        raise SourceError("layout changed: rotterdamrentalservice detail page has no description")
    text = clean_text(block_text(BeautifulSoup(html_desc, "html.parser")), keep_lines=True)
    if not text:
        raise SourceError("layout changed: rotterdamrentalservice detail description is empty")
    listing.description = text
    fresh = listing_from_doc(doc)
    if fresh is None:
        # no longer open (under option, rented) or not a home any more: say so in the listing's own words
        listing.extra["site_status"] = str(doc.get("status"))
        if doc.get("status") not in _OPEN_STATUSES:
            listing.extra["taken"] = True          # the matcher drops it (filters.evaluate)
        return listing
    for name in ("price_total", "bedrooms", "rooms", "area_m2", "furnished", "available_from", "postcode",
                 "property_type"):
        value = getattr(fresh, name)
        if value is not None:
            setattr(listing, name, value)
    listing.extra.update(fresh.extra)
    return listing


class RotterdamRentalService(Source):
    name = "rotterdamrentalservice"
    label = "Rotterdam Rental Service"
    homepage = "https://rotterdamrentalservice.com/aanbod"
    cities = ("rotterdam",)
    per_city = False
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out, api_url, data = parse_page(http.get_text(BASE + "/aanbod"))
        page = 1
        while data.get("hasNextPage") and page < self.page_limit(MAX_PAGES, DEEP_PAGES):
            page += 1
            try:
                data = http.get_json(_page_url(api_url, page))
                out.extend(parse_api_page(data))
            except SourceError as exc:      # SourceBlocked is not a SourceError: it goes up as it is
                raise SourcePartial(out, exc, "rotterdamrentalservice page %d" % page)
            except SourceBlocked as exc:
                raise SourcePartial(out, exc, "rotterdamrentalservice page %d" % page)
        if city in ("rotterdam", "den-haag", "amsterdam", "leiden", "delft"):
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = RotterdamRentalService()
