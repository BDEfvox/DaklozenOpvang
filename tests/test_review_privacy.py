"""Review fix HH15: nothing that identifies the chat may reach a log (the Actions log of a public repo is public)."""
from __future__ import annotations

import pytest

from hunter import redact as redact_module
from hunter.notify import telegram as tg
from hunter.redact import add_secret, redact
from hunter.runner import redact as runner_redact

OLD_ID, NEW_ID = "-4001234567", "-1009876543210"


@pytest.fixture(autouse=True)
def clean_secrets(monkeypatch):
    monkeypatch.setattr(redact_module, "_extra", set())
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def migrating_api(calls, then=None):
    def fake_api(token, method, payload, session=None, **kw):
        calls.append(payload["chat_id"])
        if len(calls) == 1:
            return {"ok": False, "error_code": 400, "parameters": {"migrate_to_chat_id": int(NEW_ID)}}
        return then or {"ok": True}
    return fake_api


def test_HH15_supergroup_migration_does_not_print_the_chat_id(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tg, "api_call", migrating_api(calls))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", OLD_ID)
    n = tg.TelegramNotifier("123:abc", OLD_ID, sleep=lambda s: None)
    assert n.send("hello") is True
    assert calls == [OLD_ID, NEW_ID]            # the message still arrives, through the new id
    out = capsys.readouterr()
    text = out.out + out.err
    assert NEW_ID.lstrip("-") not in text and OLD_ID.lstrip("-") not in text
    assert "supergroup" in text and "go-live.sh --telegram" in text   # the owner is still told what to do


def test_error_text_after_a_migration_never_shows_the_new_id(monkeypatch, capsys):
    calls = []
    refused = {"ok": False, "error_code": 403, "description": "Forbidden: bot was kicked from chat %s" % NEW_ID}
    monkeypatch.setattr(tg, "api_call", migrating_api(calls, then=refused))
    n = tg.TelegramNotifier("123:abc", OLD_ID, sleep=lambda s: None)
    assert n.send("hello") is False
    out = capsys.readouterr()
    assert NEW_ID.lstrip("-") not in out.out + out.err and "kicked" in out.err
    assert NEW_ID not in redact("the run log mentions %s later on" % NEW_ID)


def test_redact_hides_environment_secrets_and_registered_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", OLD_ID)
    monkeypatch.setenv("IMAP_PASSWORD", "abcd efgh ijkl mnop")
    add_secret("987654321")
    add_secret("abc")   # too short to be a secret: ignored, so ordinary words are not masked
    text = redact("chat %s pw abcdefghijklmnop new 987654321 url chat_id=%%2D4001234567 abc" % OLD_ID)
    assert "4001234567" not in text and "abcdefghijklmnop" not in text and "987654321" not in text
    assert text.endswith(" abc")
    assert runner_redact is redact   # one implementation: the runner uses the same function
