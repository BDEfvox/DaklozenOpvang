# Changelog

## v1.2.0 (not released yet)

### New
- **Who may rent.** The checker reads whether a home is only for students, only for working people, for women
  or men only, has a minimum or maximum age, refuses or welcomes house sharers, or is shared with housemates.
  It reads this from the listing text (Dutch and English) and from the site's own fields on Kamernet,
  HousingAnywhere, Vesteda, 123Wonen and Van der Linden. Only a statement counts as a restriction: "ideaal
  voor studenten" gives no more than the mild "Students welcome" mark, "niet alleen voor studenten" sets
  nothing, and two statements that contradict each other set nothing.
- **Proof for every mark.** Each of these marks, and each warning, carries the sentence of the listing that says
  so. It is shown under the listing on the dashboard and in the Telegram alert.
- **Dashboard filters, one per thing a listing says.** Every mark the tool reads (26 of them: students only, no
  students, sharers welcome, housemates, short-term, service costs, furnished, each warning, ...) has its own
  line with two buttons: **Only** shows the homes that say it, **Hide** removes them, a second tap turns it
  off. Any number can be used at once. The filters in use stand above the list, and each can be removed there
  on its own. This replaces the first design of this version ("Who's Renting: Students, Working, Any" and three
  sharing switches), where one choice hid several things at once and nothing could be filtered for. Choices
  saved with that design are kept. A home that says nothing is never removed by Hide.
- **Your own reply text.** The Reply button opens an editor for the text "Copy Reply" copies. Details such as
  the address and the rent are filled in for each listing.
- **Subscribers.** Other chats can get the alerts through a secret invite link (`telegram-relay/`, optional,
  off until `./telegram-relay/deploy.sh` is run). The link can be replaced and all subscribers removed.
- **Optional household profile** in `config.json`: homes that state they are not for you are not alerted.

