"""Hand-maintained league file, for when the Yahoo API is not available.

You enter each team's roster once, as plain player names. Positions and NFL
teams are resolved from Sleeper, and each week's starting lineup is chosen
automatically from that week's projections, so the file does not need editing
between weeks. Pin a lineup explicitly when you want to model the lineup a
manager actually set rather than the best one available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import ROOT
from .scoring import DEFAULT_SCORING, KNOWN_STATS, derive_fg_buckets
from .yahoo_league import League, Matchup, RosterSlot, Team, _pair_sequentially

log = logging.getLogger(__name__)

DEFAULT_FILE = "league.yaml"

DEFAULT_ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF"]

# Which positions may fill each starting slot.
SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "DST": {"DEF"},
    "W/R": {"WR", "RB"},
    "W/T": {"WR", "TE"},
    "R/T": {"RB", "TE"},
    "W/R/T": {"WR", "RB", "TE"},
    "FLEX": {"WR", "RB", "TE"},
    "Q/W/R/T": {"QB", "WR", "RB", "TE"},
    "SUPERFLEX": {"QB", "WR", "RB", "TE"},
    "OP": {"QB", "WR", "RB", "TE"},
}

SCORING_PRESETS: dict[str, dict[str, float]] = {
    "ppr": dict(DEFAULT_SCORING),
    "half_ppr": {**DEFAULT_SCORING, "rec": 0.5},
    "standard": {**DEFAULT_SCORING, "rec": 0.0},
    # Score nothing by default, so `overrides` can transcribe a league's Yahoo
    # settings page exactly. Anything not listed is worth zero, which is the
    # only safe reading of a rule the league does not have.
    "none": {},
}


LEAGUE_TEMPLATE = """\
# Hand-maintained league file.
#
# Use this while you are waiting on Yahoo API access. Once your Yahoo key
# arrives, set yahoo.league_id in config.yaml and this file is ignored
# automatically — nothing here needs deleting.
#
# You only enter PLAYER NAMES. Position and NFL team are looked up from
# Sleeper, so "Josh Allen" is enough. Check your spellings any time with:
#
#     python -m src.run --check-league

league:
  name: "My League"

  # Your starting lineup, one entry per slot. Repeat a slot to start several.
  # Valid slots: QB RB WR TE K DEF, and the flex slots
  # W/R  W/T  R/T  W/R/T (or FLEX)  Q/W/R/T (or SUPERFLEX)
  roster_positions: [QB, RB, RB, WR, WR, TE, W/R/T, K, DEF]

scoring:
  # One of: ppr, half_ppr, standard
  preset: ppr

  # Anything your league does differently. Keys are the engine's stat names;
  # see src/scoring.py for the full list.
  overrides: {}
  #  rec: 0.5          # half point per reception
  #  pass_td: 6        # 6-point passing touchdowns
  #  rec_te: 0.5       # tight end premium is not supported; use rec

teams:
  - name: "Team One"
    manager: "Your Name"
    record: [0, 0]        # wins, losses (optional)
    roster:
      - Josh Allen
      - Bijan Robinson
      - De'Von Achane
      - Ja'Marr Chase
      - Puka Nacua
      - Brock Bowers
      - Jaxon Smith-Njigba
      - Chase Brown
      - Brandon Aubrey
      - Denver Broncos
    # By default the engine starts whichever legal lineup projects highest.
    # To model the lineup a manager ACTUALLY set, list it here in the same
    # order as roster_positions above:
    # starters: [Josh Allen, Bijan Robinson, Chase Brown, Ja'Marr Chase,
    #            Puka Nacua, Brock Bowers, Jaxon Smith-Njigba,
    #            Brandon Aubrey, Denver Broncos]

  - name: "Team Two"
    manager: "Someone Else"
    record: [0, 0]
    roster:
      - Lamar Jackson
      - Saquon Barkley
      - Jahmyr Gibbs
      - CeeDee Lamb
      - Amon-Ra St. Brown
      - Trey McBride
      - Nico Collins
      - Kenneth Walker III
      - Cameron Dicker
      - Philadelphia Eagles

# Who plays whom each week. Optional — without it, teams are paired in the
# order they are listed above. Each entry is [home team, away team].
schedule:
  1:
    - ["Team One", "Team Two"]
