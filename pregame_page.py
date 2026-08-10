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
from theme import (BRAND, CSS, app_bar, esc, new_game_watcher, plural,
                   stale_names_banner)


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
    # "No clear plan" is not a fifth way to spend gold, and it must not be
    # ranked beside the real ones: its raw 5.43 average is mostly survival,
    # not strategy - holding the death round fixed those boards place 0.03
    # worse (+/-0.09), ie. not at all. Listing it would put a made-up leak at
    # the top of the page.
    #
    # It is two different things in a trenchcoat, though. Two thirds are games
    # that ended before a plan could show. The rest are FAST 8 - finished
    # boards of 2-star 4-costs that park at level 8, which the plan axis
    # cannot see because they neither 3-star a cheap unit nor clear the 3.5
    # average-cost bar for Fast 9. Across the pool 147 of those survived to
    # stage 6 and averaged 2.93. Fast 8 is due as a real plan for set 18; the
    # copy below has to stay honest until then.
    #
    # Either way they still count in the "everything else" baseline, because
    # they are games this player played.
    shown = [r for r in record["rows"] if r["plan"] != comps.NO_PLAN]
    if not shown:
        return ('<p class="fine" style="margin:0">Not enough games yet to '
                'tell your plans apart.</p>')
    rows = "".join(plan_row(r, best["plan"] if best else None) for r in shown)
    table = (f'<ul class="rowlist" style="margin:0 0 12px">{rows}</ul>')

    if not best:
        played = max(shown, key=lambda r: r["n"])
        head = (f'<p class="tip"><strong>No plan has pulled ahead for you '
                f'yet.</strong><span>Your most played is '
                f'{esc(played["plan"])} at {plural(played["n"], "game")}. The number '
                f'on the right is how often each one ends in first &mdash; when '
                f'one of them separates from the rest, it will say so '
                f'here.</span></p>')
        return head + table

    head = (f'<p class="tip"><strong>{esc(best["plan"])} is where your 1sts '
            f'come from &mdash; {100 * best["first"]:.0f}% of those games, '
            f'against {100 * best["others"]:.0f}% of everything else.'
            f'</strong><span>Over {best["n"]} games. Your average placement '
            f'barely moves between plans; what changes is how often the game '
            f'ends with you on top.</span></p>')

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
            f'not listed: the board showed no plan the app can name. Most of '
            f'those ended early, but some are fast 8 boards &mdash; that is '
            f'not a plan it can pick out yet.</p>'
            if skipped else "")
    return head + table + drift + note


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
        note = (f'<p class="fine">Measured over your last {len(era)} games '
                f'against the {len(room)} boards everyone else in them '
                f'played. Marked ones are far enough from the lobby to call '
                f'a habit; the rest are just what you did.</p>')
    else:
        note = (f'<p class="fine">Measured over your last {len(era)} games '
                f'against the {len(room)} boards everyone else in them '
                f'played. None of these is far enough from the lobby to call '
                f'a settled habit yet &mdash; that takes about '
                f'{tft.MIN_PLAYSTYLE_GAMES} games and a clear gap. They are '
                f'still what you did.</p>')
    return lead + listed + note


