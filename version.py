"""Your version of a comp, against everyone else's version of the same comp.

Run:  python version.py                 every comp you have enough games in
      python version.py dark star       one comp in detail
      python version.py --field         how the field builds it, ignoring you

"Your Dark Star is a bottom-3 version" is a score, not a lesson. This file
answers the next question instead: WHICH measurable thing is different about
the way you build it, and does that thing move placement.

Two questions per metric, and a finding needs both:

  DIFFERS - your boards in this comp against the field's boards in the same
            comp. Same comp on both sides, so this is not "you play a bad
            comp", it is "you build this comp differently".
  MATTERS - inside the comp, split the FIELD at the median of that metric
            and compare placement. The field only, never your own boards,
            or the effect would be estimated from the same games it is then
            used to judge.

A metric that differs but does not matter is worth printing anyway - it is
a real fact about how you play - but it is not a leak, and the report says
which it is.

SURVIVAL IS HELD FIXED, INCLUDING ON THE METRIC
-----------------------------------------------
Everything here reads place_residual, never placement. That is index.py's
stored column: placement minus the average placement of everyone who died on
the same round. The metrics get the same treatment via tft.survival_residuals
before being compared, because "you hold more items than the field" is
otherwise just "you survived longer than the field" - a board that dies on
round 24 cannot have collected anything.

The raw-placement version of this report was built first and it inverts.
Measured on this pool, the two readings disagree in SIGN on nearly every
case that clears either bar:

    Meeple, Corki carry     residual -0.41    placement +1.90
    Dark Star, Fast 8       residual -0.17    placement +1.32
    N.O.V.A., Lvl 7 Reroll  residual +0.02    placement -1.75

The reason is that a carry on a final board is not a free early choice. It
is the unit you ENDED UP with - miss the one you wanted and you finish on
something else - so carry-at-death is partly an outcome, and reading it on
raw placement measures who survived. The residual keeps 15% of the variance
in placement (sd 0.88 against 2.29) precisely because it has thrown the
survival away, and that 15% is the part board shape is allowed to explain.

WHAT THIS COSTS IN POWER
------------------------
Every metric in one comp is one more chance to get lucky, so the bar is
raised to alpha/k over the whole family tested - see comps.bonferroni_z.
At ~15 tests that is z=3.0, and most real-looking gaps do not survive it.
That is the intended behaviour and the report prints the near-misses with
their numbers rather than going quiet, because "nothing cleared" and "there
is nothing here" are different claims.
"""

import statistics
import sys
from collections import Counter

import comps
import index
import tftdata as tft

# Shape metrics, as stored by index.board_row. `level` is deliberately absent:
# it equals board_size on 88% of boards (93% inside a comp), so testing both
# double-counts one fact and widens the correction for nothing.
SHAPE = {
    "avg_cost":    "average unit cost",
    "big_units":   "units at 4+ gold",
    "board_size":  "units on the board",
    "items_held":  "items on the board",
    "carry_items": "items on the carry",
    "carry_tier":  "carry star level",
    "lines_built": "traits committed",
    "rerolled":    "cheap units 3-starred",
}
CHOICE = {"carry": "carry", "plan": "plan"}

MIN_MINE = 8     # your games in a comp before it is worth a section at all
MIN_CELL = 8     # either side of any split needs this many boards
READABLE = 30    # tft's own bar for "actionable"; under it, say so


def load(db, set_num, puuid):
    """Every board in the set, with each shape metric residualised on round.

    The residual baseline is the whole set, not the comp, for the same reason
    index.py computes place_residual set-wide: round groups inside one comp
    get thin enough that a board ends up compared against itself. Any
    comp-level offset cancels anyway, because both sides of every comparison
    below are the same comp.
    """
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM boards WHERE set_num=? AND place_residual IS NOT NULL",
        (set_num,))]
    for key in SHAPE:
        have = [r for r in rows if r[key] is not None]
        if not have:
            continue
        res = tft.survival_residuals([(r["last_round"], r[key]) for r in have])
        for row, value in zip(have, res):
            row[key + "_res"] = value
    for r in rows:
        r["_mine"] = r["puuid"] == puuid
    return rows


