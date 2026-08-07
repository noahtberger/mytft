"""Build a self-contained web page out of your saved matches.

Run:  python build_site.py
      open site/index.html

Writes two files from one template:

  site/index.html     a complete HTML page - double-click it, no server, no
                      internet, no build step. Everything (styles, charts,
                      hover behaviour) is inlined, so you can mail the file
                      to someone and it works.
  site/artifact.html  the same page as a fragment, for publishing to a URL
                      that wraps it in its own <html>/<head>.

The numbers all come from tftdata.coverage_data, the same function coverage.py
prints from. That is deliberate: two views of one dataset are worse than
either alone if they can drift into disagreeing.

The charts are plain SVG built here in Python, so they render with JavaScript
switched off. The only script on the page attaches hover tooltips.
"""

import html
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

import tftdata as tft

OUT_DIR = Path("site")
TARGET = tft.MIN_ACTIONABLE


def esc(text):
    return html.escape(str(text), quote=True)


def plural(n, word, many=None):
    """'1 line' / '3 lines'. Small thing, but '1 different lines' on the page
    reads as a bug in the maths even when the maths is right."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def fit(text, gutter, size):
    """Trim text that would overrun a fixed label column.

    SVG text does not wrap and right-anchored text runs off the left edge
    rather than being clipped visibly, so an overlong label just loses its
    first characters and reads as a typo. Monospace makes the width
    predictable: roughly 0.6 x the font size per character. Set 18 will bring
    trait names nobody has measured, so this guards the layout in advance.
    """
    limit = int(gutter / (size * 0.6))
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


# ---------------------------------------------------------------- charts ----
# Every chart is a viewBox'd SVG with no fixed width, so it scales to its
# container. Colours are CSS variables rather than literals, which is what
# makes one drawing work in both themes without redrawing it.


def placement_chart(placements):
    """Distribution of finishes 1-8.

    Placement is the one number TFT actually gives you, and it has a natural
    midpoint: 4th and up pays LP, 5th and down loses it. So this is drawn as
    a diverging chart around that break, not a plain histogram - the split is
    the meaning. The teal/rose pair is hard to separate under deuteranopia,
    so the break is also drawn as a labelled rule and every bar carries its
    own count: colour is never the only thing saying which side you're on.
    """
    counts = [placements.count(p) for p in range(1, 9)]
    top = max(counts) or 1
    w, h = 680, 232
    left, right, base, head = 8, 8, 196, 34
    slot = (w - left - right) / 8
    bar_w = slot - 12

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             f'aria-label="Placement distribution across {len(placements)} games">']

    # Baseline and the top-4 break.
    parts.append(f'<line x1="{left}" y1="{base}" x2="{w - right}" y2="{base}" '
                 'class="axis"/>')
    split = left + slot * 4
    parts.append(f'<line x1="{split}" y1="{head - 12}" x2="{split}" y2="{base + 6}" '
                 'class="rule-dash"/>')
    parts.append(f'<text x="{split - 10}" y="{head - 18}" class="tick" '
                 'text-anchor="end">top 4</text>')
    parts.append(f'<text x="{split + 10}" y="{head - 18}" class="tick">bottom 4</text>')

    for i, count in enumerate(counts):
        place = i + 1
        bar_h = (count / top) * (base - head)
        x = left + slot * i + (slot - bar_w) / 2
        y = base - bar_h
        colour = "var(--pos)" if place <= 4 else "var(--neg)"
        pct = 100 * count / len(placements)
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'rx="4" fill="{colour}" class="mark" '
            f'data-tip="#{place} &middot; {count} games &middot; {pct:.0f}% of your finishes"/>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" '
                     f'class="val" text-anchor="middle">{count}</text>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{base + 22}" '
                     f'class="tick" text-anchor="middle">{place}</text>')

    parts.append("</svg>")
    return "".join(parts)


def coverage_chart(rows):
    """Games built per line, against the 30 needed to draw a conclusion.

    Length carries the magnitude, so colour does not have to - one hue for
    every bar. The target rule is the point of the whole chart: it is what
    turns "25 games" from a score into "5 short".
    """
    w = 680
    row_h, gap = 26, 6
    label_w, right_pad = 132, 92
    plot = w - label_w - right_pad
    h = len(rows) * (row_h + gap) + 26

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             f'aria-label="Games built per line against the {TARGET}-game bar">']
    parts.append(f'<line x1="{label_w + plot}" y1="6" x2="{label_w + plot}" '
                 f'y2="{h - 20}" class="rule-dash"/>')
    parts.append(f'<text x="{label_w + plot + 6}" y="{h - 6}" class="tick">'
                 f'{TARGET} games</text>')

    for i, (name, n, lobby) in enumerate(rows):
        y = 6 + i * (row_h + gap)
        bar = plot * min(n, TARGET) / TARGET
        done = n >= TARGET
        parts.append(f'<text x="{label_w - 10}" y="{y + row_h * 0.72:.0f}" '
                     f'class="rowlabel" text-anchor="end">'
                     f'{esc(fit(name, label_w - 14, 12))}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{plot}" height="{row_h}" '
                     'rx="4" class="track"/>')
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{bar:.1f}" height="{row_h}" rx="4" '
            f'fill="{"var(--pos)" if done else "var(--accent)"}" class="mark" '
            f'data-tip="{esc(name)} &middot; built in {n} games &middot; '
            f'{"past the bar" if done else f"{TARGET - n} more for a verdict"} '
            f'&middot; lobby builds it in {lobby:.1f}% of boards"/>')
        parts.append(f'<text x="{label_w + plot + 6}" y="{y + row_h * 0.72:.0f}" '
                     f'class="val">{n}</text>')
        parts.append(f'<text x="{label_w + plot + 34}" y="{y + row_h * 0.72:.0f}" '
                     f'class="tick">{lobby:.1f}%</text>')

    parts.append("</svg>")
    return "".join(parts)


def dumbbell_chart(rows):
    """How often you build a line vs how often the lobby does.

    Two values per category, so a dumbbell rather than paired bars: the thing
    worth seeing is the size of the gap, and a connector draws that directly
    instead of asking you to subtract two bar lengths by eye.
    """
    w = 680
    row_h = 30
    label_w, right_pad = 132, 24
    plot = w - label_w - right_pad
    h = len(rows) * row_h + 44
    top = max(max(a, b) for _, a, b in rows) * 1.12

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             'aria-label="Your build rate versus the lobby build rate">']

    for gx in range(0, int(top) + 1, 4):
        x = label_w + plot * gx / top
        parts.append(f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="{h - 30}" '
                     'class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - 12}" class="tick" '
                     f'text-anchor="middle">{gx}%</text>')

    for i, (name, mine, lobby) in enumerate(rows):
        y = 22 + i * row_h
        x1 = label_w + plot * mine / top
        x2 = label_w + plot * lobby / top
        parts.append(f'<text x="{label_w - 10}" y="{y + 4}" class="rowlabel" '
                     f'text-anchor="end">{esc(fit(name, label_w - 14, 12))}</text>')
        parts.append(f'<line x1="{x1:.1f}" y1="{y}" x2="{x2:.1f}" y2="{y}" '
                     'class="connector"/>')
        parts.append(f'<circle cx="{x2:.1f}" cy="{y}" r="6" fill="var(--room)" '
                     f'class="mark ring" data-tip="{esc(name)} &middot; the lobby '
                     f'builds it in {lobby:.1f}% of boards"/>')
        parts.append(f'<circle cx="{x1:.1f}" cy="{y}" r="6" fill="var(--accent)" '
                     f'class="mark ring" data-tip="{esc(name)} &middot; you build it '
                     f'in {mine:.0f}% of your games"/>')

    parts.append("</svg>")
    return "".join(parts)


def playstyle_chart(rows):
    """You against the room across metrics that don't share a unit.

    Unit cost, board size and rounds survived cannot go on one axis, so the
    bar is the gap divided by its own error bar - how many error bars from
    "same as the room" you sit. That is dimensionless, so every metric is
    comparable, and it encodes the significance test directly: past the
    dashed line at 1 the difference is bigger than luck, inside it isn't.
    """
    w = 680
    row_h, gap_h = 26, 8
    # The right gutter has to fit the read word ("more expensive", "less
    # rerolling") - 14 characters of 11px mono is ~92px, and at 60 they ran
    # off the canvas entirely.
    label_w, right_pad = 168, 108
    plot = w - label_w - right_pad
    h = len(rows) * (row_h + gap_h) + 40
    mid = label_w + plot / 2
    reach = max(2.2, max(abs(r[3] / r[4]) for r in rows) * 1.1)

    def px(z):
        return mid + (plot / 2) * z / reach

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             'aria-label="Playstyle differences from the rest of the lobby">']

    # The significance threshold, drawn rather than described.
    for z in (-1, 1):
        parts.append(f'<line x1="{px(z):.1f}" y1="6" x2="{px(z):.1f}" '
                     f'y2="{h - 26}" class="rule-dash"/>')
    parts.append(f'<line x1="{mid}" y1="6" x2="{mid}" y2="{h - 26}" class="axis"/>')
    parts.append(f'<text x="{mid}" y="{h - 8}" class="tick" text-anchor="middle">'
                 'same as the room</text>')
    parts.append(f'<text x="{px(-1):.1f}" y="{h - 8}" class="tick" '
                 'text-anchor="middle">−1 error bar</text>')
    parts.append(f'<text x="{px(1):.1f}" y="{h - 8}" class="tick" '
                 'text-anchor="middle">+1</text>')

    for i, (label, you, them, gap, moe, more, less) in enumerate(rows):
        y = 6 + i * (row_h + gap_h)
        z = max(-reach, min(reach, gap / moe))
        real = abs(gap) > moe
        x0, x1 = (mid, px(z)) if z >= 0 else (px(z), mid)
        colour = ("var(--pos)" if z > 0 else "var(--neg)") if real else "var(--muted)"
        parts.append(f'<text x="{label_w - 10}" y="{y + row_h * 0.7:.0f}" '
                     f'class="rowlabel" text-anchor="end">'
                     f'{esc(fit(label, label_w - 14, 12))}</text>')
        parts.append(
            f'<rect x="{x0:.1f}" y="{y + 4}" width="{max(abs(x1 - x0), 1.5):.1f}" '
            f'height="{row_h - 8}" rx="3" fill="{colour}" class="mark" '
            f'data-tip="{esc(label)} &middot; you {you:.2f} vs room {them:.2f} '
            f'&middot; {esc(more if gap > 0 else less)}'
            f'{" (bigger than luck)" if real else " (inside the error bar)"}"/>')
        read = fit(more if gap > 0 else less, right_pad - 10, 11) if real else "—"
        parts.append(f'<text x="{label_w + plot + 6}" y="{y + row_h * 0.7:.0f}" '
                     f'class="tick">{esc(read)}</text>')

    parts.append("</svg>")
    return "".join(parts)


def gap_chart(rows):
    """Effect size with its error bar, which is the honest way to show this.

    Each row is "how much better did this go, with vs without", drawn as a
    dot at the measured gap and a bar across its 95% interval. The zero rule
    does the talking: an interval touching it means the data cannot tell this
    apart from no effect at all, however big the dot looks.
    """
    w = 680
    row_h = 46
    # Wider label column than the other charts: each row carries a second line
    # of text under the name, and right-anchored text runs off the left edge
    # rather than wrapping, so the gutter has to fit the longest note.
    label_w, right_pad = 182, 24
    plot = w - label_w - right_pad
    h = len(rows) * row_h + 44
    reach = max(max(abs(g - m), abs(g + m)) for _, g, m, _ in rows) * 1.15
    mid = label_w + plot / 2

    def px(value):
        return mid + (plot / 2) * value / reach

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
             'aria-label="Placement gap with 95% confidence interval">']
    parts.append(f'<line x1="{mid}" y1="8" x2="{mid}" y2="{h - 30}" class="axis"/>')
    parts.append(f'<text x="{mid}" y="{h - 12}" class="tick" text-anchor="middle">'
                 'no difference</text>')
    parts.append(f'<text x="{label_w + 4}" y="{h - 12}" class="tick">worse with it</text>')
    parts.append(f'<text x="{w - right_pad - 4}" y="{h - 12}" class="tick" '
                 'text-anchor="end">better with it</text>')

    for i, (name, gap, moe, note) in enumerate(rows):
        y = 26 + i * row_h
        lo, hi = px(gap - moe), px(gap + moe)
        solid = abs(gap) > moe
        colour = "var(--pos)" if solid and gap > 0 else (
            "var(--neg)" if solid else "var(--muted)")
        parts.append(f'<text x="{label_w - 10}" y="{y + 4}" class="rowlabel" '
                     f'text-anchor="end">{esc(fit(name, label_w - 14, 12))}</text>')
        parts.append(f'<line x1="{lo:.1f}" y1="{y}" x2="{hi:.1f}" y2="{y}" '
                     f'stroke="{colour}" class="interval"/>')
        for cap in (lo, hi):
            parts.append(f'<line x1="{cap:.1f}" y1="{y - 5}" x2="{cap:.1f}" '
                         f'y2="{y + 5}" stroke="{colour}" class="interval"/>')
        parts.append(f'<circle cx="{px(gap):.1f}" cy="{y}" r="6" fill="{colour}" '
                     f'class="mark ring" data-tip="{esc(name)} &middot; {gap:+.2f} places '
                     f'&plusmn; {moe:.2f} &middot; {esc(note)}"/>')
        parts.append(f'<text x="{label_w - 10}" y="{y + 19}" class="sublabel" '
                     f'text-anchor="end">{esc(fit(note, label_w - 14, 10.5))}</text>')

    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------- the numbers --

# Which player, and where to write. Both come from the environment so the web
# app can render any player without this script needing an argument parser:
#   TFT_PUUID  whose games to report on (default: me.json)
#   TFT_OUT    directory to write into  (default: site/)
#   TFT_NAME   display name for the header (default: the Riot ID in the data)
games, _ = tft.load_games(os.environ.get("TFT_PUUID") or None)
set_number = games[0]["_set"]
OUT_DIR = Path(os.environ.get("TFT_OUT") or OUT_DIR)
# Riot IDs are in the match payload, so a page can name its subject without
# being told - the caller only overrides it to show the tag as typed.
WHO = (os.environ.get("TFT_NAME")
       or f"{games[0].get('riotIdGameName', '?')}#{games[0].get('riotIdTagline', '?')}")
data = tft.coverage_data(games, set_number)

placements = data["placements"]
overall = tft.summarise(placements)
built = data["built_games"]
n_games = data["games"]

def patch_key(p):
    """Sort 16.9 before 16.10. Patch numbers look like decimals but are not:
    sorted as text, "16.9" lands after "16.14" because "9" > "1"."""
    try:
        return tuple(int(part) for part in p.split("."))
    except ValueError:
        return (0,)


patches = sorted({g["_patch"] for g in games}, key=patch_key)

# Recent form needs a window AND enough history behind it to compare against.
# Early in a set there is neither: with 4 games the window is still 5, and
# "the games before that" is an empty list. stats.py has always guarded this;
# the page has to as well, or a new set opens on a crash.
recent_window = max(5, min(30, n_games // 5))
has_form = n_games >= 2 * recent_window
recent = tft.summarise(placements[:recent_window]) if has_form else None
earlier = tft.summarise(placements[recent_window:]) if has_form else None

# Lines on their way to a verdict, plus any that got there.
coverage_rows = [(l, len(built[l]), data["lobby_rate"][l])
                 for l in data["ready"] + data["building"]]

# Where your habits and the room's diverge most. Only lines one side or the
# other actually plays - a line nobody builds is not a habit, it is noise.
divergence = sorted(
    ((l, data["my_rate"][l], data["lobby_rate"][l]) for l in data["roster"]
     if data["lobby_rate"][l] >= 2.0 or data["my_rate"][l] >= 2.0),
    key=lambda r: (-abs(r[1] - r[2]), r[0]))[:7]
divergence.sort(key=lambda r: (-(r[1] - r[2]), r[0]))

# Anything with the games to be judged: lines first, then carries.
gap_rows = []
for line in data["ready"]:
    judged = tft.with_without([line in m for m in data["my_built"]], placements)
    if judged:
        gap_rows.append((line, judged["gap"],
                         tft.gap_moe(judged["with"]["games"], judged["without"]["games"]),
                         f"{judged['with']['games']} games building it"))

for name, pl in sorted(data["by_carry"].items(), key=lambda kv: (-len(kv[1]), kv[0])):
    if len(pl) < TARGET:
        continue
    judged = tft.with_without([tft.carry_of(g) == name for g in games], placements)
    if judged:
        gap_rows.append((name, judged["gap"],
                         tft.gap_moe(judged["with"]["games"], judged["without"]["games"]),
                         f"{judged['with']['games']} games carrying it"))

carry_rows = sorted(data["by_carry"].items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]

# How often somebody else is holding copies of your carry. Every copy a rival
# holds is one you cannot roll into, which is why this is worth a number.
carry_contested = 0
carry_total = 0
for g in games:
    carry = tft.carry_of(g)
    if not carry:
        continue
    carry_total += 1
    rivals = sum(1 for p in g["_lobby"] if p["puuid"] != g["puuid"]
                 and carry in tft.board_units(p))
    carry_contested += bool(rivals)

blind_real = [(l, data["lobby_rate"][l], data["splashed"][l])
              for l in data["blind"] if data["lobby_rate"][l] >= 2.0]
blind_rare = [l for l in data["blind"] if data["lobby_rate"][l] < 2.0]

total_commits = sum(len(v) for v in built.values())
verdict_ready = len(data["ready"]) + sum(1 for _, pl in data["by_carry"].items()
                                         if len(pl) >= TARGET)


# ------------------------------------------------------------------ styles --

CSS = """
:root {
  --bg:#F5F6F8; --panel:#FFFFFF; --line:#DFE3EA; --grid:#EDF0F4;
  --ink:#171B22; --muted:#5A6478;
  --accent:#B4761A; --room:#3F6BC4; --pos:#0F8C79; --neg:#C24A52;
  --track:#E7EAF0;
  --display:"Avenir Next","Avenir",ui-sans-serif,system-ui,"Segoe UI",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0B0E14; --panel:#141A24; --line:#232C3A; --grid:#1B2330;
    --ink:#E4E8F0; --muted:#8792A8;
    --accent:#BE831A; --room:#5F86D4; --pos:#17A28C; --neg:#D25A61;
    --track:#1D2534;
  }
}
:root[data-theme="dark"] {
  --bg:#0B0E14; --panel:#141A24; --line:#232C3A; --grid:#1B2330;
  --ink:#E4E8F0; --muted:#8792A8;
  --accent:#BE831A; --room:#5F86D4; --pos:#17A28C; --neg:#D25A61;
  --track:#1D2534;
}
:root[data-theme="light"] {
  --bg:#F5F6F8; --panel:#FFFFFF; --line:#DFE3EA; --grid:#EDF0F4;
  --ink:#171B22; --muted:#5A6478;
  --accent:#B4761A; --room:#3F6BC4; --pos:#0F8C79; --neg:#C24A52;
  --track:#E7EAF0;
}

