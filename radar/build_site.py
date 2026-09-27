"""Render the static site into docs/ (served by GitHub Pages)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
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
STATUS_LABELS = {
    "heating": "Heating up",
    "steady": "Steady",
    "cooling": "Cooling",
    "quiet": "Quiet",
}


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
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s)
    return env


_DEADLINE = re.compile(
    r"(?:comments?|suggestions?|feedback)[^.]{0,80}?\b(?:by|till|until|before|up to)\s+"
    r"((?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(?:\d{1,2},?\s*)?\d{4})",
    re.I,
)


def _deadline(body: str) -> str | None:
    m = _DEADLINE.search(body)
    return m.group(1).strip() if m else None


def _open_consultations(conn, months: list[str], topic_of: dict[str, str]) -> list[dict]:
    """Every draft released for comment in the last two reviews, with any topic tag and deadline."""
    if not months:
        return []
    window = months[-2:]
    rows = conn.execute(
        f"""SELECT uid, month, sector, title, body, summary, links, source_url FROM items
            WHERE action = 'consultation' AND month IN ({','.join('?' * len(window))})
            ORDER BY month DESC, sector""",
        window,
    ).fetchall()
    out = []
    for r in rows:
        slug = topic_of.get(r["uid"])
        out.append(dict(
            title=r["title"], month=r["month"], sector=r["sector"], summary=r["summary"],
            links=json.loads(r["links"]), source_url=r["source_url"],
            deadline=_deadline(r["body"]), topic=TOPIC_BY_SLUG.get(slug) if slug else None,
        ))
    return out


def build() -> None:
    conn = db.connect()
    scores = score_topics(conn)
    env = _env()
    today = date.today().isoformat()
    ctx = dict(
        attribution=PRS_ATTRIBUTION, today=today, recent_window=RECENT_WINDOW,
        baseline_window=BASELINE_WINDOW, action_weights=ACTION_WEIGHTS, action_labels=ACTION_LABELS,
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
    (OUT / "index.html").write_text(env.get_template("index.html").render(
        scores=scores, consultations=_open_consultations(conn, months, topic_of),
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
            "slug": ts.topic.slug, "name": ts.topic.name, "score": ts.score, "status": ts.status,
            "change_pct": ts.change_pct, "recent_avg": ts.recent_avg, "baseline_avg": ts.baseline_avg,
            "months": ts.months, "activity": ts.activity, "counts": ts.counts,
        } for ts in scores],
        "attribution": PRS_ATTRIBUTION,
    }, indent=1), encoding="utf-8")
    print(f"built {len(scores)} topic pages -> {OUT}/")
