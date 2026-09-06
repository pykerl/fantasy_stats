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
    "def_sack", "def_int", "def_fum_rec", "def_td", "def_safe", "def_pa",
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
            kept = {k: float(v) for k, v in stats.items() if k in KEEP_STATS and v is not None}
            if not kept:
                continue
            player = row.get("player") or {}
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
