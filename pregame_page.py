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
                "error": note or "no ranked games on record for this set"}
    prof = scout.profile(boards, set_number)
    plays = [name for name, _ in prof["lines"].most_common(2)]
    carry = [name for name, _ in prof["carries"].most_common(1)]
    forces = None
    top_build = prof["lines"].most_common(1)
    if top_build:
        name, n = top_build[0]
        if n >= 5 and n >= 0.4 * prof["games"]:
            forces = name
    return {"label": label, "error": None, "note": note,
            "games": prof["games"], "avg": prof["avg"],
            "plays": ", ".join(plays) if plays else "nothing twice",
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
    tips = pg.advice(pg.pattern(era_games, set_number),
                     pg.flat_context(games, set_number, era_games))
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

    # -- comps that are strong here, as a compact list ---------------------
    winners = [(line, n, j) for line, n, j in room_rows
               if j["solid"] and j["gap"] > 0]
    losers = [line for line, _n, j in room_rows if j["solid"] and j["gap"] < 0]
    if winners:
        strong = '<ul class="rowlist">' + "".join(
            f'<li><span>{esc(line)}</span>'
            f'<span class="v">{j["with"]["avg"]:.2f}'
            f'<span class="k"> &middot; {100 * n / len(board_rows):.0f}%</span>'
            f'</span></li>' for line, n, j in winners) + '</ul>'
        strong += ('<p class="fine">Average placement of boards that built '
                   'it, and how many boards do. Only comps with a real edge '
                   'are listed.</p>')
    else:
        strong = ('<p class="fine" style="margin:0">Nothing clears the bar '
                  'yet - no comp here is proven stronger than the field.</p>')
    if losers:
        strong += f'<p class="fine">Weak here: {esc(", ".join(losers))}.</p>'

    detail_rows = "".join(
        f'<tr><td>{esc(line)}</td>'
        f'<td class="num">{n}</td>'
        f'<td class="num">{100 * n / len(board_rows):.1f}%</td>'
        f'<td class="num">{j["with"]["avg"]:.2f}</td>'
        f'<td class="num">{j["without"]["avg"]:.2f}</td>'
        f'<td class="num">{j["gap"]:+.2f}</td></tr>'
        for line, n, j in room_rows)
    details = f"""<details>
  <summary>every comp, with the numbers</summary>
  <div class="tablewrap"><table>
    <thead><tr><th>comp</th><th class="num">boards</th>
    <th class="num">rate</th><th class="num">with</th>
    <th class="num">without</th><th class="num">gap</th></tr></thead>
    <tbody>{detail_rows}</tbody></table></div>
  <p class="fine">All {len(board_rows)} boards from your own games. A comp
  only counts when the board actually built it.</p>
</details>""" if room_rows else ""

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
  <div class="card">
    <h3>Strong in your lobbies</h3>
    {strong}
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
