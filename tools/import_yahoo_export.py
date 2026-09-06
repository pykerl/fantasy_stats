"""Build league.yaml from a Yahoo league export workbook.

The workbook has two sheets:

  "Draft and Waivers" — Round | Pick | Player (Team - Position) | Fantasy Team
                        Draft rounds plus any later waiver additions.
  "League Settings"   — Setting | Value, the Yahoo settings page verbatim,
                        including roster positions and every scoring rule.

Re-run this whenever you re-export from Yahoo; it rewrites league.yaml in
place. Rosters are the union of drafted and added players, so a player who was
dropped stays on the roster until the export stops listing them — check the
team sizes this script prints.

    python tools/import_yahoo_export.py path/to/export.xlsx
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DRAFT_SHEET = "Draft and Waivers"
SETTINGS_SHEET = "League Settings"

# Yahoo settings label -> (canonical stat key, how to read the value).
# "N yards per point" becomes 1/N points per yard.
RATE = "rate"
FLAT = "flat"

SCORING_LABELS: dict[str, tuple[str, str]] = {
    "passing yards": ("pass_yd", RATE),
    "passing touchdowns": ("pass_td", FLAT),
    "interceptions": ("pass_int", FLAT),
    "rushing yards": ("rush_yd", RATE),
    "rushing touchdowns": ("rush_td", FLAT),
    "receptions": ("rec", FLAT),
    "receiving yards": ("rec_yd", RATE),
    "receiving touchdowns": ("rec_td", FLAT),
    "return touchdowns": ("ret_td", FLAT),
    "2-point conversions": ("two_pt", FLAT),
    "fumbles lost": ("fum_lost", FLAT),
    "offensive fumble return td": ("fum_ret_td", FLAT),
    "field goals total yards": ("fgm_yd", RATE),
    "field goals missed 0-19 yards": ("fgmiss_0_19", FLAT),
    "field goals missed 20-29 yards": ("fgmiss_20_29", FLAT),
    "field goals missed 30-39 yards": ("fgmiss_30_39", FLAT),
    "field goals missed 40-49 yards": ("fgmiss_40_49", FLAT),
    "field goals missed 50+ yards": ("fgmiss_50p", FLAT),
    "field goals 0-19 yards": ("fgm_0_19", FLAT),
    "field goals 20-29 yards": ("fgm_20_29", FLAT),
    "field goals 30-39 yards": ("fgm_30_39", FLAT),
    "field goals 40-49 yards": ("fgm_40_49", FLAT),
    "field goals 50+ yards": ("fgm_50p", FLAT),
    "point after attempt made": ("xpm", FLAT),
    "point after attempt missed": ("xpm_miss", FLAT),
    "sack": ("def_sack", FLAT),
    "interception": ("def_int", FLAT),
    "fumble recovery": ("def_fum_rec", FLAT),
    "touchdown": ("def_td", FLAT),
    "safety": ("def_safe", FLAT),
    "block kick": ("def_blk", FLAT),
    "kickoff and punt return touchdowns": ("def_ret_td", FLAT),
    "extra point returned": ("def_xp_ret", FLAT),
    "points allowed 0 points": ("def_pa_0", FLAT),
    "points allowed 1-6 points": ("def_pa_1_6", FLAT),
    "points allowed 7-13 points": ("def_pa_7_13", FLAT),
    "points allowed 14-20 points": ("def_pa_14_20", FLAT),
    "points allowed 21-27 points": ("def_pa_21_27", FLAT),
    "points allowed 28-34 points": ("def_pa_28_34", FLAT),
    "points allowed 35+ points": ("def_pa_35p", FLAT),
}

# Slots that do not count toward a team's score.
BENCH_SLOTS = {"BN", "IR", "IL", "NA"}


def clean(text: str) -> str:
    """Normalize the non-breaking spaces Yahoo's export is full of."""
    return unicodedata.normalize("NFKC", str(text)).replace("\xa0", " ").strip()


def parse_player(cell: str) -> tuple[str, str, str]:
    """'Jahmyr Gibbs(Det - RB)' -> ('Jahmyr Gibbs', 'DET', 'RB')."""
    text = clean(cell)
    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", text)
    if not match:
        return text, "", ""
    name = match.group(1).strip()
    inner = [part.strip() for part in match.group(2).split("-")]
    team = inner[0].upper() if inner else ""
    position = inner[1].upper() if len(inner) > 1 else ""
    return name, team, position


def read_settings(worksheet) -> dict:
    rows = [
        (clean(r[0]).rstrip(":").lower(), r[1])
        for r in worksheet.iter_rows(min_row=2, values_only=True)
        if r[0] is not None
    ]
    settings = {label: value for label, value in rows if value is not None}

    scoring: dict[str, float] = {}
    unmapped: list[str] = []
    for label, value in rows:
        if label in {"offense", "kickers", "defense/special teams", "yahoo default"}:
            continue
        if label not in SCORING_LABELS:
            continue
        key, kind = SCORING_LABELS[label]
        number = _number(value)
        if number is None:
            unmapped.append(f"{label}={value!r}")
            continue
        scoring[key] = round(1.0 / number, 6) if kind == RATE else number

    # Yahoo lists one 2-point conversion rule covering all three routes.
    if "two_pt" in scoring:
        two = scoring.pop("two_pt")
        scoring["pass_2pt"] = scoring["rush_2pt"] = scoring["rec_2pt"] = two

    return {"settings": settings, "scoring": scoring, "unmapped": unmapped}


