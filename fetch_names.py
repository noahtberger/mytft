"""Build names.json - a lookup from Riot's internal IDs to the names you see in game.

Run:  python fetch_names.py

Riot's match data uses IDs like "TFT17_DRX" and "TFT17_Aatrox". The
human-readable names live in Data Dragon, Riot's public static-data CDN.
Data Dragon needs no API key, so this is separate from fetch_matches.py.

Re-run it when a new set launches, or when new names show up as raw IDs.
"""

import json
import re
from pathlib import Path

import requests

DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"


def get(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# Data Dragon is versioned; index 0 is the newest patch.
version = get(f"{DDRAGON}/api/versions.json")[0]
print(f"Using Data Dragon version {version}")

# Traits: keys are already plain IDs like "TFT17_DRX".
traits = {
    api_id: entry["name"]
    for api_id, entry in get(f"{DDRAGON}/cdn/{version}/data/en_US/tft-trait.json")["data"].items()
}

# Champions: keys are full asset paths, so keep only the part after the last "/",
# e.g. ".../Shop/TFT17_Aatrox" -> "TFT17_Aatrox".
units = {
    path.rsplit("/", 1)[-1]: entry["name"]
    for path, entry in get(f"{DDRAGON}/cdn/{version}/data/en_US/tft-champion.json")["data"].items()
}

# Some traits have per-game variants that Data Dragon does NOT distinguish -
# all seven Stargazer constellations are just called "Stargazer" there. The
# real name only appears inside the trait's description text, tagged like
# "This game: <TFTStargazer>The Boar</TFTStargazer>". Community Dragon
# publishes those descriptions, so we scrape the tag out of them.
#
# This matters because the asset suffix lies: TFT17_Stargazer_Wolf is
# "The Boar" in game, and TFT17_Stargazer_Shield is "The Altar".
print("Fetching Community Dragon for trait variant names (~25 MB)...")
cdragon = get(CDRAGON)

variants = {}
for entry in cdragon.get("setData", []):
    for trait in entry.get("traits", []):
        match = re.search(r"<TFTStargazer>(.*?)</TFTStargazer>", trait.get("desc") or "")
        if match:
            variants[trait["apiName"]] = match.group(1)

# Work out which traits belong to exactly one champion ("unique" traits, like
# Graves' Factory New). Don't try to spot these by name - TFT17_GravesTrait is
# unique but lacks the "UniqueTrait" suffix that most of them have. Counting
# how many champions carry each trait is the only reliable test.
#
# setData holds several entries per set (normal, PvE, Pairs...); only the
# entry whose mutator is plain "TFTSet<n>" describes the standard game.
#
# Keep this keyed BY SET. Display names get reused across sets - "Sniper" is
# a one-champion trait in one set and a generic one in another - so merging
# every set into a single list mislabels traits.
unique_by_set = {}
# Which trait IDs belong to which set, recorded rather than guessed from the
# ID text. Every set so far has used a "TFT<n>_" prefix, but Set 18 arrived
# on PBE as "DA_18_Blossom" - so a report that filters on "TFT18_" would find
# no traits at all and quietly report a set with no comps in it. Community
# Dragon knows the real membership; write it down and stop guessing.
trait_ids_by_set = {}
for entry in cdragon.get("setData", []):
    number = entry.get("number")
    if entry.get("mutator") != f"TFTSet{number}":
        continue
    owners = {}
    for champ in entry.get("champions", []):
        for trait in champ.get("traits") or []:
            owners[trait] = owners.get(trait, 0) + 1
    unique_by_set[str(number)] = sorted(n for n, count in owners.items() if count == 1)

    # Only traits some champion actually carries. Riot files a few oddities
    # among a set's traits that no unit has and nobody can build toward -
    # "Set17_CarouselMarket_EmpoweredHexTrait" ("God-Blessed") is a carousel
    # effect, not a comp. The old TFT<n>_ prefix filter dropped those by
    # accident; owning champions is the test that does it on purpose.
    all_ids = [t["apiName"] for t in entry.get("traits", []) if t.get("apiName")]
    carried = [t["apiName"] for t in entry.get("traits", [])
               if t.get("apiName") and t.get("name") in owners]
    # ...but only when the champion list is actually complete. In a finished
    # set nearly every trait is carried by somebody - Set 17 is 42 of 43 -
    # so a low ratio means the data is half-built, not that the traits are
    # fake. Community Dragon's Set 18 entry is exactly that: 36 traits but
    # champions for only 5 of them, and it mislabels the set "Set10". Taking
    # the filter at face value there would report a 5-comp set. A roster with
    # a stray hex effect in it beats a roster that is mostly missing.
    ENOUGH_COVERAGE = 0.8
    if all_ids and len(carried) >= ENOUGH_COVERAGE * len(all_ids):
        trait_ids_by_set[str(number)] = sorted(carried)
    elif all_ids:
        print(f"  ! set {number}: champion data covers only "
              f"{len(carried)}/{len(all_ids)} traits - keeping all of them")
        trait_ids_by_set[str(number)] = sorted(all_ids)

Path("names.json").write_text(json.dumps({
    "version": version,
    "traits": traits,
    "units": units,
    "variants": variants,
    "unique_traits_by_set": unique_by_set,
    "trait_ids_by_set": trait_ids_by_set,
}, indent=2))

print(f"Saved {len(traits)} trait names, {len(units)} unit names, "
      f"{len(variants)} variant names to names.json")
print(f"  unique traits per set: "
      f"{ {k: len(v) for k, v in sorted(unique_by_set.items())} }")
print(f"  example: TFT17_DRX          -> {traits.get('TFT17_DRX')}")
print(f"  example: TFT17_Stargazer_Wolf -> {variants.get('TFT17_Stargazer_Wolf')}")
