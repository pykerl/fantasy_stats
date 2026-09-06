"""The hand-maintained league file: parsing, validation, and lineup selection."""
from __future__ import annotations

import itertools

import pytest
import yaml

from src.manual_league import (
    SCORING_PRESETS,
    SLOT_ELIGIBILITY,
    LeagueFileError,
    ManualPlayer,
    build_league,
    choose_lineup,
    load_manual_league,
    pin_lineup,
    resolve_rosters,
)
from src.manual_league import ManualTeam
from src.player_matching import MatchIndex, PlayerRef

MINIMAL = {
    "league": {"name": "Test League", "roster_positions": ["QB", "RB", "WR", "W/R/T", "DEF"]},
    "scoring": {"preset": "half_ppr", "overrides": {"pass_td": 6}},
    "teams": [
        {"name": "Alpha", "roster": ["Josh Allen", "Bijan Robinson", "Ja'Marr Chase"], "record": [2, 1]},
        {"name": "Bravo", "roster": ["Lamar Jackson", "Saquon Barkley", "CeeDee Lamb"], "record": "1-2"},
    ],
    "schedule": {1: [["Alpha", "Bravo"]]},
}


def write(tmp_path, data):
    path = tmp_path / "league.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------


def test_loads_a_valid_file(tmp_path):
    league = load_manual_league(write(tmp_path, MINIMAL))
    assert league.name == "Test League"
    assert len(league.teams) == 2
    assert league.roster_positions == ["QB", "RB", "WR", "W/R/T", "DEF"]
    assert league.schedule == {1: [["Alpha", "Bravo"]]}


def test_scoring_preset_and_overrides_are_applied(tmp_path):
    league = load_manual_league(write(tmp_path, MINIMAL))
    assert league.scoring["rec"] == 0.5           # half_ppr preset
    assert league.scoring["pass_td"] == 6.0       # override
    assert league.scoring["rush_td"] == 6.0       # untouched preset value


def test_scoring_accepts_a_bare_preset_name(tmp_path):
    data = {**MINIMAL, "scoring": "standard"}
    assert load_manual_league(write(tmp_path, data)).scoring["rec"] == 0.0


def test_presets_differ_only_in_reception_value():
    assert SCORING_PRESETS["ppr"]["rec"] == 1.0
    assert SCORING_PRESETS["half_ppr"]["rec"] == 0.5
    assert SCORING_PRESETS["standard"]["rec"] == 0.0


def test_defense_scoring_is_present_in_every_scoring_preset():
    """Team defenses used to score ~0 because these keys were missing.

    `none` is excluded deliberately: it scores nothing so that a league file can
    transcribe a Yahoo settings page exactly, without preset values leaking in.
    """
    for name, preset in SCORING_PRESETS.items():
        if name == "none":
            continue
        assert preset["def_sack"] > 0
        assert preset["def_pa_0"] > preset["def_pa_7_13"] > preset["def_pa_28_34"]


def test_the_none_preset_scores_nothing_on_its_own():
    assert SCORING_PRESETS["none"] == {}


def test_none_preset_yields_exactly_the_overrides(tmp_path):
    data = {**MINIMAL, "scoring": {"preset": "none", "overrides": {"rec": 0.5, "pass_td": 4}}}
    scoring = load_manual_league(write(tmp_path, data)).scoring
    assert scoring == {"rec": 0.5, "pass_td": 4.0}


def test_distance_scored_field_goals_are_expressible(tmp_path):
    """Some leagues score FGs by total yardage rather than by distance bucket."""
    data = {**MINIMAL, "scoring": {"preset": "none", "overrides": {"fgm_yd": 0.1, "xpm": 1}}}
    scoring = load_manual_league(write(tmp_path, data)).scoring
    assert scoring["fgm_yd"] == 0.1
    assert "fgm_0_19" not in scoring  # no bucket scoring to double-count against


def test_records_parse_from_a_list_or_a_string(tmp_path):
    league = load_manual_league(write(tmp_path, MINIMAL))
    alpha, bravo = league.teams
    assert (alpha.wins, alpha.losses) == (2, 1)
    assert (bravo.wins, bravo.losses) == (1, 2)


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(LeagueFileError, match="not found"):
        load_manual_league(tmp_path / "nope.yaml")


def test_invalid_yaml_is_reported_clearly(tmp_path):
    path = tmp_path / "league.yaml"
    path.write_text("league: [unclosed\n")
    with pytest.raises(LeagueFileError, match="not valid YAML"):
        load_manual_league(path)


