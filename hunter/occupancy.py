"""What a listing's text says about who lives in the home and what the rent buys.

Three different things, which a listing can state separately:

  capacity      how many people the home houses in all ("gedeelde huisvesting voor twee bewoners")
  places free   how many of those places can be rented now ("één plek is al bezet", "1 kamer vrij")
  one place     the quoted rent buys one room or one tenant's place, not the home ("je huurt één slaapkamer")

Only what the text states is returned. A sentence that stands on two lines of the listing, or that is denied
("geen huisgenoten"), says nothing.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from . import tenancy

Span = Tuple[int, int]

_WORDS = {"nul": 0, "zero": 0, "een": 1, "één": 1, "eén": 1, "one": 1, "a": 1, "an": 1, "twee": 2, "two": 2, "drie": 3, "three": 3,
          "vier": 4, "four": 4, "vijf": 5, "five": 5, "zes": 6, "six": 6}
_N = r"(\d|" + "|".join(sorted((w for w in _WORDS if w not in ("a", "an")), key=len, reverse=True)) + r")"
# A number that is written as one: "een plek" and "a place" are an article, not a count.
_NS = r"(\d|" + "|".join(sorted((w for w in _WORDS if w not in ("a", "an", "een")), key=len, reverse=True)) + r")"
_N1 = r"(1|een|één|eén|one|a|an)"                    # one, where "a room" also counts
_N3 = r"(1|een|één|eén|one|a|an|2|twee|two|3|drie|three)"

_HOME_NL = r"(?:huisvesting|woning|woonruimte|appartement|huis|woonhuis|studentenhuis|flat)"
_HOME_EN = r"(?:accommodation|apartment|flat|house|home|housing)"
_PEOPLE_NL = r"(?:bewoners|huurders|personen|studenten|mensen)"
_PEOPLE_EN = r"(?:residents|tenants|people|persons|students|occupants)"
_MATE_NL = r"(?:huisgeno(?:ot|ote|ten|tes|otje|otjes)|medebewoners?|medehuurders?|kamergeno(?:ot|ten))"
_MATE_EN = r"(?:house\s?mates?|flat\s?mates?|room\s?mates?|room\s?ies?)"
_PLACE_NL = r"(?:plek(?:ken|je)?|plaats(?:en)?)"
_PLACE_EN = r"(?:places?|spots?|spaces?)"

# The home as a whole houses N people.
_CAPACITY = re.compile(
    r"\bgedeelde\s+" + _HOME_NL + r"\s+voor\s+" + _N + r"\s+" + _PEOPLE_NL + r"\b"
    r"|\bshared\s+" + _HOME_EN + r"\s+for\s+" + _N + r"\s+" + _PEOPLE_EN + r"\b"
    r"|\b(?:in\s+totaal|totaal)\s+" + _N + r"\s+huisgenoten\b"
    r"|\b" + _N + r"\s+(?:housemates|flatmates)\s+in\s+total\b")
# "Geschikt voor in totaal 2 bewoners": how many people fit. It does not say that the home is shared by people who
# rent a place each: a whole home for two is worded the same way.
_CAPACITY_PLAIN = re.compile(
    r"\b(?:in\s+totaal|totaal)\s+" + _N + r"\s+(?:bewoners|huurders)\b"
    r"|\b" + _N + r"\s+(?:residents|tenants)\s+in\s+total\b")

# "je deelt de woning met 2 huisgenoten", "you share with one other resident": N others, so N + 1 in all.
# It does not say whether those others live there already.
_OTHERS = re.compile(
    r"\bmet\s+" + _N + r"\s+(?:andere\s+(?:bewoners?|huurders?|studenten?|personen|persoon|mensen|meiden|jongens)"
    r"|(?:gezellige\s+|leuke\s+|fijne\s+)?" + _MATE_NL + r")\b"
    r"|\bwith\s+" + _N + r"\s+(?:other\s+(?:residents?|tenants?|people|persons?|students?|girls|guys)"
    r"|(?:friendly\s+|nice\s+|lovely\s+)?" + _MATE_EN + r")\b")

# The listing is for a room in a home other people live in: "kamer te huur in studentenhuis", "room in shared
# flat", "wij zoeken een derde huisgenoot", "ieder huurt een eigen kamer met een eigen huurcontract".
_ROOM_LET = re.compile(
    r"\b(?:studenten)?kamer\s+te\s+huur\b"
    r"|\b(?:(?<!living\s)(?<!storage\s)(?<!dining\s)(?<!laundry\s)room)\s+(?:for\s+rent|to\s+rent|to\s+let)\b"
    r"|\broom\s+(?:is\s+)?available\s+in\b"
    r"|\bkamer\s+(?:is\s+)?(?:beschikbaar|vrij)\s+in\b"
    r"|\bkamers?\s+in\s+(?:een\s+|ons\s+|het\s+|dit\s+)?(?:\w+\s+){0,2}?"
    r"(?:studentenhuis|studentenwoning|studentenflat|woongroep|gedeeld\w*|deelwoning)\b"
    r"|\brooms?\s+in\s+(?:a\s+|an\s+|our\s+|the\s+|this\s+)?(?:\w+\s+){0,2}?"
    r"(?:student\s+house|student\s+flat|shared\s+\w+|house\s?share|flat\s?share)\b"
    # "room in a 3-bedroom apartment" is a room; "the rooms in this 2-bedroom apartment are bright" is a home
    r"|\broom\s+in\s+(?:a|an|our)\s+(?:\w+\s+){0,2}?\d[- ]bed(?:room)?\s+\w+\b"
    r"|\bshared\s+(?:flat|apartment|house|home|accommodation|housing)\b"
    r"|\bgedeelde?\s+(?:woning|appartement|huis|huisvesting|woonruimte|studentenwoning)\b"
    r"|\b(?:tweede|derde|vierde|vijfde|zesde|second|third|fourth|fifth|sixth|2e|3e|4e|5e|2nd|3rd|4th|5th)\s+"
    r"(?:" + _MATE_NL + r"|" + _MATE_EN + r")\b"
    r"|\b(?:ieder|iedere|iedereen|elke|elk)\s+(?:huurder\s+|bewoner\s+|student\s+)?(?:huurt|krijgt|heeft)\s+"
    r"(?:een\s+)?eigen\s+(?:(?:slaap)?kamer|huurcontract|contract)\b"
    r"|\b(?:each|every)\s+(?:tenant|resident|student|housemate|person)\s+(?:has|gets|rents|signs|will\s+have|will\s+get)\s+"
    r"(?:an?\s+|their\s+|his\s+or\s+her\s+)?(?:own|individual|separate)\s+(?:(?:bed)?room|contract|lease|rental\s+contract)\b"
    r"|\b(?:eigen|individueel|apart)\s+huurcontract\s+per\s+(?:persoon|kamer|huurder|bewoner)\b"
    r"|\b(?:individual|separate)\s+(?:contracts?|leases?)\s+(?:per|for\s+each)\s+(?:person|room|tenant)\b")

# Places that are taken: "één van de twee plekken is al bezet", "de andere kamer is verhuurd".
_TAKEN_NL = r"(?:al\s+|reeds\s+|momenteel\s+|nu\s+|op\s+dit\s+moment\s+)?(?:bezet|verhuurd|bewoond|vergeven)"
_TAKEN_EN = r"(?:already\s+|currently\s+|now\s+)?(?:occupied|taken|rented(?:\s+out)?|let)"
_OCCUPIED = re.compile(
    r"\b" + _NS + r"\s+(?:van\s+de\s+" + _NS + r"\s+)?(?:" + _PLACE_NL + r"|(?:slaap)?kamers?)\s+(?:is|zijn)\s+" + _TAKEN_NL + r"\b"
    r"|\b" + _NS + r"\s+(?:of\s+the\s+" + _NS + r"\s+)?(?:" + _PLACE_EN + r"|(?:bed)?rooms?)\s+(?:is|are)\s+" + _TAKEN_EN + r"\b")
# One other room or place is taken, in a home the text speaks of as having two.
_OTHER_TAKEN = re.compile(
    r"\bde\s+andere\s+(?:" + _PLACE_NL + r"|(?:slaap)?kamer)\s+is\s+" + _TAKEN_NL + r"\b"
    r"|\bthe\s+other\s+(?:" + _PLACE_EN + r"|(?:bed)?room)\s+is\s+" + _TAKEN_EN + r"\b")
# Somebody lives there now: at least one place is taken.
_LIVES_THERE = re.compile(
    r"\b(?:huidige|zittende)\s+(?:bewoners?|huurders?|" + _MATE_NL + r")\b"
    r"|\b(?:current|existing|sitting)\s+(?:residents?|tenants?|occupants?|" + _MATE_EN + r")\b"
    r"|\b(?:bewoner|huurder|" + _MATE_NL + r")\s+(?:woont|wonen)\s+er\s+al\b"
    r"|\b(?:resident|tenant|" + _MATE_EN + r")\s+(?:already\s+lives?|is\s+already\s+living|lives?\s+there\s+already)\b")

# Places that can be rented now.
_FREE = re.compile(
    r"\b" + _NS + r"\s+" + _PLACE_NL + r"\s+(?:is\s+|zijn\s+|komt\s+|komen\s+)?(?:er\s+)?(?:nog\s+)?(?:vrij|beschikbaar|over)\b"
    r"|\b" + _NS + r"\s+(?:vrije|beschikbare)\s+(?:" + _PLACE_NL + r"|kamers?)\b"
    r"|\b" + _NS + r"\s+(?:slaap)?kamers?\s+(?:is\s+|zijn\s+|komt\s+|komen\s+)(?:er\s+)?(?:nog\s+)?(?:vrij|beschikbaar)\b"
    r"|\b(?:nog|slechts|alleen\s+nog)\s+" + _NS + r"\s+(?:" + _PLACE_NL + r"|kamers?)\s+(?:vrij|beschikbaar|over)\b"
    r"|\b" + _NS + r"\s+" + _PLACE_EN + r"\s+(?:is\s+|are\s+)?(?:still\s+)?(?:available|free|left|open)\b"
    r"|\b" + _NS + r"\s+(?:free|available|vacant)\s+(?:" + _PLACE_EN + r"|(?:bed)?rooms?)\b"
    r"|\b" + _NS + r"\s+(?:bed)?rooms?\s+(?:is|are|becomes?)\s+(?:still\s+)?(?:available|free|vacant)\b"
    r"|\b(?:er\s+)?(?:is|komt)\s+(?:nog\s+|per\s+direct\s+)?(een)\s+(?:slaap)?kamer\s+(?:vrij|beschikbaar)\b"
    r"|\b(a)\s+(?:bed)?room\s+(?:is|becomes|has\s+become)\s+(?:still\s+)?(?:available|free|vacant)\b"
    r"|\b(?:only|just)\s+" + _NS + r"\s+(?:" + _PLACE_EN + r"|(?:bed)?rooms?)\s+(?:available|free|left)\b")
# "wij zoeken een nieuwe huisgenoot", "looking for 2 new housemates": that many places are free.
_WANTED = re.compile(
    # The people who live there say it ("wij zoeken"), or it is an ad's heading ("huisgenoot gezocht").
    r"\b(?:wij|we|ik)\s+(?:zijn\s+op\s+zoek\s+naar|ben\s+op\s+zoek\s+naar|zoeken|zoek)\b[^.!?\n]{0,25}?\b" + _N3
    + r"\s+(?:nieuwe\s+|gezellige\s+|leuke\s+){0,2}" + _MATE_NL + r"\b"
    r"|\b" + _N3 + r"\s+(?:nieuwe\s+|gezellige\s+|leuke\s+){0,2}" + _MATE_NL + r"\s+gezocht\b"
    r"|\bgezocht\s*:?\s+" + _N3 + r"\s+(?:nieuwe\s+|gezellige\s+|leuke\s+){0,2}" + _MATE_NL + r"\b"
    r"|\b(?:looking|searching)\s+for\s+" + _N3 + r"\s+(?:new\s+|nice\s+|friendly\s+){0,2}" + _MATE_EN + r"\b")

# The rent buys one room or place of the home.
_ONE_PLACE = re.compile(
    r"\b(?:je|u|jij)\s+huurt\s+" + _N1 + r"\s+(?:eigen\s+|priv[ée]\s+)?(?:slaap)?kamer\b"
    r"|\byou(?:\s+will|['’]ll)?\s+rent\s+" + _N1 + r"\s+(?:private\s+|own\s+|single\s+)?(?:bed)?room\b")

# "de huidige huurder vertrekt": the person who lives there now is leaving, so the place is not taken.
_LEAVING = re.compile(r"vertrek|vertrok|verlaat|verlat|verliet|verhuis|verhuiz|gaat\s+weg|gaan\s+weg|leav|\bleft\b|"
                      r"mov(?:e|es|ed|ing)\s+out|zeg\w*\s+op|opgezegd|opzeg|terminat|gave\s+notice|given\s+notice|"
                      r"overname|over\s+te\s+nemen|overnemen|take\s+over|taken\s+over")
# "De gehele woning komt vrij": the home is let as a whole, whoever lives there now.
_WHOLE_HOME_FREE = re.compile(
    r"\b(?:gehele|hele|volledige|complete)\s+(?:woning|appartement|huis|woonruimte)\s+(?:komt|is|wordt)\s+(?:\w+\s+){0,3}?(?:vrij|beschikbaar|verhuurd)\b"
    r"|\b(?:entire|whole|complete)\s+(?:home|apartment|house|flat|property)\s+(?:is|becomes|will\s+be)\s+(?:\w+\s+){0,3}?(?:available|free|vacant|for\s+rent)\b")
# No place is left: "geen plekken meer vrij", "alle plekken zijn bezet".
_NONE_FREE = re.compile(
    r"\bgeen\s+" + _PLACE_NL + r"\s+(?:meer\s+)?(?:vrij|beschikbaar)\b"
    r"|\balle\s+" + _PLACE_NL + r"\s+zijn\s+(?:al\s+|reeds\s+)?(?:bezet|verhuurd|vergeven)\b"
    r"|\bno\s+" + _PLACE_EN + r"\s+(?:are\s+)?(?:left|available|free)\b"
    r"|\ball\s+" + _PLACE_EN + r"\s+are\s+(?:already\s+)?(?:taken|occupied|rented)\b")

# Two people who know each other can apply for the home together.
_TOGETHER = re.compile(
    r"\bsamen\s+met\s+(?:een|je|uw|jouw)\s+(?:toekomstige?\s+)?(?:" + _MATE_NL + r"|vriend(?:in)?|studiegenoot|kennis)\s+"
    r"(?:aanmelden|inschrijven|reageren|huren|solliciteren)\b"
    r"|\bapply\s+together\s+with\s+(?:a|your)\s+(?:prospective\s+|future\s+)?(?:" + _MATE_EN + r"|friend|fellow\s+student)\b"
    r"|\b(?:apply|register|rent(?:\s+it)?)\s+(?:as\s+a\s+pair|with\s+a\s+friend|together\s+with\s+a\s+friend)\b")


def _num(word: Optional[str]) -> Optional[int]:
    if not word:
        return None
    if word.isdigit():
        return int(word)
    return _WORDS.get(word)


# A free "plek" or "space" in the bicycle shed, the garage or the storage is no place to live.
_NOT_LIVING = re.compile(r"fiets|parkeer|garage|stalling|berging|kelder|parking|bike|bicycle|storage|\bcar\b|\bauto\b|"
                         r"scooter|motor|boot|ligplaats|"
                         # a place at a viewing, or a room with another use
                         r"bezichtiging|kijkdag|kijkavond|open\s+huis|open\s+dag|viewing|open\s+house|open\s+day|"
                         # a place on a list is no place in the home
                         r"inschrijv|inschrijf|wachtlijst|wachttijd|reservelijst|loting|waiting\s?list|wait\s?list|"
                         r"\blist\b|\blijst\b|"
                         r"werkkamer|studeerkamer|logeerkamer|hobbykamer|kantoor|opslag|zolder|"
                         r"\bstudy\b|office|guest\s+room|attic")
_DENIAL_INSIDE = re.compile(r"\b(?:geen|niet|nooit|no|not|never)\b|n['’]t\b")
# "1 kamer vrij indeelbaar", "2 kamers vrij te gebruiken": about the layout, not about who lives there.
# "1 kamer is beschikbaar als werkkamer": a use of the room, not a place for a tenant.
_NOT_FREE_AFTER = re.compile(r"\s+(?:in\s?deelbaar|in\s+te\s+delen|te\s+gebruiken|te\s+besteden|van\b|of\s+charge|"
                             r"als\b|as\s+(?:a|an|your)\b|om\s+te\b|for\s+(?:storage|guests|work))")


# A whole home that may be used as a shared home ("ook te gebruiken als gedeelde woning", "suitable as a shared
# flat for 2 friends", "woningdelen toegestaan"), a question ("Kamer te huur? Nee, een compleet appartement"), or
# a refusal after the words ("gedeelde huisvesting is niet toegestaan"): none of them says the home is let by
# the room.
_AS_BEFORE = re.compile(r"(?:\bals(?:\s+een)?|\bas(?:\s+an?)?|\bvoor\s+een|\bfor\s+an?|"
                        r"\bmag|\bkan|\bcan\s+be\s+\w+)\s+(?:\w+\s+){0,2}$")
_ALLOWED_NEAR = re.compile(r"toegestaan|woningdel|\bdelers\b|sharers|\bvrienden\b|\bvriend(?:in)?\b|\bfriends?\b|"
                           r"\bmogelijk\b|\ballowed\b|\bpossible\b|\bpermitted\b")
_REFUSED_AFTER = re.compile(r"\s+(?:is|zijn|are|wordt|worden|will\s+be)\s+(?:\w+\s+)?(?:niet|not|geen|no)\b")


def _offered_as(raw: str, low: str, m: "re.Match[str]") -> bool:
    left, right = tenancy.clause(raw, m.start(), m.end())
    if _AS_BEFORE.search(low[max(left, m.start() - 40):m.start()]):
        return True
    if _ALLOWED_NEAR.search(low, left, right) or _REFUSED_AFTER.match(low, m.end()):
        return True
    return raw[right:right + 1] == "?" or raw[m.end():m.end() + 1] == "?"


def _hits(pattern: "re.Pattern[str]", raw: str, low: str, places: bool = False, denial_inside: bool = True):
    """The matches that are statements: on one line, not denied, and (for free or taken places) about a place
    to live."""
    for m in pattern.finditer(low):
        if "\n" in m.group(0) or tenancy.denied_before(raw, low, m.start()):
            continue
        if denial_inside and _DENIAL_INSIDE.search(m.group(0)):
            continue
        if places:
            left, right = tenancy.clause(raw, m.start(), m.end())
            if _NOT_LIVING.search(low, left, right) or _NOT_FREE_AFTER.match(low, m.end()):
                continue
        yield m


def read(raw: str) -> Dict[str, Tuple[object, Span]]:
    """{name: (value, span)} for what the text states. Names: "capacity" (int), "places_free" (int),
    "one_place" (True), "together" (True), and "shared" (True) when the text itself establishes that the home is
    let by the place: a stated capacity of a shared home, a place that is taken, or a rent for one room.
    "housemates" (True) when the text says other people share the home, whether or not they live there yet
    ("gedeelde woning voor 3 bewoners", "met 2 huisgenoten"): the rent may then be the rent of one place.
    "places_firm" (True) when the count of free places counts rooms, taken places or wanted housemates, not
    "plekken" (which a viewing day has too).
    "places_guess" (int) is a count the text does not state but suggests ("de huidige bewoner" in a home for
    two): a thing to ask, never a reason to drop a home.

    A count of free places is only as good as that evidence. "Nog 1 plek vrij" can be about a viewing, and
    "een kamer beschikbaar" about a study: without "shared" (or a rent per person, which the caller knows) the
    count is a question to ask, not a reason to drop a home."""
    low = raw.lower()
    if len(low) != len(raw):
        raw = low
    out: Dict[str, Tuple[object, Span]] = {}
    shared: Optional[Span] = None

    capacity: Optional[Tuple[int, Span]] = None
    for m in _hits(_CAPACITY, raw, low):
        n = _num(next((g for g in m.groups() if g), None))
        if n and n >= 2 and not _offered_as(raw, low, m):
            capacity = (n, m.span())
            shared = m.span()                              # "gedeelde huisvesting voor twee bewoners"
            break
    mates: Optional[Span] = shared
    if capacity is None:
        for m in _hits(_OTHERS, raw, low):
            n = _num(next((g for g in m.groups() if g), None))
            if n:
                capacity = (n + 1, m.span())
                mates = m.span()                           # "met 2 huisgenoten": others share the home
                break
    if capacity is None:
        for m in _hits(_CAPACITY_PLAIN, raw, low):
            n = _num(next((g for g in m.groups() if g), None))
            if n and n >= 2:
                capacity = (n, m.span())
                break
    one = next(iter(_hits(_ONE_PLACE, raw, low)), None)
    if one is not None:
        out["one_place"] = (True, one.span())
        shared = shared or one.span()

    if mates is None:
        for let in _hits(_ROOM_LET, raw, low, places=True):
            left, _ = tenancy.clause(raw, let.start(), let.end())
            if _DENIAL_INSIDE.search(low, left, let.start()) or _offered_as(raw, low, let):
                continue                                   # "not suitable for a shared house"
            mates = let.span()
            break
    free: Optional[Tuple[int, Span]] = None
    # A count is firm by its own words when it counts rooms, taken places or wanted housemates. "Nog 1 plek vrij"
    # counts places of something: of the home, of a viewing day, of a tour.
    firm = False
    # places that are taken: a statement about this home's places, whatever else the text says
    for m in _hits(_OCCUPIED, raw, low, places=True):
        groups = [g for g in m.groups() if g]
        taken = _num(groups[0]) if groups else None
        of = _num(groups[1]) if len(groups) > 1 else (capacity[0] if capacity else None)
        if taken and of and of >= taken:
            free = (of - taken, m.span())
            firm = True
            shared = shared or m.span()
            if capacity is None:
                capacity = (of, m.span())
            break
    if free is None:
        for m in _hits(_OTHER_TAKEN, raw, low, places=True):
            if capacity is None or capacity[0] == 2:      # "the other room": a home of two places
                free = (1, m.span())
                firm = True
                shared = shared or m.span()
                if capacity is None:
                    capacity = (2, m.span())
                break
    # places that are free, or wanted housemates
    if free is None:
        for m in _hits(_FREE, raw, low, places=True):
            n = _num(next((g for g in m.groups() if g), None))
            if n is not None:                             # "0 plekken vrij" is a statement too
                free = (n, m.span())
                firm = re.search(r"kamer|room", m.group(0)) is not None
                break
    if free is None:
        for m in _NONE_FREE.finditer(low):
            left, right = tenancy.clause(raw, m.start(), m.end())
            if "\n" not in m.group(0) and not _NOT_LIVING.search(low, left, right):
                free = (0, m.span())
                break
    if free is None:
        for m in _hits(_WANTED, raw, low):
            _, right = tenancy.clause(raw, m.start(), m.end())
            if raw[right:right + 1] == "?":               # "Zoek je nog een huisgenoot?" asks, it does not say
                continue
            n = _num(next((g for g in m.groups() if g), None))
            if n:
                free = (n, m.span())
                firm = True
                break
    # A shared home stated to house two, and the text speaks of somebody who lives there: perhaps one place is
    # left. The text does not say so ("bezichtiging in overleg met de huidige huurder" is said of a home that
    # comes free as a whole too), so this is only ever a thing to ask.
    stated_two = capacity is not None and capacity[0] == 2 and shared is not None
    if free is None and stated_two and not _WHOLE_HOME_FREE.search(low) and not _LEAVING.search(low):
        for m in _hits(_LIVES_THERE, raw, low, places=True):
            out["places_guess"] = (1, m.span())
            break

    # "You can apply together with a prospective flatmate": two people can apply as a pair. It does not say that
    # both places are free, so it sets no count.
    together = next(iter(_hits(_TOGETHER, raw, low)), None)
    if together is not None:
        out["together"] = (True, together.span())
    if capacity is not None:
        out["capacity"] = capacity
    if free is not None:
        out["places_free"] = free
        if firm:
            out["places_firm"] = (True, free[1])
    if shared is not None:
        out["shared"] = (True, shared)
    if mates is not None or shared is not None:
        out["housemates"] = (True, mates or shared)
    return out
