"""Who may rent a home, read from the listing's own words.

Flags: students_only, no_students, students_ok, working_only, women_only, men_only, min_age:N, max_age:N,
no_sharers, sharers_ok and housemates (people already live there, or kitchen/bathroom/living room are shared).

Accuracy comes first. The rules:
- A flag is set by a statement, never by a guess. "Ideal for students" says students are welcome
  (students_ok); only "students only" / "alleen voor studenten" says nobody else may rent.
- A denial in front of a phrase, or inside it, cancels it: "niet alleen voor studenten",
  "no shared kitchen", "geschikt voor een stel, niet voor woningdelers".
- "Only for students or young professionals" excludes neither group, so it sets nothing.
- When two statements contradict each other, neither is reported: no answer beats a wrong one.
- Every flag carries the sentence that set it (`evidence`), so a person can check it on the page.

The patterns run on lowercased text. config.json can add patterns per flag under "tenant_rules";
it cannot remove these.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple

# --------------------------------------------------------------------------- building blocks
_STUD = (r"(?:(?:internationale?|international|incoming|exchange|uitwisselings|eerstejaars|first[- ]year|nieuwe|new|"
         r"full[- ]?time|voltijds?)\s+)?student(?:en|s)?\b")
_ONLY_NL = r"(?:alleen|enkel|uitsluitend|exclusief|slechts)"
_ONLY_EN = r"(?:only|exclusively|solely|strictly)"
_AVAIL_NL = r"(?:(?:nog\s+)?(?:beschikbaar|bedoeld|geschikt|te\s+huur|toegankelijk|verhuurd|toegestaan|mogelijk)\s+)?"
_AVAIL_EN = r"(?:(?:available|suitable|meant|intended|rented(?:\s+out)?|open|reserved)\s+)?"
_COUNT = r"(?:(?:max(?:imaal|imum|\.)?|up\s+to)\s*)?(?:(?:\d|een|één|twee|drie|vier|one|two|three|four|a|an)\s+)?"
_WORK_NL = (r"(?:werkenden?\b|werkende\s+(?:personen?|mensen|stel(?:len)?|koppels?|huurders?|jongeren|professionals?)\b|"
            r"young\s+professionals?\b|(?:jonge\s+)?professionals?\b|expats?\b)")
_WORK_EN = (r"(?:(?:working|young)\s+professionals?\b|professionals\b|"
            r"working\s+(?:people|persons?|couples?|tenants?|adults?|individuals?)\b|"
            r"employed\s+(?:people|persons?|tenants?)\b|workers\b|expats?\b)")
_GAP = r"(?:[^.!?;:()]{0,60}?[\s,/])?"       # a few words between the verb and the group, within one clause
_SHARERS_NL = r"(?:woningdelers|delers|woongroep(?:en)?)"
_SHARERS_EN = r"(?:(?:house|home|flat)[- ]?)?sharers"
_NOT_A_PET = r"(?!\s+(?:(?:a|an|my|our|the|two|three|\d)\s+)?(?:\w+\s+)?(?:cats?|dogs?|pets?|katten|kat|honden|hond|huisdier(?:en)?)\b)"
# "je deelt de woning met je partner of een vriend": the people who move in together, not someone who lives there
_NOT_OWN_COMPANY = (r"(?!\s+(?:(?:je|uw|jouw|jullie|your)\s+(?:eigen\s+|own\s+)?(?:partner|vriend(?:in|en|innen)?|friends?|"
                    r"girlfriend|boyfriend|spouse|wife|husband|gezin|family)\b|elkaar\b|each\s+other\b))")

RULES: Dict[str, List[str]] = {
    "students_only": [
        rf"\b{_ONLY_NL}\s+{_AVAIL_NL}(?:voor|aan)\s+{_COUNT}{_STUD}",
        rf"\b{_ONLY_NL}\s+te\s+huur\s+[^.!?;]{{0,60}}?\baan\s+{_STUD}",
        rf"\b{_STUD}\s+only\b(?!\s+(?:pay|need|have|get|€|\d))",
        rf"\b{_ONLY_EN}\s+{_AVAIL_EN}(?:for|to)\s+{_COUNT}{_STUD}",
        rf"\bonly\s+{_STUD}(?:\s+(?:or|and)\s+\w+)?\s+(?:are\s+|is\s+|can\s+|may\s+)?(?:eligible|allowed|accepted|welcome|apply)\b",
        r"\b(?:must|need\s+to|have\s+to|should)\s+be\s+(?:a\s+|an\s+)?"
        r"(?:(?:full[- ]?time|enrolled|registered|current|international)\s+)?student\b",
        r"\b(?:dat|that)\s+(?:u|je|jij|you)\s+(?:momenteel\s+|currently\s+|nog\s+)*"
        r"(?:student\s+bent|are\s+(?:currently\s+|still\s+)?a\s+student)\b",
        r"\b(?:je|u|jij)\s+(?:moet|dient)\s+(?:een\s+)?(?:voltijds?\s*)?student\s+(?:te\s+)?zijn\b",
        r"\bcampuscontract\b",     # by law a campus contract can only be given to a student
    ],
    "no_students": [
        rf"\bgeen\s+{_STUD}(?!\s+(?:te\s+zijn|bent|zijn|nodig|hoeft|hoeven)\b)",
        rf"\bgeen\s+[\w-]+\s*(?:,|of|en|/)\s*{_STUD}(?!\s+(?:te\s+zijn|bent|zijn|nodig|hoeft|hoeven)\b)",   # "geen huisdieren of studenten"
        rf"\bniet\s+(?:geschikt|bedoeld|beschikbaar|mogelijk|toegestaan)\s+voor\s+(?:[^.!?;:()]{{0,40}}?[\s,/])?{_STUD}",
        rf"\bniet\s+voor\s+{_STUD}",
        rf"\b{_STUD}\s+(?:zijn\s+|is\s+|helaas\s+|are\s+)*(?:niet|not)\s+"
        r"(?:toegestaan|welkom|mogelijk|geaccepteerd|allowed|welcome|accepted|permitted|eligible)\b",
        r"\bno\s+(?:\w+\s*(?:,|or|and|/)\s*)?students\b",
        rf"\bnot\s+(?:suitable|available|meant|intended|possible|allowed)\s+for\s+(?:[^.!?;:()]{{0,40}}?[\s,/])?{_STUD}",
        rf"\bnot\s+for\s+{_STUD}",
        r"\b(?:geen|no)\s+(?:studentenhuis|studentenwoning|student\s+house)\b",
    ],
    "students_ok": [
        rf"\b(?:geschikt|ideaal|perfect|bedoeld)\s+voor\s+{_GAP}{_STUD}",
        rf"\b(?:suitable|ideal|perfect|great)\s+for\s+{_GAP}{_STUD}",
        rf"\b{_STUD}\s+(?:zijn\s+|are\s+)?(?:ook\s+|also\s+|van\s+harte\s+)?(?:welkom|welcome|toegestaan|allowed)\b",
        rf"\b(?:apartment|appartment|flat|house|housing|home|woning|appartement|huis|woonruimte)\s+(?:for|voor)\s+{_STUD}",
    ],
    "working_only": [
        rf"\b{_ONLY_NL}\s+{_AVAIL_NL}(?:voor|aan)\s+{_COUNT}{_WORK_NL}",
        rf"\b{_ONLY_EN}\s+{_AVAIL_EN}(?:for|to)\s+{_COUNT}{_WORK_EN}",
        rf"\b{_WORK_EN}\s+only\b",
        r"\bmax(?:imaal|imum|\.)?\s*\d\s+werkende?\s+(?:persoon|personen)\b",
        r"\bmax(?:imum|\.)?\s*\d\s+working\s+(?:person|persons|people)\b",
        r"\bwerkende?\s+(?:huisgeno\w+|huurders?|persoon|personen|stel|koppel)\s+gezocht\b",
        r"\b(?:must|need\s+to|have\s+to)\s+(?:be\s+employed|have\s+(?:a\s+)?"
        r"(?:full[- ]?time\s+|permanent\s+|steady\s+)?(?:job|employment))\b",
    ],
    "women_only": [
        rf"\b{_ONLY_NL}\s+{_AVAIL_NL}(?:voor|aan)\s+{_COUNT}(?:vrouwen|dames|meiden|meisjes|vrouwelijke\s+\w+)\b",
        rf"\b{_ONLY_EN}\s+{_AVAIL_EN}(?:for|to)\s+{_COUNT}(?:females?|women|girls|ladies|female\s+\w+)\b",
        r"\b(?:females?|women|girls|ladies|vrouwen|dames)\s+only\b",
        r"\b(?:female|vrouwelijke)\s+(?:tenants?|students?|huurders?|studenten?)\s+only\b",
    ],
    "men_only": [
        rf"\b{_ONLY_NL}\s+{_AVAIL_NL}(?:voor|aan)\s+{_COUNT}(?:mannen|heren|jongens|mannelijke\s+\w+)\b",
        rf"\b{_ONLY_EN}\s+{_AVAIL_EN}(?:for|to)\s+{_COUNT}(?:males?|men|boys|guys|male\s+\w+)\b",
        r"\b(?:males?|men|boys|mannen|heren)\s+only\b",
        r"\b(?:male|mannelijke)\s+(?:tenants?|students?|huurders?|studenten?)\s+only\b",
    ],
    "no_sharers": [
        r"\bniet\s+(?:geschikt|bedoeld|beschikbaar|mogelijk|toegestaan)\s+(?:voor|om)\s+(?:[^.!?;:()]{0,40}?[\s,/])?"
        r"(?:woningdelers|woningdelen|delers|te\s+delen|woongroep(?:en)?|kamerbewoning|kamergewijze\s+verhuur|kamerverhuur)\b",
        r"\b(?:woningdelen|woningdelers|delers|kamerbewoning|kamerverhuur|woongroep(?:en)?)\s+"
        r"(?:is\s+|zijn\s+|helaas\s+|hier\s+)*(?:niet|not)\s+(?:toegestaan|mogelijk|welkom|geschikt|bespreekbaar)\b",
        r"\bgeen\s+(?:\w+\s*(?:,|of|en|/)\s*)?(?:woningdelers|woningdelen|delers|woongroep(?:en)?|kamerbewoning|kamerverhuur)\b",
        r"\bniet\s+voor\s+(?:woningdelers|delers)\b",
        r"\b(?:woningdelers|woningdelen|delers|delen)\s+(?:toegestaan|mogelijk|welkom)\s*[:=]\s*(?:nee|neen|niet)\b",
        r"\b(?:sharers|(?:house|home|flat)[- ]?sharing|sharing)\s+(?:allowed|possible|permitted|welcome)\s*[:=]\s*no\b",
        rf"\bnot\s+for\s+{_SHARERS_EN}\b",
        rf"\bno\s+(?:\w+\s*(?:,|or|and|/)\s*)?{_SHARERS_EN}\b",
        r"\bno\s+(?:\w+\s*(?:,|or|and|/)\s*)?(?:house|home|flat|apartment)[- ]?sharing\b",   # "no students or house sharing"
        r"\bnot\s+(?:suitable|available|possible|allowed|meant|intended)\s+(?:for|to)\s+(?:[^.!?;:()]{0,30}?[\s,/])?"
        r"(?:(?:house|home|flat)[- ]?)?(?:sharers|sharing|share)\b",
        r"\b(?:(?:house|home|flat)[- ]?)?(?:sharing|sharers)\s+(?:is\s+|are\s+)?not\s+(?:allowed|possible|permitted|welcome|accepted)\b",
        # only a couple or a family may rent: friends who share are refused
        r"\b(?:alleen|enkel|uitsluitend)\s+(?:geschikt\s+|bedoeld\s+|beschikbaar\s+)?voor\s+(?:een\s+)?"
        r"(?:stel(?:len)?|koppels?|gezin(?:nen)?)\b",
        r"\b(?:couples?|famil(?:y|ies))\s+only\b",
        r"\bonly\s+(?:suitable\s+|available\s+)?for\s+(?:a\s+)?(?:couples?|famil(?:y|ies))\b",
    ],
    "sharers_ok": [
        r"\b(?:geschikt|ideaal|ideale\s+woning|perfect|bedoeld|beschikbaar|ontwikkeld|ontworpen|gebouwd)\s+(?:is\s+|zijn\s+)?voor\s+"
        rf"(?:[^.!?;:()]{{0,50}}?[\s,/])?{_SHARERS_NL}\b",
        rf"\b{_SHARERS_NL}\s+(?:zijn\s+|is\s+)?(?:ook\s+|van\s+harte\s+)?(?:toegestaan|welkom|mogelijk)\b",
        r"\bwoningdelen\s+(?:is\s+)?(?:ook\s+)?(?:toegestaan|mogelijk)\b",
        r"\b(?:suitable|ideal|perfect|available|developed|designed|built)\s+for\s+(?:[^.!?;:()]{0,50}?[\s,/])?"
        rf"{_SHARERS_EN}\b",
        rf"\b{_SHARERS_EN}\s+(?:are\s+)?(?:also\s+)?(?:welcome|allowed|accepted)\b",
        r"\b(?:house|home|flat)[- ]?sharing\s+(?:is\s+)?(?:allowed|possible|permitted)\b",
        r"\bomzettingsvergunning\s+(?:is\s+)?(?:aanwezig|verleend|afgegeven)\b",
    ],
    "housemates": [
        r"\bhuisgeno(?:o)?t(?:e|en|je|jes)?\s+gezocht\b",
        r"\b(?:room|house|flat)mates?\s+(?:wanted|needed)\b",
        r"\blooking\s+for\s+(?:a\s+|an\s+|one\s+|only\s+one\s+|two\s+|\d\s+)?(?:new\s+|nice\s+|clean\s+|tidy\s+)*(?:room|house|flat)mates?\b",
        r"\b(?:op\s+zoek\s+naar|zoeken|zoek)\s+(?:een\s+|één\s+|twee\s+|\d\s+)?"
        r"(?:nieuwe\s+|leuke\s+|gezellige\s+|rustige\s+|werkende\s+)*huisgeno\w+",
        r"\b(?:je|u|jij)\s+deelt\s+(?:hier\s+)?(?:dus\s+)?(?:de|het|dit|deze)\s+"
        r"(?:woning|appartement|huis|keuken|badkamer|woonkamer|douche|toilet)\b[^.!?;]{0,40}?\bmet\b" + _NOT_OWN_COMPANY,
        r"\bdeel\s+je\s+met\s+(?:\w+\s+){0,5}?"
        r"(?:huisgeno\w+|woningdelers?|medebewoners?|bewoners?|studenten|student|personen|persoon|anderen)\b",
        r"\bmet\s+(?:mij|ons|de\s+(?:eigenaar|eigenaresse|hospita|verhuurder|hoofdbewoner|huidige\s+bewoners?)|"
        r"(?:\w+\s+){0,3}?(?:huisgeno\w+|medebewoners?))\b[^.!?;]{0,30}?\bgedeeld\b",
        r"\bgedeeld\s+met\s+(?:mij|ons|de\s+(?:eigenaar|eigenaresse|hospita|verhuurder|hoofdbewoner)|"
        r"(?:\w+\s+){0,3}?(?:huisgeno\w+|medebewoners?|andere\s+(?:studenten|bewoners|huurders|personen)))\b",
        # "1 badkamer en 1 keuken, die gedeeld worden door 5 kamers", "te delen met een student in de kamer ernaast"
        r"\bgedeeld\s+(?:wordt\s+|worden\s+)?(?:door|met)\s+(?:de\s+)?(?:\d+|andere|overige|alle)\s+(?:andere\s+)?"
        r"(?:kamers|bewoners|huurders|studenten|personen)\b",
        r"\bte\s+delen\s+met\s+(?:een|één|de|\d+|twee|drie)\s+(?:andere\s+)?"
        r"(?:student(?:en)?|huisgeno\w+|medebewoners?|bewoners?|huurders?|personen|persoon)\b",
        # Rooms people live in. A shared garden, storage, entrance or laundry room says nothing about housemates.
        r"\b(?:gedeelde|gezamenlijke|gemeenschappelijke)\s+(?:woon)?(?:keuken|badkamer|douche|toilet|woonkamer|sanitair)\b",
        r"\b(?:shared|communal)\s+(?:kitchen|bathroom|shower|toilet|living\s?room)\b",
        r"\b(?:keuken|badkamer|douche|toilet|woonkamer)\s+(?:en\s+\w+\s+)?(?:is|wordt|worden|zijn)\s+(?:gedeeld|gezamenlijk)\b",
        r"\b(?:kitchen|bathroom|shower|toilet|living\s?room)\s+(?:and\s+\w+\s+)?(?:is|are|will\s+be)\s+shared\b",
        r"\byou\s+(?:will|would|'ll)\s+(?:be\s+)?shar(?:e|ing)\b[^.!?;]{0,60}?\bwith\b" + _NOT_A_PET + _NOT_OWN_COMPANY,
        r"\b(?:room|kamer)\s+in\s+(?:a|an|een)\s+shared\s+(?:apartment|house|flat|home)\b",
        r"\b(?:current|other|existing)\s+(?:room|house|flat)mates?\b",
        r"\b(?:andere|huidige)\s+huisgeno\w+",
        r"\b(?:\d|one|two|three|een|één|twee|drie)\s+(?:other\s+|andere\s+)(?:(?:room|house|flat)mates?|huisgeno\w+)\b",
        r"\bhospita(?:verhuur|kamer|contract)?\b",
    ],
}

# Age limits: (pattern, kind). Group 1 is the age; a range has groups 1 and 2.
# "older than 25" means from 26, "younger than 30" means up to 29. `ctx` patterns are loose wording
# ("vanaf 23 jaar") that counts only in a clause that also talks about people or age.
AGE_RULES: List[Tuple[str, str]] = [
    (r"\bminim(?:ale|um)\s*leeftijd\s*(?:van|is|:|=)?\s*(\d{2})\b", "min"),
    (r"\bleeftijd\s*(?:vanaf|minimaal|van\s+minimaal|min\.?|:)\s*(\d{2})\s*(?:jaar|\+)", "min"),
    (r"\b(\d{2})\s+jaar\s+(?:of|en)\s+ouder\b", "min"),
    # "55+ woning", "55-plus appartement": a home for that age. "30-plussers in de wijk" and "ook voor 65+ers
    # geschikt" describe people and limit nothing; "alleen voor 55-plussers" does.
    (r"\b(\d{2})\s?\+\s?(?:woning(?:en)?|appartement(?:en)?|(?:woon)?complex|seniorenwoning(?:en)?|housing|only)\b", "min"),
    (r"\b(\d{2})[- ]?plus[- ]?(?:woning(?:en)?|appartement(?:en)?|(?:woon)?complex|flat)\b", "min"),
    (r"\b(?:alleen|enkel|uitsluitend|only|exclusively)\s+(?:\w+\s+)?(?:voor|for)\s+(\d{2})\s?(?:\+|[- ]?plus)(?:s?ers)?(?![\w])", "min"),
    # "seniorenwoning voor 65-plussers"; "ook voor 65+ers geschikt" only says they are welcome too
    (r"(?<!\book\s)(?<!\balso\s)\b(?:voor|for)\s+(\d{2})\s?(?:\+|[- ]?plus)s?ers\b", "min"),
    (r"\bminimum\s+age\s*(?:of|is|:|=)?\s*(\d{2})\b", "min"),
    (r"\b(?:aged?|ages)\s+(\d{2})\s*(?:\+|and\s+(?:over|older|above|up)|or\s+(?:over|older|above))", "min"),
    (r"\b(\d{2})\s+years?\s+(?:old\s+)?(?:or|and)\s+(?:older|over|above)\b", "min"),
    (r"\bat\s+least\s+(\d{2})\s+years?\s+old\b", "min"),
    (r"\bouder\s+dan\s+(\d{2})(?:\s+jaar)?\b", "min+1 ctx"),            # "het pand is ouder dan 25 jaar"
    (r"\bolder\s+than\s+(\d{2})\b", "min+1 ctx"),
    (r"\bover\s+the\s+age\s+of\s+(\d{2})\b", "min+1"),
    (r"\bvanaf\s+(\d{2})\s+jaar\b(?!\s+(?:geleden|ervaring|oud\b))", "min ctx"),
    (r"\bmaxim(?:ale|um)\s*leeftijd\s*(?:van|is|:|=)?\s*(\d{2})\b", "max"),
    (r"\bmaximum\s+age\s*(?:of|is|:|=)?\s*(\d{2})\b", "max"),
    (r"\b(?:aged?|ages)\s+(\d{2})\s+(?:or|and)\s+(?:under|younger|below)\b", "max"),
    (r"\bup\s+to\s+(\d{2})\s+years?\s+old\b", "max"),
    (r"\bjonger\s+dan\s+(\d{2})(?:\s+jaar)?\b", "max-1 ctx"),
    (r"\byounger\s+than\s+(\d{2})\b", "max-1 ctx"),
    (r"\bunder\s+the\s+age\s+of\s+(\d{2})\b", "max-1"),
    (r"\b(?:tot|t/m|tot\s+en\s+met)\s+(\d{2})\s+jaar\b(?!\s+(?:geleden|ervaring))", "max ctx"),
    # "aged 26 and 28" names two people's ages, so "and" / "en" joins a range only after "between the ages of"
    (r"\b(?:leeftijd|age[sd]?)\s*:?\s*(\d{2})\s*"
     r"(?:-|–|tot(?:\s+en\s+met)?|t/m|to)\s*(\d{2})\b(?!\s*(?:m2|m²|uur|euro|%|min))", "range"),
    (r"\bbetween\s+the\s+ages\s+of\s+(\d{2})\s*(?:-|–|to|and)\s*(\d{2})\b", "range"),
    # "tussen 20 en 30 minuten fietsen" is no age: the years must be named
    (r"\b(?:tussen(?:\s+de)?|between)\s+(\d{2})\s*(?:-|–|and|en)\s*(\d{2})\s+(?:jaar|years?)\b"
     r"(?!\s+(?:geleden|ervaring|ago|(?:of\s+)?experience)\b)", "range"),
    (r"\b(?:van|from)\s+(\d{2})\s*(?:-|–|tot(?:\s+en\s+met)?|t/m|to)\s*(\d{2})\s+(?:jaar|years?(?:\s+old)?)\b", "range ctx"),
]
_AGE_CONTEXT = re.compile(r"leeftijd|jongeren|huurders?|bewoners?|kandidat|personen|student|starters|senior|tenant|"
                          r"\bage[sd]?\b|applicant|resident|occupant")

# The age of the building, the kitchen or the boiler: "de woning is tussen 20 en 30 jaar oud", "leeftijd van het pand".
_THING = (r"(?:pand|gebouw|woning|huis|appartement|complex|keuken|badkamer|cv[- ]?ketel|ketel|dak|vloer|building|house|"
          r"apartment|property|home|kitchen|bathroom|boiler|roof)")
_THING_AGE = re.compile(r"\bleeftijd\s+van\s+(?:het|de|dit|deze)\s+" + _THING + r"\b|"
                        r"\b(?:het|de|dit|deze|the|this)\s+" + _THING + r"\s+(?:is|was|zijn|werd|dateert|stamt)\b")
_PEOPLE = re.compile(r"jongeren|huurders?|bewoners?|kandidat|personen|student|starters|senior|tenant|applicant|resident|"
                     r"occupant|iemand|someone|people")
# "55+ woning": a home for that age. Next to "ook" ("wij verhuren ook 55+ woningen") or a younger group
# ("voor starters en senioren (55+ woning)") it is no limit on this home.
_AGED_HOME = re.compile(r"^\d{2}\s?(?:\+|[- ]?plus)[- ]?\s?(?:woning|appartement|(?:woon)?complex|seniorenwoning|housing|flat)")
_NOT_ONLY_AGED = re.compile(r"\b(?:ook|also|starters?|jongeren|studenten|students|gezinnen|families|young)\b")

# A group named right after "only for <group>": the home is open to both, so it is not "only" anything.
_OTHER_GROUP = (r"(?:professionals?|werkenden?|werkende\b|expats?|starters?|working\s+people|workers|candidates|kandidaten|"
                r"promovendi|phd|trainees?|jongeren|young\s+people|mensen|iedereen|anyone|everyone|"
                r"couples?|stellen|famil(?:y|ies)|gezinnen|graduates|afgestudeerden)")
# "... or <anything>" is a choice (interns are students: "only for students or interns" stays). After a comma or
# "and" it must name another group ("alleen voor studenten, geen huisdieren" stays a statement about students).
# A word between the two groups, but no denial: "alleen voor een stel, geen studenten" names no second group.
_GAP_WORD = r"(?:(?!(?:geen|niet|no|not|zonder|without)\b)[\w-]+\s+)"
_ALSO_WORKERS = re.compile(r"\s*\(?\s*(?:(?:/|&|\+|\bof\b|\bor\b)(?!\s*(?:\w+\s+)?(?:interns?|stagiaire?s?|stagelopers?)\b)|(?:,|\ben\b|\band\b)\s*" + _GAP_WORD + r"{0,3}?" + _OTHER_GROUP + r")")
# "... or <anything that is not working people>" is a choice too: "werkenden of gepensioneerden", "a permanent job
# or sufficient savings". "Werkenden of expats" still names working people only.
# ("Werkende persoon of stel" names a kind of household, not another occupation.)
_WORKING_WORD = (r"(?:werkend|working|employed|professional|expat|workers?\b|job\b|employment|baan\b|dienstverband|"
                 r"stel(?:len)?\b|koppels?\b|couples?\b|alleenstaand|single)")
_ALSO_STUDENTS = re.compile(r"\s*\(?\s*(?:(?:,|/|&|\+|\bof\b|\bor\b|\ben\b|\band\b)\s*" + _GAP_WORD + r"{0,2}?student|"
                            r"(?:/|\bof\b|\bor\b)(?!\s*(?:[\w-]+\s+){0,3}?" + _WORKING_WORD + r"))")
_ALSO_SHARERS = re.compile(r"\s*\(?\s*(?:,|/|&|\+|\bof\b|\bor\b|\ben\b|\band\b)\s*" + _GAP_WORD + r"{0,3}?"
                           r"(?:woningdelers|delers|sharers|vrienden|friends|studenten|students)")
# "alleen voor vrouwen of stellen": others may rent too
_ALSO_NOT_WOMEN = re.compile(r"\s*\(?\s*(?:/|\bof\b|\bor\b)(?!\s*(?:\w+\s+){0,2}?(?:vrouw|dame|meid|meisje|female|women|girl|lad(?:y|ies)))")
_ALSO_NOT_MEN = re.compile(r"\s*\(?\s*(?:/|\bof\b|\bor\b)(?!\s*(?:\w+\s+){0,2}?(?:man(?:nen)?\b|heren|jongens|males?\b|men\b|boys|guys))")
_FOLLOWED_BY = {"students_only": _ALSO_WORKERS, "working_only": _ALSO_STUDENTS, "no_sharers": _ALSO_SHARERS,
                "women_only": _ALSO_NOT_WOMEN, "men_only": _ALSO_NOT_MEN}
# A field answered with "no": "Woningdelers toegestaan: nee", "Students welcome: no".
_ANSWERED_NO = re.compile(r"\s*[:=]\s*(?:nee|neen|niet|no|not)\b")
# "Voor studenten werken wij met een campuscontract": a contract for the students among the tenants, not a rule
# that only students may rent.
_FOR_STUDENTS = re.compile(r"\b(?:voor|for|aan|bij|with|met)\s+(?:\w+\s+)?student")

# "You don't have to be a student", "ook als je geen student bent": about students, but no rule against them.
_NOT_REQUIRED = re.compile(r"\b(?:hoef|hoeft|hoeven|als\s+(?:je|u|jij)|if\s+you|"
                           r"(?:indien|wanneer)\s+(?:je|u|jij)\s+(?:[\w-]+\s+){0,2}?(?:student|studeert)|"
                           r"when\s+you\s+(?:are\s+)?(?:an?\s+)?(?:[\w-]+\s+)?stud(?:ent|y)|don['’]?t\s+(?:have|need)\s+to|"
                           r"do\s+not\s+(?:have|need)\s+to|need\s+not|no\s+need\s+to)\b")
# "alleen voor stellen", "couples only": the wording of no_sharers that a "niet"/"not" in front turns around.
_ONLY_START = re.compile(r"(?:alleen|enkel|uitsluitend|only|couples?|famil)")
# Shared with the neighbours, but no part of the home: says nothing about housemates.
_COMMON_AREA = re.compile(r"\b(?:binnen|dak|gemeenschappelijke\s+|gezamenlijke\s+)?(?:tuin|terras|binnenplaats|berging|"
                          r"fietsenberging|fietsenstalling|stalling|entree|hal|trappenhuis|portiek|wasruimte|waskeuken|"
                          r"wasmachineruimte|parkeer\w*|garage|lift|garden|courtyard|patio|terrace|roof\s?top|storage|"
                          r"entrance|hallway|staircase|laundry(?:\s+room)?|parking|bike\s+(?:shed|storage)|bicycle\s+\w+)\b")
# What makes a shared thing a sign of housemates: a room people live in, the word housemate itself, or the home
# as the thing that is shared ("je deelt de woning met"). "De tuin van deze woning deel je met de buren" has none.
_LIVING_SPACE = re.compile(r"\b(?:woon)?(?:keuken|badkamer|douche|toilet|wc|woonkamer|sanitair)\b|"
                           r"\b(?:kitchen|bathroom|shower|living\s?room)\b|huisgeno|(?:room|house|flat)mate|"
                           r"\b(?:deelt?|delen|share|sharing)\s+(?:hier\s+)?(?:dus\s+)?(?:de|het|dit|deze|the|this|an?)\s+"
                           r"(?:woning|appartement|huis|apartment|house|flat|home|place)\b")

# Statements that a denial in front, or inside, cancels. The "no_*" flags are denials themselves.
_GUARDED = ("students_only", "students_ok", "working_only", "women_only", "men_only", "sharers_ok", "housemates", "age")
_DENIAL_BEFORE = re.compile(r"(?:\b(?:niet|geen|not|no|never|nooit|zonder|without)|n['’]t)\s+(?:\w+\s+)?$")
_DENIAL_INSIDE = re.compile(r"\b(?:niet|geen|not|no|never|nooit|zonder|without)\b|n['’]t\b")
# Where a sentence or bullet ends. A full stop counts only before a new sentence: not in "max. 2 personen",
# "ca. 85 m2" or "o.a. een berging".
_CLAUSE_END = re.compile(r"[!?;•*|\n\r]|\s[-–—]\s|\.(?=\s+[A-ZÀ-Ý\"'“‘(\[]|\s*$)")
_ABBREV = re.compile(r"(?:\b(?:max|min|ca|incl|excl|evt|nr|approx|bijv|etc|tel|st|str|dhr|mevr|mr|mrs|dr|vs|no)|\b\w\.\w)$", re.I)

EVIDENCE_MAX = 160


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> Optional[Pattern[str]]:
    try:
        return re.compile(pattern)
    except re.error:
        return None   # a broken pattern from config.json never stops a run


def _ends(text: str, lo: int, hi: int) -> Iterable[Tuple[int, int]]:
    for m in _CLAUSE_END.finditer(text, lo, hi):
        if m.group(0) == "." and _ABBREV.search(text[max(m.start() - 8, 0):m.start()]):
            continue
        yield m.start(), m.end()


def clause(text: str, start: int, end: int) -> Tuple[int, int]:
    """The sentence or bullet that holds text[start:end]. `text` is in the page's own spelling."""
    left = 0
    for _, stop in _ends(text, 0, start):
        left = stop
    right = len(text)
    for begin, _ in _ends(text, end, len(text)):
        right = begin
        break
    return left, right


