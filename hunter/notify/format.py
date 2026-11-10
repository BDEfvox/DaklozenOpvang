"""Pure functions that build Telegram HTML messages. Every third-party string is escaped."""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from ..filters import MatchResult
from ..models import CITIES, Listing

MAX_LEN = 3900

FLAG_LABELS: Dict[str, str] = {
    "no_sharers": "No house sharers",
    "max_persons": "Max {n} persons",
    "no_students": "No students",
    "sharers_ok": "Sharers welcome",
    "no_registration": "No registration possible",
    "income_requirement": "Income requirement",
    "short_term": "Short-term only",
    "utilities_included": "Utilities included",
    "suspicious_price": "Suspiciously cheap",
    "scam_signals": "Possible scam",
    "bedrooms_unknown": "Bedrooms unknown",
    "bedrooms_unconfirmed": "Bedrooms not confirmed",
    "price_unknown": "Price unknown",
    "rooms_only": "Only single rooms",
}

STATUS_EMOJI = {"ok": "✅", "partial": "🟡", "blocked": "⛔", "error": "❌", "disabled": "💤"}


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def clip(s: Optional[str], n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def euro(n: Optional[int]) -> str:
    if n is None:
        return "?"
    return "€" + "{:,}".format(int(n)).replace(",", ".")


def city_name(slug: Optional[str]) -> str:
    return CITIES.get(slug or "", slug or "")


def flag_label(flag: str, labels: Optional[Dict[str, str]] = None) -> str:
    labels = labels or FLAG_LABELS
    key, _, val = flag.partition(":")
    label = labels.get(key, key.replace("_", " "))
    return label.replace("{n}", val)


def fill_reply(listing: Listing, cfg: Dict[str, Any]) -> str:
    tpl = cfg.get("reply_template", "") or ""
    vals = {
        "title": listing.title or "", "address": listing.address or listing.title or "",
        "city": city_name(listing.city), "url": listing.url or "",
    }
    out = tpl
    for k, v in vals.items():
        out = out.replace("{" + k + "}", v)
    return out


def _tier_emoji(tier: Optional[str]) -> str:
    return "🟢" if tier == "great" else "🟡"


def _facts(listing: Listing) -> List[str]:
    parts: List[str] = []
    if listing.bedrooms is not None:
        parts.append("%d bedroom%s" % (listing.bedrooms, "" if listing.bedrooms == 1 else "s"))
    if listing.rooms is not None:
        parts.append("%d rooms" % listing.rooms)
    if listing.area_m2 is not None:
        parts.append("%d m²" % listing.area_m2)
    if listing.furnished:
        parts.append(listing.furnished)
    return parts


def _price_line(listing: Listing, result: MatchResult) -> str:
    line = "💶 <b>%s</b>/month" % e(euro(listing.price_total))
    if listing.price_includes_utilities is True:
        line += " (incl. utilities)"
    elif listing.price_includes_utilities is False:
        line += " (excl. utilities)"
    pps = ["%s pp for %d" % (euro(m.price_pp), m.persons) for m in result.matches if m.price_pp is not None]
    if pps:
        line += " · " + e(" · ".join(pps))
    return line


def _body(listing: Listing, result: MatchResult, cfg: Dict[str, Any], header: str,
          source_label: str, extra_price_line: Optional[str] = None) -> str:
    labels = FLAG_LABELS
    if (listing.url or "").startswith(("https://", "http://")):
        lines = ["%s <a href=\"%s\"><b>%s</b></a>" % (header, e(listing.url), e(clip(listing.title, 150)))]
    else:
        lines = ["%s <b>%s</b>" % (header, e(clip(listing.title, 150)))]
    loc = ", ".join(x for x in (clip(listing.address, 100), city_name(listing.city)) if x)
    if loc:
        lines.append("📍 " + e(loc))
    if extra_price_line:
        lines.append(extra_price_line)
    lines.append(_price_line(listing, result))
    facts = _facts(listing)
    if facts:
        lines.append("🛏 " + e(" · ".join(facts)))
    if listing.available_from or listing.available_until:
        d = "📅 "
        if listing.available_from:
            d += "Available from " + e(listing.available_from)
        if listing.available_until:
            d += (" " if listing.available_from else "") + "until " + e(listing.available_until)
        lines.append(d)
    warn = [f for f in result.flags if f not in ("utilities_included", "sharers_ok")]
    if warn:
        lines.append("⚠️ " + e(" · ".join(flag_label(f, labels) for f in warn)))
    if "sharers_ok" in result.flags:
        lines.append("✅ " + e(flag_label("sharers_ok", labels)))
    lines.append("🔎 " + e(source_label))
    return "\n".join(lines)


def _with_reply(base: str, listing: Listing, cfg: Dict[str, Any]) -> str:
    reply = fill_reply(listing, cfg)
    if not reply:
        return base
    quote = "<blockquote expandable>%s</blockquote>" % e(clip(reply, 1500))
    msg = base + "\n\n✉️ Reply template:\n" + quote
    return msg if len(msg) <= MAX_LEN else base


def format_new_match(listing: Listing, result: MatchResult, cfg: Dict[str, Any],
                     source_label: Optional[str] = None) -> str:
    base = _body(listing, result, cfg, _tier_emoji(result.best_tier), source_label or listing.source)
    return _with_reply(base, listing, cfg)


def format_price_drop(listing: Listing, result: MatchResult, old_price: int, cfg: Dict[str, Any],
                      source_label: Optional[str] = None) -> str:
    extra = "📉 <b>Price drop</b>: %s → %s" % (e(euro(old_price)), e(euro(listing.price_total)))
    base = _body(listing, result, cfg, _tier_emoji(result.best_tier), source_label or listing.source,
                 extra_price_line=extra)
    return _with_reply(base, listing, cfg)


def _dash_link(url: Optional[str]) -> str:
    return "\n\n📊 <a href=\"%s\">Dashboard</a>" % e(url) if url else ""


def format_first_run_summary(total: int, sources: List[str], url: Optional[str]) -> str:
    msg = "👋 Found <b>%d</b> current match%s on %s. " % (
        total, "" if total == 1 else "es", e(", ".join(sources)))
    msg += "All of them are on the dashboard"
    msg += ":" if url else "."
    return clip(msg, MAX_LEN) + _dash_link(url)


def format_overflow(count: int, url: Optional[str]) -> str:
    return "➕ <b>%d more new match%s</b> not sent individually (message limit per run). See the dashboard." % (
        count, "" if count == 1 else "es") + _dash_link(url)


def format_health_alert(label: str, status: str, failures: int, error: Optional[str]) -> str:
    if status == "blocked":
        why = "the site is blocking automated checks from GitHub's servers"
    else:
        why = clip(error, 300) or "unknown error"
    return "⚠️ <b>%s</b> has failed %d runs in a row: %s.\nListings from this source are not being checked." % (
        e(label), failures, e(why))


def format_recovery(label: str) -> str:
    return "✅ <b>%s</b> is working again." % e(label)


def format_digest(per_city: Dict[str, int], sources: List[Dict[str, Any]], url: Optional[str],
                  day_label: str) -> str:
    lines = ["📋 <b>Daily digest</b> (new matches %s)" % e(day_label)]
    total = sum(per_city.values())
    if total == 0:
        lines.append("No new matches.")
    else:
        for slug, n in sorted(per_city.items()):
            lines.append("%s: %d" % (e(city_name(slug)), n))
    lines.append("")
    for s in sources:
        lines.append("%s %s" % (STATUS_EMOJI.get(s.get("status", ""), "•"), e(s.get("label") or s.get("name"))))
    return clip("\n".join(lines), MAX_LEN) + _dash_link(url)


def format_status_report(sources: List[Dict[str, Any]], url: Optional[str]) -> str:
    """Sent once, on the very first run: which sites can be checked from here."""
    lines = ["🏁 <b>House Hunter is running.</b> Sites checked this first run:"]
    for s in sources:
        st = s.get("status", "")
        line = "%s %s" % (STATUS_EMOJI.get(st, "•"), e(s.get("label") or s.get("name")))
        if st in ("ok", "partial") and s.get("count") is not None:
            line += " (%d listings)" % int(s["count"])
        elif st == "blocked":
            line += ": blocks automated checks from here"
        elif st == "error":
            line += ": " + e(clip(s.get("error"), 120))
        elif st == "disabled":
            line += ": " + e(clip(s.get("note"), 120))
        lines.append(line)
    lines.append("")
    lines.append("New matches will be posted here as soon as they appear. A short summary comes every morning.")
    return clip("\n".join(lines), MAX_LEN) + _dash_link(url)
