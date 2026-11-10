from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.sources.base import SourceError
from hunter.sources.vesteda import SOURCE

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def post_json(self, url, payload, **kw):
        self.calls.append((url, payload))
        return self.data


def test_parse_keeps_open_units_in_city():
    data = json.loads((FIX / "vesteda.json").read_text())
    http = FakeHttp(data)
    out = SOURCE.fetch("leiden", http)
    assert len(http.calls) == 1
    assert http.calls[0][1]["priceTo"] == 2400
    # status 3 (rented with reservation) and the Leiderdorp unit are dropped
    assert [l.source_id for l in out] == ["8695", "7562"]
    first = out[0]
    assert first.uid == "vesteda:8695"
    assert first.city == "leiden" and first.price_total == 1670 and first.bedrooms == 2
    assert first.area_m2 == 75 and first.property_type == "apartment"
    assert first.url.startswith("https://www.vesteda.com/nl/huurwoningen-leiden/")
    assert first.address == "Betaplein 38" and first.postcode == "2321KS"


def test_layout_change_raises():
    for bad in ({}, {"results": {}}, {"results": {"objects": "x"}}, []):
        with pytest.raises(SourceError):
            SOURCE.parse(bad, "leiden")


def test_empty_is_fine():
    assert SOURCE.parse({"count": 0, "results": {"objects": [], "complexes": []}}, "leiden") == []
