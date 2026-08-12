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
             or fast 8 (a board built on 2-star 4-costs), or fast 9
             (the legendaries), or neither.

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
from statistics import NormalDist

import tftdata as tft

REROLL_PLAN = {1: "Lvl 4/5 Reroll", 2: "Lvl 6 Reroll", 3: "Lvl 7 Reroll"}
FAST9 = "Fast 9"
FAST9_COST = 3.5      # average unit cost at/above this reads as fast 9
FAST9_BIG = 6         # ...or this many 4/5-costs
FAST8 = "Fast 8"
FAST8_BIG = 4         # 4/5-costs that say "built on 4-costs", under fast 9
FIVECOST_UNIQUES = 3  # uniques online for a flat board to be "five cost"

# Boards the meta needs before it stops reaching back through patches. Eight
# to a game, so this is about 25 games - enough for a handful of comps to
# clear the 10-board display bar rather than a card that says nothing.
MIN_META_BOARDS = 200

NO_COMP = "Never came together"
NO_PLAN = "No clear plan"

# The plan axis as bare strings, in one place. Comp lists filter rows that
# are a plan with no identity attached - "Fast 8" alone is a leftover bucket,
# not a comp anyone can go and play - and that set used to be rebuilt inline
# at every site, so adding a plan silently broke one of them. Derive from
# these rather than writing another literal: the next plan tier should be a
# one-line change here.
REROLL_NAMES = frozenset(REROLL_PLAN.values())
LEVEL_NAMES = frozenset({FAST9, FAST8})
PLAN_NAMES = REROLL_NAMES | LEVEL_NAMES

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
        # Fast 8: a board built around 4-costs rather than legendaries. It
        # 3-stars nothing cheap (that would be reroll) and it does not clear
        # the fast 9 bar, because a board of 2-star 4-costs averages about
        # 3.2 gold - which is exactly why this used to fall out of the model
        # entirely and land in "no plan".
        #
        # It is named for the plan, not for where the player stopped: 62% of
        # this bucket finished on level 9 or 10. The rule reads composition,
        # never level, because level-at-death cannot tell "parked at 8 on
        # purpose" from "died before reaching 9".
        #
        # Fast 9 is tested first, so no board that already had a PLAN gets a
        # new one. Boards that had only an identity do change name - Dark
        # Star becomes Fast 8 Dark Star - which splits samples; best_variants
        # is what stops that emptying the meta list.
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
    elif plan in LEVEL_NAMES and (
            uniq_online >= FIVECOST_UNIQUES or big >= 5):
        # Tests the BOARD, not the label. Gating this on `plan == FAST9`
        # meant that once a tier existed below fast 9, a tenth of a gold in
        # average cost decided whether a flat legendary board got a name or
        # fell into the bare-plan bucket that both comp lists filter out.
        identity = "Five Cost"
    elif plan and threes:
        identity = tft.carry_of(board)
    else:
        identity = None
    return plan, identity


def name_from(p):
    """The comp name for an already-computed parts() result."""
    if p is None:
        return None
    plan, identity = p
    if plan and identity:
        # Only reroll plans get the "(7)" suffix form. Testing `== FAST9` here
        # instead sent every Fast 8 board down the reroll branch, where
        # "Fast 8".split(" Reroll")[0] renders "Brawler Reroll (Fast 8)".
        if plan not in REROLL_NAMES:
            return f"{plan} {identity}"
        return f"{identity} Reroll ({plan.split(' Reroll')[0].removeprefix('Lvl ')})"
    if identity:
        return identity
    if plan:
        return plan
    return NO_COMP


def name(board, set_number):
    """The comp as a player would say it, or NO_COMP."""
    return name_from(parts(board, set_number))


def plan_of(board, set_number):
    p = parts(board, set_number)
    return p[0] if p else None


VARIANT_MIN = 10   # boards a variant needs before it can represent its comp


