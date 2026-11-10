# House Hunter

Checks Dutch rental sites every ~10 minutes for a whole home in **Den Haag, Amsterdam or Leiden**
that fits **2 people (2+ bedrooms, up to €1,600/month)** or **3 people (3+ bedrooms, up to €2,400/month)**,
so about €800 per person or less. New matches go to your **Telegram group**, and every match is on a
**dashboard page**. It runs for free on **GitHub Actions**, so your computer can be off.

- 🟢 **great**: €700 per person or less. 🟡 **ok**: up to €800 per person.
- Warnings on each listing: no house sharers, max N persons, no students, no registration possible,
  income requirement, short-term only, suspiciously cheap, possible scam.
- Each alert has a ready-to-send Dutch reply. The tool never contacts landlords itself.
- A daily summary at 9:00 tells you it's still alive. **If the daily summary stops coming, the checker has stopped.**
  See [Troubleshooting](#troubleshooting).

## Go live (about 10 minutes, once)

You need: a GitHub account, Telegram on your phone, and this folder on your Mac.

1. **Make a Telegram bot.** In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username
   ending in `bot`. BotFather replies with a **token** (looks like `123456:ABC...`). Keep it private.
2. **Make the group.** Create a Telegram group with your friends and add your bot to it.
3. **Run the setup script** in Terminal:
   ```bash
   cd ~/ProjectRollingThunder/house-hunter && ./go-live.sh
   ```
   It will:
   - sign you in to GitHub (a browser window opens)
   - ask for the bot token (typing is hidden), then ask you to send `/start@yourbot` in the group so it can find the group
   - optionally set up the Pararius/Funda email inbox (see below; you can skip this and do it later)
   - create a **public** GitHub repo `house-hunter`, push the code, store the token as an encrypted GitHub secret
   - turn on the dashboard page and the 10-minute schedule, then run the first check while you watch
4. Within a few minutes the group gets the current best matches and a list of which sites work from GitHub's servers.

The dashboard is at `https://<your-github-name>.github.io/house-hunter/`. It can take a few minutes to appear the first time.

> **Public repo:** anyone who finds it can see the code, your search settings and the listings it found (these are public
> listings anyway). Your Telegram token and email password are stored as encrypted secrets and are never visible.

## Pararius and Funda (optional, recommended)

Pararius, Funda, Huurwoningen.nl and Huurstunt block automated checks, and this tool doesn't try to get around that.
Pararius and Funda are the biggest rental sites, though, so here's the workaround: they send free **email alerts**,
and the tool can read those emails and forward the matches to Telegram.

1. Make a new Gmail address just for this (don't use your main inbox: the tool gets read access to it).
2. Turn on 2-step verification for it, then create an **app password**: Google Account → Security → App passwords.
3. On Pararius and Funda, make an account with that Gmail and save searches with email alerts (choose "direct" / instant if offered):
   - Den Haag, Amsterdam, Leiden; huur; max €2,400 (or €1,600 if it's just two of you); 2+ slaapkamers.
4. Run `./go-live.sh` again and say **yes** to the email question (host `imap.gmail.com`, your new Gmail address, the app password).

## Things to know

- **Three people is harder than two.** In all three cities, a landlord usually needs a permit (omzettingsvergunning)
  to rent a normal home to 3 or more unrelated adults. That's why many listings say "max 2 personen" or "geen woningdelers".
  The tool flags those and leaves them out of the 3-person search.
- **Income:** landlords usually want a gross monthly income of 3 to 4 times the rent, combined. Some count a second earner
  for only 50%. Students often need a guarantor (garantsteller).
- **Utilities:** listed rent usually excludes gas, water, electricity and internet. Add roughly €150–250 a month for the household.
- **Scams:** never pay anything before you've viewed the home in person and signed a contract. Watch out for landlords who are
  "abroad", keys sent by post, Airbnb or other "escrow" payment links, and requests for your passport before a viewing.
  "Inschrijving niet mogelijk" (no municipal registration) is a red flag too.
- **Speed matters:** good listings get dozens of reactions within hours. Fill in the bracketed parts of the reply template
  once (`reply_template` in `config.json`), then you can answer from your phone in a minute.
- **Paid sites:** you can see Kamernet and DirectWonen listings for free, but replying needs a paid account.
- **Not covered:** Facebook groups (they need a login) and social housing (Woonnet Haaglanden, WoningNet,
  Woningzoeken Holland Rijnland, which need years on a waiting list). Do register for those anyway.

## Sites it checks

Checked every run (all tested working from a home connection on 9 Nov 2026; the first GitHub run reports which
ones also work from GitHub's servers):

| Site | Cities | Replying |
|---|---|---|
| Kamernet (apartments only) | all three | paid Kamernet account |
| HousingAnywhere | all three | platform fee; mostly furnished, mid-term |
| DirectWonen | all three | paid account |
| 123Wonen | all three | free, via the agency |
| Rotsvast | all three | free, via the agency |
| MVGM (ikwilhuren.nu) | all three | free account |
| Vesteda | all three | free account |
| Heimstaden | all three | free |
| Van der Linden | Amsterdam | free, via the agency (includes "2PEER" homes built for sharers) |
| Pararius, Funda, Huurwoningen (via their email alerts) | all three | free; optional, see above |

Not checked, on purpose:
- **Pararius, Funda, Huurwoningen, Huurstunt, Holland2Stay, REBO** block automated checks. The tool doesn't get around
  that; use the email alerts for the first three.
- **Marktplaats and Interhouse** forbid automated access to their search results in their robots.txt.
- **Rentola** is a paywalled aggregator with lots of complaints.
- **Facebook groups** need a login.

## Mac backup (optional)

Some sites may refuse GitHub's servers but work fine from your home connection. The first run tells you which.
Your Mac can cover just those sites:

```bash
./local-backup.sh on
```

- It checks **only the sites the GitHub checker reports as blocked or broken**, so you never get the same alert twice.
  It also knows every listing GitHub has already seen.
- It's also a **watchdog**: if the GitHub checker stops running for 3 hours, you get a Telegram warning, and another
  when it's back.
- Its messages start with "💻 Mac backup". It never sends the daily summary; GitHub does.
- It only runs while the Mac is **awake**. To keep it awake when plugged in: System Settings → Battery → Options →
  "Prevent automatic sleeping on power adapter when the display is off".
- `./local-backup.sh status` shows whether it's on and its last log lines. `./local-backup.sh off` stops it,
  and `./local-backup.sh remove` deletes the background job.
- Not using GitHub at all? Set `"mode": "all"` under `local_backup` in `config.json` to check every site from the Mac.
  Don't do this while GitHub is running too, or you'll get every alert twice.
- One small catch: if GitHub later regains access to a site the Mac was covering, a few listings may be alerted twice once.

## Changing the search

Edit `config.json` and push (or edit it on github.com: open the file and click the pencil). The next run picks it up.
- `searches`: group sizes, bedrooms, max total rent, the "great" threshold.
- `cities`: `den-haag`, `amsterdam`, `leiden`.
- `disabled_sources`: e.g. `["marktplaats"]` to stop a site.
- `reply_template`: your reply text. Fill in the parts in [brackets].
- `telegram.quiet_hours`: messages between 23:00 and 07:00 arrive silently.

## Commands (on your Mac, in this folder)

```bash
.venv/bin/python -m hunter check
```
Checks every site once and prints what it finds. It sends and saves nothing.

```bash
.venv/bin/python -m hunter run --dry-run
```
Runs one full round and prints the Telegram messages instead of sending them.

```bash
.venv/bin/python -m pytest -q
```
Runs the tests.

## Troubleshooting

- **No daily summary / dashboard says "the checker may be down":** open the repo on github.com → **Actions** → `hunt`.
  If it says the workflow was disabled (GitHub disables schedules in public repos after 60 days without activity),
  click **Enable workflow**. If runs are red, open the latest one to see the error. GitHub also emails you when a scheduled run fails.
- **A site shows "blocked" on the dashboard:** that site refuses automated checks from GitHub's servers. The others keep
  working. Turn on the [Mac backup](#mac-backup-optional) to cover it from home, or use its email alerts if it has them.
- **A site shows "error" / "layout changed":** the site changed its page. The others keep working; the parser for that site
  needs an update.
- **Telegram stopped after you changed the group:** if Telegram upgrades the group to a "supergroup" (for example after
  you make someone admin), its ID changes. Run `./go-live.sh --telegram`; it finds the group again and updates the secret.
- **Timing:** GitHub runs schedules "best effort", so checks can be 10–20 minutes apart at busy times.

## How it works

See [DESIGN.md](DESIGN.md). In short: `hunter/sources/` has one file per site; `hunter/filters.py` decides what
matches; `hunter/state.py` remembers what you've already been sent (`data/`); `hunter/notify/` formats and sends Telegram
messages; `hunter/dashboard.py` writes `docs/data.json` for the page in `docs/`; `.github/workflows/hunt.yml` runs it all
every 10 minutes and commits the updated data.
