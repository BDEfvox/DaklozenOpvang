from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hunter import __main__ as cli, dashboard, sources
from hunter.notify import telegram
from tests.conftest import FakeSource


@pytest.mark.parametrize("quiet,hour,minute,expected", [
    ([0, 0], 23, 0, False),     # 00:00 Amsterdam (next day)
    ([7, 7], 6, 0, False),      # 07:00
    ([23, 7], 22, 30, True),    # 23:30
    ([23, 7], 2, 0, True),      # 03:00
    ([23, 7], 11, 0, False),   # 12:00
    ([9, 17], 7, 59, False),   # 08:59
    ([9, 17], 8, 0, True),     # 09:00
    ([9, 17], 16, 0, False),   # 17:00
])
def test_quiet_hours(quiet, hour, minute, expected):
    now = datetime(2026, 11, 9, hour, minute, tzinfo=timezone.utc)
    assert telegram.in_quiet_hours(now, quiet, "Europe/Amsterdam") is expected


@pytest.mark.parametrize("quiet", [
    ["a", 7], ["23", 7], [25, 7], [-1, 7], [23.5, 7], [False, 7],
    [23], [23, 7, 1], {"start": 23, "end": 7}, 23, None,
])
def test_malformed_quiet_hours_are_disabled(quiet):
    now = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)
    assert not telegram.in_quiet_hours(now, quiet, "Europe/Amsterdam")


def test_dashboard_loads_sources_once_and_preserves_labels(cfg, monkeypatch):
    source = FakeSource("known")
    custom = FakeSource("custom")
    calls = []
    written = {}

    def all_sources():
        calls.append(True)
        return {source.name: source}

    def write_json(path, data):
        written.update(data)

    monkeypatch.setattr(sources, "all_sources", all_sources)
    monkeypatch.setattr(dashboard, "atomic_write_json", write_json)
    state = SimpleNamespace(
        health={source.name: {}, custom.name: {"label": custom.label, "homepage": custom.homepage}},
        matches={
            "known:1": {"listing": {"source": source.name}},
            "custom:1": {"listing": {"source": custom.name}},
        },
        meta={},
    )
    dashboard.write_dashboard(cfg, state)

    assert {row["name"]: (row["label"], row["homepage"]) for row in written["sources"]} == {
        source.name: (source.label, source.homepage),
        custom.name: (custom.label, custom.homepage),
    }
    assert {row["uid"]: row["source_label"] for row in written["listings"]} == {
        "known:1": source.label,
        "custom:1": custom.name,
    }
    assert len(calls) == 1


@pytest.fixture
def telegram_setup(monkeypatch):
    token = "123456:test-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    writes = []
    monkeypatch.setattr(cli, "write_env_value", lambda key, value: writes.append((key, value)))

    def run(updates):
        events = []
        now = 0
        responses = iter(updates)

        def sleep(seconds):
            nonlocal now
            events.append(("sleep", seconds))
            now += seconds

        def api_call(bot_token, method, payload=None, timeout=30):
            nonlocal now
            assert bot_token == token
            events.append(method)
            if method == "getMe":
                return {"ok": True, "result": {"username": "house_test_bot"}}
            if method == "sendMessage":
                assert payload["chat_id"] == "-1001"
                return {"ok": True}
            assert method == "getUpdates"
            now += 1
            return next(responses, {"ok": True, "result": []})

        monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: now, sleep=sleep))
        monkeypatch.setattr(telegram, "api_call", api_call)
        return cli.cmd_telegram_setup(Namespace(chat_id=None, wait=10)), events, writes

    return run, token


@pytest.mark.parametrize("code,description", [(401, "Unauthorized"), (409, "Webhook is active")])
def test_telegram_setup_stops_on_permanent_error(telegram_setup, capsys, code, description):
    run, token = telegram_setup
    encoded = token.replace(":", "%3a")
    result, events, writes = run([
        {"ok": False, "error_code": code, "description": f"{description} ({token} / {encoded})"},
    ])

    assert result == 1
    assert events == ["getMe", "getUpdates"]
    assert writes == []
    output = capsys.readouterr().out
    assert output.count(description) == 1
    assert token not in output and encoded not in output


@pytest.mark.parametrize("transient_error", [False, True])
def test_telegram_setup_finds_group(telegram_setup, capsys, transient_error):
    run, _ = telegram_setup
    updates = []
    events_before_success = ["getMe"]
    if transient_error:
        updates.append({"ok": False, "error_code": 500, "description": "Server error"})
        events_before_success += ["getUpdates", ("sleep", 3)]
    updates.append({"ok": True, "result": [
        {"update_id": 12, "message": {"chat": {"id": -1001, "type": "group", "title": "Rental search"}}},
    ]})

    result, events, writes = run(updates)

    assert result == 0
    assert events == events_before_success + ["getUpdates", "sendMessage"]
    assert writes == [("TELEGRAM_CHAT_ID", "-1001")]
    output = capsys.readouterr().out
    assert "Found group: Rental search" in output
    assert output.count("Server error") == int(transient_error)
