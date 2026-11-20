"""NederWoon: rental agency (Atreon group) with homes in Amsterdam's surroundings, Leiden, Den Haag and more.

https://www.nederwoon.nl/search?search_type=1&city=<name> is one server-rendered page per place, every hit on
one page ("Leiden - 5 resultaten"). One request per city. The place search works with a radius: "Amsterdam"
also returns Diemen and Amstelveen, so every card is kept or dropped by the town in its own link
(/huurwoning/<town>/<id>/<type>-<street>). Parking places and garages are in the same list: dropped.

The card gives rent ("Kale huur/per maand"), area, rooms ("kamers", never bedrooms), finish and availability.
The listing's page adds the description, the service costs and the "Voorwaarden" table (students, sharers,
maximum number of persons). The site states no bedroom count: `bedrooms` stays None (the filters read the text).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceError

BASE = "https://www.nederwoon.nl"
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_AVAIL_RE = re.compile(r"beschikbaar\s+per\s+(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_COUNT_RE = re.compile(r"(\d+)\s+resultaa?t(?:en)?\b", re.I)
_ID_RE = re.compile(r"^/huurwoning/([a-z0-9-]+)/(\d+)/([a-z0-9-]+)$")
_TAKEN = ("verhuurd", "onder optie", "onder bod")
_NOT_A_HOME = ("parkeer", "garage", "berging", "opslag", "bedrijf", "kantoor", "winkel", "horeca", "praktijk",
               "loods", "grond", "box")
_HOUSE = ("woonhuis", "eengezins", "tussenwoning", "hoekwoning", "vrijstaand", "2-onder-1", "twee-onder-een",
          "herenhuis", "villa", "bungalow", "woning", "huis")
_APARTMENT = ("appartement", "penthouse", "maisonnette", "bovenwoning", "benedenwoning", "portiek", "galerij",
              "flat", "etage")


def _text(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip() if el else ""


def _property_type(kind: str) -> Optional[str]:
    """'Appartement' -> apartment, 'Studio' -> studio, 'Kamer' -> room, 'Parkeerplaats' -> parking. Unknown -> None."""
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
    if "ongemeubileerd" in t or "niet gemeubileerd" in t:
        return "unfurnished"
    if "gemeubileerd" in t:
        return "furnished"
    if "gestoffeerd" in t:
        return "upholstered"
    if "kaal" in t:
        return "unfurnished"
    return None


def _iso(text: str) -> Optional[str]:
    m = _AVAIL_RE.search(text)
    if not m:
        return None            # "Per direct beschikbaar" and the like: no date
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1)))


def parse_page(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main", id="main")
    h1 = main.find("h1") if main is not None else None
    if h1 is None:
        raise SourceError("layout changed: nederwoon result page has no heading with the number of results")
    heading = _text(h1)
    m = _COUNT_RE.search(heading)
    if m:
        expected = int(m.group(1))
    elif re.search(r"\bgeen\s+resultaten\b", heading, re.I):
        expected = 0
    else:
        raise SourceError("layout changed: nederwoon heading has no result count: %r" % heading[:80])
    anchors = main.select("a.see-page-button")
    if len(anchors) != expected:
        raise SourceError("layout changed: nederwoon says %d results but the page has %d cards" % (expected, len(anchors)))
    out: List[Listing] = []
    seen = set()
    for a in anchors:
        href = (a.get("href") or "").split("?")[0]
        m = _ID_RE.match(href)
        if not m:
            raise SourceError("layout changed: nederwoon detail link not recognised: %s" % href[:80])
        town_slug, sid = m.group(1), m.group(2)
        row = a.find_parent("div", class_="click-see-page-button")
        row = row.parent if row is not None else None
        if row is None:
            raise SourceError("layout changed: nederwoon card container not found")
        if sid in seen:
            continue
        seen.add(sid)
        paras = row.select("div.click-see-page-button > p")
        place = _text(paras[0]) if paras else ""                      # "2321LB Leiden"
        kind_line = _text(paras[1]) if len(paras) > 1 else ""         # "Appartement | Nieuwbouw"
        pm = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", place)
        postcode = pm.group(1).replace(" ", "") if pm else None
        town = pm.group(2) if pm else place
        city = city_slug(town) or city_slug(town_slug.replace("-", " "))
        if city is None:
            continue                                                   # Diemen, Amstelveen, Arnhem ...
        card_text = _text(row).lower()
        if any(w in card_text for w in _TAKEN):
            continue                                                   # "Verhuurd onder voorbehoud"
        kind = kind_line.split("|")[0].strip()
        ptype = _property_type(kind)
        if ptype in ("parking", "other"):
            continue
        facts = [_text(li) for li in row.select("div.click-see-page-button ul li")]
        area = rooms = None
        finish = avail = None
        for f in facts:
            fl = f.lower()
            if fl.startswith("woonoppervlakte"):
                area = parse_int(f) if re.search(r"\d", f) else None
            elif re.match(r"^\d+\s+kamers?$", fl):
                rooms = parse_int(f)
            elif fl.startswith("oplevering"):
                finish = f
            elif "beschikbaar" in fl:
                avail = f
        price_el = row.select_one("p.heading-md")
        price = parse_price(_text(price_el)) if price_el is not None else None
        basis = ""
        if price_el is not None:
            nxt = price_el.find_next_sibling("p")
            basis = _text(nxt)                                         # "Kale huur/per maand"
        street = _text(a)
        img = row.select_one("img[data-src], img[src]")
        src = (img.get("data-src") or img.get("src")) if img is not None else None
        extra: Dict[str, object] = {}
        if basis:
            extra["price_label"] = basis[:60]
        out.append(Listing(
            source="nederwoon",
            source_id=sid,
            url=BASE + href,
            title="%s, %s" % (street, town) if street else town,
            city=city,
            price_total=price,
            rooms=rooms,
            area_m2=area,
            property_type=ptype,
            furnished=_furnished(finish or ""),
            address=street or None,
            postcode=postcode,
            available_from=_iso(avail or ""),
            image_url=(BASE + src) if src and src.startswith("/") else src,
            extra=extra,
        ))
    return out


# --------------------------------------------------------------------------- detail page
def _section_rows(soup: BeautifulSoup, anchor_id: str) -> List[Tuple[str, str]]:
    """The (label, value) rows of the tables between <h2 id=anchor_id> and the next <h2> (Kenmerken has several
    tables: Bouw, Oppervlakte, Indeling ...). Cells after the label are joined."""
    h2 = soup.find("h2", id=anchor_id)
    rows: List[Tuple[str, str]] = []
    if h2 is None:
        return rows
    for sib in h2.find_next_siblings():
        if sib.name == "h2":
            break
        for tr in sib.find_all("tr") if sib.name == "table" else []:
            cells = [_text(td) for td in tr.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                rows.append((cells[0], " ".join(c for c in cells[1:] if c)))
    return rows


def _cents(text: Optional[str]) -> Optional[int]:
    """'€ 1.203,75' -> 120375: exact, for checking the page's own sums."""
    if not text:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\b", text)
    return int(m.group(1).replace(".", "")) * 100 + int(m.group(2)) if m else None


