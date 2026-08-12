"""One flat table of every board on disk, so item questions are askable.

Run:  python index.py            build or top up
      python index.py --rebuild  throw it away and start again
      python index.py --stats    what's in there

WHY THIS EXISTS
---------------
The match files are a filing cabinet: a file per match, eight boards inside
each, units inside each board, items inside each unit. Every report opens all
of them and walks that tree from the top. That is fine at 513 files and 53ms,
and it is the wrong shape for the item work, which wants to slice the same
4,000 boards twenty different ways in a row - "this item, on this carry, among
boards that held three items and died on the same round". Each of those slices
currently re-reads and re-parses everything.

This flattens it once into two tables: one row per BOARD, one row per
ITEM-ON-A-UNIT. Those are the two things item questions are actually about.

THE SURVIVAL RESIDUAL IS STORED, NOT COMPUTED LATER
---------------------------------------------------
`place_residual` is placement minus the average placement of everyone who
died on the same round. It is here because forgetting that control is the
single most repeated mistake in this project - four item "findings" looked
solid last week and three evaporated when survival was held fixed, two of
them flipping sign. Baking it into a column means the honest comparison is
AVG(place_residual) and the naive one is AVG(placement): the correct query
is now shorter than the wrong one.

Use `placement` when survival IS the outcome - a comp's win rate, contested
versus not. Use `place_residual` for anything about board SHAPE. That is the
same rule as tft.survival_residuals, which this column is computed with.

IT IS A CACHE, NEVER THE SOURCE OF TRUTH
----------------------------------------
matches/ stays authoritative: it is the only copy of games Riot has deleted,
and it is why this repo exists. This file is gitignored and disposable. If
you cannot delete it and rebuild with one command, something is wrong. Any
change to how comps.parts() or tft.playstyle() classify a board makes every
derived column stale, so MODEL_VERSION below is stamped into the file and a
mismatch forces a full rebuild rather than a silent lie.
"""

import json
import sqlite3
import sys
from pathlib import Path

import comps
import tftdata as tft

DB_PATH = Path(__file__).with_name("boards.db")

