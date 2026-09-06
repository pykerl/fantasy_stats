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


# --------------------------------------------------------------------------
# Request budgeting
#
# The Odds API bills one credit per market per region for every event priced,
# and its events endpoint returns the whole season. Pricing everything it
# returns costs several times the free tier's monthly allowance in one run, so
# these guard the filter that keeps a weekly pull affordable.
# --------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from src.sources.vegas import (  # noqa: E402
    WEEK_WINDOW_DAYS,
    filter_events_to_week,
    week_window,
)


class FakeSession:
    """Stands in for Sleeper's state endpoint."""

    def __init__(self, start="2026-09-09"):
        self._start = start

    def get(self, url, timeout=None):
        class Response:
            def __init__(self, start):
                self._start = start

            def json(self):
                return {"season_start_date": self._start} if self._start else {}

        return Response(self._start)


def season_events(weeks=18, per_week=16, first="2026-09-10T00:20:00Z"):
    """A full season of events, as the API actually returns them."""
    start = datetime.fromisoformat(first.replace("Z", "+00:00"))
    events = []
    for week in range(weeks):
        base = start + timedelta(days=7 * week)
        kickoffs = [base]
        kickoffs += [base + timedelta(days=3, hours=17)] * (per_week - 2)
        kickoffs += [base + timedelta(days=4)]  # Monday night, which is Tuesday in UTC
        for index, kickoff in enumerate(kickoffs):
            events.append(
                {"id": f"w{week + 1}g{index}", "commence_time": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")}
            )
    return events


def test_week_window_spans_eight_days_from_the_tuesday_anchor():
    begin, end = week_window(1, 2026, FakeSession())
    assert (end - begin).days == WEEK_WINDOW_DAYS
    assert begin == datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_week_window_advances_seven_days_per_week():
    first, _ = week_window(1, 2026, FakeSession())
    fifth, _ = week_window(5, 2026, FakeSession())
    assert (fifth - first).days == 28


def test_filter_keeps_only_the_target_week():
    events = season_events()
    kept = filter_events_to_week(events, 3, 2026, FakeSession())
    assert len(kept) == 16
    assert {e["id"].split("g")[0] for e in kept} == {"w3"}


def test_filter_does_not_bleed_into_adjacent_weeks():
    """The Monday-night game kicks off after midnight UTC; the next Thursday must stay out."""
    events = season_events()
    for week in (1, 2, 9, 18):
        kept = filter_events_to_week(events, week, 2026, FakeSession())
        assert {e["id"].split("g")[0] for e in kept} == {f"w{week}"}, f"week {week} bled"


def test_filter_bounds_the_cost_of_a_pull():
    """The whole point: a week's pull must cost a fraction of the monthly tier."""
    events = season_events()
    markets = 6
    unfiltered = len(events) * markets
    filtered = len(filter_events_to_week(events, 1, 2026, FakeSession())) * markets
    assert unfiltered > 1500          # what pricing everything would have cost
    assert filtered <= 100            # what one week actually costs
    assert filtered * 5 < 500         # and five weekly runs still fit the free tier


def test_filter_skips_events_with_unusable_timestamps():
    events = [
        {"id": "good", "commence_time": "2026-09-13T17:00:00Z"},
        {"id": "missing"},
        {"id": "malformed", "commence_time": "not a date"},
    ]
    kept = filter_events_to_week(events, 1, 2026, FakeSession())
    assert [e["id"] for e in kept] == ["good"]


def test_window_falls_back_to_now_when_the_season_start_is_unknown():
    begin, end = week_window(1, 2026, FakeSession(start=None))
    assert (end - begin).days == WEEK_WINDOW_DAYS
    assert begin <= datetime.now(timezone.utc) <= end
