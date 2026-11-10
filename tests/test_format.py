from __future__ import annotations

from hunter.filters import evaluate
from hunter.notify import format as fmt
from hunter.notify.telegram import in_quiet_hours, TelegramNotifier
from tests.conftest import make_listing
from datetime import datetime, timezone


def test_new_match_escapes_and_contains(cfg):
    l = make_listing(title="<script>alert(1)</script> & co", address="A<b>x", price_total=1450,
                     price_includes_utilities=False, furnished="unfurnished", available_from="2026-12-01")
    r = evaluate(l, cfg)
    msg = fmt.format_new_match(l, r, cfg, "Fake <Site>")
    assert "<script>" not in msg and "&lt;script&gt;" in msg
    assert "Fake &lt;Site&gt;" in msg
    assert "€1.450" in msg and "€725 pp for 2" in msg and "excl. utilities" in msg
    assert "<blockquote expandable>" in msg and "🟡" in msg
    assert "2026-12-01" in msg and len(msg) < 4000


def test_great_tier_and_flags(cfg):
    l = make_listing(price_total=1300, description="geen woningdelers, max 2 personen")
    msg = fmt.format_new_match(l, evaluate(l, cfg), cfg, "X")
    assert "🟢" in msg and "No house sharers" in msg and "Max 2 persons" in msg


def test_long_fields_stay_under_limit(cfg):
    l = make_listing(title="T" * 5000, address="A" * 5000)
    msg = fmt.format_new_match(l, evaluate(l, cfg), cfg, "X")
    assert len(msg) < 4000


def test_other_messages():
    assert "Price drop" in fmt.format_price_drop(make_listing(price_total=1300), evaluate(make_listing(price_total=1300), {"cities": ["den-haag"], "searches": [{"name": "2 people", "persons": 2, "min_bedrooms": 2, "max_total": 1600, "great_total": 1400}], "reply_template": "x"}), 1500, {"reply_template": "x"})
    assert "http://d" in fmt.format_first_run_summary(5, ["A", "B"], "http://d")
    assert "blocking automated checks" in fmt.format_health_alert("X", "blocked", 6, None)
    d = fmt.format_digest({"den-haag": 2}, [{"label": "A<", "status": "ok"}], None, "2026-11-08")
    assert "Den Haag: 2" in d and "A&lt;" in d


def test_quiet_hours():
    q = [23, 7]
    assert in_quiet_hours(datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc), q, "Europe/Amsterdam")  # 23:30
    assert not in_quiet_hours(datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc), q, "Europe/Amsterdam")
    assert in_quiet_hours(datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc), q, "Europe/Amsterdam")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))

        class R:
            def __init__(s, d):
                s.d = d

            def json(s):
                return s.d
        return R(self.responses.pop(0))


def test_telegram_429_and_migrate():
    sess = FakeSession([
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 2}},
        {"ok": False, "error_code": 400, "parameters": {"migrate_to_chat_id": -1009}},
        {"ok": True},
    ])
    sleeps = []
    n = TelegramNotifier("TOKEN", "-1", [23, 7], sleep=sleeps.append, session=sess,
                         clock=lambda: datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc))
    assert n.send("hi", url="https://x/y", dashboard_url="https://d/")
    assert n.chat_id == "-1009" and sleeps and sleeps[0] >= 2
    last = sess.calls[-1][1]
    assert last["chat_id"] == "-1009" and last["disable_notification"] is False
    assert last["link_preview_options"] == {"url": "https://x/y"}
    assert [b["text"] for b in last["reply_markup"]["inline_keyboard"][0]] == ["Open listing", "Dashboard"]


def test_token_never_leaked(capsys):
    class Boom:
        def post(self, url, json=None, timeout=None):
            raise RuntimeError("failed for " + url)
    n = TelegramNotifier("SECRETTOKEN", "-1", sleep=lambda s: None, session=Boom())
    assert n.send("x") is False
    err = capsys.readouterr()
    assert "SECRETTOKEN" not in err.out + err.err
