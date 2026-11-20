from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError, SourcePartial
from hunter.sources.vva import LIST_URL, SOURCE, _price_cap, next_page, parse_page

FIX = Path(__file__).parent / "fixtures"


def _page(name):
    return (FIX / name).read_text()


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.pages[url] if isinstance(self.pages, dict) else self.pages


def test_price_filtered_page_keeps_homes_and_drops_parking_and_offers():
    out = parse_page(_page("vva_amsterdam.html"))
    # 8 cards: 5 parking places and Karspeldreef 1393 ("Onder bod") are left out
    assert [l.source_id for l in out] == ["amsterdam/burgemeester-hogguerstraat/395-g", "amsterdam/ref-204"]
    a = out[0]
    assert a.uid == "vva:amsterdam/burgemeester-hogguerstraat/395-g" and a.city == "amsterdam"
    assert a.url == "https://www.vva.amsterdam/woningaanbod/huur/amsterdam/burgemeester-hogguerstraat/395-g"
    assert a.title == "Burgemeester Hogguerstraat 395G, Amsterdam"
    assert a.address == "Burgemeester Hogguerstraat 395G" and a.postcode == "1064CS"
    assert a.price_total == 2000 and a.rooms == 2 and a.bedrooms == 1 and a.area_m2 == 42
    assert a.property_type == "apartment" and a.furnished == "furnished"
    assert a.image_url.startswith("https://haywebattachments.blob.core.windows.net/public/")
    b = out[1]                                  # the agency hides the street: postcode only, no bedrooms stated
    assert b.address is None and b.postcode == "1072LC" and b.title == "1072LC, Amsterdam"
    assert b.price_total == 1850 and b.bedrooms is None and b.rooms == 2


def test_id_and_url_do_not_carry_the_query_string():
    out = parse_page(_page("vva_page1.html"))
    assert [l.source_id for l in out] == ["amsterdam/keizersgracht/ref-1155", "amsterdam/nieuwevaart/93",
                                          "amsterdam/leeuwendalersweg/676-0", "amsterdam/oosterpark/60-c-ref-07541"]
    assert all("?" not in l.url and "take=16" not in l.url for l in out)
    assert [(l.price_total, l.bedrooms, l.rooms, l.area_m2) for l in out] == [
        (3500, 2, 3, 110), (2450, 1, 2, 70), (2750, 3, 4, 100), (2200, 2, 3, 68)]
    assert out[2].furnished is None             # no finish on the card: not guessed


def test_project_card_with_a_price_range_is_not_a_home():
    page = _page("vva_page1.html")
    project = _page("vva_project.html")
    assert "van € 1.445,- tot € 1.545,-" in project
    html = page.replace('<div class="sys-page-of-properties object_list objectgrid__list gallery">',
                        '<div class="sys-page-of-properties object_list objectgrid__list gallery">' + project, 1)
    assert [l.source_id for l in parse_page(html)] == [l.source_id for l in parse_page(page)]
    # also without the project class the range itself is enough
    html2 = html.replace("object project ", "object ")
    assert len(parse_page(html2)) == 4


def test_honestly_empty_page():
    assert parse_page(_page("vva_empty.html")) == []                                    # "Geen objecten gevonden"


@pytest.mark.parametrize("html", [
    "",
    "<html><body>x</body></html>",
    '<button class="sys-property-count">Resultaten</button><div class="sys-page-of-properties"></div>',
    '<button class="sys-property-count">12 objecten gevonden</button><div class="sys-page-of-properties"></div>',
    '<button class="sys-property-count">12 objecten gevonden</button>',
])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_singular_count_text_is_understood():
    html = _page("vva_page1.html").replace("146 objecten gevonden", "1 object gevonden")
    assert len(parse_page(html)) == 4


def test_unrecognised_card_link_raises():
    html = _page("vva_page1.html").replace("/woningaanbod/huur/amsterdam/keizersgracht/ref-1155", "/aanbod/keizersgracht")
    with pytest.raises(SourceError):
        parse_page(html)


def test_price_cap_uses_the_menu_values_of_the_site():
    assert _price_cap(1600) == 1750 and _price_cap(2000) == 2000 and _price_cap(2400) == 2500


