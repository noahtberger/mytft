"""Which items actually earn their slot, and whether you build them.

Run:  python items.py                 the whole pool
      python items.py --mine          only lobbies you were in
      python items.py Nunu            one carry in detail
      python items.py --you           what you build vs what wins
      python items.py --pooled        every item, across all carries

Reads index.py's flat table, because every question here is the same slice
of 4,000 boards asked twenty different ways and walking the JSON tree once
per question was the slow, error-prone way to do it.

THE CONTROL IS THE WHOLE POINT
------------------------------
An item on a winning board is not evidence that the item wins. Boards that
survive longer hold more items, so on a naive top-4-versus-bottom-4 split
EVERY item looks good. Measured here first: Warmog's Armor appeared to cost
0.10-0.17 places across four separate carries - a pattern consistent enough
to look real - and came out at +0.00 (+/-0.12) once survival and item count
were held fixed. Adaptive Helm looked like the best item on Nunu at +0.14
and is +0.06. Three of the first four "findings" evaporated and two flipped
sign.

So every comparison in this file:
  - uses place_residual, not placement, so a board is only ever compared
    with boards that died on the same round;
  - holds the item COUNT fixed, because a 3-item carry has more chances to
    contain any given item than a 2-item one;
  - compares within one CARRY, because items and carries are not
    independent and the good items on a tank are not the good items on a
    backline unit.

What survives all three is a short list. That is the honest answer, not a
failure of the analysis.
"""

import re
import sys

import comps
import index
import tftdata as tft

MIN_BOARDS = 25    # a with/without split thinner than this says nothing
CARRY_MIN = 40     # boards on a carry before its items are worth ranking


def pretty(item_id):
    """'TFT_Item_GuinsoosRageblade' -> 'Guinsoos Rageblade'.

    Item display names are not in names.json - Data Dragon has them and
    fetch_names.py does not pull them yet, so this un-camel-cases the API id.
    Readable, occasionally ugly; when the real names land this is the one
    place to change.
    """
    name = tft.NAMES.get("items", {}).get(item_id)
    if name:
        return name
    stripped = re.sub(r"^TFT\d*_", "", item_id)
    stripped = re.sub(r"Item$|^Item_", "", stripped).replace("_", " ")
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stripped)
    return re.sub(r"\s+", " ", spaced).strip() or item_id


def item_effect(db, item, carry=None, count=3, set_num=None, mine=None):
    """One item's effect on placement, with survival and item count fixed.

    Returns (gap, moe, n_with, n_without) where a NEGATIVE gap is good -
    placement is a golf score. None when either side is too thin to speak.
    """
    where = ["b.set_num = ?", "b.carry_items = ?"]
    args = [set_num, count]
    if carry:
        where.append("b.carry = ?")
        args.append(carry)
    if mine:
        where.append("b.puuid = ?")
        args.append(mine)
    sql = f"""
        SELECT b.place_residual AS r,
               EXISTS (SELECT 1 FROM board_items i
                       WHERE i.match_id = b.match_id AND i.puuid = b.puuid
                         AND i.on_carry = 1 AND i.item = ?) AS has_it
        FROM boards b
        WHERE {' AND '.join(where)} AND b.place_residual IS NOT NULL
    """
    rows = db.execute(sql, [item] + args).fetchall()
    with_it = [r["r"] for r in rows if r["has_it"]]
    without = [r["r"] for r in rows if not r["has_it"]]
    if len(with_it) < MIN_BOARDS or len(without) < MIN_BOARDS:
        return None
    judged = tft.mean_gap(with_it, without)
    if not judged:
        return None
    return judged, len(with_it), len(without)


def carries(db, set_num, mine=None, floor=CARRY_MIN):
    """Carries with enough itemised boards to rank their items."""
    sql = ("SELECT carry, COUNT(*) n FROM boards "
           "WHERE set_num = ? AND carry IS NOT NULL AND carry_items >= 2")
    args = [set_num]
    if mine:
        sql += " AND puuid = ?"
        args.append(mine)
    sql += " GROUP BY carry HAVING n >= ? ORDER BY n DESC"
    args.append(floor)
    return db.execute(sql, args).fetchall()


def items_on(db, carry, set_num, floor=MIN_BOARDS):
    """Every item seen often enough on that carry to be worth testing."""
    return [r["item"] for r in db.execute(
        "SELECT i.item, COUNT(*) n FROM boards b "
        "JOIN board_items i USING (match_id, puuid) "
        "WHERE b.set_num = ? AND b.carry = ? AND i.on_carry = 1 "
        "GROUP BY i.item HAVING n >= ? ORDER BY n DESC",
        (set_num, carry, floor))]


def rank(db, items_list, carry=None, set_num=None, mine=None):
    """Test a list of items and mark only what survives the correction.

    Testing k items at the ordinary bar is k chances to get lucky, and this
    list is long: across 33 items on 4,102 boards, two cleared 1.96 - and
    pure chance predicts 1.7. Both vanish at the corrected bar. Marking them
    "real" would have been the same mistake the plan card made this morning,
    which is why the correction lives here rather than in a comment.
    """
    out, tested = [], []
    for item in items_list:
        got = item_effect(db, item, carry=carry, set_num=set_num, mine=mine)
        if got:
            tested.append((item, got))
    z = comps.bonferroni_z(len(tested))
    for item, (judged, n_with, _) in tested:
        sd = judged["moe"] / 1.96
        out.append({"item": item, "gap": judged["gap"], "moe": judged["moe"],
                    "n": n_with, "real": bool(sd) and abs(judged["gap"]) > z * sd})
    out.sort(key=lambda r: r["gap"])
    return out, z