def _lookup(rows: List[Tuple[str, str]], label: str) -> Optional[str]:
    for k, v in rows:
        if k.lower() == label.lower():
            return v
    return None


def _yes_no(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    return True if v == "ja" else False if v == "nee" else None


def apply_details(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("div.description-content")
    if box is None or soup.find("h2", id="omschrijving") is None:
        raise SourceError("layout changed: nederwoon detail page has no description block")
    desc = clean_text(block_text(box), keep_lines=True)
    if not desc:
        raise SourceError("layout changed: nederwoon description block is empty")
    listing.description = desc

    price_rows = _section_rows(soup, "huurprijs")
    cond_rows = _section_rows(soup, "voorwaarden")
    feat_rows = _section_rows(soup, "kenmerken")
    if not price_rows or not cond_rows:
        raise SourceError("layout changed: nederwoon detail page has no Huurprijs/Voorwaarden table")

    bare = parse_price(_lookup(price_rows, "Kale huur"))
    service = parse_price(_lookup(price_rows, "Voorschot servicekosten"))
    furnishing = parse_price(_lookup(price_rows, "Stoffering"))
    bare_c, service_c = _cents(_lookup(price_rows, "Kale huur")), _cents(_lookup(price_rows, "Voorschot servicekosten"))
    furnishing_c, total_c = _cents(_lookup(price_rows, "Stoffering")), _cents(_lookup(price_rows, "Totale huur per maand"))
    if listing.price_total is None and bare is not None:
        listing.price_total = bare
    # "Kale huur 1.350 + Voorschot servicekosten 65 = Totale huur per maand 1.415": the page's own sum shows that
    # the listed rent is the bare rent and the extra lines come on top of it.
    sums = (bare_c is not None and total_c is not None and bare == listing.price_total
            and bare_c + (service_c or 0) + (furnishing_c or 0) == total_c)
    if service:
        listing.extra["service_costs"] = service
        if sums:
            listing.extra["service_costs_on_top"] = True
            parts = ["Kale huur %s" % _lookup(price_rows, "Kale huur"),
                     "Voorschot servicekosten %s" % _lookup(price_rows, "Voorschot servicekosten")]
            if furnishing:
                parts.append("Stoffering %s" % _lookup(price_rows, "Stoffering"))
            parts.append("Totale huur per maand %s" % _lookup(price_rows, "Totale huur per maand"))
            listing.extra["service_costs_note"] = "; ".join(parts)
    if furnishing and sums:
        # "Stoffering" is a line of the page's own monthly total
        listing.extra["mandatory_costs"] = furnishing
        listing.extra["mandatory_costs_note"] = "Stoffering %s per maand (in Totale huur per maand)" % _lookup(
            price_rows, "Stoffering")

    facts: List[str] = []
    students_ok = _yes_no(_lookup(cond_rows, "Studenten toegestaan"))
    students_only = _yes_no(_lookup(cond_rows, "Alleen studenten"))
    sharers = _yes_no(_lookup(cond_rows, "Woningdelers"))
    max_persons = _lookup(cond_rows, "Max aantal personen")
    for label in ("Studenten toegestaan", "Alleen studenten", "Max aantal personen", "Woningdelers"):
        v = _lookup(cond_rows, label)
        if v is not None:
            facts.append("%s: %s" % (label, v))
    if students_ok is True:
        listing.extra["site_students_ok"] = True
    elif students_ok is False:
        listing.extra["site_no_students"] = True
    if students_only is True:
        listing.extra["site_students_only"] = True
    if sharers is True:
        listing.extra["site_sharers_ok"] = True
    elif sharers is False:
        listing.extra["site_no_sharers"] = True
    if max_persons is not None and re.fullmatch(r"\d+", max_persons.strip()):
        listing.extra["max_persons"] = int(max_persons)
    if facts:
        listing.extra["site_facts"] = " · ".join(facts)[:200]

    kind = _lookup(feat_rows, "Soort woonruimte")
    if kind and listing.property_type is None:
        listing.property_type = _property_type(kind)
    finish = _lookup(feat_rows, "Afwerking")
    if finish and listing.furnished is None:
        listing.furnished = _furnished(finish)
    if listing.area_m2 is None:
        area = _lookup(feat_rows, "Woonoppervlakte")
        if area and re.search(r"\d", area):
            listing.area_m2 = parse_int(area)
    if listing.rooms is None:
        rooms = _lookup(feat_rows, "Aantal kamers")
        if rooms and rooms.strip().isdigit() and int(rooms) > 0:
            listing.rooms = int(rooms)
    return listing


class NederWoon(Source):
    name = "nederwoon"
    label = "NederWoon"
    homepage = "https://www.nederwoon.nl/search"
    cities = ("amsterdam", "den-haag", "leiden", "delft", "rotterdam")
    note = "Reply via the agency (free)"

    def fetch(self, city: str, http: Http) -> List[Listing]:
        if city not in CITIES:
            return []
        url = "%s/search?search_type=1&city=%s" % (BASE, quote_plus(CITIES[city]))
        return [l for l in parse_page(http.get_text(url)) if l.city == city]

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        return apply_details(listing, http.get_text(listing.url))


SOURCE = NederWoon()
