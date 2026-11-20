"""ViaDaan: landlord/agency with furnished homes all over the Netherlands (feed of the Pararius objects it lets).

https://viadaan.nl/aanbod is a Livewire page: the server renders the ten newest homes (of ~80, newest first by
database id) and the rest loads through Livewire update calls on scrolling. Those calls are not made here, and
the site ignores `?city=` in the address, so a plain GET shows the ten newest homes of the whole country, with
no way to ask for one city. One request per run (per_city=False), filtered to the five cities afterwards. This
works as an alert feed (a new home appears at the top and stays in the first ten for many runs), not as a
full list: homes that were already there before the first run are not seen unless they are among the ten newest.
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

BASE = "https://viadaan.nl"
_HREF_RE = re.compile(r"^/aanbod/([a-z0-9-]+)$")
_DATE_RE = re.compile(r"Beschikbaar\s+vanaf\s+(\d{1,2})-(\d{1,2})-(\d{4})", re.I)
_INTERIOR = {"gemeubileerd": "furnished", "gestoffeerd": "upholstered", "kaal": "unfurnished"}


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip() if el else ""


def _city(name: str) -> Optional[str]:
    slug = city_slug(name)
    if slug is None and name.lower().endswith(" rotterdam"):
        slug = "rotterdam"          # "Hoogvliet Rotterdam": a district of Rotterdam, listed as its own town
    return slug


def parse_page(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=_HREF_RE)
    if not links:
        if re.search(r"\b0\s+resultaten", soup.get_text(" ", strip=True)) and soup.find("select"):
            return []
        raise SourceError("layout changed: viadaan page has no listing cards")
    out: List[Listing] = []
    seen = set()
    for a in links:
        li = a.find_parent("li")
        header = a.find_parent("header")
        if li is None or header is None or header.find("h3") is None or header.find("b") is None:
            raise SourceError("layout changed: viadaan card has an unknown structure")
        slug_part = _HREF_RE.match(a["href"]).group(1)
        m = re.search(r"-(h\d+)$", slug_part)
        sid = m.group(1) if m else slug_part
        if sid in seen:
            continue
        seen.add(sid)
        street = _text(header.find("h3"))
        town = _text(header.find("p"))
        slug = _city(town)
        if slug is None:
            continue
        img = li.find("img")
        alt = (img.get("alt") or "").strip() if img is not None else ""
        # the picture's text is the full address: "Woubruggestraat 9, Amsterdam"
        address = re.sub(r",\s*[^,]*$", "", alt).strip(" ,") if alt.lower().endswith(town.lower()) else ""
        address = address if address.startswith(street.split(" ")[0]) and re.search(r"\d", address) else ""
        chips = [_text(c) for c in header.find("ul").find_all("li")] if header.find("ul") else []
        counts = [c for c in chips if re.fullmatch(r"\d+", c)]
        area = next((parse_int(c) for c in chips if re.search(r"\bm\s*2$", c)), None)
        # three numbers, then the area; checked against the pages of two homes: bedrooms, toilets, bathrooms
        bedrooms = int(counts[0]) if len(counts) == 3 else None
        src = img.get("src") if img is not None else None
        out.append(Listing(
            source="viadaan",
            source_id=sid,
            url=BASE + a["href"],
            title="%s, %s" % (address or street, town),
            city=slug,
            price_total=parse_price(_text(header.find("b"))),
            bedrooms=bedrooms,
            area_m2=area,
            address=address or street,
            image_url=src or None,
        ))
    return out


def _pairs(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for dl in soup.select("aside dl"):
        for row in dl.find_all("div", recursive=False):
            dt, dd = row.find("dt"), row.find("dd")
            if dt is not None and dd is not None:
                out.setdefault(_text(dt), _text(dd))
    return out


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    facts = _pairs(soup)
    if article is None or "Slaapkamers" not in facts:
        raise SourceError("layout changed: viadaan detail page has no description or details")
    canon = soup.find("link", rel="canonical")
    if canon is not None and canon.get("href") and canon["href"].rstrip("/") != listing.url.rstrip("/"):
        raise SourceError("layout changed: viadaan detail page is another page (%s)" % canon["href"])
    heading = article.find("h2")
    if heading is not None:
        heading.extract()
    desc = clean_text(block_text(article), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: viadaan detail description is empty")
    listing.description = desc
    for label, name in (("Slaapkamers", "bedrooms"), ("Kamers", "rooms")):
        n = parse_int(facts.get(label))
        if n is not None:
            setattr(listing, name, n)
    if parse_int(facts.get("Oppervlakte")) is not None:
        listing.area_m2 = parse_int(facts["Oppervlakte"])
    interior = _INTERIOR.get(facts.get("Interieur", "").strip().lower())
    if interior:
        listing.furnished = interior
    m = _DATE_RE.search(_text(soup.find("h1").parent) if soup.find("h1") is not None else "")
    if m:
        listing.available_from = "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    return listing


class ViaDaan(Source):
    name = "viadaan"
    label = "ViaDaan"
    homepage = "https://viadaan.nl/aanbod"
    cities = ("amsterdam", "rotterdam", "den-haag", "leiden", "delft")
    per_city = False
    note = "Newest 10 homes of the site only; reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out = parse_page(http.get_text(BASE + "/aanbod"))
        if city in CITIES:
            out = [l for l in out if l.city == city]
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = ViaDaan()
