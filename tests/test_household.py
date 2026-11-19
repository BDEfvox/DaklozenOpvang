"""Two people who rent together: can both move in, what does each pay, and what is still to be checked.

The household in these tests is conftest.TWO_STUDENTS (two students who share, 19 and 24).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import hunter.config
from conftest import TWO_STUDENTS, FakeNotifier, FakeSource, make_listing, neutral_config
from hunter import occupancy
from hunter.dashboard import build_data, flag_kinds, home_key
from hunter.filters import capacity, evaluate, places_available, price_basis, rent_basis
from hunter.models import Listing
from hunter.notify import format as fmt
from hunter.notify.format import FLAG_LABELS, flag_label
from hunter.sources.vanderlinden import apply_details
from hunter.runner import DETAIL_VERSION, run_once
from hunter.state import State

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _cfg(persons=2, max_total=1600, household=TWO_STUDENTS):
    c = neutral_config()
    c["household"] = dict(household)
    c["searches"] = [{"name": "%d people" % persons, "persons": persons, "min_bedrooms": min(persons, 2),
                      "max_total": max_total, "great_total": max_total - 200, "min_area_if_unknown": 45}]
    return c


def _judge(text=None, cfg=None, **kw):
    return evaluate(make_listing(description=text, **kw), cfg or _cfg())


def _match(r):
    assert len(r.matches) == 1
    m = r.matches[0]
    return m.price_pp, m.extra_pp


# ----------------------------------------------------------------------------- Cruquiusweg 85 (Van der Linden 2Peer)
def _cruquiusweg(html, price):
    l = Listing("vanderlinden", "2703", "https://www.vanderlinden.nl/huurwoning/Cruquiusweg-85-Amsterdam/2703/",
                "Cruquiusweg 85-R, Amsterdam", "amsterdam", price_total=price, rooms=3, area_m2=86,
                address="Cruquiusweg 85-R")
    return apply_details(l, html)


def _reported_page():
    """The page with the facts of the report: a home for two, one place already taken, € 967 bare rent for the
    place that is left."""
    html = (FIX / "vanderlinden_detail_2703.html").read_text()
    html = html.replace("966", "967").replace("1.161", "1.162").replace("1,161", "1,162")
    together_nl = "Je kunt je samen met een toekomstig medebewoner aanmelden."
    together_en = "You can apply together with a prospective flatmate."
    assert together_nl in html and together_en in html
    return (html.replace(together_nl, "Eén van de twee plekken is al bezet.")
            .replace(together_en, "One of the two places is already occupied."))


def test_cruquiusweg_one_place_left_is_not_a_home_for_two():
    # House Hunter once showed this home at about € 483 a person: the rent of one place, divided by two.
    l = _cruquiusweg(_reported_page(), 967)
    r = evaluate(l, _cfg(max_total=5000))            # no budget in the way: the places decide
    assert not r.matched
    assert "places_free:1" in r.flags and "capacity:2" in r.flags and "price_per_person" in r.flags
    assert r.evidence["places_free"] == "Eén van de twee plekken is al bezet"
    assert r.evidence["capacity"] == "Gedeelde huisvesting voor twee bewoners."
    assert capacity(r.flags) == 2 and places_available(r.flags) == 1          # two different numbers
    assert rent_basis(r.flags) == "place"
    # with the real budget it is no match either
    assert not evaluate(_cruquiusweg(_reported_page(), 967), _cfg()).matched


def test_cruquiusweg_shows_967_excl_for_the_place_that_is_left():
    # Someone who looks for one place sees the place's own rent: € 967, never € 483 and never € 1.934.
    l = _cruquiusweg(_reported_page(), 967)
    cfg = _cfg(persons=1, max_total=1600, household={"persons": 1})
    r = evaluate(l, cfg)
    assert _match(r) == (967, 195)                   # € 967 bare rent, € 195 service costs on top
    assert price_basis(r.flags, l.price_includes_utilities) == "excl"
    assert r.evidence["price_per_person"] == "Kale huurprijs: € 967 per maand per persoon."
    msg = fmt.format_new_match(l, r, cfg, "Van der Linden")
    assert "💶 <b>€967 excl.</b> each + €195 costs a month · rent per person or room" in msg
    assert "€483" not in msg and "€484" not in msg


def test_cruquiusweg_as_the_page_read_on_19_nov_2026():
    # The saved page says € 966 per person, "Gedeelde huisvesting voor twee bewoners" and "Je kunt je samen met
    # een toekomstig medebewoner aanmelden". It does not say how many of the two places are free, so no count is
    # shown. Each person pays € 966 + € 195: for two that is over a budget of € 1.600, which is why the home is
    # not on the dashboard today.
    html = (FIX / "vanderlinden_detail_2703.html").read_text()
    r = evaluate(_cruquiusweg(html, 966), _cfg())
    assert not r.matched
    assert "capacity:2" in r.flags and "service_costs:195" in r.flags and "price_per_person" in r.flags
    assert not [f for f in r.flags if f.startswith("places_free")]             # the page gives no count: none is made up
    assert "apply_together" in r.flags
    assert r.evidence["apply_together"] == "Je kunt je samen met een toekomstig medebewoner aanmelden"
    # with a budget that allows it, the home stays for review: each pays € 966 excl., how many places are free is to be asked
    r = evaluate(_cruquiusweg(html, 966), _cfg(max_total=2400))               # 2 x 1161 = 2322
    assert _match(r) == (966, 195) and r.matches[0].tier == "ok"
    assert "places_unclear" in r.flags and "rent_unclear" not in r.flags
    assert not evaluate(_cruquiusweg(html, 966), _cfg(max_total=2300)).matched


def test_van_der_linden_search_page_alone_does_not_halve_a_rent():
    # The search page shows "€ 966 per maand, 3 kamers, 86 m²" for one place in a home for two, exactly as it
    # shows a whole home. Until the listing's own page has been read, the rent is not divided: an alert sent
    # before that read once said € 483 a person.
    from hunter.sources.vanderlinden import parse_page
    cards = parse_page((FIX / "vanderlinden.html").read_text())
    assert cards and all(c.extra.get("rent_basis_unread") is True for c in cards)
    l = Listing("vanderlinden", "2703", "https://x/2703/", "Cruquiusweg 85-R, Amsterdam", "amsterdam", price_total=966,
                rooms=3, area_m2=86, address="Cruquiusweg 85-R", extra={"rent_basis_unread": True})
    r = evaluate(l, _cfg())
    assert _match(r) == (None, 0) and "rent_unclear" in r.flags and r.matches[0].tier == "ok"
    msg = fmt.format_new_match(l, r, _cfg(), "Van der Linden")
    assert "€483" not in msg and "💶 <b>€966</b>/month listed · each one's share is not settled" in msg
    # the page read: the mark is gone and the page's own words decide
    read = apply_details(l, (FIX / "vanderlinden_detail_2703.html").read_text())
    assert "rent_basis_unread" not in read.extra and not evaluate(read, _cfg()).matched
    # a whole home of the same agency is split once its page has been read
    home = make_listing(source="vanderlinden", price_total=1500, bedrooms=2)
    assert _match(evaluate(home, _cfg())) == (750, 0)
    # and on later runs, when the search page is seen again and the saved read is put back onto it
    from hunter.runner import apply_detail_snapshot, detail_snapshot
    read_home = make_listing(source="vanderlinden", price_total=1500, bedrooms=2, description="Ruim appartement.")
    snapshot = detail_snapshot(read_home, 1500, [], {})
    seen_again = make_listing(source="vanderlinden", price_total=1500, bedrooms=None, rooms=3,
                              extra={"rent_basis_unread": True})
    assert _match(evaluate(make_listing(source="vanderlinden", price_total=1500, rooms=3,
                                        extra={"rent_basis_unread": True}), _cfg())) == (None, 0)
    apply_detail_snapshot(seen_again, snapshot)
    assert "rent_basis_unread" not in seen_again.extra and _match(evaluate(seen_again, _cfg())) == (750, 0)
    # a read by older rules did not look for what the rent buys: the question stays open until the page is read again
    old = dict(snapshot, v=DETAIL_VERSION - 1)
    seen_again = make_listing(source="vanderlinden", price_total=1500, rooms=3, extra={"rent_basis_unread": True})
    apply_detail_snapshot(seen_again, old)
    assert seen_again.extra.get("rent_basis_unread") is True and _match(evaluate(seen_again, _cfg())) == (None, 0)


def test_a_home_whose_page_was_read_stays_split_on_the_next_runs(tmp_path):
    # Every run the search page comes back with "not read yet". The saved read answers that; a run once put the
    # mark back and every home of the source turned into "Rent basis unclear" from its second run on.
    cfg = _cfg()

    def cards(*more):
        return [make_listing("1", price_total=1500, bedrooms=None, rooms=3,
                             extra={"rent_basis_unread": True, "label": "nieuw"})] + list(more)

    class Reads(FakeSource):
        def fetch_details(self, listing, http):
            self.detail_calls += 1
            listing.description = "Ruim appartement met twee slaapkamers."
            listing.bedrooms = 2
            listing.extra.pop("rent_basis_unread", None)
            return listing

    def run(src):
        st = State(str(tmp_path / "data"), cfg)
        run_once(cfg, st, {"fake": src}, FakeNotifier(), http=None, now=NOW, docs_dir=str(tmp_path / "docs"))
        return State(str(tmp_path / "data"), cfg)

    st = run(FakeSource("fake", []))                                     # the source's first run is silent otherwise
    st.seen["fake:0"] = {"source": "fake", "first_seen": "x", "last_seen": "x", "matched": []}
    st.save(NOW)
    first = Reads("fake", cards())
    st = run(first)
    assert first.detail_calls == 1 and st.seen["fake:1"]["details"]["v"] == DETAIL_VERSION
    card = st.matches["fake:1"]
    assert "rent_unclear" not in card["flags"] and card["matches"][0]["price_pp"] == 750
    again = Reads("fake", cards())
    st = run(again)                                                      # only the search page this time
    card = st.matches["fake:1"]
    assert again.detail_calls == 0
    assert "rent_unclear" not in card["flags"] and card["matches"][0]["price_pp"] == 750
    assert "rent_basis_unread" not in card["listing"]["extra"] and card["listing"]["extra"]["label"] == "nieuw"
    # a page that cannot be read keeps the question open, and takes nothing away from the home beside it
    class Fails(FakeSource):
        def fetch_details(self, listing, http):
            raise OSError("no answer")

    other = make_listing("2", price_total=1500, bedrooms=None, rooms=3, area_m2=80, extra={"rent_basis_unread": True})
    st = run(Fails("fake", cards(other)))
    assert "rent_unclear" in st.matches["fake:2"]["flags"] and st.matches["fake:2"]["matches"][0]["price_pp"] is None
    assert "rent_unclear" not in st.matches["fake:1"]["flags"]


# ----------------------------------------------------------------------------- a whole home, split in two
def test_a_whole_home_is_split_between_the_two():
    r = _judge("Ruim appartement met twee slaapkamers. Huurprijs € 1.400 per maand exclusief gas, water en licht.",
               price_total=1400, bedrooms=2)
    assert _match(r) == (700, 0)
    assert rent_basis(r.flags) == "home" and price_basis(r.flags, False) == "excl"
    assert "rent_unclear" not in r.flags and "places_unclear" not in r.flags
    l = make_listing(price_total=1400, bedrooms=2, price_includes_utilities=False)
    msg = fmt.format_new_match(l, evaluate(l, _cfg()), _cfg(), "Fake")
    assert "💶 <b>€700 excl.</b> each a month · €1.400 for the home" in msg


def test_incl_and_excl_only_where_the_listing_establishes_it():
    r = _judge(price_total=1400)
    assert price_basis(r.flags, None) is None                    # nothing said: no label
    assert price_basis(r.flags, True) == "incl"
    assert price_basis(r.flags, False) == "excl"
    r = _judge(price_total=1300, extra={"service_costs": 80, "service_costs_on_top": True})
    assert price_basis(r.flags, True) == "excl"                  # utilities are in, service costs are not
    assert _match(r) == (650, 40)
    r = _judge("Verplichte afname van een parkeerplaats voor € 150 per maand.", price_total=1300)
    assert price_basis(r.flags, None) == "excl" and _match(r) == (650, 75)
    msg = fmt.format_new_match(make_listing(price_total=1400), _judge(price_total=1400), _cfg(), "Fake")
    assert "💶 <b>€700</b> each a month · €1.400 for the home" in msg and "incl." not in msg and "excl." not in msg


def test_an_odd_rent_is_shared_with_halves_rounded_up():
    assert _match(_judge(price_total=1249)) == (625, 0)
    r = _judge(price_total=1249, extra={"service_costs": 85, "service_costs_on_top": True})
    assert _match(r) == (625, 42)                                # (1249 + 85) / 2 = 667 each


def test_rent_stated_for_two_people_is_the_rent_that_is_split():
    r = _judge("Prijs voor 2 personen € 1.500 per maand.", price_total=1400)
    assert "rent_for_2:1500" in r.flags and _match(r) == (750, 0)


# ----------------------------------------------------------------------------- rent per person or per room
def test_rent_per_person_is_paid_by_each_and_never_divided():
    r = _judge("De huur is € 700 per persoon per maand.", price_total=700, bedrooms=2)
    assert _match(r) == (700, 0) and rent_basis(r.flags) == "place"
    assert not _judge("De huur is € 850 per persoon per maand.", price_total=850).matched      # 1700 for two
    # the site's service costs belong to the same one-person contract: each pays them
    r = _judge("Huur € 700 per persoon.", price_total=700, extra={"service_costs": 60, "service_costs_on_top": True})
    assert _match(r) == (700, 60)
    assert not _judge("Huur € 700 per persoon.", price_total=700,
                      extra={"service_costs": 120, "service_costs_on_top": True}).matched       # 2 x 820
    # a compulsory parking place is one per home: 2 x 700 + 150 = 1550, each 700 rent and 75 on top
    r = _judge("Huur € 700 per persoon per maand. Verplichte afname van een parkeerplaats voor € 150 per maand.",
               price_total=700)
    assert _match(r) == (700, 75)


def test_a_listing_that_rents_one_room_is_priced_per_place():
    r = _judge("Je huurt één slaapkamer met eigen badkamer. Er zijn 2 plekken vrij.", price_total=650)
    assert "price_per_person" in r.flags and "places_free:2" in r.flags
    assert r.evidence["price_per_person"] == "Je huurt één slaapkamer met eigen badkamer"
    assert _match(r) == (650, 0)
    r = _judge("You rent one bedroom. The other bedroom is already rented out.", price_total=650)
    assert not r.matched and "places_free:1" in r.flags and "capacity:2" in r.flags


def test_free_places_decide_whatever_the_rent():
    # where the listing shows that the home is let by the place, a count of one is the end of it for two people
    for text in ("De andere kamer is al verhuurd.", "Eén van de twee plekken is al bezet.",
                 "Je huurt één slaapkamer. Er is nog 1 plek vrij.",
                 "Huur € 600 per persoon. Wij zoeken een nieuwe huisgenoot.",
                 "Gedeelde huisvesting voor twee bewoners. One room is available from January."):
        r = _judge(text, price_total=600)
        assert not r.matched and "places_free:1" in r.flags, text
    r = _judge(price_total=1200, extra={"places_free": 1, "site_facts": "Space for 1 person"})   # the site's field
    assert not r.matched and r.evidence["places_free"] == "Space for 1 person"
    r = _judge(price_total=1200, extra={"places_free": 2, "site_facts": "Space for 2 people"})
    assert r.matched and places_available(r.flags) == 2
    assert not _judge(price_total=2000, extra={"places_free": 2}, cfg=_cfg(persons=3, max_total=2400), bedrooms=3).matched


@pytest.mark.parametrize("text", [
    "Er is nog 1 plek vrij.",                                 # at a viewing? in the home? the listing does not show
    "Wij zoeken een nieuwe huisgenoot.",
    "One room is available from January.",
    "We are looking for a new flatmate.",
    "0 plekken vrij.",
])
def test_a_count_without_proof_of_a_shared_home_is_a_question_not_a_verdict(text):
    # A whole home must never be dropped on such a sentence alone: it stays, with "Free places unclear".
    r = _judge(text, price_total=1400)
    assert r.matched and "places_unclear" in r.flags and not [f for f in r.flags if f.startswith("places_free")], text
    assert r.evidence["places_unclear"]


@pytest.mark.parametrize("text", [
    "Nog 1 plek beschikbaar voor de bezichtiging op zaterdag.",
    "Only 1 spot left for the viewing.",
    "Zoek je nog een huisgenoot? Wij helpen je graag.",
    "Looking for a flatmate? This apartment is perfect for two.",
    "Er is een kamer beschikbaar als werkkamer.",
    "Daarnaast is er 1 vrije kamer op zolder.",
    "1 room is available as a study.",
    "Ideaal om met een huisgenoot te delen. Vloer ter overname van de huidige huurder.",
])
def test_ordinary_sentences_set_no_count_at_all(text):
    r = _judge(text, price_total=1400)
    assert r.matched and _match(r)[0] in (700, None), text                    # None: "housemate" wording leaves the rent open
    assert not [f for f in r.flags if f.startswith("places_free") or f == "places_unclear"], text


def test_a_tenant_who_has_left_holds_no_place():
    for text in ("Gedeelde woning voor twee bewoners. De zittende huurder is vertrokken.",
                 "Shared apartment for two tenants. The current tenant has moved out.",
                 "Shared apartment for two tenants. The current tenant left last month."):
        assert "places_free" not in occupancy.read(text), text


def test_per_place_rent_with_housemates_and_no_count_asks_how_many_places_are_free():
    r = _judge("Huur € 650 per persoon. Je deelt de keuken met je huisgenoten.", price_total=650)
    assert _match(r) == (650, 0)
    assert "places_unclear" in r.flags and "housemates" in r.flags
    assert flag_label("places_unclear") == "Free places unclear"


# ----------------------------------------------------------------------------- the listing leaves the rent open
def test_housemates_without_a_rent_per_person_leave_each_share_open():
    r = _judge("Je deelt de woonkamer en keuken met een huisgenoot.", price_total=900)
    assert r.matched and _match(r) == (None, 0)                  # not 450: it may be the rent of one room
    assert "rent_unclear" in r.flags and "housemates" in r.flags and rent_basis(r.flags) is None
    assert r.matches[0].tier == "ok"                             # never "great" on a rent that is not settled
    msg = fmt.format_new_match(make_listing(price_total=900), r, _cfg(), "Fake")
    assert "💶 <b>€900</b>/month listed · each one's share is not settled" in msg and "€450" not in msg
    # over budget even if it is the rent of the whole home: no match
    assert not _judge("Je deelt de woonkamer en keuken met een huisgenoot.", price_total=1700).matched


def test_a_rent_too_low_for_the_home_is_not_divided():
    r = _judge(price_total=600, bedrooms=3, area_m2=110)
    assert "suspicious_price" in r.flags and "rent_unclear" in r.flags and _match(r) == (None, 0)


# ----------------------------------------------------------------------------- who may rent
EXCLUDED = [
    ("Alleen voor werkenden.", "working_only"),
    ("Deze woning is uitsluitend voor werkende huurders.", "working_only"),
    ("Professionals only.", "working_only"),
    ("Only for working people.", "working_only"),
    ("Working professionals only, no students.", "no_students"),
    ("Geen studenten.", "no_students"),
    ("Not suitable for students.", "no_students"),
    ("Minimumleeftijd 23 jaar.", "min_age:23"),              # the younger one is 19
    ("Huurders vanaf 23 jaar.", "min_age:23"),
    ("Minimum age 25.", "min_age:25"),
    ("Tenants must be at least 23 years old.", "min_age:23"),
    ("Minimale leeftijd: 21 jaar.", "min_age:21"),
    ("Maximumleeftijd 23 jaar.", "max_age:23"),               # the older one is 24
    ("Voor jongeren tot 23 jaar.", "max_age:23"),
    ("Geen woningdelers.", "no_sharers"),
    ("Niet geschikt voor woningdelers.", "no_sharers"),
    ("No sharers.", "no_sharers"),
    ("Alleen voor een stel.", "no_sharers"),                  # two friends are no couple
    ("Couple only.", "no_sharers"),
    ("Uitsluitend voor 1 persoon.", "max_persons:1"),
    # wordings a review found missing
    ("Huurders moeten minimaal 23 jaar zijn.", "min_age:23"),
    ("Minimaal 23 jaar oud.", "min_age:23"),
    ("You must be 23 or older.", "min_age:23"),
    ("No tenants under 25.", "min_age:25"),
    ("Geen huurders onder de 25.", "min_age:25"),
    ("Niet jonger dan 25 jaar.", "min_age:25"),
    ("Students 22-27 years old.", "min_age:22"),              # the younger one is 19
    ("Max. age 23.", "max_age:23"),
    ("Huurders maximaal 23 jaar.", "max_age:23"),
    ("Uitsluitend werkende huurders.", "working_only"),
    ("Alleen werkenden.", "working_only"),
]


@pytest.mark.parametrize("text,flag", EXCLUDED)
def test_a_stated_mismatch_is_filtered_out(text, flag):
    r = _judge(text)
    assert flag in r.flags and not r.matched


KEPT = [
    ("Studenten welkom.", "students_ok"),
    ("Alleen voor studenten.", "students_only"),
    ("Geschikt voor werkenden en studenten.", "students_ok"),
    ("Ideaal voor young professionals.", None),               # praise, not a condition
    ("Minimum age: 18.", None),
    ("Minimumleeftijd 19 jaar.", "min_age:19"),               # the younger one is exactly 19
    ("Maximum age 27.", "max_age:27"),
    ("Geschikt voor 2 personen.", "max_persons:2"),
    ("Woningdelers zijn welkom.", "sharers_ok"),
    ("Geen huisdieren en niet roken.", None),
    ("Alleen voor vrouwen.", "women_only"),                   # the household's settings do not say: stays for review
    # numbers and "alleen" that are about something else
    ("Huurcontract minimaal 2 jaar.", None),
    ("De woning is minimaal 25 jaar oud.", None),
    ("Het pand is niet jonger dan 30 jaar.", None),
    ("The room must be 20 m2.", None),
    ("Niet alleen werkenden zijn welkom.", None),
    ("Alleen werkenden of studenten.", None),
    ("Je woont hier niet alleen: werkende buren zijn dichtbij.", None),
]


@pytest.mark.parametrize("text,flag", KEPT)
def test_what_is_allowed_or_not_stated_stays(text, flag):
    r = _judge(text)
    assert r.matched, text
    if flag:
        assert flag in r.flags
    else:
        assert not [f for f in r.flags if f.partition(":")[0] in ("min_age", "max_age", "working_only", "no_students")], r.flags


@pytest.mark.parametrize("text", [
    "Available only for a couple of months.",                # "a couple of": a few
    "A couple only pays € 1400 per month.",
    "Beschikbaar alleen voor een stel maanden.",
    "Only for a couple of weeks in summer the garden is closed.",
])
def test_a_couple_of_months_is_no_couple(text):
    r = _judge(text)
    assert r.matched and "no_sharers" not in r.flags and "max_persons:2" not in r.flags, text


def test_no_place_left_is_no_match_for_anyone():
    for text in ("Twee van de twee plekken zijn al bezet.", "Gedeelde huisvesting voor twee bewoners. 0 plekken vrij.",
                 "Je huurt één slaapkamer. Er zijn geen plekken meer vrij."):
        r = _judge(text, price_total=600)
        assert not r.matched and "places_free:0" in r.flags, text
        assert not _judge(text, cfg=_cfg(persons=1, household={"persons": 1}), bedrooms=1, price_total=600).matched
    r = _judge(extra={"places_free": 0, "site_facts": "Space for 0 people"})          # a site's own field
    assert not r.matched and "places_free:0" in r.flags


def test_boundary_ages_use_the_youngest_and_the_oldest():
    for household, text, matched in (
            ({"ages": [23, 26]}, "Minimumleeftijd 23 jaar.", True), ({"ages": [23, 26]}, "Minimumleeftijd 24 jaar.", False),
            ({"ages": [23, 26]}, "Maximumleeftijd 26 jaar.", True), ({"ages": [23, 26]}, "Maximumleeftijd 25 jaar.", False),
            ({"age": 21}, "Minimumleeftijd 22 jaar.", False),                         # the older setting: one age
            ({"ages": []}, "Minimumleeftijd 30 jaar.", True)):                        # no ages given: nothing to hold it against
        assert _judge(text, cfg=_cfg(household=household)).matched is matched, (household, text)


def test_a_couple_keeps_the_homes_that_refuse_sharers():
    couple = dict(TWO_STUDENTS, sharers=False)
    for text in ("Geen woningdelers.", "Alleen voor een stel.", "Couple only."):
        assert _judge(text, cfg=_cfg(household=couple)).matched, text
        assert not _judge(text).matched, text


def test_nobody_in_particular_sees_every_home(cfg):
    for text, flag in EXCLUDED:
        if flag.startswith("max_persons"):
            continue                                  # a limit of one person is no home for two, whoever they are
        r = evaluate(make_listing(description=text), cfg)
        assert "2 people" in [m.search for m in r.matches], text


# ----------------------------------------------------------------------------- the "2 people" mark
def test_a_limit_of_two_is_a_plain_fact_for_two_people():
    # Kamernet's own field "Aantal huurders: 2 personen" used to show as a red warning "Max 2 persons".
    r = _judge(extra={"max_persons": 2, "site_facts": "Aantal huurders: 2 personen"})
    assert r.matched and "max_persons:2" in r.flags and "sharing_unclear" not in r.flags
    assert flag_label("max_persons:2") == "Up to 2 people"
    assert flag_kinds()["max_persons"] == "info"


def test_a_limit_worded_for_a_couple_says_what_to_check():
    for text in ("Geschikt voor een alleenstaande of stel.", "Suitable for a single person or a couple.",
                 "Voorwaarden: max. 1 persoon of stel."):
        r = _judge(text)
        assert r.matched and "max_persons:2" in r.flags and "sharing_unclear" in r.flags, text
        assert r.evidence["sharing_unclear"]
    assert flag_label("sharing_unclear") == "Sharing policy unclear"
    assert flag_kinds()["sharing_unclear"] == "check"
    # the listing answers it itself: nothing to check
    assert "sharing_unclear" not in _judge("Alleenstaande of stel. Woningdelers zijn welkom.").flags
    assert "sharing_unclear" not in _judge("Alleenstaande of stel. Geen woningdelers.", cfg=_cfg(household={})).flags
    assert "sharing_unclear" not in _judge("Geschikt voor 2 personen.").flags
    assert "sharing_unclear" not in _judge("Alleenstaande of stel. Huur € 600 per persoon.", price_total=600).flags


@pytest.mark.parametrize("text", [
    "Ideaal voor een stel.", "Suitable for a couple.", "Geschikt voor een jong stel of starters.",
    "Perfect for a young couple.", "Uitermate geschikt voor tweeverdieners.",
])
def test_a_home_offered_with_a_couple_in_mind_says_what_to_check(text):
    # No limit and no verdict: two sharers keep the home, and the card names the question.
    r = _judge(text)
    assert _match(r) == (700, 0) and "sharing_unclear" in r.flags and r.evidence["sharing_unclear"], text
    assert not [f for f in r.flags if f.startswith("max_persons")], text


@pytest.mark.parametrize("text", [
    "Ideaal voor een stel of twee vrienden.", "Ideaal voor een stel. Woningdelers zijn welkom.",
    "Niet geschikt voor een stel.", "Beschikbaar voor een stel maanden.", "Perfect for a couple of weeks.",
    "Suitable for a couple or sharers.", "Ideaal voor een stel. Je deelt de keuken met 2 huisgenoten.",
])
def test_couple_wording_that_leaves_nothing_to_ask(text):
    assert "sharing_unclear" not in _judge(text).flags, text


@pytest.mark.parametrize("text", [
    "Ruim appartement. Vanaf 25 jaar.", "Ruim appartement. Maximaal 28 jaar.", "Voor jonge professionals tot 30 jaar.",
    "25+", "Under 30 years preferred.", "Looking for someone older than 25.",
])
def test_loose_age_wording_is_a_question_and_excludes_nobody(text):
    # ages 19 and 24: a firm "minimaal 25 jaar" would exclude them, this wording does not
    r = _judge(text)
    assert _match(r) == (700, 0) and "age_unclear" in r.flags and r.evidence["age_unclear"], text
    assert not [f for f in r.flags if f.startswith(("min_age", "max_age"))], text


@pytest.mark.parametrize("text", [
    "Huurcontract vanaf 2 jaar. Bouwjaar 1930. Vanaf 1 januari beschikbaar.",
    "Wij verhuren al 25 jaar woningen. Het pand is ouder dan 40 jaar. Contract maximaal 24 jaar.",
    "Energielabel A+. Servicekosten € 45 + € 20.", "Winkels op 20+ minuten lopen.",
    "Onze makelaars hebben vanaf 20 jaar ervaring.", "Kinderen tot 18 jaar reizen gratis met de tram.",
    "Ruim appartement van 70 m2 op de 3e verdieping.",
])
def test_numbers_that_are_no_age(text):
    assert "age_unclear" not in _judge(text).flags, text


def test_an_age_limit_that_was_read_needs_no_question():
    r = _judge("Minimumleeftijd 21 jaar. Vanaf 21 jaar.", cfg=_cfg(household={}))
    assert "min_age:21" in r.flags and "age_unclear" not in r.flags
    assert flag_label("age_unclear") == "Age limit unclear" and flag_kinds()["age_unclear"] == "check"


def test_every_flag_has_a_label_and_a_kind():
    kinds = flag_kinds()
    assert set(kinds) == set(FLAG_LABELS)
    assert set(kinds.values()) == {"warn", "check", "good", "info"}
    for name in ("sharing_unclear", "places_unclear", "rent_unclear", "capacity", "places_free"):
        assert name in FLAG_LABELS
    assert flag_label("capacity:2") == "Shared home for 2 people" and flag_label("places_free:1") == "Places free: 1"


# ----------------------------------------------------------------------------- what the text says about places
SAYS = [
    ("Gedeelde huisvesting voor twee bewoners.", {"capacity": 2}),
    ("Shared accommodation for two residents.", {"capacity": 2}),
    ("Je deelt de woning met 2 huisgenoten.", {"capacity": 3}),
    ("You share the flat with one other tenant.", {"capacity": 2}),
    ("Eén van de twee plekken is al bezet.", {"capacity": 2, "places_free": 1}),
    ("One of the two places is already occupied.", {"capacity": 2, "places_free": 1}),
    ("De andere kamer is al verhuurd.", {"capacity": 2, "places_free": 1}),
    ("The other bedroom is already rented out.", {"capacity": 2, "places_free": 1}),
    ("Er is nog 1 plek vrij.", {"places_free": 1}),
    ("Nog 1 kamer beschikbaar in dit studentenhuis.", {"places_free": 1}),
    ("1 kamer komt vrij per 1 januari.", {"places_free": 1}),
    ("Only 1 room left.", {"places_free": 1}),
    ("2 places available.", {"places_free": 2}),
    ("We are looking for 2 new housemates.", {"places_free": 2}),
    ("Wij zoeken een nieuwe huisgenoot.", {"places_free": 1}),
    ("Je huurt één slaapkamer met eigen sanitair.", {"one_place": True}),
    ("You rent one bedroom with private sanitary facilities.", {"one_place": True}),
    ("Twee van de twee plekken zijn al bezet.", {"capacity": 2, "places_free": 0}),
    ("0 plekken vrij.", {"places_free": 0}),
    ("Er zijn geen plekken meer vrij.", {"places_free": 0}),
    ("All places are already taken.", {"places_free": 0}),
    ("You can apply together with a prospective flatmate.", {"together": True}),
    # somebody lives in the shared home for two: that suggests one place is left, it does not state it
    ("Gedeelde woning voor twee bewoners. De huidige bewoner blijft.", {"capacity": 2, "places_guess": 1}),
    ("Shared apartment for two tenants. The current tenant is a student.", {"capacity": 2, "places_guess": 1}),
    # how many people fit, in the words a whole home is offered with too
    ("Geschikt voor in totaal 2 bewoners.", {"capacity": 2}),
    ("2 tenants in total.", {"capacity": 2}),
    ("In totaal 4 huisgenoten.", {"capacity": 4}),
    # "you can apply together" says a pair may apply; it does not say that both places are free
    ("Gedeelde woning voor twee bewoners. Je kunt je samen met een vriend aanmelden.",
     {"capacity": 2, "together": True}),
    # the tenant who lives there now is leaving: the place is not taken
    ("Gedeelde woning voor twee bewoners. De huidige bewoner vertrekt per 1 december.", {"capacity": 2}),
    # they have given notice, and the home is let as a whole
    ("Gedeelde woning voor twee bewoners. De huidige huurders hebben de huur opgezegd. De gehele woning komt vrij.",
     {"capacity": 2}),
    ("Gedeelde woning voor twee bewoners. De gehele woning komt per 1 januari beschikbaar. Vraag de huidige bewoner.",
     {"capacity": 2}),
    # one place taken is stated: that wins over "you can apply together"
    ("Shared accommodation for two residents. One of the two places is already occupied. "
     "You can apply together with a prospective flatmate.", {"capacity": 2, "places_free": 1, "together": True}),
]


@pytest.mark.parametrize("text,expected", SAYS)
def test_what_the_text_says_about_places(text, expected):
    said = {k: v[0] for k, v in occupancy.read(text).items()}
    said.pop("shared", None)                                     # tested on its own below
    said.pop("housemates", None)
    said.pop("places_firm", None)
    assert said == expected


def test_what_shows_that_a_home_is_let_by_the_place():
    for text in ("Gedeelde huisvesting voor twee bewoners.", "Je huurt één slaapkamer met eigen sanitair.",
                 "Eén van de twee plekken is al bezet.", "The other bedroom is already rented out."):
        assert "shared" in occupancy.read(text), text
    for text in ("Je deelt de woning met 2 huisgenoten.", "Er is nog 1 plek vrij.", "Wij zoeken een nieuwe huisgenoot.",
                 "You can apply together with a prospective flatmate."):
        assert "shared" not in occupancy.read(text), text
    # "in totaal 2 bewoners" is how a whole home for two is offered too: no shared home, and nobody else in it
    for text in ("Geschikt voor in totaal 2 bewoners.", "2 tenants in total."):
        said = occupancy.read(text)
        assert "shared" not in said and "housemates" not in said, text


def test_what_shows_that_other_people_share_the_home():
    for text in ("Gedeelde huisvesting voor twee bewoners.", "Shared house for 3 students.",
                 "Je deelt de woning met 2 huisgenoten.", "Kamer in studentenhuis met 3 huisgenoten.",
                 "You share the flat with one other tenant.", "De andere kamer is al verhuurd.", "In totaal 4 huisgenoten."):
        assert "housemates" in occupancy.read(text), text
    for text in ("Er is nog 1 plek vrij.", "Geschikt voor in totaal 2 bewoners.", "Geen huisgenoten.",
                 "Ruim appartement met twee slaapkamers."):
        assert "housemates" not in occupancy.read(text), text


@pytest.mark.parametrize("text,price", [
    ("Gedeelde woning voor 3 bewoners.", 966),
    ("Gedeelde huisvesting voor twee bewoners. Kale huurprijs: € 966 per maand. "
     "Je kunt je samen met een medebewoner aanmelden.", 966),
    ("Kamer in studentenhuis met 3 huisgenoten.", 966),
    ("Shared house for 3 students.", 1400),
    ("You will share the apartment with 2 other students.", 900),
])
def test_a_shared_home_without_a_rent_per_person_is_never_halved(text, price):
    # The listing says other people share the home and gives one amount. It may be one place's rent or the
    # home's: the amount is shown as listed, each one's share is left open, and the home stays for review.
    r = _judge(text, price_total=price)
    assert _match(r) == (None, 0) and r.matches[0].tier == "ok", text
    assert "rent_unclear" in r.flags and "housemates" in r.flags and r.evidence["housemates"], text
    l = make_listing(description=text, price_total=price)
    msg = fmt.format_new_match(l, evaluate(l, _cfg()), _cfg(), "Fake")
    assert ("€%s" % fmt.euro(price // 2).lstrip("€")) not in msg and "each one's share is not settled" in msg, text


def test_a_shared_home_with_a_rent_per_person_is_priced_by_the_place():
    r = _judge("Gedeelde woning voor 3 bewoners. Huur € 600 per persoon per maand.", price_total=600)
    assert _match(r) == (600, 0) and "rent_unclear" not in r.flags


@pytest.mark.parametrize("text", [
    "Mooi appartement. Geschikt voor in totaal 2 bewoners. Bezichtigingen in overleg met de huidige huurder.",
    "In totaal 2 bewoners. Current tenant leaves on 1 March. Viewings with the current tenant.",
    "Ruim appartement, 2 tenants in total. The current tenant is moving abroad.",
])
def test_a_whole_home_whose_tenant_is_still_there_is_split_as_a_whole_home(text):
    r = _judge(text, price_total=1400)
    assert _match(r) == (700, 0), text
    assert not [f for f in r.flags if f.startswith(("places_free", "places_unclear", "rent_unclear", "housemates"))], text


@pytest.mark.parametrize("text", [
    "Gedeelde woning voor 2 bewoners. De huidige bewoner zoekt een nieuwe huisgenoot.",
    "Shared apartment for two tenants. The current tenant is a student.",
    "Gedeelde huisvesting voor twee bewoners. De huidige bewoner blijft.",
])
def test_somebody_living_there_is_a_question_not_a_count(text):
    # It suggests that one place is taken. The listing does not say so: the home stays, with what to ask.
    r = _judge(text, price_total=900)
    assert r.matched and "places_unclear" in r.flags and r.evidence["places_unclear"], text
    assert not [f for f in r.flags if f.startswith("places_free")], text
    # and with a rent per person too: two places may be free
    r = _judge(text + " Huur € 600 per persoon.", price_total=600)
    assert _match(r) == (600, 0) and "places_unclear" in r.flags, text


@pytest.mark.parametrize("text", [
    "Huur € 700 per persoon. Nog 1 plek vrij op de wachtlijst.",
    "Huur € 700 per persoon. Nog 1 plek vrij op de inschrijflijst.",
    "Rent € 700 per person. 1 spot left on the waiting list.",
    "Rent € 700 per person. Only 1 spot left for the open day.",
    "Huur € 700 per persoon. Nog 2 plekken vrij voor de open dag.",
])
def test_a_place_on_a_list_or_at_an_open_day_is_no_place_in_the_home(text):
    r = _judge(text, price_total=700)
    assert _match(r) == (700, 0), text
    assert not [f for f in r.flags if f.startswith(("places_free", "places_unclear"))], text


@pytest.mark.parametrize("text", [
    "Huur € 700 per persoon. Nog 1 plek vrij voor de rondleiding.",
    "Huur € 700 per persoon. Nog 1 plek vrij voor een afspraak.",
    "Huur € 700 per persoon. Nog 1 plek vrij op zaterdag.",
    "Rent € 700 per person. Only 1 spot left for the tour.",
])
def test_a_count_of_places_with_only_a_rent_per_person_is_a_question(text):
    # "plek" is a place in the home, at a tour or on a Saturday. Only a count of rooms is a count of rooms.
    r = _judge(text, price_total=700)
    assert _match(r) == (700, 0) and "places_unclear" in r.flags, text
    assert not [f for f in r.flags if f.startswith("places_free")], text


def test_a_count_of_rooms_with_a_rent_per_person_is_a_count():
    for text in ("Huur € 700 per persoon. Nog 1 kamer vrij.", "Rent € 700 per person. Only 1 room left."):
        r = _judge(text, price_total=700)
        assert not r.matched and "places_free:1" in r.flags, text


@pytest.mark.parametrize("text", [
    "Kamer te huur in gezellig studentenhuis.", "Room available in shared flat.",
    "Room for rent in a 3-bedroom apartment.", "Kamer in woongroep, 4 bewoners.",
    "Wij (2 studenten) zoeken een derde huisgenoot.", "Shared apartment, 2 tenants. Each tenant has an own contract.",
    "Ieder huurt een eigen kamer met een eigen huurcontract.", "Eigen huurcontract per persoon.",
    "Room in a 3-bedroom apartment.", "Je woont in een gedeelde woning met 3 studenten.",
    "Kamer in een gedeeld appartement.",
])
def test_a_room_in_a_home_others_live_in_is_never_halved(text):
    r = _judge(text, price_total=966)
    assert _match(r) == (None, 0) and "rent_unclear" in r.flags and "housemates" in r.flags, text


@pytest.mark.parametrize("text", [
    "Ook beschikbaar voor een woongroep of woningdelers van max 3 personen.",
    "De tweede huurder moet ook een inkomen aantonen.",
    "3-kamer appartement te huur met gedeelde tuin en berging.",
    "Geen gedeelde bewoning. Not suitable for a shared house.",
    "Ruim appartement tegenover een studentenhuis, 2 slaapkamers.",
    "Living room for rent? No: the whole apartment.", "De werkkamer te huur als kantoor is er niet bij.",
    # a whole home that may be used as a shared home: the homes two sharers are looking for
    "Woningdelen toegestaan: deze woning mag als gedeelde woning worden verhuurd.",
    "Ook te gebruiken als gedeelde woning.", "Suitable as a shared apartment for 2 friends.",
    "Can be rented as a shared flat by 2 people.", "Geschikt als gedeelde woning voor 2 personen.",
    "Een gedeelde woning met je vriend? Dat kan hier.",
    "The rooms in this 2-bedroom apartment are bright and spacious.",
    "Gedeelde huisvesting is niet toegestaan.", "Eigen huurcontract per persoon is niet mogelijk.",
    "Kamer te huur? Nee, een compleet appartement.", "Shared house? Not at all: the whole house is yours.",
])
def test_a_whole_home_that_only_mentions_sharing_is_split(text):
    r = _judge(text, price_total=1400)
    assert _match(r) == (700, 0) and "housemates" not in r.flags and "rent_unclear" not in r.flags, text


@pytest.mark.parametrize("text", ["Een complex met 40+ appartementen.", "Wij verhuren 50+ woningen in Den Haag."])
def test_a_number_of_homes_is_no_minimum_age(text):
    r = _judge(text)
    assert _match(r) == (700, 0) and "age_unclear" in r.flags, text
    assert not [f for f in r.flags if f.startswith("min_age")], text
    assert "min_age:55" in _judge("55+ woning in rustig complex.", cfg=_cfg(household={})).flags


SAYS_NOTHING = [
    "Geen huisgenoten: de woning is geheel voor jezelf.",
    "Het appartement heeft twee slaapkamers en een ruime woonkamer.",
    "Er zijn 2 slaapkamers beschikbaar als werkkamer.",
    "De huidige huurder vertrekt per 1 december.",
    "Geschikt voor 2 personen.",
    "De woning is per direct beschikbaar.",
    "Er is een plaats vrij in de fietsenstalling.",
    "Er is 1 plek vrij in de garage.",
    "Rondrijden en hopen dat er ergens een vrije plek is.",       # "een" is an article here, not a count
    "1 parkeerplaats beschikbaar.",
    "One parking space available.",
    "Nog 1 kamer vrij indeelbaar.",
    "2 rooms are available free of charge for storage.",
    "Wij zoeken geen nieuwe huisgenoot maar een stel.",
    "Ideaal voor twee studenten.",
    "A place to call home is available now.",
    "Er is nog 1\nplek vrij.",                                   # two lines of the listing: no statement
    # found by a second review: none of these says that the rent buys one room or that a place is taken
    "Je huurt geen appartement, maar een eengezinswoning met twee slaapkamers.",
    "Je huurt dus geen volledig zelfstandig appartement.",
    "Het gehele appartement heeft twee slaapkamers. 1 kamer is beschikbaar als werkkamer.",
    "One room is available as an office.",
    "Geen plek vrij in de fietsenstalling.",
]


@pytest.mark.parametrize("text", SAYS_NOTHING)
def test_wording_that_says_nothing_about_places(text):
    assert occupancy.read(text) == {}
    assert _judge(text).matched


# ----------------------------------------------------------------------------- what the dashboard is given
def _data(cards, tmp_path, cfg=None):
    state = State(str(tmp_path / "data"), cfg or _cfg())
    for uid, (listing, extra) in cards.items():
        r = evaluate(listing, cfg or _cfg())
        entry = {"listing": listing.to_dict(), "first_seen": "2026-11-19T10:00:00Z", "last_seen": "2026-11-19T10:00:00Z",
                 "matches": [m.to_dict() for m in r.matches], "flags": r.flags, "evidence": r.evidence,
                 "best_tier": r.best_tier}
        entry.update(extra)
        state.matches[uid] = entry
    return build_data(cfg or _cfg(), state)


def test_data_json_carries_rent_basis_places_and_position(tmp_path):
    home = make_listing("1", price_total=1400, price_includes_utilities=True, address="Daltonstraat 12-II")
    place = make_listing("2", price_total=650, description="Huur € 650 per persoon. Er zijn nog 2 kamers vrij.",
                         address="Daltonstraat")
    data = _data({"fake:1": (home, {"geo": {"lat": 52.0712345, "lon": 4.3012345, "precision": "address", "q": "x"}}),
                  "fake:2": (place, {"geo": None})}, tmp_path)
    assert data["schema"] == 2 and data["household"] == {"persons": 2}
    assert data["flag_kinds"]["sharing_unclear"] == "check" and data["flag_labels"]["max_persons"] == "Up to {n} people"
    by = {l["uid"]: l for l in data["listings"]}
    a, b = by["fake:1"], by["fake:2"]
    assert (a["rent_basis"], a["price_basis"], a["capacity"], a["places_available"]) == ("home", "incl", None, None)
    assert a["matches"] == [{"search": "2 people", "persons": 2, "price_pp": 700, "extra_pp": 0, "tier": "great"}]
    assert (a["lat"], a["lon"], a["geo"]) == (52.07123, 4.30123, "address")
    assert a["home_key"] == "a:den-haag:daltonstraat:12ii"
    assert (b["rent_basis"], b["price_basis"], b["places_available"]) == ("place", None, 2)
    assert b["matches"][0]["price_pp"] == 650
    assert (b["lat"], b["lon"], b["geo"]) == (None, None, None)
    assert b["home_key"] == "u:fake:2"                           # a street name alone can be several homes
    assert '"ages"' not in json.dumps(data) and "occupation" not in json.dumps(data)   # who is looking stays out


def test_home_key_names_one_home_across_sites_and_spellings():
    a = home_key({"address": "Cruquiusweg 85-R", "city": "amsterdam"}, "vanderlinden:2703")
    b = home_key({"address": "cruquiusweg 85 R", "city": "amsterdam"}, "pararius:9")
    assert a == b == "a:amsterdam:cruquiusweg:85r"
    assert home_key({"address": "Cruquiusweg 85R", "city": "amsterdam"}, "x:9") == a
    assert home_key({"address": "Cruquiusweg 87", "city": "amsterdam"}, "x:1") != a
    assert home_key({"address": "Cruquiusweg 85-R", "city": "rotterdam"}, "x:1") != a
    assert home_key({"address": None, "city": "amsterdam"}, "x:1") == "u:x:1"
    assert home_key({"address": "Centrum", "city": "amsterdam"}, "x:2") == "u:x:2"
    # a digit in the street's name is no house number: hiding one home there must not hide the street
    assert home_key({"address": "2e De Riemerstraat", "city": "den-haag"}, "x:3") == "u:x:3"
    assert home_key({"address": "2e De Riemerstraat", "city": "den-haag"}, "x:4") == "u:x:4"
    assert home_key({"address": "2e De Riemerstraat 14", "city": "den-haag"}, "x:5") == "a:den-haag:2ederiemerstraat:14"
    # a floor after a hyphen is part of the number: "Kerkstraat 10-1" is not "Kerkstraat 101"
    first = home_key({"address": "Kerkstraat 10-1", "city": "amsterdam"}, "x:6")
    assert first == "a:amsterdam:kerkstraat:10-1" != home_key({"address": "Kerkstraat 101", "city": "amsterdam"}, "x:7")


# ----------------------------------------------------------------------------- where the household is kept
def test_the_household_can_live_outside_the_repository(monkeypatch, tmp_path):
    assert hunter.config.load_config()["household"]["ages"] == []           # config.json names no ages
    monkeypatch.setenv("HOUSEHOLD", json.dumps(TWO_STUDENTS))
    assert hunter.config.load_config()["household"] == TWO_STUDENTS
    monkeypatch.delenv("HOUSEHOLD")
    path = tmp_path / "household.json"
    path.write_text(json.dumps({"persons": 2, "occupation": "working", "_note": "ignored"}))
    monkeypatch.setattr(hunter.config, "HOUSEHOLD_PATH", path)
    assert hunter.config.load_config()["household"] == {"persons": 2, "occupation": "working", "ages": [], "sharers": False}
    for broken in ("not json", "[1, 2]", ""):
        monkeypatch.setenv("HOUSEHOLD", broken)
        path.write_text(broken)
        assert hunter.config.load_config()["household"]["occupation"] is None    # unreadable: nobody is excluded
    monkeypatch.delenv("HOUSEHOLD")
    path.write_bytes(b"\xff\xfe{\x00")                                         # not text at all
    assert hunter.config.load_config()["household"]["occupation"] is None


def test_config_json_searches_for_two_people_only():
    c = hunter.config.load_config()
    assert [s["persons"] for s in c["searches"]] == [2] and c["household"]["persons"] == 2


def test_a_run_without_the_household_sends_no_alert_for_a_home_that_was_ruled_out(tmp_path):
    # The run with the household rules a home out ("alleen voor werkenden"). A run on another machine that has no
    # household file would match it again and call it new: it is shown again, but nobody is alerted. A home that
    # run sees for the first time is alerted as always.
    with_household = _cfg()
    with_household["household_private"] = True
    without = _cfg(household={"persons": 2})
    ruled_out = make_listing("1", description="Alleen voor werkenden.")
    cheaper = dict(description="Geen studenten.", price_total=1500)      # ruled out too, and its rent falls later

    def run(c, listings):
        st, n = State(str(tmp_path / "data"), c), FakeNotifier()
        run_once(c, st, {"fake": FakeSource("fake", listings)}, n, http=None, now=NOW, docs_dir=str(tmp_path / "docs"))
        return State(str(tmp_path / "data"), c), n

    run(with_household, [make_listing("0")])                             # the source's first run is silent
    st, n = run(with_household, [make_listing("0"), ruled_out, make_listing("3", **cheaper)])
    assert "fake:1" not in st.matches and "fake:3" not in st.matches and not n.messages
    cheaper["price_total"] = 1300
    st, n = run(without, [make_listing("0"), ruled_out, make_listing("2"), make_listing("3", **cheaper)])
    assert "fake:1" in st.matches and "fake:2" in st.matches and "fake:3" in st.matches
    assert len(n.messages) == 1 and "Nice flat 2" in n.messages[0]       # no "new", and no "price drop" either
    st, n = run(with_household, [make_listing("0"), ruled_out, make_listing("2"), make_listing("3", **cheaper)])
    assert "fake:1" not in st.matches and "fake:3" not in st.matches and not n.messages


def test_a_run_without_the_household_other_runs_had_says_so(tmp_path, capsys):
    # The scheduled run and the backup run at home write the same state. The state remembers only that a private
    # household was used, never what it holds.
    cfg = _cfg()
    cfg["household_private"] = True

    def run(c):
        st = State(str(tmp_path / "data"), c)
        run_once(c, st, {"fake": FakeSource("fake", [])}, FakeNotifier(), http=None, now=NOW, docs_dir=str(tmp_path / "docs"))
        return capsys.readouterr().err

    assert "[household]" not in run(cfg)
    meta = json.loads((tmp_path / "data" / "meta.json").read_text())
    assert meta["household_private"] is True
    assert "students" not in json.dumps(meta) and "19" not in json.dumps(meta.get("household_private"))
    neutral = _cfg(household={"persons": 2})
    assert "[household] earlier runs judged with a private household and this run has none" in run(neutral)
    assert "[household]" in run(neutral)                                 # every such run says so
    assert "[household]" not in run(cfg)
    assert hunter.config.load_config().get("household_private") is None  # config.json alone is no private household


# ----------------------------------------------------------------------------- cards that were saved earlier
def test_a_saved_card_whose_page_was_never_read_is_not_halved(tmp_path):
    # A card saved by an older version: a rent of € 1.580 shown as € 790 each, from the search page alone. Until
    # the listing's own page is read by today's rules, the rent stands as listed.
    from hunter.runner import detail_snapshot, rejudge_cards

    class Agency(FakeSource):
        rent_basis_from_page = True

    cfg = _cfg()
    state = State(str(tmp_path / "data"), cfg)

    def saved(sid, **kw):
        l = make_listing(sid, source="agency", price_total=1580, **kw)
        state.matches[l.uid] = {
            "listing": l.to_dict(), "first_seen": "2026-11-10T10:00:00Z", "last_seen": "2026-11-10T10:00:00Z",
            "flags": [], "evidence": {}, "best_tier": "ok",
            "matches": [{"search": "2 people", "persons": 2, "price_pp": 790, "extra_pp": 0, "tier": "ok"}]}
        state.seen[l.uid] = {"source": "agency", "first_seen": "x", "last_seen": "x", "matched": ["2 people"]}
        return l

    never, old, read = saved("1"), saved("2"), saved("3")
    state.seen[old.uid]["details"] = dict(detail_snapshot(old, 1580, [], {}), v=DETAIL_VERSION - 1)
    state.seen[read.uid]["details"] = detail_snapshot(read, 1580, [], {})
    sources = {"agency": Agency("agency", [])}
    rejudge_cards(state, cfg, sources=sources)
    for l in (never, old):
        card = state.matches[l.uid]
        assert card["matches"][0]["price_pp"] is None and "rent_unclear" in card["flags"], l.uid
    assert state.matches[read.uid]["matches"][0]["price_pp"] == 790 and "rent_unclear" not in state.matches[read.uid]["flags"]
    # a source whose search page does say what the rent buys is left alone
    state.matches[never.uid]["matches"][0]["price_pp"] = 790
    state.matches[never.uid]["flags"] = []
    rejudge_cards(state, cfg, sources={"agency": FakeSource("agency", [])})
    assert state.matches[never.uid]["matches"][0]["price_pp"] == 790
    from hunter.sources.vanderlinden import VanDerLinden
    assert VanDerLinden.rent_basis_from_page is True and FakeSource.rent_basis_from_page is False


def test_saved_mailbox_cards_are_judged_again_from_their_own_flags(tmp_path):
    # A card from an alert email keeps no text (it came from the user's mailbox). What the text said is in the
    # card's flags, so today's rules still reach it: who is looking, and what each person pays.
    from hunter.runner import rejudge_cards
    cfg = _cfg()
    state = State(str(tmp_path / "data"), cfg)

    def saved(sid, flags, **kw):
        l = make_listing(sid, source="email_alerts", extra={"site_prefiltered": True, "private_text": True}, **kw)
        d = l.to_dict()
        d["description"] = None
        state.matches[l.uid] = {
            "listing": d, "first_seen": "2026-11-10T10:00:00Z", "last_seen": "2026-11-10T10:00:00Z", "flags": flags,
            "evidence": {}, "best_tier": "great",
            "matches": [{"search": "2 people", "persons": 2, "price_pp": 700, "tier": "great"},
                        {"search": "3 people", "persons": 3, "price_pp": 467, "tier": "great"}]}
        return l.uid

    plain = saved("1", ["bedrooms_unconfirmed"], price_total=1400, bedrooms=None, rooms=3)
    working = saved("2", ["working_only", "income_requirement"], price_total=1400)
    refused = saved("3", ["no_sharers"], price_total=1300)
    per_person = saved("4", ["price_per_person"], price_total=700)
    too_old = saved("5", ["min_age:30"], price_total=1400)
    assert rejudge_cards(state, cfg) == 5
    assert sorted(state.matches) == sorted([plain, per_person])
    assert state.matches[plain]["matches"] == [
        {"search": "2 people", "persons": 2, "price_pp": 700, "extra_pp": 0, "tier": "great"}]   # no search for three any more
    assert state.matches[plain]["flags"] == ["bedrooms_unconfirmed"]
    assert state.matches[per_person]["matches"][0]["price_pp"] == 700                            # each pays it: not 350
    assert state.matches[per_person]["flags"] == ["price_per_person"]
    assert all(c["evidence"] == {} and c["listing"]["description"] is None for c in state.matches.values())
    assert rejudge_cards(state, cfg) == 0                                                        # settled
    # for nobody in particular the same cards all stay
    neutral = neutral_config()
    for sid, flags in (("6", ["working_only"]), ("7", ["no_sharers"]), ("8", ["min_age:30"])):
        saved(sid, flags, price_total=1400)
    rejudge_cards(state, neutral)
    assert {"email_alerts:6", "email_alerts:7", "email_alerts:8"} <= set(state.matches)