# Bump when comps.parts(), tft.playstyle() or the columns below change what
# a row MEANS. A stamp mismatch triggers a rebuild instead of mixing two
# models in one table.
MODEL_VERSION = "4"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stamp (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS boards (
    match_id       TEXT NOT NULL,
    puuid          TEXT NOT NULL,
    set_num        INTEGER,
    patch          TEXT,
    played_at      INTEGER,
    name           TEXT,
    placement      INTEGER,
    place_residual REAL,
    last_round     INTEGER,
    stage          TEXT,
    level          INTEGER,
    gold_left      INTEGER,
    plan           TEXT,
    identity       TEXT,
    comp           TEXT,
    carry          TEXT,
    avg_cost       REAL,
    big_units      INTEGER,
    rerolled       INTEGER,
    lines_built    INTEGER,
    board_size     INTEGER,
    items_held     INTEGER,
    carry_items    INTEGER,
    carry_tier     INTEGER,
    PRIMARY KEY (match_id, puuid)
);

CREATE TABLE IF NOT EXISTS board_items (
    match_id  TEXT NOT NULL,
    puuid     TEXT NOT NULL,
    unit      TEXT,
    unit_cost INTEGER,
    unit_tier INTEGER,
    item      TEXT,
    on_carry  INTEGER
);

CREATE INDEX IF NOT EXISTS boards_set    ON boards(set_num, patch);
CREATE INDEX IF NOT EXISTS boards_carry  ON boards(set_num, carry);
CREATE INDEX IF NOT EXISTS boards_puuid  ON boards(puuid, set_num);
CREATE INDEX IF NOT EXISTS items_item    ON board_items(item);
CREATE INDEX IF NOT EXISTS items_board   ON board_items(match_id, puuid);
CREATE INDEX IF NOT EXISTS items_carry   ON board_items(on_carry, item);
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def stamp_of(db):
    try:
        row = db.execute("SELECT value FROM stamp WHERE key='model'").fetchone()
    except sqlite3.OperationalError:
        return None
    return row["value"] if row else None


def board_row(match_id, info, board, residual):
    """One board, flattened. Returns (row, item_rows)."""
    plan, identity = comps.parts(board, info["set_num"]) or (None, None)
    prof = tft.playstyle(board, info["set_num"])
    carry = tft.carry_of(board)
    units = tft.shop_units(board)

    items, carry_items, carry_tier = [], 0, None
    for u in units:
        unit = tft.display(u["character_id"], "units")
        cost = tft.UNIT_COST[u["rarity"]]
        held = u.get("itemNames") or []
        if unit == carry:
            carry_items = len(held)
            carry_tier = u["tier"]
        for it in held:
            items.append((match_id, board["puuid"], unit, cost, u["tier"],
                          it, int(unit == carry)))

    name = board.get("riotIdGameName") or ""
    tag = board.get("riotIdTagline") or ""
    row = (
        match_id, board["puuid"], info["set_num"], info["patch"],
        info["played_at"], f"{name}#{tag}" if name else None,
        board["placement"], residual, board["last_round"],
        tft.stage(board["last_round"]), board["level"],
        board.get("gold_left", 0),
        plan, identity, comps.name_from((plan, identity)), carry,
        prof["avg_cost"] if prof else None,
        prof["big_units"] if prof else None,
        prof["rerolled"] if prof else None,
        prof["lines_built"] if prof else None,
        prof["board_size"] if prof else None,
        sum(len(u.get("itemNames") or []) for u in units),
        carry_items, carry_tier,
    )
    return row, items


def read_match(path):
    """(match_id, info, boards) for one file, or None if it isn't usable."""
    import re
    raw = json.loads(path.read_text())
    info = raw.get("info") or {}
    boards = info.get("participants") or []
    if not boards:
        return None
    found = re.search(r"(\d+\.\d+)", info.get("game_version") or "")
    return (raw["metadata"]["match_id"],
            {"set_num": info.get("tft_set_number"),
             "patch": found.group(1) if found else None,
             "played_at": info.get("game_datetime")},
            boards)


def build(db, only_new=True, progress=None):
    """Fill the tables from every match file. Returns (matches, boards)."""
    db.executescript(SCHEMA)
    have = set()
    if only_new:
        have = {r["match_id"] for r in
                db.execute("SELECT DISTINCT match_id FROM boards")}

    # The survival residual needs every board in a set at once - it is
    # "against everyone who died on the same round" - so read first, write
    # after. At this size that is a few MB and far simpler than trying to
    # keep a running mean correct as rows arrive.
    pending, per_set = [], {}
    for path in tft.match_files():
        got = read_match(path)
        if got is None:
            continue
        match_id, info, boards = got
        if match_id in have:
            continue
        have.add(match_id)
        pending.append((match_id, info, boards))
        # Only boards that will actually get a row feed the per-round means.
        # Counting disconnects here and not below left the stored residual
        # disagreeing with tft.survival_residuals by up to 0.011 - harmless
        # in size, but this column exists to be the canonical answer.
        per_set.setdefault(info["set_num"], []).extend(
            (b["last_round"], b["placement"])
            for b in boards if tft.shop_units(b))

    if not pending:
        return 0, 0

    # Residuals are per set: rounds mean different things across sets.
    means = {}
    for set_num, entries in per_set.items():
        by_round = {}
        for rnd, place in entries:
            by_round.setdefault(rnd, []).append(place)
        means[set_num] = {r: sum(v) / len(v) for r, v in by_round.items()}

    rows, item_rows = [], []
    for i, (match_id, info, boards) in enumerate(pending):
        for b in boards:
            if not tft.shop_units(b):
                continue          # a disconnect has no board to describe
            base = means[info["set_num"]].get(b["last_round"])
            residual = b["placement"] - base if base is not None else None
            row, items = board_row(match_id, info, b, residual)
            rows.append(row)
            item_rows.extend(items)
        if progress and i % 200 == 0:
            progress(i, len(pending))

    db.executemany(
        f"INSERT OR REPLACE INTO boards VALUES ({','.join('?' * 24)})", rows)
    db.executemany(
        "INSERT INTO board_items VALUES (?,?,?,?,?,?,?)", item_rows)
    db.execute("INSERT OR REPLACE INTO stamp VALUES ('model', ?)",
               (MODEL_VERSION,))
    db.commit()
    return len(pending), len(rows)


def ensure(path=DB_PATH, quiet=True):
    """Open the index, building or rebuilding it if it needs it.

    Callers get a usable connection or an exception - never a half-built or
    stale-model database.
    """
    fresh = not path.exists()
    db = connect(path)
    if not fresh and stamp_of(db) != MODEL_VERSION:
        db.close()
        path.unlink()
        db = connect(path)
        fresh = True
        if not quiet:
            print("  model changed - rebuilding from scratch")
    build(db, only_new=not fresh)
    return db


def show_stats(db):
    b = db.execute("SELECT COUNT(*) n FROM boards").fetchone()["n"]
    i = db.execute("SELECT COUNT(*) n FROM board_items").fetchone()["n"]
    m = db.execute("SELECT COUNT(DISTINCT match_id) n FROM boards").fetchone()["n"]
    print(f"  {m} matches, {b} boards, {i} items")
    print(f"  file: {DB_PATH.name}, {DB_PATH.stat().st_size / 1e6:.1f} MB")
    print("\n  boards per set and patch:")
    for r in db.execute("SELECT set_num, patch, COUNT(*) n FROM boards "
                        "GROUP BY set_num, patch ORDER BY set_num, patch"):
        print(f"    set {r['set_num']}  patch {r['patch']}  {r['n']}")


def main():
    args = sys.argv[1:]
    if "--rebuild" in args and DB_PATH.exists():
        DB_PATH.unlink()
        print("  dropped the old index")
    fresh = not DB_PATH.exists()
    db = connect()
    if not fresh and stamp_of(db) != MODEL_VERSION:
        db.close()
        DB_PATH.unlink()
        db = connect()
        fresh = True
        print("  model version changed - rebuilding from scratch")

    def progress(i, total):
        print(f"    {i}/{total} matches...", end="\r")

    matches, boards = build(db, only_new=not fresh, progress=progress)
    print(f"  indexed {matches} new matches, {boards} boards" if matches
          else "  already up to date")
    if "--stats" in args or matches:
        show_stats(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