body {
  margin:0; background:var(--bg); color:var(--ink);
  font-family:var(--display); font-size:16px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:820px; margin:0 auto; padding:0 24px 96px; }
p { max-width:64ch; }

/* ---- the recurring device: a mono, letterspaced instrument label ---- */
.eyebrow {
  font-family:var(--mono); font-size:11px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin:0 0 6px;
}

header.masthead { padding:64px 0 28px; border-bottom:1px solid var(--line); }
.who { font-family:var(--mono); font-size:13px; color:var(--muted); }
h1 {
  font-size:clamp(34px,6vw,52px); line-height:1.04; letter-spacing:-.035em;
  font-weight:800; margin:14px 0 0; text-wrap:balance; max-width:16ch;
}
.standfirst { color:var(--muted); font-size:18px; margin:18px 0 0; max-width:56ch; }

/* ---- verdict panel: the honest headline, stated as a reading ---- */
.verdict {
  margin:28px 0 0; padding:22px 24px; background:var(--panel);
  border:1px solid var(--line); border-left:3px solid var(--accent);
  border-radius:3px;
}
.verdict strong { font-weight:700; }

section { padding:44px 0 0; }
h2 {
  font-size:23px; letter-spacing:-.02em; font-weight:700; margin:0 0 4px;
  text-wrap:balance;
}
.lede { color:var(--muted); margin:0 0 22px; }

