"""Download your recent TFT matches and save each one as its own JSON file.

Run:  python fetch_matches.py
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

# --- SETTINGS ---
API_KEY   = os.environ.get("RIOT_API_KEY") or sys.exit(
    "No API key found. Get one at developer.riotgames.com, then run:\n"
    "  export RIOT_API_KEY='RGAPI-...'")
GAME_NAME = "orobas"
TAG_LINE  = "tomo"
REGION    = "americas"  # NA/BR/LAN/LAS all use the "americas" routing host
OUT_DIR   = Path("matches")
MIN_SET   = 17          # keep games from this set ONWARD, so a new set is
                        # picked up automatically; None = keep everything
RANKED_QUEUE = 1100     # ranked TFT. None = keep every queue
PAGE_SIZE = 200         # max match IDs Riot returns per request
SLEEP     = 1.25        # seconds between match downloads (dev key: 100 per 2 min)
STOP_AFTER_OLD = 5      # consecutive pre-MIN_SET games before we stop paging
# ----------------

BASE = f"https://{REGION}.api.riotgames.com"


def riot_get(url, **params):
    """GET a Riot endpoint, retrying politely if we hit the rate limit.

    Riot accepts the key either as an X-Riot-Token header or as an api_key
    query parameter. We use the query parameter because some corporate
    proxies and VPNs strip unrecognised headers, which makes a perfectly
    good key look invalid.
    """
    params["api_key"] = API_KEY
    while True:
        response = requests.get(url, params=params, timeout=15)

        if response.status_code == 429:
            # Riot tells us how long to wait in the Retry-After header.
            wait = int(response.headers.get("Retry-After", 5))
            print(f"  rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue

        if response.status_code in (401, 403):
            sys.exit(f"Error {response.status_code}: your API key is invalid or expired.\n"
                     "Dev keys from developer.riotgames.com last only 24 hours.\n"
                     "Get a fresh one, then run:  export RIOT_API_KEY='RGAPI-...'")

        if response.status_code != 200:
            sys.exit(f"Error {response.status_code} from Riot: {response.text[:200]}")

        return response.json()


# Step 1: Riot ID -> PUUID (the permanent account ID every other endpoint wants)
print(f"Looking up {GAME_NAME}#{TAG_LINE}...")
account = riot_get(f"{BASE}/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}")
puuid = account["puuid"]
print(f"Found: {account['gameName']}#{account['tagLine']}")

# Step 2: page through every match ID Riot still has for you, newest first.
# The endpoint caps each response at PAGE_SIZE, so we walk forward using
# `start` until a short page tells us we've reached the end of the history.
print("\nListing your match history...")
match_ids = []
start = 0
while True:
    page = riot_get(f"{BASE}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                    start=start, count=PAGE_SIZE)
    match_ids.extend(page)
    print(f"  {len(match_ids)} match IDs so far...")
    if len(page) < PAGE_SIZE:
        break
    start += PAGE_SIZE

print(f"Riot has {len(match_ids)} matches on record for you")

# Step 3: download each one, oldest-set games ending the scan early.
# The ID list doesn't say which set a game belongs to, but IDs come back
# newest-first, so once we've seen a run of older-set games everything
# further down is older too and there's no point spending requests on it.
OUT_DIR.mkdir(exist_ok=True)
downloaded = skipped = wrong_set = wrong_queue = 0
consecutive_old = 0

for i, match_id in enumerate(match_ids, start=1):
    path = OUT_DIR / f"{match_id}.json"

    if path.exists():
        skipped += 1
        consecutive_old = 0
        continue

    match = riot_get(f"{BASE}/tft/match/v1/matches/{match_id}")
    info = match["info"]
    time.sleep(SLEEP)

    if MIN_SET is not None and (info.get("tft_set_number") or 0) < MIN_SET:
        wrong_set += 1
        consecutive_old += 1
        if consecutive_old >= STOP_AFTER_OLD:
            print(f"  hit {STOP_AFTER_OLD} games from an older set - "
                  f"stopping at {i} of {len(match_ids)}")
            break
        continue

    consecutive_old = 0

    if RANKED_QUEUE is not None and info.get("queue_id") != RANKED_QUEUE:
        wrong_queue += 1
        continue

    with open(path, "w") as f:
        json.dump(match, f, indent=2)

    me = next(p for p in info["participants"] if p["puuid"] == puuid)
    downloaded += 1
    # flush=True so progress appears live even when output is piped to a file
    print(f"[{i}/{len(match_ids)}] {match_id} - placed #{me['placement']} "
          f"({downloaded} saved)", flush=True)

print(f"\nDone."
      f"\n  {downloaded} newly saved"
      f"\n  {skipped} already had"
      f"\n  {wrong_queue} skipped (not ranked)"
      f"\n  {wrong_set} skipped (older set)"
      f"\n  {len(list(OUT_DIR.glob('*.json')))} total in {OUT_DIR}/")

# Save your PUUID so later scripts know which participant is you.
Path("me.json").write_text(json.dumps({
    "puuid": puuid,
    "gameName": account["gameName"],
    "tagLine": account["tagLine"],
}, indent=2))
