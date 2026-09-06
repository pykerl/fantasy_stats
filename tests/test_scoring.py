"""Scoring math: league rules applied to raw stat lines."""
from __future__ import annotations

from src.scoring import (
    DEFAULT_SCORING,
    derive_fg_buckets,
    map_yahoo_stat_name,
    score_stats,
    scoring_from_yahoo,
)


def test_score_stats_full_ppr():
    stats = {"pass_yd": 300, "pass_td": 3, "pass_int": 1, "rush_yd": 20, "rush_td": 1}
    # 300*.04 + 3*4 + 1*-1 + 20*.1 + 1*6 = 12 + 12 - 1 + 2 + 6
    assert score_stats(stats, DEFAULT_SCORING) == 31.0


def test_score_stats_respects_half_ppr():
    stats = {"rec": 8, "rec_yd": 90, "rec_td": 1}
    full = score_stats(stats, DEFAULT_SCORING)
    half = score_stats(stats, {**DEFAULT_SCORING, "rec": 0.5})
    assert full == 8 + 9 + 6
    assert half == 4 + 9 + 6
    assert full - half == 4.0


def test_score_stats_ignores_unscored_stats():
    """A stat the league does not score contributes nothing."""
    stats = {"rec_tgt": 12, "rec": 1, "rec_yd": 10}
    assert score_stats(stats, {"rec": 1.0, "rec_yd": 0.1}) == 2.0


def test_score_stats_handles_missing_and_zero():
    assert score_stats({}, DEFAULT_SCORING) == 0.0
    assert score_stats({"rush_yd": 0}, DEFAULT_SCORING) == 0.0


def test_map_yahoo_stat_names():
    assert map_yahoo_stat_name("Passing Yards") == "pass_yd"
    assert map_yahoo_stat_name("Reception Yards") == "rec_yd"
    assert map_yahoo_stat_name("Receiving Yards") == "rec_yd"
    assert map_yahoo_stat_name("Field Goals 50+ Yards") == "fgm_50p"
    assert map_yahoo_stat_name("Completely Made Up Category") is None


def test_scoring_from_yahoo_builds_canonical_dict():
    categories = {
        "4": {"name": "Passing Yards"},
        "5": {"name": "Passing Touchdowns"},
        "11": {"name": "Receptions"},
        "16": {"name": "2-Point Conversions"},
    }
    modifiers = {"4": "0.04", "5": "4", "11": "0.5", "16": "2"}
    scoring = scoring_from_yahoo(categories, modifiers)

    assert scoring["pass_yd"] == 0.04
    assert scoring["rec"] == 0.5  # half PPR, not the sites' full PPR
    # The single Yahoo 2-point category fans out to all three scoring routes.
    assert scoring["pass_2pt"] == scoring["rush_2pt"] == scoring["rec_2pt"] == 2.0


def test_scoring_from_yahoo_falls_back_when_nothing_maps():
    scoring = scoring_from_yahoo({"1": {"name": "Nonsense"}}, {"1": 5})
    assert scoring == DEFAULT_SCORING


def test_derive_fg_buckets_combines_sub_buckets():
    """ESPN reports one 0-39 bucket; Yahoo splits it into three."""
    scoring = derive_fg_buckets({"fgm_0_19": 3.0, "fgm_20_29": 3.0, "fgm_30_39": 4.0})
    assert scoring["fgm_0_39"] == (3.0 + 3.0 + 4.0) / 3


def test_derive_fg_buckets_leaves_existing_value_alone():
    scoring = derive_fg_buckets({"fgm_0_19": 3.0, "fgm_0_39": 9.0})
    assert scoring["fgm_0_39"] == 9.0
