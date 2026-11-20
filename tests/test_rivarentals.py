from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceBlocked, SourceError, SourcePartial
from hunter.sources.rivarentals import SOURCE, apply_details, parse_page

FIX = Path(__file__).parent / "fixtures"


def _fx(name):
    return (FIX / name).read_text(encoding="utf-8")


class FakeHttp:
    def __init__(self, pages, fail=None):
        self.pages = pages
        self.urls = []
        self.fail = fail or {}

    def get_text(self, url, **kw):
        self.urls.append(url)
        if url in self.fail:
            raise self.fail[url]
        return self.pages[url]


def _http(**kw):
    return FakeHttp({"https://rivarentals.nl/aanbod/": _fx("rivarentals_page1.html"),
                     "https://rivarentals.nl/aanbod/page/2/": _fx("rivarentals_page2.html"),
                     "https://rivarentals.nl/aanbod/page/3/": _fx("rivarentals_page2.html")}, **kw)


def test_page_one_cards_and_exact_fields():
    out, last = parse_page(_fx("rivarentals_page1.html"))
    assert last == 5
    # 12 cards: two parking places in Nieuwerkerk aan den IJssel are in another town
    assert len(out) == 10 and {l.city for l in out} == {"rotterdam"}
    by = {l.source_id: l for l in out}
    p = by["piet-van-de-polsingel-rotterdam-h178262174"]
    assert p.uid == "rivarentals:piet-van-de-polsingel-rotterdam-h178262174"
    assert p.url == "https://rivarentals.nl/object/piet-van-de-polsingel-rotterdam-h178262174/"
    assert (p.price_total, p.bedrooms, p.area_m2) == (3195, 4, 130)
    assert p.address == "Piet van de Polsingel" and p.title == "Piet van de Polsingel, Rotterdam"
    assert p.rooms is None and p.postcode is None and p.property_type is None
    assert p.image_url.startswith("https://pararius-office-prod.global.ssl.fastly.net/")
    r = by["rosestraat-rotterdam-h178262248"]
    assert (r.price_total, r.bedrooms, r.area_m2) == (2150, 1, 70)
    # "Hoogvliet rotterdam" is a district of Rotterdam
    g = by["goede-verwachtinghof-hoogvliet-rotterdam-h178262098"]
    assert (g.city, g.price_total, g.bedrooms, g.area_m2) == ("rotterdam", 2495, 3, 106)


def test_page_two_drops_other_towns():
    out, last = parse_page(_fx("rivarentals_page2.html"))
    assert len(out) == 11 and "vaartweg-vlaardingen-h178262242" not in {l.source_id for l in out}
    assert [l.price_total for l in out][:3] == [2650, 2500, 2150]


def test_rented_cards_are_left_out():
    html = _fx("rivarentals_page1.html").replace("status-beschikbaar-forrent", "status-verhuurd-forrent", 3)
    out, _ = parse_page(html)
    assert len(out) == 7 and "henegouwerlaan-rotterdam-h178262250" not in {l.source_id for l in out}


def test_fetch_reads_pages_up_to_the_limit():
    http = _http()
    assert SOURCE.per_city is False
    out = SOURCE.fetch("*", http)
    assert http.urls == ["https://rivarentals.nl/aanbod/", "https://rivarentals.nl/aanbod/page/2/",
                         "https://rivarentals.nl/aanbod/page/3/"]       # 5 pages exist; the run stops at 3
    assert len(out) == 10 + 11 + 11
    assert SOURCE.fetch("delft", _http()) == []


def test_single_page_is_one_request():
    html = _fx("rivarentals_page1.html").replace('<a class="page-numbers"', '<a class="x"')
    html = html.replace('<span aria-current="page" class="page-numbers current">1</span>', "")
    http = FakeHttp({"https://rivarentals.nl/aanbod/": html})
    assert len(SOURCE.fetch("rotterdam", http)) == 10 and len(http.urls) == 1


def test_later_page_failure_keeps_the_first_page():
    http = _http(fail={"https://rivarentals.nl/aanbod/page/2/": SourceBlocked("429")})
    with pytest.raises(SourcePartial) as e:
        SOURCE.fetch("*", http)
    assert len(e.value.listings) == 10 and e.value.blocked is True


def _card(sid):
    return {l.source_id: l for l in parse_page(_fx("rivarentals_page1.html"))[0]}[sid]


def test_detail_house():
    l = _card("piet-van-de-polsingel-rotterdam-h178262174")
    http = FakeHttp({l.url: _fx("rivarentals_detail_house.html")})
    l = SOURCE.fetch_details(l, http)
    assert http.urls == [l.url]
    assert (l.property_type, l.furnished, l.bedrooms, l.area_m2, l.available_from) == ("house", "upholstered", 4, 130, "2026-12-08")
    lines = l.description.split("\n")
    assert "Geschikt voor maximaal 2 woningdelers" in lines and "Inkomenseis: minimaal 3× de maandhuur (bruto)" in lines
    assert "Huisdieren niet toegestaan" in lines
    assert l.price_total == 3195


def test_detail_apartment():
    l = _card("willem-beukelszstraat-rotterdam-h178260122")
    l = apply_details(l, _fx("rivarentals_detail_apartment.html"))
    assert l.property_type == "apartment" and l.bedrooms == 1 and l.area_m2 == 38 and l.price_total == 1350


def test_detail_parking_place_gets_its_type():
    out, _ = parse_page(_fx("rivarentals_page1.html").replace("Nieuwerkerk aan den ijssel", "Rotterdam"))
    l = [x for x in out if x.source_id.startswith("verzetsstraat")][0]
    assert (l.price_total, l.bedrooms, l.area_m2) == (200, 0, None)     # the card's "0 m²" is no area
    l = apply_details(l, _fx("rivarentals_detail_parking.html"))
    assert l.property_type == "parking" and l.furnished == "unfurnished"


def test_detail_of_another_page_or_unknown_layout_raises():
    l = _card("rosestraat-rotterdam-h178262248")
    with pytest.raises(SourceError):
        apply_details(l, _fx("rivarentals_detail_house.html"))
    with pytest.raises(SourceError):
        apply_details(l, "<html><body><p>x</p></body></html>")


@pytest.mark.parametrize("html", ["", "<html><body>x</body></html>",
                                  '<div class="wtfy-pararius-grid"><a href="/object/x-h1/"><p>x</p></a></div>'])
def test_unrecognised_page_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_card_without_status_is_a_layout_change():
    with pytest.raises(SourceError):
        parse_page(_fx("rivarentals_page1.html").replace("status-beschikbaar-forrent", "x"))


def test_honestly_empty_grid():
    assert parse_page('<html><body><div class="wtfy-pararius-grid"></div></body></html>') == ([], 1)
