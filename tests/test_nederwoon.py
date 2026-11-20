from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.nederwoon import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def _page(name):
    return (FIX / name).read_text()


def test_leiden_page_skips_parking_and_homes_under_reservation():
    out = parse_page(_page("nederwoon_leiden.html"))
    # 5 cards: a parking place (38831) and "Verhuurd onder voorbehoud" (38890) are left out
    assert [l.source_id for l in out] == ["38891", "38946", "38947"]
    a = out[0]
    assert a.uid == "nederwoon:38891" and a.city == "leiden"
    assert a.url == "https://www.nederwoon.nl/huurwoning/leiden/38891/appartement-alfabetweg"
    assert a.title == "Alfabetweg, Leiden" and a.address == "Alfabetweg" and a.postcode == "2321LB"
    assert a.price_total == 1350 and a.area_m2 == 50 and a.rooms == 2 and a.bedrooms is None
    assert a.property_type == "apartment" and a.furnished == "unfurnished"       # "Oplevering kaal"
    assert a.available_from == "2026-12-01"
    assert a.image_url == "https://www.nederwoon.nl/media/vhe/155454.jpg"
    b = out[1]
    assert (b.price_total, b.area_m2, b.rooms, b.postcode) == (1625, 74, 3, "2321LA")


def test_den_haag_studios_and_cent_amounts_are_rounded():
    out = parse_page(_page("nederwoon_denhaag.html"))
    assert len(out) == 6 and {l.city for l in out} == {"den-haag"}
    s = out[0]
    assert s.source_id == "38971" and s.property_type == "studio"
    assert s.price_total == 775 and s.area_m2 == 25 and s.rooms == 1               # "€ 774,94"
    assert s.furnished == "furnished"                                              # "gemeubileerd en gestoffeerd"
    assert s.available_from is None                                                # "Per direct beschikbaar"
    assert [l.price_total for l in out] == [775, 809, 796, 775, 768, 768]


def test_radius_hits_in_other_towns_are_dropped():
    # the search for "Amsterdam" returns Diemen (3 homes) and an Amstelveen garage box: none is in Amsterdam
    html = _page("nederwoon_amsterdam.html")
    assert [l.city for l in parse_page(html)] == []
    http = FakeHttp(html)
    assert SOURCE.fetch("amsterdam", http) == []
    assert http.urls == ["https://www.nederwoon.nl/search?search_type=1&city=Amsterdam"]


def test_fetch_uses_one_request_per_city_and_the_town_name():
    http = FakeHttp(_page("nederwoon_denhaag.html"))
    out = SOURCE.fetch("den-haag", http)
    assert len(out) == 6 and len(http.urls) == 1
    assert http.urls[0] == "https://www.nederwoon.nl/search?search_type=1&city=Den+Haag"
    assert SOURCE.per_city is True
    # the page of another city asked for under this slug yields nothing
    assert SOURCE.fetch("leiden", FakeHttp(_page("nederwoon_denhaag.html"))) == []
    assert SOURCE.fetch("utrecht", FakeHttp("")) == []


def test_delft_page_with_one_result_singular_count():
    out = parse_page(_page("nederwoon_delft.html"))                                  # "Delft - 1 resultaat"
    assert [(l.source_id, l.city, l.price_total, l.area_m2, l.rooms) for l in out] == [("38889", "delft", 1204, 50, 1)]
    assert out[0].available_from == "2026-12-01" and out[0].address == "Oude Langendijk"
    assert out[0].extra == {"price_label": "Kale huur/per maand"}


def test_honestly_empty_page():
    assert parse_page(_page("nederwoon_empty.html")) == []                          # "Rotterdam - Geen resultaten"


@pytest.mark.parametrize("html", [
    "",
    "<html><body>x</body></html>",
    '<main id="main"><h1>Leiden</h1></main>',                                       # no count
    '<main id="main"><h1>Leiden - 3 resultaten</h1></main>',                        # says 3, shows none
])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_unrecognised_link_raises():
    html = _page("nederwoon_leiden.html").replace("/huurwoning/leiden/38891/", "/woning/leiden/38891/")
    with pytest.raises(SourceError):
        parse_page(html)


def _detail(sid, price, **kw):
    listing = Listing("nederwoon", sid, "https://www.nederwoon.nl/huurwoning/x/%s/y" % sid, "x", "leiden",
                      price_total=price, **kw)
    http = FakeHttp(_page("nederwoon_detail_%s.html" % sid))
    out = SOURCE.fetch_details(listing, http)
    assert http.urls == [listing.url]
    return out


def test_detail_costs_and_who_may_rent():
    l = _detail("38891", 1350)
    assert l.price_total == 1350
    assert l.extra["service_costs"] == 65 and l.extra["service_costs_on_top"] is True   # 1.350 + 65 = 1.415
    assert "Totale huur per maand € 1.415,00" in l.extra["service_costs_note"]
    assert l.extra["site_students_ok"] is True and "site_students_only" not in l.extra
    assert l.extra["site_no_sharers"] is True and l.extra["max_persons"] == 2
    assert l.extra["site_facts"] == "Studenten toegestaan: Ja · Alleen studenten: Nee · Max aantal personen: 2 · Woningdelers: Nee"
    assert l.area_m2 == 50 and l.rooms == 2 and l.property_type == "apartment" and l.furnished == "unfurnished"   # Soort woonruimte, Afwerking
    assert l.description.startswith("Prachtig appartement met energielabel A+++")
    assert "Er zijn twee ruime slaapkamers." in l.description                           # kept for the flag rules
    assert "Toon volledige omschrijving" not in l.description
    assert all(isinstance(v, (str, int, bool, type(None))) for v in l.extra.values())


def test_detail_students_only_and_furnishing_line():
    l = _detail("38971", 775, property_type="studio")
    assert l.extra["site_students_only"] is True and l.extra["site_students_ok"] is True
    assert l.extra["max_persons"] == 1 and l.extra["site_no_sharers"] is True
    # 774,94 + 210,25 + 55,72 = 1.040,91: the page's own sum, with the furnishing line in it
    assert l.extra["service_costs"] == 210 and l.extra["service_costs_on_top"] is True
    assert l.extra["mandatory_costs"] == 56 and "Stoffering € 55,72" in l.extra["mandatory_costs_note"]
    assert l.description.startswith("Nieuw in de verhuur | Studentenwoningen in Den Haag")
    assert "English version available below" in l.description


def test_detail_costs_are_not_added_when_the_sum_does_not_match():
    html = _page("nederwoon_detail_38891.html").replace("1.415,00", "1.500,00")
    out = SOURCE.fetch_details(Listing("nederwoon", "1", "u", "x", "leiden", price_total=1350), FakeHttp(html))
    assert out.extra["service_costs"] == 65 and "service_costs_on_top" not in out.extra


@pytest.mark.parametrize("cut", [
    lambda h: h.replace('id="omschrijving"', 'id="iets-anders"'),
    lambda h: h.replace("description-content", "tekst"),
    lambda h: h.replace('id="voorwaarden"', 'id="x"'),
])
def test_detail_layout_change_raises(cut):
    with pytest.raises(SourceError):
        SOURCE.fetch_details(Listing("nederwoon", "1", "u", "x", "leiden"), FakeHttp(cut(_page("nederwoon_detail_38891.html"))))
