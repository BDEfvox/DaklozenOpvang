"""Pararius / Funda / Huurwoningen / Kamernet email alerts, read from a dedicated IMAP inbox.

Those sites block automated checks, so the user subscribes to their free email alerts (already filtered on
city, price and bedrooms) with a dedicated address, and this source reads that inbox.

Env: IMAP_HOST, IMAP_USER, IMAP_PASSWORD, optional IMAP_FOLDER (default INBOX).

The parser is deliberately not tied to one email layout: it looks for links whose URL matches each site's
listing-URL pattern, takes the anchor text as title and picks price / rooms / m2 from the text around the link.
Tracking redirect links (not on the site's own domain) are resolved with ONE request each to the *tracking*
host, without following the redirect (read the Location header), at most MAX_RESOLVE per run. The
pararius / funda / huurwoningen pages themselves are never requested.
The mailbox is opened read-only; nothing is flagged, moved or deleted.
"""
from __future__ import annotations

import email
import imaplib
import ipaddress
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from ..models import Listing
from ..utils import city_slug, clean_text, parse_price
from .base import Source, SourceError

SINCE_DAYS = 4
MAX_RESOLVE = 30
MAX_MESSAGES = 60
SENDER_KEYWORDS = ("pararius", "funda", "huurwoningen", "kamernet")
SITE_DOMAINS = {
    "pararius": ("pararius.nl", "pararius.com"),
    "funda": ("funda.nl",),
    "huurwoningen": ("huurwoningen.nl", "huurwoningen.com"),
    "kamernet": ("kamernet.nl",),
}
SITE_LABELS = {"pararius": "Pararius", "funda": "Funda", "huurwoningen": "Huurwoningen", "kamernet": "Kamernet"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# (site, regex). Groups: city, kind (may be missing), id, slug. Matched against the URL without query/fragment.
_P = r"https?://(?:www\.)?"
_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("pararius", re.compile(
        _P + r"pararius\.(?:nl|com)/(?P<kind>appartement|huis|studio|kamer|apartment|house|room)-(?:te-huur|for-rent)"
        r"/(?P<city>[^/?#]+)/(?P<id>[A-Za-z0-9]{6,12})(?=[/?#]|$)(?:/(?P<slug>[^/?#]+))?", re.I)),
    ("funda", re.compile(
        _P + r"funda\.nl/(?:en/)?(?:detail/)?(?:huur|rent)/(?P<city>[^/?#]+)/"
        r"(?P<kind>appartement|huis|woonhuis|studio|kamer|apartment|house|room|parkeergelegenheid|garage)"
        r"-(?P<id>\d+)(?:-(?P<slug>[^/?#]+))?", re.I)),
    ("huurwoningen", re.compile(
        _P + r"huurwoningen\.(?:nl|com)/(?:huren|rent)/(?P<city>[^/?#]+)/(?P<id>[A-Za-z0-9]*\d[A-Za-z0-9]*)(?=[/?#]|$)"
        r"(?:/(?P<slug>[^/?#]+))?", re.I)),
    ("kamernet", re.compile(
        _P + r"kamernet\.nl/(?:en/)?(?:huren|for-rent)/(?P<kind>appartement|kamer|studio|anti-kraak|apartment|room)"
        r"-(?P<city>[^/?#]+)/(?P<slug>[^/?#]+)/(?:appartement|kamer|studio|anti-kraak|apartment|room)-(?P<id>\d+)", re.I)),
]
_KIND_TYPE = {
    "appartement": "apartment", "apartment": "apartment", "huis": "house", "woonhuis": "house", "house": "house",
    "studio": "studio", "kamer": "room", "room": "room", "anti-kraak": "other",
    "parkeergelegenheid": "parking", "garage": "parking",
}
_GENERIC_ANCHOR = re.compile(
    r"^(?:bekijk|view|meer info|more info|details?|lees meer|read more|naar de woning|bekijk woning|"
    r"bekijk deze woning|open|klik hier|click here|toon|show)\b", re.I)
_SKIP_ANCHOR = re.compile(r"afmelden|unsubscribe|uitschrijven|privacy|voorwaarden|terms|opzeggen|preferences|voorkeuren",
                          re.I)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_PRICE = re.compile(r"(?:€|\bEUR\b)\s?(\d{1,3}(?:[.,]\d{3})+|\d{3,6})(?:[.,]\d{1,2}|,-)?")
_ROOMS = re.compile(r"(\d{1,2})\s*(?:kamers?|rooms?)\b", re.I)
_BEDROOMS = re.compile(r"(\d{1,2})\s*(?:slaapkamers?|slaapk\.?|slk|bedrooms?|bed\b)", re.I)
_AREA = re.compile(r"(\d{2,4})\s*(?:m²|m2|m\^2|vierkante meter)", re.I)


