"""Flags about who may rent, the sentence behind each flag, and what they do to matching and saving."""
from __future__ import annotations

from datetime import datetime, timezone

from hunter.dashboard import build_data
from hunter.filters import detect, evaluate
from hunter.notify import format as fmt
from hunter.runner import DETAIL_VERSION, _needs_detail_check, detail_snapshot, rejudge_cards, run_once
from hunter.state import State
from hunter.utils import rooms_from_dutch_text
from tests.conftest import FakeNotifier, FakeSource, make_listing

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def names(r):
    return [m.search for m in r.matches]


# ----------------------------------------------------------------------------- flags and their sentences
def test_every_text_flag_carries_its_sentence(cfg):
    l = make_listing(description="Licht appartement. Geen woningdelers. Inschrijving niet mogelijk. "
                                 "Voor deze woning geldt een minimale leeftijd van 55 jaar of ouder.")
    flags, quotes = detect(l, cfg)
    assert flags[:2] == ["no_sharers", "no_registration"] and "min_age:55" in flags
    assert quotes["no_sharers"] == "Geen woningdelers"
    assert quotes["no_registration"] == "Inschrijving niet mogelijk"
    assert quotes["min_age"] == "Voor deze woning geldt een minimale leeftijd van 55 jaar of ouder"


def test_students_welcome_is_no_longer_sharers_welcome(cfg):
    flags, _ = detect(make_listing(description="Studenten welkom!"), cfg)
    assert "students_ok" in flags and "sharers_ok" not in flags


def test_max_persons_reads_more_nouns_and_quotes_its_sentence(cfg):
    flags, quotes = detect(make_listing(description="Een ideale woning voor een gezin, stel of maximaal 2 woningdelers."), cfg)
    assert "max_persons:2" in flags and "sharers_ok" in flags
    assert "maximaal 2 woningdelers" in quotes["max_persons"]
    flags, _ = detect(make_listing(description="Voorwaarden: - Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN)"), cfg)
    assert "max_persons:2" in flags and "working_only" in flags and "no_students" in flags   # one person or a couple
    assert "max_persons:2" in detect(make_listing(description="Suitable for up to 2 people."), cfg)[0]


def test_site_fields_set_flags_with_the_sites_own_labels_as_proof(cfg):
    l = make_listing(extra={"site_students_only": True, "site_min_age": 23, "site_max_age": 30, "site_women_only": True,
                            "max_persons": 2, "site_facts": "Bezigheid: Student · Leeftijd: 23 - 30 jaar · Geslacht: Vrouw"})
    flags, quotes = detect(l, cfg)
    assert flags == ["students_only", "women_only", "min_age:23", "max_age:30", "max_persons:2"]
    assert quotes["students_only"] == quotes["min_age"] == quotes["max_persons"] == l.extra["site_facts"]


def test_site_field_beats_text_for_age_and_contradictions_are_dropped(cfg):
    l = make_listing(description="Minimum age 21.", extra={"site_min_age": 25})
    assert "min_age:25" in detect(l, cfg)[0]
    l = make_listing(description="Students only.", extra={"site_no_students": True})
    flags, quotes = detect(l, cfg)
    assert "students_only" not in flags and "no_students" not in flags and not quotes   # they cannot both be true
    l = make_listing(extra={"site_min_age": 40, "site_max_age": 30})
    assert detect(l, cfg)[0] == []


def test_mailbox_text_decides_flags_but_is_never_quoted(cfg):
    l = make_listing(description="Geen studenten. Groet, Voornaam Achternaam", extra={"private_text": True})
    flags, quotes = detect(l, cfg)
    assert "no_students" in flags and quotes == {}


def test_short_stay_in_words(cfg):
    flags, quotes = detect(make_listing(description="Let op: alleen short stay, maximaal 6 maanden."), cfg)
    assert "short_term" in flags and "short stay" in quotes["short_term"]
    assert "short_term" not in detect(make_listing(description="Maximale huurperiode van 24 maanden."), cfg)[0]
    assert "short_term" in detect(make_listing(description="Maximale huurperiode van 6 maanden."), cfg)[0]
    for text in ("Te huur voor maximaal 6 maanden.", "Available for a maximum of 6 months.", "Huurtermijn: max. 9 maanden."):
        assert "short_term" in detect(make_listing(description=text), cfg)[0], text
    # seen live on Heimstaden: a deposit of at most two months is not a short stay
    for text in ("Bij het huren van deze woning wordt standaard minimaal 1 en maximaal 2 maanden waarborgsom gevraagd.",
                 "De borg is maximaal 2 maanden huur.", "Opzegtermijn van maximaal 1 maanden.",
                 "Te huur voor minimaal 12 maanden."):
        assert "short_term" not in detect(make_listing(description=text), cfg)[0], text


def test_one_person_wording_in_a_list_of_who_the_home_suits_is_no_limit(cfg):
    # seen live on MVGM: a two-bedroom home was dropped as "one person only"
    text = ("De praktische indeling zorgt voor voldoende leefruimte en maakt de woning geschikt voor zowel "
            "alleenstaanden, stellen als kleine gezinnen.")
    r = evaluate(make_listing(description=text), cfg)
    assert not any(f.startswith("max_persons") for f in r.flags) and names(r) == ["2 people"]
    for text in ("Ideaal voor alleenstaanden, stellen en gezinnen.", "Suitable for a single person, a couple or a family."):
        assert not any(f.startswith("max_persons") for f in detect(make_listing(description=text), cfg)[0]), text
    for text in ("Geschikt voor alleenstaanden en stellen.", "Voor een alleenstaande, of een stel zonder kinderen."):
        assert "max_persons:2" in detect(make_listing(description=text), cfg)[0], text
    for text in ("Geschikt voor alleenstaanden.", "Alleen voor een alleenstaande, niet geschikt voor gezinnen.",
                 "Single occupancy only. Not suitable for families or couples.",
                 "Single occupancy in this single-family home."):
        assert "max_persons:1" in detect(make_listing(description=text), cfg)[0], text


def test_a_storage_room_or_parking_place_is_not_a_home(cfg):
    # seen live on Rotsvast: no type on the card, 48 m2 for 500 euro, shown as a great match for two
    store = make_listing(property_type=None, bedrooms=None, rooms=None, area_m2=48, price_total=500, title="Bovenstraat, Rotterdam",
                         description="Opslagruimte van circa 48m2 te huur met electra en water aansluiting.")
    assert not evaluate(store, cfg).matched and store.property_type == "other"
    for text in ("Parkeerplaats te huur in de garage onder het appartementencomplex.", "Te huur: ruime garagebox.",
                 "Kantoorruimte op de eerste verdieping."):
        l = make_listing(property_type=None, bedrooms=None, rooms=None, area_m2=60, title="Bovenstraat 1", description=text)
        assert not evaluate(l, cfg).matched, text
    for text in ("Ruim appartement met eigen parkeerplaats en opslagruimte in de kelder.",
                 "Woning met garagebox. Parkeerplaats te huur voor 100 euro."):
        l = make_listing(property_type=None, description=text)
        assert evaluate(l, cfg).matched and l.property_type is None, text
    typed = make_listing(property_type="apartment", description="Parkeerplaats inbegrepen.")   # the site's type stands
    assert evaluate(typed, cfg).matched