/* ---- stat row ---- */
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:1px;
         background:var(--line); border:1px solid var(--line); border-radius:3px;
         margin:26px 0 0; overflow:hidden; }
.stat { background:var(--panel); padding:16px 18px; }
.stat .n { font-family:var(--mono); font-size:27px; font-variant-numeric:tabular-nums;
           letter-spacing:-.02em; display:block; }
.stat .k { font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
           text-transform:uppercase; color:var(--muted); }
.stat .pm { font-size:12px; color:var(--muted); font-family:var(--mono); }

figure { margin:0; background:var(--panel); border:1px solid var(--line);
         border-radius:3px; padding:18px 18px 12px; overflow-x:auto; }
figcaption { color:var(--muted); font-size:13.5px; margin-top:12px;
             padding-top:12px; border-top:1px solid var(--line); }
.chart { width:100%; height:auto; display:block; min-width:460px; }

/* ---- svg primitives ---- */
.axis { stroke:var(--muted); stroke-width:1; }
.grid { stroke:var(--grid); stroke-width:1; }
.rule-dash { stroke:var(--muted); stroke-width:1; stroke-dasharray:2 4; opacity:.75; }
.connector { stroke:var(--muted); stroke-width:2; opacity:.5; }
.interval { stroke-width:2; }
.track { fill:var(--track); }
.mark { transition:opacity .12s ease; }
.ring { stroke:var(--panel); stroke-width:2; }
svg text { font-family:var(--mono); fill:var(--ink); }
.tick { font-size:11px; fill:var(--muted); }
.val { font-size:12px; font-variant-numeric:tabular-nums; }
.rowlabel { font-size:12px; }
.sublabel { font-size:10.5px; fill:var(--muted); }
.chart:hover .mark { opacity:.45; }
.chart .mark:hover { opacity:1; }

