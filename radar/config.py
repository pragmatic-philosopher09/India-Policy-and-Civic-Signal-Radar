"""Topic definitions and scoring constants.

A *topic* is a citizen-facing theme (e.g. "Gig workers & social security").
Each topic is matched against PRS items via keyword patterns (case-insensitive
regex fragments). PRS sector headings are used as a secondary hint.

Topics are intentionally framed for an 18-30 audience: jobs, exams, taxes,
digital life, gig work. Add or edit freely; the pipeline is topic-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    tagline: str
    keywords: tuple[str, ...]
    sectors: tuple[str, ...] = field(default_factory=tuple)
    color: str = "#2563eb"


TOPICS: list[Topic] = [
    Topic(
        slug="digital-and-data",
        name="Digital rights & data protection",
        tagline="Your data, your feed, your device — who gets to set the rules.",
        keywords=(
            r"data protection", r"\bDPDP\b", r"digital personal data", r"privacy",
            r"\bIT rules\b", r"information technology rules", r"intermediary guidelines",
            r"social media", r"online gaming", r"cyber ?security", r"\bCERT-?In\b",
            r"telecom", r"\bTRAI\b", r"broadcasting", r"\bOTT\b", r"digital india",
            r"aadhaar", r"encryption", r"content (?:blocking|takedown)",
            r"(?:digital|online) platform", r"interception",
        ),
        sectors=("Information Technology", "Electronics", "Communications", "Telecom"),
        color="#7c3aed",
    ),
    Topic(
        slug="ai-regulation",
        name="AI regulation",
        tagline="Deepfakes, model rules, compute missions — how India decides to govern AI.",
        keywords=(
            r"artificial intelligence", r"\bAI\b", r"deepfake", r"synthetic(?:ally generated)? (?:media|content|information)",
            r"algorithm", r"machine learning", r"large language model", r"\bLLMs?\b", r"generative",
            r"IndiaAI", r"AI (?:governance|mission|summit|impact|safety|model)", r"frontier model",
            r"automated decision", r"facial recognition", r"\bGPUs?\b", r"compute capacity",
            r"labell?ing of (?:AI|synthetic)", r"AI[- ]generated",
        ),
        sectors=("Information Technology", "Electronics"),
        color="#db2777",
    ),
    Topic(
        slug="criminal-law-and-justice",
        name="Criminal law & justice",
        tagline="New criminal codes, policing, bail, prisons and how fast courts actually move.",
        keywords=(
            r"Bharatiya Nyaya Sanhita", r"\bBNS\b", r"Bharatiya Nagarik Suraksha", r"\bBNSS\b",
            r"Bharatiya Sakshya", r"\bBSA\b", r"criminal (?:law|procedure|justice|code)",
            r"\bIPC\b", r"Indian Penal Code", r"\bCrPC\b", r"\bbail\b", r"undertrial",
            r"\bprisons?\b", r"\bjails?\b", r"\bpolic(?:e|ing)\b", r"custodial", r"sedition",
            r"death penalty", r"capital punishment", r"\bFIRs?\b", r"forensic", r"\bNIA\b",
            r"\bUAPA\b", r"\bPMLA\b", r"money laundering", r"witness protection",
            r"judicial (?:appointment|vacanc|infrastructure|reform)", r"pendency", r"case backlog",
            r"fast[- ]track court", r"legal aid", r"advocates? (?:\(amendment\) )?bill",
            r"e-?courts", r"\bCBI\b", r"enforcement directorate", r"cyber ?crime",
            r"\bPOCSO\b", r"criminal offence", r"jan vishwas", r"number of (?:supreme court|high court) judges",
        ),
        sectors=("Law and Justice", "Home Affairs"),
        color="#4f46e5",
    ),
    Topic(
        slug="jobs-and-employment",
        name="Jobs & employment",
        tagline="Hiring, layoffs, apprenticeships and the schemes meant to create work.",
        keywords=(
            r"employment", r"unemploy", r"\bjobs?\b", r"apprentice", r"skill(?:ing| development)",
            r"\bPLFS\b", r"labour force", r"internship", r"\bELI scheme\b",
            r"employment linked incentive", r"\bMGNREG", r"rozgar", r"workforce",
            r"\bPM ?Vishwakarma\b", r"startup",
        ),
        sectors=("Labour and Employment", "Skill Development"),
        color="#059669",
    ),
    Topic(
        slug="education-and-exams",
        name="Education & exam integrity",
        tagline="Entrance exams, paper leaks, universities and who regulates them.",
        keywords=(
            r"\bNEET\b", r"\bJEE\b", r"\bUGC\b", r"\bNTA\b", r"\bCUET\b", r"paper leak",
            r"unfair means", r"public examinations?", r"entrance exam", r"\bexams?\b",
            r"higher education", r"universit", r"\bNEP\b", r"national education policy",
            r"school", r"student", r"scholarship", r"\bAICTE\b", r"\bNCERT\b",
            r"foreign universit", r"coaching",
        ),
        sectors=("Education",),
        color="#d97706",
    ),
    Topic(
        slug="personal-finance-and-tax",
        name="Personal finance & taxes",
        tagline="Income tax, GST, UPI, loans and the fine print that hits your wallet.",
        keywords=(
            r"income[- ]tax", r"\bGST\b", r"goods and services tax", r"\bTDS\b", r"\bTCS\b",
            r"\bUPI\b", r"digital payment", r"\bRBI\b", r"reserve bank", r"\bSEBI\b",
            r"mutual fund", r"insurance", r"\bIRDAI\b", r"\bEPF", r"provident fund",
            r"pension", r"\bNPS\b", r"unified pension", r"credit card", r"lending",
            r"\bNBFC\b", r"crypto", r"virtual digital asset", r"banking", r"deposit insurance",
            r"finance bill", r"union budget",
        ),
        sectors=("Finance", "Corporate Affairs"),
        color="#dc2626",
    ),
    Topic(
        slug="gig-work-and-labour-codes",
        name="Gig work & labour codes",
        tagline="Platform workers, social security and the four labour codes.",
        keywords=(
            r"gig work", r"platform work", r"labour codes?", r"code on wages",
            r"industrial relations code", r"social security code", r"code on social security",
            r"occupational safety", r"\bOSH\b", r"minimum wage", r"\bESIC?\b",
            r"contract labour", r"trade union", r"working hours", r"aggregator",
            r"delivery (?:worker|partner)", r"maternity", r"wage", r"\blabour\b",
        ),
        sectors=("Labour and Employment",),
        color="#0891b2",
    ),
]

TOPIC_BY_SLUG = {t.slug: t for t in TOPICS}

# Weight of an item by what *kind* of government action it represents.
# Enacted law counts more than a draft; a draft more than a committee note.
ACTION_WEIGHTS: dict[str, float] = {
    "enacted": 3.0,      # passed by Parliament / received assent
    "introduced": 2.0,   # bill introduced in Parliament
    "rules": 2.0,        # rules/notification issued (binding)
    "cabinet": 1.8,      # cabinet approval
    "consultation": 1.5, # draft released for public comment
    "committee": 1.2,    # standing committee report
    "scheme": 1.2,       # scheme launched/approved
    "court": 1.5,        # Supreme Court / High Court judgement
    "other": 1.0,
}

# Momentum windows (in months)
RECENT_WINDOW = 3
BASELINE_WINDOW = 6

# PRS attribution (CC BY 4.0). Must be visible on every generated page.
PRS_ATTRIBUTION = (
    "Source data © PRS Legislative Research (prsindia.org), "
    "licensed under CC BY 4.0. Summaries and scores are our own."
)
PRS_BASE = "https://prsindia.org"
USER_AGENT = "PolicySignalRadar/0.1 (+https://github.com; civic research, CC BY reuse)"
CRAWL_DELAY_SECONDS = 10  # matches prsindia.org robots.txt
