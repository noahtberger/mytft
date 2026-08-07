"""Analyse the matches saved in matches/ and print a performance breakdown.

Run:  python stats.py
      TFT_SET=17 python stats.py   (look back at an older set)

Uses only the Python standard library - no API key or network needed,
because fetch_matches.py already saved everything to disk. Loading, naming
and the summary maths live in tftdata.py, shared with the other reports, so
a fix to any of them lands everywhere at once.
"""

from collections import defaultdict

import tftdata as tft

MIN_GAMES = 3       # don't report a trait/unit seen fewer times than this


def print_table(title, groups, built=None):
    """Print name/games/avg/top4 rows, best average placement first.

    `built` adds a column counting the games you actually COMMITTED to each
    line, next to the games it was merely active. The two are wildly
    different and the gap is the whole point: Bastion shows up on 119 boards
    at 3.97 and was built past the splash stage exactly zero times, so that
    3.97 measures "could I afford a frontline", not "is Bastion good for me".
    """
    rows = [(name, tft.summarise(pl)) for name, pl in groups.items()
            if len(pl) >= MIN_GAMES]
    # Name breaks ties. Without it two traits on the same average swap places
    # between runs: these tables are built by iterating sets of strings, whose
    # order changes per process, and a stable sort inherits that order.
    rows.sort(key=lambda r: (r[1]["avg"], r[0]))

    # Show every qualifying row. Truncating to a "top N" hides your worst
    # lines, which are exactly the ones worth knowing about.
    print(f"\n{title}  (at least {MIN_GAMES} games)")
    if built is None:
        print(f"  {'':<22} {'games':>5} {'avg':>6} {'top4':>6}")
        for name, s in rows:
            print(f"  {name:<22} {s['games']:>5} {s['avg']:>6.2f} {s['top4']:>5.0f}%")
    else:
        print(f"  {'':<22} {'board':>5} {'avg':>6} {'top4':>6} {'built':>6}")
        for name, s in rows:
            print(f"  {name:<22} {s['games']:>5} {s['avg']:>6.2f} "
                  f"{s['top4']:>5.0f}% {len(built.get(name, ())):>6}")

    if not rows:
        print(f"  (nothing appeared {MIN_GAMES}+ times yet)")


games, _ = tft.load_games()
placements = [g["placement"] for g in games]
overall = tft.summarise(placements)

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
# show as "Stargazer"), so active_traits returns a set - otherwise a single
# game would count toward "Stargazer" seven times and skew its average.
by_trait = defaultdict(list)
for g in games:
    for name in tft.active_traits(g):
        by_trait[name].append(g["placement"])

# Same idea for units on my final board (duplicates matter less here, but a
# unit should still count once per game rather than once per copy).
by_unit = defaultdict(list)
for g in games:
    for name in tft.board_units(g):
        by_unit[name].append(g["placement"])

# Unique traits belong to a single champion, so "I had Doomer active" really
# means "I fielded Vex". They measure a unit, not a comp direction, which is
# why they deserve their own table rather than competing with real verticals.
# The list is per-set: the same display name can be unique in one set and
# generic in another. load_games() filtered to a single set, so games[0]
# speaks for all of them.
set_number = games[0]["_set"]
UNIQUE = tft.unique_traits(set_number)

shared_traits = {n: pl for n, pl in by_trait.items() if n not in UNIQUE}
unique_traits = {n: pl for n, pl in by_trait.items() if n in UNIQUE}

# Games where the line was genuinely built, not just switched on. Passed to
# the table below so the two counts sit side by side.
built_lines = defaultdict(list)
for g in games:
    for name in tft.committed_lines(g, set_number):
        built_lines[name].append(g["placement"])

print_table("TRAITS ON YOUR FINAL BOARD - shared", shared_traits, built_lines)
print("  'board' counts every game the trait was switched on, including a")
print("  2-unit splash; 'built' counts the games you truly committed (second")
print("  breakpoint, or 4+ units). Where those diverge, the avg belongs to the")
print("  splash, not to the line - run coverage.py for the committed-only view.")
print_table("BEST TRAITS - unique to one champion", unique_traits)
print_table("BEST UNITS", by_unit)

# Variants (Stargazer constellations) are dealt to you, not chosen, so what
# matters is what you did with each one. A trait at tier_current == 0 was on
# the board but never activated - that's "seen but passed on".
seen = defaultdict(list)       # variant -> placements in every game it appeared
committed = defaultdict(list)  # variant -> placements only when activated

for g in games:
    for t in g["traits"]:
        family, suffix = tft.variant_of(t["name"])
        if not family:
            continue
        seen[suffix].append(g["placement"])
        if t["tier_current"] > 0:
            committed[suffix].append(g["placement"])

if seen:
    family_name = next(tft.variant_of(t["name"])[0]
                       for g in games for t in g["traits"]
                       if tft.variant_of(t["name"])[0])
    all_committed = [p for pl in committed.values() for p in pl]

    print(f"\n{family_name.upper()} - committing to it")
    if all_committed:
        s = tft.summarise(all_committed)
        print(f"  Activated in {s['games']} of {len(games)} games, "
              f"avg {s['avg']:.2f}, top4 {s['top4']:.0f}%")
    else:
        print(f"  Never activated in these {len(games)} games")

    print(f"\n{family_name.upper()} - by variant  (a log, NOT yet a statistic)")
    print(f"  {'':<16} {'seen':>5} {'played':>7} {'commit%':>8} {'avg when played':>16}")
    for label in sorted(seen, key=lambda v: (-len(seen[v]), v)):
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
    recent = tft.summarise([g["placement"] for g in games[:window]])
    earlier = tft.summarise([g["placement"] for g in games[window:]])
    trend = earlier["avg"] - recent["avg"]
    # Check the swing against its own error bar before calling it a trend.
    # Everything else here refuses to state a gap it cannot defend; this line
    # used to announce one regardless, and a short recent window makes its
    # margin wide - at 30 vs 166 games luck alone moves this by 0.89 places.
    moe = tft.gap_moe(recent["games"], earlier["games"])
    print(f"\nRECENT FORM")
    print(f"  Last {recent['games']} games   avg {recent['avg']:.2f}")
    print(f"  The {earlier['games']} before  avg {earlier['avg']:.2f}")
    if abs(trend) > moe:
        print(f"  -> genuinely {'better' if trend > 0 else 'worse'} by "
              f"{abs(trend):.2f} places (bigger than the +/-{moe:.2f} error bar)")
    else:
        print(f"  -> no real change: the {abs(trend):.2f}-place difference is")
        print(f"     inside the +/-{moe:.2f} that luck alone produces here")

# Rough guide: the standard error of a mean placement is about 2.3/sqrt(n),
# so a row needs ~50 games before its average is worth acting on.
reliable = sum(1 for pl in by_trait.values() if len(pl) >= 50)
print(f"\nSample-size note: a trait needs roughly 50 games before its average")
print(f"means much - the margin of error at 10 games is about +/- 1.5 places,")
print(f"at 50 games about +/- 0.65. You have {reliable} traits at 50+ games;")
print("read the rest as hints, not conclusions.")
print("\nFor which lines you have actually tested enough to judge, and where")
print("your data is still blank, run:  python coverage.py")
