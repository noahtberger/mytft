"""The pre-game screen as a page in the local app.

Everything you'd want in the queue: your leaks (blunt, with the numbers as
the receipt), a box to scout the lobby you're about to fight, the meta
measured in your own games, what your record has proven, and the one-line
reminder of how you play. All the analysis comes from pregame.py and
scout.py, the same code the terminal reports run.

The lobby box is manual on purpose: reading your live lobby needs Riot's
spectator API, which personal keys don't get - and typing two or three
names you recognise from the loading screen is most of the value anyway.
Everything here is public match history from before the game starts, which
is the side of the line Riot's rules allow.
"""

from urllib.parse import quote

import pregame as pre
import scout
import tftdata as tft
from theme import CSS, esc


def query(puuid, who):
    return f"p={quote(puuid)}&n={quote(who)}"


def player_read(label, boards, set_number, note=None):
    """One scouted player, as data for the results table."""
    if not boards:
        return {"label": label,
                "error": note or "no ranked games on record for this set"}
    prof = scout.profile(boards, set_number)
    forces = None
    top_build = prof["lines"].most_common(1)
    if top_build:
        name, n = top_build[0]
        if n >= 5 and n >= 0.4 * prof["games"]:
            forces = name
    return {"label": label, "error": None, "note": note,
            "games": prof["games"], "avg": prof["avg"],
            "builds": ", ".join(scout.top(prof["lines"], prof["games"])),
            "carries": ", ".join(scout.top(prof["carries"], prof["games"])),
            "forces": forces}


def leak_tips(gaps):
    """The rate gaps as blunt do/why pairs, loudest first."""
    grouped = {}
    for row in gaps:
        grouped.setdefault(pre.bucket(row), []).append(row)

    tips = []
    for line, my_pct, room_pct, v in sorted(
            grouped.get("leak", ()), key=lambda r: (-r[3]["gap"], r[0])):
        tips.append({
            "do": f"Learn {line}.",
            "why": f"the lobby wins with it ({v['gap']:+.2f} places) and "
                   f"you build it {my_pct:.0f}% of games to their "
                   f"{room_pct:.0f}%."})
    for line, my_pct, room_pct, v in sorted(
            grouped.get("bad_force", ()), key=lambda r: (r[3]["gap"], r[0])):
        tips.append({
            "do": f"Ease off {line}.",
            "why": f"the lobby loses with it ({v['gap']:+.2f}) and you "
                   f"build it {my_pct:.0f}% to their {room_pct:.0f}%."})
    for line, my_pct, room_pct, v in sorted(
            grouped.get("force", ()), key=lambda r: (r[2] - r[1], r[0])):
        if v and v["solid"] and v["gap"] > 0:
            continue   # heavy on a comp the lobby also wins with - fine
        tips.append({
            "do": f"Watch your {line} rate.",
            "why": f"you build it {my_pct:.0f}% to the lobby's "
                   f"{room_pct:.0f}%, and nothing in the lobby's results "
                   "says it pays a premium."})

    quiet = sorted(r[0] for r in grouped.get("skip_neutral", ()))
    losers = sorted(r[0] for r in grouped.get("skip_loser", ()))
    return tips, quiet, losers


def scouted_table(scouted):
    rows = []
    for r in scouted:
        if r.get("error"):
            rows.append(f'<tr><td>{esc(r["label"])}</td>'
                        f'<td colspan="4" style="color:var(--muted)">'
                        f'{esc(r["error"])}</td></tr>')
            continue
        note = f' <span style="color:var(--muted)">({esc(r["note"])})</span>' \
            if r.get("note") else ""
        rows.append(
            f'<tr><td>{esc(r["label"])}{note}</td>'
            f'<td class="num">{r["games"]}</td>'
            f'<td class="num">{r["avg"]:.2f}</td>'
            f'<td>{esc(r["builds"])}</td><td>{esc(r["carries"])}</td></tr>')
    table = f"""<div class="tablewrap"><table>
      <thead><tr><th>player</th><th class="num">games seen</th>
      <th class="num">avg place</th><th>builds</th><th>carries</th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table></div>"""

    forcers = [r for r in scouted if r.get("forces")]
    warns = ""
    if forcers:
        lines = "".join(
            f'<p style="margin:0 0 10px"><strong>{esc(r["label"])} forces '
            f'{esc(r["forces"])}.</strong><br>'
            f'<span style="color:var(--muted);font-size:14px">why: '
            f'{esc(r["builds"].split(",")[0])} of their games. Don&rsquo;t '
            'fight them for it unless you can take it early.</span></p>'
            for r in forcers)
        warns = f'<div class="verdict" style="margin-top:16px">{lines}</div>'
    return table + warns


