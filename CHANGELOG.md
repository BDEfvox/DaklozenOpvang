# Changelog

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
