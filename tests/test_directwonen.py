from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.directwonen import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_tiles():
    html = (FIX / "directwonen.html").read_text()
    http = FakeHttp(html)
    out = SOURCE.fetch("den-haag", http)
    assert http.urls == ["https://directwonen.nl/huurwoningen-huren/den-haag"]
    assert [l.source_id for l in out] == ["523826", "523748", "523693", "523697"]  # Rijswijk dropped
    a = out[0]
    assert a.uid == "directwonen:523826"
    assert a.url == "https://directwonen.nl/huurwoningen-huren/den-haag/norenburg/appartement-523826"  # not the payment page
    assert a.price_total == 1080 and a.rooms == 2 and a.area_m2 == 55
    assert a.property_type == "apartment"
    assert a.address == "Norenburg" and a.available_from == "2026-12-01"
    house = out[1]
    # the tile header "Woning" is the site's generic word for a dwelling: it does not say house or apartment
    assert house.property_type is None and house.rooms == 6 and house.price_total == 4500
    assert any(l.property_type == "room" for l in out)
    # "(Excl.)" / "(Incl.)" are never explained on the tile or the detail page (G.W.E., Servicekosten and
    # Totale huurprijs are empty): they are not read as "utilities included"
    assert [l.price_includes_utilities for l in out] == [None] * len(out)


def test_other_city_is_dropped():
    html = (FIX / "directwonen.html").read_text()
    assert [l.city for l in parse_page(html, "den-haag")] == ["den-haag"] * 4
    assert [l.address for l in parse_page(html, "leiden")] == []


