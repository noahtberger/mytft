"""Turn crawled matches into arrays the network can train on.

Reads corpus/*.jsonl.gz (and matches/ + pool/, so this works before the
first crawl finishes) and writes corpus/dataset.npz: one row per board,
grouped by the match it came from.

Needs numpy, which arrives with torch anyway. Nothing else in the repo
imports this, so `python stats.py` still runs on a clone with nothing
installed - the promise in the README survives.

## What is deliberately not a feature

Riot's participant record mixes two kinds of thing, and telling them apart
is the whole difference between a model that answers the question and one
that cheats.

Board state is what you built: units, their stars, their items, which trait
breakpoints are lit, what level you reached, what gold you sat on.

Outcome is what happened to it: last_round, time_eliminated,
players_eliminated, total_damage_to_players. Every one of those is a
restatement of the placement. A network given last_round learns "eliminated
on 3-2" -> "eighth" in about four minutes, scores beautifully on the
validation split, and has learned nothing whatsoever about traits. So they
are dropped here rather than in the model, where someone would eventually
add them back to fix a loss curve.

## The confound that cannot be dropped, only conditioned on

This is end-of-game data, so the board *is* partly the outcome: an eighth
place has a stunted board because they died at level 6, not because level 6
is a bad idea. Left alone, the model learns "more units, more stars, more
items = better placement", which is true, circular, and useless.

Level and gold are kept for exactly that reason. Holding them fixed is what
turns the question from "did this board do well" into "among boards that
reached level 8, did this trait help" - which is the question worth asking,
and the only one this data can answer. Every counterfactual the model is
later asked must hold them fixed too, or it is measuring survival again.

Run:  python dataset.py             build from everything on disk
      python dataset.py --status    what the vocabularies look like
"""

import gzip
import json
import sys
from pathlib import Path

import numpy as np

import crawl
import tftdata as tft

CORPUS = Path("corpus")
OUT = CORPUS / "dataset.npz"
VOCAB = CORPUS / "vocab.json"

MAX_UNITS = 12       # level 10 plus a Tactician's Crown, with room to spare
MAX_ITEMS = 3        # Riot's own cap per unit
PAD = 0              # index 0 is "nothing here" in every vocabulary

# Riot's rarity numbers are not contiguous - 0,1,2,4,6 are the 1 through 5
# costs and 9 is whatever the set is calling special this time. Mapped to
# 0-5 so the cost histogram has a fixed width no matter which of them a set
# happens to use.
RARITY = {0: 0, 1: 1, 2: 2, 4: 3, 6: 4, 9: 5}

# The set-invariant features, in order. Every one of these is a fact about
# TFT's rules rather than about Set 17: interest caps at 5 gold, XP costs
# per level, three copies to a 2-star, three item slots, eight players
# sharing one pool. None of it references a champion or a trait by name,
# which is the entire point - a trunk trained on these keeps its meaning on
# the morning Set 18 lands, while the embedding table beside it does not.
INVARIANT = (
    "level", "gold", "units",
    "cost1", "cost2", "cost3", "cost4", "cost5", "cost6",
    "star1", "star2", "star3",
    "items", "max_items_on_one",
    "traits_active", "traits_committed", "best_trait_tier",
    "contested", "board_value",
)
CONTESTED = INVARIANT.index("contested")


class Vocab:
    """Stable id -> index maps, so a rebuild never renumbers the model out.

    Written to disk and only ever extended. Re-crawling after a patch adds
    champions and traits; if that renumbered the existing ones, every
    embedding trained yesterday would be pointing at the wrong unit today
    and the loss would look like a bug in the network.
    """

    KINDS = ("champs", "traits", "items", "patches")

    def __init__(self, tables=None):
        self.tables = tables or {kind: {} for kind in self.KINDS}

    @classmethod
    def load(cls):
        try:
            return cls(json.loads(VOCAB.read_text()))
        except (FileNotFoundError, ValueError):
            return cls()

    def save(self):
        CORPUS.mkdir(exist_ok=True)
        VOCAB.write_text(json.dumps(self.tables, indent=1, sort_keys=True))

    def index(self, kind, key):
        table = self.tables[kind]
        if key not in table:
            table[key] = len(table) + 1     # +1 leaves PAD at zero
        return table[key]

    def size(self, kind):
        return len(self.tables[kind]) + 1


