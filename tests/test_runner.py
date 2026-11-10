from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from hunter.runner import run_once
from hunter.sources.base import SourceBlocked, SourceError
from hunter.state import State
from tests.conftest import FakeNotifier, FakeSource, make_listing

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # 13:00 Amsterdam (after digest hour)


def go(cfg, tmp_path, sources, notifier=None, now=T0, write=True, **kw):
    notifier = notifier or FakeNotifier()
    state = State(str(tmp_path / "data"), cfg)
    code = run_once(cfg, state, {s.name: s for s in sources}, notifier, object(), now=now, write=write,
                    docs_dir=str(tmp_path / "docs"), **kw)
    return code, notifier, state


def listings(n, **kw):
    return [make_listing(str(i), price_total=1000 + i * 10, **kw) for i in range(n)]


def non_digest(n):
    return [m for m in n.messages if "Daily digest" not in m and "House Hunter is running" not in m]


def test_first_run_pool_cap_and_summary_then_dedupe(cfg, tmp_path):
    src = FakeSource(listings=listings(14))
    code, n, st = go(cfg, tmp_path, [src])
    assert code == 0
    msgs = non_digest(n)
    assert len(msgs) == 11  # 10 best + 1 summary
    status = [m for m in n.messages if "House Hunter is running" in m]
    assert len(status) == 1 and "Fake" in status[0] and "(14 listings)" in status[0]
    assert "Found <b>14</b>" in msgs[-1] and "Fake" in msgs[-1]
    assert "€500 pp" in msgs[0] or "€500" in msgs[0]  # cheapest first
    assert (tmp_path / "docs" / "data.json").exists()
    assert len(st.matches) == 14
    # second run: nothing new
    code, n2, _ = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert non_digest(n2) == []


def test_new_match_after_baseline_and_details(cfg, tmp_path):
    src = FakeSource(listings=listings(2))
    go(cfg, tmp_path, [src])
    src.detail_calls = 0
    src.listings.append(make_listing("99", price_total=1300))
    src.description = "geen woningdelers"
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert len(n.messages) == 1 and "Nice flat 99" in n.messages[0]
    assert src.detail_calls == 1 and "No house sharers" in n.messages[0]
    assert st.seen["fake:99"]["notified_at"]


def test_message_cap_and_overflow(cfg, tmp_path):
    src = FakeSource(listings=listings(1))
    go(cfg, tmp_path, [src])
    src.detail_calls = 0
    src.listings = listings(1) + [make_listing("n%d" % i, price_total=1100 + i) for i in range(20)]
    code, n, _ = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert len(n.messages) == 16  # 15 + overflow
    assert "5 more new match" in n.messages[-1]
    assert src.detail_calls == 10


def test_price_drop(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("1", price_total=1700)])  # does not match
    go(cfg, tmp_path, [src])
    # make baseline exist via another matching listing
    src.listings = [make_listing("1", price_total=1700), make_listing("2", price_total=1000)]
    go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=5))
    src.listings = [make_listing("1", price_total=1500), make_listing("2", price_total=1000)]
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert len(n.messages) == 1 and "Price drop" in n.messages[0]
    assert st.matches["fake:1"]["price_drop_from"] == 1700
    # matched & dropped >= 3%
    src.listings = [make_listing("1", price_total=1400), make_listing("2", price_total=1000)]
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=15))
    assert len(n.messages) == 1 and "Price drop" in n.messages[0]
    # tiny drop (<3%) is silent
    src.listings = [make_listing("1", price_total=1390), make_listing("2", price_total=1000)]
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=20))
    assert n.messages == []


def test_health_alert_and_recovery(cfg, tmp_path):
    good = FakeSource("good", listings(1))
    bad = FakeSource("bad", raises=SourceBlocked("blocked"))
    alerts = []
    for i in range(6):
        code, n, st = go(cfg, tmp_path, [good, bad], now=T0 + timedelta(minutes=10 * i))
        assert code == 0  # one source works
        alerts += [m for m in n.messages if "failed" in m]
    assert len(alerts) == 1 and "blocking automated checks" in alerts[0]
    code, n, st = go(cfg, tmp_path, [good, bad], now=T0 + timedelta(minutes=70))
    assert not [m for m in n.messages if "failed" in m]  # once only
    bad.raises = None
    code, n, st = go(cfg, tmp_path, [good, bad], now=T0 + timedelta(minutes=80))
    assert any("working again" in m for m in n.messages)
    assert st.health["bad"]["status"] == "ok" and st.health["bad"]["consecutive_failures"] == 0


def test_all_sources_failing_exit_code_and_generic_exception(cfg, tmp_path):
    a = FakeSource("a", raises=SourceError("layout changed"))
    b = FakeSource("b", raises=ValueError("boom"))
    code, n, st = go(cfg, tmp_path, [a, b])
    assert code == 0  # a Telegram health alert covers this; exit 1 is for crashes only
    assert st.health["b"]["status"] == "error" and "ValueError" in st.health["b"]["error"]
    assert st.health["a"]["status"] == "error"