# ----------------------------------------------------------------------------- rent per person
def test_rent_stated_per_person_is_paid_once_for_each_person(cfg):
    text = ("De woning is geschikt voor een stel of woningdelers. De prijs die vermeld staat is per persoon, "
            "oftewel de totale huurprijs bedraagt €2.650,- voor twee personen.")
    r = evaluate(make_listing(price_total=1325, bedrooms=3, description=text), cfg)
    assert "price_per_person" in r.flags and not r.matched           # 2 x 1325 is over the 2-person budget
    assert r.evidence["price_per_person"].startswith("De prijs die vermeld staat is per persoon")
    r = evaluate(make_listing(price_total=750, bedrooms=3, description="Rent is €750 per person per month."), cfg)
    assert names(r) == ["2 people", "3 people"]
    assert [(m.persons, m.price_pp, m.tier) for m in r.matches] == [(2, 750, "ok"), (3, 750, "ok")]   # 1500 and 2250 in total
    assert "suspicious_price" not in r.flags


def test_only_the_listed_amount_or_a_plain_statement_makes_the_rent_per_person(cfg):
    for text in ("Servicekosten €50,- per persoon per maand.", "Borg €1.000 per persoon.",
                 "De huurprijs is exclusief servicekosten van €50 per persoon.",
                 "De huur is €1.400 per maand, dus €700 per persoon."):
        price = 1400 if "1.400" in text else 1000
        assert "price_per_person" not in evaluate(make_listing(price_total=price, description=text), cfg).flags, text
    for text, price in (("De huurprijs bedraagt €978,- per maand per persoon, exclusief €195,- servicekosten.", 978),
                        ("€ 700 p.p.p.m. excl.", 700), ("700 euro per person", 700), ("Price is per person.", 700)):
        assert "price_per_person" in evaluate(make_listing(price_total=price, description=text), cfg).flags, text
    assert "price_per_person" in evaluate(make_listing(extra={"price_per_person": True}), cfg).flags


def test_a_denial_or_another_cost_per_person_does_not_make_the_rent_per_person(cfg):
    for text in ("De huurprijs is voor de hele woning en niet per persoon.",
                 "Rent is for the whole apartment, not per person.",
                 "De huur is per maand, de borg is per persoon.",
                 "De huurprijs is exclusief g/w/e, dit wordt per persoon verrekend.",
                 "The price is for the whole apartment (not per person)."):
        r = evaluate(make_listing(description=text), cfg)
        assert "price_per_person" not in r.flags and names(r) == ["2 people"], text
    for text in ("De prijs die vermeld staat is per persoon.", "The rent is per person.", "Huurprijs is per kamer."):
        assert "price_per_person" in evaluate(make_listing(description=text), cfg).flags, text


def test_a_parking_place_that_is_not_compulsory_costs_nothing(cfg):
    for text in ("Er is geen verplichte huur van een parkeerplaats (optioneel € 125 p/m).",
                 "Geen verplichte afname van een parkeerplaats; deze kost € 125 per maand.",
                 "Een parkeerplaats is niet verplicht en kost € 125 per maand."):
        r = evaluate(make_listing(price_total=1500, description=text), cfg)
        assert not any(f.startswith("mandatory_costs") for f in r.flags) and names(r) == ["2 people"], text


def test_a_home_whose_text_opens_with_its_parking_place_is_still_a_home(cfg):
    for text in ("Parkeerplaats voor de deur en een ruime tuin. De woning heeft 3 slaapkamers.",
                 "Opslagruimte in de kelder en een balkon op het zuiden. Moderne keuken."):
        l = make_listing(property_type=None, title="Ruime bovenwoning", description=text)
        assert evaluate(l, cfg).matched and l.property_type is None, text
    assert not evaluate(make_listing(property_type=None, bedrooms=None, rooms=None, area_m2=20, title="Parkeerplaats Bovenstraat"), cfg).matched
    # the bedrooms a text states make it a home, whatever is for rent beside it
    l = make_listing(property_type=None, bedrooms=None, rooms=None, title="Bovenstraat 1",
                     description="Parkeerplaats in de garage te huur naast dit ruime appartement met 3 slaapkamers.")
    assert evaluate(l, cfg).matched and l.property_type is None and l.bedrooms == 3


def test_not_only_short_stay_is_not_short_stay(cfg):
    assert "short_term" not in detect(make_listing(description="Niet alleen voor short stay, ook lange termijn mogelijk."), cfg)[0]


# ----------------------------------------------------------------------------- compulsory costs
def test_compulsory_parking_counts_towards_the_budget(cfg):
    l = make_listing(price_total=2240, bedrooms=3, extra={"mandatory_costs": 215,
                                                          "mandatory_costs_note": "Verplichte afname parkeerplaats € 215"})
    r = evaluate(l, cfg)
    assert "mandatory_costs:215" in r.flags and not r.matched         # 2455 is over the 3-person limit of 2400
    assert r.evidence["mandatory_costs"] == "Verplichte afname parkeerplaats € 215"
    r = evaluate(make_listing(price_total=1300, bedrooms=2,
                              description="Verplichte afname van een parkeerplaats voor € 150 per maand."), cfg)
    assert "mandatory_costs:150" in r.flags
    # each pays 650 rent and 75 for the parking place: 725 a month, which is what the budget is held against
    assert [(m.persons, m.price_pp, m.extra_pp) for m in r.matches] == [(2, 650, 75)]


# ----------------------------------------------------------------------------- rooms and bedrooms
def test_dutch_room_count_from_text():
    assert rooms_from_dutch_text("Dit tweekamerappartement ligt centraal") == 2
    assert rooms_from_dutch_text("Ruim 3-kamer appartement van 83 m²") == 3
    assert rooms_from_dutch_text("Mooi 4 kamer appartement") == 4
    assert rooms_from_dutch_text("3-kamer huurappartement") == 3
    assert rooms_from_dutch_text("2-slaapkamer appartement") is None       # bedrooms, another question
    assert rooms_from_dutch_text("two-room apartment") is None             # English is used both ways
    assert rooms_from_dutch_text("twee- en driekamerappartementen te huur: een 2-kamerappartement of 3-kamerappartement") is None


