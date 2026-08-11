"""What comp a board played, named the way a player would say it.

The old definition - "committed" means a trait past its second breakpoint -
called the user's best board "no comp". A fast-9 board of five-costs never
takes anything past its first breakpoint on purpose; its identity is the
unique traits it assembled, not a vertical it declined to build.

The shape of this model came from clustering 3,182 real boards on shape
alone (no placement input). The clusters that fell out were: reroll with a
vertical, fast 8 with a vertical, fast 9 five-cost, pure reroll, an
in-between bucket, and died-early. Those map onto two axes a player would
recognise instantly, so the app ships the axes as rules rather than the
clustering itself - rules are stable, explainable, and work on day one of
a new set when there is nothing to cluster.

  PLAN     - how the gold was spent. Reroll (split by the cost tier you
             3-starred, which is the fingerprint of WHERE you rerolled:
             1-costs = lvl 4/5 reroll, 2-costs = lvl 6, 3-costs = lvl 7),
             or fast 8 (rolled at 8, board of 2-star 4-costs), or fast 9
             (the expensive board), or neither.

  IDENTITY - what the board was. The deepest vertical if there is one;
             "Five Cost" for the flat legendary board; the carry for a
             pure reroll board.

A board that shows neither a plan nor an identity usually just died early
- that is an outcome, not a comp, and it is named honestly.

Fast 8 shipped late. The clustering above named it from the start, but the
first rules collapsed it into fast 9 and "no plan", because a board of
2-star 4-costs averages about 3.2 gold and misses the fast 9 bar while
3-starring nothing cheap. That cost the model a real population: of the
boards filed as "no plan", 147 were still alive at stage 6 on level 8.9
with 4.2 big units, averaging 2.93 - finished boards, not games that ended
early. The user spotted the gap from the game side ("fast8 is a thing too -
dark star this set, ionia last set") before the data was asked.
"""

import json
import re
import statistics
from collections import Counter, defaultdict

import tftdata as tft

REROLL_PLAN = {1: "Lvl 4/5 Reroll", 2: "Lvl 6 Reroll", 3: "Lvl 7 Reroll"}
FAST9 = "Fast 9"
FAST9_COST = 3.5      # average unit cost at/above this reads as fast 9
FAST9_BIG = 6         # ...or this many 4/5-costs
FAST8 = "Fast 8"
FAST8_BIG = 4         # 4/5-costs that say "rolled at 8", below the fast 9 bar
FIVECOST_UNIQUES = 3  # uniques online for a flat board to be "five cost"

NO_COMP = "Never came together"
NO_PLAN = "No clear plan"

# The plan axis as bare strings. Two places filter comp lists with this to
# drop rows that are a plan with no identity attached - "Fast 8" alone is a
# leftover bucket, not a comp anyone can go and play - and both used to
# rebuild the set inline, so adding a plan silently broke one of them.
PLAN_NAMES = set(REROLL_PLAN.values()) | {FAST9, FAST8}

_META_CACHE = {}