def test_unknown_roster_slot_is_rejected(tmp_path):
    data = {**MINIMAL, "league": {"roster_positions": ["QB", "WIDEOUT"]}}
    with pytest.raises(LeagueFileError, match="unknown roster slot"):
        load_manual_league(write(tmp_path, data))


def test_unknown_scoring_preset_is_rejected(tmp_path):
    data = {**MINIMAL, "scoring": {"preset": "superflex_ppr_deluxe"}}
    with pytest.raises(LeagueFileError, match="unknown scoring preset"):
        load_manual_league(write(tmp_path, data))


def test_duplicate_team_names_are_rejected(tmp_path):
    data = {**MINIMAL, "teams": [{"name": "Alpha", "roster": []}, {"name": "Alpha", "roster": []}]}
    with pytest.raises(LeagueFileError, match="duplicate team name"):
        load_manual_league(write(tmp_path, data))


def test_a_single_team_is_rejected(tmp_path):
    data = {**MINIMAL, "teams": [{"name": "Alpha", "roster": []}]}
    with pytest.raises(LeagueFileError, match="at least two teams"):
        load_manual_league(write(tmp_path, data))


def test_schedule_referencing_an_unknown_team_is_rejected(tmp_path):
    data = {**MINIMAL, "schedule": {1: [["Alpha", "Ghost Team"]]}}
    with pytest.raises(LeagueFileError, match="unknown team 'Ghost Team'"):
        load_manual_league(write(tmp_path, data))


def test_team_matched_against_itself_is_rejected(tmp_path):
    data = {**MINIMAL, "schedule": {1: [["Alpha", "Alpha"]]}}
    with pytest.raises(LeagueFileError, match="matched against itself"):
        load_manual_league(write(tmp_path, data))


def test_pinned_starters_must_match_the_slot_count(tmp_path):
    data = {**MINIMAL}
    data["teams"] = [
        {"name": "Alpha", "roster": ["Josh Allen"], "starters": ["Josh Allen"]},
        {"name": "Bravo", "roster": ["Lamar Jackson"]},
    ]
    with pytest.raises(LeagueFileError, match="starters. has 1 entries"):
        load_manual_league(write(tmp_path, data))


# --------------------------------------------------------------------------
# Lineup selection
# --------------------------------------------------------------------------


def player(name, position, points_map, value):
    ref = ManualPlayer(name=name, player_id=name, position=position, team="SF", resolved=True)
    points_map[name] = value
    return ref


def brute_force_best(roster, slots, points):
    """Exhaustive optimum, for checking the solver."""
    best = 0.0
    for combo in itertools.permutations(roster, len(slots)):
        total = 0.0
        legal = True
        for slot, entry in zip(slots, combo):
            if entry.position not in SLOT_ELIGIBILITY[slot]:
                legal = False
                break
            total += points[entry.name]
        if legal:
            best = max(best, total)
    return best


def lineup_total(starters, points):
    return sum(points.get(s.name, 0.0) for s in starters if not s.empty)


def test_lineup_starts_the_best_player_at_each_slot():
    points: dict[str, float] = {}
    roster = [
        player("Starter QB", "QB", points, 24.0),
        player("Backup QB", "QB", points, 12.0),
        player("Good RB", "RB", points, 18.0),
        player("Bad RB", "RB", points, 6.0),
    ]
    starters, bench = choose_lineup(roster, ["QB", "RB"], points)
    assert [s.name for s in starters] == ["Starter QB", "Good RB"]
    assert {b.name for b in bench} == {"Backup QB", "Bad RB"}


def test_flex_takes_the_best_leftover_regardless_of_position():
    points: dict[str, float] = {}
    roster = [
        player("RB1", "RB", points, 18.0),
        player("RB2", "RB", points, 9.0),
        player("WR1", "WR", points, 20.0),
        player("TE1", "TE", points, 14.0),
    ]
    starters, _ = choose_lineup(roster, ["RB", "WR", "W/R/T"], points)
    assert [s.name for s in starters] == ["RB1", "WR1", "TE1"]


def test_lineup_is_optimal_with_overlapping_non_nested_slots():
    """W/T and R/T overlap without nesting, which defeats a greedy fill."""
    points: dict[str, float] = {}
    roster = [
        player("TE1", "TE", points, 20.0),
        player("WR1", "WR", points, 15.0),
        player("RB1", "RB", points, 14.0),
    ]
    slots = ["W/T", "R/T"]
    starters, _ = choose_lineup(roster, slots, points)
    assert lineup_total(starters, points) == pytest.approx(35.0)
    assert lineup_total(starters, points) == pytest.approx(brute_force_best(roster, slots, points))


