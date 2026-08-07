"""What have you actually tested this set, and where are you still blind?

Run:  python coverage.py            (newest set on disk)
      TFT_SET=17 python coverage.py (look back at an older set)

This is a report about DATA, not about skill. The other scripts here tell you
how a line performed; this one tells you which lines you have played enough
for that answer to mean anything, and which ones you have no read on at all.
It is the report to run in the first weeks of a new set, when the honest
answer to almost every question is "not enough games yet".

Two things it leans on:

  - "committed" to a line means the second trait breakpoint, or 4+ units for
    a trait with only one breakpoint (tftdata.committed). A 2-unit splash is
    not a direction, and counting it as one is the easiest way to fool
    yourself into thinking you have tested something.
  - the other seven players in every match file are the yardstick for "does
    anyone actually play this?". A line you have never built is only a blind
    spot if the lobby builds it; nobody needs data on a dead trait.
"""

import textwrap

import tftdata as tft

BAR_WIDTH = 12    # characters in the progress-toward-a-verdict bar
LOBBY_FLOOR = 2.0  # a line under this % of lobby boards is too rare to chase


def bar(n, target=tft.MIN_ACTIONABLE):
    """Progress toward a sample size worth acting on."""
    filled = min(BAR_WIDTH, round(BAR_WIDTH * n / target))
    return "█" * filled + "·" * (BAR_WIDTH - filled)


def wrap(text, indent="  "):
    print(textwrap.fill(text, width=68,
                        initial_indent=indent, subsequent_indent=indent))


def with_without(selected, placements):
    """tftdata's comparison, plus the sentence this report prints for it."""
    judged = tft.with_without(selected, placements)
    if judged is None:
        return None
    gap, solid = judged["gap"], judged["solid"]
    verdict = (f"genuinely {'better' if gap > 0 else 'worse'} ({gap:+.2f})"
               if solid else "no real difference")
    return judged["with"], judged["without"], gap, verdict


games, _ = tft.load_games()
set_number = games[0]["_set"]
data = tft.coverage_data(games, set_number)

# Unpacked into the names the printing code below already uses. The maths all
# lives in tftdata.coverage_data so build_site.py renders the same numbers.
n_games = data["games"]
my_built, placements = data["my_built"], data["placements"]
# A plain dict on purpose. Wrapping it back into a defaultdict would re-open
# the bug tftdata just closed: one stray lookup of a line you never built adds
# an empty entry, and the "spread across N lines" count below silently becomes
# the size of the whole set roster.
built_games = data["built_games"]
splashed, roster = data["splashed"], data["roster"]
opp_boards = data["opp_boards"]
lobby_rate, my_rate = data["lobby_rate"], data["my_rate"]

print("=" * 68)
print(f"  SET {set_number} COVERAGE - {n_games} ranked games")
print("=" * 68)
print()
wrap("This is about what your data can and cannot answer yet. The bar is "
     f"{tft.MIN_ACTIONABLE} committed games per line: at 30 games an average "
     "placement carries a margin of error of about +/- 0.8 places, at 10 "
     "games it is +/- 1.5 - wide enough to hide the entire difference "
     "between a good line and a bad one.")

ready, building, blind = data["ready"], data["building"], data["blind"]

# ---- lines you can actually judge ---------------------------------------
print(f"\nVERDICT-READY  ({tft.MIN_ACTIONABLE}+ games where you built the line)")

if ready:
    print(f"\n  {'':<16}{'games':>6}{'with':>7}{'without':>9}{'gap':>7}   verdict")
    for line in ready:
        judged = with_without([line in mine for mine in my_built], placements)
        if not judged:
            continue
        with_it, without, gap, verdict = judged
        print(f"  {line:<16}{with_it['games']:>6}{with_it['avg']:>7.2f}"
              f"{without['avg']:>9.2f}{gap:>+7.2f}   {verdict}")
    print()
    wrap("Read the gap, not the 'with' column on its own: your overall "
         "average already contains these games, so comparing a line against "
         "it compares the line partly against itself.")
else:
    total_commits = sum(len(v) for v in built_games.values())
    spread = len(built_games) or 1
    print("\n  Nothing has reached the bar yet.")
    print()
    wrap(f"Your {n_games} games contain {total_commits} committed line-games "
         f"spread across {spread} different lines - an average of "
         f"{total_commits / spread:.0f} per line. That is the "
         "finding: it is not that the lines are untested by accident, it is "
         "that playing a different direction most games means no direction "
         "ever accumulates a sample. Repetition is what buys a verdict.")

# ---- lines on their way there -------------------------------------------
if building:
    print("\nBUILDING TOWARD A VERDICT")
    print(f"\n  {'':<16}{'built':>6}{'more':>6}  {'progress':<{BAR_WIDTH}}"
          f"  {'lobby':>6}")
    for line in building:
        n = len(built_games[line])
        print(f"  {line:<16}{n:>6}{tft.MIN_ACTIONABLE - n:>6}  {bar(n)}"
              f"  {lobby_rate[line]:>5.1f}%")
    print()
    wrap("'more' is how many further games building that line would take it "
         "to a verdict. 'lobby' is how often the other seven players commit "
         "to it, so you can spend those games on something the meta actually "
         "rewards rather than on a line nobody builds.")

