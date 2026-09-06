"""ESPN adapter: distinguishing a missing projection from a projected zero."""
from __future__ import annotations

from src.sources.espn import ESPNSource, _is_unprojected
from src.config import Config


def entry(**kw):
    base = {"name": "A Player", "position": "WR", "team": "SF", "points": 12.0, "stats": {"53": 5.0}}
    base.update(kw)
    return base


def test_a_row_with_no_stats_and_no_points_is_not_a_projection():
    """ESPN emits these for players it simply does not forecast."""
    assert _is_unprojected(entry(points=0.0, stats={}))
    assert _is_unprojected(entry(points=None, stats={}))


def test_a_genuine_projection_is_kept():
    assert not _is_unprojected(entry())
    assert not _is_unprojected(entry(points=8.2, stats={"74": 0.3}))


def test_a_real_zero_with_stats_is_kept():
    """A forecast of zero still carries its stat keys; only empty rows are missing."""
    assert not _is_unprojected(entry(points=0.0, stats={"53": 0.0, "42": 0.0}))


def test_parse_drops_unprojected_players():
    """The bug: a missing value averaged in as 0.0 dragged real starters down ~35%."""
    payload = {"week": 1, "players": [
        entry(name="Projected", points=12.0, stats={"53": 5.0, "42": 70.0}),
        entry(name="Not Projected", points=0.0, stats={}),
    ]}
    names = [p.name for p in ESPNSource(Config()).parse(payload)]
    assert names == ["Projected"]


def test_kicker_yardage_is_derived_only_for_kickers():
    payload = {"week": 1, "players": [
        entry(name="Kicker", position="K", points=8.0, stats={"80": 1.0, "77": 0.5, "74": 0.2}),
        entry(name="Receiver", position="WR", points=12.0, stats={"53": 5.0}),
    ]}
    parsed = {p.name: p.stats for p in ESPNSource(Config()).parse(payload)}
    assert parsed["Kicker"]["fgm_yd"] > 0
    assert "fgm_yd" not in parsed["Receiver"]
