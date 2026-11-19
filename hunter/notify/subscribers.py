"""Client for the subscriber relay (telegram-relay/, a Cloudflare Worker): who else gets the alerts.

The relay keeps the list of subscribed chats; this talks to it with RELAY_PULL_SECRET. It never raises,
and it never prints a chat id or the secret: the Actions log of a public repository is public.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Any, Callable, List, Optional

import requests

from ..redact import add_secret, redact

ID_RE = re.compile(r"^-?\d{1,20}$")
MAX_SUBSCRIBERS = 200
TRIES = 3
BACKOFF = (1.0, 3.0)  # seconds to wait before the 2nd and the 3rd try
TIMEOUT = 15
USER_AGENT = "house-hunter"  # Cloudflare rejects some default Python agents with error 1010


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


class RelayClient:
    def __init__(self, url: str, secret: str, session: Any = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.url = (url or "").strip().rstrip("/")
        self.secret = (secret or "").strip()
        self.session = session or requests
        self._sleep = sleep

    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + self.secret, "User-Agent": USER_AGENT}

    def _warn(self, what: str, why: str) -> None:
        print(redact("[relay] %s failed (%s)" % (what, why)), file=sys.stderr)

    def _usable(self, what: str) -> bool:
        # https only: the secret travels in a header (plain http is accepted for a relay on this computer, for tests)
        local = self.url.startswith(("http://127.0.0.1", "http://localhost"))
        if not self.secret or not (self.url.startswith("https://") or local):
            self._warn(what, "RELAY_URL or RELAY_PULL_SECRET is not usable")
            return False
        return True

    def list(self) -> Optional[List[str]]:
        """The subscribed chat ids, or None when the relay cannot be asked or keeps failing."""
        if not self._usable("fetching the subscriber list"):
            return None
        why = "unknown"
        for attempt in range(TRIES):
            if attempt:
                self._sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            try:
                resp = self.session.get(self.url + "/subscribers", headers=self._headers(), timeout=TIMEOUT)
                if resp.status_code != 200:
                    why = "HTTP %s" % resp.status_code
                    if resp.status_code in (401, 403):
                        break  # the secret is wrong: asking again will not help
                    continue
                chats = self._parse(resp.json())
                if chats is None:
                    why = "unexpected answer"
                    continue
                return chats
            except Exception as exc:  # network errors, bad JSON: the message may hold the URL, so only the type
                why = type(exc).__name__
        self._warn("fetching the subscriber list", why + "; sending to the owner only")
        return None

    @staticmethod
    def _parse(data: Any) -> Optional[List[str]]:
        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list):
            return None
        out: List[str] = []
        for item in chats:
            if valid_id(item) and item not in out:
                add_secret(item)  # before anything else can log it
                out.append(item)
        return out[:MAX_SUBSCRIBERS]

    def _post(self, path: str, payload: dict, what: str) -> bool:
        if not self._usable(what):
            return False
        try:
            resp = self.session.post(self.url + path, json=payload, headers=self._headers(), timeout=TIMEOUT)
            if 200 <= resp.status_code < 300:
                return True
            self._warn(what, "HTTP %s" % resp.status_code)
        except Exception as exc:
            self._warn(what, type(exc).__name__)
        return False

    def remove(self, chat_id: str) -> bool:
        """Tell the relay a chat is gone (the bot was removed or blocked)."""
        if not valid_id(str(chat_id)):
            return False
        add_secret(chat_id)
        return self._post("/subscribers/remove", {"chat_id": str(chat_id)}, "removing a subscriber")

    def migrate(self, old: str, new: str) -> bool:
        """Tell the relay a group became a supergroup, so its subscription moves to the new id."""
        if not (valid_id(str(old)) and valid_id(str(new))):
            return False
        add_secret(old)
        add_secret(new)
        return self._post("/subscribers/migrate", {"from": str(old), "to": str(new)}, "moving a subscriber")
