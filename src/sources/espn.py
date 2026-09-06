"""ESPN projections via the public league-defaults endpoint (no auth)."""
from __future__ import annotations

import json
import logging

from .base import Projection, Source

log = logging.getLogger(__name__)

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3"

# ESPN keys raw projections by numeric stat id.
STAT_IDS = {
    "0": "pass_att", "1": "pass_cmp", "2": "pass_inc", "3": "pass_yd", "4": "pass_td",
    "19": "pass_2pt", "20": "pass_int",
    "23": "rush_att", "24": "rush_yd", "25": "rush_td", "26": "rush_2pt",
    "42": "rec_yd", "43": "rec_td", "44": "rec_2pt", "53": "rec", "58": "rec_tgt",
    "68": "fum", "72": "fum_lost",
    # Kicking. ESPN stores (made, attempted, missed) triples per distance bucket;
    # these coefficients reproduce ESPN's own appliedTotal exactly (0.0 residual).
    "86": "xpm", "88": "xpm_miss",
    "80": "fgm_0_39", "82": "fgmiss_0_39",
    "77": "fgm_40_49", "79": "fgmiss_40_49",
    "74": "fgm_50p", "76": "fgmiss_50p",
}

POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

PRO_TEAMS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
    17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# statSourceId 1 = projection (0 = actual); statSplitTypeId 1 = single week.
PROJECTION_SOURCE = 1
WEEKLY_SPLIT = 1


class ESPNSource(Source):
    name = "espn"

    def fetch(self, week: int, season: int):
        headers = {
            "x-fantasy-filter": json.dumps(
                {"players": {"limit": 700, "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
            ),
            "Accept": "application/json",
        }
        response = self.get(URL.format(season=season), params={"view": "kona_player_info"}, headers=headers)
        payload = response.json()
        # Keep only the week we care about so the cache file stays small.
        return {"week": week, "players": _slim(payload.get("players", []), week)}

    def parse(self, payload) -> list[Projection]:
        out: list[Projection] = []
        for entry in payload.get("players", []):
            stats = {STAT_IDS[k]: float(v) for k, v in (entry.get("stats") or {}).items() if k in STAT_IDS}
            if not stats and entry.get("points") is None:
                continue
            out.append(
                Projection(
                    source=self.name,
                    name=entry["name"],
                    position=entry.get("position") or "",
                    team=entry.get("team") or "",
                    stats=stats,
                    points=entry.get("points"),
                )
            )
        return out


def _slim(players: list, week: int) -> list:
    """Reduce ESPN's very fat payload to one weekly projection row per player."""
    slim = []
    for wrapper in players:
        player = wrapper.get("player") or {}
        row = None
        for stat in player.get("stats") or []:
            if (
                stat.get("statSourceId") == PROJECTION_SOURCE
                and stat.get("statSplitTypeId") == WEEKLY_SPLIT
                and stat.get("scoringPeriodId") == week
            ):
                row = stat
                break
        if row is None:
            continue
        slim.append(
            {
                "name": player.get("fullName") or "",
                "position": POSITION_IDS.get(player.get("defaultPositionId"), ""),
                "team": PRO_TEAMS.get(player.get("proTeamId"), ""),
                "points": row.get("appliedTotal"),
                "stats": row.get("stats") or {},
            }
        )
    return slim
