"""Net A: a board goes in, a strength score comes out.

The score is not a placement. It is a number whose *differences* mean
something - give it eight boards and the ordering it puts them in is the
prediction. That is the right shape for TFT, where placements are a
permutation the eight of you divide up rather than eight separate results.
Nobody gets 4th on their own; they get 4th because three people built
something better that day.

So the loss is Plackett-Luce over the lobby: the chance that the winner
outscored the other seven, times the chance second outscored the remaining
six, and so on down. Two things fall out of that for free. Lobby strength
cancels, because only differences inside a room are ever compared - a
mediocre board in a weak lobby stops looking good. And the counterfactual
at the bottom of this file becomes a subtraction.

## The split that has to survive Set 18

    trunk   set-invariant. Level, gold, cost histogram, star histogram,
            item count, contest, board value. Facts about TFT's rules -
            interest caps at 5, three copies to a star, three item slots,
            eight players in a pool - none of which stop being true on the
            26th of August.

    head    set-specific. The champion embedding table, the item table, the
            trait tiers. Every one of these is a name, and every name dies
            with the set.

On launch day you keep the trunk, throw away the head, and fit ~200 fresh
embeddings on a network that already knows how the game works, instead of
starting from nothing while everyone else does too. Whether that head start
is real is untestable until the 26th - there is no Set 16 data anywhere on
this machine to rehearse it against - but the split costs nothing to build
and cannot be retrofitted, so it goes in now.

Run:  python net_a.py                       train and score
      python net_a.py --traits              what the model thinks of traits
      python net_a.py --transfer 17.8 17.9   train on one patch, test the next
      python net_a.py --relaunch 17.7 17.8 17.9   rehearse launch day
"""

import json
import sys

import numpy as np
import torch
import torch.nn as nn

from dataset import CORPUS, INVARIANT, OUT, Vocab

THIN = 1000          # boards a trait must be built on before it is worth quoting
EPOCHS = 60
BATCH = 256          # lobbies, not boards
HIDDEN = 128
TRUNK_OUT = 64
HEAD_OUT = 64
LR = 3e-4
SEED = 17

DEVICE = ("mps" if torch.backends.mps.is_available()
          else "cuda" if torch.cuda.is_available() else "cpu")


class BoardNet(nn.Module):
    def __init__(self, n_champs, n_items, n_traits, n_patches,
                 use_trunk=True, use_head=True):
        super().__init__()
        self.use_trunk, self.use_head = use_trunk, use_head
        # --- trunk: keeps its meaning across sets -------------------------
        self.trunk = nn.Sequential(
            nn.Linear(len(INVARIANT), HIDDEN), nn.ReLU(),
            nn.Linear(HIDDEN, TRUNK_OUT), nn.ReLU())

        # --- head: dies with the set --------------------------------------
        # A champion and its star level share one embedding rather than an
        # embedding plus a number, because a 3-star 1-cost and a 1-star
        # 1-cost are not the same unit scaled - one wins the game and one is
        # a placeholder you sell.
        self.champs = nn.EmbeddingBag(n_champs, 32, mode="sum", padding_idx=0)
        self.items = nn.EmbeddingBag(n_items, 16, mode="sum", padding_idx=0)
        # Trait tiers run 0-6 in real data, not 0-4 as the roster implies:
        # a set can stack styles higher than its own breakpoint table.
        self.traits = nn.Linear(n_traits * 7, 32)
        self.head = nn.Sequential(
            nn.Linear(32 + 16 + 32, HEAD_OUT), nn.ReLU())

        self.patch = nn.Embedding(n_patches, 8)

        self.score = nn.Sequential(
            nn.Linear(TRUNK_OUT + HEAD_OUT + 8, HIDDEN), nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(HIDDEN, 1))

    def forward(self, invariant, units, items, traits, patch):
        body = self.trunk(invariant)
        named = self.head(torch.cat([
            self.champs(units),
            self.items(items.flatten(1)),
            self.traits(traits)], dim=1))
        # Zeroing a side rather than skipping it keeps the parameter count
        # and the shapes identical across an ablation, so the comparison is
        # about the information and not about the size of the network.
        if not self.use_trunk:
            body = torch.zeros_like(body)
        if not self.use_head:
            named = torch.zeros_like(named)
        both = torch.cat([body, named, self.patch(patch)], dim=1)
        return self.score(both).squeeze(-1)

    def reset_head(self):
        """Forget every name, keep everything else. This is the 26th of August."""
        for module in (self.champs, self.items, self.traits, self.head):
            for layer in ([module] if not isinstance(module, nn.Sequential)
                          else module):
                if hasattr(layer, "reset_parameters"):
                    layer.reset_parameters()


