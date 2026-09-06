"""Static site generator for GitHub Pages.

Writes docs/ — an index with the latest week, one page per week, and a JSON
archive index so past weeks survive across runs. Pages serves docs/ directly;
there is no Jekyll or Ruby involved (hence the .nojekyll marker).
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import DOCS_DIR
from .markdown_lite import to_html
from .simulate import LeagueSim, MatchupSim, TeamSim

log = logging.getLogger(__name__)

ARCHIVE_FILE = "archive.json"


def build_site(config, result) -> Path:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / ".nojekyll").write_text("")
    (DOCS_DIR / "style.css").write_text(STYLESHEET)

    site = {
        "title": config.get_path("site.title", "The Talented Mr. Roto-ish Weekly"),
        "tagline": config.get_path("site.tagline", ""),
        "league": result.league.name,
    }

    archive = _load_archive()
    archive[str(result.week)] = {
        "week": result.week,
        "season": result.season,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league": result.league.name,
        "is_demo": result.league.is_demo,
        "matchups": len(result.sim.matchups),
        "top_team": result.sim.highest_projected.team.name,
        "top_points": round(result.sim.highest_projected.mean, 1),
    }
    _save_archive(archive)

    week_page = DOCS_DIR / f"week-{result.week}.html"
    week_page.write_text(_render_week_page(site, result, archive))

    (DOCS_DIR / "index.html").write_text(_render_week_page(site, result, archive, is_index=True))
    (DOCS_DIR / "data" / f"week-{result.week}.json").parent.mkdir(exist_ok=True)
    (DOCS_DIR / "data" / f"week-{result.week}.json").write_text(json.dumps(_week_data(result), indent=2))

    log.info("site written to %s", DOCS_DIR)
    return DOCS_DIR


def _load_archive() -> dict:
    path = DOCS_DIR / ARCHIVE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.warning("archive.json is unreadable; starting a new archive")
        return {}


def _save_archive(archive: dict) -> None:
    (DOCS_DIR / ARCHIVE_FILE).write_text(json.dumps(archive, indent=2, sort_keys=True))


def _week_data(result) -> dict:
    """Machine-readable dump of the week, published alongside the page."""
    return {
        "week": result.week,
        "season": result.season,
        "league": result.league.name,
        "is_demo": result.league.is_demo,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_status": result.source_status,
        "matchups": [
            {
                "home": m.home.team.name,
                "away": m.away.team.name,
                "home_projected": round(m.home.mean, 2),
                "away_projected": round(m.away.mean, 2),
                "home_sigma": round(m.home.sigma, 2),
                "away_sigma": round(m.away.sigma, 2),
                "home_win_probability": round(m.home_win_probability, 4),
                "spread": m.spread,
                "boom_bust_index": m.boom_bust,
            }
            for m in result.sim.matchups
        ],
        "players": [
            {
                "name": p.name,
                "position": p.position,
                "team": p.team,
                "consensus": p.consensus,
                "sigma": p.sigma,
                "sources": p.by_source,
                "vegas_implied": p.vegas_points,
            }
            for p in sorted(result.projections.values(), key=lambda x: -x.consensus)[:300]
        ],
    }


def _render_week_page(site: dict, result, archive: dict, is_index: bool = False) -> str:
    sim: LeagueSim = result.sim
    body: list[str] = []

    if result.league.is_demo:
        body.append(
            '<div class="banner"><strong>Demo mode.</strong> Yahoo credentials are not '
            "configured, so the teams below are snake-drafted from this week's projections. "
            "The projections, the Vegas math and the simulation are real; the league is not.</div>"
        )

    body.append(_render_scoreboard(sim))
    body.append(_render_league_notes(sim))
    body.append(f'<section class="column">{to_html(result.markdown)}</section>')
    body.append(_render_source_table(result.source_status))
    body.append(_render_player_table(result.projections))
    body.append(_render_archive(archive))

    subtitle = f"Week {result.week} · {result.season} · {html.escape(result.league.name)}"
    return _page(
        title=f"Week {result.week} — {site['title']}",
        heading=site["title"],
        tagline=site["tagline"],
        subtitle=subtitle,
        body="\n".join(body),
    )


def _render_scoreboard(sim: LeagueSim) -> str:
    # A tag that fires on every card is not a tag. "High variance" is judged
    # against this slate's own median rather than an absolute threshold, which
    # would light up every game in a high-scoring league.
    booms = sorted(m.boom_bust for m in sim.matchups)
    if booms:
        median = booms[len(booms) // 2] if len(booms) % 2 else (booms[len(booms) // 2 - 1] + booms[len(booms) // 2]) / 2
        volatile_cut = median * 1.08
    else:
        volatile_cut = float("inf")
    cards = [_matchup_card(m, volatile_cut) for m in sim.matchups]
    if not cards:
        return '<p class="empty">No matchups to project this week.</p>'
    return (
        '<section><h2 class="section-title">Projected Scoreboard</h2>'
        f'<div class="grid">{"".join(cards)}</div></section>'
    )


def _matchup_card(m: MatchupSim, volatile_cut: float) -> str:
    home_pct = m.home_win_probability * 100
    away_pct = 100 - home_pct
    if m.is_coin_flip:
        # Nobody is favored, so "upset" has no meaning here.
        upset = '<span class="tag tag-flip">Coin flip</span>'
    elif m.is_upset_alert:
        upset = '<span class="tag tag-upset">Upset alert</span>'
    else:
        upset = ""
    volatile = (
        '<span class="tag tag-volatile">High variance</span>' if m.boom_bust > volatile_cut else ""
    )
    return f"""
