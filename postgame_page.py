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

import comps
import postgame as pg
import tftdata as tft
from theme import (BRAND, CSS, SCRIPT, app_bar, esc, new_game_watcher,
                   purpose, stale_names_banner, why)

PICKER = 10   # recent games shown in the switcher row

# A circular arrow, drawn rather than pulled from an icon font: the page has
# to work offline and with no external requests.
REFRESH_ICON = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" '
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true" focusable="false">'
    '<polyline points="23 4 23 10 17 10"/>'
    '<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>')


def query(puuid, who):
    return f"p={quote(puuid)}&n={quote(who)}"


def cap(text):
    """Sentence-case a label without lower-casing the rest of it."""
    return text[0].upper() + text[1:] if text else text


def board_row(facts, me, lpbreak=False):
    """One lobby seat as a table row. Your row is marked, not just styled -
    colour alone shouldn't be the only thing saying which seat is yours.
    `lpbreak` draws the line between 4th and 5th, where LP changes sign."""
    prof = facts["prof"]
    name = f"{esc(facts['name'])}{' (you)' if me else ''}"
    comp_cell = esc(facts["comp"]) if facts["comp"] else "&mdash;"
    carry = esc(facts["carry"]) if facts["carry"] else "&mdash;"
    if prof is None:
        cells = '<td colspan="4">disconnected</td>'
    else:
        cells = (f'<td class="num">{facts["items"]}</td>'
                 f'<td class="num">{prof["avg_cost"]:.1f}</td>'
                 f'<td class="num">{prof["big_units"]:.0f}</td>'
                 f'<td class="num">{prof["rerolled"]:.0f}</td>')
    row_classes = " ".join(c for c in ("me" if me else "",
                                       "lpbreak" if lpbreak else "") if c)
    attr = f' class="{row_classes}"' if row_classes else ""
    tone = "win" if facts["place"] <= 4 else "loss"
    return (f'<tr{attr}>'
            f'<td class="num"><span class="{tone}">{facts["place"]}</span></td>'
            f'<td>{name}</td>'
            f'<td>{comp_cell}</td><td>{carry}</td>{cells}</tr>')


