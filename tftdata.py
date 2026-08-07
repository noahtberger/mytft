"""Shared loading and naming helpers for the tracker.

Both stats.py and ask.py need the same things: read the saved matches, turn
Riot's internal IDs into readable names, and compare a player against the
other seven people in their lobby. That lives here so there's one copy.
"""

import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

MATCH_DIR = Path("matches")   # YOUR games. Written only by fetch_matches.py.
POOL_DIR = Path("pool")       # everyone else's, downloaded by serve.py/scout.py


def pool_dirs():
    """Every folder holding match files, your own first.

    Two folders, one logical pool. `matches/` stays exactly your history -
    it's the copy worth committing, since Riot deletes old games and nobody
    else will keep them for you. `pool/` is everything fetched for other
    players, which is disposable and re-downloadable.

    Readers take the union rather than duplicating files, so the shared pool
    already contains your games without a second copy of them existing. Your
    own folder comes first so it wins any dedupe: the same match downloaded
    for two different people is one file, not two.
    """
    return tuple(d for d in (MATCH_DIR, POOL_DIR) if d.exists())


def match_files():
    """Every match file across the pool, deduped by match id."""
    seen = set()
    for folder in pool_dirs():
        for path in folder.glob("*.json"):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            yield path
PLACEMENT_SD = 2.29  # spread of placements 1-8; used for margin-of-error maths
MIN_ACTIONABLE = 30  # games before an average is worth acting on (see README)


def load_names():
    """Load the ID -> display-name lookup built by fetch_names.py."""
    try:
        return json.loads(Path("names.json").read_text())
    except FileNotFoundError:
        print("names.json not found - run 'python fetch_names.py' for real names.\n")
        return {}


NAMES = load_names()


def display(api_id, kind):
    """'TFT17_DRX' -> 'N.O.V.A.'   kind is 'traits' or 'units'.

    Falls back to stripping the set prefix if the ID isn't in the lookup, so a
    brand-new set still prints something readable instead of crashing.
    """
    name = NAMES.get(kind, {}).get(api_id)
    if name:
        return name
    return api_id.split("_", 1)[1] if "_" in api_id else api_id


def variant_of(api_id):
    """Detect IDs like TFT17_Stargazer_Wolf that are a flavour of a base trait.

    Returns ('Stargazer', 'The Boar'), or (None, None) if it isn't a variant.
    The readable variant name comes from names.json - never use the ID suffix
    as a label, it disagrees with the game (_Wolf is "The Boar").
    """
    base, _, suffix = api_id.rpartition("_")
    traits = NAMES.get("traits", {})
    if base in traits and traits[base] == traits.get(api_id):
        label = NAMES.get("variants", {}).get(api_id, f"{suffix} (raw id)")
        return traits[base], label
    return None, None


def unique_traits(set_number):
    """Display names of traits belonging to exactly one champion, for one set."""
    return set(NAMES.get("unique_traits_by_set", {}).get(str(set_number), []))


def set_traits(set_number):
    """Every comp direction a set offers, as display names.

    Read off the names table rather than off your own games, so a line you
    have never touched still shows up - that absence is the whole point of a
    coverage report. Trait IDs are prefixed by set ("TFT17_Vanguard"), which
    also filters out the odd non-comp entries Riot files elsewhere
    ("Set17_CarouselMarket_..." is a hex effect, not a line you can build).

    Unique traits are excluded: they belong to one champion, so fielding one
    is a unit choice, not a direction.
    """
    prefix = f"TFT{set_number}_"
    names = {name for api_id, name in NAMES.get("traits", {}).items()
             if api_id.startswith(prefix)}
    return names - unique_traits(set_number)


def committed(trait):
    """Did this board actually BUILD the line, or just splash two units?

    Past the splash stage means the second breakpoint, or 4+ units for traits
    with only one breakpoint to hit. This matters more than it sounds: across
    this player's games, 103 of 117 Vanguard boards were 2-unit splash, so
    counting "trait was active" as "played the line" measures frontline
    affordability, not comp direction.
    """
    return trait["tier_current"] >= 2 or (
        trait["tier_current"] > 0 and trait.get("num_units", 0) >= 4)


def committed_lines(participant, set_number):
    """Display names of the comp directions this board truly committed to."""
    return {display(t["name"], "traits") for t in participant["traits"]
            if committed(t)} - unique_traits(set_number)


