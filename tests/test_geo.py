"""Offline geocoder contract tests: fixture responses, never a live HTTP client."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from hunter import geo
from hunter.sources.base import SourceBlocked, SourceError
from hunter.utils import CITIES

FIXTURES = Path(__file__).parent / "fixtures" / "geo"
NOW = date(2026, 11, 19)


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


class FakeHttp:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        assert urlparse(url).scheme == "https"
        assert urlparse(url).netloc == "api.pdok.nl"
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return fixture(answer) if isinstance(answer, str) else copy.deepcopy(answer)

    @property
    def queries(self):
        return [(parse_qs(urlparse(url).query)["fq"][0],
                 parse_qs(urlparse(url).query)["q"][0]) for url in self.urls]


def fields(**kw):
    value = dict(address="Cruquiusweg 85-R", postcode="1019 AT", city="amsterdam", title="Flat")
    value.update(kw)
    return value


def card(**kw):
    return {"listing": fields(**kw)}


@pytest.mark.parametrize("address,street,number", [
    ("Cruquiusweg 85-R", "Cruquiusweg", "85-R"),
    ("Sneeuwbalstraat 48", "Sneeuwbalstraat", "48"),
    ("Daltonstraat 12 II", "Daltonstraat", "12II"),
    ("Laan van Meerdervoort 1234A", "Laan van Meerdervoort", "1234A"),
    ("Prinsengracht 263 H", "Prinsengracht", "263H"),
    ("Kerkstraat 10-1", "Kerkstraat", "10-1"),                 # an Amsterdam floor number
    ("Eerste Helmersstraat 12/2", "Eerste Helmersstraat", "12/2"),
    ("2e De Riemerstraat", "2e De Riemerstraat", ""),          # a digit in the street's name is no house number
    ("Aelbrechtskade", "Aelbrechtskade", ""),
    ("2316XD Leiden", "", ""),
    ("Cruquiusweg 85-R, 1019 AT Amsterdam", "Cruquiusweg", "85-R"),
    ("Cruquiusweg 85-R, Amsterdam", "Cruquiusweg", "85-R"),
    ("Den Haag", "", ""),
    (None, "", ""),
])
def test_parse_address(address, street, number):
    result = geo.parse_address(address)
    assert result["street"] == street
    assert result["number"] == number
    if address and "2316" in address:
        assert result["postcode"] == "2316XD"


def test_parsing_explicit_postcode_and_city_alias():
    assert geo.parse_address("Sneeuwbalstraat 48", "2565 AA", "'s-Gravenhage") == {
        "street": "Sneeuwbalstraat", "number": "48", "postcode": "2565AA", "city": "den-haag"}


def test_normalised_keys_and_suffix_changes():
    a = fields(address="D’Ártagnanstraat 85-R")
    b = fields(address="d'artagnanstraat 85-r", postcode="1019at", city="Amsterdam")
    assert geo.cache_key(a) == geo.cache_key(b)
    assert geo.cache_key(a) != geo.cache_key(dict(a, address="D’Ártagnanstraat 85A"))
    assert geo.cache_key(a) != geo.cache_key(dict(a, city="delft"))


def test_postcode_number_first_and_suffix_may_differ():
    http, cache = FakeHttp("address"), {}
    result = geo.locate(fields(), http, cache, NOW)
    assert result == {"lat": 52.36845364, "lon": 4.95329264, "precision": "address"}
    assert http.queries == [("type:adres", "1019AT 85-R")]
    assert cache == {geo.cache_key(fields()): result}
    params = parse_qs(urlparse(http.urls[0]).query)
    assert params["rows"] == ["5"]
    assert "centroide_ll" in params["fl"][0]


def test_all_strategy_order_and_postcode_fallback():
    http = FakeHttp("empty", "empty", "empty", "postcode")
    assert geo.locate(fields(), http, {}, NOW)["precision"] == "postcode"
    assert http.queries == [
        ("type:adres", "1019AT 85-R"), ("type:adres", "Cruquiusweg 85-R Amsterdam"),
        ("type:weg", "Cruquiusweg Amsterdam"), ("type:postcode", "1019AT")]


def test_street_number_strategy():
    http = FakeHttp("address")
    assert geo.locate(fields(postcode=None), http, {}, NOW)["precision"] == "address"
    assert http.queries == [("type:adres", "Cruquiusweg 85-R Amsterdam")]


def test_street_strategy_and_hague_display_name():
    http = FakeHttp("empty", "street")
    result = geo.locate(fields(address="Sneeuwbalstraat 48", postcode=None, city="den-haag"), http, {}, NOW)
    assert result == {"lat": 52.07747045, "lon": 4.263762, "precision": "street"}
    assert http.queries == [("type:adres", "Sneeuwbalstraat 48 Den Haag"),
                            ("type:weg", "Sneeuwbalstraat Den Haag")]


def test_street_only():
    http = FakeHttp("street")
    assert geo.locate(fields(address="Sneeuwbalstraat", postcode=None, city="den-haag"), http, {}, NOW)
    assert http.queries == [("type:weg", "Sneeuwbalstraat Den Haag")]


def test_postcode_only():
    http = FakeHttp("postcode")
    assert geo.locate(fields(address="1019 AT Amsterdam", postcode=None), http, {}, NOW)["precision"] == "postcode"
    assert http.queries == [("type:postcode", "1019AT")]


@pytest.mark.parametrize("address", [None, "", "Amsterdam"])
def test_no_address_does_not_use_city_or_marketing_title(address):
    http = FakeHttp()
    assert geo.locate(fields(address=address, postcode=None, title="Wonderful Amsterdam flat"), http, {}, NOW) is None
    assert not http.urls


@pytest.mark.parametrize("change", [
    {"straatnaam": "Wrong street"}, {"huisnummer": 86},
    {"centroide_ll": "POINT(2.9 52.3)"}, {"centroide_ll": "POINT(4.9 54.0)"},
    {"centroide_ll": "POINT(4.3007 52.0705)"},  # The Hague, ~50+ km away
    {"postcode": "1019AB"}, {"type": "weg"},
])
def test_untrustworthy_address_rejected_next_strategy(change):
    bad = fixture("address")
    bad["response"]["docs"][0].update(change)
    http = FakeHttp(bad, "address")
    assert geo.locate(fields(), http, {}, NOW)["precision"] == "address"
    assert len(http.urls) == 2


def test_60km_wrong_city_rejected():
    http = FakeHttp("address", "empty")
    assert geo.locate(fields(postcode=None, city="rotterdam"), http, {}, NOW) is None
    assert len(http.urls) == 2


def test_wrong_street_response_rejected_and_postcode_must_match():
    bad = fixture("postcode")
    bad["response"]["docs"][0]["postcode"] = "1019AB"
    http = FakeHttp("street", bad)
    assert geo.locate(fields(address="Cruquiusweg"), http, {}, NOW) is None


def test_accents_and_apostrophes_and_display_address_fallback():
    answer = fixture("address")
    doc = answer["response"]["docs"][0]
    doc.pop("straatnaam")
    doc.pop("huisnummer")
    doc["weergavenaam"] = "D’Ártagnanstraat 85A, 1019AT Amsterdam"
    assert geo.locate(fields(address="d'artagnanstraat 85-R"), FakeHttp(answer), {}, NOW)


def test_unknown_city_skips_distance_check_and_centres_cover_supported_cities():
    assert set(geo.CITY_CENTRES) == set(CITIES)
    assert geo.locate(fields(city="unknown-town"), FakeHttp("address"), {}, NOW)


def test_cache_hit_costs_no_request_at_zero_budget():
    cache = {}
    geo.locate(fields(), FakeHttp("address"), cache, NOW)
    cards, http = {"a": card()}, FakeHttp()
    assert geo.locate_cards(cards, http, cache, 0, NOW) == (0, None)
    assert cards["a"]["geo"]["q"] == geo.cache_key(fields())
    assert not http.urls


@pytest.mark.parametrize("days,requests", [(0, 0), (6, 0), (7, 4), (8, 4)])
def test_miss_cache_expiry(days, requests):
    from datetime import timedelta
    cache = {}
    geo.locate(fields(), FakeHttp(*(["empty"] * 4)), cache, NOW)
    assert cache == {geo.cache_key(fields()): {"not_found": NOW.isoformat()}}
    http = FakeHttp(*(["empty"] * 4))
    cards = {"a": dict(card(), geo=None, geo_q=geo.cache_key(fields()))}
    assert geo.locate_cards(cards, http, cache, 10, NOW + timedelta(days=days)) == (requests, None)
    assert cards["a"]["geo"] is None
    assert cards["a"]["geo_q"] == geo.cache_key(fields())


@pytest.mark.parametrize("contents", ["{", "[]", "null", "\xff"])
def test_broken_cache_file(tmp_path, contents):
    path = tmp_path / "cache.json"
    path.write_bytes(contents.encode("latin1"))
    assert geo.load_cache(path) == {}
    assert geo.load_cache(tmp_path / "missing.json") == {}


def test_atomic_save_and_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text('{"old":1}')
    cache = {"some key": {"not_found": NOW.isoformat()}}
    original = geo.os.replace
    calls = []

    def replace(source, target):
        assert Path(source).parent == path.parent
        assert json.loads(Path(source).read_text()) == cache
        assert json.loads(path.read_text()) == {"old": 1}
        calls.append((source, target))
        original(source, target)

    monkeypatch.setattr(geo.os, "replace", replace)
    geo.save_cache(path, cache)
    assert len(calls) == 1
    assert geo.load_cache(path) == cache
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["replace", "serialize"])
def test_atomic_save_failure_preserves_old_file_and_cleans_temp(tmp_path, monkeypatch, failure):
    path = tmp_path / "cache.json"
    path.write_text('{"old":1}')
    cache = {"value": object()} if failure == "serialize" else {}
    if failure == "replace":
        def fail(*args):
            raise OSError("replace failed")
        monkeypatch.setattr(geo.os, "replace", fail)
    with pytest.raises((OSError, TypeError)):
        geo.save_cache(path, cache)
    assert geo.load_cache(path) == {"old": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_budget_counts_requests_not_cards_and_partial_is_untried():
    cards = {"a": card(), "b": card(address="Otherstraat 1")}
    http, cache = FakeHttp("empty", "empty"), {}
    assert geo.locate_cards(cards, http, cache, 2, NOW) == (2, None)
    assert all("geo" not in c and "geo_q" not in c for c in cards.values())
    assert cache == {}


def test_budget_allows_later_cached_and_native_cards():
    cache = {}
    geo.locate(fields(), FakeHttp("address"), cache, NOW)
    cards = {"new": card(address="Otherstraat 1"), "cached": card(),
             "native": card(extra={"lat": 52.1, "lon": 4.3})}
    assert geo.locate_cards(cards, FakeHttp(), cache, 0, NOW) == (0, None)
    assert "geo" not in cards["new"]
    assert cards["cached"]["geo"] and cards["native"]["geo"]


def test_current_success_and_address_change():
    cards, http, cache = {"a": card()}, FakeHttp("address"), {}
    assert geo.locate_cards(cards, http, cache, 4, NOW) == (1, None)
    assert geo.locate_cards(cards, http, cache, 4, NOW) == (0, None)
    cards["a"]["listing"]["address"] = "Otherstraat 1"
    assert geo.locate_cards(cards, FakeHttp(), cache, 0, NOW) == (0, None)
    assert "geo" not in cards["a"]
    assert geo.locate_cards(cards, FakeHttp(*(["empty"] * 4)), cache, 4, NOW) == (4, None)
    assert cards["a"]["geo"] is None
    assert cards["a"]["geo_q"] == geo.cache_key(cards["a"]["listing"])


def test_duplicate_addresses_share_cache():
    cards, http = {"a": card(), "b": card()}, FakeHttp("address")
    assert geo.locate_cards(cards, http, {}, 1, NOW) == (1, None)
    assert cards["a"]["geo"] == cards["b"]["geo"]


def test_native_coordinates_no_requests_even_without_address():
    cards = {"a": card(address=None, postcode=None, extra={"lat": 52.1, "lon": 4.3})}
    assert geo.locate_cards(cards, FakeHttp(), {}, 0, NOW) == (0, None)
    assert cards["a"]["geo"] == {"lat": 52.1, "lon": 4.3, "precision": "address",
                                  "q": geo.cache_key(cards["a"]["listing"])}


@pytest.mark.parametrize("lat,lon", [(True, 4.3), (52.1, False), ("52.1", "4.3"),
                                     (float("nan"), 4.3), (52.1, float("inf")),
                                     (54, 4.3), (52, 3.1), (None, None)])
def test_invalid_native_coordinates_fall_through(lat, lon):
    cards = {"a": card(extra={"lat": lat, "lon": lon})}
    assert geo.locate_cards(cards, FakeHttp("address"), {}, 1, NOW) == (1, None)
    assert cards["a"]["geo"]["lat"] == 52.36845364


@pytest.mark.parametrize("failure", [SourceBlocked("private address"), SourceError("private address"),
                                      OSError("private address"), ValueError("private address"),
                                      {}, {"response": {"docs": "bad", "numFound": 1}},
                                      {"response": {"docs": [{}], "numFound": 1}}])
def test_service_failure_in_middle_stops_batch_no_negative_cache(failure, capsys):
    cards = {"a": card(), "b": card(address="Otherstraat 1"), "c": card(address="Thirdstraat 2")}
    cache, http = {}, FakeHttp("address", failure)
    assert geo.locate_cards(cards, http, cache, 15, NOW) == (2, "service_error")
    assert cards["a"]["geo"]
    assert "geo" not in cards["b"] and "geo" not in cards["c"]
    assert len(cache) == 1
    assert geo.cache_key(cards["b"]["listing"]) not in cache
    assert capsys.readouterr() == ("", "")


def test_malformed_point_stops_batch_instead_of_marking_not_found():
    answer = fixture("address")
    answer["response"]["docs"][0]["centroide_ll"] = "not a point"
    cache = {}
    assert geo.locate_cards({"a": card()}, FakeHttp(answer), cache, 10, NOW) == (1, "service_error")
    assert cache == {}


def test_not_found_versus_not_tried_and_datetime_now():
    cards = {"miss": card(address=None, postcode=None), "pending": card()}
    assert geo.locate_cards(cards, FakeHttp(), {}, 0, datetime(2026, 11, 19, tzinfo=timezone.utc)) == (0, None)
    assert cards["miss"]["geo"] is None and "geo_q" in cards["miss"]
    assert "geo" not in cards["pending"] and "geo_q" not in cards["pending"]


def test_locate_service_error_propagates_without_cache():
    cache = {}
    with pytest.raises(SourceError):
        geo.locate(fields(), FakeHttp(SourceError("failure")), cache, NOW)
    assert not cache


# ----------------------------------------------------------------------------- street names written with initials
def _streets(*names):
    """A PDOK answer with one "weg" document per (street name, lon, lat)."""
    docs = [{"type": "weg", "weergavenaam": "%s, 's-Gravenhage" % n, "straatnaam": n,
             "centroide_ll": "POINT(%s %s)" % (lon, lat)} for n, lon, lat in names]
    return {"response": {"numFound": len(docs), "docs": docs}}


@pytest.mark.parametrize("listed,official,same", [
    ("D.S. Lohmanlaan", "De Savornin Lohmanlaan", True),
    ("V. Zeggelenlaan", "Van Zeggelenlaan", True),
    ("J.W. Frisostraat", "Johan Willem Frisostraat", True),
    ("D.L. Reyweg", "De la Reyweg", True),
    ("B. Baumannlaan", "Burgemeester Baumannlaan", True),
    ("D.S. Lohmanlaan", "Lohmanlaan", False),                    # nothing is abbreviated there
    ("D.S. Lohmanlaan", "Dirk Lohmanlaan", False),               # one word for two initials
    ("B. Baumannlaan", "Professor Baumannlaan", False),          # the initial is another letter
    ("B. Baumannlaan", "Burgemeester Baumannstraat", False),     # the written-out part differs
    ("Lohmanlaan", "De Savornin Lohmanlaan", False),             # no initials: an ordinary street name
])
def test_initials_abbreviate_only_the_street_they_spell(listed, official, same):
    assert geo._spells_out(listed, official) is same


def test_a_street_written_with_initials_is_found_under_its_full_name():
    # DirectWonen writes "D.S. Lohmanlaan"; the land registry knows "De Savornin Lohmanlaan".
    http = FakeHttp("empty", _streets(("De Savornin Lohmanlaan", 4.2417, 52.0714), ("Lohmanlaan", 4.30, 52.06)))
    cache = {}
    result = geo.locate(fields(address="D.S. Lohmanlaan", postcode=None, city="den-haag"), http, cache, NOW)
    assert result == {"lat": 52.0714, "lon": 4.2417, "precision": "street"}
    assert http.queries == [("type:weg", "D.S. Lohmanlaan Den Haag"), ("type:weg", "Lohmanlaan Den Haag")]


def test_two_streets_that_fit_the_initials_give_no_point():
    http = FakeHttp("empty", _streets(("Jan Meewisstraat", 4.83, 52.38), ("Jacob Meewisstraat", 4.84, 52.37)))
    cache = {}
    assert geo.locate(fields(address="J. Meewisstraat", postcode=None, city="amsterdam"), http, cache, NOW) is None
    assert list(cache.values()) == [{"not_found": "2026-11-19"}]


def test_a_full_street_name_asks_nothing_extra():
    http = FakeHttp("empty")
    assert geo.locate(fields(address="Lohmanlaan", postcode=None, city="den-haag"), http, {}, NOW) is None
    assert http.queries == [("type:weg", "Lohmanlaan Den Haag")]


# ----------------------------------------------------------------------------- inside a run
def test_a_run_puts_homes_on_the_map_and_remembers_the_lookups(tmp_path, capsys):
    from hunter.runner import locate_homes
    from hunter.state import State
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    cfg = {"geo_lookups_per_run": 5}
    state = State(str(tmp_path / "data"), cfg)
    state.matches["x:1"] = card()
    http = FakeHttp("address")
    locate_homes(cfg, state, http, now)
    assert state.matches["x:1"]["geo"]["precision"] == "address" and len(http.urls) == 1
    saved = json.loads((tmp_path / "data" / "geo.json").read_text())
    assert len(saved) == 1
    # the next run rebuilds the card: its place comes from the saved lookups, without a request
    state.matches["x:1"] = card()
    quiet = FakeHttp()
    locate_homes(cfg, state, quiet, now)
    assert quiet.urls == [] and state.matches["x:1"]["geo"]["precision"] == "address"
    # the address service is down: the run goes on, the home is tried again next time
    state.matches["x:2"] = card(address="Sneeuwbalstraat 48", postcode=None, city="den-haag")
    locate_homes(cfg, state, FakeHttp(SourceError("PDOK answered HTTP 503 for https://api.pdok.nl/?q=Sneeuwbalstraat")), now)
    assert "geo" not in state.matches["x:2"] and state.matches["x:1"]["geo"]
    err = capsys.readouterr().err
    assert "geo:" in err and "Sneeuwbalstraat" not in err                 # no address in the public run log
    assert json.loads((tmp_path / "data" / "geo.json").read_text()) == saved
    # switched off, or a dry run: nothing is asked or nothing is written
    locate_homes({"geo_lookups_per_run": 0}, state, FakeHttp(), now)
    assert "geo" not in state.matches["x:2"]
    locate_homes(cfg, state, FakeHttp("empty", "street"), now, write=False)      # no such address, but the street
    assert state.matches["x:2"]["geo"]["precision"] == "street"
    assert json.loads((tmp_path / "data" / "geo.json").read_text()) == saved


# ----------------------------------------------------------------------------- the same address in another city
def _address(street, number, place, lon, lat, postcode="2512AB"):
    doc = {"type": "adres", "weergavenaam": "%s %s, %s %s" % (street, number, postcode, place), "straatnaam": street,
           "huisnummer": number, "postcode": postcode, "woonplaatsnaam": place, "centroide_ll": "POINT(%s %s)" % (lon, lat)}
    return {"response": {"numFound": 1, "docs": [doc]}}


def test_the_same_street_and_number_in_a_neighbouring_city_is_not_the_home():
    # "Schoolstraat 12" exists in Den Haag and in Delft, 9 km apart: within range of both city centres.
    delft = _address("Schoolstraat", 12, "Delft", 4.3571, 52.0116, "2611HS")
    street_in_delft = {"response": {"numFound": 1, "docs": [{"type": "weg", "weergavenaam": "Schoolstraat, Delft",
                       "straatnaam": "Schoolstraat", "woonplaatsnaam": "Delft", "centroide_ll": "POINT(4.3571 52.0116)"}]}}
    http = FakeHttp(delft, street_in_delft)
    assert geo.locate(fields(address="Schoolstraat 12", postcode=None, city="den-haag"), http, {}, NOW) is None
    here = _address("Schoolstraat", 12, "'s-Gravenhage", 4.3113, 52.0772)
    assert geo.locate(fields(address="Schoolstraat 12", postcode=None, city="den-haag"), FakeHttp(here), {}, NOW) == {
        "lat": 52.0772, "lon": 4.3113, "precision": "address"}
    # a district that carries the city's name is the city; a neighbour whose name only starts alike is not
    assert geo._in_city({"woonplaatsnaam": "Hoogvliet Rotterdam"}, "rotterdam")
    assert geo._in_city({"weergavenaam": "Cruquiusweg 85R, 1019AT Amsterdam"}, "amsterdam")
    assert not geo._in_city({"woonplaatsnaam": "Leiderdorp"}, "leiden")
    assert not geo._in_city({"woonplaatsnaam": "Rijswijk"}, "den-haag")
    assert not geo._in_city({}, "delft")
