# fantasy_stats

A weekly fantasy football projection engine that aggregates player projections
from multiple providers (including betting-market implied points), simulates
every matchup in a Yahoo league, and publishes the results as a GitHub Pages
site with a hype-heavy weekly column.

## What it does

1. **Aggregates projections** from Sleeper, ESPN, FantasyPros, CBS, and Vegas
   player props. Every source that publishes raw stat lines is **re-scored under
   your league's own scoring rules** — a half-PPR league never silently inherits
   a site's full-PPR totals.
2. **Prices the betting markets.** Player props are de-vigged (American odds →
   implied probabilities, normalized to sum to 1) and turned into an expected
   stat line, giving a fifth projection sourced from people with money at risk.
3. **Simulates every matchup** 10,000 times, drawing each starter from
   `Normal(mean, sigma)` truncated at zero, with a positive correlation between
   starters who share an NFL team.
4. **Writes the column** — spreads, win probabilities, Love/Hate picks, upset
   alerts and a boom-bust index, wrapped in an affectionate homage to the
   Matthew Berry weekly-column voice.
5. **Publishes to GitHub Pages** as a static site in `docs/`.

## Quick start

```bash
pip install -r requirements.txt
python -m src.run --week 1
```

With no credentials configured this runs in **demo mode**: real projections,
real Vegas math, real simulation, but a synthetic ten-team league snake-drafted
from the week's projections. Output lands in `output/week_1.md` and `docs/`.

Open `docs/index.html` in a browser to preview the site locally.

## Importing a Yahoo league export

Yahoo can export the draft board and the settings page to a spreadsheet before
API access is approved. `tools/import_yahoo_export.py` turns that workbook into
`league.yaml`, transcribing every scoring rule exactly:

```bash
python tools/import_yahoo_export.py path/to/export.xlsx
python -m src.run --check-league
```

The workbook needs two sheets: **Draft and Waivers**
(`Round | Pick | Player (Team - Position) | Fantasy Team Name`) and **League
Settings** (`Setting | Value`, the settings page verbatim). Re-run it after each
re-export as waivers accumulate.

Two things the export does not carry, both of which the importer flags:

- **Drops.** Rosters are the union of drafted and added players, so a team that
  added without a recorded drop carries an extra bench player. Bench players
  never score, so this does not affect any projection.
- **The schedule.** Without it, teams are paired in draft order, which is not
  who actually plays whom. The importer writes a commented-out schedule block
  pre-filled with your real team names — uncomment it and fill in the pairings.
  Every other number is unaffected, and the report says the pairings are
  placeholders until you do.

Scoring is written with `preset: none`, meaning nothing scores unless it is
listed, so the overrides are the league's complete rule set rather than a diff
against an assumed default. That matters for leagues that score unusually —
field goals by total distance, say, rather than by distance bucket.

## No Yahoo API key yet? Keep the rosters yourself

Yahoo takes a week or two to approve API access. Until then, `league.yaml`
holds your league by hand and everything else works unchanged — real
projections, real Vegas math, real simulation, real matchups.

```bash
python -m src.run --init-league   # writes league.yaml (skip; one is committed)
# edit league.yaml: your teams, your rosters
python -m src.run --check-league  # confirms every name resolves
python -m src.run --week 1
```

You enter **player names only**. Position and NFL team are looked up from
Sleeper, so `Josh Allen` is enough, and a defense can be written any of the ways
you would naturally write it — `Denver Broncos`, `Broncos`, `Broncos D/ST`, or
`DEN`. Scoring comes from a preset (`ppr`, `half_ppr`, `standard`) plus any
overrides your league needs.

`--check-league` is the important one. It prints every roster, flags any name it
cannot match, and suggests the spelling it thinks you meant:

```
Team One: 10 players (1 K, 1 QB, 3 RB, 3 WR)
  UNMATCHED  Brok Bowerz  did you mean: Brock Bowers (TE, LV)?
  NO PLAYER for the TE slot
```

### Lineups

You enter each roster **once**. Every week the engine starts whichever legal
lineup projects highest, so the file does not need touching between weeks. That
is a genuine modelling difference from Yahoo, and the report says so: it means a
manager's actual lineup mistake will not show up. To model a real lineup, pin it
with a `starters:` list on that team.

### Switching to Yahoo later

Put your league key in `config.yaml` under `yahoo.league_id` and Yahoo takes
over automatically — `league.yaml` is ignored, not deleted. `league_source` in
`config.yaml` forces the issue either way (`auto`, `yahoo`, `manual`).

## Configuration

`config.yaml` holds everything non-secret — league id, source weights,
positional variance priors, simulation settings. Secrets are read from the
environment only; the file merely names the variables to read.

Put a real league id in `config.local.yaml` (gitignored) if you would rather not
commit it:

```yaml
yahoo:
  league_id: "461.l.123456"
```

