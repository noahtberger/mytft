"""The pre-game screen as a page in the local app.

A dashboard, not an article: a row of cards with one idea each, sized to
be glanced at between the queue popping and the loading screen. Anything
that needs a paragraph is small print or hidden behind a click.

Two rules from the product direction, both set by the user:

  - No comp prescriptions. "Learn Marauder" is exactly the advice this
    tracker promised not to give - the comp sites own comps; this thing
    coaches playstyle. Comp strength is shown as information ("strong in
    your lobbies"), never as an instruction, and the top card reports the
    player's OWN record rather than telling them what to play.
  - Auto-scouting the live lobby is the goal, but it needs a helper on
    the machine running League (that is how the big apps do it - the
    spectator web API is production-key-only). Until that helper exists,
    the box is manual and honest about it.

Everything here is public match history from before the game starts,
which is the side of the line Riot's rules allow.
"""

from urllib.parse import quote

import comps
import pregame as pre
import scout
import tftdata as tft
from theme import (BRAND, CSS, app_bar, esc, fold, new_game_watcher, plural,
                   purpose, stale_names_banner, why)


def query(puuid, who):
    return f"p={quote(puuid)}&n={quote(who)}"


def player_read(label, boards, set_number, note=None):
    """One scouted player, boiled down to a glance."""
    if not boards:
        return {"label": label,
                "error": note or "No ranked games on record for this set"}
    prof = scout.profile(boards, set_number)
    plays = [name for name, _ in prof["comps"].most_common(2)]
    carry = [name for name, _ in prof["carries"].most_common(1)]
    forces = None
    top_build = prof["comps"].most_common(1)
    if top_build:
        name, n = top_build[0]
        if n >= 5 and n >= 0.4 * prof["games"]:
            forces = name
    return {"label": label, "error": None, "note": note,
            "games": prof["games"], "avg": prof["avg"],
            "plays": ", ".join(plays) if plays else "Nothing twice",
            "carry": carry[0] if carry else None,
            "forces": forces}


def scouted_rows(scouted):
    """One line per player: who, how they do, what they play."""
    out = []
    for r in scouted:
        if r.get("error"):
            out.append(f'<li><span class="k">{esc(r["label"])}</span>'
                       f'<span class="k">{esc(r["error"])}</span></li>')
            continue
        bits = esc(r["plays"])
        if r["carry"]:
            bits += f' &middot; {esc(r["carry"])}'
        if r["forces"]:
            bits = (f'<span class="pill blank">forces {esc(r["forces"])}</span> '
                    + bits)
        out.append(
            f'<li><span><strong>{esc(r["label"])}</strong><br>'
            f'<span class="k" style="font-size:13px">{bits}</span></span>'
            f'<span class="v">{r["avg"]:.2f}<br>'
            f'<span class="k" style="font-size:11.5px">{r["games"]} games</span>'
            f'</span></li>')
    return f'<ul class="rowlist">{"".join(out)}</ul>'


def plan_row(row, best_plan):
    """One plan's own record: how often it wins, not what it averages."""
    mark = ' style="font-weight:600"' if row["plan"] == best_plan else ""
    return (f'<li><span{mark}>{esc(row["plan"])}<br>'
            f'<span class="k" style="font-size:12px">'
            f'{plural(row["n"], "game")} '
            f'&middot; avg {row["avg"]:.2f} &middot; {row["tier"]}</span></span>'
            f'<span class="v">{100 * row["first"]:.0f}%<br>'
            f'<span class="k" style="font-size:11.5px">'
            f'top 4 {100 * row["top4"]:.0f}%</span></span></li>')


def plan_summary(shown):
    """The label on the fold: what is inside, not "show more".

    Named so somebody can decide whether to open it without opening it - a
    summary that says "details" makes you click to find out whether you cared.
    """
    return f'All {plural(len(shown), "plan")}, with the numbers'


