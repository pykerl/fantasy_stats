"""CLI entrypoint: python -m src.run --week 3"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import DOCS_DIR, load_config
from .pipeline import run_week
from .site import build_site

log = logging.getLogger("fantasy")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.run", description="Fantasy weekly projection engine")
    parser.add_argument("--week", type=int, help="NFL week to project (default: the current week)")
    parser.add_argument("--season", type=int, help="Season year (default: config.yaml)")
    parser.add_argument("--refresh", action="store_true", help="Bust the cache and refetch every source")
    parser.add_argument("--llm", action="store_true", help="Write the column with Claude instead of templates")
    parser.add_argument("--sources", help="Comma-separated subset of sources to use")
    parser.add_argument("--no-site", action="store_true", help="Write output/week_N.md but skip the Pages site")
    parser.add_argument("--config", help="Path to a config file (default: config.yaml)")
    parser.add_argument("--auth", action="store_true", help="Run the one-time Yahoo OAuth setup and exit")
    parser.add_argument("--list-leagues", action="store_true", help="List your Yahoo league keys and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def current_week(default: int = 1) -> int:
    """Ask Sleeper what week the NFL is on."""
    import requests  # noqa: PLC0415

    try:
        state = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15).json()
        return int(state.get("display_week") or state.get("week") or default)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not determine the current week (%s); defaulting to %d", exc, default)
        return default


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)

    if args.auth:
        return _run_auth(config)
    if args.list_leagues:
        return _list_leagues(config)

    week = args.week or current_week()
    log.info("projecting week %d", week)

    result = run_week(
        config,
        week=week,
        season=args.season,
        refresh=args.refresh,
        use_llm=args.llm,
        only_sources=args.sources.split(",") if args.sources else None,
    )

    print(f"\nWeek {result.week}: {len(result.sim.matchups)} matchups, "
          f"{len(result.projections)} players projected from {len(result.source_status)} sources")
    for matchup in result.sim.matchups:
        print(f"  {matchup.favorite.team.name} by {abs(matchup.spread):.1f} "
              f"({matchup.favorite_win_probability:.0%}) over {matchup.underdog.team.name}")

    if not args.no_site:
        build_site(config, result)
        print(f"\nSite written to {DOCS_DIR}")
    print(f"Report written to output/week_{result.week}.md")
    return 0


def _run_auth(config) -> int:
    """One-time Yahoo OAuth: writes the token JSON the league loader reads."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from .config import ROOT  # noqa: PLC0415

    key = config.secret("yahoo.consumer_key_env")
    secret = config.secret("yahoo.consumer_secret_env")
    if not key or not secret:
        print(
            "Set the Yahoo consumer key and secret first:\n"
            f"  export {config.get_path('yahoo.consumer_key_env')}=...\n"
            f"  export {config.get_path('yahoo.consumer_secret_env')}=...\n\n"
            "Create the app at https://developer.yahoo.com/apps/ with Fantasy Sports "
            "read permission and redirect URI 'oob'.",
            file=sys.stderr,
        )
        return 1

    token_file = Path(config.get_path("yahoo.token_file", "oauth2.json"))
    if not token_file.is_absolute():
        token_file = ROOT / token_file
    token_file.write_text(json.dumps({"consumer_key": key, "consumer_secret": secret}))

    from yahoo_oauth import OAuth2  # noqa: PLC0415

    session = OAuth2(None, None, from_file=str(token_file))
    if session.token_is_valid():
        print(f"Yahoo OAuth complete. Token stored in {token_file}.")
        return 0
    print("OAuth did not complete.", file=sys.stderr)
    return 1


def _list_leagues(config) -> int:
    from pathlib import Path  # noqa: PLC0415

    from .config import ROOT  # noqa: PLC0415

    token_file = Path(config.get_path("yahoo.token_file", "oauth2.json"))
    if not token_file.is_absolute():
        token_file = ROOT / token_file
    if not token_file.exists():
        print("Run `python -m src.run --auth` first.", file=sys.stderr)
        return 1

    import yahoo_fantasy_api as yfa  # noqa: PLC0415
    from yahoo_oauth import OAuth2  # noqa: PLC0415

    session = OAuth2(None, None, from_file=str(token_file))
    game = yfa.Game(session, config.get_path("yahoo.game_code", "nfl"))
    for league_key in game.league_ids():
        try:
            name = yfa.League(session, league_key).settings().get("name", "")
        except Exception:  # noqa: BLE001
            name = ""
        print(f"{league_key}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
