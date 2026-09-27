"""Plain-English one-line summaries.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise falls back to an extractive
first-sentence summary so the pipeline never blocks on an API key.
Summaries are cached in the DB, so each item is summarised at most once.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3

log = logging.getLogger(__name__)

_PROMPT = """You write for Indian readers aged 18-30 who are not policy experts.
Rewrite the following government policy update as ONE sentence (max 30 words) explaining
what changed and why an ordinary person might care. No jargon, no hedging, no preamble.

Title: {title}
Details: {body}"""

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
# Abbreviations whose trailing period must not end a sentence
_ABBR = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|Shri|Smt|Hon|No|Nos|Rs|vs|Jr|Sr|St|Lt|Gen|Col|Sec|Art|Cl|Para|Sch|Vol|approx|etc|viz|i\.e|e\.g|U\.S|U\.K|[A-Z])\.",
    re.I,
)
_DOT = "\u2024"  # one-dot leader, restored after splitting


def _sentences(text: str) -> list[str]:
    protected = _ABBR.sub(lambda m: m.group(0)[:-1] + _DOT, text.strip())
    return [s.replace(_DOT, ".").strip() for s in _SENT.split(protected) if s.strip()]


def extractive(title: str, body: str, target: int = 240) -> str:
    sents = _sentences(body) or [title]
    out = sents[0]
    # A very short opener ("The Bill was passed.") is padded with the next sentence
    if len(out) < 90 and len(sents) > 1 and len(out) + len(sents[1]) + 1 <= target:
        out = f"{out} {sents[1]}"
    if len(out) > target:
        cut = out[:target].rsplit(" ", 1)[0]
        out = cut.rstrip(",;:") + "…"
    return out


def _claude(title: str, body: str, client) -> str:
    resp = client.messages.create(
        model=os.environ.get("RADAR_MODEL", "claude-3-5-haiku-latest"),
        max_tokens=80,
        messages=[{"role": "user", "content": _PROMPT.format(title=title, body=body[:2500])}],
    )
    return resp.content[0].text.strip()


def summarise_missing(conn: sqlite3.Connection, limit: int = 200) -> int:
    rows = conn.execute(
        """SELECT i.uid, i.title, i.body FROM items i
           WHERE i.summary IS NULL AND (i.action = 'consultation'
                 OR EXISTS (SELECT 1 FROM item_topics t WHERE t.uid = i.uid))
           ORDER BY i.month DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    if not rows:
        return 0

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception as exc:  # pragma: no cover
            log.warning("Anthropic client unavailable (%s); using extractive summaries", exc)

    n = 0
    for r in rows:
        summary = None
        if client is not None:
            try:
                summary = _claude(r["title"], r["body"], client)
            except Exception as exc:
                log.warning("Claude failed for %s: %s", r["uid"], exc)
        if not summary:
            summary = extractive(r["title"], r["body"])
        conn.execute("UPDATE items SET summary = ? WHERE uid = ?", (summary, r["uid"]))
        n += 1
    conn.commit()
    return n
