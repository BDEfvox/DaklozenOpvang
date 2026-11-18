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
