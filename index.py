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
MODEL_VERSION = "8"

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
    -- Added for version.py, which asks a board what KIND of board it is and
    -- then asks only the questions that kind is about. big_2star is the one
    -- that earned its place on measurement: across 2,215 boards it moves
    -- placement harder than any shape column above it, because "four 4-costs"
    -- and "four 2-star 4-costs" are different boards and nothing here could
    -- tell them apart.
    big_2star      INTEGER,
    uniques        INTEGER,
    active_traits  INTEGER,
    top_cost       INTEGER,
    unitemised     INTEGER,
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
    big_2star = unitemised = 0
    top_cost = 0
    for u in units:
        unit = tft.display(u["character_id"], "units")
        cost = tft.UNIT_COST[u["rarity"]]
        held = u.get("itemNames") or []
        if unit == carry:
            carry_items = len(held)
            carry_tier = u["tier"]
        if cost >= 4 and u["tier"] >= 2:
            big_2star += 1
        if not held:
            unitemised += 1
        top_cost = max(top_cost, cost)
        for it in held:
            items.append((match_id, board["puuid"], unit, cost, u["tier"],
                          it, int(unit == carry)))

    # Uniques: traits a single special unit turns on whole (tier_total 1).
    # This column was "maxed_line" - traits at their own last breakpoint -
    # until the user asked what "finished" meant and whether it was useful.
    # Measured: 87% of "finished" traits were uniques finishing by existing,
    # the honest finished-a-real-vertical version carries nothing (z=0.3),
    # and the whole z=3.0 signal was the uniques (r=0.95). The column now
    # counts the thing it was actually measuring, under its true name.
    traits = board.get("traits") or []
    uniques = sum(1 for t in traits
                  if t.get("tier_current") and t.get("tier_total") == 1)
    # Plain count of lit traits, splashes included. This replaced the
    # color-weighted sum (bronze 1 / silver 2 / gold 3 / prismatic 4): the
    # sum screened stronger (z=3.5 against 2.7) but the two are r=0.88 -
    # the same information twice - and the owner twice misread the summed
    # version on the page. A metric that has to be asked about fails at its
    # job regardless of its z.
    active_traits = sum(1 for t in traits if t.get("tier_current"))

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
        big_2star, uniques, active_traits, top_cost, unitemised,
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
    return (raw["metadata"]["match_id"],
            {"set_num": info.get("tft_set_number"),
             "patch": tft.patch_of(info),
             "played_at": info.get("game_datetime")},
            boards)


def build(db, only_new=True, progress=None):
    """Fill the tables from every match file. Returns (matches, boards)."""
    db.executescript(SCHEMA)
    have = set()
    if only_new:
        have = {r["match_id"] for r in
                db.execute("SELECT DISTINCT match_id FROM boards")}

    pending = []
    for path in tft.match_files():
        got = read_match(path)
        if got is None:
            continue
        match_id, info, boards = got
        if match_id in have:
            continue
        have.add(match_id)
        pending.append((match_id, info, boards))

    if not pending:
        return 0, 0

    # Rows go in with a null residual and the whole set is re-derived below.
    # Computing it here from the boards in hand is what the first version did,
    # and it was wrong on exactly the path that matters - see
    # recompute_residuals.
    rows, item_rows, touched = [], [], set()
    for i, (match_id, info, boards) in enumerate(pending):
        for b in boards:
            if not tft.shop_units(b):
                continue          # a disconnect has no board to describe
            row, items = board_row(match_id, info, b, None)
            rows.append(row)
            item_rows.extend(items)
            touched.add(info["set_num"])
        if progress and i % 200 == 0:
            progress(i, len(pending))

    db.executemany(
        f"INSERT OR REPLACE INTO boards VALUES ({','.join('?' * 29)})", rows)
    db.executemany(
        "INSERT INTO board_items VALUES (?,?,?,?,?,?,?)", item_rows)
    recompute_residuals(db, touched)
    db.execute("INSERT OR REPLACE INTO stamp VALUES ('model', ?)",
               (MODEL_VERSION,))
    db.commit()
    return len(pending), len(rows)


def recompute_residuals(db, sets):
    """Re-derive place_residual for a whole set, from every board in it.

    This runs over the entire set on every build, not just the new rows, and
    that is the point. The residual is "against everyone who died on the same
    round", so it is a property of the population, not of one board: adding
    games shifts the round means and every existing row goes slightly stale.

    The first version computed the means from the boards being inserted. On a
    full rebuild that is the whole set and it is correct, which is why it
    tested clean. On a top-up - fetch a few games, run index.py, the ordinary
    daily path - it computed them from the handful of new boards instead.
    Measured on this pool by building once in full and once as 274 matches
    plus a 3-match top-up, all 24 topped-up boards disagreed, by up to 0.94
    places against a column whose whole standard deviation is 0.88, and 5 of
    them came out at exactly 0.00 because their death round appeared once in
    the batch and they were being compared against themselves.

    Day one of a new set is the worst case: the newest set holds one match,
    so every board in it would be scored against the seven people it just
    played. MODEL_VERSION is bumped alongside this so an index built by the
    old code is thrown away rather than half-corrected.
    """
    for set_num in sorted(s for s in sets if s is not None):
        rows = db.execute(
            "SELECT match_id, puuid, last_round, placement FROM boards "
            "WHERE set_num=?", (set_num,)).fetchall()
        if not rows:
            continue
        res = tft.survival_residuals(
            [(r["last_round"], r["placement"]) for r in rows])
        db.executemany(
            "UPDATE boards SET place_residual=? WHERE match_id=? AND puuid=?",
            [(v, r["match_id"], r["puuid"]) for r, v in zip(rows, res)])


def ensure(path=DB_PATH, quiet=True):
    """Open the index, building or rebuilding it if it needs it.

    Callers get a usable connection or an exception - never a half-built or
    stale-model database.
    """
    fresh = not path.exists()
    db = connect(path)
    if not fresh and stamp_of(db) != MODEL_VERSION:
        reset(db)
        fresh = True
        if not quiet:
            print("  model changed - rebuilding from scratch")
    build(db, only_new=not fresh)
    return db


def reset(db):
    """Throw the tables away, without touching the file on disk.

    Deleting boards.db is the obvious way to start over and it fails on
    Windows. serve.py calls ensure() on every post-game render, so the app
    holds this file open, and unlink() raises PermissionError [WinError 32]
    where macOS would quietly succeed. A MODEL_VERSION bump would then break
    the index on exactly the machine being played on - and silently, because
    postgame_page catches the exception and just leaves the card out.

    Dropping the tables needs no exclusive access and works the same on both
    platforms. The file does not shrink, since SQLite keeps the freed pages
    for reuse; that is a cosmetic difference and the next build fills them.
    """
    db.executescript("DROP TABLE IF EXISTS boards;"
                     "DROP TABLE IF EXISTS board_items;"
                     "DROP TABLE IF EXISTS stamp;")
    db.commit()


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
    db = connect()
    fresh = False
    # One copy of the model check, shared with ensure(). Two copies is how the
    # unlink survived here after being fixed there.
    if "--rebuild" in args:
        reset(db)
        fresh = True
        print("  dropped the old index")
    elif stamp_of(db) != MODEL_VERSION:
        reset(db)
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
