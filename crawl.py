"""Crawl the ranked ladder for training data, one patch at a time.

fetch_matches.py answers "what did this player do". This answers a different
question - "what did forty thousand boards do on patch 16.15" - and the two
need opposite shapes. fetch_matches walks one puuid to the end of its
history. This walks outward: seed from the top of the ladder, download their
recent games, and every game hands back seven more players to walk to next.
That is the only way the API lets you sample a patch rather than a person.

Nothing here lands in matches/ or pool/. Those two folders are about you -
your history, and the strangers you looked up - and the reports treat them
as such. A corpus of forty thousand games from people you have never played
against would quietly become "the meta" in every report that reads the pool,
which is a change nobody asked for. So it goes in corpus/, which is
gitignored, disposable, and read only by the model.

Run:  python crawl.py                 crawl until you stop it
      python crawl.py --hours 4       crawl for four hours and stop
      python crawl.py --seed          top up the queue from the ladder again
      python crawl.py --status        what is on disk, no network
      python crawl.py --watch         a live count, refreshed in place

Stopping is always safe. Ctrl-C, a four-hour limit, and a key that expired
at 3am all take the same exit: write the queue down and say where it got to.
Re-running picks up from there. This matters more than it sounds - a dev key
dies every 24 hours, so an un-resumable crawler would never once finish a
night, and the games you miss are gone. Riot keeps match history for a while
and a patch for two weeks; a patch nobody crawled has no data, ever.
"""

import datetime as dt
import gzip
import json
import re
import signal
import sys
import time
from collections import deque
from pathlib import Path

import riot

CORPUS = Path("corpus")
STATE = CORPUS / "state.json"
SEEN = CORPUS / "seen.txt"          # match ids already written, one per line

TIERS = ("challenger", "grandmaster", "master")
GAMES_PER_PLAYER = 20   # recent games only: this samples a patch, not a career
QUEUE_CAP = 200_000     # stop enqueueing beyond this so state.json stays sane
REPORT_EVERY = 25       # matches between progress lines


def patch_of(info):
    """'Linux Version 16.15.712.9042 (Aug ...)' -> '16.15'.

    The whole corpus is sharded on this string, because a patch is the unit
    the model trains on: numbers that changed on Tuesday describe a different
    game than the ones before them, and averaging across the boundary is the
    mistake this entire project exists to avoid.
    """
    found = re.search(r"(\d+)\.(\d+)", info.get("game_version", ""))
    return found.group(0) if found else "unknown"


# TFT numbers its own patches by set: 17.9 is the ninth patch of Set 17, and
# the counter rolls over to 18.1 when the next set lands. That is the number
# printed on the patch notes, so it is the only one that can be joined to a
# list of which traits Riot actually changed.
#
# It is not the number in game_version. That one is the League client build
# ("16.16"), whose major version follows the calendar year, and it drifts
# across a TFT patch boundary because a client build outlives a balance
# change. The two agree often enough to be mistaken for each other and not
# often enough to be used interchangeably.
#
# Release days, from the official notes, newest first. A game belongs to the
# last patch released on or before it was played. Anything earlier than the
# oldest entry is "unknown" rather than guessed: a wrong label here would
# quietly credit a trait change to the wrong fortnight, which is the exact
# error this field exists to prevent. Append new patches as they ship.
TFT_PATCHES = (
    ("2026-08-11", "17.9"),
    ("2026-07-28", "17.8"),
    ("2026-07-14", "17.7"),
    ("2026-06-23", "17.6"),
    ("2026-06-09", "17.5"),
    ("2026-05-27", "17.4"),
    ("2026-05-12", "17.3"),
    ("2026-04-28", "17.2"),
    ("2026-04-14", "17.1"),     # Set 17 launch
    ("2026-03-31", "16.8"),
    ("2026-03-17", "16.7"),
    ("2026-03-03", "16.6"),
)


def tft_patch_of(info):
    """Which TFT patch a match was played on - '17.9', not '16.16'.

    Decided by when the game was played, not by the client build string,
    because the build is the thing that drifts. Riot rolls a patch out over
    a day or so across regions, so a game inside that window can land on
    either side; at a fortnight's cadence that is a rounding error, but it
    is why this is not exact for the handful of games played on release day.
    """
    played = info.get("gameCreation") or info.get("game_datetime")
    if not played:
        return "unknown"
    day = dt.datetime.fromtimestamp(played / 1000, dt.timezone.utc).date()
    for released, name in TFT_PATCHES:
        if day >= dt.date.fromisoformat(released):
            return name
    return "unknown"


