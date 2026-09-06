"""The weekly hype report.

An affectionate homage to the Matthew Berry column voice — rambling cold open,
relentless self-deprecation, Love/Hate picks driven by the actual model output,
and a sincere closer. Template-driven with a randomized bank of constructions,
with an optional `--llm` path that hands the same data to Claude.
"""
from __future__ import annotations

import collections
import logging
import random
from dataclasses import dataclass, field

from .simulate import LeagueSim, MatchupSim, StarterProjection, TeamSim

log = logging.getLogger(__name__)

TITLE = "The Talented Mr. Roto-ish Weekly"

VOICE_GUIDELINES = """\
You are writing a weekly fantasy football preview column. The voice is an
affectionate homage to Matthew Berry's "Love/Hate" column. You are NOT Matthew
Berry and must never claim to be; the column is called "The Talented Mr.
Roto-ish Weekly".

Voice rules:
- Open cold with a rambling personal anecdote that seems totally unrelated (a
  DMV line, a middle-school dance, a bad haircut, an argument about a parking
  spot) and then land a strained-but-delightful segue into the week's biggest
  matchup.
- Self-deprecation is a feature. Constantly reference past wrong calls: "I know,
  I know — I'm the guy who told you to bench him last week. Nobody listens to me
  anyway."
- Use the "It's not that... it's just..." construction at least twice.
- Every matchup gets a Love pick and a Hate pick, and you must justify both with
  the actual numbers provided.
- Drop oddly specific stats from the data and flex about it: "He has the
  third-highest implied touchdown probability on the slate. Third! I counted."
- Pop-culture references that are 5-15 years out of date, on purpose.
- End with one genuinely warm, sincere line about the league, then sign off
  abruptly.

Hard rules: never invent a number. Every statistic must come from the data you
are given. Never claim a player is injured unless the data says so.
"""

ANECDOTES = [
    "So I'm at the DMV last Tuesday — number G-114, seat that has clearly been sat in by every citizen of this county — and the woman next to me is doing a crossword in pen. In pen! No hesitation. No eraser. Total conviction in a situation that does not reward conviction. And I thought: that's it. That's fantasy football.",
    "My middle school held a dance in the cafeteria where they never fully mopped up lunch, so we all slow-danced in a faint aroma of Salisbury steak. I asked exactly one person to dance. She said she was 'waiting for a different song.' Reader, the song never came. I bring this up because waiting for a different song is precisely what half of you are doing with your flex spot.",
    "I got a haircut on Thursday. I said 'just clean it up.' He heard 'give me the silhouette of a man who has recently lost a bet.' I have been wearing a hat since. The lesson, as always, is that small decisions compound violently, which brings us neatly to the top of the slate.",
    "There is a man in my neighborhood who mows his lawn diagonally. Not once — every week, diagonally, with the confidence of someone who has run the numbers. I've never spoken to him. I think about him constantly. He is, in my mind, the platonic ideal of the manager who starts a backup tight end and is somehow right.",
    "I spent forty minutes on hold with my internet provider, and the hold music was a saxophone cover of a song I loved in college, which felt like a targeted attack. By minute thirty-five I had accepted my fate. By minute forty I was rooting for the saxophone. That is the emotional arc of owning a running back in a committee backfield.",
    "My flight was delayed and the gate agent kept saying 'we should have an update in about twenty minutes,' and then, twenty minutes later, would say it again, with the same warmth, as though it were new information. I have never respected anyone more. That is also, I'm told, my entire projection model.",
    "I tried to assemble a piece of furniture without the instructions because I am a grown adult with a spatial imagination. Four hours later I had a bookshelf with a confident lean and three screws left over that I've decided were optional. Sometimes the process is wrong and the outcome is fine. Sometimes it's the other way around. Anyway, here's your week.",
    "Somebody at the grocery store had eleven items in the ten-items-or-fewer lane and made eye contact with me the entire time. Just held it. Didn't flinch. I said nothing, because I am a coward, and because on some level I admired the commitment. That man is starting a quarterback on bye this week and he will not apologize.",
    "I found a receipt in an old jacket from a restaurant that closed in 2019. Two entrees, one dessert, and a tip I'd describe as 'aspirational.' I have no memory of this meal. I have, however, retained perfect recall of every waiver claim I've ever lost. The brain is a magnificent and deeply unserious organ.",
    "My neighbor's kid set up a lemonade stand and charged four dollars a cup. No ice. Warm lemonade, four dollars, take it or leave it. Sold out in an hour. I've thought about that pricing strategy every day since, and I think about it especially when I look at what people paid at the waiver wire this week.",
]

SEGUES = [
    "Which brings us, somehow, to {matchup}.",
    "Anyway. {matchup}. Let's talk about it.",
    "And that, if you squint, is exactly the situation in {matchup}.",
    "I tell you all of that so I can tell you this: {matchup} is the game of the week.",
    "Point being: {matchup}, and I have feelings.",
]

class Deck:
    """Deals lines without replacement, so a bank of eight does not serve the
    same joke three times in one issue. Reshuffles only once exhausted."""

    def __init__(self, items, rng: random.Random) -> None:
        self._items = list(items)
        self.size = len(self._items)
        self._rng = rng
        self._pile: list = []

    def deal(self):
        if not self._pile:
            self._pile = list(self._items)
            self._rng.shuffle(self._pile)
        return self._pile.pop()


