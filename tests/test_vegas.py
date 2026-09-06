"""De-vig math: American odds to fair probabilities to implied points."""
from __future__ import annotations

import math

import pytest

from src.sources.vegas import (
    american_to_probability,
    anytime_td_probability,
    devig,
    devig_two_way,
    expected_from_line,
    score_vegas,
)


def test_american_to_probability_negative_odds():
    assert american_to_probability(-110) == pytest.approx(110 / 210)
    assert american_to_probability(-200) == pytest.approx(2 / 3)


def test_american_to_probability_positive_odds():
    assert american_to_probability(100) == pytest.approx(0.5)
    assert american_to_probability(150) == pytest.approx(0.4)


def test_american_to_probability_rejects_zero():
    with pytest.raises(ValueError):
        american_to_probability(0)


def test_raw_probabilities_carry_the_vig():
    """A -110/-110 market sums to more than 1 — that excess is the book's edge."""
    total = american_to_probability(-110) + american_to_probability(-110)
    assert total > 1.0
    assert total == pytest.approx(1.0476, abs=1e-4)


def test_devig_normalizes_to_one():
    result = devig([0.55, 0.52])
    assert sum(result) == pytest.approx(1.0)
    # Proportional method: the favorite keeps its share of the overround.
    assert result[0] / result[1] == pytest.approx(0.55 / 0.52)


def test_devig_two_way_balanced_market_is_fifty_fifty():
    over, under = devig_two_way(-110, -110)
    assert over == pytest.approx(0.5)
    assert under == pytest.approx(0.5)


def test_devig_two_way_favors_the_shorter_price():
    over, under = devig_two_way(-140, 115)
    assert over > under
    assert over + under == pytest.approx(1.0)


def test_devig_rejects_empty_market():
    with pytest.raises(ValueError):
        devig([0.0, 0.0])


def test_anytime_td_devigs_when_both_sides_priced():
    both = anytime_td_probability(180, -220)
    yes_only = anytime_td_probability(180)
    # De-vigging always lowers the raw implied probability.
    assert both < yes_only
    assert both == pytest.approx(0.3419, abs=1e-3)


def test_expected_from_line_returns_the_line_on_a_balanced_price():
    assert expected_from_line(65.5, 0.5) == pytest.approx(65.5)


def test_expected_from_line_shifts_with_a_lopsided_price():
    assert expected_from_line(65.5, 0.58) > 65.5
    assert expected_from_line(65.5, 0.42) < 65.5


def test_score_vegas_resolves_anytime_td_by_position():
    scoring = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "rush_td": 6.0}
    stats = {"rec": 6.0, "rec_yd": 80.0, "_td_probability": 0.5}

    receiver = score_vegas(stats, scoring, "WR")
    assert receiver == pytest.approx(6 + 8 + 3)  # TD probability scored as a rec TD

    runner = score_vegas({"rush_yd": 80.0, "_td_probability": 0.5}, scoring, "RB")
    assert runner == pytest.approx(8 + 3)  # ...and as a rush TD for a back


def test_score_vegas_without_td_market():
    scoring = {"rec": 1.0, "rec_yd": 0.1}
    assert score_vegas({"rec": 5.0, "rec_yd": 50.0}, scoring, "WR") == pytest.approx(10.0)
