"""Scout the people you're about to play against.

Run:  python scout.py orobas#tomo ...    (named players)
      python scout.py <puuid> --offline  (saved matches only, no network)
      python scout.py lobby              (needs a production key - see below)

`lobby` is written and waiting on a key, not on code: spectator-tft-v5
returns 403 for a personal key, so reading your live lobby is blocked on the
same production key the multi-user plan needs. Scouting by name works today.

What this is allowed to be
--------------------------
Riot's rules draw the line at real-time: adapting advice to what is
happening inside a running game is out, static and pre-game information is
fine. This reads public match history before the fight starts and never
looks at live game state, so it stays on the legal side. It is the same
thing you could do by opening someone's match history yourself - just
faster, and only for the seven people actually in your lobby.

Where the data comes from
-------------------------
Your own matches/ folder first. Every saved game holds all eight players, so
196 games is already ~1372 opponent boards, and in a narrow rank band the
same names come round again. That lookup is free and instant. Only when a
player is thin locally does this go to the network, which matters because a
dev key allows 100 requests per two minutes and a full lobby could blow
through that in one go.

Downloaded games land in pool/, never in matches/ - that folder is
your own history and the reports assume every file in it contains you.
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import riot
import tftdata as tft

CACHE_DIR = tft.POOL_DIR   # the shared pool; matches/ stays yours
FETCH_PER_PLAYER = 8    # matches to pull for a player we have no local read on
ENOUGH_LOCALLY = 5      # local games above which we don't spend requests at all
TOP_N = 4               # lines/carries to show per player

# The API lives in riot.py, shared with serve.py. These aliases keep the code
# below reading the way it did before the split.
REGIONAL, RANKED_QUEUE, SLEEP = riot.REGIONAL, riot.RANKED_QUEUE, riot.SLEEP
FORBIDDEN = riot.FORBIDDEN
resolve = riot.resolve


def riot_get(*a, **kw):
    """riot.riot_get, but exiting the way a CLI should instead of raising."""
    try:
        return riot.riot_get(*a, **kw)
    except riot.RiotError as problem:
        sys.exit(str(problem))


def local_boards(set_number=None):
    """Return ({puuid: boards}, set_number) from every match file on disk.

    Reads matches/ and pool/ together: a game saved for one reason is
    just as good evidence for another.

    set_number=None means "whatever set the newest saved game belongs to",
    worked out from the files themselves. It used to come from load_games(),
    which meant scouting an opponent required YOU to have played this set -
    so a fresh copy with an empty matches/ could not scout anybody, even
    though nothing about scouting needs your history.
    """
    by_set = defaultdict(lambda: defaultdict(list))
    # tft.match_files() walks matches/ then pool/ and drops any id it has
    # already seen. The dedupe matters: the same game legitimately lands in
    # both folders, since scouting someone downloads a game you were also in.
    # Counted twice it inflates their game count and drags their average
    # toward one lobby.
    for path in tft.match_files():
        info = json.loads(path.read_text())["info"]
        for p in info["participants"]:
            by_set[info.get("tft_set_number") or 0][p["puuid"]].append(p)

    if not by_set:
        return {}, set_number
    chosen = set_number if set_number is not None else max(by_set)
    return by_set.get(chosen, {}), chosen


def profile(boards, set_number):
    """Turn a pile of one player's boards into what they tend to do."""
    lines = Counter()
    carries = Counter()
    for b in boards:
        for line in tft.committed_lines(b, set_number):
            lines[line] += 1
        carry = tft.carry_of(b)
        if carry:
            carries[carry] += 1
    placements = [b["placement"] for b in boards]
    return {
        "games": len(boards),
        "avg": sum(placements) / len(placements),
        "lines": lines,
        "carries": carries,
    }


def top(counter, games):
    """The habits worth naming, as 'Name (4 of 9)' strings."""
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    return [f"{name} ({n} of {games})" for name, n in ranked] or ["nothing repeated"]


def fetch_player_games(puuid, set_number, have):
    """Download a few of this player's recent ranked games into pool/.

    Returns (boards, set_number). Stops as soon as the set stops matching: IDs
    come back newest first, so once we're into the previous set everything
    below is older too.

    set_number=None means nothing on disk told us which set is current - so
    the newest game this player has defines it. That is what makes scouting
    work from an empty matches/ folder: the answer to "which set are we in"
    comes from the person being scouted, not from the operator's own history.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    ids = riot_get(f"{REGIONAL}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                   start=0, count=FETCH_PER_PLAYER)
    boards = list(have)
    known = {m for folder in (tft.MATCH_DIR, CACHE_DIR) if folder.exists()
             for m in (p.stem for p in folder.glob("*.json"))}

    for match_id in ids:
        if match_id in known:
            continue
        match = riot_get(f"{REGIONAL}/tft/match/v1/matches/{match_id}")
        time.sleep(SLEEP)
        info = match["info"]
        game_set = info.get("tft_set_number") or 0
        if set_number is None:
            set_number = game_set
        if game_set != set_number:
            break
        if info.get("queue_id") != RANKED_QUEUE:
            continue
        (CACHE_DIR / f"{match_id}.json").write_text(json.dumps(match))
        board = next((p for p in info["participants"] if p["puuid"] == puuid), None)
        if board:
            boards.append(board)
    return boards, set_number


def lobby_puuids(puuid=None):
    """Who else is in this player's live game. riot.active_lobby, but exiting
    the way a CLI should. Returns a list, None if not in a game, or FORBIDDEN
    when the key isn't allowed to ask (which a personal key never is)."""
    try:
        return riot.active_lobby(puuid or tft.default_puuid())
    except riot.RiotError as problem:
        sys.exit(str(problem))


