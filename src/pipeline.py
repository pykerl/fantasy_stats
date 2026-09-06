"""Wires the sources, league, aggregation, simulation and report together."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

from .aggregate import PlayerProjection, aggregate
from .config import OUTPUT_DIR, Config
from .player_matching import MatchIndex, build_index
from .report import ReportContext, build_context, render_markdown, render_with_llm
from .simulate import LeagueSim, simulate_league
from .sources.base import USER_AGENT, Projection, SourceError
from .sources.cbs import CBSSource
from .sources.espn import ESPNSource
from .sources.fantasypros import FantasyProsSource
from .sources.sleeper import SleeperSource, fetch_player_master
from .sources.vegas import VegasSource
from .yahoo_league import League, load_league

log = logging.getLogger(__name__)

SOURCE_CLASSES = {
    "sleeper": SleeperSource,
    "espn": ESPNSource,
    "fantasypros": FantasyProsSource,
    "cbs": CBSSource,
    "vegas": VegasSource,
}


@dataclass
class WeeklyResult:
    week: int
    season: int
    league: League
    sim: LeagueSim
    projections: dict[str, PlayerProjection]
    context: ReportContext
    markdown: str
    source_status: dict[str, str] = field(default_factory=dict)


def run_week(
    config: Config,
    week: int,
    season: int | None = None,
    refresh: bool = False,
    use_llm: bool = False,
    only_sources: list[str] | None = None,
) -> WeeklyResult:
    season = season or int(config.get_path("season", 2026))
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    index = _build_index(config, session, refresh)
    source_projections, source_status = _load_sources(config, session, week, season, refresh, only_sources)

    # Scoring rules come from the league, but the demo league drafts from
    # already-scored projections — so load the league first for its scoring,
    # aggregate under those rules, then fill demo rosters from the result.
    league = load_league(config, week, refresh=refresh)

    projections = aggregate(
        source_projections,
        index,
        league.scoring,
        config.get_path("source_weights") or {},
        config.get_path("positional_cv") or {},
    )

    if league.is_demo:
        from .yahoo_league import build_demo_league  # noqa: PLC0415

        notes = list(league.notes)
        league = build_demo_league(sorted(projections.values(), key=lambda p: -p.consensus), week)
        for note in league.notes:
            if note not in notes:
                notes.append(note)
        league.notes = notes

    sim = simulate_league(
        league,
        projections,
        index,
        iterations=int(config.get_path("simulation.iterations", 10000)),
        seed=config.get_path("simulation.seed"),
        stack_correlation=float(config.get_path("simulation.stack_correlation", 0.25)),
    )
    sim.week = week

    context = build_context(
        sim=sim,
        week=week,
        season=season,
        league_name=league.name,
        source_status=source_status,
        league_notes=league.notes,
        is_demo=league.is_demo,
    )

    markdown = None
    if use_llm:
        markdown = render_with_llm(context, config)
    if markdown is None:
        markdown = render_markdown(context)

    output = OUTPUT_DIR / f"week_{week}.md"
    output.write_text(markdown)
    log.info("wrote %s", output)

    return WeeklyResult(
        week=week,
        season=season,
        league=league,
        sim=sim,
        projections=projections,
        context=context,
        markdown=markdown,
        source_status=source_status,
    )


def _build_index(config: Config, session, refresh: bool) -> MatchIndex:
    players = fetch_player_master(session, refresh=refresh)
    return build_index(
        players,
        overrides=config.get_path("matching.overrides") or {},
        fuzzy_threshold=int(config.get_path("matching.fuzzy_threshold", 90)),
    )


def _load_sources(
    config: Config,
    session,
    week: int,
    season: int,
    refresh: bool,
    only_sources: list[str] | None,
) -> tuple[dict[str, list[Projection]], dict[str, str]]:
    """Load every source, surviving any individual failure."""
    wanted = only_sources or list(SOURCE_CLASSES)
    projections: dict[str, list[Projection]] = {}
    status: dict[str, str] = {}

    for name in wanted:
        cls = SOURCE_CLASSES.get(name)
        if cls is None:
            log.warning("unknown source %r, skipping", name)
            continue
        # The Vegas free tier is 500 requests/month, so it gets a TTL rather
        # than being refetched on every --refresh.
        ttl = float(config.get_path("odds_api.cache_ttl_hours", 24)) if name == "vegas" else None
        source = cls(config, session=session)
        try:
            rows = source.load(week, season, refresh=refresh and name != "vegas", ttl_hours=ttl)
        except SourceError as exc:
            status[name] = f"unavailable — {exc}"
            continue
        if not rows:
            status[name] = "returned no usable projections"
            continue
        projections[name] = rows
        status[name] = f"{len(rows)} projections"

    if not projections:
        raise RuntimeError("every projection source failed; nothing to report on")
    return projections, status