### Credentials

| Variable | Needed for | Where to get it |
| --- | --- | --- |
| `YAHOO_CONSUMER_KEY` / `YAHOO_CONSUMER_SECRET` | Real rosters, lineups, matchups and league scoring | [developer.yahoo.com/apps](https://developer.yahoo.com/apps/) — create an app with Fantasy Sports **read** permission and redirect URI `oob` |
| `ODDS_API_KEY` | Vegas implied points | [the-odds-api.com](https://the-odds-api.com/) — free tier is 500 requests/month |
| `ANTHROPIC_API_KEY` | The `--llm` column | [console.anthropic.com](https://console.anthropic.com/) |
| `FANTASYPROS_API_KEY` | Full FantasyPros coverage (optional) | FantasyPros API access — without it the public page exposes only ~10 players per position |

### Yahoo one-time setup

```bash
export YAHOO_CONSUMER_KEY=...
export YAHOO_CONSUMER_SECRET=...
python -m src.run --auth          # opens the browser flow, writes oauth2.json
python -m src.run --list-leagues  # prints your league keys
```

Then put the league key in `config.yaml` (or `config.local.yaml`) and run
normally. `oauth2.json` is gitignored — it is a live credential.

## CLI

```
python -m src.run --week 3              # project week 3
python -m src.run                       # project the current week
python -m src.run --week 3 --refresh    # bust the cache and refetch
python -m src.run --week 3 --llm        # write the column with Claude
python -m src.run --sources sleeper,espn --no-site
python -m src.run --auth                # Yahoo OAuth setup
python -m src.run --list-leagues        # print your Yahoo league keys
python -m src.run --init-league         # scaffold league.yaml
python -m src.run --check-league        # validate league.yaml and every roster name
```

Every source fails soft: if one is down, the run proceeds with the rest and the
report's Source Report Card says what was missing.

## Publishing

The `Weekly projections` workflow runs every Tuesday at 13:00 UTC and can also
be triggered by hand from the Actions tab (with an optional week override). It
regenerates the site, commits `docs/` and `output/`, and deploys Pages.

To enable it:

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**, add the secrets you have:
   `YAHOO_CONSUMER_KEY`, `YAHOO_CONSUMER_SECRET`, `YAHOO_OAUTH_JSON` (the whole
   contents of your local `oauth2.json`), `ODDS_API_KEY`, `ANTHROPIC_API_KEY`,
   `FANTASYPROS_API_KEY`.
3. Run the workflow once by hand to confirm it publishes.

Any secret you omit degrades gracefully — no Yahoo means demo mode, no Odds API
means four sources instead of five.

## Layout

```
config.yaml            league id, weights, variance priors, sim settings
league.yaml            hand-maintained rosters, used when Yahoo is unavailable
tools/
  import_yahoo_export.py   build league.yaml from a Yahoo export workbook
src/
  sources/             one module per projection provider
    sleeper.py         free, no auth; the canonical player-id spine
    espn.py            public league-defaults endpoint
    fantasypros.py     ~100-analyst consensus (down-weighted for overlap)
    cbs.py             fourth independent source, defensively scraped
    vegas.py           The Odds API player props, de-vigged
  yahoo_league.py      scoring settings, rosters, lineups, matchups
  manual_league.py     the hand-maintained league file and lineup solver
  player_matching.py   cross-source id resolution
  aggregate.py         consensus projections + sigma
  simulate.py          Monte Carlo matchup sim
  report.py            the weekly column
  site.py              static site generator for GitHub Pages
  run.py               CLI
data/cache/            cached raw pulls, timestamped
output/week_{N}.md     the weekly report
docs/                  the published site
tests/                 scoring, de-vig, matching, aggregation, simulation
```

## Notes on the sources

- **Sleeper** publishes raw stat projections and is the canonical join key.
- **ESPN**'s raw stat ids were validated against ESPN's own point totals
  (median absolute difference of 0.003 across skill positions).
- **FantasyPros** truncates its public projection table to roughly ten players
  per position for anonymous visitors; set `FANTASYPROS_API_KEY` for full
  coverage. It is down-weighted by default because it already aggregates
  analysts who overlap the other sources.
- **CBS** serves season-long totals under the weekly URL until a week's
  projections publish. The scraper detects this via the games-played column and
  skips rather than feeding season totals into a weekly consensus.
- **Vegas** props are cached hard — the free tier is 500 requests/month.

## Tests

```bash
python -m pytest tests/ -q
```

Covers league scoring math, de-vig math, player matching, consensus/sigma
building, the simulation's statistical properties, and the league file (parsing,
validation, and a brute-force check that the lineup solver is optimal).

## Disclaimer

An affectionate homage, not affiliated with or endorsed by Matthew Berry, Yahoo,
ESPN, CBS, FantasyPros, or Sleeper. Projection data is scraped from public pages
for personal league use.
