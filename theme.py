"""One look for every page the tracker serves.

The stats page and the post-game page are two views of the same player, so
they share one stylesheet, one tooltip script, and one set of text helpers.
They live here rather than in build_site.py because build_site.py is a
top-to-bottom script - importing it re-runs it - while every page needs
these pieces.
"""

import html

# The app's name. MetaTFT tells you what the world is playing; this tells
# you what you play, measured against the seven people in your own lobbies.
BRAND = "MyTFT"

# Three hexes, because the TFT board is a hex grid and nothing says the game
# faster. Two outlined and one filled: the lobby, and you in it - the same
# idea the whole app is built on, at 22 pixels.
HEX_MARK = (
    '<svg class="hexmark" viewBox="0 0 23 24" width="24" height="25" '
    'aria-hidden="true" focusable="false">'
    '<polygon class="h-out" points="6.00,1.00 1.50,3.60 1.50,8.80 6.00,11.40 '
    '10.50,8.80 10.50,3.60"/>'
    '<polygon class="h-out" points="17.00,1.00 12.50,3.60 12.50,8.80 '
    '17.00,11.40 21.50,8.80 21.50,3.60"/>'
    '<polygon class="h-me" points="11.50,12.20 7.00,14.80 7.00,20.00 '
    '11.50,22.60 16.00,20.00 16.00,14.80"/>'
    '</svg>')
BRAND_HTML = f'{HEX_MARK}<b>My<span>TFT</span></b>'


def brand_link(href="/"):
    return (f'<a class="brand" href="{href}" aria-label="{BRAND} home">'
            f'{BRAND_HTML}</a>')


def esc(text):
    return html.escape(str(text), quote=True)


# Which view is which, in the order they get used across a session.
TABS = [("Pre-game", "/pregame"), ("Post-game", "/game"), ("Stats", "/player")]


def stale_names_banner(set_number):
    """A visible nag when names.json predates the set being shown.

    The terminal reports write this to stderr, which nobody sees through a
    browser - so the pages carry their own copy.
    """
    import tftdata as tft          # imported here to keep theme dependency-free
    if not tft.names_missing(set_number):
        return ""
    return ('<div class="banner"><strong>Names for this set are missing.</strong> '
            'Comps are showing Riot&rsquo;s internal ids, and the list of comps '
            'that exist is incomplete. Run <code>python fetch_names.py</code> '
            'to fix it.</div>')


def new_game_watcher(q):
    """A banner that appears when the server notices you finished a game.

    The page asks; the server never pushes. It records the match id it saw
    on arrival and only speaks when that changes, so opening a page right
    after a game doesn't immediately claim there's a newer one.

    Deliberately a link rather than an automatic redirect: nothing should
    yank the page out from under someone mid-read.
    """
    return f"""<div id="newgame" role="status">
  <span>Your latest game just finished.</span>
  <a href="/game?{q}">See the debrief &rarr;</a>
</div>
<script>
(function () {{
  var banner = document.getElementById('newgame');
  var seen = null, first = true;
  function poll() {{
    fetch('/latest').then(function (r) {{ return r.json(); }}).then(function (d) {{
      if (first) {{ seen = d.match_id; first = false; return; }}
      if (d.match_id && d.match_id !== seen) {{ banner.style.display = 'flex'; }}
    }}).catch(function () {{}});
  }}
  poll();
  setInterval(poll, 20000);
}})();
</script>"""


def app_bar(who, set_number, q, here, extra=""):
    """The header every app view shares: wordmark, whose games you are
    looking at, the three views, and the way back out.

    One copy here rather than three in the page modules, because a header
    that differs between tabs is a header that looks broken when you click
    between them.

    `extra` is for a view's own control (post-game's refresh button).
    """
    links = "".join(
        f'<a{" class=" + chr(34) + "active" + chr(34) if name == here else ""} '
        f'href="{path}?{q}">{name}</a>' for name, path in TABS)
    # "Switch player" rather than "log out": nobody logged in - you typed a
    # Riot ID, and any name works - so calling it a logout would promise a
    # session that does not exist.
    switch = '<a class="switch" href="/">Switch player</a>'
    return (f'<div class="bar">{brand_link()}'
            f'<span class="who">{esc(who)} &middot; Set {set_number}</span>'
            f'<nav class="tabs">{links}{extra}{switch}</nav></div>')