def test_fetch_is_one_request_with_the_price_filter():
    http = FakeHttp(_page("vva_amsterdam.html"))
    out = SOURCE.fetch("*", http)
    assert len(out) == 2 and len(http.urls) == 1
    assert http.urls[0] == LIST_URL + "&pricerange.maxprice=2500"
    assert "ReturnUrl" not in http.urls[0]
    assert SOURCE.per_city is False and SOURCE.cities == ("amsterdam",)
    assert [l.city for l in SOURCE.fetch("den-haag", FakeHttp(_page("vva_amsterdam.html")))] == []


def test_fetch_follows_next_link_and_keeps_the_price_filter():
    p1 = _page("vva_page1.html")
    nxt = next_page(p1, 2500)
    assert nxt == LIST_URL.replace("&orderby=9", "&orderby=9") + "&skip=16&pricerange.maxprice=2500"
    p2 = p1.replace('rel="next"', "").replace("keizersgracht/ref-1155", "x/1").replace("nieuwevaart/93", "x/2")
    http = FakeHttp({LIST_URL + "&pricerange.maxprice=2500": p1, nxt: p2})
    out = SOURCE.fetch("*", http)
    assert http.urls == [LIST_URL + "&pricerange.maxprice=2500", nxt]
    assert len(out) == 6        # 4 + 2 new: the same home on two pages is returned once


def test_fetch_never_reads_more_than_three_pages():
    p1 = _page("vva_page1.html")          # every page links to a next page
    http = FakeHttp({})
    http.get_text = lambda url, **kw: (http.urls.append(url), p1)[1]
    SOURCE.fetch("*", http)
    assert len(http.urls) == 3


def test_fetch_refuses_a_next_page_that_is_another_search():
    http = FakeHttp({LIST_URL + "&pricerange.maxprice=2500": _page("vva_page1.html"),
                     next_page(_page("vva_page1.html"), 2500): _page("vva_amsterdam.html")})
    with pytest.raises(SourcePartial) as err:         # page 1 was fine: its homes travel with the error
        SOURCE.fetch("*", http)
    assert len(err.value.listings) == 4


def test_fetch_error_on_the_first_page_is_a_plain_error():
    with pytest.raises(SourceError) as err:
        SOURCE.fetch("*", FakeHttp("<html></html>"))
    assert not isinstance(err.value, SourcePartial)


def _detail(**kw):
    listing = Listing("vva", "amsterdam/burgemeester-hogguerstraat/395-g",
                      "https://www.vva.amsterdam/woningaanbod/huur/amsterdam/burgemeester-hogguerstraat/395-g", "x", "amsterdam", **kw)
    http = FakeHttp(_page("vva_detail_395g.html"))
    out = SOURCE.fetch_details(listing, http)
    assert http.urls == [listing.url]
    return out


def test_detail_description_and_rows():
    l = _detail(price_total=2000)
    assert l.rooms == 2 and l.bedrooms == 1 and l.area_m2 == 42       # "2 (waarvan 1 slaapkamer)", "42 m²"
    assert l.posted_at == "2026-11-05" and l.available_from is None   # "In overleg": no date
    assert l.property_type == "apartment"
    assert l.description.startswith("ENGLISH TEXT BELOW\nAVAILABLE FOR STUDENTS ONLY!\nComfortabel wonen")
    assert "- Uitsluitend bedoeld voor studenten." in l.description.split("\n")      # the listing's own lines
    assert l.description.endswith("- Exclusively available to students.")
    assert "Lees de volledige omschrijving" not in l.description
    assert l.extra == {}                                               # no cost or tenancy field on this page


def test_detail_fills_what_the_card_lacked():
    l = _detail()
    assert l.price_total == 2000


@pytest.mark.parametrize("cut", [
    lambda h: h.replace('id="objectdescription"', 'id="x"'),
    lambda h: h.replace('id="objectfeatures"', 'id="y"'),
    lambda h: h.replace("<dl", "<div").replace("</dl>", "</div>"),
])
def test_detail_layout_change_raises(cut):
    with pytest.raises(SourceError):
        SOURCE.fetch_details(Listing("vva", "a/b", "u", "x", "amsterdam"), FakeHttp(cut(_page("vva_detail_395g.html"))))
