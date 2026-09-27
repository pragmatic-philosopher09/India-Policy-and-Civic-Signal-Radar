"""Push the weekly brief and deadline reminders to a Telegram channel.

    python -m radar notify --digest     # Monday: 3 signals + open consultations
    python -m radar notify --pings      # daily: "7 days left" / "48 hours left" reminders

Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID the messages are printed (dry run), so the
composition can be checked locally. Sent messages are recorded in the ``posts`` table so a
re-run of the workflow never double-posts.
"""

from __future__ import annotations

import html
import logging
import os
import sqlite3
from datetime import date

import requests

from .config import CHANNEL_URL, RECENT_WINDOW
from .i18n import nice_date, pretty_month, t

log = logging.getLogger(__name__)

SITE = os.environ.get("POLICY_PULSE_SITE_URL", "https://pragmatic-philosopher09.github.io/India-Policy-and-Civic-Signal-Radar/")
API = "https://api.telegram.org/bot{token}/sendMessage"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    key       TEXT PRIMARY KEY,
    posted_at TEXT NOT NULL DEFAULT (datetime('now')),
    preview   TEXT
);
"""

STATUS_EMOJI = {"heating": "🔥", "steady": "➡️", "cooling": "🧊", "quiet": "😴"}


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def compose_digest(scores, consultations: dict, today: date, lang: str = "en") -> str:
    brief_moving = [ts for ts in scores if ts.status != "quiet"][:3]
    quiet = [ts for ts in scores if ts.status == "quiet"]
    lines = [f"<b>📡 Policy Pulse · {nice_date(today, lang)}</b>", ""]

    open_items = consultations["open"] + consultations["unknown"]
    lines.append("<b>🗣 " + _esc(t(lang, "respond_h2")) + "</b>")
    if open_items:
        for c in open_items[:5]:
            if c["deadline"]:
                due = (t(lang, "closes_today") if c["days_left"] == 0 else t(lang, "days_left", n=c["days_left"])) \
                      + f" · {nice_date(c['deadline'], lang)}"
            else:
                due = t(lang, "deadline_unknown")
            link = c["links"][0] if c["links"] else c["source_url"]
            label = (c.get("hook") if lang == "en" and c.get("hook") else (c.get("title_l") or c["title"]))
            lines.append(f"• <a href=\"{_esc(link)}\">{_esc(label)}</a> — {_esc(due)}")
    else:
        lines.append("• " + _esc(t(lang, "respond_none")))
    lines.append("")

    lines.append("<b>🔥 " + _esc(t(lang, "moving_h2")) + "</b>")
    for ts in brief_moving:
        change = (f"{'+' if ts.change_pct > 0 else ''}{ts.change_pct}%" if ts.change_pct is not None else t(lang, "new"))
        conf = t(lang, f"conf_{ts.confidence}")
        url = f"{SITE}{'' if lang == 'en' else lang + '/'}topic/{ts.topic.slug}.html"
        lines.append(f"{STATUS_EMOJI.get(ts.status, '•')} <a href=\"{url}\">{_esc(ts.topic.label(lang))}</a> · "
                     f"{_esc(t(lang, f'status_{ts.status}'))} {change} · {_esc(conf)} {_esc(t(lang, 'confidence_word'))}")
        for e in ts.why[:2]:
            mark = " ✓" if e.corroborated else ""
            label = e.hook if lang == "en" and e.hook else (e.title_l or e.title)
            lines.append(f"    ↳ {_esc(label)}{mark}")
    lines.append("")

    if quiet:
        lines.append("😴 <b>" + _esc(t(lang, "quiet_h2")) + ":</b> " + _esc(" · ".join(ts.topic.label(lang) for ts in quiet)))
        lines.append("")

    lines.append("<i>" + _esc(t(lang, "brief_footer")) + "</i> ✓ = " + _esc(t(lang, "corroborated").lower()))
    lines.append(f"<a href=\"{SITE}{'' if lang == 'en' else lang + '/'}\">{_esc(t(lang, 'site_title'))}</a>")
    return "\n".join(lines)


def compose_pings(consultations: dict, lang: str = "en") -> list[tuple[str, str]]:
    """Return [(dedupe_key, message)] for consultations at the 7-day and 48-hour marks."""
    out = []
    for c in consultations["open"]:
        d = c["days_left"]
        stage = "7d" if 3 <= d <= 7 else "48h" if 0 <= d <= 2 else None
        if not stage:
            continue
        due = t(lang, "closes_today") if d == 0 else t(lang, "one_day_left") if d == 1 else t(lang, "days_left", n=d)
        link = c["links"][0] if c["links"] else c["source_url"]
        label = (c.get("hook") if lang == "en" and c.get("hook") else (c.get("title_l") or c["title"]))
        msg = (f"⏰ <b>{_esc(due)}</b> · {nice_date(c['deadline'], lang)}\n"
               f"<a href=\"{_esc(link)}\">{_esc(label)}</a>\n"
               f"{_esc(t(lang, 'respond_via', body=c['route']['body']) if c['route']['body'] else t(lang, 'route_generic'))}\n"
               f"<a href=\"{SITE}\">{_esc(t(lang, 'how_to_respond'))} →</a>")
        out.append((f"ping:{c['uid']}:{stage}", msg))
    return out


def send(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("--- DRY RUN (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) ---")
        print(text)
        print("---")
        return False
    resp = requests.post(API.format(token=token), timeout=30, json={
        "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if resp.status_code != 200:
        log.error("telegram error %s: %s", resp.status_code, resp.text[:300])
        return False
    return True


def _already(conn: sqlite3.Connection, key: str) -> bool:
    conn.executescript(SCHEMA)
    return conn.execute("SELECT 1 FROM posts WHERE key = ?", (key,)).fetchone() is not None


def _record(conn: sqlite3.Connection, key: str, preview: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO posts (key, preview) VALUES (?,?)", (key, preview[:200]))


def notify(conn: sqlite3.Connection, scores, consultations: dict, *, digest: bool, pings: bool,
           today: date | None = None, lang: str = "en") -> int:
    today = today or date.today()
    sent = 0
    if digest:
        key = f"digest:{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}:{lang}"
        if _already(conn, key):
            log.info("digest %s already posted", key)
        else:
            text = compose_digest(scores, consultations, today, lang)
            if send(text):
                _record(conn, key, text)
                sent += 1
    if pings:
        for key, msg in compose_pings(consultations, lang):
            if _already(conn, key):
                continue
            if send(msg):
                _record(conn, key, msg)
                sent += 1
    return sent
