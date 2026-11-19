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
- **Dashboard filters:** Who's Renting (Students, Working, Any, and your age) and House Sharing (hide homes that
  refuse sharers, only homes that welcome sharers, hide homes with housemates). A home that says nothing is
  never hidden by these.
- **Your own reply text.** The Reply button opens an editor for the text "Copy Reply" copies. Details such as
  the address and the rent are filled in for each listing.
- **Subscribers.** Other chats can get the alerts through a secret invite link (`telegram-relay/`, optional,
  off until `./telegram-relay/deploy.sh` is run). The link can be replaced and all subscribers removed.
- **Optional household profile** in `config.json`: homes that state they are not for you are not alerted.

### Changed
- Group size reads "2 People, 3 People, Any" (Any moved to the right).
- The "Great Prices Only" switch is gone: the budget slider does the same.
- "Students welcome" no longer counts as "sharers welcome". It is its own mark.

### Bug fixes:

Prices
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

### Other changes
- Every existing card is read once more by the new readers (about 10 per run), so the dashboard corrects itself
  over the next hours without sending alerts.
- 333 new tests (695 in total), 30 dashboard checks and 25 relay checks. The who-may-rent rules are tested
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
