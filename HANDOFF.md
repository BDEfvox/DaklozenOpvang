# Handoff: upload House Hunter to GitHub (paused 9 Nov 2026)

**Status:** everything is built and tested locally (111 tests pass). **Nothing is on GitHub yet**, and there are no git
commits. The upload stopped at the GitHub sign-in, which only you can do.

## Done since the last summary
- `.github/workflows/hunt.yml` now **skips every run until the Telegram secrets exist**. Pushing the code without
  Telegram set up therefore can't quietly use up your first batch of alerts.

## To finish (about 10 minutes, with you at the keyboard)
Easiest: do it all in one go, Telegram included:
1. Telegram: open @BotFather, send `/newbot`, copy the token. Make a group with your friends and add the bot.
2. Run:
   ```bash
   cd ~/ProjectRollingThunder/house-hunter && ./go-live.sh
   ```
   It signs you in to GitHub in the browser, sets a private git identity (your GitHub noreply address), creates the
   **public** repo `house-hunter`, pushes, stores the secrets, turns on the dashboard (GitHub Pages) and runs the first check.

Or, to upload only the code now and do Telegram later, ask Claude to "continue the GitHub upload". The steps:
- `~/.local/bin/gh auth login --web --git-protocol https` then `~/.local/bin/gh auth setup-git` (you sign in)
- set the repo-local git identity from `gh api user` (login and id → `<id>+<login>@users.noreply.github.com`)
- final check: no `.env`, `data/` holds only `.gitkeep`, no secrets → `git add -A` → commit
- `gh repo create house-hunter --public --source . --remote origin --push`
- `gh variable set DASHBOARD_URL --body https://<login>.github.io/house-hunter/`
- enable Pages from `main:/docs` (`gh api -X POST repos/<owner>/house-hunter/pages -f "source[branch]=main" -f "source[path]=/docs"`)
- later, `./go-live.sh` handles Telegram: it sees the existing repo, sets the secrets and runs the first check.

## Still unverified
- Whether each site works from GitHub's servers (the first run's Telegram status message shows this). If some are
  blocked: `./local-backup.sh on`.
- `go-live.sh` and `telegram-setup` against the real GitHub and Telegram.
- The Pararius/Funda email-alert reader against real alert emails.

Delete this file once the upload is done.