def _number(value) -> float | None:
    """Read '4', '-2', '25 yards per point', or 'None'."""
    if isinstance(value, (int, float)):
        return float(value)
    text = clean(value)
    if not text or text.lower() in {"none", "n/a", "#value!"}:
        return None
    match = re.match(r"^(-?\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def read_rosters(worksheet) -> tuple[dict[str, list[str]], list[str]]:
    """Team name -> roster, in draft order, with waiver additions appended."""
    rosters: dict[str, list[str]] = {}
    waivers: list[str] = []
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        if row[2] is None or row[3] is None:
            continue
        team = clean(row[3])
        name, _, _ = parse_player(row[2])
        if not name:
            continue
        roster = rosters.setdefault(team, [])
        if name not in roster:
            roster.append(name)
        if str(row[0]).strip().lower() == "waiver":
            waivers.append(f"{team}: {name}")
    return rosters, waivers


def roster_positions(settings: dict) -> list[str]:
    raw = settings.get("roster positions", "")
    slots = [clean(s).upper() for s in str(raw).split(",") if clean(s)]
    starters = [s for s in slots if s not in BENCH_SLOTS]
    return starters or ["QB", "RB", "RB", "WR", "WR", "TE", "W/R/T", "K", "DEF"]


def build(path: Path, out: Path) -> int:
    import openpyxl  # noqa: PLC0415

    workbook = openpyxl.load_workbook(path, data_only=True)
    for sheet in (DRAFT_SHEET, SETTINGS_SHEET):
        if sheet not in workbook.sheetnames:
            print(f"error: workbook has no '{sheet}' sheet (found {workbook.sheetnames})", file=sys.stderr)
            return 1

    parsed = read_settings(workbook[SETTINGS_SHEET])
    settings, scoring = parsed["settings"], parsed["scoring"]
    rosters, waivers = read_rosters(workbook[DRAFT_SHEET])
    slots = roster_positions(settings)

    league = {
        "league": {
            "name": clean(settings.get("league name", "My League")),
            "roster_positions": slots,
        },
        # `none` scores nothing by default, so the overrides below are the
        # league's complete rule set rather than a diff against a preset.
        "scoring": {"preset": "none", "overrides": dict(sorted(scoring.items()))},
        "teams": [
            {"name": team, "record": [0, 0], "roster": roster}
            for team, roster in rosters.items()
        ],
    }

    out.write_text(_render(league, settings, waivers) + _schedule_stub(list(rosters)))

    print(f"Wrote {out.name}")
    print(f"  league   : {league['league']['name']} (Yahoo id {settings.get('league id#', '?')})")
    print(f"  starters : {', '.join(slots)}")
    print(f"  scoring  : {len(scoring)} rules transcribed")
    print(f"  teams    : {len(rosters)}")
    sizes = {team: len(roster) for team, roster in rosters.items()}
    spread = sorted(set(sizes.values()))
    print(f"  roster sizes: {spread}")
    if len(spread) > 1:
        odd = [t for t, n in sizes.items() if n != spread[0]]
        print(f"    larger rosters (waiver adds with no matching drop in the export): {', '.join(odd)}")
    if waivers:
        print(f"  waiver adds: {len(waivers)}")
        for entry in waivers:
            print(f"    {entry}")
    if parsed["unmapped"]:
        print(f"  settings rows skipped: {', '.join(parsed['unmapped'])}")
    print("\nNext: python -m src.run --check-league")
    return 0


def _render(league: dict, settings: dict, waivers: list[str]) -> str:
    header = f"""\
# {league['league']['name']} — generated from the Yahoo league export.
#
# Regenerate after each re-export:
#     python tools/import_yahoo_export.py <export.xlsx>
#
# Yahoo league id : {settings.get('league id#', '?')}
# Scoring type    : {clean(settings.get('scoring type', '?'))}
# Roster          : {clean(settings.get('roster positions', '?'))}
# Playoffs        : {clean(settings.get('playoffs', '?'))}
#
# Rosters are the union of draft picks and waiver additions in the export.
# Yahoo's export does not record drops, so a team that added a player without a
# recorded drop carries an extra bench player here. That does not affect
# scoring — bench players never count — but it does mean the bench is not
# guaranteed to match Yahoo exactly.
#
# `scoring.preset: none` means nothing scores unless it is listed below, so
# these overrides are the league's complete rule set, transcribed from the
# settings page.

"""
    return header + yaml.safe_dump(league, sort_keys=False, allow_unicode=True, width=200)


def _schedule_stub(teams: list[str]) -> str:
    """A commented-out schedule block, pre-filled with the real team names.

    The Yahoo export carries no schedule, so without this the engine pairs teams
    in the order they were drafted — which is not who actually plays whom.
    """
    lines = [
        "",
        "# ---------------------------------------------------------------------------",
        "# NO SCHEDULE IN THE EXPORT.",
        "#",
        "# Yahoo's export does not include the matchup schedule, so until you fill this",
        "# in the engine pairs teams in the order above — which is NOT who actually",
        "# plays whom. Every other number (projections, spreads, win probabilities) is",
        "# correct; only the pairings are placeholders, and the report says so.",
        "#",
        "# Uncomment and set each week's matchups as [home, away]:",
        "# ---------------------------------------------------------------------------",
        "#",
        "# schedule:",
        "#   1:",
    ]
    for index in range(0, len(teams) - 1, 2):
        home = teams[index].replace('"', '\"')
        away = teams[index + 1].replace('"', '\"')
        lines.append(f'#     - ["{home}", "{away}"]')
    lines.append("#   2:")
    lines.append("#     - [...]")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", help="Yahoo league export .xlsx")
    parser.add_argument("-o", "--out", default=str(ROOT / "league.yaml"), help="output path (default: league.yaml)")
    args = parser.parse_args()

    path = Path(args.workbook)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 1
    return build(path, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
