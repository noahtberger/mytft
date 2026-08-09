"""The pre-game screen as a page in the local app.

A dashboard, not an article: a row of cards with one idea each, sized to
be glanced at between the queue popping and the loading screen. Anything
that needs a paragraph is small print or hidden behind a click.

Two rules from the product direction, both set by the user:

  - No comp prescriptions. "Learn Marauder" is exactly the advice this
    tracker promised not to give - the comp sites own comps; this thing
    coaches playstyle. The reminders here are the same playstyle advice
    the post-game page shows, and comp strength is shown as information
    ("strong in your lobbies"), never as an instruction.
  - Auto-scouting the live lobby is the goal, but it needs a helper on
    the machine running League (that is how the big apps do it - the
    spectator web API is production-key-only). Until that helper exists,
    the box is manual and honest about it.

Everything here is public match history from before the game starts,
which is the side of the line Riot's rules allow.
"""

from urllib.parse import quote

import comps
import postgame as pg
import pregame as pre
import scout
import tftdata as tft
from theme import (BRAND, CSS, app_bar, esc, new_game_watcher,
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


def render(games, set_number, who, puuid, scouted=None):
    q = query(puuid, who)
    board_rows = pre.lobby_boards(games, set_number)
    room_rows = pre.room_table(board_rows, set_number)

    # -- reminders: the same playstyle advice the post-game page gives -----
    mine, _ = tft.lobby_profiles(games, set_number)
    shift = tft.biggest_shift(mine)
    timeline = list(reversed(games))
    era_games = timeline[shift["cut"]:] if shift else timeline
    _, level_par = tft.load_games(puuid)
    tips = pg.advice(pg.pattern(era_games, set_number),
                     pg.flat_context(games, set_number, era_games),
                     pg.board_habits(era_games, level_par))
    if tips:
        reminders = "".join(
            f'<p class="tip"><strong>{esc(t["do"])}</strong>'
            f'<span>{esc(t["why"])}</span></p>' for t in tips)
    else:
        reminders = ('<p class="fine" style="margin:0">No repeated pattern '
                     'in your losses right now. Play your game.</p>')

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

    rec = comps.recommend(era_games, set_number, m["comps"])
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
                f'{esc(rec["top_plan"] or "")} in {rec["share"]:.0f}% of '
                f'your recent games &middot; Carries '
                f'{esc(", ".join(f["carries"][:3]))}</span></p>')
        elif rec["top_plan"]:
            lines.append(
                f'<p class="fine">That also fits you: you play '
                f'{esc(rec["top_plan"])} in {rec["share"]:.0f}% of your '
                f'recent games.</p>')
        foryou = "".join(lines)
    else:
        foryou = ('<p class="fine" style="margin:0">Not enough boards in '
                  'the pool yet. Seed some ladder games below.</p>')

    plan_rows = "".join(
        f'<li><span>{esc(p["plan"])}</span>'
        f'<span class="v">{p["avg"]:.2f}'
        f'<span class="k"> &middot; {p["n"]}</span></span></li>'
        for p in m["plans"])

    def comp_row(r):
        # A row earns a flag when its sample is still thin - solid rows
        # speak for themselves.
        flag = (" " + pill(r["confidence"])
                if r["confidence"] in ("hunch", "early read") else "")
        return (f'<li><span>{esc(r["comp"])}{flag}</span>'
                f'<span class="v">{r["avg"]:.2f}'
                f'<span class="k"> &middot; {r["n"]}</span></span></li>')

    # The headline list is COMPS - plan + identity, "fast 9 Brawler" - not
    # the bare plan axis. Solid samples outrank hot streaks: an 11-board
    # 3.00 must not sit above a 51-board 3.20, so solid rows lead and thin
    # ones only pad the tail, wearing their confidence pill.
    named = [r for r in m["comps"] if r["n"] >= 10 and r["comp"] != comps.NO_COMP
             and r["comp"] not in set(comps.REROLL_PLAN.values()) | {comps.FAST9}]
    solid = [r for r in named if r["n"] >= 30]
    top = (solid + [r for r in named if r["n"] < 30])[:5]
    strong = (f'<ul class="rowlist">{"".join(comp_row(r) for r in top)}</ul>'
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
    details = seed_form

    era_n, verdicts, sentence = pre.playstyle_now(games, set_number)
    if sentence:
        style = esc(sentence)
    elif era_n < tft.MIN_PLAYSTYLE_GAMES:
        style = (f"Not enough games yet &mdash; it takes about "
                 f"{tft.MIN_PLAYSTYLE_GAMES} before a habit can be told "
                 "apart from luck.")
    else:
        style = "Nothing about how you play stands out from the lobby."
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
    <h3>Keep in mind</h3>
    {reminders}
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
    <h3>The meta &middot; {m["boards"]} boards</h3>
    {strong}
    {more}
    <p class="fine">These numbers read {m["boards"]} final boards: your own
    games, the seven other boards in each of your lobbies, and top-ladder
    games pulled in by the seed button below. Ranked, this set only. The
    four ways to spend gold, ranked over the same boards:</p>
    <ul class="rowlist" style="margin-top:8px">{plan_rows}</ul>
    {details}
  </div>
  <div class="card">
    <h3>How you play</h3>
    <p style="margin:0">{style}</p>
    <p class="fine">Measured over your last {era_n} games against everyone
    else in them. The full chart is on the stats page.</p>
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
