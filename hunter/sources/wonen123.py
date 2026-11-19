"""123Wonen: rental agency with city searches. Server-rendered list, newest first.

https://www.123wonen.nl/huurwoningen/in/<city>/sort/newest  (12 listings per page, one request per city).
The site lists the homes that are still for rent first and the rented ones after them (checked on
19 Nov 2026: Amsterdam page 1 held 10 homes for rent and 2 rented ones, pages 2 and 3 rented ones only).
So page 1 is normally everything there is. Page 2 (/page/2/sort/newest) and page 3 are read only when
every card on the page before them is still for rent, which means more homes for rent may follow.
fetch_details reads the detail page once (description, spec rows, costs).
Rented listings stay on the page with a "Verhuurd" label; they are skipped here.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial

BASE = "https://www.123wonen.nl"

_TYPES = (
    ("kamer", "room"), ("studio", "studio"), ("parkeer", "parking"), ("garage", "parking"),
    ("eengezins", "house"), ("woonhuis", "house"), ("tussenwoning", "house"), ("hoekwoning", "house"),
    ("villa", "house"), ("bungalow", "house"), ("herenhuis", "house"), ("grachtenpand", "house"),
    ("appartement", "apartment"), ("flat", "apartment"), ("maisonnette", "apartment"),
    ("benedenwoning", "apartment"), ("bovenwoning", "apartment"), ("penthouse", "apartment"),
    ("portiek", "apartment"), ("galerij", "apartment"), ("loft", "apartment"),
)
_FURNISHED_RE = re.compile(r"(?<![\w-])(ongemeubileerd|niet gemeubileerd|gemeubileerd|gestoffeerd|kaal)(?![\w-])", re.I)
_FURNISHED_KIND = {"ongemeubileerd": "unfurnished", "niet gemeubileerd": "unfurnished", "kaal": "unfurnished",
                   "gemeubileerd": "furnished", "gestoffeerd": "upholstered"}
_NOT_AVAILABLE = ("verhuurd", "optie", "voorbehoud", "gereserveerd")
# The headline of an ad that offers ONE room of a shared home. Specific on purpose: "3-kamerappartement
# te huur" and "tweekamerappartement" must not match.
_ROOM_AD_RE = re.compile(
    r"(?<![\w-])(?<!\d )kamers?\s+te\s+huur|"
    r"(?<![\w-])(?<!\d )kamer\s+beschikbaar|"
    r"nog\s+(?:slechts\s+)?(?:één|een|1)\s+(?:\w+\s+)?kamer\b", re.I)
_SHARED_RE = re.compile(
    # (not "je deelt de woning met je partner": the people who move in together are no housemates)
    r"(?:je|u)\s+deelt\s+de\s+woning(?!\s+met\s+(?:(?:je|uw|jouw|jullie)\s+(?:eigen\s+)?(?:partner|vriend|gezin)|elkaar))|"
    r"gedeelde\s+(?:woonkamer|keuken|badkamers?)|"
    r"gezamenlijke\s+(?:woonkamer|keuken)", re.I)
_UTILITIES_WORDS = r"(?:nutsvoorzieningen|nuts|gas|water|elektra|energie|g/w/e|gwe|internet)"
# "- € 30.00 per maand servicekosten", "Huurprijs: 2.500,- + €10,- servicekosten" (the amount stands in front)
_SERVICE_BEFORE_RE = re.compile(r"€\s*(\d[\d.]*?)(?:[.,](?:-|\d{2}))?\s*(?:per\s+maand\s+|p/m\s+|per\s+mnd\s+)?servicekosten\b", re.I)
_SERVICE_INCLUDED_RE = re.compile(r"\b(?:incl\.?|inclusief|waarvan|inbegrepen|including|included|inclusive)(?!\w)", re.I)
_SERVICE_RE = re.compile(r"servicekosten\s*:?\s*€\s*(\d[\d.]*)(?:,(?:-|\d{2}))?\s*(?:per\s+maand|p/m|p\.m\.|/\s*maand|per\s+mnd)", re.I)


def _ptype(label: Optional[str]) -> Optional[str]:
    low = (label or "").lower()
    for key, val in _TYPES:
        if key in low:
            return val
    return "other" if low else None


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")) if el else ""


def _furnished(text: Optional[str]) -> Optional[str]:
    """furnished | upholstered | unfurnished from a page's own wording; "Gem. mogelijk" (possible) says nothing."""
    m = _FURNISHED_RE.search(text or "")
    return _FURNISHED_KIND.get(m.group(1).lower()) if m else None


def _is_room_ad(*texts: Optional[str]) -> bool:
    return any(_ROOM_AD_RE.search(t or "") for t in texts)


def _max_stay(*values: Optional[str]) -> Optional[int]:
    """Months from "Max. huurperiode" values: "7 maanden" -> 7, "24 maanden Minimaal 12 maanden" -> 24.
    A "Minimaal ..." value is a minimum, "Voor onbepaalde tijd" is no limit: both give None."""
    found = []
    for raw in values:
        t = re.sub(r"minimaal\s+\d+\s*(?:maanden|maand|jaar)", " ", (raw or "").lower())
        m = re.search(r"(\d+)\s*(maanden|maand|jaar)\b", t)
        if m:
            found.append(int(m.group(1)) * (12 if m.group(2) == "jaar" else 1))
    return min(found) if found else None


