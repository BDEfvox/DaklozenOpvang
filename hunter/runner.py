"""One hunting run: fetch every source, find new matches, notify, update state and dashboard."""
from __future__ import annotations

import os
import re
import sys
import traceback
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .dashboard import write_dashboard
from .filters import MatchResult, evaluate
from .models import Listing
from .notify import format as fmt
from .notify.telegram import Notifier
from .sources.base import Source, SourceBlocked, SourceError
from .state import State, iso

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

PRICE_DROP_MIN_PCT = 0.03


# --------------------------------------------------------------------------- fetching
@dataclass
class FetchResult:
    listings: List[Listing] = field(default_factory=list)
    status: str = "ok"                       # ok | partial | blocked | error
    error: Optional[str] = None
    per_city: Dict[str, Tuple[int, Optional[str]]] = field(default_factory=dict)  # city -> (count, error)


def redact(text: str) -> str:
    """Remove secret values (raw and URL-encoded) from text that may be published or logged."""
    for key in ("TELEGRAM_BOT_TOKEN", "IMAP_PASSWORD", "IMAP_USER"):
        val = os.environ.get(key, "").strip()
        if len(val) >= 4:
            text = text.replace(val, "***").replace(quote(val, safe=""), "***")
    return text


def _describe(exc: Exception) -> str:
    if isinstance(exc, (SourceBlocked, SourceError)):
        return redact(str(exc))[:300]
    return redact("%s: %s" % (type(exc).__name__, exc))[:300]


def fetch_source(source: Source, cfg: Dict[str, Any], http: Any, only_city: Optional[str] = None) -> FetchResult:
    """Fetch all configured cities of one source. Never raises."""
    res = FetchResult()
    cities_cfg = list(cfg.get("cities", []))
    errors: List[Tuple[str, Exception]] = []
    ok_cities = 0
    if source.per_city:
        cities = [c for c in source.cities if c in cities_cfg and (only_city is None or c == only_city)]
        for city in cities:
            try:
                items = list(source.fetch(city, http) or [])
            except Exception as exc:
                errors.append((city, exc))
                res.per_city[city] = (0, _describe(exc))
                if isinstance(exc, SourceBlocked):
                    break  # do not keep knocking on a door that is closed
                continue
            items = [l for l in items if l.city in cities_cfg]
            res.listings.extend(items)
            res.per_city[city] = (len(items), None)
            ok_cities += 1
    else:
        try:
            items = list(source.fetch("*", http) or [])
            items = [l for l in items if l.city in cities_cfg and (only_city is None or l.city == only_city)]
            res.listings.extend(items)
            res.per_city["*"] = (len(items), None)
            ok_cities = 1
        except Exception as exc:
            errors.append(("*", exc))
            res.per_city["*"] = (0, _describe(exc))
    if errors and ok_cities:
        res.status = "partial"
        res.error = "; ".join("%s: %s" % (c, _describe(x)) for c, x in errors)[:300]
    elif errors:
        blocked = all(isinstance(x, SourceBlocked) for _, x in errors)
        res.status = "blocked" if blocked else "error"
        res.error = _describe(errors[0][1])
    return res


