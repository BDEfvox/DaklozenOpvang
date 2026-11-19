"""ikwilhuren.nu (MVGM): large institutional landlord/manager; many Den Haag and Amsterdam homes.

The search page can only filter per city through a CSRF-protected form plus two geocoding POSTs, which would be
4 requests per city. Instead we read the nationwide list in the site's default order:
GET https://ikwilhuren.nu/aanbod/[?page=N]  (10 per page, server-rendered), pages 1-3,
and keep what lies in the configured cities. The default order is newest first (the cards marked "Nieuw" come first,
with a growing "Sinds N dagen online"). The sort option "Publicatiedatum nieuw - oud" (?sort=aanbodDESC) is NOT
newest first: checked live on 2026-11-19, ?sort=aanbodDESC page 1 had the same ten cards as a page saved earlier the
same day, none marked "Nieuw", while the default /aanbod/ page 1 had ten "Nieuw" cards (0.68 days online) and page 2
went on with 0.68, 1 and 1.68 days.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..http import Http
from ..models import CITIES, Listing
from ..utils import block_text, city_slug, clean_text, parse_int, parse_price
from .base import Source, SourceBlocked, SourceError, SourcePartial, summary_description

BASE = "https://ikwilhuren.nu"
PAGES = 3
_TYPES = (
    ("kamer", "room"), ("studio", "studio"), ("parkeer", "parking"), ("garage", "parking"),
    ("eengezins", "house"), ("woonhuis", "house"), ("tussenwoning", "house"), ("hoekwoning", "house"),
    ("villa", "house"), ("herenhuis", "house"), ("kapwoning", "house"), ("bungalow", "house"),
    ("vrijstaande woning", "house"), ("geschakelde woning", "house"),
    ("appartement", "apartment"), ("maisonnette", "apartment"), ("benedenwoning", "apartment"),
    ("bovenwoning", "apartment"), ("penthouse", "apartment"), ("galerij", "apartment"),
    ("portiek", "apartment"), ("flat", "apartment"),
)
_NOT_AVAILABLE = ("verhuurd", "optie", "gereserveerd")
_MONTHS = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
           "september": 9, "oktober": 10, "november": 11, "december": 12}
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)
# "verplichte afname parkeerplaats €214" (card badge) and the same in a description: the amount follows the phrase
_MANDATORY_PARKING = re.compile(
    r"verplichte?\s+afname\s+(?:van\s+(?:een\s+)?)?(?:de\s+)?parkeer(?:plaats|gelegenheid)\D{0,60}?"
    r"(?:€|euro)\s*(\d+(?:[.,]\d+)?)|"
    r"verplichte?\s+afname\s+(?:van\s+(?:een\s+)?)?(?:de\s+)?parkeer(?:plaats|gelegenheid)\D{0,60}?(\d+(?:[.,]\d+)?)\s*euro",
    re.I,
)


def _text(el: Optional[Tag]) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el else ""


def _ptype(label: str) -> Optional[str]:
    low = label.lower()
    if low == "woning":
        return "house"
    for key, val in _TYPES:
        if key in low:
            return val
    return "other" if low else None


# first words that do name something that is not a home
_OTHER_WORD = re.compile(r"bedrijf|kantoor|winkel|berging|opslag|praktijk|horeca|atelier|loods|stalling", re.I)
# the detail page's own type, after the postcode and town: "2316XD Leiden (Appartement)"
_PAGE_TYPE = re.compile(r"\b\d{4}\s?[A-Z]{2}\s+[^\n()]{2,40}\(([^()\n]{3,40})\)")


def _iso(dmy: Optional[str]) -> Optional[str]:
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", dmy or "")
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None


def _long_iso(text: Optional[str]) -> Optional[str]:
    """'1 april 2027' -> '2027-04-01'; 'Per direct' -> None."""
    m = _LONG_DATE.search(text or "")
    return "%s-%02d-%02d" % (m.group(3), _MONTHS[m.group(2).lower()], int(m.group(1))) if m else None


def _mandatory_parking(text: str) -> Optional[Tuple[int, str]]:
    m = _MANDATORY_PARKING.search(text or "")
    if not m:
        return None
    amount = m.group(1) or m.group(2)
    return int(float(amount.replace(",", "."))), re.sub(r"\s+", " ", m.group(0)).strip()


def parse_page(html: str) -> List[Listing]:
    """All listings on one page, any town (the caller filters by city)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.card-woning")
    if not cards:
        if soup.select_one("form#frmFilter") is not None and "geen" in soup.get_text(" ").lower():
            return []
        raise SourceError("layout changed: ikwilhuren page has no listing cards")
    out: List[Listing] = []
    for card in cards:
        link = card.select_one("a.stretched-link[href*='/object/']")
        if link is None:
            raise SourceError("layout changed: ikwilhuren card without /object/ link")
        href = link["href"]
        m = re.search(r"-([0-9a-f]{32})/?$", href)
        if not m:
            raise SourceError("layout changed: ikwilhuren object link has no id: %s" % href)
        # a card can carry several badges ("verplichte afname parkeerplaats €214" before "Te huur")
        badges = [_text(b) for b in card.select(".badge")]
        if any(w in b.lower() for b in badges for w in _NOT_AVAILABLE):
            continue
        title = _text(link)
        place = None
        for span in card.select(".card-body > span"):
            pm = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", _text(span))
            if pm:
                place = (pm.group(1).replace(" ", ""), pm.group(2).strip())
                break
        if place is None:
            raise SourceError("layout changed: ikwilhuren card without 'postcode town' line")
        slug = city_slug(place[1])
        if slug is None:
            continue
        price = parse_price(_text(card.select_one(".dotted-spans .fw-bold")))
        area = bedrooms = None
        for span in card.select(".dotted-spans span:not(.fw-bold)"):
            txt = _text(span).lower()
            if "slaapkamer" in txt:
                bedrooms = parse_int(txt)
            elif "m" in txt and area is None and re.search(r"\d+\s*m", txt):
                area = parse_int(txt)
        words = title.split(maxsplit=2)
        label_end = 1
        if len(words) >= 2 and words[0].lower() in ("vrijstaande", "geschakelde") and words[1].lower() == "woning":
            label_end = 2
        ptype_label = " ".join(words[:label_end])
        address = " ".join(words[label_end:]) or None
        ptype = _ptype(ptype_label)
        if ptype == "other" and not _OTHER_WORD.search(ptype_label):
            # "Schipholweg 386": a card whose title has no type word. The first word is the street, not a type;
            # the detail page states the type ("2316XD Leiden (Appartement)").
            ptype, address = None, title
        img = card.select_one("img")
        src = img.get("src") if img else None
        if src and src.startswith("//"):
            src = "https:" + src
        avail = _text(card.select_one(".small .d-flex"))
        extra: Dict[str, object] = {"availability": avail or None}
        for b in badges:
            parking = _mandatory_parking(b)
            if parking:
                extra["mandatory_costs"], extra["mandatory_costs_note"] = parking
                break
        out.append(Listing(
            source="ikwilhuren",
            source_id=m.group(1),
            url=BASE + href,
            title="%s, %s" % (title, place[1]),
            city=slug,
            price_total=price,
            bedrooms=bedrooms,
            area_m2=area,
            property_type=ptype,
            address=address,
            postcode=place[0],
            available_from=_iso(avail),
            image_url=src,
            extra=extra,
        ))
    return out


