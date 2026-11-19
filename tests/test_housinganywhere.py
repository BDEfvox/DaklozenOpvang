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


def _detail(entity, twitter=None, og=None, desc_meta=None):
    metas = ""
    if twitter is not None:
        metas += '<meta property="twitter:description" content="%s"/>' % twitter
    if og is not None:
        metas += '<meta property="og:description" content="%s"/>' % og
    if desc_meta is not None:
        metas += '<meta name="description" content="%s"/>' % desc_meta
    state = json.dumps({"listing": {"entity": entity}})
    return '<html><head>%s</head><script>window.__PRELOADED_STATE__= (%s)</script></html>' % (metas, state)


def test_details_reads_description():
    html = _detail({"description": None}, twitter="Lovely home.\n\nNo sharers please.",
                   desc_meta="Apartment for rent in The Hague: 3 bedroom(s), WiFi")
    items = json.loads((FIX / "housinganywhere_listings.json").read_text())
    lst = parse_search(page(items), "den-haag")[0]
    lst.bedrooms = None
    lst = apply_details(lst, html)
    assert lst.description == "Lovely home.\nNo sharers please."   # the text's own line break is kept
    assert lst.bedrooms == 3
    with pytest.raises(SourceError):
        apply_details(lst, "<html></html>")
    with pytest.raises(SourceError, match="layout changed"):
        apply_details(lst, "<script>window.__PRELOADED_STATE__= ({\"listing\":{}})</script>")


# ---- accuracy fixes ---------------------------------------------------------------------------------------------
def _rows():
    return {r["unitTypeInternalID"]: r for r in json.loads((FIX / "housinganywhere_search_rows.json").read_text())}


def _entities():
    return json.loads((FIX / "housinganywhere_detail_entities.json").read_text())


def _listing(uid, rows=None):
    rows = rows or _rows()
    out = parse_search(page([rows[uid]]), "den-haag")
    assert len(out) == 1
    return out[0]


def test_price_headline_minimum_when_flat_price_is_shown_nowhere():
    # ut1684872: priceEUR (flat) 3350 is on no page; the page says "From €2650 per month", card €2650 - €3999.
    lst = _listing(1684872)
    assert lst.price_total == 2650
    assert lst.extra["price_min"] == 2650 and lst.extra["price_max"] == 3999


def test_price_is_the_headline_and_the_first_bookable_month_is_kept_beside_it():
    # ut1498809: available from 28 Feb 2027; precise["02-2027"] is 3011; the page headlines "From €2038".
    # The headline is the price (the same on every run); the month price moves with the calendar.
    lst = _listing(1498809)
    assert lst.available_from == "2027-02-28"
    assert lst.price_total == 2038 and lst.extra["price_first_month"] == 3011
    assert lst.extra["price_min"] == 2038 and lst.extra["price_max"] == 3011
    assert lst.extra["price_basis"] == "from"
    later = dict(_rows()[1498809], bookableDateFromStart="2027-06-01T00:00:00Z")     # the calendar moves on
    assert parse_search(page([later]), "den-haag")[0].price_total == 2038


def test_price_single_value_has_no_range():
    for uid, price in ((1342881, 2495), (1688576, 2500)):
        lst = _listing(uid)
        assert lst.price_total == price
        assert "price_min" not in lst.extra and "price_max" not in lst.extra


def test_price_falls_back_to_flat_price():
    row = dict(_rows()[1342881], minPrice=None, maxPrice=None)
    assert parse_search(page([row]), "den-haag")[0].price_total == 2495


def test_available_from_ignores_the_no_date_value():
    assert _listing(1342881).available_from is None          # 0001-01-01T00:00:00Z
    assert _listing(1688576).available_from == "2026-11-01"


def test_scheveningen_row_is_kept_as_den_haag():
    lst = _listing(1412013)
    assert lst.city == "den-haag" and lst.address == "Jurriaan Kokstraat" and lst.price_total == 1995
    assert lst.available_from == "2027-03-02"
    rows = dict(_rows())
    rows[1] = dict(_rows()[1412013], city="Voorburg", unitTypePath="/room/ut1/nl/x/y", listingPath=None)
    assert parse_search(page([rows[1]]), "den-haag") == []


def _details(uid):
    ent = _entities()[uid]
    lst = _listing({"ut1688576": 1688576, "ut1342881": 1342881, "ut1684872": 1684872, "ut1498809": 1498809}[uid])
    return apply_details(lst, _detail(ent, twitter=ent["description"], og=ent["og_description"]))


def test_saved_ut1688576_working_professionals_over_30_space_for_2():
    ex = _details("ut1688576").extra
    assert ex["site_working_only"] is True and ex["site_min_age"] == 30 and ex["places_free"] == 2
    assert ex["site_facts"] == ("Tenant type: Working professionals only · Age: At least 30 · Space for 2 people · "
                                "You can register at this address (inschrijven)")
    assert "site_housemates" not in ex and ex["registration_possible"] is True