def best_variants(rows, min_boards=VARIANT_MIN):
    """One row per comp: whichever way of playing it does better.

    Adding a plan tier splits every comp in two - "Dark Star" became
    "Dark Star" and "Fast 8 Dark Star" - which halves each sample and,
    measured on this pool, pushed seven comps under the display bar and out
    of the meta list entirely. The user's call on how to resolve it: show the
    better one. "If fast 8 Dark Star is better than Dark Star in general,
    just put fast 8 Dark Star only. If there's a fast 8 N.O.V.A. that
    significantly underperforms regular N.O.V.A., don't include fast 8."

    Better means the same thing it means everywhere else on that card: the
    worst the average could plausibly be given the sample (avg + moe), so a
    lucky handful of boards cannot win the row.

    A variant needs `min_boards` to speak for the comp at all. When no
    variant clears that on its own the comp is pooled back into one row under
    its plain identity, which is what it was called before plans could split
    it - a comp must never vanish just because the model got finer.
    """
    groups = defaultdict(list)
    singles = []
    for r in rows:
        if r.get("identity"):
            groups[r["identity"]].append(r)
        else:
            singles.append(r)          # bare plans, NO_COMP - nothing to group

    out = list(singles)
    for identity, variants in groups.items():
        if len(variants) == 1:
            out.append(variants[0])
            continue
        ranked = [v for v in variants if v["n"] >= min_boards]
        if ranked:
            out.append(min(ranked, key=lambda v: v["avg"] + v["moe"]))
            continue
        # Nothing thick enough alone: pool the variants back together rather
        # than let the comp disappear.
        places = [p for v in variants for p in v["places"]]
        carries = Counter()
        for v in variants:
            carries.update(v["carries"])
        n = len(places)
        out.append({
            "comp": identity, "n": n, "identity": identity,
            "avg": statistics.mean(places),
            "top4": 100 * sum(1 for p in places if p <= 4) / n,
            "moe": tft.placement_moe(n),
            "confidence": tft.confidence(n),
            "carries": list(carries)[:3],
            "places": places,
        })
    return out


