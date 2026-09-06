"""Consensus projections and per-player variance.

Every source's raw stat line is re-scored under the league's own rules, then
combined into a weighted consensus. Player sigma blends cross-provider
disagreement with a positional variance prior, so a player covered by one
source does not look deceptively certain.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from .player_matching import MatchIndex, PlayerRef
from .scoring import score_stats
from .sources.base import Projection
from .sources.vegas import score_vegas

log = logging.getLogger(__name__)

# Floor on sigma so a player everyone agrees on is not modelled as a constant.
MIN_SIGMA = 1.0
# How much weight the cross-source disagreement carries against the positional
# prior. With one source there is no disagreement signal, so the prior is used.
DISAGREEMENT_WEIGHT = 0.5


@dataclass
class PlayerProjection:
    player_id: str
    name: str
    position: str
    team: str
    consensus: float = 0.0
    sigma: float = 0.0
    by_source: dict[str, float] = field(default_factory=dict)
    spread: float = 0.0  # max - min across sources
    sources_used: list[str] = field(default_factory=list)
    partial_sources: list[str] = field(default_factory=list)
    vegas_points: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len(self.by_source)

    @property
    def vegas_delta(self) -> float | None:
        """How far the market sits from the model consensus."""
        if self.vegas_points is None:
            return None
        return round(self.vegas_points - self.consensus, 2)

    @property
    def agreement(self) -> float:
        """0-1 score: how tightly the sources cluster, relative to the mean."""
        if self.consensus <= 0 or self.source_count < 2:
            return 0.0
        return max(0.0, 1.0 - (self.spread / self.consensus))


def score_projection(projection: Projection, scoring: dict[str, float], position: str) -> float | None:
    """Score one source's projection under league rules.

    Raw stats are preferred; a site's own point total is a last resort because
    it bakes in that site's scoring, not the league's.
    """
    if projection.source == "vegas":
        return score_vegas(projection.stats, scoring, position)
    if projection.stats:
        return score_stats(projection.stats, scoring)
    if projection.points is not None:
        return round(float(projection.points), 3)
    return None


def aggregate(
    source_projections: dict[str, list[Projection]],
    index: MatchIndex,
    scoring: dict[str, float],
    weights: dict[str, float],
    positional_cv: dict[str, float],
) -> dict[str, PlayerProjection]:
    """Resolve every source to Sleeper ids and build weighted consensus values."""
    buckets: dict[str, dict] = {}
    unmatched: dict[str, list[str]] = {}

    for source, projections in source_projections.items():
        for projection in projections:
            ref = _resolve(projection, index)
            if ref is None:
                unmatched.setdefault(source, []).append(projection.name)
                continue
            points = score_projection(projection, scoring, ref.position)
            if points is None:
                continue
            bucket = buckets.setdefault(
                ref.player_id,
                {"ref": ref, "points": {}, "partial": [], "notes": []},
            )
            bucket["points"][source] = points
            if projection.partial:
                bucket["partial"].append(source)
            if projection.notes:
                bucket["notes"].append(f"{source}: {projection.notes}")

    for source, names in unmatched.items():
        log.warning("%s: %d players unmatched (e.g. %s)", source, len(names), ", ".join(names[:5]))

    out: dict[str, PlayerProjection] = {}
    for player_id, bucket in buckets.items():
        ref: PlayerRef = bucket["ref"]
        points = bucket["points"]

        # A Vegas line built from a single thin market is noise, not signal.
        usable = {
            source: value
            for source, value in points.items()
            if not (source in bucket["partial"] and len(points) > 1)
        }
        if not usable:
            usable = points

        consensus = _weighted_mean(usable, weights)
        values = list(usable.values())
        spread = max(values) - min(values) if len(values) > 1 else 0.0
        sigma = _sigma(consensus, values, ref.position, positional_cv)

        out[player_id] = PlayerProjection(
            player_id=player_id,
            name=ref.name,
            position=ref.position,
            team=ref.team,
            consensus=round(consensus, 2),
            sigma=round(sigma, 2),
            by_source={k: round(v, 2) for k, v in points.items()},
            spread=round(spread, 2),
            sources_used=sorted(usable),
            partial_sources=sorted(bucket["partial"]),
            vegas_points=round(points["vegas"], 2) if "vegas" in points else None,
            notes=bucket["notes"],
        )
    log.info("aggregated %d players across %d sources", len(out), len(source_projections))
    return out


def _resolve(projection: Projection, index: MatchIndex) -> PlayerRef | None:
    if projection.player_id and projection.player_id in index.players:
        return index.players[projection.player_id]
    return index.resolve(projection.name, projection.position, projection.team)


def _weighted_mean(points: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = 0.0
    total = 0.0
    for source, value in points.items():
        weight = float(weights.get(source, 1.0))
        total += value * weight
        total_weight += weight
    return total / total_weight if total_weight else 0.0


def _sigma(consensus: float, values: list[float], position: str, positional_cv: dict[str, float]) -> float:
    """Blend cross-provider disagreement with a positional CV prior."""
    prior = consensus * float(positional_cv.get(position, 0.5))

    if len(values) < 2:
        return max(MIN_SIGMA, prior)

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    disagreement = math.sqrt(variance)

    # Provider disagreement measures model risk, not game-day outcome risk, so
    # it widens the prior rather than replacing it.
    blended = math.sqrt(prior**2 + (DISAGREEMENT_WEIGHT * disagreement * 2) ** 2)
    return max(MIN_SIGMA, blended)