def _facts(*parts: Optional[str]) -> Optional[str]:
    """The page's own words behind the extra keys, whole quotes only, at most 200 characters."""
    out = ""
    for part in (p.strip(" ;") for p in parts if p and p.strip(" ;")):
        joined = out + ("; " if out else "") + part
        if len(joined) <= 200:
            out = joined
        elif not out:
            out = part[:200]
    return out or None


MAX_PAGES = 3


def more_may_follow(html: str, page: int) -> bool:
    """True when every card on this page is still for rent and the page links to the next one: the homes for
    rent come first, so only then can the next page hold more of them."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.pandlist-container")
    if not cards:
        return False
    for card in cards:
        status = _text(card.select_one(".pand-status")).lower()
        if "verhuurd" in (card.get("class") or []) or any(w in status for w in _NOT_AVAILABLE):
            return False
    return soup.select_one("a[href*='/page/%d/']" % (page + 1)) is not None


def parse_page(html: str, city: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    counter = None
    for el in soup.find_all(string=re.compile(r"^\s*Gevonden\s*$")):
        nxt = el.find_next("i")
        if nxt is not None:
            counter = parse_int(nxt.get_text())
            break
    if counter is None:
        raise SourceError("layout changed: 123wonen result counter ('Gevonden') not found")
    cards = soup.select("div.pandlist-container")
    if counter == 0:
        return []
    if not cards:
        raise SourceError("layout changed: 123wonen shows %d results but no listing cards" % counter)

    out: List[Listing] = []
    for card in cards:
        classes = card.get("class") or []
        status = _text(card.select_one(".pand-status")).lower()
        if "verhuurd" in classes or any(w in status for w in _NOT_AVAILABLE):
            continue
        link = card.select_one("a[href*='/huur/']")
        href = link.get("href") if link else None
        if not href:
            m = re.search(r"location\.href='([^']+)'", card.get("onclick") or "")
            href = m.group(1) if m else None
        if not href:
            raise SourceError("layout changed: 123wonen card without detail link")
        m = re.search(r"-(\d+-\d+)/?$", href)
        if not m:
            raise SourceError("layout changed: 123wonen detail link has no id: %s" % href)
        sid = m.group(1)
        title_el = card.select_one(".pand-title")
        town = _text(title_el).split(",")[0].strip() if title_el else ""
        if city_slug(town) != city:
            continue
        address_el = card.select_one(".pand-address")
        address = _text(address_el) or None

        specs: Dict[str, str] = {}
        for li in card.select(".pand-specs li"):
            spans = li.find_all("span", recursive=False)
            if len(spans) >= 2:
                specs[_text(spans[0]).lower()] = _text(spans[1])
        price = parse_price(_text(card.select_one(".pand-price")))
        furnished = _furnished(specs.get("interieur"))
        avail = specs.get("beschikbaarheid", "")
        available_from = _iso(avail)  # "Vanaf 01-12-2026"; "Per direct" -> None
        img = card.select_one(".pand-image")
        slogan = _text(card.select_one(".pand-slogan"))
        room = _is_room_ad(slogan)
        ptype = "room" if room else _ptype(specs.get("type"))
        extra = {"availability": avail or None}
        stay = _max_stay(specs.get("max. huurperiode"))
        if stay:
            extra["max_stay_months"] = stay
        out.append(Listing(
            source="123wonen",
            source_id=sid,
            url=href,
            title=("%s %s, %s" % ("Kamer" if room else (specs.get("type") or "Woning"), address or "", town)).replace("  ", " "),
            city=city,
            price_total=price,
            bedrooms=parse_int(specs.get("slaapkamers")),
            area_m2=parse_int(specs.get("woonoppervlakte")),
            property_type=ptype,
            furnished=furnished,
            address=address,
            available_from=available_from,
            description=clean_text(slogan + ". " + specs.get("max. huurperiode", "")) if slogan else
            clean_text(specs.get("max. huurperiode")),
            image_url=img.get("data-src") if img else None,
            posted_at=_iso(specs.get("aangeboden sinds")),
            extra=extra,
        ))
    return out


def _lines(el: Tag) -> List[str]:
    return [re.sub(r"\s+", " ", x.replace("\xa0", " ")).strip() for x in el.get_text("\n").split("\n")
            if x.strip()]


def apply_details(listing: Listing, html: str) -> Listing:
    """Detail page: the listing's own description, the specification rows and the costs it states."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select("#omschrijving div.panddetail-desc")
    specs_blocks = soup.select("div.pand-specs.panddetail-desc")
    if len(blocks) != 1:
        raise SourceError("layout changed: 123wonen detail page has no single description block (#omschrijving)")
    if len(specs_blocks) != 1:
        raise SourceError("layout changed: 123wonen detail page has no single specification block")
    body = blocks[0]
    for junk in body.select("h2, a.panddetail-moreinfolink"):
        junk.decompose()
    lines = _lines(body)
    if not lines:
        raise SourceError("layout changed: 123wonen detail page description is empty")
    slogan = _text(soup.select_one("div.pand-slogan")) or None
    first = body.find("p")
    headline = _text(first) if first is not None else lines[0]

    rows: Dict[str, List[str]] = {}
    for scope in (soup.select_one("div.pand-specs"), specs_blocks[0]):
        for li in (scope.select("li") if scope is not None else []):
            spans = li.find_all("span", recursive=False)
            if len(spans) >= 2:
                rows.setdefault(_text(spans[0]).lower(), []).append(_text(spans[1]))
    first_row = lambda key: (rows.get(key) or [None])[0]  # noqa: E731

    # line breaks where the page has them (<br>, paragraphs), not at every <strong> or link
    listing.description = clean_text((slogan + ".\n" if slogan else "") + block_text(body).replace("\xa0", " "),
                                     keep_lines=True)
    facts: List[Optional[str]] = []
    if listing.property_type != "room" and _is_room_ad(slogan, headline):
        listing.property_type = "room"
        listing.title = re.sub(r"^\S+", "Kamer", listing.title, count=1)
    if listing.property_type == "room":
        facts.append('Slogan: "%s"' % (slogan if _is_room_ad(slogan) else headline[:90]))
        facts.append("Type: %s" % first_row("type") if first_row("type") else None)
    if _SHARED_RE.search(" ".join(lines)):
        listing.extra["site_housemates"] = True
        m = re.search(r"(?:je|u) deelt de woning met [^.]{0,60}", " ".join(lines), re.I)
        facts.append('"%s"' % m.group(0).strip() if m else None)

    if listing.furnished is None:
        listing.furnished = _furnished(first_row("interieur"))
    if listing.furnished is None:
        for line in lines:
            rest = re.sub(r"^(?:oplevering\s*:\s*)?(?:volledig\s+)?", "", line, flags=re.I).strip(" ;.")
            if _FURNISHED_RE.fullmatch(rest):
                listing.furnished = _furnished(rest)
                facts.append('"%s"' % line)
                break
    if listing.rooms is None:
        listing.rooms = parse_int(first_row("kamers"))
    if listing.bedrooms is None:
        listing.bedrooms = parse_int(first_row("slaapkamers"))
    if listing.area_m2 is None:
        listing.area_m2 = parse_int(first_row("woonoppervlakte"))
    if listing.available_from is None:
        listing.available_from = _iso(first_row("beschikbaarheid"))

    stay = _max_stay(*(rows.get("max. huurperiode") or []))
    if stay:
        listing.extra["max_stay_months"] = stay
        facts.append("Max. huurperiode: %s" % first_row("max. huurperiode"))
    for line in lines:
        if re.match(r"^huur(?:prijs)?\s*:", line, re.I):
            utilities = None
            if re.search(r"exclusief\s+(?:de\s+)?" + _UTILITIES_WORDS, line, re.I) or \
                    re.search(r"\+\s*€\s*\d[\d.]*(?:,(?:-|\d{2}))?\s*voorschot\s+" + _UTILITIES_WORDS, line, re.I):
                utilities = False
            elif re.search(r"inclusief\s+(?:de\s+)?" + _UTILITIES_WORDS, line, re.I):
                utilities = True
            if utilities is not None:
                listing.price_includes_utilities = utilities
                facts.append('"%s"' % line)
            break
    for line in lines:
        m = _SERVICE_RE.search(line) or _SERVICE_BEFORE_RE.search(line)
        if m:
            listing.extra["service_costs"] = int(m.group(1).replace(".", ""))
            # its own line next to the rent, "+ €10,- servicekosten", or a line of a breakdown that ends in a total.
            # "Huurprijs: € 1.575,- inclusief € 50,- servicekosten" says the rent already holds them.
            if not _SERVICE_INCLUDED_RE.search(line[:m.start()]):
                listing.extra["service_costs_on_top"] = True
                listing.extra["service_costs_note"] = line[:160]
            facts.append('"%s"' % line)
            break
    site_facts = _facts(*facts)
    if site_facts:
        listing.extra["site_facts"] = site_facts
    return listing


class Wonen123(Source):
    name = "123wonen"
    label = "123Wonen"
    homepage = "https://www.123wonen.nl"
    cities = tuple(CITIES)
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        if city not in self.cities:
            return []
        out: List[Listing] = []
        seen = set()
        for page in range(1, MAX_PAGES + 1):
            path = "/sort/newest" if page == 1 else "/page/%d/sort/newest" % page
            try:
                html = http.get_text("%s/huurwoningen/in/%s%s" % (BASE, city, path))
                found = parse_page(html, city)
            except (SourceError, SourceBlocked) as exc:
                if page == 1:
                    raise
                raise SourcePartial(out, exc, "page %d" % page)
            for l in found:
                if l.source_id not in seen:
                    seen.add(l.source_id)
                    out.append(l)
            if not more_may_follow(html, page):
                break
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = Wonen123()
