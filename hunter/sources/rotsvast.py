"""Rotsvast: rental agency with server-rendered city searches.

https://www.rotsvast.nl/huren/?search=<City>   (10 per page; available listings come before the rented ones,
so page 1 is enough unless it contains no rented card, then page 2 is read as well: max 2 requests per city).
The card icons are: surface, bed (= bedrooms, checked against the detail page), door (interior), clock.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial, summary_description

BASE = "https://www.rotsvast.nl"
_FURNISHED = (("gemeubileerd", "furnished"), ("gestoffeerd", "upholstered"), ("kaal", "unfurnished"))


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)


# "alleen short stay", "dit betreft alleen short stay bewoning"; not "geen short stay" / "niet voor short stay"
_SHORT_STAY = re.compile(r"\balleen\s+(?:voor\s+)?short[- ]?stay\b")
# "te huur aanbieden voor maximaal 6 maanden"; not "borg van maximaal 2 maanden huur"
_MAX_MONTHS = re.compile(r"\bmaximaal\s+(\d+)\s+maanden\b")


def _max_stay_months(low: str) -> Optional[int]:
    """"... te huur aanbieden voor maximaal 6 maanden" -> 6. Not "borg van maximaal 2 maanden huur"."""
    for m in _MAX_MONTHS.finditer(low):
        before, after = low[max(0, m.start() - 40):m.start()], low[m.end():m.end() + 12]
        if "borg" in before or after.lstrip().startswith(("huur", "kale", "borg")):
            continue
        return int(m.group(1))
    return None


def _date(text: Optional[str]) -> Optional[str]:
    """'1-12-2026' or '1 december 2026' -> ISO; 'Per direct beschikbaar' -> None."""
    iso = _iso(text)
    if iso:
        return iso
    m = _LONG_DATE.search(text or "")
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1))) if m else None


def _details_table(soup: BeautifulSoup) -> Dict[str, Tuple[str, Dict[str, str]]]:
    """The 'Details' block as {lower-case label: (value, {child label: child value})}.
    A row like "Totale huur > Servicekosten": value "EUR 3.450", child {"Servicekosten": "Inbegrepen"}."""
    rows: Dict[str, Tuple[str, Dict[str, str]]] = {}
    for li in soup.select(".house-features li"):
        label_el, value_el = li.find("span"), li.find("strong")
        if label_el is None or value_el is None:
            continue
        kid_labels = [_text(k) for k in label_el.select("small")]
        kid_values = [_text(k) for k in value_el.select("small")]
        for k in label_el.select("small"):
            k.extract()
        for k in value_el.select("small"):
            k.extract()
        rows[_text(label_el).lower()] = (_text(value_el), dict(zip(kid_labels, kid_values)))
    return rows


def _share_address(soup: BeautifulSoup, city: str) -> Optional[str]:
    """'Deel deze woning' block: "Ariana Nozemanstraat 82 Amsterdam 137 m2 3 kamers EUR 3.450 p.m." -> street + number."""
    block = soup.select_one(".share-popup__block") or soup.select_one(".share-popup")
    town = CITIES.get(city)
    if block is None or not town:
        return None
    m = re.match(r"^(.+?)\s+" + re.escape(town) + r"(?:\s|$)", _text(block))
    return m.group(1).strip() or None if m else None


def parse_page(html: str, city: str):
    """Returns (listings, has_rented_card, has_next_page)."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".house-list__overview__items") is None:
        raise SourceError("layout changed: rotsvast result container not found")
    cards = [a for a in soup.select(".house-list__overview__items a.card--house")
             if re.search(r"-h\d+/?$", a.get("href") or "")]
    has_next = soup.select_one("a[href*='/huren/page/2/']") is not None
    if not cards:
        # an empty search shows a message; a page without cards and without it is suspicious
        if "geen resultaten" in soup.get_text(" ").lower() or "geen woningen" in soup.get_text(" ").lower():
            return [], False, False
        raise SourceError("layout changed: rotsvast page has no listing cards")
    out: List[Listing] = []
    any_rented = False
    for a in cards:
        label = _text(a.select_one(".card-house__label")).lower()
        if "verhuurd" in label or "onder optie" in label:
            any_rented = True
            continue
        texts = a.select(".card-house__text")
        if not texts or not _text(texts[0]):
            raise SourceError("layout changed: rotsvast card without town")
        town = _text(texts[0])
        if city_slug(town) != city:
            continue
        address = _text(a.select_one(".card-house__title")) or None
        price = parse_price(_text(texts[-1])) if len(texts) > 1 else None  # some cards have no price
        if price is not None and price < 100:
            price = None  # placeholder prices such as "EUR 10 p.m." on unfinished listings
        area = bedrooms = None
        furnished = avail_text = None
        for li in a.select(".card-house__list li"):
            icon = li.find("i")
            cls = " ".join(icon.get("class") or []) if icon else ""
            val = _text(li)
            if "icon-surface" in cls:
                area = parse_int(val)
            elif "icon-bed" in cls:
                bedrooms = parse_int(val)
            elif "icon-door" in cls:
                for key, f in _FURNISHED:
                    if key in val.lower():
                        furnished = f
            elif "icon-clock" in cls:
                avail_text = val
        href = a["href"]
        sid = re.search(r"-(h\d+)/?$", href).group(1)
        img = a.select_one("img[data-lazy-src]") or a.select_one("img")
        image = (img.get("data-lazy-src") or img.get("src")) if img else None
        if image and image.startswith("data:"):
            image = None
        out.append(Listing(
            source="rotsvast",
            source_id=sid,
            url=href,
            title="%s, %s" % (address or "Woning", town),
            city=city,
            price_total=price,
            bedrooms=bedrooms,
            area_m2=area,
            furnished=furnished,
            address=address,
            available_from=_iso(avail_text),
            image_url=image,
            extra={"availability": avail_text},
        ))
    return out, any_rented, has_next


