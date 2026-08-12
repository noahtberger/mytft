"""Ranked LP over time - the one thing Riot will not tell you twice.

Riot serves your rank as a single current value: tier, division, LP, and
nothing else. There is no history endpoint, and match files carry no rank
field at all (the only `tier` in them is a unit's star level). So every site
that draws an LP graph - MetaTFT, lolchess, op.gg - draws it from snapshots
they took themselves. The history is not retrieved, it is *kept*.

That makes this file the same kind of thing as matches/: append-only, the
only copy, and committed to git on purpose. boards.db is rebuildable and
this is not - deleting it loses the graph permanently, and no amount of
re-fetching brings it back. Whatever we have not written down before now is
already gone, which is why recording starts the day it is turned on rather
than the day someone wants the chart.

JSONL rather than a database: one line per snapshot, appended, never
rewritten. That is diff-friendly in git, survives a half-finished write with
at most one bad line, and can be read by eye. Growth is slow because a
snapshot is only kept when the rank actually MOVED - the watcher polls every
180s, and storing that unchanged would be a hundred thousand lines a year
saying nothing.
"""

import json
import time
from pathlib import Path

HISTORY = Path(__file__).with_name("rank_history.jsonl")

# Riot's ladder as one number, so it can go on a y-axis. Four divisions of
# 100 LP per tier up to Diamond; Master, Grandmaster and Challenger are one
# continuous pool above that, which is why they share a floor.
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
         "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISIONS = {"IV": 0, "III": 1, "II": 2, "I": 3}
APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}
TIER_SPAN = 400


def ladder_points(tier, division, lp):
    """One monotone number for a rank, or None if it isn't a ranked tier.

    Iron IV 0 LP is 0; every division is 100 points; Diamond I 100 LP and
    Master 0 LP are the same height, because that is the same place on the
    ladder. Master upward is a single pool, so a Challenger at 900 LP sits
    900 above the Master floor rather than two tiers up.
    """
    if not tier:
        return None
    tier = tier.upper()
    if tier not in TIERS:
        return None
    if tier in APEX:
        return TIERS.index("MASTER") * TIER_SPAN + (lp or 0)
    step = DIVISIONS.get((division or "").upper())
    if step is None:
        return None
    return TIERS.index(tier) * TIER_SPAN + step * 100 + (lp or 0)


def tier_at(points):
    """A y-axis tick: which rank a ladder-points value lands in."""
    if points is None:
        return ""
    step = int(points) // TIER_SPAN
    if step >= TIERS.index("MASTER"):
        over = int(points) - TIERS.index("MASTER") * TIER_SPAN
        return f"Master +{over}"
    tier = TIERS[max(step, 0)].title()
    div = ["IV", "III", "II", "I"][(int(points) % TIER_SPAN) // 100]
    return f"{tier} {div}"


def label(tier, division, lp):
    """'Diamond III 22 LP', the way a player would say it."""
    if not tier:
        return "Unranked"
    nice = tier.title()
    if tier.upper() in APEX:
        return f"{nice} {lp} LP"
    return f"{nice} {division} {lp} LP"


def entry_from(payload):
    """Pull the ranked-TFT entry out of what the league endpoint returns."""
    for row in payload or []:
        if row.get("queueType") == "RANKED_TFT":
            return row
    return None


def last_for(puuid, path=HISTORY):
    """The newest snapshot on record for one player, or None."""
    rows = history(puuid, path)
    return rows[-1] if rows else None


def history(puuid, path=HISTORY):
    """Every snapshot for one player, oldest first, deduplicated.

    A malformed line is skipped rather than fatal: this file is appended to
    by a background thread, and one torn write must not cost the graph.

    Deduplicating on read is what makes the file safe to merge. Two machines
    both running the app append to their own copies, and .gitattributes
    tells git to take the union of both rather than declare a conflict -
    which can leave the same reading in twice. A reading is identified by
    (player, timestamp), so a repeat is dropped and nothing is lost either
    way. The alternative was a merge conflict on every machine switch, on
    the one file in the repo that cannot be regenerated.
    """
    if not path.exists():
        return []
    out, seen = [], set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("puuid") != puuid:
            continue
        stamp = row.get("at")
        if stamp in seen:
            continue
        seen.add(stamp)
        out.append(row)
    out.sort(key=lambda r: r.get("at", 0))
    return out


def record(puuid, entry, name=None, path=HISTORY, at=None):
    """Append a snapshot, but only when the rank actually moved.

    Returns the row written, or None when nothing changed. The watcher calls
    this every few minutes; writing unchanged rows would bury the signal and
    make the file enormous for no gain.
    """
    if not entry:
        return None
    row = {
        "puuid": puuid,
        "at": at if at is not None else int(time.time() * 1000),
        "tier": entry.get("tier"),
        "div": entry.get("rank"),
        "lp": entry.get("leaguePoints", 0),
        "wins": entry.get("wins"),
        "losses": entry.get("losses"),
    }
    if name:
        row["name"] = name
    row["points"] = ladder_points(row["tier"], row["div"], row["lp"])

    previous = last_for(puuid, path)
    if previous and all(previous.get(k) == row[k]
                        for k in ("tier", "div", "lp")):
        return None
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def series(puuid, path=HISTORY):
    """(when, points, label) per snapshot - what a chart needs."""
    return [(r["at"], r["points"], label(r["tier"], r["div"], r["lp"]))
            for r in history(puuid, path) if r.get("points") is not None]
