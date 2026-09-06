"""Sleeper projections — free, no auth, and the canonical player-id spine."""
from __future__ import annotations

import logging

from .base import Projection, Source

log = logging.getLogger(__name__)

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
PROJECTIONS_URL = "https://api.sleeper.app/projections/nfl/{season}/{week}"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

# Sleeper's stat keys are already our canonical vocabulary; this filters the
# hundreds of derived keys (adp, snap counts, bucketed yardage) down to the ones
# league scoring can act on.
KEEP_STATS = {
    "pass_att", "pass_cmp", "pass_inc", "pass_yd", "pass_td", "pass_int", "pass_2pt",
    "rush_att", "rush_yd", "rush_td", "rush_2pt",
    "rec", "rec_yd", "rec_td", "rec_2pt", "rec_tgt",
    "fum", "fum_lost",
    "xpm", "fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50p",
    "def_td",
}

# Sleeper names team-defense stats differently from offensive ones (`sack`, not
# `def_sack`), and publishes the points-allowed tier as a set of flags, which is
# what makes tiered defense scoring expressible as a linear stat line.
DEF_STAT_ALIASES = {
    "sack": "def_sack",
    "int": "def_int",
    "fum_rec": "def_fum_rec",
    "safe": "def_safe",
    "blk_kick": "def_blk",
    "def_td": "def_td",
    "pts_allow_0": "def_pa_0",
    "pts_allow_1_6": "def_pa_1_6",
    "pts_allow_7_13": "def_pa_7_13",
    "pts_allow_14_20": "def_pa_14_20",
    "pts_allow_21_27": "def_pa_21_27",
    "pts_allow_28_34": "def_pa_28_34",
    "pts_allow_35p": "def_pa_35p",
}


def fetch_player_master(session, refresh: bool = False) -> dict:
    """Sleeper's full player dictionary — ~5MB, so it is cached for a week."""
    from .. import cache

    payload = None if refresh else cache.read("sleeper_players", 0, 0, ttl_hours=24 * 7)
    if payload is None:
        response = session.get(PLAYERS_URL, timeout=120)
        response.raise_for_status()
        payload = response.json()
        cache.write("sleeper_players", 0, 0, payload)
    return payload


class SleeperSource(Source):
    name = "sleeper"

    def fetch(self, week: int, season: int):
        params = [("season_type", "regular"), ("order_by", "ppr")]
        params += [("position[]", pos) for pos in POSITIONS]
        url = PROJECTIONS_URL.format(season=season, week=week)
        return self.get(url, params=params).json()

    def parse(self, payload) -> list[Projection]:
        rows = payload if isinstance(payload, list) else []
        out: list[Projection] = []
        for row in rows:
            stats = row.get("stats") or {}
            player = row.get("player") or {}
            if (player.get("position") or "") == "DEF":
                kept = {
                    canonical: float(stats[key])
                    for key, canonical in DEF_STAT_ALIASES.items()
                    if stats.get(key) is not None
                }
            else:
                kept = {k: float(v) for k, v in stats.items() if k in KEEP_STATS and v is not None}
            if not kept:
                continue
            name = " ".join(filter(None, [player.get("first_name"), player.get("last_name")])).strip()
            if not name:
                continue
            out.append(
                Projection(
                    source=self.name,
                    name=name,
                    position=player.get("position") or "",
                    team=row.get("team") or player.get("team") or "",
                    stats=kept,
                    points=stats.get("pts_ppr"),
                    player_id=str(row.get("player_id")) if row.get("player_id") else None,
                )
            )
        return out
