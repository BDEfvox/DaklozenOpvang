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
    assert out.extra["service_costs"] == 72 and out.extra["service_costs_on_top"] is True


@pytest.mark.parametrize("html", ["", "<html>x</html>", '<div id="hose-object-search-result-card-list-wrapper"></div>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def _detail(tag, **kw):
    l = Listing(source="heimstaden", source_id="x", url="https://heimstaden.com/nl/huurwoningen/x/", title="x", city="den-haag", **kw)
    return SOURCE.fetch_details(l, FakeHttp((FIX / ("heimstaden_detail_%s.html" % tag)).read_text()))


def test_postcode_from_json_ld():
    assert _detail("direct_denhaag").postcode == "2571AD"
    assert _detail("dated_rotterdam").postcode == "3059RB"


def test_direct_has_no_date_even_though_json_ld_has_one():
    # page: "Beschikbaar: Direct" next to JSON-LD "availabilityStarts": "2026-11-08" (stale)
    out = _detail("direct_denhaag")
    assert '"availabilityStarts": "2026-11-08"' in (FIX / "heimstaden_detail_direct_denhaag.html").read_text()
    assert out.available_from is None


def test_available_from_from_facts_block_and_card():
    assert _detail("dated_rotterdam").available_from == "2026-12-01"  # "Beschikbaar: 1 december, 2026"
    assert all(l.available_from is None for l in parse_page((FIX / "heimstaden.html").read_text()))  # cards say "Direct"


def test_card_date_is_read():
    html = (FIX / "heimstaden.html").read_text()
    html = html.replace("Beschikbaar Direct", "Beschikbaar 1 december, 2026")
    out = parse_page(html)
    assert out and all(l.available_from == "2026-12-01" for l in out)


def test_utilities_exclusive():
    # "Huurprijs per maand: € 1.800 – exclusief g/w/l."
    assert _detail("direct_denhaag").price_includes_utilities is False


def test_utilities_inclusive_and_unstated():
    html = (FIX / "heimstaden_detail_direct_denhaag.html").read_text().replace("exclusief g/w/l", "inclusief g/w/l")
    l = Listing(source="heimstaden", source_id="x", url="https://heimstaden.com/nl/huurwoningen/x/", title="x", city="den-haag")
    assert SOURCE.fetch_details(l, FakeHttp(html)).price_includes_utilities is True
    assert _detail("dated_rotterdam").price_includes_utilities is None  # the Rotterdam page does not say


def test_full_description_is_kept():
    out = _detail("age_amsterdam")
    # the intro is 4453 characters; the old 4000 character cut lost the end
    assert len(out.description) > 4400
    assert out.description.rstrip().endswith("ontleend.") or "Wij adviseren online te reageren" in out.description