def test_partial_status(cfg, tmp_path):
    s = FakeSource(listings=listings(1), city_errors={"leiden": SourceError("x")})
    code, n, st = go(cfg, tmp_path, [s])
    assert code == 0 and st.health["fake"]["status"] == "partial"


def test_disabled_source(cfg, tmp_path):
    a = FakeSource("a", reason="IMAP not configured")
    b = FakeSource("b", listings(1))
    cfg["disabled_sources"] = ["c"]
    c = FakeSource("c", raises=SourceError("x"))
    code, n, st = go(cfg, tmp_path, [a, b, c])
    assert code == 0
    assert st.health["a"]["status"] == "disabled" and st.health["a"]["note"] == "IMAP not configured"
    assert st.health["c"]["status"] == "disabled"
    assert not [m for m in n.messages if "failed" in m]


def test_non_per_city_source_drops_unconfigured_cities(cfg, tmp_path):
    s = FakeSource("mail", [make_listing("1", source="mail", city="utrecht"), make_listing("2", source="mail", city="leiden")], per_city=False)
    code, n, st = go(cfg, tmp_path, [s])
    assert "fake" not in st.seen and list(st.seen) == ["mail:2"]


def test_dry_run_writes_nothing(cfg, tmp_path):
    s = FakeSource(listings=listings(2))
    go(cfg, tmp_path, [s], write=False)
    assert not (tmp_path / "data").exists() and not (tmp_path / "docs").exists()


def test_sources_filter(cfg, tmp_path):
    a, b = FakeSource("a", listings(1)), FakeSource("b", listings(1))
    code, n, st = go(cfg, tmp_path, [a, b], only=["a"])
    assert "a" in st.health and "b" not in st.health


def test_changed_detection_and_heartbeat(cfg, tmp_path):
    s = FakeSource(listings=listings(2))
    go(cfg, tmp_path, [s])
    f = tmp_path / "data" / "meta.json"
    first = f.read_text()
    go(cfg, tmp_path, [s], now=T0 + timedelta(minutes=10))
    assert f.read_text() == first  # nothing but timestamps changed: not rewritten
    go(cfg, tmp_path, [s], now=T0 + timedelta(minutes=65))
    assert f.read_text() != first  # heartbeat


def test_cross_source_duplicate_not_notified_twice(cfg, tmp_path):
    a = FakeSource("a", [make_listing("1", source="a", price_total=1000, address="Dalton 5")])
    b = FakeSource("b", [make_listing("7", source="b", price_total=1000, address="Dalton 5")])
    go(cfg, tmp_path, [a])
    code, n, st = go(cfg, tmp_path, [a, b], now=T0 + timedelta(minutes=10))
    assert n.messages == []  # b's listing is a duplicate of a's (same address+price)... b baseline is suppressed
    assert "b:7" in st.seen and len(st.matches) == 1


def test_digest_once_a_day(cfg, tmp_path):
    s = FakeSource(listings=listings(1))
    go(cfg, tmp_path, [s])  # first ever run: no digest, marked
    next_day = T0 + timedelta(days=1)
    code, n, st = go(cfg, tmp_path, [s], now=next_day)
    assert len([m for m in n.messages if "Daily digest" in m]) == 1
    code, n, st = go(cfg, tmp_path, [s], now=next_day + timedelta(minutes=10))
    assert not [m for m in n.messages if "Daily digest" in m]


def test_notifier_failure_retries_next_run(cfg, tmp_path):
    s = FakeSource(listings=listings(1))
    go(cfg, tmp_path, [s])
    s.listings.append(make_listing("50", price_total=1200))
    bad = FakeNotifier()
    bad.ok = False
    go(cfg, tmp_path, [s], notifier=bad, now=T0 + timedelta(minutes=10))
    code, n, st = go(cfg, tmp_path, [s], now=T0 + timedelta(minutes=20))
    assert len(n.messages) == 1 and "Nice flat 50" in n.messages[0]


def test_data_json_schema(cfg, tmp_path):
    go(cfg, tmp_path, [FakeSource(listings=listings(2))])
    d = json.loads((tmp_path / "docs" / "data.json").read_text())
    for k in ("schema", "generated_at", "last_run", "dashboard_url", "cities", "searches", "flag_labels",
              "reply_template", "sources", "listings"):
        assert k in d
    l = d["listings"][0]
    for k in ("uid", "source", "source_label", "url", "title", "city", "price_total", "first_seen",
              "last_seen", "price_drop_from", "best_tier", "matches", "flags"):
        assert k in l
    assert d["sources"][0]["status"] == "ok" and d["sources"][0]["listings_last_run"] == 2


def test_detail_page_that_rules_listing_out_removes_card(cfg, tmp_path):
    # Kamernet-style: search data has only the area, the detail page shows 1 bedroom.
    src = FakeSource(listings=listings(1))
    go(cfg, tmp_path, [src])
    src.detail_calls = 0
    src.listings.append(make_listing("77", price_total=1300, bedrooms=None, rooms=None, area_m2=56))

    def one_bedroom(listing, http):
        src.detail_calls += 1
        listing.bedrooms = 1
        listing.rooms = 2
        return listing
    src.fetch_details = one_bedroom
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert src.detail_calls == 1
    assert non_digest(n) == []
    assert "fake:77" not in st.matches
    assert st.seen["fake:77"]["matched"] == []
    data = json.loads((tmp_path / "docs" / "data.json").read_text())
    assert all(l["uid"] != "fake:77" for l in data["listings"])


