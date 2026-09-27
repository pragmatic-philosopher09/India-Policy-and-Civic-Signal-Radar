"""Editorial enrichment of items: hook headline + "so what for you" persona lines + policy chains.

    hook      one-line, question-style headline a 22-year-old would actually open
    so_what   {"student": ..., "gig": ..., "founder": ..., "salaried": ...} — only personas that matter
    chains    the same policy instrument tracked across months (committee → draft → bill → rules)

Seeded by hand in data/enrichment.json and data/chains.json; new items are enriched by Claude in
the weekly run when ANTHROPIC_API_KEY is set. The site degrades gracefully: no hook -> PRS title,
no so_what -> block hidden.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

SEED = Path("data/enrichment.json")
CHAINS = Path("data/chains.json")
PERSONAS = ("student", "gig", "founder", "salaried")
BATCH = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichment (
    uid     TEXT PRIMARY KEY,           -- item uid, or ann:<key> for live announcements
    hook    TEXT,
    so_what TEXT,                        -- JSON {persona: line}
    source  TEXT NOT NULL DEFAULT 'llm'  -- llm | seed
);
"""

_PROMPT = """You are the editor of Policy Pulse, a free weekly brief on Indian government policy for readers aged 18-30
(students, gig workers, small founders, salaried employees). For each item write:

"hook": one line (max 14 words), a plain-English question or sharp statement that says why an ordinary person
might care. No clickbait, no claims not supported by the text.
"so_what": an object with only the personas this genuinely affects, from: student, gig, founder, salaried.
Each value: one sentence, max 22 words, concrete and honest ("probably not urgent" is allowed). Omit personas
that are not affected. Return {{}} if none.

Return ONLY a JSON object mapping id -> {{"hook": ..., "so_what": {{...}}}}.

Items:
{payload}"""


def load_seed(conn: sqlite3.Connection) -> int:
    conn.executescript(SCHEMA)
    if not SEED.exists():
        return 0
    data = json.loads(SEED.read_text(encoding="utf-8"))
    existing = {r["uid"] for r in conn.execute("SELECT uid FROM items")}
    n = 0
    with conn:
        for uid, e in data.items():
            if uid.startswith("_") or (uid not in existing and not uid.startswith("ann:")):
                continue
            so_what = {k: v for k, v in (e.get("so_what") or {}).items() if k in PERSONAS and v}
            cur = conn.execute(
                "INSERT OR REPLACE INTO enrichment (uid, hook, so_what, source) VALUES (?,?,?,'seed')",
                (uid, e.get("hook"), json.dumps(so_what, ensure_ascii=False) if so_what else None))
            n += cur.rowcount
    return n


def enrich_missing(conn: sqlite3.Connection, uids: list[str], limit: int = 200) -> int:
    load_seed(conn)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as exc:  # pragma: no cover
        log.warning("Anthropic client unavailable (%s); skipping enrichment", exc)
        return 0
    done = {r["uid"] for r in conn.execute("SELECT uid FROM enrichment")}
    todo = [u for u in uids if u not in done][:limit]
    if not todo:
        return 0
    rows = {r["uid"]: r for r in conn.execute(
        "SELECT uid, title, body FROM items WHERE uid IN (%s)" % ",".join("?" * len(todo)), todo)}
    n = 0
    for i in range(0, len(todo), BATCH):
        batch = [dict(id=u, title=rows[u]["title"], text=rows[u]["body"][:1500]) for u in todo[i:i + BATCH]]
        try:
            resp = client.messages.create(
                model=os.environ.get("RADAR_MODEL", "claude-3-5-haiku-latest"), max_tokens=4000,
                messages=[{"role": "user", "content": _PROMPT.format(payload=json.dumps(batch, ensure_ascii=False))}])
            text = resp.content[0].text
            out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except Exception as exc:
            log.warning("enrichment batch failed: %s", exc)
            continue
        with conn:
            for uid, e in out.items():
                if uid not in rows:
                    continue
                so_what = {k: v for k, v in (e.get("so_what") or {}).items() if k in PERSONAS and v}
                conn.execute("INSERT OR REPLACE INTO enrichment VALUES (?,?,?,'llm')",
                             (uid, e.get("hook"), json.dumps(so_what, ensure_ascii=False) if so_what else None))
                n += 1
    return n


def lookup(conn: sqlite3.Connection) -> dict[str, dict]:
    conn.executescript(SCHEMA)
    out = {}
    for r in conn.execute("SELECT uid, hook, so_what FROM enrichment"):
        out[r["uid"]] = {"hook": r["hook"], "so_what": json.loads(r["so_what"]) if r["so_what"] else {}}
    return out


def load_chains(conn: sqlite3.Connection) -> list[dict]:
    """Chains are curated lists of uids for one instrument; steps missing from the DB are dropped."""
    if not CHAINS.exists():
        return []
    existing = {r["uid"]: dict(r) for r in conn.execute("SELECT uid, month, title, action, source_url FROM items")}
    chains = []
    for c in json.loads(CHAINS.read_text(encoding="utf-8")):
        steps = [dict(existing[u], uid=u) for u in c.get("steps", []) if u in existing]
        steps.sort(key=lambda s: (s["month"], s["uid"]))
        if len(steps) >= 2:
            chains.append(dict(c, steps=steps))
    return chains


NOTES = Path("data/editor_notes.json")

_NOTE_PROMPT = """You are the editor of Policy Pulse. Write this week's note (max 120 words, one paragraph, plain English,
no hype) for readers aged 18-30 in India. Lead with anything they can still act on (open consultations with deadlines),
then what is genuinely moving and why, then what is quiet. Every claim must come from the data below; do not invent.
Return ONLY the paragraph.

Data:
{payload}"""


def editor_note(conn: sqlite3.Connection, week: str, lang: str, payload: dict | None = None) -> str | None:
    """Seeded note for the ISO week if present; else Claude (English only) when a key exists."""
    if NOTES.exists():
        data = json.loads(NOTES.read_text(encoding="utf-8"))
        note = (data.get(week) or {}).get(lang)
        if note:
            return note
    if lang != "en" or payload is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(model=os.environ.get("RADAR_MODEL", "claude-3-5-haiku-latest"), max_tokens=400,
                                      messages=[{"role": "user", "content": _NOTE_PROMPT.format(payload=json.dumps(payload, ensure_ascii=False))}])
        text = resp.content[0].text.strip()
        data = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {}
        data.setdefault(week, {})[lang] = text
        NOTES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return text
    except Exception as exc:  # pragma: no cover
        log.warning("editor note failed: %s", exc)
        return None
