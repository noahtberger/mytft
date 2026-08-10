"""The one report to read before you queue.

Run:  python pregame.py                     (your data only - no network)
      python pregame.py name#tag ...        (also scout those players)
      python pregame.py <puuid> --offline   (scout from saved games only)
      TFT_SET=17 python pregame.py          (look back at an older set)

Four questions, in the order they matter at the queue screen:

  1. What actually wins in your lobbies?  Every saved match holds all 8
     final boards, so the meta here is measured at YOUR rank on YOUR
     patches - 8 boards per game, which is why this table reaches verdicts
     your per-line personal record never will.
  2. Where do you and that meta disagree?  The intersection is the
     coaching: a comp the lobby wins with that you never build is a leak
     no public site can see, because it needs your history AND your
     lobbies' in one place.
  3. What has your own record actually proven?  Only things past the
     30-game bar, with the with-vs-without comparison - everything else
     is a hint and says so.
  4. How do you play right now?  The current-era playstyle sentence, as a
     reminder of the habits you already know about before they cost you
     another game.

Plus scouting, if you name the people you expect to face. All of it is
public match history from before the game starts - nothing reads live
game state, which is the line Riot's rules draw.
"""

import sys
import textwrap
from collections import Counter

import scout
import tftdata as tft

MIN_BOARDS = 30    # committed boards before a room line is worth listing
RATE_FLOOR = 2.0   # % of boards below which a line is too rare to coach on
RECENT_WINDOW = 50  # for the "which patches is this" heads-up


def wrap(text, indent="  ", first=None):
    print(textwrap.fill(text, width=70,
                        initial_indent=first if first is not None else indent,
                        subsequent_indent=indent))


def lobby_boards(games, set_number):
    """(placement, committed comps, is it you) for all 8 seats of every game.

    The raw material for both the meta table and the rate gaps, and for the
    web page's versions of them - one builder so nobody's loop drifts.
    """
    me = games[0]["puuid"]
    rows = []
    for g in games:
        for p in g["_lobby"]:
            rows.append((p["placement"],
                         tft.committed_lines(p, set_number),
                         p["puuid"] == me))
    return rows


def bucket(row):
    """Which coaching group one rate gap belongs to."""
    line, my_pct, room_pct, v = row
    under = my_pct < room_pct
    wins = v and v["solid"] and v["gap"] > 0
    loses = v and v["solid"] and v["gap"] < 0
    if under and wins:
        return "leak"
    if not under and loses:
        return "bad_force"
    if not under:
        return "force"
    return "skip_loser" if loses else "skip_neutral"


def room_table(board_rows, set_number):
    """Every line the room commits to often enough to judge, best first.

    Uses all 8 boards per game - yours included, since your boards are just
    as much evidence about a line's strength here as anyone's. The verdict
    column is the same with-vs-without comparison the personal reports use,
    only the sample is boards instead of your games, which is what makes
    it reach significance: a line on 5% of boards has ~80 samples here
    versus ~10 in your own history.
    """
    placements = [pl for pl, _, _ in board_rows]
    rows = []
    for line in sorted(tft.set_traits(set_number)):
        sel = [line in lines for _, lines, _ in board_rows]
        n = sum(sel)
        if n < MIN_BOARDS:
            continue
        judged = tft.with_without(sel, placements)
        if judged:
            rows.append((line, n, judged))
    rows.sort(key=lambda r: (-r[2]["gap"], r[0]))
    return rows


def rate_gaps(games, board_rows, set_number, room_rows):
    """Lines where how often YOU build something differs from the room.

    A build rate is a mean of 0s and 1s, so mean_gap - the same test every
    other comparison here uses - is exactly the two-proportion test this
    needs: your 196 boards against the room's ~1370, error bar included.

    Each real gap is annotated with the room's verdict on that line, because
    the rate alone isn't coaching. Building less of a line is only a leak if
    the room wins with it; building more is only a worry if nothing says it
    pays.
    """
    my_lines = [tft.committed_lines(g, set_number) for g in games]
    room_lines = [lines for _, lines, is_me in board_rows if not is_me]
    verdict_by_line = {line: judged for line, _, judged in room_rows}

    out = []
    for line in sorted(tft.set_traits(set_number)):
        mine = [line in s for s in my_lines]
        room = [line in s for s in room_lines]
        my_pct = 100 * sum(mine) / len(mine)
        room_pct = 100 * sum(room) / len(room)
        if max(my_pct, room_pct) < RATE_FLOOR:
            continue
        judged = tft.mean_gap([int(x) for x in mine], [int(x) for x in room])
        if not judged or not judged["real"]:
            continue
        out.append((line, my_pct, room_pct, verdict_by_line.get(line)))
    return out


