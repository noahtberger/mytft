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


def short_name(board):
    """A player's name, or their seat when a name is somehow missing."""
    return board.get("riotIdGameName") or f"the {ordinal(board['placement'])} board"


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
        who = ", ".join(short_name(b) for b in contested)
        shared = sorted(set().union(*(tft.committed_lines(b, set_number) & my_lines
                                      for b in contested)))
        bullets.append(f"You were contested: {who} played your comp "
                       f"({', '.join(shared)}) and finished ahead of you.")
    if my_carry:
        holders = [b for b in above if my_carry in tft.board_units(b)]
        if holders:
            bullets.append(f"{len(holders)} of the boards that beat you had "
                           f"copies of your carry {my_carry}. Every copy "
                           "they held was one you couldn't hit.")

    committed_above = sum(1 for p in above_profs if p["lines_built"] >= 1)
    if (my_prof["lines_built"] == 0 and committed_above
            and my_prof["avg_cost"] < SOUP_COST):
        # A flat EXPENSIVE board is the fast-9 style played on purpose and
        # gets no bullet - flat expensive places as well as verticals in
        # these lobbies. Flat and cheap is the shape that loses.
        bullets.append("Your board never came together - no comp built and "
                       "no big units. "
                       f"{committed_above} of the {len(above)} boards that "
                       "beat you finished a comp.")

    par = level_par.get(mine["last_round"])
    if par is not None and par - mine["level"] >= LEVEL_SLACK:
        bullets.append(f"You died at level {mine['level']}. Players who "
                       f"last to round {mine['last_round']} average level "
                       f"{par:.1f}, so you were behind on XP.")

    if mine.get("gold_left", 0) >= GOLD_FLOOR:
        bullets.append(f"You died holding {mine['gold_left']} gold. Unless "
                       "you were banking for a slow roll, that gold did "
                       "nothing.")

    my_items, their_items = items_held(mine), \
        statistics.mean(items_held(b) for b in above)
    if their_items - my_items >= 2:
        bullets.append(f"The boards that beat you averaged "
                       f"{their_items:.0f} items to your {my_items}. Part "
                       "of that is them surviving longer, but it's worth "
                       "noticing.")
    return bullets


SOUP_COST = 3.5   # a flat board at/above this avg cost is the deliberate
                  # fast-9 style, not a board that failed to come together


def flat_context(games, set_number, era_games):
    """Is this player's flat style deliberate, and does it work here?

    "No comp built" covers two very different boards: the fast-9 flat board
    of 4- and 5-costs stacking unique traits on purpose, and the cheap board
    that never came together. Measured across this player's lobbies those
    place about a full placement apart (flat expensive ~ verticals, flat
    cheap far worse), so any advice about committing has to know which kind
    of flat player it is talking to - the user rightly pushed back when the
    tracker called an intentional soup board "not a real comp".
    """
    lobby = {"soup": [], "vertical": [], "stuck": []}
    for g in games:
        for b in g["_lobby"]:
            prof = tft.playstyle(b, set_number)
            if prof is None:
                continue
            kind = ("vertical" if prof["lines_built"] >= 1 else
                    "soup" if prof["avg_cost"] >= SOUP_COST else "stuck")
            lobby[kind].append(b["placement"])

    mine = {"soup": 0, "stuck": 0}
    for g in era_games:
        prof = tft.playstyle(g, set_number)
        if prof and prof["lines_built"] == 0:
            mine["soup" if prof["avg_cost"] >= SOUP_COST else "stuck"] += 1

    judged = (tft.mean_gap(lobby["soup"], lobby["vertical"])
              if len(lobby["soup"]) >= 2 and len(lobby["vertical"]) >= 2
              else None)
    return {
        # A soup player has real volume in the style, mostly on purpose.
        "soup_player": mine["soup"] >= 10 and mine["soup"] >= 2 * mine["stuck"],
        # Placement is a golf score: soup holds up unless it is genuinely
        # WORSE than verticals by more than the error bar.
        "soup_ok": judged is not None
                   and not (judged["real"] and judged["gap"] > 0),
        "soup_avg": statistics.mean(lobby["soup"]) if lobby["soup"] else None,
        "vertical_avg": (statistics.mean(lobby["vertical"])
                         if lobby["vertical"] else None),
        "stuck_avg": statistics.mean(lobby["stuck"]) if lobby["stuck"] else None,
    }


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


