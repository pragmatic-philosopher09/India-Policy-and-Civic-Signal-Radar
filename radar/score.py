"""Action classification, topic tagging and evidence-weighted momentum scoring.

Everything here is deliberately simple and inspectable: a reader should be able
to look at the evidence list on a topic page and reproduce the number by hand.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field

from .config import ACTION_WEIGHTS, BASELINE_WINDOW, RECENT_WINDOW, TOPICS, Topic
from .parse import Item

# Ordered: first match wins. Patterns are applied to the item title.
_ACTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("enacted", re.compile(r"\b(passe[sd]|enacted|receives? (?:the )?president'?s assent|brought into (?:effect|force)|comes? into (?:effect|force))\b", re.I)),
    ("introduced", re.compile(r"\bintroduced\b", re.I)),
    ("court", re.compile(r"\b(supreme court|high court|\bSC\b|judg?ement|str(?:ikes|uck) down|uph(?:olds|eld)|stays?)\b", re.I)),
    ("cabinet", re.compile(r"\bcabinet\b", re.I)),
    ("consultation", re.compile(r"\b(draft|comments? invited|for (?:public )?comments|consultation|white paper)\b", re.I)),
    ("committee", re.compile(r"\b(standing committee|committee (?:submits|presents|report)|joint parliamentary committee|\bJPC\b)\b", re.I)),
    ("rules", re.compile(r"\b(notifie[sd]|notification|ordinance|rules|regulations?|guidelines|amends? .*rules|circular)\b", re.I)),
    ("scheme", re.compile(r"\b(scheme|mission|launche[sd]|approve[sd]|yojana|programme)\b", re.I)),
]


def classify_action(title: str) -> str:
    for action, pat in _ACTION_RULES:
        if pat.search(title):
            return action
    return "other"


def _compiled(topic: Topic) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{k})" for k in topic.keywords), re.I)


_TOPIC_PATTERNS = {t.slug: _compiled(t) for t in TOPICS}


def tag_topics(item: Item, threshold: int = 4) -> dict[str, int]:
    """Return {topic_slug: hit_score} for topics this item is evidence for.

    Title matches weigh 3x, PRS sector match adds 1, body matches 1x each.
    """
    out: dict[str, int] = {}
    for topic in TOPICS:
        pat = _TOPIC_PATTERNS[topic.slug]
        score = 3 * len(pat.findall(item.title)) + len(pat.findall(item.body))
        if any(s.lower() in item.sector.lower() for s in topic.sectors):
            score += 1
        if score >= threshold:
            out[topic.slug] = score
    return out


@dataclass
class Evidence:
    uid: str
    month: str
    sector: str
    title: str
    action: str
    weight: float
    summary: str | None
    links: list[str]
    source_url: str
    title_l: str | None = None    # localised title (set at build time)
    summary_l: str | None = None
    corroborations: list[dict] = field(default_factory=list)   # independent coverage (gov/news/other)
    hook: str | None = None                                      # editorial headline (enrichment)
    so_what: dict = field(default_factory=dict)                  # {persona: line}

    @property
    def date_hint(self) -> str | None:
        """Approximate day from independent coverage (PRS only gives the month)."""
        dates = sorted(h["published"] for h in self.corroborations
                       if h.get("published") and h.get("kind") != "other" and h["published"][:7] >= self.month)
        return dates[0] if dates else None

    @property
    def corroborated(self) -> bool:
        gov = sum(1 for h in self.corroborations if h["kind"] == "government")
        news = len({h["outlet"] for h in self.corroborations if h["kind"] == "news"})
        return gov >= 1 or news >= 2


@dataclass
class TopicScore:
    topic: Topic
    months: list[str]                       # full timeline, oldest -> newest
    activity: list[float]                   # weighted activity per month
    counts: list[int]                       # raw item counts per month
    recent_avg: float
    baseline_avg: float
    change_pct: float | None                # None when baseline is ~0
    breadth: int                            # distinct PRS sectors in recent window
    score: int                              # 0-100 composite (used for ordering only)
    status: str                             # heating / steady / cooling / quiet
    evidence: list[Evidence] = field(default_factory=list)
    confidence: str = "low"                 # low / medium / high
    confidence_reasons: list[tuple[str, dict]] = field(default_factory=list)
    caveats: list[tuple[str, dict]] = field(default_factory=list)  # (key, params) — rendered per language
    why: list[Evidence] = field(default_factory=list)  # top recent actions driving the signal
    corroborated_recent: int = 0           # recent evidence items confirmed by independent coverage
    outlets_recent: list[str] = field(default_factory=list)
    why_moving: dict = field(default_factory=dict)      # structured causal explanation of the label
    counter: tuple[str, dict] | None = None             # what would make this signal fade / flip
    connections: list[dict] = field(default_factory=list)  # other topics sharing recent evidence

    @property
    def total_items(self) -> int:
        return sum(self.counts)

    @property
    def recent_items(self) -> int:
        return sum(self.counts[-RECENT_WINDOW:])


def _status(recent: float, change: float | None) -> str:
    if recent < 1.0:
        return "quiet"
    if change is None or change >= 50:
        return "heating"
    if change <= -33:
        return "cooling"
    return "steady"


def _confidence(ts: TopicScore) -> tuple[str, list[tuple[str, dict]]]:
    """How much should a reader trust this signal? Deliberately conservative.

    Reasons are returned as (message_key, params) so they can be rendered in any language.
    """
    reasons: list[tuple[str, dict]] = []
    n_total, n_recent = ts.total_items, ts.recent_items
    recent = ts.activity[-RECENT_WINDOW:]
    busiest = max(recent) if recent else 0.0
    concentration = busiest / sum(recent) if sum(recent) else 0.0
    active_months = sum(1 for v in recent if v > 0)

    reasons.append(("conf_total", dict(n=n_total, m=len(ts.months))))
    reasons.append(("conf_recent", dict(n=n_recent, w=RECENT_WINDOW, a=active_months)))
    reasons.append(("conf_breadth", dict(b=ts.breadth)))
    if n_recent and ts.corroborated_recent:
        reasons.append(("conf_corroborated", dict(k=ts.corroborated_recent, n=n_recent,
                                                  outlets=", ".join(ts.outlets_recent[:4]))))
    elif n_recent:
        reasons.append(("conf_uncorroborated", dict(n=n_recent)))
    else:
        reasons.append(("conf_single_source", {}))

    level = "high"
    if n_total < 8 or n_recent < 2:
        level = "low"
    elif n_total < 20 or ts.breadth < 3 or concentration > 0.8:
        level = "medium"
    # Independent confirmation of most recent evidence lifts confidence one step
    if n_recent >= 2 and ts.corroborated_recent / n_recent >= 0.7 and level != "high":
        level = {"low": "medium", "medium": "high"}[level]
        reasons.append(("conf_lifted", {}))
    if concentration > 0.8 and n_recent >= 2:
        reasons.append(("conf_concentrated", {}))
    return level, reasons


def _caveats(ts: TopicScore) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    recent = ts.activity[-RECENT_WINDOW:]
    if ts.status == "quiet":
        out.append(("caveat_quiet", dict(n=ts.recent_items, w=RECENT_WINDOW)))
    if ts.status == "heating" and ts.recent_items <= 4:
        out.append(("caveat_few_actions", dict(n=ts.recent_items)))
    if sum(recent) and max(recent) / sum(recent) > 0.8 and ts.recent_items >= 2:
        busiest = ts.months[-RECENT_WINDOW:][recent.index(max(recent))]
        out.append(("caveat_concentrated", dict(month=busiest)))
    if ts.change_pct is None and ts.status == "heating":
        out.append(("caveat_no_baseline", {}))
    out.append(("caveat_not_importance", {}))
    return out


def _why_moving(ts: TopicScore) -> dict:
    recent_months = ts.months[-RECENT_WINDOW:]
    recent_ev = [e for e in ts.evidence if e.month in set(recent_months)]
    by_month = {m: [e for e in recent_ev if e.month == m] for m in recent_months}
    active = [m for m in recent_months if by_month[m]]
    institutions = sorted({e.sector for e in recent_ev})
    kinds: dict[str, int] = {}
    for e in recent_ev:
        kinds[e.action] = kinds.get(e.action, 0) + 1
    steps = sorted(recent_ev, key=lambda e: (e.date_hint or e.month + "-99", -e.weight))
    if ts.status == "quiet":
        key, params = "why_quiet", dict(w=RECENT_WINDOW)
    elif ts.status == "heating":
        if len(institutions) >= 2:
            key, params = "why_multi_inst", dict(k=len(institutions), inst=", ".join(institutions[:3]),
                                                 months=" & ".join(active) if len(active) <= 2 else f"{active[0]}–{active[-1]}")
        else:
            key, params = "why_single_inst", dict(inst=institutions[0] if institutions else "one body", n=len(recent_ev),
                                                  months=" & ".join(active))
    elif ts.status == "cooling":
        key, params = "why_cooling", dict(n=len(recent_ev), w=RECENT_WINDOW, base=ts.baseline_avg)
    else:
        key, params = "why_steady", dict(n=len(recent_ev), w=RECENT_WINDOW, b=BASELINE_WINDOW)
    return dict(interpretation=(key, params), months=[dict(month=m, n=len(by_month[m]),
                weighted=round(sum(e.weight for e in by_month[m]), 1)) for m in recent_months],
                institutions=institutions, kinds=kinds, steps=steps[:6], single_month=len(active) == 1)


def _counter(ts: TopicScore) -> tuple[str, dict]:
    if ts.status == "heating":
        return "counter_heating", dict(n=ts.recent_items)
    if ts.status == "quiet":
        return "counter_quiet", {}
    if ts.status == "cooling":
        return "counter_cooling", {}
    return "counter_steady", {}


def _composite(recent: float, change: float | None, breadth: int) -> int:
    # Volume: saturates around ~12 weighted points/month
    vol = min(recent / 12.0, 1.0)
    # Momentum: log-ratio squashed to 0..1 (x2 growth -> ~0.75)
    ratio = 1.0 + (change or 0.0) / 100.0
    mom = 1 / (1 + math.exp(-2.0 * math.log(max(ratio, 0.05))))
    brd = min(breadth / 4.0, 1.0)
    return round(100 * (0.45 * mom + 0.40 * vol + 0.15 * brd))


def score_topics(conn: sqlite3.Connection, corroborations: dict[str, list[dict]] | None = None,
                 enrichment: dict[str, dict] | None = None) -> list[TopicScore]:
    months = [r["month"] for r in conn.execute("SELECT month FROM months ORDER BY month")]
    if not months:
        return []
    corroborations = corroborations or {}
    enrichment = enrichment or {}
    idx = {m: i for i, m in enumerate(months)}
    results: list[TopicScore] = []

    for topic in TOPICS:
        rows = conn.execute(
            """SELECT i.uid, i.month, i.sector, i.title, i.action, i.summary, i.links, i.source_url
               FROM items i JOIN item_topics t ON t.uid = i.uid
               WHERE t.topic = ? ORDER BY i.month DESC, t.hits DESC""",
            (topic.slug,),
        ).fetchall()

        activity = [0.0] * len(months)
        counts = [0] * len(months)
        evidence: list[Evidence] = []
        for r in rows:
            w = ACTION_WEIGHTS.get(r["action"], 1.0)
            i = idx[r["month"]]
            activity[i] += w
            counts[i] += 1
            evidence.append(Evidence(r["uid"], r["month"], r["sector"], r["title"], r["action"], w,
                                     r["summary"], json.loads(r["links"]), r["source_url"],
                                     corroborations=corroborations.get(r["uid"], []),
                                     hook=(enrichment.get(r["uid"]) or {}).get("hook"),
                                     so_what=(enrichment.get(r["uid"]) or {}).get("so_what") or {}))

        recent = activity[-RECENT_WINDOW:]
        baseline = activity[-(RECENT_WINDOW + BASELINE_WINDOW):-RECENT_WINDOW]
        recent_avg = sum(recent) / max(len(recent), 1)
        baseline_avg = sum(baseline) / len(baseline) if baseline else 0.0
        change = None if baseline_avg < 0.5 else round(100 * (recent_avg - baseline_avg) / baseline_avg)

        recent_months = set(months[-RECENT_WINDOW:])
        breadth = len({e.sector for e in evidence if e.month in recent_months})

        ts = TopicScore(
            topic=topic, months=months, activity=activity, counts=counts,
            recent_avg=round(recent_avg, 1), baseline_avg=round(baseline_avg, 1),
            change_pct=change, breadth=breadth,
            score=_composite(recent_avg, change, breadth),
            status=_status(recent_avg, change), evidence=evidence,
        )
        recent_ev = [e for e in evidence if e.month in recent_months]
        ts.corroborated_recent = sum(1 for e in recent_ev if e.corroborated)
        seen: list[str] = []
        for e in recent_ev:
            for h in e.corroborations:
                if h["kind"] != "other" and h["outlet"] not in seen:
                    seen.append(h["outlet"])
        ts.outlets_recent = seen
        ts.confidence, ts.confidence_reasons = _confidence(ts)
        ts.caveats = _caveats(ts)
        ts.why_moving = _why_moving(ts)
        ts.counter = _counter(ts)
        ts.why = sorted((e for e in evidence if e.month in recent_months),
                        key=lambda e: (-e.weight, e.month))[:3]
        results.append(ts)

    # cross-topic connections: recent evidence shared by two topics
    recent_set = set(months[-RECENT_WINDOW:])
    owner: dict[str, list[TopicScore]] = {}
    for ts in results:
        for e in ts.evidence:
            if e.month in recent_set:
                owner.setdefault(e.uid, []).append(ts)
    for uid, owners in owner.items():
        if len(owners) < 2:
            continue
        title = next(e.hook or e.title for e in owners[0].evidence if e.uid == uid)
        for ts in owners:
            for other in owners:
                if other is ts:
                    continue
                c = next((c for c in ts.connections if c["slug"] == other.topic.slug), None)
                if c is None:
                    c = dict(slug=other.topic.slug, topic=other.topic, shared=[])
                    ts.connections.append(c)
                c["shared"].append(title)

    results.sort(key=lambda t: t.score, reverse=True)
    return results