def parts(board, set_number):
    """(plan, identity) for one final board, either possibly None."""
    units = tft.shop_units(board)
    if not units:
        return None
    costs = [tft.UNIT_COST[u["rarity"]] for u in units]
    threes = [c for c, u in zip(costs, units) if c <= 3 and u["tier"] >= 3]
    big = sum(1 for c in costs if c >= 4)
    avg = sum(costs) / len(costs)

    plan = None
    if threes:
        plan = REROLL_PLAN[max(threes)]
    elif avg >= FAST9_COST or big >= FAST9_BIG:
        plan = FAST9
    elif big >= FAST8_BIG:
        # Fast 8: rolled at 8 and hit, so the board is 2-star 4-costs. It
        # 3-stars nothing cheap (that would be reroll) and it does not clear
        # the fast 9 bar, because a board of 4-costs averages about 3.2 gold
        # - which is exactly why this used to fall out of the model entirely
        # and land in "no plan". Fast 9 is tested FIRST on purpose: a board
        # with 6+ big units keeps the label it already had, so adding this
        # plan relabels nothing that was previously named.
        plan = FAST8

    # Deepest vertical: most units in a committed, non-unique trait.
    uniques = tft.unique_traits(set_number)
    vertical, depth = None, 0
    for t in board["traits"]:
        name = tft.display(t["name"], "traits")
        if name in uniques or not tft.committed(t):
            continue
        if t.get("num_units", 0) > depth:
            vertical, depth = name, t.get("num_units", 0)

    uniq_online = sum(1 for t in board["traits"]
                      if t["tier_current"] > 0
                      and tft.display(t["name"], "traits") in uniques)

    if vertical:
        identity = vertical
    elif plan == FAST9 and (uniq_online >= FIVECOST_UNIQUES or big >= 5):
        identity = "Five Cost"
    elif plan and threes:
        identity = tft.carry_of(board)
    else:
        identity = None
    return plan, identity


def name(board, set_number):
    """The comp as a player would say it, or NO_COMP."""
    p = parts(board, set_number)
    if p is None:
        return None
    plan, identity = p
    if plan and identity:
        # Only reroll plans get the "(7)" suffix form. Testing `== FAST9` here
        # instead sent every Fast 8 board down the reroll branch, where
        # "Fast 8".split(" Reroll")[0] renders "Brawler Reroll (Fast 8)".
        if plan not in REROLL_PLAN.values():
            return f"{plan} {identity}"
        return f"{identity} Reroll ({plan.split(' Reroll')[0].removeprefix('Lvl ')})"
    if identity:
        return identity
    if plan:
        return plan
    return NO_COMP


def plan_of(board, set_number):
    p = parts(board, set_number)
    return p[0] if p else None


def meta(set_number=None):
    """Every comp across every board on disk, with outcome stats.

    This is an OUTCOME table - which comps end games well - so it uses raw
    placements on purpose. Survival is part of winning here, unlike the
    board-shape comparisons, which must control for it.

    Uses the whole pool: your games, your opponents', and anything seeded
    from the ladder. That is the point - 196 of your games is a diet, the
    3,000+ boards on disk are a meal, and on day one of a set the seeded
    boards are the only meal there is.

    Newest patch only. Patches shift the meta - a 1.8-place swing was
    measured between patches in this very dataset - so blending them
    reports an average of two different games. The cost is a smaller
    sample after every patch day; the seed button refills it.
    """
    files = []
    chosen = set_number
    for path in tft.match_files():
        info = json.loads(path.read_text())["info"]
        files.append(info)
        if set_number is None:
            chosen = max(chosen or 0, info.get("tft_set_number") or 0)

    cache_key = (chosen, len(files))
    if _META_CACHE.get("key") == cache_key:
        return _META_CACHE["value"]

    def patch_of(info):
        found = re.search(r"(\d+\.\d+)", info.get("game_version") or "")
        return found.group(1) if found else None

    in_set = [i for i in files if (i.get("tft_set_number") or 0) == chosen]
    patches = {p for i in in_set if (p := patch_of(i))}
    newest = (max(patches, key=lambda s: tuple(int(n) for n in s.split(".")))
              if patches else None)

    by_comp = defaultdict(lambda: {"places": [], "carries": Counter()})
    by_plan = defaultdict(list)
    boards = 0

    for info in in_set:
        if newest and patch_of(info) != newest:
            continue
        for b in info["participants"]:
            comp = name(b, chosen)
            if comp is None:
                continue
            boards += 1
            by_comp[comp]["places"].append(b["placement"])
            carry = tft.carry_of(b)
            if carry:
                by_comp[comp]["carries"][carry] += 1
            plan = plan_of(b, chosen)
            if plan:
                by_plan[plan].append(b["placement"])

    rows = []
    for comp, d in by_comp.items():
        n = len(d["places"])
        rows.append({
            "comp": comp, "n": n,
            "avg": statistics.mean(d["places"]),
            "top4": 100 * sum(1 for p in d["places"] if p <= 4) / n,
            "moe": tft.placement_moe(n),
            "confidence": tft.confidence(n),
            "carries": [c for c, _ in d["carries"].most_common(3)],
        })
    rows.sort(key=lambda r: (r["avg"], r["comp"]))

    plans = [{"plan": p, "n": len(v), "avg": statistics.mean(v),
              "moe": tft.placement_moe(len(v)),
              "confidence": tft.confidence(len(v))}
             for p, v in by_plan.items()]
    plans.sort(key=lambda r: r["avg"])
    out = {"set": chosen, "patch": newest, "boards": boards,
           "comps": rows, "plans": plans}
    _META_CACHE["key"], _META_CACHE["value"] = cache_key, out
    return out


