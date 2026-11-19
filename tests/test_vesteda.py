from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.vesteda import SOURCE

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def post_json(self, url, payload, **kw):
        self.calls.append((url, payload))
        return self.data

    def get_text(self, url, **kw):
        self.calls.append((url, None))
        return self.data


def test_parse_keeps_open_units_in_city():
    data = json.loads((FIX / "vesteda.json").read_text())
    http = FakeHttp(data)
    out = SOURCE.fetch("leiden", http)
    assert len(http.calls) == 1
    assert http.calls[0][1]["priceTo"] == 2400
    # status 3 (rented with reservation) and the Leiderdorp unit are dropped
    assert [l.source_id for l in out] == ["8695", "7562"]
    first = out[0]
    assert first.uid == "vesteda:8695"
    assert first.city == "leiden" and first.price_total == 1670 and first.bedrooms == 2
    assert first.area_m2 == 75 and first.property_type == "apartment"
    assert first.url.startswith("https://www.vesteda.com/nl/huurwoningen-leiden/")
    assert first.address == "Betaplein 38" and first.postcode == "2321KS"


def test_layout_change_raises():
    for bad in ({}, {"results": {}}, {"results": {"objects": "x"}}, []):
        with pytest.raises(SourceError):
            SOURCE.parse(bad, "leiden")


def test_empty_is_fine():
    assert SOURCE.parse({"count": 0, "results": {"objects": [], "complexes": []}}, "leiden") == []


def _units():
    data = json.loads((FIX / "vesteda_search_open_units.json").read_text())
    out = {}
    for city in ("amsterdam", "den-haag"):
        for l in SOURCE.parse(data, city):
            out[l.source_id] = l
    return out


def _details(source_id, tag):
    l = _units()[source_id]
    http = FakeHttp((FIX / ("vesteda_detail_%s.html" % tag)).read_text())
    out = SOURCE.fetch_details(l, http)
    assert http.calls == [(l.url, None)]  # one request per listing
    return out


def test_has_detail_pages():
    assert SOURCE.has_detail_pages


def test_kenmerken_available_from_service_costs_and_bedrooms():
    # "Beschikbaar vanaf 17 december 2026", "Servicekosten p.m. EUR 190,-", "Aantal slaapkamers 3"
    out = _details("38126", "westerdok_248")
    assert out.available_from == "2026-12-17" and out.extra["service_costs"] == 190 and out.bedrooms == 3
    assert _details("103384", "van_boecopkade_18").available_from == "2026-12-22"
    assert _details("103384", "van_boecopkade_18").extra["service_costs"] == 75


def test_mandatory_parking_from_kenmerken():
    # "Soort parkeergelegenheid: Afname parkeerplaats verplicht 215 euro per maand"
    out = _details("38126", "westerdok_248")
    assert out.extra["mandatory_costs"] == 215
    assert out.extra["mandatory_costs_note"] == "Afname parkeerplaats verplicht 215 euro per maand"
    assert "site_facts" not in out.extra  # no age / sharers key here: the note carries the parking words
    assert _details("39239", "joan_muyskenweg_6d1").extra["mandatory_costs"] == 157
    assert _details("8954", "jp_kloosstraat_82").extra["mandatory_costs"] == 55


def test_no_parking_keys_without_mandatory_parking():
    out = _details("103384", "van_boecopkade_18")
    assert "mandatory_costs" not in out.extra and "mandatory_costs_note" not in out.extra


def test_mandatory_parking_from_description_when_the_row_is_missing():
    html = (FIX / "vesteda_detail_joan_muyskenweg_6d1.html").read_text().replace("Soort parkeergelegenheid", "Iets anders")
    out = SOURCE.fetch_details(_units()["39239"], FakeHttp(html))
    assert out.extra["mandatory_costs"] == 157  # "verplichte afname van een parkeerplaats voor \u20ac157 per maand"


def test_description_is_the_pages_own_text():
    out = _details("38126", "westerdok_248")
    assert "twee ruime slaapkamers en een kleinere kamer" in out.description
    assert out.description.startswith("Dit 4-kamerappartement van Vesteda")


def test_rooms_from_description():
    assert _details("38126", "westerdok_248").rooms == 4      # "Dit 4-kamerappartement"
    assert _details("39239", "joan_muyskenweg_6d1").rooms == 3  # "modern 3-kamer huurappartement"
    assert _details("103384", "van_boecopkade_18").rooms is None  # not stated


def test_json_ld_out_of_stock_is_not_used():
    html = (FIX / "vesteda_detail_westerdok_248.html").read_text()
    assert "OutOfStock" in html
    assert "availability" not in _details("38126", "westerdok_248").extra


def test_sharers_from_api_and_kenmerken():
    units = _units()
    assert units["39239"].extra["site_sharers_ok"] is True and "suited for home sharers" in units["39239"].extra["site_facts"]
    assert "site_sharers_ok" not in units["38126"].extra
    out = _details("8954", "jp_kloosstraat_82")  # "Geschikt voor: Woningdelen"
    assert out.extra["site_sharers_ok"] is True and "Geschikt voor: Woningdelen" in out.extra["site_facts"]
    assert "parkeer" not in out.extra["site_facts"].lower() and len(out.extra["site_facts"]) <= 200
    assert "site_sharers_ok" not in _details("103384", "van_boecopkade_18").extra  # "Geschikt voor Sleutelberoepen"


def test_age_limits_from_api_fields():
    # the saved units all have ageFrom 0 / onlySixtyFivePlus false: no key. Variations of the object are synthetic.
    data = json.loads((FIX / "vesteda_search_open_units.json").read_text())
    objs = {o["id"]: o for o in data["results"]["objects"]}
    assert all("site_min_age" not in l.extra for l in SOURCE.parse(data, "amsterdam"))
    objs[38126]["ageFrom"] = 23
    objs[39239]["onlySixtyFivePlus"] = True
    objs[8954]["ageFrom"] = 18  # 18 is no limit
    by = {l.source_id: l for l in SOURCE.parse(data, "amsterdam")}
    assert by["38126"].extra["site_min_age"] == 23 and "minimum age 23" in by["38126"].extra["site_facts"]
    assert by["39239"].extra["site_min_age"] == 65 and "only for people of 65 and older" in by["39239"].extra["site_facts"]
    assert "site_min_age" not in by["8954"].extra


def test_api_notes_stay_in_the_description():
    out = _details("39239", "joan_muyskenweg_6d1")
    assert out.description.endswith("Geschikt voor woningdelers. Complex De Enter")
    again = SOURCE.fetch_details(out, FakeHttp((FIX / "vesteda_detail_joan_muyskenweg_6d1.html").read_text()))
    assert again.description.count("Geschikt voor woningdelers") == 1


def test_detail_without_kenmerken_raises():
    l = Listing(source="vesteda", source_id="1", url="https://www.vesteda.com/x", title="x", city="amsterdam")
    with pytest.raises(SourceError):
        SOURCE.fetch_details(l, FakeHttp("<html><body><p>niets</p></body></html>"))


def test_without_description_block_falls_back_on_json_ld_summary():
    html = (FIX / "vesteda_detail_westerdok_248.html").read_text().replace("Korte omschrijving", "Iets anders")
    out = SOURCE.fetch_details(_units()["38126"], FakeHttp(html))
    assert out.extra["description_from_summary"] is True
    assert out.description.startswith("Dit 4-kamerappartement van Vesteda")
    assert out.rooms == 4
