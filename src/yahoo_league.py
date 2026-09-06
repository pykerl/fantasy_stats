"""Yahoo fantasy league integration.

Pulls scoring settings, rosters, starting lineups and the matchup schedule.
When credentials are absent the module returns a synthetic demo league built
from the week's own projections, so the full pipeline (and the published site)
can be exercised end to end before OAuth is wired up.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT
from .scoring import DEFAULT_SCORING, scoring_from_yahoo

log = logging.getLogger(__name__)

# Slots that count toward a team's score. BN/IR are carried for the report but
# never summed.
STARTING_SLOTS = {"QB", "RB", "WR", "TE", "W/R", "W/R/T", "W/T", "Q/W/R/T", "FLEX", "K", "DEF", "DST"}
BENCH_SLOTS = {"BN", "IR", "IL", "NA"}


@dataclass
class RosterSlot:
    slot: str
    name: str
    position: str
    team: str
    player_id: str | None = None
    status: str = ""  # injury designation, "" when healthy
    empty: bool = False


@dataclass
class Team:
    team_id: str
    name: str
    manager: str = ""
    logo: str = ""
    wins: int = 0
    losses: int = 0
    ties: int = 0
    starters: list[RosterSlot] = field(default_factory=list)
    bench: list[RosterSlot] = field(default_factory=list)

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


@dataclass
class Matchup:
    home: Team
    away: Team


@dataclass
class League:
    name: str
    scoring: dict[str, float]
    teams: list[Team]
    matchups: list[Matchup]
    is_demo: bool = False
    notes: list[str] = field(default_factory=list)
    # Set when the league came from a hand-maintained file whose lineups are
    # chosen from projections, so the pipeline knows to fill them later.
    manual: object | None = None
    # False when the matchups are placeholder pairings rather than the real
    # schedule. Everything matchup-derived is withheld until this is true,
    # because a spread between two teams that do not play is worse than no
    # spread at all.
    schedule_known: bool = True

    @property
    def needs_lineups(self) -> bool:
        return self.is_demo or self.manual is not None


def load_league(config, week: int, refresh: bool = False) -> League:
    """Resolve the league, in order of fidelity: Yahoo, then a hand-maintained
    league file, then a synthetic demo league.

    Rosters are returned unfilled; the caller sets lineups once projections have
    been scored under the league's rules.
    """
    source = str(config.get_path("league_source", "auto")).lower()

    if source in {"auto", "yahoo"}:
        try:
            return _load_yahoo(config, week)
        except Exception as exc:  # noqa: BLE001 - fail soft, the report says so
            if source == "yahoo":
                raise
            yahoo_error = str(exc)
            log.info("Yahoo unavailable (%s); trying the league file", yahoo_error)
    else:
        yahoo_error = ""

    if source in {"auto", "manual", "file"}:
        league = _load_manual(config, week)
        if league is not None:
            return league

    log.warning("no Yahoo credentials and no usable league file; using the demo league")
    league = build_demo_league([], week)
    if yahoo_error:
        league.notes.append(f"Yahoo league unavailable: {yahoo_error}")
    return league


def _load_manual(config, week: int) -> League | None:
    """Load the hand-maintained league file, if there is a usable one."""
    from .manual_league import LeagueFileError, league_file_path, load_manual_league  # noqa: PLC0415

    path = league_file_path(config)
    if not path.exists():
        log.info("no league file at %s", path.name)
        return None
    try:
        manual = load_manual_league(path)
    except LeagueFileError as exc:
        log.error("league file %s is unusable: %s", path.name, exc)
        return None

    log.info("loaded %d teams from %s", len(manual.teams), path.name)
    # Teams and matchups are filled in once projections exist.
    return League(
        name=manual.name,
        scoring=manual.scoring,
        teams=[],
        matchups=[],
        is_demo=False,
        manual=manual,
    )


def _load_yahoo(config, week: int) -> League:
    league_id = config.get_path("yahoo.league_id")
    if not league_id:
        raise RuntimeError("yahoo.league_id is not set in config.yaml")

    token_file = Path(config.get_path("yahoo.token_file", "oauth2.json"))
    if not token_file.is_absolute():
        token_file = ROOT / token_file
    if not token_file.exists():
        raise RuntimeError(f"OAuth token file {token_file.name} not found; run `python -m src.run --auth`")

    from yahoo_fantasy_api import League as YahooLeagueAPI  # noqa: PLC0415
    from yahoo_oauth import OAuth2  # noqa: PLC0415

    session = OAuth2(None, None, from_file=str(token_file))
    if not session.token_is_valid():
        session.refresh_access_token()

    league_key = _league_key(league_id, config)
    api = YahooLeagueAPI(session, league_key)

    settings = api.settings()
    categories = _stat_categories(api)
    modifiers = _stat_modifiers(api)
    scoring = scoring_from_yahoo(categories, modifiers)

    teams = _load_teams(api, week)
    matchups = _load_matchups(api, teams, week)

    return League(
        name=settings.get("name", "Yahoo League"),
        scoring=scoring,
        teams=list(teams.values()),
        matchups=matchups,
    )


def _league_key(league_id: str, config) -> str:
    league_id = str(league_id).strip()
    if "." in league_id:  # already a full key like 449.l.123456
        return league_id
    raise RuntimeError(
        f"yahoo.league_id '{league_id}' must be a full league key such as '461.l.123456'. "
        "Find it in your league URL or run `python -m src.run --list-leagues`."
    )


def _stat_categories(api) -> dict:
    """stat_id -> {'name': ...} from the league's stat categories."""
    out: dict[str, dict] = {}
    try:
        raw = api.stat_categories()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read Yahoo stat categories: %s", exc)
        return out
    if isinstance(raw, dict):
        for stat_id, meta in raw.items():
            out[str(stat_id)] = meta if isinstance(meta, dict) else {"name": str(meta)}
    elif isinstance(raw, list):
        for entry in raw:
            stat_id = entry.get("stat_id") or entry.get("id")
            if stat_id is not None:
                out[str(stat_id)] = entry
    return out


