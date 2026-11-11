"""`python -m hunter email-debug`: why does the alert mailbox give no listings?

Prints a summary of the alert emails that is safe for a public log: how many messages each folder holds,
the senders' domains, and the *shape* of the links (hostname, fixed path words, names of query
parameters). Never printed: subjects, names, email addresses, body text, link tokens or query values.
The mailbox is opened read-only, like the checker does.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

from .config import load_config
from .filters import evaluate
from .sources import email_alerts as ea
from .sources.base import SourceError
from .utils import city_slug

MAX_SHAPES = 60      # lines per group

_WORD = re.compile(r"[a-z]+|\d{1,4}[a-z]?")


def _segment(seg: str) -> str:
    """Keep plain lowercase words (path names, city, street + house number); hide ids and tokens."""
    if not seg:
        return ""
    if len(seg) > 80:
        return "<%d chars>" % len(seg)
    out = []
    for tok in seg.split("-"):
        if _WORD.fullmatch(tok):
            out.append(tok)
        elif tok.isdigit():
            out.append("<%d digits>" % len(tok))
        else:
            return "<%d chars>" % len(seg)
    return "-".join(out)


def url_shape(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.hostname or "?").lower()
        path = "/".join(_segment(s) for s in p.path.split("/"))
        names = sorted({k for k, _ in parse_qsl(p.query, keep_blank_values=True) if re.fullmatch(r"[A-Za-z_.-]{1,24}", k)})
        return host + path + ("?" + "&".join(names) if names else "")
    except ValueError:
        return "<unparsable>"


def _sender_domain(msg: Any) -> str:
    addr = parseaddr(msg.get("From", ""))[1].lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    # only show domains of the four sites; anything else in the mailbox is none of the log's business
    return domain if any(k in domain for k in ea.SENDER_KEYWORDS) else "<other sender>"


def _classify(href: str) -> str:
    info = ea.match_listing_url(href)
    if info is not None:
        kind = ea._KIND_TYPE.get(info["kind"]) if info["kind"] else None
        if kind in ("room", "parking"):
            return "LISTING, skipped: %s" % kind
        if city_slug(info["city"]) is None and city_slug(info["city"].replace("-", " ")) is None:
            return "LISTING, skipped: city not in config"
        return "LISTING, accepted"
    host = urlparse(href).netloc.lower()
    if ea._tracking_host(host):
        return "tracking link on the site's mail subdomain"
    if ea._site_host(host):
        return "site link, not a listing"
    if ea._embedded_target(href):
        return "tracking link with the listing in a parameter"
    return "tracking link on another host"


def _payload_hint(url: str) -> str:
    """Does a long path segment hold the target URL in base64? Says yes/no and the target's shape."""
    for seg in urlparse(url).path.split("/"):
        if len(seg) < 80:
            continue
        try:
            raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))
        except (ValueError, binascii.Error):
            return " [long segment: not base64]"
        m = re.search(rb"https?://[\x21-\x7e]+", raw)
        if m:
            return " [long segment: base64 holding %s]" % url_shape(m.group(0).decode("ascii", "replace"))
        return " [long segment: base64, no URL inside]"
    return ""


_KEEP_WORDS = set("""per maand mnd p/m m² m2 kamers kamer slaapkamers slaapkamer appartement huis studio woning
woningen te huur nieuw nieuwe in bekijk gemeubileerd gestoffeerd kaal excl incl vanaf beschikbaar direct den haag
amsterdam leiden 's-gravenhage the hague room rooms bedroom bedrooms apartment house for rent month view new
furnished upholstered oppervlakte huurprijs prijs woonoppervlakte aangeboden sinds makelaar deze alle van de het
een op bij voor jouw je zoekopdracht resultaten meer details""".split())


