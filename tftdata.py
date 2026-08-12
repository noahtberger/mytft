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


def trait_ids_for_set(set_number):
    """Every trait id belonging to one set, from names.json.

    Prefer the list fetch_names.py records, and fall back to matching a
    "TFT<n>_" prefix for a names.json written before that existed.

    The recorded list matters: Set 18 turned up on PBE with ids like
    "DA_18_Blossom" instead of "TFT18_Blossom", so a prefix guess would
    have found nothing and reported a set containing no comps at all.
    """
    recorded = NAMES.get("trait_ids_by_set", {}).get(str(set_number))
    if recorded:
        return set(recorded)
    prefix = f"TFT{set_number}_"
    return {api_id for api_id in NAMES.get("traits", {})
            if api_id.startswith(prefix)}


def names_missing(set_number):
    """True when names.json has never heard of this set.

    The day a new set launches, every id in a match file changes prefix and
    the local names table predates all of them. Nothing crashes - display()
    falls back to the raw id suffix - but two things go quietly wrong, and
    both look like bugs rather than stale data:

      - Names become internal ones. "Space Groove" prints as "SpaceGroove",
        and traits nobody should ever see, like "RhaastUniqueTrait" and
        "FlexTrait", appear as if they were comps.
      - set_traits() returns nothing, so the roster shrinks to whatever has
        been observed. Blind spots - comps the lobby builds and you never
        have - silently stop existing, because the report no longer knows
        what the set contains.

    The cure is one command, `python fetch_names.py`, so the reports say so
    rather than letting someone puzzle over it.
    """
    return not trait_ids_for_set(set_number)


STALE_NAMES_WARNING = (
    "This set's names aren't in names.json yet, so comps are showing "
    "Riot's internal ids and the list of comps that exist is incomplete. "
    "Run:  python fetch_names.py")


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
    known = NAMES.get("traits", {})
    names = {known[api_id] for api_id in trait_ids_for_set(set_number)
             if api_id in known}
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


def stage(last_round):
    """Round number -> the stage notation players actually use.

    Stage 1 is four rounds, every stage after is seven. Checked against the
    data: no eliminations land on x-4 or x-7 (carousel and monster rounds),
    which is exactly where this mapping says they can't.
    """
    if last_round <= 4:
        return f"1-{last_round}"
    r = last_round - 4
    return f"{2 + (r - 1) // 7}-{(r - 1) % 7 + 1}"


def stage_avg(value):
    """An AVERAGE round shown as a stage - approximate by nature, marked so."""
    return f"~{stage(int(round(value)))}"


# How much to trust a number, by sample size. The old approach was a hard
# gate at 30 games - correct about the maths, useless as a product, because
# the answer to most questions became silence. Saying the number WITH its
# confidence tells the same truth and is actually usable on day one of a
# set: everything starts as a hunch and firms up as games arrive.
CONFIDENCE_TIERS = ((100, "confirmed"), (30, "solid"),
                    (10, "early read"), (0, "hunch"))


def confidence(n):
    for floor, label in CONFIDENCE_TIERS:
        if n >= floor:
            return label
    return "hunch"


def placement_moe(n):
    """The error bar on an average placement over n boards."""
    return 1.96 * PLACEMENT_SD / max(n, 1) ** 0.5


def survival_residuals(entries):
    """Placement, minus the average placement of everyone who died as late.

    `entries` is (last_round, placement) pairs. Returns them in the same
    order, each expressed as "how did this board do, for a board that
    lasted this long".

    Comparing final boards without this mostly measures who survived, and
    the difference is not small. Boards with no completed trait looked 1.26
    places worse than boards that had one - until you hold the death round
    fixed, at which point the gap is 0.11. They were not losing because the
    board was unfinished; the board was unfinished because they lost, and
    a board that dies on round 24 cannot have a finished anything.

    So: any comparison of board SHAPE has to go through here first.
    Anything that compares outcomes - a comp's win rate, contested versus
    not - must NOT, because there the survival is the thing being measured.
    """
    by_round = defaultdict(list)
    for rnd, place in entries:
        by_round[rnd].append(place)
    means = {r: statistics.mean(v) for r, v in by_round.items()}
    return [place - means[rnd] for rnd, place in entries]