- **For two people who rent together.** The tool now answers three questions for each home: can both move in,
  what does each pay a month, and what is still to be checked.
  - *Whole home or one place.* A rent for the whole home is split in two. A rent per person or per room is what
    each pays; it is never halved. When a listing does not settle which it is (housemates and no rent per person,
    or a rent far too low for the home), the card shows the listed rent and "Rent basis unclear" instead of a share.
    That includes a home the text calls shared ("gedeelde woning voor 3 bewoners", "shared house for 3 students",
    "met 2 huisgenoten", "kamer te huur in studentenhuis", "room in shared flat", "eigen huurcontract per persoon")
    that gives one amount without "per persoon".
  - *Capacity and free places* are read as two numbers ("gedeelde huisvesting voor twee bewoners" against "één
    van de twee plekken is al bezet", "1 kamer vrij", "wij zoeken een nieuwe huisgenoot"). A home with fewer free
    places than people is dropped, whatever its rent, where the listing shows that it is let by the place. In an
    ordinary listing such a sentence can be about a viewing or a study: there the home stays, marked "Free places
    unclear". So does a shared home for two whose text only mentions "de huidige bewoner": that suggests a taken
    place and does not state one. A place on a waiting list or at an open day is no place in the home, and with
    only a rent per person a count of "plekken" is a question too ("nog 1 plek vrij voor de rondleiding").
  - *"incl." and "excl."* stand next to the price only where the listing establishes it: `€850 excl.` when
    service costs, a compulsory extra or utilities come on top, `€850 incl.` when utilities are in it. The costs
    on top still count towards the budget.
  - *Who may rent.* Homes whose own text rules the household out are dropped: "alleen voor werkenden",
    "professionals only", "geen studenten", a minimum age above the youngest, a maximum age below the oldest,
    "geen woningdelers", "alleen voor een stel". A home that says nothing, or says it unclearly, stays. Two
    marks say what to ask: "Age limit unclear" for a bare "Vanaf 25 jaar", "25+" or "tot 30 jaar", and "Sharing
    policy unclear" where the home is offered with a couple in mind ("ideaal voor een stel"). "Een complex met 40+
    appartementen" counts homes: it was read as a minimum age of 40.
  - A run on a machine without the household (the backup run at home, when `household.json` is missing there)
    says so in its log and sends no alert for a home that was known and did not match before.
  - The household can be kept out of this public repository: the `HOUSEHOLD` repository secret or a git-ignored
    `household.json` replaces the block in `config.json`.
- **"Sharing policy unclear".** A limit of two people used to show as a red "Max 2 persons" warning on every card
  whose site or text gave a number, most often Kamernet's own field "Aantal huurders: 2 personen". For two people
  that limit changes nothing: it is now the plain fact "Up to 2 people". When the limit is worded for a couple
  ("alleenstaande of stel") and the listing says nothing about sharers, the card says "Sharing policy unclear":
  that is the thing to ask. Other open points have their own words: "Rent basis unclear", "Free places unclear".
- **Simpler cards.** The price each person pays leads (`€850 excl.`). Under it: bedrooms, m², the term or move-in
  date, and "Furnished" when the listing confirms it; what is missing is left out. The site's name and the arrow
  are gone; the whole card still opens the listing. Warnings and open questions stay on the card, plain facts and
  the listing's own sentences are under "Details".
- **Hide Listing.** A home you have ruled out leaves the list and the map and stays away after a reload, a
  refresh of the data, and when the same address comes back from another site. "Hidden Homes" in the filters
  brings homes back. The choice is saved in the browser, so each phone or computer keeps its own.
- **Map.** List | Map. Move or zoom the map and choose "Search This Area" to keep only the homes in view; select
  a marker to find its listing. Filters and hidden homes apply to both views. Positions come from PDOK, the Dutch
  land registry's free address service (a few lookups per run, saved in `data/geo.json`); a home whose address
  cannot be found is in the list but not on the map, and a dashed marker means the point is the street, not the
  house.

### Changed
- The search for three people is gone from `config.json`: the household is two. Homes that only fitted three
  people leave the dashboard. (More searches can still be added; the group-size control then comes back.)
- The price on a card and in an alert is the rent each person pays, without the costs on top; those are named
  next to it ("+ €43 costs"). Before, they were folded into one figure.
- Saved cards from alert emails are judged again on each run from their own marks (their text is never saved),
  so a change of rules or of household reaches them too.
- Group size reads "2 People, 3 People, Any" (Any moved to the right).
- The "Great Prices Only" switch is gone: the budget slider does the same.
- "Students welcome" no longer counts as "sharers welcome". It is its own mark.

### Bug fixes:

Prices
- Cruquiusweg 85 (Van der Linden 2Peer) was once shown at about €483 per person: the rent of one place in a
  home for two, divided by two. A rent for one place is now shown as it is (€966 excl., service costs €195 on
  top), and with one of the two places taken the home is no match for two people. A Van der Linden rent is no
  longer divided before the listing's own page has been read: the search page shows the same "€ 966 per maand"
  for one place as for a whole home.
- A rent stated per person was shown as the total. Five listings on the dashboard were shown at half their
  real price (for example €1,325 per person as "€662 per person, great price"). The total is now rent × people.
- A compulsory parking place (Vesteda, MVGM) was left out of the total. It is now added before the budget test.
- HousingAnywhere: the price shown was one that appears nowhere on the page for some listings. It is now the
  page's own "From €X per month", and a home whose rent depends on the month says so with the highest amount.
- Van der Linden 2Peer homes: the total was made up by multiplying a per-person amount. The page's own amount
  is kept and marked "per person".
- DirectWonen: "(Excl.)" was read as "excluding utilities" without the site saying so.

Homes that were wrongly dropped or shown
- "Geschikt voor alleenstaanden, stellen en kleine gezinnen" was read as "one person only", which dropped a
  two-bedroom home. A list of who the home suits is no longer a limit.
- A storage room (48 m² for €500, no type on the site) was shown as a great match for two people. A listing
  whose first words name a storage room, parking place or office is not a home.
- "Maximaal 2 maanden waarborgsom" (a deposit) was read as a short stay.
- A condition found at the end of a long text was shown on the card but forgotten for matching on the next
  run, so the match it ruled out came back.
- A card whose listing had left the search pages kept whatever an older version decided until it expired
  after 30 days. Every saved card is now judged again on each run with the current rules.

What kind of home it is
- 123Wonen: one room in a shared penthouse was shown as a whole apartment with two "great" matches.
- A site's bedroom count that equals the room count in the text ("tweekamerappartement" with "2 bedrooms") was
  trusted. The match is now marked "bedrooms not confirmed" and counted as one bedroom less.
- DirectWonen: "Woning" (the site's word for any home) was read as a house.
- Kamernet: "suitable for N persons" is an id, not a count; id 4 means 3 people and id 8 means 4.

Listings that were missed
- Kamernet: paid "top" listings were not read.
- HousingAnywhere: homes in Scheveningen, Loosduinen, Kijkduin, Ypenburg and Leidschenveen were dropped as "not
  Den Haag".
- MVGM (ikwilhuren.nu): the list was read in an order that is not newest first, so new homes could be missed.
- MVGM: a card with a parking badge in front of its "Verhuurd" badge was not recognised as rented.

Text and facts
- Conditions at the end of a long description ("geen woningdelers", "alleen voor studenten") were cut off
  before they were read. The reading limit went from 4,000 to 20,000 characters.
- 123Wonen and Vesteda had no detail step, so their descriptions and conditions were never read.
- Van der Linden: the description included the page footer.
- Kamernet: only one language of a two-language description was read.
- Available-from dates, postcodes, house numbers, furnishing and utilities were missing or wrong on Heimstaden,
  Rotsvast, MVGM, Vesteda, Van der Linden and HousingAnywhere.
- Short stay stated in words ("alleen short stay", "maximaal 6 maanden") was not flagged.

### Checked against the pages

On 19 Nov 2026 the checker's reading of 120 listings on 9 sites was compared, field by field, with what
independent readers wrote down from the same saved pages. Rent agreed on 118 of 120 (the two others were cents,
rounded down) and the size on all 120. These misreadings were found and fixed:

Short stay
- A one-year contract ("1 nov 2026 tot 31 okt 2027", 364 days) was marked as a short stay.
- Short stays in words were missed: "available for a temporary period of 2 months", "available for 1 to 6
  months", and two dates in the text ("beschikbaar vanaf 16 december t/m 28 februari").

Who may rent
- HousingAnywhere: "Space for 1 person", the maximum age and "students welcome" were not read. A room for one
  person, for two months, was shown as a home for three.
- "No students or house sharing" refused students but not sharers.
- Housemates were missed in "wordt gedeeld met 3 andere bewoners" and "te delen met een student".
- "Geschikt voor een alleenstaande, een stel of woningdelers" was read as "two people at most" and did not get
  "sharers welcome".
- The ages of the people who live there ("the roommates are aged 26 and 28") were read as an age limit.
- "Inschrijven bij de gemeente is niet mogelijk" and "you can't register" were not flagged.
- Kamernet: "Geslacht: Man" was not read. "Werkende student" next to "Werkend" was read as "students only".

Costs
- Service costs that the site lists next to the rent (MVGM, Vesteda, Heimstaden, 123Wonen, and Rotsvast's
  "Totale huur") were not shown and not counted. They are now added to the total before the budget test and
  shown as "Plus €N service costs a month". Some cards leave the list because their real total is over budget.
- HousingAnywhere: a compulsory monthly fee (cleaning, for example) was left out of the total.
- Rents with cents were cut off instead of rounded (€1,228.55 is now €1,229).
- "Inclusief gas, water en elektra" in the text was ignored when the site had no field for it. When text and
  site disagree, nothing is claimed.
- A whole home far below any real rent for its size (€750 for 200 m²) now gets the "suspiciously cheap" warning.

What kind of home it is
- MVGM: a card whose title has no type word ("Schipholweg 386") was dropped as "not a home". The type is now
  read from the listing's page.
- Bedrooms described one by one ("slaapkamer van 13 m² ... drie slaapkamers van 10, 9 en 6 m²") were counted
  as three instead of four. "2-BR" was not read.
- Van der Linden: "Per 15 november 2026 beschikbaar" was not read as the available-from date.

Listings that were missed
- 123Wonen: only the first page was read. The next pages (three at most) are read while a page is still full
  of homes that are for rent.

Not checked, because the pages may not be fetched: the cards that come from Pararius and Funda mail alerts,
and DirectWonen's members-only pages.

Relay
- `/stop` right after `/start` could leave a chat subscribed, and `/status` could show the old state, because
  Cloudflare answers reads from a cache for up to a minute. `/start` and `/stop` now always write.

### Found by a second review

A second reviewer (Gemini) read the whole project and tried sentences against the rules. Confirmed and fixed:

- Phrases from the lists in `config.json` skipped the checks for "niet", "of" and "hoeft". "Je hoeft geen
  student te zijn" set "no students", "niet uitsluitend voor een stel" set "no sharers", and "geen
  omzettingsvergunning aanwezig" set "sharers welcome". They now pass the same checks as every other rule.
- A limit on the number of people was read from sentences that set none: "geschikt voor 2 personen of een
  gezin", "alleen voor een stel of woningdelers", "niet geschikt voor 1 persoon", and "een derde kamer voor een
  persoon" (which dropped a three-bedroom home for everyone).
- An age limit was read from the age of the building ("de woning is tussen 20 en 30 jaar oud") and from "wij
  verhuren ook 55+ woningen".
- "Je deelt de woning met je partner of een vriend" was read as "shared with housemates".
- "Students only" from "voor studenten werken wij met een campuscontract"; "working only" from "werkenden of
  gepensioneerden" and "a permanent job or sufficient savings"; "women only" from "vrouwen of stellen".
- "De prijs voor de eindschoonmaak is per kamer" was read as a rent per person, which tripled the total and
  dropped the home.
- Bedrooms: "Bedrooms: 3 rooms in total", "voorheen 2 slaapkamers" and "mogelijkheid tot 2 slaapkamers" were
  counted as the bedrooms the home has.
- A rent that depends on the group ("Huurprijs voor 3 personen € 2395, voor 2 personen € 1995") was shown to
  three people at the two-person rent, as a "great price". The amount stated for the group is now used and
  shown ("€2395 a month for 3 people").
- A card whose listing had left the search pages kept what an older version had read from its page (one card
  still said "max 8 persons", an id read as a head count). Such pages are now read once more.
- List items without a full stop were read as one sentence, so the quoted sentence ran on into the next line,
  and "Huisdieren: geen" above "Studenten welkom" could be read as "geen studenten". Line breaks of the
  listing's text are now kept and no rule reads across them. On the 120 audited pages this changed no mark,
  and 22 quoted sentences became the exact line.

A last review of these fixes (the advisor) found seven more slips, all fixed before this was published. Among
them: "geen woningdelers, geen studenten" lost its "no sharers" mark, "prijs voor 2 personen € 950 per persoon"
was read as the rent for two, "keuken inclusief elektrische kookplaat" was read as "utilities included",
123Wonen's "huurprijs inclusief € 50 servicekosten" was added on top, and two lists of the same bedrooms were
added up.

Checked and not changed: the Mac backup runs on when it cannot read GitHub's memory (it only checks sites
GitHub cannot reach, so a double alert is rare and a missed home costs more), and the subscriber limit of 50
is approximate when several chats subscribe within the same minute.

### Other changes
- Every existing card is read once more by the new readers (about 10 per run), so the dashboard corrects itself
  over the next hours without sending alerts.
- 761 new tests (1,123 in total), 53 dashboard checks and 26 relay checks. The who-may-rent rules are tested
  against 77 real and made-up phrases.

## v1.1.0 (2026-11-19)

The first numbered version. Everything before it counts as 1.0.

### Bug fixes:

Alerts that were missed
- A home found on two sites at once was hidden as a duplicate even when the first listing turned out not to fit, so the home that did fit was never shown. Duplicates are now decided after the detail pages are read, and a duplicate is shown again when the listing it duplicated stops fitting.
- Two different homes on one street at the same rent, listed without a house number, counted as one home. Size and bedrooms now have to agree, and a house number from the detail page is used.
- A first-scan alert that could not be sent was never sent again.
- A price drop that brought a home into budget was lost when its alert could not be sent, or when no detail page could be read in that run.
- "Single bed", "single-family home" and "a single person or a couple" were read as "one person only", which hid homes for two and three people.
- Two Pararius homes with the same street name in one mail shared the first home's rent and room count.
- Mail prices written with two spaces after the euro sign were not read, and a small amount before the rent (a discount, a fee) hid the rent.
- An error on a later results page of MVGM (ikwilhuren.nu) threw away the homes already read from the first page.
- HousingAnywhere pages that list unit types only were rejected as a layout change.
- Vesteda was always asked for homes up to €2,400, whatever the budget in `config.json`.
- A correction on a site without detail pages (Vesteda, 123Wonen, mail alerts) was undone on every run by an older copy of the listing.

Alerts that were wrong or repeated
- A "price drop" was announced when the detail page showed the rent had fallen by only a euro or two.
- The Mac backup announced a price drop that the GitHub checker had already announced.
- A site that worked again for some of its cities kept its failure count, so "X has failed N runs" was never followed by a recovery message. It now sends one "partly working again" message.
- A site that was blocked before it returned anything was shown as "partial" instead of "blocked".

Flags and facts
- "No registration possible" and a short maximum stay were ignored when the site gave them as fields instead of text.
- A warning flag that no current text supported stayed on the dashboard card.
- Heimstaden: "1 tot 3 slaapkamers" was read as 3 bedrooms, and the word "een" as a count.
- MVGM: home types such as "Woning" and "Vrijstaande woning" were dropped as "other".
- DirectWonen: a relative link behind the payment page was used as it was.
- Mail listings were looked up again on every run, using up the run's detail-page allowance.

Dashboard
- An unknown price per person was shown as "€0 pp".
- "Cheapest first" and the great/ok badge ignored the selected group size.
- A tab left open reported "the checker may be down" after two hours, because it never loaded new data. It now refreshes every 5 minutes.
- "Copy reply" left the address empty when a listing had none; "1 bedrooms".

Privacy
- When a Telegram group became a supergroup, its new chat id was printed in the run log, which is public for a public repository. No chat id is printed any more, and Telegram error text is redacted.

Page reader
- A listing number could match a longer number that contains it (123456 inside 1234567), so another listing's page counted as this one.
- The description of another listing on the same page (a "similar homes" block) could be used when it was the longest.

Small things
- Quiet hours written as `[0, 0]` made every message silent; start = end now means "no quiet hours".
- `telegram-setup` kept calling Telegram without a pause when Telegram answered with an error.

### Other changes
- 163 new tests (362 in total), including a browser-free check of the dashboard script.
- Removed unused counters and a double load of the source modules.
