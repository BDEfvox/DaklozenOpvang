# House Hunter: design spec

Personal alert tool: 2–3 friends looking for a whole rental home in **Den Haag, Amsterdam, Leiden, Delft or Rotterdam**.
It runs on **GitHub Actions** every ~10 minutes, sends new matches to a **Telegram group**, and
publishes a **dashboard** on GitHub Pages (public repo).

## Hard rules (do not break)
- Be polite: at most ~3 requests per site per city per run, ≥2 s between requests to the same host
  (`hunter.http.Http` does this), respect robots.txt `Disallow` paths.
- Never contact landlords automatically. Alerts include a reply template the user sends by hand.
- No secrets in the repo. Secrets come from environment variables (GitHub Actions secrets, or a
  local `.env`, which is gitignored).
- Code must run on **Python 3.9** (local Mac) and 3.12 (Actions): use `from __future__ import annotations`,
  `typing.Optional/List/Dict`, no `match`, no `X | Y` at runtime.
- Dependencies: `requests`, `beautifulsoup4` only (plus `pytest` for tests). Use `html.parser`, not lxml.

## Layout
```
hunter/
  models.py          Listing dataclass                      (core, done)
  http.py            polite HTTP client, block detection     (core, done)
  pagereader.py      walls (status + challenge text) and a page's own summary (JSON-LD, meta
                     description), ported from a browser-based page reader. Stdlib only; another
                     project uses a byte-identical copy: change the copies together.
  utils.py           price/int parsing, city normalisation   (core, done)
  sources/base.py    Source interface + exceptions           (core, done)
  sources/__init__.py auto-discovers every module in sources/ (core, done)
  sources/<site>.py  one module per site, defines SOURCE = <Source instance>
  config.py          loads config.json
  filters.py         decides matches, tiers, flags
  tenancy.py         who may rent: students / working people / age / sharers / housemates, read from the
                     listing's text, with the sentence that says so
  state.py           seen listings, source health, daily digest bookkeeping
  notify/telegram.py Telegram Bot API (sendMessage), quiet hours, rate limit
  occupancy.py       what the text says about capacity, free places and a rent for one room
  geo.py             where a home is: PDOK Locatieserver lookups through http.py, cached in data/geo.json
  dashboard.py       writes docs/data.json (the page in docs/ reads it)
  __main__.py        CLI: run | check | telegram-setup | dashboard
config.json          searches, cities, flags phrases, schedule options
data/                state (committed by the workflow): seen.json, matches.json, health.json, meta.json, geo.json
docs/                dashboard (GitHub Pages from main:/docs): index.html, app.js, style.css, data.json,
                     vendor/leaflet (Leaflet 1.9.4, unmodified)
.github/workflows/hunt.yml
go-live.sh           one-time setup the USER runs (gh login, Telegram token, repo, secrets, Pages)
telegram-relay/     Cloudflare Worker for subscribers (invite link): worker.mjs, deploy.sh (the USER runs it), tests
tests/
```

## Listing contract (`hunter/models.py`)
Sources return `Listing` objects. `city` is a slug: `den-haag` | `amsterdam` | `leiden` | `delft` | `rotterdam`.
`price_total` is the monthly rent in whole euros **as listed**; set `price_includes_utilities` when the
site says so. `rooms` is the Dutch "kamers" count (includes the living room); `bedrooms` is
"slaapkamers". Fill what the site gives; leave the rest `None`. `uid = "<source>:<source_id>"` must be
stable across runs.

## Source contract (`hunter/sources/base.py`)
- `fetch(city, http) -> List[Listing]`: newest listings for that city, pre-filtered on the site where
  the site supports it (rent ≤ 2400, whole homes, not rooms). One search page per city is enough;
  2 at most.
- `fetch_details(listing, http) -> Listing` (optional): called only for NEW listings that pass the
  filters, at most ~10 per run, to fill `description` (used for flags) and missing fields.
  The description keeps the listing's own line breaks: `clean_text(text, keep_lines=True)`, and for HTML
  `utils.block_text(element)`, which breaks the line at `<br>` and around paragraphs and list items (not at every
  `<strong>` or link). The flag rules never read two lines as one sentence, so a list such as "Huisdieren: geen" /
  "Studenten welkom" is not read as "geen studenten". Text from alert emails stays on one line (mail wraps lines
  in the middle of a sentence).
- **Who may rent, from the site's own fields.** A source may put what the site states as data into
  `listing.extra`; `filters.detect` turns it into flags. Scalars only. Set a key only when the saved pages show
  the field's value next to the label the site prints for it; an id whose label was never seen sets nothing.
  - `site_students_only`, `site_working_only`, `site_no_students`, `site_students_ok`, `site_no_sharers`,
    `site_sharers_ok`, `site_housemates`, `site_women_only`, `site_men_only`: `True`.
  - `site_min_age`, `site_max_age`: int. `max_persons`: int. `registration_possible`: `False`.
  - `max_stay_months`: int. `price_per_person`: `True` (the listed rent is for one person).
  - `mandatory_costs`: int, a monthly cost that cannot be declined (a compulsory parking place), with
    `mandatory_costs_note`: the page's words for it. `service_costs`: int; with `service_costs_on_top: True` (and
    `service_costs_note`) when the page lists it next to the rent, so that it is paid on top (see "Service costs").
  - `site_facts`: up to 200 characters in the page's own labels ("Bezigheid: Student · Leeftijd: 23 - 30 jaar").
    It is shown to the user as the proof for every flag that came from these keys.
- Raise `SourceBlocked` for bot walls, `SourceError` for anything else. Never return a half-parsed
  page silently: if the page structure is not recognised, raise `SourceError("layout changed: …")`.
  Returning `[]` means "the site really shows no listings".
- A detail page without its usual description block may fall back on `base.summary_description`: the
  page's own JSON-LD (listing types only) or meta description, used only when the page's og:url or
  canonical URL is the listing's (a redirect to a search page describes other homes). It marks the
  listing `description_from_summary`, which the run log reports; with no summary it raises.
  Never parse "largest <main>/<article>" text: on listing sites that holds other listings.

