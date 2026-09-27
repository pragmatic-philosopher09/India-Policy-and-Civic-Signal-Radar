"""Render the static site into docs/ (served by GitHub Pages)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import db
from .config import ACTION_WEIGHTS, BASELINE_WINDOW, PRS_ATTRIBUTION, RECENT_WINDOW, TOPIC_BY_SLUG
from .score import score_topics

OUT = Path("docs")
HERE = Path(__file__).parent

ACTION_LABELS = {
    "enacted": "Law passed",
    "introduced": "Bill introduced",
    "rules": "Rules notified",
    "cabinet": "Cabinet approval",
    "consultation": "Open for comment",
    "committee": "Committee report",
    "scheme": "Scheme / programme",
    "court": "Court ruling",
    "other": "Update",
}
# Plain-English glossary shown as tooltips and on the method page
ACTION_GLOSSARY = {
    "enacted": "Parliament passed it (or it got the President's assent). It is now law, though it may take effect later.",
    "introduced": "A Bill was tabled in Parliament. It can still change, be sent to a committee, or lapse.",
    "rules": "The government issued binding rules, regulations or a notification under an existing law. No vote needed.",
    "cabinet": "The Union Cabinet approved a proposal — usually the step before a Bill is introduced or a scheme launches.",
    "consultation": "A draft was published for public comment. Anyone can respond before the deadline.",
    "committee": "A parliamentary committee of MPs from all parties examined an issue and made recommendations. Not binding.",
    "scheme": "A government programme was launched, approved or expanded.",
    "court": "A court ruling or stay that changes how a law works in practice.",
    "other": "A statement, report or development that doesn't fit the categories above.",
}
STATUS_LABELS = {
    "heating": "Heating up",
    "steady": "Steady",
    "cooling": "Cooling",
    "quiet": "Quiet",
}
STATUS_GLOSSARY = {
    "heating": "Recent activity is at least 50% above the prior six-month average.",
    "steady": "Recent activity is roughly in line with the prior six months.",
    "cooling": "Recent activity is at least a third below the prior six-month average.",
    "quiet": "Almost no tagged government action in the last three months.",
}
CONFIDENCE_LABELS = {"low": "Low", "medium": "Medium", "high": "High"}

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d %B, %Y")


def parse_deadline(text: str | None) -> date | None:
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.replace(",", ", ")).replace(" ,", ",").strip()
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def pretty_month(ym: str) -> str:
    y, m = ym.split("-")
    return date(int(y), int(m), 1).strftime("%b %Y")


def sparkline(values: list[float], w: int = 160, h: int = 36) -> Markup:
    """Inline SVG polyline; no JS needed."""
    if not values:
        return Markup("")
    mx = max(max(values), 1.0)
    n = len(values)
    step = w / max(n - 1, 1)
    pts = " ".join(f"{i * step:.1f},{h - (v / mx) * (h - 4) - 2:.1f}" for i, v in enumerate(values))
    return Markup(
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" class="spark" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["month"] = pretty_month
    env.filters["spark"] = sparkline
    env.filters["action"] = lambda a: ACTION_LABELS.get(a, a)
    env.filters["action_help"] = lambda a: ACTION_GLOSSARY.get(a, "")
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s)
    env.filters["status_help"] = lambda s: STATUS_GLOSSARY.get(s, "")
    env.filters["confidence"] = lambda c: CONFIDENCE_LABELS.get(c, c)
    env.filters["nice_date"] = lambda d: d.strftime("%-d %b %Y") if d else ""
    return env


_DEADLINE = re.compile(
    r"(?:comments?|suggestions?|feedback|objections?|responses?)[^.]{0,80}?\b(?:by|till|until|before|up to|latest by|on or before)\s+"
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?\d{4})",
    re.I,
)


def _deadline(body: str) -> str | None:
    m = _DEADLINE.search(body)
    return m.group(1).strip() if m else None


def _consultations(conn, months: list[str], topic_of: dict[str, str], today: date) -> dict[str, list[dict]]:
    """Drafts released for comment in the last two reviews, split into open / unknown-deadline / closed."""
    if not months:
        return {"open": [], "unknown": [], "closed": []}
    window = months[-2:]
    rows = conn.execute(
        f"""SELECT uid, month, sector, title, body, summary, links, source_url FROM items
            WHERE action = 'consultation' AND month IN ({','.join('?' * len(window))})
            ORDER BY month DESC, sector""",
        window,
    ).fetchall()
    buckets: dict[str, list[dict]] = {"open": [], "unknown": [], "closed": []}
    for r in rows:
        slug = topic_of.get(r["uid"])
        raw = _deadline(r["body"])
        due = parse_deadline(raw)
        days_left = (due - today).days if due else None
        entry = dict(
            title=r["title"], month=r["month"], sector=r["sector"], summary=r["summary"],
            links=json.loads(r["links"]), source_url=r["source_url"],
            deadline=due, deadline_raw=raw, days_left=days_left,
            topic=TOPIC_BY_SLUG.get(slug) if slug else None,
        )
        if due is None:
            buckets["unknown"].append(entry)
        elif days_left >= 0:
            buckets["open"].append(entry)
        else:
            buckets["closed"].append(entry)
    buckets["open"].sort(key=lambda e: e["days_left"])
    buckets["closed"].sort(key=lambda e: e["days_left"], reverse=True)
    return buckets


def build() -> None:
    conn = db.connect()
    scores = score_topics(conn)
    env = _env()
    today_d = date.today()
    today = today_d.isoformat()
    ctx = dict(
        attribution=PRS_ATTRIBUTION, today=today, recent_window=RECENT_WINDOW,
        baseline_window=BASELINE_WINDOW, action_weights=ACTION_WEIGHTS, action_labels=ACTION_LABELS,
        action_glossary=ACTION_GLOSSARY, status_glossary=STATUS_GLOSSARY, status_labels=STATUS_LABELS,
    )

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "topic").mkdir(parents=True)
    shutil.copytree(HERE / "static", OUT / "static")
    (OUT / ".nojekyll").write_text("")

    n_months = len(scores[0].months) if scores else 0
    n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    months = scores[0].months if scores else db.months_present(conn)
    topic_of = {r["uid"]: r["topic"] for r in conn.execute(
        "SELECT uid, topic FROM item_topics ORDER BY hits")}  # highest-hit topic wins
    consultations = _consultations(conn, months, topic_of, today_d)
    (OUT / "index.html").write_text(env.get_template("index.html").render(
        scores=scores, consultations=consultations,
        n_months=n_months, n_items=n_items, **ctx), encoding="utf-8")

    for ts in scores:
        by_month: dict[str, list] = {}
        for e in ts.evidence:
            by_month.setdefault(e.month, []).append(e)
        timeline = [(m, by_month.get(m, [])) for m in reversed(ts.months)]
        (OUT / "topic" / f"{ts.topic.slug}.html").write_text(
            env.get_template("topic.html").render(ts=ts, timeline=timeline, **ctx), encoding="utf-8")

    (OUT / "method.html").write_text(env.get_template("method.html").render(**ctx), encoding="utf-8")

    # Machine-readable export for anyone who wants to build on top
    (OUT / "radar.json").write_text(json.dumps({
        "generated": today,
        "topics": [{
            "slug": ts.topic.slug, "name": ts.topic.name, "status": ts.status,
            "confidence": ts.confidence, "change_pct": ts.change_pct,
            "recent_avg": ts.recent_avg, "baseline_avg": ts.baseline_avg,
            "total_actions": ts.total_items, "recent_actions": ts.recent_items, "breadth": ts.breadth,
            "sort_score": ts.score, "months": ts.months, "activity": ts.activity, "counts": ts.counts,
        } for ts in scores],
        "consultations": [{
            "title": c["title"], "sector": c["sector"], "deadline": c["deadline"].isoformat() if c["deadline"] else None,
            "status": k, "source": c["source_url"], "links": c["links"],
        } for k in ("open", "unknown", "closed") for c in consultations[k]],
        "attribution": PRS_ATTRIBUTION,
    }, indent=1), encoding="utf-8")
    print(f"built {len(scores)} topic pages -> {OUT}/")
