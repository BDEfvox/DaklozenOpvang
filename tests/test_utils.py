from __future__ import annotations

import pytest

from hunter.utils import bedrooms_from_text


@pytest.mark.parametrize("text,want", [
    # phrases taken from live listings (10 Nov 2026)
    ("Deze verzorgde woning beschikt over 3 slaapkamers, een moderne keuken", 3),
    ("• 3 slaapkamers van verschillende afmetingen; • Separaat toilet", 3),
    ("De woning beschikt over drie ruime slaapkamers. Eerste verdieping: Overloop met toegang tot twee slaapkamers", 3),
    ("beschikt over twee slaapkamers, perfect voor een stel • Woonoppervlakte: 87 m² • Aantal slaapkamers: 2 •", 2),
    ("ongeveer 50 m² en bestaat uit: • 1 slaapkamer • Woonkamer met open keuken", 1),
    ("uitgebreide keuken met alle apparatuur, 3 slaapkamers van 4,5 x 4, 3,5 x 2,20", 3),
    # other ways of saying it
    ("4 kamers waarvan 2 slaapkamers", 2),
    ("4-kamerappartement met twee grote, lichte slaapkamers", 2),
    ("Spacious 2-bedroom apartment in the centre", 2),
    ("Lovely three bedroom house with garden", 3),
    ("2 bedrooms, 1 bathroom", 2),
    ("Number of bedrooms: 3", 3),
    ("Slaapkamers: 3", 3),
    ("3 slaapkamers 2 badkamers", 3),
    ("1 ruime slaapkamer en een werkkamer", 1),
    ("De hoofdslaapkamer ligt achter, de tweede slaapkamer aan de voorzijde.", 2),
    ("Slaapkamer 1: 12 m². Slaapkamer 2: 9 m². Slaapkamer 3: 6 m².", 3),
])
def test_bedrooms_from_text(text, want):
    assert bedrooms_from_text(text) == want


@pytest.mark.parametrize("text", [
    "met toegang tot een balkon, één tot drie slaapkamers en een moderne badkamer",  # a whole building
    "Appartementen met 2 of 3 slaapkamers",
    "2-3 slaapkamers beschikbaar",
    "Slaapkamers: 4,5 x 3 m",                       # a size, not a count
    "een slaapkamer aan de achterzijde met balkon",  # "een" is just the article
    "Gelegen op 3 hoog. De slaapkamer is ruim.",
    "woning van 100 m² met 2 badkamers en slaapkamers boven",
    "15 minuten van het centrum; slaapkamer 12 m2",
    "Nice flat", "", None,
])
def test_bedrooms_from_text_says_nothing(text):
    assert bedrooms_from_text(text) is None


def test_city_slug_den_haag_districts():
    from hunter.utils import city_slug
    for name in ("Scheveningen", "Loosduinen", "Kijkduin", "Ypenburg", "Leidschenveen", " scheveningen "):
        assert city_slug(name) == "den-haag", name
    for name in ("Rijswijk", "Voorburg", "Leidschendam", "Wassenaar", "Zoetermeer", "Delft"):
        assert city_slug(name) != "den-haag", name
    assert city_slug("Rijswijk") is None and city_slug("Voorburg") is None


