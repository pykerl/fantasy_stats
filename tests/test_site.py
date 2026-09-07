"""The published page: executive summary, box plot, and accessible twin."""
from __future__ import annotations

import re

import numpy as np
import pytest

from src.aggregate import PlayerProjection
from src.config import Config
from src.simulate import LeagueSim, StarterProjection, TeamSim
from src.site import _nice_domain, _render_box_plot, _render_executive, _render_power_rankings
from src.yahoo_league import RosterSlot, Team


def team_sim(name: str, centre: float, sigma: float, seed: int) -> TeamSim:
    scores = np.random.default_rng(seed).normal(centre, sigma, 8000)
    starter = StarterProjection(
        slot=RosterSlot(slot="QB", name=f"{name} QB", position="QB", team="SF"),
        projection=PlayerProjection("1", f"{name} QB", "QB", "SF", consensus=centre / 8),
    )
    return TeamSim(Team(name, name), [starter], float(scores.mean()), float(scores.std()), scores)


@pytest.fixture
def sim() -> LeagueSim:
    teams = [team_sim(f"Team {i}", 80 + i * 1.5, 15.0, seed=i + 1) for i in range(16)]
    return LeagueSim(week=1, matchups=[], teams=teams)


def test_percentiles_are_ordered(sim):
    for team in sim.teams:
        assert team.floor < team.q1 < team.median < team.q3 < team.ceiling
        assert team.swing == pytest.approx(team.ceiling - team.floor, abs=0.11)


def test_nice_domain_rounds_outward():
    assert _nice_domain(64.2, 115.8) == (60, 120)
    assert _nice_domain(70.0, 110.0) == (70, 110)


# --------------------------------------------------------------------------
# Executive summary
# --------------------------------------------------------------------------


def test_exactly_one_hero_figure(sim):
    """The house rule is one hero figure per view; the second item is a tile."""
    assert _render_executive(sim).count("exec-figure") == 1


def test_hero_compares_weekly_swing_to_the_league_gap(sim):
    html = _render_executive(sim)
    ratio = float(re.search(r'exec-figure">([\d.]+)&times;', html).group(1))
    swings = sorted(t.swing for t in sim.teams)
    typical = swings[len(swings) // 2]
    gap = max(t.mean for t in sim.teams) - min(t.mean for t in sim.teams)
    assert ratio == pytest.approx(typical / gap, abs=0.1)
    assert ratio > 1, "the whole point is that the swing dwarfs the standings gap"


def test_contender_count_is_real(sim):
    html = _render_executive(sim)
    stated = int(re.search(r'exec-tile-figure">(\d+) of', html).group(1))
    top = max(t.mean for t in sim.teams)
    assert stated == sum(1 for t in sim.teams if t.ceiling >= top)


# --------------------------------------------------------------------------
# Box plot
# --------------------------------------------------------------------------


def test_one_box_per_team(sim):
    svg = _render_box_plot(sim)
    assert svg.count('class="bp-row"') == len(sim.teams)
    assert svg.count('class="bp-box"') == len(sim.teams)
    assert svg.count('class="bp-median"') == len(sim.teams)


def test_boxes_are_sorted_by_median(sim):
    svg = _render_box_plot(sim)
    names = re.findall(r'class="bp-name"[^>]*>([^<]+)<', svg)
    medians = {t.team.name: t.median for t in sim.teams}
    ordered = [medians[n] for n in names]
    assert ordered == sorted(ordered, reverse=True)


def test_box_geometry_is_inside_the_plot_area(sim):
    """A box that overflows its axis is a lie about the data."""
    from src.site import BOX_LEFT, BOX_RIGHT

    svg = _render_box_plot(sim)
    for x, width in re.findall(r'class="bp-box" x="([\d.]+)" y="[\d.-]+" width="([\d.]+)"', svg):
        assert BOX_LEFT - 0.5 <= float(x)
        assert float(x) + float(width) <= BOX_RIGHT + 0.5


def test_every_row_is_keyboard_reachable_and_labelled(sim):
    svg = _render_box_plot(sim)
    assert svg.count('tabindex="0"') == len(sim.teams)
    assert svg.count("data-readout=") == len(sim.teams)


def test_hit_target_covers_the_whole_row(sim):
    """A 14px box is a pinpoint target; the row height is the hit area."""
    from src.site import BOX_ROW_H

    assert BOX_ROW_H >= 24
    assert f'height="{BOX_ROW_H}"' in _render_box_plot(sim)


def test_axis_ticks_are_rendered_inside_the_container(sim):
    """A container sized to the plot alone crops the axis into a nested scroll."""
    from src.site import BOX_AXIS_H, BOX_ROW_H, BOX_TOP

    svg = _render_box_plot(sim)
    height = float(re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg).group(1))
    assert height >= BOX_TOP + len(sim.teams) * BOX_ROW_H + BOX_AXIS_H
    tick_ys = [float(y) for y in re.findall(r'class="bp-tick" x="[\d.]+" y="([\d.]+)"', svg)]
    assert tick_ys and max(tick_ys) <= height


def test_a_single_series_carries_no_legend(sim):
    """Every box is the same kind of thing, so there is nothing to distinguish."""
    assert "legend" not in _render_box_plot(sim).lower()


def test_gridlines_are_solid_not_dashed(sim):
    assert "stroke-dasharray" not in _render_box_plot(sim)


def test_team_names_are_escaped_into_the_svg():
    teams = [team_sim("Ben & Jerry's <script>", 90, 15, 1), team_sim("Other", 88, 15, 2)]
    svg = _render_box_plot(LeagueSim(week=1, matchups=[], teams=teams))
    assert "<script>" not in svg
    assert "&amp;" in svg and "&lt;script&gt;" in svg


# --------------------------------------------------------------------------
# The table view the tooltip must not be the only route to
# --------------------------------------------------------------------------


def test_rankings_table_carries_every_value_the_tooltip_shows(sim):
    """Tooltips enhance, they never gate."""
    table = _render_power_rankings(sim)
    for header in ("Proj", "Median", "Middle half", "Range"):
        assert f">{header}<" in table
    for team in sim.teams:
        assert f"{team.median:.1f}" in table
        assert f"{team.q1:.0f}&ndash;{team.q3:.0f}" in table
        assert f"{team.floor:.0f}&ndash;{team.ceiling:.0f}" in table


def test_rankings_table_has_one_row_per_team(sim):
    assert _render_power_rankings(sim).count("<tr><td class='num rank'>") == len(sim.teams)
