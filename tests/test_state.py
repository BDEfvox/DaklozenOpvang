from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hunter.state import State, iso

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def test_roundtrip_and_atomic(tmp_path, cfg):
    s = State(str(tmp_path), cfg)
    s.seen["a:1"] = {"first_seen": iso(T0), "last_seen": iso(T0), "last_price": 1}
    assert s.save(T0)
    assert not list(tmp_path.glob("*.tmp"))
    s2 = State(str(tmp_path), cfg)
    assert s2.seen["a:1"]["last_price"] == 1


def test_timestamp_only_change_not_written_until_heartbeat(tmp_path, cfg):
    s = State(str(tmp_path), cfg)
    s.seen["a:1"] = {"first_seen": iso(T0), "last_seen": iso(T0), "last_price": 1}
    s.save(T0)
    s = State(str(tmp_path), cfg)
    s.seen["a:1"]["last_seen"] = iso(T0 + timedelta(minutes=10))
    s.meta["last_run"] = iso(T0 + timedelta(minutes=10))
    assert not s.changed()
    assert not s.save(T0 + timedelta(minutes=10))
    assert s.save(T0 + timedelta(minutes=61))  # heartbeat
    s.seen["a:1"]["last_price"] = 2
    assert s.changed()
    assert s.save(T0 + timedelta(minutes=62))


def test_prune(tmp_path, cfg):
    s = State(str(tmp_path), cfg)
    old = iso(T0 - timedelta(days=50))
    s.seen["a:old"] = {"last_seen": old}
    s.seen["a:new"] = {"last_seen": iso(T0)}
    s.matches["a:old"] = {"last_seen": old}
    s.save(T0)
    assert "a:old" not in s.seen and "a:new" in s.seen and "a:old" not in s.matches