def splashed_lines(participant, set_number):
    """Traits that were online but never built past the splash stage."""
    return {display(t["name"], "traits") for t in participant["traits"]
            if t["tier_current"] > 0 and not committed(t)} - unique_traits(set_number)


def with_without(selected, placements):
    """Split placements by whether a game used the thing, and judge the gap.

    This is the comparison that matters: a line's own average means little
    against your overall average, because your overall average already
    contains those games - it is partly the line compared against itself.
    Returns None when either side is empty and there is nothing to compare.

    The gap is signed so positive always means "better with it": placement is
    a golf score, so it is (without - with).
    """
    yes = [p for p, s in zip(placements, selected) if s]
    no = [p for p, s in zip(placements, selected) if not s]
    if not yes or not no:
        return None
    with_it, without = summarise(yes), summarise(no)
    gap = without["avg"] - with_it["avg"]
    return {
        "with": with_it,
        "without": without,
        "gap": gap,
        "solid": abs(gap) > gap_moe(with_it["games"], without["games"]),
    }


def coverage_data(games, set_number):
    """Everything the coverage report and the website both need.

    Kept here rather than in either one so the page and the terminal can
    never drift into disagreeing about the same numbers - the thing that
    makes two views of one dataset worse than either alone.

    Sorts break ties on the name throughout: these tables are built by
    iterating sets of strings, whose order changes per process, so without a
    tiebreak equal rows swap places between runs on identical data.
    """
    my_built = [committed_lines(g, set_number) for g in games]
    placements = [g["placement"] for g in games]

    built_games = defaultdict(list)
    splashed, opp_built = Counter(), Counter()
    opp_boards = 0

    for g, mine in zip(games, my_built):
        for line in mine:
            built_games[line].append(g["placement"])
        for line in splashed_lines(g, set_number) - mine:
            splashed[line] += 1
        for p in g["_lobby"]:
            if p["puuid"] == g["puuid"]:
                continue
            opp_boards += 1
            for line in committed_lines(p, set_number):
                opp_built[line] += 1

    # The roster comes from names.json so untouched lines still appear, but a
    # mid-set patch can add a trait the local names file predates. Union in
    # everything observed so nothing is silently dropped from coverage.
    roster = set_traits(set_number) | set(built_games) | set(opp_built) | set(splashed)

    # Freeze to a plain dict BEFORE anything reads it by roster key. Indexing a
    # defaultdict inserts the key, so `built_games[line]` for a line you never
    # built would quietly add an empty entry - and then "spread across N
    # different lines" counts the whole set roster instead of the lines you
    # actually built. (Counter is safe here: its __missing__ returns 0 without
    # inserting, which is why splashed/opp_built can be indexed freely.)
    built = dict(built_games)

    lobby_rate = {l: 100 * opp_built[l] / opp_boards if opp_boards else 0.0
                  for l in roster}
    my_rate = {l: 100 * len(built.get(l, ())) / len(games) for l in roster}

    by_carry = defaultdict(list)
    for g in games:
        carry = carry_of(g)
        if carry:
            by_carry[carry].append(g["placement"])

    return {
        "games": len(games),
        "set": set_number,
        "placements": placements,
        "my_built": my_built,
        "built_games": built,
        "splashed": splashed,
        "opp_boards": opp_boards,
        "lobby_rate": lobby_rate,
        "my_rate": my_rate,
        "roster": roster,
        "by_carry": dict(by_carry),
        "ready": sorted((l for l in roster
                         if len(built.get(l, ())) >= MIN_ACTIONABLE),
                        key=lambda l: (statistics.mean(built[l]), l)),
        "building": sorted((l for l in roster
                            if 0 < len(built.get(l, ())) < MIN_ACTIONABLE),
                           key=lambda l: (-len(built[l]), l)),
        "blind": sorted((l for l in roster if not built.get(l)),
                        key=lambda l: (-lobby_rate[l], l)),
    }


def gap_moe(n1, n2):
    """95% margin of error on a DIFFERENCE of two average placements.

    Wider than either side's own margin: subtracting two noisy numbers adds
    their variances. The sqrt(1/n1 + 1/n2) is why a split needs more games
    than either column would need alone.

    Placement-only, because it assumes a known spread (PLACEMENT_SD): finishes
    are always 1-8, so the sd is a constant you don't have to measure. For any
    other metric use mean_gap, which measures the spread it actually sees.
    """
    return 1.96 * PLACEMENT_SD * (1 / n1 + 1 / n2) ** 0.5


