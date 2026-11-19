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
    # the default order is newest first; ?sort=aanbodDESC is not (checked live, see the module docstring)
    assert http.urls == ["https://ikwilhuren.nu/aanbod/", "https://ikwilhuren.nu/aanbod/?page=2", "https://ikwilhuren.nu/aanbod/?page=3"]
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


def _details(name, **kw):
    base = dict(source="ikwilhuren", source_id="x", url="https://ikwilhuren.nu/object/x/", title="x", city="den-haag")
    base.update(kw)
    return SOURCE.fetch_details(Listing(**base), FakeHttp((FIX / name).read_text()))


def test_kenmerken_table_overrides_the_card():
    # card said 54 m2 / 2 bedrooms; the table: "Woonoppervlakte 53 m 2", "Slaapkamers 2", "Servicekosten € 70,- /mnd"
    out = _details("ikwilhuren_detail_schedeldoekshaven_320.html", area_m2=54, bedrooms=1)
    assert out.area_m2 == 53 and out.bedrooms == 2 and out.extra["service_costs"] == 70
    assert out.available_from is None  # "Beschikbaar vanaf Per direct" is no date
    assert out.extra["availability"] == "Per direct"


def test_kenmerken_available_from_date():
    out = _details("ikwilhuren_detail_randveen_82.html", area_m2=61)
    assert out.available_from == "2027-04-01" and out.area_m2 == 60 and out.bedrooms == 1
    assert out.extra["service_costs"] == 85


def test_card_without_kenmerken_rows_keeps_card_values():
    html = "<html><body><h2>Omschrijving</h2><p>Mooi huis.</p><p>Gebruik van deze site</p></body></html>"
    l = Listing(source="ikwilhuren", source_id="x", url="u", title="x", city="den-haag", area_m2=50, bedrooms=2)
    out = SOURCE.fetch_details(l, FakeHttp(html))
    assert out.area_m2 == 50 and out.bedrooms == 2 and "service_costs" not in out.extra


def _parking_card_page():
    # the real Utrecht card with the badge, moved to Den Haag so that the city filter keeps it
    html = (FIX / "ikwilhuren_card_parking.html").read_text().replace("3521AH Utrecht", "3521AH Den Haag")
    return html


def test_card_badge_mandatory_parking():
    out = parse_page(_parking_card_page())
    assert len(out) == 1
    l = out[0]
    assert l.price_total == 1666 and l.extra["mandatory_costs"] == 214
    assert l.extra["mandatory_costs_note"] == "verplichte afname parkeerplaats €214"


def test_parking_badge_before_status_badge_does_not_hide_verhuurd():
    # the parking badge is the FIRST badge of the card; the old code only read the first one
    html = _parking_card_page().replace("Te huur", "Verhuurd")
    assert "Verhuurd" in html and parse_page(html) == []


def test_detail_text_mandatory_parking():
    # synthetic sentence in the wording of the card badge; no saved detail page carries one
    html = ("<html><body><div class='object-description'><h2>Omschrijving</h2><p>Mooi appartement. "
            "Verplichte afname parkeerplaats \u20ac 214 per maand.</p><p>Gebruik van deze site</p></div></body></html>")
    l = Listing(source="ikwilhuren", source_id="x", url="u", title="x", city="den-haag")
    out = SOURCE.fetch_details(l, FakeHttp(html))
    assert out.extra["mandatory_costs"] == 214 and "arkeerplaats" in out.extra["mandatory_costs_note"]


def test_no_mandatory_parking_without_text():
    assert "mandatory_costs" not in _details("ikwilhuren_detail_randveen_82.html").extra