<article class="card">
  <div class="card-tags">{upset}{volatile}</div>
  <div class="teams">
    <div class="team team-home {'lead' if m.home_win_probability >= 0.5 else ''}">
      <span class="team-name">{html.escape(m.home.team.name)}</span>
      <span class="team-score">{m.home.mean:.1f}</span>
      <span class="team-meta">&sigma; {m.home.sigma:.1f} · {html.escape(m.home.team.record)}</span>
    </div>
    <div class="vs">vs</div>
    <div class="team team-away {'lead' if m.home_win_probability < 0.5 else ''}">
      <span class="team-name">{html.escape(m.away.team.name)}</span>
      <span class="team-score">{m.away.mean:.1f}</span>
      <span class="team-meta">&sigma; {m.away.sigma:.1f} · {html.escape(m.away.team.record)}</span>
    </div>
  </div>
  <div class="winbar" role="img" aria-label="{html.escape(m.home.team.name)} {home_pct:.0f} percent, {html.escape(m.away.team.name)} {away_pct:.0f} percent">
    <div class="winbar-home" style="width:{home_pct:.1f}%"></div>
  </div>
  <div class="winbar-labels"><span>{home_pct:.0f}%</span><span>{away_pct:.0f}%</span></div>
  <dl class="stats">
    <div><dt>Spread</dt><dd>{html.escape(m.favorite.team.name)} &minus;{abs(m.spread):.1f}</dd></div>
    <div><dt>Total</dt><dd>{m.total:.1f}</dd></div>
    <div><dt>Boom-bust</dt><dd>{m.boom_bust:.1f}</dd></div>
  </dl>
