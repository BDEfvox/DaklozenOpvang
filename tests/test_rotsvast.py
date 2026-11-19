from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.runner import fetch_source
from hunter.sources.base import SourceBlocked, SourceError
from hunter.sources.rotsvast import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        page = self.pages[url] if isinstance(self.pages, dict) else self.pages
        if isinstance(page, Exception):
            raise page
        return page


def test_parse_skips_rented_and_other_towns():
    html = (FIX / "rotsvast.html").read_text()
    http = FakeHttp(html)
    out = SOURCE.fetch("den-haag", http)
    assert http.urls == ["https://www.rotsvast.nl/huren/?search=Den+Haag"]  # page has a rented card: no page 2
    assert [l.source_id for l in out] == ["h1025411947", "h1025411958", "h1025410133"]
    a = out[2]
    assert a.uid == "rotsvast:h1025410133" and a.city == "den-haag"
    assert a.price_total == 2150 and a.bedrooms == 3 and a.area_m2 == 100
    assert a.furnished == "upholstered" and a.address == "Van Diemenstraat 80"
    assert out[0].available_from == "2026-11-01"


def test_fetch_details_reads_description():
    html = (FIX / "rotsvast_detail.html").read_text()
    l = Listing(source="rotsvast", source_id="h1", url="https://www.rotsvast.nl/huren/x-h1/", title="x", city="den-haag")
    out = SOURCE.fetch_details(l, FakeHttp(html))
    assert "Niet geschikt voor studenten of om te delen" in out.description
    assert out.rooms == 4 and out.price_includes_utilities is False


def test_layout_change_raises():
    for bad in ("<html></html>", '<div class="house-list__overview__items"></div>'):
        with pytest.raises(SourceError):
            parse_page(bad, "den-haag")


def test_empty_search_is_ok():
    html = '<div class="house-list__overview__items"></div><p>Geen resultaten gevonden</p>'
    assert parse_page(html, "leiden")[0] == []


def test_card_without_price_is_kept_with_unknown_price():
    html = ('<div class="house-list__overview__items"><a href="https://www.rotsvast.nl/huren/breestraat-leiden-h103580391/" '
            'class="card card--house "><div class="card-house__label"></div><div class="card-house__content">'
            '<div class="card-house__text">Leiden</div><div class="card-house__title"> Breestraat 74 </div>'
            '<ul class="card-house__list"><li><i class="icon-surface"></i>272 m²</li><li><i class="icon-bed"></i>4</li></ul>'
            '</div></a></div>')
    out = parse_page(html, "leiden")[0]
    assert len(out) == 1 and out[0].price_total is None and out[0].bedrooms == 4 and out[0].area_m2 == 272


@pytest.mark.parametrize("city, town", [("delft", "Delft"), ("rotterdam", "Rotterdam")])
def test_fetch_keeps_page_one_listings_when_page_two_fails(cfg, city, town):
    html = (FIX / "rotsvast.html").read_text()
    html = html.replace("Den Haag", town).replace("Den+Haag", town).replace("Verhuurd", "")
    first_url = f"https://www.rotsvast.nl/huren/?search={town}"
    second_url = f"https://www.rotsvast.nl/huren/page/2/?search={town}"
    http = FakeHttp({first_url: html, second_url: SourceError("HTTP 503")})

    result = fetch_source(SOURCE, cfg, http, only_city=city)

    assert http.urls == [first_url, second_url]
    assert result.status == "partial"
    assert result.per_city[city] == (4, "page 2: HTTP 503")
    assert all(l.city == city and l.extra["income_requirement"] for l in result.listings)


def test_pagination_block_keeps_page_one_and_stops_requests(cfg):
    html = (FIX / "rotsvast.html").read_text().replace("Verhuurd", "")
    first_url = "https://www.rotsvast.nl/huren/?search=Den+Haag"
    second_url = "https://www.rotsvast.nl/huren/page/2/?search=Den+Haag"
    http = FakeHttp({first_url: html, second_url: SourceBlocked("HTTP 429")})

    result = fetch_source(SOURCE, cfg, http)

    assert http.urls == [first_url, second_url]
    assert result.status == "partial"
    assert result.per_city == {"den-haag": (4, "page 2: HTTP 429")}


def _details(tag, city="amsterdam", **kw):
    base = dict(source="rotsvast", source_id="h1", url="https://www.rotsvast.nl/huren/x-h1/", title="x", city=city)
    base.update(kw)
    return SOURCE.fetch_details(Listing(**base), FakeHttp((FIX / ("rotsvast_detail_%s.html" % tag)).read_text()))


@pytest.mark.parametrize("tag, card_street, expected", [
    ("wolbrantskerkweg_64k", "Wolbrantskerkweg", "Wolbrantskerkweg 64 K"),  # "Deel deze woning Wolbrantskerkweg 64 K Amsterdam ..."
    ("ariana_nozemanstraat_82", "Ariana Nozemanstraat", "Ariana Nozemanstraat 82"),
    ("ariana_nozemanstraat_156", "Ariana Nozemanstraat", "Ariana Nozemanstraat 156"),
])
def test_address_gets_house_number_from_share_block(tag, card_street, expected):
    assert _details(tag, address=card_street).address == expected


