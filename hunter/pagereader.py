"""What a fetched page says about itself, and whether it is a wall instead of the page.

Ported from a browser-based public-page reader (its reader.mjs and extract.mjs), cut down to what a
scraper that reads static HTML can use. Standard library only, Python 3.9+. Another project uses a
byte-identical copy of this file: change the copies together.

Taken from the browser-based reader:
- Access barriers: wall status codes and bot-check / human-verification text mean "stop", never
  "parse what came back". Nothing here tries to get past a wall.
- A password field or a captcha frame explains a page that lacks the content you expected.
- The page's own metadata: title, description, canonical URL, JSON-LD.

Left out on purpose:
- The browser and JavaScript rendering. The listing pages these tools read serve their text in the
  HTML, and installing a browser on every run would cost more time than a run has.
- "Largest <article>/<main>" body text. On listing sites that is mostly other people's listings
  (search tiles, "similar ads"), whose RAM, bedrooms and wording would get mixed into this one.
- Visibility checks, which need a browser. So a password field alone is not a wall (many pages
  carry a hidden login form): callers look at it only when the content they expected is missing.
"""
from __future__ import annotations

import html as _html
import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit

# Status codes that mean "not for you": login, payment, bot check, rate limit.
# 503 is left out: usually it is a server fault, so the caller decides.
WALL_STATUSES = {401: "login required", 402: "payment required", 403: "access denied",
                 407: "proxy login required", 429: "rate limited"}

# Text that only appears on bot-check and challenge pages. Matched in the first HEAD_CHARS
# characters only, because real pages can mention "access denied" further down.
WALL_MARKERS = (
    ("just a moment...", "Cloudflare check"),
    ("cf-chl-", "Cloudflare check"),
    ("challenge-platform", "Cloudflare check"),
    ("attention required! | cloudflare", "Cloudflare block"),
    ("je bent bijna op de pagina", "bot check"),
    ("captcha-delivery.com", "DataDome check"),
    ("px-captcha", "PerimeterX check"),
    ("request unsuccessful. incapsula", "Incapsula block"),
    ("access denied</title>", "access denied"),
    ("verify you are human", "human verification"),
    ("verify that you are human", "human verification"),
    ("checking your browser", "browser check"),
    ("unusual traffic", "unusual-traffic block"),
)
HEAD_CHARS = 6000

# JSON-LD types that describe one item for rent or sale. WebSite, WebPage, Organization and the like
# are left out: their description is often the site's own blurb, the same on every page.
# ImageObject is in because Kleinanzeigen puts the ad's full text in its photos' descriptions.
LISTING_TYPES = frozenset((
    "product", "individualproduct", "offer", "aggregateoffer", "imageobject",
    "residence", "accommodation", "apartment", "house", "singlefamilyresidence", "apartmentcomplex",
    "room", "realestatelisting",
))
_MAX_LD_OBJECTS = 200


def wall_reason(status: int, text: str = "") -> Optional[str]:
    """Why this response is a wall instead of the page that was asked for, or None."""
    if status in WALL_STATUSES:
        return "HTTP %d, %s" % (status, WALL_STATUSES[status])
    head = (text or "")[:HEAD_CHARS].lower()
    for marker, reason in WALL_MARKERS:
        if marker in head:
            return reason
    return None


class PageSummary:
    """The page's own facts. Every string comes from the website: data, never instructions."""

    def __init__(self) -> None:
        self.title = ""
        self.meta: Dict[str, str] = {}          # lower-cased name/property -> first content
        self.canonical: Optional[str] = None
        self.json_ld: List[Dict[str, Any]] = []  # every object, nested @graph/mainEntity/offers included
        self.json_ld_urls: List[Optional[str]] = []  # per object: the page URL it (or its parent) names
        self.password_field = False
        self.captcha_frame = False

    def own_urls(self) -> List[str]:
        return [u for u in (self.meta.get("og:url"), self.canonical) if u]


def read_page(text: str) -> PageSummary:
    """Read a page's title, meta tags, canonical URL, JSON-LD and login/captcha signs."""
    reader = _Reader()
    try:
        reader.feed(text or "")
        reader.close()
    except Exception:  # malformed markup: keep what was read up to there
        pass
    return reader.page


def same_page(page: PageSummary, url: str) -> bool:
    """Does the page say it is the one at `url`? Compares its og:url and canonical URL with `url`:
    the same path, or the same listing number (the readable part of an address can change when the
    seller edits the title). The number must match as a whole: 123456 is not 1234567.
    Relative URLs are resolved against `url`; the host is not compared, so www/m. aliases count.
    A page that names no URL of its own does not count."""
    return any(_same_item(own, url) for own in page.own_urls())