</article>"""


def _render_league_notes(sim: LeagueSim) -> str:
    top = sim.highest_projected
    volatile = sim.most_volatile
    scorer = sim.top_scorer
    tiles = [
        ("Favorite of the week", html.escape(top.team.name), f"{top.mean:.1f} projected"),
        ("Volatility watch", html.escape(volatile.team.name), f"&sigma; {volatile.sigma:.1f}"),
    ]
    if scorer and scorer.projection:
        p = scorer.projection
        tiles.append(("Projected top scorer", html.escape(p.name), f"{p.consensus:.1f} pts · {html.escape(p.position)}"))
    closest = sim.closest_matchup
    if closest:
        tiles.append((
            "Closest call",
            f"{html.escape(closest.home.team.name)} / {html.escape(closest.away.team.name)}",
            f"{abs(closest.spread):.1f}-point spread",
        ))
    cells = "".join(
        f'<div class="tile"><span class="tile-label">{label}</span>'
        f'<span class="tile-value">{value}</span>'
        f'<span class="tile-sub">{sub}</span></div>'
        for label, value, sub in tiles
    )
    return f'<section><h2 class="section-title">League Power Notes</h2><div class="tiles">{cells}</div></section>'


def _render_source_table(status: dict[str, str]) -> str:
    if not status:
        return ""
    rows = "".join(
        f'<tr><td>{html.escape(source)}</td><td>{html.escape(text)}</td></tr>'
        for source, text in sorted(status.items())
    )
    return (
        '<section><h2 class="section-title">Source Report Card</h2>'
        f'<table class="data"><thead><tr><th>Source</th><th>Status</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></section>"
    )


def _render_player_table(projections: dict) -> str:
    top = sorted(projections.values(), key=lambda p: -p.consensus)[:50]
    if not top:
        return ""
    rows = []
    for p in top:
        vegas = f"{p.vegas_points:.1f}" if p.vegas_points is not None else "&mdash;"
        rows.append(
            f"<tr><td>{html.escape(p.name)}</td><td>{html.escape(p.position)}</td>"
            f"<td>{html.escape(p.team)}</td><td class='num'>{p.consensus:.1f}</td>"
            f"<td class='num'>{p.sigma:.1f}</td><td class='num'>{p.spread:.1f}</td>"
            f"<td class='num'>{vegas}</td><td class='num'>{p.source_count}</td></tr>"
        )
    return (
        '<section><h2 class="section-title">Top 50 Projections</h2>'
        '<p class="note">Consensus is weighted across every source that resolved this player, '
        'scored under the league\'s own rules. Spread is the gap between the most and least '
        'optimistic source — a big number there is exactly what the column means by "hate".</p>'
        '<div class="scroll"><table class="data"><thead><tr>'
        "<th>Player</th><th>Pos</th><th>Team</th><th>Proj</th><th>&sigma;</th>"
        "<th>Spread</th><th>Vegas</th><th>Src</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _render_archive(archive: dict) -> str:
    weeks = sorted((int(k) for k in archive), reverse=True)
    if len(weeks) <= 1:
        return ""
    links = "".join(
        f'<li><a href="week-{w}.html">Week {w}</a>'
        f'<span class="archive-meta">{html.escape(str(archive[str(w)].get("top_team", "")))}</span></li>'
        for w in weeks
    )
    return f'<section><h2 class="section-title">Archive</h2><ul class="archive">{links}</ul></section>'


def _page(title: str, heading: str, tagline: str, subtitle: str, body: str) -> str:
    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(tagline)}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="masthead">
  <div class="wrap">
    <h1>{html.escape(heading)}</h1>
    <p class="tagline">{html.escape(tagline)}</p>
    <p class="subtitle">{subtitle}</p>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="wrap">
  <p>Generated {generated} by the fantasy projection engine. Projections are aggregated from
  Sleeper, ESPN, FantasyPros, CBS and betting-market implied points, then simulated 10,000 times
  per matchup.</p>
  <p class="disclaimer">An affectionate homage, not affiliated with or endorsed by Matthew Berry,
  Yahoo, ESPN, CBS, FantasyPros or Sleeper.</p>
</footer>
</body>
</html>
"""