def mean_gap(sample_a, sample_b):
    """Difference between two means, with the error bar that judges it.

    The general form of gap_moe: it takes the spread from the samples instead
    of assuming placement's fixed 1-8 range, so it works on unit costs, board
    sizes, traits built - anything. Returns None when either side is too small
    to have a variance worth using.
    """
    if len(sample_a) < 2 or len(sample_b) < 2:
        return None
    mean_a, mean_b = statistics.mean(sample_a), statistics.mean(sample_b)
    se = (statistics.pvariance(sample_a) / len(sample_a)
          + statistics.pvariance(sample_b) / len(sample_b)) ** 0.5
    moe = 1.96 * se
    gap = mean_a - mean_b
    return {"a": mean_a, "b": mean_b, "gap": gap, "moe": moe,
            "real": abs(gap) > moe}


# Riot's `rarity` is a code, not a price: 0/1/2/4/6 are the 1/2/3/4/5-gold
# shop units. Anything else (9) is a summon or a PvE monster - Elder Dragon
# and Summon are not units anybody bought, and counting them inflates both
# board size and average cost.
UNIT_COST = {0: 1, 1: 2, 2: 3, 4: 4, 6: 5}


def shop_units(board):
    """The units on this board that were actually bought from the shop."""
    return [u for u in board["units"] if u["rarity"] in UNIT_COST]


def playstyle(board, set_number):
    """How someone played, described without naming anything set-specific.

    Deliberately contains no trait or champion names. Every one of those
    changes when a new set launches, and comp data resets to zero on day one -
    but "expensive board", "rerolled a cheap unit to 3-star", "committed to a
    vertical instead of splashing" mean exactly the same thing in set 18 as in
    set 17. That is what lets a player's profile survive the rollover, and
    it's the thing the big comp sites don't tell you about yourself.

    Returns None for a board with no shop units at all (a disconnect).
    """
    units = shop_units(board)
    if not units:
        return None
    items = [len(u.get("itemNames") or []) for u in units]
    carried = sum(items)
    return {
        "avg_cost": statistics.mean(UNIT_COST[u["rarity"]] for u in units),
        "big_units": sum(1 for u in units if UNIT_COST[u["rarity"]] >= 4),
        "rerolled": sum(1 for u in units
                        if UNIT_COST[u["rarity"]] <= 3 and u["tier"] >= 3),
        "board_size": len(units),
        "lines_built": len(committed_lines(board, set_number)),
        # How much of your item pool sat on one unit: 1.0 is everything on a
        # single carry, low means you spread items across the board.
        "item_focus": max(items) / carried if carried else 0.0,
        "last_round": board["last_round"],
    }


def lobby_profiles(games, set_number):
    """(yours, the room's) playstyle profiles, oldest game first.

    One pass over every board in every lobby, split into the loaded player's
    boards and everyone else's. Chronological because biggest_shift scans for
    where a style changed, and that question only makes sense oldest-first.

    Boards with no shop units (a disconnect) are dropped, the same way
    playstyle() reports them: they describe a network problem, not a player.
    """
    me = games[0]["puuid"]
    mine, room = [], []
    for g in reversed(games):
        for board in g["_lobby"]:
            prof = playstyle(board, set_number)
            if prof is None:
                continue
            (mine if board["puuid"] == me else room).append(prof)
    return mine, room


# Display order and labels for a playstyle profile, plus which direction reads
# as "more" of that trait, so a report can phrase the comparison in English.
MIN_ERA = 20        # games either side before a style change is worth naming
SHIFT_THRESHOLD = 3.0   # standard errors; below this it's ordinary wobble


