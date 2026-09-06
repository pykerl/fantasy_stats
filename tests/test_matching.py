"""Cross-source player identity resolution."""
from __future__ import annotations

import pytest

from src.player_matching import (
    MatchIndex,
    PlayerRef,
    build_index,
    is_defense,
    normalize_name,
    normalize_position,
    normalize_team,
)

SLEEPER_PLAYERS = {
    "4034": {"full_name": "Christian McCaffrey", "position": "RB", "team": "SF"},
    "5846": {"first_name": "D.K.", "last_name": "Metcalf", "position": "WR", "team": "PIT"},
    "5848": {"full_name": "Marquise Brown", "position": "WR", "team": "PHI"},
    "6794": {"full_name": "Amon-Ra St. Brown", "position": "WR", "team": "DET"},
    "9509": {"full_name": "Marvin Harrison Jr.", "position": "WR", "team": "ARI"},
    "12530": {"full_name": "Travis Hunter", "position": "DB", "fantasy_positions": ["DB", "WR"], "team": "JAX"},
    "1234": {"full_name": "Mike Williams", "position": "WR", "team": "NYJ"},
    "5678": {"full_name": "Mike Williams", "position": "TE", "team": "CAR"},
    "JAX": {"full_name": "Jacksonville Jaguars", "position": "DEF", "team": "JAX"},
    "PHI": {"full_name": "Philadelphia Eagles", "position": "DEF", "team": "PHI"},
    "9999": {"full_name": "Some Longsnapper", "position": "LS", "team": "NE"},
}


@pytest.fixture
def index() -> MatchIndex:
    return build_index(SLEEPER_PLAYERS)


def test_normalize_name_collapses_initials():
    assert normalize_name("D.K. Metcalf") == "dk metcalf"
    assert normalize_name("A.J. Brown") == "aj brown"
    assert normalize_name("DK Metcalf") == "dk metcalf"


def test_normalize_name_strips_suffixes_and_punctuation():
    assert normalize_name("Marvin Harrison Jr.") == "marvin harrison"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("De'Von Achane") == "devon achane"


def test_normalize_name_folds_accents():
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_normalize_name_applies_nickname_aliases():
    assert normalize_name("Hollywood Brown") == normalize_name("Marquise Brown")


def test_normalize_team_resolves_to_sleepers_abbreviations():
    # Sleeper uses JAX, so JAC must fold into it and not the other way around.
    assert normalize_team("JAC") == "JAX"
    assert normalize_team("JAX") == "JAX"
    assert normalize_team("WSH") == "WAS"
    assert normalize_team("OAK") == "LV"
    assert normalize_team(None) == ""


def test_normalize_position_handles_defense_spellings():
    assert normalize_position("DST") == "DEF"
    assert normalize_position("D/ST") == "DEF"
    assert normalize_position("PK") == "K"


def test_is_defense_detects_team_units():
    assert is_defense("Jaguars D/ST", "DST")
    assert is_defense("Philadelphia Eagles Defense")
    assert not is_defense("Christian McCaffrey", "RB")


def test_index_excludes_non_fantasy_positions(index):
    assert "9999" not in index.players  # long snapper


def test_index_prefers_a_fantasy_position_over_the_depth_chart(index):
    """Travis Hunter is listed DB but is startable as a WR."""
    assert index.players["12530"].position == "WR"


def test_resolve_exact_match(index):
    ref = index.resolve("Christian McCaffrey", "RB", "SF")
    assert ref is not None and ref.player_id == "4034"


def test_resolve_handles_punctuation_differences(index):
    assert index.resolve("DK Metcalf", "WR", "PIT").player_id == "5846"
    assert index.resolve("D.K. Metcalf", "WR", "PIT").player_id == "5846"


def test_resolve_handles_suffix_differences(index):
    assert index.resolve("Marvin Harrison", "WR", "ARI").player_id == "9509"


def test_resolve_uses_team_to_break_a_name_collision(index):
    assert index.resolve("Mike Williams", "WR", "NYJ").player_id == "1234"
    assert index.resolve("Mike Williams", "TE", "CAR").player_id == "5678"


def test_resolve_tolerates_a_stale_team(index):
    """A source with last season's team still resolves on name + position."""
    ref = index.resolve("Christian McCaffrey", "RB", "CAR")
    assert ref is not None and ref.player_id == "4034"


def test_resolve_defense_by_team_abbreviation(index):
    assert index.resolve("Jaguars D/ST", "DST", "JAC").player_id == "JAX"
    assert index.resolve("Philadelphia Eagles", "DEF", "PHI").player_id == "PHI"


def test_resolve_returns_none_for_an_unknown_player(index):
    assert index.resolve("Nobody Atall", "WR", "SEA") is None


def test_unmatched_players_are_recorded(index):
    index.resolve("Nobody Atall", "WR", "SEA")
    assert any("Nobody Atall" in entry for entry in index.unmatched)


def test_manual_override_wins(index):
    overridden = MatchIndex(players=index.players, overrides={"Mystery Man": "4034"})
    assert overridden.resolve("Mystery Man", "WR", "SEA").player_id == "4034"


def test_fuzzy_threshold_rejects_a_bad_match():
    strict = MatchIndex(
        players={"1": PlayerRef("1", "Christian McCaffrey", "RB", "SF")},
        fuzzy_threshold=99,
    )
    assert strict.resolve("Chris McCaff", "RB", "SF") is None
