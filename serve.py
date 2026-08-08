"""A little local web app: type a Riot ID, get that player's report.

Run:  export RIOT_API_KEY='RGAPI-...'
      python serve.py
      open http://127.0.0.1:8000

Why this is a server and not just a page
----------------------------------------
The generated site/index.html is a static file, and a static file cannot go
and fetch someone's match history. Three separate walls stop it:

  - Riot's API sends no CORS headers, so browser JavaScript cannot call it
    at all, from any page.
  - Your API key would have to travel to the browser to make that call, which
    means handing it to anyone who opens the page.
  - The published claude.ai copy runs under a strict CSP that blocks every
    external host outright.

So the key stays here, on your machine, and this process does the fetching.
It binds to 127.0.0.1 on purpose: nothing outside this computer can reach it,
which is what makes holding the key in memory safe. Putting it on the open
internet would need a rethink, starting with not trusting whoever connects.

What it costs
-------------
A player nobody has looked up before needs their history downloaded, and a
personal key allows 100 requests every 2 minutes - so a first lookup takes
roughly a minute for GAMES_WANTED games. After that they are cached in
matches/ and it is instant. Because every match file holds all 8 boards, one
person's download also fills in seven other people's history for free.
"""

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import postgame_page
import pregame_page
import riot
import scout
import theme
import tftdata as tft

HOST, PORT = "127.0.0.1", 8000
GAMES_WANTED = 50    # enough to clear the 30-game bar without a 4-minute wait
REFRESH_WANTED = 10  # "check for a new game" only needs the newest few

JOBS = {}
JOBS_LOCK = threading.Lock()

STATS_CACHE = {}   # (puuid, label) -> (newest match id, rendered stats page)


def own_dir(puuid):
    """matches/ when the player is the owner of this copy, else the pool.

    matches/ is the durable, committed folder and it must contain only the
    owner's games - but the owner's OWN new games belong there, or the copy
    that matters would sit in the re-downloadable pool while fetch_matches.py
    pays a second request to put it where it should have gone.
    """
    try:
        me = json.loads(Path("me.json").read_text())["puuid"]
    except (FileNotFoundError, KeyError):
        return None
    return tft.MATCH_DIR if puuid == me else None


def set_job(job_id, **fields):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


def get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id) or {})


def run_job(job_id, riot_id):
    """Resolve, backfill, render. Runs on its own thread so the page can poll."""
    try:
        set_job(job_id, status="Looking up the account...")
        puuid, label = riot.resolve(riot_id)

        set_job(job_id, status=f"Found {label}. Checking saved games...", label=label)

        def progress(i, total, have):
            set_job(job_id, status=f"Downloading {label}'s games - {i} of {total} "
                                   f"checked, {have} ready")

        have, _ = riot.backfill(puuid, want=GAMES_WANTED, progress=progress,
                                into=own_dir(puuid))
        if not have:
            set_job(job_id, status=None, error=(
                f"{label} has no ranked TFT games in the current set. "
                "Only ranked games are analysed."))
            return

        set_job(job_id, status=None,
                redirect=f"/player?p={quote(puuid)}&n={quote(label)}")

    except riot.RiotError as problem:
        set_job(job_id, status=None, error=str(problem))
    except Exception as problem:                      # noqa: BLE001
        set_job(job_id, status=None, error=f"{type(problem).__name__}: {problem}")


def refresh_job(job_id, puuid, label):
    """Fetch whatever this player has played since we last looked, then land
    on the newest game's debrief. A just-finished game shows up in Riot's
    history within a minute or two of the end screen, so this is the
    play -> alt-tab -> refresh loop."""
    try:
        def progress(i, total, have):
            set_job(job_id, status=f"Checking recent games - {i} of {total}")

        riot.backfill(puuid, want=REFRESH_WANTED, progress=progress,
                      into=own_dir(puuid))
        set_job(job_id, status=None,
                redirect=f"/game?p={quote(puuid)}&n={quote(label)}")
    except riot.RiotError as problem:
        set_job(job_id, status=None, error=str(problem))
    except Exception as problem:                      # noqa: BLE001
        set_job(job_id, status=None, error=f"{type(problem).__name__}: {problem}")


