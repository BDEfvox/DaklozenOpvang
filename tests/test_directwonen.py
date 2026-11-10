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
    assert [l.source_id for l in out] == ["523826", "523748", out[2].source_id, out[3].source_id]  # Rijswijk dropped
    a = out[0]
    assert a.uid == "directwonen:523826"
    assert a.url == "https://directwonen.nl/huurwoningen-huren/den-haag/norenburg/appartement-523826"  # not the payment page
    assert a.price_total == 1080 and a.rooms == 2 and a.area_m2 == 55
    assert a.property_type == "apartment" and a.price_includes_utilities is False
    assert a.address == "Norenburg" and a.available_from == "2026-12-01"
    house = out[1]
    assert house.property_type == "house" and house.rooms == 6 and house.price_total == 4500
    assert any(l.property_type == "room" for l in out)
    assert any(l.price_includes_utilities is True for l in out)


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