def test_saved_ut1342881_students_and_workers_age_18_is_no_limit():
    lst = _details("ut1342881")
    ex = lst.extra
    assert ex["places_free"] == 3 and "site_min_age" not in ex and "site_working_only" not in ex
    assert ex["site_facts"].startswith("Tenant type: Students, working professionals · Age: At least 18 · Space for 3 people")
    assert lst.available_from == "2026-11-19"  # bookablePeriods[0].from


def test_saved_ut1684872_no_registration_min_age_25():
    ex = _details("ut1684872").extra
    assert ex["registration_possible"] is False and ex["site_min_age"] == 25 and ex["places_free"] == 4
    assert "You can't register at this address (inschrijven)" in ex["site_facts"]


def test_saved_ut1498809_nothing_stated_means_no_keys():
    lst = _details("ut1498809")
    ex = lst.extra
    assert ex["places_free"] == 2 and ex["site_facts"] == "Space for 2 people"
    assert not any(k.startswith("site_") and k != "site_facts" for k in ex)
    assert lst.available_from == "2027-02-28"


def test_housemates_from_a_shared_facility_only_and_unknown_values_ignored():
    base = _entities()["ut1498809"]
    lst = _listing(1498809)
    ent = dict(base, currentOccupancy=2)     # its meaning was never seen next to a label: not read
    assert "site_housemates" not in apply_details(lst, _detail(ent, twitter="x")).extra
    lst = _listing(1498809)
    ent = dict(base, facilities=dict(base["facilities"], kitchen="shared", tenant_status="students only"),
               preferredGender="female", maxAge=40)
    ex = apply_details(lst, _detail(ent, twitter="x")).extra
    assert ex["site_housemates"] is True and ex["site_max_age"] == 40
    assert not any(k in ex for k in ("site_students_only", "site_working_only", "site_women_only"))


def test_fields_seen_next_to_their_labels_on_19_nov_2026():
    """ut1747098 showed "Age: 25 - 45", "Space for 2 people" and a required monthly cleaning fee; ut1749760 and
    ut260681 showed "Space for 1 person" and "Tenant type: Students, working professionals"."""
    base = _entities()["ut1498809"]

    def read(**fields):
        ent = dict(base, **fields)
        return apply_details(_listing(1498809), _detail(ent, twitter="x")).extra

    ex = read(minAge=25, maxAge=45, freePlaces=2, facilities=dict(base["facilities"], tenant_status="working professionals only"),
              costs={"costs": {
                  "cleaning-fee": {"value": 10000, "payableBy": "tenant", "payableAt": "monthly", "required": True, "isEstimated": False},
                  "electricity-bill": {"value": 12000, "payableBy": "tenant", "payableAt": "monthly", "required": True, "isEstimated": True},
                  "security-deposit": {"value": 500000, "payableBy": "tenant", "payableAt": "move-in", "required": True},
                  "parking": {"value": 9000, "payableBy": "tenant", "payableAt": "monthly", "required": False}}})
    assert ex["site_min_age"] == 25 and ex["site_max_age"] == 45 and ex["site_working_only"] is True
    assert ex["mandatory_costs"] == 100 and "cleaning fee €100" in ex["mandatory_costs_note"]
    assert "Age: 25 - 45" in ex["site_facts"] and "Required every month: cleaning fee €100" in ex["site_facts"]

    ex = read(minAge=0, maxAge=0, freePlaces=1, facilities=dict(base["facilities"], tenant_status="any"))
    assert ex["places_free"] == 1 and ex["site_students_ok"] is True
    assert ex["site_facts"].startswith("Tenant type: Students, working professionals · Space for 1 person")
    assert "site_max_age" not in ex and "site_min_age" not in ex and "mandatory_costs" not in ex

    ex = read(minAge=30, maxAge=20, freePlaces=0)                 # a maximum below the minimum is not believed
    assert ex["site_min_age"] == 30 and "site_max_age" not in ex and "places_free" not in ex


def test_description_prefers_entity_text_over_short_summary():
    ent = dict(_entities()["ut1498809"], description="The long real text. No sharers.")
    html = _detail(ent, og="Bills included, 41 m², Space for 2 people. Check it out.")
    assert apply_details(_listing(1498809), html).description == "The long real text. No sharers."
    ent2 = dict(ent, description=None)
    assert apply_details(_listing(1498809), _detail(ent2, twitter="Twitter text here.", og="Short.")).description \
        == "Twitter text here."
    assert apply_details(_listing(1498809), _detail(ent2, og="Short.")).description == "Short."


def test_extra_values_are_scalar():
    for uid in _entities():
        ex = _details(uid).extra
        assert all(isinstance(v, (str, int, float, bool)) for v in ex.values()) and len(ex["site_facts"]) <= 200


def test_module_documents_why_page_two_is_not_fetched():
    from hunter.sources import housinganywhere
    assert "robots.txt" in housinganywhere.__doc__ and "page 2" in housinganywhere.__doc__


def test_search_url():
    assert SOURCE.search_url("den-haag") == "https://housinganywhere.com/s/The-Hague--Netherlands/apartment-for-rent"
