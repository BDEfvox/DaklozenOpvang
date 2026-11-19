"""RelayClient: the Python side of the subscriber relay (offline, fake sessions)."""
from __future__ import annotations

from typing import Any, List

import pytest

from hunter import redact as redact_mod
from hunter.notify.subscribers import RelayClient

SECRET = "pull-secret-for-tests-123"
URL = "https://relay.example.workers.dev"


@pytest.fixture(autouse=True)
def clean_redaction():
    saved = set(redact_mod._extra)
    yield
    redact_mod._extra.clear()
    redact_mod._extra.update(saved)


class Resp:
    def __init__(self, status: int = 200, data: Any = None, bad_json: bool = False) -> None:
        self.status_code = status
        self._data = data
        self._bad = bad_json

    def json(self) -> Any:
        if self._bad:
            raise ValueError("not json")
        return self._data


class Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: List[dict] = []

    def _next(self, kind: str, url: str, **kw: Any) -> Any:
        self.calls.append(dict(kind=kind, url=url, **kw))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url: str, **kw: Any) -> Any:
        return self._next("get", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self._next("post", url, **kw)


def client(session: Session, sleeps: List[float] = None) -> RelayClient:
    sleeps = sleeps if sleeps is not None else []
    return RelayClient(URL + "/", SECRET, session=session, sleep=sleeps.append)


def test_list_success_sends_bearer_and_user_agent(capsys):
    s = Session(Resp(200, {"chats": ["123456789", "-1001234567890"]}))
    assert client(s).list() == ["123456789", "-1001234567890"]
    call = s.calls[0]
    assert call["url"] == URL + "/subscribers"
    assert call["headers"]["Authorization"] == "Bearer " + SECRET
    assert call["headers"]["User-Agent"] == "house-hunter"
    assert capsys.readouterr().err == ""


def test_list_retries_three_times_with_backoff_then_gives_up(capsys):
    sleeps: List[float] = []
    s = Session(Resp(500))
    assert client(s, sleeps).list() is None
    assert len(s.calls) == 3 and len(sleeps) == 2 and all(x > 0 for x in sleeps)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "[relay]" in err and SECRET not in err


def test_list_recovers_on_a_later_try():
    s = Session(RuntimeError("boom"), Resp(502), Resp(200, {"chats": ["42424242"]}))
    assert client(s).list() == ["42424242"]
    assert len(s.calls) == 3


def test_list_wrong_secret_is_not_retried(capsys):
    s = Session(Resp(403))
    assert client(s).list() is None
    assert len(s.calls) == 1
    assert "403" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"chats": "123"}, {"chats": None}, {"other": []}])
def test_list_bad_payloads_give_none(payload, capsys):
    assert client(Session(Resp(200, payload))).list() is None
    assert capsys.readouterr().err.count("\n") == 1


def test_list_bad_json_gives_none():
    assert client(Session(Resp(200, bad_json=True))).list() is None


def test_list_keeps_only_valid_string_ids_without_duplicates():
    chats = ["12345678", 12345678, "abc", "", "1.5", "--5", "-100123456789", "12345678", None, ["1"],
             "123456789012345678901", "1\n2", " 5", "99999999"]
    assert client(Session(Resp(200, {"chats": chats}))).list() == ["12345678", "-100123456789", "99999999"]


def test_list_is_capped_at_200():
    chats = [str(1000000 + i) for i in range(500)]
    got = client(Session(Resp(200, {"chats": chats}))).list()
    assert got == chats[:200]


def test_list_registers_every_id_as_secret_before_anything_logs_it():
    client(Session(Resp(200, {"chats": ["555000111", "-100777888999"]}))).list()
    shown = redact_mod.redact("sent to 555000111 and -100777888999 and chat_id=%2D100777888999")
    assert "555000111" not in shown and "100777888999" not in shown


def test_unusable_settings_never_raise(capsys):
    assert RelayClient("", SECRET, session=Session(Resp())).list() is None
    assert RelayClient("ftp://x", SECRET, session=Session(Resp())).list() is None
    assert RelayClient(URL, "", session=Session(Resp())).list() is None
    assert RelayClient(URL, "", session=Session(Resp())).remove("12345678") is False


def test_network_errors_print_only_the_exception_type(capsys):
    boom = ConnectionError("failed to reach %s with %s" % (URL, SECRET))
    assert client(Session(boom)).list() is None
    err = capsys.readouterr().err
    assert "ConnectionError" in err and SECRET not in err and URL not in err


def test_remove_posts_the_id_and_hides_it(capsys):
    s = Session(Resp(204))
    assert client(s).remove("-100999888777") is True
    call = s.calls[0]
    assert call["url"] == URL + "/subscribers/remove"
    assert call["json"] == {"chat_id": "-100999888777"}
    assert call["headers"]["Authorization"] == "Bearer " + SECRET and call["headers"]["User-Agent"] == "house-hunter"
    assert "100999888777" not in redact_mod.redact("x -100999888777 y")


def test_remove_failure_returns_false_and_logs_one_clean_line(capsys):
    assert client(Session(Resp(500))).remove("-100999888777") is False
    assert client(Session(OSError("x"))).remove("-100999888777") is False
    err = capsys.readouterr().err
    assert err.count("\n") == 2 and "100999888777" not in err and SECRET not in err


def test_migrate_posts_both_ids(capsys):
    s = Session(Resp(204))
    assert client(s).migrate("-4455667788", "-1004455667788") is True
    assert s.calls[0]["url"] == URL + "/subscribers/migrate"
    assert s.calls[0]["json"] == {"from": "-4455667788", "to": "-1004455667788"}
    assert client(Session(Resp(400))).migrate("-4455667788", "-1004455667788") is False
    err = capsys.readouterr().err
    assert "4455667788" not in err


@pytest.mark.parametrize("bad", ["", "abc", "1.5", None, "12345678901234567890123"])
def test_remove_and_migrate_refuse_invalid_ids_without_a_call(bad):
    s = Session(Resp(204))
    c = client(s)
    assert c.remove(bad) is False
    assert c.migrate(bad, "123456") is False
    assert c.migrate("123456", bad) is False
    assert s.calls == []


def test_the_secret_is_never_sent_over_plain_http(capsys):
    calls = []

    class Session:
        def get(self, url, **kw):
            calls.append(url)
            raise AssertionError("no request expected")

    assert RelayClient("http://relay.example.workers.dev", "secret", session=Session()).list() is None
    assert calls == [] and "not usable" in capsys.readouterr().err