_APP_PASSWORD = re.compile(r"[a-z]{4}(?:\s+[a-z]{4}){3}")


def imap_credentials() -> Tuple[str, str, str]:
    """(host, user, password) from the environment, cleaned of copy-paste damage.

    Google shows an app password as four groups ("abcd efgh ijkl mnop") separated by non-breaking spaces;
    pasted as-is, imaplib cannot even encode it. The spaces are not part of the password, so drop them.
    """
    host = os.environ["IMAP_HOST"].replace("\xa0", " ").strip()
    user = os.environ["IMAP_USER"].replace("\xa0", " ").strip()
    password = os.environ["IMAP_PASSWORD"].replace("\xa0", " ").strip()
    if _APP_PASSWORD.fullmatch(password):
        password = re.sub(r"\s+", "", password)
    return host, user, password


def imap_date(dt: datetime) -> str:
    """IMAP wants dd-Mon-yyyy with English month names, whatever the locale."""
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


def _public_http_url(url: str) -> bool:
    """True for a plain web address on the public internet. A link in an email must never make the
    bot call something on the machine or network it runs on (localhost, 10.x, cloud metadata, ...)."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except (ValueError, OSError):
        return False


def _host_ok(host: str, domains: Tuple[str, ...]) -> bool:
    host = host.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in domains)


def match_listing_url(url: str) -> Optional[Dict[str, str]]:
    """If `url` is a listing URL of one of the four sites, return site/id/city/slug/kind and the clean URL."""
    for site, rx in _PATTERNS:
        m = rx.match(url)
        if not m:
            continue
        gd = m.groupdict()
        kind = (gd.get("kind") or "").lower()
        slug = gd.get("slug") or ""
        if site == "huurwoningen" and not slug:
            continue
        base = url.split("?")[0].split("#")[0]
        clean = "https://" + base[:m.end()].split("://", 1)[1]
        return {"site": site, "id": gd["id"], "city": gd["city"].lower(), "slug": slug, "kind": kind, "clean": clean}
    return None


def _embedded_target(url: str) -> Optional[str]:
    """Tracking URLs often carry the real target in a query parameter (url=, redirect=, ...)."""
    try:
        for _, value in parse_qsl(urlparse(url).query):
            v = unquote(value)
            if v.startswith("http") and match_listing_url(v):
                return v
    except ValueError:
        pass
    return None


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def message_bodies(msg: Message) -> Tuple[List[str], List[str]]:
    htmls: List[str] = []
    texts: List[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        ctype = part.get_content_type()
        if ctype == "text/html":
            htmls.append(_decode_part(part))
        elif ctype == "text/plain":
            texts.append(_decode_part(part))
    return htmls, texts


def _sender_site(msg: Message) -> Optional[str]:
    addr = parseaddr(msg.get("From", ""))[1].lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for site, domains in SITE_DOMAINS.items():
        if _host_ok(domain, domains):
            return site
    return None


def _extract_facts(text: str) -> Dict[str, Optional[int]]:
    price = None
    m = _PRICE.search(text)
    if m:
        p = parse_price(m.group(1))
        if p is not None and 300 <= p <= 20000:
            price = p
    rooms = _ROOMS.search(text)
    beds = _BEDROOMS.search(text)
    area = _AREA.search(text)
    return {
        "price": price,
        "rooms": int(rooms.group(1)) if rooms else None,
        "bedrooms": int(beds.group(1)) if beds else None,
        "area": int(area.group(1)) if area else None,
    }


class _Link:
    def __init__(self, href: str, anchor_text: str, context: str, from_html: bool = False) -> None:
        self.href = href
        self.anchor_text = anchor_text
        self.context = context
        # True: anchor_text is the text of the <a> itself. False (plain-text mail): it is merely a line
        # near the URL, which can just as well be the greeting ("Beste <name>,").
        self.from_html = from_html


def _links_from_html(html: str) -> List[_Link]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    anchors = [a for a in soup.find_all("a", href=True) if a["href"].startswith("http")]
    links: List[_Link] = []
    for a in anchors:
        href = a["href"]
        text = a.get_text(" ", strip=True)
        # climb while the parent still holds only anchors that point at this same href
        node = a
        for _ in range(8):
            parent = node.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            inner = {x["href"] for x in parent.find_all("a", href=True) if x["href"].startswith("http")}
            inner = {h for h in inner if not _SKIP_ANCHOR.search(h)}
            if len(inner) > 1 and inner != {href}:
                # tolerate extra links to the same listing under a different tracking URL: stop if clearly another
                if len(inner) > 3 or parent.name in ("table", "tbody", "ul", "ol"):
                    break
            node = parent
            if len(node.get_text(" ", strip=True)) > 600:
                break
        context = node.get_text(" ", strip=True)
        links.append(_Link(href, text, context, from_html=True))
    return links


def _links_from_text(text: str) -> List[_Link]:
    links: List[_Link] = []
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for i, para in enumerate(paragraphs):
        urls = _URL_IN_TEXT.findall(para)
        if not urls:
            continue
        rest = _URL_IN_TEXT.sub(" ", para)
        if len(rest.strip()) < 15 and i > 0:  # URL alone in its paragraph: description is the paragraph before
            rest = _URL_IN_TEXT.sub(" ", paragraphs[i - 1]) + " " + rest
        lines = [ln.strip(" -*\t<>[]()") for ln in rest.splitlines() if ln.strip(" -*\t<>[]()")]
        title = lines[0] if lines else ""
        for u in urls:
            links.append(_Link(u.rstrip(".,;"), title, " ".join(lines)))
    return links


def _good_anchor(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= 4 and not _GENERIC_ANCHOR.match(t)


def _title_from(info: Dict[str, str], link: _Link) -> str:
    """Title for a listing found in an email. Titles are published (public repo, dashboard, Telegram),
    so they only ever come from the link's own text or the listing URL, never from the surrounding
    email text, which may hold the reader's name or address."""
    t = (clean_text(link.anchor_text, 160) or "") if link.from_html else ""
    if not t or _GENERIC_ANCHOR.match(t) or len(t) < 4 or "@" in t:
        slug_title = info["slug"].replace("-", " ").strip().title() if info["slug"] else ""
        t = slug_title or "Woning"
    return t


