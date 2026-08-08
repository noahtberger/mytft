"""The post-game debrief as a page in the local app.

serve.py renders this live from the games already on disk - no build step,
no cache, because the page changes every time a game finishes. All the
analysis comes from postgame.py, the same functions the terminal report
prints from, so the page and the terminal can never disagree about a game.

The one thing the page has that the terminal doesn't is names: match files
carry every player's Riot ID, so "the 2nd-place board" can be a person.
"""

from datetime import datetime
from urllib.parse import quote

import postgame as pg
import tftdata as tft
from theme import CSS, SCRIPT, esc

PICKER = 10   # recent games shown in the switcher row


def query(puuid, who):
    return f"p={quote(puuid)}&n={quote(who)}"


def cap(text):
    """Sentence-case a label without lower-casing the rest of it."""
    return text[0].upper() + text[1:] if text else text


def board_row(facts, me):
    """One lobby seat as a table row. Your row is marked, not just styled -
    colour alone shouldn't be the only thing saying which seat is yours."""
    prof = facts["prof"]
    name = f"{esc(facts['name'])}{' (you)' if me else ''}"
    comps = esc(" + ".join(facts["comps"])) if facts["comps"] else "&mdash;"
    carry = esc(facts["carry"]) if facts["carry"] else "&mdash;"
    if prof is None:
        cells = '<td colspan="4">disconnected</td>'
    else:
        cells = (f'<td class="num">{facts["items"]}</td>'
                 f'<td class="num">{prof["avg_cost"]:.1f}</td>'
                 f'<td class="num">{prof["big_units"]:.0f}</td>'
                 f'<td class="num">{prof["rerolled"]:.0f}</td>')
    return (f'<tr{" class=" + chr(34) + "me" + chr(34) if me else ""}>'
            f'<td class="num">{facts["place"]}</td><td>{name}</td>'
            f'<td>{comps}</td><td>{carry}</td>{cells}</tr>')


