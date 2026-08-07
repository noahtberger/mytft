import requests
import json

# --- FILL THESE IN ---
API_KEY   = "RGAPI-8a1d0caf-39a0-44e1-8744-6c84315fb3cc"
GAME_NAME = "orobas"   # the part before the #
TAG_LINE  = "tomo"        # the part after the #
# ----------------------

HEADERS = {"X-Riot-Token": API_KEY}

# Step 1: Get your PUUID (unique account ID)
print("Looking up your account...")
url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{GAME_NAME}/{TAG_LINE}"
account = requests.get(url, headers=HEADERS).json()

if "status" in account:
    print(f"Error: {account['status']['message']}")
    exit()

puuid = account["puuid"]
print(f"Found: {account['gameName']}#{account['tagLine']}")

# Step 2: Get your recent TFT match IDs
print("\nFetching your recent matches...")
url = f"https://americas.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count=10"
match_ids = requests.get(url, headers=HEADERS).json()
print(f"Found {len(match_ids)} recent TFT games")

# Step 3: Pull your most recent match
print("\nLoading your most recent game...")
url = f"https://americas.api.riotgames.com/tft/match/v1/matches/{match_ids[0]}"
match = requests.get(url, headers=HEADERS).json()

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
