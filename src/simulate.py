"""Monte Carlo matchup simulation.

Each starter is drawn from Normal(mean, sigma) truncated at zero. Starters on
the same NFL team share a correlated shock, so a QB and his WR1 boom and bust
together rather than averaging each other out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .aggregate import PlayerProjection
from .yahoo_league import League, Matchup, RosterSlot, Team

log = logging.getLogger(__name__)


@dataclass
class StarterProjection:
    slot: RosterSlot
    projection: PlayerProjection | None

    @property
    def mean(self) -> float:
        return self.projection.consensus if self.projection else 0.0

    @property
    def sigma(self) -> float:
        return self.projection.sigma if self.projection else 0.0

    @property
    def name(self) -> str:
        return self.slot.name or "(empty)"

    @property
    def problem(self) -> str:
        """Why this slot is a liability, if it is. Prime trash-talk material."""
        if self.slot.empty or not self.slot.name or self.slot.name == "(empty)":
            return "empty slot"
        if self.projection is None:
            return "no projection (bye or inactive)"
        if self.slot.status in {"O", "IR", "SUSP"}:
            return f"listed {self.slot.status}"
        if self.slot.status in {"Q", "D", "GTD"}:
            return f"listed {self.slot.status}"
        return ""


@dataclass
class TeamSim:
    team: Team
    starters: list[StarterProjection]
    mean: float
    sigma: float
    scores: np.ndarray = field(repr=False, default=None)

    @property
    def floor(self) -> float:
        """10th-percentile team score — a realistic bad week, not the worst case."""
        return round(float(np.percentile(self.scores, 10)), 1) if self.scores is not None and self.scores.size else 0.0

    @property
    def ceiling(self) -> float:
        """90th-percentile team score."""
        return round(float(np.percentile(self.scores, 90)), 1) if self.scores is not None and self.scores.size else 0.0

    @property
    def problems(self) -> list[StarterProjection]:
        return [s for s in self.starters if s.problem]

    @property
    def top_starter(self) -> StarterProjection | None:
        real = [s for s in self.starters if s.projection]
        return max(real, key=lambda s: s.mean) if real else None


@dataclass
class MatchupSim:
    home: TeamSim
    away: TeamSim
    home_win_probability: float
    spread: float  # positive: home favored
    total: float
    boom_bust: float  # sigma of the margin — how little the model knows
    margin_p10: float
    margin_p90: float

    @property
    def favorite(self) -> TeamSim:
        return self.home if self.spread >= 0 else self.away

    @property
    def underdog(self) -> TeamSim:
        return self.away if self.spread >= 0 else self.home

    @property
    def favorite_win_probability(self) -> float:
        return max(self.home_win_probability, 1 - self.home_win_probability)

    @property
    def underdog_win_probability(self) -> float:
        return 1 - self.favorite_win_probability

    @property
    def is_upset_alert(self) -> bool:
        """A genuine underdog with real chances, per the spec's >40% threshold."""
        return self.underdog_win_probability > 0.40

    @property
    def favorite_margin_p10(self) -> float:
        """10th-percentile margin from the favorite's perspective.

        `margin_p10`/`margin_p90` are home-minus-away, so they read backwards
        whenever the away team is favored.
        """
        return self.margin_p10 if self.spread >= 0 else -self.margin_p90

    @property
    def favorite_margin_p90(self) -> float:
        return self.margin_p90 if self.spread >= 0 else -self.margin_p10

    @property
    def is_coin_flip(self) -> bool:
        """Too close for 'upset' to mean anything — nobody is really favored."""
        return self.favorite_win_probability < 0.57


