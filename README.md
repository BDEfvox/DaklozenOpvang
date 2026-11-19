# House Hunter

Checks Dutch rental sites every ~10 minutes for a whole home in **Den Haag, Amsterdam, Leiden, Delft or Rotterdam**
that fits **2 people (2+ bedrooms, up to €1,600/month)** or **3 people (3+ bedrooms, up to €2,400/month)**,
so about €800 per person or less. New matches go to your **Telegram group**, and every match is on a
**dashboard page**. It runs for free on **GitHub Actions**, so your computer can be off.

- 🟢 **great**: €700 per person or less. 🟡 **ok**: up to €800 per person.
- Warnings on each listing: no house sharers, max N persons, no students, no registration possible,
  income requirement, short-term only, suspiciously cheap, possible scam.
- **Who may rent:** it reads whether a home is only for students, only for working people, has a minimum or
  maximum age, refuses or welcomes house sharers (woningdelers), or is shared with housemates. Each of these is
  shown with the listing's own sentence, so you can check it at a glance, and you can filter on all of them.
- Each alert has a ready-to-send Dutch reply, and the dashboard lets you write your own. The tool never contacts
  landlords itself.
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
   - turn on the dashboard page, then start the checker and run the first check while you watch
4. Within a few minutes the group gets the current best matches and a list of which sites work from GitHub's servers.

The dashboard is at `https://<your-github-name>.github.io/house-hunter/`. It can take a few minutes to appear the first time.
GitHub Pages republishes it by itself after every save by the checker, usually within a minute, and an open tab
fetches the new data every 5 minutes, so you don't need to reload it.

> **Public repo:** anyone who finds it can see the code, your search settings and the listings it found (these are public
> listings anyway). Your Telegram token and email password are stored as encrypted secrets and are never visible.

## Pararius and Funda (optional, recommended)

Pararius, Funda, Huurwoningen.nl and Huurstunt block automated checks, and this tool doesn't try to get around that.
Pararius and Funda are the biggest rental sites, though, so here's the workaround: they send free **email alerts**,
and the tool can read those emails and forward the matches to Telegram.

