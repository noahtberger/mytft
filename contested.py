"""Where do you land when your line is contested vs when you're alone in it?

Run:  python contested.py            (newest set on disk)
      TFT_SET=17 python contested.py (look back at an older set)

Every match file holds all 8 final boards, so for each line you committed to
we can count how many opponents committed to the same one and split your
record into "alone" vs "shared". Same trick for your carry: how many
opponents fielded copies of it (every copy they hold is one you can't roll).

Definitions are proxies - stated here so the numbers can be argued with:
  - "committed" to a line = trait past the splash stage on the final board:
    second breakpoint reached, or 4+ units for single-breakpoint traits.
    Two splashed units are not a direction.
  - an opponent "contests" your line = they also ended committed to it.
  - your "carry" = your most-itemised unit (2+ items), via tftdata.carry_of.
"""

import textwrap
from collections import defaultdict

import tftdata as tft

MIN_CELL = 10   # one side of a split needs this many games before a gap prints

committed = tft.committed   # shared with coverage.py - see tftdata for why
gap_moe = tft.gap_moe

games, _ = tft.load_games()
set_number = games[0]["_set"]
UNIQUE = tft.unique_traits(set_number)

# line -> {"alone": [placements], "shared": [placements], "rivals": [counts]}
by_line = defaultdict(lambda: {"alone": [], "shared": [], "rivals": []})
# my carry fielded by 0 / 1 / 2+ opponents -> placements
by_carry_rivals = defaultdict(list)
committed_games = 0  # games where I truly built at least one line

for g in games:
    opponents = [p for p in g["_lobby"] if p["puuid"] != g["puuid"]]
    opp_lines = [{tft.display(t["name"], "traits") for t in p["traits"]
                  if committed(t)} for p in opponents]

    # Unique traits are one champion, so "contested" there means the unit is
    # contested - the carry section below covers that side of it.
    mine = {tft.display(t["name"], "traits") for t in g["traits"]
            if committed(t)} - UNIQUE
    committed_games += bool(mine)

    for line in mine:
        rivals = sum(1 for ol in opp_lines if line in ol)
        by_line[line]["shared" if rivals else "alone"].append(g["placement"])
        if rivals:
            by_line[line]["rivals"].append(rivals)

    carry = tft.carry_of(g)
    if carry:
        fielded = sum(1 for p in opponents
                      if carry in tft.board_units(p))
        by_carry_rivals[min(fielded, 2)].append(g["placement"])


print("=" * 60)
print(f"  CONTESTED LINES - {len(games)} ranked games, set {set_number}")
print("=" * 60)
print("\n  When you and a rival build the same thing, who should blink?")

# ---- lines --------------------------------------------------------------
print("\nYOUR LINES")
print("  (a line counts when you truly built it: 4+ units or the second")
print("   trait breakpoint. A 2-unit splash is not a direction.)")

rows, thin = [], []
for line, d in by_line.items():
    if len(d["alone"]) >= MIN_CELL and len(d["shared"]) >= MIN_CELL:
        rows.append((line, d))
    elif len(d["alone"]) + len(d["shared"]) >= MIN_CELL:
        thin.append((line, len(d["alone"]) + len(d["shared"])))

# Worst-when-contested first: those are the scouting decisions that matter.
# Name breaks ties so the table doesn't reshuffle between runs on identical
# data (these dicts are filled by iterating sets, whose order varies).
rows.sort(key=lambda r: (tft.summarise(r[1]["alone"])["avg"]
                         - tft.summarise(r[1]["shared"])["avg"], r[0]))