def print_rows(rows):
    for r in rows:
        label = pretty(r["item"])
        if len(label) > 26:
            label = label[:25] + "\u2026"
        print(f"    {label:<27}{'helps' if r['gap'] < 0 else 'costs'} "
              f"{abs(r['gap']):.2f} (+/-{r['moe']:.2f})  on {r['n']:>4} boards"
              f"{'   <- clears' if r['real'] else ''}")


def report_carry(db, carry, set_num, mine=None):
    """Rank one carry's items, most helpful first."""
    rows, z = rank(db, items_on(db, carry, set_num), carry=carry,
                   set_num=set_num, mine=mine)
    if not rows:
        print(f"\n  {carry}: nothing has {MIN_BOARDS}+ boards both ways yet")
        return
    kept = sum(r["real"] for r in rows)
    print(f"\n  {carry}   ({len(rows)} items tested, bar raised to "
          f"z={z:.2f}, {kept or 'none'} clear{'s' if kept == 1 else ''} it)")
    print_rows(rows)


def report_pooled(db, set_num, mine=None, floor=120):
    """Every item across every carry - the only cut with real sample size.

    Per-carry cells run to a few dozen boards, which cannot separate a
    0.1-place effect from nothing. Pooling gives 120-640 boards an item and
    is the version most likely to find something first.
    """
    where = "b.puuid = ?" if mine else "1=1"
    args = [set_num] + ([mine] if mine else []) + [floor]
    listed = [r["item"] for r in db.execute(
        f"SELECT i.item, COUNT(*) n FROM boards b "
        f"JOIN board_items i USING (match_id, puuid) "
        f"WHERE b.set_num = ? AND i.on_carry = 1 AND b.carry_items = 3 "
        f"AND {where} GROUP BY i.item HAVING n >= ? ORDER BY n DESC", args)]
    rows, z = rank(db, listed, set_num=set_num, mine=mine)
    kept = [r for r in rows if r["real"]]
    print(f"\nACROSS ALL CARRIES   ({len(rows)} items, bar raised to z={z:.2f} "
          f"for testing that many)")
    print_rows(rows)
    print()
    if kept:
        print(f"  {len(kept)} item(s) survive: "
              f"{', '.join(pretty(r['item']) for r in kept)}")
    else:
        loose = sum(1 for r in rows if abs(r["gap"]) > r["moe"])
        print(f"  Nothing survives. {loose} would clear the uncorrected bar, "
              f"and chance alone predicts {0.05 * len(rows):.1f} of them.")
        print("  Item effects are real in the game but smaller than this pool "
              "can measure.\n  The fix is more boards, not more statistics - "
              "seed the pool and come back.")


def report_you(db, puuid, set_num):
    """Do you hold the items that work, on the carries you actually play?"""
    print("\nWHAT YOU BUILD, AGAINST WHAT WORKS")
    print("  (only items that survive the controls are worth matching)\n")
    for row in carries(db, set_num, mine=puuid, floor=8):
        carry = row["carry"]
        # rank() applies the same correction the other reports use. Reading
        # mean_gap's own `real` here instead let this section announce a
        # 0.28-place item on a carry where nothing else came close - one
        # lucky draw out of everything tested, dressed up as advice.
        ranked, _ = rank(db, items_on(db, carry, set_num), carry=carry,
                         set_num=set_num)
        good = [(r["gap"], r["item"]) for r in ranked
                if r["real"] and r["gap"] < 0]
        if not good:
            print(f"  {carry:<20}{row['n']:>3} games   "
                  f"nothing on it clears the bar either way")
            continue
        good.sort()
        rate = {}
        for _, item in good:
            r = db.execute(
                "SELECT AVG(EXISTS (SELECT 1 FROM board_items i "
                " WHERE i.match_id=b.match_id AND i.puuid=b.puuid "
                "   AND i.on_carry=1 AND i.item=?)) s "
                "FROM boards b WHERE b.set_num=? AND b.carry=? AND b.puuid=?",
                (item, set_num, carry, puuid)).fetchone()["s"] or 0
            rate[item] = r
        print(f"  {carry:<20}{row['n']:>3} games")
        for gap, item in good:
            print(f"      {pretty(item):<22}worth {abs(gap):.2f} places   "
                  f"you build it in {100 * rate[item]:.0f}% of them")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    db = index.ensure()
    set_num = db.execute("SELECT MAX(set_num) s FROM boards").fetchone()["s"]
    puuid = tft.default_puuid()
    mine = puuid if "--mine" in flags else None

    total = db.execute("SELECT COUNT(*) n FROM boards WHERE set_num=?",
                       (set_num,)).fetchone()["n"]
    scope = "your lobbies" if mine else "every board on disk"
    print(f"ITEMS - set {set_num}, {total} boards, {scope}")
    print(f"Survival and item count held fixed; a gap needs "
          f"{MIN_BOARDS}+ boards each way.")

    if "--you" in flags:
        report_you(db, puuid, set_num)
        return 0

    if "--pooled" in flags:
        report_pooled(db, set_num, mine=mine)
        return 0

    if args:
        want = args[0].lower()
        found = [r["carry"] for r in carries(db, set_num, floor=1)
                 if want in r["carry"].lower()]
        if not found:
            print(f"\n  No carry matching {args[0]!r}.")
            return 1
        for carry in found[:3]:
            report_carry(db, carry, set_num, mine=mine)
        return 0

    report_pooled(db, set_num, mine=mine)
    print("\n\nPER CARRY  (thinner cells, so the bar is easier to miss)")
    for row in carries(db, set_num, mine=mine):
        report_carry(db, row["carry"], set_num, mine=mine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