def plan_mix(games, set_number):
    """How one player splits their games across plans, newest era first."""
    counts = Counter()
    for g in games:
        p = plan_of(g, set_number)
        counts[p or NO_PLAN] += 1
    total = sum(counts.values()) or 1
    return [(plan, n, 100 * n / total) for plan, n in counts.most_common()]


MIN_PLAN_GAMES = 15   # below this a plan's record is too thin to rank
RECENT_GAMES = 20     # "lately" - about a long evening, and it matches MIN_ERA


def _first_rate(placements):
    """Share of games won outright, with its error bar."""
    n = len(placements) or 1
    p = sum(1 for x in placements if x == 1) / n
    return p, 1.96 * (p * (1 - p) / n) ** 0.5


def plan_record(games, set_number, recent=RECENT_GAMES):
    """How this player's own results split across the ways to spend gold.

    Average placement is the wrong number to read here and that is the whole
    point of this function. Over 196 games this player averages 3.95 on Fast 9
    and 3.87 on Lvl 6 Reroll - a gap of 0.08 against an error bar of 1.05, ie.
    nothing. On that number the two plans are interchangeable. They are not:
    28% of his Fast 9 games end in first place against 10% of everything else.
    Same average, completely different game. One plan plays for the win, the
    other plays for top 4, and an average placement cannot tell them apart
    because a 1st and a 4th both count as "good".

    So the headline is the rate of firsts, tested as a proportion against the
    player's other games, and only claimed when it clears its own error bar.

    Returns None when no plan has enough games. Otherwise a dict with `rows`
    (every plan, most played first), `best` (the plan whose first-place rate
    beats the rest, or None), and `then`/`now` shares of that plan, so the
    page can say whether they are still playing the thing that wins.
    """
    by_plan = defaultdict(list)
    for g in games:
        by_plan[plan_of(g, set_number) or NO_PLAN].append(g["placement"])

    rows = []
    for plan, places in by_plan.items():
        rate, moe = _first_rate(places)
        rows.append({"plan": plan, "n": len(places),
                     "avg": statistics.mean(places),
                     "moe": tft.placement_moe(len(places)),
                     "first": rate, "first_moe": moe,
                     "top4": sum(1 for x in places if x <= 4) / len(places),
                     "tier": tft.confidence(len(places))})
    rows.sort(key=lambda r: -r["n"])

    def beats_the_rest(label, places, rest):
        """Does `places` win outright more often than the player's other
        games, by more than the error bar on the difference?"""
        if not places or not rest:
            return None
        p1, _ = _first_rate(places)
        p2, _ = _first_rate(rest)
        n1, n2 = len(places), len(rest)
        se = (p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) ** 0.5
        if not se or p1 - p2 <= 1.96 * se:
            return None
        return {"plan": label, "n": n1, "first": p1, "others": p2,
                "gap": p1 - p2, "gap_moe": 1.96 * se,
                "tier": tft.confidence(n1)}

    # The best plan for winning, judged against the player's OTHER games -
    # not against the second-best plan, which would be a comparison the
    # player never actually faces. They are choosing between "this plan" and
    # "how I otherwise play".
    ranked = [r for r in rows if r["plan"] != NO_PLAN
              and r["n"] >= MIN_PLAN_GAMES]
    best = None
    for row in sorted(ranked, key=lambda r: -r["first"]):
        others = [p for plan, places in by_plan.items() if plan != row["plan"]
                  for p in places]
        found = beats_the_rest(row["plan"], by_plan[row["plan"]], others)
        if found:
            best = {**row, **found}
        break

    # Nothing separated? Try the coarse split. A player who plays a different
    # comp every game never gets 15 games on any single plan, so the test
    # above can never fire for them and the card would read "no plan has
    # pulled ahead" forever. Rolling for 3-stars versus levelling to the big
    # units is the one division every player is on one side of, and it
    # doubles the sample. It is a weaker claim, so it is marked `coarse` and
    # the page says which question it answered.
    if best is None:
        reroll = [p for plan, places in by_plan.items()
                  if plan in REROLL_PLAN.values() for p in places]
        standard = [p for plan, places in by_plan.items()
                    if plan in (FAST9, FAST8) for p in places]
        for label, side, other in (("Rerolling", reroll, standard),
                                   ("Levelling", standard, reroll)):
            if len(side) < MIN_PLAN_GAMES:
                continue
            found = beats_the_rest(label, side, other)
            if found:
                best = {**found, "coarse": True}
                break

    # How widely they spread. Counting distinct comps would mostly count how
    # many games are on disk (r=0.71 across the players here); the share of
    # games on the single most played comp does not (r=+0.11).
    ids = Counter(p[1] for g in games if (p := parts(g, set_number)) and p[1])
    spread = None
    if ids:
        total = sum(ids.values())
        spread = {"comps": len(ids), "games": total,
                  "top": ids.most_common(1)[0][0],
                  "share": ids.most_common(1)[0][1] / total}

    out = {"rows": rows, "best": best, "spread": spread,
           "then": None, "now": None}
    if best and not best.get("coarse") and len(games) > recent:
        # games arrive newest first, so the recent stretch is the head.
        share = lambda gs: sum(                       # noqa: E731
            1 for g in gs if plan_of(g, set_number) == best["plan"]) / len(gs)
        out["now"] = share(games[:recent])
        out["then"] = share(games[recent:])
        out["now_n"] = recent
    return out