def shard(patch):
    return CORPUS / f"{patch}.jsonl.gz"


def append(patch, match):
    """Write one match to its patch's shard.

    Opened and closed per match on purpose. gzip only writes a readable
    trailer when the file is closed, so a handle held across a crawl would
    lose its tail every time the key expires mid-download - which is nightly.
    Each write is its own gzip member; Python reads a file of concatenated
    members back as one stream, so the cost is a little compression and the
    payoff is that no interruption can corrupt what is already on disk.
    """
    CORPUS.mkdir(exist_ok=True)
    with gzip.open(shard(patch), "at", encoding="utf-8") as out:
        out.write(json.dumps(match, separators=(",", ":")) + "\n")


def load_seen():
    """Every match id already written, so a re-run never pays for one twice."""
    if not SEEN.exists():
        return set()
    return {line.strip() for line in SEEN.read_text().splitlines() if line.strip()}


def mark_seen(match_id):
    """Record an id as handled - including ones we deliberately threw away.

    Wrong queue and wrong set get marked too. They cost a request to discover
    and the answer never changes, so remembering the rejection is worth as
    much as remembering the keep. Without this, every re-run re-downloads
    every normal game in the queue to reject it again.
    """
    CORPUS.mkdir(exist_ok=True)
    with SEEN.open("a") as out:
        out.write(match_id + "\n")


def load_state():
    try:
        saved = json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return deque(), set(), None
    return (deque(saved.get("queue", [])),
            set(saved.get("visited", [])),
            saved.get("set"))


def save_state(queue, visited, set_number):
    CORPUS.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({
        "queue": list(queue),
        "visited": sorted(visited),
        "set": set_number,
    }))


def seed(queue, visited):
    """Fill the queue from the top three ranked tiers.

    High elo first is not snobbery, it is signal. The question the model is
    being asked is "is this trait strong", and a lobby of people who do not
    know the set yet answers "is this trait obvious". The crawl drifts down
    the ladder on its own as it snowballs through lobbies, which is fine -
    it is the starting point that decides the average, not the tail.
    """
    added = 0
    for tier in TIERS:
        try:
            league = riot.riot_get(f"{riot.PLATFORM}/tft/league/v1/{tier}")
        except riot.RiotError as problem:
            print(f"  {tier}: {problem}")
            continue
        entries = league.get("entries", [])
        found = [e["puuid"] for e in entries if e.get("puuid")]
        if entries and not found:
            print(f"  {tier}: {len(entries)} entries but no puuid field - "
                  f"Riot changed the shape of this endpoint again.")
            continue
        fresh = [p for p in found if p not in visited]
        queue.extend(fresh)
        added += len(fresh)
        print(f"  {tier}: {len(found)} players, {len(fresh)} new")
        time.sleep(riot.SLEEP)
    return added


def watch(every=20):
    """A live count, for leaving open in a corner and glancing at.

    Rate is measured between refreshes rather than averaged from the start,
    because the average hides exactly the thing worth watching: a crawl that
    is quietly decaying towards nothing looks fine in a cumulative number
    for hours. The night this was written, one decayed from 2,022 an hour to
    138 and the cumulative figure never dropped below four figures.
    """
    was = None
    while True:
        total = sum(count for _, count in shard_counts())
        line = f"{total:,} matches   {total * 8:,} boards"
        if was is not None:
            rate = (total - was) * 3600 / every
            line += f"   {rate:,.0f}/hr"
        print(f"\r  {line}          ", end="", flush=True)
        was = total
        time.sleep(every)