def testable(pool, mine, field):
    """Which metrics this comp can actually be asked about.

    A metric with one value across the whole comp is not a finding waiting
    for more data, it is a definition - "Five Cost" boards commit zero traits
    because that is what the name means. Reporting +0.00 +/-0.00 for it would
    be a hardcoded conclusion dressed as a measurement, and it would inflate
    the correction for every real test beside it.
    """
    shape = [k for k in sorted(SHAPE)
             if len({r[k] for r in pool if r[k] is not None}) > 1
             and sum(1 for r in mine if r.get(k + "_res") is not None) >= 2]
    levels = {}
    for col in sorted(CHOICE):
        seen = Counter(r[col] for r in field if r[col])
        named = sum(seen.values())
        keep = sorted(k for k, n in seen.items()
                      if n >= MIN_CELL and named - n >= MIN_CELL)
        # With exactly two levels the two tests are the same test: "Fast 8 vs
        # the rest" IS "Fast 9 vs the rest" with the sign flipped. Printing
        # both says one thing twice and charges the correction for two.
        levels[col] = keep[:1] if len(keep) == 2 else keep
    return shape, levels


def shape_finding(key, mine, field, z):
    """One numeric metric: do you differ, and does it move placement."""
    a = [r[key + "_res"] for r in mine if r.get(key + "_res") is not None]
    b = [r[key + "_res"] for r in field if r.get(key + "_res") is not None]
    differs = tft.mean_gap(a, b, z=z)
    if not differs:
        return None
    vals = [r[key + "_res"] for r in field if r.get(key + "_res") is not None]
    cut = statistics.median(vals)
    hi = [r["place_residual"] for r in field
          if r.get(key + "_res") is not None and r[key + "_res"] > cut]
    lo = [r["place_residual"] for r in field
          if r.get(key + "_res") is not None and r[key + "_res"] <= cut]
    matters = (tft.mean_gap(hi, lo, z=z)
               if len(hi) >= MIN_CELL and len(lo) >= MIN_CELL else None)
    raw_you = statistics.mean([r[key] for r in mine if r[key] is not None])
    raw_them = statistics.mean([r[key] for r in field if r[key] is not None])
    return {"kind": "shape", "key": key, "label": SHAPE[key],
            "you": raw_you, "them": raw_them,
            "differs": differs, "matters": matters,
            # How far past its own bar the difference sits. Ranking on this
            # rather than on the raw gap keeps metrics on different units
            # (gold, unit counts, item counts) comparable in one list.
            "strength": abs(differs["gap"]) / (differs["moe"] or 1e9)}


def choice_finding(col, level, mine, field, z):
    """One categorical level - a carry, a plan - against the rest of the comp."""
    with_ = [r["place_residual"] for r in field if r[col] == level]
    without = [r["place_residual"] for r in field if r[col] and r[col] != level]
    effect = tft.mean_gap(with_, without, z=z)
    if not effect:
        return None
    played = [r for r in mine if r[col]]
    yours = sum(1 for r in played if r[col] == level)
    share_them = len(with_) / sum(1 for r in field if r[col])
    return {"kind": "choice", "key": col, "label": CHOICE[col], "level": level,
            "n": len(with_), "yours": yours, "played": len(played),
            "share_them": share_them, "effect": effect,
            "strength": abs(effect["gap"]) / (effect["moe"] or 1e9)}


def study(rows, identity):
    """Everything measurable about how you build one comp. Never returns None."""
    pool = [r for r in rows if r["identity"] == identity]
    mine = [r for r in pool if r["_mine"]]
    field = [r for r in pool if not r["_mine"]]
    if len(mine) < MIN_MINE or len(field) < MIN_CELL * 2:
        return None
    shape, levels = testable(pool, mine, field)
    family = len(shape) + sum(len(v) for v in levels.values())
    z = comps.bonferroni_z(family)

    found = []
    for key in shape:
        got = shape_finding(key, mine, field, z)
        if got:
            found.append(got)
    for col in sorted(levels):
        for level in levels[col]:
            got = choice_finding(col, level, mine, field, z)
            if got:
                found.append(got)
    # Sort ties on the name: these lists are built by iterating dict keys and
    # two findings on the same strength would otherwise swap places between
    # runs on identical data - see the note in README.
    found.sort(key=lambda f: (-f["strength"], f.get("level") or f["label"]))
    return {"identity": identity, "mine": len(mine), "field": len(field),
            "family": family, "z": z, "found": found}


def real_shape(f):
    """A shape metric that both differs and moves placement."""
    return (f["kind"] == "shape" and f["differs"]["real"]
            and f["matters"] and f["matters"]["real"])


def real_choice(f):
    """A carry or plan whose effect is real AND that you actually play."""
    return f["kind"] == "choice" and f["effect"]["real"] and f["yours"] > 0


