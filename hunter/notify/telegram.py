"""Telegram Bot API notifier (sendMessage). The bot token is never printed or logged."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

API = "https://api.telegram.org/bot%s/%s"


def sanitize(text: Any, token: Optional[str]) -> str:
    s = str(text)
    if token:
        s = s.replace(token, "***").replace(token.replace(":", "%3A"), "***").replace(token.replace(":", "%3a"), "***")
    return s


def _web_url(u: Optional[str]) -> Optional[str]:
    return u if isinstance(u, str) and u.startswith(("https://", "http://")) else None


def api_call(token: str, method: str, payload: Optional[Dict[str, Any]] = None,
             timeout: float = 30, session: Any = None) -> Dict[str, Any]:
    """Call a Bot API method; returns the decoded JSON (even for ok=false). Errors are sanitized."""
    http = session or requests
    try:
        resp = http.post(API % (token, method), json=payload or {}, timeout=timeout)
        return resp.json()
    except Exception as exc:  # requests errors contain the URL, hence the token
        # "from None": the original exception holds the request URL, which contains the token
        raise RuntimeError("Telegram request failed: %s: %s" % (type(exc).__name__, sanitize(exc, token))) from None


def in_quiet_hours(now_utc: datetime, quiet: List[int], tz_name: str) -> bool:
    if not quiet or len(quiet) != 2:
        return False
    if ZoneInfo is not None:
        try:
            local = now_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = now_utc
    else:  # pragma: no cover
        local = now_utc
    start, end = int(quiet[0]), int(quiet[1])
    h = local.hour
    return (start <= h < end) if start < end else (h >= start or h < end)


class Notifier:
    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None) -> bool:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdout
        self.sent: List[str] = []

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None) -> bool:
        self.sent.append(text)
        print("---- message ----\n%s\n[url=%s]\n" % (text, url or "-"), file=self.stream)
        return True


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, quiet_hours: Optional[List[int]] = None,
                 timezone: str = "Europe/Amsterdam", min_gap: float = 3.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = None, session: Any = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.quiet = quiet_hours or []
        self.tz = timezone
        self.min_gap = min_gap
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.session = session
        self._last = 0.0
        self.sent_count = 0

    def _payload(self, text: str, url: Optional[str], dashboard_url: Optional[str]) -> Dict[str, Any]:
        url, dashboard_url = _web_url(url), _web_url(dashboard_url)
        p: Dict[str, Any] = {
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "disable_notification": in_quiet_hours(self._clock(), self.quiet, self.tz),
        }
        if url:
            p["link_preview_options"] = {"url": url}
        else:
            p["link_preview_options"] = {"is_disabled": True}
        row = []
        if url:
            row.append({"text": "Open listing", "url": url})
        if dashboard_url:
            row.append({"text": "Dashboard", "url": dashboard_url})
        if row:
            p["reply_markup"] = {"inline_keyboard": [row]}
        return p

    def _wait_gap(self) -> None:
        gap = time.monotonic() - self._last
        if self._last and gap < self.min_gap:
            self._sleep(self.min_gap - gap)

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None) -> bool:
        retried_429 = False
        migrated = False
        while True:
            self._wait_gap()
            payload = self._payload(text, url, dashboard_url)
            try:
                data = api_call(self.token, "sendMessage", payload, session=self.session)
            except RuntimeError as exc:
                print("[telegram] %s" % exc, file=sys.stderr)
                self._last = time.monotonic()
                return False
            self._last = time.monotonic()
            if data.get("ok"):
                self.sent_count += 1
                return True
            params = data.get("parameters") or {}
            if params.get("migrate_to_chat_id") and not migrated:
                new_id = str(params["migrate_to_chat_id"])
                print("!" * 70, file=sys.stderr)
                print("[telegram] WARNING: the group was upgraded to a supergroup.\n"
                      "[telegram] Set the secret/variable TELEGRAM_CHAT_ID to the NEW id: %s" % new_id,
                      file=sys.stderr)
                print("!" * 70, file=sys.stderr)
                self.chat_id = new_id
                migrated = True
                continue
            if data.get("error_code") == 429 and not retried_429:
                retried_429 = True
                self._sleep(min(float(params.get("retry_after", 5)) + 1, 60))
                continue
            print("[telegram] sendMessage failed: %s %s" % (
                data.get("error_code"), sanitize(data.get("description", ""), self.token)), file=sys.stderr)
            return False


def make_notifier(cfg: Dict[str, Any]) -> Notifier:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set: printing messages to the console",
              file=sys.stderr)
        return ConsoleNotifier()
    t = cfg.get("telegram", {})
    return TelegramNotifier(token, chat, t.get("quiet_hours"), t.get("timezone", "Europe/Amsterdam"))