def disagree_section(gaps):
    """Print the rate gaps, loudest first, quietest compressed to one line.

    A leak - a comp the lobby wins with that you rarely build - gets its own
    rows. Comps you skip that the lobby gets nothing from get one shared
    sentence: they are the same fact repeated, and six identical paragraphs
    would bury the two that matter.
    """
    grouped = {}
    for row in gaps:
        grouped.setdefault(bucket(row), []).append(row)

    # Within the leaks, the lobby's payoff ranks them: a +0.74 comp you skip
    # outranks a +0.44 one, whatever the rate gaps are.
    for line, my_pct, room_pct, v in sorted(
            grouped.get("leak", ()), key=lambda r: (-r[3]["gap"], r[0])):
        print(f"\n  {line:<16} you {my_pct:>4.1f}%   lobby {room_pct:>4.1f}%")
        wrap(f"the lobby wins with this (+{v['gap']:.2f}) and you almost "
             "never build it - the clearest kind of leak", indent="        ",
             first="     -> ")

    for line, my_pct, room_pct, v in sorted(
            grouped.get("bad_force", ()), key=lambda r: (r[3]["gap"], r[0])):
        print(f"\n  {line:<16} you {my_pct:>4.1f}%   lobby {room_pct:>4.1f}%")
        wrap(f"you force a comp the lobby loses with ({v['gap']:+.2f})",
             indent="        ", first="     -> ")

    for line, my_pct, room_pct, v in sorted(
            grouped.get("force", ()), key=lambda r: (r[2] - r[1], r[0])):
        print(f"\n  {line:<16} you {my_pct:>4.1f}%   lobby {room_pct:>4.1f}%")
        note = (f"you're heavy on a comp the lobby also wins with "
                f"(+{v['gap']:.2f})" if v and v["solid"] else
                "you build it well above the lobby's rate, and nothing in "
                "the lobby's results says it pays a premium")
        wrap(note, indent="        ", first="     -> ")

    quiet = sorted(r[0] for r in grouped.get("skip_neutral", ()))
    if quiet:
        print()
        wrap(f"You also build {', '.join(quiet)} less than the lobby does, "
             "but the lobby's results with them are ordinary - nothing "
             "proven is being missed there.")
    losers = sorted(r[0] for r in grouped.get("skip_loser", ()))
    if losers:
        print()
        wrap(f"And you skip {', '.join(losers)}, which the lobby loses "
             "with - the right call.")


def proven(games, data):
    """Only what has crossed the 30-game bar, judged with-vs-without."""
    placements = data["placements"]
    rows = []
    for line in data["ready"]:
        judged = tft.with_without([line in mine for mine in data["my_built"]],
                                  placements)
        if judged:
            rows.append((f"{line} (comp)", judged))
    for name, pl in sorted(data["by_carry"].items(),
                           key=lambda kv: (-len(kv[1]), kv[0])):
        if len(pl) < tft.MIN_ACTIONABLE:
            continue
        judged = tft.with_without([tft.carry_of(g) == name for g in games],
                                  placements)
        if judged:
            rows.append((f"{name} (carry)", judged))
    return rows


def playstyle_now(games, set_number):
    """How this player plays right now: (era size, what changed, the facts).

    `shift` is the most recent change of style, or None. `facts` is always
    populated when there are games at all - see tft.playstyle_facts for why
    this no longer returns a sentence that can be empty.
    """
    mine, room = tft.lobby_profiles(games, set_number)
    shift = tft.latest_shift(mine)
    era = mine[shift["cut"]:] if shift else mine
    return len(era), shift, tft.playstyle_facts(era, room)


