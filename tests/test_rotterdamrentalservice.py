from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from hunter.sources import rotterdamrentalservice as rrs
from hunter.sources.base import SourceBlocked, SourceError, SourcePartial
from hunter.sources.rotterdamrentalservice import SOURCE, apply_details, listing_from_doc, parse_api_page, parse_page

FIX = Path(__file__).parent / "fixtures"


def _fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def _json(name):
    return json.loads(_fx(name))


class FakeHttp:
    def __init__(self, html, pages=None, fail_at=None, fail_with=None):
        self.html = html
        self.pages = pages or {}
        self.urls = []
        self.fail_at = fail_at
        self.fail_with = fail_with

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.html

    def get_json(self, url, **kw):
        self.urls.append(url)
        n = int(re.search(r"[?&]page=(\d+)", url).group(1))
        if n == self.fail_at:
            raise self.fail_with
        return self.pages[n] if n in self.pages else copy.deepcopy(self.pages[max(self.pages)])


def _http():
    return FakeHttp(_fx("rotterdamrentalservice_aanbod.html"),
                    {2: _json("rotterdamrentalservice_api_p2.json"), 3: _json("rotterdamrentalservice_api_p3.json")})


def test_first_page_reads_the_embedded_listings_and_drops_other_towns():
    out, api_url, data = parse_page(_fx("rotterdamrentalservice_aanbod.html"))
    # six records: Buizerdhof 423 is in Capelle aan den IJssel, which is not one of the five cities
    assert len(data["docs"]) == 6 and len(out) == 5 and {l.city for l in out} == {"rotterdam"}
    assert data["hasNextPage"] is True and data["totalDocs"] == 15
    assert "page=1&limit=6" in api_url
    by = {l.title: l for l in out}
    b = by["Baan 34-A17"]
    assert b.uid == "rotterdamrentalservice:6afc5bdf8e1312859eea11ba"
    assert b.url == "https://rotterdamrentalservice.com/detail/baan-34-a17/6afc5bdf8e1312859eea11ba"
    assert (b.price_total, b.bedrooms, b.rooms, b.area_m2) == (2995, 2, 3, 87)
    assert (b.property_type, b.furnished, b.available_from, b.postcode) == ("apartment", "furnished", "2026-11-23", "3011CB")
    assert b.address == "Baan 34-A17" and b.image_url.startswith("https://media02.ogonline.nl/")
    j = by["Jan Sonjéstraat 25"]       # rooms 2 is "kamers", bedrooms 1 is "slaapkamers": both as the site states
    assert (j.price_total, j.bedrooms, j.rooms, j.area_m2, j.available_from) == (1900, 1, 2, 100, "2026-12-01")
    assert j.url.endswith("/detail/jan-sonjestraat-25/6afd91498e1312859efe5221")      # the site's own slug
    n = by["Nieuwe Binnenweg 243-A02"]
    assert n.furnished == "upholstered" and n.price_total == 1225 and n.bedrooms == 1
    s = by["Siciliëboulevard 122"]
    assert s.property_type == "house" and s.bedrooms == 5 and s.rooms == 6 and s.url.split("/")[-2] == "sicilieboulevard-122"
    assert by["Jan Sonjéstraat 25-A"].available_from is None       # "directly": no date stated


def test_page_decoded_as_latin1_gives_the_same_listings():
    html = _fx("rotterdamrentalservice_aanbod.html")
    wrong = html.encode("utf-8").decode("latin-1")        # what requests does with text/html without a charset
    assert "SiciliÃ«" in wrong
    assert [l.to_dict() for l in parse_page(wrong)[0]] == [l.to_dict() for l in parse_page(html)[0]]


def test_fetch_follows_the_pages_own_address_for_pages_two_and_three():
    http = _http()
    assert SOURCE.per_city is False
    out = SOURCE.fetch("*", http)
    assert len(http.urls) == 3 and http.urls[0] == "https://rotterdamrentalservice.com/aanbod"
    assert "page=2&limit=6" in http.urls[1] and "page=3&limit=6" in http.urls[2]
    assert all(u.startswith("https://cpl01.ogonline.nl/api/listings?") for u in http.urls[1:])
    # 5 + 5 (Goudmijn is in Rhoon) + 2 (Hilde erf is in Capelle)
    assert len(out) == 12 and len({l.source_id for l in out}) == 12
    assert {l.city for l in out} == {"rotterdam"}
    assert "Calandstraat 35" in {l.title for l in out} and "Goudmijn 24" not in {l.title for l in out}
    assert len(SOURCE.fetch("delft", _http())) == 0


