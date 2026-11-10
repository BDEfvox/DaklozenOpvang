from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.wonen123 import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_skips_rented():
    html = (FIX / "wonen123.html").read_text()
    http = FakeHttp(html)
    out = SOURCE.fetch("amsterdam", http)
    assert http.urls == ["https://www.123wonen.nl/huurwoningen/in/amsterdam/sort/newest"]
    assert len(out) == 3  # fourth card is "Verhuurd"
    first = out[0]
    assert first.uid.startswith("123wonen:") and first.source_id == "3632-14"
    assert first.city == "amsterdam" and first.price_total == 2500 and first.bedrooms == 1
    assert first.area_m2 == 69 and first.property_type == "apartment"
    assert first.available_from == "2026-12-01" and first.posted_at == "2026-11-05"
    assert first.url.startswith("https://www.123wonen.nl/huur/amsterdam/")
    assert first.address == "Rokin"


def test_other_town_is_dropped():
    html = (FIX / "wonen123.html").read_text()
    assert parse_page(html, "leiden") == []


def test_zero_results_is_empty_list():
    html = '<div class="mzlistcontrols"><div>Gevonden <i>0</i></div></div>'
    assert parse_page(html, "den-haag") == []


@pytest.mark.parametrize("html", ["<html><body>Welkom</body></html>", "",
                                  '<div>Gevonden <i>5</i></div><p>geen kaarten</p>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html, "amsterdam")