def render(games, level_par, set_number, idx, who, puuid):
    """The whole page for one game, newest first: idx 0 is the latest."""
    g = games[idx]
    q = query(puuid, who)
    when = datetime.fromtimestamp(g["_datetime"] / 1000).strftime("%b %d")
    placed = pg.ordinal(g["placement"])

    lobby = sorted(g["_lobby"], key=lambda p: p["placement"])
    facts = [pg.board_facts(b, set_number) for b in lobby]
    above = [b for b in lobby if b["placement"] < g["placement"]]
    won = not above

    # -- game switcher: your recent games, newest first --------------------
    picks = []
    for i, past in enumerate(games[:PICKER]):
        date = datetime.fromtimestamp(past["_datetime"] / 1000).strftime("%b %d")
        label = f"{pg.ordinal(past['placement'])}"
        cls = ' class="active"' if i == idx else ""
        picks.append(f'<a{cls} href="/game?{q}&amp;g={i + 1}" '
                     f'title="{date}">{label}</a>')
    picker = f'<nav class="tabs picker">{"".join(picks)}</nav>'

    # -- the lobby, as a table ---------------------------------------------
    rows = "".join(board_row(f, b["puuid"] == g["puuid"])
                   for b, f in zip(lobby, facts))
    table = f"""<div class="tablewrap"><table>
      <thead><tr><th class="num">#</th><th>player</th><th>comp</th>
      <th>carry</th><th class="num">items</th><th class="num">avg cost</th>
      <th class="num">4/5 costs</th><th class="num">3-stars</th></tr></thead>
      <tbody>{rows}</tbody></table></div>"""

    # -- what this one game says -------------------------------------------
    if won:
        margin = """<div class="card"><h3>This game</h3>
        <p style="margin:0"><strong>You won it.</strong> Nothing to debrief
        &mdash; the lobby below is the one you beat.</p></div>"""
    else:
        bullets = pg.margin_bullets(g, above, set_number, level_par)
        if bullets:
            items = "".join(f'<li><span>{esc(b)}</span></li>'
                            for b in bullets)
            margin = f"""<div class="card">
  <h3>This game</h3>
  <ul class="rowlist">{items}</ul>
  <p class="fine">One game only &mdash; the advice beside this is built
  from all of them.</p>
</div>"""
        else:
            margin = """<div class="card">
  <h3>This game</h3>
  <p style="margin:0">Nothing obvious on the board side &mdash; no contest,
  no level or item gap, no wasted gold. This one probably came down to
  fights.</p>
</div>"""

    # -- the advice, and the numbers it came from --------------------------
    mine, _ = tft.lobby_profiles(games, set_number)
    shift = tft.biggest_shift(mine)
    timeline = list(reversed(games))
    era_games = timeline[shift["cut"]:] if shift else timeline
    pattern_rows = pg.pattern(era_games, set_number)
    tips = pg.advice(pattern_rows,
                     pg.flat_context(games, set_number, era_games))

    def stat_list():
        return "".join(
            f'<li><span class="k">{esc(cap(pg.PATTERN_WORDS[k][0]))}</span>'
            f'<span class="v">'
            f'{esc(pg.PATTERN_WORDS[k][1] if mean > 0 else pg.PATTERN_WORDS[k][2])}'
            f' {mean:+.2f}</span></li>'
            for k, mean, _n in pattern_rows)

    if tips:
        tip_html = "".join(
            f'<p class="tip"><strong>{esc(t["do"])}</strong>'
            f'<span>{esc(t["why"])}</span></p>' for t in tips)
        pattern = f"""<div class="card">
  <h3>Next game</h3>
  {tip_html}
  <details>
    <summary>the pattern behind this</summary>
    <ul class="rowlist">{stat_list()}</ul>
    <p class="fine">Over your last {len(era_games)} games, this is how the
    boards that beat you differ from yours &mdash; gaps too big to be luck
    only. They also lived longer, which inflates the size and item rows by
    itself, so the advice only comes from the choice-shaped gaps.</p>
  </details>
</div>"""
    elif pattern_rows:
        pattern = f"""<div class="card">
  <h3>Next game</h3>
  <p style="margin:0">No tip this time. The boards that beat you do differ
  from yours, but only in ways that come from surviving longer &mdash; size
  and items &mdash; not from a choice you could copy.</p>
  <details>
    <summary>the pattern behind this</summary>
    <ul class="rowlist">{stat_list()}</ul>
  </details>
</div>"""
    else:
        pattern = f"""<div class="card">
  <h3>Next game</h3>
  <p style="margin:0">Nothing yet. Across your last {len(era_games)} games,
  no consistent difference between your boards and the ones that beat you
  has cleared its error bar &mdash; losses are landing for varied reasons,
  not one repeated one.</p>
</div>"""

    headline = "You won" if won else f"You placed {placed}"
    body = f"""
<div class="bar">
  <span class="who">{esc(who)} &middot; Set {set_number}</span>
  <nav class="tabs">
    <a href="/pregame?{q}">Pre-game</a>
    <a class="active" href="/game?{q}">Post-game</a>
    <a href="/player?{q}">Stats</a>
    <form method="post" action="/refresh" style="margin:0">
      <input type="hidden" name="p" value="{esc(puuid)}">
      <input type="hidden" name="n" value="{esc(who)}">
      <button type="submit">Check for a new game</button>
    </form>
  </nav>
</div>
<div class="hero">
  <h1>{headline}</h1>
  <span class="sub">{when} &middot; patch {esc(g["_patch"])}</span>
</div>
{picker}
<div class="grid" style="margin-top:20px">
  {margin}
  {pattern}
  <div class="card full">
    <h3>The lobby</h3>
    {table}
    <p class="fine">Everyone here played the same game you did, on the same
    patch, with the same items dropping.</p>
  </div>
</div>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Post-game &middot; {esc(who)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="app">{body}</div>
<div id="tip" role="status" aria-live="polite"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""