# --------------------------------------------------------------------------- helpers
def _norm_addr(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fingerprint(l: Listing) -> Optional[str]:
    a = _norm_addr(l.address)
    if len(a) < 5 or l.price_total is None:
        return None
    return "%s|%s|%d" % (a, l.city, l.price_total)


def _local_tz(cfg: Dict[str, Any]):
    name = cfg.get("telegram", {}).get("timezone", "Europe/Amsterdam")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone(timedelta(hours=1))


@dataclass
class Item:
    source: Source
    listing: Listing
    result: MatchResult
    detailed: bool = False
    search_price: Optional[int] = None
    had_snapshot: bool = False

    @property
    def sort_key(self) -> Tuple[int, int]:
        pp = self.result.best_price_pp
        return (0 if self.result.best_tier == "great" else 1, pp if pp is not None else 10 ** 9)


class Sender:
    """Wraps the notifier: counts, stops after repeated failures."""

    def __init__(self, notifier: Notifier, dashboard_url: Optional[str]) -> None:
        self.n = notifier
        self.dash = dashboard_url or None
        self.fail_streak = 0
        self.sent = 0

    @property
    def broken(self) -> bool:
        return self.fail_streak >= 2

    def send(self, text: str, url: Optional[str] = None) -> bool:
        if self.broken:
            return False
        ok = self.n.send(text, url=url, dashboard_url=self.dash)
        self.fail_streak = 0 if ok else self.fail_streak + 1
        if ok:
            self.sent += 1
        return ok


def _card(state: State, it: Item, now_s: str) -> None:
    old = state.matches.get(it.listing.uid, {})
    flags = list(it.result.flags)
    for f in old.get("flags", []):  # keep flags found from a detail page earlier
        if f not in flags and f.split(":")[0] in ("no_sharers", "max_persons", "no_students", "scam_signals",
                                                  "no_registration", "income_requirement", "sharers_ok"):
            flags.append(f)
    state.matches[it.listing.uid] = {
        "listing": it.listing.to_dict(),
        "first_seen": old.get("first_seen") or now_s,
        "last_seen": now_s,
        "matches": [m.to_dict() for m in it.result.matches],
        "flags": flags,
        "best_tier": it.result.best_tier,
        "price_drop_from": old.get("price_drop_from"),
    }


_DETAIL_FIELDS = ("bedrooms", "rooms", "area_m2", "property_type", "price_total", "price_includes_utilities",
                  "description", "address", "postcode", "available_from", "available_until")


def detail_snapshot(l: Listing, search_price: Optional[int]) -> Dict[str, Any]:
    """What a detail page told us, so later runs (which only see the search page) don't forget it."""
    d: Dict[str, Any] = {k: getattr(l, k) for k in _DETAIL_FIELDS if getattr(l, k) is not None}
    if "description" in d:
        d["description"] = d["description"][:2500]
    if l.extra:
        d["extra"] = {k: v for k, v in l.extra.items() if isinstance(v, (str, int, float, bool)) or v is None}
    d["search_price"] = search_price
    return d


def apply_detail_snapshot(l: Listing, d: Dict[str, Any]) -> None:
    for k in _DETAIL_FIELDS:
        if k == "price_total" or d.get(k) is None:
            continue
        setattr(l, k, d[k])
    # Only reuse a detail-page price while the search page still shows the same price as back then;
    # if the landlord changed the price, the search page wins.
    if d.get("price_total") is not None and l.price_total == d.get("search_price"):
        l.price_total = d["price_total"]
    if d.get("extra"):
        l.extra.update(d["extra"])


DETAIL_MAX_TRIES = 3


def _bedrooms_unsure(it: Item) -> bool:
    return any(f in ("bedrooms_unconfirmed", "bedrooms_unknown") for f in it.result.flags)


def _needs_detail_check(prev: Dict[str, Any], it: Item) -> bool:
    """Is this known match worth a (new) look at its detail page?"""
    if int(prev.get("detail_tries") or 0) >= DETAIL_MAX_TRIES:
        return False
    d = prev.get("details")
    if not d:
        return True
    # An earlier look that taught us nothing about the bedrooms and found no description
    # (e.g. the source could not read detail pages yet): look again, unless the site hides the page.
    return (_bedrooms_unsure(it) and not d.get("description")
            and not (d.get("extra") or {}).get("details_unavailable"))


def _enrich(items: List[Item], cfg: Dict[str, Any], http: Any, budget: List[int]) -> List[Item]:
    """fetch_details for the first `budget[0]` items; re-evaluate; drop those that no longer match."""
    out: List[Item] = []
    for it in items:
        if budget[0] > 0:
            budget[0] -= 1
            try:
                it.search_price = it.listing.price_total
                it.listing = it.source.fetch_details(it.listing, http) or it.listing
                it.result = evaluate(it.listing, cfg)
                it.detailed = True
            except Exception as exc:
                print("[detail] %s: %s" % (it.listing.uid, _describe(exc)), file=sys.stderr)  # redacted
        if it.result.matched:
            out.append(it)
    return out


# --------------------------------------------------------------------------- the run
def run_once(cfg: Dict[str, Any], state: State, sources: Dict[str, Source], notifier: Notifier, http: Any,
             now: Optional[datetime] = None, only: Optional[List[str]] = None, write: bool = True,
             docs_dir: Optional[str] = None) -> int:
    now = now or datetime.now(timezone.utc)
    now_s = iso(now)
    tz = _local_tz(cfg)
    local_now = now.astimezone(tz)
    today = local_now.date().isoformat()
    tcfg = cfg.get("telegram", {})
    sender = Sender(notifier, cfg.get("dashboard_url"))
    dash = cfg.get("dashboard_url") or None
    first_ever = not state.meta.get("last_run")

    disabled_cfg = set(cfg.get("disabled_sources", []))
    selected = {n: s for n, s in sources.items() if (not only or n in only)}

    def set_health(name: str, src: Source, **kw: Any) -> Dict[str, Any]:
        h = state.health.setdefault(name, {})
        h.update({"label": src.label or name, "homepage": src.homepage, "note": src.note})
        h.update(kw)
        return h

    # 1. which sources run
    active: Dict[str, Source] = {}
    for name, src in selected.items():
        if name in disabled_cfg:
            set_health(name, src, status="disabled", error=None, note="disabled in config.json",
                       consecutive_failures=0, alerted=False, listings_last_run=None)
            continue
        reason = None
        try:
            reason = src.unavailable_reason()
        except Exception as exc:
            reason = "unavailable: %s" % _describe(exc)
        if reason:
            set_health(name, src, status="disabled", error=None, note=reason,
                       consecutive_failures=0, alerted=False, listings_last_run=None)
            continue
        active[name] = src

    # 2. fetch
    fetched: Dict[str, FetchResult] = {}
    for name, src in active.items():
        try:
            fetched[name] = fetch_source(src, cfg, http)
        except Exception as exc:  # fetch_source should never raise, but be safe
            fetched[name] = FetchResult(status="error", error=_describe(exc))

    # 3-6. evaluate
    baseline_pool: List[Item] = []
    baseline_sources: List[str] = []
    new_items: List[Item] = []
    drop_items: List[Tuple[Item, int]] = []
    backlog: List[Item] = []  # matches already on the dashboard whose detail page was never read
    run_fps: Dict[str, str] = {}
    seen_uids = set()

    for name, src in active.items():
        fr = fetched[name]
        if fr.status in ("blocked", "error"):
            continue
        is_baseline = bool(fr.listings) and not state.source_has_seen(name)
        if is_baseline:
            baseline_sources.append(src.label or name)
        for l in fr.listings:
            if l.uid in seen_uids:
                continue
            seen_uids.add(l.uid)
            old_card = state.matches.get(l.uid)
            if old_card and not l.description:
                l.description = (old_card.get("listing") or {}).get("description")
            res = evaluate(l, cfg)
            it = Item(src, l, res)
            fp = fingerprint(l)
            prev = state.seen.get(l.uid)
            if prev is not None and prev.get("details"):
                apply_detail_snapshot(l, prev["details"])
                res = evaluate(l, cfg)
                it = Item(src, l, res, had_snapshot=True)
                fp = fingerprint(l)

            if prev is not None and prev.get("dup_of"):
                # same home already shown from another site: keep it out of alerts and the dashboard
                prev["last_seen"] = now_s
                continue

            if prev is not None:
                # known listing: refresh, check price drop
                old_price = prev.get("last_price")
                prev["last_seen"] = now_s
                prev["last_price"] = l.price_total
                if fp:
                    prev["fp"] = fp
                was_matched = bool(prev.get("matched"))
                prev["matched"] = [m.search for m in res.matches]
                if res.matched:
                    _card(state, it, now_s)
                    if (old_price is not None and l.price_total is not None and l.price_total < old_price
                            and (not was_matched
                                 or (old_price - l.price_total) / float(old_price) >= PRICE_DROP_MIN_PCT)):
                        state.matches[l.uid]["price_drop_from"] = old_price
                        drop_items.append((it, old_price))
                    elif not was_matched and not is_baseline:
                        new_items.append(it)  # e.g. details or the budget in config.json changed
                    elif _needs_detail_check(prev, it):
                        backlog.append(it)
                else:
                    state.matches.pop(l.uid, None)
                continue

            # new listing
            dup_of = None
            if fp and res.matched:
                other = run_fps.get(fp)
                if other is None:
                    dup_of = state.find_duplicate(fp, name, now)
                    run_fps[fp] = name
                elif other != name:
                    dup_of = other
            entry = {"first_seen": now_s, "last_seen": now_s, "last_price": l.price_total,
                     "matched": [m.search for m in res.matches], "notified_at": None, "source": name}
            if fp:
                entry["fp"] = fp
            if dup_of:
                entry["dup_of"] = dup_of
            state.seen[l.uid] = entry
            if dup_of or not res.matched:
                continue
            if is_baseline:
                _card(state, it, now_s)
                baseline_pool.append(it)
            else:
                _card(state, it, now_s)
                new_items.append(it)

    detail_budget = [int(cfg.get("detail_fetch_max_per_run", 10))]

    def label(it: Item) -> str:
        return it.source.label or it.source.name

    def unmark(it: Item) -> None:
        state.seen.pop(it.listing.uid, None)
        state.matches.pop(it.listing.uid, None)

    def enrich_and_prune(items: List[Item], budget: Optional[List[int]] = None) -> List[Item]:
        # A detail page can show that a listing doesn't fit after all (e.g. 1 bedroom, "geen woningdelers").
        kept = _enrich(items, cfg, http, detail_budget if budget is None else budget)
        for it in items:
            entry = state.seen.get(it.listing.uid)
            if it.detailed and entry is not None:
                entry["details"] = detail_snapshot(it.listing, it.search_price)
                entry["last_price"] = it.listing.price_total
                entry["matched"] = [m.search for m in it.result.matches]
        kept_ids = {id(i) for i in kept}
        for it in items:
            if id(it) not in kept_ids:
                state.matches.pop(it.listing.uid, None)
                if it.listing.uid in state.seen:
                    state.seen[it.listing.uid]["matched"] = []
        return kept

    # 4. first-run pool
    if baseline_pool:
        baseline_pool.sort(key=lambda i: i.sort_key)
        # Verify detail pages in order (best first) until we have enough confirmed matches to send:
        # search pages can be misleading (per-person prices, rooms listed as apartments, unknown bedrooms).
        want = int(tcfg.get("first_run_max", 10))
        first_budget = [int(cfg.get("first_run_detail_max", 30))]
        top: List[Item] = []
        for it in baseline_pool:
            if len(top) >= want:
                break
            top.extend(enrich_and_prune([it], first_budget))
        top.sort(key=lambda i: i.sort_key)
        total = sum(1 for it in baseline_pool if it.listing.uid in state.matches)
        for it in top:
            _card(state, it, now_s)
            if sender.send(fmt.format_new_match(it.listing, it.result, cfg, label(it)), it.listing.url):
                state.seen[it.listing.uid]["notified_at"] = now_s
        sender.send(fmt.format_first_run_summary(total, baseline_sources, dash))

    # 5/6. new matches and price drops
    cap = int(tcfg.get("max_messages_per_run", 15))
    new_items.sort(key=lambda i: i.sort_key)
    new_items = enrich_and_prune(new_items)
    new_items.sort(key=lambda i: i.sort_key)
    for it in new_items:
        _card(state, it, now_s)
    # Re-check the detail page before announcing a price drop (the search price may be per person, etc.).
    if drop_items:
        kept_drops = {id(i) for i in enrich_and_prune([it for it, _ in drop_items])}
        verified_drops: List[Tuple[Item, int]] = []
        for it, old in drop_items:
            price = it.listing.price_total
            if it.had_snapshot and not it.detailed:
                # the old price came from a detail page we couldn't re-check this run: try again next run
                if it.listing.uid in state.seen:
                    state.seen[it.listing.uid]["last_price"] = old
                if it.listing.uid in state.matches:
                    state.matches[it.listing.uid]["price_drop_from"] = None
                continue
            if id(it) in kept_drops and price is not None and price < old:
                _card(state, it, now_s)
                state.matches[it.listing.uid]["price_drop_from"] = old
                verified_drops.append((it, old))
            elif it.listing.uid in state.matches:
                state.matches[it.listing.uid]["price_drop_from"] = None
        drop_items = verified_drops
    # 6b. Older matches that were never checked against their detail page (the first run only verifies
    # the ones it sends; busy runs can exceed the budget). Use what is left of this run's budget, starting
    # with the ones whose bedroom count is a guess. No alerts: this only corrects or removes dashboard cards.
    backlog = [it for it in backlog if not it.detailed and it.listing.uid in state.matches]
    backlog.sort(key=lambda i: (0 if _bedrooms_unsure(i) else 1,) + i.sort_key)
    todo = backlog[:max(detail_budget[0], 0)]
    if todo:
        kept_backlog = {id(i) for i in enrich_and_prune(todo)}
        for it in todo:
            entry = state.seen.get(it.listing.uid)
            if entry is not None and (not it.detailed or not it.listing.description):
                # the page failed to load or said nothing: try again later, but not forever
                entry["detail_tries"] = int(entry.get("detail_tries") or 0) + 1
            if it.detailed and id(it) in kept_backlog:
                _card(state, it, now_s)

    queue: List[Tuple[Item, Optional[int]]] = [(it, old) for it, old in drop_items] + [(it, None) for it in new_items]
    sent_msgs = 0
    overflow = 0
    for it, old in queue:
        if sent_msgs >= cap:
            overflow += 1
            if old is None:
                state.count_new_match(today, it.listing.city)
            continue
        if old is not None:
            text = fmt.format_price_drop(it.listing, it.result, old, cfg, label(it))
        else:
            text = fmt.format_new_match(it.listing, it.result, cfg, label(it))
        if sender.send(text, it.listing.url):
            sent_msgs += 1
            if old is None:
                state.count_new_match(today, it.listing.city)
            if old is None and it.listing.uid in state.seen:
                state.seen[it.listing.uid]["notified_at"] = now_s
        else:
            if old is None:
                unmark(it)  # retry next run
            else:
                state.matches.get(it.listing.uid, {})["price_drop_from"] = None
                if it.listing.uid in state.seen:
                    state.seen[it.listing.uid]["last_price"] = old
    if overflow:
        sender.send(fmt.format_overflow(overflow, dash))

    # 7. health
    threshold = int(cfg.get("health_alert_after_failures", 6))
    for name, src in active.items():
        fr = fetched[name]
        h = state.health.setdefault(name, {})
        n_listings = len(fr.listings)
        set_health(name, src, listings_last_run=n_listings)
        if fr.status in ("ok", "partial"):
            h["status"] = fr.status
            h["last_ok"] = now_s
            h["error"] = fr.error if fr.status == "partial" else None
            if fr.status == "ok":
                if h.get("alerted") and sender.send(fmt.format_recovery(src.label or name)):
                    h["alerted"] = False
                h["consecutive_failures"] = 0
        else:
            h["status"] = fr.status
            h["last_error"] = now_s
            h["error"] = fr.error
            h["consecutive_failures"] = int(h.get("consecutive_failures", 0)) + 1
            if h["consecutive_failures"] >= threshold and not h.get("alerted"):
                if sender.send(fmt.format_health_alert(src.label or name, fr.status,
                                                       h["consecutive_failures"], fr.error)):
                    h["alerted"] = True

    # 7b. very first run: tell the group which sites work from where this runs (GitHub's servers)
    if first_ever:
        rows = [{"name": n, "label": h.get("label"), "status": h.get("status"), "note": h.get("note"),
                 "error": h.get("error"), "count": h.get("listings_last_run")}
                for n, h in sorted(state.health.items())]
        sender.send(fmt.format_status_report(rows, dash))

    # 8. daily digest
    if (tcfg.get("digest", True) and int(tcfg.get("digest_hour", 9)) <= local_now.hour
            and state.meta.get("last_digest_date") != today):
        if first_ever:
            state.meta["last_digest_date"] = today
        else:
            yday = (local_now.date() - timedelta(days=1)).isoformat()
            per_city = state.meta.get("new_matches", {}).get(yday, {})
            rows = [{"name": n, "label": h.get("label"), "status": h.get("status")}
                    for n, h in sorted(state.health.items())]
            if sender.send(fmt.format_digest(per_city, rows, dash, yday)):
                state.meta["last_digest_date"] = today

    # 9. save
    state.meta["last_run"] = now_s
    if write:
        if state.save(now):
            write_dashboard(cfg, state, docs_dir, now)

    # 10. exit code: 0 even when every site failed. The Telegram health alert covers that; a failed
    # scheduled run would make GitHub email the user every 10 minutes. Exit 1 is for crashes only.
    results = [fetched[n] for n in active]
    if results and all(r.status in ("blocked", "error") for r in results):
        print("[run] WARNING: every enabled source failed this run", file=sys.stderr)
    return 0


def run(cfg: Dict[str, Any], dry_run: bool = False, only: Optional[List[str]] = None,
        data_dir: Optional[str] = None, docs_dir: Optional[str] = None) -> int:
    from .http import Http
    from .notify.telegram import ConsoleNotifier, make_notifier
    from .sources import all_sources
    try:
        state = State(data_dir, cfg)
        notifier = ConsoleNotifier() if dry_run else make_notifier(cfg)
        return run_once(cfg, state, all_sources(), notifier, Http(), only=only, write=not dry_run,
                        docs_dir=docs_dir)
    except Exception:
        traceback.print_exc()
        return 1