def test_site_bedrooms_that_contradict_the_dutch_room_count_are_not_trusted(cfg):
    # The site's field says 2 bedrooms; the text calls it a two-room flat, which has one bedroom.
    l = make_listing(price_total=1400, bedrooms=2, rooms=None, description="Dit tweekamerappartement ligt op de 2e etage.")
    r = evaluate(l, cfg)
    assert l.rooms == 2 and not r.matched
    l = make_listing(price_total=1400, bedrooms=2, rooms=None, description="Dit driekamerappartement ligt op de 2e etage.")
    r = evaluate(l, cfg)
    assert l.rooms == 3 and names(r) == ["2 people"] and "bedrooms_unconfirmed" not in r.flags
    l = make_listing(price_total=1400, bedrooms=3, rooms=None, description="Dit driekamerappartement heeft 3 slaapkamers.")
    r = evaluate(l, cfg)                                                 # at most 2 bedrooms: still fits two people
    assert names(r) == ["2 people"] and "bedrooms_unconfirmed" in r.flags


# ----------------------------------------------------------------------------- the household in config.json
def test_household_profile_drops_homes_that_exclude_it_and_nothing_else(cfg):
    student_home = make_listing(description="Alleen voor studenten.")
    worker_home = make_listing(description="Geen studenten.")
    senior_home = make_listing(description="Minimale leeftijd 55 jaar.")
    young_home = make_listing(description="Voor jongeren tot 27 jaar.")
    silent = make_listing()
    assert all(evaluate(l, cfg).matched for l in (student_home, worker_home, senior_home, young_home, silent))  # no profile
    cfg["household"] = {"age": 24, "occupation": "students"}
    assert [evaluate(l, cfg).matched for l in (student_home, worker_home, senior_home, young_home, silent)] == \
        [True, False, False, True, True]
    assert "no_students" in evaluate(worker_home, cfg).flags            # the flag is still reported
    cfg["household"] = {"age": 30, "occupation": "working"}
    assert [evaluate(l, cfg).matched for l in (student_home, worker_home, senior_home, young_home, silent)] == \
        [False, True, False, False, True]
    cfg["household"] = {"age": "24", "occupation": "retired"}           # nonsense values exclude nothing
    assert all(evaluate(l, cfg).matched for l in (student_home, worker_home, senior_home, young_home, silent))


# ----------------------------------------------------------------------------- messages
def test_alert_shows_good_signs_requirements_and_two_quotes(cfg):
    l = make_listing(description="Studenten welkom. Geen woningdelers. Minimale leeftijd 23 jaar. <b>x</b>")
    r = evaluate(l, cfg)
    msg = fmt.format_new_match(l, r, cfg, "Fake")
    assert "✅ Students welcome" in msg
    assert "No house sharers" in msg and "Minimum age 23" in msg
    assert msg.count("💬") == 2                                          # the first two sentences behind the flags
    assert "💬 <i>“Geen woningdelers”</i>" in msg and "💬 <i>“Studenten welkom”</i>" in msg
    r = evaluate(make_listing(price_total=750, bedrooms=2, description="Price is per person."), cfg)
    assert ("💶 <b>€750</b> each a month · rent per person or room"
            in fmt.format_new_match(make_listing(price_total=750), r, cfg, "Fake"))


def test_two_cuts_of_one_long_sentence_are_quoted_once(cfg):
    from hunter.filters import MatchResult
    one = "…Huurprijs voor 2 personen € 1995,- Ook beschikbaar voor een woongroep/woningdelers van max 3 personen."
    longer = "…kan gebruikt worden in de woonkamer) " + one[1:]
    r = MatchResult(flags=["sharers_ok", "max_persons:3", "no_students"],
                    evidence={"sharers_ok": one, "max_persons": longer, "no_students": "Geen studenten"})
    assert fmt.quotes(r) == [longer, "Geen studenten"]


# ----------------------------------------------------------------------------- saving
def _run(cfg, tmp_path, src, n=None, now=NOW):
    st = State(str(tmp_path), cfg)
    n = n or FakeNotifier()
    run_once(cfg, st, {src.name: src}, n, http=None, now=now, docs_dir=str(tmp_path))
    return st, n


def test_flags_from_the_end_of_a_long_text_survive_with_their_sentence(cfg, tmp_path):
    long_text = ("Ruime woning. " * 400) + "Voorwaarden: deze woning is alleen voor studenten."
    assert len(long_text) > 5000
    src = FakeSource("fake", [make_listing("1")])
    src.description = long_text
    _run(cfg, tmp_path, FakeSource("fake", []))                          # the source's first run is silent otherwise
    st = State(str(tmp_path), cfg)
    st.seen["fake:0"] = {"source": "fake", "first_seen": "x", "last_seen": "x", "matched": []}
    st.save(NOW)
    st, n = _run(cfg, tmp_path, src)
    card = st.matches["fake:1"]
    assert "students_only" in card["flags"]
    assert card["evidence"]["students_only"] == "Voorwaarden: deze woning is alleen voor studenten"
    assert len(card["listing"]["description"]) == 4000                   # the saved text is cut, the flag is not lost
    snap = st.seen["fake:1"]["details"]
    assert snap["v"] == DETAIL_VERSION and "students_only" in snap["flags"]
    assert snap["evidence"]["students_only"].endswith("alleen voor studenten") and len(snap["description"]) == 2500
    # The next run only sees the search page again: the flag and its sentence come back from the snapshot.
    src2 = FakeSource("fake", [make_listing("1")])
    st, _ = _run(cfg, tmp_path, src2)
    assert src2.detail_calls == 0
    assert "students_only" in st.matches["fake:1"]["flags"]
    assert st.matches["fake:1"]["evidence"]["students_only"].endswith("alleen voor studenten")
    data = build_data(cfg, st, NOW)
    item = [x for x in data["listings"] if x["uid"] == "fake:1"][0]
    assert item["evidence"]["students_only"].endswith("alleen voor studenten")
    assert data["flag_labels"]["students_only"] == "Students only"


def test_a_page_read_by_an_older_version_is_read_once_more(cfg, tmp_path):
    src = FakeSource("fake", [make_listing("1")])
    st, _ = _run(cfg, tmp_path, src)
    st.seen["fake:1"]["details"] = {"bedrooms": 2, "flags": [], "search_price": 1400, "description": "Oud."}   # no "v"
    st.seen["fake:1"]["detail_tries"] = 3                                # tries of the older version do not count
    st.save(NOW)
    src2 = FakeSource("fake", [make_listing("1")])
    src2.description = "Alleen voor studenten."
    st, n = _run(cfg, tmp_path, src2)
    assert src2.detail_calls == 1 and not n.messages                     # a re-read never alerts
    assert st.seen["fake:1"]["details"]["v"] == DETAIL_VERSION
    assert "students_only" in st.matches["fake:1"]["flags"]
    src3 = FakeSource("fake", [make_listing("1")])
    st, _ = _run(cfg, tmp_path, src3)
    assert src3.detail_calls == 0                                        # once is enough


