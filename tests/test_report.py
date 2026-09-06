"""The column's prose engine: variety, and not leaking one matchup into another."""
from __future__ import annotations

import random
import re

import numpy as np
import pytest

from src.aggregate import PlayerProjection
from src.report import (
    POP_CULTURE,
    STANDINGS_LINES,
    Deck,
    _pick,
    _pronoun,
    _subject,
    build_context,
    render_markdown,
)
from src.simulate import LeagueSim, MatchupSim, StarterProjection, TeamSim
from src.yahoo_league import RosterSlot, Team


def test_deck_deals_without_replacement():
    deck = Deck(list("abcdef"), random.Random(1))
    dealt = [deck.deal() for _ in range(6)]
    assert sorted(dealt) == list("abcdef")


def test_deck_reshuffles_only_once_exhausted():
    deck = Deck(["a", "b"], random.Random(1))
    assert sorted(deck.deal() for _ in range(2)) == ["a", "b"]
    assert sorted(deck.deal() for _ in range(2)) == ["a", "b"]


def test_pick_deals_an_index_not_a_cached_string():
    """The options are rebuilt with each matchup's own numbers, so caching the
    strings would replay the first matchup's text — names and all — everywhere."""
    decks: dict = {}
    rng = random.Random(7)
    first = _pick(decks, "beat", ["Alpha wins", "Alpha loses"], rng)
    second = _pick(decks, "beat", ["Bravo wins", "Bravo loses"], rng)

    assert first.startswith("Alpha")
    assert second.startswith("Bravo"), "a later matchup was served an earlier one's sentence"


def test_pick_does_not_repeat_a_variant_until_the_bank_is_spent():
    decks: dict = {}
    rng = random.Random(3)
    seen = [_pick(decks, "beat", ["one", "two", "three"], rng) for _ in range(3)]
    assert sorted(seen) == ["one", "three", "two"]


def test_subject_gives_a_defence_an_article():
    defence = StarterProjection(
        slot=RosterSlot(slot="DEF", name="Minnesota Vikings", position="DEF", team="MIN"),
        projection=PlayerProjection("MIN", "Minnesota Vikings", "DEF", "MIN", consensus=7.0),
    )
    assert _subject(defence) == "the Vikings"
    assert _pronoun(defence) == "them"


def test_subject_leaves_a_person_alone():
    player = StarterProjection(
        slot=RosterSlot(slot="RB", name="Bijan Robinson", position="RB", team="ATL"),
        projection=PlayerProjection("1", "Bijan Robinson", "RB", "ATL", consensus=17.0),
    )
    assert _subject(player) == "Bijan Robinson"
    assert _pronoun(player) == "him"


# --------------------------------------------------------------------------
# Whole-column behaviour
# --------------------------------------------------------------------------


def make_team(name, players, seed):
    rng = np.random.default_rng(seed)
    starters = [
        StarterProjection(
            slot=RosterSlot(slot=pos, name=n, position=pos, team=nfl),
            projection=PlayerProjection(n, n, pos, nfl, consensus=pts, sigma=pts * 0.4,
                                        by_source={"a": pts, "b": pts + 3}, spread=3.0),
        )
        for n, pos, nfl, pts in players
    ]
    scores = rng.normal(sum(p[3] for p in players), 15, 4000)
    return TeamSim(Team(name, name), starters, float(scores.mean()), float(scores.std()), scores)


def make_sim(pairs=6):
    teams, matchups = [], []
    for i in range(pairs * 2):
        teams.append(make_team(
            f"Team {i}",
            [(f"QB{i}", "QB", "SF", 20.0), (f"RB{i}", "RB", "SF", 15.0),
             (f"WR{i}", "WR", "DAL", 13.0), (f"TE{i}", "TE", "KC", 9.0)],
            seed=i + 1,
        ))
    for i in range(0, len(teams), 2):
        home, away = teams[i], teams[i + 1]
        margin = home.scores - away.scores
        matchups.append(MatchupSim(
            home=home, away=away,
            home_win_probability=float((margin > 0).mean()),
            spread=round(home.mean - away.mean, 2), total=round(home.mean + away.mean, 2),
            boom_bust=round(float(margin.std()), 2),
            margin_p10=round(float(np.percentile(margin, 10)), 2),
            margin_p90=round(float(np.percentile(margin, 90)), 2),
        ))
    return LeagueSim(week=1, matchups=matchups, teams=teams)


def column(week=1):
    sim = make_sim()
    ctx = build_context(sim, week, 2026, "Test League", {}, [], is_demo=False)
    return render_markdown(ctx), ctx


def test_a_riff_never_names_a_team_from_another_matchup():
    """The regression this file exists for: cached, pre-formatted lines leaked
    one matchup's teams and players into every later matchup's paragraph."""
    md, ctx = column()
    # Stop at the rankings table, which legitimately lists every team.
    slate = md[md.index("## The Slate"):md.index("## Power Rankings")]
    blocks = re.split(r"\n### ", slate)[1:]
    all_teams = {t.team.name for t in ctx.sim.teams}
    assert len(blocks) == len(ctx.narratives) > 1

    for block in blocks:
        header, body = block.split("\n", 1)
        own = {t.strip() for t in header.split(" at ")}
        for other in all_teams - own:
            # Whole-name match: "Team 1" is a substring of "Team 10".
            assert not re.search(rf"{re.escape(other)}\b", body), f"{header!r} mentions {other!r}"


def test_riffs_vary_in_length():
    md, _ = column()
    riffs = [l for l in md.split("\n") if l.startswith(("The model", "Call it", "This is a coin"))]
    lengths = {len(re.split(r"(?<=[.!?]) ", r)) for r in riffs}
    assert len(lengths) > 1, "every paragraph is the same shape"


def test_the_standings_line_is_not_identical_everywhere():
    md, _ = column()
    lines = [l for l in md.split("\n") if l.startswith("*Standings implication")]
    assert len(set(lines)) > 1


def test_the_love_pick_is_not_restated_inside_the_riff():
    """Its own line follows immediately; saying it twice was pure padding."""
    _, ctx = column()
    for narrative in ctx.narratives:
        assert "The model's favorite thing on the board" not in narrative.riff


def test_different_weeks_produce_different_prose():
    first, _ = column(week=1)
    second, _ = column(week=2)
    assert first != second


def test_every_matchup_gets_a_love_and_a_hate():
    _, ctx = column()
    for narrative in ctx.narratives:
        assert narrative.picks.love is not None
        assert narrative.picks.love_reason


def test_standings_lines_describe_records_that_can_actually_happen():
    """A 0-0 underdog winning goes to 1-0, never 1-1: the chalk and upset
    outcomes need separate records, not one shared pair."""
    from src.report import STANDINGS_LINES, _standings_line

    _, ctx = column()
    for narrative in ctx.narratives:
        fav, dog = narrative.sim.favorite.team, narrative.sim.underdog.team
        played = fav.wins + fav.losses
        for record in re.findall(r"\b(\d+)-(\d+)\b", narrative.standings_line):
            assert int(record[0]) + int(record[1]) == played + 1, (
                f"{narrative.standings_line!r} implies a team played "
                f"{sum(map(int, record))} games after week {played + 1}"
            )


def test_every_standings_template_renders():
    """All six frames must supply every placeholder they reference."""
    from src.report import STANDINGS_LINES, Deck, _standings_line

    _, ctx = column()
    matchup = ctx.narratives[0].sim
    for index in range(len(STANDINGS_LINES)):
        decks = {"standings": Deck([STANDINGS_LINES[index]], random.Random(0))}
        assert _standings_line(matchup, decks).startswith("Standings implication:")