# ---- found by the page audit of 19 Nov 2026 --------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    # Direct Wonen 524088: one bedroom on one floor, three on the next: four, not three
    ("Slaapkamer van circa. 13 m². Vanuit de slaapkamer en de keuken heeft u toegang tot het balkon. U neemt de trap "
     "naar de 4e etage. Drie slaapkamers van circa. 10 m², 9 m², 6 m².", 4),
    ("Twee slaapkamers van 12 m² en 9 m². De slaapkamer van 12 m² ligt aan de voorzijde.", 2),   # one of the two again
    ("Slaapkamer van 13 m². Slaapkamer van 9 m². Bedroom of 13 m². Bedroom of 9 m².", 2),         # Dutch and English
    ("3 slaapkamers. Op de eerste verdieping twee slaapkamers van 12 m² en 10 m².", 3),
    ("Master bedroom of 16 sqm, two bedrooms of 9 m² and 8 m²", 3),
    ("2 slaapkamers van 12 m², 9 m² en 7 m²", 2),                                                 # count and sizes disagree
    ("Het complex heeft appartementen met 1 tot 3 slaapkamers van 10 m² tot 16 m²", None),
    ("De woonkamer van 30 m² en slaapkamer met balkon", None),
    ("Newly Furnished 2-BR Apartment in Den Haag Centrum", 2),                                    # Kamernet 2414230
    ("3BR house with garden", 3), ("BR 2", None),
])
def test_bedrooms_described_one_by_one_and_abbreviated(text, expected):
    from hunter.utils import bedrooms_from_text
    assert bedrooms_from_text(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("1228.55", 1229), ("€ 1.248,55", 1249), ("€ 1.248,49", 1248), ("€ 1.250,-", 1250), ("€1,250.00", 1250), ("1250", 1250),
    ("118,35", 118), ("€ 2.400", 2400),
])
def test_price_with_cents_is_rounded_like_the_site_shows_it(text, expected):
    from hunter.utils import parse_price
    assert parse_price(text) == expected


# ----------------------------------------------------------------------------- second review (Gemini, 19 Nov 2026)
@pytest.mark.parametrize("text,expected", [
    ("Premium 2-Bedroom Apartment (100 m2). * Bedrooms: 3 rooms in total. Two bedrooms are equipped with comfortable "
     "beds, and the third can be used as a home office.", 2),                # HousingAnywhere ut1749760
    ("Woning met 1 slaapkamer (voorheen 2 slaapkamers).", 1),
    ("Ruim appartement met 1 slaapkamer, mogelijkheid tot 2 slaapkamers.", 1),
    ("Geen 2 slaapkamers maar 1 ruime slaapkamer.", 1),
    ("Eventueel 3 slaapkamers te maken. Nu 2 slaapkamers.", 2),
    ("Slaapkamers: 3", 3),
    ("Geen huisdieren\n3 slaapkamers", 3),                                   # the denial stands on another line
    # a second list that repeats sizes describes the same rooms again
    ("3 slaapkamers van 12 m², 10 m² en 8 m². Op de eerste verdieping 2 slaapkamers van 12 m² en 10 m².", 3),
    ("2 slaapkamers van 12 m² en 10 m². Eerste verdieping: 2 slaapkamers van 12 m² en 9,5 m².", 2),
    ("Slaapkamer van circa. 13 m². Drie slaapkamers van circa. 10 m², 9 m², 6 m².", 4),
])
def test_bedrooms_the_home_had_or_could_have_are_not_counted(text, expected):
    assert bedrooms_from_text(text) == expected


def test_clean_text_keeps_line_breaks_only_on_request():
    from hunter.utils import clean_text
    assert clean_text("  Een \n\n twee\tdrie \r\n vier ") == "Een twee drie vier"
    assert clean_text("  Een \n\n twee\tdrie \r\n vier ", keep_lines=True) == "Een\ntwee drie\nvier"
    assert clean_text("abc\ndef", 4, keep_lines=True) == "abc"               # a cut never ends on a line break
    assert clean_text(" \n ", keep_lines=True) is None


def test_block_text_breaks_lines_where_the_page_does():
    from bs4 import BeautifulSoup
    from hunter.utils import block_text, clean_text
    html = ("<div><p>Geen <b>huisdieren</b></p><ul><li>Studenten welkom</li><li>Roken: nee</li></ul>"
            "Per direct<br>beschikbaar <a href='#'>lees meer</a><script>x()</script><!-- note --></div>")
    text = clean_text(block_text(BeautifulSoup(html, "html.parser")), keep_lines=True)
    assert text == "Geen huisdieren\nStudenten welkom\nRoken: nee\nPer direct\nbeschikbaar lees meer"