def _titled(link: _Link) -> bool:
    return link.from_html and _good_anchor(link.anchor_text) and "@" not in link.anchor_text


class EmailAlerts(Source):
    name = "email_alerts"
    label = "Pararius/Funda email alerts"
    homepage = "https://www.pararius.nl"
    note = "Reads the alert emails these sites send you"
    per_city = False

    def __init__(self) -> None:
        self._resolver: Optional[Callable[[str], Optional[str]]] = None

    # ---- config -------------------------------------------------------------------------------
    def unavailable_reason(self) -> Optional[str]:
        if not (os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER") and os.environ.get("IMAP_PASSWORD")):
            return "Email alerts not configured"
        return None

    # ---- IMAP (overridable in tests) ----------------------------------------------------------
    def _connect(self) -> Any:
        host, user, password = imap_credentials()
        port = int(os.environ.get("IMAP_PORT", "993") or 993)
        try:
            conn = imaplib.IMAP4_SSL(host, port, timeout=30)
            conn.login(user, password)
        except UnicodeEncodeError:
            raise SourceError("IMAP user or password contains characters a mail server cannot accept; set the secret again")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise SourceError(f"IMAP login/connect failed for {host}: {type(exc).__name__}")
        return conn

    def _fetch_messages(self, conn: Any, now: datetime) -> List[Message]:
        folder = os.environ.get("IMAP_FOLDER") or "INBOX"
        quoted = '"%s"' % folder.replace('"', "") if re.search(r"[\s()]", folder) else folder
        typ, _ = conn.select(quoted, readonly=True)
        if typ != "OK":
            raise SourceError(f"IMAP folder {folder!r} cannot be opened")
        since = imap_date(now - timedelta(days=SINCE_DAYS))
        ids: List[bytes] = []
        for kw in SENDER_KEYWORDS:
            typ, data = conn.search(None, "SINCE", since, "FROM", kw)
            if typ != "OK":
                raise SourceError("IMAP search failed")
            for chunk in data or []:
                if chunk:
                    ids.extend(chunk.split())
        unique = sorted(set(ids), key=lambda b: int(b))[-MAX_MESSAGES:]
        out: List[Message] = []
        for mid in reversed(unique):  # newest first
            typ, data = conn.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not data:
                continue
            raw = None
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    raw = bytes(item[1])
                    break
            if raw:
                out.append(email.message_from_bytes(raw))
        return out

    # ---- tracking redirects -------------------------------------------------------------------
    def _resolve(self, url: str) -> Optional[str]:
        """One request to the tracking host; read Location; never follow it."""
        if self._resolver is not None:
            return self._resolver(url)
        host = urlparse(url).netloc
        if any(_host_ok(host, d) for d in SITE_DOMAINS.values()):
            return None  # never request the sites themselves
        if not _public_http_url(url):
            return None
        try:
            resp = requests.get(url, allow_redirects=False, timeout=10, stream=True,
                                headers={"User-Agent": "Mozilla/5.0 (house-hunter email link resolver)"})
            loc = resp.headers.get("Location")
            resp.close()
        except requests.RequestException:
            return None
        if loc and loc.startswith("/"):
            return None
        return loc

    # ---- parsing ------------------------------------------------------------------------------
    def parse_message(self, msg: Message, budget: List[int]) -> List[Listing]:
        site_hint = _sender_site(msg)
        if site_hint is None:
            return []
        try:
            posted = parsedate_to_datetime(msg.get("Date", "")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            posted = None
        htmls, texts = message_bodies(msg)
        links: List[_Link] = []
        for h in htmls:
            links.extend(_links_from_html(h))
        for t in texts:
            links.extend(_links_from_text(t))
        found: Dict[Tuple[str, str], Listing] = {}
        anchors: Dict[Tuple[str, str], bool] = {}
        for link in links:
            if _SKIP_ANCHOR.search(link.anchor_text) or _SKIP_ANCHOR.search(link.href):
                continue
            info = match_listing_url(link.href)
            if info is None:
                info = self._try_redirect(link.href, budget)
            if info is None:
                continue
            kind_type = _KIND_TYPE.get(info["kind"]) if info["kind"] else None
            if kind_type in ("room", "parking"):
                continue
            city = city_slug(info["city"]) or city_slug(info["city"].replace("-", " "))
            if city is None:
                continue
            key = (info["site"], info["id"])
            facts = _extract_facts(link.context)
            existing = found.get(key)
            if existing is not None:
                self._merge(existing, facts, link)
                if _titled(link) and not anchors[key]:
                    anchors[key] = True
                    existing.title = f"{SITE_LABELS[info['site']]}: {_title_from(info, link)}"
                continue
            anchors[key] = _titled(link)
            title = _title_from(info, link)
            site_label = SITE_LABELS[info["site"]]
            address = info["slug"].replace("-", " ").strip().title() if info["slug"] else None
            found[key] = Listing(
                source="email_alerts",
                source_id=f"{info['site']}-{info['id']}",
                url=info["clean"],
                title=f"{site_label}: {title}",
                city=city,
                price_total=facts["price"],
                rooms=facts["rooms"],
                bedrooms=facts["bedrooms"],
                area_m2=facts["area"],
                property_type=kind_type,
                address=address or None,
                # Used in memory for the filters only. private_text tells the runner never to save it:
                # it is text from the user's own mailbox and the state files are public.
                description=clean_text(link.context, 600),
                posted_at=posted,
                extra={"site_prefiltered": True, "site": info["site"], "via": site_label, "private_text": True},
            )
        return list(found.values())

    @staticmethod
    def _merge(existing: Listing, facts: Dict[str, Optional[int]], link: _Link) -> None:
        if existing.price_total is None and facts["price"] is not None:
            existing.price_total = facts["price"]
        if existing.rooms is None:
            existing.rooms = facts["rooms"]
        if existing.bedrooms is None:
            existing.bedrooms = facts["bedrooms"]
        if existing.area_m2 is None:
            existing.area_m2 = facts["area"]
        if not existing.description and link.context:
            existing.description = clean_text(link.context, 600)

    def _try_redirect(self, href: str, budget: List[int]) -> Optional[Dict[str, str]]:
        host = urlparse(href).netloc.lower()
        if not host or any(_host_ok(host, d) for d in SITE_DOMAINS.values()):
            return None  # a site link that is not a listing link (search page, logo, ...)
        embedded = _embedded_target(href)
        if embedded:
            return match_listing_url(embedded)
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        target = self._resolve(href)
        if target:
            return match_listing_url(target)
        return None

    # ---- Source API ---------------------------------------------------------------------------
    def fetch(self, city: str, http: "Any") -> List[Listing]:
        reason = self.unavailable_reason()
        if reason:
            raise SourceError(reason)
        conn = self._connect()
        try:
            try:
                messages = self._fetch_messages(conn, datetime.now(timezone.utc))
            except (imaplib.IMAP4.error, OSError) as exc:
                raise SourceError(f"IMAP error: {type(exc).__name__}")
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
        budget = [MAX_RESOLVE]
        seen: Dict[str, Listing] = {}
        for msg in messages:  # newest first, so the freshest copy of a listing wins
            for lst in self.parse_message(msg, budget):
                if lst.source_id not in seen:
                    seen[lst.source_id] = lst
                else:
                    EmailAlerts._merge(seen[lst.source_id], {"price": lst.price_total, "rooms": lst.rooms,
                                                              "bedrooms": lst.bedrooms, "area": lst.area_m2},
                                       _Link(lst.url, "", lst.description or ""))
        return list(seen.values())


SOURCE = EmailAlerts()
