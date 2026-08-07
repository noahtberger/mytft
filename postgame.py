"""What could've been better: your board against the boards that beat you.

Run:  python postgame.py            (your latest game)
      python postgame.py 3          (your 3rd most recent game)
      python postgame.py NA1_55...  (a specific match id)
      python postgame.py --list     (recent games, so you can pick one)

The comparison is deliberately narrow: the other boards from the SAME
lobby, same patch, same eight players, same items dropping. That controls
for almost everything a global comp site can't - if the winner's board
looks different from yours, it isn't because they were playing a
different meta.

One game is one game, though. The per-game section states facts and
draws no conclusions; the PATTERN section at the bottom is where
conclusions live, because it averages the same comparison over every
game in your current era and only speaks when a gap clears its error
bar. That split - observe per game, conclude per era - is the whole
design.
"""

import statistics
import sys
import textwrap
from datetime import datetime

import tftdata as tft

GOLD_FLOOR = 10   # eliminated holding this much reads as unspent tempo
LEVEL_SLACK = 0.4  # |level - par| below this isn't worth a bullet


def wrap(text, indent="  ", first=None):
    print(textwrap.fill(text, width=70,
                        initial_indent=first if first is not None else indent,
                        subsequent_indent=indent))


def items_held(board):
    """Items sitting on units someone actually bought."""
    return sum(len(u.get("itemNames") or []) for u in tft.shop_units(board))


def ordinal(place):
    return f"{place}{'st nd rd th th th th th'.split()[place - 1]}"


def board_facts(board, set_number):
    """One board's debrief facts, as data.

    Shared by the terminal row below and the web page, so the two views of
    the same board can never disagree. `prof` is None for a disconnect.
    """
    name = board.get("riotIdGameName") or ""
    tag = board.get("riotIdTagline") or ""
    return {
        "place": board["placement"],
        "name": f"{name}#{tag}" if name else "(unknown player)",
        "comps": sorted(tft.committed_lines(board, set_number)),
        "carry": tft.carry_of(board),
        "items": items_held(board),
        "prof": tft.playstyle(board, set_number),
    }


def board_line(board, set_number):
    """One board compressed to the facts that matter, as a printed row."""
    f = board_facts(board, set_number)
    place = ordinal(f["place"])
    built = " + ".join(f["comps"]) if f["comps"] else "no committed comp"
    carried = f"carry {f['carry']}" if f["carry"] else "no 2-item carry"
    if f["prof"] is None:
        return f"  {place:<5}{built:<34}(disconnected board)"
    return (f"  {place:<5}{built:<34}{carried:<22}"
            f"cost {f['prof']['avg_cost']:.1f}  "
            f"{f['prof']['big_units']:.0f} big  "
            f"{f['prof']['rerolled']:.0f} rerolled  "
            f"{f['items']} items")


def margin_bullets(mine, above, set_number, level_par):
    """Facts about where this one game's margin lived. Facts only: at one
    game of evidence a bullet describes, it must not conclude."""
    bullets = []
    my_prof = tft.playstyle(mine, set_number)
    above_profs = [p for p in (tft.playstyle(b, set_number) for b in above) if p]
    if not my_prof or not above_profs:
        return bullets

    my_lines = tft.committed_lines(mine, set_number)
    my_carry = tft.carry_of(mine)

    contested = [b for b in above
                 if tft.committed_lines(b, set_number) & my_lines]
    if contested:
        who = ", ".join(f"the {b['placement']}-place board" for b in contested)
        shared = sorted(set().union(*(tft.committed_lines(b, set_number) & my_lines
                                      for b in contested)))
        bullets.append(f"{who} finished committed to your comp "
                       f"({', '.join(shared)}) - you were contested and "
                       "they came out ahead of you in it.")
    if my_carry:
        holders = [b for b in above if my_carry in tft.board_units(b)]
        if holders:
            bullets.append(f"{len(holders)} of the boards above you fielded "
                           f"your carry {my_carry} - every copy they held "
                           "was one you couldn't roll into.")

    committed_above = sum(1 for p in above_profs if p["lines_built"] >= 1)
    if my_prof["lines_built"] == 0 and committed_above:
        bullets.append(f"{committed_above} of the {len(above)} boards above "
                       "you were committed to at least one comp; you ended "
                       "on splashes only.")

    par = level_par.get(mine["last_round"])
    if par is not None and par - mine["level"] >= LEVEL_SLACK:
        bullets.append(f"you were eliminated at level {mine['level']}; "
                       f"players who die at round {mine['last_round']} "
                       f"average level {par:.1f} - you were behind on "
                       "experience for how long you lasted.")

    if mine.get("gold_left", 0) >= GOLD_FLOOR:
        bullets.append(f"you were eliminated holding {mine['gold_left']} "
                       "gold. Sometimes that is a slow-roll bank, but gold "
                       "you die with bought nothing.")

    my_items, their_items = items_held(mine), \
        statistics.mean(items_held(b) for b in above)
    if their_items - my_items >= 2:
        bullets.append(f"the boards above you averaged {their_items:.0f} "
                       f"items to your {my_items} - a slot-machine gap, "
                       "but also what fighting earlier and longer buys.")
    return bullets


# The pattern section compares against these; last_round is left out on
# purpose - the boards that beat you survived longer by definition, so
# "they were eliminated later" is the outcome restated, not a finding.
# Each key carries its label and the words for either direction, so a gap
# prints as "more rerolling" or "cheaper" instead of a bare signed number.
PATTERN_WORDS = {k: (label, more, less)
                 for k, label, more, less in tft.PLAYSTYLE_LABELS
                 if k != "last_round"}