def recommend(games, set_number, meta_rows, min_boards=10):
    """Match how this player plays against what is winning in the pool.

    Two lines: the strongest thing in the pool outright, and the strongest
    thing that fits the player's own dominant plan. If they are the same,
    one line. Every claim carries its sample size and confidence - a hunch
    is shown as a hunch, not hidden and not dressed up.
    """
    # A comp needs an identity to be recommended - a bare "Fast 9" or
    # "Fast 8" row is the leftover boards that fit no named comp, not a
    # thing to go play.
    usable = [r for r in meta_rows if r["n"] >= min_boards
              and r["comp"] != NO_COMP and r["comp"] not in PLAN_NAMES]
    if not usable:
        return None
    # Ranked by the worst the average could plausibly be (avg + its error
    # bar), not the raw average. A lucky 12 boards at 3.3 can't outrank a
    # steady forty at 3.4 - and a big pile of mediocre boards can't either,
    # which "prefer the solid sample" got wrong: right after a patch the
    # only solid rows are whatever got played most, not what wins.
    pool = sorted(usable, key=lambda r: r["avg"] + r["moe"])
    best = pool[0]

    mix = plan_mix(games, set_number)
    top_plan = next((p for p, _, _ in mix if p != NO_PLAN), None)

    fit = None
    if top_plan:
        family = "Reroll" if "Reroll" in top_plan else top_plan
        for r in pool:
            if family in r["comp"] or r["comp"].startswith(family):
                fit = r
                break
    share = next((pct for p, _, pct in mix if p == top_plan), 0)
    return {"best": best, "fit": fit, "top_plan": top_plan, "share": share}
