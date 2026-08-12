"""Your version of a comp, against everyone else's version of the same comp.

Run:  python version.py                 every comp you have enough games in
      python version.py dark star       one comp in detail

"Your Dark Star is a bottom-3 version" is a score, not a lesson. This file
answers the next question instead: WHICH measurable thing is different about
the way you build it, and does that thing move placement.

Two tests per metric, and a finding needs both:

  DIFFERS - your boards against the field's boards in the SAME comp, so this
            is never "you play a bad comp", it is "you build this one
            differently".
  MATTERS - split the field at the median of that metric and compare
            placement. The field only, never your own boards, or the effect
            would be estimated from the same games it is then used to judge.

A metric that differs and does not matter still prints, marked as such. It is
a real fact about how you play; it is just not a leak.

IT ASKS EACH BOARD ONLY ITS OWN QUESTIONS
-----------------------------------------
The first version tested everything on every board - all nine shape metrics,
every carry in the comp, every plan. That is a lot of chances to get lucky, so
the bar had to rise to z~2.95 to stay honest, and at that height nothing ever
cleared. The card said "nothing about your version clears the bar" on all of
them, which was true and useless.

So the questions now come from what the board IS. A reroll board is asked
whether it hit the 3-star; a fast 8 board is asked whether its 4-costs are
2-star; a board with a vertical is asked whether it finished the vertical. And
it tests the carry you actually played rather than all seven in the comp,
because a card about one board has no business asking about a carry that board
never had.

Fewer swings, so a lower bar is safe. Measured over 250 shuffled seasons of
this player's own games - labels and placements randomised, so anything that
fires is a false positive:

    scan everything    ~15 tests   z~2.95   fires on 6.4% of noise
    ask this board     ~7 tests    z=2.69   fires on 2.1% of noise

The looser-looking design is the more trustworthy one, because the correction
was never the thing keeping it honest - the number of attempts was. 2.1% is
where the rest of the app sits: plan_record was tuned to 2.0%, PLAYSTYLE_Z to
2-5%.

The mapping from plan to questions is FIXED BELOW, before anything is
measured. Choosing metrics because they came out significant is how a card
learns to always find something.

SURVIVAL IS HELD FIXED, INCLUDING ON THE METRIC
-----------------------------------------------
Everything here reads place_residual, never placement - index.py's stored
column, placement minus the average placement of everyone who died on the same
round. The metrics get the same treatment via tft.survival_residuals before
being compared, because "you hold more items than the field" is otherwise just
"you survived longer than the field": a board that dies on round 24 cannot
have collected anything.

The raw-placement version was built first and it inverts. Measured on this
pool, the two readings disagree in SIGN on nearly every case that clears
either bar:

    Meeple, Corki carry     residual -0.41    placement +1.90
    Dark Star, Fast 8       residual -0.17    placement +1.32
    N.O.V.A., Lvl 7 Reroll  residual +0.02    placement -1.75

A carry on a final board is not a free early choice. It is the unit you ENDED
UP with - miss the one you wanted and you finish on something else - so
carry-at-death is partly an outcome, and reading it on raw placement measures
who survived. The residual keeps 15% of the variance in placement (sd 0.88
against 2.29) precisely because it has thrown the survival away, and that 15%
is the part board shape is allowed to explain.
"""

import statistics
import sys
from collections import Counter

import comps
import index
import tftdata as tft

# Every shape metric the index stores, with the words a player would use.
# `level` is deliberately absent: it equals board_size on 88% of boards and
# 93% inside a comp, so testing both states one fact twice.
SHAPE = {
    "avg_cost":    "average unit cost",
    "big_units":   "units at 4+ gold",
    "big_2star":   "big units at 2-star or better",
    "board_size":  "units on the board",
    "items_held":  "items on the board",
    "carry_items": "items on the carry",
    "carry_tier":  "carry star level",
    "lines_built": "traits committed",
    "rerolled":    "cheap units 3-starred",
    "maxed_line":  "traits finished",
    "style_sum":   "trait value on the board",
    "top_cost":    "priciest unit",
    "unitemised":  "units holding no items",
}

