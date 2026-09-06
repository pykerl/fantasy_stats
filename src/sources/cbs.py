"""CBS Sports weekly projections (scraped).

CBS serves season-long numbers under the weekly URL whenever the week's
projections have not published yet (notably in the preseason). We detect that
via the Games Played column and skip rather than feeding season totals into a
weekly consensus. Layout drift is logged and skipped, never raised.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from .base import Projection, Source, to_float

log = logging.getLogger(__name__)

URL = "https://www.cbssports.com/fantasy/football/stats/{position}/{season}/{week}/projections/ppr/"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
POSITION_LABEL = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DST": "DEF"}

# CBS column headers carry a short code plus a long description; we key off the
# code and the section (Passing/Rushing/Receiving) the column sits under.
HEADER_MAP = {
    ("passing", "att"): "pass_att",
    ("passing", "cmp"): "pass_cmp",
    ("passing", "yds"): "pass_yd",
    ("passing", "td"): "pass_td",
    ("passing", "int"): "pass_int",
    ("rushing", "att"): "rush_att",
    ("rushing", "yds"): "rush_yd",
    ("rushing", "td"): "rush_td",
    ("receiving", "rec"): "rec",
    ("receiving", "tgt"): "rec_tgt",
    ("receiving", "yds"): "rec_yd",
    ("receiving", "td"): "rec_td",
    ("misc", "fl"): "fum_lost",
    ("kicking", "xpm"): "xpm",
    ("kicking", "fgm"): "fgm",
}

MAX_WEEKLY_GAMES = 1.5


class CBSSource(Source):
    name = "cbs"

    def fetch(self, week: int, season: int):
        pages = {}
        for position in POSITIONS:
            try:
                pages[position] = self.get(URL.format(position=position, season=season, week=week)).text
            except Exception as exc:  # noqa: BLE001 - one bad position must not kill the source
                log.warning("cbs %s page failed: %s", position, exc)
        if not pages:
            raise RuntimeError("no cbs pages fetched")
        return pages

    def parse(self, payload) -> list[Projection]:
        out: list[Projection] = []
        skipped: list[str] = []
        for position, html in payload.items():
            try:
                rows = self._parse_page(position, html)
            except _NotWeekly:
                skipped.append(position)
                continue
            except Exception as exc:  # noqa: BLE001 - layout drift: log and skip
                log.warning("cbs %s parse failed (layout change?): %s", position, exc)
                continue
            out.extend(rows)
        if skipped:
            log.warning(
                "cbs returned season-long totals for %s; weekly projections are not "
                "published yet, so those positions were skipped",
                ", ".join(skipped),
            )
        return out

    def _parse_page(self, position: str, html: str) -> list[Projection]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table is None:
            raise RuntimeError("no table on page")
        rows = table.find_all("tr")
        if len(rows) < 3:
            raise RuntimeError("table too short")

        sections = _sections(rows[0])
        columns = _columns(rows[1], sections)
        if "player" not in columns:
            raise RuntimeError("no player column")

        out: list[Projection] = []
        for tr in rows[2:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            record = dict(zip(columns, cells))

            games = to_float(record.get("gp", 1))
            if games > MAX_WEEKLY_GAMES:
                raise _NotWeekly(position)

            name, team = _split_player(record.get("player", ""))
            if not name:
                continue
            stats = {
                key: to_float(value)
                for key, value in record.items()
                if key in HEADER_MAP.values() and value not in (None, "")
            }
            out.append(
                Projection(
                    source=self.name,
                    name=name,
                    position=POSITION_LABEL[position],
                    team=team,
                    stats=stats,
                    points=to_float(record.get("fpts")) or None,
                )
            )
        if not out:
            raise RuntimeError("parsed zero rows")
        return out


class _NotWeekly(RuntimeError):
    """The page is showing season-long numbers, not this week's projection."""


def _sections(row) -> list[str]:
    """Expand the merged section header ('', '', 'Passing', 'Rushing') by colspan."""
    out: list[str] = []
    for cell in row.find_all(["th", "td"]):
        label = cell.get_text(" ", strip=True).lower()
        span = int(cell.get("colspan", 1) or 1)
        out.extend([label] * span)
    return out


def _columns(row, sections: list[str]) -> list[str]:
    """Map each column to a canonical stat key using its code + section."""
    columns: list[str] = []
    for index, cell in enumerate(row.find_all(["th", "td"])):
        text = cell.get_text(" ", strip=True)
        code = text.split()[0].lower() if text else ""
        section = sections[index] if index < len(sections) else ""
        if code == "player":
            columns.append("player")
        elif code in {"gp", "fpts"}:
            columns.append(code)
        else:
            columns.append(HEADER_MAP.get((section, code), f"_ignore_{index}"))
    return columns


def _split_player(cell: str) -> tuple[str, str]:
    """'J. Allen QB BUF Josh Allen QB BUF' -> ('Josh Allen', 'BUF').

    CBS renders an abbreviated and a full copy of the name in one cell, each
    followed by POSITION TEAM. We locate the first POSITION/TEAM pair and take
    the tokens between it and the trailing pair, which is the full name.
    """
    parts = cell.split()
    if len(parts) < 3:
        return cell, ""
    position, team = parts[-2], parts[-1]
    if not (position.isupper() and team.isupper()):
        return cell, ""
    for index in range(len(parts) - 2):
        if parts[index] == position and parts[index + 1] == team:
            full = parts[index + 2 : -2]
            if full:
                return " ".join(full), team
            break
    # Only one copy of the name present.
    return " ".join(parts[:-2]), team
