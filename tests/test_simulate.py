"""Monte Carlo properties. These are statistical, so tolerances are generous."""
from __future__ import annotations

import numpy as np
import pytest

from src.aggregate import PlayerProjection
from src.simulate import StarterProjection, TeamSim, _simulate_matchup, _simulate_team
from src.yahoo_league import RosterSlot, Team

ITERATIONS = 40000


def starter(name: str, nfl_team: str, mean: float, sigma: float, position: str = "WR", status: str = "") -> StarterProjection:
    return StarterProjection(
        slot=RosterSlot(slot=position, name=name, position=position, team=nfl_team, status=status),
        projection=PlayerProjection(
            player_id=name, name=name, position=position, team=nfl_team, consensus=mean, sigma=sigma
        ),
    )


def test_independent_starters_add_variance_in_quadrature():
    lineup = [starter(f"p{i}", f"T{i}", 15.0, 6.0) for i in range(9)]
    scores = _simulate_team(lineup, np.random.default_rng(1), ITERATIONS, stack_correlation=0.0)
    # sigma = sqrt(9 * 6^2) = 18
    assert scores.std() == pytest.approx(18.0, rel=0.03)
    assert scores.mean() == pytest.approx(135.0, rel=0.02)


def test_stacking_teammates_increases_team_variance():
    """A QB and his receivers boom and bust together; that must widen the team."""
    spread_out = [starter(f"p{i}", f"T{i}", 15.0, 6.0) for i in range(9)]
    stacked = [starter(f"p{i}", "SF", 15.0, 6.0) for i in range(9)]

    independent = _simulate_team(spread_out, np.random.default_rng(2), ITERATIONS, 0.25)
    correlated = _simulate_team(stacked, np.random.default_rng(2), ITERATIONS, 0.25)

    assert correlated.std() > independent.std()
    # sigma = 6 * sqrt(n + n(n-1)*rho) with n=9, rho=0.25
    assert correlated.std() == pytest.approx(6 * np.sqrt(9 + 9 * 8 * 0.25), rel=0.04)


def test_zero_correlation_matches_independent_draws():
    stacked = [starter(f"p{i}", "SF", 15.0, 6.0) for i in range(9)]
    scores = _simulate_team(stacked, np.random.default_rng(3), ITERATIONS, stack_correlation=0.0)
    assert scores.std() == pytest.approx(18.0, rel=0.03)


def test_scores_are_truncated_at_zero():
    """A low-mean, high-sigma player must never post a negative score."""
    lineup = [starter("dud", "NE", 1.0, 5.0, position="K")]
    scores = _simulate_team(lineup, np.random.default_rng(4), ITERATIONS, 0.0)
    assert scores.min() >= 0.0
    assert scores.mean() > 1.0  # clipping lifts the mean above the raw projection


def test_empty_lineup_scores_zero():
    scores = _simulate_team([], np.random.default_rng(5), 100, 0.0)
    assert scores.sum() == 0.0


def test_evenly_matched_teams_are_a_coin_flip():
    lineup = [starter(f"p{i}", f"T{i}", 15.0, 6.0) for i in range(9)]
    home_scores = _simulate_team(lineup, np.random.default_rng(6), ITERATIONS, 0.0)
    away_scores = _simulate_team(lineup, np.random.default_rng(7), ITERATIONS, 0.0)

    home = TeamSim(Team("h", "Home"), lineup, float(home_scores.mean()), float(home_scores.std()), home_scores)
    away = TeamSim(Team("a", "Away"), lineup, float(away_scores.mean()), float(away_scores.std()), away_scores)
    result = _simulate_matchup(home, away)

    assert result.home_win_probability == pytest.approx(0.5, abs=0.02)
    assert result.spread == pytest.approx(0.0, abs=1.0)
    assert result.is_coin_flip


def test_a_clear_favorite_is_reported_from_the_right_side():
    strong = [starter(f"s{i}", f"T{i}", 20.0, 5.0) for i in range(9)]
    weak = [starter(f"w{i}", f"U{i}", 12.0, 5.0) for i in range(9)]
    strong_scores = _simulate_team(strong, np.random.default_rng(8), ITERATIONS, 0.0)
    weak_scores = _simulate_team(weak, np.random.default_rng(9), ITERATIONS, 0.0)

    # Put the strong team on the road, which is where the sign errors hide.
    home = TeamSim(Team("h", "Home"), weak, float(weak_scores.mean()), float(weak_scores.std()), weak_scores)
    away = TeamSim(Team("a", "Away"), strong, float(strong_scores.mean()), float(strong_scores.std()), strong_scores)
    result = _simulate_matchup(home, away)

    assert result.favorite.team.name == "Away"
    assert result.underdog.team.name == "Home"
    assert result.home_win_probability < 0.5
    assert result.favorite_win_probability > 0.5
    # Margin percentiles must be stated from the favorite's perspective.
    assert result.favorite_margin_p90 > result.favorite_margin_p10
    assert result.favorite_margin_p90 > 0


def test_problem_flags_surface_broken_lineup_slots():
    empty = StarterProjection(slot=RosterSlot(slot="WR", name="", position="WR", team="", empty=True), projection=None)
    injured = starter("Hurt Guy", "SF", 10.0, 4.0, status="O")
    healthy = starter("Fine Guy", "SF", 10.0, 4.0)

    assert empty.problem == "empty slot"
    assert "O" in injured.problem
    assert healthy.problem == ""
