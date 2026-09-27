"""Render the static site into docs/ (served by GitHub Pages), once per language.

    docs/index.html, docs/topic/<slug>.html, docs/method.html      -> English
    docs/hi/index.html, docs/hi/topic/<slug>.html, docs/hi/method.html -> Hindi
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import db
from .config import ACTION_WEIGHTS, BASELINE_WINDOW, CHANNEL_URL, PRS_ATTRIBUTION, RECENT_WINDOW, TOPIC_BY_SLUG
from .crosscheck import lookup as corroborations_for
from .enrich import PERSONAS, load_chains, lookup as enrichment_for
from .i18n import DEFAULT_LANG, LANGS, nice_date, pretty_month, sector_label, t
from .score import score_topics
from .translate import lookup as translations_for

OUT = Path("docs")
HERE = Path(__file__).parent

ACTIONS = ("enacted", "introduced", "rules", "cabinet", "consultation", "committee", "scheme", "court", "other")

_DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d %B, %Y")


def parse_deadline(text: str | None) -> date | None:
    if not text:
        return None
    s = re.sub(r"\s+", " ", text.replace(",", ", ")).replace(" ,", ",").strip()
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


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


def _env(lang: str) -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.globals["lang"] = lang
    env.globals["langs"] = LANGS
    env.globals["personas"] = PERSONAS
    env.globals["t"] = lambda key, **kw: Markup(t(lang, key, **kw))
    env.filters["month"] = lambda ym: pretty_month(ym, lang)
    env.filters["nice_date"] = lambda d: nice_date(d, lang)
    env.filters["sector"] = lambda x: sector_label(x, lang)
    env.filters["spark"] = sparkline
    env.filters["action"] = lambda a: t(lang, f"act_{a}")
    env.filters["action_help"] = lambda a: t(lang, f"gl_{a}")
    env.filters["status"] = lambda s: t(lang, f"status_{s}")
    env.filters["status_help"] = lambda s: t(lang, f"status_help_{s}")
    env.filters["confidence"] = lambda c: t(lang, f"conf_{c}")
    env.filters["msg"] = lambda pair: Markup(_render_msg(lang, pair))
    return env


def _render_msg(lang: str, pair: tuple[str, dict]) -> str:
    key, params = pair
    params = dict(params)
    if key == "caveat_quiet" and params.get("n") == 0:
        key = "caveat_quiet_zero"
    if "month" in params:
        params["month"] = pretty_month(params["month"], lang)
    return t(lang, key, **params)


# Where comments on a draft normally go, by issuing body. The exact address is always in the
# notice itself, so every route also says "check the draft".
RESPOND_ROUTES = [
    (re.compile(r"\bRBI\b|Reserve Bank", re.I), dict(body="RBI", url="https://www.rbi.org.in", how="rbi")),
    (re.compile(r"\bSEBI\b", re.I), dict(body="SEBI", url="https://www.sebi.gov.in/reports-and-statistics/reports/reports-for-public-comments.html", how="sebi")),
    (re.compile(r"\bTRAI\b", re.I), dict(body="TRAI", url="https://www.trai.gov.in/release-publication/consultation", how="trai")),
    (re.compile(r"\bIRDAI\b", re.I), dict(body="IRDAI", url="https://irdai.gov.in", how="generic")),
    (re.compile(r"\bPFRDA\b", re.I), dict(body="PFRDA", url="https://www.pfrda.org.in", how="generic")),
    (re.compile(r"\bUGC\b|University Grants", re.I), dict(body="UGC", url="https://www.ugc.gov.in", how="generic")),
    (re.compile(r"\bMeitY\b|Electronics and Information Technology|IT Rules", re.I), dict(body="MeitY", url="https://www.meity.gov.in", how="email")),
    (re.compile(r"Department of Telecommunication|\bDoT\b|Telecom(?:munications)? \(", re.I), dict(body="DoT", url="https://dot.gov.in", how="email")),
    (re.compile(r"Ministry of ([A-Z][\w ,&]+?)(?: has| released| invited| notified|\.|,)", re.I), dict(body="ministry", url="https://www.mygov.in/group-issue/", how="email")),
]


def respond_route(title: str, body: str) -> dict:
    text = f"{title}\n{body[:600]}"
    for pat, route in RESPOND_ROUTES:
        m = pat.search(text)
        if m:
            r = dict(route)
            if r["body"] == "ministry":
                r["body"] = "Ministry of " + m.group(1).strip()
            return r
    return dict(body="", url="", how="generic")


_DEADLINE = re.compile(
    r"(?:comments?|suggestions?|feedback|objections?|responses?)[^.]{0,80}?\b(?:by|till|until|before|up to|latest by|on or before)\s+"
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?\d{4})",
    re.I,
)


def _deadline(body: str) -> str | None:
    m = _DEADLINE.search(body)
    return m.group(1).strip() if m else None


def _consultations(conn, months: list[str], topic_of: dict[str, str], today: date,
                   corr: dict[str, list[dict]] | None = None, enr: dict[str, dict] | None = None) -> dict[str, list[dict]]:
    """Drafts released for comment in the last two reviews, split into open / unknown-deadline / closed."""
    corr = corr or {}
    enr = enr or {}
    buckets: dict[str, list[dict]] = {"open": [], "unknown": [], "closed": []}
    if not months:
        return buckets
    window = months[-2:]
    rows = conn.execute(
        f"""SELECT uid, month, sector, title, body, summary, links, source_url FROM items
            WHERE action = 'consultation' AND month IN ({','.join('?' * len(window))})
            ORDER BY month DESC, sector""",
        window,
    ).fetchall()
    for r in rows:
        slug = topic_of.get(r["uid"])
        raw = _deadline(r["body"])
        due = parse_deadline(raw)
        days_left = (due - today).days if due else None
        entry = dict(
            uid=r["uid"], title=r["title"], month=r["month"], sector=r["sector"], summary=r["summary"],
            links=json.loads(r["links"]), source_url=r["source_url"],
            deadline=due, deadline_raw=raw, days_left=days_left,
            topic=TOPIC_BY_SLUG.get(slug) if slug else None,
            route=respond_route(r["title"], r["body"]), corroborations=corr.get(r["uid"], []),
            hook=(enr.get(r["uid"]) or {}).get("hook"), so_what=(enr.get(r["uid"]) or {}).get("so_what") or {},
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


def _localise(scores, consultations, tr: dict[str, dict]) -> None:
    """Attach translated title/summary (or None) to every evidence row and consultation."""
    for ts in scores:
        for e in ts.evidence:
            x = tr.get(e.uid) or {}
            e.title_l = x.get("title")
            e.summary_l = x.get("summary")
    for bucket in consultations.values():
        for c in bucket:
            x = tr.get(c["uid"]) or {}
            c["title_l"] = x.get("title")
            c["summary_l"] = x.get("summary")


def _render_lang(lang: str, conn, scores, consultations, ctx: dict, n_items: int, n_months: int) -> None:
    env = _env(lang)
    out = OUT if lang == DEFAULT_LANG else OUT / lang
    (out / "topic").mkdir(parents=True, exist_ok=True)
    top = "" if lang == DEFAULT_LANG else "../"          # from a top-level page back to docs/
    ctx = dict(ctx, other_langs=[l for l in LANGS if l != lang])

    (out / "index.html").write_text(env.get_template("index.html").render(
        scores=scores, consultations=consultations, n_months=n_months, n_items=n_items,
        page="index.html", root=top, **ctx), encoding="utf-8")

    for ts in scores:
        by_month: dict[str, list] = {}
        for e in ts.evidence:
            by_month.setdefault(e.month, []).append(e)
        timeline = [(m, by_month.get(m, [])) for m in reversed(ts.months)]
        topic_chains = [c for c in ctx["chains"] if ts.topic.slug in c.get("topics", [])]
        (out / "topic" / f"{ts.topic.slug}.html").write_text(
            env.get_template("topic.html").render(ts=ts, timeline=timeline, root=top + "../", topic_chains=topic_chains,
                                                  page=f"topic/{ts.topic.slug}.html", **ctx), encoding="utf-8")

    (out / "method.html").write_text(env.get_template(f"method_{lang}.html").render(
        page="method.html", root=top, **ctx), encoding="utf-8")


def brief(scores, consultations, k: int = 3) -> dict:
    """The week's edit: what to respond to, what's moving, what's confirmed quiet."""
    moving = [ts for ts in scores if ts.status != "quiet"][:k]
    quiet = [ts for ts in scores if ts.status == "quiet"]
    return dict(respond=consultations["open"] + consultations["unknown"], moving=moving, quiet=quiet)


