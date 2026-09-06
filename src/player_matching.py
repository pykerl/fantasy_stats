"""Cross-source player identity resolution.

Sleeper player ids are the canonical join key. Every other source arrives as
(name, team, position) and is resolved against the Sleeper master list by
normalized exact match first, then a rapidfuzz fallback.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

log = logging.getLogger(__name__)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Sources disagree on a handful of abbreviations. Sleeper is the canonical id
# space, so every alias resolves to the abbreviation Sleeper itself uses
# (notably JAX, not JAC).
TEAM_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "WFT": "WAS", "LA": "LAR", "STL": "LAR",
    "SD": "LAC", "SDG": "LAC", "OAK": "LV", "LVR": "LV", "ARZ": "ARI",
    "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "NOR": "NO", "NWE": "NE",
    "SFO": "SF", "TAM": "TB", "GNB": "GB", "KAN": "KC",
}

# Nicknames and short forms the fuzzy matcher will not bridge on its own. Keys
# and values are both post-normalization, so generational suffixes are already
# stripped and must not appear here. Never alias two distinct players together:
# "Mike Williams" and "Michael Williams" are different people.
NAME_ALIASES = {
    "hollywood brown": "marquise brown",
    "chig okonkwo": "chigoziem okonkwo",
    "gabe davis": "gabriel davis",
    "josh palmer": "joshua palmer",
    "cam ward": "cameron ward",
}

# Team defenses are named a dozen ways; normalize to the team abbreviation.
_DST_TOKENS = ("d/st", "dst", "defense", "def", "d st")


def normalize_team(team: str | None) -> str:
    if not team:
        return ""
    key = team.strip().upper()
    return TEAM_ALIASES.get(key, key)


def normalize_name(name: str) -> str:
    """Fold a display name to a comparable key.

    'D.K. Metcalf' -> 'dk metcalf'; 'Marvin Harrison Jr.' -> 'marvin harrison'.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("&", " and ")
    # Collapse initials before stripping punctuation so "D.K." -> "dk".
    text = re.sub(r"\b([a-z])\.\s*(?=[a-z]\.)", r"\1", text)
    text = re.sub(r"[.'`’]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    parts = [p for p in text.split() if p and p not in _SUFFIXES]
    key = " ".join(parts)
    return NAME_ALIASES.get(key, key)


def normalize_position(position: str | None) -> str:
    if not position:
        return ""
    key = position.strip().upper().replace("/", "")
    if key in {"DST", "DEF", "DST", "D"}:
        return "DEF"
    if key == "PK":
        return "K"
    return key


def is_defense(name: str, position: str | None = None) -> bool:
    if normalize_position(position) == "DEF":
        return True
    low = (name or "").lower()
    return any(token in low for token in _DST_TOKENS)


@dataclass
class PlayerRef:
    """A player in the canonical Sleeper id space."""

    player_id: str
    name: str
    position: str
    team: str
    key: str = ""

    def __post_init__(self) -> None:
        self.position = normalize_position(self.position)
        self.team = normalize_team(self.team)
        self.key = normalize_name(self.name)


@dataclass
class MatchIndex:
    """Index of Sleeper players supporting exact then fuzzy resolution."""

    players: dict[str, PlayerRef]
    overrides: dict[str, str] = field(default_factory=dict)
    fuzzy_threshold: int = 90
    _by_key: dict[tuple[str, str, str], PlayerRef] = field(default_factory=dict, init=False)
    _by_name_pos: dict[tuple[str, str], list[PlayerRef]] = field(default_factory=dict, init=False)
    unmatched: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        for ref in self.players.values():
            self._by_key.setdefault((ref.key, ref.position, ref.team), ref)
            self._by_name_pos.setdefault((ref.key, ref.position), []).append(ref)
        self.overrides = {normalize_name(k): v for k, v in (self.overrides or {}).items()}

    def resolve(self, name: str, position: str | None = None, team: str | None = None) -> PlayerRef | None:
        key = normalize_name(name)
        pos = normalize_position(position)
        tm = normalize_team(team)

        override_id = self.overrides.get(key)
        if override_id and override_id in self.players:
            return self.players[override_id]

        if is_defense(name, position):
            return self._resolve_defense(name, tm)

        hit = self._by_key.get((key, pos, tm))
        if hit:
            return hit

        # Name + position is usually unique; only trust it when it is.
        candidates = self._by_name_pos.get((key, pos), [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1 and tm:
            for ref in candidates:
                if ref.team == tm:
                    return ref

        return self._fuzzy(key, pos, tm, name)

    def _resolve_defense(self, name: str, team: str) -> PlayerRef | None:
        # Sleeper uses the team abbreviation as the player_id for DEF.
        if team and team in self.players:
            return self.players[team]
        low = normalize_name(name)
        for ref in self.players.values():
            if ref.position == "DEF" and (ref.key in low or low in ref.key):
                return ref
        return None

    def _fuzzy(self, key: str, pos: str, team: str, original: str) -> PlayerRef | None:
        pool = [r for r in self.players.values() if (not pos or r.position == pos) and (not team or r.team == team)]
        if not pool:
            pool = [r for r in self.players.values() if not pos or r.position == pos]
        if not pool:
            return None
        choices = {i: r.key for i, r in enumerate(pool)}
        best = process.extractOne(key, choices, scorer=fuzz.WRatio, score_cutoff=self.fuzzy_threshold)
        if best is None:
            self.unmatched.append(f"{original} ({pos or '?'}/{team or '?'})")
            return None
        return pool[best[2]]


FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


def _fantasy_position(raw: dict) -> str | None:
    """Pick the fantasy-relevant position for a Sleeper player.

    Sleeper's `position` is the depth-chart position, which hides two-way and
    utility players (Travis Hunter is listed DB with fantasy_positions
    ['DB', 'WR']). Prefer whichever listed position a fantasy league can start.
    """
    candidates = [raw.get("position")] + list(raw.get("fantasy_positions") or [])
    for candidate in candidates:
        normalized = normalize_position(candidate)
        if normalized in FANTASY_POSITIONS:
            return normalized
    return None


def build_index(sleeper_players: dict, overrides: dict | None = None, fuzzy_threshold: int = 90) -> MatchIndex:
    """Build a MatchIndex from Sleeper's /players/nfl payload."""
    refs: dict[str, PlayerRef] = {}
    for pid, raw in sleeper_players.items():
        if not isinstance(raw, dict):
            continue
        position = _fantasy_position(raw)
        if position is None:
            continue
        name = raw.get("full_name") or " ".join(
            filter(None, [raw.get("first_name"), raw.get("last_name")])
        )
        if not name:
            continue
        refs[str(pid)] = PlayerRef(
            player_id=str(pid),
            name=name,
            position=position,
            team=raw.get("team") or "",
        )
    log.info("built match index with %d players", len(refs))
    return MatchIndex(players=refs, overrides=overrides or {}, fuzzy_threshold=fuzzy_threshold)
