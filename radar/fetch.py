"""Polite HTTP fetcher with on-disk cache and robots.txt crawl-delay."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import requests

from .config import CRAWL_DELAY_SECONDS, USER_AGENT

log = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/html")
_last_request_at = 0.0


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")


def fetch(url: str, *, force: bool = False, timeout: int = 30) -> str | None:
    """Return page HTML, using the cache unless ``force``. Returns None on 404."""
    global _last_request_at
    path = _cache_path(url)
    if path.exists() and not force:
        return path.read_text(encoding="utf-8")

    wait = CRAWL_DELAY_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)

    log.info("GET %s", url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    _last_request_at = time.monotonic()

    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text
