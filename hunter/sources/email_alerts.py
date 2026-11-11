"""Pararius / Funda / Huurwoningen / Kamernet email alerts, read from a dedicated IMAP inbox.

Those sites block automated checks, so the user subscribes to their free email alerts (already filtered on
city, price and bedrooms) with a dedicated address, and this source reads that inbox.

Env: IMAP_HOST, IMAP_USER, IMAP_PASSWORD, optional IMAP_FOLDER (default INBOX).

The parser is deliberately not tied to one email layout: it looks for links whose URL matches each site's
listing-URL pattern, takes the anchor text as title and picks price / rooms / m2 from the text around the link.
Tracking redirect links are resolved with ONE request each to the *tracking* host, without following the
redirect (read the Location header), at most MAX_RESOLVE per run. A tracking host is either on another
domain or a mail-tracking subdomain of the site (url1234.pararius.nl, links.funda.nl: the sites' email
providers, see _TRACKING_LABEL). Each link is requested once: the answer is remembered in `link_cache`
(the runner keeps it in data/meta.json, keyed by a hash of the link, so no tracking token is stored).
The pararius / funda / huurwoningen pages themselves are never requested.
A mail that also gives the listings' own links needs no tracking link at all, and then none is requested:
Funda's mails carry tracked links in the HTML part and the clean funda.nl links in the plain-text part. The
clean links say which homes there are; a tracked link whose text is the street of one of them ("Kikkerstraat
5 A" for .../appartement-kikkerstraat-5-a/...) is that home, so its card supplies price, size and rooms.
Pararius is different again: its tracking host (url1234.pararius.nl) answers every program with a bot check,
so those links are not requested at all. Each home in a Pararius mail has a photo whose address holds the
listing's id, and pararius.nl sends /<type>-te-huur/<city>/<id>/<anything> on to the right page, so the
listing's own link is put together from the mail (see _pararius_cards). A tracking host that answers with
a bot check (403/429) is left alone for the rest of the day.
The mailbox is opened read-only; nothing is flagged, moved or deleted.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import ipaddress
import os
import re
import socket
import unicodedata
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
MAX_RESOLVE = 60
LINK_TRIES = 3          # a tracking link that gives no redirect is tried in this many runs, then given up
LINK_CACHE_DAYS = 10    # forget remembered links after this long (mails older than SINCE_DAYS are not read)
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
    # since 2024: /detail/huur/<city>/<kind>-<street>-<number>/<id>/
    ("funda", re.compile(
        _P + r"funda\.nl/(?:en/)?detail/(?:huur|rent)/(?P<city>[^/?#]+)/"
        r"(?P<kind>[a-z]+)-(?P<slug>[^/?#]+)/(?P<id>\d{6,})(?=[/?#]|$)", re.I)),
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
# First label of a host that only redirects mail links (SendGrid's url1234., links., click., ...). Hosts
# under a site's domain with any other first label (www, help, ...) are the site itself: never requested.
_TRACKING_LABEL = re.compile(r"^(?:url\d*|links?|lnk|click|clicks|email|e?mail\d*|em\d*|track|tracking|trk|go|r|t|ct|news|nieuwsbrief)$")


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


# Photo of a home in a Pararius mail: .../listings/api/media/<listing uuid>/<photo uuid>. The first eight
# characters of the listing uuid are the id in the listing's URL.
_PARARIUS_PHOTO = re.compile(
    r"https?://casco-media[^/\s\"')]*/listings/api/media/([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/",
    re.I)
_POSTCODE_CITY = re.compile(r"\b\d{4}\s?[A-Z]{2}\s+(?P<city>[^(€\d]{2,40}?)\s*(?:\(|€|\d|$)")
_KIND_PREFIX = re.compile(r"^(appartement|apartment|flat|huis|house|woonhuis|studio|kamer|room)\s+(?=\S)", re.I)
_KIND_SLUG = {"appartement": "appartement", "apartment": "appartement", "flat": "appartement", "huis": "huis",
              "house": "huis", "woonhuis": "huis", "studio": "studio", "kamer": "kamer", "room": "kamer"}


def _site_host(host: str) -> bool:
    return any(_host_ok(host, d) for d in SITE_DOMAINS.values())


def _tracking_host(host: str) -> bool:
    """True for a mail-tracking subdomain of one of the sites (url2220.pararius.nl, links.funda.nl)."""
    host = host.lower().split(":")[0]
    for domains in SITE_DOMAINS.values():
        for d in domains:
            if host.endswith("." + d):
                return bool(_TRACKING_LABEL.match(host[:-len(d) - 1].split(".")[0]))
    return False


def _resolvable_host(host: str) -> bool:
    """A link on this host may be asked where it leads: any other domain, or a site's tracking subdomain."""
    return bool(host) and (not _site_host(host) or _tracking_host(host))