def assemble(conn, today_d: date | None = None, lang: str | None = None):
    """Everything the site, the JSON and the Telegram digest are built from."""
    today_d = today_d or date.today()
    corr = corroborations_for(conn)
    enr = enrichment_for(conn)
    scores = score_topics(conn, corr, enr)
    months = scores[0].months if scores else db.months_present(conn)
    topic_of = {r["uid"]: r["topic"] for r in conn.execute(
        "SELECT uid, topic FROM item_topics ORDER BY hits")}  # highest-hit topic wins
    consultations = _consultations(conn, months, topic_of, today_d, corr, enr)
    if lang:
        _localise(scores, consultations, {} if lang == DEFAULT_LANG else translations_for(conn, lang))
    return scores, consultations


def build() -> None:
    conn = db.connect()
    today_d = date.today()
    today = today_d.isoformat()
    scores, consultations = assemble(conn, today_d)
    ctx = dict(
        attribution=PRS_ATTRIBUTION, today=today, recent_window=RECENT_WINDOW,
        baseline_window=BASELINE_WINDOW, action_weights=ACTION_WEIGHTS, actions=ACTIONS,
        brief=brief(scores, consultations), channel_url=CHANNEL_URL, personas=PERSONAS,
        chains=load_chains(conn),
    )

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(HERE / "static", OUT / "static")
    (OUT / ".nojekyll").write_text("")

    n_months = len(scores[0].months) if scores else 0
    n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    for lang in LANGS:
        _localise(scores, consultations, {} if lang == DEFAULT_LANG else translations_for(conn, lang))
        _render_lang(lang, conn, scores, consultations, ctx, n_items, n_months)

    # Machine-readable export for anyone who wants to build on top
    (OUT / "radar.json").write_text(json.dumps({
        "generated": today,
        "topics": [{
            "slug": ts.topic.slug, "name": ts.topic.name, "name_hi": ts.topic.name_hi, "status": ts.status,
            "confidence": ts.confidence, "change_pct": ts.change_pct,
            "recent_avg": ts.recent_avg, "baseline_avg": ts.baseline_avg,
            "total_actions": ts.total_items, "recent_actions": ts.recent_items, "breadth": ts.breadth,
            "recent_corroborated": ts.corroborated_recent, "recent_outlets": ts.outlets_recent,
            "sort_score": ts.score, "months": ts.months, "activity": ts.activity, "counts": ts.counts,
        } for ts in scores],
        "consultations": [{
            "title": c["title"], "sector": c["sector"], "deadline": c["deadline"].isoformat() if c["deadline"] else None,
            "status": k, "days_left": c["days_left"], "source": c["source_url"], "links": c["links"],
            "respond_via": c["route"]["body"] or None,
        } for k in ("open", "unknown", "closed") for c in consultations[k]],
        "chains": [{"slug": c["slug"], "name": c["name"], "topics": c.get("topics", []), "next": c.get("next"),
                    "steps": [{"uid": st["uid"], "month": st["month"], "action": st["action"], "title": st["title"]} for st in c["steps"]]}
                   for c in ctx["chains"]],
        "attribution": PRS_ATTRIBUTION,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"built {len(scores)} topic pages x {len(LANGS)} languages -> {OUT}/")
