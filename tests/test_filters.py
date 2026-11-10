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


def test_bedrooms_read_from_description_beat_the_room_count(cfg):
    # "4 kamers" would pass for 3 people on rooms - 1, but the text says 2 bedrooms.
    l = make_listing(price_total=2200, bedrooms=None, rooms=4, description="4 kamers waarvan twee slaapkamers en een werkkamer")
    r = evaluate(l, cfg)
    assert not r.matched and l.bedrooms == 2  # too expensive for 2 people, too small for 3
    l = make_listing(price_total=1500, bedrooms=None, rooms=4, description="Aantal slaapkamers: 2")
    r = evaluate(l, cfg)
    assert names(r) == ["2 people"] and "bedrooms_unconfirmed" not in r.flags
    l = make_listing(price_total=2200, bedrooms=None, rooms=4, description="De woning beschikt over drie ruime slaapkamers")
    r = evaluate(l, cfg)
    assert names(r) == ["3 people"] and l.bedrooms == 3 and "bedrooms_unconfirmed" not in r.flags


def test_room_count_alone_is_flagged_as_unconfirmed(cfg):
    r = evaluate(make_listing(price_total=2200, bedrooms=None, rooms=4), cfg)
    assert names(r) == ["3 people"] and "bedrooms_unconfirmed" in r.flags
    r = evaluate(make_listing(price_total=2200, bedrooms=3, rooms=4), cfg)
    assert names(r) == ["3 people"] and "bedrooms_unconfirmed" not in r.flags
    assert "bedrooms_unconfirmed" not in evaluate(make_listing(price_total=2200, bedrooms=None, rooms=2), cfg).flags


def test_site_bedroom_count_wins_over_text_and_impossible_text_is_ignored(cfg):
    l = make_listing(bedrooms=2, rooms=3, description="3 slaapkamers")
    evaluate(l, cfg)
    assert l.bedrooms == 2
    l = make_listing(bedrooms=None, rooms=3, description="Slaapkamers: 5")  # more bedrooms than rooms
    evaluate(l, cfg)
    assert l.bedrooms is None


def test_income_requirement_wordings(cfg):
    yes = [
        # Van der Linden, the listing that was missed
        "Inkomensrichtlijnen - Als alleenverdiener bedraagt je bruto maandsalaris (inclusief vakantiegeld) "
        "3,5x keer de vermelde huurprijs van deze woning.",
        "Het bruto-jaarinkomen moet 42x de kale maandhuur zijn.",
        "U dient minimaal 3 keer de maandhuur te verdienen.",
        "Huurders moeten 3x de huur verdienen.",
        "Wij vragen een werkgeversverklaring en recente loonstroken.",
        "Alleen voor huurders met een vast inkomen.",
        "Income guidelines apply to this property.",
        "Your gross monthly salary must be at least 3.5 times the stated monthly rent.",
        "Proof of income is required.",
        "Tenants should earn at least 3x the rent.",
        "Vereist: 3 keer de huur.",
        "Requirement: 3 times the monthly rent.",
        "Eis: 3x de maandhuur bruto.",
    ]
    for text in yes:
        assert "income_requirement" in evaluate(make_listing(description=text), cfg).flags, text
    no = [
        "Borg 2x de huur. Huisdieren in overleg.",
        "Waarborgsom: 2 keer de maandhuur.",
        "Deposit: 2 times the rent. Available from January.",
        "Ruime woonkamer van 4 x 5 meter, 2 slaapkamers.",
        "Geen inschrijvingskosten. Dichtbij winkels en openbaar vervoer.",
    ]
    for text in no:
        assert "income_requirement" not in evaluate(make_listing(description=text), cfg).flags, text


def test_income_requirement_from_the_landlords_standing_rule(cfg):
    r = evaluate(make_listing(description="Licht appartement.", extra={"income_requirement": True}), cfg)
    assert r.flags.count("income_requirement") == 1 and r.matched
    r = evaluate(make_listing(description="Inkomenseis: 3x de huur.", extra={"income_requirement": True}), cfg)
    assert r.flags.count("income_requirement") == 1
