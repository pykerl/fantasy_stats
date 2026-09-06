"""FantasyPros weekly consensus projections (scraped).

FantasyPros is itself an aggregate of ~100 analysts, so it overlaps every other
source in this project. It is deliberately down-weighted in config.yaml.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from .base import Projection, Source, to_float

log = logging.getLogger(__name__)

URL = "https://www.fantasypros.com/nfl/projections/{position}.php"
API_URL = "https://api.fantasypros.com/v2/json/nfl/{season}/projections"
POSITIONS = ["qb", "rb", "wr", "te", "k", "dst"]

# The tables repeat column labels (ATT/YDS/TDS appear under both Passing and
# Rushing), so columns are addressed positionally per position group.
COLUMNS = {
    "qb": ["pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td", "fum_lost", "points"],
    "rb": ["rush_att", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "fum_lost", "points"],
    "wr": ["rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td", "fum_lost", "points"],
    "te": ["rec", "rec_yd", "rec_td", "fum_lost", "points"],
    "k": ["fgm", "fga", "xpm", "points"],
    "dst": ["def_sack", "def_int", "def_fum_rec", "def_ff", "def_td", "def_safe", "def_pa", "def_yds_allowed", "points"],
}

POSITION_LABEL = {"qb": "QB", "rb": "RB", "wr": "WR", "te": "TE", "k": "K", "dst": "DEF"}

# Field names used by the official FantasyPros API (enabled with FANTASYPROS_API_KEY).
API_FIELDS = {
    "pass_att": "pass_att", "pass_cmp": "pass_cmp", "pass_yds": "pass_yd",
    "pass_tds": "pass_td", "pass_ints": "pass_int",
    "rush_att": "rush_att", "rush_yds": "rush_yd", "rush_tds": "rush_td",
    "rec": "rec", "rec_yds": "rec_yd", "rec_tds": "rec_td",
    "fumbles_lost": "fum_lost",
}


class FantasyProsSource(Source):
    name = "fantasypros"

    def fetch(self, week: int, season: int):
        api_key = self.config.secret("fantasypros.key_env") if self.config else None
        if api_key:
            data = self._fetch_api(week, season, api_key)
            if data:
                return {"mode": "api", "data": data}
            log.warning("fantasypros API returned nothing; falling back to the public page")
        return {"mode": "scrape", "data": self._fetch_pages(week)}

    def _fetch_api(self, week: int, season: int, api_key: str) -> dict:
        out = {}
        for position in POSITIONS:
            try:
                response = self.get(
                    API_URL.format(season=season),
                    params={"position": position.upper(), "week": week, "scoring": "PPR"},
                    headers={"x-api-key": api_key},
                )
                out[position] = response.json()
            except Exception as exc:  # noqa: BLE001 - one bad position must not kill the source
                log.warning("fantasypros API %s failed: %s", position, exc)
        return out

    def _fetch_pages(self, week: int) -> dict:
        pages = {}
        for position in POSITIONS:
            try:
                response = self.get(URL.format(position=position), params={"week": week, "scoring": "PPR"})
                pages[position] = response.text
            except Exception as exc:  # noqa: BLE001 - one bad position must not kill the source
                log.warning("fantasypros %s page failed: %s", position, exc)
        if not pages:
            raise RuntimeError("no fantasypros pages fetched")
        return pages

    def parse(self, payload) -> list[Projection]:
        # Older cache files are a bare {position: html} mapping.
        if "mode" not in payload:
            payload = {"mode": "scrape", "data": payload}
        if payload["mode"] == "api":
            return self._parse_api(payload["data"])

        out: list[Projection] = []
        for position, html in payload["data"].items():
            try:
                out.extend(self._parse_page(position, html))
            except Exception as exc:  # noqa: BLE001 - layout drift: log and skip
                log.warning("fantasypros %s parse failed (layout change?): %s", position, exc)
        if 0 < len(out) <= len(POSITIONS) * 12:
            log.warning(
                "fantasypros returned only %d players — the public projection table is "
                "truncated for anonymous visitors. Set FANTASYPROS_API_KEY for full coverage.",
                len(out),
            )
        return out

    def _parse_api(self, data: dict) -> list[Projection]:
        out: list[Projection] = []
        for position, blob in data.items():
            for row in (blob or {}).get("players", []):
                stats = {
                    canonical: to_float(row.get(field))
                    for field, canonical in API_FIELDS.items()
                    if row.get(field) is not None
                }
                name = row.get("name") or row.get("player_name") or ""
                if not name:
                    continue
                out.append(
                    Projection(
                        source=self.name,
                        name=name,
                        position=POSITION_LABEL.get(position, position.upper()),
                        team=row.get("team_id") or row.get("team") or "",
                        stats=stats,
                        points=to_float(row.get("fpts")) or None,
                    )
                )
        return out

    def _parse_page(self, position: str, html: str) -> list[Projection]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", id="data") or soup.find("table")
        if table is None:
            raise RuntimeError("projection table not found")
        body = table.find("tbody")
        if body is None:
            raise RuntimeError("projection table has no tbody")

        columns = COLUMNS[position]
        rows: list[Projection] = []
        for tr in body.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name, team = _split_player(cells[0])
            if not name:
                continue
            values = cells[1:]
            if len(values) != len(columns):
                # Tolerate a trailing/leading extra column rather than dropping
                # the whole page, but only when the totals column still lines up.
                if len(values) < len(columns):
                    continue
                values = values[: len(columns)]
            stats = {key: to_float(val) for key, val in zip(columns, values) if key != "points"}
            rows.append(
                Projection(
                    source=self.name,
                    name=name,
                    position=POSITION_LABEL[position],
                    team=team,
                    stats=stats,
                    points=to_float(values[columns.index("points")]),
                )
            )
        if not rows:
            raise RuntimeError("projection table parsed to zero rows")
        return rows


def _split_player(cell: str) -> tuple[str, str]:
    """'Jalen Hurts PHI' -> ('Jalen Hurts', 'PHI')."""
    parts = cell.split()
    if len(parts) >= 2 and parts[-1].isupper() and 2 <= len(parts[-1]) <= 3:
        return " ".join(parts[:-1]), parts[-1]
    return cell, ""