class Rotsvast(Source):
    name = "rotsvast"
    label = "Rotsvast"
    homepage = "https://www.rotsvast.nl/huren/"
    cities = tuple(CITIES)
    note = "Reply via the agency (free)"
    # candidates are screened with an income test of at least 3x the monthly rent (rotsvast.nl/faq)
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        term = CITIES.get(city)
        if not term:
            return []
        q = quote_plus(term)
        listings, rented, has_next = parse_page(http.get_text("%s/huren/?search=%s" % (BASE, q)), city)
        if has_next and not rented:
            try:
                more, _, _ = parse_page(http.get_text("%s/huren/page/2/?search=%s" % (BASE, q)), city)
            except (SourceError, SourceBlocked) as exc:
                raise SourcePartial(listings, exc, "page 2") from exc
            listings += more
        return listings

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        html = http.get_text(listing.url)
        soup = BeautifulSoup(html, "html.parser")
        desc = soup.select_one(".house-description")
        if desc is None:
            text = summary_description(listing, html, "rotsvast").description
        else:
            text = clean_text(desc.get_text(" ", strip=True).replace("Beschrijving", "", 1).replace("Lees verder Lees minder", ""))
            listing.description = text
        rows = _details_table(soup)
        # the page's own Details table is more exact than the search card and the description
        addr = _share_address(soup, listing.city)
        if addr:
            listing.address = addr
        if rows.get("aantal kamers") and parse_int(rows["aantal kamers"][0]):
            listing.rooms = parse_int(rows["aantal kamers"][0])
        if rows.get("aantal slaapkamers") and parse_int(rows["aantal slaapkamers"][0]) is not None:
            listing.bedrooms = parse_int(rows["aantal slaapkamers"][0])
        if rows.get("oppervlakte") and parse_int(rows["oppervlakte"][0]):
            listing.area_m2 = parse_int(rows["oppervlakte"][0])
        if rows.get("startdatum"):
            listing.available_from = _date(rows["startdatum"][0]) or listing.available_from
        # price_total stays the "Kale huur" the search card showed. The table's "Totale huur" row repeats that figure
        # with "Servicekosten: Inbegrepen" on every saved page, even where the description says "Servicekosten:
        # EUR 100 per maand" (a template default, not data), so neither is stored. Only a stated amount is used.
        total = rows.get("totale huur")
        if total and parse_price(total[1].get("Servicekosten", "")):
            listing.extra["service_costs"] = parse_price(total[1]["Servicekosten"])
        svc_row = rows.get("servicekosten")
        if svc_row and parse_price(svc_row[0]):
            listing.extra["service_costs"] = parse_price(svc_row[0])
        other = rows.get("overige kosten")
        if other:
            states = [v.lower() for k, v in other[1].items() if k in ("Gas/Warmte", "Water", "Elektriciteit")]
            if states and all(v.startswith("niet inbegrepen") for v in states):
                listing.price_includes_utilities = False
            elif states and all(v.startswith("inbegrepen") for v in states):
                listing.price_includes_utilities = True
        if text:
            m = re.search(r"(\d+)[- ]kamer", text, re.I)
            if m and not rows.get("aantal kamers"):
                listing.rooms = int(m.group(1))
            low = text.lower()
            if "excl. g/w/e" in low or "exclusief g/w/e" in low:
                listing.price_includes_utilities = False
            if _SHORT_STAY.search(low):
                months = _max_stay_months(low)
                if months:
                    listing.extra["max_stay_months"] = months
            if "appartement" in low or "bovenwoning" in low or "benedenwoning" in low:
                listing.property_type = listing.property_type or "apartment"
            elif "woonhuis" in low or "eengezins" in low or "tussenwoning" in low or "hoekwoning" in low:
                listing.property_type = listing.property_type or "house"
        return listing


SOURCE = Rotsvast()