def matches_on_disk():
    """Every match we have, from the corpus shards and the two match folders.

    The corpus is the training data proper. matches/ and pool/ are folded in
    because they are free, already downloaded, and mean this whole file can
    be written and checked against real boards before the first crawl has
    run for an hour.
    """
    seen = set()

    for shard in sorted(CORPUS.glob("*.jsonl.gz")):
        with gzip.open(shard, "rt", encoding="utf-8") as handle:
            for line in handle:
                match = json.loads(line)
                match_id = match.get("metadata", {}).get("match_id")
                if match_id in seen:
                    continue
                seen.add(match_id)
                yield match

    for path in tft.match_files():
        if path.stem in seen:
            continue
        seen.add(path.stem)
        yield json.loads(path.read_text())


def encode(participant, vocab, uniques=()):
    """One board -> (unit ids, item ids, trait tiers, invariant features).

    A unit's index folds in its star level, so a 1-star Fizz and a 3-star
    Fizz are different embeddings rather than the same one with a number
    bolted on. They are different units in every way that matters: the
    3-star wins the game and the 1-star is a placeholder you sell.

    Items stay attached to the unit holding them rather than being counted
    into a board-wide bag. Rageblade on the carry and Rageblade on the
    frontline are not the same board, and a bag cannot tell them apart.
    """
    units = np.zeros(MAX_UNITS, dtype=np.int32)
    items = np.zeros((MAX_UNITS, MAX_ITEMS), dtype=np.int32)

    # Sort so the same board always encodes identically. Riot's ordering is
    # not stable, and without this the model would see one board as many.
    board = sorted(participant["units"],
                   key=lambda u: (-u.get("tier", 0), u["character_id"]))

    costs = [0] * 6
    stars = [0] * 3
    total_items = most_items = value = 0

    for slot, unit in enumerate(board[:MAX_UNITS]):
        star = min(max(unit.get("tier", 1), 1), 3)
        units[slot] = vocab.index("champs", f"{unit['character_id']}*{star}")
        held = unit.get("itemNames", [])[:MAX_ITEMS]
        for hole, item in enumerate(held):
            items[slot, hole] = vocab.index("items", item)

        cost = RARITY.get(unit.get("rarity", 0), 0)
        costs[cost] += 1
        stars[star - 1] += 1
        total_items += len(held)
        most_items = max(most_items, len(held))
        # What the board cost to assemble, in units of one 1-cost copy. Three
        # copies make a star, so a 2-star 4-cost is nine times a 1-cost. This
        # is the closest thing to "how far ahead were you" that does not just
        # restate how long you survived.
        value += (cost + 1) * 3 ** (star - 1)

    # Traits are keyed by display name, not by Riot's id, and that is what
    # folds the variants together: TFT17_Stargazer_Wolf and its siblings are
    # three flavours of one trait and were being counted as three traits, so
    # the model saw a third of the evidence for each of them.
    #
    # Unique traits are dropped for the reason tftdata already gives - they
    # belong to exactly one champion, so "did you commit to it" is a fact
    # about whether that champion was on the board and not a choice about a
    # line. Left in, they dominated the trait report with rows that were
    # really just "Blitzcrank is good" under another name.
    traits = {}
    for trait in participant["traits"]:
        if trait.get("tier_current", 0) <= 0:
            continue
        name, _ = tft.variant_of(trait["name"])
        if name is None:
            name = tft.display(trait["name"], "traits")
        if name in uniques:
            continue
        index = vocab.index("traits", name)
        # Two flavours of one trait can both be lit; the board committed as
        # hard as the harder of them.
        traits[index] = max(traits.get(index, 0), trait["tier_current"])

    tiers = list(traits.values())
    invariant = np.array([
        participant.get("level", 0),
        participant.get("gold_left", 0),
        int((units > 0).sum()),
        *costs, *stars,
        total_items, most_items,
        len(tiers), sum(1 for t in tiers if t >= 2), max(tiers, default=0),
        0,                      # contested - needs the lobby, filled in by build()
        value,
    ], dtype=np.float32)

    return units, items, traits, invariant