def biggest_shift(profiles, min_era=MIN_ERA):
    """Find where someone's playstyle changed, scanning oldest-to-newest.

    `profiles` is a list of playstyle() dicts in chronological order. For every
    possible split point it measures each metric on both sides and keeps the
    largest standardised difference, then reports that split.

    Why this exists: a lifetime average blends every version of a player. This
    player rerolled for the first 75 games of set 17 and went fast-8 after,
    which averages out to a description of nobody. Worse, playstyle is
    *supposed* to change - a new set is exactly when it should - so a tracker
    that reports one number per set will be wrong every September.

    Returns None if nothing crosses SHIFT_THRESHOLD. Scanning every split and
    keeping the maximum will always find *something*, which is why the
    threshold is deliberately high: this reports landslides, not tremors.
    """
    if len(profiles) < 2 * min_era:
        return None

    best = None
    for cut in range(min_era, len(profiles) - min_era + 1):
        before, after = profiles[:cut], profiles[cut:]
        for key, label, more, less in PLAYSTYLE_LABELS:
            judged = mean_gap([p[key] for p in after], [p[key] for p in before])
            if not judged or judged["moe"] == 0:
                continue
            # moe is 1.96 standard errors; recover the plain z-score.
            z = abs(judged["gap"]) / (judged["moe"] / 1.96)
            if best is None or z > best["z"]:
                best = {"z": z, "cut": cut, "key": key, "label": label,
                        "before": judged["b"], "after": judged["a"],
                        "gap": judged["gap"],
                        "direction": more if judged["gap"] > 0 else less}
    return best if best and best["z"] >= SHIFT_THRESHOLD else None


def playstyle_sentence(verdicts):
    """Plain English for a playstyle profile, or None if nothing stands out.

    `verdicts` maps a playstyle key to a mean_gap result. Every clause fires
    only on a gap that cleared its own error bar, so the sentence can never
    claim a tendency the table didn't measure.

    Lives here rather than in the report because the terminal and the website
    both say this out loud, and two hand-written versions of the same sentence
    drift - the site's first draft read "you are more expensive, more, less
    rerolling", which is column headings pretending to be a sentence.
    """
    def leans(key):
        v = verdicts.get(key)
        return 0 if not v or not v["real"] else (1 if v["gap"] > 0 else -1)

    clauses = []
    if leans("avg_cost") > 0 or leans("big_units") > 0:
        clauses.append("you buy an expensive board")
    elif leans("avg_cost") < 0 or leans("rerolled") > 0:
        clauses.append("you play cheap units and reroll them")

    if leans("rerolled") < 0:
        clauses.append("you almost never reroll for 3-stars")
    if leans("lines_built") < 0:
        clauses.append("you splash traits rather than commit to one")
    elif leans("lines_built") > 0:
        clauses.append("you commit hard to verticals")
    if leans("board_size") > 0:
        clauses.append("you go wide")
    elif leans("board_size") < 0:
        clauses.append("you stay small")
    if leans("item_focus") > 0:
        clauses.append("you funnel items onto one carry")
    elif leans("item_focus") < 0:
        clauses.append("you spread items across the board")
    if leans("last_round") > 0:
        clauses.append("you survive longer than the room")
    elif leans("last_round") < 0:
        clauses.append("you die earlier than the room")

    if not clauses:
        return None
    if len(clauses) == 1:
        joined = clauses[0]
    elif len(clauses) == 2:
        joined = f"{clauses[0]} and {clauses[1]}"
    else:
        joined = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"
    # capitalize() would lower-case the rest, so raise the first char by hand.
    return joined[0].upper() + joined[1:] + "."


PLAYSTYLE_LABELS = [
    ("avg_cost",    "average unit cost",      "more expensive", "cheaper"),
    ("big_units",   "4 and 5 cost units",     "more",           "fewer"),
    ("rerolled",    "3-starred cheap units",  "more rerolling", "less rerolling"),
    ("board_size",  "units on final board",   "wider",          "smaller"),
    ("lines_built", "traits actually built",  "more committed", "more splashing"),
    ("item_focus",  "items on one carry",     "more funnelled", "more spread"),
    ("last_round",  "round eliminated",       "later",          "earlier"),
]


def default_puuid():
    """Whoever fetch_matches.py last downloaded for - the owner of this copy.

    Split out from load_games so "which player" is a question the caller can
    answer, instead of a fact baked into the loader. This one line was the
    only thing tying every report to a single person.
    """
    try:
        return json.loads(Path("me.json").read_text())["puuid"]
    except FileNotFoundError:
        sys.exit("me.json not found - run fetch_matches.py first, or pass a "
                 "puuid to load_games().")