PATTERN_WORDS["items"] = ("items on the board", "more items", "fewer items")
PATTERN_KEYS = [k for k in PATTERN_WORDS if k != "items"]


def pattern(era_games, set_number):
    """The same board-vs-beaters comparison, averaged over the whole era.

    Paired by game: each game contributes one delta (the mean of the boards
    above you, minus you), so a 7-board 8th place and a 1-board 2nd place
    weigh the same. The error bar is the paired one-sample kind - it asks
    whether the average delta could be zero, which is the honest version of
    "do they consistently differ from me".
    """
    deltas = {k: [] for k in PATTERN_KEYS}
    deltas["items"] = []
    for g in era_games:
        if g["placement"] == 1:
            continue
        mine = tft.playstyle(g, set_number)
        above = [p for p in g["_lobby"] if p["placement"] < g["placement"]]
        profs = [p for p in (tft.playstyle(b, set_number) for b in above) if p]
        if not mine or not profs:
            continue
        for k in PATTERN_KEYS:
            deltas[k].append(statistics.mean(p[k] for p in profs) - mine[k])
        deltas["items"].append(
            statistics.mean(items_held(b) for b in above) - items_held(g))

    rows = []
    for k, ds in deltas.items():
        if len(ds) < 2:
            continue
        mean = statistics.mean(ds)
        moe = 1.96 * statistics.stdev(ds) / len(ds) ** 0.5
        if abs(mean) > moe:
            rows.append((k, mean, len(ds)))
    rows.sort(key=lambda r: (-abs(r[1]), r[0]))
    return rows


def show_game(g, games, level_par, set_number):
    when = datetime.fromtimestamp(g["_datetime"] / 1000).strftime("%b %d")
    idx = games.index(g) + 1
    print("=" * 70)
    print(f"  POST-GAME - placed {g['placement']} on {when}, patch "
          f"{g['_patch']}  (game {idx} of {len(games)}, newest first)")
    print("=" * 70)

    print("\nYOUR BOARD")
    print(board_line(g, set_number))

    above = sorted((p for p in g["_lobby"]
                    if p["placement"] < g["placement"]),
                   key=lambda p: p["placement"])
    if not above:
        print("\nYOU WON THIS ONE - nothing in the lobby beat you. The rest")
        print("of the room, for the record:")
        rest = sorted((p for p in g["_lobby"] if p["puuid"] != g["puuid"]),
                      key=lambda p: p["placement"])
        for b in rest[:3]:
            print(board_line(b, set_number))
        return

    label = "BOARD" if len(above) == 1 else "BOARDS"
    print(f"\nTHE {len(above)} {label} THAT BEAT YOU  (same lobby, same "
          "patch, same items dropping)")
    for b in above:
        print(board_line(b, set_number))

    bullets = margin_bullets(g, above, set_number, level_par)
    print("\nWHERE THE MARGIN LIVED - facts from this game, not conclusions")
    if bullets:
        for b in bullets:
            print()
            wrap(b, indent="    ", first="  - ")
    else:
        print()
        wrap("Nothing structural stands out - no contest, no level or item "
             "deficit, no unspent gold. Games like this are usually lost "
             "on fights, not on the board's shape.")


def main():
    if "--list" in sys.argv:
        games, _ = tft.load_games()
        set_number = games[0]["_set"]
        print("  most recent first - `python postgame.py 3` is the 3rd row")
        for i, g in enumerate(games[:12], start=1):
            when = datetime.fromtimestamp(g["_datetime"] / 1000).strftime("%b %d")
            lines = sorted(tft.committed_lines(g, set_number))
            print(f"  {i:>2}  {when}  placed {g['placement']}   "
                  f"{' + '.join(lines) if lines else 'no committed comp'}")
        return

    games, level_par = tft.load_games()
    set_number = games[0]["_set"]

    arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    if arg.isdigit():
        n = int(arg)
        if not 1 <= n <= len(games):
            sys.exit(f"You have {len(games)} games - pick 1 (latest) to "
                     f"{len(games)}.")
        game = games[n - 1]
    else:
        game = next((g for g in games if g["_match_id"] == arg
                     or g["_match_id"].endswith(arg)), None)
        if game is None:
            sys.exit(f"No saved game with id {arg!r}. python postgame.py "
                     "--list shows recent ones.")

    show_game(game, games, level_par, set_number)

    # ---- the era-level pattern, where conclusions are allowed ------------
    mine, _ = tft.lobby_profiles(games, set_number)
    shift = tft.biggest_shift(mine)
    timeline = list(reversed(games))
    era_games = timeline[shift["cut"]:] if shift else timeline

    rows = pattern(era_games, set_number)
    print(f"\nTHE PATTERN - what beats you  (current era, {len(era_games)} "
          "games)")
    print()
    if rows:
        wrap("Averaged over every game in your era, the boards that beat "
             "you consistently differ from yours here (gaps that cleared "
             "their error bars, biggest first):")
        print()
        for k, mean, n in rows:
            label, more, less = PATTERN_WORDS[k]
            print(f"    {label:<24} {more if mean > 0 else less:<16} "
                  f"({mean:+.2f})")
        print()
        wrap("Part of every gap is survival itself - a board that lives "
             "longer is richer, bigger and more levelled. Read these as "
             "directions to look at, not sizes to chase. And they describe "
             "the boards that beat you, which partly means: the boards "
             "that were ahead. The habit worth changing is the one that "
             "shows up here AND in pregame.py's coaching.")
    else:
        wrap("No consistent difference between your boards and the ones "
             "that beat you has cleared its error bar this era. Losses are "
             "landing for varied reasons, not one repeated one.")


if __name__ == "__main__":
    main()