.legend { display:flex; gap:18px; flex-wrap:wrap; font-family:var(--mono);
          font-size:11.5px; color:var(--muted); margin:0 0 14px; }
.legend span { display:flex; align-items:center; gap:7px; }
.dot { width:10px; height:10px; border-radius:50%; display:inline-block; }

table { width:100%; border-collapse:collapse; font-size:14px; }
th { font-family:var(--mono); font-size:10.5px; letter-spacing:.13em;
     text-transform:uppercase; color:var(--muted); text-align:left;
     font-weight:400; padding:0 10px 8px 0; }
td { padding:8px 10px 8px 0; border-top:1px solid var(--line);
     font-variant-numeric:tabular-nums; }
td.num, th.num { text-align:right; font-family:var(--mono); }
.tablewrap { overflow-x:auto; background:var(--panel); border:1px solid var(--line);
             border-radius:3px; padding:16px 18px; }

.pill { font-family:var(--mono); font-size:10.5px; letter-spacing:.08em;
        text-transform:uppercase; padding:2px 8px; border-radius:2px;
        border:1px solid currentColor; white-space:nowrap; }
.pill.blank { color:var(--neg); }
.pill.part  { color:var(--accent); }
.pill.ready { color:var(--pos); }

.notes { margin:14px 0 0; padding-left:20px; color:var(--muted); font-size:14.5px; }
.notes li { margin:7px 0; }