## Matching (`filters.py`)
`config.json` holds `searches`. There is one: the household is two people.
- `2 people`: persons 2, min bedrooms 2, max total €1,600, "great" ≤ €1,400 (≤ €700 pp)

More searches can be added (a group of three with its own budget); the code and the tests handle several.

A listing matches a search when: city is one of the configured cities; property type is not
room/studio/parking/other; price known and ≤ max; bedrooms ≥ min. If the site gives no bedroom count, the
count stated in the title/description is used (`utils.bedrooms_from_text`: "Aantal slaapkamers: 2", "drie
ruime slaapkamers", "2-bedroom"). If only `rooms` is known, `rooms - 1` is assumed and the match is flagged
"bedrooms not confirmed" (a 4-room home often has 2 bedrooms). If both are unknown and the area is known,
area ≥ 45 m² (`min_area_if_unknown`) counts as "bedrooms unknown" match (flagged); otherwise no match. Result:
matched searches, € per person per search, tier (`great`/`ok`), flags.

**What the rent buys, and who can move in** (`filters.evaluate`, text rules in `occupancy.py`). Three things a
listing can state separately:
- *Rent basis.* `place`: the rent is per person or per room (`price_per_person`: "€ 700 p.p.", "per kamer", "je
  huurt één slaapkamer", or the site's field). `home`: the rent covers the whole home. Not settled (`None`): the
  home is shared with housemates and no per-person rent is stated, or the rent is suspiciously low for the home;
  it may be the rent of one room. "Shared with housemates" is `tenancy.py`'s flag `housemates`, which
  `occupancy.py` also sets when the text says that other people share the home, whether or not they live there
  yet: "gedeelde woning voor 3 bewoners", "shared house for 3 students", "met 2 huisgenoten", a place that is
  taken, and the wordings of a room in a home others live in ("kamer te huur", "room in shared flat", "wij zoeken
  een derde huisgenoot", "ieder huurt een eigen kamer", "eigen huurcontract per persoon"). So "Gedeelde
  huisvesting voor twee bewoners, € 966 per maand" without "per persoon" is shown as € 966 listed with "Rent basis
  unclear", never as € 483 each. A whole home that only mentions sharing ("ook voor een woongroep", "tegenover
  een studentenhuis", "de tweede huurder") is not such evidence.
- *Capacity* (`capacity:N`): how many people the home houses ("gedeelde huisvesting voor twee bewoners", "je deelt
  de woning met 2 huisgenoten" = 3). `max_persons:N` stays the listing's limit on who may move in.
- *Places free* (`places_free:N`): how many of those places can be rented now ("één van de twee plekken is al
  bezet", "1 kamer vrij", "wij zoeken een nieuwe huisgenoot", HousingAnywhere's `freePlaces`). A count from the
  text is taken as a count only where the listing shows that the home is let by the place: a rent per person or
  room, a shared home's stated capacity, a place that is taken, or the site's own field. Elsewhere ("nog 1 plek
  vrij" in an ordinary listing can be about a viewing) it becomes the flag `places_unclear`: a thing to ask, never
  a reason to drop a home. "Je kunt je samen met een toekomstig medebewoner aanmelden" is the flag
  `apply_together`; it does not say that both places are free, so it sets no count. "In totaal 2 bewoners" is a
  capacity and nothing more: a whole home for two is offered in the same words. A count the text only suggests
  ("de huidige bewoner" in a shared home for two) is `places_unclear` too, never a count; and a place "op de
  wachtlijst", "op de inschrijflijst" or at an open day is no place in the home. With only a rent per person to
  go on, a count of "plekken" or "spots" ("nog 1 plek vrij voor de rondleiding") is `places_unclear`; a count of
  rooms, a taken place and a wanted housemate are counts.

The rules that follow:
- Fewer free places, or a smaller capacity or limit, than the group: no match, whatever the rent.
- Rent for the whole home and all can move in: each pays an equal share (halves rounded up).
- Rent per place: each pays that amount; it is never divided, and never multiplied into a made-up total on the card.
- Not settled: the match stays for review with `price_pp` null and the flag `rent_unclear`; it is dropped only
  when the listed rent alone is over the budget. Tier is never "great". A source whose search page shows one
  amount for whole homes and single places alike (Van der Linden) sets `extra["rent_basis_unread"]` until the
  listing's own page has been read, so an alert sent before that read never shows a halved rent. On later runs
  the saved read of the page (the detail snapshot) answers the question, but only one made by the current
  `DETAIL_VERSION`: an older read did not look for it, and the mark stays until the page is read again.
- Rent per place: the site's service costs are paid by each person, a compulsory extra named in the text (a
  parking place) once for the home.
- Rent per place, shared with housemates, no count of free places: flag `places_unclear`.
- `price_pp` is the quoted rent each person pays. Costs that must be paid on top (service costs the site lists
  next to the rent, a compulsory extra) are `extra_pp`; the budget is held against the sum.
- `price_basis`: `excl` when such costs come on top or the listing says utilities are excluded, `incl` when it
  says utilities are included, else nothing. The card shows `€850 excl.`, `€850 incl.` or `€850`.

Every match gets its detail page read once: new matches first, then (with what is left of
`detail_fetch_max_per_run`) older dashboard cards that were never checked, bedroom guesses first. That
re-check sends no alerts; it only corrects or removes cards. A page that fails is tried 3 times.

Flags (case-insensitive phrase lists in config.json, Dutch + English), detected in title + description:
`no_sharers`, `max_persons:N`, `no_students`, `sharers_ok`, `no_registration`, `income_requirement`,
`short_term` (rental period < 12 months when dates are known, a site field, or wording such as "alleen short
stay" / "maximaal 6 maanden"), `utilities_included`,
`suspicious_price` (total < €700 for 2+ bedrooms, or less than `suspicious_rent_per_m2_below` = €6 per m² for 30 m² or
more: "€750, 200 m², 6 kamers" is almost certainly one room or a price per person), `scam_signals` (payment before viewing, landlord abroad,
keys by mail, western union, airbnb payment, whatsapp-only…). Flags never hide a listing by themselves; a
limit below the group size does, and so does what rules out the household (below).

`max_persons:N` is a plain fact ("Up to 2 people"), not a warning: for two people a limit of two changes nothing.
It used to show as a red "Max 2 persons" on every card whose site field or text gave a number, most often
Kamernet's own field "Aantal huurders: 2 personen". When the limit of two is worded for a couple ("alleenstaande
of stel", "single person or a couple") and the listing says nothing about sharers, the flag `sharing_unclear`
("Sharing policy unclear") says what to ask. The same flag, without a limit, is set when the home is offered
with a couple in mind ("ideaal voor een stel", "suitable for a couple") and nothing is said about sharers; "een
stel of twee vrienden" and "woningdelers welkom" leave nothing to ask. "Alleen voor een stel" / "couple only"
(`couple_only_patterns`) is `no_sharers`.

`age_unclear` ("Age limit unclear") marks wording that looks like an age condition and is no statement the age
rules accept: a bare "Vanaf 25 jaar", "25+", "tot 30 jaar", "under 30 years". It excludes nobody. It is set only
when no `min_age` or `max_age` was read, and not where the clause is about a contract, experience, the building,
a distance or children (`tenancy.age_hint`). "55+ woning" states an age; "een complex met 40+ appartementen"
counts homes and is only this question.

### Who may rent (`tenancy.py`)
Flags: `students_only`, `no_students`, `students_ok`, `working_only`, `women_only`, `men_only`, `min_age:N`,
`max_age:N`, `no_sharers`, `sharers_ok`, `housemates` (people already live there, or kitchen or bathroom is
shared). The dashboard filters on these, so the rules put accuracy before coverage:
- **Only a statement sets a restriction.** "Alleen voor studenten", "uitsluitend werkenden", "minimale
  leeftijd 23 jaar", "55+ woning". "Ideaal voor studenten" sets only the mild `students_ok` ("Students
  welcome"), which hides nothing; "populair bij expats" and "30-plussers in de wijk" set nothing.
- **Denials are read.** "Niet alleen voor studenten", "je hoeft geen student te zijn", "niet alleen voor
  stellen", "no shared kitchen", "geen huisgenoten" set nothing.
- **A choice is not a restriction.** "Alleen voor studenten of young professionals" and "must be a student or
  have a job" set neither flag.
- **Look-alikes are excluded.** "Tussen 20 en 30 minuten fietsen" and "€ 65 plus € 120" are no ages; "het pand
  is ouder dan 25 jaar" is none without people in the sentence; a shared garden, storage or entrance is no
  housemate.
- **Contradictions drop both sides.** Students only together with no students or working only: all dropped.
  Women only with men only: both dropped. A minimum age above the maximum age: both dropped.
- **Ages** count only from 19 to 80 (minimum) and 18 to 99 (maximum), so "18+" and house numbers set nothing.
- The site's own fields (`extra["site_*"]`, see the source contract) set the same flags. A site's age beats the
  age in the text.
  Kamernet's occupation list: only student kinds → students only; exactly "Werkend" → working only; no student
  kind → no students; "Student" among others → students welcome; "Werkende student" next to non-student kinds →
  nothing (it is not clear whether a full-time student may rent). Kamernet's gender id is believed only when the
  visible label agrees. HousingAnywhere: `tenantStatus` "any" → students welcome; `maxAge` 16 to 99; `freePlaces`
  → `max_persons`; costs that are required, monthly, paid by the tenant, not an estimate and not a utility bill
  → `mandatory_costs`.
- The phrase lists in config.json (`flags.no_sharers`, `flags.no_students`, `flags.sharers_ok`) go through the
  same checks as the built-in rules (`tenancy.read(..., phrases=...)`): "je hoeft geen student te zijn", "niet
  uitsluitend voor een stel" and "geen omzettingsvergunning aanwezig" set nothing. A field answered with no
  ("Woningdelers toegestaan: nee") sets the opposite flag.
- **A choice is no restriction**: "alleen voor werkenden of gepensioneerden", "a permanent job or sufficient
  savings", "alleen voor vrouwen of stellen", "alleen voor studenten (of starters)". "Werkenden of expats" and
  "werkende persoon of stel" still name working people only.
- **Not about the tenant**: the age of the building or the kitchen ("de woning is tussen 20 en 30 jaar oud",
  "leeftijd van het pand"), "wij verhuren ook 55+ woningen", a campus contract "voor studenten" (the students
  among the tenants get one), and "je deelt de woning met je partner" (no housemates).
- `tenant_rules` in config.json can add patterns per flag (`{"students_only": ["..."], "age": [["...", "min"]]}`);
  a broken pattern is ignored.
- The corpus of real and made-up phrases is `tests/test_tenancy.py`. Add the phrase there first when a rule
  changes.

**Evidence.** Every flag that comes from text carries the listing's own sentence (the clause around the match,
at most 160 characters, cut between words). A flag from site fields carries `site_facts`. `MatchResult.evidence`
maps flag name → sentence; it is saved on the card, sent in the alert (two sentences at most) and shown on the
dashboard, so every filterable claim can be checked at a glance. Two cuts of one long sentence are shown once.
Text from the user's mailbox decides flags but is never quoted or saved (`evidence` is `{}`).

**Rent per person** (`price_per_person`): set when the listing says the listed amount is per person ("de prijs
is per persoon", or the listed amount itself followed by "per persoon"), or by the source. The total for a
search is then rent × persons. Deposits and service costs "per persoon" do not count.

**Rent that depends on the group** (`rent_for_2:N`, `rent_for_3:N`, `rent_for_4:N`): "Huurprijs voor 3 personen
€ 2395,- ... Huurprijs voor 2 personen € 1995,-" on a listing of €1,995. The amount stated for a group replaces
the listed rent in that group's search (three people pay €2,395 here, not €1,995). Only an amount that differs
from the listed rent, lies between half and double of it, and is the only amount named for that group; not
when the rent is per person.

**Compulsory costs** (`mandatory_costs:N`): a monthly amount that cannot be declined, from the source or from
wording such as "verplichte afname van een parkeerplaats voor € 150 per maand". It is added to the total
before the budget test.

**Rooms against bedrooms.** `utils.rooms_from_dutch_text` reads "tweekamerappartement" / "3-kamer appartement"
(Dutch only; never "slaapkamer"). When the site's bedroom count is not below that room count, the site's
number is the room count: bedrooms become rooms − 1 and the match is marked "bedrooms not confirmed".

**The household**: `{"persons": 2, "occupation": "students" | "working" | null, "ages": [..], "sharers": true}`.
A home whose own flags exclude the household is not matched and not alerted: `no_students` or `working_only` for
students, `students_only` for working people, a minimum age above the youngest, a maximum age below the oldest,
`no_sharers` when `sharers` is true (people who rent together without being a couple or a family). A home that
says nothing, or says it unclearly, is never excluded. The repository is public, so the real values need not be
in `config.json`: `config.load_config` takes the household from the `HOUSEHOLD` environment variable (a JSON
object; the workflow passes the repository secret of that name to the Hunt step only, because a variable would
show in the public run logs) or from a git-ignored `household.json`, and
only then from `config.json`. A value that cannot be read excludes nobody. `docs/data.json` carries only
`household.persons`. Two machines write the same state (the scheduled run and the backup run at home): a run
that has no private household while the one before it had one would bring ruled-out homes back and alert them
as new. `data/meta.json` therefore keeps `household_private` (true once a run had one, never the values). A
run without one then prints a `[household]` line to its log and sends no alert for a home that was known and did
not match before; a home it sees for the first time is alerted as always. To stop using a private household for
good, remove that key from `data/meta.json`.

**Remembering what a detail page said.** The saved text is cut (card 4,000 characters, snapshot 2,500; the
reading limit is 20,000), so the flags and sentences found in the full text are stored in the snapshot. On
later runs they are passed back into `evaluate(listing, cfg, remembered=...)`, so they count for matching as
well as for display: a "geen woningdelers" from the end of a long text keeps the three-person match out. Only
a snapshot of the current `DETAIL_VERSION` is believed; what an older version remembered is ignored until the
page has been read again.
`runner.DETAIL_VERSION` is raised when the readers learn something new: every card whose page was read by an
older version is read once more (within the normal allowance per run), without alerts. It was 3 after the page audit of
19 Nov 2026 (see CHANGELOG, "Checked against the pages") and is 4 since the readers look for what the rent buys
and how many places are free.

**Saved cards follow today's rules.** At the end of every run `runner.rejudge_cards` judges each card whose
listing was not on a search page in that run again, from what was saved (no request, no alert). A correction
to the matcher therefore reaches every card in the next run; a card that no longer fits is removed. A card
made from mailbox text, whose text was never saved, is judged again from its own saved flags. A card of a source
with `rent_basis_from_page` (Van der Linden) whose page was not read by the current `DETAIL_VERSION` is judged
with the rent basis open: its rent stands as listed until the page is read.
What was saved can itself be an old misreading (an id stored as "max 8 persons"). So before that step,
`runner.reread_lost_cards` reads the detail page once more for cards that have left the search pages and whose
page was last read by an older `DETAIL_VERSION`: with what is left of the run's detail allowance, only for
sites that answered in this run, three tries at most, no alerts. A page that cannot be read leaves the card as
it was.

**Not a home.** A listing without a type, bedrooms or rooms whose first words name something else gets type
`other` (`filters.infer_type`) and never matches: either the thing is named as what is for rent ("Opslagruimte
van circa 48 m2 te huur", "Te huur: garagebox"), or the text names no part of a home anywhere. A home whose
text opens with "Parkeerplaats voor de deur" stays a home.

**A price that the source reads differently.** A source may set `extra["price_basis"]`. When it differs from
the one saved with the listing, the new number is not announced as a price drop. HousingAnywhere prices per
month of the year: `price_total` is the page's headline "From €X" (stable), `extra["price_first_month"]` and
`price_max` hold the rest, and the flag `price_varies:N` says "Rent depends on the month, up to €N".

**One person in a list.** "Geschikt voor alleenstaanden, stellen en kleine gezinnen" is a list of who the
home suits, not a limit: with families or house sharers in the sentence ("een alleenstaande, een stel of
woningdelers") it sets no `max_persons`, with couples only it sets 2. "Niet alleen voor stellen" sets nothing.
The same holds for a number and for a couple: "geschikt voor 2 personen of een gezin" and "alleen voor een stel
of woningdelers" set nothing, "2 personen of 3 woningdelers" sets 3. A denial up to three words in front
("niet geschikt voor 1 persoon") cancels one-person wording, and "een derde kamer voor een persoon" in a home
with two or more bedrooms describes a room.

**Service costs** (`service_costs:N`, "Plus €N service costs a month"). MVGM, Vesteda, Heimstaden and 123Wonen
show the rent and the service costs as two lines; Rotsvast shows a "Totale huur" above its "Kale huur". Those
sources set `service_costs_on_top`, and the amount is then added to the total before the budget test and in the
price per person, like a compulsory parking place. Checked on 19 Nov 2026: MVGM's text for Randveen 808 says
"De maandelijkse huurprijs bedraagt € 1.333,55", which is its rent of € 1.248,55 plus € 85 service costs. An
amount found in running text only (Van der Linden) is stored but not counted, because the page does not say
whether the rent includes it.

**Short stay.** `short_term` is set by dates less than 350 days apart (a year less a day, "1 nov 2026 tot
31 okt 2027", is a one-year contract), by `max_stay_months` < 12, or by wording: a named maximum period,
"available for a temporary period of 2 months", "available for 1 to 6 months", or two dates in the text next
to a word about availability ("Beschikbaar vanaf 16 december t/m 28 februari"). Viewing days ("bezichtigingen
van 3 tot 5 december") are not a rental period.

**Utilities.** `filters.infer_utilities` reads the text ("inclusief gas, water en elektra", "exclusief g/w/e",
"utilities are not included"). It decides when the site says nothing. When text and site field disagree, or
the text says both, `price_includes_utilities` becomes unknown: nothing is claimed. An advance ("voorschot
inclusief gas, water, elektriciteit") is paid on top.

**Ages of the people who live there** ("the current roommates are aged 26 and 28", "huisgenoten tussen 22 en
28 jaar") are no limit, unless the sentence is about who is wanted ("wij zoeken een huisgenoot tussen 22 en 28").

**Bedrooms described one by one** with their sizes are added up ("Slaapkamer van 13 m². ... Drie slaapkamers
van 10 m², 9 m², 6 m²" is four), per language, each mention once. Bedrooms the home had or could have are not
counted ("voorheen 2 slaapkamers", "mogelijkheid tot 2 slaapkamers", "geen 2 slaapkamers maar 1"), and
"Bedrooms: 3 rooms in total" is no bedroom count.

`income_requirement` has two more inputs besides its phrase list. (1) `income_patterns` in config.json:
regexes for the many wordings ("inkomensrichtlijnen", "bruto maandsalaris 3,5x keer de huurprijs", "proof of
income", payslips asked for). A multiple of the rent counts on its own only from 2.5x up, because deposits are
worded the same way. (2) A source with `income_requirement = True` (Vesteda, MVGM, Van der Linden, Rotsvast,
Heimstaden) applies an income test to every home under its own published conditions, so all its listings get
the flag even when the listing text is silent.

## State (`state.py`, files in `data/`)
- `seen.json`: `{uid: {first_seen, last_seen, last_price, matched: [search names], notified_at}}`.
  Prune entries not seen for 45 days.
- `health.json`: per source `{status: ok|blocked|error, last_ok, last_error, error, consecutive_failures,
  alerted}`.
- `meta.json`: `last_run`, `last_digest_date`, `runs_today`, counters.
- Write files only when content changed (ignore pure timestamp changes) or when the last write is
  older than 60 minutes (heartbeat). Keeps commits low.

## Notifications (`notify/telegram.py`)
Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Missing → print messages to stdout (dry run).
- New match: HTML message. Title line with tier emoji, city, total rent + € pp for each matching group
  size, bedrooms/rooms/m², furnished, available from, flags as short warnings, up to two of the listing's
  own sentences behind those flags (`💬`), source label, link.
  An inline-keyboard button "Open listing". An expandable blockquote with the Dutch reply template
  from config.json.
- Price drop into budget for a seen listing → "price drop" message.
- First run of a source (no seen entries for it yet): send at most 10 best matches + one summary line
  with the dashboard link, mark the rest seen.
- Max 15 messages per run, ≥3 s apart (Telegram group limit is 20/min); overflow → one summary message.
- Quiet hours 23:00–07:00 Europe/Amsterdam: `disable_notification=true`.
- Source health: alert once when a source has failed 6 runs in a row; once when it recovers.
- Daily digest at the first run after 09:00 Amsterdam time: yesterday's new matches per city, source
  status, dashboard link.

## Dashboard (`docs/`)
Static page (no build step) reading `data.json`. Plain HTML/CSS/JS, system fonts, light and dark.
- **Listings:** one list of the matches from the last 30 days, newest first. A card leads with the price each
  person pays as a short label (`€850 incl.`, `€850 excl.`, `€850`; `€1.700 listed` when `price_pp` is null;
  "Price unknown"), then the title and address, one facts line (bedrooms, m², "from 1 Dec" / "now" /
  "20 Dec to 10 Jan", "Furnished"), the marks to review (`warn`, `check`,
  `good`: icon + text), a "Details" disclosure with the plain facts (`info` marks) and the listing's own
  sentences, and across the foot of the card "Show on Map" (for a home with a known place; from the list it
  opens the map on that home), "Hide Listing" and "Copy Reply". A
  title from an alert email ("Pararius: Maria Snelplantsoen") is shown without the site's name. No source name, no arrow: the whole card is the link. With one search
  (`searches` has one entry) the Group Size control is gone and no group is named. A blue dot marks listings
  found in the last 24 h. Marks follow `flag_kinds` (`warn`, `good`, `info`, `check`; `check` is a grey
  question mark: "ask the landlord"), else the lists in `app.js`.
- **Hide Listing:** hides a home by `home_key` (else `uid`) in `localStorage["househunter.hidden.v1"]`
  (`{key: {t, a, at, u}}`; `u` is the hidden listing's id, which still finds the home when its key changes
  because a house number was read later); survives reloads, refreshes and the same home on another site. A bar offers Undo for 6 s.
  "Hidden Homes" (end of the Filters form, only when any) opens a sheet with "Show Again" / "Show All Again".
  Keys of homes no longer in the data are dropped 60 days after `at`. Hidden homes are not counted.
- **Map:** List | Map switch in the list head. Leaflet 1.9.4 is vendored in `docs/vendor/leaflet/` and loaded only
  when the map is first opened (all use goes through `window.L`, which the checks replace with a fake); if it
  fails the map area says so and the list works on. Tiles: PDOK BRT (`Kaartgegevens © Kadaster`). Markers are
  price pills (`?` when unknown); the selected one is bigger, blue, with a tail; a dashed edge means the point is
  only a street or postcode. Markers on one point are laid out as a small grid (pixel offsets). Markers = the
  listings that pass the filters and are not hidden, with a location. Selecting one: side by side (75em+) the list
  scrolls to its card and flashes it; otherwise a compact card with "Open Listing" shows over the map and the
  card is outlined (`data-picked`). "Search This Area" (appears after the user moves the map, not after code
  moves it) turns the map bounds into a filter on the list and count (chip "Map Area"; not saved, kept between
  List and Map; homes without a location are not inside it). The line under the map counts homes passing the
  filters that have no location. Layout: map 60% of the height under the search bar; on 75em+ side by side with a
  sticky map. Keyboard: markers are focusable with a label, "Skip the map" link, the list is the full equivalent.
- **Filters:** group size (2 People, 3 People, Any), cities, max € per person and your age; search and sort sit
  above the list. Then **What the Listing Says**: one row for each thing the checker reads from a listing
  (`ATTR_GROUPS` in `app.js`: who may rent, house sharing, rent and costs, home and contract, warnings), with the
  number of listings that say it and two buttons. **Only** keeps the listings that say it, **Hide** removes
  them; pressing the button that is on turns the filter off. Each row is a filter of its own and any number can
  be in use at once (they must all agree). A row is "said" by one flag, by any of several (`age_limit` =
  `min_age` or `max_age`; `any_warning` = the warning flags), or by a fact that is no flag (`furnished`,
  `price_drop`). Every row is always there, also at a count of 0: listings change every few minutes and a
  filter set now holds for the ones that come later. A home that says nothing about a thing is never removed by
  Hide. There is no "great prices only" filter: the budget slider does that.
  The filters in use stand above the list as chips ("Hide Short-Term Only ×"); a chip removes that one filter,
  and "Clear All" appears from two. On a narrow screen the filters are in a sheet behind the Filters button
  (Done keeps the changes, Cancel puts back what was set before). On a wide screen (60em and up) the same form
  sits in a side column; `app.js` moves it there. Filters are remembered in localStorage (`attrs`: key →
  `"only"` | `"hide"`); what the earlier version saved ("Who's Renting", the sharing switches, "hide
  warnings") is read as the same choices.
- **Proof:** under a row's marks stands the sentence of the listing (or the site's own fields) that set them,
  as a quote (`.quote`, plain text, at most three).
- **Reply:** the Reply button opens an editor for the text that "Copy Reply" copies. Chips insert a detail
  (`{title}`, `{address}`, `{city}`, `{price}`, `{bedrooms}`, `{area}`, `{source}`, `{url}`) at the cursor, a
  preview shows the text filled in for the first listing, and a detail a listing lacks is left out. The text is
  kept in this browser only (`localStorage["househunter.reply.v1"]`); "Use the Standard Text" goes back to
  `reply_template` from config.json. Telegram alerts always use `reply_template`.
- **Status:** the line under the title shows the last check and how many sources work; it opens the
  Sources sheet (one row per source: status icon and word, listings in the last check, note, error).
- **States:** stale check (> 2 h) banner, sample-data banner, "no matching listings" with Clear Filters,
  and "can't load listings" with Try Again.
- **Colour:** text is neutral (label, secondary, tertiary). Blue is the only accent and means "you can
  tap this" or "new". Status is never coloured text: it is a small filled symbol (green tick, red triangle,
  orange or grey circle) next to plain text. The one coloured label is the green "Great price" capsule.
  Every colour is a custom property at the top of `style.css` with light, dark and increased-contrast
  values; text pairs are at least 4.5:1.
- **Motion:** everything that moves runs on a spring and can be interrupted.
  - `spring()` in `app.js` is a small spring solver (duration, bounce, starting velocity). It drives what
    follows a finger: the group-size thumb and the sheets. CSS transitions use the same springs as
    `linear()` curves, with a `cubic-bezier` fallback for browsers without `linear()`.
  - Group size: the thumb slides to the touched choice at touch-down, can be dragged, and settles on the
    choice its speed carries it to.
  - Sheets open and close on a spring, can be dragged down by the top bar (or by the content when it is
    scrolled to the top), resist when pulled up, and close on a long or fast pull. The dimmed background
    (`#scrim`) follows the sheet. While a sheet closes, the page behind is already usable.
  - The list: when a filter changes, rows that stay slide to their new place, new rows fade in and removed
    rows fade out (`snapshot()` / `flip()`). Rows are reused per listing (`rowFor()`). While the
    budget slider moves, only the amount and the count follow it; the list animates once, when the slider
    pauses (120 ms) or is let go. Typing in search updates the list without animation.
  - Small feedback: buttons shrink while pressed and spring back, rows highlight at touch-down, the city
    tick draws in, the filter count pops, "Copy Reply" rolls to "Copied".
  - With Reduce Motion nothing slides or scales: sheets and rows fade, and sheets cannot be dragged.
- Checked by `tests/dashboard_check.js` (run through `tests/test_dashboard_js.py`).

## Workflow (`.github/workflows/hunt.yml`)
GitHub's `schedule` event is not used for timing: after go-live it did not fire at all for hours, and it is
"best effort" even when it works. Instead the runs form a chain, one short run per check:
- Step "Start the next run and keep the rhythm" (first step of the job): stop if the workflow is
  `disabled_manually`; stop if a newer chain run is already unfinished (ends a doubled chain; the newest chain
  run never stops for this reason); work out when this run's check is due (`after + INTERVAL_SECONDS`, 600);
  `gh workflow run hunt.yml -f chain=true -f after=<due>` with `GITHUB_TOKEN` (`actions: write`); sleep until
  due. Then checkout, Python 3.12, `python -m hunter run`, commit `data/` and `docs/data.json` if changed and
  push (rebase; if `data/` changed on GitHub meanwhile, this run's state wins). An error answer from GitHub's
  API never stops the chain. If the dispatch fails: retried for 15 more minutes after the check, then a
  Telegram warning.
- The successor first waits for this job to end (`concurrency: hunt` on the `hunt` job, which also makes sure
  two checks never overlap), then in the environment `hunt-timer` (wait timer 8 min, `deployment: false`),
  where GitHub holds it back without a runner. A run therefore occupies a runner for about 2 minutes: about
  1 minute of sleep, 1 minute of work. Other runs use `hunt-now` (no rules). `go-live.sh` creates both.
  Without the timer the run sleeps the whole interval on the runner and warns the group every six hours.
- Job `guard` (schedule and manual runs only): continue only when no chain run and no older starter run is
  unfinished; when the question fails, continue.
- Cron `7,37 * * * *` restarts a dead chain, if GitHub's schedule works. A runner lost in the first seconds
  of a job (before the dispatch) stops the chain silently: the dashboard's "checker may be down" warning and
  the Mac backup's watchdog are the signals; "Run workflow" restarts it.
- Stop: disable the workflow (the waiting run ends without a successor), or cancel the waiting run.
- Dry run (`dry_run=true`): one check, nothing sent or committed, no successor, own concurrency group.
- Only the steps that call GitHub's API get `GH_TOKEN`; the hunt step does not.

## Mac backup (`hunter/backup.py`, `local-backup.sh`)
Off by default; `./local-backup.sh on` writes a launchd job (every 10 min while awake). Mode `auto` reads the public
repo's `data/health.json`, `meta.json` and `seen.json` (raw.githubusercontent.com), runs only sources GitHub reports as
`blocked`/`error`, seeds local memory with GitHub's seen listings, warns once when GitHub's `last_run` is older than
`stale_hours` (and once on recovery), never sends a digest, and tags every message "💻 Mac backup". State in
`data-local/` (gitignored). Mode `all` runs every source (only without GitHub).

## Subscribers (`telegram-relay/`)
Optional. Other chats can receive the same alerts if they have the secret invite link. A Cloudflare Worker
(`telegram-relay/worker.mjs`, deployed by `telegram-relay/deploy.sh`, tests: `node --test telegram-relay/worker.test.mjs`)
receives the bot's webhook and keeps the subscriber list in Workers KV. The Worker never holds the bot token: it answers
Telegram by returning `sendMessage` as the webhook response. The list is never in this public repo.

Endpoints:
- `POST /telegram`: Telegram's webhook; header `X-Telegram-Bot-Api-Secret-Token` must equal `WEBHOOK_SECRET`
  (constant time; unset means 403). Body cap 64 KB; a malformed or oversized body gets 204 so Telegram does not retry.
  Handles, in private/group/supergroup chats only: `/start <code>`, `/stop`, `/status` (case-insensitive, `@botname`
  allowed); `my_chat_member` with new status `left` or `kicked` (chat removed); `message.migrate_to_chat_id` (group
  became a supergroup: the subscription moves to the new id). Anything else: 204, no reply.
  `/start`: code equal to `INVITE_CODE` (constant time, unset never matches) and fewer than `MAX_SUBSCRIBERS`
  (default 50; a soft cap, because the count is read from the same cached list: chats that subscribe within the
  same minute can take it a few over, and the checker itself sends to 200 at most) → the chat is written to the list and welcomed (with `DASHBOARD_URL`), or told it already gets the
  alerts; wrong or missing code → "already" for a subscribed chat, else one fixed "invite-only" reply; full → fixed reply.
  Workers KV answers reads from an edge cache for up to a minute after a change (seen in the live test of 19 Nov
  2026: `/stop` right after `/start` answered "not subscribed" and left the chat on the list). So `/start` with the
  right code always writes and `/stop` always deletes, whatever the read says; reads use `cacheTtl: 30`, the
  shortest KV allows; and the `/status` replies say that a change can take a minute to show.
- `GET /subscribers`: bearer `PULL_SECRET` → `{"chats": ["<id>", ...]}` (strings).
- `POST /subscribers/remove` `{"chat_id"}` and `POST /subscribers/migrate` `{"from", "to"}`: same bearer; ids must match
  `^-?\d{1,20}$`; 204.
- `POST /subscribers/reset` `{"confirm": "remove-all"}`: same bearer; deletes every `chat:` key and answers
  `{"removed": n}` (`deploy.sh --remove-all`, which first sets a new invite code). Anything else: 404.

Storage: KV binding `SUBSCRIBERS`, one key `chat:<id>`, value `{"type", "added_at"}`. Nothing else about a chat is kept (no
names, usernames, titles or message text). `deploy.sh` also keeps `RELAY_INVITE_CODE` (re-runs keep the same code),
`RELAY_URL` and `RELAY_PULL_SECRET` in the local `.env`, and sets `RELAY_PULL_SECRET` (secret) and `RELAY_URL`
(variable) on GitHub.

Python side: `notify/subscribers.py` (`RelayClient`: `list()` with 3 tries, `remove()`, `migrate()`; never raises, prints
no id or secret) and `BroadcastNotifier` in `notify/telegram.py`. `make_notifier` builds it when both `RELAY_URL` and
`RELAY_PULL_SECRET` are set; if the relay cannot be reached the run sends to the owner only. `send()` goes to the owner
first and returns the OWNER's result; subscribers (skipping the owner's own id) are best effort and only get the message
when the owner's send worked. A subscriber whose chat answers 403 / "chat not found" is reported to the relay
(`remove`), and a group that migrated to `migrate`.

Audience rule: `Notifier.send(..., audience="all"|"owner")`. Owner-only: source health alerts, recovery messages, the
first-ever status report, and the Mac backup's watchdog messages. Everything else (new match, price drop, overflow,
first-run summary, daily digest) is `"all"`.

Security: invite-only (secret code in the link, compared in constant time; an unset code never matches); every
reply is a fixed string, nothing a user typed is echoed, no `parse_mode`; chat ids are registered with `redact.add_secret`
and never logged; `observability` is off on the Worker (requests carry secrets); the bot token is not on Cloudflare; a
bounded list (50 chats). Anyone in a subscribed group can `/stop` it. While the webhook is set, `getUpdates` answers 409
(`telegram-setup` explains how to switch the relay off first).

## Sources
Bot walls seen on 9 Nov 2026 (from a home connection): Pararius, Huurwoningen.nl, Huurstunt (Cloudflare),
Funda (own bot check), Holland2Stay (403). → not scraped. Pararius, Funda and Huurwoningen.nl are covered via
email alerts (`sources/email_alerts.py` reads their alert emails); Huurstunt and Holland2Stay are not covered: the
email reader has no parser for their emails.
Open (HTTP 200 with data): Kamernet (`__NEXT_DATA__`), HousingAnywhere (`window.__PRELOADED_STATE__`),
Marktplaats (`/lrp/api/search`, category 2143 "Huizen te huur"), 123Wonen, DirectWonen, Interhouse,
Van der Linden, Vesteda (SPA). To check: ikwilhuren.nu (MVGM), wonenbijbouwinvest.nl, Heimstaden,
Rotsvast, vbtverhuurmakelaars.nl, rebowonenhuur.nl, Domica, HouseHunting, Duinzigt Wonen.
Avoid: Rentola (paywalled aggregator, many complaints).
Pages: 123Wonen sorts homes that are for rent first, so its page 2 and 3 are read only while every card on the
page before is still for rent and the page links to the next (`wonen123.more_may_follow`). HousingAnywhere's
page 2 is disallowed by its robots.txt and is not read.

## Dashboard data schema (`docs/data.json`, written by `hunter/dashboard.py`, read by `docs/app.js`)
```json
{
  "schema": 2,
  "generated_at": "2026-11-09T16:20:00Z",
  "last_run": "2026-11-09T16:20:00Z",
  "dashboard_url": "https://<user>.github.io/house-hunter/",
  "cities": {"den-haag": "Den Haag", "amsterdam": "Amsterdam", "leiden": "Leiden", "delft": "Delft", "rotterdam": "Rotterdam"},
  "searches": [
    {"name": "2 people", "persons": 2, "max_total": 1600, "great_total": 1400}
  ],
  "household": {"persons": 2},
  "flag_kinds": {"no_sharers": "warn", "sharing_unclear": "check", "sharers_ok": "good", "max_persons": "info"},
  "flag_labels": {"no_sharers": "No house sharers", "max_persons": "Up to {n} people", "no_students": "No students",
                  "sharers_ok": "Sharers welcome", "no_registration": "No registration possible",
                  "income_requirement": "Income requirement", "short_term": "Short-term only",
                  "utilities_included": "Utilities included", "suspicious_price": "Suspiciously cheap",
                  "scam_signals": "Possible scam", "bedrooms_unknown": "Bedrooms unknown",
                  "students_only": "Students only", "working_only": "Working people only",
                  "min_age": "Minimum age {n}", "housemates": "Shared with housemates",
                  "price_per_person": "Rent is per person or room", "capacity": "Shared home for {n} people",
                  "places_free": "Places free: {n}", "sharing_unclear": "Sharing policy unclear",
                  "places_unclear": "Free places unclear", "rent_unclear": "Rent basis unclear",
                  "mandatory_costs": "Compulsory extra €{n} a month",
                  "service_costs": "Plus €{n} service costs a month",
                  "rent_for_3": "€{n} a month for 3 people"},
  "reply_template": "Beste verhuurder, ... {title} ... {address} ... {url}",
  "sources": [
    {"name": "kamernet", "label": "Kamernet", "homepage": "https://kamernet.nl",
     "status": "ok", "last_ok": "2026-11-09T16:20:00Z", "last_error": null, "error": null,
     "note": "Paid account needed to reply", "listings_last_run": 17}
  ],
  "listings": [
    {"uid": "kamernet:2417430", "source": "kamernet", "source_label": "Kamernet",
     "url": "https://...", "title": "...", "city": "den-haag", "address": "Daltonstraat", "postcode": null,
     "price_total": 1450, "price_includes_utilities": false, "bedrooms": 2, "rooms": 3, "area_m2": 70,
     "property_type": "apartment", "furnished": "unfurnished", "available_from": "2026-12-01",
     "available_until": null, "image_url": "https://...", "first_seen": "2026-11-09T16:20:00Z",
     "last_seen": "2026-11-09T16:20:00Z", "price_drop_from": null, "best_tier": "ok",
     "matches": [{"search": "2 people", "persons": 2, "price_pp": 725, "extra_pp": 0, "tier": "ok"}],
     "flags": ["max_persons:2", "income_requirement"],
     "evidence": {"max_persons": "Geschikt voor maximaal 2 personen"},
     "home_key": "a:den-haag:daltonstraat:12", "rent_basis": "home", "price_basis": "excl",
     "capacity": 2, "places_available": null, "lat": 52.07123, "lon": 4.30123, "geo": "address"}
  ]
}
```
`status` is one of `ok | partial | blocked | error | disabled`. A flag may carry a value after a colon
(`max_persons:2`); `flag_labels` is keyed by the part before the colon and may contain `{n}`.
`evidence` maps a flag's name to the listing's own sentence (or the site's fields) behind it; a flag
without one has no entry.
`matches[].price_pp` is the quoted rent each person pays (null when the listing does not settle it) and
`extra_pp` what each must pay on top. `rent_basis` is `home`, `place` or null; `price_basis` is `incl`, `excl` or
null. `capacity` and `places_available` are numbers only when the listing states them. `home_key` names the home
for "Hide Listing": city, street and house number (`a:<city>:<street>:<number>`; "10-1" is not "101", a digit
in a street's name is no house number), else the listing's own id (`u:<uid>`). `lat` /
`lon` / `geo` (`address`, `street` or `postcode`: how exact the point is) come from `geo.py` and are null when
the address was not found; the map never guesses. `flag_kinds` says what a mark is: `warn`, `check` (the
listing leaves something open), `good` or `info`.
All strings in `listings` come from third-party sites: the page must render them with `textContent`,
never `innerHTML`.