def report(label, prof, set_number):
    print(f"\n  {label}")
    if not prof["games"]:
        print("     no games on record for this set - nothing to scout")
        return
    print(f"     {prof['games']} games seen, avg placement {prof['avg']:.2f}")
    print(f"     builds : {', '.join(top(prof['lines'], prof['games']))}")
    print(f"     carries: {', '.join(top(prof['carries'], prof['games']))}")


KNOWN_FLAGS = {"--offline"}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    # Reject a flag we don't recognise rather than dropping it. --offline is a
    # promise not to touch the network, so silently ignoring "--offlien" or
    # "--offline=1" would send requests the caller explicitly asked not to send -
    # the one failure mode this flag exists to prevent.
    unknown = [f for f in flags if f not in KNOWN_FLAGS]
    if unknown:
        sys.exit(f"Unknown option: {' '.join(unknown)}\n"
                 f"The only flag here is --offline (exactly that spelling).")

    offline = "--offline" in flags

    if not args:
        sys.exit(__doc__.strip().split("\n\n")[0] + "\n\n"
                 "Usage: python scout.py lobby\n"
                 "       python scout.py name#tag [name#tag ...]\n"
                 "       add --offline to skip the network entirely")

    # Which set to report on comes from the saved games themselves, not from the
    # operator's own history. TFT_SET still overrides, as everywhere else.
    wanted_set = os.environ.get("TFT_SET")
    if wanted_set:
        try:
            wanted_set = int(wanted_set)
        except ValueError:
            sys.exit(f"TFT_SET must be a set number, not {wanted_set!r}")
    else:
        wanted_set = None

    boards_by_puuid, set_number = local_boards(wanted_set)
    targets = []   # (label, puuid)

    if args == ["lobby"]:
        if offline:
            sys.exit("--offline can't ask Riot who is in your lobby. Name the "
                     "players instead: python scout.py name#tag ...")
        print("Checking whether you're in a game...")
        puuids = lobby_puuids()

        if puuids is FORBIDDEN:
            sys.exit(
                "Riot won't let this key read live lobbies (403 from\n"
                "spectator-tft-v5). That is a permission, not a mistake you can\n"
                "fix: a personal key doesn't include spectator, so `lobby` mode\n"
                "needs the production key - the same one the multi-user plan is\n"
                "waiting on. Verified Aug 7 2026, don't burn an evening on it.\n\n"
                "Everything else here works today. Scout by name instead:\n"
                "  python scout.py name#tag name#tag ...")

        if puuids is None:
            sys.exit("You're not in a game right now.\n"
                     "Riot only lists a lobby while the game is live, so run this "
                     "once the game has started.\n"
                     "To scout specific people any time: python scout.py name#tag ...")

        print(f"Found your game: {len(puuids)} opponents.")
        targets = [(f"opponent {i}", p) for i, p in enumerate(puuids, start=1)]
    else:
        # A raw puuid is accepted as well as a Riot ID. Match files identify
        # players only by puuid - Riot IDs appear nowhere in them - so this is
        # the only way to scout a recurring opponent you have games on but no
        # name for, and it is the one form that works with --offline.
        for who in args:
            if "#" not in who:
                targets.append((f"{who[:8]}... (puuid)", who))
            elif offline:
                sys.exit(f"--offline can't turn '{who}' into an account id - that "
                         "needs Riot. Pass a raw puuid instead, or drop --offline.")
            else:
                puuid, label = resolve(who)
                targets.append((label, puuid))

    if offline and set_number is None:
        sys.exit("Nothing saved yet, and --offline can't ask Riot which set we're "
                 "in.\nRun fetch_matches.py first, or drop --offline.")

    print("=" * 68)
    print(f"  SCOUTING {len(targets)} players"
          f"{f' - set {set_number}' if set_number is not None else ''}")
    print("=" * 68)

    for label, puuid in targets:
        have = boards_by_puuid.get(puuid, [])
        if len(have) < ENOUGH_LOCALLY and not offline:
            print(f"\n  {label}: {len(have)} games locally, asking Riot for more...")
            # The first player scouted on an empty disk also settles which set
            # we're reporting on, so later targets are filtered consistently.
            have, set_number = fetch_player_games(puuid, set_number, have)
        report(label, profile(have, set_number) if have
               else {"games": 0, "avg": 0, "lines": Counter(), "carries": Counter()},
               set_number)

    print(f"""
  How to read this: 'builds' is what they committed to, not what they
  splashed, so it is their actual direction. A line that shows up in most
  of someone's games is a player who forces it - that is the one worth
  avoiding or punishing. Anything under ~5 games is a hint, not a read.

  This is public match history from before the game starts. Nothing here
  looks at the live board, which is the line Riot's rules draw.""")


if __name__ == "__main__":
    main()