footer { margin-top:56px; padding-top:22px; border-top:1px solid var(--line);
         color:var(--muted); font-size:13.5px; }
code { font-family:var(--mono); font-size:.92em; background:var(--track);
       padding:1px 5px; border-radius:2px; }

#tip {
  position:fixed; pointer-events:none; opacity:0; transform:translate(-50%,-140%);
  background:var(--ink); color:var(--bg); font-family:var(--mono); font-size:11.5px;
  padding:6px 9px; border-radius:3px; white-space:nowrap; z-index:9; max-width:90vw;
  transition:opacity .1s ease;
}
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
"""

SCRIPT = """
(function () {
  var tip = document.getElementById('tip');
  var PAD = 8;
  function show(e) {
    var t = e.target.closest('[data-tip]');
    if (!t) { tip.style.opacity = '0'; return; }
    tip.innerHTML = t.getAttribute('data-tip');
    tip.style.opacity = '1';

    // Keep the whole tooltip on screen. It is centred on the cursor and sits
    // above it, so without clamping a mark near the left edge loses the front
    // of its label and one near the top loses the label entirely - and a
    // half-read tooltip is worse than none, because the part you can see
    // still looks like a complete sentence.
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var half = w / 2;
    var x = Math.min(Math.max(e.clientX, half + PAD), window.innerWidth - half - PAD);
    var above = e.clientY - h * 1.4 >= PAD;
    tip.style.left = x + 'px';
    tip.style.top = e.clientY + 'px';
    tip.style.transform = above
      ? 'translate(-50%,-140%)'
      : 'translate(-50%,40%)';
  }
  document.addEventListener('mousemove', show);
  document.addEventListener('mouseleave', function () {
    tip.style.opacity = '0';
  }, true);
})();
"""


# ------------------------------------------------------------------- body ---

def stat(value, label, sub=""):
    extra = f'<span class="pm">{esc(sub)}</span>' if sub else ""
    return (f'<div class="stat"><span class="k">{esc(label)}</span>'
            f'<span class="n">{esc(value)}</span>{extra}</div>')


trend = earlier["avg"] - recent["avg"] if has_form else None
# Show this one with its error bar too. It was the only bare number on a page
# whose entire argument is that bare numbers mislead - and at 30 vs 166 games
# luck alone moves it by about 0.9 places, four times the value displayed.
form_moe = tft.gap_moe(recent["games"], earlier["games"]) if has_form else None
form_note = (f"last {recent_window}, ±{form_moe:.2f}" if has_form else "")

body = []
# The headline is a claim about the data, so it has to be read off the data.
# Hardcoding "nothing has reached the bar" would quietly become a lie the
# first session that pushes a line over it.
lines_touched = len(data["building"]) + len(data["ready"])
per_line = total_commits / max(lines_touched, 1)

if data["ready"]:
    headline = (f"<strong>{len(data['ready'])} of your lines have reached the "
                f"{TARGET} games</strong> it takes to tell them apart from luck")
else:
    headline = (f"<strong>not one comp direction has reached the {TARGET} "
                f"games</strong> it takes to tell it apart from luck")

if gap_rows:
    names = esc(", ".join(n for n, _, _, _ in gap_rows))
    solid = [n for n, gap, moe, _ in gap_rows if abs(gap) > moe]
    # `solid` is a list, so testing it for truthiness would let a single
    # significant row speak for every row - the page would claim "a difference
    # bigger than luck" about nine things when it was true of one, and
    # contradict its own figcaption two screens down.
    only_one = len(gap_rows) == 1
    if not solid:
        finding = ("<strong>no real difference</strong>" if only_one else
                   "<strong>no real difference</strong> in any of them")
    elif len(solid) == len(gap_rows):
        finding = ("<strong>a difference bigger than luck</strong>" if only_one
                   else "<strong>a difference bigger than luck</strong> in every one")
    else:
        finding = (f"a difference bigger than luck in "
                   f"<strong>{len(solid)} of {len(gap_rows)}</strong> "
                   f"({esc(', '.join(solid))})")
    lead = ("Past that bar" if data["ready"]
            else f"The {'exception is' if only_one else 'exceptions are'}")
    exception = f" {lead} {names} &mdash; and the error bars show {finding}."
else:
    exception = " Nothing at all clears that bar yet, carries included."

body.append(f"""
<header class="masthead">
  <div class="who">{esc(WHO)} &middot; Set {set_number} ranked</div>
  <h1>What {n_games} games can and can&rsquo;t tell you</h1>
  <p class="standfirst">A tracker for one player, built on the rule that a
  number nobody can trust is worse than no number at all.</p>
  <div class="verdict">
    <p class="eyebrow">The reading</p>
    <p style="margin:0">Across {n_games} ranked games, {headline}. You committed
    to a line {plural(total_commits, "time")}, spread over
    {plural(lines_touched, "different line")} &mdash; about
    {plural(round(per_line), "game")} each.{exception} Everything below is
    shown with the error bars that lead to that conclusion.</p>
  </div>
