"""Who may rent, read from a listing's own words (hunter/tenancy.py).

Most sentences below are copied from real listings the checker has found; the rest are the wordings that must NOT
set a flag. Each case names the flags that must be there and the flag names that must not."""
from __future__ import annotations

import pytest

from hunter import tenancy
from hunter.tenancy import clause, evidence, read

CASES = [
 # text, expected flags (set), forbidden flags (set)
 # --- wordings a review found that set a flag wrongly (they hid homes on the dashboard)
 ("Je hoeft geen student te zijn om hier te wonen.", set(), {"no_students", "students_only"}),
 ("Ook als je geen student bent, ben je welkom.", set(), {"no_students"}),
 ("You don't have to be a student to live here.", set(), {"no_students", "students_only"}),
 ("Deze woning is niet alleen voor stellen bedoeld.", set(), {"no_sharers"}),
 ("Not only for couples: friends are welcome too.", set(), {"no_sharers"}),
 ("This apartment is not only for families.", set(), {"no_sharers"}),
 ("Alleen voor een stel of gezin.", {"no_sharers"}, set()),
 ("Tussen 20 en 30 minuten fietsen naar het centrum.", set(), {"min_age", "max_age"}),
 ("Between 20 and 30 minutes by bike to the city centre.", set(), {"min_age", "max_age"}),
 ("Voor huurders tussen 20 en 30 jaar.", {"min_age:20", "max_age:30"}, set()),
 ("Ook voor 65+ers geschikt dankzij de lift.", set(), {"min_age"}),
 ("Leuke wijk met veel 30-plussers en gezinnen.", set(), {"min_age"}),
 ("Servicekosten: € 65 plus € 120 voorschot.", set(), {"min_age"}),
 ("Het pand is ouder dan 25 jaar.", set(), {"min_age"}),
 ("Dit is een 55+ woning.", {"min_age:55"}, set()),
 ("Uitsluitend voor 55-plussers.", {"min_age:55"}, set()),
 ("De tuin deel je met de andere bewoners van het complex.", set(), {"housemates"}),
 ("De binnentuin wordt gedeeld met andere bewoners.", set(), {"housemates"}),
 ("De fietsenberging wordt met de medebewoners gedeeld.", set(), {"housemates"}),
 ("You will share the garden with the downstairs neighbours.", set(), {"housemates"}),
 ("Perfect for sharing the apartment with a friend.", set(), {"housemates"}),
 ("Can be rented as a shared apartment for two friends.", set(), {"housemates"}),
 ("De keuken deel je met de andere bewoners.", {"housemates"}, set()),
 ("You will share the kitchen and bathroom with two other tenants.", {"housemates"}, set()),
 ("Only for students, PhD candidates and young professionals.", set(), {"students_only", "working_only"}),
 ("You must be a student or have a job.", set(), {"students_only", "working_only"}),
 ("Alleen voor studenten, geen huisdieren.", {"students_only"}, set()),
 ("Alleen voor starters op de woningmarkt.", set(), {"working_only"}),
 ("De tuin van deze woning deel je met de andere bewoners.", set(), {"housemates"}),
 ("De binnentuin van het appartement wordt gedeeld met andere bewoners.", set(), {"housemates"}),
 ("You will share the garden of the house with the neighbours.", set(), {"housemates"}),
 ("Je deelt de woning en de tuin met twee andere bewoners.", {"housemates"}, set()),
 ("Seniorenwoning voor 65-plussers.", {"min_age:65"}, set()),
 ("Voor 55-plussers.", {"min_age:55"}, set()),
 ("Voorwaarden: - Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN) - Min. 1 jaar contract", {"working_only","no_students"}, {"students_only","students_ok"}),
 ("Requirements: - Max. 1 WORKING PERSON OR A COUPLE (NO STUDENTS) - Min. 1 year contract", {"working_only","no_students"}, {"students_only"}),
 ("Deze woonruimte is ALLEEN beschikbaar voor MAX 2 studenten die niet staan ingeschreven in de gemeente Rotterdam", {"students_only"}, {"no_students","working_only"}),
 ("Deze woning is enkel te huur voor een maximale periode van één jaar aan studenten. U dient aan te tonen dat u momenteel nog student bent en een garantsteller heeft.", {"students_only"}, {"no_students"}),
 ("LET OP! Voor deze woning geldt een minimale leeftijd van 55 jaar of ouder. *** Een ruim 3-kamer appartement van 83 m²", {"min_age:55"}, {"max_age"}),
 ("is perfect for expats, medical professionals, corporate sharing, or families looking for a high-end temporary home", set(), {"working_only","sharers_ok","housemates","students_ok"}),
 ("Aan de Sumatrakade zijn studio’s, twee-, drie- of vierkamerappartementen en seniorenwoningen te huur.", set(), {"min_age"}),
 ("Geschikt voor 3 personen maar tegen meerprijs kan ook een 4e bewoner in. Geschikt voor studenten/werkende/expats.", {"students_ok"}, {"students_only","working_only","no_students"}),
 ("De woning is geschikt voor een stel of woningdelers. De prijs die vermeld staat is per persoon", {"sharers_ok"}, {"no_sharers"}),
 ("Energielabel A 3 slaapkamers Borg 1 maand Geen huisdieren Niet geschikt voor studenten of om te delen!", {"no_students","no_sharers"}, {"students_ok","sharers_ok"}),
 ("Voor de selectie maken wij enkel gebruik van objectieve selectiecriteria zoals leeftijd, de hoogte van het inkomen", set(), {"min_age","max_age","students_only"}),
 ("* LET OP: 2PEER IS SPECIAAL ONTWIKKELD VOOR WONINGDELERS. SCHRIJF JE NU SAMEN MET EEN VRIEND OF KENNIS IN", {"sharers_ok"}, {"no_sharers"}),
 ("Elke bewoner huurt een eigen zeer ruime slaapkamer mét luxe badkamer, de woonkamer/keuken/balkon deel je met één andere huisgenoot.", {"housemates"}, set()),
 ("DIT APPARTEMENT HEEFT 2 SLAAPKAMERS EN 2 BADKAMERS, JE DEELT HIER DUS DE WONING MET EEN HUISGENOOT", {"housemates"}, set()),
 ("* LET OP: 2PEER IS SPECIAAL ONTWIKKELD VOOR DELERS * PER DIRECT BESCHIKBAAR*", {"sharers_ok"}, set()),
 ("Royaal appartement geschikt voor 2 delers - Ieder heeft zijn eigen slaap- en badkamer", {"sharers_ok"}, {"housemates"}),
 ("Woning te huur Niet geschikt voor woningdelers Op een fijne locatie in Leiden", {"no_sharers"}, {"sharers_ok"}),
 ("Een ideale woning voor een gezin, stel of maximaal 2 woningdelers die op zoek zijn naar een fijne woning", {"sharers_ok"}, {"no_sharers"}),
 ("Ook beschikbaar voor een woongroep/woningdelers van max 3 personen. **Van deze 3 personen mag er maar een student zijn.", {"sharers_ok"}, {"students_only","no_students"}),
 ("Studenten wonen meestal alleen voor de duur van hun studie in Rotterdam. Daarom zien we een groep bewoners die voor de helft of meer uit studenten bestaat meestal niet als woongroep.", set(), {"students_only","no_students","no_sharers","sharers_ok"}),
 ("Registration at the address is possible About us: We helps students find long-term housing in The Hague. This place is for students only. No smoking inside, no pets.", {"students_only"}, {"no_students"}),
 ("open kitchen, separate bedroom and your own bathroom: nothing is shared - Suitable for up to 2 people", set(), {"housemates"}),
 ("Ideal for a couple, an international exchange student, or a working professional looking for a comfortable place.", {"students_ok"}, {"students_only","working_only"}),
 ("1 room available with address registration. Only for students or interns ! THE LOCATION", {"students_only"}, set()),
 ("INTERESTED? Only students or interns are eligible. Please check the location", {"students_only"}, set()),
 ("Het appartement is ideaal voor expats, internationale professionals, studenten, stagiairs of iedereen die tijdelijk woonruimte zoekt", {"students_ok"}, {"students_only","working_only"}),
 ("Belangrijk: deze woning is uitsluitend beschikbaar voor studenten die aan de volgende voorwaarden voldoen", {"students_only"}, set()),
 ("Voor internationale studenten gelden geen aanvullende eisen met betrekking tot het vorige woonadres.", set(), {"no_students","students_only"}),
 ("De woning beschikt over twee ruime slaapkamers en is ideaal voor twee studenten die samen willen wonen.", {"students_ok"}, {"students_only"}),
 ("Separaat toilet - Gedeelde was- en droogruimte - Gelegen midden in het centrum van Delft", set(), {"housemates"}),
 ("Gezamenlijke (fietsen)berging. Gedeelde achtertuin van 900,00 m². Gesloten gezamenlijke entree", set(), {"housemates"}),
 ("You would be sharing the apartment with one other student who is currently in the final year of their Master's.", {"housemates"}, {"students_only"}),
 ("currently fully furnished, but the furniture is owned by the current roommate.", {"housemates"}, set()),
 ("Huisgenoot gezocht Werkende huisgenoot gezocht. Woning is 110 m^2. Het wordt alleen met mij (en mijn kat Hiro) gedeeld. Registratie mogelijk.", {"housemates","working_only"}, set()),
 ("INCOMING STUDENTS ONLY! | Fully Furnished Student Room in Rotterdam", {"students_only"}, set()),
 ("**PLEASE NOTE: This room is exclusively available to INCOMING STUDENTS who are not registered in Rotterdam.**", {"students_only"}, set()),
 ("You will share this apartment with two other students (male). - Shared kitchen with cooking facilities - Shared bathroom with toilet", {"housemates"}, set()),
 ("Ideaal voor een stel of twee werkende woningdelers. Huur € 2.000 per maand", {"sharers_ok"}, {"working_only","no_sharers"}),
 ("Looking for only one flatmate — the apartment has 2 balconies. You will share with me, a working professional in his 30s", {"housemates"}, {"working_only","min_age","max_age"}),
 ("I am looking for someone professional, reliable, respectful ⛔ No room sharing ⛔ No pets", set(), {"working_only"}),
 ("ALLEEN VOOR STELLEN – geen studenten, geen delers. Op zoek naar een fijne plek", {"no_students","no_sharers"}, {"sharers_ok","students_ok"}),
 ("De Rivierenbuurt is rustig en groen, met mooie lanen en de Amstel om de hoek.", set(), {"no_sharers"}),
 ("70 m private apartment in De Pijp NO STUDENTS!!! AVAILABLE FROM 1st NOVEMBER.", {"no_students"}, set()),
 ("Also sharing the space are two cats (Patitas & Ranhou), who will appreciate a little company", set(), {"housemates"}),
 ("DOELGROEP Voorkeur gewenste huursamenstelling / doelgroep: single, stel, max. 1 kind.", set(), {"no_sharers","working_only"}),
 ("ER ZIJN ALLEEN NOG LOSSE KAMERS BESCHIKBAAR, WONINGDELEN MET EEN VRIEND OF VRIENDIN KAN OP DIT MOMENT NIET!", set(), {"sharers_ok"}),
 ("the living room/kitchen/balcony with one or a maximum of two other home sharers.", set(), {"no_sharers"}),
 ("PLEASE NOTE: 2PEER IS SPECIALLY DEVELOPED FOR HOUSE SHARERS.", {"sharers_ok"}, set()),
 # synthetic negatives and edge cases
 ("Deze woning is niet alleen voor studenten geschikt, ook werkenden zijn welkom.", set(), {"students_only","working_only"}),
 ("Only for students or young professionals.", set(), {"students_only","working_only"}),
 ("Alleen voor werkenden en studenten.", set(), {"students_only","working_only"}),
 ("Alleen voor werkenden. Geen huisdieren.", {"working_only"}, {"students_only"}),
 ("Working professionals only. No pets.", {"working_only"}, set()),
 ("Suitable for a couple, not for sharers.", {"no_sharers"}, {"sharers_ok"}),
 ("Geschikt voor een stel, niet voor woningdelers.", {"no_sharers"}, {"sharers_ok"}),
 ("Alleen voor een stel of 2 woningdelers.", set(), {"no_sharers"}),
 ("Uitsluitend voor een stel.", {"no_sharers"}, set()),
 ("No shared kitchen, you have everything for yourself.", set(), {"housemates"}),
 ("Geen gedeelde keuken of badkamer.", set(), {"housemates"}),
 ("De keuken wordt gedeeld met twee andere huurders.", {"housemates"}, set()),
 ("Er geldt geen minimale leeftijd van 25 jaar.", set(), {"min_age"}),
 ("Minimum age 23. Students welcome.", {"min_age:23","students_ok"}, set()),
 ("Jongerencontract voor huurders van 18 tot 27 jaar.", {"max_age:27"}, {"min_age"}),
 ("Voor jongeren tot 28 jaar.", {"max_age:28"}, set()),
 ("Leeftijd: 25 - 35 jaar", {"min_age:25","max_age:35"}, set()),
 ("Huurders ouder dan 25 jaar.", {"min_age:26"}, set()),
 ("55+ woning in een rustig complex.", {"min_age:55"}, set()),
 ("Jaren 30 woning met 100 m2 woonoppervlak, huurprijs vanaf 20 jaar geleden gelijk.", set(), {"min_age","max_age"}),
 ("Energielabel A+++ en 85 m2. Borg 2 maanden. Contract van 12 tot 24 maanden.", set(), {"min_age","max_age"}),
 ("Students only pay half of the deposit.", set(), {"students_only"}),
 ("Students are not allowed.", {"no_students"}, {"students_ok","students_only"}),
 ("Campuscontract: je huurt zolang je studeert.", {"students_only"}, set()),
 ("Kamer bij hospita, eigen ingang.", {"housemates"}, set()),
 ("Woningdelen is helaas niet mogelijk.", {"no_sharers"}, set()),
 ("Woningdelers zijn welkom.", {"sharers_ok"}, set()),
 ("House sharing is not allowed.", {"no_sharers"}, set()),
 ("Couples only.", {"no_sharers"}, set()),
]


