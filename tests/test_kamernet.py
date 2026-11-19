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


# ---- accuracy fixes: ideal-tenant block, persons ids, top ads, descriptions -------------------------------------
def _detail_html(det, block=""):
    data = {"props": {"pageProps": {"targetPageProps": {"listingDetails": det}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">%s</script>%s</html>' % (json.dumps(data), block)


def _fresh(city="den-haag"):
    from hunter.models import Listing
    return Listing("kamernet", "1", "https://kamernet.nl/huren/appartement-den-haag/x/appartement-1", "t", city,
                   price_total=1500, property_type="apartment")


def _saved_cases():
    return {c["listing_id"]: c for c in json.loads((FIX / "kamernet_ideal_tenant.json").read_text())}


def _apply_saved(lid, with_block=True):
    c = _saved_cases()[lid]
    return apply_details(_fresh(), _detail_html(c["details"], c["block_html"] if with_block else ""))


def test_saved_2418936_female_students_aged_16_to_40():
    ex = _apply_saved(2418936).extra
    assert ex["max_persons"] == 2 and ex["site_students_only"] is True and ex["site_women_only"] is True
    assert ex["site_max_age"] == 40 and "site_min_age" not in ex  # 16 is the default
    assert ex["site_facts"] == ("Aantal huurders: 2 personen · Leeftijd: 16 - 40 jaar · Geslacht: Vrouw · "
                                "Bezigheid: Student, Werkende student")
    assert not ex.get("site_no_students") and not ex.get("site_working_only")


def test_saved_2414230_everybody_except_a_minimum_age():
    ex = _apply_saved(2414230).extra
    assert ex["max_persons"] == 2 and ex["site_min_age"] == 20
    assert "site_max_age" not in ex  # 99 is the default
    assert not any(k in ex for k in ("site_students_only", "site_no_students", "site_working_only", "site_women_only"))
    assert ex["site_facts"].endswith("Bezigheid: Student, Werkende student, Werkend, Werkzoekend")


def test_saved_2419708_all_null_gives_no_restriction():
    ex = _apply_saved(2419708).extra
    assert ex["max_persons"] == 2
    assert set(ex) == {"max_persons", "site_facts"}
    assert ex["site_facts"] == ("Aantal huurders: 2 personen · Leeftijd: Niet belangrijk · Geslacht: Iedereen welkom · "
                                "Bezigheid: Iedereen welkom")


def test_saved_2418807_shared_room_three_persons_not_four():
    lst = _apply_saved(2418807)
    assert lst.extra["max_persons"] == 3  # suitableForNumberOfPersons is the id 4, the page says "3 personen"
    assert lst.extra["site_housemates"] is True and lst.property_type == "room"
    assert "site_min_age" not in lst.extra and "site_max_age" not in lst.extra


def test_saved_2419482_four_persons_not_eight():
    ex = _apply_saved(2419482).extra
    assert ex["max_persons"] == 4 and ex["site_min_age"] == 20
    assert "site_housemates" not in ex  # all facilities are 2 = own


def test_saved_2417636_students_and_workers_mix_gives_nothing():
    ex = _apply_saved(2417636).extra
    assert ex["max_persons"] == 2
    assert not any(k in ex for k in ("site_students_only", "site_no_students", "site_working_only", "site_min_age"))


@pytest.mark.parametrize("sid,expected", [(1, 1), (2, 2), (4, 3), (8, 4), (16, None), (3, None), (0, None)])
def test_persons_id_mapping_without_visible_text(sid, expected):
    det = {"suitableForNumberOfPersons": sid}
    ex = apply_details(_fresh(), _detail_html(det)).extra
    assert ex.get("max_persons") == expected


def test_visible_persons_text_wins_over_the_id():
    block = ('<section><h5>Verhuurders ideale huurder</h5><div><div><p>Aantal huurders</p><p>3 personen</p></div>'
             '</div></section>')
    ex = apply_details(_fresh(), _detail_html({"suitableForNumberOfPersons": 2}, block)).extra
    assert ex["max_persons"] == 3


@pytest.mark.parametrize("ids,expected", [
    ([1], {"site_students_only"}),
    ([1, 2], {"site_students_only"}),
    ([4], {"site_no_students", "site_working_only"}),
    ([8], {"site_no_students"}),
    ([4, 8], {"site_no_students"}),
    ([1, 4], set()),
    ([2, 8], set()),
    (None, set()),
    ([16], set()),   # unknown bit: believe nothing
    ([1, 99], set()),
])
def test_candidate_status_mapping(ids, expected):
    ex = apply_details(_fresh(), _detail_html({"candidateStatusId": ids})).extra
    assert {k for k in ex if k.startswith("site_") and k != "site_facts"} == expected


def test_status_that_disagrees_with_the_visible_label_is_ignored():
    block = ('<section><h5>Verhuurders ideale huurder</h5><div><div><p>Bezigheid</p><p>Student , Werkend</p></div>'
             '</div></section>')
    ex = apply_details(_fresh(), _detail_html({"candidateStatusId": [1]}, block)).extra
    assert "site_students_only" not in ex and ex["site_facts"] == "Bezigheid: Student, Werkend"


@pytest.mark.parametrize("lo,hi,expected", [
    (16, 99, {}), (18, 99, {}), (None, None, {}), (19, 0, {"site_min_age": 19}),
    (16, 40, {"site_max_age": 40}), (25, 35, {"site_min_age": 25, "site_max_age": 35}),
])
def test_age_limits_only_when_real(lo, hi, expected):
    ex = apply_details(_fresh(), _detail_html({"candidateMinAgeId": lo, "candidateMaxAgeId": hi})).extra
    assert {k: v for k, v in ex.items() if k.startswith("site_") and k != "site_facts"} == expected


@pytest.mark.parametrize("gender,women", [(2, True), (8, False), (None, False), (1, False), (4, False)])
def test_gender_only_the_proven_id_counts(gender, women):
    ex = apply_details(_fresh(), _detail_html({"candidateGenderId": gender})).extra
    assert ex.get("site_women_only", False) is women and "site_men_only" not in ex


@pytest.mark.parametrize("field", ["kitchenId", "livingRoomId", "showerId", "toiletId"])
def test_any_shared_facility_sets_housemates(field):
    ex = apply_details(_fresh(), _detail_html({field: 1})).extra
    assert ex["site_housemates"] is True
    assert "site_housemates" not in apply_details(_fresh(), _detail_html({field: 2})).extra


def test_site_facts_is_short_and_extra_is_scalar():
    for c in _saved_cases().values():
        ex = _apply_saved(c["listing_id"]).extra
        assert len(ex.get("site_facts", "")) <= 200
        assert all(isinstance(v, (str, int, float, bool)) for v in ex.values())


def test_top_ads_are_read_and_deduped():
    top = json.loads((FIX / "kamernet_top_ad.json").read_text())
    items = json.loads((FIX / "kamernet_listings.json").read_text())
    resp = {"listings": items, "topAdListings": [top, dict(items[0])], "total": len(items) + 1}
    data = {"props": {"pageProps": {"targetPageProps": {"findListingsResponse": resp}}}}
    html = '<script id="__NEXT_DATA__">%s</script>' % json.dumps(data)
    out = parse_search(html, "den-haag")
    ids = [l.source_id for l in out]
    assert "2417441" in ids and len(ids) == len(set(ids)) == 4
    ad = [l for l in out if l.source_id == "2417441"][0]
    assert ad.price_total == 1300 and ad.area_m2 == 43 and ad.furnished == "furnished"
    # only top ads and no normal rows
    resp2 = {"listings": None, "topAdListings": [top], "total": 1}
    data2 = {"props": {"pageProps": {"targetPageProps": {"findListingsResponse": resp2}}}}
    assert [l.source_id for l in parse_search('<script id="__NEXT_DATA__">%s</script>' % json.dumps(data2),
                                              "den-haag")] == ["2417441"]


def test_description_uses_dutch_then_english_without_repeating_the_title():
    det = {"dutchTitle": "Op de 1e etage gelegen ruim", "dutchDescription": "Op de 1e etage gelegen ruim appartement. Geen studenten.",
           "englishTitle": "Situated on the 1st floor", "englishDescription": "Situated on the 1st floor a spacious flat. No students."}
    d = apply_details(_fresh(), _detail_html(det)).description
    assert d.count("Op de 1e etage gelegen ruim") == 1 and d.count("Situated on the 1st floor") == 1
    assert d.index("Geen studenten") < d.index("No students")


def test_description_keeps_a_title_that_is_not_a_prefix_and_skips_identical_english():
    det = {"dutchTitle": "Zeer mooi", "dutchDescription": "Appartement van 2005. ",
           "englishTitle": "Zeer mooi", "englishDescription": "Appartement van 2005."}
    assert apply_details(_fresh(), _detail_html(det)).description == "Zeer mooi Appartement van 2005."


def test_description_english_only():
    det = {"dutchTitle": None, "dutchDescription": None, "englishTitle": "T", "englishDescription": "Registration not possible."}
    assert apply_details(_fresh(), _detail_html(det)).description == "T Registration not possible."


def test_rooms_from_num_of_rooms_and_registration():
    lst = apply_details(_fresh(), _detail_html({"numOfRooms": 2, "numOfBedrooms": 1, "isRegistrationAllowed": False}))
    assert lst.rooms == 2 and lst.bedrooms == 1
    assert lst.extra["registration_possible"] is False and lst.extra["registration_allowed"] is False
