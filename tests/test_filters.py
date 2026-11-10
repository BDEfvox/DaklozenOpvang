from __future__ import annotations

from hunter.filters import evaluate
from tests.conftest import make_listing


def names(r):
    return [m.search for m in r.matches]


def test_basic_two_person_match(cfg):
    r = evaluate(make_listing(price_total=1400, bedrooms=2), cfg)
    assert names(r) == ["2 people"]
    assert r.matches[0].price_pp == 700 and r.matches[0].tier == "great"
    assert r.best_tier == "great"


def test_ok_tier_and_three_person(cfg):
    r = evaluate(make_listing(price_total=2000, bedrooms=3), cfg)
    assert names(r) == ["3 people"]  # 2000 > 1600
    assert r.matches[0].tier == "great" and r.matches[0].price_pp == 667
    r = evaluate(make_listing(price_total=1500, bedrooms=3), cfg)
    assert names(r) == ["2 people", "3 people"]
    assert r.matches[0].tier == "ok"


def test_too_expensive_or_wrong_city_or_type(cfg):
    assert not evaluate(make_listing(price_total=2500, bedrooms=3), cfg).matched
    assert not evaluate(make_listing(city="utrecht"), cfg).matched
    assert not evaluate(make_listing(property_type="room"), cfg).matched
    assert not evaluate(make_listing(property_type="studio"), cfg).matched


def test_bedrooms_fallback_to_rooms(cfg):
    assert evaluate(make_listing(bedrooms=None, rooms=3), cfg).matched
    assert not evaluate(make_listing(bedrooms=None, rooms=2), cfg).matched


def test_area_fallback_flags_unknown(cfg):
    r = evaluate(make_listing(bedrooms=None, rooms=None, area_m2=50), cfg)
    assert names(r) == ["2 people"] and "bedrooms_unknown" in r.flags
    assert not evaluate(make_listing(bedrooms=None, rooms=None, area_m2=40), cfg).matched
    assert not evaluate(make_listing(bedrooms=None, rooms=None, area_m2=None), cfg).matched


def test_price_unknown_no_match(cfg):
    assert not evaluate(make_listing(price_total=None), cfg).matched


def test_site_prefiltered_unknowns(cfg):
    l = make_listing(bedrooms=None, rooms=None, area_m2=None, price_total=1500, extra={"site_prefiltered": True})
    r = evaluate(l, cfg)
    assert names(r) == ["2 people", "3 people"] and "bedrooms_unknown" in r.flags
    l = make_listing(bedrooms=None, rooms=None, area_m2=None, price_total=None, extra={"site_prefiltered": True})
    r = evaluate(l, cfg)
    assert names(r) == ["2 people"] and "bedrooms_unknown" in r.flags and "price_unknown" in r.flags


def test_no_sharers_removes_three_person_only(cfg):
    r = evaluate(make_listing(price_total=1500, bedrooms=3, description="Helaas geen woningdelers."), cfg)
    assert names(r) == ["2 people"] and "no_sharers" in r.flags


def test_max_persons(cfg):
    r = evaluate(make_listing(price_total=1500, bedrooms=3, description="Geschikt voor 2 personen"), cfg)
    assert names(r) == ["2 people"] and "max_persons:2" in r.flags
    r = evaluate(make_listing(description="alleen voor een stel"), cfg)
    assert "max_persons:2" in r.flags and names(r) == ["2 people"]
    r = evaluate(make_listing(description="Single occupancy"), cfg)
    assert not r.matched and "max_persons:1" in r.flags


def test_other_flags(cfg):
    r = evaluate(make_listing(description="No students. Inschrijving niet mogelijk. Betaal via Western Union. "
                                          "Bruto maandinkomen 3x de huur", price_includes_utilities=True,
                              available_from="2026-12-01", available_until="2027-06-01"), cfg)
    for f in ("no_students", "no_registration", "scam_signals", "income_requirement", "short_term",
              "utilities_included"):
        assert f in r.flags
    assert r.matched  # flags never hide


def test_suspicious_price(cfg):
    r = evaluate(make_listing(price_total=600, bedrooms=2), cfg)
    assert "suspicious_price" in r.flags and r.matched
    assert "short_term" not in evaluate(make_listing(available_from="2026-12-01", available_until="2027-12-01"),
                                        cfg).flags
