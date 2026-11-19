"""Telegram Bot API notifier (sendMessage). The bot token is never printed or logged."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import requests

from ..redact import add_secret, redact

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
    if not isinstance(quiet, (list, tuple)) or len(quiet) != 2:
        return False
    if any(type(hour) is not int or not 0 <= hour <= 23 for hour in quiet):
        return False
    start, end = quiet
    if start == end:
        return False
    if ZoneInfo is not None:
        try:
            local = now_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = now_utc
    else:  # pragma: no cover
        local = now_utc
    h = local.hour
    return (start <= h < end) if start < end else (h >= start or h < end)


class Notifier:
    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None,
             audience: str = "all") -> bool:
        """audience "all": the owner and every subscriber; "owner": the owner's chat only."""
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdout
        self.sent: List[str] = []

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None,
             audience: str = "all") -> bool:
        self.sent.append(text)
        print("---- message [audience=%s] ----\n%s\n[url=%s]\n" % (audience, text, url or "-"), file=self.stream)
        return True


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, quiet_hours: Optional[List[int]] = None,
                 timezone: str = "Europe/Amsterdam", min_gap: float = 3.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = None, session: Any = None,
                 owner: bool = True, on_gone: Optional[Callable[[], Any]] = None,
                 on_migrate: Optional[Callable[[str], Any]] = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.quiet = quiet_hours or []
        self.tz = timezone
        self.min_gap = min_gap
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.session = session
        self.owner = owner            # False for a subscriber's chat
        self.on_gone = on_gone        # called when Telegram says the bot was removed or blocked
        self.on_migrate = on_migrate  # called with the new id when the group became a supergroup
        self._last = 0.0

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

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None,
             audience: str = "all") -> bool:
        retried_429 = False
        migrated = False
        while True:
            self._wait_gap()
            payload = self._payload(text, url, dashboard_url)
            try:
                data = api_call(self.token, "sendMessage", payload, session=self.session)
            except RuntimeError as exc:
                print("[telegram] %s" % redact(str(exc)), file=sys.stderr)
                self._last = time.monotonic()
                return False
            self._last = time.monotonic()
            if data.get("ok"):
                return True
            params = data.get("parameters") or {}
            if params.get("migrate_to_chat_id") and not migrated:
                new_id = str(params["migrate_to_chat_id"])
                add_secret(new_id)  # never in a log: the Actions log of a public repository is public
                if self.owner:
                    print("!" * 70, file=sys.stderr)
                    print("[telegram] WARNING: the group was upgraded to a supergroup, so its chat id changed.\n"
                          "[telegram] Messages still arrive (this run uses the new id), but TELEGRAM_CHAT_ID is\n"
                          "[telegram] out of date. The new id is not printed here because this log may be public:\n"
                          "[telegram] on your computer run ./go-live.sh --telegram, which finds it and updates the secret.",
                          file=sys.stderr)
                    print("!" * 70, file=sys.stderr)
                self.chat_id = new_id
                migrated = True
                self._call(self.on_migrate, new_id)
                continue
            if data.get("error_code") == 429 and not retried_429:
                retried_429 = True
                self._sleep(min(float(params.get("retry_after", 5)) + 1, 60))
                continue
            print("[telegram] sendMessage failed: %s %s" % (
                data.get("error_code"), redact(sanitize(data.get("description", ""), self.token))), file=sys.stderr)
            if _is_gone(data):
                self._call(self.on_gone)
            return False

    @staticmethod
    def _call(callback: Optional[Callable[..., Any]], *args: Any) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # a relay problem must never break the run
            print("[telegram] subscriber bookkeeping failed (%s)" % type(exc).__name__, file=sys.stderr)


def _is_gone(data: Dict[str, Any]) -> bool:
    """The bot was removed from the chat or blocked: 403, or 400 "chat not found"."""
    code = data.get("error_code")
    if code == 403:
        return True
    return code == 400 and "chat not found" in str(data.get("description", "")).lower()


class BroadcastNotifier(Notifier):
    """The owner's chat first, then every subscriber (best effort).

    send() returns the OWNER's result only: a subscriber who blocked the bot must not make the run think
    delivery failed (it would retry the alert for the owner). Subscribers get a message only when the owner's
    send succeeded and the audience is "all"."""

    def __init__(self, owner: Notifier, subscribers: List[TelegramNotifier], relay: Any = None,
                 spacing: float = 0.1, sleep: Callable[[float], None] = time.sleep) -> None:
        self.owner = owner
        self.relay = relay
        self.spacing = spacing
        self._sleep = sleep
        self.gone: set = set()
        owner_id = str(getattr(owner, "chat_id", ""))
        self.subscribers: List[TelegramNotifier] = []
        seen = {owner_id}
        for sub in subscribers:
            sid = str(sub.chat_id)
            if sid in seen:  # the owner's own chat, or a duplicate
                continue
            seen.add(sid)
            sub.owner = False
            if relay is not None:
                sub.on_gone = self._gone_callback(sub)
                sub.on_migrate = self._migrate_callback(sid)
            self.subscribers.append(sub)

    def _gone_callback(self, sub: Any) -> Callable[[], Any]:
        def gone() -> None:
            current = str(sub.chat_id)  # the id after a migration, if the group moved earlier in this run
            self.gone.add(current)
            self.relay.remove(current)
        return gone

    def _migrate_callback(self, sid: str) -> Callable[[str], Any]:
        def migrated(new_id: str) -> None:
            self.relay.migrate(sid, new_id)
        return migrated

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None,
             audience: str = "all") -> bool:
        ok = self.owner.send(text, url=url, dashboard_url=dashboard_url, audience=audience)
        if not ok or audience != "all":
            return ok
        for sub in self.subscribers:
            if str(sub.chat_id) in self.gone:
                continue
            if self.spacing:
                self._sleep(self.spacing)
            try:
                sub.send(text, url=url, dashboard_url=dashboard_url)
            except Exception as exc:  # best effort
                print("[telegram] a subscriber send failed (%s)" % type(exc).__name__, file=sys.stderr)
        return ok


def make_notifier(cfg: Dict[str, Any]) -> Notifier:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set: printing messages to the console",
              file=sys.stderr)
        return ConsoleNotifier()
    t = cfg.get("telegram", {})
    quiet, tz = t.get("quiet_hours"), t.get("timezone", "Europe/Amsterdam")
    owner = TelegramNotifier(token, chat, quiet, tz)
    relay_url = os.environ.get("RELAY_URL", "").strip()
    relay_secret = os.environ.get("RELAY_PULL_SECRET", "").strip()
    if not relay_url or not relay_secret:
        return owner
    from .subscribers import RelayClient
    relay = RelayClient(relay_url, relay_secret)
    chats = relay.list()
    if chats is None:  # RelayClient has printed the one stderr line
        return owner
    subs = [TelegramNotifier(token, cid, quiet, tz, owner=False) for cid in chats]
    return BroadcastNotifier(owner, subs, relay)
