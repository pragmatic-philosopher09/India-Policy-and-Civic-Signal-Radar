"""Notice intelligence for consultations: recover deadlines and channels PRS didn't state.

Sources, in order of trust:
  1. PRS's own text ("comments are invited till 4 September 2026")
  2. Headlines of independent coverage ("BCI ... Invites Suggestions Till 31 July")
  3. Curated overrides in data/respond_overrides.json (a human or model read the notice)
  4. Inference: a draft older than ~60 days with no known deadline is *likely closed*
     (India's Pre-Legislative Consultation Policy prescribes 30-day windows).

Every recovered value carries its source so the UI can say where it came from.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

OVERRIDES = Path("data/respond_overrides.json")
LIKELY_CLOSED_AFTER_DAYS = 60

_MONTH = "(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_HEADLINE_DEADLINE = re.compile(
    r"(?:till|until|by|before|deadline|last date|closes?|ends?)\s*[:\-–]?\s*"
    rf"((?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?)(?:,?\s*\d{{4}})?)",
    re.I,
)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _parse_loose(text: str, year_hint: int) -> date | None:
    t = re.sub(r"(\d)(st|nd|rd|th)", r"\1", text).replace(",", " ")
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?", t) or re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?", t)
    if not m:
        return None
    g = m.groups()
    if g[0].isdigit():
        day, mon, yr = int(g[0]), g[1], g[2]
    else:
        mon, day, yr = g[0], int(g[1]), g[2]
    mi = next((i for i, n in enumerate(_MONTHS) if mon.lower().startswith(n)), None)
    if mi is None:
        return None
    try:
        return date(int(yr) if yr else year_hint, mi + 1, day)
    except ValueError:
        return None


def deadline_from_headlines(hits: list[dict], month: str) -> tuple[date | None, str | None]:
    """Scan corroborating headlines for a stated deadline. Returns (date, outlet)."""
    year = int(month[:4])
    for h in hits:
        if h.get("kind") == "other":
            continue
        m = _HEADLINE_DEADLINE.search(h.get("title") or "")
        if m:
            d = _parse_loose(m.group(1), year)
            if d and abs((d - date(year, int(month[5:7]), 1)).days) < 200:
                return d, h.get("outlet")
    return None, None


def load_overrides() -> dict:
    if not OVERRIDES.exists():
        return {}
    return {k: v for k, v in json.loads(OVERRIDES.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def apply(entry: dict, today: date) -> dict:
    """Enrich one consultation entry in place with recovered deadline/route info."""
    ov = load_overrides().get(entry["uid"], {})
    entry.setdefault("deadline_source", "prs" if entry.get("deadline") else None)

    if ov.get("deadline") and entry.get("deadline_source") != "prs-announcements":
        entry["deadline"] = datetime.strptime(ov["deadline"], "%Y-%m-%d").date()
        entry["deadline_source"] = ov.get("source_name", "notice")
    elif not entry.get("deadline"):
        d, outlet = deadline_from_headlines(entry.get("corroborations", []), entry["month"])
        if d:
            entry["deadline"], entry["deadline_source"] = d, outlet

    if ov.get("body"):
        entry["route"] = dict(entry["route"], body=ov["body"], url=ov.get("url", entry["route"].get("url", "")),
                              how=ov.get("how", entry["route"].get("how", "generic")))
    if ov.get("email"):
        entry["email"] = ov["email"]
    if ov.get("note"):
        entry["route_note"] = ov["note"]

    entry["days_left"] = (entry["deadline"] - today).days if entry.get("deadline") else None
    y, m = int(entry["month"][:4]), int(entry["month"][5:7])
    entry["age_days"] = (today - date(y, m, 1)).days
    entry["likely_closed"] = entry["deadline"] is None and entry["age_days"] > LIKELY_CLOSED_AFTER_DAYS
    return entry


# ---------------------------------------------------------------------------
# Reading the notice itself (PDF or ministry page) for the submission address.

import io
import json as _json
import logging
import sqlite3

import requests
from bs4 import BeautifulSoup

from .config import USER_AGENT

log = logging.getLogger(__name__)

FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_facts (
    url         TEXT PRIMARY KEY,
    emails      TEXT,     -- JSON list
    addressee   TEXT,     -- "Parliamentary Standing Committee on Finance" / "Department of Atomic Energy"
    deadline    TEXT,     -- ISO, if the notice states one
    excerpt     TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ADDRESSEE = re.compile(
    r"((?:Parliamentary |Joint |Select )?(?:Standing )?Committee on [A-Z][\w,&\- ]{2,60}?(?=\s(?:headed|under|chaired|have|has|,|\.))"
    r"|Joint (?:Parliamentary )?Committee[\w ,&\-]{0,40}"
    r"|Ministry of [A-Z][\w,&\- ]{2,50}?(?=\s(?:has|have|invites|released|,|\.))"
    r"|Department of [A-Z][\w,&\- ]{2,50}?(?=\s(?:has|have|invites|released|,|\.))"
    r"|Bar Council of India|Reserve Bank of India|Securities and Exchange Board of India)"
)
_DOMAIN_BODY = {
    "dae.gov.in": "Department of Atomic Energy", "meity.gov.in": "Ministry of Electronics & IT",
    "mohfw.gov.in": "Ministry of Health and Family Welfare", "mohfw-dohfw.gov.in": "Ministry of Health and Family Welfare",
    "dfpd.gov.in": "Department of Food and Public Distribution", "barcouncilofindia.org": "Bar Council of India",
    "labour.gov.in": "Ministry of Labour and Employment", "dot.gov.in": "Department of Telecommunications",
    "rbi.org.in": "Reserve Bank of India", "sebi.gov.in": "SEBI", "trai.gov.in": "TRAI", "ugc.gov.in": "UGC",
    "mospi.gov.in": "Ministry of Statistics and Programme Implementation", "mha.gov.in": "Ministry of Home Affairs",
    "dpiit.gov.in": "DPIIT", "education.gov.in": "Ministry of Education", "moef.gov.in": "Ministry of Environment",
}
_DEADLINE_TXT = re.compile(
    r"(?:by|till|until|before|on or before|latest by|not later than)\s+"
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+,?\s+\d{4})|(?:[A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}))"
)


def _text_of(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=40)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    if "pdf" in ct or url.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(resp.content))
        text = " ".join((p.extract_text() or "") for p in reader.pages[:6])
    else:
        text = BeautifulSoup(resp.text, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def read_notice(conn: sqlite3.Connection, url: str, refresh_days: int = 14) -> dict | None:
    """Fetch and mine a notice once; cached in notice_facts."""
    conn.executescript(FACTS_SCHEMA)
    row = conn.execute("SELECT * FROM notice_facts WHERE url = ? AND fetched_at > datetime('now', ?)",
                       (url, f"-{refresh_days} days")).fetchone()
    if row:
        return dict(row, emails=_json.loads(row["emails"] or "[]"))
    try:
        text = _text_of(url)
    except Exception as exc:
        log.warning("could not read notice %s: %s", url, exc)
        return None
    emails = sorted({e.rstrip(".") for e in _EMAIL.findall(text) if not e.lower().endswith(("prsindia.org",))})
    ctx0 = re.search(r".{0,400}(?:views|suggestions|comments|feedback).{0,400}", text, re.I)
    m = _ADDRESSEE.search(ctx0.group(0)) if ctx0 else None
    if not m:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().removeprefix("www.")
        addressee = next((v for k, v in _DOMAIN_BODY.items() if host == k or host.endswith("." + k)), None)
        if addressee is None and "sansad.in" in " ".join(emails):
            m = _ADDRESSEE.search(text)
    else:
        addressee = None
    if m:
        addressee = m.group(1).strip(" ,")
    d = None
    dm = _DEADLINE_TXT.search(text)
    if dm:
        d = _parse_loose(dm.group(1), date.today().year)
    ctx = re.search(r".{0,160}(?:views|suggestions|comments|feedback).{0,240}", text, re.I)
    excerpt = ctx.group(0).strip() if ctx else text[:300]
    with conn:
        conn.execute("INSERT OR REPLACE INTO notice_facts (url, emails, addressee, deadline, excerpt) VALUES (?,?,?,?,?)",
                     (url, _json.dumps(emails), addressee, d.isoformat() if d else None, excerpt))
    return dict(url=url, emails=emails, addressee=addressee, deadline=d.isoformat() if d else None, excerpt=excerpt)


def facts_for(conn: sqlite3.Connection, urls: list[str]) -> dict | None:
    """Best available facts across a consultation's notice URLs (cached rows only; no fetching)."""
    conn.executescript(FACTS_SCHEMA)
    for u in urls:
        if not u:
            continue
        row = conn.execute("SELECT * FROM notice_facts WHERE url = ?", (u,)).fetchone()
        if row and (row["emails"] not in (None, "[]") or row["addressee"]):
            return dict(row, emails=_json.loads(row["emails"] or "[]"))
    return None