def mean_gap(sample_a, sample_b, z=1.96):
    """Difference between two means, with the error bar that judges it.

    The general form of gap_moe: it takes the spread from the samples instead
    of assuming placement's fixed 1-8 range, so it works on unit costs, board
    sizes, traits built - anything. Returns None when either side is too small
    to have a variance worth using.

    `z` is how many standard errors count as real. The 1.96 default is the
    usual 95% for a single comparison; a caller testing several metrics at
    once should pass more (see PLAYSTYLE_Z).
    """
    if len(sample_a) < 2 or len(sample_b) < 2:
        return None
    mean_a, mean_b = statistics.mean(sample_a), statistics.mean(sample_b)
    se = (statistics.pvariance(sample_a) / len(sample_a)
          + statistics.pvariance(sample_b) / len(sample_b)) ** 0.5
    moe = z * se
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
    boards and everyone else's. Chronological because latest_shift scans for
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

# Naming a playstyle habit is seven simultaneous tests, one per metric, and at
# the usual 1.96 any one of them can get lucky. Measured against this player's
# own history (resampling his games and checking the claim against the verdict
# from all 114), the ordinary threshold called something wrong in 13-22% of
# samples at EVERY size up to 60 games - more games never fixed it, because
# the problem is the number of metrics, not the sample. At 3.2 that falls to
# 2-5%, and it costs nothing: his real habits score between 4 and 11.6.
PLAYSTYLE_Z = 3.2
# And below this many games nothing is named at all: at 4 games even the
# strict threshold was wrong 19% of the time, and day one of a new set is
# exactly when someone would read it.
MIN_PLAYSTYLE_GAMES = 15


def playstyle_verdicts(era, room):
    """Judge every playstyle metric for one player against their lobbies.

    Returns (verdicts, enough_games). `verdicts` maps a metric key to a
    mean_gap result; when there aren't enough games it is still populated
    with the numbers - the report can show them - but nothing is marked
    real, so no habit gets named.
    """
    enough = len(era) >= MIN_PLAYSTYLE_GAMES
    verdicts = {}
    for key, *_rest in PLAYSTYLE_LABELS:
        judged = mean_gap([p[key] for p in era], [p[key] for p in room],
                          z=PLAYSTYLE_Z)
        if not judged:
            continue
        if not enough:
            judged = {**judged, "real": False}
        verdicts[key] = judged
    return verdicts, enough