def plan_card(record):
    """Which way of spending gold actually wins this player games.

    Deliberately not average placement. Two plans can share an average and
    play completely differently - this player averages 3.95 on Fast 9 and
    3.87 on Lvl 6 Reroll, a gap well inside its error bar, while winning
    28% of the first and 9% of the second. Averaging a 1st with a 4th hides
    the only difference that matters.

    There is always something to read here. When no plan has separated
    itself the card still shows every plan's record and says plainly that
    it is too early - a card that goes blank is a card that gets ignored.
    """
    if not record or not record["rows"]:
        return ('<p class="fine" style="margin:0">Not enough games yet to '
                'tell your plans apart.</p>')

    best = record["best"]
    # "No clear plan" is not a way to spend gold and must not be ranked
    # beside the real ones: its raw average is mostly survival, not strategy
    # - holding the death round fixed those boards place 0.03 worse
    # (+/-0.09), ie. not at all. Listing it would put a made-up leak at the
    # top of the page. It still counts in the "everything else" baseline,
    # because they are games this player played.
    #
    # It used to be twice the size it is now: 276 of those boards were fast 8
    # and had nowhere else to go. They have a plan of their own since.
    shown = [r for r in record["rows"] if r["plan"] != comps.NO_PLAN]
    if not shown:
        return ('<p class="fine" style="margin:0">Not enough games yet to '
                'tell your plans apart.</p>')
    rows = "".join(plan_row(r, best["plan"] if best else None) for r in shown)
    table = (f'<ul class="rowlist" style="margin:0 0 12px">{rows}</ul>')

    spread = record.get("spread")
    if not best:
        played = max(shown, key=lambda r: r["n"])
        head = (f'<p class="tip"><strong>No plan has pulled ahead for you '
                f'yet.</strong><span>Your most played is '
                f'{esc(played["plan"])} at {plural(played["n"], "game")}. The '
                f'number on the right is how often each one ends in first '
                f'&mdash; when one of them separates from the rest, it will '
                f'say so here.</span></p>')
        # For someone who plays something different every game this is the
        # finding, not a shrug: nothing has been repeated often enough to
        # read yet, and that is worth saying with the count attached. Only
        # when it is actually TRUE, though - a player whose top plan has 29
        # games has a verdict-free card for a different reason (their plan
        # simply doesn't win more), and telling them they spread too wide
        # would be both wrong and unhelpful.
        thin = max((r["n"] for r in shown), default=0) < comps.MIN_PLAN_GAMES
        if spread and thin:
            head += (f'<p class="fine" style="margin:0 0 12px">You have '
                     f'played {plural(spread["comps"], "comp")} across '
                     f'{plural(spread["games"], "game")} and none of them '
                     f'often enough to read yet &mdash; your most played is '
                     f'{esc(spread["top"])}, at {100 * spread["share"]:.0f}% '
                     f'of games. That is a finding, not a gap in the data: '
                     f'playing something different every game is a style.</p>')
        return head + fold(plan_summary(shown), table)

    # The verdict as a stat pair rather than a sentence - the user's call,
    # measuring reading time: "what if i just put like fast 9 1st place:
    # 28%". The rule that keeps it honest is that the PAIR is the claim.
    # 28% alone is any tracker's win-rate widget; 28-against-7 is a finding.
    # A lone number never ships without its baseline beside it, and the
    # no-verdict path above keeps its sentence - a big confident number
    # would manufacture a verdict the model declined to reach.
    #
    # Reuses the stats page's own .stats/.stat tiles rather than inventing
    # a second look for the same idea. The context sentence that used to
    # follow the verdict moves into the fold, above the table.
    # "1sts on it" rather than "1sts on Fast 9": the plan's name now sits
    # big in the card heading, and repeating it in a 10px label under a
    # 27px number demotes it back to fine print - the exact complaint.
    label = "1sts on it"
    if best.get("coarse"):
        context = ('No single plan has enough games to read on its own, so '
                   'this is the coarser question: rolling for 3-stars, or '
                   'levelling to the big units.')
    else:
        context = ('Your average placement barely moves between plans; what '
                   'changes is how often the game ends with you on top.')
    head = (f'<div class="stats" style="margin:0 0 12px">'
            f'<div class="stat"><span class="n">{100 * best["first"]:.0f}%'
            f'</span><span class="k">{esc(label)}</span></div>'
            f'<div class="stat"><span class="n">{100 * best["others"]:.0f}%'
            f'</span><span class="k">everything else</span></div>'
            f'<div class="stat"><span class="n">{best["n"]}</span>'
            f'<span class="k">games on it</span></div></div>')
    context_note = f'<p class="fine" style="margin:0 0 10px">{context}</p>'

    drift = ""
    if record["now"] is not None:
        now, then = record["now"], record["then"]
        played = round(now * record["now_n"])
        moved = (f'down from {100 * then:.0f}%' if now < then else
                 f'up from {100 * then:.0f}%')
        drift = (f'<p class="fine" style="margin:12px 0 0">You played it in '
                 f'{played} of your last {record["now_n"]} games, {moved} of '
                 f'everything before that.</p>')
    skipped = sum(r["n"] for r in record["rows"] if r["plan"] == comps.NO_PLAN)
    note = (f'<p class="fine" style="margin:8px 0 0">{plural(skipped, "game")} '
            f'not listed: the board never showed a plan at all, which is '
            f'almost always a game that ended early.</p>'
            if skipped else "")
    # The verdict is the card; the per-plan records are the working. Feedback
    # from players who are not the author: "the actual statistics like fast 9
    # win rate, avg, lvl 7 reroll could all be hidden behind a drop down".
    # They are right that a table of every plan is not what somebody wants in
    # the seconds before they queue, and the tiles above already carry the
    # answer.
    return head + fold(plan_summary(shown), context_note + table + drift + note)