@pytest.mark.parametrize("text,want,forbidden", CASES, ids=[c[0][:60] for c in CASES])
def test_statement(text, want, forbidden):
    flags, quotes = read(text)
    names = {f.split(":")[0] for f in flags}
    assert not [w for w in want if w not in flags], "missing in %r" % flags
    assert not [f for f in forbidden if f in names], "must not be in %r" % flags
    for name in names:                       # every flag carries the sentence that set it
        assert quotes.get(name), name


def test_gender_only_wording():
    assert "women_only" in read("Female students only. No pets.")[0]
    assert "women_only" in read("Alleen voor vrouwen.")[0]
    assert "men_only" in read("Only for men, sorry.")[0]
    assert read("Not only for women.")[0] == []
    assert read("Women only and men only.")[0] == []          # contradicting: neither is reported


def test_evidence_is_the_sentence_in_the_pages_own_spelling():
    text = "Mooi appartement. Voorwaarden: - Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN) - Min. 1 jaar contract"
    flags, quotes = read(text)
    assert quotes["no_students"] == "Max. 1 WERKEND PERSOON OF STEL (GEEN STUDENTEN)"   # "Max." does not end the sentence
    assert quotes["working_only"] == quotes["no_students"]


def test_evidence_is_cut_around_the_match_in_a_long_sentence():
    text = "x " * 200 + "deze woning is alleen voor studenten " + "y " * 200
    flags, quotes = read(text)
    q = quotes["students_only"]
    assert "alleen voor studenten" in q and len(q) <= 162 and q.startswith("…") and q.endswith("…")