STYLESHEET = """\
:root {
  --bg: #f7f6f3;
  --surface: #ffffff;
  --border: #e2ded6;
  --text: #1c1a17;
  --muted: #6c665c;
  --accent: #b4472b;
  --accent-soft: #f2e3dd;
  --lead: #1f6f4a;
  --warn: #8a5a10;
  --radius: 12px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14130f;
    --surface: #1d1b17;
    --border: #322e27;
    --text: #f0ece4;
    --muted: #a49c8e;
    --accent: #e2724f;
    --accent-soft: #33221c;
    --lead: #6bc294;
    --warn: #d9a441;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 0 20px; }
.masthead {
  background: var(--surface);
  border-bottom: 3px solid var(--accent);
  padding: 40px 0 28px;
  margin-bottom: 36px;
}
.masthead h1 {
  margin: 0;
  font-size: clamp(28px, 5vw, 44px);
  letter-spacing: -0.02em;
  font-weight: 800;
}
.tagline { margin: 6px 0 0; color: var(--muted); font-style: italic; }
.subtitle {
  margin: 14px 0 0;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
  font-weight: 700;
}
.section-title {
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
  margin: 44px 0 20px;
}
.banner {
  background: var(--accent-soft);
  border-left: 4px solid var(--accent);
  padding: 14px 18px;
  border-radius: 0 var(--radius) var(--radius) 0;
  margin-bottom: 28px;
  font-size: 15px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
}
.card-tags { min-height: 22px; display: flex; gap: 6px; flex-wrap: wrap; }
.tag {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 3px 8px;
  border-radius: 999px;
}
.tag-upset { background: var(--accent-soft); color: var(--accent); }
.tag-volatile { background: transparent; color: var(--warn); border: 1px solid var(--warn); }
.tag-flip { background: transparent; color: var(--muted); border: 1px solid var(--border); }
/* Both sides share one grid so the two scores sit on the same row no matter
   how many lines a team name wraps to. `display: contents` lifts each team's
   spans into the shared grid; the rows below place them. */
.teams { display: grid; grid-template-columns: 1fr auto 1fr; column-gap: 10px; align-items: end; }
.team { display: contents; }
.team-home > * { grid-column: 1; }
.team-away > * { grid-column: 3; text-align: right; }
.team-name { grid-row: 1; font-weight: 700; font-size: 15px; align-self: end; }
.team-score { grid-row: 2; }
.team-meta { grid-row: 3; }
.team-score { font-size: 30px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.15; }
.team.lead .team-score { color: var(--lead); }
.team-meta { font-size: 12px; color: var(--muted); }
.vs { grid-column: 2; grid-row: 2; color: var(--muted); font-size: 12px; align-self: center; }
.winbar {
  height: 8px;
  background: var(--border);
  border-radius: 999px;
  overflow: hidden;
  margin-top: 14px;
}
.winbar-home { height: 100%; background: var(--accent); }
.winbar-labels {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}
.stats { display: flex; gap: 18px; margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--border); }
.stats div { display: flex; flex-direction: column; }
.stats dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.stats dd { margin: 0; font-weight: 700; font-size: 14px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
.tile {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.tile-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); }
.tile-value { font-size: 18px; font-weight: 700; line-height: 1.3; }
.tile-sub { font-size: 13px; color: var(--muted); }
.column {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 8px 28px 28px;
  margin-top: 44px;
}
.column h1 { font-size: 26px; margin-top: 24px; }
.column h2 { font-size: 20px; margin-top: 32px; }
.column h3 { font-size: 17px; margin-top: 30px; padding-top: 18px; border-top: 1px solid var(--border); }
.column h1 + h3, .column h3:first-of-type { border-top: none; padding-top: 0; }
.column blockquote {
  margin: 20px 0;
  padding: 2px 18px;
  border-left: 3px solid var(--accent);
  color: var(--muted);
}
.column hr { border: 0; border-top: 1px solid var(--border); margin: 32px 0; }
.column strong { color: var(--accent); }
.scroll { overflow-x: auto; }
table.data { width: 100%; border-collapse: collapse; font-size: 14px; }
table.data th {
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
  white-space: nowrap;
}
table.data td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
table.data tr:hover td { background: var(--accent-soft); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { font-size: 14px; color: var(--muted); margin-top: -6px; }
.archive { list-style: none; padding: 0; margin: 0; }
.archive li {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.archive a { color: var(--accent); font-weight: 600; text-decoration: none; }
.archive a:hover { text-decoration: underline; }
.archive-meta { color: var(--muted); font-size: 14px; }
footer {
  margin: 56px auto 40px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 13px;
}
.disclaimer { font-size: 12px; }
.empty { color: var(--muted); }
@media (max-width: 560px) {
  .teams { grid-template-columns: 1fr auto; }
  .team-away > * { grid-column: 2; }
  .vs { display: none; }
}
"""