@pytest.mark.parametrize("html", ["", "<html>x</html>", '<div class="tiles-container"></div>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html, "den-haag")


def test_promo_tiles_are_skipped():
    promo = ('<div class="tile"><a href="https://directwonen.nl/hoe-werkt-het" class="inner-content">'
             '<div class="new-search-advert upsell-advert"></div></a></div>')
    html = (FIX / "directwonen.html").read_text().replace('<div class="tiles-container tiles-container-cutome">',
                                                          '<div class="tiles-container tiles-container-cutome">' + promo)
    assert len(parse_page(html, "den-haag")) == 4


DETAIL = """<html><body><div id="detailsHouseContainer"><div id="detailsHouse" class="details-house desc-information">
<div class="advert-roomno">4 Kamers</div>
<div id="advertInformation" class="information">
  <div class="shortDescription col-md-12"><p class="DWO-advert-info"><p>Ruim appartement met 2 slaapkamers.</p></p></div>
  <div class="contentToToggle line-break"><p>Woonkamer, werkkamer en&nbsp;2 slaapkamers aan de achterzijde.</p></div>
</div></div></div></body></html>"""


def test_details_give_description_with_bedrooms(cfg):
    from hunter.filters import evaluate
    from hunter.models import Listing
    url = "https://directwonen.nl/huurwoningen-huren/den-haag/norenburg/appartement-523826"
    l = Listing(source="directwonen", source_id="523826", url=url, title="Appartement Norenburg", city="den-haag",
                price_total=2200, rooms=4, area_m2=85, property_type="apartment")
    assert [m.search for m in evaluate(l, cfg).matches] == ["3 people"]  # on the tile: 4 rooms
    http = FakeHttp(DETAIL)
    l = SOURCE.fetch_details(l, http)
    assert http.urls == [url]
    assert "2 slaapkamers aan de achterzijde" in l.description
    assert not evaluate(l, cfg).matched and l.bedrooms == 2


def test_details_layout_change_raises_and_foreign_url_is_left_alone():
    from hunter.models import Listing
    l = Listing(source="directwonen", source_id="1", url="https://directwonen.nl/huurwoningen-huren/den-haag/x/woning-1",
                title="t", city="den-haag")
    with pytest.raises(SourceError):
        SOURCE.fetch_details(l, FakeHttp("<html>x</html>"))
    l.url = "https://example.com/elsewhere"
    http = FakeHttp(DETAIL)
    assert SOURCE.fetch_details(l, http) is l and http.urls == []


def test_smart_only_adverts_are_not_fetched_and_payment_page_is_not_an_error():
    from hunter.models import Listing
    url = "https://directwonen.nl/huurwoningen-huren/leiden/bachstraat/appartement-520581"
    l = Listing(source="directwonen", source_id="520581", url=url, title="t", city="leiden", rooms=3,
                extra={"smart_only": True})
    http = FakeHttp(DETAIL)
    assert SOURCE.fetch_details(l, http) is l and http.urls == []
    assert l.extra["details_unavailable"] is True and l.description is None
    # not marked on the tile, but the listing page turns out to be the payment page
    l = Listing(source="directwonen", source_id="523879", url=url, title="t", city="leiden", rooms=4)
    paywall = '<html><title>Smart account betaling</title><input id="RoomAdvertPrice" value="2350"></html>'
    assert SOURCE.fetch_details(l, FakeHttp(paywall)).extra["details_unavailable"] is True


# ---------------------------------------------------------------- audit fixes (pages saved 2026-11-19)
def _detail(sid):
    return (FIX / ("directwonen_detail_%s.html" % sid)).read_text()


def _tiles():
    return {l.source_id: l for l in parse_page((FIX / "directwonen_tiles.html").read_text(), "den-haag")}


def _fetch(sid, tile_id=None, **kw):
    from hunter.models import Listing
    url = "https://directwonen.nl/huurwoningen-huren/den-haag/x/appartement-%s" % sid
    l = Listing(source="directwonen", source_id=sid, url=url, title="t", city="den-haag", **kw)
    http = FakeHttp(_detail(sid))
    out = SOURCE.fetch_details(l, http)
    assert http.urls == [url]
    return out


def test_tile_types_come_from_the_tile_wording_and_excl_is_not_read():
    tiles = _tiles()
    assert tiles["524088"].property_type is None            # "Woning": generic
    assert tiles["514568"].property_type == "apartment" and tiles["524047"].property_type == "room"
    assert tiles["483719"].property_type == "studio"
    assert all(l.price_includes_utilities is None for l in tiles.values())  # every tile says "(Excl.)" or "(Incl.)"


def test_house_by_tile_header_is_dropped_but_a_real_house_word_is_kept():
    html = (FIX / "directwonen_tiles.html").read_text().replace(">Woning<", ">Eengezinswoning<")
    assert {l.source_id: l for l in parse_page(html, "den-haag")}["524088"].property_type == "house"


def test_524088_is_an_apartment_by_its_own_wording():
    # tile header "Woning"; page: "Geweldig en ruim 5-kamerappartement", "Afgesloten portiek ... trappenhuis ... 3e etage"
    l = _fetch("524088", property_type=_tiles()["524088"].property_type)
    assert l.property_type == "apartment"
    assert l.furnished is None                               # the floor chip shows "-"
    assert l.description.count("Geweldig en ruim 5-kamerappartement") == 1  # not the cut preview twice
    assert "karakteristie...." not in l.description
    assert "Het appartement is geschikt voor een stel/ gezin." in l.description
    assert "Drie slaapkamers van circa. 10 m², 9 m², 6 m²." in l.description  # the end of the text is there too


def test_a_tile_type_that_is_specific_is_not_overruled():
    l = _fetch("524088", property_type="house")
    assert l.property_type == "house"


def test_furnished_from_the_floor_chip():
    assert _fetch("514568", property_type="apartment").furnished == "upholstered"   # "Gestoffeerd"
    assert _fetch("509993", property_type="apartment").furnished == "upholstered"
    l = _fetch("520768", property_type="apartment")                                   # "Gemeubileerd"
    assert l.furnished == "furnished" and "volledig gemeubileerd opgeleverd" in l.description
    assert "ideaal is voor professionals, stellen of iedereen" in l.description


def test_unknown_chip_wording_gives_no_furnished():
    html = _detail("514568").replace("Gestoffeerd", "Op aanvraag")
    from hunter.models import Listing
    l = Listing(source="directwonen", source_id="1", url="https://directwonen.nl/huurwoningen-huren/den-haag/x/appartement-1",
                title="t", city="den-haag")
    assert SOURCE.fetch_details(l, FakeHttp(html)).furnished is None
