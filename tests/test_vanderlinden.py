from __future__ import annotations

from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.vanderlinden import SOURCE, parse_page

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_parse_skips_senior_option_and_duplicates():
    html = (FIX / "vanderlinden.html").read_text()
    out = parse_page(html)
    ids = [l.source_id for l in out]
    # Ittmannserf (Nunspeet, senior) and Bottelarijstraat (onder optie) are dropped; duplicate card merged
    assert len(ids) == len(set(ids)) and "2571" not in ids
    ams = [l for l in out if l.city == "amsterdam"]
    assert {l.source_id for l in ams} == {"2579", "2612"}
    a = [l for l in ams if l.source_id == "2579"][0]
    assert a.uid == "vanderlinden:2579" and a.price_total == 1550 and a.rooms == 3 and a.bedrooms is None and a.area_m2 == 77
    assert a.url == "https://www.vanderlinden.nl/huurwoning/Bert-Haanstrakade-552-Amsterdam/2579/"
    assert a.address == "Bert Haanstrakade 552"
    assert a.image_url.startswith("https://www.vanderlinden.nl/media_huur/")


def test_fetch_filters_by_city_and_uses_one_request():
    html = (FIX / "vanderlinden.html").read_text()
    http = FakeHttp(html)
    assert SOURCE.per_city is False
    out = SOURCE.fetch("*", http)
    assert len(http.urls) == 1
    assert {l.city for l in out} == {"amsterdam", "den-haag"}
    assert [l.city for l in SOURCE.fetch("den-haag", FakeHttp(html))] == ["den-haag"]


@pytest.mark.parametrize("html", ["", "<html>x</html>", '<div id="zoekresultaten"></div>'])
def test_layout_change_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_detail_per_person_price_becomes_total():
    from hunter.models import Listing
    from hunter.sources.vanderlinden import apply_details
    html = ("<html><body><nav>menu</nav><div>Woon oppervlakte 72 m² Aantal kamers 3</div>"
            "<div>Over de woning Speciaal ontwikkeld voor woningdelers. Huren vanaf €1.682,- all-in, bestaande uit; "
            "Gemiddelde kale huurprijs €841,- per persoon</div></body></html>")
    l = Listing("vanderlinden", "2627", "https://www.vanderlinden.nl/huurwoning/x/2627/", "x", "amsterdam",
                price_total=841, rooms=3, area_m2=72)
    l = apply_details(l, html)
    assert l.price_total == 1682 and l.extra["price_per_person"] == 841 and l.rooms == 3
    assert "woningdelers" in l.description
    # without an all-in total: per person x bedrooms (rooms - 1)
    l2 = Listing("vanderlinden", "1", "u", "x", "amsterdam", price_total=887, rooms=4)
    l2 = apply_details(l2, html.replace("Huren vanaf €1.682,- all-in, bestaande uit; ", "").replace("841", "887").replace("kamers 3", "kamers 4"))
    assert l2.price_total == 887 * 3