def scout_job(job_id, puuid, label, raw_names):
    """Scout the typed lobby, then hand back the pre-game page with the
    results filled in. Names already in the saved games cost nothing; a
    stranger costs a resolve plus a few match downloads."""
    try:
        names = [n.strip() for n in re.split(r"[\n,]+", raw_names)
                 if n.strip()][:8]
        if not names:
            set_job(job_id, status=None,
                    error="No names to scout - type one per line.")
            return

        games, _ = tft.load_games(puuid)
        set_number = games[0]["_set"]
        boards_by_puuid, _ = scout.local_boards(set_number)

        # Names live inside the match files, so anyone you've already met
        # can be found with no network at all - including tagless names
        # from the loading screen, when they're unambiguous.
        by_full, by_game_name = {}, {}
        for pid, boards in boards_by_puuid.items():
            b = boards[-1]
            gname = (b.get("riotIdGameName") or "").lower()
            tag = (b.get("riotIdTagline") or "").lower()
            if gname:
                by_full[f"{gname}#{tag}"] = pid
                by_game_name.setdefault(gname, set()).add(pid)

        results = []
        for i, who in enumerate(names, start=1):
            set_job(job_id, status=f"Scouting {who} - {i} of {len(names)}...")
            pid, shown = by_full.get(who.lower()), who
            if pid is None and "#" not in who:
                candidates = by_game_name.get(who.lower(), set())
                if len(candidates) == 1:
                    pid = next(iter(candidates))
            if pid is None:
                if "#" not in who:
                    results.append({"label": who, "error":
                                    "not in your saved games - add the #tag "
                                    "(like name#NA1) so Riot can find them"})
                    continue
                try:
                    pid, shown = riot.resolve(who)
                except riot.RiotError as problem:
                    results.append({"label": who, "error": str(problem)})
                    continue

            have = boards_by_puuid.get(pid, [])
            note = None
            if len(have) < scout.ENOUGH_LOCALLY:
                set_job(job_id, status=f"Downloading {shown}'s games...")
                try:
                    riot.backfill(pid, want=scout.FETCH_PER_PLAYER,
                                  set_number=set_number)
                    boards_by_puuid, _ = scout.local_boards(set_number)
                    have = boards_by_puuid.get(pid, [])
                except riot.RiotError as problem:
                    note = f"showing saved games only: {problem}"
            results.append(pregame_page.player_read(shown, have,
                                                    set_number, note))

        set_job(job_id, status=None,
                html=pregame_page.render(games, set_number, label, puuid,
                                         scouted=results))
    except (Exception, SystemExit) as problem:  # noqa: BLE001
        set_job(job_id, status=None, error=str(problem))


def build_stats(puuid, label):
    """The stats page for one player, rebuilt only when their games change.

    build_site.py runs as a separate process rather than an import: it is a
    top-to-bottom script, so importing it twice would re-execute it, and a
    subprocess also means one player's crash can't take the server down.
    TFT_LINKS tells the page it is being served by the app, which is what
    puts the Stats / Post-game tabs on it.
    """
    games, _ = tft.load_games(puuid)
    newest = games[0]["_match_id"]
    cached = STATS_CACHE.get((puuid, label))
    if cached and cached[0] == newest:
        return cached[1]

    out = Path(tempfile.mkdtemp(prefix="tft-report-"))
    env = {**os.environ, "TFT_PUUID": puuid, "TFT_OUT": str(out),
           "TFT_NAME": label, "TFT_LINKS": "1"}
    done = subprocess.run([sys.executable, "build_site.py"], env=env,
                          capture_output=True, text=True,
                          cwd=Path(__file__).parent)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "unknown error")

    page = (out / "index.html").read_text()
    STATS_CACHE[(puuid, label)] = (newest, page)
    return page


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyTFT</title>
{refresh}
<style>
:root {{
  --bg:#F5F6F8; --panel:#fff; --line:#DFE3EA; --ink:#171B22; --muted:#5A6478;
  --accent:#B4761A; --neg:#C24A52;
  --accent-solid:#8F5D12; --accent-ink:#FFFFFF;
  --display:"Avenir Next","Avenir",ui-sans-serif,system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0B0E14; --panel:#141A24; --line:#232C3A; --ink:#E4E8F0;
           --muted:#8792A8; --accent:#BE831A; --neg:#D25A61;
           --accent-solid:#C58A1E; --accent-ink:#0B0E14; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center;
       background:var(--bg); color:var(--ink); font-family:var(--display);
       line-height:1.6; padding:24px; }}
