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
    assert [(m.persons, m.price_pp) for m in r.matches] == [(2, 725)]   # (1300 + 150) / 2


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
    assert "💶 <b>€750</b>/month per person" in fmt.format_new_match(make_listing(price_total=750), r, cfg, "Fake")


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
