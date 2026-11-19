"""Review fixes in the runner: duplicates, first-run retry, detail snapshots, price drops, health."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hunter.runner import fetch_source, run_once
from hunter.sources.base import Source, SourceBlocked, SourceError, SourcePartial
from hunter.state import State, iso
from tests.conftest import FakeNotifier, FakeSource, make_listing

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # 13:00 Amsterdam, after the digest hour


def go(cfg, tmp_path, sources, notifier=None, now=T0):
    notifier = notifier or FakeNotifier()
    state = State(str(tmp_path / "data"), cfg)
    run_once(cfg, state, {s.name: s for s in sources}, notifier, object(), now=now,
             docs_dir=str(tmp_path / "docs"))
    return notifier, state


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def failing():
    n = FakeNotifier()
    n.ok = False
    return n


def alerts(n):
    return [u for u in n.urls if u]


class FeedOnly(FakeSource):
    """A source without a detail page (like Vesteda, 123Wonen, email alerts): Source.fetch_details is a no-op."""
    fetch_details = Source.fetch_details


class DetailPrice(FakeSource):
    """Detail page that shows a different price than the search page."""
    detail_price = None

    def fetch_details(self, listing, http):
        listing = super().fetch_details(listing, http)
        if self.detail_price is not None:
            listing.price_total = self.detail_price
        return listing


class DetailAddress(FakeSource):
    """Search page shows the street only; the detail page adds the house number."""
    def fetch_details(self, listing, http):
        listing = super().fetch_details(listing, http)
        if listing.address == "Daltonstraat":
            listing.address = "Daltonstraat 5"
        return listing


def two_sources(cfg, tmp_path, a_cls=FakeSource, b_cls=FakeSource):
    a = a_cls("a", [make_listing("base", source="a")])
    b = b_cls("b", [make_listing("base", source="b")])
    go(cfg, tmp_path, [a, b])  # first scans: baselines
    return a, b


# --------------------------------------------------------------------------- duplicates (HH1, R2 2.4)
def test_HH01_street_only_twin_hides_the_only_eligible_home(cfg, tmp_path):
    """Two different homes on the same street at the same price, no house numbers."""
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat", price_total=1400, title="Home X"))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat", price_total=1400, title="Home Y"))
    a.description = "one person only"  # X's detail page disqualifies X
    n1, st = go(cfg, tmp_path, [a, b], now=at(10))
    n2, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert "https://example.com/y" in n1.urls + n2.urls, "Y (eligible) never alerted: hidden as X's duplicate"
    assert "https://example.com/x" not in n1.urls + n2.urls


def test_HH01b_same_run_duplicate_records_a_uid_not_a_source_name(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat 5", price_total=1400))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat 5", price_total=1400))
    n, st = go(cfg, tmp_path, [a, b], now=at(10))
    assert st.seen["b:y"].get("dup_of") == "a:x"
    assert alerts(n) == ["https://example.com/x"]          # one alert for one home
    assert "b:y" not in st.matches
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert alerts(n) == [] and st.seen["b:y"]["dup_of"] == "a:x"


def test_two_homes_on_one_street_with_different_sizes_are_both_alerted(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat", area_m2=55))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat", area_m2=90, bedrooms=3, rooms=4))
    n, st = go(cfg, tmp_path, [a, b], now=at(10))
    assert sorted(alerts(n)) == ["https://example.com/x", "https://example.com/y"]
    assert not st.seen["a:x"].get("dup_of") and not st.seen["b:y"].get("dup_of")


def test_a_later_run_with_a_different_size_on_the_same_street_is_not_a_duplicate(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat", area_m2=55))
    go(cfg, tmp_path, [a, b], now=at(10))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat", area_m2=90))
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert alerts(n) == ["https://example.com/y"]
    b.listings.append(make_listing("z", source="b", address="Daltonstraat", area_m2=56))  # this one is X again
    n, st = go(cfg, tmp_path, [a, b], now=at(30))
    assert alerts(n) == [] and st.seen["b:z"]["dup_of"] == "a:x"


def test_duplicate_is_shown_once_the_listing_it_duplicated_no_longer_fits(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat 5"))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat 5"))
    n, st = go(cfg, tmp_path, [a, b], now=at(10))
    assert alerts(n) == ["https://example.com/x"]
    a.listings[-1] = make_listing("x", source="a", address="Daltonstraat 5", price_total=2600)  # over budget now
    n, st = go(cfg, tmp_path, [a, b], now=at(20))   # X stops fitting in this run ...
    n2, st = go(cfg, tmp_path, [a, b], now=at(30))  # ... so Y stands on its own at the latest in the next
    assert "https://example.com/y" in alerts(n) + alerts(n2)
    assert "dup_of" not in st.seen["b:y"] and "b:y" in st.matches and "a:x" not in st.matches


def test_source_name_left_by_an_older_version_is_resolved_to_the_listing(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat 5"))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat 5"))
    _, st = go(cfg, tmp_path, [a, b], now=at(10))
    st.seen["b:y"]["dup_of"] = "a"  # what the old code wrote
    st.save(at(11), force=True)
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert st.seen["b:y"]["dup_of"] == "a:x" and alerts(n) == []


def test_source_name_without_a_fitting_listing_behind_it_is_dropped(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    b.listings.append(make_listing("y", source="b", address="Daltonstraat 5"))
    _, st = go(cfg, tmp_path, [a, b], now=at(10))
    st.seen["b:y"].update(dup_of="a", notified_at=None)
    st.matches.pop("b:y")
    st.save(at(11), force=True)
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert alerts(n) == ["https://example.com/y"] and "dup_of" not in st.seen["b:y"]


def test_failed_alert_for_a_home_on_two_sites_is_retried_once(cfg, tmp_path):
    a, b = two_sources(cfg, tmp_path)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat 5"))
    b.listings.append(make_listing("y", source="b", address="Daltonstraat 5"))
    go(cfg, tmp_path, [a, b], notifier=failing(), now=at(10))
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    n2, _ = go(cfg, tmp_path, [a, b], now=at(30))
    assert len(alerts(n) + alerts(n2)) == 1


def test_full_address_from_the_detail_page_finds_the_earlier_listing(cfg, tmp_path):
    """R2 2.4.3: the fingerprint is refreshed after the detail page, and compared again."""
    a, b = two_sources(cfg, tmp_path, b_cls=DetailAddress)
    a.listings.append(make_listing("x", source="a", address="Daltonstraat 5"))
    n, st = go(cfg, tmp_path, [a, b], now=at(10))
    assert alerts(n) == ["https://example.com/x"]
    b.listings.append(make_listing("y", source="b", address="Daltonstraat"))  # search page: street only
    n, st = go(cfg, tmp_path, [a, b], now=at(20))
    assert alerts(n) == []
    assert st.seen["b:y"]["dup_of"] == "a:x" and st.seen["b:y"]["fp"] == st.seen["a:x"]["fp"]


# --------------------------------------------------------------------------- first scan (HH2)
def test_HH02_failed_first_batch_is_retried(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("1")])
    go(cfg, tmp_path, [src], notifier=failing())
    n, _ = go(cfg, tmp_path, [src], now=at(10))
    assert "https://example.com/1" in n.urls
    n, _ = go(cfg, tmp_path, [src], now=at(20))
    assert alerts(n) == []  # and only once


def test_first_batch_that_fails_halfway_sends_the_rest_next_run(cfg, tmp_path):
    src = FakeSource(listings=[make_listing(str(i), price_total=1300 + i) for i in range(4)])

    class FailsAfterTwo(FakeNotifier):
        def send(self, text, url=None, dashboard_url=None):
            if len(alerts(self)) >= 2:
                return False
            return super().send(text, url, dashboard_url)

    first, _ = go(cfg, tmp_path, [src], notifier=FailsAfterTwo())
    second, _ = go(cfg, tmp_path, [src], now=at(10))
    assert len(alerts(first)) == 2
    assert sorted(alerts(first) + alerts(second)) == ["https://example.com/%d" % i for i in range(4)]


# --------------------------------------------------------------------------- detail snapshots (HH3, R2 2.2, 2.7)
def test_HH03_feed_correction_is_not_undone_by_a_snapshot(cfg, tmp_path):
    src = FeedOnly("feed", [make_listing("base", source="feed")])
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("base", source="feed"), make_listing("x", source="feed", bedrooms=2, rooms=3)]
    n, st = go(cfg, tmp_path, [src], now=at(10))
    assert alerts(n) == ["https://example.com/x"] and "details" not in st.seen["feed:x"]
    src.listings = [make_listing("base", source="feed"), make_listing("x", source="feed", bedrooms=1, rooms=2)]
    _, st = go(cfg, tmp_path, [src], now=at(20))
    assert "feed:x" not in st.matches


def test_snapshot_left_by_an_older_version_for_a_feed_only_source_is_dropped(cfg, tmp_path):
    src = FeedOnly("feed", [make_listing("base", source="feed"), make_listing("x", source="feed")])
    _, st = go(cfg, tmp_path, [src])
    st.seen["feed:x"]["details"] = {"bedrooms": 2, "rooms": 3, "price_total": 1400, "search_price": 1400}
    st.save(at(1), force=True)
    src.listings[1] = make_listing("x", source="feed", bedrooms=1, rooms=2)
    _, st = go(cfg, tmp_path, [src], now=at(10))
    assert "details" not in st.seen["feed:x"] and "feed:x" not in st.matches


def test_feed_only_source_spends_no_detail_budget_and_can_announce_a_drop(cfg, tmp_path):
    cfg["detail_fetch_max_per_run"] = 1
    feed = FeedOnly("feed", [make_listing("base", source="feed")])
    site = FakeSource("site", [make_listing("base", source="site")])
    go(cfg, tmp_path, [feed, site])
    feed.listings.append(make_listing("f", source="feed", price_total=1300))
    site.listings.append(make_listing("s", source="site", price_total=1390))
    site.detail_calls = 0
    n, st = go(cfg, tmp_path, [feed, site], now=at(10))
    assert sorted(alerts(n)) == ["https://example.com/f", "https://example.com/s"]
    assert site.detail_calls == 1 and st.seen["site:s"].get("details")  # the one read went to the real page
    st.seen["feed:f"]["details"] = {"price_total": 1300, "search_price": 1300}  # older version's leftover
    st.save(at(11), force=True)
    feed.listings[-1] = make_listing("f", source="feed", price_total=1200)
    n, _ = go(cfg, tmp_path, [feed, site], now=at(20))
    assert [m for m in n.messages if "Price drop" in m], "a feed-only source could no longer announce a drop"


def test_R2_2_2_private_text_listing_is_not_looked_at_forever(cfg, tmp_path):
    def fresh():
        return [make_listing("base"), make_listing("x", bedrooms=None, rooms=3, description="card text",
                                                   extra={"private_text": True})]
    src = FakeSource(listings=fresh())
    go(cfg, tmp_path, [src])
    src.detail_calls = 0
    for i in range(1, 7):
        src.listings = fresh()
        _, st = go(cfg, tmp_path, [src], now=at(10 * i))
    assert src.detail_calls == 3 and st.seen["fake:x"]["detail_tries"] == 3


def test_R2_2_7_flag_without_support_does_not_stay_on_the_card(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("1")])
    _, st = go(cfg, tmp_path, [src])
    st.matches["fake:1"]["flags"].append("no_students")
    st.save(at(1), force=True)
    _, st = go(cfg, tmp_path, [src], now=at(10))
    assert "no_students" not in st.matches["fake:1"]["flags"]


def test_flag_found_on_the_detail_page_stays_while_that_snapshot_is_current(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("base"), make_listing("x")])
    go(cfg, tmp_path, [src])
    src.listings.append(make_listing("y"))
    src.description = "Geen studenten. " + "x" * 3000   # the saved text is cut; the flag is remembered
    _, st = go(cfg, tmp_path, [src], now=at(10))
    assert "no_students" in st.seen["fake:y"]["details"]["flags"]
    st.seen["fake:y"]["details"]["description"] = "ingekort"  # as if the flag sat beyond the cut
    st.save(at(11), force=True)
    _, st = go(cfg, tmp_path, [src], now=at(20))
    assert "no_students" in st.matches["fake:y"]["flags"]


# --------------------------------------------------------------------------- price drops (HH10, R2 2.6)
def test_HH10_one_euro_verified_drop_is_not_announced(cfg, tmp_path):
    src = DetailPrice(listings=[make_listing("base")])
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("base"), make_listing("x", price_total=1400)]
    go(cfg, tmp_path, [src], now=at(10))
    src.listings = [make_listing("base"), make_listing("x", price_total=1200)]
    src.detail_price = 1399
    n, st = go(cfg, tmp_path, [src], now=at(20))
    assert not [m for m in n.messages if "Price drop" in m]
    assert st.matches["fake:x"]["price_drop_from"] is None
    src.detail_calls = 0
    n, _ = go(cfg, tmp_path, [src], now=at(30))   # and the same search price is not checked again every run
    assert n.messages == [] and src.detail_calls == 0


def test_verified_drop_over_the_threshold_is_announced(cfg, tmp_path):
    src = DetailPrice(listings=[make_listing("base"), make_listing("x", price_total=1400)])
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("base"), make_listing("x", price_total=1200)]
    src.detail_price = 1250
    n, _ = go(cfg, tmp_path, [src], now=at(10))
    assert len([m for m in n.messages if "Price drop" in m]) == 1


def test_R2_2_6_failed_small_drop_into_budget_is_retried(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("base"), make_listing("x", price_total=1620)])
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("base"), make_listing("x", price_total=1600)]
    _, st = go(cfg, tmp_path, [src], notifier=failing(), now=at(10))
    assert "fake:x" not in st.matches  # nothing on the dashboard that was never announced
    n, st = go(cfg, tmp_path, [src], now=at(20))
    assert alerts(n) == ["https://example.com/x"] and "fake:x" in st.matches
    n, _ = go(cfg, tmp_path, [src], now=at(30))
    assert alerts(n) == []


def test_home_that_fits_now_without_a_real_drop_is_announced_as_new(cfg, tmp_path):
    cfg["searches"] = [dict(s, max_total=1600) if s["persons"] == 3 else s for s in cfg["searches"]]
    src = DetailPrice(listings=[make_listing("base"), make_listing("x", price_total=1700, bedrooms=3, rooms=4)])
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("base"), make_listing("x", price_total=1500, bedrooms=3, rooms=4)]
    src.detail_price = 1500
    n, _ = go(cfg, tmp_path, [src], now=at(10))
    assert alerts(n) == ["https://example.com/x"]


# --------------------------------------------------------------------------- Vesteda budget (HH8)
def test_HH08_vesteda_asks_for_the_configured_budget(cfg):
    from hunter.sources.vesteda import SOURCE as VESTEDA
    sent = {}

    class Http:
        def post_json(self, url, payload, **kw):
            sent.update(payload)
            return {"results": {"objects": []}}

    cfg["searches"] = [dict(s, max_total=2800) if s["persons"] == 3 else s for s in cfg["searches"]]
    try:
        fetch_source(VESTEDA, cfg, Http())
        assert sent["priceTo"] == 2800
    finally:
        VESTEDA.cfg = None
    assert VESTEDA.max_rent(2400) == 2400  # used on its own: the old default


# --------------------------------------------------------------------------- health (R2 2.5)
def test_R2_2_5a_partial_run_ends_the_failure_streak_with_one_message(cfg, tmp_path):
    good = FakeSource("good", [make_listing("1", source="good")])
    bad = FakeSource("bad", raises=SourceBlocked("blocked"))
    warned = []
    for i in range(6):
        n, _ = go(cfg, tmp_path, [good, bad], now=at(10 * i))
        warned += [m for m in n.messages if "failed" in m]
    assert len(warned) == 1
    bad.raises = None
    bad.listings = [make_listing("b1", source="bad")]
    bad.city_errors = {"leiden": SourceError("x")}
    n, st = go(cfg, tmp_path, [good, bad], now=at(70))
    assert st.health["bad"]["status"] == "partial"
    assert st.health["bad"]["consecutive_failures"] == 0 and st.health["bad"]["alerted"] is False
    assert [m for m in n.messages if "partly working again" in m and "leiden" in m]
    n, st = go(cfg, tmp_path, [good, bad], now=at(80))
    assert not [m for m in n.messages if "working again" in m]   # said once
    bad.city_errors = {}
    n, st = go(cfg, tmp_path, [good, bad], now=at(90))
    assert st.health["bad"]["status"] == "ok" and not [m for m in n.messages if "working again" in m]


def test_partial_recovery_message_that_fails_is_sent_next_run(cfg, tmp_path):
    bad = FakeSource("bad", raises=SourceBlocked("blocked"))
    for i in range(6):
        go(cfg, tmp_path, [bad], now=at(10 * i))
    bad.raises = None
    bad.listings = [make_listing("b1", source="bad")]
    bad.city_errors = {"leiden": SourceError("x")}
    _, st = go(cfg, tmp_path, [bad], notifier=failing(), now=at(70))
    assert st.health["bad"]["alerted"] is True and st.health["bad"]["consecutive_failures"] == 0
    n, st = go(cfg, tmp_path, [bad], now=at(80))
    assert [m for m in n.messages if "partly working again" in m] and st.health["bad"]["alerted"] is False


def test_R2_2_5b_blocked_before_any_listing_is_blocked_not_partial(cfg):
    class S(FakeSource):
        def fetch(self, city, http):
            raise SourcePartial([], SourceBlocked("captcha"), city)

    assert fetch_source(S(), cfg, object()).status == "blocked"

    class E(FakeSource):
        def fetch(self, city, http):
            raise SourcePartial([], SourceError("timeout"), city)

    assert fetch_source(E(), cfg, object()).status == "error"

    class P(FakeSource):
        def fetch(self, city, http):
            raise SourcePartial([make_listing("1", city=city)], SourceError("page 2"), city)

    assert fetch_source(P(), cfg, object()).status == "partial"
