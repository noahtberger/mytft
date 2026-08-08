"""The pre-game screen as a page in the local app.

Built to be glanced at in a queue, not read: a couple of playstyle
reminders, a box to scout the lobby, and the comps that are strong in
your own games as tiles. Every full table lives behind a click.

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
from theme import CSS, esc


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


def scouted_table(scouted):
    """One line per player: who they are, how they do, what they play."""
    rows = []
    for r in scouted:
        if r.get("error"):
            rows.append(f'<tr><td>{esc(r["label"])}</td>'
                        f'<td colspan="2" style="color:var(--muted)">'
                        f'{esc(r["error"])}</td></tr>')
            continue
        plays = esc(r["plays"])
        if r["carry"]:
            plays += (f' <span style="color:var(--muted)">&middot; carries '
                      f'{esc(r["carry"])}</span>')
        if r["forces"]:
            plays += (f' <span class="pill blank">forces '
                      f'{esc(r["forces"])}</span>')
        note = (f' <span style="color:var(--muted)">({esc(r["note"])})</span>'
                if r.get("note") else "")
        rows.append(
            f'<tr><td>{esc(r["label"])}{note}</td>'
            f'<td class="num">{r["avg"]:.2f} <span style="color:var(--muted)">'
            f'over {r["games"]}</span></td>'
            f'<td>{plays}</td></tr>')
    return f"""<div class="tablewrap" style="margin-top:16px"><table>
      <thead><tr><th>player</th><th class="num">avg place</th>
      <th>plays</th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table></div>"""


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
        tip_html = "".join(
            f'<p style="margin:0 0 12px"><strong>{esc(t["do"])}</strong><br>'
            f'<span style="color:var(--muted);font-size:14px">why: '
            f'{esc(t["why"])}</span></p>' for t in tips)
        reminders = f'<div class="verdict">{tip_html}</div>'
    else:
        reminders = ('<p class="lede">No repeated pattern in your losses '
                     'right now. Play your game.</p>')

    # -- scouting ----------------------------------------------------------
    results_html = scouted_table(scouted) if scouted else ""
    box = f"""<form class="scoutbox" method="post" action="/scout">
  <input type="hidden" name="p" value="{esc(puuid)}">
  <input type="hidden" name="n" value="{esc(who)}">
  <textarea name="names" placeholder="one per line, like&#10;orobas#tomo&#10;KittyCat#EMT"></textarea>
  <button type="submit">Scout them</button>
</form>"""

    # -- strong comps here, as tiles; the table behind a click -------------
    winners = [(line, n, j) for line, n, j in room_rows
               if j["solid"] and j["gap"] > 0]
    losers = [(line, n, j) for line, n, j in room_rows
              if j["solid"] and j["gap"] < 0]
    if winners:
        tiles = "".join(
            f'<div class="stat"><span class="k">{esc(line)}</span>'
            f'<span class="n">{j["with"]["avg"]:.2f}</span>'
            f'<span class="pm">avg place &middot; '
            f'{100 * n / len(board_rows):.0f}% play it</span></div>'
            for line, n, j in winners)
        strong = f'<div class="stats">{tiles}</div>'
    else:
        strong = ('<p class="lede">Nothing clears the bar yet - no comp '
                  'here is proven stronger than the field.</p>')
    weak_note = ""
    if losers:
        weak_note = (f'<p class="lede" style="margin-top:14px">Weak here: '
                     f'{esc(", ".join(l for l, _, _ in losers))}.</p>')

    detail_rows = "".join(
        f'<tr><td>{esc(line)}</td>'
        f'<td class="num">{n}</td>'
        f'<td class="num">{100 * n / len(board_rows):.1f}%</td>'
        f'<td class="num">{j["with"]["avg"]:.2f}</td>'
        f'<td class="num">{j["without"]["avg"]:.2f}</td>'
        f'<td class="num">{j["gap"]:+.2f}</td></tr>'
        for line, n, j in room_rows)
    details = f"""<details>
  <summary>show the numbers behind this</summary>
  <div class="tablewrap"><table>
    <thead><tr><th>comp</th><th class="num">boards</th>
    <th class="num">rate</th><th class="num">avg with</th>
    <th class="num">avg without</th><th class="num">gap</th></tr></thead>
    <tbody>{detail_rows}</tbody></table></div>
  <p class="lede" style="margin-top:12px">All {len(board_rows)} boards from
  your own games. A comp only counts when the board actually built it.
  Gaps smaller than the error bars are listed but prove nothing.</p>
</details>""" if room_rows else ""

    era_n, verdicts, sentence = pre.playstyle_now(games, set_number)
    style = esc(sentence) if sentence else \
        "Nothing about how you play stands out from the lobby."

    body = f"""
<nav class="tabs">
  <a class="active" href="/pregame?{q}">Pre-game</a>
  <a href="/game?{q}">Post-game</a>
  <a href="/player?{q}">Stats</a>
</nav>
<header class="masthead">
  <div class="who">{esc(who)} &middot; Set {set_number} ranked</div>
  <h1>Before you queue</h1>
  <p class="standfirst">{style}</p>
</header>
<section>
  <p class="eyebrow">Reminders</p>
  <h2>Keep in mind</h2>
  {reminders}
</section>
<section>
  <p class="eyebrow">Scouting</p>
  <h2>Scout your lobby</h2>
  <p class="lede">Type the names from the loading screen, one per line.
  Anyone already in your saved games is instant. (Filling this in
  automatically needs a small helper on the PC running League &mdash;
  it&rsquo;s on the list.)</p>
  {box}
  {results_html}
</section>
<section>
  <p class="eyebrow">Your lobbies</p>
  <h2>Strong in your lobbies</h2>
  {strong}
  {weak_note}
  {details}
</section>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pre-game &middot; {esc(who)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">{body}</div>
</body>
</html>
"""
