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

import comps
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
        "comp": comps.name(board, set_number),
        "carry": tft.carry_of(board),
        "items": items_held(board),
        "prof": tft.playstyle(board, set_number),
    }


def board_line(board, set_number):
    """One board compressed to the facts that matter, as a printed row."""
    f = board_facts(board, set_number)
    place = ordinal(f["place"])
    built = f["comp"] or comps.NO_COMP
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

    my_carry = tft.carry_of(mine)

    # Contested = same IDENTITY, not same plan: two "fast 9 five cost"
    # boards fight over the same legendaries even though neither has a
    # vertical, and that is exactly the contest the old trait-overlap
    # check could not see.
    mine_parts = comps.parts(mine, set_number)
    my_identity = mine_parts[1] if mine_parts else None
    contested = []
    if my_identity:
        for b in above:
            bp = comps.parts(b, set_number)
            if bp and bp[1] == my_identity:
                contested.append(b)
    if contested:
        who = ", ".join(short_name(b) for b in contested)
        bullets.append(f"You were contested: {who} played your comp "
                       f"({my_identity}) and finished ahead of you.")
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
                       f"last to stage {tft.stage(mine['last_round'])} "
                       f"average level {par:.1f}, so you were behind on XP.")

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
    that never came together.

    Every comparison here is made against boards that DIED ON THE SAME
    ROUND, and that is not a detail. On raw averages the flat-and-cheap
    board looks 0.96 places worse than the flat-and-expensive one; holding
    survival fixed, the gap is 0.30. Most of what looked like a bad board
    shape was just dying earlier - a board that ends on round 24 has no
    time to be expensive, so "cheap" and "lost" are the same event being
    counted twice. An earlier version of this function quoted the raw
    numbers in a tip, overstating the case about threefold.
    """
    entries, kinds = [], []
    for g in games:
        for b in g["_lobby"]:
            prof = tft.playstyle(b, set_number)
            if prof is None:
                continue
            kinds.append("vertical" if prof["lines_built"] >= 1 else
                         "soup" if prof["avg_cost"] >= SOUP_COST else "stuck")
            entries.append((b["last_round"], b["placement"]))

    adjusted = tft.survival_residuals(entries)
    lobby = {"soup": [], "vertical": [], "stuck": []}
    for kind, value in zip(kinds, adjusted):
        lobby[kind].append(value)

    mine = {"soup": 0, "stuck": 0}
    for g in era_games:
        prof = tft.playstyle(g, set_number)
        if prof and prof["lines_built"] == 0:
            mine["soup" if prof["avg_cost"] >= SOUP_COST else "stuck"] += 1

    def gap(a, b):
        if len(lobby[a]) < 2 or len(lobby[b]) < 2:
            return None
        return tft.mean_gap(lobby[a], lobby[b])

    vs_vertical = gap("soup", "vertical")
    stuck_cost = gap("stuck", "soup")
    return {
        # A soup player has real volume in the style, mostly on purpose.
        "soup_player": mine["soup"] >= 10 and mine["soup"] >= 2 * mine["stuck"],
        # Placement is a golf score: soup holds up unless it is genuinely
        # WORSE than verticals by more than the error bar.
        "soup_ok": vs_vertical is not None
                   and not (vs_vertical["real"] and vs_vertical["gap"] > 0),
        # What staying flat AND cheap costs, against flat-and-expensive
        # boards that lasted just as long. None when it can't be measured.
        "stuck_cost": stuck_cost["gap"] if stuck_cost else None,
        "stuck_moe": stuck_cost["moe"] if stuck_cost else None,
        "stuck_real": bool(stuck_cost and stuck_cost["real"]),
        "counts": {k: len(v) for k, v in lobby.items()},
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


# How big a gap has to be before it is worth acting on, in each metric's
# own units. The error bar says whether a gap is REAL; this says whether it
# is BIG. They are different questions, and with enough games the first one
# starts saying yes to trivia: a 0.04 difference in how concentrated
# someone's items were cleared its error bar at 114 games and produced
# advice to change how they itemise. Four percent of one unit's items is
# not a habit anybody can act on.
MIN_EFFECT = {
    "avg_cost":    0.25,   # gold, on the 1-5 shop scale
    "big_units":   0.75,   # nobody plays three quarters of a unit
    "rerolled":    0.50,   # 3-starred cheap units
    "board_size":  0.75,
    "lines_built": 0.40,   # traits taken to their second breakpoint
    "item_focus":  0.12,   # share of your items on one unit, 0-1
    "items":       1.50,
}


def pattern(era_games, set_number):
    """The same board-vs-beaters comparison, averaged over the whole era.

    Paired by game: each game contributes one delta (the mean of the boards
    above you, minus you), so a 7-board 8th place and a 1-board 2nd place
    weigh the same. The error bar is the paired one-sample kind - it asks
    whether the average delta could be zero, which is the honest version of
    "do they consistently differ from me".

    Returns dicts, each carrying both sides' averages as well as the gap,
    so a report can say "1.2 against your 0.5" instead of only "+0.70".
    `big` marks the gaps that clear MIN_EFFECT - the ones worth advice.
    """
    deltas = {k: [] for k in PATTERN_KEYS}
    deltas["items"] = []
    mine_vals = {k: [] for k in deltas}
    their_vals = {k: [] for k in deltas}

    for g in era_games:
        if g["placement"] == 1:
            continue
        mine = tft.playstyle(g, set_number)
        above = [p for p in g["_lobby"] if p["placement"] < g["placement"]]
        profs = [p for p in (tft.playstyle(b, set_number) for b in above) if p]
        if not mine or not profs:
            continue
        for k in PATTERN_KEYS:
            theirs = statistics.mean(p[k] for p in profs)
            deltas[k].append(theirs - mine[k])
            mine_vals[k].append(mine[k])
            their_vals[k].append(theirs)
        theirs_items = statistics.mean(items_held(b) for b in above)
        deltas["items"].append(theirs_items - items_held(g))
        mine_vals["items"].append(items_held(g))
        their_vals["items"].append(theirs_items)

    rows = []
    for k, ds in deltas.items():
        if len(ds) < 2:
            continue
        mean = statistics.mean(ds)
        moe = 1.96 * statistics.stdev(ds) / len(ds) ** 0.5
        if abs(mean) > moe:
            rows.append({
                "key": k, "gap": mean, "n": len(ds),
                "yours": statistics.mean(mine_vals[k]),
                "theirs": statistics.mean(their_vals[k]),
                "big": abs(mean) >= MIN_EFFECT.get(k, 0),
            })
    rows.sort(key=lambda r: (-abs(r["gap"]), r["key"]))
    return rows


def board_habits(era_games, level_par):
    """Countable habits from your own boards - no comparison needed.

    These are the concrete ones: what level you died at against everyone
    who died on the same round, and how much gold you were still holding.
    Both are things you can act on directly next game, unlike "commit more",
    and both come from data every match file already has.
    """
    levels, gold = [], []
    for g in era_games:
        par = level_par.get(g["last_round"])
        if par is not None:
            levels.append(g["level"] - par)
        gold.append(g.get("gold_left", 0))
    if not levels or not gold:
        return None
    rich = sum(1 for x in gold if x >= 20)
    return {
        "games": len(era_games),
        "level_gap": statistics.mean(levels),
        "behind_share": sum(1 for x in levels if x < -0.5) / len(levels),
        "gold_avg": statistics.mean(gold),
        "rich_share": rich / len(gold),
    }


def advice(rows, flat=None, habits=None):
    """One or two concrete things to try next game.

    Three rules decide what may become a tip:

      - It cleared its error bar AND it is big enough to act on. Those are
        different tests, and only the first was here before: at 114 games a
        0.04 difference in item concentration was "real" and produced advice
        about itemising. MIN_EFFECT is the second test.
      - It is about a choice. Item count and board size mostly describe
        having survived longer, so they stay in the numbers and never
        become instructions.
      - It says a number you can picture. "Commit more" is not advice;
        "the boards that beat you finish with 1.2 completed traits against
        your 0.5" is something you can go and do.

    `flat` is flat_context(): for a player whose flat boards are the
    deliberate expensive kind, and whose lobbies don't punish that style,
    "commit to a comp" would be wrong - their real failure mode is the flat
    board that stays cheap. The reroll tip is skipped for them too: it is
    the same evidence read from the other side, and it would sit on the
    page contradicting the tip above it.
    """
    big = {r["key"]: r for r in rows if r.get("big")}
    tips = []
    soup = flat and flat["soup_player"] and flat["soup_ok"]

    # Blunt on purpose: the tip is the order, the "why" is the receipt.
    built = big.get("lines_built")
    if built and built["gap"] > 0:
        if soup:
            # Only worth saying if staying cheap actually costs something
            # once survival is held fixed. Raw averages made this look like
            # a full placement; it is about a third of that.
            if not (flat.get("stuck_real") and flat.get("stuck_cost")
                    and flat["stuck_cost"] >= 0.25):
                return tips[:2]
            tips.append({
                "do": "If you're still on cheap units when the big ones "
                      "should have landed, take a trait to its second "
                      "breakpoint rather than holding the flat board.",
                "why": f"among boards that lasted as long as yours, staying "
                       f"flat and cheap costs about "
                       f"{flat['stuck_cost']:.2f} places "
                       f"(±{flat['stuck_moe']:.2f}) against flat boards that "
                       f"got expensive. Your fast-9 style itself is fine - "
                       f"it is the half-finished version that isn't."})
        else:
            tips.append({
                "do": "Take one trait to its second breakpoint before you add "
                      "a third trait to the board.",
                "why": f"boards that beat you finish with "
                       f"{built['theirs']:.1f} completed traits; yours finish "
                       f"with {built['yours']:.1f}."})
    elif built and built["gap"] < 0:
        tips.append({
            "do": "Stop forcing a vertical when the units aren't there - a "
                  "wider board of stronger units beats a half-built trait.",
            "why": f"boards that beat you finish with {built['theirs']:.1f} "
                   f"completed traits against your {built['yours']:.1f}."})

    reroll = big.get("rerolled")
    cost = big.get("avg_cost")
    if not soup and reroll:
        if reroll["gap"] > 0 and cost and cost["gap"] < 0:
            tips.append({
                "do": "Take the reroll spot when you're offered one - stay at "
                      "level 6 or 7 and 3-star a cheap carry instead of "
                      "pushing levels every game.",
                "why": f"boards that beat you 3-star {reroll['theirs']:.1f} "
                       f"cheap units a game to your {reroll['yours']:.1f}, on "
                       f"a board averaging {cost['theirs']:.1f} gold against "
                       f"your {cost['yours']:.1f}."})
        elif reroll["gap"] > 0:
            tips.append({
                "do": "Look for a 3-star: when you hold three of a cheap unit "
                      "by stage 3, roll for the last copies rather than "
                      "levelling past them.",
                "why": f"boards that beat you 3-star {reroll['theirs']:.1f} "
                       f"cheap units a game; you manage {reroll['yours']:.1f}."})
        elif reroll["gap"] < 0:
            tips.append({
                "do": "Stop rerolling for 3-stars - level instead and buy the "
                      "bigger units.",
                "why": f"boards that beat you 3-star {reroll['theirs']:.1f} "
                       f"cheap units against your {reroll['yours']:.1f}, and "
                       f"still place above you."})

    focus = big.get("item_focus")
    if focus and focus["gap"] > 0:
        tips.append({
            "do": "Put your finished items on one carry instead of spreading "
                  "them - build the third item on the unit that already has "
                  "two.",
            "why": f"boards that beat you keep "
                   f"{100 * focus['theirs']:.0f}% of their items on one unit; "
                   f"you keep {100 * focus['yours']:.0f}%."})
    elif focus and focus["gap"] < 0:
        tips.append({
            "do": "Spread your items - give the second carry real items "
                  "rather than stacking a fourth onto one unit.",
            "why": f"boards that beat you keep "
                   f"{100 * focus['theirs']:.0f}% of items on one unit; you "
                   f"keep {100 * focus['yours']:.0f}%."})

    # Concrete habits that need no comparison at all.
    if habits:
        if habits["level_gap"] <= -0.4:
            tips.append({
                "do": "Buy XP earlier - level on 2-5 and 3-2 even when it "
                      "costs you a reroll.",
                "why": f"at the stage you die, everyone else is on average "
                       f"{abs(habits['level_gap']):.1f} levels higher, and "
                       f"you're behind in "
                       f"{100 * habits['behind_share']:.0f}% of your games."})
        if habits["rich_share"] >= 0.25:
            tips.append({
                "do": "Spend down to your interest breakpoint before the last "
                      "fight - gold you die holding bought nothing.",
                "why": f"you die with 20+ gold in "
                       f"{100 * habits['rich_share']:.0f}% of games, "
                       f"averaging {habits['gold_avg']:.0f} left over."})

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
    tips = advice(rows, flat_context(games, set_number, era_games),
                  board_habits(era_games, level_par))

    def stat_rows():
        print()
        for r in rows:
            label, more, less = PATTERN_WORDS[r["key"]]
            word = more if r["gap"] > 0 else less
            note = "" if r["big"] else "  (small)"
            print(f"    {label:<24} {word:<16} ({r['gap']:+.2f}){note}")
        print()

    print(f"\nFOR YOUR NEXT GAME  (from your last {len(era_games)} games, "
          "not just this one)")
    print()
    if tips:
        for tip in tips:
            wrap(tip["do"], first="  -> ", indent="     ")
            wrap(f"why: {tip['why']}", first="     ", indent="     ")
            print()
        wrap("The full pattern - how the boards that beat you differ from "
             "yours, gaps too big to be luck only:")
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