def mask_text(text: str, limit: int = 240) -> str:
    """Layout of a text without its content: letters become a/A and digits 9, except a few fixed words
    of the mail template (kamers, per maand, ...) and the configured city names."""
    out = []
    for tok in (text or "").split()[:80]:
        bare = tok.strip(".,;:!?()[]|·•-–—\"'").lower()
        if bare in _KEEP_WORDS:
            out.append(tok)
        else:
            out.append(re.sub(r"[^\W\d_]", lambda m: "A" if m.group(0).isupper() else "a", re.sub(r"\d", "9", tok)))
    return " ".join(out)[:limit]


def _outline(msg: Any, limit: int = 45) -> None:
    """The anchors of one mail, in order: where they sit, masked text, masked surrounding text, image host."""
    from bs4 import BeautifulSoup
    htmls, _ = ea.message_bodies(msg)
    if not htmls:
        print("  (no HTML part)")
        return
    soup = BeautifulSoup(htmls[0], "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    links = ea._links_from_html(htmls[0])
    anchors = [a for a in soup.find_all("a", href=True) if a["href"].startswith("http")]
    for n, (a, link) in enumerate(zip(anchors, links)):
        if n >= limit:
            print("  ... %d more anchors" % (len(anchors) - limit))
            break
        chain = []
        node = a.parent
        while node is not None and node.name not in ("[document]", "html", "body") and len(chain) < 5:
            chain.append(node.name)
            node = node.parent
        img = a.find("img")
        img_part = ""
        if img is not None:
            img_part = " img[%s alt:%s]" % (url_shape(img.get("src", "")) if str(img.get("src", "")).startswith("http")
                                            else "?", mask_text(img.get("alt", ""), 60) or "-")
        skip = " (skipped: unsubscribe/privacy)" if (ea._SKIP_ANCHOR.search(link.anchor_text)
                                                      or ea._SKIP_ANCHOR.search(link.href)) else ""
        print("  #%02d <%s>%s%s text[%s] around[%s]" % (n, "<".join(chain), img_part, skip,
                                                        mask_text(link.anchor_text, 80), mask_text(link.context)))


def _count(conn: Any, *criteria: str) -> str:
    try:
        typ, data = conn.search(None, *criteria)
    except Exception as exc:  # noqa: BLE001
        return "error %s" % type(exc).__name__
    if typ != "OK":
        return "search failed"
    return str(sum(len(c.split()) for c in (data or []) if c))


def _folders(conn: Any) -> List[Tuple[str, str]]:
    """(label, quoted mailbox name) for the inbox plus Gmail's All Mail and Spam, found by their flags."""
    out = [("INBOX", "INBOX")]
    try:
        typ, data = conn.list()
    except Exception:  # noqa: BLE001
        return out
    if typ != "OK":
        return out
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        for flag, label in (("\\All", "All Mail"), ("\\Junk", "Spam"), ("\\Trash", "Trash")):
            if flag.lower() in m.group("flags").lower():
                out.append((label, m.group("name").strip()))
    return out


def run() -> int:
    src = ea.EmailAlerts()
    reason = src.unavailable_reason()
    if reason:
        print(reason)
        return 2
    now = datetime.now(timezone.utc)
    since = ea.imap_date(now - timedelta(days=ea.SINCE_DAYS))
    folder = os.environ.get("IMAP_FOLDER") or "INBOX"
    print("Folder the checker reads: %s (custom IMAP_FOLDER set: %s)" % (
        "INBOX" if folder == "INBOX" else "<custom>", "yes" if os.environ.get("IMAP_FOLDER") else "no"))
    try:
        conn = src._connect()
    except SourceError as exc:
        print("LOGIN FAILED: %s" % exc)
        return 1
    print("Login: ok")
    try:
        print("\n== Messages of the last %d days, per folder ==" % ea.SINCE_DAYS)
        for label, name in _folders(conn):
            try:
                typ, _ = conn.select(name, readonly=True)
            except Exception as exc:  # noqa: BLE001
                print("%-9s cannot open (%s)" % (label, type(exc).__name__))
                continue
            if typ != "OK":
                print("%-9s cannot open" % label)
                continue
            parts = ["all senders: %s" % _count(conn, "SINCE", since), "any date: %s" % _count(conn, "ALL")]
            for kw in ea.SENDER_KEYWORDS:
                parts.append("from %s: %s" % (kw, _count(conn, "SINCE", since, "FROM", kw)))
            for kw in ea.SENDER_KEYWORDS:
                parts.append("mentions %s: %s" % (kw, _count(conn, "SINCE", since, "TEXT", kw)))
            print("%-9s %s" % (label, " | ".join(parts)))

        print("\n== What the checker fetches ==")
        messages = src._fetch_messages(conn, now)
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    print("messages fetched: %d (limit %d)" % (len(messages), ea.MAX_MESSAGES))

    # Parse exactly like the checker does, and note where every tracking link led.
    resolved: dict = {}
    real_resolve = src._resolve

    def recording_resolve(url: str) -> Optional[str]:
        target = real_resolve(url)
        resolved[url] = (target, src.last_status)
        return target

    src._resolve = recording_resolve  # type: ignore[method-assign]
    budget = [ea.MAX_RESOLVE]
    listings: List[Any] = []
    for msg in messages:
        listings.extend(src.parse_message(msg, budget))

    senders: Counter = Counter()
    shapes: Counter = Counter()
    for msg in messages:
        dom = _sender_domain(msg)
        site = ea._sender_site(msg)
        htmls, texts = ea.message_bodies(msg)
        links: List[Tuple[str, Any]] = []
        for h in htmls:
            links.extend(("html", l) for l in ea._links_from_html(h))
        for t in texts:
            links.extend(("text", l) for l in ea._links_from_text(t))
        senders[(dom, "sender accepted as %s" % site if site else "SENDER NOT ACCEPTED", "html parts %d" % len(htmls),
                 "text parts %d" % len(texts), "links %s" % ("0" if not links else "1+"))] += 1
        for part, link in links:
            if ea._SKIP_ANCHOR.search(link.anchor_text) or ea._SKIP_ANCHOR.search(link.href):
                continue
            line = "[%s %s] %s :: %s%s" % (dom, part, _classify(link.href), url_shape(link.href), _payload_hint(link.href))
            if link.href in resolved:
                target, status = resolved[link.href]
                if target:
                    line += "  -> HTTP %s -> %s :: %s" % (status, _classify(target), url_shape(target))
                else:
                    line += "  -> HTTP %s, no redirect" % status
            shapes[line] += 1

    print("\n== Layout of the newest mail per sender (content masked) ==")
    shown = set()
    for msg in messages:
        dom = _sender_domain(msg)
        if dom in shown or ea._sender_site(msg) is None:
            continue
        shown.add(dom)
        print("[%s]" % dom)
        _outline(msg)

    print("\n== Senders ==")
    for key, n in senders.most_common(MAX_SHAPES):
        print("%3dx  %s" % (n, " | ".join(key)))

    print("\n== Link shapes (unsubscribe/privacy links left out) ==")
    for line, n in shapes.most_common(MAX_SHAPES):
        print("%3dx  %s" % (n, line))
    if len(shapes) > MAX_SHAPES:
        print("... and %d more shapes" % (len(shapes) - MAX_SHAPES))
    print("tracking links requested: %d (limit per run %d)" % (len(resolved), ea.MAX_RESOLVE))

    print("\n== Listings the checker gets out of these messages: %d ==" % len(listings))
    cfg = load_config()
    for l in listings:
        l.description = l.description or ""
        res = evaluate(l, cfg)
        print("  %-12s %-9s %-9s price:%s bedrooms:%s rooms:%s m2:%s title:%s -> %s" % (
            l.extra.get("site"), l.city, l.property_type or "?",
            "yes" if l.price_total is not None else "no", "yes" if l.bedrooms is not None else "no",
            "yes" if l.rooms is not None else "no", "yes" if l.area_m2 is not None else "no",
            "from link text" if l.address and l.address not in (l.title or "") else "from URL",
            "MATCHES your searches" if res.matched else "does not match your searches"))
    return 0
