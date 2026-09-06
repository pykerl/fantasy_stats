"""Timestamped JSON cache for raw API/scrape payloads."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import CACHE_DIR

log = logging.getLogger(__name__)


def cache_path(source: str, week: int, season: int) -> Path:
    return CACHE_DIR / f"{source}_{season}_wk{week}.json"


def read(source: str, week: int, season: int, ttl_hours: float | None = None) -> Any | None:
    path = cache_path(source, week, season)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("cache %s unreadable (%s), ignoring", path.name, exc)
        return None
    if ttl_hours is not None:
        age_hours = (time.time() - blob.get("fetched_at", 0)) / 3600
        if age_hours > ttl_hours:
            log.info("cache %s is %.1fh old (ttl %.1fh), refetching", path.name, age_hours, ttl_hours)
            return None
    return blob.get("payload")


def write(source: str, week: int, season: int, payload: Any) -> None:
    path = cache_path(source, week, season)
    body = {"source": source, "season": season, "week": week, "fetched_at": time.time(), "payload": payload}
    path.write_text(json.dumps(body))
    log.debug("cached %s", path.name)
