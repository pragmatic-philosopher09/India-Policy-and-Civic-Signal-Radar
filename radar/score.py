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
    confidence_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)   # "what this does NOT mean"
    why: list[Evidence] = field(default_factory=list)  # top recent actions driving the signal

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


def _confidence(ts: TopicScore) -> tuple[str, list[str]]:
    """How much should a reader trust this signal? Deliberately conservative."""
    reasons: list[str] = []
    n_total, n_recent = ts.total_items, ts.recent_items
    recent = ts.activity[-RECENT_WINDOW:]
    busiest = max(recent) if recent else 0.0
    concentration = busiest / sum(recent) if sum(recent) else 0.0
    active_months = sum(1 for v in recent if v > 0)

    reasons.append(f"{n_total} tagged action{'s' if n_total != 1 else ''} across {len(ts.months)} months")
    reasons.append(f"{n_recent} in the last {RECENT_WINDOW} months, spread over {active_months} month{'s' if active_months != 1 else ''}")
    reasons.append(f"{ts.breadth} ministr{'ies' if ts.breadth != 1 else 'y'} active recently")
    reasons.append("single source (PRS) — no independent cross-check yet")

    level = "high"
    if n_total < 8 or n_recent < 2:
        level = "low"
    elif n_total < 20 or ts.breadth < 3 or concentration > 0.8:
        level = "medium"
    if concentration > 0.8 and n_recent >= 2:
        reasons.append("recent activity concentrated in a single month")
    return level, reasons


def _caveats(ts: TopicScore) -> list[str]:
    out: list[str] = []
    recent = ts.activity[-RECENT_WINDOW:]
    if ts.status == "quiet":
        n = ts.recent_items
        opener = (f"Only {n} tagged action{'s' if n != 1 else ''}" if n else "Zero tagged actions")
        out.append(
            f"{opener} in the last {RECENT_WINDOW} months. This radar measures formal government "
            "action recorded by PRS — bills, rules, committee reports, drafts. It does not see implementation, "
            "court challenges, strikes or news coverage. \"Quiet\" can mean dormant, or happening outside the "
            "legislative pipeline."
        )
    if ts.status == "heating" and ts.recent_items <= 4:
        out.append(
            f"The rise rests on just {ts.recent_items} action{'s' if ts.recent_items != 1 else ''}. One or two "
            "documents can swing this number. Read it as \"worth watching\", not \"reform is accelerating\"."
        )
    if sum(recent) and max(recent) / sum(recent) > 0.8 and ts.recent_items >= 2:
        busiest = ts.months[-RECENT_WINDOW:][recent.index(max(recent))]
        out.append(
            f"Almost all recent activity fell in one month ({busiest}). Parliament sits in bursts, so a session "
            "month looks like a surge even when the underlying attention is steady."
        )
    if ts.change_pct is None and ts.status == "heating":
        out.append("There was almost no comparable activity in the baseline period, so no percentage change is shown.")
    out.append("Activity is not importance. A topic with three high-stakes actions can matter more than one with ten routine ones.")
    return out


def _composite(recent: float, change: float | None, breadth: int) -> int:
    # Volume: saturates around ~12 weighted points/month
    vol = min(recent / 12.0, 1.0)
    # Momentum: log-ratio squashed to 0..1 (x2 growth -> ~0.75)
    ratio = 1.0 + (change or 0.0) / 100.0
    mom = 1 / (1 + math.exp(-2.0 * math.log(max(ratio, 0.05))))
    brd = min(breadth / 4.0, 1.0)
    return round(100 * (0.45 * mom + 0.40 * vol + 0.15 * brd))


def score_topics(conn: sqlite3.Connection) -> list[TopicScore]:
    months = [r["month"] for r in conn.execute("SELECT month FROM months ORDER BY month")]
    if not months:
        return []
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
                                     r["summary"], json.loads(r["links"]), r["source_url"]))

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
        ts.confidence, ts.confidence_reasons = _confidence(ts)
        ts.caveats = _caveats(ts)
        ts.why = sorted((e for e in evidence if e.month in recent_months),
                        key=lambda e: (-e.weight, e.month))[:3]
        results.append(ts)

    results.sort(key=lambda t: t.score, reverse=True)
    return results
