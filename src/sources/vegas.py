"""Betting-market implied fantasy points via The Odds API.

Player props are the market's own projection, priced by people with money at
risk. Each over/under pair is de-vigged (American odds -> implied probabilities,
normalized to sum to 1) before being turned into an expected stat line, which is
then scored under the league's own rules like any other source.

The free tier is 500 requests/month, so we pull once per week and cache hard.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .base import Projection, Source

log = logging.getLogger(__name__)

SPORT = "americanfootball_nfl"
EVENTS_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT}/events"
EVENT_ODDS_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{{event_id}}/odds"

# Prop market -> the canonical stat it implies.
MARKET_STATS = {
    "player_pass_yds": "pass_yd",
    "player_pass_tds": "pass_td",
    "player_rush_yds": "rush_yd",
    "player_reception_yds": "rec_yd",
    "player_receptions": "rec",
    "player_anytime_td": "_anytime_td",
}

# A player with fewer than this many resolved markets is flagged partial so the
# consensus does not treat thin Vegas coverage as a low projection.
MIN_MARKETS_FOR_FULL = 2

# The events endpoint is free but returns the WHOLE SEASON's schedule, while
# each event-odds call is billed one credit per market per region. Pulling every
# event returned would cost 272 x 6 = 1632 credits against a 500/month free
# tier, so events are filtered to the target week before anything is fetched.
SEASON_STATE_URL = "https://api.sleeper.app/v1/state/nfl"

# An NFL week starts Tuesday and its Monday-night game kicks off after midnight
# UTC, so the window runs eight days from the Tuesday anchor. The next week's
# Thursday game is nine days out and stays excluded.
WEEK_WINDOW_DAYS = 8


def american_to_probability(odds: float) -> float:
    """Convert American odds to an implied probability (vig included)."""
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def devig(probabilities: list[float]) -> list[float]:
    """Normalize a set of raw implied probabilities so they sum to 1.

    This is the multiplicative (proportional) method: each outcome keeps its
    share of the overround. A two-way market priced -110/-110 has a raw sum of
    ~1.0476; de-vigging returns 0.5/0.5.
    """
    total = sum(probabilities)
    if total <= 0:
        raise ValueError("probabilities must sum to a positive number")
    return [p / total for p in probabilities]


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    """De-vig an over/under pair, returning (p_over, p_under)."""
    raw = [american_to_probability(over_odds), american_to_probability(under_odds)]
    return tuple(devig(raw))  # type: ignore[return-value]


def anytime_td_probability(yes_odds: float, no_odds: float | None = None) -> float:
    """De-vigged probability that a player scores at least one touchdown.

    When the book only prices the 'Yes' side we fall back to the raw implied
    probability, which slightly overstates the true chance by the vig.
    """
    if no_odds is None:
        return american_to_probability(yes_odds)
    return devig_two_way(yes_odds, no_odds)[0]


def expected_from_line(line: float, p_over: float) -> float:
    """Expected value of a stat given its prop line and de-vigged over price.

    The line itself is the market's median. When the price is balanced the line
    is the expectation; a lopsided price means the market's mean sits off the
    line, so we nudge it proportionally. The 2x scaling keeps the adjustment
    bounded at +/-1 line-unit-ish for the extreme prices books actually post.
    """
    return float(line) * (1.0 + (p_over - 0.5) * 0.5)


def _quota_remaining(response) -> int | None:
    try:
        return int(response.headers.get("x-requests-remaining", ""))
    except (TypeError, ValueError):
        return None


def season_start(season: int, session=None) -> datetime | None:
    """The season's start date, from Sleeper's state endpoint."""
    import requests  # noqa: PLC0415

    try:
        getter = session.get if session is not None else requests.get
        state = getter(SEASON_STATE_URL, timeout=15).json()
        raw = state.get("season_start_date")
        if not raw:
            return None
        return datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc)
    except Exception as exc:  # noqa: BLE001 - fall back to a now-relative window
        log.warning("could not read the season start date (%s)", exc)
        return None


def week_window(week: int, season: int, session=None) -> tuple[datetime, datetime]:
    """The UTC window containing week `week`'s games."""
    start = season_start(season, session)
    if start is None:
        now = datetime.now(timezone.utc)
        log.warning("using a now-relative window for week %d", week)
        return now, now + timedelta(days=WEEK_WINDOW_DAYS)
    # Anchor on the Tuesday before the season's first game.
    anchor = start - timedelta(days=1) + timedelta(days=7 * (week - 1))
    return anchor, anchor + timedelta(days=WEEK_WINDOW_DAYS)


def filter_events_to_week(events: list, week: int, season: int, session=None) -> list:
    """Keep only the events kicking off inside the target week.

    The events endpoint returns the entire season, and every event we then price
    costs credits, so this filter is what keeps a weekly pull affordable.
    """
    begin, end = week_window(week, season, session)
    kept = []
    for event in events:
        raw = event.get("commence_time")
        if not raw:
            continue
        try:
            kickoff = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if begin <= kickoff < end:
            kept.append(event)
    log.debug("week %d window %s to %s kept %d of %d events", week, begin, end, len(kept), len(events))
    return kept


@dataclass
class PropLine:
    line: float | None
    p_over: float | None
    partial: bool = False