# What each kind of board is worth being asked about. Keyed off comps' own
# constants rather than the strings, so renaming a plan there cannot silently
# empty a row here.
_REROLL_EARLY = ["rerolled", "unitemised", "board_size"]
BY_PLAN = {
    comps.REROLL_PLAN[1]: _REROLL_EARLY,           # Lvl 4/5 Reroll
    comps.REROLL_PLAN[2]: _REROLL_EARLY,           # Lvl 6 Reroll
    comps.REROLL_PLAN[3]: ["rerolled", "big_2star", "board_size"],
    comps.FAST8:          ["big_2star", "avg_cost", "board_size"],
    # top_cost is deliberately not asked of a fast 9 board: essentially every
    # one of them fields a 5-cost, so it measures nothing (z=0.5 across 2,215
    # boards) and spends a test that board_size uses better.
    comps.FAST9:          ["big_2star", "avg_cost", "board_size"],
    None:                 ["board_size", "avg_cost", "big_units"],
}
# Only asked of a board whose identity IS a trait - "did you finish it" is not
# a question you can put to a flat Five Cost board or a pure reroll carry.
VERTICAL = ["maxed_line", "style_sum"]

MIN_MINE = 8     # your boards in a comp before it is worth a card at all
MIN_CELL = 8     # either side of any split needs this many boards
READABLE = 30    # tft's own bar for "actionable"; under it, say so


def load(db, set_num, puuid):
    """Every board in the set, with each shape metric residualised on round.

    The residual baseline is the whole set, not the comp, for the same reason
    index.py computes place_residual set-wide: round groups inside one comp get
    thin enough that a board ends up compared against itself. Any comp-level
    offset cancels anyway, because both sides of every comparison below are
    the same comp.
    """
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM boards WHERE set_num=? AND place_residual IS NOT NULL",
        (set_num,))]
    for key in SHAPE:
        have = [r for r in rows if r.get(key) is not None]
        if not have:
            continue
        res = tft.survival_residuals([(r["last_round"], r[key]) for r in have])
        for row, value in zip(have, res):
            row[key + "_res"] = value
    for r in rows:
        r["_mine"] = r["puuid"] == puuid
    return rows


def questions(plan, identity, set_number):
    """Which shape metrics a board of this kind is about.

    Fixed by the plan, decided before any of it is measured. The vertical
    questions are added only when the comp's identity is a trait, which is
    what tells "Dark Star" (a vertical someone can finish) apart from "Five
    Cost" (a flat board with no vertical to finish) and from a reroll comp
    named for its carry.
    """
    picked = list(BY_PLAN.get(plan, BY_PLAN[None]))
    try:
        verticals = set(tft.set_traits(set_number))
    except Exception:                                    # noqa: BLE001
        verticals = set()
    if identity in verticals:
        picked += VERTICAL
    return list(dict.fromkeys(picked))


def live(pool, mine, keys):
    """Drop metrics this comp cannot be asked about.

    A metric with one value across the whole comp is not a finding waiting for
    more data, it is a definition - "Five Cost" boards commit zero traits
    because that is what the name means. Reporting +0.00 +/-0.00 for it would
    be a hardcoded conclusion dressed as a measurement, and it would inflate
    the correction for every real test beside it.
    """
    return [k for k in keys
            if len({r[k] for r in pool if r.get(k) is not None}) > 1
            and sum(1 for r in mine if r.get(k + "_res") is not None) >= 2]


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
            # rather than the raw gap keeps metrics on different units (gold,
            # unit counts, item counts) comparable in one list.
            "strength": abs(differs["gap"]) / (differs["moe"] or 1e9)}


def choice_finding(col, level, mine, field, z):
    """One carry or plan - the one this board played - against the rest."""
    with_ = [r["place_residual"] for r in field if r[col] == level]
    without = [r["place_residual"] for r in field if r[col] and r[col] != level]
    if len(with_) < MIN_CELL or len(without) < MIN_CELL:
        return None
    effect = tft.mean_gap(with_, without, z=z)
    if not effect:
        return None
    played = [r for r in mine if r[col]]
    yours = sum(1 for r in played if r[col] == level)
    share_them = len(with_) / sum(1 for r in field if r[col])
    return {"kind": "choice", "key": col, "label": col, "level": level,
            "n": len(with_), "yours": yours, "played": len(played),
            "share_them": share_them, "effect": effect,
            "strength": abs(effect["gap"]) / (effect["moe"] or 1e9)}