def test_address_stays_when_share_block_has_no_number():
    # "Waldeck Pyrmontkade Den Haag 1 kamers EUR 10 p.m."
    assert _details("waldeck_shortstay", city="den-haag", address="Waldeck Pyrmontkade").address == "Waldeck Pyrmontkade"


def test_details_table_fills_rooms_bedrooms_area_and_date():
    out = _details("ariana_nozemanstraat_156", bedrooms=1, area_m2=80)  # table: Aantal kamers 3, slaapkamers 2, 83 m2
    assert out.rooms == 3 and out.bedrooms == 2 and out.area_m2 == 83
    assert out.available_from is None  # "Startdatum Per direct beschikbaar"
    assert _details("ariana_nozemanstraat_82").rooms == 4  # the description has no "N-kamer"


def test_utilities_not_included_from_overige_kosten():
    for tag in ("wolbrantskerkweg_64k", "ariana_nozemanstraat_82", "ariana_nozemanstraat_156", "waldeck_shortstay"):
        assert _details(tag).price_includes_utilities is False, tag


def test_utilities_included_when_the_table_says_so():
    html = (FIX / "rotsvast_detail_wolbrantskerkweg_64k.html").read_text().replace("Niet inbegrepen", "Inbegrepen")
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="amsterdam")
    assert SOURCE.fetch_details(l, FakeHttp(html)).price_includes_utilities is True


def test_price_total_is_kept_and_contradicting_figures_are_not_guessed():
    # "Kale huur 3.450", "Totale huur 3.450 / Servicekosten Inbegrepen"; the description says "Servicekosten: 100 per maand"
    out = _details("ariana_nozemanstraat_82", price_total=3450)
    assert out.price_total == 3450
    assert "service_costs" not in out.extra and "total_rent" not in out.extra and "service_costs_included" not in out.extra
    assert "Servicekosten: € 100 per maand" in out.description


def test_service_costs_amount_in_the_table_is_used():
    html = (FIX / "rotsvast_detail_ariana_nozemanstraat_82.html").read_text().replace("Inbegrepen", "€100", 1)
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="amsterdam")
    assert SOURCE.fetch_details(l, FakeHttp(html)).extra["service_costs"] == 100


def test_placeholder_rent_gives_no_price():
    out = _details("waldeck_shortstay", city="den-haag")  # "Kale huur EUR 10"
    assert out.price_total is None


def test_short_stay_only():
    out = _details("waldeck_shortstay", city="den-haag")
    assert out.extra["max_stay_months"] == 6
    assert "alleen short stay" in out.description
    assert "max_stay_months" not in _details("wolbrantskerkweg_64k").extra


def test_old_detail_fixture_still_works():
    html = (FIX / "rotsvast_detail.html").read_text()
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="den-haag", address="Van Diemenstraat", bedrooms=3)
    out = SOURCE.fetch_details(l, FakeHttp(html))
    assert out.address == "Van Diemenstraat" and out.bedrooms == 3 and out.rooms == 4


@pytest.mark.parametrize("sentence", [
    "Geen short stay mogelijk. De borg bedraagt maximaal 2 maanden huur.",
    "Niet voor short stay. Waarborgsom van maximaal 2 maanden.",
    "Huurcontract voor onbepaalde tijd, maximaal 3 maanden opzegtermijn.",
])
def test_ordinary_home_is_not_short_stay(sentence):
    html = ('<div class="house-description"><h2>Beschrijving</h2><div class="house-description__intro"><p>Ruim appartement. %s</p>'
            '</div></div>' % sentence)
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="amsterdam")
    assert "max_stay_months" not in SOURCE.fetch_details(l, FakeHttp(html)).extra


def test_a_total_above_the_bare_rent_is_paid_on_top():
    # h1025010362 and h1025010426 (19 Nov 2026): "Kale huur €500 / Totale huur €525", "Kale huur €845 / Totale huur €900".
    html = (FIX / "rotsvast_detail_ariana_nozemanstraat_82.html").read_text()
    head, tail = html.split("Totale huur", 1)
    html = head + "Totale huur" + tail.replace("€3.450", "€3.505", 1)
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="amsterdam", price_total=3450)
    ex = SOURCE.fetch_details(l, FakeHttp(html)).extra
    assert ex["service_costs"] == 55 and ex["service_costs_on_top"] is True
    assert "€3.450" in ex["service_costs_note"] and "€3.505" in ex["service_costs_note"]
    # the same total as the bare rent (every other saved page) claims nothing
    same = _details("ariana_nozemanstraat_82", price_total=3450).extra
    assert "service_costs" not in same and "service_costs_on_top" not in same
    # a total that does not belong to the card's rent is not believed
    l = Listing(source="rotsvast", source_id="h1", url="u", title="x", city="amsterdam", price_total=2000)
    assert "service_costs_on_top" not in SOURCE.fetch_details(l, FakeHttp(html)).extra
