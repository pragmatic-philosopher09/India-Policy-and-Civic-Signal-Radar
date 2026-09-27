"""Translate item titles/summaries into other languages.

Uses Claude when ANTHROPIC_API_KEY is set (batched, JSON in/out), caches results in the
``translations`` table, and never blocks the build: items without a translation fall back
to English with a small language tag in the UI.

Seed translations (hand-written, source='seed') can be loaded from data/translations.<lang>.json
so a new language is usable before any API key exists.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

SEED_DIR = Path("data")
BATCH = 15

_PROMPT = """You translate Indian government policy updates into natural, everyday {language} for readers aged 18-30.
Keep proper nouns, Act names, acronyms (GST, UPI, RBI, SEBI, AI, IT Rules) and numbers as they are commonly written in {language} media.
Do not add or drop information. Keep titles short.

Return ONLY a JSON object mapping each id to an object with "title" and "summary" (summary may be null if input summary is null).

Input:
{payload}"""

LANGUAGE_NAMES = {"hi": "Hindi (Devanagari script)"}


def visible_uids(conn: sqlite3.Connection) -> list[str]:
    """Items that actually appear on the site: topic-tagged, or recent consultations."""
    months = [r["month"] for r in conn.execute("SELECT month FROM months ORDER BY month DESC LIMIT 2")]
    q = ("SELECT DISTINCT i.uid FROM items i LEFT JOIN item_topics t ON t.uid = i.uid "
         "WHERE t.uid IS NOT NULL OR (i.action = 'consultation' AND i.month IN (%s)) ORDER BY i.month DESC"
         % ",".join("?" * len(months)))
    return [r["uid"] for r in conn.execute(q, months)]


def load_seed(conn: sqlite3.Connection, lang: str) -> int:
    path = SEED_DIR / f"translations.{lang}.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    existing = {r["uid"] for r in conn.execute("SELECT uid FROM items")}
    n = 0
    with conn:
        for uid, tr in data.items():
            if uid not in existing:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO translations (uid, lang, title, summary, source) VALUES (?,?,?,?,'seed')",
                (uid, lang, tr.get("title"), tr.get("summary")),
            )
            n += cur.rowcount
    return n


def _claude_batch(client, lang: str, rows: list[sqlite3.Row]) -> dict[str, dict]:
    payload = json.dumps([{"id": r["uid"], "title": r["title"], "summary": r["summary"]} for r in rows],
                         ensure_ascii=False)
    resp = client.messages.create(
        model=os.environ.get("RADAR_MODEL", "claude-3-5-haiku-latest"),
        max_tokens=4000,
        messages=[{"role": "user", "content": _PROMPT.format(language=LANGUAGE_NAMES.get(lang, lang), payload=payload)}],
    )
    text = resp.content[0].text.strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def translate_missing(conn: sqlite3.Connection, lang: str, limit: int = 300) -> int:
    load_seed(conn, lang)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as exc:  # pragma: no cover
        log.warning("Anthropic client unavailable (%s); skipping translation", exc)
        return 0

    uids = visible_uids(conn)
    done = {r["uid"] for r in conn.execute("SELECT uid FROM translations WHERE lang = ?", (lang,))}
    todo = [u for u in uids if u not in done][:limit]
    if not todo:
        return 0
    rows = {r["uid"]: r for r in conn.execute(
        "SELECT uid, title, summary FROM items WHERE uid IN (%s)" % ",".join("?" * len(todo)), todo)}
    n = 0
    for i in range(0, len(todo), BATCH):
        batch = [rows[u] for u in todo[i:i + BATCH]]
        try:
            out = _claude_batch(client, lang, batch)
        except Exception as exc:
            log.warning("translation batch failed: %s", exc)
            continue
        with conn:
            for uid, tr in out.items():
                if uid in rows and tr.get("title"):
                    conn.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?,'llm')",
                                 (uid, lang, tr["title"], tr.get("summary")))
                    n += 1
    return n


def lookup(conn: sqlite3.Connection, lang: str) -> dict[str, dict]:
    return {r["uid"]: {"title": r["title"], "summary": r["summary"]}
            for r in conn.execute("SELECT uid, title, summary FROM translations WHERE lang = ?", (lang,))}