def test_mailbox_sentences_are_not_saved_or_sent(cfg, tmp_path):
    l = make_listing("1", source="mail", description="Geen studenten. Met groet, Voornaam", extra={"private_text": True})
    src = FakeSource("mail", [l])
    st, n = _run(cfg, tmp_path, src)
    card = st.matches["mail:1"]
    assert "no_students" in card["flags"] and card["evidence"] == {}
    assert not any("Voornaam" in m for m in n.messages)
    snap = detail_snapshot(l, 1400, ["no_students"], {"no_students": "Geen studenten. Met groet, Voornaam"})
    assert "evidence" not in snap and "description" not in snap


def test_a_condition_from_the_end_of_a_long_text_keeps_counting_on_later_runs(cfg, tmp_path):
    long_text = ("Ruime woning. " * 400) + "Let op: geen woningdelers."
    _run(cfg, tmp_path, FakeSource("fake", [make_listing("0")]))                    # the source's first run
    src = FakeSource("fake", [make_listing("0"), make_listing("1", price_total=1500, bedrooms=3)])
    src.description = long_text
    st, n = _run(cfg, tmp_path, src)
    assert [m["persons"] for m in st.matches["fake:1"]["matches"]] == [2]           # not for three sharers
    # Later runs see the search page and the cut text only. The three-person match must not come back.
    for _ in range(2):
        st, n2 = _run(cfg, tmp_path, FakeSource("fake", [make_listing("0"), make_listing("1", price_total=1500, bedrooms=3)]))
        card = st.matches["fake:1"]
        assert [m["persons"] for m in card["matches"]] == [2] and "no_sharers" in card["flags"]
        assert card["evidence"]["no_sharers"] == "Let op: geen woningdelers"
        assert st.seen["fake:1"]["matched"] == ["2 people"] and not n2.messages


def test_saved_cards_are_judged_again_with_todays_rules_without_a_request(cfg, tmp_path):
    src = FakeSource("fake", [make_listing("1"), make_listing("2"), make_listing("3", price_total=1500, bedrooms=3)])
    mail = FakeSource("mail", [make_listing("9", source="mail", description="Geen woningdelers. Groet, Voornaam",
                                            price_total=1500, bedrooms=3, extra={"private_text": True})])
    st = State(str(tmp_path), cfg)
    run_once(cfg, st, {"fake": src, "mail": mail}, FakeNotifier(), http=None, now=NOW, docs_dir=str(tmp_path))
    assert set(st.matches) == {"fake:1", "fake:2", "fake:3", "mail:9"}
    # What an older version of the matcher saved: a rent that is per person shown as the total, a storage room
    # shown as a home, and a card without the flag its text has.
    st.matches["fake:1"]["listing"]["description"] = "De prijs die vermeld staat is per persoon."
    st.matches["fake:1"]["listing"]["price_total"] = 1325
    st.matches["fake:2"]["listing"].update(property_type=None, bedrooms=None, rooms=None, area_m2=48, price_total=500,
                                            title="Bovenstraat, Rotterdam", description="Opslagruimte van circa 48m2 te huur.")
    st.matches["fake:3"]["listing"]["description"] = "Alleen voor studenten. Geen woningdelers."
    last_seen = st.matches["fake:3"]["last_seen"]
    st.save(NOW)
    # The next run finds none of them on a search page: the sites show other homes now.
    st = State(str(tmp_path), cfg)
    n = FakeNotifier()
    later = NOW.replace(hour=13)
    run_once(cfg, st, {"fake": FakeSource("fake", [make_listing("7")]), "mail": FakeSource("mail", [])}, n,
             http=None, now=later, docs_dir=str(tmp_path))
    assert "fake:1" not in st.matches and st.seen["fake:1"]["matched"] == []        # 2 x 1325 is over budget
    assert "fake:2" not in st.matches
    card = st.matches["fake:3"]
    assert [m["persons"] for m in card["matches"]] == [2] and st.seen["fake:3"]["matched"] == ["2 people"]
    assert {"students_only", "no_sharers"} <= set(card["flags"])
    assert card["evidence"]["students_only"] == "Alleen voor studenten"
    assert card["last_seen"] == last_seen                                           # it still expires on time
    mailcard = st.matches["mail:9"]                                                 # its text was never saved:
    assert [m["persons"] for m in mailcard["matches"]] == [2] and mailcard["evidence"] == {}   # the decision stands
    assert not any("Nice flat 3" in m or "Nice flat 1" in m for m in n.messages)    # a correction is not news
    assert rejudge_cards(st, cfg) == 0                                              # nothing left to correct


def test_flags_remembered_by_an_older_version_do_not_count(cfg, tmp_path):
    # An older version read "alleenstaanden, stellen en gezinnen" as "one person only" and saved that with the page.
    src = FakeSource("fake", [make_listing("1")])
    st, _ = _run(cfg, tmp_path, src)
    st.seen["fake:1"]["details"] = {"bedrooms": 2, "flags": ["max_persons:1"], "search_price": 1400}     # no "v"
    st.save(NOW)
    quiet = FakeSource("fake", [make_listing("1")])
    quiet.fetch_details = lambda listing, http: (_ for _ in ()).throw(RuntimeError("site down"))         # no re-read
    st, _ = _run(cfg, tmp_path, quiet)
    card = st.matches["fake:1"]
    assert [m["persons"] for m in card["matches"]] == [2] and "max_persons:1" not in card["flags"]
    st.seen["fake:1"]["details"].update(v=DETAIL_VERSION)                # the same flag from today's reader counts
    st.save(NOW)
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1")]))
    assert "fake:1" not in st.matches


def test_a_card_todays_rules_cannot_read_stays_and_does_not_stop_the_run(cfg, tmp_path, capsys):
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1"), make_listing("2")]))
    st.matches["fake:1"]["listing"]["price_total"] = "veel"              # saved in a shape nobody expects
    st.matches["fake:2"]["listing"] = "kapot"
    st.seen["fake:1"] = "ook kapot"
    assert rejudge_cards(st, cfg) == 0
    assert set(st.matches) == {"fake:1", "fake:2"}
    assert "[rejudge] fake:1" in capsys.readouterr().err


def test_a_source_that_reads_its_price_another_way_announces_no_price_drop(cfg, tmp_path):
    first = FakeSource("fake", [make_listing("0"), make_listing("1", price_total=1590)])
    _run(cfg, tmp_path, first)
    # The source now takes the page's headline price: 1590 became 1300 without the rent changing.
    changed = make_listing("1", price_total=1300, extra={"price_basis": "from"})
    st, n = _run(cfg, tmp_path, FakeSource("fake", [make_listing("0"), changed]))
    assert not n.messages and st.matches["fake:1"]["price_drop_from"] is None
    assert st.seen["fake:1"]["price_basis"] == "from" and st.seen["fake:1"]["last_price"] == 1300
    # From then on a lower price is a drop again.
    lower = make_listing("1", price_total=1100, extra={"price_basis": "from"})
    st, n = _run(cfg, tmp_path, FakeSource("fake", [make_listing("0"), lower]))
    assert len(n.messages) == 1 and st.matches["fake:1"]["price_drop_from"] == 1300


