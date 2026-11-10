from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.housinganywhere import SOURCE, apply_details, parse_search

FIX = Path(__file__).parent / "fixtures"


def page(listings):
    inner = json.dumps({"loaderData": {"0": None, "0-22": {"listings": listings}}})
    literal = json.dumps(inner)  # a JS string literal: JSON-encoded string
    return ('<html><script>window.__PRELOADED_STATE__= ({"a":undefined})</script>'
            '<script nonce="x">window.__staticRouterHydrationData = JSON.parse(%s);</script></html>' % literal)


def test_parse_drops_studios_and_other_cities():
    items = json.loads((FIX / "housinganywhere_listings.json").read_text())
    out = parse_search(page(items), "den-haag")
    assert [l.property_type for l in out] == ["house", "apartment"]
    for l in out:
        assert l.city == "den-haag" and l.source == "housinganywhere"
        assert l.url.startswith("https://housinganywhere.com/room/ut") and " " not in l.url
        assert l.source_id.startswith("ut")
        assert isinstance(l.price_total, int) and l.price_total > 1000
        assert l.furnished == "furnished"
        assert l.bedrooms is not None and l.area_m2
        assert l.price_includes_utilities in (True, False, None)


def test_empty_and_layout_errors():
    assert parse_search(page([]), "leiden") == []
    for bad in ["<html></html>", "<script>window.__staticRouterHydrationData = 5</script>",
                '<script>window.__staticRouterHydrationData = JSON.parse("{\\"loaderData\\":{}}")</script>']:
        with pytest.raises(SourceError, match="layout changed"):
            parse_search(bad, "den-haag")


def test_details_reads_description():
    html = ('<html><head><meta name="description" content="Apartment for rent in The Hague: 3 bedroom(s), WiFi"/>'
            '<meta property="twitter:description" content="Lovely home.\n\nNo sharers please."/></head></html>')
    items = json.loads((FIX / "housinganywhere_listings.json").read_text())
    lst = parse_search(page(items), "den-haag")[0]
    lst.bedrooms = None
    lst = apply_details(lst, html)
    assert lst.description == "Lovely home. No sharers please."
    assert lst.bedrooms == 3
    with pytest.raises(SourceError):
        apply_details(lst, "<html></html>")


def test_search_url():
    assert SOURCE.search_url("den-haag") == "https://housinganywhere.com/s/The-Hague--Netherlands/apartment-for-rent"