def test_clause_ends_at_sentence_ends_and_bullets_not_at_abbreviations():
    text = "Eerste zin. Tweede zin met ca. 85 m2 en o.a. een berging! Derde"
    start = text.index("85")
    left, right = clause(text, start, start + 2)
    assert text[left:right].strip() == "Tweede zin met ca. 85 m2 en o.a. een berging"
    assert evidence(text, start, start + 2) == "Tweede zin met ca. 85 m2 en o.a. een berging"


def test_extra_rules_from_config_add_patterns_and_a_broken_one_is_ignored():
    assert read("Voor studerenden alleen.")[0] == []
    rules = {"students_only": [r"voor studerenden alleen", r"(broken"], "age": [[r"minstens (\d{2}) lentes", "min"]]}
    flags, _ = read("Voor studerenden alleen. Minstens 30 lentes.", rules)
    assert flags == ["students_only", "min_age:30"]


def test_text_that_changes_length_when_lowercased_does_not_break_quotes():
    flags, quotes = read("İstanbul Apartment. Students only.")
    assert flags == ["students_only"] and "students only" in quotes["students_only"].lower()


def test_a_long_sentence_is_cut_between_words_and_never_inside_the_match():
    text = ("woord " * 40) + "deze woning is alleen voor studenten " + ("einde " * 40)
    start = text.index("alleen voor studenten")
    q = tenancy.evidence(text, start, start + len("alleen voor studenten"))
    assert q.startswith("…woord ") and q.endswith(" einde…") and "alleen voor studenten" in q
    assert len(q) <= tenancy.EVIDENCE_MAX + 2
    assert all(w in ("woord", "deze", "woning", "is", "alleen", "voor", "studenten", "einde") for w in q.strip("…").split())


def test_two_cuts_of_one_passage_count_as_the_same_quote():
    long_ = "…kan gebruikt worden in de woonkamer) Huurprijs voor 2 personen € 1995,- Ook beschikbaar voor woningdelers van max 3 personen."
    assert tenancy.same_passage(long_, "…€ 1995,- Ook beschikbaar voor woningdelers van max 3 personen.")
    assert tenancy.same_passage("Ook beschikbaar voor een woongroep of woningdelers van max 3 personen in overleg",
                                "voor een woongroep of woningdelers van max 3 personen in overleg met de verhuurder")
    assert not tenancy.same_passage("Geen woningdelers", "Geen studenten")
    assert not tenancy.same_passage("", "Geen studenten")
