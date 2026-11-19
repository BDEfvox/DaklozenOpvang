"""BroadcastNotifier, make_notifier with a relay, and the runner's owner-only messages (offline)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from hunter import redact as redact_mod
from hunter.backup import TaggedNotifier, run_backup
from hunter.notify import telegram as tg
from hunter.notify.telegram import BroadcastNotifier, ConsoleNotifier, TelegramNotifier, make_notifier
from hunter.runner import run_once
from hunter.sources.base import SourceBlocked
from hunter.state import State
from tests.conftest import FakeNotifier, FakeSource, make_listing

TOKEN = "123456:TESTTOKEN-abcdefghijklmnop"
OWNER = "-1009990001111"
SUB_A = "-1001234567801"
SUB_B = "7654321001"
SUB_C = "8765432002"
RELAY_SECRET = "relay-pull-secret-xyz-789"
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_redaction():
    saved = set(redact_mod._extra)
    yield
    redact_mod._extra.clear()
    redact_mod._extra.update(saved)


class FakeTelegram:
    """Stands in for requests: records (chat id, text) and answers per chat."""

    def __init__(self, answers: Dict[str, Dict[str, Any]] = None) -> None:
        self.answers = answers or {}
        self.sent: List[Any] = []

    def post(self, url: str, json: Dict[str, Any] = None, timeout: float = 0) -> Any:
        chat = str(json["chat_id"])
        self.sent.append((chat, json["text"]))
        answer = self.answers.get(chat, {"ok": True})
        if callable(answer):
            answer = answer()
        return type("R", (), {"json": lambda self_: answer})()


class FakeRelay:
    def __init__(self) -> None:
        self.removed: List[str] = []
        self.migrated: List[Any] = []

    def remove(self, chat_id: str) -> bool:
        self.removed.append(chat_id)
        return True

    def migrate(self, old: str, new: str) -> bool:
        self.migrated.append((old, new))
        return True


def notifier(chat: str, session: FakeTelegram) -> TelegramNotifier:
    return TelegramNotifier(TOKEN, chat, min_gap=0, sleep=lambda s: None, session=session)


def broadcast(session: FakeTelegram, subs: List[str], relay: Any = None, owner: str = OWNER) -> BroadcastNotifier:
    return BroadcastNotifier(notifier(owner, session), [notifier(s, session) for s in subs], relay,
                             spacing=0, sleep=lambda s: None)


def chats(session: FakeTelegram) -> List[str]:
    return [c for c, _ in session.sent]


# ----------------------------------------------------------------------------------------- BroadcastNotifier
def test_owner_first_then_every_subscriber():
    s = FakeTelegram()
    assert broadcast(s, [SUB_A, SUB_B]).send("hello") is True
    assert chats(s) == [OWNER, SUB_A, SUB_B]


def test_owner_result_decides_even_when_subscribers_fail():
    s = FakeTelegram({SUB_A: {"ok": False, "error_code": 500, "description": "boom"}})
    assert broadcast(s, [SUB_A, SUB_B]).send("hello") is True
    assert chats(s) == [OWNER, SUB_A, SUB_B]


def test_owner_failure_sends_to_nobody_and_returns_false():
    s = FakeTelegram({OWNER: {"ok": False, "error_code": 500, "description": "boom"}})
    assert broadcast(s, [SUB_A, SUB_B]).send("hello") is False
    assert chats(s) == [OWNER]


def test_audience_owner_skips_subscribers():
    s = FakeTelegram()
    assert broadcast(s, [SUB_A]).send("health", audience="owner") is True
    assert chats(s) == [OWNER]


def test_subscriber_with_the_owners_id_or_a_duplicate_is_skipped():
    s = FakeTelegram()
    broadcast(s, [OWNER, SUB_A, SUB_A, SUB_B]).send("hello")
    assert chats(s) == [OWNER, SUB_A, SUB_B]


def test_gone_subscriber_is_reported_once_and_not_tried_again(capsys):
    s = FakeTelegram({SUB_A: {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}})
    relay = FakeRelay()
    b = broadcast(s, [SUB_A, SUB_B], relay)
    assert b.send("one") is True
    assert b.send("two") is True
    assert relay.removed == [SUB_A]
    assert chats(s).count(SUB_A) == 1 and chats(s).count(SUB_B) == 2


def test_chat_not_found_counts_as_gone_but_other_400s_do_not():
    s = FakeTelegram({SUB_A: {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
                      SUB_B: {"ok": False, "error_code": 400, "description": "Bad Request: message is too long"}})
    relay = FakeRelay()
    broadcast(s, [SUB_A, SUB_B], relay).send("hello")
    assert relay.removed == [SUB_A]


def test_migrated_subscriber_is_moved_in_the_relay_and_gets_the_message(capsys):
    new_id = "-1005550006666"
    s = FakeTelegram({SUB_A: lambda: ({"ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded",
                                       "parameters": {"migrate_to_chat_id": int(new_id)}} if len(s.sent) < 3 else {"ok": True})})
    relay = FakeRelay()
    b = broadcast(s, [SUB_A], relay)
    b.send("hello")
    assert relay.migrated == [(SUB_A, new_id)]
    assert chats(s) == [OWNER, SUB_A, new_id]
    err = capsys.readouterr().err
    assert "TELEGRAM_CHAT_ID" not in err and "supergroup" not in err  # the owner's warning is not for subscribers
    assert new_id not in err


def test_owner_migration_keeps_todays_warning(capsys):
    new_id = "-1005550007777"
    s = FakeTelegram({OWNER: lambda: ({"ok": False, "error_code": 400, "description": "x",
                                       "parameters": {"migrate_to_chat_id": int(new_id)}} if len(s.sent) < 2 else {"ok": True})})
    assert broadcast(s, []).send("hello") is True
    err = capsys.readouterr().err
    assert "WARNING: the group was upgraded to a supergroup" in err and "./go-live.sh --telegram" in err
    assert new_id not in err


def test_gone_after_a_migration_removes_the_new_id():
    new_id = "-1005550008888"
    calls = {"n": 0}

    def answer():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "error_code": 400, "description": "x", "parameters": {"migrate_to_chat_id": int(new_id)}}
        return {"ok": True}
    s = FakeTelegram({SUB_A: answer, new_id: {"ok": False, "error_code": 403, "description": "Forbidden"}})
    relay = FakeRelay()
    b = broadcast(s, [SUB_A], relay)
    b.send("one")
    b.send("two")
    b.send("three")
    assert relay.migrated == [(SUB_A, new_id)]
    assert relay.removed == [new_id]
    assert chats(s).count(new_id) == 1  # the retry after the migration got the 403; never tried again


def test_relay_errors_never_break_a_send():
    class Broken(FakeRelay):
        def remove(self, chat_id: str) -> bool:
            raise RuntimeError("relay exploded")
    s = FakeTelegram({SUB_A: {"ok": False, "error_code": 403, "description": "Forbidden"}})
    assert broadcast(s, [SUB_A, SUB_B], Broken()).send("hello") is True
    assert chats(s) == [OWNER, SUB_A, SUB_B]


def test_nothing_secret_reaches_stderr(capsys):
    s = FakeTelegram({SUB_A: {"ok": False, "error_code": 403, "description": "Forbidden: bot was kicked from the group %s" % SUB_A},
                      SUB_B: {"ok": False, "error_code": 500, "description": "oops %s" % SUB_B}})
    for cid in (OWNER, SUB_A, SUB_B):
        redact_mod.add_secret(cid)
    broadcast(s, [SUB_A, SUB_B], FakeRelay()).send("hello")
    err = capsys.readouterr().err
    for secret in (SUB_A, SUB_B, OWNER, TOKEN, RELAY_SECRET):
        assert secret not in err
    assert "100123456780" not in err


# ----------------------------------------------------------------------------------------- audience plumbing
def test_console_notifier_prints_the_audience():
    import io
    out = io.StringIO()
    ConsoleNotifier(out).send("hi", audience="owner")
    assert "audience=owner" in out.getvalue()


def test_tagged_notifier_passes_the_audience_on():
    inner = FakeNotifier()
    TaggedNotifier(inner).send("x", audience="owner")
    TaggedNotifier(inner).send("y")
    assert inner.audiences == ["owner", "all"]


# ----------------------------------------------------------------------------------------- make_notifier
@pytest.fixture
def env(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RELAY_URL", "RELAY_PULL_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", OWNER)
    return monkeypatch


def fake_relay_class(monkeypatch, result):
    seen = {}

    class Fake:
        def __init__(self, url: str, secret: str, **kw: Any) -> None:
            seen["args"] = (url, secret)

        def list(self):
            return result

    import hunter.notify.subscribers as subs
    monkeypatch.setattr(subs, "RelayClient", Fake)
    return seen


def test_make_notifier_without_relay_values_is_todays_notifier(env):
    assert type(make_notifier({})) is TelegramNotifier
    env.setenv("RELAY_URL", "https://relay.example")  # only one of the two
    assert type(make_notifier({})) is TelegramNotifier
    env.delenv("RELAY_URL")
    env.setenv("RELAY_PULL_SECRET", RELAY_SECRET)
    assert type(make_notifier({})) is TelegramNotifier


def test_make_notifier_with_relay_builds_a_broadcast(env):
    seen = fake_relay_class(env, [SUB_A, SUB_B])
    env.setenv("RELAY_URL", "https://relay.example")
    env.setenv("RELAY_PULL_SECRET", RELAY_SECRET)
    n = make_notifier({"telegram": {"quiet_hours": [23, 7]}})
    assert isinstance(n, BroadcastNotifier)
    assert seen["args"] == ("https://relay.example", RELAY_SECRET)
    assert [s.chat_id for s in n.subscribers] == [SUB_A, SUB_B]
    assert n.owner.chat_id == OWNER and n.owner.owner is True
    assert all(s.owner is False and s.quiet == [23, 7] for s in n.subscribers)


def test_make_notifier_relay_unreachable_sends_to_the_owner_only(env, capsys):
    fake_relay_class(env, None)
    env.setenv("RELAY_URL", "https://relay.example")
    env.setenv("RELAY_PULL_SECRET", RELAY_SECRET)
    n = make_notifier({})
    assert type(n) is TelegramNotifier and n.chat_id == OWNER
    assert capsys.readouterr().err == ""  # the real client prints the single line (next test)


def test_make_notifier_real_client_failure_is_quiet_about_secrets(env, capsys):
    class Down:
        def get(self, *a, **kw):
            raise ConnectionError("no route to %s" % RELAY_SECRET)
    import hunter.notify.subscribers as subs
    real = subs.RelayClient
    env.setattr(subs, "RelayClient", lambda url, secret: real(url, secret, session=Down(), sleep=lambda s: None))
    env.setenv("RELAY_URL", "https://relay.example")
    env.setenv("RELAY_PULL_SECRET", RELAY_SECRET)
    assert type(make_notifier({})) is TelegramNotifier
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "owner only" in err
    assert RELAY_SECRET not in err and OWNER not in err


def test_the_relay_secret_is_redacted_from_the_environment(env):
    env.setenv("RELAY_PULL_SECRET", RELAY_SECRET)
    assert RELAY_SECRET not in redact_mod.redact("Bearer " + RELAY_SECRET)


# ----------------------------------------------------------------------------------------- the runner
def go(cfg, tmp_path, sources, now=T0):
    n = FakeNotifier()
    state = State(str(tmp_path / "data"), cfg)
    run_once(cfg, state, {s.name: s for s in sources}, n, object(), now=now, docs_dir=str(tmp_path / "docs"))
    return n


def audience_of(n: FakeNotifier, needle: str) -> List[str]:
    return [a for m, a in zip(n.messages, n.audiences) if needle in m]


def test_runner_audiences(cfg, tmp_path):
    good = FakeSource("good", [make_listing(str(i), price_total=1000 + i * 10) for i in range(2)])
    bad = FakeSource("bad", raises=SourceBlocked("blocked"))
    first = go(cfg, tmp_path, [good, bad])
    assert audience_of(first, "House Hunter is running") == ["owner"]          # first-ever status report
    assert audience_of(first, "Nice flat") and set(audience_of(first, "Nice flat")) == {"all"}  # first-run matches
    alerts: List[str] = []
    for i in range(1, 7):
        n = go(cfg, tmp_path, [good, bad], now=T0 + timedelta(minutes=10 * i))
        alerts += audience_of(n, "failed")
    assert alerts == ["owner"]                                                  # health alert
    bad.raises = None
    n = go(cfg, tmp_path, [good, bad], now=T0 + timedelta(minutes=80))
    assert audience_of(n, "working again") == ["owner"]                          # recovery


def test_runner_new_match_digest_and_summary_go_to_everyone(cfg, tmp_path):
    src = FakeSource("good", [make_listing("1", price_total=1100)])
    go(cfg, tmp_path, [src])
    src.listings.append(make_listing("2", price_total=1200, address="Other 5"))
    n = go(cfg, tmp_path, [src], now=T0 + timedelta(minutes=10))
    assert n.messages and set(n.audiences) == {"all"}
    next_day = go(cfg, tmp_path, [src], now=T0 + timedelta(days=1))
    digest = audience_of(next_day, "Daily digest")
    assert digest == ["all"]


def test_backup_watchdog_messages_are_owner_only(cfg, tmp_path):
    n = FakeNotifier()
    stale = {"last_run": "2026-01-01T00:00:00+00:00"}
    fetch = lambda url: {} if "health" in url else stale  # noqa: E731
    run_backup(cfg, "auto", now=datetime(2026, 11, 9, tzinfo=timezone.utc), fetch=fetch, notifier=n,
               http=object(), sources={}, repo="o/r", data_dir=str(tmp_path / "local"))
    assert n.audiences == ["owner"] and "hasn't saved anything" in n.messages[0]