# ---- lines you have no read on at all -----------------------------------
if blind:
    print("\nBLIND SPOTS  (never built past the splash stage)")
    real = [l for l in blind if lobby_rate[l] >= LOBBY_FLOOR]
    rare = [l for l in blind if lobby_rate[l] < LOBBY_FLOOR]

    if real:
        print()
        for line in real:
            note = (f"splashed in {splashed[line]} games, never built"
                    if splashed[line] else "never touched at all")
            print(f"  {line:<16}{lobby_rate[line]:>5.1f}% of lobby boards"
                  f"   ({note})")
        print()
        wrap("These are ranked by how often your lobbies build them. A line "
             "high on this list is one your opponents keep winning games "
             "with while you have zero data on it - that is a blind spot. "
             "It is not advice to force it; it is where your report is "
             "currently silent.")

    if rare:
        print()
        wrap("Too rare in your lobbies to be worth chasing (under "
             f"{LOBBY_FLOOR:.0f}% of boards): {', '.join(rare)}.")

# ---- how your habits compare to the room --------------------------------
divergence = sorted(
    ((my_rate[l] - lobby_rate[l], l) for l in roster
     if lobby_rate[l] >= LOBBY_FLOOR or my_rate[l] >= LOBBY_FLOOR),
    key=lambda t: (-abs(t[0]), t[1]))[:6]

if divergence:
    print("\nYOUR HABITS vs THE ROOM")
    print(f"\n  {'':<16}{'you':>6}{'lobby':>8}")
    for diff, line in sorted(divergence, key=lambda t: (-t[0], t[1])):
        arrow = "much more often" if diff > 0 else "much less often"
        print(f"  {line:<16}{my_rate[line]:>5.0f}%{lobby_rate[line]:>7.1f}%"
              f"   you build it {arrow}")
    print()
    wrap("Playing off-meta is not automatically wrong - a line you know well "
         "can beat one the room forces. This table is here because the gaps "
         "explain the shape of your data: what you over-play is what you "
         "will get a verdict on first.")

# ---- carries -------------------------------------------------------------
by_carry = data["by_carry"]
judged_carries = []

if by_carry:
    top = sorted(by_carry.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]
    carried = sum(len(v) for v in by_carry.values())
    print("\nYOUR CARRIES  (most-itemised unit, 2+ items)")
    print(f"\n  {'':<16}{'games':>6}  progress")
    for name, pl in top:
        print(f"  {name:<16}{len(pl):>6}  {bar(len(pl))}")

    # A carry that crosses the bar earns the same with-vs-without treatment
    # the lines get. Carries usually get there long before lines do: you
    # re-roll into a known unit far more often than you rebuild a whole line.
    judged_carries = []
    for name, pl in sorted(by_carry.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(pl) < tft.MIN_ACTIONABLE:
            continue
        judged = with_without([tft.carry_of(g) == name for g in games],
                              placements)
        if judged:
            judged_carries.append((name, judged))

    if judged_carries:
        print(f"\n  Past the {tft.MIN_ACTIONABLE}-game bar, so these can "
              "actually be judged:")
        print(f"\n  {'':<16}{'games':>6}{'with':>7}{'without':>9}{'gap':>7}"
              "   verdict")
        for name, (with_it, without, gap, verdict) in judged_carries:
            print(f"  {name:<16}{with_it['games']:>6}{with_it['avg']:>7.2f}"
                  f"{without['avg']:>9.2f}{gap:>+7.2f}   {verdict}")
        print()
        wrap("'without' here means every game a different unit was your "
             "most-itemised one, not games you never fielded the unit at "
             "all - this measures leaning on it as the carry.")

    print()
    wrap(f"{len(by_carry)} different carries across {carried} games. Carries "
         "spread thinner than lines do, so treat every carry average in the "
         "other reports as a hint until one of these bars fills.")

# ---- what this means -----------------------------------------------------
print("\nWHERE THIS LEAVES YOU")
print()
if ready:
    wrap(f"{len(ready)} of {len(roster)} lines are judgeable, "
         f"{len(building)} are partway, {len(blind)} are blank.")
else:
    wrap(f"None of the {len(roster)} lines in set {set_number} are judgeable "
         f"yet, {len(building)} are partway there, and {len(blind)} are "
         "blank. Every line number in the other reports is a hint at this "
         "point, not a conclusion.")
print()
if judged_carries:
    names = ", ".join(name for name, _ in judged_carries)
    wrap(f"The exception is your carry: {names} has the games to be judged, "
         "and it is the only number here you can lean on.")
    print()
wrap("Run this again after a session: the bars only move when you repeat a "
     "line, and repeating a line is the only thing that turns this tracker "
     "from a log into an answer.")
