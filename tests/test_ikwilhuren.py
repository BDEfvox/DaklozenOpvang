from __future__ import annotations

from pathlib import Path

import pytest

from hunter.models import Listing
from hunter.sources.base import SourceError
from hunter.sources.ikwilhuren import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_page_skips_rented_keeps_all_towns():
    out = parse_page((FIX / "ikwilhuren.html").read_text())
    # Utrecht is not one of our cities and the fifth card is "Verhuurd"
    assert {l.address for l in out} == {"Batteryparkstraat 3", "Schedeldoekshaven 320", "Eosstraat 143"}
    by = {l.address: l for l in out}
    a = by["Batteryparkstraat 3"]
    assert a.city == "amsterdam" and a.postcode == "1031KH" and a.price_total == 1733
    assert a.bedrooms == 1 and a.area_m2 == 68 and a.property_type == "apartment"
    assert len(a.source_id) == 32 and a.uid == "ikwilhuren:" + a.source_id
    assert a.url.startswith("https://ikwilhuren.nu/object/amsterdam-1031kh-3-batteryparkstraat-")
    assert a.image_url.startswith("https://")
    assert by["Schedeldoekshaven 320"].city == "den-haag" and by["Schedeldoekshaven 320"].bedrooms == 2


def test_fetch_reads_three_pages_and_filters_city():
    http = FakeHttp((FIX / "ikwilhuren.html").read_text())
    out = SOURCE.fetch("den-haag", http)
    assert len(http.urls) == 3 and "sort=aanbodDESC" in http.urls[0] and http.urls[1].endswith("&page=2")
    assert [l.city for l in out] == ["den-haag"]  # same page thrice: de-duplicated
    allc = SOURCE.fetch("*", FakeHttp((FIX / "ikwilhuren.html").read_text()))
    assert {l.city for l in allc} == {"amsterdam", "den-haag"}


def test_fetch_details():
    l = Listing(source="ikwilhuren", source_id="x", url="https://ikwilhuren.nu/object/x/", title="x", city="amsterdam")
    out = SOURCE.fetch_details(l, FakeHttp((FIX / "ikwilhuren_detail.html").read_text()))
    assert out.description and "Amstelveenseweg" in out.description
    assert out.extra["service_costs"] == 115


@pytest.mark.parametrize("html", ["", "<html>x</html>"])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)