def _kenmerken(soup: BeautifulSoup) -> Dict[str, str]:
    """The 'Kenmerken' table of the detail page as {lower-case label: text}. Empty when the page has none."""
    for th in soup.select("th"):
        if _text(th).lower() == "huurprijs":
            table = th.find_parent("table")
            if table is not None:
                rows: Dict[str, str] = {}
                for tr in table.select("tr"):
                    h, d = tr.find("th"), tr.find("td")
                    if h is not None and d is not None:
                        rows[_text(h).lower()] = _text(d)
                return rows
    return {}


class IkWilHuren(Source):
    name = "ikwilhuren"
    label = "MVGM (ikwilhuren.nu)"
    homepage = "https://ikwilhuren.nu/aanbod/"
    cities = tuple(CITIES)
    per_city = False
    note = "Free account on ikwilhuren.nu needed to reply"
    # MVGM only considers candidates who meet the home's income norm (ikwilhuren.nu/vragen and /voorwaarden)
    income_requirement = True

    def fetch(self, city: str, http: Http) -> List[Listing]:
        out: List[Listing] = []
        seen = set()
        for page in range(1, PAGES + 1):
            url = BASE + "/aanbod/" + ("?page=%d" % page if page > 1 else "")
            try:
                listings = parse_page(http.get_text(url))
            except (SourceError, SourceBlocked) as exc:
                if page == 1:
                    raise
                raise SourcePartial(out, exc, city) from exc
            for l in listings:
                if l.source_id not in seen:
                    seen.add(l.source_id)
                    if city not in self.cities or l.city == city:
                        out.append(l)
        return out

    def fetch_details(self, listing: Listing, http: Http) -> Listing:
        html = http.get_text(listing.url)
        soup = BeautifulSoup(html, "html.parser")
        text = block_text(soup).replace("\xa0", " ")
        m = re.search(r"Omschrijving\s+(.*?)\s+(?:Gebruik van deze site|Kenmerken)", text, re.S)
        if m:
            listing.description = clean_text(m.group(1), keep_lines=True)
        else:
            summary_description(listing, html, "ikwilhuren")
        if listing.property_type is None:
            tm = _PAGE_TYPE.search(text)
            if tm:
                listing.property_type = _ptype(tm.group(1).strip())
        rows = _kenmerken(soup)
        # the Kenmerken table is the detail page's own data; it overrides the search card (area was 1 m2 off)
        if rows.get("woonoppervlakte"):
            listing.area_m2 = parse_int(rows["woonoppervlakte"]) or listing.area_m2
        if rows.get("slaapkamers"):
            beds = parse_int(rows["slaapkamers"])
            listing.bedrooms = beds if beds is not None else listing.bedrooms
        if rows.get("beschikbaar vanaf"):
            listing.available_from = _long_iso(rows["beschikbaar vanaf"]) or listing.available_from  # "Per direct": no date
            listing.extra["availability"] = rows["beschikbaar vanaf"]
        costs = rows.get("servicekosten")
        if costs is None:
            sm = re.search(r"Servicekosten\s+€\s*([\d.,]+)", text)
            costs = sm.group(1) if sm else None
        if costs is not None and parse_price(costs) is not None:
            listing.extra["service_costs"] = parse_price(costs)
            # MVGM shows the rent and the service costs as two lines; where the text gives a total it is their sum
            # ("De maandelijkse huurprijs bedraagt € 1.333,55" = € 1.248,55 + € 85, Randveen 808, 19 Nov 2026).
            listing.extra["service_costs_on_top"] = True
            listing.extra["service_costs_note"] = "Servicekosten € %s per maand (MVGM lists them next to the rent)" % costs.strip()
        if "mandatory_costs" not in listing.extra:
            # not the whole page text: "Vergelijkbare huurwoningen" shows the badges of other homes
            parking = _mandatory_parking(listing.description or "") or _mandatory_parking(" ".join(rows.values()))
            if parking:
                listing.extra["mandatory_costs"], listing.extra["mandatory_costs_note"] = parking
        if listing.bedrooms is None and listing.description:
            bm = re.search(r"(\d+)\s*slaapkamers?", listing.description, re.I)
            if bm:
                listing.bedrooms = int(bm.group(1))
        if listing.rooms is None and listing.description:
            rm = re.search(r"(\d+)[- ]kamer", listing.description, re.I)
            if rm:
                listing.rooms = int(rm.group(1))
        return listing


SOURCE = IkWilHuren()
