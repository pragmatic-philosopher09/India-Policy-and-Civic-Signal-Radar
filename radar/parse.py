"""Parse PRS Monthly Policy Review pages into structured items.

Page anatomy (Drupal export of a Word doc):
  div.view-content
    div[style*=border-bottom]  -> sector heading ("Finance", "Education", ...)
    p > strong > span[color:#3366ff] -> item title
    p > em (contains @prsindia.org)  -> author line (skipped)
    p / ul ...                         -> item body until next title/sector
    a[name^=_edn]                      -> endnotes (source citations)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup, Tag

from .config import PRS_BASE

TITLE_COLOR = "3366ff"
MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


@dataclass
class Item:
    month: str            # ISO "YYYY-MM"
    sector: str
    title: str
    body: str
    links: list[str] = field(default_factory=list)
    source_url: str = ""
    position: int = 0

    @property
    def uid(self) -> str:
        return f"{self.month}:{self.position}"


def month_slug(d: date) -> str:
    return f"{MONTHS[d.month - 1]}-{d.year}"


def month_url(d: date) -> str:
    return f"{PRS_BASE}/policy/monthly-policy-review/{month_slug(d)}"


def iso_month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


_FOOTNOTE = re.compile(r"\[\d+\]")
_WS = re.compile(r"[ \t\u00a0]+")


def _clean(text: str) -> str:
    text = _FOOTNOTE.sub("", text)
    text = _WS.sub(" ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def _is_sector_heading(tag: Tag) -> bool:
    return tag.name == "div" and "border-bottom" in (tag.get("style") or "")


_HEADING_TAGS = {"p", "h1", "h2", "h3", "h4"}
_SMALL_WORDS = {"and", "of", "&", "the", "for"}
KNOWN_SECTORS = {
    "parliament", "macroeconomic development", "finance", "education", "agriculture", "defence",
    "communications", "transport", "home affairs", "law and justice", "energy", "power", "mining",
    "commerce and industry", "health", "health and family welfare", "environment", "labour",
    "labour and employment", "electronics and it", "information technology", "external affairs",
    "social justice", "social justice and empowerment", "housing and urban affairs",
    "pharmaceuticals", "environment and water", "petroleum and natural gas", "rural development",
    "women and child development", "civil aviation", "railways", "shipping", "coal", "steel",
    "textiles", "tourism", "culture", "sports", "food", "consumer affairs", "skill development",
    "science and technology", "space", "statistics", "corporate affairs", "tribal affairs",
    "minority affairs", "water resources", "jal shakti", "planning", "niti aayog", "roads",
    "road transport and highways", "ports", "urban development", "chemicals and fertilisers",
    "heavy industries", "msme", "new and renewable energy", "personnel", "panchayati raj",
    "north east", "youth affairs", "food processing", "animal husbandry", "fisheries",
    "electronics and information technology", "cooperation", "disinvestment",
}


def _blue_heading_text(tag: Tag) -> str | None:
    """Return the heading text if ``tag`` is a block rendered entirely in PRS heading blue."""
    if tag.name not in _HEADING_TAGS:
        return None
    text = tag.get_text(" ", strip=True)
    if not text or len(text) > 220:
        return None
    blue = tag.find("span", style=lambda s: s and TITLE_COLOR in s.lower())
    if blue is None:
        return None
    if tag.name == "p":
        strong = tag.find("strong")
        if strong is None or strong.get_text(" ", strip=True) != text:
            return None
    return text


def looks_like_sector(text: str) -> bool:
    t = text.strip().rstrip(":")
    if t.lower() in KNOWN_SECTORS:
        return True
    words = t.split()
    if not 1 <= len(words) <= 4 or re.search(r"[\d,()%'’]", t):
        return False
    if len(words) == 4 and "and" not in (w.lower() for w in words):
        return False
    return all(w.lower() in _SMALL_WORDS or w[:1].isupper() for w in words)


def _is_author(tag: Tag) -> bool:
    return tag.name == "p" and "@prsindia.org" in tag.get_text()


def _extract_links(tag: Tag) -> list[str]:
    out = []
    for a in tag.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        if href.startswith("/"):
            href = PRS_BASE + href
        if not href.startswith(("http://", "https://")):
            continue
        out.append(href)
    return out


def parse_month(html: str, month: date, source_url: str = "") -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("div.view-content")
    if content is None:
        return []

    items: list[Item] = []
    sector: str | None = None  # None until the first heading -> skips "Highlights" recap
    current: Item | None = None
    body_parts: list[str] = []
    ym = iso_month(month)

    def flush() -> None:
        nonlocal current, body_parts
        if current is not None and sector is not None:
            current.body = _clean("\n".join(p for p in body_parts if p))
            current.position = len(items)
            items.append(current)
        current, body_parts = None, []

    for child in content.children:
        if not isinstance(child, Tag):
            continue
        # Stop at endnotes
        if child.find("a", attrs={"name": re.compile(r"^_edn\d+$")}):
            break
        if _is_sector_heading(child):
            flush()
            sector = _clean(child.get_text(" ", strip=True)) or sector or "General"
            continue
        heading = _blue_heading_text(child)
        if heading is not None:
            flush()
            heading = _clean(heading)
            if heading.lower().startswith("highlights of this issue"):
                sector = None
                continue
            if looks_like_sector(heading):
                sector = heading
                continue
            if sector is None:
                continue
            current = Item(month=ym, sector=sector, title=heading, body="", source_url=source_url)
            continue
        if current is None or _is_author(child):
            continue
        text = child.get_text(" ", strip=True)
        if text:
            body_parts.append(_clean(text))
        current.links.extend(_extract_links(child))

    flush()
    return items
