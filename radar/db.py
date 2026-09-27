"""SQLite persistence. One file, committed to the repo so history compounds publicly."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .parse import Item

DB_PATH = Path("data/radar.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid        TEXT PRIMARY KEY,
    month      TEXT NOT NULL,
    sector     TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    links      TEXT NOT NULL,   -- JSON list
    source_url TEXT NOT NULL,
    action     TEXT NOT NULL DEFAULT 'other',
    summary    TEXT             -- plain-English one-liner (LLM or extractive)
);
CREATE INDEX IF NOT EXISTS idx_items_month ON items(month);

CREATE TABLE IF NOT EXISTS item_topics (
    uid   TEXT NOT NULL REFERENCES items(uid) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    hits  INTEGER NOT NULL,
    PRIMARY KEY (uid, topic)
);

CREATE TABLE IF NOT EXISTS item_impacts (
    uid    TEXT NOT NULL REFERENCES items(uid) ON DELETE CASCADE,
    impact TEXT NOT NULL,
    PRIMARY KEY (uid, impact)
);

CREATE TABLE IF NOT EXISTS months (
    month      TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    n_items    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS translations (
    uid     TEXT NOT NULL REFERENCES items(uid) ON DELETE CASCADE,
    lang    TEXT NOT NULL,
    title   TEXT,
    summary TEXT,
    source  TEXT NOT NULL DEFAULT 'llm',   -- llm | seed
    PRIMARY KEY (uid, lang)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_month(conn: sqlite3.Connection, month: str, source_url: str, items: list[Item],
                 actions: dict[str, str], topics: dict[str, dict[str, int]]) -> None:
    """Replace all rows for a month atomically. Preserves existing summaries."""
    with conn:
        old_summaries = {
            r["uid"]: r["summary"]
            for r in conn.execute("SELECT uid, summary FROM items WHERE month = ?", (month,))
        }
        old_translations = conn.execute(
            "SELECT t.uid, t.lang, t.title, t.summary, t.source FROM translations t "
            "JOIN items i ON i.uid = t.uid WHERE i.month = ?", (month,)).fetchall()
        conn.execute("DELETE FROM items WHERE month = ?", (month,))
        for it in items:
            conn.execute(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)",
                (it.uid, it.month, it.sector, it.title, it.body, json.dumps(it.links),
                 it.source_url, actions.get(it.uid, "other"), old_summaries.get(it.uid)),
            )
            for topic, hits in topics.get(it.uid, {}).items():
                conn.execute("INSERT INTO item_topics VALUES (?,?,?)", (it.uid, topic, hits))
        new_uids = {it.uid for it in items}
        for r in old_translations:
            if r["uid"] in new_uids:
                conn.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?,?)", tuple(r))
        conn.execute(
            "INSERT OR REPLACE INTO months (month, source_url, n_items) VALUES (?,?,?)",
            (month, source_url, len(items)),
        )


def months_present(conn: sqlite3.Connection) -> list[str]:
    return [r["month"] for r in conn.execute("SELECT month FROM months ORDER BY month")]


OVERRIDES_PATH = Path("data/tag_overrides.json")


def load_overrides(path: Path = OVERRIDES_PATH) -> dict:
    if not path.exists():
        return {"remove": {}, "add": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"remove": data.get("remove", {}), "add": data.get("add", {})}


def retag_all(conn: sqlite3.Connection, classify, tag, tag_impacts=None) -> int:
    """Recompute action, domain tags and impact lenses for every stored item; then apply overrides."""
    rows = conn.execute("SELECT uid, month, sector, title, body, links, source_url FROM items").fetchall()
    ov = load_overrides()
    removed = {(u, topic) for topic, uids in ov["remove"].items() for u in uids}
    so_whats: dict[str, dict] = {}
    try:
        for r in conn.execute("SELECT uid, so_what FROM enrichment WHERE so_what IS NOT NULL"):
            so_whats[r["uid"]] = json.loads(r["so_what"])
    except sqlite3.OperationalError:
        pass
    with conn:
        conn.execute("DELETE FROM item_topics")
        conn.execute("DELETE FROM item_impacts")
        domains_of: dict[str, dict[str, int]] = {}
        for r in rows:
            it = Item(month=r["month"], sector=r["sector"], title=r["title"], body=r["body"],
                      links=json.loads(r["links"]), source_url=r["source_url"])
            conn.execute("UPDATE items SET action = ? WHERE uid = ?", (classify(it.title), r["uid"]))
            tags = {topic: hits for topic, hits in tag(it).items() if (r["uid"], topic) not in removed}
            domains_of[r["uid"]] = tags
            for topic, hits in tags.items():
                conn.execute("INSERT INTO item_topics VALUES (?,?,?)", (r["uid"], topic, hits))
        for topic, uids in ov["add"].items():
            for u in uids:
                conn.execute("INSERT OR REPLACE INTO item_topics VALUES (?,?,?)", (u, topic, 99))
                domains_of.setdefault(u, {})[topic] = 99
        if tag_impacts is not None:
            for r in rows:
                if not domains_of.get(r["uid"]):
                    continue  # impacts only matter for items that appear on the site
                it = Item(month=r["month"], sector=r["sector"], title=r["title"], body=r["body"])
                for imp in tag_impacts(it, domains_of[r["uid"]], so_whats.get(r["uid"])):
                    conn.execute("INSERT OR IGNORE INTO item_impacts VALUES (?,?)", (r["uid"], imp))
    return len(rows)