def test_a_rent_that_depends_on_the_month_says_so(cfg):
    r = evaluate(make_listing(price_total=1400, extra={"price_min": 1400, "price_max": 1900}), cfg)
    assert "price_varies:1900" in r.flags and names(r) == ["2 people"]
    assert "Rent depends on the month, up to €1900" in fmt.format_new_match(make_listing(), r, cfg, "Fake")
    assert not any(f.startswith("price_varies") for f in evaluate(make_listing(extra={"price_max": 1400}), cfg).flags)


def test_a_site_value_the_reader_no_longer_sets_does_not_survive_the_new_read(cfg, tmp_path):
    # An older reader took Kamernet's id 8 for "8 persons" and saved it with the page.
    src = FakeSource("fake", [make_listing("1")])
    st, _ = _run(cfg, tmp_path, src)
    st.seen["fake:1"]["details"] = {"bedrooms": 2, "flags": [], "search_price": 1400, "extra": {"max_persons": 8, "deposit": 900}}
    st.save(NOW)
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1")]))        # today's reader sets no head count
    assert "max_persons" not in st.seen["fake:1"]["details"].get("extra", {})
    assert not any(f.startswith("max_persons") for f in st.matches["fake:1"]["flags"])
    # A page that cannot be read keeps what was known.
    st.seen["fake:1"]["details"] = {"bedrooms": 2, "flags": [], "search_price": 1400, "extra": {"max_persons": 2}}
    st.save(NOW)
    down = FakeSource("fake", [make_listing("1")])
    down.fetch_details = lambda listing, http: (_ for _ in ()).throw(RuntimeError("site down"))
    st, _ = _run(cfg, tmp_path, down)
    assert st.seen["fake:1"]["details"]["extra"]["max_persons"] == 2 and "max_persons:2" in st.matches["fake:1"]["flags"]


def test_what_the_search_page_says_today_beats_the_saved_page_and_a_new_listing_keeps_its_price_basis(cfg, tmp_path):
    first = make_listing("1", price_total=1300, extra={"mandatory_costs": 100, "mandatory_costs_note": "badge: parkeerplaats €100",
                                                         "price_basis": "from"})
    src = FakeSource("fake", [make_listing("0"), first])
    _run(cfg, tmp_path, FakeSource("fake", [make_listing("0")]))
    st, _ = _run(cfg, tmp_path, src)
    assert st.seen["fake:1"]["price_basis"] == "from" and "mandatory_costs:100" in st.matches["fake:1"]["flags"]
    # The badge on the search page changes; the saved detail page still says 100.
    now = make_listing("1", price_total=1300, extra={"mandatory_costs": 180, "mandatory_costs_note": "badge: parkeerplaats €180",
                                                      "price_basis": "from"})
    st, n = _run(cfg, tmp_path, FakeSource("fake", [make_listing("0"), now]))
    assert "mandatory_costs:180" in st.matches["fake:1"]["flags"] and not n.messages
    # A real drop right after the first sighting is announced (the basis was saved with the new listing).
    lower = make_listing("1", price_total=1100, extra={"mandatory_costs": 180, "price_basis": "from"})
    st, n = _run(cfg, tmp_path, FakeSource("fake", [make_listing("0"), lower]))
    assert len(n.messages) == 1 and st.matches["fake:1"]["price_drop_from"] == 1300


# ----------------------------------------------------------------------------- found by the live audit of 19 Nov 2026
def test_not_only_for_couples_limits_nobody(cfg):
    for text in ("Deze woning is niet alleen voor stellen, ook gezinnen zijn welkom.",
                 "Not only for couples: families are welcome too.", "This is not a couple only apartment."):
        flags, _ = detect(make_listing(description=text), cfg)
        assert not [f for f in flags if f.startswith("max_persons")], text
    for text in ("Alleen voor een stel.", "Deze woning is uitsluitend voor een stel", "Couple only."):
        flags, quotes = detect(make_listing(description=text), cfg)
        assert "max_persons:2" in flags and quotes["max_persons"], text


def test_far_too_little_rent_for_the_size_is_warned_about_not_dropped(cfg):
    # An alert email: "€ 750 per maand 200 m² · 6 kamers", no type named. Almost certainly one room of that home.
    def mail(price, area, rooms):
        return evaluate(make_listing(source="email_alerts", title="Pararius: Straat", price_total=price, area_m2=area,
                                     rooms=rooms, bedrooms=None, property_type=None,
                                     extra={"site_prefiltered": True, "private_text": True}), cfg)
    r = mail(750, 200, 6)
    assert r.matched and "suspicious_price" in r.flags
    for price, area, rooms in ((1166, 101, 4), (880, 38, 3), (1023, 84, 3), (1500, None, None)):   # real cards of that day
        assert "suspicious_price" not in mail(price, area, rooms).flags
    # a rent the listing itself states per person is cheap for a reason
    pp = evaluate(make_listing(price_total=500, area_m2=120, bedrooms=3, rooms=4,
                               description="De huurprijs is per persoon."), cfg)
    assert "price_per_person" in pp.flags and "suspicious_price" not in pp.flags
    off = dict(cfg, suspicious_rent_per_m2_below=None)
    assert "suspicious_price" not in evaluate(make_listing(price_total=750, area_m2=200, rooms=6, bedrooms=None), off).flags


# ----------------------------------------------------------------------------- the page-by-page audit of 19 Nov 2026
# 120 saved listing pages were read by independent readers and compared with the checker. Each case below is a
# sentence or field from one of those pages that the checker read wrongly or missed.
def _flags(cfg, text, **kw):
    return detect(make_listing(description=text, **kw), cfg)


def test_audit_sharing_and_housemates_wordings(cfg):
    flags, quotes = _flags(cfg, "- No students or house sharing -")
    assert "no_sharers" in flags and "no_students" in flags
    for text in ("Naast de kamers heeft het pand 2 toiletten, 1 badkamer en 1 keuken, die gedeeld worden door 5 kamers.",
                 "Een nette zelfstandige studio voor een student, te delen met een student in de kamer er naast."):
        assert "housemates" in _flags(cfg, text)[0], text
    for text in ("De tuin wordt gedeeld met de andere bewoners.", "Gedeelde fietsenstalling, te delen met de buren."):
        assert "housemates" not in _flags(cfg, text)[0], text


def test_audit_a_list_that_names_sharers_is_no_two_person_limit(cfg):
    flags, quotes = _flags(cfg, "Er zijn twee slaapkamers aanwezig waardoor de woning geschikt is voor een alleenstaande, "
                                "een stel of voor woningdelers!")
    assert "sharers_ok" in flags and not [f for f in flags if f.startswith("max_persons")]
    assert "woningdelers" in quotes["sharers_ok"]
    # without sharers in the list the older reading stays: one or two people
    assert "max_persons:2" in _flags(cfg, "Geschikt voor zowel een alleenstaande als een stel.")[0]
    flags, _ = _flags(cfg, "Niet geschikt voor woningdelers, wel voor een alleenstaande of een stel.")
    assert "no_sharers" in flags and "max_persons:2" in flags


