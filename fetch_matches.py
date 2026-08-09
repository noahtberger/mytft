"""Download a player's ranked TFT matches, one JSON file per game.

Run:  python fetch_matches.py              your games (from me.json)
      python fetch_matches.py Name#NA1     someone else's, into pool/
      python fetch_matches.py --all        every set, not just recent ones

With no argument this fetches the owner of this copy - whoever me.json
names - into matches/, the folder that gets committed because Riot deletes
old history and nobody else keeps it for you. Naming somebody else puts
their games in pool/ instead, which is disposable.

It walks the whole match history rather than the recent slice riot.backfill
takes, because this is the script you run once to seed a new install.
"""

import json
import sys
import time
from pathlib import Path

import riot
import tftdata as tft

MIN_SET = 17        # keep games from this set onward; --all to keep everything
PAGE_SIZE = 200     # most match ids Riot returns per request
STOP_AFTER_OLD = 5  # consecutive older-set games before we stop paging


def resolve_target(args):
    """Who to fetch, and where their games belong.

    Returns (puuid, label, folder, is_owner).
    """
    named = [a for a in args if not a.startswith("-")]
    if named:
        puuid, label = riot.resolve(named[0])
        try:
            owner = json.loads(Path("me.json").read_text())["puuid"]
        except (FileNotFoundError, KeyError):
            owner = None
        # Naming yourself still writes to matches/ - the folder is defined by
        # whose games they are, not by how the script was invoked.
        is_owner = puuid == owner
        return puuid, label, (tft.MATCH_DIR if is_owner else tft.POOL_DIR), is_owner

    try:
        me = json.loads(Path("me.json").read_text())
    except FileNotFoundError:
        sys.exit("No me.json yet, so I don't know whose games to fetch.\n"
                 "Name yourself once and it will be remembered:\n"
                 "  python fetch_matches.py Name#NA1")
    return (me["puuid"], f"{me.get('gameName')}#{me.get('tagLine')}",
            tft.MATCH_DIR, True)


def main():
    args = sys.argv[1:]
    keep_every_set = "--all" in args
    unknown = [a for a in args if a.startswith("-") and a != "--all"]
    if unknown:
        sys.exit(f"Unknown option: {' '.join(unknown)}. The only flag is --all.")

    try:
        riot.api_key()          # fail now, not sixty requests in
        puuid, label, out_dir, is_owner = resolve_target(args)
    except riot.RiotError as problem:
        sys.exit(str(problem))

    print(f"Fetching {label} into {out_dir}/")

    # Page through every match id Riot still has, newest first.
    match_ids = []
    start = 0
    while True:
        try:
            page = riot.riot_get(
                f"{riot.REGIONAL}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                start=start, count=PAGE_SIZE)
        except riot.RiotError as problem:
            sys.exit(str(problem))
        match_ids.extend(page)
        print(f"  {len(match_ids)} match ids so far...")
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    print(f"Riot has {len(match_ids)} matches on record for {label}")

    out_dir.mkdir(exist_ok=True)
    known = riot.saved_match_ids()      # both folders, so nothing downloads twice
    downloaded = skipped = wrong_set = wrong_queue = 0
    consecutive_old = 0

    for i, match_id in enumerate(match_ids, start=1):
        if match_id in known:
            skipped += 1
            consecutive_old = 0
            continue

        try:
            match = riot.riot_get(f"{riot.REGIONAL}/tft/match/v1/matches/{match_id}")
        except riot.RiotError as problem:
            sys.exit(f"{problem}\n  (stopped after {downloaded} new games; "
                     f"re-run to carry on where this left off)")
        time.sleep(riot.SLEEP)
        info = match["info"]

        if not keep_every_set and (info.get("tft_set_number") or 0) < MIN_SET:
            wrong_set += 1
            consecutive_old += 1
            if consecutive_old >= STOP_AFTER_OLD:
                print(f"  hit {STOP_AFTER_OLD} games from an older set - "
                      f"stopping at {i} of {len(match_ids)}")
                break
            continue
        consecutive_old = 0

        if info.get("queue_id") != riot.RANKED_QUEUE:
            wrong_queue += 1
            continue

        (out_dir / f"{match_id}.json").write_text(json.dumps(match, indent=2))
        mine = next((p for p in info["participants"] if p["puuid"] == puuid), None)
        downloaded += 1
        print(f"[{i}/{len(match_ids)}] {match_id} - placed "
              f"#{mine['placement'] if mine else '?'} ({downloaded} saved)",
              flush=True)

    print(f"\nDone."
          f"\n  {downloaded} newly saved"
          f"\n  {skipped} already had"
          f"\n  {wrong_queue} skipped (not ranked)"
          f"\n  {wrong_set} skipped (older set)"
          f"\n  {len(list(out_dir.glob('*.json')))} total in {out_dir}/")

    # Remember who this copy belongs to, so the reports have a default player.
    if is_owner:
        name, _, tag = label.partition("#")
        Path("me.json").write_text(json.dumps(
            {"puuid": puuid, "gameName": name, "tagLine": tag}, indent=2))


if __name__ == "__main__":
    main()