def _stat_modifiers(api) -> dict:
    """stat_id -> points per unit, from the league settings block."""
    settings = api.settings()
    modifiers = settings.get("stat_modifiers") or {}
    stats = modifiers.get("stats") if isinstance(modifiers, dict) else modifiers
    out: dict[str, float] = {}
    for entry in stats or []:
        stat = entry.get("stat", entry) if isinstance(entry, dict) else {}
        stat_id = stat.get("stat_id")
        value = stat.get("value")
        if stat_id is not None and value not in (None, ""):
            try:
                out[str(stat_id)] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def _load_teams(api, week: int) -> dict[str, Team]:
    teams: dict[str, Team] = {}
    standings = {}
    try:
        for row in api.standings():
            key = str(row.get("team_key") or row.get("team_id"))
            standings[key] = row.get("outcome_totals", {})
    except Exception as exc:  # noqa: BLE001 - standings are cosmetic
        log.debug("standings unavailable: %s", exc)

    for team_key, meta in api.teams().items():
        record = standings.get(str(team_key), {})
        team = Team(
            team_id=str(team_key),
            name=meta.get("name", str(team_key)),
            manager=_manager_name(meta),
            logo=_logo_url(meta),
            wins=int(record.get("wins", 0) or 0),
            losses=int(record.get("losses", 0) or 0),
            ties=int(record.get("ties", 0) or 0),
        )
        try:
            roster = api.to_team(team_key).roster(week)
        except Exception as exc:  # noqa: BLE001 - an unset lineup is report material
            log.warning("roster for %s unavailable: %s", team.name, exc)
            roster = []
        for entry in roster:
            slot = (entry.get("selected_position") or "BN").upper()
            player = RosterSlot(
                slot=slot,
                name=entry.get("name", ""),
                position=(entry.get("primary_position") or "").upper(),
                team=(entry.get("editorial_team_abbr") or "").upper(),
                player_id=str(entry.get("player_id")) if entry.get("player_id") else None,
                status=(entry.get("status") or "").upper(),
            )
            if slot in BENCH_SLOTS:
                team.bench.append(player)
            else:
                team.starters.append(player)
        teams[team.team_id] = team
    return teams


def _manager_name(meta: dict) -> str:
    managers = meta.get("managers") or []
    if isinstance(managers, list) and managers:
        first = managers[0]
        manager = first.get("manager", first) if isinstance(first, dict) else {}
        return manager.get("nickname", "") or ""
    return ""


def _logo_url(meta: dict) -> str:
    logos = meta.get("team_logos") or []
    if isinstance(logos, list) and logos:
        first = logos[0]
        logo = first.get("team_logo", first) if isinstance(first, dict) else {}
        return logo.get("url", "") if isinstance(logo, dict) else ""
    return ""


