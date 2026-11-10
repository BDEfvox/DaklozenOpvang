# House Hunter: design spec

Personal alert tool: 2–3 friends looking for a whole rental home in **Den Haag, Amsterdam or Leiden**.
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
  utils.py           price/int parsing, city normalisation   (core, done)
  sources/base.py    Source interface + exceptions           (core, done)
  sources/__init__.py auto-discovers every module in sources/ (core, done)
  sources/<site>.py  one module per site, defines SOURCE = <Source instance>
  config.py          loads config.json
  filters.py         decides matches, tiers, flags
  state.py           seen listings, source health, daily digest bookkeeping
  notify/telegram.py Telegram Bot API (sendMessage), quiet hours, rate limit
  dashboard.py       writes docs/data.json (the page in docs/ reads it)
  __main__.py        CLI: run | check | telegram-setup | dashboard
config.json          searches, cities, flags phrases, schedule options
data/                state (committed by the workflow): seen.json, health.json, meta.json
docs/                dashboard (GitHub Pages from main:/docs): index.html, app.js, style.css, data.json
.github/workflows/hunt.yml
go-live.sh           one-time setup the USER runs (gh login, Telegram token, repo, secrets, Pages)
tests/
```

## Listing contract (`hunter/models.py`)
Sources return `Listing` objects. `city` is a slug: `den-haag` | `amsterdam` | `leiden`.
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
- Raise `SourceBlocked` for bot walls, `SourceError` for anything else. Never return a half-parsed
  page silently: if the page structure is not recognised, raise `SourceError("layout changed: …")`.
  Returning `[]` means "the site really shows no listings".

## Matching (`filters.py`)
`config.json` holds `searches`, default:
- `2 people`: persons 2, min bedrooms 2, max total €1,600, "great" ≤ €1,400 (≤ €700 pp)
- `3 people`: persons 3, min bedrooms 3, max total €2,400, "great" ≤ €2,100

A listing matches a search when: city is one of the configured cities; property type is not
room/studio/parking/other; price known and ≤ max; bedrooms ≥ min. If the site gives no bedroom count, the
count stated in the title/description is used (`utils.bedrooms_from_text`: "Aantal slaapkamers: 2", "drie
ruime slaapkamers", "2-bedroom"). If only `rooms` is known, `rooms - 1` is assumed and the match is flagged
"bedrooms not confirmed" (a 4-room home often has 2 bedrooms). If both are unknown and the area is known,
area ≥ 45 m² (2p) / 60 m² (3p) counts as "bedrooms unknown" match (flagged); otherwise no match. Result:
matched searches, € per person per search, tier (`great`/`ok`), flags.

Every match gets its detail page read once: new matches first, then (with what is left of
`detail_fetch_max_per_run`) older dashboard cards that were never checked, bedroom guesses first. That
re-check sends no alerts; it only corrects or removes cards. A page that fails is tried 3 times.

Flags (case-insensitive phrase lists in config.json, Dutch + English), detected in title + description:
`no_sharers`, `max_persons:N`, `no_students`, `sharers_ok`, `no_registration`, `income_requirement`,
`short_term` (rental period < 12 months when dates are known), `utilities_included`,
`suspicious_price` (total < €700 for 2+ bedrooms), `scam_signals` (payment before viewing, landlord abroad,
keys by mail, western union, airbnb payment, whatsapp-only…). Flags never hide a listing; a
`no_sharers` or `max_persons:2` flag removes it from the 3-person search only.

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
  size, bedrooms/rooms/m², furnished, available from, flags as short warnings, source label, link.
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
Static page (no build step) reading `data.json`: cards for matches from the last 30 days, newest first;
filters for city, group size, max € pp, "hide flagged"; NEW badge (< 24 h); source health table; last
check time. Works on a phone. Plain HTML/CSS/JS.

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

## Sources
Bot walls seen on 9 Nov 2026 (from a home connection): Pararius, Huurwoningen.nl, Huurstunt (Cloudflare),
Funda (own bot check), Holland2Stay (403). → covered via email alerts, not scraped.
Open (HTTP 200 with data): Kamernet (`__NEXT_DATA__`), HousingAnywhere (`window.__PRELOADED_STATE__`),
Marktplaats (`/lrp/api/search`, category 2143 "Huizen te huur"), 123Wonen, DirectWonen, Interhouse,
Van der Linden, Vesteda (SPA). To check: ikwilhuren.nu (MVGM), wonenbijbouwinvest.nl, Heimstaden,
Rotsvast, vbtverhuurmakelaars.nl, rebowonenhuur.nl, Domica, HouseHunting, Duinzigt Wonen.
Avoid: Rentola (paywalled aggregator, many complaints).

## Dashboard data schema (`docs/data.json`, written by `hunter/dashboard.py`, read by `docs/app.js`)
```json
{
  "schema": 1,
  "generated_at": "2026-11-09T16:20:00Z",
  "last_run": "2026-11-09T16:20:00Z",
  "dashboard_url": "https://<user>.github.io/house-hunter/",
  "cities": {"den-haag": "Den Haag", "amsterdam": "Amsterdam", "leiden": "Leiden"},
  "searches": [
    {"name": "2 people", "persons": 2, "max_total": 1600, "great_total": 1400},
    {"name": "3 people", "persons": 3, "max_total": 2400, "great_total": 2100}
  ],
  "flag_labels": {"no_sharers": "No house sharers", "max_persons": "Max {n} persons", "no_students": "No students",
                  "sharers_ok": "Sharers welcome", "no_registration": "No registration possible",
                  "income_requirement": "Income requirement", "short_term": "Short-term only",
                  "utilities_included": "Utilities included", "suspicious_price": "Suspiciously cheap",
                  "scam_signals": "Possible scam", "bedrooms_unknown": "Bedrooms unknown"},
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
     "matches": [{"search": "2 people", "persons": 2, "price_pp": 725, "tier": "ok"}],
     "flags": ["max_persons:2", "income_requirement"]}
  ]
}
```
`status` is one of `ok | partial | blocked | error | disabled`. A flag may carry a value after a colon
(`max_persons:2`); `flag_labels` is keyed by the part before the colon and may contain `{n}`.
All strings in `listings` come from third-party sites: the page must render them with `textContent`,
never `innerHTML`.