def own_description(page: PageSummary, url: str) -> str:
    """The longest description the page gives of the item at `url`, as plain text ('' if none).

    Only a page whose og:url or canonical URL is `url` counts (see same_page): a listing that was
    taken down often redirects to a search page, whose JSON-LD describes other people's items.
    Sources: JSON-LD objects of a listing type (LISTING_TYPES), then the og/twitter/meta description.
    A JSON-LD object that names another page as its own (its `url` or `mainEntityOfPage`, or its
    parent's) is someone else's item, such as a "similar ads" tile, and is left out. Photo objects
    are kept whatever their `url` says: that is the picture's address, and Kleinanzeigen puts the
    ad's full text in its photos' descriptions.
    """
    if not same_page(page, url):
        return ""
    urls = page.json_ld_urls + [None] * (len(page.json_ld) - len(page.json_ld_urls))
    found = [obj["description"] for obj, named in zip(page.json_ld, urls)
             if _types(obj) & LISTING_TYPES and isinstance(obj.get("description"), str)
             and ("imageobject" in _types(obj) or not named or _same_item(named, url))]
    found += [page.meta[k] for k in ("og:description", "twitter:description", "description") if page.meta.get(k)]
    texts = [t for t in (_plain(f) for f in found) if t]
    return max(texts, key=len) if texts else ""


# --------------------------------------------------------------------------- internals
class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = PageSummary()
        self._title: Optional[List[str]] = None
        self._title_done = False
        self._ld: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        page = self.page
        if tag == "title":
            if not self._title_done and self._title is None:  # the document's title, not an SVG's
                self._title = []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            if key and a.get("content", "").strip() and key not in page.meta:
                page.meta[key] = a["content"].strip()
        elif tag == "link":
            if "canonical" in a.get("rel", "").lower().split() and a.get("href") and not page.canonical:
                page.canonical = a["href"].strip()
        elif tag == "script":
            if a.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
                self._ld = []
        elif tag == "input":
            if a.get("type", "").strip().lower() == "password":
                page.password_field = True
        elif tag == "iframe":
            src = a.get("src", "").lower()
            if "captcha" in src or "challenges.cloudflare.com" in src:
                page.captcha_frame = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title is not None:
            self.page.title = re.sub(r"\s+", " ", "".join(self._title)).strip()
            self._title = None
            self._title_done = True
        elif tag == "script" and self._ld is not None:
            self._add_ld("".join(self._ld))
            self._ld = None

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
        elif self._title is not None:
            self._title.append(data)

    def _add_ld(self, raw: str) -> None:
        raw = raw.strip()
        if not raw or len(self.page.json_ld) >= _MAX_LD_OBJECTS:
            return
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            return
        for obj, named in _walk(data):
            if len(self.page.json_ld) >= _MAX_LD_OBJECTS:
                break
            self.page.json_ld.append(obj)
            self.page.json_ld_urls.append(named)


def _walk(obj: Any, depth: int = 0, named: Optional[str] = None) -> Iterator[Tuple[Dict[str, Any], Optional[str]]]:
    """Every JSON-LD object with the page URL it names for itself, or else the one its parent names."""
    if depth > 4:
        return
    if isinstance(obj, list):
        for item in obj:
            yield from _walk(item, depth + 1, named)
    elif isinstance(obj, dict):
        named = _named_url(obj) or named
        yield obj, named
        for key in ("@graph", "mainEntity", "itemOffered", "offers"):
            if key in obj:
                yield from _walk(obj[key], depth + 1, named)


def _named_url(obj: Dict[str, Any]) -> Optional[str]:
    for key in ("url", "mainEntityOfPage"):
        value = obj.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _same_item(own: str, url: str) -> bool:
    """Is `own` (a URL a page or JSON-LD object gives for itself, possibly relative) the item at `url`?"""
    want = _path(url)
    if not want:
        return False
    try:
        have = _path(urljoin(url.strip(), own.strip()))
    except ValueError:
        return False
    if have == want:
        return True
    ids = re.findall(r"\d{6,}", want)
    if not ids:
        return False
    number = max(ids, key=len)
    return re.search(r"(?<!\d)%s(?!\d)" % number, have) is not None


def _types(obj: Dict[str, Any]) -> set:
    t = obj.get("@type")
    return {str(x).strip().lower() for x in (t if isinstance(t, list) else [t]) if x}


def _path(url: str) -> str:
    try:
        path = unquote(urlsplit((url or "").strip()).path)
    except ValueError:
        return ""
    return path.rstrip("/").lower()


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()
