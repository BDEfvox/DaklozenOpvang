from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hunter.backup import choose_sources, github_stale, parse_repo, run_backup
from tests.conftest import FakeNotifier, FakeSource, make_listing

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
REPO = "someone/house-hunter"


def fake_github(health, last_run, seen=None):
    files = {"data/health.json": health, "data/meta.json": {"last_run": last_run}, "data/seen.json": seen or {}}
    calls = []

    def fetch(url):
        calls.append(url)
        for path, data in files.items():
            if url.endswith(path):
                return data
        raise IOError("404")
    fetch.calls = calls
    return fetch


def go(cfg, tmp_path, sources, fetch, now=T0, mode="auto", notifier=None):
    n = notifier or FakeNotifier()
    code = run_backup(cfg, mode=mode, now=now, fetch=fetch, notifier=n, http=object(),
                      sources={s.name: s for s in sources}, repo=REPO, data_dir=str(tmp_path / "local"))
    return code, n


def test_parse_repo():
    assert parse_repo("https://github.com/sam/house-hunter.git\n") == "sam/house-hunter"
    assert parse_repo("git@github.com:sam/house-hunter.git") == "sam/house-hunter"
    assert parse_repo("https://github.com/sam/house-hunter") == "sam/house-hunter"
    assert parse_repo("") is None and parse_repo("https://gitlab.com/a/b") is None


def test_choose_sources_and_stale():
    health = {"a": {"status": "ok"}, "b": {"status": "blocked"}, "c": {"status": "error"},
              "d": {"status": "partial"}, "gone": {"status": "blocked"}}
    assert choose_sources(health, ["a", "b", "c", "d"]) == ["b", "c"]
    assert github_stale({"last_run": "2026-11-09T08:00:00Z"}, T0, 3)
    assert not github_stale({"last_run": "2026-11-09T11:30:00Z"}, T0, 3)
    assert github_stale({}, T0, 3)


def test_runs_only_sites_github_cannot_reach_and_tags_messages(cfg, tmp_path):
    ok = FakeSource("okay", listings=[make_listing("1", source="okay")])
    blocked = FakeSource("walled", listings=[make_listing("2", source="walled")])
    fetch = fake_github({"okay": {"status": "ok"}, "walled": {"status": "blocked"}}, "2026-11-09T11:50:00Z")
    code, n = go(cfg, tmp_path, [ok, blocked], fetch)
    assert code == 0
    assert all(m.startswith("💻 <i>Mac backup</i>\n") for m in n.messages)
    joined = "\n".join(n.messages)
    assert "Nice flat 2" in joined and "Nice flat 1" not in joined  # GitHub covers "okay"
    assert "Daily digest" not in joined  # GitHub sends the digest


def test_nothing_to_do_when_github_reaches_everything(cfg, tmp_path):
    src = FakeSource("okay", listings=[make_listing("1", source="okay")])
    fetch = fake_github({"okay": {"status": "ok"}}, "2026-11-09T11:50:00Z")
    code, n = go(cfg, tmp_path, [src], fetch)
    assert code == 0 and n.messages == []
    assert not any(u.endswith("seen.json") for u in fetch.calls)


def test_listings_github_already_knows_are_not_sent_again(cfg, tmp_path):
    # GitHub saw "walled" listings before the site started blocking it.
    known = {"walled:2": {"first_seen": "2026-11-01T00:00:00Z", "last_seen": "2026-11-08T00:00:00Z",
                          "last_price": 1400, "matched": ["2 people", "3 people"], "source": "walled"}}
    blocked = FakeSource("walled", listings=[make_listing("2", source="walled"), make_listing("3", source="walled")])
    fetch = fake_github({"walled": {"status": "blocked"}}, "2026-11-09T11:50:00Z", seen=known)
    code, n = go(cfg, tmp_path, [blocked], fetch)
    listing_msgs = [m for m in n.messages if "Nice flat" in m]
    assert len(listing_msgs) == 1 and "Nice flat 3" in listing_msgs[0]  # only the really new one
    # second run: nothing new
    code, n2 = go(cfg, tmp_path, [blocked], fetch, now=T0 + timedelta(minutes=10))
    assert n2.messages == []


def test_watchdog_warns_once_when_github_stops_and_once_when_back(cfg, tmp_path):
    src = FakeSource("okay", listings=[make_listing("1", source="okay")])
    stale = fake_github({"okay": {"status": "ok"}}, "2026-11-09T06:00:00Z")
    code, n = go(cfg, tmp_path, [src], stale)
    assert len(n.messages) == 1 and "hasn't saved anything" in n.messages[0] and REPO in n.messages[0]
    code, n = go(cfg, tmp_path, [src], stale, now=T0 + timedelta(minutes=10))
    assert n.messages == []  # no repeat
    fresh = fake_github({"okay": {"status": "ok"}}, "2026-11-09T12:15:00Z")
    code, n = go(cfg, tmp_path, [src], fresh, now=T0 + timedelta(minutes=20))
    assert len(n.messages) == 1 and "running again" in n.messages[0]


def test_github_unreachable_or_no_repo_does_nothing(cfg, tmp_path):
    src = FakeSource("okay", listings=[make_listing("1", source="okay")])

    def broken(url):
        raise IOError("offline")
    code, n = go(cfg, tmp_path, [src], broken)
    assert code == 0 and n.messages == []
    n2 = FakeNotifier()
    code = run_backup(cfg, mode="auto", now=T0, fetch=broken, notifier=n2, http=object(),
                      sources={"okay": src}, repo=None, data_dir=str(tmp_path / "x"))
    assert code == 0 and n2.messages == []


def test_mode_all_runs_everything(cfg, tmp_path):
    a = FakeSource("a", listings=[make_listing("1", source="a")])
    b = FakeSource("b", listings=[make_listing("2", source="b")])

    def never(url):
        raise AssertionError("mode all must not read GitHub")
    code, n = go(cfg, tmp_path, [a, b], never, mode="all")
    joined = "\n".join(n.messages)
    assert code == 0 and "Nice flat 1" in joined and "Nice flat 2" in joined
