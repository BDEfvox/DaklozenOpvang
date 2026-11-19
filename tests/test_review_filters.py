from __future__ import annotations

import pytest

from hunter.filters import evaluate
from tests.conftest import make_listing


@pytest.mark.parametrize("text", ["Three bedrooms, each with a single bed.",
                                  "A lovely single-family home with three bedrooms."])
def test_HH05_single_bed_is_not_an_occupancy_limit(cfg, text):
    """R1 HH#5 / R2 2.3."""
    r = evaluate(make_listing("1", price_total=1500, bedrooms=3, rooms=4, description=text), cfg)
    assert {m.search for m in r.matches} == {"2 people", "3 people"}, r.flags


def test_HH05b_single_person_or_couple_admits_two(cfg):
    r = evaluate(make_listing("1", price_total=1500, description="Suitable for a single person or a couple."), cfg)
    assert "2 people" in {m.search for m in r.matches}, r.flags


def test_HH05c_single_occupancy_still_caps(cfg):
    """Guard (passes today): the fix must keep this."""
    r = evaluate(make_listing("1", price_total=1500, description="Single occupancy only."), cfg)
    assert not r.matched


@pytest.mark.parametrize("text", ["The windows have single glazing.", "The home has a single garage."])
def test_single_features_do_not_cap_occupancy(cfg, text):
    listing = make_listing(price_total=1500, bedrooms=3, description=text)

    result = evaluate(listing, cfg)

    assert {match.persons for match in result.matches} == {2, 3}
    assert not any(flag.startswith("max_persons:") for flag in result.flags)


@pytest.mark.parametrize("text", [
    "Geschikt voor een alleenstaande of een stel.",
    "Beschikbaar voor 1 of 2 personen.",
    "Suitable for a single person or a couple.",
])
def test_occupancy_alternatives_cap_at_two(cfg, text):
    listing = make_listing(price_total=1500, bedrooms=3, description=text)

    result = evaluate(listing, cfg)

    assert {match.persons for match in result.matches} == {2}
    assert "max_persons:2" in result.flags


@pytest.mark.parametrize("text", [
    "For a single person.",
    "One person only.",
    "Voor een alleenstaande.",
    "Voor een persoon.",
    "Geschikt voor 1 persoon.",
    "Suitable for a single person or a couple. Maximum occupancy: one person only.",
])
def test_single_person_requirements_cap_at_one(cfg, text):
    listing = make_listing(description=text)

    result = evaluate(listing, cfg)

    assert not result.matched
    assert "max_persons:1" in result.flags


def test_structured_occupancy_limit_still_applies(cfg):
    listing = make_listing(
        description="Suitable for a single person or a couple.",
        extra={"max_persons": 1},
    )

    assert "max_persons:1" in evaluate(listing, cfg).flags


@pytest.mark.parametrize("extra", [{"registration_allowed": False}, {"registration_possible": False}])
def test_HH07_structured_no_registration_is_flagged(cfg, extra):
    """R1 HH#7 / R2 2.1. Kamernet and HousingAnywhere store the restriction; detect_flags ignores it."""
    assert "no_registration" in evaluate(make_listing("1", extra=dict(extra)), cfg).flags


def test_HH07b_three_month_max_stay_is_short_term(cfg):
    r = evaluate(make_listing("1", available_from="2026-12-01", extra={"max_stay_months": 3}), cfg)
    assert "short_term" in r.flags


@pytest.mark.parametrize("extra", [
    {"registration_allowed": True},
    {"registration_possible": True},
    {"registration_allowed": None, "registration_possible": None},
    {},
])
def test_registration_without_explicit_prohibition_has_no_flag(cfg, extra):
    assert "no_registration" not in evaluate(make_listing(extra=extra), cfg).flags


def test_no_registration_flag_is_not_duplicated(cfg):
    listing = make_listing(
        description="Inschrijving niet mogelijk.",
        extra={"registration_allowed": False, "registration_possible": False},
    )

    assert evaluate(listing, cfg).flags.count("no_registration") == 1


@pytest.mark.parametrize("extra", [
    {"max_stay_months": 12},
    {"max_stay_months": None},
    {"max_stay_months": 0},
    {},
])
def test_max_stay_without_a_short_limit_has_no_flag(cfg, extra):
    assert "short_term" not in evaluate(make_listing(extra=extra), cfg).flags


def test_structured_and_date_based_short_stay_add_one_flag(cfg):
    listing = make_listing(
        available_from="2026-12-01",
        available_until="2027-02-01",
        extra={"max_stay_months": 3},
    )

    assert evaluate(listing, cfg).flags.count("short_term") == 1


@pytest.mark.parametrize("extra", [None, []])
def test_extra_without_a_dictionary_has_no_structured_flags(cfg, extra):
    result = evaluate(make_listing(extra=extra), cfg)

    assert result.matched
    assert "no_registration" not in result.flags
    assert "short_term" not in result.flags


@pytest.mark.parametrize("text", [
    "Geschikt voor alleenstaanden.",
    "Alleen voor een eenpersoonshuishouden.",
    "Single tenant only.",
    "Maximaal 1 persoon.",
    "Uitsluitend voor 1 persoon.",
])
def test_more_one_person_wording_caps_at_one(cfg, text):
    assert "max_persons:1" in evaluate(make_listing(description=text), cfg).flags


@pytest.mark.parametrize("text", [
    "Geschikt voor alleenstaanden of stellen.",
    "Voor een alleenstaande of werkend stel.",
    "Geschikt voor een alleenstaande of een koppel.",
    "Voor een persoon of een stel.",
    "Ideal for a couple or a single person only.",
    "For a single person or a working couple.",
    "Geschikt voor een stel of een alleenstaande.",
])
def test_one_person_wording_next_to_a_couple_caps_at_two(cfg, text):
    flags = evaluate(make_listing(price_total=1500, bedrooms=3, description=text), cfg).flags
    assert "max_persons:2" in flags and "max_persons:1" not in flags, flags


@pytest.mark.parametrize("text", [
    "De tweede slaapkamer heeft een bed voor 1 persoon breed.",
    "Slaapkamer met een eenpersoonsbed en een tweepersoonsbed.",
    "Singles and families welcome.",
    "Single storey home, single bathroom.",
    "Alleenstaande woning met tuin en garage.",
])
def test_wording_that_is_not_about_occupancy(cfg, text):
    flags = evaluate(make_listing(price_total=1500, bedrooms=3, description=text), cfg).flags
    assert not any(f.startswith("max_persons:") for f in flags), flags
