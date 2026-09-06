"""Importing a Yahoo league export workbook into league.yaml."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("import_yahoo_export", ROOT / "tools" / "import_yahoo_export.py")
importer = importlib.util.module_from_spec(spec)
sys.modules["import_yahoo_export"] = importer
spec.loader.exec_module(importer)


def test_parse_player_splits_name_team_and_position():
    assert importer.parse_player("Jahmyr Gibbs(Det - RB)") == ("Jahmyr Gibbs", "DET", "RB")


def test_parse_player_tolerates_a_space_before_the_bracket():
    assert importer.parse_player("Spencer Shrader (Ind - K)") == ("Spencer Shrader", "IND", "K")


def test_parse_player_handles_non_breaking_spaces():
    """Yahoo's export is full of \\xa0, which breaks naive splitting."""
    assert importer.parse_player("Christian Kirk\xa0(SF - WR)") == ("Christian Kirk", "SF", "WR")


def test_parse_player_handles_a_defense():
    assert importer.parse_player("Bengals(Cin - DEF)") == ("Bengals", "CIN", "DEF")


def test_parse_player_handles_a_name_with_punctuation():
    assert importer.parse_player("Ja'Marr Chase(Cin - WR)") == ("Ja'Marr Chase", "CIN", "WR")


def test_parse_player_survives_an_unexpected_shape():
    assert importer.parse_player("Just A Name") == ("Just A Name", "", "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4, 4.0),
        (-2, -2.0),
        ("25 yards per point", 25.0),
        ("10 yards per point", 10.0),
        ("None", None),
        ("#VALUE!", None),
        ("", None),
        (None, None),
    ],
)
def test_number_reads_yahoos_value_formats(value, expected):
    assert importer._number(value) == expected


class FakeSheet:
    """Minimal stand-in for an openpyxl worksheet."""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=1, values_only=True):
        return iter(self._rows[min_row - 1 :])


SETTINGS_ROWS = [
    ("Setting", "Value"),
    ("League Name:", "Philly_VZW"),
    ("League ID#:", 779718),
    ("Roster Positions:", "QB, WR, RB, TE, W/R/T, W/R/T, K, DEF, BN, BN, BN, BN, BN, IR, IR"),
    ("Offense", "League Value"),
    ("Passing Yards", "25 yards per point"),
    ("Passing Touchdowns", 4),
    ("Interceptions", -2),
    ("Yahoo Default", "None"),
    ("Rushing Yards", "10 yards per point"),
    ("Receptions", 0.5),
    ("2-Point Conversions", 2),
    ("Kickers", "League Value"),
    ("Field Goals Total Yards", "10 yards per point"),
    ("Field Goals Missed 0-19 Yards", -3),
    ("Point After Attempt Made", 1),
    ("Defense/Special Teams", "League Value"),
    ("Sack", 1),
    ("Points Allowed 0 points", 8),
    ("Points Allowed 35+ points", -4),
]


def test_rate_scoring_becomes_points_per_unit():
    """'25 yards per point' is 0.04 points per yard, not 25 of anything."""
    scoring = importer.read_settings(FakeSheet(SETTINGS_ROWS))["scoring"]
    assert scoring["pass_yd"] == 0.04
    assert scoring["rush_yd"] == 0.1
    assert scoring["fgm_yd"] == 0.1


def test_flat_scoring_is_read_verbatim():
    scoring = importer.read_settings(FakeSheet(SETTINGS_ROWS))["scoring"]
    assert scoring["pass_td"] == 4.0
    assert scoring["pass_int"] == -2.0
    assert scoring["rec"] == 0.5
    assert scoring["fgmiss_0_19"] == -3.0
    assert scoring["def_sack"] == 1.0
    assert scoring["def_pa_0"] == 8.0
    assert scoring["def_pa_35p"] == -4.0


def test_two_point_conversions_fan_out_to_every_route():
    scoring = importer.read_settings(FakeSheet(SETTINGS_ROWS))["scoring"]
    assert scoring["pass_2pt"] == scoring["rush_2pt"] == scoring["rec_2pt"] == 2.0
    assert "two_pt" not in scoring


def test_section_headers_and_yahoo_defaults_are_not_scored():
    scoring = importer.read_settings(FakeSheet(SETTINGS_ROWS))["scoring"]
    assert not any(key in scoring for key in ("offense", "kickers", "yahoo default"))


def test_roster_positions_drop_bench_and_ir_slots():
    settings = importer.read_settings(FakeSheet(SETTINGS_ROWS))["settings"]
    assert importer.roster_positions(settings) == ["QB", "WR", "RB", "TE", "W/R/T", "W/R/T", "K", "DEF"]


def test_roster_positions_fall_back_when_the_setting_is_missing():
    assert importer.roster_positions({})[0] == "QB"


DRAFT_ROWS = [
    ("Round", "Pick", "Player (Team - Position)", "Fantasy Team Name"),
    (1, 1, "Jahmyr Gibbs(Det - RB)", "Alpha"),
    (1, 2, "Bijan Robinson(Atl - RB)", "Bravo"),
    (2, 1, "Ja'Marr Chase(Cin - WR)", "Alpha"),
    ("Waiver", "N/A", "Christian Kirk\xa0(SF - WR)", "Bravo"),
]


def test_rosters_group_by_team_in_draft_order():
    rosters, waivers = importer.read_rosters(FakeSheet(DRAFT_ROWS))
    assert rosters["Alpha"] == ["Jahmyr Gibbs", "Ja'Marr Chase"]
    assert rosters["Bravo"] == ["Bijan Robinson", "Christian Kirk"]


def test_waiver_additions_are_reported():
    _, waivers = importer.read_rosters(FakeSheet(DRAFT_ROWS))
    assert waivers == ["Bravo: Christian Kirk"]


def test_a_player_listed_twice_is_not_duplicated():
    rows = DRAFT_ROWS + [(3, 1, "Jahmyr Gibbs(Det - RB)", "Alpha")]
    rosters, _ = importer.read_rosters(FakeSheet(rows))
    assert rosters["Alpha"].count("Jahmyr Gibbs") == 1


def test_schedule_stub_pairs_every_team_and_stays_commented():
    stub = importer._schedule_stub(["Alpha", "Bravo", "Charlie", "Delta"])
    assert all(line.startswith("#") or not line.strip() for line in stub.splitlines())
    assert '#     - ["Alpha", "Bravo"]' in stub
    assert '#     - ["Charlie", "Delta"]' in stub