def style_card(games, set_number):
    """How this player plays right now - and never a blank card.

    The old version printed one sentence built only from gaps that cleared
    the significance bar, and "Nothing about how you play stands out from
    the lobby" whenever none did. After the era fix that is the normal state:
    20 games is not enough for a seven-metric comparison to name anything,
    and the closest this player gets is z=2.01 against a 3.2 bar.

    Silence was the wrong answer to that, and it was not even honest -
    "nothing cleared the bar" is not "there is nothing to say about you".
    So the card always shows what the player actually does, in whole
    sentences with their own numbers, and marks a habit as settled only
    when it earns it. The threshold still governs what may be CALLED a
    habit; it no longer governs whether the card speaks.
    """
    if not games:
        return ('<p class="fine" style="margin:0">No games on record for '
                'this set yet.</p>')
    mine, room = tft.lobby_profiles(games, set_number)
    if not mine or not room:
        return ('<p class="fine" style="margin:0">No games on record for '
                'this set yet.</p>')
    shift = tft.latest_shift(mine)
    era = mine[shift["cut"]:] if shift else mine
    facts = tft.playstyle_facts(era, room)
    if not facts:
        return ('<p class="fine" style="margin:0">Not enough games yet to '
                'describe how you play.</p>')

    # The most interesting true thing about a player is usually that they
    # changed. latest_shift already knows; the page used to throw it away.
    if shift:
        said = tft.shift_sentence(shift)
        lead = (f'<p class="tip"><strong>{plural(len(era), "game")} ago '
                f'{esc(said)}.</strong><span>Everything below is the player '
                f'you have been since then, not an average of you and who '
                f'you were before.</span></p>')
    else:
        lead = (f'<p class="tip"><strong>Your style has held steady across '
                f'all {len(era)} games this set.</strong><span>No change big '
                f'enough to split your history into before and after.</span>'
                f'</p>')

    rows = []
    for f in facts[:4]:
        pill = ('<span class="pill ready">habit</span> ' if f["real"] else "")
        rows.append(f'<li><span>{pill}{esc(f["text"])}</span>'
                    f'<span class="k" style="font-size:12px;text-align:right">'
                    f'{esc(f["lobby"])}</span></li>')
    listed = f'<ul class="rowlist" style="margin:0">{"".join(rows)}</ul>'

    settled = sum(1 for f in facts if f["real"])
    if settled:
        note = why("What counts as a habit",
                   f'Measured over your last {len(era)} games against the '
                   f'{len(room)} boards everyone else in them played. Marked '
                   f'ones are far enough from the lobby to call a habit; the '
                   f'rest are just what you did.')
    else:
        note = why("What counts as a habit",
                   f'Measured over your last {len(era)} games against the '
                   f'{len(room)} boards everyone else in them played. None of '
                   f'these is far enough from the lobby to call a settled '
                   f'habit yet &mdash; that takes about '
                   f'{tft.MIN_PLAYSTYLE_GAMES} games and a clear gap. They '
                   f'are still what you did.')
    # The whole card folds - the user's call: "making the how you play a
    # drop down button entirely." It reads the same for weeks at a time
    # (an era moves every ~20 games), so on a daily page it is furniture;
    # the fold label carries the era size so the face still says what is
    # being offered. The sparse paths above stay unfolded on purpose - a
    # toggle that opens onto "no data" is a trap, not a card.
    return fold(f'Your last {plural(len(era), "game")}, next to your '
                f'lobbies', lead + listed + note)