def render(games, set_number, who, puuid, scouted=None):
    q = query(puuid, who)
    board_rows = pre.lobby_boards(games, set_number)
    room_rows = pre.room_table(board_rows, set_number)

    # -- what wins YOUR games: your own record, split by plan --------------
    record = comps.plan_record(games, set_number)
    wins = plan_card(record)

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

    rec = comps.recommend(patch_games, set_number, m["comps"])
    if rec:
        lines = []
        b = rec["best"]
        lines.append(
            f'<p class="tip"><strong>Strongest right now: {esc(b["comp"])} '
            f'&mdash; avg {b["avg"]:.2f} over {b["n"]} boards.</strong>'
            f'<span>{pill(b["confidence"])} &middot; Usual carries: '
            f'{esc(", ".join(b["carries"][:3]))}</span></p>')
        f = rec["fit"]
        if f and f["comp"] != b["comp"]:
            lines.append(
                f'<p class="tip"><strong>Closest to how you play: '
                f'{esc(f["comp"])} &mdash; avg {f["avg"]:.2f} over '
                f'{f["n"]} boards.</strong>'
                f'<span>{pill(f["confidence"])} &middot; You play '
                f'{esc(rec["top_plan"] or "")} in {rec["share"]:.0f}% '
                f'{on_patch} &middot; Carries '
                f'{esc(", ".join(f["carries"][:3]))}</span></p>')
        elif rec["top_plan"]:
            lines.append(
                f'<p class="fine">That also fits you: You play '
                f'{esc(rec["top_plan"])} in {rec["share"]:.0f}% '
                f'{on_patch}.</p>')
        foryou = "".join(lines)
    else:
        foryou = ('<p class="fine" style="margin:0">Not enough boards in '
                  'the pool yet. Seed some ladder games below.</p>')

    plan_rows = "".join(
        f'<li><span>{esc(p["plan"])}</span>'
        f'<span class="v">{p["avg"]:.2f}'
        f'<span class="k"> &middot; {p["n"]}</span></span></li>'
        for p in m["plans"])

    def comp_row(r, show_pill=True):
        # A row earns a flag when its sample is still thin - solid rows
        # speak for themselves.
        flag = (" " + pill(r["confidence"])
                if show_pill and r["confidence"] in ("hunch", "early read")
                else "")
        return (f'<li><span>{esc(r["comp"])}{flag}</span>'
                f'<span class="v">{r["avg"]:.2f}'
                f'<span class="k"> &middot; {r["n"]}</span></span></li>')

    # The headline list is COMPS - plan + identity, "Fast 9 Brawler" - not
    # the bare plan axis. Ordered like recommend(): by avg plus error bar,
    # so neither a hot dozen boards nor a big pile of mediocre ones can
    # claim the top. Rows still display the plain average.
    named = [r for r in m["comps"] if r["n"] >= 10 and r["comp"] != comps.NO_COMP
             and r["comp"] not in set(comps.REROLL_PLAN.values()) | {comps.FAST9}]
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
        more = (f'<details><summary>All {len(named)} comps with 10+ '
                f'boards</summary><ul class="rowlist" style="margin-top:10px">'
                f'{"".join(comp_row(r) for r in named)}</ul></details>')
    seed_form = f"""<form class="scoutbox" method="post" action="/seed"
  style="margin-top:14px">
  <input type="hidden" name="p" value="{esc(puuid)}">
  <input type="hidden" name="n" value="{esc(who)}">
  <button type="submit">Seed from the top of the ladder</button>
</form>
<p class="fine">Pulls recent games from the ladder&rsquo;s best players
into the pool - a few minutes, and the read above gets sharper. Numbers
are average placement over how many boards; hunch &lt;10, early read 10+,
solid 30+, confirmed 100+.</p>"""
    patch_note = f"patch {m['patch']} only - " if m["patch"] else ""
    provenance = (
        f'<p class="fine">Everything above reads {patch_note}'
        f'{m["boards"]} final boards: your games on it, the seven other '
        f'boards in each of those lobbies, and seeded top-ladder games, '
        f'ranked. The number after a Reroll comp is the level the rolling '
        f'happened at: Reroll (7) means rolling at level 7 to 3-star a '
        f'3-cost carry, (6) a 2-cost at level 6, (4/5) a 1-cost at '
        f'level 4 or 5. The top list is ordered by the worst each average '
        f'could plausibly be given its sample, so a hot dozen boards '
        f'can&rsquo;t outrank a steady forty.</p>')
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
<div class="grid">
  <div class="card full">
    <h3>What wins you games</h3>
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
  <div class="card full">
    <h3>For you</h3>
    {foryou}
  </div>
  <div class="card">
    <h3>The meta &middot; {f'patch {m["patch"]} &middot; ' if m["patch"] else ""}{m["boards"]} boards</h3>
    {strong}
    {more}
    <p class="fine">The four ways to spend gold:</p>
    <ul class="rowlist" style="margin-top:8px">{plan_rows}</ul>
    {details}
  </div>
  <div class="card">
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