def study(rows, identity, plan=None, carry=None):
    """How you build one comp, asked the questions that comp's boards are about.

    `plan` and `carry` describe ONE board - the game just played - and make
    this a card about that board. Left out, it falls back to your most-played
    plan and carry in the comp, which is the same card about your typical
    board there.

    Returns None only when there is nothing honest to compare: too few of your
    boards, or too small a field. Never returns an empty finding list for a
    comp it accepted.
    """
    pool = [r for r in rows if r["identity"] == identity]
    mine = [r for r in pool if r["_mine"]]
    field = [r for r in pool if not r["_mine"]]
    if len(mine) < MIN_MINE or len(field) < MIN_CELL * 2:
        return None

    set_number = pool[0]["set_num"]
    if plan is None:
        plan = _most_played(mine, "plan")
    if carry is None:
        carry = _most_played(mine, "carry")

    keys = live(pool, mine, questions(plan, identity, set_number))
    choices = [(col, val) for col, val in (("carry", carry), ("plan", plan))
               if val]
    family = len(keys) + len(choices)
    z = comps.bonferroni_z(family)

    found = []
    for key in keys:
        got = shape_finding(key, mine, field, z)
        if got:
            found.append(got)
    for col, val in choices:
        got = choice_finding(col, val, mine, field, z)
        if got:
            found.append(got)
    # Sort ties on the name: these lists are built by iterating dict keys and
    # two findings on the same strength would otherwise swap places between
    # runs on identical data - see the note in README.
    found.sort(key=lambda f: (-f["strength"], f.get("level") or f["label"]))
    return {"identity": identity, "plan": plan, "carry": carry,
            "mine": len(mine), "field": len(field),
            "family": family, "z": z, "found": found}