def scout_targets(names, set_number, offline):
    """Scout the named players, degrading to saved data when Riot is out of
    reach - a missing key shouldn't cost you the rest of the report."""
    import riot

    boards_by_puuid, _ = scout.local_boards(set_number)
    for who in names:
        if "#" in who:
            if offline:
                print(f"\n  {who}: --offline can't turn a name into an "
                      "account id. Pass a raw puuid instead.")
                continue
            try:
                puuid, label = riot.resolve(who)
            except riot.RiotError as problem:
                print(f"\n  {who}: {problem}")
                continue
        else:
            puuid, label = who, f"{who[:8]}... (puuid)"

        have = boards_by_puuid.get(puuid, [])
        if len(have) < scout.ENOUGH_LOCALLY and not offline:
            print(f"\n  {label}: {len(have)} games locally, asking Riot...")
            try:
                riot.backfill(puuid, want=scout.FETCH_PER_PLAYER,
                              set_number=set_number)
            except riot.RiotError as problem:
                print(f"  (couldn't fetch more: {problem})")
            boards_by_puuid, _ = scout.local_boards(set_number)
            have = boards_by_puuid.get(puuid, [])
        scout.report(label, scout.profile(have, set_number) if have
                     else {"games": 0, "avg": 0,
                           "lines": Counter(), "carries": Counter()},
                     set_number)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    unknown = [f for f in flags if f != "--offline"]
    if unknown:
        sys.exit(f"Unknown option: {' '.join(unknown)}\n"
                 f"The only flag here is --offline (exactly that spelling).")
    offline = "--offline" in flags

    games, _ = tft.load_games()
    set_number = games[0]["_set"]
    baseline = tft.summarise([g["placement"] for g in games])

    board_rows = lobby_boards(games, set_number)

    patches = sorted({g["_patch"] for g in games},
                     key=lambda s: tuple(int(x) for x in s.split(".")))
    recent = sorted({g["_patch"] for g in games[:RECENT_WINDOW]},
                    key=lambda s: tuple(int(x) for x in s.split(".")))

    print("=" * 70)
    print(f"  PRE-GAME - {baseline['games']} ranked games, set {set_number}, "
          f"avg {baseline['avg']:.2f}")
    print("=" * 70)

    # ---- 1. the meta, measured in your own lobbies -----------------------
    room_rows = room_table(board_rows, set_number)
    print(f"\nWHAT WINS IN YOUR LOBBIES  ({len(board_rows)} final boards, "
          f"all 8 seats)")
    if room_rows:
        print(f"\n  {'':<16}{'boards':>7}{'rate':>7}{'with':>7}{'without':>9}"
              f"{'gap':>7}   verdict")
        for line, n, judged in room_rows:
            verdict = ("the lobby WINS with it" if judged["solid"]
                       and judged["gap"] > 0 else
                       "the lobby LOSES with it" if judged["solid"]
                       else "no real difference")
            print(f"  {line:<16}{n:>7}{100 * n / len(board_rows):>6.1f}%"
                  f"{judged['with']['avg']:>7.2f}{judged['without']['avg']:>9.2f}"
                  f"{judged['gap']:>+7.2f}   {verdict}")
        print()
        wrap("Positive gap means boards that built the comp place better "
             "than boards without it. Only rows with a WINS or LOSES clear "
             "their own error bars; the rest are hints, listed so you can "
             "see the whole field.")
        print()
        wrap(f"One caveat, always: these games span patches "
             f"{patches[0]}-{patches[-1]}, and strong comps move between "
             f"patches. Your last {min(RECENT_WINDOW, len(games))} games are "
             f"{', '.join(recent)}. A comp nerfed last week still carries "
             "its old wins in this table.")
    else:
        wrap(f"No comp has {MIN_BOARDS}+ built boards yet - early in a "
             "set this table is empty on purpose. It fills in on its own.")

    # ---- 2. where you and the meta disagree ------------------------------
    gaps = rate_gaps(games, board_rows, set_number, room_rows)
    print("\nWHERE YOU DISAGREE WITH THE LOBBY")
    if gaps:
        disagree_section(gaps)
    else:
        print()
        wrap("Your build rates match the lobby's everywhere it matters - "
             "no real gaps to coach on.")

    # ---- 3. what your own record has proven ------------------------------
    data = tft.coverage_data(games, set_number)
    rows = proven(games, data)
    print(f"\nWHAT YOUR OWN GAMES HAVE PROVEN  ({tft.MIN_ACTIONABLE}+ games)")
    if rows:
        print(f"\n  {'':<26}{'games':>6}{'with':>7}{'without':>9}{'gap':>7}"
              "   verdict")
        for label, judged in rows:
            verdict = (f"genuinely {'better' if judged['gap'] > 0 else 'worse'}"
                       if judged["solid"] else "no real difference")
            print(f"  {label:<26}{judged['with']['games']:>6}"
                  f"{judged['with']['avg']:>7.2f}{judged['without']['avg']:>9.2f}"
                  f"{judged['gap']:>+7.2f}   {verdict}")
        print()
        wrap("Everything else in your history is still under 30 games and "
             "lives in coverage.py, not here - a pre-game screen is the "
             "wrong place for numbers that can't be trusted yet.")
    else:
        print()
        wrap("Nothing has crossed the bar yet. Every line and carry average "
             "you half-remember is still a hint - coverage.py shows how far "
             "each one has to go.")

    # ---- 4. how you play right now ---------------------------------------
    era_n, shift, facts = playstyle_now(games, set_number)
    print(f"\nHOW YOU PLAY RIGHT NOW  (current era, {era_n} games)")
    print()
    if shift:
        wrap(f"{era_n} games ago {tft.shift_sentence(shift)}. Everything "
             "below is the player you have been since then, not an average "
             "of you and who you were before.")
    else:
        wrap(f"Your style has held steady across all {era_n} games this set "
             "- no change big enough to split them into before and after.")
    print()
    for f in facts:
        mark = "  <- habit" if f["real"] else ""
        print(f"     {f['text']:<44}{f['lobby']}{mark}")
    print()
    if any(f["real"] for f in facts):
        wrap("Marked lines are far enough from the lobby to call a habit; "
             "the rest are simply what you did.")
    else:
        wrap("None of these is far enough from the lobby to call a settled "
             f"habit yet - that takes about {tft.MIN_PLAYSTYLE_GAMES} games "
             "and a clear gap. They are still what you did.")

    # ---- 5. the people you're about to face ------------------------------
    if args:
        print("\nSCOUTING")
        scout_targets(args, set_number, offline)
    else:
        print()
        wrap("To scout the lobby too: python pregame.py name#tag ... - "
             "or scout.py for just that part.")


if __name__ == "__main__":
    main()