def evidence(raw: str, start: int, end: int, limit: int = EVIDENCE_MAX) -> str:
    """The clause around a match, in the page's own spelling, cut to `limit` characters around the match."""
    left, right = clause(raw, start, end)
    cut_left = cut_right = False
    if right - left > limit:
        spare = max(limit - (end - start), 0)
        new_left = max(left, start - spare // 2)
        new_right = min(right, new_left + limit)
        cut_left, cut_right = new_left > left, new_right < right
        # A cut never falls inside a word, and never inside the words that were matched.
        if cut_left and not raw[new_left - 1].isspace():
            gap = re.search(r"\s", raw[new_left:start])
            if gap:
                new_left += gap.end()
        if cut_right and not raw[new_right].isspace():
            gaps = [g.start() for g in re.finditer(r"\s", raw[end:new_right])]
            if gaps:
                new_right = end + gaps[-1]
        left, right = new_left, new_right
    out = re.sub(r"\s+", " ", raw[left:right]).strip(" ,:-–—")
    return ("…" if cut_left else "") + out + ("…" if cut_right else "")


def same_passage(a: str, b: str, shared: int = 40) -> bool:
    """True when two quotes are cut from the same passage: one holds the other, or they share a long run of text."""
    a, b = a.replace("…", "").strip(), b.replace("…", "").strip()
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if short in long_:
        return True
    return any(short[i:i + shared] in long_ for i in range(0, len(short) - shared + 1))


def denied_before(raw: str, low: str, start: int) -> bool:
    """Is the statement that starts at `start` turned around by a "niet" / "geen" / "not" right in front of it?"""
    left, _ = clause(raw, start, start)
    return bool(_DENIAL_BEFORE.search(low[left:start]))


def _denied(raw: str, low: str, start: int, end: int) -> bool:
    left, _ = clause(raw, start, end)
    return bool(_DENIAL_BEFORE.search(low[left:start]) or _DENIAL_INSIDE.search(low[start:end]))


def first_match(low: str, patterns: Iterable[str], flag: str = "", raw: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Span of the first statement one of `patterns` finds, after the checks that keep it honest.
    `raw` is the same text in its own spelling (same length), used to find sentence ends."""
    raw = raw if raw is not None and len(raw) == len(low) else low
    guarded = flag in _GUARDED
    follow = _FOLLOWED_BY.get(flag)
    best: Optional[Tuple[int, int]] = None
    for pat in patterns:
        rx = _compiled(pat)
        if rx is None:
            continue
        for m in rx.finditer(low):
            if "\n" in m.group(0):
                continue      # two lines of the listing ("Huisdieren: geen" / "Studenten welkom") are no statement
            if guarded and _denied(raw, low, m.start(), m.end()):
                continue
            # For no_sharers the choice only undoes "only for a couple ..." wording ("alleen voor een stel of
            # woningdelers"); "geen woningdelers, geen studenten" refuses both.
            if (follow is not None and (flag != "no_sharers" or _ONLY_START.match(low, m.start()))
                    and follow.match(low, m.end())):
                continue
            if guarded and _ANSWERED_NO.match(low, m.end()):
                continue
            left, right = clause(raw, m.start(), m.end())
            if flag == "students_only" and m.group(0) == "campuscontract" and _FOR_STUDENTS.search(low[left:m.start()]):
                continue
            if flag == "no_students" and _NOT_REQUIRED.search(low[left:m.start()]):
                continue
            if flag == "students_only" and _NOT_REQUIRED.search(low[left:m.start()]):
                continue
            if flag == "no_sharers" and _ONLY_START.match(low, m.start()) and _DENIAL_BEFORE.search(low[left:m.start()]):
                continue
            if flag == "housemates" and _COMMON_AREA.search(low, left, right) and not _LIVING_SPACE.search(low, left, right):
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), m.end())
            break
    return best


# Ages of the people who live there now ("the current roommates are aged 26 and 28", "huisgenoten tussen 22 en 28
# jaar") are no limit for the new tenant, unless the sentence is about who is wanted.
_RESIDENTS = re.compile(r"\b(?:huisgeno\w+|medebewoners?|(?:room|house|flat)mates?|huidige\s+(?:bewoners?|huurders?)|"
                        r"current\s+(?:tenants?|residents?|occupants?)|wij\s+zijn|we\s+are|ik\s+ben|i\s+am)\b")
_WANTED = re.compile(r"\b(?:zoek\w*|gezocht|wanted|looking\s+for|seeking|search\w*|nieuwe|new|voorkeur|prefer\w*|"
                     r"ideal\w*|idea+l\w*|moet\w*|must|should|graag)\b")


def _ages(raw: str, low: str, extra: Iterable[Any]) -> Tuple[Optional[Tuple[int, Tuple[int, int]]], Optional[Tuple[int, Tuple[int, int]]]]:
    mins: List[Tuple[int, Tuple[int, int]]] = []
    maxs: List[Tuple[int, Tuple[int, int]]] = []
    rules = list(AGE_RULES)
    for item in extra or []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item):
            rules.append((item[0], item[1]))
    for pat, kind in rules:
        rx = _compiled(pat)
        if rx is None:
            continue
        for m in rx.finditer(low):
            span = (m.start(), m.end())
            if "\n" in m.group(0) or _denied(raw, low, *span):
                continue
            left, right = clause(raw, *span)
            if _RESIDENTS.search(low[left:m.start()]) and not _WANTED.search(low[left:m.start()]):
                continue
            thing = None
            for thing in _THING_AGE.finditer(low, left, m.end()):
                pass                                          # the last thing named before the number
            if thing is not None and not _PEOPLE.search(low[thing.end():m.start()]):
                continue
            if _AGED_HOME.match(m.group(0)) and _NOT_ONLY_AGED.search(low[left:right]):
                continue
            if "ctx" in kind:
                if not _AGE_CONTEXT.search(low[left:right]):
                    continue
            try:
                a = int(m.group(1))
                b = int(m.group(2)) if kind.startswith("range") else None
            except (IndexError, ValueError, TypeError):
                continue
            if re.search(r"(?:€|\beur(?:o)?)\s*$", low[max(0, m.start(1) - 6):m.start(1)]):
                continue                                      # "€ 65 plus € 120": money, not an age
            if kind.startswith("range"):
                if b is None or not a < b:
                    continue
                mins.append((a, span))
                maxs.append((b, span))
            elif kind.startswith("min"):
                mins.append((a + 1 if kind.startswith("min+1") else a, span))
            else:
                maxs.append((a - 1 if kind.startswith("max-1") else a, span))
    # An adult's age (18 and under) is no limit worth a flag; implausible numbers are misreadings.
    mins = [x for x in mins if 19 <= x[0] <= 80]
    maxs = [x for x in maxs if 18 <= x[0] <= 99]
    lo = max(mins, key=lambda x: x[0]) if mins else None     # the strictest of each
    hi = min(maxs, key=lambda x: x[0]) if maxs else None
    if lo and hi and lo[0] > hi[0]:
        return None, None                                     # contradicting numbers: say nothing
    return lo, hi


def settle(names: Dict[str, Any]) -> None:
    """Remove flags that contradict each other or are made redundant by a stricter one (in place)."""
    if "students_only" in names and ("working_only" in names or "no_students" in names):
        for k in ("students_only", "working_only", "no_students"):
            names.pop(k, None)                                # two statements that cannot both be true
    if "students_only" in names or "no_students" in names:
        names.pop("students_ok", None)                        # the stricter statement stands
    if "no_sharers" in names:
        names.pop("sharers_ok", None)                         # "niet geschikt voor woningdelers" holds the positive phrase
    if "women_only" in names and "men_only" in names:
        names.pop("women_only")
        names.pop("men_only")


def read(raw: str, extra_rules: Optional[Dict[str, Any]] = None,
         phrases: Optional[Dict[str, Any]] = None) -> Tuple[List[str], Dict[str, str]]:
    """(flags, evidence per flag name) for one listing's title + description.
    `phrases` are config.json's phrase lists for these flags ("flags": {"no_sharers": ["geen woningdelers", ...]}).
    They are whole-word phrases and pass the same checks as the rules here: "je hoeft geen student te zijn",
    "niet uitsluitend voor een stel" and "geen omzettingsvergunning aanwezig" set nothing."""
    raw = raw or ""
    low = raw.lower()
    if len(low) != len(raw):          # a few letters change length when lowercased: quote the lowercase then
        raw = low
    extra_rules = extra_rules if isinstance(extra_rules, dict) else {}
    phrases = phrases if isinstance(phrases, dict) else {}
    found: Dict[str, Tuple[int, int]] = {}
    for flag, patterns in RULES.items():
        more = [p for p in (extra_rules.get(flag) or []) if isinstance(p, str)]
        listed = phrases.get(flag) if isinstance(phrases.get(flag), (list, tuple)) else []
        more += [r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)" for p in listed if isinstance(p, str) and p]
        span = first_match(low, list(patterns) + more, flag, raw)
        if span:
            found[flag] = span
    settle(found)
    flags = list(found)
    ev = {flag: evidence(raw, s, e) for flag, (s, e) in found.items()}
    lo, hi = _ages(raw, low, extra_rules.get("age") or [])
    if lo:
        flags.append("min_age:%d" % lo[0])
        ev["min_age"] = evidence(raw, *lo[1])
    if hi:
        flags.append("max_age:%d" % hi[0])
        ev["max_age"] = evidence(raw, *hi[1])
    return flags, ev
