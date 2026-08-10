"""Seed the pool with games from the top of the ranked ladder.

Run:  python seed.py                 10 players, 10 games each
      python seed.py 8 12            8 players, 12 games each

This is what makes day one of a new set work. Your own games take weeks
to accumulate; the ladder's best players have ranked games within hours
of a set going live. A few minutes of downloading gives the meta table
hundreds of boards to read before you have played at all.

Every downloaded match carries all eight boards, so seeding five players
also pulls in the thirty-five people they played against. Games land in
pool/ - disposable, re-downloadable, never mixed into matches/.
"""

import sys

import riot
import tftdata as tft

LADDER = ("challenger", "grandmaster", "master")


def top_players(count):
    """(puuid, league points, tier) for the top of the ladder.

    Walks down the tiers because early in a set the top ones are empty -
    on day one even challenger has nobody in it yet, and whoever is
    highest at the time is by definition the best read available.
    """
    out = []
    for tier in LADDER:
        data = riot.riot_get(f"{riot.PLATFORM}/tft/league/v1/{tier}")
        entries = sorted(data.get("entries", []),
                         key=lambda e: -e.get("leaguePoints", 0))
        for e in entries:
            if e.get("puuid"):
                out.append((e["puuid"], e.get("leaguePoints", 0), tier))
            if len(out) >= count:
                return out
    return out


def seed(players=10, games_each=10, progress=None):
    """Pull recent ranked games for the ladder's best. Returns
    (players_seeded, boards_now_in_pool_for_them)."""
    tops = top_players(players)
    fetched = 0
    for i, (puuid, lp, tier) in enumerate(tops, start=1):
        if progress:
            progress(i, len(tops), tier, lp)
        have, _ = riot.backfill(puuid, want=games_each)
        fetched += have
    return len(tops), fetched


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    players = int(args[0]) if args else 10
    games_each = int(args[1]) if len(args) > 1 else 10

    try:
        riot.api_key()
    except riot.RiotError as problem:
        sys.exit(str(problem))

    print(f"Seeding from the top {players} ladder players, "
          f"{games_each} games each...")

    def progress(i, total, tier, lp):
        print(f"  [{i}/{total}] a {tier} player at {lp} LP")

    try:
        n, fetched = seed(players, games_each, progress)
    except riot.RiotError as problem:
        sys.exit(str(problem))

    total = len(list(tft.match_files()))
    print(f"\nDone: {n} players seeded, {fetched} of their games on disk.")
    print(f"The pool now holds {total} matches ({total * 8} boards).")


if __name__ == "__main__":
    main()
