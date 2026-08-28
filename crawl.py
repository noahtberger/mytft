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
      python crawl.py --seed --wide   reach into Diamond/Emerald/Platinum too
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
import tftdata as tft

CORPUS = Path("corpus")
STATE = CORPUS / "state.json"
SEEN = CORPUS / "seen.txt"          # match ids already written, one per line

TIERS = ("challenger", "grandmaster", "master")

# Where to seed from when the apex tiers are empty. They are empty exactly
# once per set, on the morning it launches: TFT soft-resets ranks, so on the
# 26th of August there is no Challenger and no Grandmaster, and a crawler
# that only knows those three names seeds nobody on the one day the data is
# worth most. These are read a division at a time and there are plenty.
DIVISIONS = (("DIAMOND", "I"), ("DIAMOND", "II"),
             ("EMERALD", "I"), ("EMERALD", "II"),
             ("PLATINUM", "I"), ("PLATINUM", "II"))
ENOUGH_SEEDS = 500      # below this the apex tiers count as empty
PAGES_PER_DIVISION = 5  # 205 players a page, so five is about a thousand each

GAMES_PER_PLAYER = 200  # Riot's cap per request. See seed_window() for why
                        # asking for the maximum is cheaper, not greedier.
QUEUE_CAP = 200_000     # stop enqueueing beyond this so state.json stays sane
REPORT_EVERY = 25       # matches between progress lines


def patch_of(info):
    """'Linux Version 16.15.712.9042 (Aug ...)' -> '16.15', or 'unknown'.

    Kept for reading the old shards, and no longer used to name new ones.
    Set 18 moved TFT to Unreal and the field became the literal string
    "TFT Unreal Version ?.?.?.?" - no digits anywhere - so on launch morning
    every match in the new set filed itself under "unknown". The date-based
    tft_patch_of has no such dependency and is what shards are named by now.

    The whole corpus is sharded on this string, because a patch is the unit
    the model trains on: numbers that changed on Tuesday describe a different
    game than the ones before them, and averaging across the boundary is the
    mistake this entire project exists to avoid.
    """
    found = re.search(r"(\d+)\.(\d+)", info.get("game_version", ""))
    return found.group(0) if found else "unknown"


# The patch table lives in tftdata, shared with every report, because two
# copies means adding the next patch to one of them and silently blending a
# fortnight of games into the previous one in the other.
tft_patch_of = tft.patch_of
TFT_PATCHES = tft.TFT_PATCHES


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


def seed(queue, visited, wide=False):
    """Fill the queue from the top three ranked tiers.

    High elo first is not snobbery, it is signal. The question the model is
    being asked is "is this trait strong", and a lobby of people who do not
    know the set yet answers "is this trait obvious". The crawl drifts down
    the ladder on its own as it snowballs through lobbies, which is fine -
    it is the starting point that decides the average, not the tail.
    """
    # Seeds go to the FRONT. A re-seed is a deliberate "go and look at
    # these", and the queue it joins holds ninety thousand players snowballed
    # out of lobbies already crawled - the exact exhausted neighbourhood the
    # re-seed exists to escape. Appended to the back they would not be
    # reached for weeks. The snowball inside crawl() still appends, because
    # a lobby-mate is a lead rather than a decision.
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
        queue.extendleft(fresh)
        added += len(fresh)
        print(f"  {tier}: {len(found)} players, {len(fresh)} new")
        time.sleep(riot.SLEEP)

    if added >= ENOUGH_SEEDS and not wide:
        return added

    # Two ways to end up here. On set launch day the apex is genuinely empty
    # and this is the only way to start at all. The other is exhaustion: the
    # apex is about ten thousand players who mostly play each other, so once
    # their recent games are collected, every further request returns ids
    # already on disk and the crawl decays towards nothing. It did - to
    # fourteen matches an hour, with the process perfectly healthy. Neither
    # a rate limiter nor a retry can fix that; only more players can, and
    # the divisional ladder has an order of magnitude more of them.
    why = ("--wide asked for it" if added >= ENOUGH_SEEDS else
           f"only {added} players in the apex tiers - normal on launch day")
    print(f"  reaching into the divisional ladder ({why})")
    for tier, division in DIVISIONS:
        for page_number in range(1, PAGES_PER_DIVISION + 1):
            try:
                page = riot.riot_get(
                    f"{riot.PLATFORM}/tft/league/v1/entries/{tier}/{division}",
                    page=page_number)
            except riot.RiotError as problem:
                print(f"  {tier} {division} p{page_number}: {problem}")
                break
            if not page:
                break       # ran off the end of this division
            # Inactive players cost a request and return nothing, which is
            # the exact failure being fixed. Riot flags them; believe it.
            found = [e["puuid"] for e in page
                     if e.get("puuid") and not e.get("inactive")]
            fresh = [p for p in found if p not in visited]
            queue.extendleft(fresh)
            added += len(fresh)
        print(f"  {tier} {division}: {added:,} queued so far")
        if added >= ENOUGH_SEEDS and not wide:
            break
    return added


def seed_window():
    """Epoch seconds for the start of the current TFT patch, or None.

    Riot lets the id request filter by start time, and using it is the
    difference between a crawl that scales and one that grinds. Without it
    every id request comes back full of games from patches already crawled,
    each of which costs a download to identify and throw away - and asking
    for more ids per player makes that worse rather than better, because the
    extra ones reach further into history.

    With it, one request returns up to two hundred ids that are all from the
    patch being sampled. The same request buys ten times the useful work,
    which is the actual fix for a crawl decaying to fourteen matches an hour.
    """
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    for released, _ in TFT_PATCHES:
        if released <= today:
            return int(dt.datetime.fromisoformat(released)
                       .replace(tzinfo=dt.timezone.utc).timestamp())
    return None


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

            since = seed_window()
            ids = riot.riot_get(
                f"{riot.REGIONAL}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                start=0, count=GAMES_PER_PLAYER,
                **({"startTime": since} if since else {}))
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
                if set_number is None:
                    set_number = game_set
                    print(f"Crawling set {set_number}.")
                # A newer set is a launch, not a mismatch. Without this the
                # saved state pins the crawl to the set it was started on,
                # and on the one morning that matters it downloads the new
                # set's games, rejects every one, marks them seen so they are
                # never retried, and reports a healthy-looking skip count.
                # Verified the hard way: state.json still said 17 at 13:23 on
                # the day Set 18 shipped.
                if game_set > set_number:
                    print(f"\nSet {game_set} has landed - was crawling "
                          f"{set_number}. Switching, and forgetting which "
                          f"players have been walked so their new games are "
                          f"reachable.")
                    set_number, visited = game_set, set()
                if game_set != set_number or info.get("queue_id") != riot.RANKED_QUEUE:
                    rejected += 1
                    continue

                append(tft_patch_of(info), match)
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
               and a not in ("--seed", "--hours", "--status", "--watch",
                             "--wide")]
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
        added = seed(queue, visited, wide="--wide" in args)
        save_state(queue, visited, set_number)
        print(f"{added:,} players added, {len(queue):,} queued.")
        if deadline is None:
            return

    crawl(deadline)


if __name__ == "__main__":
    main()
