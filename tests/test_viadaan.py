from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.viadaan import SOURCE, apply_details, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def _fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def _all():
    return {l.source_id: l for l in parse_page(_fx("viadaan_aanbod.html"))}


def test_ten_cards_four_in_the_five_cities():
    html = _fx("viadaan_aanbod.html")
    assert html.count("Bekijken") == 10
    out = _all()
    # Eindhoven (2), Nijmegen (2), Stein and Zandvoort are not among the five cities
    assert set(out) == {"h101330917", "h101334182", "h101334177", "h101334179"}


def test_card_fields():
    w = _all()["h101330917"]
    assert w.uid == "viadaan:h101330917" and w.city == "amsterdam"
    assert w.url == "https://viadaan.nl/aanbod/woubruggestraat-amsterdam-h101330917"
    assert (w.price_total, w.bedrooms, w.area_m2) == (3300, 3, 92)
    assert w.address == "Woubruggestraat 9" and w.title == "Woubruggestraat 9, Amsterdam"
    assert w.rooms is None and w.property_type is None and w.postcode is None
    assert w.image_url.startswith("https://viadaan.ams3.digitaloceanspaces.com/")
    k = _all()["h101334182"]
    assert (k.price_total, k.bedrooms, k.area_m2, k.address) == (2500, 3, 97, "Kiekstraat 165")
    # the card's chips are bedrooms, toilets, bathrooms: 2 / 0 / 1 for the Leiden home
    m = _all()["h101334179"]
    assert (m.city, m.price_total, m.bedrooms, m.area_m2) == ("leiden", 2495, 2, 82)
    assert m.address == "Marepoortkade"          # the page shows no house number


def test_fetch_is_one_plain_request_and_filters_the_city():
    http = FakeHttp(_fx("viadaan_aanbod.html"))
    assert SOURCE.per_city is False
    assert len(SOURCE.fetch("*", http)) == 4 and http.urls == ["https://viadaan.nl/aanbod"]
    assert [l.city for l in SOURCE.fetch("leiden", FakeHttp(_fx("viadaan_aanbod.html")))] == ["leiden"]
    assert SOURCE.fetch("delft", FakeHttp(_fx("viadaan_aanbod.html"))) == []


def test_hoogvliet_rotterdam_is_rotterdam_and_other_towns_stay_out():
    html = _fx("viadaan_aanbod.html").replace("<p class=\"text-sm text-gray-600\">\n    Stein", "<p>Stein")
    html = html.replace(">Stein<", ">Hoogvliet Rotterdam<", 1).replace(">Zandvoort<", ">Schiedam<", 1)
    cities = {l.city for l in parse_page(html)}
    assert "rotterdam" in cities and len(parse_page(html)) == 5


def test_detail_amsterdam_fields_and_description():
    l = SOURCE.fetch_details(_all()["h101330917"], FakeHttp(_fx("viadaan_detail_amsterdam.html")))
    assert (l.bedrooms, l.rooms, l.area_m2, l.furnished, l.available_from) == (3, 4, 92, "furnished", "2026-12-01")
    lines = l.description.split("\n")
    assert lines[0] == "(scroll down for english)"
    assert "Max 3 personen." in l.description and "Beschrijving" not in lines
    assert l.price_total == 3300


def test_detail_leiden_without_availability_date():
    l = apply_details(_all()["h101334179"], _fx("viadaan_detail_leiden.html"))
    assert (l.bedrooms, l.rooms, l.area_m2, l.available_from) == (2, 3, 82, None)
    assert l.description.startswith("This property is not available to house sharers.")


def test_detail_of_another_page_or_unknown_layout_raises():
    other = _all()["h101334182"]
    with pytest.raises(SourceError):
        apply_details(other, _fx("viadaan_detail_amsterdam.html"))       # canonical link names another home
    with pytest.raises(SourceError):
        apply_details(other, "<html><body><article><h2>Beschrijving</h2><p>x</p></article></body></html>")


@pytest.mark.parametrize("html", ["", "<html><body>x</body></html>", "<ul><li>no links</li></ul>"])
def test_unrecognised_page_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_card_without_price_is_a_layout_change():
    html = _fx("viadaan_aanbod.html").replace("<b ", "<i ").replace("</b>", "</i>")
    with pytest.raises(SourceError):
        parse_page(html)


def test_honestly_empty_offer():
    html = "<html><body><select></select><p>Aanbod in Nederland</p><p>0 resultaten</p></body></html>"
    assert parse_page(html) == []