class VegasSource(Source):
    name = "vegas"

    def fetch(self, week: int, season: int):
        api_key = self.config.secret("odds_api.key_env")
        if not api_key:
            raise RuntimeError(
                "no Odds API key: set the environment variable named by odds_api.key_env"
            )
        markets = self.config.get_path("odds_api.markets") or list(MARKET_STATS)
        regions = self.config.get_path("odds_api.regions", "us")
        bookmakers = self.config.get_path("odds_api.bookmakers") or []
        max_events = int(self.config.get_path("odds_api.max_events_per_pull", 20))

        response = self.get(EVENTS_URL, params={"apiKey": api_key, "regions": regions})
        all_events = response.json()
        remaining = _quota_remaining(response)

        events = filter_events_to_week(all_events, week, season, self.session)
        if not events:
            raise RuntimeError(
                f"no games found in week {week}'s date window "
                f"(the events endpoint returned {len(all_events)} events across the season)"
            )

        # One credit per market per region, per event.
        cost = len(events) * len(markets)
        log.info(
            "odds api: %d of %d events are in week %d; fetching %d markets each "
            "(estimated %d credits, %s remaining)",
            len(events), len(all_events), week, len(markets), cost,
            remaining if remaining is not None else "unknown",
        )

        if len(events) > max_events:
            raise RuntimeError(
                f"week {week} matched {len(events)} events, more than "
                f"odds_api.max_events_per_pull ({max_events}). That would cost {cost} "
                "credits. Raise the cap deliberately if this is right."
            )
        if remaining is not None and remaining < cost:
            raise RuntimeError(
                f"pull needs about {cost} credits but only {remaining} remain this month. "
                "Reduce odds_api.markets or wait for the quota to reset."
            )

        odds = []
        for event in events:
            params = {
                "apiKey": api_key,
                "regions": regions,
                "markets": ",".join(markets),
                "oddsFormat": "american",
            }
            if bookmakers:
                params["bookmakers"] = ",".join(bookmakers)
            try:
                event_response = self.get(EVENT_ODDS_URL.format(event_id=event["id"]), params=params)
                odds.append(event_response.json())
                remaining = _quota_remaining(event_response) or remaining
            except Exception as exc:  # noqa: BLE001 - one dead game must not kill the pull
                log.warning("odds api: event %s failed: %s", event.get("id"), exc)
            time.sleep(0.2)  # be polite to a free tier
        log.info("odds api: pulled %d events, %s credits remaining", len(odds), remaining)
        return {"fetched_at": time.time(), "week": week, "events": odds}

    def parse(self, payload) -> list[Projection]:
        players: dict[str, dict] = {}
        for event in payload.get("events", []):
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    stat = MARKET_STATS.get(market.get("key"))
                    if not stat:
                        continue
                    self._collect(players, market, stat)

        out: list[Projection] = []
        for name, record in players.items():
            stats: dict[str, float] = {}
            for stat, samples in record["stats"].items():
                # Average the books, which smooths a single stale line.
                if stat == "_anytime_td":
                    stats["_anytime_td"] = sum(samples) / len(samples)
                else:
                    stats[stat] = sum(samples) / len(samples)

            anytime = stats.pop("_anytime_td", None)
            markets_hit = len(stats) + (1 if anytime is not None else 0)
            if markets_hit == 0:
                continue

            # An anytime-TD probability is a blended rush/rec TD expectation.
            # Attributing it to the position's primary scoring route keeps the
            # league's own rush_td / rec_td values correct.
            if anytime is not None:
                stats["_td_probability"] = anytime

            out.append(
                Projection(
                    source=self.name,
                    name=name,
                    position="",
                    team=record.get("team", ""),
                    stats=stats,
                    partial=markets_hit < MIN_MARKETS_FOR_FULL,
                    notes=f"{markets_hit} market(s)",
                )
            )
        return out

    def _collect(self, players: dict, market: dict, stat: str) -> None:
        """Group a market's outcomes by player and de-vig each one."""
        by_player: dict[str, dict[str, dict]] = {}
        for outcome in market.get("outcomes", []):
            player = outcome.get("description") or outcome.get("name")
            if not player:
                continue
            side = (outcome.get("name") or "").lower()
            by_player.setdefault(player, {})[side] = outcome

        for player, sides in by_player.items():
            record = players.setdefault(player, {"stats": {}, "team": ""})
            try:
                if stat == "_anytime_td":
                    yes = sides.get("yes")
                    no = sides.get("no")
                    if not yes:
                        continue
                    probability = anytime_td_probability(yes["price"], no["price"] if no else None)
                    record["stats"].setdefault("_anytime_td", []).append(probability)
                else:
                    over, under = sides.get("over"), sides.get("under")
                    if not over or over.get("point") is None:
                        continue
                    if under and under.get("price") is not None:
                        p_over, _ = devig_two_way(over["price"], under["price"])
                    else:
                        p_over = american_to_probability(over["price"])
                    value = expected_from_line(over["point"], p_over)
                    record["stats"].setdefault(stat, []).append(value)
            except (ValueError, KeyError, TypeError) as exc:
                log.debug("vegas: skipping %s/%s: %s", player, stat, exc)


def score_vegas(stats: dict[str, float], scoring: dict[str, float], position: str) -> float:
    """Score a Vegas stat line, resolving the anytime-TD probability by position."""
    from ..scoring import score_stats

    stats = dict(stats)
    probability = stats.pop("_td_probability", None)
    total = score_stats(stats, scoring)
    if probability is not None:
        # QBs rush for their anytime TDs; everyone else is scored on the route
        # their position actually scores from.
        key = "rush_td" if position in {"QB", "RB"} else "rec_td"
        total += probability * float(scoring.get(key, 6.0))
    return round(total, 3)
