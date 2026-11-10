from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunter.config import load_config  # noqa: E402
from hunter.models import Listing  # noqa: E402
from hunter.notify.telegram import Notifier  # noqa: E402
from hunter.sources.base import Source  # noqa: E402


@pytest.fixture
def cfg() -> Dict[str, Any]:
    c = load_config()
    c["dashboard_url"] = "https://example.github.io/house-hunter/"
    return c


def make_listing(sid: str = "1", source: str = "fake", **kw: Any) -> Listing:
    base: Dict[str, Any] = dict(
        source=source, source_id=sid, url="https://example.com/%s" % sid, title="Nice flat %s" % sid,
        city="den-haag", price_total=1400, bedrooms=2, rooms=3, area_m2=70, property_type="apartment",
        address="Daltonstraat %s" % sid)
    base.update(kw)
    return Listing(**base)


class FakeSource(Source):
    def __init__(self, name: str = "fake", listings: Optional[List[Listing]] = None, per_city: bool = True,
                 raises: Optional[Exception] = None, reason: Optional[str] = None,
                 city_errors: Optional[Dict[str, Exception]] = None) -> None:
        self.name = name
        self.label = name.title()
        self.homepage = "https://%s.example" % name
        self.per_city = per_city
        self.listings = listings or []
        self.raises = raises
        self.reason = reason
        self.city_errors = city_errors or {}
        self.detail_calls = 0
        self.description: Optional[str] = None

    def unavailable_reason(self) -> Optional[str]:
        return self.reason

    def fetch(self, city: str, http: Any) -> List[Listing]:
        if self.raises:
            raise self.raises
        if city in self.city_errors:
            raise self.city_errors[city]
        if city == "*":
            return list(self.listings)
        return [l for l in self.listings if l.city == city]

    def fetch_details(self, listing: Listing, http: Any) -> Listing:
        self.detail_calls += 1
        if self.description:
            listing.description = self.description
        return listing


class FakeNotifier(Notifier):
    def __init__(self) -> None:
        self.messages: List[str] = []
        self.urls: List[Optional[str]] = []
        self.ok = True

    def send(self, text: str, url: Optional[str] = None, dashboard_url: Optional[str] = None) -> bool:
        if not self.ok:
            return False
        self.messages.append(text)
        self.urls.append(url)
        return True
