"""Decides whether a listing matches a search, which tier, and which flags apply."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import tenancy
from .models import Listing
from .utils import bedrooms_from_text, rooms_from_dutch_text


@dataclass
class SearchMatch:
    search: str
    persons: int
    price_pp: Optional[int]
    tier: str  # "great" | "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {"search": self.search, "persons": self.persons,
                "price_pp": self.price_pp, "tier": self.tier}


@dataclass
class MatchResult:
    matches: List[SearchMatch] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    # flag name -> the sentence of the listing that set it (text from a third-party site: data, never markup)
    evidence: Dict[str, str] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    @property
    def best_tier(self) -> Optional[str]:
        if not self.matches:
            return None
        return "great" if any(m.tier == "great" for m in self.matches) else "ok"

    @property
    def best_price_pp(self) -> Optional[int]:
        vals = [m.price_pp for m in self.matches if m.price_pp is not None]
        return min(vals) if vals else None


def _phrase_span(text: str, phrases: List[str]) -> Optional[Tuple[int, int]]:
    # Whole words only: "geen inschrijving" must not match "geen inschrijvingskosten".
    best: Optional[Tuple[int, int]] = None
    for p in phrases:
        if not p:
            continue
        m = re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", text)
        if m and (best is None or m.start() < best[0]):
            best = m.span()
    return best


def _contains_any(text: str, phrases: List[str]) -> bool:
    return _phrase_span(text, phrases) is not None


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


_COUPLE = r"(?:couples?|stel(?:len|letje)?|koppels?|tweetal|two|twee|2)"
_OR = r"(?:or|of|and|en|und|oder|&|/)"
# right after one-person wording: "... or a (working) couple", "... of 2 personen"
_COUPLE_AFTER = re.compile(r"\W{0,3}" + _OR + r"\W{1,3}(?:(?:a|an|een)\W+)?(?:\w+\W+)?" + _COUPLE + r"\b")
# right before it: "a couple or ", "stel of een "
_COUPLE_BEFORE = re.compile(r"\b" + _COUPLE + r"\W{1,3}" + _OR + r"\W{1,3}(?:\w+\W+){0,2}$")

_FAMILY_WORD = re.compile(r"\b(?:gezin(?:nen|netje)?|famil(?:y|ies|ie))\b")
_COUPLE_WORD = re.compile(r"\b(?:couples?|stel(?:len|letje)?|koppels?)\b")
_NOT_FOR = re.compile(r"\b(?:geen|niet|not|no|ongeschikt|unsuitable)\b[^,;]{0,25}$")


def _listed_with(text: str, raw: str, start: int, end: int) -> Optional[str]:
    """Who else the sentence around one-person wording names: "family", "couple" or None.
    "single-family home" names no family, and "niet geschikt voor gezinnen" names none either."""
    left, right = tenancy.clause(raw, start, end)
    found = None
    for rx, kind in ((_FAMILY_WORD, "family"), (_COUPLE_WORD, "couple")):
        for m in rx.finditer(text, left, right):
            if start <= m.start() < end or _NOT_FOR.search(text[left:m.start()]):
                continue
            if kind == "family" and text[max(left, m.start() - 7):m.start()] in ("single-", "single "):
                continue
            if kind == "family":
                return "family"
            found = "couple"
    return found


# A listing that is not a home at all, said in its first words: "Opslagruimte van circa 48 m2 te huur".
_NOT_A_HOME = re.compile(
    r"^\W*(?:te\s+huur\W+)?(?:(?:een|ruime|nette|afgesloten|inpandige|overdekte|droge|grote|kleine|priv[eé])\s+){0,3}"
    r"(?:opslagruimte|opslagbox|opslagunit|garagebox|parkeerplaats|parkeerplek|parkeerplaatsen|stallingsplaats|"
    r"bedrijfsruimte|bedrijfspand|kantoorruimte|kantoorpand|winkelruimte|winkelpand|praktijkruimte|"
    r"storage\s+(?:unit|space|room)|parking\s+(?:space|spot|place)|office\s+space)\b", re.I)


_HOME_WORD = re.compile(r"slaapkamer|woonkamer|keuken|badkamer|appartement|woning|woonhuis|bovenwoning|benedenwoning|studio|"
                        r"maisonnette|penthouse|\bkamers?\b|bedroom|living\s?room|kitchen|bathroom|apartment|\bflat\b|"
                        r"\bhouse\b|\bhome\b", re.I)


_FOR_RENT = re.compile(r"[^.!?;,]{0,30}?\b(?:te\s+huur|for\s+rent|to\s+let)\b", re.I)
_FOR_RENT_FIRST = re.compile(r"^\W*te\s+huur\b", re.I)


def infer_type(listing: Listing) -> None:
    """A site that gives no type sometimes lists storage rooms and parking places between its homes.
    Such a listing says so in its first words and names no part of a home anywhere. A home whose text happens
    to open with its parking place ("Parkeerplaats voor de deur en een ruime tuin. 3 slaapkamers.") stays."""
    if listing.property_type or listing.bedrooms or listing.rooms:
        return
    title, text = listing.title or "", listing.description or ""
    hits = [m for m in (_NOT_A_HOME.search(title), _NOT_A_HOME.search(text)) if m]
    # "Parkeerplaats te huur ...", "Te huur: garagebox": the thing for rent is named, whatever else the text holds
    for_rent = any(_FOR_RENT.match(m.string, m.end()) or _FOR_RENT_FIRST.match(m.group(0)) for m in hits)
    if hits and (for_rent or not _HOME_WORD.search(title + " " + text)):
        listing.property_type = "other"
        if isinstance(listing.extra, dict):
            listing.extra["type_from_text"] = True


# The listed rent is per person (or per room), not for the whole home. Two kinds of proof count:
# a sentence that says so about the price itself ("De prijs die vermeld staat is per persoon"), or the
# listed amount itself followed by "per persoon" ("€978,- per maand per persoon" on a listing of €978).
# Any other amount per person (service costs, a deposit) says nothing about the rent.
_PER = r"(?:p\.?p\.?(?:p\.?m\.?)?|per\s+pers(?:oon|on)|per\s+kamer|per\s+room|per\s+slaapkamer|per\s+bedroom|each)\b"
_PP_STATED = re.compile(
    r"(?:\b(?:huur|kamer)?prijs|\bhuur|\bprice|\brent)\b[^.!?;:,()€\d]{0,50}?\b(?:is|are|betreft|geldt|staat|staan)\b"
    r"[^.!?;:,()€\d]{0,30}?\b(?:per\s+pers(?:oon|on)|per\s+kamer|per\s+room|per\s+slaapkamer|per\s+bedroom)\b")
# "... en niet per persoon", "de borg is per persoon", "g/w/e wordt per persoon verrekend": not about the rent
_PP_NOT = re.compile(r"\b(?:niet|geen|not|no)\b|n['’]t\b|\b(?:borg|waarborg\w*|deposit|servicekosten|service\s+costs?|"
                     r"voorschot|g/w/e|gwe|gwl|energie\w*|utilities|nutsvoorzieningen|verrekend|kosten|fee)\b")
_PP_AMOUNT = re.compile(
    r"(?:€|\beur(?:o)?\b)\s?(\d{1,2}[.,]\d{3}|\d{3,4})(?:[.,]\d{2}|[.,]-)?\s*(?:,-\s*)?"
    r"(?:(?:p\.?/?m\.?|per\s+maand|per\s+month|/\s?m(?:aa)?nd|/\s?month|a\s+month)\s*)?" + _PER)
_PP_AMOUNT_AFTER = re.compile(
    r"\b(\d{1,2}[.,]\d{3}|\d{3,4})\s?(?:€|euro?\b)\s*(?:(?:per\s+maand|per\s+month|/\s?month)\s*)?" + _PER)


_NOT_RENT = re.compile(r"(?:borg|waarborg|deposit|servicekosten|service\s+costs?|bijkomende\s+kosten|voorschot|g/w/e|gwl|"
                       r"energie|utilities|nutsvoorzieningen|fee|kosten)\W{0,3}(?:\w+\W+){0,3}$")


def _per_person_span(text: str, price: Optional[int]) -> Optional[Tuple[int, int]]:
    for m in _PP_STATED.finditer(text):
        if not _PP_NOT.search(m.group(0)):
            return m.span()
    if price is None:
        return None
    for rx in (_PP_AMOUNT, _PP_AMOUNT_AFTER):
        for m in rx.finditer(text):
            try:
                amount = int(re.sub(r"[.,]", "", m.group(1)))
            except ValueError:
                continue
            # A deposit or service costs per person can equal one month's rent by coincidence.
            if amount == price and not _NOT_RENT.search(text[max(0, m.start() - 40):m.start()]):
                return m.span()
    return None


# Flags found by a phrase list in config.json ("flags"), in the order they are reported.
_PHRASE_FLAGS = ("no_sharers", "no_students", "sharers_ok", "no_registration", "income_requirement",
                 "scam_signals", "rooms_only")
# Flags a site states in its own fields (a source sets extra["site_<flag>"], see DESIGN.md "Who may rent").
_SITE_FLAGS = ("students_only", "working_only", "no_students", "students_ok", "no_sharers", "sharers_ok", "housemates",
               "women_only", "men_only")
# Where the tenancy flags go in the list: after the phrase flags, before max_persons.
_TENANCY_ORDER = ("students_only", "students_ok", "working_only", "women_only", "men_only", "housemates")

# A cost that must be paid every month on top of the rent: a parking place that comes with the home.
_MANDATORY_COSTS = re.compile(
    r"\bverplichte?\s+(?:afname|huur)\s+(?:van\s+)?(?:een\s+|de\s+)?(?:\w+\s+)?parkeer(?:plaats|plek|gelegenheid)"
    r"[^.!?;]{0,60}?(?:€|\beuro?\b)\s?(\d{2,3})\b"
    r"|\bparkeer(?:plaats|plek)\b[^.!?;]{0,40}?\b(?:is|wordt)\s+verplicht\b[^.!?;]{0,60}?(?:€|\beuro?\b)\s?(\d{2,3})\b"
    r"|\bmandatory\s+parking\b[^.!?;]{0,60}?(?:€|\beuro?\b)\s?(\d{2,3})\b")
_OPTIONAL = re.compile(r"\b(?:optioneel|optional|niet\s+verplicht|not\s+mandatory|geen\s+verplicht\w*|indien\s+gewenst|desgewenst)\b")
# The home is let for a short time only, said in words (dates and extra["max_stay_months"] are checked too).
_SHORT_STAY = re.compile(
    r"\b(?:alleen|uitsluitend|only)\s+(?:voor\s+|for\s+)?short[\s-]?stay\b"
    # "maximale huurperiode van 6 maanden", "maximum stay of 6 months": the period must be named, because
    # "maximaal 2 maanden waarborgsom" (a deposit) and "maximaal 2 maanden huur" are worded the same way
    r"|\bmax(?:imaal|imale|imum|\.)?\s+(?:huurperiode|huurtermijn|verblijf|verblijfsduur|(?:rental\s+)?(?:period|stay))\s+"
    r"(?:van\s+|of\s+|is\s+)?(\d{1,2})\s+(?:maanden|months)\b"
    r"|\b(?:huurperiode|huurtermijn|verblijfsduur|rental\s+period)\s*(?:van|of|is|:)?\s*max(?:imaal|imum|\.)?\s+"
    r"(\d{1,2})\s+(?:maanden|months)\b"
    # "te huur voor maximaal 6 maanden", "available for a maximum of 6 months"
    r"|\b(?:te\s+huur(?:\s+aanbieden)?|verhuurd?|beschikbaar|huren|available|for\s+rent)\s+(?:voor\s+|for\s+)"
    r"(?:een\s+periode\s+van\s+|a\s+period\s+of\s+)?(?:max(?:imaal|imum|\.)?|a\s+maximum\s+of|up\s+to)\s+"
    r"(\d{1,2})\s+(?:maanden|months)\b")


def detect(listing: Listing, cfg: Dict[str, Any]) -> Tuple[List[str], Dict[str, str]]:
    """(flags, evidence). Evidence maps a flag's name to the sentence that set it, or to the site's own
    fields (extra["site_facts"]) when the flag comes from those."""
    raw = (listing.title or "") + " \n " + (listing.description or "")
    text = raw.lower()
    if len(text) != len(raw):
        raw = text
    fl = cfg.get("flags", {})
    extra = listing.extra if isinstance(listing.extra, dict) else {}
    site_facts = extra.get("site_facts") if isinstance(extra.get("site_facts"), str) else ""
    spans: Dict[str, Tuple[int, int]] = {}
    for key in _PHRASE_FLAGS:
        span = _phrase_span(text, fl.get(key, []))
        if span:
            spans[key] = span
    ev: Dict[str, str] = {k: tenancy.evidence(raw, *v) for k, v in spans.items()}

    # who may rent: students, working people, age, sharers, housemates (rules in tenancy.py)
    who, who_ev = tenancy.read(raw, cfg.get("tenant_rules"), known=spans)
    names: Dict[str, Optional[str]] = {k: None for k in spans}       # name -> value after the colon
    for f in who:
        k, _, v = f.partition(":")
        names[k] = v or None
        ev[k] = who_ev.get(k, ev.get(k, ""))
    for k in list(names):                                             # the phrase flags read() dropped
        if k in tenancy.RULES and k not in {f.partition(":")[0] for f in who}:
            del names[k]
            ev.pop(k, None)
    # the site's own fields say it too, or say it alone
    for k in _SITE_FLAGS:
        if extra.get("site_" + k) is True and k not in names:
            names[k] = None
            ev[k] = site_facts
    for k in ("min_age", "max_age"):
        v = extra.get("site_" + k)
        if type(v) is int and 16 <= v <= 99:                          # the site's field beats the free text
            names[k] = str(v)
            ev[k] = site_facts
    lo, hi = names.get("min_age"), names.get("max_age")
    if lo and hi and int(lo) > int(hi):
        for k in ("min_age", "max_age"):
            names.pop(k, None)
            ev.pop(k, None)
    before = set(names)
    tenancy.settle(names)
    for k in before - set(names):
        ev.pop(k, None)

    out: List[str] = [k for k in _PHRASE_FLAGS if k in names]
    out += [k for k in _TENANCY_ORDER if k in names]
    out += ["%s:%s" % (k, names[k]) for k in ("min_age", "max_age") if names.get(k)]

    if "no_registration" not in out and (
            extra.get("registration_allowed") is False or extra.get("registration_possible") is False):
        out.insert(len([k for k in _PHRASE_FLAGS[:3] if k in out]), "no_registration")
        ev["no_registration"] = site_facts
    if "income_requirement" not in out:
        hit = None
        for pat in cfg.get("income_patterns", []):
            hit = re.search(pat, text)
            if hit:
                break
        if extra.get("income_requirement") is True or hit:               # True: the landlord's standing rule
            out.insert(len([k for k in _PHRASE_FLAGS[:4] if k in out]), "income_requirement")
            if hit:
                ev["income_requirement"] = tenancy.evidence(raw, *hit.span())
    # max persons: every number the text gives, the lowest wins
    nums: List[Tuple[int, Optional[Tuple[int, int]]]] = []
    for pat in cfg.get("max_persons_patterns", []):
        for m in re.finditer(pat, text):
            try:
                nums.append((int(m.group(1)), m.span()))
            except (IndexError, ValueError):
                pass
    couple_spans = [m.span() for pat in cfg.get("couple_patterns", []) for m in re.finditer(pat, text)]
    if couple_spans:
        nums.append((2, couple_spans[0]))
    for pat in cfg.get("single_patterns", []):
        for m in re.finditer(pat, text):
            # A single-person option within a couple allowance does not lower that allowance.
            if any(start < m.end() and m.start() < end for start, end in couple_spans):
                continue
            # "alleenstaande of werkend stel", "a couple or a single person": one or two people may live there
            offered_with_couple = (_COUPLE_AFTER.match(text, m.end())
                                   or _COUPLE_BEFORE.search(text[max(0, m.start() - 40):m.start()]))
            # One-person wording inside a list of who the home suits ("alleenstaanden, stellen als kleine
            # gezinnen") limits nothing when families are in the list, and means two when couples are.
            listed = _listed_with(text, raw, m.start(), m.end())
            if listed == "family":
                continue
            nums.append((2 if offered_with_couple or listed == "couple" else 1, m.span()))
    mp = extra.get("max_persons")
    if isinstance(mp, int) and not isinstance(mp, bool) and mp > 0:
        nums.append((mp, None))  # structured value from the site (e.g. Kamernet "suitable for N persons")
    if nums:
        n, span = min(nums, key=lambda x: (x[0], x[1] is None))   # of equal numbers, the one with a sentence
        out.append("max_persons:%d" % n)
        ev["max_persons"] = tenancy.evidence(raw, *span) if span else site_facts
    # short term
    a, b = _parse_date(listing.available_from), _parse_date(listing.available_until)
    max_stay_months = extra.get("max_stay_months")
    stay = None
    for m in _SHORT_STAY.finditer(text):
        months = next((g for g in m.groups() if g), None)
        if tenancy.denied_before(raw, text, m.start()):  # "niet alleen voor short stay"
            continue
        if months is None or 0 < int(months) < 12:      # "maximaal 24 maanden" is not short
            stay = m.span()
            break
    if ((a and b and (b - a).days < 365)
            or (type(max_stay_months) is int and 0 < max_stay_months < 12) or stay):
        out.append("short_term")
        if stay:
            ev["short_term"] = tenancy.evidence(raw, *stay)
        elif type(max_stay_months) is int and 0 < max_stay_months < 12:
            ev["short_term"] = site_facts
    # a monthly cost that cannot be declined (the site's own field, or the listing's words)
    must = extra.get("mandatory_costs")
    must_note = extra.get("mandatory_costs_note") if isinstance(extra.get("mandatory_costs_note"), str) else ""
    if not (type(must) is int and must > 0):
        must, must_note = None, ""
        for m in _MANDATORY_COSTS.finditer(text):
            # "geen verplichte afname van een parkeerplaats (optioneel € 125)" obliges nothing
            if tenancy.denied_before(raw, text, m.start()) or _OPTIONAL.search(m.group(0)):
                continue
            must = int(next(g for g in m.groups() if g))
            must_note = tenancy.evidence(raw, *m.span())
            break
    if type(must) is int and must > 0:
        out.append("mandatory_costs:%d" % must)
        ev["mandatory_costs"] = must_note or site_facts
    if listing.price_includes_utilities:
        out.append("utilities_included")
    # The site prices per month of the year (HousingAnywhere): the listed rent is the lowest, this is the highest.
    top = extra.get("price_max")
    if type(top) is int and type(listing.price_total) is int and top > listing.price_total:
        out.append("price_varies:%d" % top)
    pp = _per_person_span(text, listing.price_total)
    if pp or extra.get("price_per_person") is True:
        out.append("price_per_person")
        ev["price_per_person"] = tenancy.evidence(raw, *pp) if pp else site_facts
    if extra.get("private_text"):
        # The text came from the user's mailbox (an alert email), which can hold their name and address.
        # It may decide flags, but no sentence of it is quoted anywhere: not in Telegram, not on the dashboard.
        return out, {}
    return out, {k: v for k, v in ev.items() if v}


def detect_flags(listing: Listing, cfg: Dict[str, Any]) -> List[str]:
    return detect(listing, cfg)[0]


def infer_bedrooms(listing: Listing) -> None:
    """If the site gave no bedroom count, take the one the title or description states."""
    if listing.rooms is None:
        # "tweekamerappartement": the Dutch room count, living room included (see utils.rooms_from_dutch_text)
        rooms = rooms_from_dutch_text((listing.title or "") + " \n " + (listing.description or ""))
        if rooms is not None:
            listing.rooms = rooms
            if isinstance(listing.extra, dict):
                listing.extra["rooms_from_text"] = True
    if listing.bedrooms is not None:
        return
    n = bedrooms_from_text((listing.title or "") + " \n " + (listing.description or ""))
    if n is None:
        return
    if listing.rooms is not None and n > listing.rooms:
        return  # more bedrooms than rooms: we misread the text
    listing.bedrooms = n


def _effective_bedrooms(listing: Listing) -> Tuple[Optional[int], bool]:
    """(bedrooms, guessed). Guessed = only the room count is known, so we assume rooms - 1, which is
    the most it can be: a "4 kamers" home often has 2 bedrooms plus a study or a second living room."""
    if listing.bedrooms is not None:
        # A site field can say "2 bedrooms" for what the text calls a "tweekamerappartement" (two rooms: a living
        # room and ONE bedroom). The Dutch wording has one meaning, so it sets the most the count can be.
        text_rooms = isinstance(listing.extra, dict) and listing.extra.get("rooms_from_text") is True
        if text_rooms and listing.rooms is not None and listing.bedrooms >= listing.rooms >= 2:
            return listing.rooms - 1, True
        return listing.bedrooms, False
    if listing.rooms is not None:
        return max(listing.rooms - 1, 0), True
    return None, False


def _excluded(flags: List[str], household: Any) -> bool:
    """Does the listing itself say this household may not rent it? `household` is config.json's
    {"age": 24, "occupation": "students" | "working"}; without it (the default) nothing is excluded.
    Only what the listing states counts: a listing that says nothing is never excluded."""
    if not isinstance(household, dict):
        return False
    names = {}
    for f in flags:
        k, _, v = f.partition(":")
        names[k] = v
    occupation = household.get("occupation")
    if occupation == "students" and ("no_students" in names or "working_only" in names):
        return True
    if occupation == "working" and "students_only" in names:
        return True
    age = household.get("age")
    if type(age) is int:
        if names.get("min_age", "").isdigit() and int(names["min_age"]) > age:
            return True
        if names.get("max_age", "").isdigit() and int(names["max_age"]) < age:
            return True
    return False


def _with_remembered(flags: List[str], evidence: Dict[str, str], old_flags: Any,
                     old_evidence: Any) -> Tuple[List[str], Dict[str, str]]:
    """Add the flags an earlier read of the full text found and the text at hand no longer shows.
    What the text at hand says about the same thing wins; contradictions are settled as in detect()."""
    names: Dict[str, str] = {f.partition(":")[0]: f for f in flags}
    added = []
    for f in old_flags or []:
        name = str(f).partition(":")[0]
        if name and name not in names:
            names[name] = str(f)
            added.append(name)
    if not added:
        return flags, evidence
    tenancy.settle(names)
    out = [f for f in flags if f.partition(":")[0] in names] + [names[n] for n in added if n in names]
    ev = {k: v for k, v in evidence.items() if k in names}
    for name in added:
        quote = old_evidence.get(name) if isinstance(old_evidence, dict) else None
        if name in names and isinstance(quote, str) and quote:
            ev[name] = quote
    return out, ev


def evaluate(listing: Listing, cfg: Dict[str, Any],
             remembered: Optional[Tuple[Any, Any]] = None) -> MatchResult:
    """`remembered`: (flags, evidence) that an earlier read of the listing's full text found. The text that
    is kept is cut, so without them a condition from the end of a long text ("geen woningdelers") would be
    forgotten by the next run, and the match it ruled out would come back."""
    infer_bedrooms(listing)   # first: a text that states its bedrooms is about a home
    infer_type(listing)
    flags, evidence = detect(listing, cfg)
    if remembered:
        flags, evidence = _with_remembered(flags, evidence, remembered[0], remembered[1])
        if isinstance(listing.extra, dict) and listing.extra.get("private_text"):
            evidence = {}
    res = MatchResult(flags=flags, evidence=evidence)
    if listing.city not in cfg.get("cities", []):
        return res
    if (listing.property_type or "") in cfg.get("exclude_types", []):
        return res
    if "rooms_only" in flags:  # "alleen nog losse kamers": you can't rent the whole home together
        return res

    if _excluded(flags, cfg.get("household")):  # the home is not for this household: no match, no alert
        return res

    price = listing.price_total
    searches = list(cfg.get("searches", []))
    if not searches:
        return res
    prefiltered = isinstance(listing.extra, dict) and bool(listing.extra.get("site_prefiltered"))
    all_unknown = (listing.bedrooms is None and listing.rooms is None and listing.area_m2 is None)

    extra_flags: List[str] = []
    if price is None:
        if not prefiltered:
            return res
        searches = [min(searches, key=lambda s: s["max_total"])]
        extra_flags.append("price_unknown")

    eff_bed, bed_guessed = _effective_bedrooms(listing)
    # The listing states its rent per person: the group pays that amount once for each of them.
    per_person = "price_per_person" in flags
    must_pay = 0  # what must be paid every month on top of the rent (a compulsory parking place)
    for f in flags:
        if f.startswith("mandatory_costs:"):
            must_pay = int(f.split(":", 1)[1])
    susp = cfg.get("suspicious_total_below")
    if price is not None and susp and price < susp and (eff_bed is None or eff_bed >= 2) and not per_person:
        flags.append("suspicious_price")

    max_persons: Optional[int] = None
    for f in flags:
        if f.startswith("max_persons:"):
            max_persons = int(f.split(":", 1)[1])
    no_sharers = "no_sharers" in flags

    used_unknown = False
    used_guess = False
    for s in searches:
        persons = int(s["persons"])
        total = price * persons if (price is not None and per_person) else price
        if total is not None:
            total += must_pay
        if total is not None and total > s["max_total"]:
            continue
        if eff_bed is not None:
            if eff_bed < s["min_bedrooms"]:
                continue
        elif all_unknown and prefiltered:
            used_unknown = True
        elif listing.area_m2 is not None:
            if listing.area_m2 < s.get("min_area_if_unknown", 0):
                continue
            used_unknown = True
        else:
            continue
        if persons >= 3 and no_sharers:
            continue
        if max_persons is not None and max_persons < persons:
            continue
        used_guess = used_guess or (bed_guessed and eff_bed is not None)
        pp = int(round(total / persons)) if total is not None else None
        tier = "great" if (total is not None and total <= s["great_total"]) else "ok"
        res.matches.append(SearchMatch(s["name"], persons, pp, tier))

    if res.matches:
        if used_unknown:
            extra_flags.append("bedrooms_unknown")
        if used_guess:
            extra_flags.append("bedrooms_unconfirmed")
        for f in extra_flags:
            if f not in flags:
                flags.append(f)
    return res