def plackett_luce(scores):
    """Negative log-likelihood of the observed finishing order.

    `scores` arrives as (lobbies, 8) already sorted best-placement first.
    The likelihood of that order is the chance the winner beat all eight,
    times the chance second beat the remaining seven, and so on - which is
    a sum of (score - logsumexp of everyone still standing).

    The suffix logsumexp is the awkward part, so it is done by reversing,
    taking a cumulative logsumexp, and reversing back.
    """
    suffix = torch.logcumsumexp(scores.flip(1), dim=1).flip(1)
    return -(scores - suffix).sum(1).mean()


def metrics(scores, placements):
    """Pairwise ordering accuracy inside each lobby, and mean |rank error|.

    Pairwise accuracy is the honest headline: of every pair of players in a
    lobby, how often does the model put the one who actually placed better
    on top. 50% is a coin flip. It is readable without knowing what a loss
    curve is, which matters when the point of this project is to not fool
    yourself.
    """
    better = placements[:, :, None] < placements[:, None, :]
    predicted = scores[:, :, None] > scores[:, None, :]
    pairs = better.sum()
    correct = (better & predicted).sum()

    order = torch.argsort(torch.argsort(-scores, dim=1), dim=1) + 1
    error = (order.float() - placements.float()).abs().mean()
    return (correct / pairs).item(), error.item()


def lobbies(data, rows=None):
    """Reshape flat boards into (lobbies, 8, ...) sorted best-placement first.

    Everything downstream assumes a lobby is intact. A split that put four
    of a match's boards in train and four in test would be handing the model
    the answer - it only ever has to beat the people in the room.
    """
    keep = np.arange(len(data["placement"])) if rows is None else rows
    lobby = data["lobby"][keep]
    order = np.lexsort((data["placement"][keep], lobby))
    keep, lobby = keep[order], lobby[order]

    ids, counts = np.unique(lobby, return_counts=True)
    full = np.isin(lobby, ids[counts == 8])      # drop any short lobby
    keep = keep[full]
    n = len(keep) // 8

    def take(name, dtype):
        block = data[name][keep][: n * 8]
        return torch.tensor(block, dtype=dtype).reshape(n, 8, *block.shape[1:])

    return {
        "invariant": take("invariant", torch.float32),
        "units": take("units", torch.long),
        "items": take("items", torch.long),
        "traits": take("traits", torch.long),
        "patch": take("patch", torch.long),
        "placement": take("placement", torch.long),
    }


def normalise(train, *others):
    """Standardise the trunk features on the training split only."""
    flat = train["invariant"].reshape(-1, len(INVARIANT))
    mean, sd = flat.mean(0), flat.std(0).clamp(min=1e-6)
    for split in (train, *others):
        split["invariant"] = (split["invariant"] - mean) / sd
    return mean, sd


def normalise_to(split, stats):
    """Standardise `split` with constants normalise() already returned.

    A trunk carried across a set boundary only means anything if the
    features arriving on the far side are on the scale it was fitted to.
    Re-standardising the new set on itself would quietly move the ground
    under it and make the head start look worse than it is.
    """
    mean, sd = stats
    split["invariant"] = (split["invariant"] - mean) / sd


def forward(model, batch, index):
    n = len(index)
    flat = lambda name, extra=(): batch[name][index].reshape(n * 8, *extra)
    traits = torch.nn.functional.one_hot(
        flat("traits", (-1,)).clamp(0, 6), 7).flatten(1).float()
    scores = model(flat("invariant", (-1,)),
                   flat("units", (-1,)),
                   flat("items", (-1, 3)),
                   traits.to(DEVICE),
                   flat("patch").reshape(-1))
    return scores.reshape(n, 8)