def purpose(text):
    """One line under a page title saying what the page is for.

    The three tabs never announced themselves. A reader landing on Post-game
    has to work out from the contents whether it is about one game or all of
    them, and players who were not the author reported exactly that: the tabs
    were clear, what each tab was FOR was not.

    It sits on its own row under the hero rather than beside the title,
    because .hero is a baseline-aligned flex row and a sentence dropped into
    it would line up against the h1 and read as a subtitle.
    """
    return f'<p class="purpose">{text}</p>'


def fold(summary, html):
    """Any markup, folded away until somebody wants it.

    why() is this with a paragraph wrapper, and is the right one for prose.
    This one takes lists and tables, which cannot go inside a <p> - a <ul>
    nested in a paragraph closes it early, and the browser silently reparents
    everything after it.
    """
    return (f'<details class="why"><summary>{summary}</summary>'
            f'{html}</details>')


def why(summary, text):
    """An explanation, folded away until somebody wants it.

    This app explains itself constantly - every number carries the reason it
    can be trusted, which is the whole point of it and also why the pages read
    as a wall. Pre-game was carrying 282 words of small print and post-game
    94, all of it true and none of it what somebody wants in the ten seconds
    before they queue.

    Folding rather than cutting: every word stays and stops arriving at once.
    <details> is the right element for it - it works with no JavaScript, which
    matters because build_site.py's page has to survive being mailed to
    somebody as a single file.
    """
    return fold(summary, f'<p class="fine">{text}</p>')


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


