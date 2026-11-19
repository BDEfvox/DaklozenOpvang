from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.vanderlinden import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_skips_senior_option_and_duplicates():
    html = (FIX / "vanderlinden.html").read_text()
    out = parse_page(html)
    ids = [l.source_id for l in out]
    # Ittmannserf (Nunspeet, senior) and Bottelarijstraat (onder optie) are dropped; duplicate card merged
    assert len(ids) == len(set(ids)) and "2571" not in ids
    ams = [l for l in out if l.city == "amsterdam"]
    assert {l.source_id for l in ams} == {"2579", "2612"}
    a = [l for l in ams if l.source_id == "2579"][0]
    assert a.uid == "vanderlinden:2579" and a.price_total == 1550 and a.rooms == 3 and a.bedrooms is None and a.area_m2 == 77
    assert a.url == "https://www.vanderlinden.nl/huurwoning/Bert-Haanstrakade-552-Amsterdam/2579/"
    assert a.address == "Bert Haanstrakade 552"
    assert a.image_url.startswith("https://www.vanderlinden.nl/media_huur/")


def test_fetch_filters_by_city_and_uses_one_request():
    html = (FIX / "vanderlinden.html").read_text()
    http = FakeHttp(html)
    assert SOURCE.per_city is False
    out = SOURCE.fetch("*", http)
    assert len(http.urls) == 1
    assert {l.city for l in out} == {"amsterdam", "den-haag"}
    assert [l.city for l in SOURCE.fetch("den-haag", FakeHttp(html))] == ["den-haag"]


@pytest.mark.parametrize("html", ["", "<html>x</html>", '<div id="zoekresultaten"></div>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def _detail(sid):
    return (FIX / ("vanderlinden_detail_%s.html" % sid)).read_text()


def _listing(sid, price, **kw):
    from hunter.models import Listing
    return Listing("vanderlinden", sid, "https://www.vanderlinden.nl/huurwoning/x/%s/" % sid, "x", "amsterdam",
                   price_total=price, **kw)


def _fetch(sid, price, **kw):
    listing = _listing(sid, price, **kw)
    http = FakeHttp(_detail(sid))
    out = SOURCE.fetch_details(listing, http)
    assert http.urls == [listing.url]
    return out


def test_2peer_reports_what_the_page_states_and_invents_no_total():
    # card and header: "€ 966 per maand"; text: "Kale huurprijs: € 966 per maand per persoon. Servicekosten: € 195 ...
    # per persoon. Totaal maandbedrag: € 1.161 per persoon"; the old code reported 1161 x 2 = 2322
    l = _fetch("2703", 966, rooms=3, area_m2=86)
    assert l.price_total == 966
    assert l.extra["price_per_person"] is True
    assert l.extra["service_costs"] == 195
    assert l.extra["site_housemates"] is True            # "eigen woonruimte in een gedeelde 2Peer-woning"
    facts = l.extra["site_facts"]
    assert len(facts) <= 200 and "Kale huurprijs: € 966 per maand per persoon" in facts
    assert "Totaal maandbedrag: € 1.161 per persoon" in facts and "gedeelde 2Peer-woning" in facts
    assert l.furnished == "furnished"                    # "Meubilering en stoffering inbegrepen"
    assert l.available_from is None                      # "Per direct beschikbaar"
    assert l.rooms == 3 and l.area_m2 == 86
    assert all(isinstance(v, (str, int, bool, type(None))) for v in l.extra.values())


def test_price_is_taken_from_the_header_when_the_card_had_none():
    assert _fetch("2703", None).price_total == 966


def test_description_is_the_listing_text_only():
    l = _fetch("2703", 966)
    assert l.description.startswith("NEDERLANDS Cruquiusweg 85R, Amsterdam – eigen woonruimte")
    assert "Meer informatie over het woonconcept vind je op 2peer.nl." in l.description
    assert "More information about the housing concept is available at 2peer.nl." in l.description  # complete, not cut
    for junk in ("Hier ga je wonen", "Inloggen", "Lees verder", "MAP"):
        assert junk not in l.description
    l = _fetch("2603", 1173)
    assert l.description.endswith("er kunnen geen rechten aan ontleend worden.")
    assert "Hier ga je wonen" not in l.description and "Lees verder" not in l.description
    assert "De woning is niet gemeubileerd" in l.description


def test_studio_service_costs_and_no_year_dates():
    l = _fetch("2603", 1173)
    assert l.property_type == "studio"                   # bullet "• Studio •"
    assert l.extra["service_costs"] == 105               # "Servicekosten €105,- per maand"
    assert l.available_from is None                      # "Beschikbaar per 1 oktober": no year on the page
    assert l.furnished == "unfurnished"                  # "De woning is niet gemeubileerd"
    assert "price_per_person" not in l.extra and "site_housemates" not in l.extra


def test_available_from_with_year_and_service_costs():
    l = _fetch("2579", 1550, rooms=3)
    assert l.available_from == "2026-11-01"              # "Beschikbaar per 1 november 2026!"
    assert l.extra["service_costs"] == 65                # "Servicekosten €65,00 per maand"
    assert l.furnished is None and l.property_type is None and l.price_total == 1550
    assert "price_per_person" not in l.extra


def test_not_furnished_and_date_without_year():
    l = _fetch("2326", 2905)
    assert l.furnished == "unfurnished" and l.available_from is None  # "Beschikbaar per 15 Juli!"
    assert l.extra["service_costs"] == 65 and l.property_type is None


def test_per_person_amount_is_not_multiplied():
    html = ("<html><body><div>Woon oppervlakte 72 m² Aantal kamers 3</div><div><h2>Over de woning</h2>"
            "<p>Speciaal ontwikkeld voor woningdelers. Huren vanaf €1.682,- all-in, bestaande uit; "
            "Gemiddelde kale huurprijs €841,- per persoon<br/>Servicekosten: € 195 per maand per persoon</p></div></body></html>")
    l = SOURCE.fetch_details(_listing("2627", 841, rooms=3), FakeHttp(html))
    assert l.price_total == 841 and l.extra["price_per_person"] is True and l.extra["service_costs"] == 195
    assert "1682" not in str(l.to_dict()["price_total"]) and l.rooms == 3
    assert "woningdelers" in l.description


@pytest.mark.parametrize("cut", [
    lambda h: h.replace("Over de woning", "Iets anders"),
    lambda h: h.replace("<p>", "<div>").replace("</p>", "</div>"),
])
def test_detail_layout_change_raises(cut):
    with pytest.raises(SourceError):
        SOURCE.fetch_details(_listing("2579", 1550), FakeHttp(cut(_detail("2579"))))