SPREAD_LINES = [
    "{favorite} is favored by {spread:.1f} with a {win:.0%} win probability, which sounds decisive until you notice the margin swings {boom:.1f} points either way.",
    "The model has {favorite} by {spread:.1f} and calls it {win:.0%} to win, though the honest version of that sentence includes a {boom:.1f}-point error bar.",
    "{spread:.1f} points separates these two on paper. Ten thousand simulations later, {favorite} takes it {win:.0%} of the time.",
    "{favorite} by {spread:.1f}, {win:.0%} to win, and a margin that has been as generous as +{p90:.0f} and as cruel as {p10:.0f} depending on which simulation you'd like to believe.",
    "Call it {favorite} by {spread:.1f}. The win probability says {win:.0%}. The boom-bust index says {boom:.1f} and the boom-bust index has never once apologized.",
    "{favorite} by {spread:.1f}. I ran this ten thousand times and {underdog} won {lose:.0%} of them, which is either comforting or terrifying depending on which side of it you're sitting.",
    "The spread here is {spread:.1f} in favour of {favorite}, a number I would describe as confident rather than convincing.",
    "{favorite} {win:.0%}, {underdog} {lose:.0%}. Two teams separated by {spread:.1f} points and roughly one running back's Sunday afternoon.",
]

COIN_FLIP_LINES = [
    "This is a coin flip wearing a spread. {favorite} is nominally favored by {spread:.1f}, which is within the rounding error of my own competence.",
    "{favorite} by {spread:.1f} is not a prediction, it's a shrug with a decimal point. Genuinely too close to call.",
    "The model gives {favorite} a {win:.0%} edge here, which is the statistical equivalent of 'sure, why not.'",
    "{spread:.1f} points. That's the whole edge. That's one screen pass and a favourable spot of the ball.",
    "I have {favorite} at {win:.0%} and I want you to understand how little that means. Flip a coin, then feel bad about it either way.",
]

SELF_DEPRECATION = [
    "I know, I know — I'm the guy who told you to bench him last week. Nobody listens to me anyway.",
    "Last week I called this exact kind of game 'a lock.' It was not a lock. Locks have tumblers. This had vibes.",
    "You should know my track record here is roughly that of a coin that has been dropped several times.",
    "I was wrong about this player in August, September, and — let me check my notes — yes, also right now, probably.",
    "My model and I have an arrangement: it does the math, I take the blame.",
    "Every year I say I've learned. Every year the tape says otherwise.",
    "I want to be clear that I have been paid exactly nothing for these opinions, and my subscribers would tell you I have been overpaid.",
    "Take this with the seriousness it deserves, which is to say roughly the amount you'd give a horoscope in an airline magazine.",
    "I have been doing this long enough to know better and short enough to keep doing it anyway.",
    "There is a version of this column where I'm right about everything. It is not this one. It has never been this one.",
]

NOT_THAT = [
    "It's not that I don't believe in {team}. It's just that {problem}.",
    "It's not that {team} is bad. It's just that {problem}, and math is a cruel and literal science.",
    "It's not that I'm rooting against {team}. It's just that {problem} and I have eyes.",
    "It's not that {team} can't win this. It's just that {problem}, which is a bold way to enter a Sunday.",
]

POP_CULTURE = [
    "This is the fantasy equivalent of insisting the last season of Lost 'made sense if you think about it.'",
    "This lineup has the structural integrity of a Vine compilation.",
    "It's giving 'checking Foursquare to see who's the mayor of a Chipotle.'",
    "This is a Google+ invite of a roster and I mean that with love.",
    "Somewhere between 'Gangnam Style' and 'planking' on the confidence spectrum.",
    "This is the roster construction equivalent of buying a selfie stick in 2014. Bold then. Bold now.",
    "I haven't been this unsure about an outcome since the Snyder Cut discourse.",
    "This roster is the fantasy football answer to a Blockbuster late fee: technically survivable, emotionally not.",
    "It has the swagger of a man still using a BlackBerry in 2013 and the results to match.",
    "This is 'nobody tell him the Harlem Shake is over' energy.",
    "The whole thing feels like an Angry Birds sequel — familiar, confident, and about four years past the moment.",
    "I rate this lineup somewhere between a Fitbit and a Segway on the scale of ideas that seemed great at the time.",
]

CLOSERS = [
    "Look — the projections are just numbers, and the numbers are wrong more than I'd like. But twelve people showing up every Sunday to argue about a backup tight end is a genuinely good way to stay in each other's lives. Don't take that for granted. Okay bye.",
    "One sincere thing before I go: some of you have been in this league longer than you've lived in your current city. That's not nothing. That's a decade of group chat. Go win. Or don't. See you next week.",
    "Truthfully? The best part of this whole thing isn't the trophy, it's that someone texts you at 1 p.m. on a Sunday for no reason at all. Cherish the stupid league. Alright, I'm out.",
    "Real talk for one sentence: the standings will be forgotten and the group chat will not. Play the games, talk the trash, be kind on Tuesday. Okay, that's enough of that. Goodnight.",
    "I'll say the earnest part quickly so we can both pretend it didn't happen: this league is one of the good ones. Now go start somebody you shouldn't. Bye.",
    "Last sincere thing and then I'll stop: a league that survives a decade of bad drafts and worse trades is a rare thing. Yours has. Go be insufferable about a win. Bye.",
    "Genuinely, for one line: the projections are a hobby, the group chat is the point. Take care of each other and absolutely none of your kickers. See you next week.",
]

