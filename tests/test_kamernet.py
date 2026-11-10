from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.kamernet import SOURCE, apply_details, parse_search

FIX = Path(__file__).parent / "fixtures"


def page(listings, total=None):
    data = {"props": {"pageProps": {"targetPageProps": {"findListingsResponse": {
        "listings": listings, "total": len(listings) if total is None else total}}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">%s</script></html>' % json.dumps(data)


def test_parse_search_keeps_only_city_apartments():
    items = json.loads((FIX / "kamernet_listings.json").read_text())
    out = parse_search(page(items), "den-haag")
    assert out, "expected apartments"
    assert all(l.city == "den-haag" and l.property_type == "apartment" for l in out)
    assert len(out) == 3  # the room and the other-city item are dropped
    first = out[0]
    assert first.source == "kamernet" and first.uid == "kamernet:" + first.source_id
    assert first.url.startswith("https://kamernet.nl/huren/appartement-den-haag/")
    assert "displayroomadvert" not in first.url
    assert first.url.endswith("appartement-" + first.source_id)
    assert isinstance(first.price_total, int) and first.area_m2
    assert first.furnished in ("furnished", "upholstered", "unfurnished", None)


def test_empty_results_are_not_an_error():
    assert parse_search(page(None, total=0), "leiden") == []
    assert parse_search(page([], total=0), "leiden") == []


@pytest.mark.parametrize("html", [
    "<html>nothing</html>",
    '<script id="__NEXT_DATA__">{"props":{}}</script>',
    '<script id="__NEXT_DATA__">not json</script>',
    page(None, total=5),
])
def test_layout_changes_raise(html):
    with pytest.raises(SourceError, match="layout changed"):
        parse_search(html, "den-haag")


def test_details_fill_bedrooms_and_description():
    items = json.loads((FIX / "kamernet_listings.json").read_text())
    lst = parse_search(page(items), "den-haag")[0]
    det = json.loads((FIX / "kamernet_detail.json").read_text())
    data = {"props": {"pageProps": {"targetPageProps": {"listingDetails": det}}}}
    html = '<script id="__NEXT_DATA__">%s</script>' % json.dumps(data)
    lst = apply_details(lst, html)
    assert lst.bedrooms == det["numOfBedrooms"]
    assert lst.description and "bedroom" in lst.description
    assert lst.postcode == det["postalCode"].replace(" ", "")
    with pytest.raises(SourceError):
        apply_details(lst, "<html></html>")


def test_source_metadata():
    assert SOURCE.name == "kamernet" and SOURCE.per_city
    assert SOURCE.search_url("amsterdam") == "https://kamernet.nl/huren/appartement-amsterdam?radius=1"


def test_shared_facilities_make_it_a_room_and_persons_flag():
    import json as _json
    from hunter.config import load_config
    from hunter.filters import evaluate
    from hunter.models import Listing
    from hunter.sources.kamernet import apply_details
    det = {"numOfBedrooms": None, "numOfRooms": None, "kitchenId": 1, "livingRoomId": 1, "showerId": 1,
           "toiletId": 1, "suitableForNumberOfPersons": 1, "dutchTitle": "Room with registration"}
    html = ('<script id="__NEXT_DATA__" type="application/json">%s</script>'
            % _json.dumps({"props": {"pageProps": {"targetPageProps": {"listingDetails": det}}}}))
    l = Listing("kamernet", "1", "https://kamernet.nl/huren/appartement-amsterdam/x/appartement-1", "t",
                "amsterdam", price_total=890, area_m2=130, property_type="apartment")
    l = apply_details(l, html)
    assert l.property_type == "room" and l.extra["max_persons"] == 1
    assert not evaluate(l, load_config()).matched
    det.update(kitchenId=2, livingRoomId=2, suitableForNumberOfPersons=2, numOfBedrooms=2)
    html2 = ('<script id="__NEXT_DATA__" type="application/json">%s</script>'
             % _json.dumps({"props": {"pageProps": {"targetPageProps": {"listingDetails": det}}}}))
    l2 = apply_details(Listing("kamernet", "2", "https://kamernet.nl/huren/appartement-amsterdam/x/appartement-2",
                               "t", "amsterdam", price_total=1500, area_m2=70, property_type="apartment"), html2)
    r = evaluate(l2, load_config())
    assert "max_persons:2" in r.flags and [m.persons for m in r.matches] == [2]
