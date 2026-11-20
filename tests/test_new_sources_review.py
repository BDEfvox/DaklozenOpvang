"""What a first review found in the sources added on 20 Nov 2026. Each case is a saved page with one thing
changed; every one of them used to give a wrong fact or lose a home without a word."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from conftest import make_listing, neutral_config
from hunter.filters import evaluate
from hunter.sources import livresidential, nederwoon, rotterdamrentalservice as rrs, vastgoednederland, vbtverhuur, vva
from hunter.sources.base import SourceError

FIX = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- Rotterdam Rental Service
def _doc() -> dict:
    return copy.deepcopy(json.loads(_fx("rotterdamrentalservice_api_p2.json"))["docs"][0])


def test_rrs_a_rent_with_cents_is_rounded_not_cut():
    doc = _doc()
    doc["rentalsPrice"]["amount"] = 1600.75
    assert rrs.listing_from_doc(doc).price_total == 1601            # 1600 would pass a limit of 1600
    doc["rentalsPrice"]["amount"] = 1600.49
    assert rrs.listing_from_doc(doc).price_total == 1600
    doc["rentalsPrice"]["amount"] = 2495
    assert rrs.listing_from_doc(doc).price_total == 2495
    for bad in (0, -5, None, "1600", True):
        doc["rentalsPrice"]["amount"] = bad
        assert rrs.listing_from_doc(doc).price_total is None


def test_rrs_a_record_without_a_status_is_a_changed_layout_not_a_taken_home():
    doc = _doc()
    assert rrs.listing_from_doc(doc) is not None
    doc.pop("status")
    with pytest.raises(SourceError, match="layout changed"):
        rrs.listing_from_doc(doc)
    data = json.loads(_fx("rotterdamrentalservice_api_p2.json"))
    for d in data["docs"]:
        d.pop("status", None)
    with pytest.raises(SourceError, match="layout changed"):
        rrs.parse_api_page(data)
    doc = _doc()
    doc["status"] = "under_option"                                  # a status it has: the home is taken
    assert rrs.listing_from_doc(doc) is None


def test_rrs_a_home_taken_on_its_own_page_is_dropped():
    html = _fx("rotterdamrentalservice_detail.html")
    listings, _, _ = rrs.parse_page(_fx("rotterdamrentalservice_aanbod.html"))
    by_id = {l.source_id: l for l in listings}
    sid = next((s for s in by_id if s in html), None)
    if sid is None:                                                 # the detail fixture's home is on another page
        sid = re.search(r'"id":"([0-9a-f]{24})"', html).group(1)
        by_id[sid] = make_listing(source="rotterdamrentalservice", source_id=sid, city="rotterdam")
    still_open = rrs.apply_details(copy.deepcopy(by_id[sid]), html)
    assert "taken" not in still_open.extra
    taken_html = re.sub(r'(\\?"status\\?":\s*\\?")available', lambda m: m.group(1) + "under_option", html)
    assert taken_html != html
    taken = rrs.apply_details(copy.deepcopy(by_id[sid]), taken_html)
    assert taken.extra.get("taken") is True and taken.extra["site_status"] == "under_option"
    taken.price_total, taken.bedrooms, taken.area_m2 = 1400, 2, 70
    assert not evaluate(taken, neutral_config()).matched


# ----------------------------------------------------------------------------- vb&t: a compulsory parking place
@pytest.mark.parametrize("amount,expected", [("€ 80,-", 80), ("€ 1200,-", 1200), ("€ 1.200,-", 1200), ("€ 80,75", 81),
                                             ("€80", 80)])
def test_vbt_compulsory_parking_amount_is_read_whole_and_rounded(amount, expected):
    html = _fx("vbtverhuur_detail_delft.html")
    optional = "<p>Bij deze woning is het huren van een parkeerplaats een mogelijkheid (€94)</p>"
    assert optional in html
    l = make_listing(source="vbtverhuur", city="delft", extra={})
    assert "mandatory_costs" not in vbtverhuur.apply_details(l, html).extra          # optional: no cost
    required = "<p>Bij deze woning is het huren van een parkeerplaats à %s p/m een vereiste.</p>" % amount
    l = make_listing(source="vbtverhuur", city="delft", extra={})
    got = vbtverhuur.apply_details(l, html.replace(optional, required)).extra
    assert got["mandatory_costs"] == expected and "parkeerplaats" in got["mandatory_costs_note"]


# ----------------------------------------------------------------------------- NederWoon
def test_nederwoon_ongemeubileerd_is_not_furnished():
    assert nederwoon._furnished("Oplevering ongemeubileerd") == "unfurnished"
    assert nederwoon._furnished("Oplevering gemeubileerd") == "furnished"
    assert nederwoon._furnished("Oplevering gestoffeerd") == "upholstered"
    assert nederwoon._furnished("Oplevering kaal") == "unfurnished"
    html = _fx("nederwoon_leiden.html")
    before = [l.furnished for l in nederwoon.parse_page(html)]
    after = [l.furnished for l in nederwoon.parse_page(html.replace("Oplevering kaal", "Oplevering ongemeubileerd"))]
    assert before and before == after and set(after) == {"unfurnished"}


# ----------------------------------------------------------------------------- LIV Residential
def test_liv_a_link_with_a_query_string_is_the_same_home():
    html = _fx("livresidential_rotterdam.html")
    plain = livresidential.parse_page(html)
    assert len(plain) == 11
    with_query = re.sub(r'(href="https://livresidential\.nl/huurwoningen/rotterdam/[^"?#]+)"', r'\1?ref=search"', html)
    assert with_query != html
    got = livresidential.parse_page(with_query)
    assert [l.source_id for l in got] == [l.source_id for l in plain]            # the id never holds the query
    assert [l.url for l in got] == [l.url for l in plain]


def test_liv_a_card_that_lost_a_line_is_still_read_or_reported():
    html = _fx("livresidential_rotterdam.html")
    plain = livresidential.parse_page(html)
    without_empty = re.sub(r"<p[^>]*>\s*</p>", "", html)
    if without_empty != html:
        got = livresidential.parse_page(without_empty)
        assert [(l.source_id, l.city, l.price_total) for l in got] == [(l.source_id, l.city, l.price_total) for l in plain]
    # a card without its rent line is a changed layout: never a shorter list
    no_rent = re.sub(r"€\s?[\d.]+", "op aanvraag", html, count=1)
    with pytest.raises(SourceError, match="layout changed"):
        livresidential.parse_page(no_rent)


# ----------------------------------------------------------------------------- VVA and Vastgoed Nederland
def test_vva_a_card_the_parser_no_longer_sees_is_reported():
    html = _fx("vva_page1.html")
    n = len(vva.parse_page(html))
    assert n >= 2
    changed = html.replace('<article class="object', '<article class="new-card', 1)
    if changed == html:
        changed = re.sub(r'(<article[^>]*class=")([^"]*\bobject\b)', r"\1new-card", html, count=1)
    assert changed != html
    with pytest.raises(SourceError, match="layout changed"):
        vva.parse_page(changed)


def test_vastgoednederland_a_card_the_parser_no_longer_sees_is_reported():
    html = _fx("vastgoednederland_amsterdam.html")
    n = len(vastgoednederland.parse_page(html))
    assert n >= 2
    changed = html.replace("propertyLink", "new-card", 1)
    assert changed != html
    with pytest.raises(SourceError, match="layout changed"):
        vastgoednederland.parse_page(changed)


# ----------------------------------------------------------------------------- what a second review found
def test_riva_zero_bedrooms_on_a_home_with_an_area_is_not_known():
    """The feed writes 0 for what it does not know. Read as "no bedrooms", such a home failed the search
    before its page was ever read."""
    from hunter.sources import rivarentals
    html = _fx("rivarentals_page1.html")
    plain, _ = rivarentals.parse_page(html)
    assert all(l.bedrooms for l in plain if l.area_m2)
    zero, _ = rivarentals.parse_page(re.sub(r"\d+ slaapkamers", "0 slaapkamers", html))
    assert len(zero) == len(plain)
    assert all(l.bedrooms is None for l in zero if l.area_m2) and any(l.area_m2 for l in zero)
    assert all(l.bedrooms == 0 for l in zero if not l.area_m2)           # "0 m²" + "0 slaapkamers": a parking place
    cfg = neutral_config()
    for l in zero:
        if not l.area_m2:
            assert not evaluate(l, cfg).matched


def test_riva_zero_bedrooms_on_the_page_keeps_what_was_known():
    from hunter.sources import rivarentals
    html = _fx("rivarentals_detail_apartment.html")
    l = make_listing(source="rivarentals", bedrooms=None, extra={},
                     url="https://rivarentals.nl/object/willem-beukelszstraat-rotterdam-h178260122/")
    n = rivarentals.apply_details(copy.deepcopy(l), html).bedrooms
    assert n and n > 0
    soup_zero = re.sub(r"(Aantal slaapkamers\s*(?:</[^>]+>\s*)*(?:<[^>]+>\s*)*)\d+", r"\g<1>0", html, count=1)
    assert soup_zero != html
    assert rivarentals.apply_details(copy.deepcopy(l), soup_zero).bedrooms is None


def test_vva_zero_bedrooms_on_a_home_with_an_area_is_not_known():
    html = _fx("vva_page1.html")
    plain = vva.parse_page(html)
    zero_html = re.sub(r'(object_bed_rooms.*?<span class="number">)\s*\d+', r"\g<1>0", html, flags=re.S)
    assert zero_html != html
    zero = vva.parse_page(zero_html)
    assert len(zero) == len(plain) >= 2
    assert all(l.bedrooms is None for l in zero if l.area_m2) and any(l.area_m2 for l in zero)


def test_vastgoednederland_zero_bedrooms_on_the_page_keeps_what_the_card_said():
    html = _fx("vastgoednederland_detail_669748.html")
    card = make_listing(source="vastgoednederland", bedrooms=2, extra={})
    assert vastgoednederland.apply_details(copy.deepcopy(card), html).bedrooms == 2
    zero = re.sub(r"(Aantal slaapkamers</dt>\s*<dd[^>]*>\s*)\d+", r"\g<1>0", html, count=1)
    assert zero != html
    assert vastgoednederland.apply_details(copy.deepcopy(card), zero).bedrooms == 2
    unknown = make_listing(source="vastgoednederland", bedrooms=None, extra={})
    assert vastgoednederland.apply_details(unknown, zero).bedrooms is None


@pytest.mark.parametrize("banner,taken", [
    ("Deze woning is onder optie", True), ("Verhuurd", True), ("Verhuurd onder voorbehoud", True),
    ("Gereserveerd", True), ("Onder bod", True), ("Niet meer beschikbaar", True), ("This home is rented", True),
    ("Under option", True), ("Nieuw in verhuur", False), ("Open huis op zaterdag", False),
])
def test_liv_any_banner_that_says_the_home_is_gone_makes_it_taken(banner, taken):
    html = _fx("livresidential_detail_studio_onder_optie.html")
    changed = re.sub(r'(bg-red-500 text-white">)[^<]*', lambda m: m.group(1) + banner, html, count=1)
    assert banner in changed
    l = make_listing(source="livresidential", bedrooms=None, extra={},
                     url="https://livresidential.nl/huurwoningen/den-haag/den-haag/waldorpstraat-1-2521cb-den-haag")
    got = livresidential.apply_details(l, changed)
    assert got.extra["site_status"] == banner and (got.extra.get("taken") is True) == taken
    assert got.description.startswith(banner)                  # the banner is always shown with the home
