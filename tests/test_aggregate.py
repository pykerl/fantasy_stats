"""Consensus building and sigma estimation."""
from __future__ import annotations

import pytest

from src.aggregate import PlayerProjection, _sigma, _weighted_mean, aggregate, score_projection
from src.player_matching import MatchIndex, PlayerRef
from src.sources.base import Projection

SCORING = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}
CV = {"WR": 0.5}


@pytest.fixture
def index() -> MatchIndex:
    return MatchIndex(players={"1": PlayerRef("1", "Test Receiver", "WR", "SF")})


def test_weighted_mean_respects_weights():
    assert _weighted_mean({"a": 10, "b": 20}, {"a": 1, "b": 3}) == pytest.approx(17.5)


def test_weighted_mean_defaults_missing_weights_to_one():
    assert _weighted_mean({"a": 10, "b": 20}, {}) == pytest.approx(15.0)


def test_sigma_uses_the_positional_prior_with_one_source():
    assert _sigma(20.0, [20.0], "WR", CV) == pytest.approx(10.0)


def test_sigma_widens_when_sources_disagree():
    agree = _sigma(20.0, [20.0, 20.1, 19.9], "WR", CV)
    disagree = _sigma(20.0, [10.0, 20.0, 30.0], "WR", CV)
    assert disagree > agree


def test_sigma_never_returns_zero():
    """A player everyone projects at zero is still not a constant."""
    assert _sigma(0.0, [0.0, 0.0], "WR", CV) >= 1.0


def test_score_projection_prefers_raw_stats_over_site_points():
    """The site's own total bakes in the site's scoring, not the league's."""
    projection = Projection(source="espn", name="X", position="WR", team="SF",
                            stats={"rec": 10, "rec_yd": 100}, points=999.0)
    assert score_projection(projection, SCORING, "WR") == pytest.approx(20.0)


def test_score_projection_falls_back_to_site_points():
    projection = Projection(source="cbs", name="X", position="WR", team="SF", points=14.5)
    assert score_projection(projection, SCORING, "WR") == 14.5


def test_score_projection_returns_none_with_nothing_to_score():
    projection = Projection(source="cbs", name="X", position="WR", team="SF")
    assert score_projection(projection, SCORING, "WR") is None


def test_aggregate_combines_sources_under_league_scoring(index):
    sources = {
        "sleeper": [Projection("sleeper", "Test Receiver", "WR", "SF", {"rec": 6, "rec_yd": 80})],
        "espn": [Projection("espn", "Test Receiver", "WR", "SF", {"rec": 4, "rec_yd": 60})],
    }
    result = aggregate(sources, index, SCORING, {"sleeper": 1, "espn": 1}, CV)

    player = result["1"]
    assert player.by_source == {"sleeper": 14.0, "espn": 10.0}
    assert player.consensus == pytest.approx(12.0)
    assert player.spread == pytest.approx(4.0)
    assert player.source_count == 2


def test_aggregate_drops_a_thin_vegas_line_when_others_exist(index):
    """One lonely prop should not drag a well-covered player down."""
    sources = {
        "sleeper": [Projection("sleeper", "Test Receiver", "WR", "SF", {"rec": 6, "rec_yd": 80})],
        "vegas": [Projection("vegas", "Test Receiver", "WR", "SF", {"rec": 1.0}, partial=True)],
    }
    result = aggregate(sources, index, SCORING, {}, CV)

    player = result["1"]
    assert player.sources_used == ["sleeper"]        # the partial line is excluded...
    assert "vegas" in player.by_source               # ...but still shown for transparency
    assert player.consensus == pytest.approx(14.0)


def test_aggregate_keeps_a_partial_line_when_it_is_all_we_have(index):
    sources = {"vegas": [Projection("vegas", "Test Receiver", "WR", "SF", {"rec": 5.0}, partial=True)]}
    result = aggregate(sources, index, SCORING, {}, CV)
    assert result["1"].consensus == pytest.approx(5.0)


def test_aggregate_skips_players_it_cannot_resolve(index):
    sources = {"espn": [Projection("espn", "Nobody Atall", "WR", "SEA", {"rec": 5})]}
    assert aggregate(sources, index, SCORING, {}, CV) == {}


def test_vegas_delta_and_agreement():
    player = PlayerProjection(
        player_id="1", name="X", position="WR", team="SF",
        consensus=12.0, by_source={"a": 10.0, "b": 14.0}, spread=4.0, vegas_points=15.0,
    )
    assert player.vegas_delta == 3.0
    assert player.agreement == pytest.approx(1 - 4 / 12)