def describe(f):
    """One finding as a line of English with its numbers attached."""
    if f["kind"] == "shape":
        d = f["differs"]
        way = "more" if d["gap"] > 0 else "fewer"
        if f["key"] in ("avg_cost", "carry_tier"):
            way = "higher" if d["gap"] > 0 else "lower"
        line = (f"{f['label']}: you {f['you']:.2f}, the field {f['them']:.2f} "
                f"({way}, {d['gap']:+.2f} +/-{d['moe']:.2f})")
        m = f["matters"]
        if m and m["real"]:
            # matters["gap"] is high-minus-low on place_residual, and a LOWER
            # residual is a better placement - so a negative gap means the
            # high side of the metric is the good side. Getting this backwards
            # prints the advice inverted, which is worse than printing nothing.
            good_side = "more" if m["gap"] < 0 else "fewer"
            yours = "more" if d["gap"] > 0 else "fewer"
            verdict = ("and that is the better side"
                       if yours == good_side else "and that is the worse side")
            line += (f" - {verdict}, by {abs(m['gap']):.2f} "
                     f"+/-{m['moe']:.2f} places")
        elif m:
            line += (f" - but it does not move placement here "
                     f"({m['gap']:+.2f} +/-{m['moe']:.2f})")
        return line
    e = f["effect"]
    cost = "costs" if e["gap"] > 0 else "is worth"
    return (f"{f['level']} {f['label']}: {cost} {abs(e['gap']):.2f} "
            f"+/-{e['moe']:.2f} places in this comp - you play it "
            f"{f['yours']}/{f['played']}, the field {f['share_them']*100:.0f}%")


def report(s):
    """Print one comp. Says what the numbers are even when none of them clear."""
    print(f"\n  {s['identity']}   your {s['mine']} boards vs the field's "
          f"{s['field']}")
    print(f"    {s['family']} things tested, so a claim needs z={s['z']:.2f}"
          + ("" if s["mine"] >= READABLE else
             f"   ({s['mine']} games is under the {READABLE} that makes a "
             f"number actionable - treat all of this as a hint)"))

    real = [f for f in s["found"] if real_shape(f) or real_choice(f)]
    if real:
        print("\n    Clears the bar:")
        for f in real:
            print(f"      - {describe(f)}")

    rest = [f for f in s["found"] if f not in real]
    differs = [f for f in rest
               if f["kind"] == "shape" and f["differs"]["real"]]
    if differs:
        print("\n    Different, but not shown to cost you:")
        for f in differs:
            print(f"      - {describe(f)}")

    shown = {id(f) for f in real} | {id(f) for f in differs}
    near = [f for f in s["found"] if id(f) not in shown][:3]
    if near and not real:
        print("\n    Nothing else cleared. The biggest gaps, for the record:")
    elif near:
        print("\n    Next closest:")
    for f in near:
        print(f"      - {describe(f)}")


def comps_for(rows, puuid):
    """Comps you have enough games in, most played first, ties on the name."""
    count = {}
    for r in rows:
        if r["_mine"] and r["identity"]:
            count[r["identity"]] = count.get(r["identity"], 0) + 1
    return [c for c, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
            if n >= MIN_MINE]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db = index.ensure()
    set_num = db.execute(
        "SELECT MAX(set_num) s FROM boards").fetchone()["s"]
    puuid = tft.default_puuid()
    rows = load(db, set_num, puuid)
    mine = sum(1 for r in rows if r["_mine"])

    print(f"\nYOUR VERSION - set {set_num}, {mine} of your boards "
          f"against {len(rows) - mine} others")
    print("Survival held fixed on both the outcome and the metric; a gap "
          "must clear\nthe bar for the whole family tested in its comp.")

    wanted = comps_for(rows, puuid)
    if args:
        want = " ".join(args).lower()
        wanted = [c for c in wanted if want in c.lower()]
        if not wanted:
            print(f"\n  No comp matching {' '.join(args)!r} with "
                  f"{MIN_MINE}+ of your games.")
            print("  You have enough games in: "
                  + ", ".join(comps_for(rows, puuid)))
            return

    if not wanted:
        print(f"\n  No comp has {MIN_MINE}+ of your games yet. That is the "
              f"finding, not an error -")
        print("  this report needs a comp you have repeated before it can "
              "compare your version of it.")
        return

    for identity in wanted:
        s = study(rows, identity)
        if s:
            report(s)
    print()


if __name__ == "__main__":
    main()