def evaluate(model, split):
    model.eval()
    with torch.no_grad():
        scores = forward(model, split, torch.arange(len(split["placement"])))
        return metrics(scores.cpu(), split["placement"].cpu())


def baseline(split, feature):
    """Score boards by one raw feature, for something honest to beat."""
    column = INVARIANT.index(feature)
    scores = split["invariant"][:, :, column]
    # Placement is better when smaller, so a feature that helps must be
    # flipped if it correlates the other way. Try both and keep the winner:
    # this is a floor, and a floor should be as high as it honestly can be.
    a, _ = metrics(scores, split["placement"])
    b, _ = metrics(-scores, split["placement"])
    return max(a, b)


def load(train_patch=None, test_patch=None, seed=SEED):
    data = np.load(OUT)
    vocab = Vocab.load()
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)

    if train_patch:
        # A patch has two names: "17.9" is what the notes call it and what
        # you want to type, "16.16" is the client build the corpus is
        # sharded on. Whichever vocabulary recognises the name picks the
        # column, so both spellings work and neither has to be remembered.
        for kind, column in (("tft_patches", "tft_patch"), ("patches", "patch")):
            index = vocab.tables.get(kind, {})
            if column in data and train_patch in index and test_patch in index:
                break
        else:
            known = sorted(vocab.tables.get("tft_patches") or
                           vocab.tables.get("patches", {}))
            sys.exit(f"No patch {train_patch} / {test_patch} in the corpus. "
                     f"Have: {', '.join(known)}"
                     + ("\n(rebuild with: python dataset.py)"
                        if "tft_patch" not in data else ""))
        train = lobbies(data, np.where(data[column] == index[train_patch])[0])
        test = lobbies(data, np.where(data[column] == index[test_patch])[0])
    else:
        every = np.unique(data["lobby"])
        rng.shuffle(every)
        cut = int(len(every) * 0.8)
        pick = lambda ids: np.where(np.isin(data["lobby"], ids))[0]
        train = lobbies(data, pick(every[:cut]))
        test = lobbies(data, pick(every[cut:]))

    normalise(train, test)
    for split in (train, test):
        for key in split:
            split[key] = split[key].to(DEVICE)
    return data, vocab, train, test


def train(model, train_split, test_split, quiet=False):
    optimiser = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    n = len(train_split["placement"])
    best = (0.0, None)

    for epoch in range(1, EPOCHS + 1):
        model.train()
        order = torch.randperm(n, device=DEVICE)
        total = 0.0
        for start in range(0, n, BATCH):
            index = order[start:start + BATCH]
            loss = plackett_luce(forward(model, train_split, index))
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            total += loss.item() * len(index)

        accuracy, error = evaluate(model, test_split)
        if accuracy > best[0]:
            best = (accuracy, {k: v.clone() for k, v in model.state_dict().items()})
        if not quiet and (epoch % 10 == 0 or epoch == 1):
            print(f"  epoch {epoch:>3}  loss {total / n:>7.3f}   "
                  f"test pairwise {accuracy:>6.1%}   rank error {error:.2f}")

    model.load_state_dict(best[1])
    return best[0]