CSS = """
:root {
  --bg:#F5F6F8; --panel:#FFFFFF; --line:#DFE3EA; --grid:#EDF0F4;
  --ink:#171B22; --muted:#5A6478;
  --accent:#B4761A; --room:#3F6BC4; --pos:#0F8C79; --neg:#C24A52;
  --track:#E7EAF0; --accent-solid:#8F5D12; --accent-ink:#FFFFFF;
  --pos-solid:#0B6E5F; --neg-solid:#A8353D;
  --display:"Avenir Next","Avenir",ui-sans-serif,system-ui,"Segoe UI",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0B0E14; --panel:#141A24; --line:#232C3A; --grid:#1B2330;
    --ink:#E4E8F0; --muted:#8792A8;
    --accent:#BE831A; --room:#5F86D4; --pos:#17A28C; --neg:#D25A61;
    --track:#1D2534; --accent-solid:#C58A1E; --accent-ink:#0B0E14;
    --pos-solid:#0E7A68; --neg-solid:#B03F47;
  }
}
:root[data-theme="dark"] {
  --bg:#0B0E14; --panel:#141A24; --line:#232C3A; --grid:#1B2330;
  --ink:#E4E8F0; --muted:#8792A8;
  --accent:#BE831A; --room:#5F86D4; --pos:#17A28C; --neg:#D25A61;
  --track:#1D2534; --accent-solid:#C58A1E; --accent-ink:#0B0E14;
  --pos-solid:#0E7A68; --neg-solid:#B03F47;
}
:root[data-theme="light"] {
  --bg:#F5F6F8; --panel:#FFFFFF; --line:#DFE3EA; --grid:#EDF0F4;
  --ink:#171B22; --muted:#5A6478;
  --accent:#B4761A; --room:#3F6BC4; --pos:#0F8C79; --neg:#C24A52;
  --track:#E7EAF0; --accent-solid:#8F5D12; --accent-ink:#FFFFFF;
  --pos-solid:#0B6E5F; --neg-solid:#A8353D;
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

/* ---- app navigation: only rendered when serve.py is the host ---- */
/* Beveled corners, the way a tactics UI cuts them - cheap to draw, and it
   stops the menu reading like a documentation site's nav. */
.tabs { display:flex; flex-wrap:wrap; gap:7px; align-items:center;
        margin:28px 0 0; font-family:var(--mono); font-size:11.5px; }
.tabs a, .tabs button {
  padding:9px 16px; border:0; border-radius:0;
  clip-path:polygon(8px 0, 100% 0, 100% calc(100% - 8px),
                    calc(100% - 8px) 100%, 0 100%, 0 8px);
  background:var(--track); color:var(--muted); text-decoration:none;
  font:inherit; letter-spacing:.11em; text-transform:uppercase;
  cursor:pointer; transition:background .14s ease, color .14s ease; }
.tabs a:hover, .tabs button:hover { background:var(--line); color:var(--ink); }
.tabs a.active { background:var(--accent-solid); color:var(--accent-ink);
                 font-weight:700; }
/* An icon-only control: square, and the icon sits centred rather than on
   the text baseline. */
.tabs button.icon { display:flex; align-items:center; justify-content:center;
                    padding:9px 12px; }
/* The way out, set apart from the three views: it changes who you are
   looking at, not what you are looking at. */
.tabs a.switch { background:none; color:var(--muted);
                 box-shadow:inset 0 0 0 1px var(--line); }
.tabs a.switch:hover { color:var(--ink); background:none;
                       box-shadow:inset 0 0 0 1px var(--muted); }
/* The game picker, coloured by result: top 4 gains LP, bottom 4 loses it,
   so a glance down the row shows the shape of a session. The current game
   is the filled one - colour says won/lost, fill says "you are here". */
/* Every chip is filled with its result - green for a top 4, red for a
   bottom 4 - so a row of recent games is a block of colour you read at a
   glance. The game you are looking at gets a gold ring around that fill.
   The ring is not a CSS border: clip-path would clip a border away at the
   bevelled corners, so it is the outer element's background showing past
   an inner span inset by 2px. */
.picker { margin:18px 0 0; }
.picker a { padding:2px; }
.picker a > span {
  display:block; padding:7px 14px; background:transparent;
  color:#fff; font-weight:700;
  clip-path:polygon(7px 0, 100% 0, 100% calc(100% - 7px),
                    calc(100% - 7px) 100%, 0 100%, 0 7px); }
.picker a.win  { background:var(--pos-solid); }
.picker a.loss { background:var(--neg-solid); }
.picker a:hover { filter:brightness(1.12); }
/* The ring on the current game is black, not gold: a gold ring around a
   red chip is two warm hues fighting, and black frames either fill without
   introducing a third colour. It runs thicker than the 2px other chips
   carry, because on the near-black page a 2px black ring reads as nothing
   at all - at 4px the inset is plainly deliberate in either theme. */
.picker a.active { background:#000; padding:4px; }
.picker a.active > span { font-weight:800; }
.picker a.win.active  > span { background:var(--pos-solid); }
.picker a.loss.active > span { background:var(--neg-solid); }
/* Generic, so a placement reads the same colour wherever it appears - the
   headline, the picker, the lobby table. The picker's active chip keeps its
   own colour because .picker a.win.active is the more specific rule. */
.win  { color:var(--pos); }
.loss { color:var(--neg); }
/* The LP break drawn where it falls in the lobby: 4th and up gained, 5th
   and down lost. */
tr.lpbreak td { border-top:2px solid var(--muted); }
tr.me td { font-weight:700; }
tr.me td:first-child { box-shadow:inset 3px 0 0 var(--accent); }
.scoutbox textarea {
  width:100%; min-height:82px; box-sizing:border-box;
  font-family:var(--mono); font-size:14px; padding:12px 14px;
  border:1px solid var(--line); border-radius:3px;
  background:var(--panel); color:var(--ink); resize:vertical; }
.scoutbox button {
  margin-top:10px; font-family:var(--mono); font-weight:700;
  font-size:12px; letter-spacing:.11em; text-transform:uppercase;
  padding:11px 22px; border:0; border-radius:0; cursor:pointer;
  clip-path:polygon(9px 0, 100% 0, 100% calc(100% - 9px),
                    calc(100% - 9px) 100%, 0 100%, 0 9px);
  background:var(--accent-solid); color:var(--accent-ink); }
.scoutbox button:hover { filter:brightness(1.12); }
details summary { cursor:pointer; font-family:var(--mono); font-size:12.5px;
                  color:var(--muted); margin:16px 0 12px; }
details summary:hover { color:var(--ink); }

/* ---- dashboard layout: the app pages, as panels rather than an essay ----
   The stats page stays a document you read top to bottom. These are the
   screens you check between games, so they are a grid of cards with one
   idea each, no standfirsts, and every long explanation folded into small
   print or a <details>. */
.app { max-width:1080px; margin:0 auto; padding:0 20px 64px; }
.bar { display:flex; flex-wrap:wrap; align-items:center; gap:12px;
       padding:16px 0 14px; border-bottom:1px solid var(--line);
       margin-bottom:20px; }
/* ---- the wordmark: a scrap of hex board, then the name ---- */
.brand { display:inline-flex; align-items:center; gap:9px;
         text-decoration:none; color:var(--ink); }
.brand b { font-weight:800; font-size:17px; letter-spacing:-.02em; }
.brand span { color:var(--accent); }
.hexmark { display:block; overflow:visible; }
/* Mitred joins, not round: at 24px a rounded 1.5px join eats the corners
   and three hexagons turn into three blobs. */
.hexmark .h-out { fill:none; stroke:var(--muted); stroke-width:1.5;
                  stroke-linejoin:miter; }
.hexmark .h-me { fill:var(--accent); }
.hexmark polygon { transition:transform .18s ease, fill .18s ease; }
/* The gold hex - you - hops when you hover the logo. One small piece of
   whimsy; the reduced-motion rule at the top of this sheet turns it off. */
.brand:hover .h-me { transform:translateY(-2px); }
.brand:hover .h-out { stroke:var(--accent); opacity:.55; }
.bar .who { font-family:var(--mono); font-size:12.5px; color:var(--muted);
            margin-right:auto; }
.bar .tabs { margin:0; }
.hero { display:flex; flex-wrap:wrap; align-items:baseline; gap:14px;
        margin:0 0 18px; }
.hero h1 { font-size:clamp(26px,4vw,34px); letter-spacing:-.03em;
           font-weight:800; margin:0; }
.hero .sub { font-family:var(--mono); font-size:12.5px; color:var(--muted); }
/* Purpose line: what this page is for, on its own row under the hero. Its
   own row because .hero is a baseline flex and a sentence inside it aligns
   against the h1 and reads as a subtitle rather than an explanation. */
.purpose { color:var(--muted); font-size:14.5px; line-height:1.5;
           margin:-8px 0 20px; max-width:62ch; }
/* A folded explanation. Sits quiet until asked: the reasoning is the point
   of this app and it is not what somebody wants before they queue. */
.why { margin:12px 0 0; }
.why summary { font-family:var(--mono); font-size:11.5px; letter-spacing:.08em;
               text-transform:uppercase; color:var(--muted); cursor:pointer; }
.why summary:hover { color:var(--ink); }
.why .fine { margin:8px 0 0; }
/* Two columns at desktop width, one on a laptop or phone. minmax is set
   past a third of the container on purpose: three columns of these cards
   leaves an empty slot far more often than it fills one. */
.grid { display:grid; gap:14px; align-items:start;
        grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); }
.grid .full { grid-column:1/-1; }
.card { background:var(--panel); border:1px solid var(--line);
        border-radius:4px; padding:16px 18px; }
.card > h3 { font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
             text-transform:uppercase; color:var(--muted); font-weight:400;
             margin:0 0 12px; }
.card p { max-width:none; }
.card .tablewrap { background:none; border:0; padding:0; }
.card .stats { margin:0; border:0; background:var(--line); }
.card details summary { margin:12px 0 0; }
.fine { color:var(--muted); font-size:12.5px; line-height:1.5; margin:12px 0 0; }
.banner { background:var(--panel); border:1px solid var(--neg);
          border-left-width:3px; border-radius:3px; padding:12px 16px;
          margin:0 0 16px; font-size:14px; }
/* "You just finished a game" - hidden until the watcher says so. */
#newgame { display:none; align-items:center; gap:14px;
           background:var(--panel); border:1px solid var(--accent);
           border-left-width:3px; border-radius:3px; padding:12px 16px;
           margin:0 0 16px; font-size:14px; }
#newgame a { color:var(--accent); font-weight:700; }
.big { font-family:var(--mono); font-size:30px; letter-spacing:-.02em;
       font-variant-numeric:tabular-nums; display:block; }
.tip { margin:0 0 14px; }
.tip:last-child { margin-bottom:0; }
.tip strong { display:block; font-size:15.5px; line-height:1.45; }
.tip span { color:var(--muted); font-size:13px; }
.rowlist { list-style:none; margin:0; padding:0; }
.rowlist li { display:flex; justify-content:space-between; gap:12px;
              padding:9px 0; border-top:1px solid var(--line); font-size:14px; }
.rowlist li:first-child { border-top:0; padding-top:0; }
.rowlist .k { color:var(--muted); }
.rowlist .v { font-family:var(--mono); font-variant-numeric:tabular-nums;
              white-space:nowrap; }
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
