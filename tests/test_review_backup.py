from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hunter.backup import run_backup, seed_seen
from hunter.config import load_config
from hunter.state import State, iso
from tests.conftest import FakeNotifier, FakeSource, make_listing
from tests.test_backup import fake_github


T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # 13:00 Amsterdam, after the digest hour


@pytest.fixture
def cfg():
    c = load_config()
    c["dashboard_url"] = "https://example.github.io/house-hunter/"
    return c


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_HH11_mac_backup_repeats_a_drop_github_already_saw(cfg, tmp_path):
    """R1 HH#11. Local memory 1400; GitHub saw 1300 later; feed still 1300 -> Mac alerts the drop again."""
    data = tmp_path / "local"
    st = State(str(data), cfg)
    st.seen["fake:1"] = {"first_seen": iso(T0), "last_seen": iso(T0), "last_price": 1400, "source": "fake",
                         "matched": ["2 people", "3 people"], "notified_at": iso(T0)}
    st.meta["last_run"] = iso(T0)
    st.save(T0, force=True)
    later = at(120)
    gh_seen = {"fake:1": {"first_seen": iso(T0), "last_seen": iso(at(110)), "last_price": 1300, "source": "fake",
                          "matched": ["2 people", "3 people"], "notified_at": iso(T0)}}
    answers = {"data/health.json": {"fake": {"status": "blocked"}}, "data/meta.json": {"last_run": iso(later)},
               "data/seen.json": gh_seen}

    def fetch(url):
        return next(v for k, v in answers.items() if url.endswith(k))

    n = FakeNotifier()
    run_backup(cfg, mode="auto", now=later, fetch=fetch, notifier=n, http=object(),
               sources={"fake": FakeSource(listings=[make_listing("1", price_total=1300)])},
               repo="owner/repo", data_dir=str(data))
    assert not [m for m in n.messages if "Price drop" in m]


def test_backup_keeps_newer_local_price_without_repeating_the_drop(cfg, tmp_path):
    data = tmp_path / "local"
    state = State(str(data), cfg)
    state.seen["fake:1"] = {
        "first_seen": iso(T0), "last_seen": iso(at(110)), "last_price": 1300,
        "source": "fake", "matched": ["2 people", "3 people"], "notified_at": iso(at(110)),
    }
    state.meta["last_run"] = iso(at(110))
    state.save(at(110), force=True)
    github_seen = {"fake:1": {
        "first_seen": iso(T0), "last_seen": iso(at(100)), "last_price": 1400,
        "source": "fake", "matched": ["2 people", "3 people"], "notified_at": iso(T0),
    }}
    fetch = fake_github({"fake": {"status": "blocked"}}, iso(at(120)), seen=github_seen)
    notifier = FakeNotifier()

    assert run_backup(cfg, now=at(120), fetch=fetch, notifier=notifier, http=object(),
                      sources={"fake": FakeSource(listings=[make_listing("1", price_total=1300)])},
                      repo="owner/repo", data_dir=str(data)) == 0
    assert State(str(data), cfg).seen["fake:1"]["last_price"] == 1300
    assert notifier.messages == []


@pytest.mark.parametrize("local_notified", [None, iso(T0), iso(at(20))])
def test_seed_seen_merges_history_when_local_observation_is_newer(cfg, tmp_path, local_notified):
    state = State(str(tmp_path), cfg)
    state.seen["fake:1"] = {
        "first_seen": iso(T0), "last_seen": iso(at(30)), "last_price": 1300,
        "notified_at": local_notified,
    }
    github_seen = {"fake:1": {
        "first_seen": iso(at(-10)), "last_seen": iso(at(25)), "last_price": 1400,
        "notified_at": iso(at(10)),
    }}

    assert seed_seen(state, github_seen, iso(at(40))) == 0
    assert state.seen["fake:1"] == {
        "first_seen": iso(at(-10)), "last_seen": iso(at(30)), "last_price": 1300,
        "notified_at": max(local_notified or "", iso(at(10))),
    }


def test_seed_seen_uses_newer_github_fields_and_preserves_local_bookkeeping(cfg, tmp_path):
    state = State(str(tmp_path), cfg)
    state.seen["fake:1"] = {
        "first_seen": iso(T0), "last_seen": iso(at(20)), "last_price": 1400,
        "matched": ["2 people"], "source": "fake", "fp": "local fingerprint",
        "dup_of": "other:1", "details": {"old": True}, "detail_tries": 3,
        "notified_at": iso(at(15)),
    }
    github_seen = {
        "fake:1": {
            "first_seen": iso(at(10)), "last_seen": iso(at(30)), "last_price": 1300,
            "matched": ["2 people", "3 people"], "details": {"new": True},
        },
        "fake:2": {"last_price": 1500},
    }

    assert seed_seen(state, github_seen, iso(at(40))) == 1
    assert state.seen["fake:1"] == {
        "first_seen": iso(T0), "last_seen": iso(at(30)), "last_price": 1300,
        "matched": ["2 people", "3 people"], "source": "fake", "fp": "local fingerprint",
        "details": {"new": True}, "detail_tries": 3, "notified_at": iso(at(15)),
        "from_github": True,
    }
    assert state.seen["fake:2"] == {
        "first_seen": iso(at(40)), "last_seen": iso(at(40)), "last_price": 1500,
        "source": "fake", "from_github": True,
    }


@pytest.mark.parametrize(
    "local_last, github_last, expected_price",
    [
        ({}, {"last_seen": iso(T0)}, 1300),
        ({"last_seen": "garbage"}, {"last_seen": iso(T0)}, 1300),
        ({"last_seen": 42}, {"last_seen": iso(T0)}, 1300),
        ({"last_seen": iso(T0)}, {}, 1400),
        ({"last_seen": iso(T0)}, {"last_seen": "garbage"}, 1400),
        ({"last_seen": "garbage"}, {}, 1400),
        ({"last_seen": iso(T0)}, {"last_seen": "2026-11-09T13:00:00+01:00"}, 1400),
    ],
)
def test_seed_seen_compares_observation_timestamps(cfg, tmp_path, local_last, github_last, expected_price):
    state = State(str(tmp_path), cfg)
    state.seen["fake:1"] = dict(local_last, last_price=1400)
    github_seen = {"fake:1": dict(github_last, last_price=1300, fp="github", dup_of="other:1")}

    assert seed_seen(state, github_seen, iso(at(10))) == 0
    expected = dict(local_last, last_price=1400)
    if expected_price == 1300:
        expected = dict(github_seen["fake:1"], from_github=True)
    assert state.seen["fake:1"] == expected


def test_seed_seen_skips_entries_that_are_not_dicts(cfg, tmp_path):
    state = State(str(tmp_path), cfg)
    state.seen = {"fake:1": {"last_price": 1400}, "fake:3": None}
    github_seen = {"fake:1": "garbage", "fake:2": [], "fake:3": {"last_price": 1300}}

    assert seed_seen(state, github_seen, iso(T0)) == 0
    assert state.seen == {"fake:1": {"last_price": 1400}, "fake:3": None}