</header>""")

# ---- playstyle: how you play, in terms that outlive the set ---------------
# Chronological, because playstyle is reported per era rather than per set: a
# lifetime average of someone who rerolled for 82 games and then went fast-8
# describes neither player. Same split the terminal report uses.
timeline = list(reversed(games))
mine_style, room_style = [], []
for g in timeline:
    for board in g["_lobby"]:
        prof = tft.playstyle(board, set_number)
        if prof is None:
            continue
        (mine_style if board["puuid"] == g["puuid"] else room_style).append(prof)

style_shift = tft.biggest_shift(mine_style)
style_era = mine_style[style_shift["cut"]:] if style_shift else mine_style
era_games = timeline[style_shift["cut"]:] if style_shift else timeline

style_rows, style_verdicts = [], {}
for key, label, more, less in tft.PLAYSTYLE_LABELS:
    judged = tft.mean_gap([p[key] for p in style_era],
                          [p[key] for p in room_style])
    if judged and judged["moe"] > 0:
        style_verdicts[key] = judged
        style_rows.append((label, judged["a"], judged["b"],
                           judged["gap"], judged["moe"], more, less))

# Describe the histogram from the histogram. A sentence like "your
# distribution is flat" is a claim about the data, and hardcoding it means the
# page states it confidently even when the bars plainly show otherwise - which
# is exactly what it did at four games.
counts = [placements.count(p) for p in range(1, 9)]
if n_games < TARGET:
    shape = (f"At {plural(n_games, 'game')} the silhouette here is mostly noise; "
             "it takes a few hundred before the shape itself says anything.")
else:
    swing = (max(counts) - min(counts)) / (n_games / 8)
    if swing < 0.8:
        shape = ("Your distribution is close to flat: no spike at the top, no "
                 "collapse at the bottom &mdash; the shape of a player who is "
                 "rarely blown out and rarely runs away with it.")
    elif counts[0] + counts[1] >= counts[6] + counts[7]:
        shape = ("The distribution leans toward the top of the lobby: firsts and "
                 "seconds outnumber sevenths and eighths.")
    else:
        shape = ("The distribution leans toward the bottom of the lobby: "
                 "sevenths and eighths outnumber firsts and seconds.")

if style_rows:
    # Same sentence the terminal prints, from the same function - so the page
    # and coverage of it can't describe the same player differently.
    said = tft.playstyle_sentence(style_verdicts)
    summary = (f"<strong>{esc(said)}</strong>" if said else
               "Nothing about how you play is far enough from the room to "
               "name yet, which is itself an answer: you play like the lobby.")
    if style_shift:
        era_note = (
            f"Your last {len(style_era)} games only. Around game "
            f"{style_shift['cut'] + 1} (patch {esc(era_games[0]['_patch'])}) your "
            f"{esc(style_shift['label'])} went {style_shift['before']:.2f} &rarr; "
            f"{style_shift['after']:.2f} &mdash; a different player from the "
            f"{style_shift['cut']} games before, so averaging the two would "
            "describe neither.")
    else:
        era_note = (f"All {len(style_era)} games &mdash; your style hasn't "
                    "changed sharply enough this set to split it in two.")
    body.append(f"""
<section>
  <p class="eyebrow">Playstyle</p>
  <h2>How you play, not what you played</h2>
  <p class="lede">Comps and augments reset to zero every set. These do not
  &mdash; an expensive board is an expensive board in Set 18 too. {era_note}</p>
  <figure>
    {playstyle_chart(style_rows)}
    <figcaption>Each bar is the gap between you and the other seven players
    in your lobbies, divided by its own error bar &mdash; because unit cost,
    board size and rounds survived don&rsquo;t share a scale. Past the dashed
    line the difference is bigger than luck; inside it, you play like the room
    does. {summary}</figcaption>
  </figure>