def test_detail_findings_survive_later_runs(cfg, tmp_path):
    # Search page: €841 (really per person), 3 rooms. Detail page: €1,682 total -> too expensive for 2.
    pp = make_listing("pp", price_total=841, bedrooms=None, rooms=3, area_m2=72)
    # Search page: bedrooms unknown. Detail page: 2 bedrooms.
    unk = make_listing("unk", price_total=1300, bedrooms=None, rooms=None, area_m2=60)
    src = FakeSource(listings=[pp, unk])

    def details(listing, http):
        src.detail_calls += 1
        if listing.source_id == "pp":
            listing.price_total = 1682
        else:
            listing.bedrooms = 2
        return listing
    src.fetch_details = details
    go(cfg, tmp_path, [src])
    assert src.detail_calls == 2

    def fresh():  # what the search page shows on every later run
        return [make_listing("pp", price_total=841, bedrooms=None, rooms=3, area_m2=72),
                make_listing("unk", price_total=1300, bedrooms=None, rooms=None, area_m2=60)]
    src.listings = fresh()
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert src.detail_calls == 2  # no new detail fetches
    assert non_digest(n) == []
    assert "fake:pp" not in st.matches  # stays excluded
    card = st.matches["fake:unk"]
    assert card["listing"]["bedrooms"] == 2 and "bedrooms_unknown" not in card["flags"]
    # The landlord lowers the price on the search page: the search page wins over the old detail price.
    src.listings = fresh()
    src.listings[0].price_total = 790
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=20))
    assert src.detail_calls == 3  # re-checked before announcing a "price drop"
    assert non_digest(n) == []  # the detail page still says €1,682: no false alert
    assert "fake:pp" not in st.matches


def test_rooms_only_listing_never_matches(cfg):
    from hunter.filters import evaluate
    l = make_listing("r", description="Let op: er zijn alleen nog losse kamers beschikbaar.")
    r = evaluate(l, cfg)
    assert not r.matched and "rooms_only" in r.flags


def test_cross_source_duplicate_stays_hidden_on_later_runs(cfg, tmp_path):
    a = FakeSource("alpha", listings=[make_listing("1", source="alpha", address="Daltonstraat 12")])
    go(cfg, tmp_path, [a])
    b = FakeSource("beta", listings=[make_listing("9", source="beta", address="Daltonstraat 12")])
    b2 = FakeSource("gamma", listings=[make_listing("5", source="gamma", address="Other 1")])
    for minutes in (10, 20):
        code, n, st = go(cfg, tmp_path, [a, b, b2], now=T0 + timedelta(minutes=minutes))
        assert "beta:9" not in st.matches and st.seen["beta:9"]["dup_of"].startswith("alpha")


def test_known_listing_that_starts_matching_is_alerted(cfg, tmp_path):
    src = FakeSource(listings=[make_listing("1"), make_listing("2", bedrooms=1, rooms=2)])
    go(cfg, tmp_path, [src])
    src.listings[1] = make_listing("2", bedrooms=2, rooms=3)  # the landlord fixed the bedroom count
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert len(non_digest(n)) == 1 and "Nice flat 2" in non_digest(n)[0]


def test_unmatched_listing_does_not_hide_matching_twin_on_other_site(cfg, tmp_path):
    a = FakeSource("alpha", listings=[make_listing("1", source="alpha", address="Kerkstraat 5", bedrooms=1, rooms=2)])
    b = FakeSource("beta", listings=[make_listing("9", source="beta", address="Kerkstraat 5")])
    go(cfg, tmp_path, [a, b])
    st = State(str(tmp_path / "data"), cfg)
    assert "beta:9" in st.matches and not st.seen["beta:9"].get("dup_of")


def test_no_unverified_price_drop_when_detail_budget_is_used_up(cfg, tmp_path):
    cfg["detail_fetch_max_per_run"] = 0
    cfg["first_run_detail_max"] = 5
    src = FakeSource(listings=[make_listing("pp", price_total=841, bedrooms=None, rooms=3)])

    def details(listing, http):
        listing.price_total = 1300  # the detail page shows the real total
        return listing
    src.fetch_details = details
    go(cfg, tmp_path, [src])
    src.listings = [make_listing("pp", price_total=850, bedrooms=None, rooms=3)]  # per-person price went up
    code, n, st = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert non_digest(n) == []  # no false "€1.300 -> €850" alert
    assert st.seen["fake:pp"]["last_price"] == 1300


def test_secrets_are_redacted_from_error_text(monkeypatch):
    from hunter.runner import _describe
    monkeypatch.setenv("IMAP_PASSWORD", "hunter2-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCdef")
    msg = _describe(RuntimeError("login failed for hunter2-secret at https://x/bot123456%3AABCdef/send"))
    assert "hunter2-secret" not in msg and "ABCdef" not in msg