def render(games, set_number, who, puuid, scouted=None):
    q = query(puuid, who)
    board_rows = pre.lobby_boards(games, set_number)
    room_rows = pre.room_table(board_rows, set_number)
    gaps = pre.rate_gaps(games, board_rows, set_number, room_rows)
    tips, quiet, losers = leak_tips(gaps)

    # -- your leaks --------------------------------------------------------
    if tips:
        tip_html = "".join(
            f'<p style="margin:0 0 12px"><strong>{esc(t["do"])}</strong><br>'
            f'<span style="color:var(--muted);font-size:14px">why: '
            f'{esc(t["why"])}</span></p>' for t in tips)
        leaks = f'<div class="verdict">{tip_html}</div>'
    else:
        leaks = ('<p class="lede">Your comp choices match the lobby '
                 'everywhere it matters. No leaks to point at.</p>')
    quiet_note = ""
    if quiet:
        quiet_note += (f'<p class="lede" style="margin-top:14px">You also '
                       f'build {esc(", ".join(quiet))} less than the lobby '
                       'does, but its results with them are ordinary - '
                       'nothing proven is being missed.</p>')
    if losers:
        quiet_note += (f'<p class="lede" style="margin-top:6px">And you '
                       f'skip {esc(", ".join(losers))}, which the lobby '
                       'loses with. Good.</p>')

    # -- the lobby box (and results, when they exist) ----------------------
    results_html = ""
    if scouted:
        results_html = scouted_table(scouted)
    box = f"""<form class="scoutbox" method="post" action="/scout">
  <input type="hidden" name="p" value="{esc(puuid)}">
  <input type="hidden" name="n" value="{esc(who)}">
  <textarea name="names" placeholder="one per line, like&#10;orobas#tomo&#10;KittyCat#EMT"></textarea>
  <button type="submit">Scout them</button>
</form>"""

    # -- the meta, in your own lobbies -------------------------------------
    meta_rows = []
    for line, n, judged in room_rows:
        verdict = ("lobby WINS with it" if judged["solid"] and judged["gap"] > 0
                   else "lobby LOSES with it" if judged["solid"]
                   else "no real difference")
        meta_rows.append(
            f'<tr><td>{esc(line)}</td>'
            f'<td class="num">{n}</td>'
            f'<td class="num">{100 * n / len(board_rows):.1f}%</td>'
            f'<td class="num">{judged["with"]["avg"]:.2f}</td>'
            f'<td class="num">{judged["without"]["avg"]:.2f}</td>'
            f'<td class="num">{judged["gap"]:+.2f}</td>'
            f'<td>{verdict}</td></tr>')
    patches = sorted({g["_patch"] for g in games},
                     key=lambda s: tuple(int(x) for x in s.split(".")))
    meta = f"""<div class="tablewrap"><table>
      <thead><tr><th>comp</th><th class="num">boards</th>
      <th class="num">rate</th><th class="num">with</th>
      <th class="num">without</th><th class="num">gap</th><th></th></tr></thead>
      <tbody>{"".join(meta_rows)}</tbody></table></div>""" if meta_rows else \
        ('<p class="lede">No comp has enough boards yet - early in a set '
         'this table is empty on purpose.</p>')

    # -- proven + playstyle ------------------------------------------------
    data = tft.coverage_data(games, set_number)
    proven_rows = pre.proven(games, data)
    if proven_rows:
        prow = "".join(
            f'<tr><td>{esc(label)}</td>'
            f'<td class="num">{j["with"]["games"]}</td>'
            f'<td class="num">{j["with"]["avg"]:.2f}</td>'
            f'<td class="num">{j["without"]["avg"]:.2f}</td>'
            f'<td class="num">{j["gap"]:+.2f}</td>'
            f'<td>{"genuinely " + ("better" if j["gap"] > 0 else "worse")
                  if j["solid"] else "no real difference"}</td></tr>'
            for label, j in proven_rows)
        proven = f"""<div class="tablewrap"><table>
      <thead><tr><th></th><th class="num">games</th><th class="num">with</th>
      <th class="num">without</th><th class="num">gap</th><th></th></tr></thead>
      <tbody>{prow}</tbody></table></div>"""
    else:
        proven = ('<p class="lede">Nothing has 30 games yet, so nothing '
                  'here can be trusted. It fills in as you repeat comps.</p>')

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
  <p class="standfirst">{style} <span style="color:var(--muted)">
  ({era_n} recent games &mdash; full chart on the stats page.)</span></p>
</header>
<section>
  <p class="eyebrow">Your leaks</p>
  <h2>What to fix</h2>
  {leaks}
  {quiet_note}
</section>
<section>
  <p class="eyebrow">Scouting</p>
  <h2>Scout your lobby</h2>
  <p class="lede">Type the names you see on the loading screen, one per
  line. Anyone already in your saved games is instant; a new player takes
  a few seconds. This is public pre-game history &mdash; reading the live
  board would be against Riot&rsquo;s rules, and this isn&rsquo;t that.</p>
  {box}
  {results_html}
</section>
<section>
  <p class="eyebrow">The meta, measured here</p>
  <h2>What wins in your lobbies</h2>
  <p class="lede">All {len(board_rows)} boards from your own games: how
  boards that built each comp placed, against boards that didn&rsquo;t.
  These games span patches {esc(patches[0])}&ndash;{esc(patches[-1])}, so a
  recently nerfed comp still carries its old wins here.</p>
  {meta}
</section>
<section>
  <p class="eyebrow">Proven</p>
  <h2>What your own record can back up</h2>
  <p class="lede">Only things you&rsquo;ve played 30+ times. Everything
  else you half-remember about your comps is still a hint.</p>
  {proven}
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