def advice(rows, flat=None):
    """One or two concrete things to try next game, read off the pattern.

    Only speaks from gaps that already cleared their error bars, and not
    every gap earns a tip: item count and board size mostly describe having
    survived longer, so they stay in the stats list but never become
    advice. A tip has to be about a choice - committing, rerolling, how you
    spend gold and items - or it isn't advice, it's the scoreboard again.

    `flat` is flat_context(): for a player whose flat boards are the
    deliberate expensive kind, and whose lobbies don't punish that style,
    "commit to a comp" would be wrong - their real failure mode is the flat
    board that stays cheap. The reroll tip is skipped for them too: it is
    the same evidence read from the other side, and it would sit on the
    page contradicting the tip above it.
    """
    gaps = {k: mean for k, mean, _ in rows}
    tips = []
    soup = flat and flat["soup_player"] and flat["soup_ok"]

    if gaps.get("lines_built", 0) > 0:
        if soup:
            tips.append("Your flat fast-9 style holds up here: flat "
                        "expensive boards average "
                        f"{flat['soup_avg']:.2f} in your lobbies, verticals "
                        f"{flat['vertical_avg']:.2f}. What loses is the "
                        "in-between board - flat and still cheap averages "
                        f"{flat['stuck_avg']:.2f}. When the big units "
                        "aren't coming, commit to a comp instead of "
                        "staying stuck between the two plans.")
        else:
            tips.append("Commit to one comp and build it out instead of "
                        "splashing traits. The boards that beat you almost "
                        "always finish a comp.")
    elif gaps.get("lines_built", 0) < 0:
        tips.append("Stay flexible. The boards that beat you splash more "
                    "than they commit, so forcing a vertical isn't paying "
                    "off here.")

    cheaper = gaps.get("avg_cost", 0) < 0 or gaps.get("big_units", 0) < 0
    if not soup:
        if gaps.get("rerolled", 0) > 0 and cheaper:
            tips.append("Don't default to the expensive board every game. "
                        "The winners in your lobbies play cheaper units and "
                        "reroll for 3-stars. Look for those spots instead "
                        "of always pushing 4-costs.")
        elif gaps.get("rerolled", 0) > 0:
            tips.append("Look for reroll spots. The boards that beat you "
                        "3-star cheap carries more often than you do.")
        elif gaps.get("rerolled", 0) < 0 and gaps.get("avg_cost", 0) > 0:
            tips.append("Skip the reroll comps. Leveling up to bigger units "
                        "is what wins in your lobbies.")

    if gaps.get("item_focus", 0) > 0.05:
        tips.append("Stack your items on one carry instead of spreading "
                    "them around.")
    elif gaps.get("item_focus", 0) < -0.05:
        tips.append("Spread your items out. Going all-in on one carry "
                    "isn't how the winners here itemize.")

    return tips[:2]


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
    print(f"\nTHE {len(above)} {label} THAT BEAT YOU  (same lobby, same patch)")
    for b in above:
        print(board_line(b, set_number))

    bullets = margin_bullets(g, above, set_number, level_par)
    print("\nWHERE THE MARGIN LIVED  (this game only)")
    if bullets:
        for b in bullets:
            print()
            wrap(b, indent="    ", first="  - ")
    else:
        print()
        wrap("Nothing obvious on the board side - no contest, no level or "
             "item gap, no wasted gold. This one probably came down to "
             "fights.")


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
    tips = advice(rows, flat_context(games, set_number, era_games))

    def stat_rows():
        print()
        for k, mean, _n in rows:
            label, more, less = PATTERN_WORDS[k]
            print(f"    {label:<24} {more if mean > 0 else less:<16} "
                  f"({mean:+.2f})")
        print()

    print(f"\nFOR YOUR NEXT GAME  (from your last {len(era_games)} games, "
          "not just this one)")
    print()
    if tips:
        for tip in tips:
            wrap(tip, first="  -> ", indent="     ")
            print()
        wrap("Why: the boards that beat you keep differing from yours in "
             "the same ways. Only gaps too big to be luck are listed:")
        stat_rows()
        wrap("One caution: boards that beat you also lived longer, which "
             "inflates the size and item rows by itself. The tips above "
             "only come from the choice-shaped gaps - committing, "
             "rerolling, itemizing - never from the scoreboard ones.")
    elif rows:
        wrap("No tip this time: the boards that beat you do differ from "
             "yours, but only in ways that come from surviving longer - "
             "size and items - not from a choice you could copy:")
        stat_rows()
    else:
        wrap("No consistent difference between your boards and the ones "
             "that beat you has cleared its error bar. Losses are landing "
             "for varied reasons, not one repeated one.")


if __name__ == "__main__":
    main()