def trait_deltas(model, vocab, split):
    """What the model thinks each trait is worth, by asking it.

    Not a correlation. For every board that did NOT commit to a trait, the
    trait is switched on and the board is scored again - so the comparison
    is a board against itself, with its level, its gold and its board value
    held exactly where they were. That is the reading the table at the top
    of dataset.py could not give: raw averages rank the trait that survives,
    this ranks the trait that helps.

    It is still an idealisation. Lighting a trait in a real game means
    swapping units in, and here nothing else moves - so read it as "worth
    trading a slot for", not as free.

    Nothing in the invariant vector is allowed to move with it, and the first
    version of this got that wrong. Switching a trait on also bumped
    traits_committed, which is one of the survivorship features the model
    quite correctly reads as "this board lived a long time" - so every trait
    inherited the benefit of having one more finished trait, and twenty of
    twenty-two came back looking helpful. Placement is a fixed 1-to-8 split;
    a table where almost everything helps is arithmetically impossible and
    was measuring board development wearing each trait's name in turn.
    """
    names = {v: k for k, v in vocab.tables["traits"].items()}

    model.eval()
    index = torch.arange(len(split["placement"]))
    with torch.no_grad():
        base = forward(model, split, index)
        # An opponent pool to place the board against, so a strength
        # difference can be quoted as placements rather than as a bare number.
        pool = base.flatten()

        rows = []
        for trait in range(1, split["traits"].shape[-1]):
            off = split["traits"][:, :, trait] == 0
            if off.sum() < 200:
                continue
            moved = {k: v.clone() for k, v in split.items()}
            moved["traits"][:, :, trait] = torch.where(
                off, torch.full_like(off, 2, dtype=torch.long),
                moved["traits"][:, :, trait])
            after = forward(model, moved, index)

            shift = (after - base)[off]
            # Expected placement is 1 + how many of a random seven outscore
            # you, which for a Plackett-Luce score is a sum of sigmoids.
            place = lambda s: 1 + 7 * torch.sigmoid(
                pool[None, :] - s[:, None]).mean(1)
            delta = (place(after[off]) - place(base[off])).mean().item()
            rows.append((delta, shift.mean().item(), names[trait], int(off.sum())))

    rows.sort()
    return rows


def trait_report(model, data, vocab, split):
    rows = trait_deltas(model, vocab, split)
    print(f"\n  {'trait':<26}{'boards':>8}{'strength':>11}{'placement':>11}")
    for delta, shift, name, n in rows:
        print(f"  {name[:24]:<26}{n:>8,}{shift:>+11.3f}{delta:>+11.2f}")
    print("\n  Negative placement is better - the trait moves you up the lobby.")

    # The sanity check that the first version of this failed. Placement is a
    # fixed 1-to-8 split, so a trait cannot help everyone: roughly half of
    # these should be harmful. If they are not, something is leaking board
    # development into the counterfactual again and the ranking above is
    # decoration - say so here rather than letting it be quoted as a finding.
    helpful = sum(1 for delta, *_ in rows if delta < 0)
    print(f"  {helpful} of {len(rows)} traits help, "
          f"{len(rows) - helpful} hurt.")
    if rows and not 0.25 <= helpful / len(rows) <= 0.75:
        print("  ^ that split is lopsided enough to distrust: a trait cannot "
              "help\n    everyone when placements are a fixed 1-to-8 split. "
              "Treat the\n    table above as unreliable until this is nearer "
              "half and half.")


def stability(vocab, seeds, train_patch=None, test_patch=None):
    """Train the whole thing several times over and keep only what agrees.

    A network starts from random weights, so two runs on identical data give
    two slightly different answers. One run cannot tell a trait that really
    costs you a quarter of a placement from a trait the model happened to
    feel that way about this morning - and a table that cannot tell those
    apart is a table of coin flips with names attached.

    The split is redrawn per seed as well as the weights, because the
    question worth answering is "if I ran this pipeline again, how much would
    it move" - and which boards landed in the test set is part of that.

    Anything whose spread crosses zero is dropped. The model does not know
    which way that trait goes, so it does not get an opinion on it.
    """
    pooled = {}
    for run, seed in enumerate(seeds, start=1):
        _, _, train_split, test_split = load(train_patch, test_patch, seed=seed)
        model = BoardNet(vocab.size("champs"), vocab.size("items"),
                         vocab.size("traits"), vocab.size("patches")).to(DEVICE)
        accuracy = train(model, train_split, test_split, quiet=True)
        print(f"  seed {seed:<4} pairwise {accuracy:>6.1%}", flush=True)
        for delta, _, name, _ in trait_deltas(model, vocab, test_split):
            pooled.setdefault(name, []).append(delta)
        # How often the trait was actually built, which agreeing seeds do not
        # tell you. Five runs rule out the model having been in a mood; they
        # cannot conjure evidence that was never there, and a trait built on
        # 243 boards is thin however confidently the network says otherwise.
        built = {name: int((test_split["traits"][:, :, index] >= 2).sum())
                 for index, name in
                 ((i, n) for n, i in vocab.tables["traits"].items())}

    rows = []
    for name, deltas in pooled.items():
        low, high = min(deltas), max(deltas)
        rows.append((sum(deltas) / len(deltas), low, high, name,
                     low > 0 or high < 0, built.get(name, 0)))
    rows.sort()

    print(f"\n  {'trait':<20}{'placement':>11}{'range over seeds':>22}"
          f"{'built':>8}   verdict")
    for mean, low, high, name, agrees, n in rows:
        thin = " (thin)" if agrees and n < THIN else ""
        print(f"  {name[:18]:<20}{mean:>+11.2f}{f'{low:+.2f} to {high:+.2f}':>22}"
              f"{n:>8,}   {'holds' if agrees else 'noise'}{thin}")

    kept = [r for r in rows if r[4]]
    solid = [r for r in kept if r[5] >= THIN]
    print(f"\n  {len(kept)} of {len(rows)} traits point the same way in all "
          f"{len(seeds)} runs,\n  and {len(solid)} of those were built often "
          f"enough to lean on.")
    if solid:
        print("  Believable, best first: "
              + ", ".join(f"{n} ({m:+.2f})" for m, _, _, n, _, _ in solid))
    print("  Everything marked noise moved sign between runs - one training "
          "run\n  would have reported it as a finding anyway.")


