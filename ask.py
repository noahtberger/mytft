"""Pre-game companion: ask about a comp and get your own record with it.

  python ask.py                      interactive
  python ask.py stargazer boar       one-shot

Type a question in plain English - "thinking about going stargazer boar" -
and it works out which traits, units and constellations you mentioned, then
reports how you have actually done with them.

Everything here is your own match history, computed before the game starts.
It deliberately says nothing about a game in progress: Riot's third-party
policy forbids advice that reacts to live game state.
"""

import difflib
import re
import sys

import tftdata as tft

MIN_CONFIDENT = 30    # games needed before a number is worth acting on
MIN_SHOW      = 3     # below this we won't report a slice at all
RECENT_WINDOW = 50    # "recent" means within your last N games

STOPWORDS = {
    "the", "and", "for", "with", "any", "you", "your", "this", "that", "what",
    "how", "are", "was", "should", "would", "could", "can", "get", "got",
    "going", "gonna", "play", "playing", "played", "think", "thinking", "game",
    "games", "hey", "suggestions", "suggestion", "tips", "about", "into",
    "good", "bad", "best", "worst", "record", "stats", "when", "have", "been",
}


def normalize(text):
    """'N.O.V.A.' and 'nova' collapse to the same key."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def build_vocab(set_number):
    """Map every searchable spelling to (kind, canonical name, api_id)."""
    vocab = {}

    def add(key, kind, name, api_id=None):
        key = normalize(key)
        if len(key) >= 3:
            vocab.setdefault(key, (kind, name, api_id))

    prefix = f"TFT{set_number}_"
    for api_id, name in tft.NAMES.get("traits", {}).items():
        if not api_id.startswith(prefix) or tft.variant_of(api_id)[0]:
            continue
        add(name, "trait", name, api_id)

    for api_id, name in tft.NAMES.get("units", {}).items():
        if not api_id.startswith(prefix):
            continue
        add(name, "unit", name, api_id)
        # "Nunu & Willump" should also answer to "nunu"
        first = re.split(r"[ &']", name)[0]
        add(first, "unit", name, api_id)

    for api_id, name in tft.NAMES.get("variants", {}).items():
        if not api_id.startswith(prefix):
            continue
        add(name, "variant", name, api_id)
        add(name.replace("The ", ""), "variant", name, api_id)

    return vocab


def resolve(query, vocab):
    """Find the traits/units/constellations mentioned in a sentence."""
    words = [w for w in re.split(r"[^A-Za-z0-9'&.]+", query) if w]
    found, used = [], set()

    # Try longer phrases first so "space groove" beats "space".
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            if any(j in used for j in range(i, i + size)):
                continue
            phrase = normalize(" ".join(words[i:i + size]))
            if not phrase or phrase in STOPWORDS:
                continue

            hit = vocab.get(phrase)
            if not hit and size == 1 and len(phrase) >= 4:
                # Tolerate typos, but only on a whole word.
                close = difflib.get_close_matches(phrase, vocab, n=1, cutoff=0.85)
                hit = vocab[close[0]] if close else None

            if hit and hit not in found:
                found.append(hit)
                used.update(range(i, i + size))

    return found


def matches(game, entity):
    """Did this game involve the thing the user asked about?"""
    kind, name, api_id = entity
    if kind == "trait":
        return name in tft.active_traits(game)
    if kind == "unit":
        return name in tft.board_units(game)
    # A constellation only counts if it was actually activated, not merely dealt.
    return any(t["name"] == api_id and t["tier_current"] > 0 for t in game["traits"])


def seen_count(games, entity):
    """For constellations: how often it was offered, played or not."""
    _, _, api_id = entity
    return sum(1 for g in games if any(t["name"] == api_id for t in g["traits"]))


def compare(subset_placements, all_placements):
    """Compare games WITH something against your games without it.

    Comparing a line to your overall average understates the gap, because the
    overall average already contains that line. Contrasting it with the games
    that lacked it is both the fairer test and the more useful number: it
    answers "how do I do when I play this versus when I don't".
    """
    n = len(subset_placements)
    rest_n = len(all_placements) - n
    if n == 0 or rest_n == 0:
        return None

    avg = sum(subset_placements) / n
    rest_avg = (sum(all_placements) - sum(subset_placements)) / rest_n
    # Two-sample margin of error; both groups contribute uncertainty.
    moe = 1.96 * tft.PLACEMENT_SD * (1 / n + 1 / rest_n) ** 0.5
    return {"avg": avg, "rest_avg": rest_avg, "diff": avg - rest_avg,
            "moe": moe, "real": abs(avg - rest_avg) > moe}


def report(label, subset, games, level_par, baseline, note=""):
    """Print one slice with its confidence attached."""
    if len(subset) < MIN_SHOW:
        print(f"  {label}: only {len(subset)} games - too few to say anything")
        return

    s = tft.summarise([g["placement"] for g in subset])
    delta = sum(tft.level_delta(g, level_par) for g in subset) / len(subset)
    recent = sum(1 for g in subset if g in games[:RECENT_WINDOW])
    c = compare([g["placement"] for g in subset], [g["placement"] for g in games])

    print(f"\n{label} - {s['games']} games{note}")
    print(f"  avg {s['avg']:.2f}  |  top4 {s['top4']:.0f}%  |  firsts {s['wins']}"
          f"  |  level vs lobby {delta:+.2f}")
    print(f"  {recent} of them in your last {RECENT_WINDOW} games")

    # No games without it means nothing to compare against. Early in a set a
    # cheap frontline trait really is active in every game you have, so this
    # is the normal state for a week, not a freak case.
    if c is None:
        print(f"  You have no games WITHOUT it, so there is nothing to compare")
        print(f"  against - this number can't be judged until that changes.")
        return

    print(f"  without it you average {c['rest_avg']:.2f}")

    if s["games"] < MIN_CONFIDENT:
        print(f"  LOW CONFIDENCE - {s['games']} games, need ~{MIN_CONFIDENT}.")
    if not c["real"]:
        print(f"  -> gap of {c['diff']:+.2f} is inside the +/-{c['moe']:.2f} "
              f"margin of error. No real difference yet.")
    elif c["diff"] < 0:
        print(f"  -> {abs(c['diff']):.2f} places BETTER than without it. "
              f"A genuine strength.")
    else:
        print(f"  -> {c['diff']:.2f} places WORSE than without it. A genuine leak.")


def answer(query, games, level_par, vocab, baseline):
    entities = resolve(query, vocab)
    if not entities:
        print("\nI couldn't find a trait, unit or constellation in that.")
        print("Try: 'stargazer boar', 'nunu carry', 'marauder', or 'best' / 'traps'.")
        return

    named = ", ".join(f"{n} ({k})" for k, n, _ in entities)
    print(f"\nReading that as: {named}")

    # Say "carry" and we slice on games where the unit held the items, rather
    # than every game it merely stood on the board.
    wants_carry = "carry" in query.lower() or "carrying" in query.lower()

    # Each thing on its own first - those samples are the biggest.
    for e in entities:
        subset = [g for g in games if matches(g, e)]
        note = ""
        if e[0] == "variant":
            offered = seen_count(games, e)
            rate = 100 * len(subset) / offered if offered else 0
            note = f" (dealt it {offered} times, committed {rate:.0f}%)"
        report(e[1], subset, games, level_par, baseline, note)

        if wants_carry and e[0] == "unit":
            as_carry = [g for g in games if tft.carry_of(g) == e[1]]
            report(f"{e[1]} as your CARRY", as_carry, games, level_par, baseline,
                   " (held the most items)")

    # Then the combination, if they asked about more than one.
    if len(entities) > 1:
        both = [g for g in games if all(matches(g, e) for e in entities)]
        report(" + ".join(n for _, n, _ in entities), both, games, level_par, baseline)


def table(title, groups, all_placements, worst=False):
    """List only lines whose difference survives the margin of error.

    A "traps" list that includes lines you do fine on is worse than no list,
    so anything statistically indistinguishable is left out entirely.
    """
    rows = []
    for name, placements in groups.items():
        if len(placements) < MIN_CONFIDENT:
            continue
        c = compare(placements, all_placements)
        if c is None:      # in every game - nothing to compare it against
            continue
        if c["real"] and ((c["diff"] > 0) == worst):
            rows.append((name, len(placements), c))

    # Name breaks ties. It matters more here than it looks: the list is cut
    # to six, so without a tiebreak two lines on the same gap can swap which
    # one is shown at all from one run to the next (these groups are built by
    # iterating sets of strings, whose order changes per process).
    rows.sort(key=lambda r: (-r[2]["diff"] if worst else r[2]["diff"], r[0]))
    print(f"\n{title}  ({MIN_CONFIDENT}+ games, gap bigger than the error bars)")
    for name, n, c in rows[:6]:
        print(f"  {name:<20} {n:>4} games   avg {c['avg']:.2f}   "
              f"vs {c['rest_avg']:.2f} without it   ({c['diff']:+.2f})")
    if not rows:
        print("  nothing here clears the bar - no real difference from the rest")
        print("  of your games. That is a real answer, not a missing one.")
    elif any(n > len(all_placements) * 0.4 for _, n, _ in rows):
        # A trait you have in most games is partly a symptom of a working
        # board, not only a cause of one - you rarely have a full frontline
        # in games where you died early. Treat those gaps as inflated.
        print("  note: comps you play in most games are partly a marker of")
        print("  games that were already going well - read those gaps as a")
        print("  ceiling, not a measurement.")


def main():
    games, level_par = tft.load_games()
    if not games:
        sys.exit("No matches found. Run fetch_matches.py first.")

    all_placements = [g["placement"] for g in games]
    baseline = tft.summarise(all_placements)
    vocab = build_vocab(games[0]["_set"])
    unique = tft.unique_traits(games[0]["_set"])

    by_trait, by_carry = {}, {}
    for g in games:
        for name in tft.active_traits(g):
            if name not in unique:   # unique traits just restate a champion
                by_trait.setdefault(name, []).append(g["placement"])
        carry = tft.carry_of(g)
        if carry:
            by_carry.setdefault(carry, []).append(g["placement"])

    patches = sorted({g["_patch"] for g in games[:RECENT_WINDOW]})
    print("=" * 60)
    print(f"  TFT COMPANION - {baseline['games']} ranked games, set {games[0]['_set']}")
    print(f"  Your baseline: avg {baseline['avg']:.2f}, top4 {baseline['top4']:.0f}%")
    if len(patches) > 1:
        print(f"  Heads up: your last {RECENT_WINDOW} games span patches "
              f"{', '.join(patches)} - the meta changed inside this window.")
    print("=" * 60)

    def run(query):
        q = query.strip().lower()
        if q in ("best", "strengths"):
            table("YOUR BEST COMPS", by_trait, all_placements)
            table("YOUR BEST CARRIES", by_carry, all_placements)
        elif q in ("traps", "worst", "weaknesses"):
            table("YOUR TRAPS", by_trait, all_placements, worst=True)
            table("CARRIES THAT LET YOU DOWN", by_carry, all_placements, worst=True)
        elif q in ("help", "?"):
            print("\n  Ask about any comp:  'thinking about stargazer boar'")
            print("  Commands: best | traps | help | quit")
        else:
            answer(query, games, level_par, vocab, baseline)

    if len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]))
        return

    print("\nAsk me about a line, or type 'best', 'traps', 'help', 'quit'.")
    while True:
        try:
            query = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.strip().lower() in ("quit", "exit", "q"):
            break
        if query.strip():
            run(query)


if __name__ == "__main__":
    main()