def _load_matchups(api, teams: dict[str, Team], week: int) -> list[Matchup]:
    out: list[Matchup] = []
    try:
        raw = api.matchups(week)
    except Exception as exc:  # noqa: BLE001
        log.warning("matchup schedule unavailable: %s", exc)
        return _pair_sequentially(list(teams.values()))

    for pair in _walk_matchups(raw):
        if len(pair) != 2:
            continue
        home, away = (teams.get(pair[0]), teams.get(pair[1]))
        if home and away:
            out.append(Matchup(home=home, away=away))
    if not out:
        log.warning("no matchups parsed from Yahoo; pairing teams in order")
        return _pair_sequentially(list(teams.values()))
    return out


def _walk_matchups(raw) -> list[list[str]]:
    """Yahoo's matchup payload is deeply nested; pull out team_key pairs."""
    pairs: list[list[str]] = []

    def find_team_keys(node, sink: list[str]) -> None:
        if isinstance(node, dict):
            if "team_key" in node and isinstance(node["team_key"], str):
                sink.append(node["team_key"])
            for value in node.values():
                find_team_keys(value, sink)
        elif isinstance(node, list):
            for item in node:
                find_team_keys(item, sink)

    def walk(node) -> None:
        if isinstance(node, dict):
            if "matchup" in node:
                keys: list[str] = []
                find_team_keys(node["matchup"], keys)
                unique = list(dict.fromkeys(keys))
                if len(unique) >= 2:
                    pairs.append(unique[:2])
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return pairs


def _pair_sequentially(teams: list[Team]) -> list[Matchup]:
    return [Matchup(home=teams[i], away=teams[i + 1]) for i in range(0, len(teams) - 1, 2)]


# --------------------------------------------------------------------------
# Demo league
# --------------------------------------------------------------------------

DEMO_TEAM_NAMES = [
    "Waiver Wire Warriors", "The Autodrafters", "Fourth and Inches",
    "Sunday Scaries", "Bye Week Believers", "Pancake Blocks",
    "Certified Flex Enjoyers", "The Kicker Truthers", "Regression Candidates",
    "Two Tight End Sets",
]

DEMO_LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF"]

# How far down the board a demo manager will reach for a pick.
REACH_WINDOW = 4


def build_demo_league(projections, week: int) -> League:
    """Build a plausible 10-team league by snake-drafting the week's projections.

    This exists so the engine and the published site can be exercised without
    Yahoo credentials. Every report generated this way is labelled as a demo.
    """
    rng = random.Random(1234 + week)
    by_position: dict[str, list] = {}
    for player in projections:
        by_position.setdefault(player.position, []).append(player)
    for pool in by_position.values():
        pool.sort(key=lambda p: p.consensus if hasattr(p, "consensus") else 0, reverse=True)

    teams = [Team(team_id=f"demo.{i}", name=name, manager=f"Manager {i + 1}") for i, name in enumerate(DEMO_TEAM_NAMES)]
    for team in teams:
        team.wins = rng.randint(0, max(0, week - 1))
        team.losses = max(0, (week - 1) - team.wins)

    def take(slot: str):
        """Draft the next player for a slot, with a manager's-eye reach.

        A perfectly greedy snake draft produces ten near-identical teams and a
        slate of 0.5-point spreads, which tells you nothing about the model.
        Real managers reach, so each pick is drawn from the top few available
        at the position.
        """
        options = ["RB", "WR", "TE"] if slot in {"W/R/T", "FLEX", "W/R"} else [slot]
        available = []
        for pos in options:
            pool = by_position.get(pos, [])
            # Drafted players are removed from the pool, so the top of each
            # pool is always the best available at that position.
            for offset in range(min(REACH_WINDOW, len(pool))):
                available.append((pos, offset, pool[offset]))
        if not available:
            return None
        available.sort(key=lambda item: getattr(item[2], "consensus", 0), reverse=True)
        pick_pos, pick_index, player = rng.choice(available[:REACH_WINDOW])
        pool = by_position[pick_pos]
        pool.pop(pick_index)
        return player

    for round_index, slot in enumerate(DEMO_LINEUP):
        order = teams if round_index % 2 == 0 else list(reversed(teams))
        for team in order:
            pick = take(slot)
            if pick is None:
                team.starters.append(RosterSlot(slot=slot, name="(empty)", position=slot, team="", empty=True))
                continue
            team.starters.append(
                RosterSlot(
                    slot=slot,
                    name=pick.name,
                    position=pick.position,
                    team=pick.team,
                    player_id=getattr(pick, "player_id", None),
                )
            )

    rng.shuffle(teams)
    matchups = _pair_sequentially(teams)
    return League(
        name="Demo League",
        scoring=dict(DEFAULT_SCORING),
        teams=teams,
        matchups=matchups,
        is_demo=True,
        notes=["Demo league — Yahoo credentials not configured. Rosters are snake-drafted from this week's projections."],
    )