def version_card(set_number, puuid, match_id, identity):
    """Where THIS board sat among the boards that played the same comp.

    About the one game in front of the reader, not about how they build the
    comp in general. The comp-level version of this card gave the same answer
    for every game of that comp, so 37% of this player's games sat in a run of
    identical cards - one of them 13 long - and he noticed: "im building a
    diff comp per game sometimes so it should compare only that game."

    It needs no games from this player at all, only a comp with enough boards
    in the pool, which is what closes the hole the comp-level card had: it
    went blank on 24% of games because it wanted 8 of their own boards first,
    and a comp they have played five times is exactly when they want a read.

    Returns None when the comp has too few boards to place one among, or the
    board has no comp identity at all, and the caller leaves the card out.
    """
    if not identity:
        return None
    try:
        import index
        import version as ver
        db = index.ensure()
        rows = ver.load(db, set_number, puuid)
    except Exception:                                # noqa: BLE001
        return None                                  # index unavailable; skip
    board = next((r for r in rows if r["match_id"] == match_id
                  and r["puuid"] == puuid), None)
    if board is None:
        return None
    study = ver.study_board(rows, board)
    if not study:
        return None

    # The outcome decides which side of the story gets told - the user's
    # rule: "if i lose, the board i played should have negative stats, and
    # if i win, positive". A losing board shows what sat on the side that
    # usually loses this comp; a winning board, the reverse. Without this
    # the card read as a trophy case over an 8th place.
    #
    # The guard against manufacturing a story: the side each metric "usually
    # loses" on comes from the field's own placement split, never from this
    # game, and a finding's kicker still claims a consequence only when that
    # split clears its bar. Leans select; only proof speaks. Anti-aligned
    # findings are not shown as tiles, but if they are ALL that stood out,
    # the card says so instead of going quiet - "the shape sat on the
    # winning side and lost anyway" is the most useful sentence this card
    # can produce.
    top4 = board["placement"] <= 4

    def lean(f):
        """+1 this finding sits on the side that usually wins the comp,
        -1 the side that usually loses, 0 when the field gives no direction."""
        if f["kind"] == "board":
            m = f["matters"]
            if not m:
                return 0
            return 1 if f["high"] == (m["gap"] < 0) else -1
        return -1 if f["effect"]["gap"] > 0 else 1

    found = [f for f in study["found"]
             if ver.real_board(f) or ver.real_choice(f) or ver.worth_saying(f)]
    want = 1 if top4 else -1
    aligned = [f for f in found if lean(f) == want]
    # Proven consequences outrank leans; the sort is stable so strength
    # order from study_board holds within each group.
    aligned.sort(key=lambda f: not (ver.real_board(f) or ver.real_choice(f)))
    neutral = [f for f in found if lean(f) == 0]
    against = [f for f in found if lean(f) == -want]
    shown = (aligned + neutral)[:3]

    # Board findings render as tiles - the same value-plus-kicker grammar as
    # the pre-game verdict, because "3.5 gold a unit, more than 94% of
    # theirs" is a number pair wearing a sentence. The percentile line
    # mirrors ver.plain()'s wording and its consequence logic exactly, so
    # the tile and the sentence can never disagree about which side is good.
    # Carry and plan findings stay sentences: "Akali costs 0.3 places here"
    # has no value-vs-pack pair to tile.
    tiles, lines = "", []
    for f in shown:
        if f["kind"] != "board":
            lines.append(ver.plain(f))
            continue
        v = f["you"]
        n = f"{v:.0f}" if float(v) == int(v) else f"{v:.1f}"
        side = (f'more than {f["below"] * 100:.0f}% of theirs'
                if f["high"] else
                f'{f["above"] * 100:.0f}% of theirs had more')
        m = f["matters"]
        if m and m["real"]:
            side += (" &middot; the better side"
                     if f["high"] == (m["gap"] < 0) else
                     f' &middot; other side worth ~{abs(m["gap"]):.1f} places')
        tiles += (f'<div class="stat"><span class="n">{n}</span>'
                  f'<span class="k" '
                  f'data-tip="{ver.METRIC_TIPS[f["key"]]}">'
                  f'{esc(ver.SHAPE[f["key"]])}</span>'
                  f'<span class="pm" style="display:block;margin-top:2px">'
                  f'{side}</span></div>')
    if tiles:
        tiles = f'<div class="stats" style="margin:0 0 12px">{tiles}</div>'

    # When the shape disagrees with the result - better than the pack but
    # lost, or worse and won - the card does not appear. The previous
    # version said it out loud ("the 8th didn't come from the shape of it")
    # and the user saw it live and called it: "if my board is better than
    # the average but i lost, it should just not pop up, and vice versa."
    # So absence now also means "the board was not the story". Middle of
    # the pack still speaks, because "nothing stood out" aligns with any
    # result and going quiet there would read as the app having no opinion.
    if not shown and against:
        return None
    if not shown:
        lines = ["This board sat in the middle of the pack on everything "
                 "measured - whatever went wrong, it was not the shape of it."]
    items = "".join(f'<li><span>{esc(t)}</span></li>' for t in lines)
    listed = (f'<ul class="rowlist" style="margin:0">{items}</ul>'
              if items else "")
    picked = (f' Picked for the {"winning" if top4 else "losing"} side of '
              f'the pack first.' if shown else "")
    return f"""
  <div class="card full">
    <h3>This board, next to the {esc(identity)} boards in your lobbies</h3>
    {tiles}
    {listed}
    <p class="fine">Against {study["field"]} {esc(identity)} boards from games
    you were in, each compared at the round it died on.{picked}</p>
  </div>"""


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
        # Top 4 gains LP, bottom 4 loses it - the split TFT itself cares
        # about, so the row of recent games reads as a win/loss streak.
        classes = ["win" if past["placement"] <= 4 else "loss"]
        if i == idx:
            classes.append("active")
        # The label is wrapped so the chip can carry a coloured edge without
        # a CSS border, which the bevel would clip away at the corners.
        picks.append(f'<a class="{" ".join(classes)}" '
                     f'href="/game?{q}&amp;g={i + 1}" '
                     f'title="{date}"><span>{label}</span></a>')
    picker = (f'<nav class="tabs picker" aria-label="Recent games">'
              f'{"".join(picks)}</nav>')

    # -- the lobby, as a table ---------------------------------------------
    rows = "".join(board_row(f, b["puuid"] == g["puuid"],
                             lpbreak=f["place"] == 5)
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
        # A line, not a box: the page was already four cards and the feedback
        # that prompted this was that there are too many of them. It belongs
        # here anyway - "you were contested" and "here is what nobody took"
        # are halves of one thought.
        moved = pg.pivot(g, lobby, pg.comp_cores(games, set_number), set_number)
        if moved:
            bullets = bullets + [
                f"Nobody in that lobby took {moved['comp']}, and you already "
                f"had {len(moved['shared'])} of its {moved['core']} core "
                f"units: {', '.join(moved['shared'])}."]
        if bullets:
            items = "".join(f'<li><span>{esc(b)}</span></li>'
                            for b in bullets)
            margin = f"""<div class="card">
  <h3>This game</h3>
  <ul class="rowlist">{items}</ul>
  <p class="fine">One game only &mdash; the advice beside this is built
  from all of them.{" Both halves of the pivot line are read off finished "
  "boards, so it is what was open in hindsight, not what you could see at "
  "the time." if moved else ""}</p>
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
    shift = tft.latest_shift(mine)
    timeline = list(reversed(games))
    era_games = timeline[shift["cut"]:] if shift else timeline
    pattern_rows = pg.pattern(era_games, set_number)
    tips = pg.advice(pattern_rows,
                     pg.flat_context(games, set_number, era_games),
                     pg.board_habits(era_games, level_par))

    def stat_list():
        return "".join(
            f'<li><span class="k">{esc(cap(pg.PATTERN_WORDS[r["key"]][0]))}'
            f'{"" if r["big"] else " (small)"}</span>'
            f'<span class="v">'
            f'{esc(cap(pg.PATTERN_WORDS[r["key"]][1] if r["gap"] > 0 else pg.PATTERN_WORDS[r["key"]][2]))}'
            f' {r["gap"]:+.2f}</span></li>'
            for r in pattern_rows)

    if tips:
        tip_html = "".join(
            f'<p class="tip"><strong>{esc(t["do"])}</strong>'
            f'<span>{esc(t["why"])}</span></p>' for t in tips)
        pattern = f"""<div class="card">
  <h3>Next game</h3>
  {tip_html}
  <details>
    <summary>The pattern behind this</summary>
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
    <summary>The pattern behind this</summary>
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

    # Icon-only, but never unlabelled: the title is the hover tooltip and
    # the aria-label is what a screen reader announces.
    refresh_form = (
        f'<form method="post" action="/refresh" style="margin:0">'
        f'<input type="hidden" name="p" value="{esc(puuid)}">'
        f'<input type="hidden" name="n" value="{esc(who)}">'
        f'<button class="icon" type="submit" aria-label="Refresh"'
        f' title="Refresh - fetch any game you just played">{REFRESH_ICON}'
        f'</button></form>')
    top4 = g["placement"] <= 4
    tone = "win" if top4 else "loss"
    # Where this board sat among the boards that played the same comp.
    parts = comps.parts(g, set_number) or (None, None)
    yours = version_card(set_number, puuid, g["_match_id"], parts[1]) or ""

    headline = (f'You <span class="{tone}">won</span>' if won else
                f'You placed <span class="{tone}">{placed}</span>')
    body = f"""
{app_bar(who, set_number, q, 'Post-game', extra=refresh_form)}
{stale_names_banner(set_number)}
{new_game_watcher(q)}
<div class="hero">
  <h1>{headline}</h1>
  <span class="sub">{when} &middot; patch {esc(g["_patch"])}</span>
</div>
{purpose("One game, pulled apart. Your board against the seven that were "
         "in the lobby with you - same patch, same players, same items "
         "dropping - so the comparison needs no adjusting.")}
{picker}
<div class="grid" style="margin-top:20px">
  {margin}
  {pattern}
  {yours}
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
<title>{BRAND} &middot; Post-game &middot; {esc(who)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="app">{body}</div>
<div id="tip" role="status" aria-live="polite"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""
