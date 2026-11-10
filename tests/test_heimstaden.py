from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.heimstaden import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_only_our_cities():
    html = (FIX / "heimstaden.html").read_text()
    http = FakeHttp(html)
    out = SOURCE.fetch("*", http)
    assert len(http.urls) == 1
    assert len(out) == 1  # the two Breda cards are dropped
    l = out[0]
    assert l.uid == "heimstaden:86a91d40-f0a5-4d2b-abeb-5c231fd3e480" and l.city == "den-haag"
    assert l.price_total == 1800 and l.rooms == 3 and l.bedrooms is None and l.area_m2 == 90
    assert l.address == "Loosduinseweg 239" and l.property_type == "apartment"
    assert SOURCE.fetch("amsterdam", FakeHttp(html)) == []


def test_fetch_details():
    l = Listing(source="heimstaden", source_id="x", url="https://heimstaden.com/nl/huurwoningen/x/", title="x", city="den-haag")
    out = SOURCE.fetch_details(l, FakeHttp((FIX / "heimstaden_detail.html").read_text()))
    assert out.rooms == 3 and out.bedrooms == 2 and "3-kamer appartement" in out.description
    assert out.extra["service_costs"] == 72


@pytest.mark.parametrize("html", ["", "<html>x</html>", '<div id="hose-object-search-result-card-list-wrapper"></div>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)
