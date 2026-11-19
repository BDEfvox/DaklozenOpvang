"""Decides whether a listing matches a search, which tier, and which flags apply."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import tenancy
from . import occupancy
from .models import Listing
from .utils import bedrooms_from_text, rooms_from_dutch_text


@dataclass
class SearchMatch:
    search: str
    persons: int
    price_pp: Optional[int]   # the quoted rent each person pays a month; None when the listing does not settle it
    tier: str  # "great" | "ok"
    extra_pp: int = 0         # monthly costs each person must pay on top of that rent (service costs, a compulsory extra)

    def to_dict(self) -> Dict[str, Any]:
        return {"search": self.search, "persons": self.persons,
                "price_pp": self.price_pp, "extra_pp": self.extra_pp, "tier": self.tier}


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

# who widens the list beyond one or two people: a family, or people who share the home
_FAMILY_WORD = re.compile(r"\b(?:gezin(?:nen|netje)?|famil(?:y|ies|ie)|woningdelers|delers|(?:house|home|flat)[- ]?sharers|sharers)\b")
_COUPLE_WORD = re.compile(r"\b(?:couples?|stel(?:len|letje)?|koppels?)\b")
_NOT_FOR = re.compile(r"\b(?:geen|niet|not|no|ongeschikt|unsuitable)\b[^,;]{0,25}$")
# A denial up to three words before one-person wording: "niet geschikt voor 1 persoon", "niet voor een alleenstaande".
# A new part of the sentence ("niet roken en alleen voor 1 persoon") starts over.
_DENIED_NEAR = re.compile(r"(?:\b(?:geen|niet|not|no|ongeschikt|unsuitable)|n['’]t)"
                          r"(?:\s+(?!(?:en|and|maar|but|of|or|wel)\b)[\w-]+){0,3}\s+$")
# "... een derde kamer voor een persoon", "the second bedroom fits one person": about one room of the home
_ROOM_BEFORE = re.compile(r"\b(?:slaap)?kamers?\b(?:\s+[\w-]+){0,3}\s+$|\b(?:bed)?rooms?\b(?:\s+[\w-]+){0,3}\s+$")
# right after "2 personen" / "een stel": "... of een gezin", "... of 3 woningdelers", "... or friends"
_OR_MORE = re.compile(r"\s*\(?\s*(?:,|/|&|\bof\b|\bor\b|\ben\b|\band\b)\s*"
                      r"(?:(?!(?:geen|niet|no|not|zonder|without)\b)[\w-]+\s+){0,3}?"    # "max. 2 personen, geen gezin" stays a limit
                      r"(?:gezin(?:nen)?|famil(?:y|ies|ie)|woningdelers|delers|sharers|vrienden|friends|woongroep)\b")
# The home is offered with a couple in mind. "een stel maanden" and "a couple of weeks" are no couple.
_SUITS_COUPLE = re.compile(
    r"\b(?:geschikt|ideaal|perfect|bedoeld|uitermate\s+geschikt|zeer\s+geschikt)\s+voor\s+(?:een\s+)?"
    r"(?:(?:jong|werkend|net|rustig)\s+)?(?:stel(?:len|letje)?|koppels?|tweeverdieners)\b(?!\s+(?:maanden|weken|dagen|jaren|jaar))"
    r"|\b(?:suitable|ideal|perfect|great|meant)\s+for\s+(?:a\s+|an\s+)?(?:(?:young|working|professional|quiet)\s+)?"
    r"couples?\b(?!\s+of\b)")
_OR_NUMBER = re.compile(r"\s*\(?\s*(?:/|\bof\b|\bor\b)\s*(?:max(?:imaal|imum|\.)?\s*)?(\d)\s+(?:werkende\s+|working\s+)?"
                        r"(?:personen|persons|people|woningdelers|delers|sharers|bewoners|huurders|studenten|students|"
                        r"volwassenen|adults|tenants|occupants)\b")


def _listed_with(text: str, raw: str, start: int, end: int) -> Optional[str]:
    """Who else the sentence around one-person wording names: "family" (or sharers: "een alleenstaande, een stel
    of woningdelers"), "couple" or None.
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


# "De prijs voor de eindschoonmaak is per kamer": the price of something else than the home
_PP_OF_OTHER = re.compile(r"\b(?:prijs|price|huur|rent)\s+(?:voor|van|for|of)\s+(?:de\s+|het\s+|een\s+|the\s+|a\s+|an\s+)?"
                          r"(?!(?:woning|appartement|huis|kamer|kamers|studio|woonruimte|huur|apartment|house|home|"
                          r"property|flat|room|rooms|accommodation|rent|deze|dit|this)\b)\w")


def _per_person_span(text: str, price: Optional[int], raw: Optional[str] = None) -> Optional[Tuple[int, int]]:
    raw = raw if raw is not None and len(raw) == len(text) else text
    for m in _PP_STATED.finditer(text):
        if "\n" in m.group(0) or _PP_NOT.search(m.group(0)) or _PP_OF_OTHER.match(text, m.start()):
            continue
        if tenancy.denied_before(raw, text, m.start()):      # "niet de huurprijs is per persoon, maar ..."
            continue
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


# The rent depends on how many people rent: "Huurprijs voor 3 personen € 2395,- ... Huurprijs voor 2 personen € 1995,-".
_GROUP_WORDS = {"twee": 2, "drie": 3, "vier": 4, "two": 2, "three": 3, "four": 4}
_GROUP_RENT = re.compile(
    r"\b(?:huur(?:prijs)?|prijs|rent(?:al\s+price)?|price)\s+(?:voor|bij|met|for|with)\s+([2-4]|twee|drie|vier|two|three|four)\s+"
    r"(?:pers(?:onen|oons|ons)?|pesonen|people|bewoners|huurders|tenants|occupants)\b\s*(?:is|bedraagt|:|=)?\s*"
    r"(?:€|\beur(?:o)?\b)\s?(\d{1,2}[.,]\d{3}|\d{3,4})(?![\d.,]*\d)")
# ... unless the amount is per person, per week or a deposit: "prijs voor 2 personen € 950 per persoon"
_GROUP_RENT_NOT = re.compile(
    r"\s*(?:,-|[.,]-|[.,]\d{2})?\s*(?:(?:p\.?/?m\.?|per\s+maand|per\s+month|/\s?m(?:aa)?nd|/\s?month|a\s+month)\s*)?"
    r"(?:p\.?\s?p\.?|per\s+pers|per\s+kamer|per\s+room|per\s+slaapkamer|per\s+bedroom|each\b|borg|waarborg|deposit|"
    r"per\s+week|p\.?/?w\b|/\s?week|a\s+week|per\s+nacht|per\s+night|per\s+dag|per\s+day)")


def _group_rents(text: str, raw: str, price: Optional[int]) -> Dict[int, Tuple[int, Tuple[int, int]]]:
    """persons -> (monthly rent the text states for that many people, span). Only amounts that differ from the listed
    rent and are in its range: the listed rent already stands for the group it was listed for."""
    out: Dict[int, Tuple[int, Tuple[int, int]]] = {}
    if type(price) is not int or price <= 0:
        return out
    seen: Dict[int, set] = {}
    for m in _GROUP_RENT.finditer(text):
        if "\n" in m.group(0) or tenancy.denied_before(raw, text, m.start()) or _GROUP_RENT_NOT.match(text, m.end()):
            continue
        if _NOT_RENT.search(text[max(0, m.start() - 40):m.start()]):     # "Borg: prijs voor 3 personen ..."
            continue
        n = int(m.group(1)) if m.group(1).isdigit() else _GROUP_WORDS[m.group(1)]
        try:
            amount = int(re.sub(r"[.,]", "", m.group(2)))
        except ValueError:
            continue
        seen.setdefault(n, set()).add(amount)
        if amount != price and price / 2 <= amount <= price * 2:
            out.setdefault(n, (amount, m.span()))
    # two different amounts for the same number of people: the text does not decide
    return {n: v for n, v in out.items() if len(seen[n]) == 1}


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
    r"(\d{1,2})\s+(?:maanden|months)\b"
    # "available for a temporary period of 2 months", "beschikbaar voor een tijdelijke periode van 3 maanden"
    r"|\b(?:te\s+huur|beschikbaar|available|for\s+rent)\s+(?:voor\s+|for\s+)(?:een\s+|a\s+)"
    r"(?:tijdelijke\s+periode\s+van|temporary\s+period\s+of)\s+(\d{1,2})\s+(?:maanden|months)\b"
    # "available for 1 to 6 months", "te huur voor 3 tot 6 maanden": the longest stay is the second number
    r"|\b(?:te\s+huur|beschikbaar|available|for\s+rent)\s+(?:voor\s+|for\s+)\d{1,2}\s*(?:tot|to|t/m|-|–)\s*"
    r"(\d{1,2})\s+(?:maanden|months)\b")