@dataclass
class LoveHate:
    love: StarterProjection | None = None
    love_reason: str = ""
    hate: StarterProjection | None = None
    hate_reason: str = ""


@dataclass
class MatchupNarrative:
    sim: MatchupSim
    picks: LoveHate
    riff: str = ""
    standings_line: str = ""


@dataclass
class ReportContext:
    week: int
    season: int
    league_name: str
    sim: LeagueSim
    narratives: list[MatchupNarrative]
    source_status: dict[str, str] = field(default_factory=dict)
    league_notes: list[str] = field(default_factory=list)
    is_demo: bool = False
    schedule_known: bool = True
    cold_open: str = ""
    closer: str = ""
    slate_td_leaders: list[tuple[str, float]] = field(default_factory=list)


# Love/Hate is about starting decisions, so it is judged among the positions
# managers actually agonize over. A broken lineup slot is always eligible.
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}


def _positional_baseline(starters: list[StarterProjection]) -> dict[str, float]:
    """Median projection per position among this matchup's starters.

    Without this, "love" is just "whichever quarterback is playing", because
    quarterbacks out-project every other position by construction.
    """
    pools: dict[str, list[float]] = {}
    for s in starters:
        if s.projection:
            pools.setdefault(s.projection.position, []).append(s.projection.consensus)
    baseline = {}
    for position, values in pools.items():
        values.sort()
        middle = len(values) // 2
        baseline[position] = (
            values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
        )
    return baseline


def pick_love_hate(matchup: MatchupSim, rng: random.Random, decks: dict | None = None) -> LoveHate:
    """Love = beats his position's bar with the sources (and market) agreeing.
    Hate  = wide provider disagreement, ugly props, or a broken lineup slot."""
    all_starters = matchup.home.starters + matchup.away.starters
    starters = [s for s in all_starters if s.projection]
    if not starters:
        return LoveHate()

    baseline = _positional_baseline(starters)

    def love_score(s: StarterProjection) -> float:
        p = s.projection
        bar = baseline.get(p.position, p.consensus) or 1.0
        # How far above his position's bar he projects, not how many raw points.
        edge = p.consensus / bar
        score = edge * (0.6 + 0.4 * p.agreement)
        if p.vegas_delta is not None and p.vegas_delta > 0:
            score *= 1.15  # the market likes him more than the projections do
        if p.source_count >= 4:
            score *= 1.05
        return score

    def hate_score(s: StarterProjection) -> float:
        p = s.projection
        if s.problem:
            return 1000.0  # an empty or injured starting slot beats any disagreement
        if p.position not in SKILL_POSITIONS or p.consensus <= 0:
            return 0.0
        # Absolute point disagreement, weighted toward players who matter. A
        # ratio would always pick a defense, whose small projections make every
        # gap look enormous.
        score = p.spread * (0.5 + 0.5 * min(p.consensus / 15.0, 1.5))
        if p.vegas_delta is not None and p.vegas_delta < 0:
            score += abs(p.vegas_delta)
        return score

    love_pool = [s for s in starters if s.projection.position in SKILL_POSITIONS] or starters
    love = max(love_pool, key=love_score)

    hate_pool = [s for s in all_starters if s is not love and (s.problem or s.projection)]
    hate = max(hate_pool, key=hate_score) if hate_pool else None
    if hate and not hate.problem and hate.projection and hate_score(hate) <= 0:
        hate = None

    return LoveHate(
        love=love,
        love_reason=_love_reason(love, decks),
        hate=hate,
        hate_reason=_hate_reason(hate, decks) if hate else "",
    )


LOVE_FRAMES = [
    "{points:.1f} projected{agreement}{market}",
    "the sources have him at {points:.1f}{agreement}{market}",
    "{points:.1f} points{market}{agreement}",
    "projects for {points:.1f}{agreement}{market}",
]

HATE_FRAMES = [
    "{reason}",
    "and it is not personal, it is arithmetic: {reason}",
    "{reason}, which is more uncertainty than a starting spot should carry",
    "{reason}. Start him anyway and I will pretend I said nothing",
]


def _love_reason(s: StarterProjection, decks: dict | None = None) -> str:
    """Say why the model likes him, without claiming agreement that isn't there."""
    p = s.projection
    tight = p.source_count > 1 and p.spread <= max(2.0, p.consensus * 0.15)
    if p.source_count <= 1:
        agreement = ""
    elif tight:
        agreement = f", with all {p.source_count} sources inside {p.spread:.1f} points of each other"
    else:
        agreement = f", the best {p.position} in this matchup across {p.source_count} sources"

    market = ""
    if p.vegas_delta is not None:
        direction = "above" if p.vegas_delta >= 0 else "below"
        market = f", and Vegas has him {abs(p.vegas_delta):.1f} {direction} that"

    frame = decks["love"].deal() if decks else LOVE_FRAMES[0]
    return frame.format(points=p.consensus, agreement=agreement, market=market)


def _hate_reason(s: StarterProjection, decks: dict | None = None) -> str:
    p = s.projection
    if s.problem:
        return s.problem

    bits = []
    if p.spread > 0:
        bits.append(f"{p.spread:.1f} points between his highest and lowest projection")
    if p.vegas_delta is not None and p.vegas_delta < 0:
        bits.append(f"props {abs(p.vegas_delta):.1f} under the consensus")
    if not bits:
        bits.append(f"a {p.sigma:.1f}-point standard deviation on a {p.consensus:.1f}-point projection")
    reason = " and ".join(bits)

    frame = decks["hate"].deal() if decks else HATE_FRAMES[0]
    return frame.format(reason=reason)