def _link_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:20]


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
        # False: the type in href is a guess (a Pararius card without "Appartement"/"Huis" in its title)
        self.kind_known = True


def _links_from_html(html: str) -> List[_Link]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    anchors = [a for a in soup.find_all("a", href=True) if a["href"].startswith("http")]
    links: List[_Link] = []
    for a in anchors:
        links.append(_Link(a["href"], a.get_text(" ", strip=True), _anchor_context(a), from_html=True))
    return links


def _anchor_context(a: Any) -> str:
    """The text of the block around an <a>: its listing card, if the mail is built from cards."""
    href = a["href"]
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
    return node.get_text(" ", strip=True)


CARD_TEXT_MAX = 400


def _html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def _card_text(full: str, title: str, titles: List[str]) -> Optional[str]:
    """The mail's text from a home's title up to the next link text (the next home, a button), in reading
    order. The block around a link (_anchor_context) is not always the home's card: where homes are grouped
    per city it can start with the homes above it or stop before the home's last line."""
    start = full.find(title)
    if start < 0:
        return None
    end = start + CARD_TEXT_MAX
    for other in titles:
        if other and other != title:
            at = full.find(other, start + len(title))
            if 0 <= at < end:
                end = at
    return full[start:end].strip()


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _pararius_cards(html: str) -> List[_Link]:
    """Listings of a Pararius mail, as links to the listings' own pages.

    The links in the mail cannot be used (see the module text), so each card is read as: a photo (its
    address gives the listing id), then the first link with a real text (the street), with the card's text
    around it ("2511 AB Den Haag (Wijk) € 1.850 per maand 75 m² · 3 kamers"). pararius.nl forwards
    /<type>-te-huur/<city>/<id>/<anything> to the listing, so a guessed type or street does no harm.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    cards: List[_Link] = []
    pending: Optional[str] = None
    for tag in soup.find_all(True):
        attrs = " ".join(str(tag.get(k, "")) for k in ("background", "src", "style", "data-src"))
        photo = _PARARIUS_PHOTO.search(attrs) if "casco-media" in attrs else None
        if photo:
            pending = photo.group(1).lower()
            continue
        if pending is None or tag.name != "a" or not str(tag.get("href", "")).startswith("http"):
            continue
        text = tag.get_text(" ", strip=True)
        if not _good_anchor(text) or "@" in text or _SKIP_ANCHOR.search(text):
            continue
        listing_id, pending = pending, None
        context = _anchor_context(tag)
        where = _POSTCODE_CITY.search(context)
        city = city_slug(where.group("city").strip()) if where else None
        if city is None:
            continue  # no postcode line, or a town that is not searched
        kind = _KIND_PREFIX.match(text)
        kind_slug = _KIND_SLUG[kind.group(1).lower()] if kind else "appartement"
        street = _slug(text[kind.end():] if kind else text) or "woning"
        href = "https://www.pararius.nl/%s-te-huur/%s/%s/%s" % (kind_slug, city, listing_id, street)
        card = _Link(href, text, context, from_html=True)
        card.kind_known = kind is not None
        cards.append(card)
    return cards


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
        # What tracking links led to, so each is requested once and not again every run:
        # {hash of the link: {"to": listing URL or "", "tries": n, "at": "YYYY-MM-DD"}}.
        # The runner swaps in a dict that it saves with the state (see attach_link_cache).
        self.link_cache: Dict[str, Dict[str, Any]] = {}
        self.last_status = ""  # HTTP status (or error name) of the latest tracking-link request, for email-debug

    def attach_link_cache(self, cache: Dict[str, Dict[str, Any]], today: str) -> None:
        """Use `cache` (kept by the runner between runs) and drop entries older than LINK_CACHE_DAYS."""
        try:
            cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=LINK_CACHE_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            cut = ""
        for key in [k for k, v in cache.items() if not isinstance(v, dict) or str(v.get("at", "")) < cut]:
            del cache[key]
        self.link_cache = cache

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
        if not _resolvable_host(urlparse(url).netloc):
            return None  # never request the sites themselves
        if not _public_http_url(url):
            return None
        try:
            resp = requests.get(url, allow_redirects=False, timeout=10, stream=True,
                                headers={"User-Agent": "Mozilla/5.0 (house-hunter email link resolver)"})
            loc = resp.headers.get("Location")
            self.last_status = str(getattr(resp, "status_code", ""))
            resp.close()
        except requests.RequestException as exc:
            self.last_status = type(exc).__name__
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
        cards: List[_Link] = []
        if site_hint == "pararius":
            for h in htmls:
                cards.extend(_pararius_cards(h))
            links.extend(cards)
        for h in htmls:
            links.extend(_links_from_html(h))
        for t in texts:
            links.extend(_links_from_text(t))
        links = [l for l in links if not (_SKIP_ANCHOR.search(l.anchor_text) or _SKIP_ANCHOR.search(l.href))]
        # The listings this mail names by their own links, by street ("kikkerstraat-5-a"). If there are any,
        # no tracking link is requested: a tracked link is matched to one of them by its text, or ignored.
        by_street: Dict[str, Dict[str, str]] = {}
        has_direct = False
        for link in links:
            direct = match_listing_url(link.href)
            if direct is not None:
                has_direct = True
                if direct["slug"]:
                    by_street.setdefault(_slug(direct["slug"]), direct)
        found: Dict[Tuple[str, str], Listing] = {}
        anchors: Dict[Tuple[str, str], bool] = {}
        full_texts: Optional[List[str]] = None  # the HTML parts as text, made when first needed
        titles: List[str] = []
        for link in links:
            info = match_listing_url(link.href)
            if info is None and has_direct:
                if link.from_html and _good_anchor(link.anchor_text):
                    info = by_street.get(_slug(link.anchor_text))
            elif info is None and (link.from_html or not htmls):
                # the plain-text part repeats the HTML part's links: do not request those twice
                info = self._try_redirect(link.href, budget)
            if info is None:
                continue
            if link.from_html and _good_anchor(link.anchor_text):
                if full_texts is None:
                    full_texts = [_html_text(h) for h in htmls]
                    # where a card ends: the next home's title, or a button ("Bekijk het huis", "View all ...")
                    titles = sorted({l.anchor_text for l in links if l.from_html and (
                        _good_anchor(l.anchor_text) or (len(l.anchor_text) >= 4 and _GENERIC_ANCHOR.match(l.anchor_text)))})
                for full in full_texts:
                    card = _card_text(full, link.anchor_text, titles)
                    if card:
                        link.context = card
                        break
            kind_type = _KIND_TYPE.get(info["kind"]) if info["kind"] and link.kind_known else None
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
        if not _resolvable_host(urlparse(href).netloc.lower()):
            return None  # a site link that is not a listing link (search page, logo, ...)
        embedded = _embedded_target(href)
        if embedded:
            return match_listing_url(embedded)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        host_key = "host:" + urlparse(href).netloc.lower()
        if (self.link_cache.get(host_key) or {}).get("at") == today:
            return None  # this host answered with a bot check today: leave it alone until tomorrow
        key = _link_key(href)
        entry = self.link_cache.get(key)
        if entry is not None:
            if entry.get("to"):
                return match_listing_url(entry["to"])
            if int(entry.get("tries", LINK_TRIES)) >= LINK_TRIES:
                return None  # known: not a listing, or no answer in LINK_TRIES runs
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        self.last_status = ""
        target = self._resolve(href)
        if not target and self.last_status in ("403", "429"):
            self.link_cache[host_key] = {"to": "", "tries": 0, "at": today}
            return None
        info = match_listing_url(target) if target else None
        if info is not None:
            self.link_cache[key] = {"to": info["clean"], "tries": 1, "at": today}
        elif target:  # leads somewhere else (search page, app store, ...): settled
            self.link_cache[key] = {"to": "", "tries": LINK_TRIES, "at": today}
        else:         # no redirect this time (error, block): try again in a later run, a few times
            tries = int(entry.get("tries", 0)) + 1 if entry else 1
            self.link_cache[key] = {"to": "", "tries": tries, "at": today}
        return info

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
