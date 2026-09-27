# Policy Pulse

**The 3-minute weekly brief on Indian policy that tells you when you can still act.**

A free, open, no-login public site for 18–30 year-old Indians (students, first-time voters,
early-career professionals) that tracks government activity on the issues that hit their lives —
jobs, exams, taxes, digital rights, gig work, AI regulation, criminal justice — and shows how attention on each has built up
over time.

## What it does

Every Monday, Policy Pulse reads PRS Legislative Research's record of formal government action,
scores which everyday topics are moving, cross-checks each item against independent coverage, and
publishes a brief — opened by a model-drafted **"This week in 60 seconds"** — in three parts:

1. **🗣 You can still respond** — drafts open for public comment from PRS's live *Announcements*
   table (exact deadlines), with the submission email and addressee **read out of the notice
   itself**, the regulator's usual channel, and a copy-paste comment template. Daily reminders at
   7 days and 48 hours.
2. **🔥 Three signals worth watching** — each with a *why it's moving* mini-explanation (dated actions,
   institutions involved, a rule-based interpretation), a hook headline,
   "For you →" lines per persona (student / gig worker / founder / salaried — pick yours once and
   the page filters to you), a confidence label, and ✓ marks where the action was independently reported.
3. **😴 Confirmed quiet** — topics with no formal action, stated explicitly.

**Follow** any topic and the next visit opens with *"Since your last visit: 2 new actions · now
Heating up, was Steady"* — computed in your browser from a local snapshot; nothing is sent anywhere.

Each topic also shows **what the states are legislating** — Bills from 37 state legislatures tagged
to the same topics (a separate `/states.html` index lists them all). This is where "Centre quiet"
topics like gig work often turn out to be very much alive.

The website (English + Hindi) is the archive: every topic's 17-month evidence trail, policy
**journeys** (the same Bill tracked committee → draft → law → rules, with its current lifecycle
stage), what would make each signal fade, connected topics, and the full method.
The Telegram channel is the product; the site is where the receipts live.

## What makes it different

- **Evidence-weighted momentum, not a black box** — a law passed counts more than a committee report;
  the score compares the last 3 months to the 6 before. Everything is recomputable from `/method.html`.
- **Two-source discipline** — each item is checked against Google News (which indexes PIB, News On AIR
  and the national press). Government or 2+ newspaper hits = "independently confirmed"; confidence
  is lifted only when most recent evidence is confirmed. PRS itself never counts as corroboration.
- **Honest about absence** — "Quiet" says *no formal action recorded*, and explains that courts,
  strikes and implementation are outside the source.
- **Act-now first** — deadlines parsed, closed items demoted, response templates included.
- **Persistent, compounding** — SQLite committed to the repo; `docs/radar.json` is free to build on.

## Where the AI is (and isn't)

Deterministic: scoring, confidence rules, deadline parsing, corroboration counting, reading
submission emails/addressees out of notices (regex over PDF/HTML text).
Model-generated (Claude when `ANTHROPIC_API_KEY` is set; model-written seeds in `data/` until then):
plain-English summaries, hook headlines, persona "For you" lines, the weekly editor's note,
Hindi translations. Everything model-drafted is labelled "AI-drafted · source-linked" on the page.
Curated: topic tag corrections (`data/tag_overrides.json`), policy journeys (`data/chains.json`),
per-consultation facts a human verified (`data/respond_overrides.json`).

## How it works

```
PRS Monthly Policy Review + PRS Announcements (CC BY 4.0)
        │  weekly GitHub Actions cron (daily for deadlines), 10s crawl-delay
        ▼
  radar/parse.py         → structured items (month, ministry, title, body, links)
  radar/announcements.py → live drafts open for comment, exact deadlines, notice links
  radar/states.py        → state legislature Bills (37 states, this + last year) + PRS state briefs
  radar/notice.py        → reads the notice: submission email, addressee, deadline; "likely closed" inference
  radar/score.py       → action type · topic tags (+ overrides) · momentum · confidence
  radar/crosscheck.py  → independent coverage per item (Google News RSS → PIB / newspapers)
  radar/summarize.py   → one-line summary          ┐
  radar/enrich.py      → hook + "For you" lines    ├ Claude, or seed files
  radar/translate.py   → Hindi titles/summaries    ┘
  radar/build_site.py  → static site (EN + HI) + radar.json → GitHub Pages
  radar/notify.py      → Telegram: Monday digest, 7-day and 48-hour deadline pings
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m radar run --months 18          # ingest, retag, summarise, translate, enrich, crosscheck, build
python -m radar notify --digest --pings  # prints the Telegram messages (dry run without a bot token)
python -m http.server -d docs 8000       # open http://localhost:8000
pytest
```

Set `ANTHROPIC_API_KEY` to get model-written summaries, hooks, persona lines and Hindi for new items;
without it the pipeline uses first sentences and the seed files in `data/`.

## Deploy

**GitHub Pages (current, $0):**

1. Push to `main`. The workflow in `.github/workflows/radar.yml` runs every Monday 09:00 IST,
   commits refreshed `data/` + `docs/`, and deploys to GitHub Pages.
2. In repo **Settings → Pages**, set source to **GitHub Actions**.
3. Secrets: `ANTHROPIC_API_KEY` (optional), `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (for the channel).
   Variables: `POLICY_PULSE_CHANNEL_URL` (shown as the site's CTA), `POLICY_PULSE_SITE_URL`.
4. Telegram: create a public channel, create a bot via @BotFather, add the bot as channel admin,
   set `TELEGRAM_CHAT_ID` to `@yourchannel`. The Monday run posts the digest; a daily run posts
   deadline reminders. Re-runs never double-post (see the `posts` table).

**GCP (planned):** the output is a plain static folder, so moving is trivial — sync `docs/` to a
Cloud Storage bucket behind Cloud CDN (or serve via Cloud Run + nginx), and trigger
`python -m radar run` from Cloud Scheduler → Cloud Run Job. The SQLite file moves to the bucket
or Cloud SQL when the dataset outgrows git.

## Roadmap

- [x] Phase 0 — PRS pipeline → momentum score → public site
- [x] Phase 1 — Hindi edition, confidence + caveats, this-week brief, Telegram digest + deadline pings
- [x] Phase 2 — independent cross-check (PIB / newspapers via Google News), policy journeys
- [x] Phase 3a — live consultations feed with exact deadlines; notices read for submission address; persona lens; editor's note
- [x] Phase 3b — follow topics + "since your last visit", "why is this moving?", counter-signals, connected topics, lifecycle stages
- [x] Phase 3c — state legislatures layer (PRS state Bills + briefs), states index page
- [ ] Phase 3d — email digest, exam (GS-paper) tags, Hindi hooks/"For you", Hindi audio, location relevance, PIB RSS direct feed
- [ ] Phase 4 — model-structured notice reading (format, page limits, addressee); PIB RSS as a direct feed

## Attribution

Source data © [PRS Legislative Research](https://prsindia.org), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). PRS is non-partisan and does not
endorse this project. Summaries, tagging and scores are our own and may contain errors — the
linked primary document is always authoritative.