</section>""")

body.append(f"""
<section>
  <p class="eyebrow">Where you land</p>
  <h2>The only number the game actually gives you</h2>
  <p class="lede">Placement, 1st through 8th, across every ranked game of the set.</p>
  <div class="stats">
    {stat(f"{overall['avg']:.2f}", "avg placement", f"± {overall['moe']:.2f}")}
    {stat(f"{overall['top4']:.0f}%", "top 4 rate")}
    {stat(overall['wins'], "firsts")}
    {stat(n_games, "ranked games")}
    {stat(f"{trend:+.2f}", "recent form", form_note) if has_form
     else stat("—", "recent form", "needs more games")}
  </div>
  <figure style="margin-top:26px">
    {placement_chart(placements)}
    <figcaption>Fourth and up pays LP, fifth and down loses it, so the chart
    diverges at that break rather than treating 1&ndash;8 as one run. {shape}
    Margin of error on the average is &plusmn;{overall['moe']:.2f} places.</figcaption>
  </figure>
</section>""")

if coverage_rows:
    # "Closest to a verdict" means most games among lines that have NOT got
    # there. coverage_rows is ready+building, and those two are sorted on
    # different keys (mean placement vs game count), so its first row is only
    # the leader while ready is empty - after that it names the best-average
    # line and prints a self-refuting "0 short".
    not_there = [r for r in coverage_rows if r[1] < TARGET]
    closest = max(not_there, key=lambda r: (r[1], r[0])) if not_there else None

    # Under-tested relative to how often the room builds it. This has to be a
    # ratio: subtracting a game count from a percentage compares two different
    # units, and since counts outgrow realistic lobby rates it just returns
    # whichever line has the fewest games - never the common-but-untested ones
    # the label promises.
    under = max(coverage_rows, key=lambda r: (r[2] / max(r[1], 1), r[0]))
    # Don't assert "nothing reaches the bar" from a hardcoded string - the
    # whole point of the page is that this changes as games accumulate.
    reached = [name for name, n, _ in coverage_rows if n >= TARGET]
    verdict_line = (f"{len(reached)} of them reach it: {esc(', '.join(reached))}."
                    if reached else "Nothing reaches it yet.")
    body.append(f"""
<section>
  <p class="eyebrow">Coverage</p>
  <h2>Which lines you have actually tested</h2>
  <p class="lede">A line counts here only when you truly built it &mdash; the second
  trait breakpoint, or four units. A two-unit splash is not a direction.</p>
  <figure>
    {coverage_chart(coverage_rows)}
    <figcaption>Bars run to the {TARGET} games an average needs before it beats
    guessing. {verdict_line} The percentage on the right is how often the
    other seven players in your lobbies commit to that line, so you can spend
    repetitions on something the room actually rewards.</figcaption>
  </figure>
  <ul class="notes">
    {f'<li><strong>Closest to a verdict:</strong> {esc(closest[0])} at '
     f'{plural(closest[1], "game")}, {TARGET - closest[1]} short.</li>'
     if closest else ''}
    <li><strong>Most under-tested for how common it is:</strong>
      {esc(under[0])} &mdash; the lobby builds it in {under[2]:.1f}% of boards,
      you have {plural(under[1], "game")} on it.</li>
  </ul>
</section>""")

blind_items = "".join(
    f'<tr><td>{esc(l)}</td><td class="num">{rate:.1f}%</td>'
    f'<td class="num">{sp}</td>'
    f'<td><span class="pill blank">no data</span></td></tr>'
    for l, rate, sp in blind_real)

if blind_real or blind_rare:
    rare_note = (f'<ul class="notes"><li>Too rare in your lobbies to be worth '
                 f'chasing: {esc(", ".join(blind_rare))}.</li></ul>'
                 if blind_rare else "")
    table = f"""<div class="tablewrap">
    <table>
      <thead><tr><th>Line</th><th class="num">Lobby builds it</th>
      <th class="num">You splashed</th><th>Your record</th></tr></thead>
      <tbody>{blind_items}</tbody>
    </table>
  </div>""" if blind_real else (
        '<p class="lede">None &mdash; you have built every line the room '
        'builds at least once.</p>')
    body.append(f"""
<section>
  <p class="eyebrow">Blind spots</p>
  <h2>Lines the room builds and you never have</h2>
  <p class="lede">Not advice to force them &mdash; this is where your own report has
  nothing to say.</p>
  {table}
  {rare_note}
</section>""")

if divergence:
    body.append(f"""
<section>
  <p class="eyebrow">Habits</p>
  <h2>You against the room</h2>
  <p class="lede">Your build rate next to the rate of the other seven players in
  the same lobbies &mdash; the fairest yardstick available, because it already
  accounts for your rank and your patch.</p>
  <div class="legend">
    <span><i class="dot" style="background:var(--accent)"></i> you</span>
    <span><i class="dot" style="background:var(--room)"></i> the lobby</span>
  </div>
  <figure>
    {dumbbell_chart(divergence)}
    <figcaption>Playing off-meta is not automatically wrong &mdash; a line you know
    well can beat one the room forces. It is here because it explains the shape
    of your data: what you over-play is what you will get a verdict on first.</figcaption>
  </figure>