def build_context(
    sim: LeagueSim,
    week: int,
    season: int,
    league_name: str,
    source_status: dict[str, str],
    league_notes: list[str],
    is_demo: bool,
    seed: int | None = None,
    schedule_known: bool = True,
) -> ReportContext:
    rng = random.Random(seed if seed is not None else week * 7919)
    slate = build_slate(sim)
    decks = {
        "spread": Deck(SPREAD_LINES, rng),
        "coin": Deck(COIN_FLIP_LINES, rng),
        "self": Deck(SELF_DEPRECATION, rng),
        "pop": Deck(POP_CULTURE, rng),
        "not_that": Deck(NOT_THAT, rng),
        "love": Deck(LOVE_FRAMES, rng),
        "hate": Deck(HATE_FRAMES, rng),
        "standings": Deck(STANDINGS_LINES, rng),
    }

    # Without the real schedule every matchup number — spread, win probability,
    # the Love/Hate picks that are chosen within a matchup — describes a game
    # nobody is playing. The column runs on the rankings instead.
    narratives = []
    if schedule_known:
        for matchup in sim.matchups:
            picks = pick_love_hate(matchup, rng, decks)
            narratives.append(
                MatchupNarrative(
                    sim=matchup,
                    picks=picks,
                    riff=_riff(matchup, picks, slate, decks, rng),
                    standings_line=_standings_line(matchup, decks),
                )
            )

    if schedule_known and sim.matchups:
        headline = min(sim.matchups, key=lambda m: (abs(m.spread), -m.total))
        matchup_label = f"{headline.home.team.name} vs. {headline.away.team.name}"
    else:
        matchup_label = f"{sim.highest_projected.team.name} sitting on top of the rankings"

    return ReportContext(
        week=week,
        season=season,
        league_name=league_name,
        sim=sim,
        narratives=narratives,
        source_status=source_status,
        league_notes=league_notes,
        is_demo=is_demo,
        schedule_known=schedule_known,
        cold_open=rng.choice(ANECDOTES) + "\n\n" + rng.choice(SEGUES).format(matchup=matchup_label),
        closer=rng.choice(CLOSERS),
        slate_td_leaders=_td_leaders(sim),
    )


def _td_leaders(sim: LeagueSim) -> list[tuple[str, float]]:
    """Highest implied touchdown probabilities among rostered starters."""
    out = []
    for team in sim.teams:
        for starter in team.starters:
            p = starter.projection
            if p and p.vegas_points is not None:
                out.append((p.name, p.vegas_points))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:5]


def _pick(decks: dict, key: str, options: list[str], rng: random.Random) -> str:
    """Choose among a beat's variants without repeating one within an issue.

    Deals an *index*, never the string. A beat's options are rebuilt with this
    matchup's own numbers on every call, so a deck of the strings themselves
    would hand the first matchup's sentences — names, teams and all — to every
    later matchup.
    """
    deck = decks.get(key)
    if deck is None or deck.size != len(options):
        deck = decks[key] = Deck(range(len(options)), rng)
    return options[deck.deal()]


def _subject(starter: StarterProjection) -> str:
    """How to name a starter in prose. A team defence is a plural thing that
    takes an article: "the Vikings", not "Vikings"."""
    projection = starter.projection
    if projection and projection.position == "DEF":
        return f"the {projection.name.split()[-1]}"
    return starter.name


def _pronoun(starter: StarterProjection) -> str:
    projection = starter.projection
    return "them" if projection and projection.position == "DEF" else "him"


ORDINALS = {1: "highest", 2: "second-highest", 3: "third-highest", 4: "fourth-highest", 5: "fifth-highest"}


@dataclass
class Slate:
    """League-wide facts a riff can cite, so the specifics differ every time.

    The spec asks for oddly specific stat-drops ("third-highest implied TD
    probability on the slate. Third! I counted."). Those only work if they are
    true, so they are computed once across every starter in the league.
    """

    position_rank: dict[str, int] = field(default_factory=dict)
    position_total: dict[str, int] = field(default_factory=dict)
    vegas_rank: dict[str, int] = field(default_factory=dict)
    spread_rank: dict[str, int] = field(default_factory=dict)


def build_slate(sim: LeagueSim) -> Slate:
    starters = [s for team in sim.teams for s in team.starters if s.projection]
    slate = Slate()

    by_position: dict[str, list] = {}
    for starter in starters:
        by_position.setdefault(starter.projection.position, []).append(starter)
    for position, group in by_position.items():
        group.sort(key=lambda s: -s.projection.consensus)
        slate.position_total[position] = len(group)
        for index, starter in enumerate(group, start=1):
            slate.position_rank[starter.projection.player_id] = index

    priced = [s for s in starters if s.projection.vegas_points is not None]
    priced.sort(key=lambda s: -s.projection.vegas_points)
    for index, starter in enumerate(priced, start=1):
        slate.vegas_rank[starter.projection.player_id] = index

    spread_sorted = sorted(starters, key=lambda s: -s.projection.spread)
    for index, starter in enumerate(spread_sorted, start=1):
        slate.spread_rank[starter.projection.player_id] = index
    return slate