def relaunch(train_patches, new_patch, sizes=(50, 100, 250, 500, 1000, 2000)):
    """Rehearse the 26th of August: keep the trunk, burn the names, refit.

    The whole trunk/head split is a bet - that a network which already knows
    how TFT works relearns a set's worth of fresh champions faster than one
    starting from nothing, on a morning when everybody is starting from
    nothing. reset_head() has been sitting in this file untested since the
    day it was written, which is a poor state for the one code path launch
    day depends on.

    This is the closest rehearsal the data allows, and it understates the
    problem: the names on the far side of this boundary are the SAME names,
    so the fresh head relearns a vocabulary the trunk has already seen
    alongside. What it does measure honestly is the mechanism - whether a
    pretrained trunk gets a cold head off the ground faster - and that is
    the part that has to hold when the names really do change.

    The curve is what matters, not any single point. Launch day is the far
    left of it: a few hundred lobbies, everyone equally blind.
    """
    data = np.load(OUT)
    vocab = Vocab.load()
    index = vocab.tables["tft_patches"]
    for name in (*train_patches, new_patch):
        if name not in index:
            sys.exit(f"No patch {name} in the corpus. "
                     f"Have: {', '.join(sorted(index))}")

    rows = lambda names: np.where(np.isin(
        data["tft_patch"], [index[n] for n in names]))[0]
    old = lobbies(data, rows(train_patches))

    # The new set, split into a fixed test half and a pool to refit from.
    # The test half never moves, so every point on the curve is scored on
    # the same lobbies and only the amount of fresh data changes.
    fresh = rows([new_patch])
    ids = np.unique(data["lobby"][fresh])
    np.random.default_rng(SEED).shuffle(ids)
    held = ids[:len(ids) // 2]
    pool = ids[len(ids) // 2:]
    pick = lambda chosen: fresh[np.isin(data["lobby"][fresh], chosen)]
    test = lobbies(data, pick(held))

    # One normalisation for everything, taken from the old set. The trunk is
    # about to be reused on the far side of a boundary, so it has to meet
    # the features on the scale it was trained on - that is the whole point
    # of it being the half that survives.
    stats = normalise(old, test)
    for split in (old, test):
        for key in split:
            split[key] = split[key].to(DEVICE)

    build = lambda: BoardNet(vocab.size("champs"), vocab.size("items"),
                             vocab.size("traits"), vocab.size("patches")).to(DEVICE)

    print(f"pretraining on {'+'.join(train_patches)}: "
          f"{len(old['placement']):,} lobbies")
    torch.manual_seed(SEED)
    pretrained = build()
    before = train(pretrained, old, test, quiet=True)
    print(f"  pretrained model, straight onto {new_patch}: {before:.1%}")
    print(f"\n  {new_patch} refit, scored on {len(test['placement']):,} "
          f"held-out lobbies")
    print(f"  {'lobbies':>8}{'trunk kept':>13}{'from scratch':>15}{'gain':>8}")

    keep = {k: v.clone() for k, v in pretrained.state_dict().items()}
    for size in sizes:
        if size > len(pool):
            break
        slice_ = lobbies(data, pick(pool[:size]))
        normalise_to(slice_, stats)
        for key in slice_:
            slice_[key] = slice_[key].to(DEVICE)

        # Warm: the trunk keeps everything it learned, the head forgets
        # every name. This is exactly what happens on launch morning.
        torch.manual_seed(SEED)
        warm = build()
        warm.load_state_dict(keep)
        warm.reset_head()
        warm_score = train(warm, slice_, test, quiet=True)

        torch.manual_seed(SEED)
        cold_score = train(build(), slice_, test, quiet=True)
        print(f"  {size:>8,}{warm_score:>12.1%}{cold_score:>14.1%}"
              f"{warm_score - cold_score:>+8.1%}")


def main():
    args = sys.argv[1:]
    if not OUT.exists():
        sys.exit("No dataset yet. Run: python dataset.py")

    if "--relaunch" in args:
        spot = args.index("--relaunch")
        names = [a for a in args[spot + 1:] if not a.startswith("-")]
        if len(names) < 2:
            sys.exit("--relaunch needs the patches to pretrain on and the one "
                     "to land on, like: --relaunch 17.7 17.8 17.9")
        return relaunch(names[:-1], names[-1])

    train_patch = test_patch = None
    if "--transfer" in args:
        spot = args.index("--transfer")
        try:
            train_patch, test_patch = args[spot + 1], args[spot + 2]
        except IndexError:
            sys.exit("--transfer needs two patches, like: --transfer 16.15 16.16")

    if "--seeds" in args:
        spot = args.index("--seeds")
        try:
            count = int(args[spot + 1])
        except (IndexError, ValueError):
            sys.exit("--seeds needs a number, like: --seeds 5")
        vocab = Vocab.load()
        print(f"Training {count} times to see which traits survive a reroll.\n")
        return stability(vocab, [SEED + i for i in range(count)],
                         train_patch, test_patch)

    data, vocab, train_split, test_split = load(train_patch, test_patch)
    print(f"{len(train_split['placement']):,} training lobbies, "
          f"{len(test_split['placement']):,} test lobbies, on {DEVICE}")
    if train_patch:
        print(f"trained on {train_patch}, tested on {test_patch} - "
              f"the gap is what a patch costs you.\n")

    build = lambda **parts: BoardNet(
        vocab.size("champs"), vocab.size("items"),
        vocab.size("traits"), vocab.size("patches"), **parts).to(DEVICE)

    model = build()
    accuracy = train(model, train_split, test_split)

    if "--ablate" in args:
        # The question this settles: does knowing WHICH champions and traits
        # are on the board beat knowing only how developed it is? The trunk
        # alone is pure survivorship - level, board value, item count. If the
        # full model cannot beat it, every name in the head is decoration and
        # the trait report below is fiction.
        print("\n  --- ablation ---")
        for label, parts in (("trunk only (no names)", dict(use_head=False)),
                             ("head only (names only)", dict(use_trunk=False))):
            other = build(**parts)
            print(f"  {label:<22}{train(other, train_split, test_split, quiet=True):>7.1%}")

    print(f"\n  {'Net A':<22}{accuracy:>7.1%}")
    for feature in ("board_value", "level", "items", "units"):
        print(f"  {'baseline: ' + feature:<22}{baseline(test_split, feature):>7.1%}")
    print(f"  {'coin flip':<22}{0.5:>7.1%}")

    torch.save({"state": model.state_dict(),
                "vocab": vocab.tables}, CORPUS / "net_a.pt")
    print(f"\nsaved to {CORPUS / 'net_a.pt'}")

    if "--traits" in args:
        trait_report(model, data, vocab, test_split)


if __name__ == "__main__":
    main()