def render(games, set_number, who, puuid, scouted=None):
    q = query(puuid, who)
    board_rows = pre.lobby_boards(games, set_number)
    room_rows = pre.room_table(board_rows, set_number)

    # -- what wins YOUR games: your own record, split by plan --------------
    record = comps.plan_record(games, set_number)
    wins = plan_card(record)
    # The answer goes in the heading, big, next to the question - the user's
    # call on the tiles' first pass: "emphasize the fast 9 more, its a little
    # too tiny." Only when there IS an answer; the no-verdict card keeps a
    # plain heading rather than promoting a plan it declined to name.
    best_plan = (record or {}).get("best") or {}
    wins_answer = (f' <span class="answer">{esc(best_plan["plan"])}</span>'
                   if best_plan.get("plan") else "")

    # -- scouting ----------------------------------------------------------
    results = scouted_rows(scouted) if scouted else ""
    box = f"""<form class="scoutbox" method="post" action="/scout">
  <input type="hidden" name="p" value="{esc(puuid)}">
  <input type="hidden" name="n" value="{esc(who)}">
  <textarea name="names" placeholder="one name per line"></textarea>
  <button type="submit">Scout them</button>
</form>"""

    # -- the meta and the recommendation, from the whole pool --------------
    m = comps.meta(set_number)

    def pill(conf):
        cls = {"confirmed": "ready", "solid": "ready",
               "early read": "part", "hunch": "blank"}[conf]
        return f'<span class="pill {cls}">{conf}</span>'

    # The meta reads the pool's newest patch; "how you play" reads YOUR
    # newest patch - the two can differ (seeded ladder games move to a new
    # patch before you've played it), and reading your mix off the pool's
    # patch would erase it entirely on every patch day.
    played = {g["_patch"] for g in games if g.get("_patch", "?") != "?"}
    my_patch = (max(played, key=lambda s: tuple(int(n) for n in s.split(".")))
                if played else None)
    patch_games = ([g for g in games if g.get("_patch") == my_patch]
                   if my_patch else games)
    on_patch = (f"of your patch-{my_patch} games" if my_patch else
                "of your recent games")

    # "For you" used to be its own card, and half of it was the meta's top
    # row restated - by construction: recommend() sorts the same rows by the
    # same pessimistic bound, so its "best" was always the list's first
    # entry. The user caught it: "it's basically just stuff that u can see
    # from the meta." The half the meta CANNOT say - which of these comps
    # fits the plan you actually play - survives as a pill on that comp's
    # row and one line under the list.
    rec = comps.recommend(patch_games, set_number, m["comps"])
    fit_name = rec["fit"]["comp"] if rec and rec["fit"] else None
    fit_note = ""
    if fit_name:
        f = rec["fit"]
        best_name = rec["best"]["comp"]
        opener = ("The top comp is also yours"
                  if fit_name == best_name else
                  f'Fits how you play: <strong>{esc(fit_name)}</strong>')
        fit_note = (
            f'<p class="fine" style="margin:10px 0 0">{opener} &mdash; you '
            f'play {esc(rec["top_plan"] or "")} in {rec["share"]:.0f}% '
            f'{on_patch}. Its usual carries: '
            f'{esc(", ".join(f["carries"][:3]))}.</p>')

    plan_rows = "".join(
        f'<li><span>{esc(p["plan"])}</span>'
        f'<span class="v">{p["avg"]:.2f}'
        f'<span class="k"> &middot; {p["n"]}</span></span></li>'
        for p in m["plans"])

    def comp_row(r, show_pill=True, show_carries=False):
        # A row earns a flag when its sample is still thin - solid rows
        # speak for themselves.
        flag = (" " + pill(r["confidence"])
                if show_pill and r["confidence"] in ("hunch", "early read")
                else "")
        if fit_name and r["comp"] == fit_name:
            flag += ' <span class="pill ready">fits you</span>'
        carries = ""
        if show_carries and r.get("carries"):
            carries = (f'<br><span class="k" style="font-size:11.5px">'
                       f'{esc(", ".join(r["carries"][:3]))}</span>')
        return (f'<li><span>{esc(r["comp"])}{flag}{carries}</span>'
                f'<span class="v">{r["avg"]:.2f}'
                f'<span class="k"> &middot; {r["n"]}</span></span></li>')

    # The headline list is COMPS - plan + identity, "Fast 9 Brawler" - not
    # the bare plan axis. Ordered like recommend(): by avg plus error bar,
    # so neither a hot dozen boards nor a big pile of mediocre ones can
    # claim the top. Rows still display the plain average.
    named = [r for r in m["comps"] if r["n"] >= 10 and r["comp"] != comps.NO_COMP
             and r["comp"] not in comps.PLAN_NAMES]
    top = sorted(named, key=lambda r: r["avg"] + r["moe"])[:5]
    # Right after a patch day every comp is an early read - five identical
    # pills say that five times. Say it once under the list instead, and
    # keep per-row pills for the mixed case where they distinguish rows.
    uniform = (top[0]["confidence"] if top
               and all(r["confidence"] == top[0]["confidence"] for r in top)
               and top[0]["confidence"] in ("hunch", "early read") else None)
    note = ""
    if uniform:
        word = "hunches" if uniform == "hunch" else "early reads"
        note = (f'<p class="fine" style="margin:8px 0 0">All {word} so far '
                f'&mdash; nothing has 30 boards on this patch yet. Seeding '
                f'below sharpens this.</p>')
    strong = (f'<ul class="rowlist">'
              f'{"".join(comp_row(r, show_pill=not uniform) for r in top)}'
              f'</ul>{note}'
              if top else
              '<p class="fine" style="margin:0">No comp has 10 boards yet '
              '- seed the pool below.</p>')
    more = ""
    if len(named) > len(top):
        # Carries show here rather than on the face - the "usual carries"
        # info the For you card used to hold, now per comp for all of them.
        more = (f'<details><summary>All {len(named)} comps with 10+ '
                f'boards</summary><ul class="rowlist" style="margin-top:10px">'
                f'{"".join(comp_row(r, show_carries=True) for r in named)}'
                f'</ul></details>')
    seed_form = f"""<form class="scoutbox" method="post" action="/seed"
  style="margin-top:14px">
  <input type="hidden" name="p" value="{esc(puuid)}">
  <input type="hidden" name="n" value="{esc(who)}">
  <button type="submit">Seed from the top of the ladder</button>
</form>""" + why(
        "What seeding does, and how to read the numbers",
        "Pulls recent games from the ladder&rsquo;s best players into the "
        "pool - a few minutes, and the read above gets sharper. Numbers are "
        "average placement over how many boards; hunch &lt;10, early read "
        "10+, solid 30+, confirmed 100+.")
    n_patches = len(m.get("patches") or [])
    patch_word = "patch" if n_patches <= 1 else "patches"
    patch_note = f"{patch_word} {m['patch']} only - " if m["patch"] else ""
    # 129 words of provenance and glossary, folded away. All of it is true and
    # none of it is what somebody wants in the ten seconds before they queue -
    # it is there for the reader who wants to argue with the number, and that
    # reader will open it.
    provenance = why(
        "Where these numbers come from",
        f'Everything above reads {patch_note}'
        f'{m["boards"]} final boards: your games on it, the seven other '
        f'boards in each of those lobbies, and seeded top-ladder games, '
        f'ranked. The number after a Reroll comp is the level the rolling '
        f'happened at: Reroll (7) means rolling at level 7 to 3-star a '
        f'3-cost carry, (6) a 2-cost at level 6, (4/5) a 1-cost at '
        f'level 4 or 5. Fast 8 is a board built on 2-star 4-costs; Fast 9 is '
        f'built on the legendaries. Where a comp is played both ways, the '
        f'list shows whichever does better. The top list is ordered '
        f'by the worst each average '
        f'could plausibly be given its sample, so a hot dozen boards '
        f'can&rsquo;t outrank a steady forty.')
    details = seed_form + provenance

    style = style_card(games, set_number)
    overall = tft.summarise([g["placement"] for g in games])

    body = f"""
{app_bar(who, set_number, q, "Pre-game")}
{stale_names_banner(set_number)}
{new_game_watcher(q)}
<div class="hero">
  <h1>Before you queue</h1>
  <span class="sub">{overall['games']} ranked games &middot; avg
  {overall['avg']:.2f} &middot; {overall['top4']:.0f}% top 4</span>
</div>
{purpose("Read this one before you hit find match. What has been working "
         "for you, what has been costing you, and what is strong right now "
         "in the lobbies you actually get put in.")}
<div class="grid">
  <div class="card full">
    <h3>What wins you games{wins_answer}</h3>
    {wins}
  </div>
  <div class="card full">
    <h3>Scout your lobby</h3>
    {box}
    {results}
    <p class="fine">Names from the loading screen, one per line. Anyone
    you&rsquo;ve played with before is instant. Filling this in
    automatically needs a helper on the PC running League &mdash; on the
    list.</p>
  </div>
  <div class="card">
    <h3>The meta &middot; {f'{patch_word} {m["patch"]} &middot; ' if m["patch"] else ""}{m["boards"]} boards</h3>
    {strong}
    {fit_note}
    {more}
    {details}
  </div>
  <div class="card">
    <h3>How each plan places</h3>
    <ul class="rowlist" style="margin:0">{plan_rows}</ul>
    <p class="fine">Same {m["boards"]} boards as the meta, grouped by plan
    instead of comp.</p>
  </div>
  <div class="card full">
    <h3>How you play</h3>
    {style}
  </div>
</div>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{BRAND} &middot; Pre-game &middot; {esc(who)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="app">{body}</div>
</body>
</html>
"""