"""


class LeagueFileError(RuntimeError):
    """The league file is missing, malformed, or internally inconsistent."""


@dataclass
class ManualPlayer:
    name: str
    player_id: str | None = None
    position: str = ""
    team: str = ""
    status: str = ""
    resolved: bool = False


@dataclass
class ManualTeam:
    name: str
    manager: str = ""
    wins: int = 0
    losses: int = 0
    ties: int = 0
    roster: list[ManualPlayer] = field(default_factory=list)
    pinned_starters: list[str] = field(default_factory=list)


@dataclass
class ManualLeague:
    name: str
    roster_positions: list[str]
    scoring: dict[str, float]
    teams: list[ManualTeam]
    schedule: dict[int, list[list[str]]] = field(default_factory=dict)
    source_path: Path | None = None


def league_file_path(config) -> Path:
    path = Path(config.get_path("league_file", DEFAULT_FILE))
    return path if path.is_absolute() else ROOT / path


def load_manual_league(path: Path) -> ManualLeague:
    """Parse and validate the league file. Raises LeagueFileError on any problem."""
    if not path.exists():
        raise LeagueFileError(f"{path.name} not found")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise LeagueFileError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise LeagueFileError(f"{path.name} must contain a mapping at the top level")

    meta = raw.get("league") or {}
    roster_positions = [str(s).upper() for s in (meta.get("roster_positions") or DEFAULT_ROSTER_POSITIONS)]
    unknown = [s for s in roster_positions if s not in SLOT_ELIGIBILITY]
    if unknown:
        raise LeagueFileError(
            f"unknown roster slot(s) {', '.join(sorted(set(unknown)))}. "
            f"Valid slots: {', '.join(sorted(SLOT_ELIGIBILITY))}"
        )

    teams = _parse_teams(raw.get("teams"), roster_positions, path)
    schedule = _parse_schedule(raw.get("schedule"), {t.name for t in teams}, path)

    return ManualLeague(
        name=meta.get("name") or "My League",
        roster_positions=roster_positions,
        scoring=_parse_scoring(raw.get("scoring")),
        teams=teams,
        schedule=schedule,
        source_path=path,
    )


def _parse_scoring(block) -> dict[str, float]:
    if not block:
        return dict(DEFAULT_SCORING)
    if isinstance(block, str):
        block = {"preset": block}
    if not isinstance(block, dict):
        raise LeagueFileError("`scoring` must be a preset name or a mapping")

    preset = str(block.get("preset", "ppr")).lower()
    if preset not in SCORING_PRESETS:
        raise LeagueFileError(
            f"unknown scoring preset '{preset}'. Valid presets: {', '.join(sorted(SCORING_PRESETS))}"
        )
    scoring = dict(SCORING_PRESETS[preset])

    overrides = block.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise LeagueFileError("`scoring.overrides` must be a mapping of stat key to points")
    unknown = sorted(k for k in overrides if k not in KNOWN_STATS)
    if unknown:
        log.warning(
            "scoring overrides use stat keys the engine does not score: %s. "
            "They will have no effect — see KNOWN_STATS in src/scoring.py for the "
            "canonical names.",
            ", ".join(unknown),
        )
    for key, value in overrides.items():
        try:
            scoring[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise LeagueFileError(f"scoring override '{key}' must be a number, got {value!r}") from exc
    return derive_fg_buckets(scoring)


def _parse_teams(block, roster_positions: list[str], path: Path) -> list[ManualTeam]:
    if not isinstance(block, list) or not block:
        raise LeagueFileError(f"{path.name} needs a `teams:` list with at least two teams")
    if len(block) < 2:
        raise LeagueFileError(f"{path.name} needs at least two teams to build a matchup")

    teams: list[ManualTeam] = []
    seen: set[str] = set()
    for index, entry in enumerate(block):
        if not isinstance(entry, dict):
            raise LeagueFileError(f"team #{index + 1} must be a mapping with a `name`")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise LeagueFileError(f"team #{index + 1} is missing a `name`")
        if name in seen:
            raise LeagueFileError(f"duplicate team name '{name}' — team names must be unique")
        seen.add(name)

        roster_names = entry.get("roster") or []
        if not isinstance(roster_names, list):
            raise LeagueFileError(f"team '{name}': `roster` must be a list of player names")
        roster = [ManualPlayer(name=str(p).strip()) for p in roster_names if str(p).strip()]
        if len(roster) < len(roster_positions):
            log.warning(
                "team '%s' has %d players for %d starting slots; empty slots will be flagged",
                name, len(roster), len(roster_positions),
            )

        pinned = entry.get("starters") or []
        if pinned and not isinstance(pinned, list):
            raise LeagueFileError(f"team '{name}': `starters` must be a list in roster_positions order")
        if pinned and len(pinned) != len(roster_positions):
            raise LeagueFileError(
                f"team '{name}': `starters` has {len(pinned)} entries but the league starts "
                f"{len(roster_positions)} ({', '.join(roster_positions)})"
            )

        record = entry.get("record") or [0, 0, 0]
        if isinstance(record, str):  # "2-1" or "2-1-0"
            record = record.split("-")
        try:
            wins, losses, ties = (list(record) + [0, 0, 0])[:3]
            wins, losses, ties = int(wins), int(losses), int(ties)
        except (TypeError, ValueError) as exc:
            raise LeagueFileError(f"team '{name}': `record` must look like [2, 1] or \"2-1\"") from exc

        teams.append(
            ManualTeam(
                name=name,
                manager=str(entry.get("manager") or ""),
                wins=wins, losses=losses, ties=ties,
                roster=roster,
                pinned_starters=[str(p).strip() for p in pinned],
            )
        )
    return teams


def _parse_schedule(block, team_names: set[str], path: Path) -> dict[int, list[list[str]]]:
    if not block:
        return {}
    if not isinstance(block, dict):
        raise LeagueFileError("`schedule` must map week numbers to a list of team pairs")

    schedule: dict[int, list[list[str]]] = {}
    for week, pairs in block.items():
        try:
            week_number = int(week)
        except (TypeError, ValueError) as exc:
            raise LeagueFileError(f"schedule key '{week}' is not a week number") from exc
        if not isinstance(pairs, list):
            raise LeagueFileError(f"schedule week {week_number} must be a list of team pairs")

        parsed: list[list[str]] = []
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise LeagueFileError(
                    f"schedule week {week_number}: each matchup must be a pair, got {pair!r}"
                )
            home, away = str(pair[0]).strip(), str(pair[1]).strip()
            for team in (home, away):
                if team not in team_names:
                    raise LeagueFileError(
                        f"schedule week {week_number} references unknown team '{team}'. "
                        f"Known teams: {', '.join(sorted(team_names))}"
                    )
            if home == away:
                raise LeagueFileError(f"schedule week {week_number}: '{home}' is matched against itself")
            parsed.append([home, away])
        schedule[week_number] = parsed
    return schedule


# --------------------------------------------------------------------------
# Resolution and lineup selection
# --------------------------------------------------------------------------


def resolve_rosters(league: ManualLeague, index) -> list[str]:
    """Resolve every roster name against Sleeper. Returns the unresolved ones."""
    unresolved: list[str] = []
    for team in league.teams:
        for player in team.roster:
            ref = index.resolve(player.name)
            if ref is None:
                unresolved.append(f"{team.name}: {player.name}")
                continue
            player.player_id = ref.player_id
            player.position = ref.position
            player.team = ref.team
            player.resolved = True
    return unresolved


# Above this many eligible candidates the exact solver is skipped for the
# greedy one. Real rosters land far below it (a 16-player roster with 9 slots
# yields roughly a dozen candidates).
MAX_EXACT_CANDIDATES = 20


def choose_lineup(
    roster: list[ManualPlayer],
    roster_positions: list[str],
    points: dict[str, float],
) -> tuple[list[RosterSlot], list[RosterSlot]]:
    """Assign the roster to starting slots to maximize projected points.

    Solved exactly. A greedy most-restrictive-first fill is optimal only when
    slot eligibility is laminar (RB inside FLEX inside SUPERFLEX); a league with
    both W/T and R/T slots breaks that, because the two eligibility sets overlap
    without nesting, and greedy strands points on the bench.
    """
    available = [p for p in roster if p.resolved]
    candidates = _candidates(available, roster_positions, points)

    if len(candidates) <= MAX_EXACT_CANDIDATES:
        assignment = _solve_exact(candidates, roster_positions, points)
    else:
        log.debug("roster too large for the exact lineup solver; using greedy")
        assignment = _solve_greedy(candidates, roster_positions, points)

    starters: list[RosterSlot] = []
    started: set[int] = set()
    for slot_index, slot in enumerate(roster_positions):
        candidate_index = assignment.get(slot_index)
        if candidate_index is None:
            starters.append(RosterSlot(slot=slot, name="(empty)", position=slot, team="", empty=True))
        else:
            player = candidates[candidate_index]
            started.add(id(player))
            starters.append(_to_slot(slot, player))

    bench = [_to_slot("BN", p) for p in roster if id(p) not in started]
    return starters, bench


def _candidates(
    available: list[ManualPlayer],
    roster_positions: list[str],
    points: dict[str, float],
) -> list[ManualPlayer]:
    """Trim the roster to the players that could possibly start.

    Only the top N players at a position can ever be used, where N is the number
    of slots that accept that position. Everyone else is strictly dominated, so
    dropping them shrinks the search without changing the answer.
    """
    capacity: dict[str, int] = {}
    for slot in roster_positions:
        for position in SLOT_ELIGIBILITY.get(slot, set()):
            capacity[position] = capacity.get(position, 0) + 1

    by_position: dict[str, list[ManualPlayer]] = {}
    for player in available:
        if player.position in capacity:
            by_position.setdefault(player.position, []).append(player)

    trimmed: list[ManualPlayer] = []
    for position, players in by_position.items():
        players.sort(key=lambda p: points.get(p.player_id or "", 0.0), reverse=True)
        trimmed.extend(players[: capacity[position]])
    return trimmed


def _solve_exact(
    candidates: list[ManualPlayer],
    roster_positions: list[str],
    points: dict[str, float],
) -> dict[int, int]:
    """Max-weight assignment of candidates to slots, by DP over a player bitmask."""
    eligible: list[list[int]] = []
    for slot in roster_positions:
        allowed = SLOT_ELIGIBILITY.get(slot, set())
        eligible.append([i for i, p in enumerate(candidates) if p.position in allowed])

    value = [points.get(p.player_id or "", 0.0) for p in candidates]
    slot_count = len(roster_positions)

    # best[mask] = (total, choice) for the slots filled so far.
    best: dict[int, tuple[float, dict[int, int]]] = {0: (0.0, {})}
    for slot_index in range(slot_count):
        nxt: dict[int, tuple[float, dict[int, int]]] = {}
        for mask, (total, choice) in best.items():
            # Leaving a slot empty is legal — a short roster has to.
            _offer(nxt, mask, total, choice)
            for candidate_index in eligible[slot_index]:
                bit = 1 << candidate_index
                if mask & bit:
                    continue
                _offer(
                    nxt,
                    mask | bit,
                    total + value[candidate_index],
                    {**choice, slot_index: candidate_index},
                )
        best = nxt

    return max(best.values(), key=lambda item: item[0])[1]


def _offer(table: dict[int, tuple[float, dict[int, int]]], mask: int, total: float, choice: dict[int, int]) -> None:
    current = table.get(mask)
    if current is None or total > current[0]:
        table[mask] = (total, choice)


def _solve_greedy(
    candidates: list[ManualPlayer],
    roster_positions: list[str],
    points: dict[str, float],
) -> dict[int, int]:
    """Most-restrictive-slot-first fill. Used only for outsized rosters."""
    order = sorted(
        range(len(roster_positions)),
        key=lambda i: len(SLOT_ELIGIBILITY.get(roster_positions[i], set())),
    )
    assignment: dict[int, int] = {}
    used: set[int] = set()
    for slot_index in order:
        allowed = SLOT_ELIGIBILITY.get(roster_positions[slot_index], set())
        best_index, best_value = None, None
        for candidate_index, player in enumerate(candidates):
            if candidate_index in used or player.position not in allowed:
                continue
            value = points.get(player.player_id or "", 0.0)
            if best_value is None or value > best_value:
                best_index, best_value = candidate_index, value
        if best_index is not None:
            assignment[slot_index] = best_index
            used.add(best_index)
    return assignment


def _to_slot(slot: str, player: ManualPlayer) -> RosterSlot:
    return RosterSlot(
        slot=slot,
        name=player.name,
        position=player.position,
        team=player.team,
        player_id=player.player_id,
        status=player.status,
    )


def pin_lineup(team: ManualTeam, roster_positions: list[str]) -> tuple[list[RosterSlot], list[RosterSlot]]:
    """Build the lineup a manager pinned in the file, in roster_positions order."""
    by_name = {p.name.lower(): p for p in team.roster}
    starters: list[RosterSlot] = []
    started: set[str] = set()
    for slot, name in zip(roster_positions, team.pinned_starters):
        player = by_name.get(str(name).strip().lower())
        if player is None:
            log.warning("team '%s': pinned starter '%s' is not on the roster", team.name, name)
            starters.append(RosterSlot(slot=slot, name=str(name), position="", team="", empty=True))
            continue
        eligible = SLOT_ELIGIBILITY.get(slot, set())
        if player.resolved and player.position not in eligible:
            log.warning(
                "team '%s': %s (%s) is not eligible for the %s slot",
                team.name, player.name, player.position, slot,
            )
        starters.append(_to_slot(slot, player))
        started.add(player.name.lower())
    bench = [_to_slot("BN", p) for p in team.roster if p.name.lower() not in started]
    return starters, bench


def build_league(
    manual: ManualLeague,
    week: int,
    index,
    points: dict[str, float],
) -> League:
    """Turn the parsed file into the League the simulator consumes."""
    unresolved = resolve_rosters(manual, index)

    teams: list[Team] = []
    auto_lineup_teams: list[str] = []
    for manual_team in manual.teams:
        if manual_team.pinned_starters:
            starters, bench = pin_lineup(manual_team, manual.roster_positions)
        else:
            starters, bench = choose_lineup(manual_team.roster, manual.roster_positions, points)
            auto_lineup_teams.append(manual_team.name)
        teams.append(
            Team(
                team_id=manual_team.name,
                name=manual_team.name,
                manager=manual_team.manager,
                wins=manual_team.wins,
                losses=manual_team.losses,
                ties=manual_team.ties,
                starters=starters,
                bench=bench,
            )
        )

    by_name = {team.name: team for team in teams}
    matchups = _matchups_for_week(manual, week, by_name, teams)

    scheduled = bool(manual.schedule.get(week))

    notes: list[str] = []
    if auto_lineup_teams:
        notes.append(
            "Lineups are projected optimal lineups, not the lineups managers have actually "
            "set — a real lineup mistake will not show up here. Pin a team's `starters:` in "
            f"{manual.source_path.name if manual.source_path else 'the league file'} to model an actual lineup."
        )
    if unresolved:
        notes.append(
            f"{len(unresolved)} roster name(s) could not be matched to a player and were left on "
            f"the bench: {', '.join(unresolved[:6])}"
            + ("…" if len(unresolved) > 6 else "")
            + ". Run `python -m src.run --check-league` to fix the spellings."
        )
    if not scheduled:
        where = (
            "No `schedule:` in the league file"
            if not manual.schedule
            else f"No schedule entry for week {week}"
        )
        notes.append(
            f"{where}, so head-to-head matchups are hidden. Team projections and the "
            "power rankings do not depend on the schedule and are unaffected. Add the "
            "week's pairings to "
            f"{manual.source_path.name if manual.source_path else 'the league file'} "
            "and the matchups come back automatically."
        )

    return League(
        name=manual.name,
        scoring=manual.scoring,
        teams=teams,
        matchups=matchups,
        is_demo=False,
        notes=notes,
        schedule_known=scheduled,
    )


def _matchups_for_week(manual: ManualLeague, week: int, by_name: dict[str, Team], teams: list[Team]) -> list[Matchup]:
    pairs = manual.schedule.get(week)
    if not pairs:
        return _pair_sequentially(teams)
    matchups = []
    for home, away in pairs:
        if home in by_name and away in by_name:
            matchups.append(Matchup(home=by_name[home], away=by_name[away]))
    return matchups or _pair_sequentially(teams)
