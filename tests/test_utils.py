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
