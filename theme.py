"""One look for every page the tracker serves.

The stats page and the post-game page are two views of the same player, so
they share one stylesheet, one tooltip script, and one set of text helpers.
They live here rather than in build_site.py because build_site.py is a
top-to-bottom script - importing it re-runs it - while every page needs
these pieces.
"""

import html


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

/* ---- app navigation: only rendered when serve.py is the host ---- */
.tabs { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
        margin:28px 0 0; font-family:var(--mono); font-size:12.5px; }
.tabs a, .tabs button {
  padding:7px 14px; border:1px solid var(--line); border-radius:3px;
  color:var(--muted); background:none; text-decoration:none;
  font:inherit; cursor:pointer; }
.tabs a.active { color:var(--ink); border-color:var(--accent); }
.tabs a:hover, .tabs button:hover { color:var(--ink); }
.picker { margin:18px 0 0; }
tr.me td { font-weight:700; }
tr.me td:first-child { box-shadow:inset 3px 0 0 var(--accent); }
.scoutbox textarea {
  width:100%; min-height:82px; box-sizing:border-box;
  font-family:var(--mono); font-size:14px; padding:12px 14px;
  border:1px solid var(--line); border-radius:3px;
  background:var(--panel); color:var(--ink); resize:vertical; }
.scoutbox button {
  margin-top:10px; font-family:var(--display); font-weight:700;
  font-size:15px; padding:11px 20px; border:0; border-radius:3px;
  cursor:pointer; background:var(--accent); color:#fff; }
.scoutbox button:hover { filter:brightness(1.08); }
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
.bar .who { font-family:var(--mono); font-size:12.5px; color:var(--muted);
            margin-right:auto; }
.bar .tabs { margin:0; }
.hero { display:flex; flex-wrap:wrap; align-items:baseline; gap:14px;
        margin:0 0 18px; }
.hero h1 { font-size:clamp(26px,4vw,34px); letter-spacing:-.03em;
           font-weight:800; margin:0; }
.hero .sub { font-family:var(--mono); font-size:12.5px; color:var(--muted); }
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