def test_audit_ages_of_the_people_who_live_there_are_no_limit(cfg):
    for text in ("The current roommates are two young professionals, aged 26 and 28, who are both working in The Hague.",
                 "Huisgenoten tussen 22 en 28 jaar oud.", "We are aged 26 and 28."):
        assert not [f for f in _flags(cfg, text)[0] if "age" in f], text
    assert {"min_age:22", "max_age:28"} <= set(_flags(cfg, "Wij zoeken een nieuwe huisgenoot tussen de 22 en 28 jaar.")[0])
    assert {"min_age:25", "max_age:35"} <= set(_flags(cfg, "We are looking for a new roommate aged 25 to 35.")[0])
    assert {"min_age:25", "max_age:40"} <= set(_flags(cfg, "Tenants must be between the ages of 25 and 40.")[0])
    flags = _flags(cfg, "Mijn huisgenoten zijn tussen 24 en 30 jaar en wij zoeken iemand van 23 jaar of ouder.")[0]
    assert "min_age:23" in flags and "max_age:30" not in flags


def test_audit_no_registration_wording(cfg):
    flags, quotes = _flags(cfg, "Let op! Inschrijven bij de gemeente niet mogelijk omdat het een vakantiewoning is!")
    assert "no_registration" in flags and "niet mogelijk" in quotes["no_registration"]
    assert "no_registration" not in _flags(cfg, "Inschrijven bij de gemeente is mogelijk.")[0]


def test_audit_short_stay_wordings_and_dates(cfg):
    short = ("Strictly available for a temporary period of 2 months (January & February 2027)",
             "Available for 1 to 6 months, fully furnished",
             "Beschikbaar vanaf 16 december t/m 28 februari (mogelijk ook maart en april).",
             "Available from 1 January until 31 March 2027.", "Huurperiode: 1 maart - 31 augustus")
    for text in short:
        flags, quotes = _flags(cfg, text)
        assert "short_term" in flags and quotes["short_term"], text
    not_short = ("Te huur van 1 februari 2027 tot 1 februari 2028.", "Beschikbaar per 1 december 2026 tot 30 november 2027",
                 "Bezichtigingen van 1 december tot 5 december.", "Available for 12 to 24 months.",
                 "Beschikbaar vanaf 1 december. Bezichtigingen van 3 december tot 5 december.",
                 "Te huur voor 6 tot 12 maanden", "De woning is beschikbaar vanaf 1 januari 2027.",
                 "Not only available from 1 June to 31 August, longer is possible.")
    for text in not_short:
        assert "short_term" not in _flags(cfg, text)[0], text


def test_audit_a_year_less_a_day_is_a_one_year_contract(cfg):
    # Kamernet: "Beschikbaar vanaf 1 nov 2026 tot 31 oct 2027; Verblijfsduur 1 jaar" was marked short-term.
    for start, end, short in (("2026-11-01", "2027-10-31", False), ("2026-12-01", "2027-11-30", False),
                              ("2026-12-01", "2027-06-30", True), ("2026-12-16", "2027-02-28", True)):
        flags, _ = detect(make_listing(available_from=start, available_until=end), cfg)
        assert ("short_term" in flags) is short, (start, end)


def test_audit_service_costs_on_top_are_shown_and_counted(cfg):
    # MVGM Randveen 808: rent € 1.248,55, "Servicekosten € 85"; the text: "De maandelijkse huurprijs bedraagt € 1.333,55".
    l = make_listing(price_total=1249, bedrooms=2, extra={"service_costs": 85, "service_costs_on_top": True,
                                                          "service_costs_note": "Servicekosten € 85 per maand"})
    r = evaluate(l, cfg)
    assert "service_costs:85" in r.flags and r.evidence["service_costs"] == "Servicekosten € 85 per maand"
    # (1249 + 85) / 2 = 667 each: 625 rent and 42 service costs
    assert [(m.price_pp, m.extra_pp) for m in r.matches if m.persons == 2] == [(625, 42)]
    assert "Plus €85 service costs a month" in fmt.format_new_match(l, r, cfg)
    # the budget test uses the total: € 1.545 + € 65 is over € 1.600 for two
    over = evaluate(make_listing(price_total=1545, bedrooms=2, extra={"service_costs": 65, "service_costs_on_top": True}), cfg)
    assert "2 people" not in names(over)
    # a source that is not sure the amount comes on top only stores it
    unsure = evaluate(make_listing(price_total=1545, bedrooms=2, extra={"service_costs": 65}), cfg)
    assert "2 people" in names(unsure) and not [f for f in unsure.flags if f.startswith("service_costs")]
    # with a compulsory parking place both are added
    both = evaluate(make_listing(price_total=1300, bedrooms=2, extra={"service_costs": 60, "service_costs_on_top": True,
                                                                     "mandatory_costs": 100}), cfg)
    assert [(m.price_pp, m.extra_pp) for m in both.matches if m.persons == 2] == [(650, 80)]


def test_audit_utilities_text_against_the_sites_field(cfg):
    def said(have, text):
        l = make_listing(description=text, price_includes_utilities=have)
        evaluate(l, cfg)
        return l.price_includes_utilities
    assert said(None, "De prijs is inclusief gas, water, elektriciteit en internet.") is True
    assert said(None, "Huurprijs is exclusief g/w/e") is False
    assert said(None, "Niet inclusief gas, water en licht") is False
    # Kamernet said "Incl. vaste lasten"; the text: "incl. the utilities (water, internet). Gas and electricity excl."
    assert said(True, "Price per month: €1.875 incl. the utilities (water, internet, TV). Gas and electricity excl.") is None
    assert said(True, "Huur exclusief gas en elektra.") is None
    assert said(False, "All-in huurprijs") is None
    # an advance that covers them is paid on top
    assert said(None, "- € 175.00 per maand voorschot inclusief gas, water, elektriciteit") is False
    assert said(True, "Mooi appartement") is True and said(None, "Inclusief servicekosten") is None
    assert said(None, "Een all-in-one printer is aanwezig") is None


# ----------------------------------------------------------------------------- cards that left the search pages
def _old_reading(st, uid, extra):
    """What an older version saved for a card: a detail snapshot without a version, and its values on the card."""
    st.seen[uid]["details"] = {"bedrooms": 2, "flags": [], "search_price": 1400, "extra": dict(extra)}     # no "v"
    st.seen[uid].pop("detail_tries", None)
    st.matches[uid]["listing"]["extra"] = dict(st.matches[uid]["listing"].get("extra") or {}, **extra)