@dataclass
class LeagueSim:
    week: int
    matchups: list[MatchupSim]
    teams: list[TeamSim]

    @property
    def highest_projected(self) -> TeamSim:
        return max(self.teams, key=lambda t: t.mean)

    @property
    def most_volatile(self) -> TeamSim:
        return max(self.teams, key=lambda t: t.sigma)

    @property
    def top_scorer(self) -> StarterProjection | None:
        candidates = [s for team in self.teams for s in team.starters if s.projection]
        return max(candidates, key=lambda s: s.mean) if candidates else None

    @property
    def closest_matchup(self) -> MatchupSim | None:
        return min(self.matchups, key=lambda m: abs(m.spread)) if self.matchups else None

    @property
    def biggest_mismatch(self) -> MatchupSim | None:
        return max(self.matchups, key=lambda m: abs(m.spread)) if self.matchups else None


def _match_starters(team: Team, projections: dict[str, PlayerProjection], by_key) -> list[StarterProjection]:
    out = []
    for slot in team.starters:
        projection = None
        if slot.player_id and slot.player_id in projections:
            projection = projections[slot.player_id]
        else:
            ref = by_key(slot.name, slot.position, slot.team)
            if ref and ref.player_id in projections:
                projection = projections[ref.player_id]
        out.append(StarterProjection(slot=slot, projection=projection))
    return out


def _simulate_team(starters: list[StarterProjection], rng: np.random.Generator, iterations: int, stack_correlation: float) -> np.ndarray:
    """Draw `iterations` team scores, truncating each player at zero."""
    scores = np.zeros(iterations)
    if not starters:
        return scores

    # One shared shock per NFL team drives the correlated portion of each
    # player's draw; the rest is independent.
    nfl_teams = sorted({s.slot.team for s in starters if s.slot.team})
    shocks = {
        team: rng.standard_normal(iterations) for team in nfl_teams
    }
    rho = max(0.0, min(1.0, float(stack_correlation)))
    shared_weight = np.sqrt(rho)
    own_weight = np.sqrt(1.0 - rho)

    for starter in starters:
        if starter.mean <= 0 or starter.sigma <= 0:
            continue
        own = rng.standard_normal(iterations)
        shared = shocks.get(starter.slot.team)
        z = own if shared is None else shared_weight * shared + own_weight * own
        draws = starter.mean + starter.sigma * z
        np.clip(draws, 0.0, None, out=draws)
        scores += draws
    return scores


def simulate_league(
    league: League,
    projections: dict[str, PlayerProjection],
    index,
    iterations: int = 10000,
    seed: int | None = None,
    stack_correlation: float = 0.25,
) -> LeagueSim:
    rng = np.random.default_rng(seed)

    def by_key(name, position, team):
        return index.resolve(name, position, team)

    team_sims: dict[str, TeamSim] = {}
    for team in league.teams:
        starters = _match_starters(team, projections, by_key)
        scores = _simulate_team(starters, rng, iterations, stack_correlation)
        team_sims[team.team_id] = TeamSim(
            team=team,
            starters=starters,
            mean=float(np.mean(scores)) if scores.size else 0.0,
            sigma=float(np.std(scores)) if scores.size else 0.0,
            scores=scores,
        )

    matchup_sims = []
    for matchup in league.matchups:
        home = team_sims.get(matchup.home.team_id)
        away = team_sims.get(matchup.away.team_id)
        if home is None or away is None:
            continue
        matchup_sims.append(_simulate_matchup(home, away))

    log.info("simulated %d matchups x %d iterations", len(matchup_sims), iterations)
    return LeagueSim(week=0, matchups=matchup_sims, teams=list(team_sims.values()))


def _simulate_matchup(home: TeamSim, away: TeamSim) -> MatchupSim:
    margin = home.scores - away.scores
    # Ties are impossible in most leagues (decimal scoring), but split them
    # rather than awarding the home team a phantom win.
    wins = float(np.mean(margin > 0) + 0.5 * np.mean(margin == 0))
    return MatchupSim(
        home=home,
        away=away,
        home_win_probability=wins,
        spread=round(home.mean - away.mean, 2),
        total=round(home.mean + away.mean, 2),
        boom_bust=round(float(np.std(margin)), 2),
        margin_p10=round(float(np.percentile(margin, 10)), 2),
        margin_p90=round(float(np.percentile(margin, 90)), 2),
    )
