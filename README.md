# India Policy Signal Radar

**Which everyday policy issues in India are gaining momentum — with the evidence, not just a score.**

A free, open, no-login public site for 18–30 year-old Indians (students, first-time voters,
early-career professionals) that tracks government activity on the issues that hit their lives —
jobs, exams, taxes, digital rights, gig work — and shows how attention on each has built up
over time.

## What makes it different

News tells you what happened today. Aggregators (PolicyRadar, PolicyStory) tell you what
happened this week. This shows what has been **building over months**:

- **Evidence-weighted momentum score** — a law passed counts more than a committee report;
  the score compares the last 3 months to the 6 before.
- **Fully transparent** — every score links to every PRS item that produced it. You can
  recompute any number by hand. See `/method.html`.
- **Persistent, compounding history** — a SQLite database committed to the repo grows every week.
- **"Open for your input"** — drafts currently out for public comment, with deadlines
  extracted, so citizen feedback lands while it still matters.
- **Machine-readable** — `docs/radar.json` is free for anyone to build on.

## How it works

```
PRS Monthly Policy Review (CC BY 4.0)
        │  weekly GitHub Actions cron, 10s crawl-delay
        ▼
  radar/parse.py      → structured items (month, ministry, title, body, links)
  radar/score.py      → action type (enacted / introduced / rules / consultation …)
                      → topic tags (keyword match against radar/config.py)
                      → momentum: Σ weights per month, recent vs baseline
  radar/summarize.py  → one-line plain-English summary (Claude, or extractive fallback)
  radar/build_site.py → static HTML + JSON into docs/ → GitHub Pages
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m radar run --months 18          # ingest (cached), summarise, build
python -m http.server -d docs 8000       # open http://localhost:8000
pytest
```

Set `ANTHROPIC_API_KEY` to get LLM summaries; without it the pipeline uses the first sentence
of each item.

## Deploy

1. Push to `main`. The workflow in `.github/workflows/radar.yml` runs every Monday 09:00 IST,
   commits refreshed `data/` + `docs/`, and deploys to GitHub Pages.
2. In repo **Settings → Pages**, set source to **GitHub Actions**.
3. Optionally add `ANTHROPIC_API_KEY` under **Settings → Secrets**.

## Roadmap

- [x] Phase 0 — PRS pipeline → momentum score → public site
- [ ] Phase 1 — weekly digest (email / Telegram / WhatsApp channel), shareable topic cards
- [ ] Phase 2 — second independent source (PIB press releases) for cross-validation
- [ ] Phase 3 — state legislatures, more topics, story-mode narratives

## Attribution

Source data © [PRS Legislative Research](https://prsindia.org), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). PRS is non-partisan and does not
endorse this project. Summaries, tagging and scores are our own and may contain errors — the
linked primary document is always authoritative.