# "Beschikbaar vanaf 16 december t/m 28 februari", "available from 1 January until 31 March 2027": a stay between
# two dates the text names. Only next to a word about availability, because viewing days are written the same way.
_MONTHS = {"januari": 1, "january": 1, "jan": 1, "februari": 2, "february": 2, "feb": 2, "maart": 3, "march": 3, "mrt": 3,
           "mar": 3, "april": 4, "apr": 4, "mei": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7, "july": 7,
           "jul": 7, "augustus": 8, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10,
           "october": 10, "okt": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_MONTH = r"(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY_MONTH = r"(\d{1,2})(?:st|nd|rd|th|e)?\s+(?:of\s+)?" + _MONTH + r"(?:\s+(20\d{2}))?"
_TEXT_PERIOD = re.compile(
    r"\b(?:beschikbaar|te\s+huur|verhuurd?|huurperiode|onderhuur|available|for\s+rent|rental\s+period|sublet)\b"
    r"[^.!?;\n]{0,25}?\b(?:van(?:af)?|from|per|:)?\s*" + _DAY_MONTH
    + r"\s*(?:t/m|tot\s+en\s+met|tot|until|till|to|-|–)\s*" + _DAY_MONTH + r"\b")


def _text_period_days(m: "re.Match[str]") -> Optional[int]:
    """Days between the two dates of a _TEXT_PERIOD match. Without years the end is the first such day after the start."""
    d1, mo1, y1, d2, mo2, y2 = m.groups()
    try:
        month1, month2 = _MONTHS[mo1], _MONTHS[mo2]
        year1 = int(y1) if y1 else (int(y2) if y2 and (month2, int(d2)) >= (month1, int(d1)) else (int(y2) - 1 if y2 else 2001))
        start = date(year1, month1, int(d1))
        year2 = int(y2) if y2 else (year1 if (month2, int(d2)) > (month1, int(d1)) else year1 + 1)
        end = date(year2, month2, int(d2))
    except (KeyError, ValueError):
        return None
    days = (end - start).days
    return days if days > 0 else None


# A year less a day ("1 nov 2026 tot 31 okt 2027") is a one-year contract, not a short stay.
SHORT_STAY_DAYS = 350


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
        if key in tenancy.RULES:
            continue      # who-may-rent phrases are read by tenancy.read, with its checks for "niet", "of", "hoeft"
        span = _phrase_span(text, fl.get(key, []))
        if span:
            spans[key] = span
    ev: Dict[str, str] = {k: tenancy.evidence(raw, *v) for k, v in spans.items()}

    # who may rent: students, working people, age, sharers, housemates (rules in tenancy.py)
    who, who_ev = tenancy.read(raw, cfg.get("tenant_rules"),
                               phrases={k: fl.get(k, []) for k in _PHRASE_FLAGS if k in tenancy.RULES})
    names: Dict[str, Optional[str]] = {k: None for k in spans}       # name -> value after the colon
    for f in who:
        k, _, v = f.partition(":")
        names[k] = v or None
        ev[k] = who_ev.get(k, "")
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
    if not names.get("min_age") and not names.get("max_age"):
        # "Vanaf 25 jaar", "25+", "tot 30 jaar" with nothing that makes it a statement about the tenant: nobody
        # is excluded on it, and the card says what to ask.
        hint = tenancy.age_hint(raw)
        if hint:
            out.append("age_unclear")
            ev["age_unclear"] = tenancy.evidence(raw, *hint)

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
                n = int(m.group(1))
            except (IndexError, ValueError):
                continue
            if "\n" in m.group(0):
                continue      # the words stand on two lines of the listing: no statement
            # "niet geschikt voor 2 personen" limits nothing; neither does "2 personen of een gezin"
            if tenancy.denied_before(raw, text, m.start()):
                continue
            more = _OR_NUMBER.match(text, m.end())   # "2 personen of 3 woningdelers": the larger group may rent
            if more:
                n = max(n, int(more.group(1)))
            elif _OR_MORE.match(text, m.end()):
                continue
            nums.append((n, m.span()))
    # "niet alleen voor stellen, ook voor gezinnen" limits nothing, and neither does "een stel of woningdelers"
    couple_spans = [m.span() for pat in cfg.get("couple_patterns", []) for m in re.finditer(pat, text)
                    if "\n" not in m.group(0) and not tenancy.denied_before(raw, text, m.start())
                    and not _OR_MORE.match(text, m.end())]
    if couple_spans:
        nums.append((2, couple_spans[0]))
    # "alleen voor een stel", "couple only": two people who are no couple may not rent it
    only_couples = [m.span() for pat in cfg.get("couple_only_patterns", []) for m in re.finditer(pat, text)
                    if "\n" not in m.group(0) and not tenancy.denied_before(raw, text, m.start())
                    and not _OR_MORE.match(text, m.end())]
    # "alleenstaande of stel": up to two people, said in words that leave open whether two friends count
    couple_worded: List[Tuple[int, int]] = [sp for sp in couple_spans if sp not in only_couples
                                            and _COUPLE_WORD.search(text, sp[0], sp[1])]
    many_bedrooms = type(listing.bedrooms) is int and listing.bedrooms >= 2
    for pat in cfg.get("single_patterns", []):
        for m in re.finditer(pat, text):
            # A single-person option within a couple allowance does not lower that allowance.
            if any(start < m.end() and m.start() < end for start, end in couple_spans):
                continue
            left, _ = tenancy.clause(raw, m.start(), m.end())
            if "\n" in m.group(0) or _DENIED_NEAR.search(text[left:m.start()]):
                continue
            if many_bedrooms and _ROOM_BEFORE.search(text[left:m.start()]):
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
            if offered_with_couple or listed == "couple":
                couple_worded.append(m.span())
    mp = extra.get("max_persons")
    if isinstance(mp, int) and not isinstance(mp, bool) and mp > 0:
        nums.append((mp, None))  # structured value from the site (e.g. Kamernet "suitable for N persons")
    if only_couples and "no_sharers" not in out and "sharers_ok" not in out:
        out.insert(0, "no_sharers")
        ev["no_sharers"] = tenancy.evidence(raw, *only_couples[0])
    if nums:
        n, span = min(nums, key=lambda x: (x[0], x[1] is None))   # of equal numbers, the one with a sentence
        out.append("max_persons:%d" % n)
        ev["max_persons"] = tenancy.evidence(raw, *span) if span else site_facts
        # The limit of two is worded for a couple and the listing says nothing about sharers: whether two
        # people who are no couple may rent it is the thing to ask.
        if n == 2 and couple_worded and not any(k in out for k in ("no_sharers", "sharers_ok", "housemates")):
            out.append("sharing_unclear")
            ev["sharing_unclear"] = tenancy.evidence(raw, *couple_worded[0])
    # "Ideaal voor een stel", "suitable for a couple": the landlord has a couple in mind and the listing says
    # nothing about sharers. No limit and no verdict: the thing to ask before two sharers count on it.
    if not any(k in out for k in ("no_sharers", "sharers_ok", "housemates", "sharing_unclear")):
        for m in _SUITS_COUPLE.finditer(text):
            if ("\n" in m.group(0) or tenancy.denied_before(raw, text, m.start()) or _OR_MORE.match(text, m.end())):
                continue
            out.append("sharing_unclear")
            ev["sharing_unclear"] = tenancy.evidence(raw, *m.span())
            break
    # short term
    a, b = _parse_date(listing.available_from), _parse_date(listing.available_until)
    max_stay_months = extra.get("max_stay_months")
    stay = None
    for m in _SHORT_STAY.finditer(text):
        months = next((g for g in m.groups() if g), None)
        if "\n" in m.group(0) or tenancy.denied_before(raw, text, m.start()):  # "niet alleen voor short stay"
            continue
        if months is None or 0 < int(months) < 12:      # "maximaal 24 maanden" is not short
            stay = m.span()
            break
    if stay is None:
        for m in _TEXT_PERIOD.finditer(text):
            days = _text_period_days(m)
            if days is not None and days < SHORT_STAY_DAYS and not tenancy.denied_before(raw, text, m.start()):
                stay = m.span()
                break
    if ((a and b and (b - a).days < SHORT_STAY_DAYS)
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
            if "\n" in m.group(0) or tenancy.denied_before(raw, text, m.start()) or _OPTIONAL.search(m.group(0)):
                continue
            must = int(next(g for g in m.groups() if g))
            must_note = tenancy.evidence(raw, *m.span())
            break
    if type(must) is int and must > 0:
        out.append("mandatory_costs:%d" % must)
        ev["mandatory_costs"] = must_note or site_facts
    # Service costs the site lists next to the rent (a separate line, or the difference between its bare rent and
    # its total): they are paid every month on top. Only where the source is sure of that (service_costs_on_top).
    svc = extra.get("service_costs")
    if extra.get("service_costs_on_top") is True and type(svc) is int and svc > 0:
        out.append("service_costs:%d" % svc)
        note = extra.get("service_costs_note")
        ev["service_costs"] = note if isinstance(note, str) and note else site_facts
    if listing.price_includes_utilities:
        out.append("utilities_included")
    # The site prices per month of the year (HousingAnywhere): the listed rent is the lowest, this is the highest.
    top = extra.get("price_max")
    if type(top) is int and type(listing.price_total) is int and top > listing.price_total:
        out.append("price_varies:%d" % top)
    pp = _per_person_span(text, listing.price_total, raw)
    occ = occupancy.read(raw)
    one_place = occ.get("one_place")            # "je huurt één slaapkamer": the rent buys one room of the home
    if pp or extra.get("price_per_person") is True or one_place:
        out.append("price_per_person")
        if pp:
            ev["price_per_person"] = tenancy.evidence(raw, *pp)
        elif extra.get("price_per_person") is True:
            ev["price_per_person"] = site_facts
        else:
            ev["price_per_person"] = tenancy.evidence(raw, *one_place[1])
        if "sharing_unclear" in out:            # a place in a shared home is for sharers
            out.remove("sharing_unclear")
            ev.pop("sharing_unclear", None)
    else:
        for n, (amount, span) in sorted(_group_rents(text, raw, listing.price_total).items()):
            out.append("rent_for_%d:%d" % (n, amount))
            ev["rent_for_%d" % n] = tenancy.evidence(raw, *span)
    # How many people the home houses, and how many of those places can be rented now: two different numbers.
    # The site's own field wins over the free text.
    for name in ("capacity", "places_free"):
        site_n = extra.get(name)
        if type(site_n) is int and (0 if name == "places_free" else 1) <= site_n <= 12:
            out.append("%s:%d" % (name, site_n))
            ev[name] = site_facts
        elif name == "capacity" and name in occ:
            out.append("capacity:%d" % occ[name][0])
            ev[name] = tenancy.evidence(raw, *occ[name][1])
        elif name in occ:
            # A count from the text is taken as a count only where the listing shows that the home is let by
            # the place: a rent per person or room, a shared home's stated capacity, a place that is taken.
            # "Nog 1 plek vrij" in an ordinary listing can be about a viewing: then it is a thing to ask.
            # With only a rent per person to go on, a count of "plekken" may be about an appointment
            # ("nog 1 plek vrij voor de rondleiding"): rooms, taken places and wanted housemates are counted.
            if "shared" in occ or ("price_per_person" in out and "places_firm" in occ):
                out.append("places_free:%d" % occ[name][0])
                ev[name] = tenancy.evidence(raw, *occ[name][1])
            else:
                out.append("places_unclear")
                ev["places_unclear"] = tenancy.evidence(raw, *occ[name][1])
        elif name == "places_free" and "places_guess" in occ:
            out.append("places_unclear")        # "de huidige bewoner" in a home for two: suggested, not stated
            ev["places_unclear"] = tenancy.evidence(raw, *occ["places_guess"][1])
    # The text says that other people share the home ("gedeelde woning voor 3 bewoners", "met 2 huisgenoten").
    # Without a rent stated per person, the listed rent may be one place's or the home's: evaluate() leaves each
    # one's share open for a listing with housemates.
    if "housemates" in occ and "housemates" not in out:
        out.append("housemates")
        ev["housemates"] = tenancy.evidence(raw, *occ["housemates"][1])
    if "housemates" in out and "sharing_unclear" in out:   # a home that is shared is for sharers
        out.remove("sharing_unclear")
        ev.pop("sharing_unclear", None)
    if "together" in occ:                       # "je kunt je samen met een medebewoner aanmelden"
        out.append("apply_together")
        ev["apply_together"] = tenancy.evidence(raw, *occ["together"][1])
    if extra.get("private_text"):
        # The text came from the user's mailbox (an alert email), which can hold their name and address.
        # It may decide flags, but no sentence of it is quoted anywhere: not in Telegram, not on the dashboard.
        return out, {}
    return out, {k: v for k, v in ev.items() if v}


def detect_flags(listing: Listing, cfg: Dict[str, Any]) -> List[str]:
    return detect(listing, cfg)[0]


# Gas, water and electricity in the rent or not, said in the listing's own words.
# ("Keuken inclusief elektrische kookplaat" and "incl. gas hob" are about the kitchen.)
_NOT_AN_APPLIANCE = (r"(?!\s*-?\s*(?:kook|fornuis|oven|hob|stove|cooker|haard|kachel|boiler|vloerverwarming|verwarming|heating|"
                     r"fireplace|aansluiting|connection))")
_UTIL = (r"(?:gas\b" + _NOT_AN_APPLIANCE + r"|g\s?/\s?w\s?/\s?[el]\b|gwe\b|gwl\b|nutsvoorzieningen|energie(?:kosten)?\b|"
         r"utilities|bills\b|vaste\s+lasten|water\s*(?:,|en|and|&)\s*(?:elektr|electr|gas|licht)|"
         r"(?:elektra|elektriciteit|electricity|stroom)\b)")
_UTIL_INCL = re.compile(
    r"\b(?:inclusief|incl\.?|including|inclusive\s+of)\s+(?:alle\s+|all\s+|de\s+|the\s+)?" + _UTIL
    + r"|\b(?:utilities|bills|gas,?\s+water\b[^.!?;]{0,30}?)\s+(?:are\s+|is\s+|zijn\s+)?(?:included|inbegrepen|inclusief)\b"
    + r"|\ball[- ]in\s+(?:huur|huurprijs|prijs|rent|price)\b|\b(?:huur|huurprijs|prijs|rent|price)\s+(?:is\s+)?all[- ]in\b")
_UTIL_EXCL = re.compile(
    r"\b(?:exclusief|excl\.?|excluding|exclusive\s+of)\s+(?:alle\s+|all\s+|de\s+|the\s+)?" + _UTIL
    + r"|\b(?:utilities|bills|gas|water|elektra|elektriciteit|electricity|energie)\s+(?:are\s+|is\s+|zijn\s+)?"
      r"(?:not\s+included|niet\s+inbegrepen|niet\s+inclusief|excluded|exclusief|excl\.?(?!\w))")
# "€ 175 per maand voorschot inclusief gas, water, elektriciteit": an advance that covers them is paid on top
_ADVANCE = re.compile(r"\b(?:voorschot|voorschotbedrag|advance(?:\s+payment)?)\b")


def infer_utilities(listing: Listing) -> None:
    """What the text says about gas, water and electricity, set against what the site's field says.
    The text alone decides when the site says nothing. When the two disagree ("Incl. vaste lasten" on the site,
    "exclusief gas en elektra" in the text), or the text says both, nothing is claimed."""
    raw = (listing.title or "") + " \n " + (listing.description or "")
    low = raw.lower()
    if len(low) != len(raw):
        raw = low
    incl = excl = False
    for m in _UTIL_INCL.finditer(low):
        if "\n" in m.group(0):
            continue
        left, _ = tenancy.clause(raw, m.start(), m.end())
        if tenancy.denied_before(raw, low, m.start()) or _ADVANCE.search(low[left:m.start()]):
            excl = True
        else:
            incl = True
    excl = excl or any("\n" not in m.group(0) for m in _UTIL_EXCL.finditer(low))
    if not incl and not excl:
        return
    said = None if incl and excl else incl
    have = listing.price_includes_utilities
    if have is None:
        listing.price_includes_utilities = said
    elif said is None or said != have:
        listing.price_includes_utilities = None


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


def _household(cfg: Dict[str, Any]) -> Dict[str, Any]:
    h = cfg.get("household")
    return h if isinstance(h, dict) else {}


def _ages(household: Dict[str, Any]) -> List[int]:
    ages = household.get("ages")
    out = [a for a in ages if type(a) is int] if isinstance(ages, list) else []
    if type(household.get("age")) is int:
        out.append(household["age"])
    return out


def _excluded(flags: List[str], household: Any) -> bool:
    """Does the listing itself say this household may not rent it? `household` is config.json's
    {"persons": 2, "occupation": "students" | "working", "ages": [19, 24], "sharers": true}; without it
    (the default) nothing is excluded. Only what the listing states counts: a listing that says nothing, or
    says it unclearly, is never excluded."""
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
    ages = _ages(household)
    if ages:
        # everyone has to be old enough and young enough
        if names.get("min_age", "").isdigit() and int(names["min_age"]) > min(ages):
            return True
        if names.get("max_age", "").isdigit() and int(names["max_age"]) < max(ages):
            return True
    # "sharers": people who rent together without being a couple or a family (woningdelers)
    if household.get("sharers") is True and "no_sharers" in names:
        return True
    return False


def _share(amount: int, persons: int) -> int:
    """One person's equal share in whole euros, halves rounded up."""
    return int(amount / persons + 0.5)


def rent_basis(flags: List[str]) -> Optional[str]:
    """What the listed rent buys: "home" (the whole home), "place" (one room, or one tenant's share), or
    None when the listing leaves it open."""
    names = {f.partition(":")[0] for f in flags}
    if "price_per_person" in names:
        return "place"
    if "rent_unclear" in names:
        return None
    return "home"


def price_basis(flags: List[str], includes_utilities: Optional[bool]) -> Optional[str]:
    """ "excl" when the listing establishes that costs come on top of the quoted rent (service costs, a
    compulsory extra, or utilities), "incl" when it establishes that utilities are in it, else None."""
    names = {f.partition(":")[0] for f in flags}
    if "service_costs" in names or "mandatory_costs" in names or includes_utilities is False:
        return "excl"
    if includes_utilities is True:
        return "incl"
    return None


def _flag_number(flags: List[str], name: str) -> Optional[int]:
    for f in flags:
        k, _, v = f.partition(":")
        if k == name and v.isdigit():
            return int(v)
    return None


def capacity(flags: List[str]) -> Optional[int]:
    """How many people the home houses, when the listing says so."""
    n = _flag_number(flags, "capacity")
    return n if n is not None else _flag_number(flags, "max_persons")


def places_available(flags: List[str]) -> Optional[int]:
    """How many places can be rented now, when the listing says so."""
    return _flag_number(flags, "places_free")


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
    infer_utilities(listing)
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
    # The listing states its rent per person or per room: everyone who moves in pays that amount.
    per_person = "price_per_person" in flags
    must_pay = 0  # what must be paid every month on top of the rent (service costs, a compulsory parking place)
    for f in flags:
        if f.startswith("mandatory_costs:") or f.startswith("service_costs:"):
            must_pay += int(f.split(":", 1)[1])
    susp = cfg.get("suspicious_total_below")
    if price is not None and susp and price < susp and (eff_bed is None or eff_bed >= 2) and not per_person:
        flags.append("suspicious_price")
    # "€750, 200 m², 6 kamers" (an alert email, which names no type): far too little rent for that much home.
    # It is almost certainly one room of it, or a price per person. Warned about, not dropped: it is a guess.
    per_m2 = cfg.get("suspicious_rent_per_m2_below")
    area = listing.area_m2
    if ("suspicious_price" not in flags and not per_person and type(per_m2) in (int, float) and per_m2 > 0
            and type(price) is int and type(area) is int and area >= 30 and price < per_m2 * area):
        flags.append("suspicious_price")

    max_persons: Optional[int] = None
    group_rent: Dict[int, int] = {}    # what the listing says this many people pay together
    for f in flags:
        if f.startswith("max_persons:"):
            max_persons = int(f.split(":", 1)[1])
        elif f.startswith("rent_for_") and not per_person:
            name, _, value = f.partition(":")
            if name[9:].isdigit() and value.isdigit():
                group_rent[int(name[9:])] = int(value)
    no_sharers = "no_sharers" in flags
    home_capacity = _flag_number(flags, "capacity")
    places = _flag_number(flags, "places_free")
    # What the listed rent buys. Per person or per room: one place. Other people share the home, or the rent is
    # far too low for the home: it may be the rent of one room, so the listing does not settle it.
    housemates = "housemates" in flags
    extra = listing.extra if isinstance(listing.extra, dict) else {}
    if per_person:
        basis: Optional[str] = "place"
    elif housemates or "suspicious_price" in flags or extra.get("rent_basis_unread") is True:
        # also: a site whose search page shows one amount for whole homes and for single places alike, until the
        # listing's own page has been read (Van der Linden: "€ 966 per maand" for one place in a home for two)
        basis = None
    else:
        basis = "home"
    # Costs on top of the rent. What the site lists next to the rent belongs to the contract the rent belongs to;
    # a compulsory extra named in the text (a parking place) is one per home.
    service = sum(int(f.split(":", 1)[1]) for f in flags if f.startswith("service_costs:"))
    compulsory = must_pay - service

    used_unknown = False
    used_guess = False
    for s in searches:
        persons = int(s["persons"])
        # Can this many people move in? Fewer free places than people: no, whatever the rent.
        if places is not None and places < persons:
            continue
        if home_capacity is not None and home_capacity < persons:
            continue
        if max_persons is not None and max_persons < persons:
            continue
        if persons >= 3 and no_sharers:
            continue
        pp: Optional[int] = None       # the quoted rent each person pays
        extra_pp = 0                   # what each must pay on top of it
        if price is None:
            total = None
        elif basis == "place":
            # each has a contract of their own: the rent and the site's service costs once a person; the
            # compulsory extra once for the home, shared
            total = (price + service) * persons + compulsory
            pp, extra_pp = price, _share(total, persons) - price
        elif basis == "home":
            # the rent is for the whole home and all of them move in: each pays an equal share
            rent = group_rent.get(persons, price)
            total = rent + must_pay
            pp = _share(rent, persons)
            extra_pp = _share(total, persons) - pp
        else:
            total = price + must_pay                  # the least it can be; each one's share is not settled
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
        used_guess = used_guess or (bed_guessed and eff_bed is not None)
        tier = "great" if (total is not None and basis is not None and total <= s["great_total"]) else "ok"
        res.matches.append(SearchMatch(s["name"], persons, pp, tier, extra_pp))

    if res.matches:
        if price is not None and basis is None:
            extra_flags.append("rent_unclear")
        # Rent per place in a home other people share, and the listing does not say how many places are free.
        if basis == "place" and housemates and places is None:
            extra_flags.append("places_unclear")
        if used_unknown:
            extra_flags.append("bedrooms_unknown")
        if used_guess:
            extra_flags.append("bedrooms_unconfirmed")
        for f in extra_flags:
            if f not in flags:
                flags.append(f)
    return res
