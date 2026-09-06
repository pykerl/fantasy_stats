"""Shared plumbing for projection sources."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

from .. import cache

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
TIMEOUT = 30


@dataclass
class Projection:
    """One source's projection for one player."""

    source: str
    name: str
    position: str
    team: str
    stats: dict[str, float] = field(default_factory=dict)
    points: float | None = None  # site's own total, used only if stats are absent
    player_id: str | None = None  # sleeper id when the source already knows it
    partial: bool = False  # incomplete coverage (Vegas props, mainly)
    notes: str = ""


class SourceError(RuntimeError):
    """A source failed in a way the caller should note but survive."""


class Source:
    """Base class. Subclasses implement `fetch` and `parse`."""

    name = "base"

    def __init__(self, config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    def fetch(self, week: int, season: int) -> Any:  # pragma: no cover - subclass
        raise NotImplementedError

    def parse(self, payload: Any) -> list[Projection]:  # pragma: no cover - subclass
        raise NotImplementedError

    def load(self, week: int, season: int, refresh: bool = False, ttl_hours: float | None = None) -> list[Projection]:
        """Fetch (or reuse cache) and parse. Never raises; returns [] on failure."""
        payload = None if refresh else cache.read(self.name, week, season, ttl_hours=ttl_hours)
        if payload is None:
            try:
                payload = self.fetch(week, season)
            except Exception as exc:  # noqa: BLE001 - fail soft, note it in the report
                log.warning("source %s failed to fetch: %s", self.name, exc)
                raise SourceError(str(exc)) from exc
            cache.write(self.name, week, season, payload)
        try:
            projections = self.parse(payload)
        except Exception as exc:  # noqa: BLE001 - layout changes must not crash the run
            log.warning("source %s failed to parse: %s", self.name, exc)
            raise SourceError(str(exc)) from exc
        log.info("source %s produced %d projections", self.name, len(projections))
        return projections


def to_float(value: Any) -> float:
    try:
        text = str(value).strip().replace(",", "")
        if text in {"", "-", "--", "N/A"}:
            return 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0
