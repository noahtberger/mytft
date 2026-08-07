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
        margin = """<div class="verdict"><p class="eyebrow">This game</p>
        <p style="margin:0"><strong>You won this one.</strong> Nothing to
        debrief &mdash; the table above is the lobby you beat.</p></div>"""
    else:
        bullets = pg.margin_bullets(g, above, set_number, level_par)
        if bullets:
            items = "".join(f"<li>{esc(b)}</li>" for b in bullets)
            margin = f"""<section>
  <p class="eyebrow">This game</p>
  <h2>What stood out</h2>
  <p class="lede">Just this one game. One game proves nothing on its own,
  which is why the advice below is built from all of them.</p>
  <ul class="notes">{items}</ul>
</section>"""
        else:
            margin = """<section>
  <p class="eyebrow">This game</p>
  <h2>What stood out</h2>
  <p class="lede">Nothing obvious on the board side &mdash; no contest, no
  level or item gap, no wasted gold. This one probably came down to
  fights.</p>
</section>"""

    # -- the advice, and the numbers it came from --------------------------
    mine, _ = tft.lobby_profiles(games, set_number)
    shift = tft.biggest_shift(mine)
    timeline = list(reversed(games))
    era_games = timeline[shift["cut"]:] if shift else timeline
    pattern_rows = pg.pattern(era_games, set_number)
    tips = pg.advice(pattern_rows)

    def stat_list():
        return "".join(
            f'<li><strong>{esc(cap(pg.PATTERN_WORDS[k][0]))}</strong>: '
            f'{esc(pg.PATTERN_WORDS[k][1] if mean > 0 else pg.PATTERN_WORDS[k][2])} '
            f'({mean:+.2f})</li>'
            for k, mean, _n in pattern_rows)

    if tips:
        tip_html = "".join(f'<p style="margin:0 0 10px"><strong>'
                           f'{esc(t)}</strong></p>' for t in tips)
        pattern = f"""<section>
  <p class="eyebrow">Next game</p>
  <h2>What to work on</h2>
  <div class="verdict">{tip_html}</div>
  <p class="lede" style="margin-top:22px">Why: over your last
  {len(era_games)} games, the boards that beat you keep differing from
  yours in the same ways. Only gaps too big to be luck are listed.</p>
  <ul class="notes">{stat_list()}</ul>
  <p class="lede" style="margin-top:14px">One caution: boards that beat you
  also lived longer, which inflates the size and item rows by itself. The
  advice only comes from the choice-shaped gaps &mdash; committing,
  rerolling, itemizing &mdash; never from the scoreboard ones.</p>
</section>"""
    elif pattern_rows:
        pattern = f"""<section>
  <p class="eyebrow">Next game</p>
  <h2>What to work on</h2>
  <p class="lede">No tip this time: the boards that beat you do differ from
  yours, but only in ways that come from surviving longer &mdash; size and
  items &mdash; not from a choice you could copy.</p>
  <ul class="notes">{stat_list()}</ul>
</section>"""
    else:
        pattern = f"""<section>
  <p class="eyebrow">Next game</p>
  <h2>What to work on</h2>
  <p class="lede">Nothing yet. Across your last {len(era_games)} games, no
  consistent difference between your boards and the ones that beat you has
  cleared its error bar &mdash; losses are landing for varied reasons, not
  one repeated one.</p>
</section>"""

    headline = "You won" if won else f"You placed {placed}"
    body = f"""
<nav class="tabs">
  <a href="/player?{q}">Stats</a>
  <a class="active" href="/game?{q}">Post-game</a>
  <form method="post" action="/refresh" style="margin:0">
    <input type="hidden" name="p" value="{esc(puuid)}">
    <input type="hidden" name="n" value="{esc(who)}">
    <button type="submit">Check for a new game</button>
  </form>
</nav>
<header class="masthead">
  <div class="who">{esc(who)} &middot; Set {set_number} ranked</div>
  <h1>{headline}</h1>
  <p class="standfirst">{when}, patch {esc(g["_patch"])} &mdash; game
  {idx + 1} of {len(games)}, newest first. Your recent games:</p>
  {picker}
</header>
<section>
  <p class="eyebrow">The lobby</p>
  <h2>All eight boards</h2>
  <p class="lede">Sorted by final placement. Everyone here played the same
  game you did, so the boards above yours are worth a look.</p>
  {table}
</section>
{margin}
{pattern}
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
<div class="wrap">{body}</div>
<div id="tip" role="status" aria-live="polite"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""