def test_a_card_that_left_the_search_pages_is_read_once_more_by_todays_reader(cfg, tmp_path):
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1"), make_listing("2")]))
    _old_reading(st, "fake:1", {"max_persons": 8})          # an id an older version took for a head count
    assert "max_persons:8" not in st.matches["fake:1"]["flags"]
    st.save(NOW)
    gone = FakeSource("fake", [make_listing("2")])          # the search page no longer shows listing 1
    gone.description = "Ruim appartement. Geen woningdelers."
    st, n = _run(cfg, tmp_path, gone, now=NOW.replace(hour=13))
    card = st.matches["fake:1"]
    assert "max_persons" not in card["listing"]["extra"] and not any(f.startswith("max_persons") for f in card["flags"])
    assert "no_sharers" in card["flags"] and card["evidence"]["no_sharers"] == "Geen woningdelers"
    assert st.seen["fake:1"]["details"]["v"] == DETAIL_VERSION and st.seen["fake:1"]["detail_tries"] == 1
    assert not n.messages                                   # a correction is not news
    calls = gone.detail_calls
    st, _ = _run(cfg, tmp_path, gone, now=NOW.replace(hour=14))
    assert gone.detail_calls == calls                       # read once, not on every run


def test_a_lost_card_whose_new_reading_no_longer_fits_goes(cfg, tmp_path):
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1"), make_listing("2")]))
    _old_reading(st, "fake:1", {})
    st.save(NOW)
    gone = FakeSource("fake", [make_listing("2")])
    gone.description = "Deze woning is uitsluitend voor 1 persoon."
    st, n = _run(cfg, tmp_path, gone, now=NOW.replace(hour=13))
    assert "fake:1" not in st.matches and st.seen["fake:1"]["matched"] == [] and not n.messages


def test_a_lost_card_whose_page_cannot_be_read_stays_and_is_tried_three_times(cfg, tmp_path, capsys):
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1"), make_listing("2")]))
    _old_reading(st, "fake:1", {"max_persons": 2})
    st.save(NOW)
    down = FakeSource("fake", [make_listing("2")])
    tries = []

    def fail(listing, http):
        tries.append(listing.uid)
        raise RuntimeError("page is gone")
    down.fetch_details = fail
    for hour in (13, 14, 15, 16, 17):
        st, _ = _run(cfg, tmp_path, down, now=NOW.replace(hour=hour))
    assert tries == ["fake:1"] * 3 and "fake:1" in st.matches
    assert st.matches["fake:1"]["listing"]["extra"]["max_persons"] == 2      # what was known is kept
    assert "[detail] fake:1" in capsys.readouterr().err


def test_lost_cards_stay_within_the_runs_detail_allowance_and_skip_sites_that_failed(cfg, tmp_path):
    cfg = dict(cfg, detail_fetch_max_per_run=1)
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [make_listing("1"), make_listing("2"), make_listing("3")]))
    for uid in ("fake:1", "fake:2"):
        _old_reading(st, uid, {})
    st.save(NOW)
    broken = FakeSource("fake", raises=RuntimeError("site down"))
    st, _ = _run(cfg, tmp_path, broken, now=NOW.replace(hour=13))
    assert broken.detail_calls == 0                          # the site did not answer: its pages are left alone
    quiet = FakeSource("fake", [make_listing("3")])
    st, _ = _run(cfg, tmp_path, quiet, now=NOW.replace(hour=14))
    assert quiet.detail_calls == 1                           # one page per run here
    st, _ = _run(cfg, tmp_path, quiet, now=NOW.replace(hour=15))
    assert quiet.detail_calls == 2
    assert all(st.seen[uid]["details"]["v"] == DETAIL_VERSION for uid in ("fake:1", "fake:2"))


# ----------------------------------------------------------------------------- second review (Gemini, 19 Nov 2026)
import pytest  # noqa: E402


def _said(text, **kw):
    return detect(make_listing(description=text, **kw), cfg_for_tests())[0]


def cfg_for_tests():
    from hunter.config import load_config
    return load_config("config.json")


@pytest.mark.parametrize("text", [
    "De woning is niet uitsluitend voor een stel.",          # a phrase from config.json with "niet" in front
    "Uitsluitend voor een stel of woningdelers.",            # ... followed by a choice
    "Je hoeft geen studenten te zijn om te reageren.",
    "Geen omzettingsvergunning aanwezig.",
    "Alleen voor een stel of woningdelers.",
    "Geschikt voor 2 personen of een gezin.",
    "Niet geschikt voor 1 persoon.",
    "Niet voor een alleenstaande.",
    "Niet alleen voor een stel.",
    "Het pand is tussen 20 en 25 jaar geleden gerenoveerd.",
    "De woning is tussen 20 en 30 jaar oud.",
    "In dit complex verhuren wij ook 55+ woningen.",
    "Geschikt voor starters en senioren (55+ woning).",
    "Leeftijd van het pand is 20 tot 30 jaar.",
    "Je deelt de woning met je partner of een vriend.",
    "You will share the apartment with your partner or friend.",
    "Voor studenten werken wij met een campuscontract.",
    "Indien je student bent krijg je een campuscontract.",
    "Alleen beschikbaar voor werkenden of gepensioneerden.",
    "Must have a permanent job or sufficient savings.",
    "Alleen voor studenten (of starters met garantstelling).",
    "Alleen voor vrouwen of stellen.",
    "De prijs voor de eindschoonmaak is per kamer.",
    "De prijs voor het linnenpakket is per persoon.",
])
def test_sentences_that_restrict_nothing_set_no_flag(text):
    assert _said(text) == [], text


def test_the_same_wordings_still_count_when_they_are_statements():
    assert _said("Uitsluitend voor een stel.") == ["no_sharers", "max_persons:2"]
    assert _said("Geen studenten.") == ["no_students"]
    assert _said("Omzettingsvergunning aanwezig.") == ["sharers_ok"]
    assert _said("Woningdelers toegestaan: nee.") == ["no_sharers"]            # a field answered with no
    assert _said("Geschikt voor 2 personen.") == ["max_persons:2"]
    assert _said("Geschikt voor 2 personen of 3 woningdelers.") == ["sharers_ok", "max_persons:3"]   # the larger group
    assert _said("Alleen voor 1 persoon.") == ["max_persons:1"]
    assert _said("Niet roken en alleen voor 1 persoon.") == ["max_persons:1"]  # the denial is about smoking
    assert _said("Wij zoeken huurders tussen 25 en 35 jaar.") == ["min_age:25", "max_age:35"]
    assert _said("De woning is alleen voor huurders vanaf 23 jaar.") == ["min_age:23"]
    assert _said("Dit is een 55+ woning.") == ["min_age:55"]
    assert _said("Je deelt de woning met twee andere studenten.") == ["housemates", "capacity:3"]
    assert _said("Je deelt de keuken en badkamer met je medehuurder.") == ["housemates"]
    assert _said("Deze woning wordt verhuurd met een campuscontract.") == ["students_only"]
    assert _said("Alleen voor werkenden of expats.") == ["working_only"]
    assert _said("Voorwaarden: max. 1 werkende persoon of stel.") == ["working_only", "max_persons:2", "sharing_unclear"]
    assert _said("Alleen voor vrouwen.") == ["women_only"]
    assert _said("De huurprijs is per persoon.") == ["price_per_person"]
    assert "price_per_person" not in _said("Geen kamerverhuur: niet de huurprijs is per persoon, maar voor de hele woning.")


