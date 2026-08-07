"""Analyse the matches saved in matches/ and print a performance breakdown.

Run:  python stats.py

Uses only the Python standard library - no API key or network needed,
because fetch_matches.py already saved everything to disk.
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

MIN_GAMES = 3       # don't report a trait/unit seen fewer times than this


def load_my_games(set_number=None):
    """Return my participant record from every saved match of ONE set,
    newest first.

    A report only makes sense within one set - trait names collide across
    sets, so one table would silently mix two different games. None means
    the newest set on disk (whatever is being played right now); look back
    at an older set with:  TFT_SET=17 python stats.py
    """
    puuid = json.loads(Path("me.json").read_text())["puuid"]
    games = []

    for path in Path("matches").glob("*.json"):
        match = json.loads(path.read_text())
        me = next(p for p in match["info"]["participants"] if p["puuid"] == puuid)
        me["_datetime"] = match["info"]["game_datetime"]
        me["_match_id"] = match["metadata"]["match_id"]
        me["_set"] = match["info"].get("tft_set_number") or 0
        games.append(me)

    if not games:
        sys.exit("No matches saved yet - run fetch_matches.py first.")

    chosen = int(set_number or os.environ.get("TFT_SET")
                 or max(g["_set"] for g in games))
    games = [g for g in games if g["_set"] == chosen]
    if not games:
        sys.exit(f"No set-{chosen} games in matches/.")

    games.sort(key=lambda g: g["_datetime"], reverse=True)
    return games


def load_names():
    """Load the ID -> display-name lookup built by fetch_names.py."""
    try:
        return json.loads(Path("names.json").read_text())
    except FileNotFoundError:
        print("names.json not found - run 'python fetch_names.py' for real names.\n")
        return {"traits": {}, "units": {}}


NAMES = load_names()


def display(api_id, kind):
    """'TFT17_DRX' -> 'N.O.V.A.'

    Falls back to stripping the set prefix if the ID isn't in the lookup,
    so a brand-new set still prints something readable instead of crashing.
    """
    name = NAMES.get(kind, {}).get(api_id)
    if name:
        return name
    return api_id.split("_", 1)[1] if "_" in api_id else api_id


def summarise(placements):
    """Turn a list of placements into the three numbers that matter."""
    n = len(placements)
    return {
        "games": n,
        "avg": sum(placements) / n,
        "top4": 100 * sum(1 for p in placements if p <= 4) / n,
        "wins": sum(1 for p in placements if p == 1),
    }


def print_table(title, groups):
    """Print name/games/avg/top4 rows, best average placement first."""
    rows = [(name, summarise(pl)) for name, pl in groups.items()
            if len(pl) >= MIN_GAMES]
    rows.sort(key=lambda r: r[1]["avg"])

    # Show every qualifying row. Truncating to a "top N" hides your worst
    # lines, which are exactly the ones worth knowing about.
    print(f"\n{title}  (at least {MIN_GAMES} games)")
    print(f"  {'':<22} {'games':>5} {'avg':>6} {'top4':>6}")
    for name, s in rows:
        print(f"  {name:<22} {s['games']:>5} {s['avg']:>6.2f} {s['top4']:>5.0f}%")

    if not rows:
        print(f"  (nothing appeared {MIN_GAMES}+ times yet)")


games = load_my_games()
placements = [g["placement"] for g in games]
overall = summarise(placements)

print("=" * 52)
print(f"  TFT PERFORMANCE - {overall['games']} ranked games, "
      f"set {games[0]['_set']}")
print("=" * 52)
print(f"\n  Average placement   {overall['avg']:.2f}")
print(f"  Top 4 rate          {overall['top4']:.0f}%")
print(f"  Firsts              {overall['wins']}")
print(f"  Avg level           {sum(g['level'] for g in games) / len(games):.1f}")
print(f"  Avg gold left       {sum(g['gold_left'] for g in games) / len(games):.1f}")

# Placement histogram - a quick read on whether you're consistent or streaky.
print("\n  Placement spread")
for place in range(1, 9):
    count = placements.count(place)
    print(f"    #{place}  {'█' * count}{'' if count else '-'} {count}")

# Group placements by every trait I actually had active that game.
# Several IDs can share one display name (the seven Stargazer_* variants all
# show as "Stargazer"), so collect names in a set first - otherwise a single
# game would count toward "Stargazer" seven times and skew its average.
by_trait = defaultdict(list)
for g in games:
    active = {display(t["name"], "traits") for t in g["traits"] if t["tier_current"] > 0}
    for name in active:
        by_trait[name].append(g["placement"])

# Same idea for units on my final board (duplicates matter less here, but a
# unit should still count once per game rather than once per copy).
by_unit = defaultdict(list)
for g in games:
    board = {display(u["character_id"], "units") for u in g["units"]}
    for name in board:
        by_unit[name].append(g["placement"])

# Unique traits belong to a single champion, so "I had Doomer active" really
# means "I fielded Vex". They measure a unit, not a comp direction, which is
# why they deserve their own table rather than competing with real verticals.
# Look up the unique list for the set these games came from - the same display
# name can be unique in one set and generic in another. load_my_games()
# filtered to a single set, so games[0] speaks for all of them.
set_number = str(games[0]["_set"])
UNIQUE = set(NAMES.get("unique_traits_by_set", {}).get(set_number, []))

shared_traits = {n: pl for n, pl in by_trait.items() if n not in UNIQUE}
unique_traits = {n: pl for n, pl in by_trait.items() if n in UNIQUE}

print_table("BEST TRAITS - shared (real comp directions)", shared_traits)
print_table("BEST TRAITS - unique to one champion", unique_traits)
print_table("BEST UNITS", by_unit)


def variant_of(api_id):
    """Detect IDs like TFT17_Stargazer_Wolf that are a flavour of a base trait.

    Returns ('Stargazer', 'The Boar'), or (None, None) if it isn't a variant.
    Rather than hardcoding Stargazer, we call it a variant when chopping the
    last _suffix leaves a real trait ID with the *same* display name.

    The readable variant name comes from names.json, scraped out of the trait
    description. Do NOT use the ID suffix as a label - it disagrees with the
    game: _Wolf is "The Boar" and _Shield is "The Altar".
    """
    base, _, suffix = api_id.rpartition("_")
    traits = NAMES.get("traits", {})
    if base in traits and traits[base] == traits.get(api_id):
        label = NAMES.get("variants", {}).get(api_id, f"{suffix} (raw id)")
        return traits[base], label
    return None, None


# Variants (Stargazer constellations) are dealt to you, not chosen, so what
# matters is what you did with each one. A trait at tier_current == 0 was on
# the board but never activated - that's "seen but passed on".
seen = defaultdict(list)       # variant -> placements in every game it appeared
committed = defaultdict(list)  # variant -> placements only when activated

for g in games:
    for t in g["traits"]:
        family, suffix = variant_of(t["name"])
        if not family:
            continue
        seen[suffix].append(g["placement"])
        if t["tier_current"] > 0:
            committed[suffix].append(g["placement"])

if seen:
    family_name = next(variant_of(t["name"])[0]
                       for g in games for t in g["traits"] if variant_of(t["name"])[0])
    all_committed = [p for pl in committed.values() for p in pl]

    print(f"\n{family_name.upper()} - committing to it")
    if all_committed:
        s = summarise(all_committed)
        print(f"  Activated in {s['games']} of {len(games)} games, "
              f"avg {s['avg']:.2f}, top4 {s['top4']:.0f}%")
    else:
        print(f"  Never activated in these {len(games)} games")

    print(f"\n{family_name.upper()} - by variant  (a log, NOT yet a statistic)")
    print(f"  {'':<16} {'seen':>5} {'played':>7} {'commit%':>8} {'avg when played':>16}")
    for label in sorted(seen, key=lambda v: -len(seen[v])):
        played = committed.get(label, [])
        avg = f"{sum(played) / len(played):.2f}" if played else "-"
        rate = 100 * len(played) / len(seen[label])
        print(f"  {label:<16} {len(seen[label]):>5} {len(played):>7} "
              f"{rate:>7.0f}% {avg:>16}")
    print("  Variants are dealt, not chosen, so these need hundreds of games")
    print("  before an average means anything. Let it accumulate.")

# Recent form: compare a recent window against everything before it. The
# window scales with how many games you have - 5 games is pure noise once
# the dataset is in the hundreds.
window = max(5, min(30, len(games) // 5))
if len(games) >= 2 * window:
    recent = summarise([g["placement"] for g in games[:window]])
    earlier = summarise([g["placement"] for g in games[window:]])
    trend = earlier["avg"] - recent["avg"]
    direction = "better" if trend > 0 else "worse"
    print(f"\nRECENT FORM")
    print(f"  Last {recent['games']} games   avg {recent['avg']:.2f}")
    print(f"  The {earlier['games']} before  avg {earlier['avg']:.2f}")
    print(f"  -> trending {direction} by {abs(trend):.2f} places")

# Rough guide: the standard error of a mean placement is about 2.3/sqrt(n),
# so a row needs ~50 games before its average is worth acting on.
reliable = sum(1 for pl in by_trait.values() if len(pl) >= 50)
print(f"\nSample-size note: a trait needs roughly 50 games before its average")
print(f"means much - the margin of error at 10 games is about +/- 1.5 places,")
print(f"at 50 games about +/- 0.65. You have {reliable} traits at 50+ games;")
print("read the rest as hints, not conclusions.")