def shard_counts():
    """(patch, matches) for every shard, newest patch last."""
    out = []
    for path in sorted(CORPUS.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            out.append((path.stem.replace(".jsonl", ""), sum(1 for _ in handle)))
    return out


def status():
    """What is on disk, without touching the network."""
    if not CORPUS.exists():
        sys.exit("Nothing crawled yet. Run: python crawl.py --seed")
    queue, visited, set_number = load_state()
    print(f"corpus/  set {set_number or '?'}")
    total = 0
    for path in sorted(CORPUS.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            count = sum(1 for _ in handle)
        total += count
        print(f"  {path.stem.replace('.jsonl', ''):>10}  {count:>7,} matches"
              f"  {count * 8:>8,} boards")
    print(f"  {'total':>10}  {total:>7,} matches  {total * 8:>8,} boards")
    print(f"\n  {len(queue):,} players queued, {len(visited):,} already walked")


def stop_politely():
    """Make `kill` behave like Ctrl-C, so a supervisor can stop us cleanly.

    A crawl started in the background has SIGINT ignored - that is what a
    non-interactive shell does to background jobs - so the only signal left
    to stop it with is SIGTERM, which by default kills Python outright and
    skips the save on the way out. Raising KeyboardInterrupt from the
    handler routes a TERM into the same exit path everything else uses.
    """
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)


def crawl(deadline):
    stop_politely()
    queue, visited, set_number = load_state()
    seen = load_seen()

    if not queue:
        print("Queue is empty - seeding from the ladder.")
        if not seed(queue, visited):
            sys.exit("Could not seed from any tier. Is the key current?")

    saved = rejected = 0
    started = time.time()
    print(f"\n{len(queue):,} players queued. Ctrl-C is safe.\n")

    try:
        while queue:
            if deadline and time.time() > deadline:
                print("\nReached the time limit.")
                break

            puuid = queue.popleft()
            if puuid in visited:
                continue
            visited.add(puuid)

            ids = riot.riot_get(
                f"{riot.REGIONAL}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                start=0, count=GAMES_PER_PLAYER)
            time.sleep(riot.SLEEP)

            for match_id in ids:
                if match_id in seen:
                    continue
                if deadline and time.time() > deadline:
                    break

                match = riot.riot_get(
                    f"{riot.REGIONAL}/tft/match/v1/matches/{match_id}")
                time.sleep(riot.SLEEP)
                seen.add(match_id)
                mark_seen(match_id)

                info = match["info"]
                game_set = info.get("tft_set_number") or 0
                # The first game to arrive defines the set, the same way every
                # other script here lets the data say which set we are in.
                # On the morning Set 18 lands, that is the whole migration.
                if set_number is None:
                    set_number = game_set
                    print(f"Crawling set {set_number}.")
                if game_set != set_number or info.get("queue_id") != riot.RANKED_QUEUE:
                    rejected += 1
                    continue

                append(patch_of(info), match)
                saved += 1

                # Every match is eight players, seven of whom are new leads.
                # This is the entire reason the crawl outruns a per-player
                # fetch: the queue grows faster than it drains.
                if len(queue) < QUEUE_CAP:
                    queue.extend(p["puuid"] for p in info["participants"]
                                 if p["puuid"] not in visited)

                if saved % REPORT_EVERY == 0:
                    rate = saved / max(time.time() - started, 1) * 3600
                    print(f"  {saved:,} saved  {rejected:,} skipped  "
                          f"{len(queue):,} queued  ~{rate:,.0f}/hr", flush=True)

    except KeyboardInterrupt:
        print("\nStopped.")
    except riot.RiotError as problem:
        # Almost always the 24h key, almost always overnight. Say so plainly
        # rather than making a dead key look like a crawler bug.
        print(f"\nRiot stopped answering: {problem}")
    finally:
        save_state(queue, visited, set_number)
        elapsed = (time.time() - started) / 3600
        print(f"\n{saved:,} matches saved ({saved * 8:,} boards) in "
              f"{elapsed:.1f}h. {rejected:,} skipped.")
        print(f"Queue saved with {len(queue):,} players still to walk - "
              f"re-run to carry on.")


def main():
    args = sys.argv[1:]
    if "--status" in args:
        return status()
    if "--watch" in args:
        try:
            return watch()
        except KeyboardInterrupt:
            print()
            return

    unknown = [a for a in args
               if a.startswith("-")
               and a not in ("--seed", "--hours", "--status", "--watch")]
    if unknown:
        sys.exit(f"Unknown option: {' '.join(unknown)}")

    try:
        riot.api_key()      # fail now, not forty thousand requests in
    except riot.RiotError as problem:
        sys.exit(str(problem))

    deadline = None
    if "--hours" in args:
        try:
            deadline = time.time() + float(args[args.index("--hours") + 1]) * 3600
        except (IndexError, ValueError):
            sys.exit("--hours needs a number, like: python crawl.py --hours 4")

    if "--seed" in args:
        queue, visited, set_number = load_state()
        print("Seeding from the ladder...")
        added = seed(queue, visited)
        save_state(queue, visited, set_number)
        print(f"{added:,} players added, {len(queue):,} queued.")
        if deadline is None:
            return

    crawl(deadline)


if __name__ == "__main__":
    main()