for line, d in rows:
    a, s = tft.summarise(d["alone"]), tft.summarise(d["shared"])
    gap = s["avg"] - a["avg"]
    # "Solid" = the gap is bigger than what shuffling luck around could
    # produce at these sample sizes. Anything smaller stays a hint.
    solid = abs(gap) > gap_moe(a["games"], s["games"])
    avg_rivals = sum(d["rivals"]) / len(d["rivals"])
    company = ("usually one rival" if avg_rivals < 1.5
               else f"{avg_rivals:.1f} rivals on average")

    if solid and gap > 0:
        head = "drop it early when you scout rivals in it"
        why = (f"contested games cost you {gap:.1f} places, and a gap that"
               " size is too big to be luck")
    elif solid:
        head = "fight for it - you win these"
        why = (f"contested games go {abs(gap):.1f} places BETTER, and a gap"
               " that size is too big to be luck")
    elif abs(gap) < 0.35:
        head = "safe to keep playing when contested"
        why = "alone or shared, your results are basically the same"
    else:
        head = (f"maybe {'worse' if gap > 0 else 'better'} when contested"
                " - just a hint for now")
        why = (f"a {abs(gap):.1f}-place gap sounds like a lot, but at"
               f" {a['games']}+{s['games']} games luck alone produces"
               " gaps that big")

    print(f"\n  {line} - {head}.")
    print(f"     alone : avg {a['avg']:.2f}  ({a['games']} games)")
    print(f"     shared: avg {s['avg']:.2f}  ({s['games']} games, {company})")
    print(f"     Why: {why}.")

if not rows:
    print("\n  Nothing to judge yet: no line has 10+ games both alone and")
    print("  contested. Keep playing - this fills in on its own.")

if thin:
    names = ", ".join(f"{line} ({n})"
                      for line, n in sorted(thin, key=lambda t: (-t[1], t[0])))
    print("\n  Too few committed games to judge yet (game counts):")
    print(textwrap.fill(names, width=62,
                        initial_indent="     ", subsequent_indent="     "))

rate = 100 * committed_games / len(games)
print(f"\n  For scale: you truly commit to a line in {rate:.0f}% of your"
      " games.")
if len(rows) < 3 and thin:
    print("  Those commits are spread across many different lines, which is")
    print("  why the list above is short - it fills in as repeats pile up.")

# ---- carry --------------------------------------------------------------
total_carry = sum(len(v) for v in by_carry_rivals.values())
shared_carry = total_carry - len(by_carry_rivals.get(0, []))

print("\nYOUR CARRY")

# No carry in any game means no denominator. That is not hypothetical early in
# a set: carry_of only names a unit holding 2+ items, so a run of games that
# ended before you completed an item leaves this empty.
if not total_carry:
    print("\n  No game here had a unit holding two or more items, so there is")
    print("  no carry to talk about yet. This fills in as games accumulate.")
else:
    # "of the games you had a carry", not "of your games": the denominator is
    # games where carry_of found a 2+ item unit, which is not every game.
    print(f"\n  A rival fields copies of your carry in "
          f"{100 * shared_carry / total_carry:.0f}% of the {total_carry} games "
          f"where you had one ({shared_carry} of {total_carry}) -")
    print("  every copy they hold is one you can't roll into.\n")

    labels = {0: "carry all to yourself", 1: "one rival", 2: "two or more rivals"}
    for n in sorted(by_carry_rivals):
        s = tft.summarise(by_carry_rivals[n])
        print(f"     {labels[n]:<22} avg {s['avg']:.2f}, top-4 {s['top4']:.0f}%"
              f"  ({s['games']} games)")

# Compare every pair of rows; only call out a difference luck can't explain.
sig = None
buckets = sorted(by_carry_rivals)
for i in buckets:
    for j in buckets:
        si, sj = tft.summarise(by_carry_rivals[i]), tft.summarise(by_carry_rivals[j])
        gap = sj["avg"] - si["avg"]
        if i < j and abs(gap) > gap_moe(si["games"], sj["games"]):
            if sig is None or abs(gap) > abs(sig[0]):
                sig = (gap, i, j)

if sig:
    gap, i, j = sig
    print(f"\n  The gap between '{labels[i]}' and '{labels[j]}'"
          f" ({abs(gap):.1f} places) is too")
    print("  big to be luck - that one is worth acting on.")
else:
    print("\n  No row stands out: differences this size are what luck alone")
    print("  produces at these sample sizes. Check back when each row is")
    print("  near 100 games.")

print("""
  One warning before acting on any of this: when a line is strong,
  everyone forces it at once - so "contested" partly just means "it
  was the meta that patch". Respect big gaps, but don't read them as
  pure cause and effect.""")