def test_fetch_stops_at_the_page_limit():
    pages = _json("rotterdamrentalservice_api_p2.json")
    pages["hasNextPage"] = True
    http = FakeHttp(_fx("rotterdamrentalservice_aanbod.html"), {2: pages})
    SOURCE.fetch("*", http)
    assert len(http.urls) == rrs.MAX_PAGES == 3   # the page + pages 2 and 3, however many the site claims
    deep = type(SOURCE)()
    deep.baseline = True                          # its first run (and one run an hour) reads on
    http = FakeHttp(_fx("rotterdamrentalservice_aanbod.html"), {2: pages})
    deep.fetch("*", http)
    assert len(http.urls) == rrs.DEEP_PAGES == 6


def test_later_page_failure_keeps_what_was_read():
    http = _http()
    http.fail_at, http.fail_with = 2, SourceError("HTTP 500")
    with pytest.raises(SourcePartial) as e:
        SOURCE.fetch("*", http)
    assert len(e.value.listings) == 5 and e.value.blocked is False
    http = _http()
    http.fail_at, http.fail_with = 3, SourceBlocked("429")
    with pytest.raises(SourcePartial) as e:
        SOURCE.fetch("*", http)
    assert len(e.value.listings) == 10 and e.value.blocked is True


def _doc(**over):
    doc = copy.deepcopy(_json("rotterdamrentalservice_api_p2.json")["docs"][0])
    doc.update(over)
    return doc


def test_taken_homes_are_left_out():
    assert listing_from_doc(_doc()) is not None
    assert listing_from_doc(_doc(status="expected")) is not None
    for status in ("under_option", "under_bid", "rented"):
        assert listing_from_doc(_doc(status=status)) is None
    doc = _doc()
    doc["consumer"]["isApartment"] = doc["consumer"]["isHouse"] = False
    assert listing_from_doc(doc) is None            # neither an apartment nor a house
    doc = _doc()
    doc["web"]["types"] = [{"title": "Kamer"}]
    assert listing_from_doc(doc).property_type == "room"


def test_api_pages_two_and_three():
    p2 = parse_api_page(_json("rotterdamrentalservice_api_p2.json"))
    c = [l for l in p2 if l.title == "Calandstraat 35"][0]
    assert (c.price_total, c.bedrooms, c.rooms, c.area_m2, c.postcode, c.property_type) == (2495, 1, 2, 58, "3016CA", "apartment")
    assert c.image_url.startswith("https://pararius-office-prod")        # no card for it: the record's first photo
    assert [l.title for l in parse_api_page(_json("rotterdamrentalservice_api_p3.json"))] == \
        ["Prins Frederikplein 206", "Van Vollenhovenstraat 38-1A"]


def test_detail_page_gives_the_description_with_its_lines():
    l = [x for x in parse_page(_fx("rotterdamrentalservice_aanbod.html"))[0] if x.title == "Jan Sonjéstraat 25"][0]
    http = FakeHttp(_fx("rotterdamrentalservice_detail.html"))
    l = SOURCE.fetch_details(l, http)
    assert http.urls == [l.url]
    lines = l.description.split("\n")
    assert lines[0].startswith("SHORT STAY - MAX 6 MONTHS")
    assert any(x.startswith("Pets:") and "Not permitted" in x for x in lines)
    assert (l.bedrooms, l.rooms, l.price_total, l.available_from) == (1, 2, 1900, "2026-12-01")


def test_detail_of_another_home_or_without_listing_raises():
    l = [x for x in parse_page(_fx("rotterdamrentalservice_aanbod.html"))[0] if x.title == "Baan 34-A17"][0]
    with pytest.raises(SourceError):
        apply_details(l, _fx("rotterdamrentalservice_detail.html"))
    with pytest.raises(SourceError):
        apply_details(l, "<html><body>nothing</body></html>")


@pytest.mark.parametrize("html", ["", "<html><body>x</body></html>"])
def test_unrecognised_page_raises(html):
    with pytest.raises(SourceError):
        parse_page(html)


def test_embedded_data_that_is_not_listings_raises():
    html = ('<script data-sveltekit-fetched data-url="https://cpl01.ogonline.nl/api/listings?page=1&amp;limit=6">'
            '{"status":200,"body":"{\\"hello\\":1}"}</script>')
    with pytest.raises(SourceError):
        parse_page(html)


def test_honestly_empty_offer():
    html = ('<script data-sveltekit-fetched data-url="https://cpl01.ogonline.nl/api/listings?page=1&amp;limit=6">'
            '{"status":200,"body":"{\\"docs\\":[],\\"totalDocs\\":0,\\"hasNextPage\\":false,\\"page\\":1}"}</script>')
    http = FakeHttp(html)
    assert SOURCE.fetch("*", http) == [] and len(http.urls) == 1


def test_api_address_must_stay_on_the_cms_host():
    with pytest.raises(SourceError):
        rrs._page_url("https://evil.example/api/listings?page=1&limit=6", 2)
    assert rrs._page_url("https://cpl01.ogonline.nl/api/listings?page=1&limit=6", 3).endswith("?page=3&limit=6")
