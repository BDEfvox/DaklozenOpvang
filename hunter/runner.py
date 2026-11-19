"""One hunting run: fetch every source, find new matches, notify, update state and dashboard."""
from __future__ import annotations

import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .dashboard import write_dashboard
from .filters import MatchResult, evaluate
from .models import Listing
from .notify import format as fmt
from .notify.telegram import Notifier
from .redact import redact
from .sources.base import Source, SourceBlocked, SourceError, SourcePartial
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


def _describe(exc: Exception) -> str:
    if isinstance(exc, (SourceBlocked, SourceError)):
        return redact(str(exc))[:300]
    return redact("%s: %s" % (type(exc).__name__, exc))[:300]


def fetch_source(source: Source, cfg: Dict[str, Any], http: Any, only_city: Optional[str] = None) -> FetchResult:
    """Fetch all configured cities of one source. Never raises."""
    res = FetchResult()
    source.cfg = cfg  # for sites that filter on price themselves, see Source.max_rent
    cities_cfg = list(cfg.get("cities", []))
    errors: List[Tuple[str, Exception]] = []
    ok_cities = 0
    cities = ["*"]
    if source.per_city:
        cities = [c for c in source.cities if c in cities_cfg and (only_city is None or c == only_city)]
    for city in cities:
        error = None
        blocked = False
        partial_empty = False
        try:
            items = list(source.fetch(city, http) or [])
        except SourcePartial as exc:
            items = exc.listings
            error = _describe(exc)
            errors.append((city, exc))
            blocked = exc.blocked
            partial_empty = not items  # failed before it had anything: that is a failure, not half a success
        except Exception as exc:
            errors.append((city, exc))
            res.per_city[city] = (0, _describe(exc))
            if isinstance(exc, SourceBlocked):
                break  # do not keep knocking on a door that is closed
            continue

        items = [l for l in items if l.city in cities_cfg]
        if not source.per_city and only_city is not None:
            items = [l for l in items if l.city == only_city]
        res.listings.extend(items)
        res.per_city[city] = (len(items), error)
        if not (error and partial_empty):
            ok_cities += 1
        if blocked:
            break
    if errors and ok_cities:
        res.status = "partial"
        res.error = "; ".join("%s: %s" % (c, _describe(x)) for c, x in errors)[:300]
    elif errors:
        blocked = all(isinstance(x, SourceBlocked) or getattr(x, "blocked", False) for _, x in errors)
        res.status = "blocked" if blocked else "error"
        res.error = _describe(errors[0][1])
    if source.income_requirement:  # the landlord's standing rule, see Source.income_requirement
        for l in res.listings:
            l.extra["income_requirement"] = True
    return res


