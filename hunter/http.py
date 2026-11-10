"""Polite HTTP client shared by all sources.

- At least `min_delay` seconds between requests to the same host.
- Detects bot walls and raises SourceBlocked. It never tries to get around them.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from .sources.base import SourceBlocked, SourceError

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Text that only appears on bot-check / challenge pages.
_WALL_MARKERS = (
    "just a moment...",
    "cf-chl-",
    "challenge-platform",
    "je bent bijna op de pagina",
    "attention required! | cloudflare",
    "captcha-delivery.com",          # DataDome
    "px-captcha",                    # PerimeterX
    "access denied</title>",
    "request unsuccessful. incapsula",
)


class Http:
    def __init__(self, min_delay: float = 2.0, timeout: float = 20.0) -> None:
        self.min_delay = min_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        })
        self._last: Dict[str, float] = {}
        self.counts: Dict[str, int] = {}

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.min_delay:
                time.sleep(self.min_delay - gap)
        self._last[host] = time.monotonic()
        self.counts[host] = self.counts.get(host, 0) + 1

    def request(self, method: str, url: str, *, accept: Optional[str] = None,
                headers: Optional[Dict[str, str]] = None, **kw: Any) -> requests.Response:
        host = urlparse(url).netloc
        h = {"Accept": accept or "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
        if headers:
            h.update(headers)
        last_exc: Optional[Exception] = None
        for attempt in range(2):  # one retry on network errors only
            self._wait(host)
            try:
                resp = self.session.request(method, url, headers=h, timeout=self.timeout, **kw)
                break
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(3)
        else:
            raise SourceError(f"network error for {host}: {last_exc}")

        head = resp.text[:6000].lower() if resp.content else ""
        if resp.status_code in (401, 403, 429) or any(m in head for m in _WALL_MARKERS):
            raise SourceBlocked(f"{host} answered {resp.status_code} (bot check or access denied)")
        if resp.status_code >= 400:
            raise SourceError(f"{host} answered HTTP {resp.status_code} for {url}")
        return resp

    def get(self, url: str, **kw: Any) -> requests.Response:
        return self.request("GET", url, **kw)

    def get_text(self, url: str, **kw: Any) -> str:
        return self.get(url, **kw).text

    def get_json(self, url: str, **kw: Any) -> Any:
        resp = self.get(url, accept="application/json", **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"expected JSON from {url}: {exc}")

    def post_json(self, url: str, payload: Any, **kw: Any) -> Any:
        resp = self.request("POST", url, accept="application/json", json=payload, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"expected JSON from {url}: {exc}")