# --------------------------------------------------------------------------
# Beats. Each returns a sentence or None when this matchup gives it nothing to
# say, so the riff is assembled from whatever is actually true rather than
# marching through the same five slots every time.
# --------------------------------------------------------------------------


def _beat_stack(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    """Two starters on the same NFL team boom and bust together."""
    for team in (m.favorite, m.underdog):
        groups: dict[str, list] = {}
        for starter in team.starters:
            if starter.projection and starter.slot.team:
                groups.setdefault(starter.slot.team, []).append(starter)
        stacks = [(nfl, g) for nfl, g in groups.items() if len(g) >= 2]
        if not stacks:
            continue
        nfl, group = max(stacks, key=lambda item: sum(s.mean for s in item[1]))
        names = " and ".join(s.name for s in sorted(group, key=lambda s: -s.mean)[:2])
        points = sum(s.mean for s in group)
        return _pick(decks, "stack", [
            f"{team.team.name} is running {names} out of the same {nfl} offence, which is "
            f"{points:.1f} points that all rise or fall on one game script. That is not a lineup, "
            f"that is a parlay.",
            f"Worth noting that {team.team.name} has {names} stacked on {nfl}. When it works it "
            f"works twice; when it doesn't, neither do they.",
            f"{names} both play for {nfl}, so {team.team.name} has quietly bet {points:.1f} points "
            f"on a single coaching staff having a good afternoon.",
        ], rng)
    return None


def _beat_slate_rank(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    """A starter who ranks near the top of the whole league at his position."""
    best = None
    for starter in m.home.starters + m.away.starters:
        if not starter.projection:
            continue
        rank = slate.position_rank.get(starter.projection.player_id)
        if rank and rank <= 3 and (best is None or rank < best[0]):
            best = (rank, starter)
    if best is None:
        return None
    rank, starter = best
    who = _subject(starter)
    position = starter.projection.position
    total = slate.position_total.get(position, 0)
    word = ORDINALS.get(rank, f"{rank}th-highest")
    return _pick(decks, "slate_rank", [
        f"{who} is the {word}-projected {position} of the {total} starting in this "
        f"league this week. {rank}! I counted.",
        f"Somebody should mention that {who} grades out as the {word} {position} on the "
        f"entire slate. Out of {total}. I checked twice because I didn't believe it either.",
        f"{who}: {word} {position} among all {total} starters this week. I have a "
        f"spreadsheet and everything.",
        f"Of every {position} starting anywhere in this league, {who} is the {word}. "
        f"That is {total} players and he is at the front of them.",
        f"Among the league's {total} starting {position}s, {who} comes in at number {rank}. "
        f"I did not expect to be typing that sentence this week either.",
        f"{who} comes in {word} of {total} at the position. I mention it because nobody "
        f"else is going to.",
    ], rng)


def _beat_vegas_gap(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    """Where the betting market and the projection sites disagree most."""
    candidates = [
        s for s in m.home.starters + m.away.starters
        if s.projection and s.projection.vegas_delta is not None and abs(s.projection.vegas_delta) >= 2.0
    ]
    if not candidates:
        return None
    starter = max(candidates, key=lambda s: abs(s.projection.vegas_delta))
    who = _subject(starter)
    them = _pronoun(starter)
    delta = starter.projection.vegas_delta
    if delta > 0:
        return _pick(decks, "vegas_up", [
            f"Vegas likes {who} {delta:.1f} points more than the projection sites do, and "
            f"Vegas is the only one of them with money at risk.",
            f"The betting markets have {who} {delta:.1f} points clear of the consensus. "
            f"Somebody is wrong and it is historically not the sportsbooks.",
            f"{who} is priced {delta:.1f} points above where the projections have {them}. "
            f"When the books and the spreadsheets disagree I know which one pays rent.",
            f"There is {delta:.1f} points of daylight between what Vegas thinks of {who} "
            f"and what the sites do, and only one of those opinions settles up on Monday.",
        ], rng)
    return _pick(decks, "vegas_down", [
        f"The props have {who} {abs(delta):.1f} points below where the sites have him, "
        f"which is the market politely declining to participate.",
        f"Vegas is {abs(delta):.1f} points under the consensus on {who}. Books rarely "
        f"leave money on a table by accident.",
        f"Every projection site is {abs(delta):.1f} points more excited about {who} than "
        f"the people taking bets on {them} are. Draw your own conclusion; mine is unprintable.",
        f"{who}'s props come in {abs(delta):.1f} under his consensus. The market has "
        f"seen something the spreadsheets haven't, or it is wrong. It usually isn't.",
        f"Somebody should ask why {who} is priced {abs(delta):.1f} points below what the "
        f"sites expect. I have a guess and it is not a fun one.",
    ], rng)


def _beat_disagreement(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    """The starter the sources cannot agree on at all."""
    candidates = [s for s in m.home.starters + m.away.starters if s.projection and s.projection.spread >= 4.0]
    if not candidates:
        return None
    starter = max(candidates, key=lambda s: s.projection.spread)
    who = _subject(starter)
    p = starter.projection
    rank = slate.spread_rank.get(p.player_id)
    tail = f" — the {ORDINALS.get(rank, str(rank) + 'th-highest')} disagreement on the slate" if rank and rank <= 3 else ""
    return _pick(decks, "disagreement", [
        f"The sources cannot agree on {who}: {p.spread:.1f} points between the most and "
        f"least optimistic of them{tail}. Somebody is going to look silly and it might be me.",
        f"{who} has a {p.spread:.1f}-point gap between his highest and lowest projection"
        f"{tail}. Four sources, four opinions, one very confused football player.",
        f"Nobody can place {who}. The projections are {p.spread:.1f} points apart{tail}, "
        f"which is less a forecast than a group argument.",
        f"There is a {p.spread:.1f}-point disagreement about {who}{tail}. Pick a source, "
        f"pick a feeling, live with it.",
        f"{who} is the week's Rorschach test: {p.spread:.1f} points between the most and "
        f"least hopeful projection{tail}.",
    ], rng)


def _beat_top_heavy(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    """A team leaning on one or two players."""
    for team in (m.favorite, m.underdog):
        real = sorted([s for s in team.starters if s.projection], key=lambda s: -s.mean)
        if len(real) < 3 or team.mean <= 0:
            continue
        share = (real[0].mean + real[1].mean) / team.mean
        if share >= 0.36:
            return _pick(decks, "top_heavy", [
                f"{real[0].name} and {real[1].name} are {share:.0%} of {team.team.name}'s "
                f"projection between them. That is not depth, that is a two-man rope bridge.",
                f"{share:.0%} of {team.team.name}'s points are projected to come from two players. "
                f"I admire the conviction and fear the bye weeks.",
                f"{team.team.name} is asking {real[0].name} and {real[1].name} to carry {share:.0%} "
                f"of the load. That is a lot of eggs and a conspicuously small basket.",
                f"Take {real[0].name} and {real[1].name} off {team.team.name} and you remove "
                f"{share:.0%} of the projection. Nobody plan a Sunday around the other six.",
                f"{share:.0%} of {team.team.name}'s week rests on two players, which is either "
                f"decisive roster-building or a single hamstring away from a rebuild.",
            ], rng)
    return None


def _beat_margin(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    return _pick(decks, "margin", [
        f"The middle eighty per cent of outcomes here runs from {m.favorite_margin_p10:+.0f} to "
        f"{m.favorite_margin_p90:+.0f} for {m.favorite.team.name}, which is a polite way of saying "
        f"nobody knows anything.",
        f"Simulated ten thousand times, this lands anywhere between {m.favorite_margin_p10:+.0f} and "
        f"{m.favorite_margin_p90:+.0f}. Bring a helmet.",
    ], rng)


def _beat_problem(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    problems = m.favorite.problems + m.underdog.problems
    if not problems:
        return None
    broken = problems[0]
    team = m.favorite if broken in m.favorite.problems else m.underdog
    return decks["not_that"].deal().format(team=team.team.name, problem=f"{broken.name} is {broken.problem}")


def _beat_self(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    return decks["self"].deal()


def _beat_pop(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    return decks["pop"].deal()


def _beat_upset(m: MatchupSim, slate: Slate, decks: dict, rng: random.Random) -> str | None:
    if not m.is_upset_alert or m.is_coin_flip:
        return None
    return _pick(decks, "upset", [
        f"Upset alert: {m.underdog.team.name} wins this {m.underdog_win_probability:.0%} of the "
        f"time, which is far too often for anyone to feel comfortable.",
        f"{m.underdog.team.name} takes this {m.underdog_win_probability:.0%} of the time. That is "
        f"not a prediction, it is a warning.",
    ], rng)


# Ordered by how much each depends on this particular matchup's data — the
# data-driven beats are preferred, and the generic banks fill in behind them.
BEATS = [
    _beat_problem,
    _beat_slate_rank,
    _beat_vegas_gap,
    _beat_stack,
    _beat_disagreement,
    _beat_top_heavy,
    _beat_upset,
    _beat_margin,
    _beat_self,
    _beat_pop,
]


def _riff(matchup: MatchupSim, picks: LoveHate, slate: Slate, decks: dict, rng: random.Random) -> str:
    """Compose the paragraph from beats that are actually true of this matchup.

    The opening frames the spread; after that the beats are drawn from whatever
    the data supports and shuffled, so neither the wording nor the shape of the
    paragraph repeats down the page. The Love pick is deliberately not restated
    here — its own line follows immediately below.
    """
    bank = decks["coin"] if matchup.is_coin_flip else decks["spread"]
    opening = bank.deal().format(
        favorite=matchup.favorite.team.name,
        underdog=matchup.underdog.team.name,
        spread=abs(matchup.spread),
        win=matchup.favorite_win_probability,
        lose=matchup.underdog_win_probability,
        boom=matchup.boom_bust,
        p90=matchup.favorite_margin_p90,
        p10=matchup.favorite_margin_p10,
    )

    usage = decks.setdefault("_usage", collections.Counter())
    candidates = []
    for index, beat in enumerate(BEATS):
        line = beat(matchup, slate, decks, rng)
        if line:
            candidates.append((beat.__name__, index, line))

    # Favour angles this issue has not leaned on yet, then the more
    # data-specific beats, then chance. Without this, the two or three beats
    # that fire for every matchup would carry every paragraph on the page.
    candidates.sort(key=lambda c: (usage[c[0]], c[1], rng.random()))
    chosen = candidates[: rng.choice([2, 3, 3, 4])]
    for name, _, _ in chosen:
        usage[name] += 1

    lines = [line for _, _, line in chosen]
    rng.shuffle(lines)
    return " ".join([opening, *lines])


# Two different outcomes are being described, so each needs its own record:
# `fav_*` is the chalk result, `dog_*` is the upset. Sharing one pair produced
# lines like "0-0 team at 1-1", which is not a thing that can happen.
STANDINGS_LINES = [
    "chalk sends {favorite} to {fav_w}-{fav_l} and drops {underdog} to {dog_lost_w}-{dog_lost_l}; the upset flips both and makes the middle of this table completely unreadable.",
    "win and {favorite} sit at {fav_w}-{fav_l}. Lose and {underdog} climb to {dog_won_w}-{dog_won_l} and this whole standings page needs rereading.",
    "{favorite} at {fav_w}-{fav_l} is the tidy outcome. {underdog} at {dog_won_w}-{dog_won_l} is the one that ruins somebody's tiebreaker in December.",
    "the expected result puts {favorite} on {fav_w}-{fav_l}; the other one puts {underdog} on {dog_won_w}-{dog_won_l} and me back at the drawing board.",
    "{favorite} to {fav_w}-{fav_l}, or {underdog} to {dog_won_w}-{dog_won_l} and a group chat that never lets it go.",
    "{favorite} are {fav_w}-{fav_l} if this goes to form and {fav_lost_w}-{fav_lost_l} if it doesn't, and I have been wrong about which often enough to hedge.",
]


def _standings_line(matchup: MatchupSim, decks: dict | None = None) -> str:
    favorite, underdog = matchup.favorite, matchup.underdog
    frame = decks["standings"].deal() if decks else STANDINGS_LINES[0]
    return "Standings implication: " + frame.format(
        favorite=favorite.team.name,
        underdog=underdog.team.name,
        fav_w=favorite.team.wins + 1, fav_l=favorite.team.losses,
        fav_lost_w=favorite.team.wins, fav_lost_l=favorite.team.losses + 1,
        dog_won_w=underdog.team.wins + 1, dog_won_l=underdog.team.losses,
        dog_lost_w=underdog.team.wins, dog_lost_l=underdog.team.losses + 1,
    )


def render_markdown(ctx: ReportContext) -> str:
    out: list[str] = []
    add = out.append

    add(f"# {TITLE}")
    add(f"### Week {ctx.week}, {ctx.season} — {ctx.league_name}")
    add("")
    if ctx.is_demo:
        add("> **Demo mode.** Yahoo credentials are not configured, so this league is "
            "snake-drafted from the week's projections. The projections, Vegas math and "
            "simulation are real; the teams are not.")
        add("")

    add(ctx.cold_open)
    add("")
    add("---")
    add("")
    if ctx.schedule_known:
        add("## The Slate")
        add("")
        for narrative in ctx.narratives:
            add(_render_matchup(narrative))
    else:
        add("## The Slate")
        add("")
        add("There isn't one yet — the league export doesn't carry the matchup schedule, "
            "and I'm not going to preview games nobody is playing. It's not that I don't "
            "have opinions. It's just that they'd be about the wrong sixteen teams. "
            "Add the pairings and the spreads come right back.")
        add("")

    add("---")
    add("")
    add("## Power Rankings")
    add("")
    add("| # | Team | Proj | Floor | Ceiling | σ |")
    add("| --- | --- | --- | --- | --- | --- |")
    for rank, team in enumerate(sorted(ctx.sim.teams, key=lambda t: -t.mean), start=1):
        add(f"| {rank} | {team.team.name} ({team.team.record}) | {team.mean:.1f} | "
            f"{team.floor:.0f} | {team.ceiling:.0f} | {team.sigma:.1f} |")
    add("")
    add("*Floor and ceiling are the 10th and 90th percentiles of 10,000 simulations. "
        "The ranges overlap almost completely, which is the honest summary of how much "
        "this order is worth in any single week.*")
    add("")
    add("---")
    add("")
    add("## League Power Notes")
    add("")
    sim = ctx.sim
    add(f"- **Favorite of the week:** {sim.highest_projected.team.name}, "
        f"{sim.highest_projected.mean:.1f} projected points.")
    if sim.top_scorer and sim.top_scorer.projection:
        p = sim.top_scorer.projection
        add(f"- **Projected top scorer:** {p.name} ({p.position}, {p.team}) at {p.consensus:.1f}.")
    add(f"- **Volatility watch:** {sim.most_volatile.team.name} carries a "
        f"{sim.most_volatile.sigma:.1f}-point standard deviation. The boom-bust index does not "
        f"care about your feelings.")

    upsets = [n for n in ctx.narratives if n.sim.is_upset_alert] if ctx.schedule_known else []
    if upsets:
        add(f"- **Upset alerts ({len(upsets)}):** " + ", ".join(
            f"{n.sim.underdog.team.name} ({n.sim.underdog_win_probability:.0%})" for n in upsets
        ) + ".")
    if ctx.slate_td_leaders:
        leader = ctx.slate_td_leaders[0]
        add(f"- **Market darling:** {leader[0]} leads all rostered starters in Vegas-implied "
            f"points at {leader[1]:.1f}. I counted.")
    add("")

    if ctx.source_status:
        add("## Source Report Card")
        add("")
        add("| Source | Status |")
        add("| --- | --- |")
        for source, status in sorted(ctx.source_status.items()):
            add(f"| {source} | {status} |")
        add("")

    for note in ctx.league_notes:
        add(f"> {note}")
    if ctx.league_notes:
        add("")

    add("---")
    add("")
    add(ctx.closer)
    add("")
    return "\n".join(out)


def _render_matchup(n: MatchupNarrative) -> str:
    m = n.sim
    lines = [
        f"### {m.away.team.name} at {m.home.team.name}",
        "",
        f"**{m.favorite.team.name} by {abs(m.spread):.1f}** · "
        f"{m.away.team.name} {m.away.mean:.1f} ({1 - m.home_win_probability:.0%}) "
        f"at {m.home.team.name} {m.home.mean:.1f} ({m.home_win_probability:.0%}) · "
        f"boom-bust index {m.boom_bust:.1f}",
        "",
        n.riff,
        "",
    ]
    if n.picks.love:
        lines.append(f"**LOVE:** {n.picks.love.name} ({n.picks.love.slot.position}, "
                     f"{n.picks.love.slot.team}) — {n.picks.love_reason}.")
        lines.append("")
    if n.picks.hate:
        lines.append(f"**HATE:** {n.picks.hate.name} ({n.picks.hate.slot.position}, "
                     f"{n.picks.hate.slot.team}) — {n.picks.hate_reason}.")
        lines.append("")
    lines.append(f"*{n.standings_line}*")
    lines.append("")
    return "\n".join(lines)


def build_llm_payload(ctx: ReportContext) -> dict:
    """The week's model output, reduced to what the prose generator needs."""
    return {
        "week": ctx.week,
        "season": ctx.season,
        "league": ctx.league_name,
        "is_demo": ctx.is_demo,
        "schedule_known": ctx.schedule_known,
        "note_for_the_writer": (
            "The matchup schedule is unknown this week. Do not invent, imply or preview "
            "any head-to-head game; write from the power rankings instead."
            if not ctx.schedule_known
            else ""
        ),
        "power_rankings": [
            {
                "rank": rank,
                "team": t.team.name,
                "projected": round(t.mean, 1),
                "floor": t.floor,
                "ceiling": t.ceiling,
            }
            for rank, t in enumerate(sorted(ctx.sim.teams, key=lambda x: -x.mean), start=1)
        ],
        "matchups": [
            {
                "home": n.sim.home.team.name,
                "away": n.sim.away.team.name,
                "home_projected": round(n.sim.home.mean, 1),
                "away_projected": round(n.sim.away.mean, 1),
                "favorite": n.sim.favorite.team.name,
                "spread": round(abs(n.sim.spread), 1),
                "favorite_win_probability": round(n.sim.favorite_win_probability, 3),
                "boom_bust_index": n.sim.boom_bust,
                "upset_alert": n.sim.is_upset_alert,
                "love": _player_payload(n.picks.love, n.picks.love_reason),
                "hate": _player_payload(n.picks.hate, n.picks.hate_reason),
                "lineup_problems": [
                    {"team": t.team.name, "player": s.name, "issue": s.problem}
                    for t in (n.sim.home, n.sim.away)
                    for s in t.problems
                ],
                "standings_implication": n.standings_line,
            }
            for n in ctx.narratives
        ],
        "league_notes": {
            "highest_projected_team": ctx.sim.highest_projected.team.name,
            "highest_projected_points": round(ctx.sim.highest_projected.mean, 1),
            "most_volatile_team": ctx.sim.most_volatile.team.name,
            "most_volatile_sigma": round(ctx.sim.most_volatile.sigma, 1),
            "projected_top_scorer": (
                ctx.sim.top_scorer.projection.name if ctx.sim.top_scorer and ctx.sim.top_scorer.projection else None
            ),
            "vegas_implied_leaders": ctx.slate_td_leaders,
        },
        "source_status": ctx.source_status,
    }


def _player_payload(starter: StarterProjection | None, reason: str) -> dict | None:
    if starter is None or starter.projection is None:
        return None
    p = starter.projection
    return {
        "name": p.name,
        "position": p.position,
        "nfl_team": p.team,
        "consensus": p.consensus,
        "sigma": p.sigma,
        "source_spread": p.spread,
        "sources": p.by_source,
        "vegas_implied": p.vegas_points,
        "vegas_delta": p.vegas_delta,
        "reason": reason,
    }


def render_with_llm(ctx: ReportContext, config) -> str | None:
    """Hand the week's data to Claude with the voice guidelines. None on failure."""
    api_key = config.secret("anthropic.key_env")
    if not api_key:
        log.warning("--llm requested but no Anthropic API key is set; using the template report")
        return None
    try:
        import json  # noqa: PLC0415

        import anthropic  # noqa: PLC0415
    except ImportError:
        log.warning("--llm requested but the `anthropic` package is not installed")
        return None

    payload = build_llm_payload(ctx)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=config.get_path("anthropic.model", "claude-opus-5"),
            max_tokens=int(config.get_path("anthropic.max_tokens", 4000)),
            system=VOICE_GUIDELINES,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Write the Week {ctx.week} column as Markdown. Start with an H1 titled "
                        f"'{TITLE}' and an H3 with the week, season and league name. Give every "
                        "matchup its own H3 section with the spread, win probability, a LOVE pick, "
                        "a HATE pick, 3-5 sentences of riffing, and a standings-implications line. "
                        "Finish with a 'League Power Notes' section and the sincere closer.\n\n"
                        "Here is this week's model output. Every number you use must come from it:\n\n"
                        + json.dumps(payload, indent=2)
                    ),
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - never let the column block the run
        log.warning("LLM report generation failed (%s); using the template report", exc)
        return None

    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return text.strip() or None