1. Make a new Gmail address just for this (don't use your main inbox: the tool gets read access to it).
2. Turn on 2-step verification for it, then create an **app password**: Google Account → Security → App passwords.
3. On Pararius and Funda, make an account with that Gmail and save searches with email alerts (choose "direct" / instant if offered):
   - Den Haag, Amsterdam, Leiden, Delft, Rotterdam; huur; max €2,400 (or €1,600 if it's just two of you); 2+ slaapkamers.
4. Run `./go-live.sh` again and say **yes** to the email question (host `imap.gmail.com`, your new Gmail address, the app password).

If alert emails arrive but nothing reaches Telegram: Actions tab → **email debug** → Run workflow. Its log shows what
the tool finds in the mailbox (counts, senders, the shape of the links; no names, addresses or mail text).

## Things to know

- **Three people is harder than two.** A landlord often needs a permit (omzettingsvergunning)
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

The checker tries these sources each run. The dashboard shows which sites respond from GitHub.

| Site | Cities | Replying |
|---|---|---|
| Kamernet (apartments only) | all five | paid Kamernet account |
| HousingAnywhere | all five | platform fee; mostly furnished, mid-term |
| DirectWonen | all five | paid account |
| 123Wonen | all five | free, via the agency |
| Rotsvast | all five | free, via the agency |
| MVGM (ikwilhuren.nu) | all five | free account |
| Vesteda | all five | free account |
| Heimstaden | all five | free |
| Van der Linden | matching cities in its feed | free, via the agency (includes "2PEER" homes built for sharers) |
| Pararius, Funda, Huurwoningen (via their email alerts) | all five | free; optional, see above |

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

## Let other chats subscribe (optional)

By default the alerts go to your own chat only. If you want a friend (or their family group) to get the same
alerts, you can give them a **secret invite link**. Whoever opens it can add the bot to their own private chat or
group; nobody else can. It stays private: the bot answers only to the invite link, and the list of subscribers is
not in this public repository.

**What a subscriber sees:** after tapping the link and pressing Start, one welcome message (with the dashboard link).
From then on they get every new match, price drop and the daily digest, exactly like you. They do **not** get the
technical messages (a site that stops working, the first-run site report, the Mac backup's warnings): those are for
you only. `/stop` ends it, `/status` says whether the chat is subscribed. If they remove the bot from their group or
block it, they are taken off the list automatically.

**What is stored where:**
- On Cloudflare (free, a small "relay"): per subscribed chat only its numeric id, whether it is a private chat or a
  group, and the time it joined. No names, usernames, group titles or messages.
- The relay never holds the bot token. Telegram's answers to `/start` and `/stop` are sent back through the relay's own
  reply, so it needs no token.
- On GitHub and in your `.env`: `RELAY_URL` and `RELAY_PULL_SECRET`, so each run can ask the relay who is subscribed.
  Chat ids are never printed in the run log.
- Your own chat is separate and unchanged (`TELEGRAM_CHAT_ID`). If the relay is unreachable, a run sends to you only.

**Set it up** (once; the first time run `npx wrangler login` to sign in to a free Cloudflare account, and make sure this
version is pushed to GitHub):

```bash
./telegram-relay/deploy.sh
```

It creates the storage, deploys the relay, connects GitHub, points the bot at the relay, tests the whole path
without subscribing anyone, and prints two invite links: one for a private chat with the bot and one for adding the bot
to a group. Send them only to people you trust. At most 50 chats can subscribe.

Two things to know. A person who has the link can pass it on, and in a group the other members may see the
invite code in the chat. If that matters, make a new link afterwards with `--new-invite`: the old link stops
working and everyone who already subscribed stays. If someone got in who should not have, `--remove-all`
removes every subscriber and makes a new link; then invite the right people again (the list holds only chat
numbers, so single chats cannot be picked out by name). And subscribers are best effort: if the relay cannot be
reached during a run, that run's alerts go to your own chat only and are not sent to subscribers later.

**Useful commands:**

```bash
./telegram-relay/deploy.sh --links        # print the invite links again
./telegram-relay/deploy.sh --new-invite   # a new invite link; the old one stops working, current subscribers stay
./telegram-relay/deploy.sh --count        # how many chats are subscribed (never which)
./telegram-relay/deploy.sh --remove-all   # remove every subscriber and make a new invite link
./telegram-relay/deploy.sh --off          # switch it off: alerts go to your own chat only again
```

`--off` removes the webhook and the GitHub and `.env` settings and tells you how to erase the stored list and the relay
itself from Cloudflare. One thing to know: while the relay is on, the bot's webhook is set, and Telegram then refuses
`./go-live.sh --telegram` (it cannot look for your group). Run `./telegram-relay/deploy.sh --off` first, then that
command, then `./telegram-relay/deploy.sh` again.

## The dashboard filters

- **Group size:** 2 People, 3 People or Any.
- **Cities** and **Maximum per Person**.
- **Who's Renting:** Students, Working or Any, and your age. This hides homes that are only for the other group
  and homes with an age limit you don't meet.
- **House Sharing:** hide homes that refuse sharers, show only homes that welcome sharers, hide homes where you'd
  have housemates (people who already live there, or a shared kitchen or bathroom).
- **Warnings:** hide every listing with a warning.

These filters only act on what a listing actually states. A home that says nothing about students, age or
sharing always stays in the list. Under each listing you see the sentence the tool based its marks on, in the
listing's own words. If a mark looks wrong, that sentence shows why.

**Your reply text.** Tap **Reply** (next to the search field) to write the message that "Copy Reply" copies. Tap
a detail (Title, Address, City, Rent, Bedrooms, Size, Site, Link) to put it where the cursor is; it is filled
in for each listing when you copy, and the preview shows the result. The text is kept on that device only.
"Use the Standard Text" brings back the text from `config.json`.

## Changing the search

Edit `config.json` and push (or edit it on github.com: open the file and click the pencil). The next run picks it up.
- `searches`: group sizes, bedrooms, max total rent, the "great" threshold.
- `cities`: `den-haag`, `amsterdam`, `leiden`, `delft`, `rotterdam`.
- `disabled_sources`: e.g. `["marktplaats"]` to stop a site.
- `reply_template`: your reply text. Fill in the parts in [brackets]. (Telegram alerts use this one; the
  dashboard uses it until you write your own there.)
- `household`: optional, e.g. `{"age": 24, "occupation": "students"}` (or `"working"`). When set, homes that
  state they are not for you (students only, a minimum age above yours, …) are not alerted at all. Leave it empty
  to get everything and filter on the dashboard.
- `tenant_rules`: optional extra wordings for the who-may-rent flags, for a phrase the tool misses.
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
  There should always be one run "waiting" (or "pending" while a check is running): each check starts the
  next one, which waits until it is due. If none is waiting, pending or running, click **Run workflow**. If it says the workflow was disabled, click
  **Enable workflow** first. If runs are red, open the latest one to see the error.
- **Stopping the checker:** Actions → `hunt` → **⋯** → **Disable workflow** (or cancel the waiting run).
  To start again: **Enable workflow**, then **Run workflow**.
- **A site shows "blocked" on the dashboard:** that site refuses automated checks from GitHub's servers. The others keep
  working. Turn on the [Mac backup](#mac-backup-optional) to cover it from home, or use its email alerts if it has them.
- **A site shows "partial":** some requests failed, but the checker still uses the listings it received.
- **A site shows "error" / "layout changed":** the site changed its page. The others keep working; the parser for that site
  needs an update.
- **Telegram stopped after you changed the group:** if Telegram upgrades the group to a "supergroup" (for example after
  you make someone admin), its ID changes. Run `./go-live.sh --telegram`; it finds the group again and updates the secret.
- **Timing:** checks are 10 minutes apart. GitHub's own schedule turned out to be unreliable (it did not run at
  all in the first hours), so each check starts the next one; the schedule is only a backup that restarts the
  checker if it ever stops.

## How it works

See [DESIGN.md](DESIGN.md). In short: `hunter/sources/` has one file per site; `hunter/filters.py` decides what
matches; `hunter/state.py` remembers what you've already been sent (`data/`); `hunter/notify/` formats and sends Telegram
messages; `hunter/dashboard.py` writes `docs/data.json` for the page in `docs/`; `.github/workflows/hunt.yml` runs it all
every 10 minutes and commits the updated data.