</section>""")

# The carry-contested figure stands on its own, so it survives even when
# nothing has the games to be judged.
# Denominator is games that HAD a carry, not all games - so say that, rather
# than "of your games". Identical today (every game has one) but it would
# overstate the moment a game ends before you finish an item.
contested_note = (
    f"<li>A rival was holding copies of your carry in "
    f"<strong>{100 * carry_contested / carry_total:.0f}%</strong> of the "
    f"{plural(carry_total, 'game')} where you had one "
    f"({carry_contested} of {carry_total}) &mdash; every copy they hold is one "
    f"you cannot roll into.</li>" if carry_total else "")
spread_note = (f"<li>{len(data['by_carry'])} different carries across {n_games} "
               f"games. Carries spread thinner than lines do.</li>"
               if data["by_carry"] else "")

if gap_rows:
    # Say what the chart actually shows rather than assuming the null result.
    # How many games back a row depends on whether it is a line or a carry,
    # so read the count off the row itself instead of guessing the source.
    inconclusive = [name for name, gap, moe, _ in gap_rows if abs(gap) <= moe]
    if len(inconclusive) == len(gap_rows):
        reading = ("Every interval here crosses &ldquo;no difference&rdquo;, "
                   "which means the data cannot separate these from every "
                   "other game you play. That is a real answer, not a missing "
                   "one.")
    elif inconclusive:
        reading = (f"{len(gap_rows) - len(inconclusive)} of {len(gap_rows)} clear "
                   f"their error bar; the rest cannot be separated from no "
                   f"effect at all.")
    else:
        reading = ("Every interval here clears &ldquo;no difference&rdquo; &mdash; "
                   "these gaps are bigger than luck at these sample sizes.")
    lede = (f"Only things past {TARGET} games appear here &mdash; "
            f"{'just one' if len(gap_rows) == 1 else str(len(gap_rows))} so far.")
    body.append(f"""
<section>
  <p class="eyebrow">The one testable thing</p>
  <h2>Does leaning on your carry actually work?</h2>
  <p class="lede">{lede}</p>
  <figure>
    {gap_chart(gap_rows)}
    <figcaption>The dot is the measured gap between games with and without;
    the bar is the 95% interval around it. {reading}</figcaption>
  </figure>
  <ul class="notes">{contested_note}{spread_note}</ul>
</section>""")
elif contested_note or spread_note:
    body.append(f"""
<section>
  <p class="eyebrow">Not testable yet</p>
  <h2>Nothing has the games to be judged</h2>
  <p class="lede">No line and no carry has reached {TARGET} games, so there is
  no gap here worth drawing. These two counts hold regardless.</p>
  <ul class="notes">{contested_note}{spread_note}</ul>
</section>""")

def pill(n):
    """Sample-size badge. Kept as a function rather than inlined into the row
    f-string: nesting quotes inside a nested f-string is how you end up
    emitting class=&#39;...&#39; and silently losing the styling."""
    if n >= TARGET:
        return '<span class="pill ready">testable</span>'
    return f'<span class="pill part">{TARGET - n} short</span>'


carry_items = "".join(
    f'<tr><td>{esc(name)}</td><td class="num">{len(pl)}</td>'
    f'<td class="num">{statistics.mean(pl):.2f}</td>'
    f'<td>{pill(len(pl))}</td></tr>'
    for name, pl in carry_rows)

if carry_rows:
    body.append(f"""
<section>
  <p class="eyebrow">Carries</p>
  <h2>What you put the items on</h2>
  <div class="tablewrap">
    <table>
      <thead><tr><th>Unit</th><th class="num">Games</th>
      <th class="num">Avg place</th><th>Sample</th></tr></thead>
      <tbody>{carry_items}</tbody>
    </table>
  </div>
</section>""")

body.append(f"""
<section>
  <p class="eyebrow">Method</p>
  <h2>Rules this page follows</h2>
  <ul class="notes">
    <li><strong>{TARGET} games minimum</strong> before a number is called
      actionable. Margin of error on an average placement is about
      4.5/&radic;n &mdash; &plusmn;1.5 places at 10 games, &plusmn;0.8 at 30.</li>
    <li><strong>Compared with-vs-without</strong>, never against your overall
      average &mdash; that average already contains the games in question.</li>
    <li><strong>Normalised against the lobby.</strong> All eight players are in
      every match file, which self-corrects for rank and patch.</li>
    <li><strong>Committed, not splashed.</strong> Counting an active trait as a
      played line measures whether you could afford a frontline, not what you
      were building.</li>
    <li><strong>Negative results are shown.</strong> A gap that fails its error
      bar is reported as no difference rather than quietly dropped.</li>
    <li>Games span patches {esc(", ".join(patches))}, and patches move
      placement by more than most of the gaps on this page.</li>
  </ul>
  <footer>
    Built from {n_games} saved match files by <code>build_site.py</code>.
    Regenerate after fetching new games:
    <code>python fetch_matches.py &amp;&amp; python build_site.py</code>.
    Every number here also prints in the terminal via <code>coverage.py</code>,
    from the same function &mdash; the page and the reports cannot disagree.
  </footer>
</section>""")

BODY = "\n".join(body)

OUT_DIR.mkdir(exist_ok=True)

# The standalone file you can open or mail to someone.
(OUT_DIR / "index.html").write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TFT Set {set_number} &middot; {esc(WHO)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{BODY}
</div>
<div id="tip" role="status" aria-live="polite"></div>
<script>{SCRIPT}</script>
</body>
</html>
""")

# The same page as a fragment, for a host that supplies its own <head>.
(OUT_DIR / "artifact.html").write_text(f"""<title>TFT Set {set_number} &middot; {esc(WHO)}</title>
<style>{CSS}</style>
<div class="wrap">
{BODY}
</div>
<div id="tip" role="status" aria-live="polite"></div>
<script>{SCRIPT}</script>
""")

print(f"Built {OUT_DIR}/index.html and {OUT_DIR}/artifact.html for {WHO} "
      f"from {n_games} set-{set_number} games.")
print(f"  open {OUT_DIR}/index.html")
