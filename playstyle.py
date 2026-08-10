"""Who you are as a TFT player right now, measured against your own lobbies.

Run:  python playstyle.py            (newest set on disk)
      TFT_SET=17 python playstyle.py (look back at an older set)

Every big site will tell you which comps and augments are strong. None of
them tell you anything about *you*: whether you buy expensive boards or
reroll cheap ones, whether you commit to a vertical or splash three traits,
whether you go wide or stay small. That is playstyle, and it is the thing
your own match files can answer and a global leaderboard cannot.

Two reasons this report is built on playstyle rather than on comps:

  1. It survives a new set. Trait and champion names all change in September
     and every comp statistic resets to zero - but "expensive board" and
     "rarely commits" mean the same thing in set 18 as in set 17, so your
     profile carries over on day one when nobody has comp data yet.

  2. It has the sample size. A per-line placement average needs ~30 games of
     that one line, which this player has never reached in 196 games. These
     are averages over whole boards, so every game measures every metric.

**It reports on your current era, not your whole history.** Playstyle is
supposed to change - a new set is exactly when it should - so a lifetime
average blends every version of you and describes none of them. This player
rerolled for 75 games and then went fast-8; averaged together that reads as
a moderate player who does neither. So the report finds where the style last
changed and profiles you from there.

The yardstick is the other seven players in your own lobbies, so it is
already your rank and your patches - not a global average across all elos.
"""

import textwrap

import tftdata as tft

WINDOW = 25   # rolling window for the trajectory table


def wrap(text, indent="  "):
    print(textwrap.fill(text, width=70,
                        initial_indent=indent, subsequent_indent=indent))


games, _ = tft.load_games()
set_number = games[0]["_set"]

# Oldest first: everything below is chronological.
timeline = list(reversed(games))
mine, room = tft.lobby_profiles(games, set_number)

shift = tft.latest_shift(mine)
era = mine[shift["cut"]:] if shift else mine
era_games = timeline[shift["cut"]:] if shift else timeline

print("=" * 70)
print(f"  YOUR PLAYSTYLE - {len(era)} games, set {set_number}")
print("=" * 70)
print()
if shift:
    wrap(f"Reporting on your current era only: the {len(era)} games since "
         f"your style last changed, around game {shift['cut'] + 1} "
         f"(patch {era_games[0]['_patch']}). The {shift['cut']} games before "
         "that were a different player, and averaging them together would "
         "describe neither.")
else:
    wrap(f"All {len(era)} games - your style has not changed sharply enough "
         "during this set to split it into eras.")
print()
wrap(f"Measured against the {len(room)} boards played by everyone else in "
     "your lobbies, so this is your rank and your patches.")

verdicts, enough = tft.playstyle_verdicts(era, room)
if not enough:
    print()
    wrap(f"Only {len(era)} games, so nothing here gets named yet - it takes "
         f"about {tft.MIN_PLAYSTYLE_GAMES}. The numbers below are real; the "
         "reading of them is not. At this many games a report like this "
         "describes the wrong player roughly a third of the time.")

print(f"\n  {'':<24}{'you':>8}{'lobby':>8}{'gap':>8}   read")
for key, label, more, less in tft.PLAYSTYLE_LABELS:
    judged = verdicts.get(key)
    if judged is None:
        continue
    read = (more if judged["gap"] > 0 else less) if judged["real"] \
        else ("too few games to say" if not enough else "same as the lobby")
    print(f"  {label:<24}{judged['a']:>8.2f}{judged['b']:>8.2f}"
          f"{judged['gap']:>+8.2f}   {read}")

print()
wrap("A gap only gets a word for it when it is far bigger than its own error "
     "bar. Seven habits are judged at once here, so any one of them can get "
     "lucky - the bar is set high enough that that stops happening.")

print("\nIN A SENTENCE")
print()
sentence = tft.playstyle_sentence(verdicts) if enough else None
wrap(sentence if sentence else
     (f"Not yet - come back around {tft.MIN_PLAYSTYLE_GAMES} games."
      if not enough else
      "Nothing about how you play is far enough from the lobby to name. "
      "That is a real answer: you play the way your lobbies do."))

# ---- how it has moved -----------------------------------------------------
if len(mine) >= 2 * WINDOW:
    print(f"\nHOW IT HAS CHANGED  (rolling {WINDOW} games, oldest first)")
    print(f"\n  {'games':<12}{'patch':<10}{'cost':>6}{'4-5c':>6}"
          f"{'3-star':>8}{'built':>7}{'size':>6}")
    for i in range(0, len(mine) - WINDOW + 1, WINDOW):
        window = mine[i:i + WINDOW]
        patches = sorted({g["_patch"] for g in timeline[i:i + WINDOW]},
                         key=lambda s: tuple(int(x) for x in s.split(".")))

        def avg(key, w=window):
            return sum(p[key] for p in w) / len(w)

        print(f"  {f'{i + 1}-{i + WINDOW}':<12}{patches[0]:<10}"
              f"{avg('avg_cost'):>6.2f}{avg('big_units'):>6.2f}"
              f"{avg('rerolled'):>8.2f}{avg('lines_built'):>7.2f}"
              f"{avg('board_size'):>6.2f}")

if shift:
    before = tft.summarise([g["placement"] for g in timeline[:shift["cut"]]])
    after = tft.summarise([g["placement"] for g in era_games])
    gap = before["avg"] - after["avg"]        # positive = better after
    moe = tft.gap_moe(before["games"], after["games"])

    print("\nTHE SWITCH")
    print()
    wrap(f"Around game {shift['cut'] + 1} your {shift['label']} went from "
         f"{shift['before']:.2f} to {shift['after']:.2f} - "
         f"{shift['direction']}. That is the sharpest change in your set.")
    print()
    print(f"  before  {before['games']:>4} games   avg {before['avg']:.2f}"
          f"   top4 {before['top4']:.0f}%")
    print(f"  after   {after['games']:>4} games   avg {after['avg']:.2f}"
          f"   top4 {after['top4']:.0f}%")
    print()
    if abs(gap) > moe:
        wrap(f"Changing style moved your placement by {abs(gap):.2f} places, "
             f"bigger than the +/-{moe:.2f} error bar - a real "
             f"{'improvement' if gap > 0 else 'regression'}.")
    else:
        wrap(f"It made no measurable difference: {abs(gap):.2f} places, "
             f"inside the +/-{moe:.2f} that luck alone produces at these "
             "sample sizes. You changed how you play and finished in the "
             "same place.")
    print()
    was = sorted({g["_patch"] for g in timeline[:shift["cut"]]},
                 key=lambda s: tuple(int(x) for x in s.split(".")))
    now = sorted({g["_patch"] for g in era_games},
                 key=lambda s: tuple(int(x) for x in s.split(".")))
    wrap(f"Read that with care: the patch changed underneath you too "
         f"({', '.join(was)} then {', '.join(now)}), so the style switch and "
         "the meta moved together and neither can be credited on its own.")

print()
wrap("None of this says the lobby is right and you are wrong. It says where "
     "you differ, which is where a habit lives - and habits are the part of "
     "your game you can actually change on purpose.")
