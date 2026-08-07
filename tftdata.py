"""Shared loading and naming helpers for the tracker.

Both stats.py and ask.py need the same things: read the saved matches, turn
Riot's internal IDs into readable names, and compare a player against the
other seven people in their lobby. That lives here so there's one copy.
"""

import json
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

MATCH_DIR = Path("matches")
PLACEMENT_SD = 2.29  # spread of placements 1-8; used for margin-of-error maths


def load_names():
    """Load the ID -> display-name lookup built by fetch_names.py."""
    try:
        return json.loads(Path("names.json").read_text())
    except FileNotFoundError:
        print("names.json not found - run 'python fetch_names.py' for real names.\n")
        return {}


NAMES = load_names()


def display(api_id, kind):
    """'TFT17_DRX' -> 'N.O.V.A.'   kind is 'traits' or 'units'.

    Falls back to stripping the set prefix if the ID isn't in the lookup, so a
    brand-new set still prints something readable instead of crashing.
    """
    name = NAMES.get(kind, {}).get(api_id)
    if name:
        return name
    return api_id.split("_", 1)[1] if "_" in api_id else api_id


def variant_of(api_id):
    """Detect IDs like TFT17_Stargazer_Wolf that are a flavour of a base trait.

    Returns ('Stargazer', 'The Boar'), or (None, None) if it isn't a variant.
    The readable variant name comes from names.json - never use the ID suffix
    as a label, it disagrees with the game (_Wolf is "The Boar").
    """
    base, _, suffix = api_id.rpartition("_")
    traits = NAMES.get("traits", {})
    if base in traits and traits[base] == traits.get(api_id):
        label = NAMES.get("variants", {}).get(api_id, f"{suffix} (raw id)")
        return traits[base], label
    return None, None


def unique_traits(set_number):
    """Display names of traits belonging to exactly one champion, for one set."""
    return set(NAMES.get("unique_traits_by_set", {}).get(str(set_number), []))


def load_games(set_number=None):
    """Read the saved matches belonging to one TFT set.

    Returns (my_games, level_par) where my_games is my own participant record
    per match (newest first) and level_par maps an elimination round to the
    average level everyone reached by then. That average is the yardstick for
    "were you ahead or behind" - it comes from all 8 players in every lobby,
    so it self-adjusts for rank and for patch.

    A report only makes sense within one set: trait names collide across sets
    and econ pacing changes, so levels from different sets must never share a
    yardstick. set_number=None means "the newest set on disk" - whatever is
    being played right now, so a new set needs no code changes. To look back
    at an older set once a new one is out:  TFT_SET=17 python ask.py
    """
    puuid = json.loads(Path("me.json").read_text())["puuid"]
    games_by_set = defaultdict(list)
    levels_by_set = defaultdict(lambda: defaultdict(list))

    for path in MATCH_DIR.glob("*.json"):
        match = json.loads(path.read_text())
        info = match["info"]
        game_set = info.get("tft_set_number") or 0

        for p in info["participants"]:
            levels_by_set[game_set][p["last_round"]].append(p["level"])

        me = next(p for p in info["participants"] if p["puuid"] == puuid)
        me["_datetime"] = info["game_datetime"]
        me["_set"] = game_set
        # game_version looks like "Version 16.12.706.1234 <Releases/16.12>";
        # we want just the "16.12" major.minor patch number.
        found = re.search(r"(\d+\.\d+)", info.get("game_version") or "")
        me["_patch"] = found.group(1) if found else "?"
        me["_lobby"] = info["participants"]
        games_by_set[game_set].append(me)

    if not games_by_set:
        sys.exit("No matches saved yet - run fetch_matches.py first.")

    chosen = int(set_number or os.environ.get("TFT_SET") or max(games_by_set))
    if chosen not in games_by_set:
        sys.exit(f"No set-{chosen} games in {MATCH_DIR}/ "
                 f"(sets on disk: {sorted(games_by_set)}).")

    my_games = sorted(games_by_set[chosen],
                      key=lambda g: g["_datetime"], reverse=True)
    level_par = {rnd: statistics.mean(v)
                 for rnd, v in levels_by_set[chosen].items()}
    return my_games, level_par


def summarise(placements):
    """The three numbers that matter, plus a margin of error."""
    n = len(placements)
    return {
        "games": n,
        "avg": statistics.mean(placements),
        "top4": 100 * sum(1 for p in placements if p <= 4) / n,
        "wins": sum(1 for p in placements if p == 1),
        "moe": 1.96 * PLACEMENT_SD / n ** 0.5,
    }


def level_delta(game, level_par):
    """How far above/below the lobby's typical level this player finished.

    Comparing within the same elimination round matters: everyone is level 9
    by round 37, so a raw level average would just measure how long you lived.
    """
    return game["level"] - level_par[game["last_round"]]


def active_traits(game):
    """Display names of traits that were actually online (tier > 0)."""
    return {display(t["name"], "traits") for t in game["traits"] if t["tier_current"] > 0}


def board_units(game):
    """Display names of the units on the final board."""
    return {display(u["character_id"], "units") for u in game["units"]}


def carry_of(game):
    """The most-itemised unit, which is almost always the intended carry."""
    best = max(game["units"], key=lambda u: len(u.get("itemNames") or []), default=None)
    if best is None or len(best.get("itemNames") or []) < 2:
        return None
    return display(best["character_id"], "units")
