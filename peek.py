"""The first script of this project: pull your latest game and print it.

Kept as the simple end-to-end example. The real workflow is fetch_matches.py
plus the report scripts.
"""

import os
import sys

import requests
import json

# --- FILL THESE IN ---
GAME_NAME = "orobas"   # the part before the #
TAG_LINE  = "tomo"        # the part after the #
# ----------------------

# The key comes from the environment, never from this file: anything written
# here gets committed, and a key in git history stays there after you rotate
# it. Riot also takes the key as an api_key query parameter, which is what
# the rest of the project uses - this Mac's sandbox mangles the X-Riot-Token
# header and makes a perfectly good key look invalid.
API_KEY = os.environ.get("RIOT_API_KEY") or sys.exit(
    "No API key found. Get one at developer.riotgames.com, then run:\n"
    "  export RIOT_API_KEY='RGAPI-...'")


def get(url):
    return requests.get(url, params={"api_key": API_KEY}, timeout=15).json()

# Step 1: Get your PUUID (unique account ID)
print("Looking up your account...")
url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
account = get(url)

if "status" in account:
    print(f"Error: {account['status']['message']}")
    exit()

puuid = account["puuid"]
print(f"Found: {account['gameName']}#{account['tagLine']}")

# Step 2: Get your recent TFT match IDs
print("\nFetching your recent matches...")
url = f"https://americas.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count=10"
match_ids = get(url)
print(f"Found {len(match_ids)} recent TFT games")

# Step 3: Pull your most recent match
print("\nLoading your most recent game...")
url = f"https://americas.api.riotgames.com/tft/match/v1/matches/{match_ids[0]}"
match = get(url)

# Step 4: Find YOU in the match and print a summary
for p in match["info"]["participants"]:
    if p["puuid"] == puuid:
        print(f"\n--- Your Most Recent Game ---")
        print(f"Placement:  #{p['placement']}")
        print(f"Level:      {p['level']}")
        print(f"Last round: {p['last_round']}")

        print(f"\nUnits you played:")
        for unit in p["units"]:
            stars = "★" * unit["tier"]
            print(f"  {stars} {unit['character_id']}")

        print(f"\nActive traits:")
        for trait in p["traits"]:
            if trait["tier_current"] > 0:
                print(f"  {trait['name']} ({trait['num_units']} units)")

# Step 5: Save the full raw data to a file so you can explore it
with open("my_match.json", "w") as f:
    json.dump(match, f, indent=2)

print("\n✓ Full raw match data saved to my_match.json")
print("  Open it in VS Code or any text editor to see everything.")