# --------------------------------------------------------------------------- helpers
def _norm_addr(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fingerprint(l: Listing) -> Optional[str]:
    a = _norm_addr(l.address)
    if len(a) < 5 or l.price_total is None:
        return None
    return "%s|%s|%d" % (a, l.city, l.price_total)


def _same_home(a: Listing, b: Dict[str, Any]) -> bool:
    """Two listings with the same fingerprint (address, city, rent): one home?
    With a house number, yes. A street name alone can be two homes on that street: then a clearly
    different size or bedroom count means two homes. Missing a home costs more than a double alert."""
    if re.search(r"\d", a.address or ""):
        return True
    area_a, area_b = a.area_m2, b.get("area_m2")
    if area_a and area_b and abs(area_a - area_b) > max(5, 0.1 * max(area_a, area_b)):
        return False
    bed_a, bed_b = a.bedrooms, b.get("bedrooms")
    if bed_a is not None and bed_b is not None and bed_a != bed_b:
        return False
    return True


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
    fp: Optional[str] = None            # fingerprint before any detail page was read this run
    baseline: bool = False              # found in a source's very first scan
    was_matched: bool = True            # price drops: did it already fit a search before this run?

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


def _private_text(l: Listing) -> bool:
    return bool(isinstance(l.extra, dict) and l.extra.get("private_text"))


def _public_dict(l: Listing) -> Dict[str, Any]:
    """The listing as it may be saved: the state files are committed to a public repository."""
    d = l.to_dict()
    if _private_text(l):  # text from the user's mailbox: fine for filtering, never for saving
        d["description"] = None
    return d


# Flags worth remembering from a detail page whose full text is not kept (it is cut to 2500 characters,
# and mailbox text is never saved).
_DETAIL_FLAGS = ("no_sharers", "max_persons", "no_students", "scam_signals", "no_registration",
                 "income_requirement", "sharers_ok")


def _card(state: State, it: Item, now_s: str) -> None:
    old = state.matches.get(it.listing.uid, {})
    flags = list(it.result.flags)
    # Flags the last detail-page read found. They live in that read's snapshot, so they go when the
    # page is read again or the snapshot is dropped; a flag no current text supports does not linger.
    details = (state.seen.get(it.listing.uid) or {}).get("details") or {}
    for f in details.get("flags") or []:
        if f not in flags and f.split(":")[0] in _DETAIL_FLAGS:
            flags.append(f)
    state.matches[it.listing.uid] = {
        "listing": _public_dict(it.listing),
        "first_seen": old.get("first_seen") or now_s,
        "last_seen": now_s,
        "matches": [m.to_dict() for m in it.result.matches],
        "flags": flags,
        "best_tier": it.result.best_tier,
        "price_drop_from": old.get("price_drop_from"),
    }


_DETAIL_FIELDS = ("bedrooms", "rooms", "area_m2", "property_type", "price_total", "price_includes_utilities",
                  "description", "address", "postcode", "available_from", "available_until")


def detail_snapshot(l: Listing, search_price: Optional[int], flags: Optional[List[str]] = None) -> Dict[str, Any]:
    """What a detail page told us, so later runs (which only see the search page) don't forget it.
    Only written after a real read of the page (see Source.has_detail_pages)."""
    d: Dict[str, Any] = {k: getattr(l, k) for k in _DETAIL_FIELDS if getattr(l, k) is not None}
    d["flags"] = sorted(f for f in (flags or []) if f.split(":")[0] in _DETAIL_FLAGS)
    if _private_text(l):
        d.pop("description", None)
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
    if not it.source.has_detail_pages:
        return False  # nothing to look at: the feed is all this source has
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
        if budget[0] > 0 and it.source.has_detail_pages:
            budget[0] -= 1
            try:
                it.search_price = it.listing.price_total
                it.listing.extra.pop("description_from_summary", None)  # restored from an older snapshot
                it.listing = it.source.fetch_details(it.listing, http) or it.listing
                it.result = evaluate(it.listing, cfg)
                it.detailed = True
                if it.listing.extra.get("description_from_summary"):
                    print("[detail] %s: description block not found (layout changed?), used the page's own "
                          "summary" % it.listing.uid, file=sys.stderr)
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
        if hasattr(src, "attach_link_cache"):  # email alerts: remember what tracking links led to
            cache = state.meta.get("email_links")
            if not isinstance(cache, dict):
                cache = state.meta["email_links"] = {}
            src.attach_link_cache(cache, now.strftime("%Y-%m-%d"))
        try:
            fetched[name] = fetch_source(src, cfg, http)
        except Exception as exc:  # fetch_source should never raise, but be safe
            fetched[name] = FetchResult(status="error", error=_describe(exc))

    def live_duplicate(prev: Dict[str, Any]) -> Optional[str]:
        """uid of the listing this one duplicates, while that listing still counts: known, fitting a
        search, and not itself a duplicate. None when it no longer does."""
        ref = prev.get("dup_of")
        target = state.seen.get(ref) if isinstance(ref, str) else None
        if target is None and isinstance(ref, str) and ":" not in ref and prev.get("fp"):
            # An older version stored the other site's name here in place of its listing: find that listing.
            for uid, e in state.seen.items():
                if (e.get("source") == ref and e.get("fp") == prev["fp"] and e.get("matched")
                        and not e.get("dup_of")):
                    return uid
            return None
        if target is not None and target.get("matched") and not target.get("dup_of"):
            return ref
        return None

    def known_duplicate(l: Listing, fp: str, name: str, skip: Any = ()) -> Optional[str]:
        """uid of the same home that an earlier run already showed from another site."""
        uid = state.find_duplicate(fp, name, now, skip=skip)
        if uid is None:
            return None
        card = (state.matches.get(uid) or {}).get("listing") or {}
        return uid if _same_home(l, card) else None

    # 3-6. evaluate
    baseline_pool: List[Item] = []
    baseline_sources: List[str] = []
    new_items: List[Item] = []
    drop_items: List[Tuple[Item, int]] = []
    backlog: List[Item] = []  # matches already on the dashboard whose detail page was never read
    run_fps: Dict[str, Item] = {}  # fingerprint -> the first new listing with it this run
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
            prev = state.seen.get(l.uid)
            had_snapshot = False
            if prev is not None and prev.get("details"):
                if src.has_detail_pages:
                    apply_detail_snapshot(l, prev["details"])
                    had_snapshot = True
                else:
                    # Written by an older version that "read" a detail page this source does not have.
                    # It only repeats the feed of that day, and would undo every later correction.
                    del prev["details"]
            res = evaluate(l, cfg)
            fp = fingerprint(l)
            it = Item(src, l, res, had_snapshot=had_snapshot, fp=fp, baseline=is_baseline)

            if prev is not None and prev.get("dup_of"):
                ref = live_duplicate(prev)
                if ref:
                    # same home already shown from another site: keep it out of alerts and the dashboard
                    prev["dup_of"] = ref
                    prev["last_seen"] = now_s
                    continue
                # The home it duplicated is gone or no longer fits: this listing stands on its own again.
                del prev["dup_of"]
                prev["matched"] = []

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
                        it.was_matched = was_matched
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
                first = run_fps.get(fp)
                if first is None:
                    dup_of = known_duplicate(l, fp, name)
                    run_fps[fp] = it
                elif first.source.name != name and _same_home(l, first.listing.to_dict()):
                    earlier = (state.seen.get(first.listing.uid) or {}).get("dup_of")
                    if earlier and (state.seen.get(earlier) or {}).get("source") != name:
                        dup_of = earlier  # both are the home an earlier run already showed
                    elif is_baseline or first.baseline:
                        dup_of = first.listing.uid
                    # Otherwise both are new this run: settle_twins decides after the detail pages are
                    # read, because a listing that turns out not to fit must not hide its twin.
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
                entry["details"] = detail_snapshot(it.listing, it.search_price, it.result.flags)
                entry["last_price"] = it.listing.price_total
                entry["matched"] = [m.search for m in it.result.matches]
                new_fp = fingerprint(it.listing)  # the detail page may give the full address
                if new_fp:
                    entry["fp"] = new_fp
        kept_ids = {id(i) for i in kept}
        for it in items:
            if id(it) not in kept_ids:
                state.matches.pop(it.listing.uid, None)
                if it.listing.uid in state.seen:
                    state.seen[it.listing.uid]["matched"] = []
        return kept

    def settle_twins(items: List[Item]) -> List[Item]:
        """The same home found on two sites: show the best listing, record the others as its duplicates.
        Runs after the detail pages were read, so only a listing that still fits can hide its twin, and
        the comparison uses the full address when the detail page gave one."""
        kept: Dict[str, Item] = {}
        out: List[Item] = []
        new_uids = {i.listing.uid for i in items}
        for it in items:
            uid = it.listing.uid
            entry = state.seen.get(uid)
            fp = fingerprint(it.listing)
            dup = None
            if fp and entry is not None:
                first = kept.get(fp)
                if first is not None:
                    if first.source.name != it.source.name and _same_home(it.listing, first.listing.to_dict()):
                        dup = first.listing.uid
                elif fp != it.fp:  # a fuller address: an earlier run may already have shown this home
                    dup = known_duplicate(it.listing, fp, it.source.name, skip=new_uids)
            if dup:
                entry["dup_of"] = dup
                state.matches.pop(uid, None)
                continue
            if fp:
                kept.setdefault(fp, it)
            out.append(it)
        return out

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
            else:
                unmark(it)  # not delivered: forget it, so the next run finds it as new and alerts
        sender.send(fmt.format_first_run_summary(total, baseline_sources, dash))

    # 5/6. new matches and price drops
    cap = int(tcfg.get("max_messages_per_run", 15))
    new_items.sort(key=lambda i: i.sort_key)
    new_items = enrich_and_prune(new_items)
    new_items.sort(key=lambda i: i.sort_key)
    new_items = settle_twins(new_items)
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
            # The detail page has the last word, also on how big the drop is: for a home that already
            # fitted, a verified drop below the threshold (search page 1200, real rent 1399) is no news.
            if (id(it) in kept_drops and price is not None and price < old
                    and (not it.was_matched or (old - price) / float(old) >= PRICE_DROP_MIN_PCT)):
                _card(state, it, now_s)
                state.matches[it.listing.uid]["price_drop_from"] = old
                verified_drops.append((it, old))
            elif it.listing.uid in state.matches:
                state.matches[it.listing.uid]["price_drop_from"] = None
                if id(it) in kept_drops and not it.was_matched:
                    new_items.append(it)  # fits now, but the rent did not really fall: a new match
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
            if entry is not None:
                # Every look counts, also one that loaded but left the question open (mailbox text is
                # never saved, so such a listing would otherwise be looked at again on every run).
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
                entry = state.seen.get(it.listing.uid)
                if entry is not None:
                    entry["last_price"] = old  # retry next run
                    if not it.was_matched:
                        # It did not fit before this drop. Put that back too, or the next run sees a
                        # known match with a small price change and never announces it.
                        entry["matched"] = []
                        state.matches.pop(it.listing.uid, None)
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
            # Partly working counts as working again: the failure streak ends, and a warning that was
            # sent is followed by one message that says which part still fails.
            still_failing = fr.error if fr.status == "partial" else None
            if h.get("alerted") and sender.send(fmt.format_recovery(src.label or name, still_failing)):
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
        sys.stderr.write(redact(traceback.format_exc()))  # the log of a public repo is public
        return 1