def meta(set_number=None):
    """Every comp across every board on disk, with outcome stats.

    This is an OUTCOME table - which comps end games well - so it uses raw
    placements on purpose. Survival is part of winning here, unlike the
    board-shape comparisons, which must control for it.

    Uses the whole pool: your games, your opponents', and anything seeded
    from the ladder. That is the point - 196 of your games is a diet, the
    3,000+ boards on disk are a meal, and on day one of a set the seeded
    boards are the only meal there is.

    Newest patch first, and only as far back as it has to go. Patches shift
    the meta - a 1.8-place swing was measured between patches in this very
    dataset - so blending them reports an average of two different games.

    But "newest patch only" alone had a failure that showed up the first
    time this ran on a fresh clone. The newest patch on disk is whatever is
    newest, not whatever is BIG: a clone holding 1,568 of the owner's boards
    across patches 16.8-16.14 reported a 104-board meta, and then pressing
    Seed made it worse rather than better - the ladder games arrive on the
    current live patch, newer than anything the owner has played, so the
    filter threw away 1,568 boards to keep 24 and every comp fell under the
    display bar. Seeding is supposed to be the fix for a thin meta, and it
    was making it thinner.

    So: take the newest patch, and if it is under MIN_META_BOARDS keep
    reaching back a patch at a time until there is enough to say anything.
    The card names every patch it used, because a two-patch read is a
    different claim from a one-patch read and the page must not imply
    otherwise.
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

    def patch_key(name):
        return tuple(int(n) for n in name.split("."))

    in_set = [i for i in files if (i.get("tft_set_number") or 0) == chosen]
    counts = Counter(p for i in in_set if (p := patch_of(i)))
    ordered = sorted(counts, key=patch_key, reverse=True)

    used, running = [], 0
    for name in ordered:
        used.append(name)
        running += counts[name] * 8      # boards, not matches
        if running >= MIN_META_BOARDS:
            break
    span = sorted(used, key=patch_key, reverse=True)
    used = set(span)
    # "16.15" when one patch was enough, "16.14 and 16.13" when it had to
    # reach back. The page says "patch" or "patches" off len(span).
    newest = (span[0] if len(span) == 1 else
              (" and ".join([", ".join(span[:-1]), span[-1]])
               if span else None))

    by_comp = defaultdict(lambda: {"places": [], "carries": Counter(),
                                   "identity": None})
    by_plan = defaultdict(list)
    boards = 0

    for info in in_set:
        if used and patch_of(info) not in used:
            continue
        for b in info["participants"]:
            # One parts() per board, reused for the name, the plan and the
            # identity - it walks the trait list and rebuilds the unique-trait
            # set, so calling it three times per board was most of this loop.
            p = parts(b, chosen)
            comp = name_from(p)
            if comp is None:
                continue
            plan, identity = p
            boards += 1
            entry = by_comp[comp]
            entry["places"].append(b["placement"])
            entry["identity"] = identity
            carry = tft.carry_of(b)
            if carry:
                entry["carries"][carry] += 1
            if plan:
                by_plan[plan].append(b["placement"])

    rows = []
    for comp, d in by_comp.items():
        n = len(d["places"])
        rows.append({
            "comp": comp, "n": n, "identity": d["identity"],
            "avg": statistics.mean(d["places"]),
            "top4": 100 * sum(1 for p in d["places"] if p <= 4) / n,
            "moe": tft.placement_moe(n),
            "confidence": tft.confidence(n),
            "carries": [c for c, _ in d["carries"].most_common(3)],
            "places": d["places"],
        })
    rows = best_variants(rows)
    rows.sort(key=lambda r: (r["avg"], r["comp"]))

    plans = [{"plan": p, "n": len(v), "avg": statistics.mean(v),
              "moe": tft.placement_moe(len(v)),
              "confidence": tft.confidence(len(v))}
             for p, v in by_plan.items()]
    plans.sort(key=lambda r: r["avg"])
    out = {"set": chosen, "patch": newest, "patches": span,
           "boards": boards, "comps": rows, "plans": plans}
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
# A plan has to win outright this much more often than the rest before the
# card will say so. Clearing an error bar is not enough on its own - see
# beats_the_rest. Eight points is roughly "one extra win every twelve games",
# which is the smallest gap worth changing how you play for.
MIN_FIRST_GAP = 0.08


def bonferroni_z(tests):
    """The z a claim must clear when it is one of `tests` being tried.

    Running k tests at the ordinary bar means k chances to get lucky, so the
    bar moves to alpha/k. At k=1 this returns 1.96 and nothing changes; at
    k=4 it is 2.50. Same reasoning as tft.PLAYSTYLE_Z, which was set by
    measuring the wrong-claim rate rather than by argument - see the note in
    plan_record for the measurement behind this one.
    """
    return NormalDist().inv_cdf(1 - 0.025 / max(tests, 1))


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

    Always returns a dict:
      rows    every plan this player has used, most played first
      best    the plan whose first-place rate beats the rest, or None. Carries
              `coarse` when it is the Rerolling/Levelling fallback rather than
              a single plan, since that answers a different question
      spread  how many comps they have played and how concentrated they are
      then/now  their share of `best` before and during the recent stretch,
              or None when there is no fine-grained best to track
    """
    # One parts() per game, shared by the plan split and the spread count.
    # It walks the trait list and rebuilds the unique-trait set, so calling
    # it once per game per use was most of this function's work.
    named = []
    by_plan = defaultdict(list)
    for g in games:
        p = parts(g, set_number)
        if p is None:
            continue
        named.append((p[0] or NO_PLAN, p[1]))
        by_plan[p[0] or NO_PLAN].append(g["placement"])

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

    def beats_the_rest(label, places, rest, z=1.96):
        """Does `places` win outright more often than the player's other
        games - by more than the error bar, AND by enough to be worth saying?

        Two tests, not one. The error bar says the gap is real; MIN_FIRST_GAP
        says it is big enough to act on. This project has already been bitten
        by running only the first: a 0.04 difference in item concentration
        cleared its error bar at 114 games and generated advice, which is why
        postgame.MIN_EFFECT exists. More data makes trivia significant.
        """
        if not places or not rest:
            return None
        p1, _ = _first_rate(places)
        p2, _ = _first_rate(rest)
        n1, n2 = len(places), len(rest)
        se = (p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) ** 0.5
        if not se or p1 - p2 <= z * se or p1 - p2 < MIN_FIRST_GAP:
            return None
        return {"plan": label, "n": n1, "first": p1, "others": p2,
                "gap": p1 - p2, "gap_moe": z * se,
                "tier": tft.confidence(n1),
                # Every non-coarse `best` is a row spread with these keys;
                # the coarse one is built from scratch, so it carries them
                # too rather than being a differently-shaped dict.
                "avg": statistics.mean(places),
                "moe": tft.placement_moe(n1),
                "first_moe": 1.96 * (p1 * (1 - p1) / n1) ** 0.5,
                "top4": sum(1 for x in places if x <= 4) / n1}

    # The best plan for winning, judged against the player's OTHER games -
    # not against the second-best plan, which would be a comparison the
    # player never actually faces. They are choosing between "this plan" and
    # "how I otherwise play".
    #
    # Every eligible plan is tested, at a threshold raised for the number of
    # tests. The first version tested only the highest first-rate plan and
    # broke, on the theory that one test needs no correction - but selecting
    # the maximum IS the comparison, so it bought nothing while costing
    # coverage. Measured over 3000 shuffles of this player's own games, with
    # placements shuffled so no plan can truly differ:
    #
    #     test the top-rate plan only, at 1.96   6.9% false positives
    #     test every plan, at 1.96               7.0%
    #     test every plan, Bonferroni            1.4%
    #
    # Identical false-positive rate, and the old way could miss a real
    # finding on a bigger plan because a small noisy one outranked it. The
    # correction costs power (75% vs 89% detection on a strong effect), which
    # is the same trade PLAYSTYLE_Z=3.2 already makes: this app's job is to
    # not name things it cannot back up, and the findings that matter here
    # clear the higher bar anyway.
    ranked = [r for r in rows if r["plan"] != NO_PLAN
              and r["n"] >= MIN_PLAN_GAMES]
    z = bonferroni_z(len(ranked))
    found = []
    for row in ranked:
        others = [p for plan, places in by_plan.items() if plan != row["plan"]
                  for p in places]
        hit = beats_the_rest(row["plan"], by_plan[row["plan"]], others, z=z)
        if hit:
            found.append({**row, **hit})
    # More than one can survive; report the biggest edge.
    best = max(found, key=lambda r: r["gap"]) if found else None

    # Nothing separated? Try the coarse split. A player who plays a different
    # comp every game never gets 15 games on any single plan, so the test
    # above can never fire for them and the card would read "no plan has
    # pulled ahead" forever. Rolling for 3-stars versus levelling to the big
    # units is the one division every player is on one side of, and it
    # doubles the sample. It is a weaker claim, so it is marked `coarse` and
    # the page says which question it answered.
    if best is None:
        reroll = [p for plan, places in by_plan.items()
                  if plan in REROLL_NAMES for p in places]
        levelling = [p for plan, places in by_plan.items()
                     if plan in LEVEL_NAMES for p in places]
        # The baseline is the OTHER side plus the games with no plan at all,
        # so it stays the same "everything else you did" the fine-grained
        # test uses. Leaving no-plan games out of both sides made the coarse
        # claim easier to fire than the finer one it stands in for.
        noplan = by_plan.get(NO_PLAN, [])
        # This pair is reached only after the fine tests failed, so by the
        # time it runs the player has had len(ranked) + 2 chances at a
        # verdict, and the bar has to account for all of them. Correcting
        # only the fine tests left this branch as the main way a shuffled
        # season still produced a headline: 6.7% of nulls fired, and 81 of
        # every 100 of those came from here.
        coarse_z = bonferroni_z(len(ranked) + 2)
        for label, side, other in (("Rerolling", reroll, levelling),
                                   ("Levelling", levelling, reroll)):
            rest = other + noplan
            # BOTH sides need a real sample. Guarding only `side` let a
            # verdict be declared against one or two games, where the normal
            # approximation is invalid and the variance collapses to zero
            # whenever the baseline happens to be 0% or 100%.
            if len(side) < MIN_PLAN_GAMES or len(rest) < MIN_PLAN_GAMES:
                continue
            found = beats_the_rest(label, side, rest, z=coarse_z)
            if found:
                best = {**found, "coarse": True}
                break

    # How widely they spread. Counting distinct comps would mostly count how
    # many games are on disk (r=0.71 across the players here); the share of
    # games on the single most played comp does not (r=+0.11).
    ids = Counter(i for _, i in named if i)
    spread = None
    if ids:
        # Denominator is games PLAYED, not games that produced an identity -
        # the page calls this number "games", and a board with no identity is
        # still a game they played.
        spread = {"comps": len(ids), "games": len(games),
                  "top": ids.most_common(1)[0][0],
                  "share": ids.most_common(1)[0][1] / len(games)}

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
            if family in r["comp"]:
                fit = r
                break
    share = next((pct for p, _, pct in mix if p == top_plan), 0)
    return {"best": best, "fit": fit, "top_plan": top_plan, "share": share}