def test_lineup_matches_brute_force_on_a_full_roster():
    points: dict[str, float] = {}
    roster = [
        player("QB1", "QB", points, 22.0), player("QB2", "QB", points, 19.0),
        player("RB1", "RB", points, 17.0), player("RB2", "RB", points, 13.0),
        player("RB3", "RB", points, 11.0), player("WR1", "WR", points, 21.0),
        player("WR2", "WR", points, 16.0), player("WR3", "WR", points, 12.0),
        player("TE1", "TE", points, 15.0),
    ]
    slots = ["QB", "RB", "WR", "W/R/T", "Q/W/R/T"]
    starters, _ = choose_lineup(roster, slots, points)
    assert lineup_total(starters, points) == pytest.approx(brute_force_best(roster, slots, points))


def test_short_roster_leaves_flagged_empty_slots():
    points: dict[str, float] = {}
    roster = [player("QB1", "QB", points, 20.0)]
    starters, _ = choose_lineup(roster, ["QB", "RB", "DEF"], points)
    assert starters[0].name == "QB1"
    assert [s.empty for s in starters] == [False, True, True]


def test_unresolved_players_never_start():
    points: dict[str, float] = {}
    roster = [player("QB1", "QB", points, 20.0), ManualPlayer(name="Typo Name")]
    starters, bench = choose_lineup(roster, ["QB"], points)
    assert starters[0].name == "QB1"
    assert "Typo Name" in {b.name for b in bench}


def test_pinned_lineup_is_used_verbatim():
    """A pinned lineup models the lineup a manager actually set, mistakes included."""
    team = ManualTeam(
        name="Alpha",
        roster=[
            ManualPlayer("Star RB", "star", "RB", "SF", resolved=True),
            ManualPlayer("Scrub RB", "scrub", "RB", "SF", resolved=True),
        ],
        pinned_starters=["Scrub RB"],
    )
    starters, bench = pin_lineup(team, ["RB"])
    assert [s.name for s in starters] == ["Scrub RB"]
    assert [b.name for b in bench] == ["Star RB"]


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


@pytest.fixture
def index() -> MatchIndex:
    return MatchIndex(players={
        "a1": PlayerRef("a1", "Josh Allen", "QB", "BUF"),
        "a2": PlayerRef("a2", "Bijan Robinson", "RB", "ATL"),
        "a3": PlayerRef("a3", "Ja'Marr Chase", "WR", "CIN"),
        "b1": PlayerRef("b1", "Lamar Jackson", "QB", "BAL"),
        "b2": PlayerRef("b2", "Saquon Barkley", "RB", "PHI"),
        "b3": PlayerRef("b3", "CeeDee Lamb", "WR", "DAL"),
        "DEN": PlayerRef("DEN", "Denver Broncos", "DEF", "DEN"),
    })


def test_resolve_rosters_reports_unmatched_names(tmp_path, index):
    data = {**MINIMAL}
    data["teams"] = [
        {"name": "Alpha", "roster": ["Josh Allen", "Notta Realguy"]},
        {"name": "Bravo", "roster": ["Lamar Jackson"]},
    ]
    league = load_manual_league(write(tmp_path, data))
    unresolved = resolve_rosters(league, index)
    assert unresolved == ["Alpha: Notta Realguy"]
    assert league.teams[0].roster[0].position == "QB"


def test_build_league_produces_matchups_and_lineups(tmp_path, index):
    league = load_manual_league(write(tmp_path, MINIMAL))
    points = {"a1": 25.0, "a2": 18.0, "a3": 16.0, "b1": 22.0, "b2": 17.0, "b3": 15.0}
    built = build_league(league, week=1, index=index, points=points)

    assert not built.is_demo
    assert built.name == "Test League"
    assert len(built.matchups) == 1
    assert built.matchups[0].home.name == "Alpha"
    assert any("projected optimal lineups" in note for note in built.notes)


def test_build_league_pairs_teams_when_the_week_is_unscheduled(tmp_path, index):
    league = load_manual_league(write(tmp_path, MINIMAL))
    built = build_league(league, week=7, index=index, points={})
    assert len(built.matchups) == 1
    assert any("week 7" in note for note in built.notes)
