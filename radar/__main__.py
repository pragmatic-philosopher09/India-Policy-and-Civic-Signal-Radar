"""CLI entry point.

  python -m radar ingest --months 18      # backfill / refresh PRS monthly reviews
  python -m radar summarise               # fill missing plain-English summaries
  python -m radar build                   # render static site into docs/
  python -m radar run --months 18         # all three
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from . import db
from .fetch import fetch
from .parse import iso_month, month_url, parse_month
from .score import classify_action, tag_topics
from .summarize import summarise_missing

log = logging.getLogger("radar")


def _month_iter(n: int, start: date | None = None):
    d = (start or date.today()).replace(day=1)
    for _ in range(n):
        yield d
        d = (d.replace(month=d.month - 1) if d.month > 1 else d.replace(year=d.year - 1, month=12))


def ingest(months: int, refresh_latest: int = 2) -> None:
    """Fetch the last ``months`` reviews. Only the newest ``refresh_latest`` are re-fetched."""
    conn = db.connect()
    have = set(db.months_present(conn))
    for i, d in enumerate(_month_iter(months)):
        ym = iso_month(d)
        force = i < refresh_latest
        if ym in have and not force:
            continue
        url = month_url(d)
        html = fetch(url, force=force)
        if html is None:
            log.info("%s not published yet", ym)
            continue
        items = parse_month(html, d, source_url=url)
        if not items:
            log.warning("%s parsed to zero items — layout change?", ym)
            continue
        actions = {it.uid: classify_action(it.title) for it in items}
        topics = {it.uid: tag_topics(it) for it in items}
        db.upsert_month(conn, ym, url, items, actions, topics)
        tagged = sum(1 for t in topics.values() if t)
        log.info("%s: %d items, %d tagged to a topic", ym, len(items), tagged)

    # Re-apply current keyword/action rules to everything so config edits propagate
    n = db.retag_all(conn, classify_action, tag_topics)
    log.info("retagged %d stored items", n)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="radar")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "run"):
        s = sub.add_parser(name)
        s.add_argument("--months", type=int, default=18)
    sub.add_parser("retag", help="re-apply topic/action rules to stored items")
    sub.add_parser("summarise")
    sub.add_parser("build")
    args = p.parse_args(argv)

    if args.cmd in ("ingest", "run"):
        ingest(args.months)
    if args.cmd == "retag":
        log.info("retagged %d items", db.retag_all(db.connect(), classify_action, tag_topics))
    if args.cmd in ("summarise", "run"):
        n = summarise_missing(db.connect())
        log.info("summarised %d items", n)
    if args.cmd in ("build", "run"):
        from .build_site import build
        build()


if __name__ == "__main__":
    main()
