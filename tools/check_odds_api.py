"""Smoke-test The Odds API key and the player-prop markets we rely on.

Deliberately cheap: two requests, not the ~16 a full weekly pull costs, so it
can be run on the free tier (500/month) without eating the budget. Reports the
quota headers the API returns so you can see what is left.

The key is read from the environment and never printed, logged, or included in
any output — request URLs are reported without their query string.

    python tools/check_odds_api.py            # events + one event's props
    python tools/check_odds_api.py --quota    # quota only (1 request)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.sources.vegas import (  # noqa: E402
    EVENT_ODDS_URL,
    EVENTS_URL,
    MARKET_STATS,
    VegasSource,
    filter_events_to_week,
)

TIMEOUT = 30


def quota(response: requests.Response) -> str:
    used = response.headers.get("x-requests-used", "?")
    left = response.headers.get("x-requests-remaining", "?")
    last = response.headers.get("x-requests-last", "?")
    return f"used {used}, remaining {left} (this call cost {last})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quota", action="store_true", help="check the key and quota only (1 request)")
    parser.add_argument("--week", type=int, default=1, help="week to cost out (default 1)")
    args = parser.parse_args()

    config = load_config()
    variable = config.get_path("odds_api.key_env", "ODDS_API_KEY")
    api_key = os.environ.get(variable)
    if not api_key:
        print(f"FAIL: {variable} is not set in this environment.", file=sys.stderr)
        print(
            "      In GitHub Actions this comes from the repository secret of the same name;\n"
            "      locally, export it before running.",
            file=sys.stderr,
        )
        return 1
    print(f"{variable} is set ({len(api_key)} characters).")

    regions = config.get_path("odds_api.regions", "us")
    bookmakers = config.get_path("odds_api.bookmakers") or []
    markets = config.get_path("odds_api.markets") or list(MARKET_STATS)

    # 1) Events for the upcoming slate. Also validates the key.
    print(f"\nGET {EVENTS_URL}")
    try:
        response = requests.get(
            EVENTS_URL, params={"apiKey": api_key, "regions": regions}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        print(f"FAIL: request error: {exc}", file=sys.stderr)
        return 1

    if response.status_code == 401:
        print("FAIL: 401 Unauthorized — the key is set but not accepted.", file=sys.stderr)
        return 1
    if response.status_code == 429:
        print(f"FAIL: 429 — monthly quota exhausted. {quota(response)}", file=sys.stderr)
        return 1
    if not response.ok:
        print(f"FAIL: HTTP {response.status_code}: {response.text[:200]}", file=sys.stderr)
        return 1

    all_events = response.json()
    print(f"  OK  {response.status_code} — {len(all_events)} events listed. Quota: {quota(response)}")
    for event in all_events[:3]:
        print(f"      {event.get('commence_time', '?')}  {event.get('away_team')} at {event.get('home_team')}")
    if not all_events:
        print("\nNo upcoming events — nothing to price. This is normal in the offseason.")
        return 0

    # The endpoint lists the whole season, and every event we price costs one
    # credit per market. Pricing the full list would cost several months of the
    # free tier in a single run, so report what a real weekly pull costs.
    import requests as _requests  # noqa: PLC0415

    session = _requests.Session()
    week_events = filter_events_to_week(all_events, args.week, 0, session)
    per_pull = len(week_events) * len(markets)
    print(f"\nBudget for week {args.week}:")
    print(f"  {len(week_events)} of {len(all_events)} events are in the week's window")
    print(f"  {len(markets)} markets x 1 region = {len(markets)} credits per event")
    print(f"  a weekly pull costs about {per_pull} credits; ~{per_pull * 4.3:.0f}/month at one run per week")
    if not week_events:
        print("  (no events in that week's window — nothing would be pulled)")
    unfiltered = len(all_events) * len(markets)
    print(f"  pricing every listed event instead would cost {unfiltered} credits")

    events = week_events or all_events
    if args.quota:
        return 0

    # 2) One event's props, to validate the market keys and response shape.
    event = events[0]
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": "american",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)

    print(f"\nGET {EVENT_ODDS_URL.format(event_id='<event id>')}")
    print(f"  markets: {', '.join(markets)}")
    response = requests.get(EVENT_ODDS_URL.format(event_id=event["id"]), params=params, timeout=TIMEOUT)
    if response.status_code == 422:
        print(f"FAIL: 422 — a market key was rejected: {response.text[:300]}", file=sys.stderr)
        return 1
    if not response.ok:
        print(f"FAIL: HTTP {response.status_code}: {response.text[:300]}", file=sys.stderr)
        return 1

    payload = response.json()
    print(f"  OK  {response.status_code}. Quota: {quota(response)}")

    books = payload.get("bookmakers", [])
    seen: dict[str, int] = {}
    for book in books:
        for market in book.get("markets", []):
            seen[market["key"]] = seen.get(market["key"], 0) + len(market.get("outcomes", []))
    print(f"  bookmakers returned: {', '.join(b['key'] for b in books) or 'none'}")
    for market in markets:
        count = seen.get(market, 0)
        status = "ok" if count else "NO DATA"
        print(f"    {market:24} {count:5} outcomes  {status}")

    missing = [m for m in markets if not seen.get(m)]
    if missing and len(missing) == len(markets):
        print("\nFAIL: no configured market returned any outcome.", file=sys.stderr)
        return 1

    # 3) Run the real parser over this event, which is what the engine does.
    source = VegasSource(config)
    projections = source.parse({"events": [payload]})
    print(f"\nParsed {len(projections)} players from this one event.")
    for projection in sorted(projections, key=lambda p: -len(p.stats))[:5]:
        stats = ", ".join(f"{k}={v:.2f}" for k, v in sorted(projection.stats.items()))
        flag = " [partial]" if projection.partial else ""
        print(f"  {projection.name:24} {stats}{flag}")

    if missing:
        print(f"\nNote: no data for {', '.join(missing)} on this event. That may just be "
              "this game, or the market may not be offered yet.")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
