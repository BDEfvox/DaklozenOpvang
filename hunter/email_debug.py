"""`python -m hunter email-debug`: why does the alert mailbox give no listings?

Prints a summary of the alert emails that is safe for a public log: how many messages each folder holds,
the senders' domains, and the *shape* of the links (hostname, fixed path words, names of query
parameters). Never printed: subjects, names, email addresses, body text, link tokens or query values.
The mailbox is opened read-only, like the checker does.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

from .sources import email_alerts as ea
from .sources.base import SourceError
from .utils import city_slug

MAX_SHAPES = 40      # lines per group
MAX_RESOLVE = 12     # tracking links followed one step, one per (host, shape)

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


def _classify(href: str) -> Tuple[str, Optional[dict]]:
    info = ea.match_listing_url(href)
    if info is not None:
        kind = ea._KIND_TYPE.get(info["kind"]) if info["kind"] else None
        if kind in ("room", "parking"):
            return "LISTING, skipped: %s" % kind, info
        if city_slug(info["city"]) is None and city_slug(info["city"].replace("-", " ")) is None:
            return "LISTING, skipped: city not in config", info
        return "LISTING, accepted", info
    host = urlparse(href).netloc.lower()
    if any(ea._host_ok(host, d) for d in ea.SITE_DOMAINS.values()):
        return "site link, not recognised as a listing", None
    if ea._embedded_target(href):
        return "tracking link with the listing in a parameter", None
    return "other host (would be resolved)", None


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

    senders: Counter = Counter()
    shapes: Counter = Counter()
    examples: dict = {}
    for msg in messages:
        dom = _sender_domain(msg)
        site = ea._sender_site(msg)
        htmls, texts = ea.message_bodies(msg)
        links: List[Any] = []
        for h in htmls:
            links.extend(ea._links_from_html(h))
        for t in texts:
            links.extend(ea._links_from_text(t))
        senders[(dom, "sender accepted as %s" % site if site else "SENDER NOT ACCEPTED", "html parts %d" % len(htmls),
                 "text parts %d" % len(texts), "links %s" % ("0" if not links else "1+"))] += 1
        for link in links:
            if ea._SKIP_ANCHOR.search(link.anchor_text) or ea._SKIP_ANCHOR.search(link.href):
                continue
            verdict, _ = _classify(link.href)
            key = (dom, verdict, url_shape(link.href))
            shapes[key] += 1
            examples.setdefault(key, link.href)

    print("\n== Senders ==")
    for key, n in senders.most_common(MAX_SHAPES):
        print("%3dx  %s" % (n, " | ".join(key)))

    print("\n== Link shapes (unsubscribe/privacy links left out) ==")
    resolved = 0
    for key, n in shapes.most_common(MAX_SHAPES):
        dom, verdict, shape = key
        line = "%3dx  [%s] %s :: %s" % (n, dom, verdict, shape)
        if verdict.startswith("other host") and resolved < MAX_RESOLVE:
            resolved += 1
            target = src._resolve(examples[key])
            if not target:
                line += "  -> no redirect"
            else:
                line += "  -> %s :: %s" % (_classify(target)[0], url_shape(target))
        print(line)
    if len(shapes) > MAX_SHAPES:
        print("... and %d more shapes" % (len(shapes) - MAX_SHAPES))

    budget = [ea.MAX_RESOLVE]
    total = sum(len(src.parse_message(m, budget)) for m in messages)
    print("\nlistings the checker gets out of these messages: %d" % total)
    return 0