def load_games(puuid=None, set_number=None):
    """Read one player's saved matches for one TFT set.

    Returns (games, level_par) where games is that player's participant record
    per match (newest first) and level_par maps an elimination round to the
    average level everyone reached by then. That average is the yardstick for
    "were they ahead or behind" - it comes from all 8 players in every lobby,
    so it self-adjusts for rank and for patch, per player.

    puuid=None means the owner of this copy (me.json), which keeps every
    existing `load_games()` call working unchanged. Pass a puuid to report on
    anyone else whose games are on disk - every match file holds all 8 boards,
    so a lobbymate's history is often already here.

    A report only makes sense within one set: trait names collide across sets
    and econ pacing changes, so levels from different sets must never share a
    yardstick. set_number=None means "the newest set on disk" - whatever is
    being played right now, so a new set needs no code changes. To look back
    at an older set once a new one is out:  TFT_SET=17 python ask.py
    """
    puuid = puuid or default_puuid()
    games_by_set = defaultdict(list)
    levels_by_set = defaultdict(lambda: defaultdict(list))

    for path in match_files():
        match = json.loads(path.read_text())
        info = match["info"]
        game_set = info.get("tft_set_number") or 0

        # A match this player wasn't in is not an error - most files on disk
        # are somebody else's once you track more than one person. Skip it
        # rather than crashing on a StopIteration, and skip it BEFORE feeding
        # level_par, so another player's lobbies can't shift the yardstick
        # this player's numbers are measured against.
        mine = next((p for p in info["participants"] if p["puuid"] == puuid), None)
        if mine is None:
            continue

        for p in info["participants"]:
            levels_by_set[game_set][p["last_round"]].append(p["level"])

        mine["_datetime"] = info["game_datetime"]
        mine["_match_id"] = match["metadata"]["match_id"]
        mine["_set"] = game_set
        # game_version looks like "Version 16.12.706.1234 <Releases/16.12>";
        # we want just the "16.12" major.minor patch number.
        found = re.search(r"(\d+\.\d+)", info.get("game_version") or "")
        mine["_patch"] = found.group(1) if found else "?"
        mine["_lobby"] = info["participants"]
        games_by_set[game_set].append(mine)

    if not games_by_set:
        sys.exit(f"No saved matches for {puuid[:8]}... in "
                 f"{'/ or '.join(str(d) for d in pool_dirs())}/ - "
                 "run fetch_matches.py for that player first.")

    wanted = set_number or os.environ.get("TFT_SET") or max(games_by_set)
    try:
        chosen = int(wanted)
    except (TypeError, ValueError):
        sys.exit(f"TFT_SET must be a set number, not {wanted!r} "
                 f"(sets on disk: {sorted(games_by_set)}).")
    if chosen not in games_by_set:
        sys.exit(f"No set-{chosen} games in {MATCH_DIR}/ "
                 f"(sets on disk: {sorted(games_by_set)}).")

    my_games = sorted(games_by_set[chosen],
                      key=lambda g: g["_datetime"], reverse=True)
    level_par = {rnd: statistics.mean(v)
                 for rnd, v in levels_by_set[chosen].items()}
    return my_games, level_par


def summarise(placements):
    """The three numbers that matter, plus a margin of error."""
    n = len(placements)
    return {
        "games": n,
        "avg": statistics.mean(placements),
        "top4": 100 * sum(1 for p in placements if p <= 4) / n,
        "wins": sum(1 for p in placements if p == 1),
        "moe": 1.96 * PLACEMENT_SD / n ** 0.5,
    }


def level_delta(game, level_par):
    """How far above/below the lobby's typical level this player finished.

    Comparing within the same elimination round matters: everyone is level 9
    by round 37, so a raw level average would just measure how long you lived.
    """
    return game["level"] - level_par[game["last_round"]]


def active_traits(game):
    """Display names of traits that were actually online (tier > 0)."""
    return {display(t["name"], "traits") for t in game["traits"] if t["tier_current"] > 0}


def board_units(game):
    """Display names of the units on the final board."""
    return {display(u["character_id"], "units") for u in game["units"]}


def carry_of(game):
    """The most-itemised unit, which is almost always the intended carry."""
    best = max(game["units"], key=lambda u: len(u.get("itemNames") or []), default=None)
    if best is None or len(best.get("itemNames") or []) < 2:
        return None
    return display(best["character_id"], "units")