.card {{ width:100%; max-width:480px; }}
.eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.16em;
           text-transform:uppercase; color:var(--muted); margin:0 0 8px; }}
h1 {{ font-size:34px; letter-spacing:-.03em; line-height:1.1; margin:0 0 10px;
     font-weight:800; }}
p {{ color:var(--muted); margin:0 0 22px; }}
form {{ display:flex; gap:10px; flex-wrap:wrap; }}
input {{ flex:1 1 220px; font-family:var(--mono); font-size:16px; padding:12px 14px;
        border:1px solid var(--line); border-radius:3px; background:var(--panel);
        color:var(--ink); }}
button {{ font-family:var(--mono); font-weight:700; font-size:12px;
         letter-spacing:.11em; text-transform:uppercase;
         padding:13px 22px; border:0; border-radius:0; cursor:pointer;
         clip-path:polygon(9px 0, 100% 0, 100% calc(100% - 9px),
                           calc(100% - 9px) 100%, 0 100%, 0 9px);
         background:var(--accent-solid); color:var(--accent-ink); }}
button:hover {{ filter:brightness(1.12); }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.status {{ font-family:var(--mono); font-size:13.5px; color:var(--muted);
          border-left:3px solid var(--accent); padding:10px 14px;
          background:var(--panel); border-radius:3px; }}
.error {{ border-left-color:var(--neg); color:var(--ink); }}
a {{ color:var(--accent); }}
.brand {{ display:inline-flex; align-items:center; gap:10px;
         margin:0 0 20px; color:var(--ink); text-decoration:none; }}
.brand b {{ font-weight:800; font-size:21px; letter-spacing:-.02em; }}
.brand span {{ color:var(--accent); }}
.hexmark {{ display:block; overflow:visible; }}
.hexmark .h-out {{ fill:none; stroke:var(--muted); stroke-width:1.5;
                  stroke-linejoin:miter; }}
.hexmark .h-me {{ fill:var(--accent); }}
.hexmark polygon {{ transition:transform .18s ease; }}
.brand:hover .h-me {{ transform:translateY(-2px); }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style></head><body><div class="card">
<p class="brand">{brand}</p>
{content}
</div></body></html>
"""


def page(content, refresh=None):
    tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return PAGE.format(content=content, refresh=tag,
                       brand=theme.BRAND_HTML).encode()


def home(message=""):
    note = f'<p class="status error">{html.escape(message)}</p>' if message else ""
    return page(f"""
<h1>Whose games?</h1>
<p>Type any Riot ID. Other sites tell you what the world is playing; this
one tells you how <em>you</em> play, measured against the seven other
people in your own lobbies. A player nobody has looked up yet takes about
a minute to download; after that it&rsquo;s instant.</p>
<form method="post" action="/go">
  <input name="riot_id" placeholder="orobas#tomo" autofocus
         aria-label="Riot ID" required>
  <button type="submit">Look them up</button>
</form>
{note}""")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass          # the default logger writes a line per request; too noisy

    def reply(self, body, code=200, content_type="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/":
            self.reply(home())
            return

        if route.path == "/player":
            q = parse_qs(route.query)
            puuid = (q.get("p") or [""])[0]
            label = (q.get("n") or [""])[0]
            if not puuid:
                self.reply(home("That link is missing a player."), 404)
                return
            try:
                self.reply(build_stats(puuid, label).encode())
            except SystemExit as problem:
                # load_games exits a CLI; here it just means "nobody by that
                # id is on disk", which the front page can say politely.
                self.reply(home(str(problem)), 404)
            except Exception as problem:              # noqa: BLE001
                self.reply(home(f"Couldn't build the report: {problem}"), 500)
            return

        if route.path == "/pregame":
            q = parse_qs(route.query)
            puuid = (q.get("p") or [""])[0]
            label = (q.get("n") or [""])[0]
            try:
                games, _ = tft.load_games(puuid or None)
            except SystemExit as problem:
                self.reply(home(str(problem)), 404)
                return
            set_number = games[0]["_set"]
            if not label:
                name = games[0].get("riotIdGameName") or games[0]["puuid"][:8]
                tag = games[0].get("riotIdTagline") or "?"
                label = f"{name}#{tag}"
            try:
                self.reply(pregame_page.render(
                    games, set_number, label, games[0]["puuid"]).encode())
            except Exception as problem:              # noqa: BLE001
                self.reply(home(f"Couldn't build the pre-game screen: "
                                f"{problem}"), 500)
            return

        if route.path == "/game":
            q = parse_qs(route.query)
            puuid = (q.get("p") or [""])[0]
            label = (q.get("n") or [""])[0]
            try:
                games, level_par = tft.load_games(puuid or None)
            except SystemExit as problem:
                self.reply(home(str(problem)), 404)
                return
            set_number = games[0]["_set"]
            if not label:
                name = games[0].get("riotIdGameName") or games[0]["puuid"][:8]
                tag = games[0].get("riotIdTagline") or "?"
                label = f"{name}#{tag}"
            try:
                idx = int((q.get("g") or ["1"])[0])
            except ValueError:
                idx = 1
            idx = min(max(idx, 1), len(games)) - 1
            try:
                self.reply(postgame_page.render(
                    games, level_par, set_number, idx,
                    label, games[0]["puuid"]).encode())
            except Exception as problem:              # noqa: BLE001
                self.reply(home(f"Couldn't build the debrief: {problem}"), 500)
            return

        if route.path.startswith("/job/"):
            job = get_job(route.path.rsplit("/", 1)[-1])
            if not job:
                self.reply(home("That lookup expired - try again."), 404)
            elif job.get("error"):
                self.reply(page(f'<h1>That didn\'t work</h1>'
                                f'<p class="status error">{html.escape(job["error"])}</p>'
                                f'<p><a href="/">Try another Riot ID</a></p>'))
            elif job.get("redirect"):
                self.send_response(303)
                self.send_header("Location", job["redirect"])
                self.end_headers()
            elif job.get("html"):
                self.reply(job["html"].encode())
            else:
                # Poll by meta-refresh rather than JavaScript: no script to
                # debug, and it works with JS switched off like the report does.
                self.reply(page(
                    f'<h1>Working on it</h1>'
                    f'<p class="status">{html.escape(job.get("status") or "Starting...")}</p>'
                    f'<p>This page refreshes itself. A first lookup is about a '
                    f'minute &mdash; Riot allows 100 requests every 2 minutes.</p>',
                    refresh=2))
            return

        self.reply(home("Unknown page."), 404)

    def do_POST(self):
        route = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        fields = parse_qs(self.rfile.read(length).decode())

        if route.path == "/scout":
            puuid = (fields.get("p") or [""])[0]
            label = (fields.get("n") or [""])[0]
            names = (fields.get("names") or [""])[0]
            if not puuid:
                self.reply(home("That scout is missing a player."), 404)
                return
            job_id = uuid.uuid4().hex[:12]
            set_job(job_id, status="Reading your saved games...")
            threading.Thread(target=scout_job,
                             args=(job_id, puuid, label, names),
                             daemon=True).start()
            self.send_response(303)
            self.send_header("Location", f"/job/{job_id}")
            self.end_headers()
            return

        if route.path == "/refresh":
            puuid = (fields.get("p") or [""])[0]
            label = (fields.get("n") or [""])[0]
            if not puuid:
                self.reply(home("That refresh is missing a player."), 404)
                return
            job_id = uuid.uuid4().hex[:12]
            set_job(job_id, status="Asking Riot for new games...")
            threading.Thread(target=refresh_job, args=(job_id, puuid, label),
                             daemon=True).start()
            self.send_response(303)
            self.send_header("Location", f"/job/{job_id}")
            self.end_headers()
            return

        riot_id = (fields.get("riot_id") or [""])[0].strip()
        if "#" not in riot_id:
            self.reply(home("A Riot ID needs the tag too, like orobas#tomo"))
            return

        job_id = uuid.uuid4().hex[:12]
        set_job(job_id, status="Starting...")
        threading.Thread(target=run_job, args=(job_id, riot_id), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", f"/job/{job_id}")
        self.end_headers()


if __name__ == "__main__":
    # Fail at startup, not on the first lookup: riot.api_key() checks the
    # env var and the .riot_key file, and its message says how to fix both.
    try:
        riot.api_key()
    except riot.RiotError as problem:
        sys.exit(f"{problem}\n"
                 "It stays in this process and is never sent to the browser.")
    print(f"MyTFT running at http://{HOST}:{PORT}")
    print("Local only - nothing outside this machine can reach it. Ctrl-C to stop.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