def build(set_number=None):
    vocab = Vocab.load()
    uniques = ()
    rows_units, rows_items, rows_traits, rows_scalars = [], [], [], []
    patches, placements, lobbies = [], [], []
    kept = skipped = 0

    for lobby, match in enumerate(matches_on_disk()):
        info = match["info"]
        game_set = info.get("tft_set_number") or 0
        if set_number is None:
            set_number = game_set
            uniques = tft.unique_traits(set_number)
            if not uniques:
                print(f"  (names.json lists no unique traits for set "
                      f"{set_number} - run fetch_names.py, or every "
                      f"one-champion trait will be reported as a line)")
        if game_set != set_number:
            skipped += 1
            continue

        patch = vocab.index("patches", crawl.patch_of(info))
        table = [encode(p, vocab, uniques) for p in info["participants"]]

        # How many other people in this lobby were on your line. It has to be
        # computed here because it is the one feature that is not a property
        # of your board at all - it is a property of the room. Being third
        # into a trait means a third of the units, and no amount of looking
        # at your own board reveals that.
        committed = [{t for t, tier in traits.items() if tier >= 2}
                     for _, _, traits, _ in table]
        for seat, (units, items, traits, invariant) in enumerate(table):
            rivals = max((sum(1 for other, lines in enumerate(committed)
                              if other != seat and line in lines)
                          for line in committed[seat]), default=0)
            invariant[CONTESTED] = rivals

            rows_units.append(units)
            rows_items.append(items)
            rows_traits.append(traits)
            rows_scalars.append(invariant)
            patches.append(patch)
            placements.append(info["participants"][seat]["placement"])
            # Which lobby a board came from. The eight boards in a match
            # competed for one set of placements, so they are not eight
            # independent draws - the model has to be told they belong
            # together or it will treat a strong lobby as eight weak boards.
            lobbies.append(lobby)
        kept += 1

    if not kept:
        sys.exit("No matches found. Run: python crawl.py --seed --hours 1")

    # Traits become dense only now that the vocabulary is final. Doing it
    # per board would have needed the width before the last trait was seen.
    width = vocab.size("traits")
    trait_matrix = np.zeros((len(rows_traits), width), dtype=np.int8)
    for row, active in enumerate(rows_traits):
        for index, tier in active.items():
            trait_matrix[row, index] = tier

    vocab.save()
    CORPUS.mkdir(exist_ok=True)
    np.savez_compressed(
        OUT,
        units=np.stack(rows_units),
        items=np.stack(rows_items),
        traits=trait_matrix,
        invariant=np.stack(rows_scalars),
        patch=np.array(patches, dtype=np.int32),
        placement=np.array(placements, dtype=np.int8),
        lobby=np.array(lobbies, dtype=np.int32),
        set_number=np.array([set_number]),
    )

    print(f"set {set_number}: {kept:,} matches -> {len(placements):,} boards"
          f"{f', {skipped:,} from another set skipped' if skipped else ''}")
    print(f"  {vocab.size('champs') - 1:,} champion+star combinations")
    print(f"  {vocab.size('traits') - 1:,} traits")
    print(f"  {vocab.size('items') - 1:,} items")
    print(f"  {vocab.size('patches') - 1:,} patches")
    print(f"  {OUT} is {OUT.stat().st_size / 1e6:.1f} MB")


def status():
    vocab = Vocab.load()
    for kind in Vocab.KINDS:
        table = vocab.tables[kind]
        print(f"{kind:>8}: {len(table):>5,}")
    if OUT.exists():
        data = np.load(OUT)
        print(f"\n{len(data['placement']):,} boards, "
              f"{len(set(data['lobby'].tolist())):,} lobbies")
        counts = np.bincount(data["patch"])
        for index, count in enumerate(counts):
            if count:
                name = next((k for k, v in vocab.tables["patches"].items()
                             if v == index), "?")
                print(f"  {name:>8}  {count:>8,} boards")


if __name__ == "__main__":
    status() if "--status" in sys.argv[1:] else build()