def _sharpest_split(profiles, min_era):
    """The single most extreme before/after split in one stretch of games.

    For every possible split point it measures each metric on both sides and
    keeps the largest standardised difference. Returns None if nothing crosses
    SHIFT_THRESHOLD. Scanning every split and keeping the maximum will always
    find *something*, which is why the threshold is deliberately high: this
    reports landslides, not tremors.
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


def all_shifts(profiles, min_era=MIN_ERA):
    """Every style change in order, oldest first - the whole career, not the
    current era.

    latest_shift answers "who is this player now" and throws the rest away.
    A chart of the set wants all of them, because a bend in the results next
    to a change of style is worth more than either alone.
    """
    out, offset, rest = [], 0, profiles
    while True:
        shift = _sharpest_split(rest, min_era)
        if shift is None:
            return out
        shift = {**shift, "cut": shift["cut"] + offset}
        out.append(shift)
        offset, rest = shift["cut"], rest[shift["cut"] - offset:]


def latest_shift(profiles, min_era=MIN_ERA):
    """Find the MOST RECENT change in someone's playstyle.

    `profiles` is a list of playstyle() dicts in chronological order. The
    returned `cut` is an index into that list: everything from it onwards is
    the player as they are now.

    Why this exists: a lifetime average blends every version of a player. This
    player rerolled for the first 80 games of set 17 and went fast 9 after,
    which averages out to a description of nobody. Worse, playstyle is
    *supposed* to change - a new set is exactly when it should - so a tracker
    that reports one number per set will be wrong every August.

    Why it is the most recent shift and not the biggest one: this used to keep
    whichever split scored highest anywhere in the history, which quietly meant
    a player's first big change outranked every later one forever. This player
    switched back to cheap reroll boards at game 176 (avg cost 3.67 -> 2.84,
    z=6.4 - a landslide by any measure), and the reports could not see it,
    because his earlier reroll -> fast 9 switch at game 82 scored 8.8 and won
    the comparison permanently. Eleven weeks of history outvoted who he
    actually is. So: find the sharpest split, then look again in the games
    after it, and keep looking until a stretch has no landslide left in it.
    The last one standing is the current era.

    Each pass judges its own stretch in isolation, which is the point - a
    change is a change relative to what came just before it, not relative to
    a player from four patches ago. Recursion terminates because every pass
    leaves at least min_era games behind, and a stretch shorter than twice
    min_era can never split again.
    """
    shift = _sharpest_split(profiles, min_era)
    if shift is None:
        return None
    later = latest_shift(profiles[shift["cut"]:], min_era)
    if later is None:
        return shift
    # Report the cut against the whole timeline, not the stretch it was
    # found in - every caller slices the full list with it.
    later["cut"] += shift["cut"]
    return later


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
        clauses.append("you survive longer than the lobby")
    elif leans("last_round") < 0:
        clauses.append("you die earlier than the lobby")

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

# PLAYSTYLE_LABELS holds column headings, and a heading spliced into a
# sentence reads like a bug: "your traits actually built went from 0.68 to
# 0.21". These are the same seven metrics phrased as something that happened
# to a player, as (rose, fell, unit). Kept apart from PLAYSTYLE_LABELS rather
# than bolted onto it because four call sites unpack that list as 4-tuples.
SHIFT_PHRASE = {
    "avg_cost":    ("your boards got more expensive", "your boards got cheaper",
                    "gold a unit"),
    "big_units":   ("you started fielding more 4- and 5-costs",
                    "you started fielding fewer 4- and 5-costs", "a game"),
    "rerolled":    ("you started rerolling for 3-stars",
                    "you stopped rerolling for 3-stars", "a game"),
    "board_size":  ("your boards got wider", "your boards got smaller",
                    "units"),
    "lines_built": ("you started committing to traits",
                    "you started splashing instead of committing", "a game"),
    "item_focus":  ("you started funnelling items onto one carry",
                    "you started spreading items around", "of your items"),
    "last_round":  ("you started surviving longer", "you started dying earlier",
                    None),
}


def shift_sentence(shift):
    """What changed, as a sentence with the numbers that back it."""
    rose, fell, unit = SHIFT_PHRASE.get(
        shift["key"], ("your play changed", "your play changed", None))
    what = rose if shift["gap"] > 0 else fell
    if shift["key"] == "last_round":
        return (f"{what} - out on {stage(round(shift['before']))} before, "
                f"{stage(round(shift['after']))} now")
    tail = f" {unit}" if unit else ""
    return (f"{what} - {shift['before']:.2f} to {shift['after']:.2f}{tail}")


# A fact is a thing a player would say about themselves out loud. Each one is
# (key, sentence about you, what the lobby did, how to read one board).
# Rates beat averages here: "you 3-star a cheap unit in 55% of games" is a
# picture, "you 3-star 1.30 cheap units a game" is a spreadsheet cell. The
# reader of this card told us so directly - "no one cares if u build .2 items
# less than other people, it's not helpful" - which is also why item focus and
# board size, the two metrics whose gaps are always small and always
# meaningless, are not in this list at all.
PLAYSTYLE_FACTS = [
    ("reroll",  "You 3-star a cheap unit in {mine:.0%} of games",
     "the lobby does it in {theirs:.0%}",       lambda p: p["rerolled"] >= 1),
    ("commit",  "You finish a trait in {mine:.0%} of games",
     "the lobby finishes one in {theirs:.0%}",  lambda p: p["lines_built"] >= 1),
    ("big",     "You field {mine:.1f} four- and five-costs",
     "the lobby fields {theirs:.1f}",           lambda p: p["big_units"]),
    ("cost",    "Your board averages {mine:.2f} gold a unit",
     "the lobby averages {theirs:.2f}",         lambda p: p["avg_cost"]),
    ("late",    "You reach stage 6 in {mine:.0%} of games",
     "the lobby reaches it in {theirs:.0%}",    lambda p: p["last_round"] >= 33),
]


def playstyle_facts(era, room, z=PLAYSTYLE_Z):
    """How this player plays, in sentences rather than metric gaps.

    Returns a list of facts, most distinctive first. Every fact carries its
    own number AND the lobby's, and `real` says whether the difference cleared
    the threshold - so the card can always show something true even when
    nothing is solid enough to call a habit.

    That "always" is the point. The old card measured seven metrics against
    the lobby and said "Nothing about how you play stands out" whenever none
    of them cleared the bar, which after the era fix is the normal state at
    20 games. A card that goes blank is a card that gets ignored, and the
    silence was never even accurate - it meant "no gap is big enough to be
    called a habit", not "there is nothing to say about you".

    Ordering is by how far the player sits from their lobbies, so the most
    distinctive thing about them leads regardless of which metric it is.
    """
    out = []
    for key, mine_text, theirs_text, measure in PLAYSTYLE_FACTS:
        ours = [float(measure(p)) for p in era]
        theirs = [float(measure(p)) for p in room]
        if not ours or not theirs:
            continue
        judged = mean_gap(ours, theirs, z=z)
        if not judged:
            continue
        spread = judged["moe"] / z if judged["moe"] else 0
        out.append({
            "key": key,
            "text": mine_text.format(mine=judged["a"], theirs=judged["b"]),
            "lobby": theirs_text.format(mine=judged["a"], theirs=judged["b"]),
            "mine": judged["a"], "theirs": judged["b"],
            "real": judged["real"] and len(era) >= MIN_PLAYSTYLE_GAMES,
            "z": abs(judged["gap"]) / spread if spread else 0.0,
        })
    out.sort(key=lambda f: -f["z"])
    return out


def sets_played(puuid=None):
    """How many games this player has in each TFT set, oldest set first.

    load_games exits the process when the set it is handed is empty. That is
    right for a report someone typed TFT_SET into, and wrong for code asking
    "is a cross-set comparison possible at all" - on day one of a new set the
    answer is no, and finding that out must not kill the report. So this
    answers the question first and the caller never asks for a set that is
    not there.
    """
    puuid = puuid or default_puuid()
    found = defaultdict(int)
    for path in match_files():
        try:
            info = json.loads(path.read_text())["info"]
        except (json.JSONDecodeError, KeyError, OSError):
            continue
        if any(p["puuid"] == puuid for p in info.get("participants") or []):
            found[info.get("tft_set_number") or 0] += 1
    return dict(sorted(found.items()))


def carryover(puuid=None, old_set=None, new_set=None, z=PLAYSTYLE_Z):
    """What changed about how you play when the set changed under you.

    Set 18 lands 26 August and every comp number in this app resets to zero
    that morning - for you and for the sites too. Playstyle is what crosses
    the boundary, so this is the one comparison that can speak on day one,
    and "how fast do you find your footing in a new set" is a real skill that
    nothing else measures.

    THE COMPARISON IS GAP-TO-LOBBY, NOT RAW NUMBERS
    -----------------------------------------------
    It compares (you minus your lobby) in the old set against (you minus your
    lobby) in the new one - never your raw averages across sets. A new set
    moves econ pacing and board sizes for all eight players at once, so if
    everybody's boards get bigger and we read raw numbers, a set-wide change
    is reported as something you did. Normalising against the lobby is the
    same rule the rest of this file follows, and here it is load-bearing
    rather than a refinement: it is the only thing making two sets
    comparable.

    Returns None when there is only one set on disk, which is the honest
    state until the rollover. `enough` says whether the new set has the games
    to name anything yet; the numbers are returned either way so a card can
    show them and say it is early.
    """
    puuid = puuid or default_puuid()
    played = sets_played(puuid)
    order = sorted(played)
    if len(order) < 2:
        return None
    new_set = new_set if new_set is not None else order[-1]
    earlier = [s for s in order if s < new_set]
    if old_set is None:
        if not earlier:
            return None
        old_set = earlier[-1]
    if old_set not in played or new_set not in played:
        return None

    sides = {}
    for label, chosen in (("old", old_set), ("new", new_set)):
        games, _ = load_games(puuid, set_number=chosen)
        sides[label] = lobby_profiles(games, chosen)

    metrics = []
    for key, label, more, less in PLAYSTYLE_LABELS:
        judged = {}
        for side in ("old", "new"):
            mine, room = sides[side]
            judged[side] = mean_gap([p[key] for p in mine],
                                    [p[key] for p in room], z=1.96)
        if not judged["old"] or not judged["new"]:
            continue
        # mean_gap's moe is z standard errors; recover the plain SE so the two
        # sets' error bars can be combined, then re-apply the strict bar.
        spread = ((judged["old"]["moe"] / 1.96) ** 2
                  + (judged["new"]["moe"] / 1.96) ** 2) ** 0.5
        change = judged["new"]["gap"] - judged["old"]["gap"]
        moe = z * spread
        metrics.append({
            "key": key, "label": label, "more": more, "less": less,
            "old_gap": judged["old"]["gap"], "new_gap": judged["new"]["gap"],
            "you_old": judged["old"]["a"], "you_new": judged["new"]["a"],
            "change": change, "moe": moe,
            "real": abs(change) > moe and moe > 0,
            "z": abs(change) / spread if spread else 0.0,
        })
    # Tie-break on the label: this list is built from a fixed sequence but two
    # metrics on an identical z would otherwise depend on sort stability.
    metrics.sort(key=lambda m: (-m["z"], m["label"]))

    new_games = len(sides["new"][0])
    return {"old": old_set, "new": new_set,
            "old_games": len(sides["old"][0]), "new_games": new_games,
            "enough": new_games >= MIN_PLAYSTYLE_GAMES,
            "metrics": metrics}


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

    # Warned here rather than in each report: every report loads games, so
    # one check covers all of them, and stderr keeps it out of piped output.
    if names_missing(chosen):
        print(f"\n  !  {STALE_NAMES_WARNING}\n", file=sys.stderr)

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