def _most_played(mine, col):
    """Your commonest value for a column, ties broken on the name."""
    counted = Counter(r[col] for r in mine if r[col])
    if not counted:
        return None
    return sorted(counted.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


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
        if f["key"] in ("avg_cost", "carry_tier", "top_cost", "style_sum"):
            way = "higher" if d["gap"] > 0 else "lower"
        # The raw means and the gap can point opposite ways, and that is not a
        # bug: the means are what everyone actually fielded, the gap holds the
        # death round fixed. Surviving longer earns you more of most of these,
        # so a player can average MORE of something overall while running
        # FEWER of it than the boards that lasted as long. Printing "you 3.70,
        # the field 3.49 (fewer)" side by side reads as broken, so the gap
        # says out loud which comparison it is.
        line = (f"{f['label']}: you {f['you']:.2f}, the field {f['them']:.2f}"
                f" - among boards that lasted as long, {way} by "
                f"{abs(d['gap']):.2f} +/-{d['moe']:.2f}")
        m = f["matters"]
        if m and m["real"]:
            # matters["gap"] is high-minus-low on place_residual, and a LOWER
            # residual is a better placement - so a negative gap means the
            # high side of the metric is the good side. Getting this backwards
            # prints the advice inverted, which is worse than printing none.
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
    return (f"{f['level']}: {cost} {abs(e['gap']):.2f} +/-{e['moe']:.2f} "
            f"places in this comp - you play it {f['yours']}/{f['played']}, "
            f"the field {f['share_them'] * 100:.0f}%")


# One sentence per metric, as (you have less, you have more). The card shows
# the survival-adjusted difference and NOT the two raw averages, which looks
# like the smaller claim and is the only honest one: measured over the 326
# findings this page would show, the raw averages point the opposite way to
# the adjusted gap 40% of the time. "You 3.70, the field 3.49 (fewer)" is not
# a typo when that happens - surviving longer earns you more of most of these,
# so you can average more of something overall while running less of it than
# the boards that lasted as long - but there is no way to show both numbers at
# a glance without it reading as broken.
PHRASE = {
    "avg_cost":    ("Your board is about {n} gold cheaper per unit.",
                    "Your board is about {n} gold pricier per unit."),
    "big_units":   ("You field about {n} fewer units at 4+ gold.",
                    "You field about {n} more units at 4+ gold."),
    "big_2star":   ("About {n} fewer of your big units reach 2-star.",
                    "About {n} more of your big units reach 2-star."),
    "board_size":  ("You finish with about {n} fewer units on the board.",
                    "You finish with about {n} more units on the board."),
    "items_held":  ("You finish with about {n} fewer items.",
                    "You finish with about {n} more items."),
    "carry_items": ("Your carry holds about {n} fewer items.",
                    "Your carry holds about {n} more items."),
    "carry_tier":  ("Your carry is usually a lower star level.",
                    "Your carry is usually a higher star level."),
    "lines_built": ("You commit to about {n} fewer traits.",
                    "You commit to about {n} more traits."),
    "rerolled":    ("You 3-star about {n} fewer cheap units.",
                    "You 3-star about {n} more cheap units."),
    "maxed_line":  ("You finish about {n} fewer traits than they do.",
                    "You finish about {n} more traits than they do."),
    "style_sum":   ("Your board picks up about {n} less trait value.",
                    "Your board picks up about {n} more trait value."),
    "top_cost":    ("Your priciest unit is usually cheaper than theirs.",
                    "Your priciest unit is usually pricier than theirs."),
    "unitemised":  ("You leave about {n} fewer units without items.",
                    "You leave about {n} more units without items."),
}


def worth_saying(f):
    """Is this difference big enough to earn a line on the card?

    A gap that rounds to 0.0 at one decimal is not a small finding, it is no
    finding: "about 0.0 fewer units on the board" is noise wearing a sentence,
    and three of those in a row is how a card teaches someone to stop reading
    it. A carry or plan has to have a measured effect for the same reason -
    "you run it 7 of 19, the field 31%" is true and says nothing.
    """
    if f["kind"] == "choice":
        return f["effect"]["real"]
    return abs(f["differs"]["gap"]) >= 0.05


def plain(f):
    """One finding as a sentence somebody can read at a glance.

    No error bars, no z, no talk of bars being cleared. The statistics decide
    WHICH findings reach this function; they are not the reader's problem once
    they have. What survives is the difference and, when it is solid enough to
    claim, what it costs.
    """
    if f["kind"] == "choice":
        e = f["effect"]
        share = (f"you run it in {f['yours']} of your {f['played']}, "
                 f"the field in {round(f['share_them'] * 100)}%")
        if e["real"] and e["gap"] > 0:
            return (f"{f['level']} is costing you about {abs(e['gap']):.1f} "
                    f"of a place here - {share}.")
        if e["real"]:
            return (f"{f['level']} is working here, worth about "
                    f"{abs(e['gap']):.1f} of a place - {share}.")
        return f"{f['level']}: {share}."

    gap = f["differs"]["gap"]
    lower, higher = PHRASE[f["key"]]
    text = (higher if gap > 0 else lower).format(n=f"{abs(gap):.1f}")
    m = f["matters"]
    if m and m["real"]:
        # matters["gap"] is high-minus-low on place_residual and a LOWER
        # residual is a better placement, so a negative gap means the high
        # side is the good side. Backwards here prints inverted advice.
        good = "more" if m["gap"] < 0 else "fewer"
        mine = "more" if gap > 0 else "fewer"
        text += (" That is the better side of it."
                 if mine == good else
                 f" Going the other way is worth about "
                 f"{abs(m['gap']):.1f} of a place.")
    return text


def report(s):
    """Print one comp. Says what the numbers are even when none of them clear."""
    asked = f"{s['plan']} / {s['carry']}" if s["plan"] else str(s["carry"])
    print(f"\n  {s['identity']}   your {s['mine']} boards vs the field's "
          f"{s['field']}")
    print(f"    asked as: {asked}")
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
    count = Counter(r["identity"] for r in rows if r["_mine"] and r["identity"])
    return [c for c, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
            if n >= MIN_MINE]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db = index.ensure()
    set_num = db.execute("SELECT MAX(set_num) s FROM boards").fetchone()["s"]
    puuid = tft.default_puuid()
    rows = load(db, set_num, puuid)
    mine = sum(1 for r in rows if r["_mine"])

    print(f"\nYOUR VERSION - set {set_num}, {mine} of your boards "
          f"against {len(rows) - mine} others")
    print("Each comp is asked the questions its own boards are about, so the\n"
          "list is short on purpose. Survival held fixed on both sides.")

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