def test_a_room_for_one_person_in_a_home_with_more_bedrooms_is_no_limit(cfg):
    text = "Twee grote slaapkamers en een derde kamer voor een persoon."
    assert names(evaluate(make_listing(description=text, bedrooms=3, price_total=1500), cfg)) == ["2 people", "3 people"]
    assert "max_persons:1" in detect(make_listing(description="De slaapkamer is geschikt voor één persoon.", bedrooms=1), cfg)[0]


def test_two_lines_of_a_listing_are_never_read_as_one_sentence(cfg):
    # "Huisdieren: geen" and "Studenten welkom" are two lines. Read as one they said "geen studenten".
    flags, quotes = detect(make_listing(description="Huisdieren: geen\nStudenten welkom\nRoken: niet\nWoningdelers toegestaan"), cfg)
    assert flags == ["sharers_ok", "students_ok"]
    assert quotes == {"students_ok": "Studenten welkom", "sharers_ok": "Woningdelers toegestaan"}
    flags, _ = detect(make_listing(description="Geschikt voor\n2 personen. Alleen voor\nstudenten."), cfg)
    assert flags == []                                       # a statement cut by a line break is not guessed at
    flags, quotes = detect(make_listing(description="Kenmerken:\nGeen huisdieren\nGeen woningdelers\nMaximaal 2 personen"), cfg)
    assert flags == ["no_sharers", "max_persons:2"] and quotes["max_persons"] == "Maximaal 2 personen"


def test_a_rent_that_depends_on_the_group_is_used_for_that_group(cfg):
    text = ("Huurprijs voor 3 personen € 2395,- (er kan een extra kamer gemaakt worden in de woonkamer)\n"
            "Huurprijs voor 2 pesonen € 1995,-\nOok beschikbaar voor woningdelers van max 3 personen.")
    r = evaluate(make_listing(description=text, price_total=1995, bedrooms=3, rooms=4, area_m2=122), cfg)
    assert "rent_for_3:2395" in r.flags and not any(f.startswith("rent_for_2") for f in r.flags)
    assert [(m.search, m.price_pp, m.tier) for m in r.matches] == [("3 people", 798, "ok")]   # was €665, "great"
    assert r.evidence["rent_for_3"].startswith("Huurprijs voor 3 personen € 2395")
    assert fmt.flag_label("rent_for_3:2395") == "€2395 a month for 3 people"
    # over budget for three at the stated rent: no match for three
    r = evaluate(make_listing(description="Huur voor 3 personen: € 2.600. Huur voor 2 personen: € 1.500.",
                              price_total=1500, bedrooms=3), cfg)
    assert names(r) == ["2 people"]
    # two different amounts for the same group, or an amount far from the listed rent: the text does not decide
    for text in ("Huur voor 3 personen € 2.000. Huur voor 3 personen € 2.200.", "Borg: prijs voor 3 personen € 4.500."):
        assert not any(f.startswith("rent_for") for f in detect(make_listing(description=text, price_total=1500), cfg)[0])
    # a rent per person already says what a group pays
    flags, _ = detect(make_listing(description="De huurprijs is per persoon. Huur voor 3 personen € 2.100.", price_total=700), cfg)
    assert "price_per_person" in flags and not any(f.startswith("rent_for") for f in flags)


# ----------------------------------------------------------------------------- advisor review of the fixes above
@pytest.mark.parametrize("text,expected", [
    ("Niet roken, geen huisdieren, geen woningdelers en geen studenten.", ["no_sharers", "no_students"]),
    ("Geen woningdelers, geen studenten.", ["no_sharers", "no_students"]),
    ("Geen woningdelers of studenten.", ["no_sharers", "no_students"]),
    ("Geen woningdelers / studenten.", ["no_sharers", "no_students"]),
    ("Uitsluitend voor een stel, geen studenten.", ["no_sharers", "no_students", "max_persons:2"]),
    ("Max. 2 personen, geen gezin.", ["max_persons:2"]),
    ("Max 2 personen en geen gezinnen.", ["max_persons:2"]),
    ("Alleen voor een stel en geen vrienden.", ["no_sharers", "max_persons:2"]),
    ("Indien u interesse heeft: geen studenten.", ["no_students"]),
    ("Open keuken inclusief elektrische kookplaat.", []),
    ("Keuken incl. gas kookplaat en badkamer inclusief elektrische vloerverwarming.", []),
    ("Huur is inclusief gas, water en elektra.", ["utilities_included"]),
])
def test_refusals_and_limits_survive_the_new_guards(cfg, text, expected):
    r = evaluate(make_listing(description=text), cfg)
    assert r.flags == expected, text


@pytest.mark.parametrize("text", [
    "Prijs voor 2 personen € 950 per persoon.", "Prijs voor 2 personen € 950,- p.p.", "Rent for 2 people € 1100 per person",
    "Prijs voor 3 personen € 1.100 borg", "Huur voor 3 personen € 2100 per week", "Huur voor 2 personen € 950 per kamer",
])
def test_an_amount_per_person_per_week_or_a_deposit_is_no_group_rent(cfg, text):
    flags = detect(make_listing(description=text, price_total=1900), cfg)[0]
    assert not any(f.startswith("rent_for") for f in flags), text


def test_a_lost_cards_reread_keeps_what_the_search_page_knew(cfg, tmp_path):
    first = make_listing("1", extra={"site_min_age": 65, "site_facts": "Leeftijd: 65+", "price_max": 1500})
    st, _ = _run(cfg, tmp_path, FakeSource("fake", [first, make_listing("2")]))
    assert "min_age:65" in st.matches["fake:1"]["flags"]
    st.seen["fake:1"]["details"] = {"bedrooms": 2, "flags": [], "search_price": 1400,
                                    "extra": dict(st.matches["fake:1"]["listing"]["extra"], max_persons=8)}   # no "v"
    st.matches["fake:1"]["listing"]["extra"]["max_persons"] = 8
    st.seen["fake:1"].pop("detail_tries", None)
    st.save(NOW)
    gone = FakeSource("fake", [make_listing("2")])
    st, n = _run(cfg, tmp_path, gone, now=NOW.replace(hour=13))
    assert gone.detail_calls == 1 and not n.messages
    card = st.matches["fake:1"]
    assert "min_age:65" in card["flags"] and "price_varies:1500" in card["flags"]       # from the search page: kept
    assert "max_persons" not in card["listing"]["extra"]                                # the old misreading: gone
    assert st.seen["fake:1"]["last_price"] == 1400 and st.seen["fake:1"]["details"]["v"] == DETAIL_VERSION
