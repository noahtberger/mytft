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
import importlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import postgame_page
import pregame_page
import seed as seed_mod
import riot
import scout
import theme
import tftdata as tft

PORT = 8000


def lan_address():
    """This machine's address on the local network, for the URL to share."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))    # no packet is sent; this just
        return probe.getsockname()[0]     # picks the outbound interface
    except Exception:                     # noqa: BLE001
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


def bind_host():
    """Loopback unless LAN access was deliberately switched on.

    127.0.0.1 means "this machine only" and is the safe default: the app
    has no login, so anything that can reach it can spend your API key.
    Creating a file called `lan-access` (or setting TFT_LAN=1) binds every
    interface instead, which is what lets another device on the same wifi
    open it. Turn it off by deleting the file - no code change, and no way
    to leave it on by accident after a rebuild.
    """
    if os.environ.get("TFT_LAN") or Path("lan-access").exists():
        return "0.0.0.0"
    return "127.0.0.1"


HOST = bind_host()
GAMES_WANTED = 50    # enough to clear the 30-game bar without a 4-minute wait
REFRESH_WANTED = 10  # "check for a new game" only needs the newest few

JOBS = {}
JOBS_LOCK = threading.Lock()

STATS_CACHE = {}   # (puuid, label) -> (newest match id, rendered stats page)

RECENT_FILE = Path("recent.json")   # gitignored; just a convenience list
RECENT_MAX = 8
RECENT_LOCK = threading.Lock()

WATCH_EVERY = 180        # seconds between "have you finished a game yet?"
WATCH = {"match_id": None, "checked": 0}   # what the poller last saw
WATCH_LOCK = threading.Lock()


def watch_for_games():
    """Notice when the owner of this copy finishes a game.

    One request per cycle - the list of recent match ids - against a budget
    of a hundred every two minutes, so it is close to free. It only fetches
    the game itself when the newest id is one we have never seen, and only
    for the owner: polling on behalf of every player ever looked up would
    spend the whole rate limit on people who aren't playing.

    The page asks /latest whether anything changed. Nothing here pushes.
    """
    try:
        owner = json.loads(Path("me.json").read_text())["puuid"]
    except (FileNotFoundError, KeyError):
        return                     # nobody owns this copy yet; nothing to watch

    while True:
        try:
            ids = riot.riot_get(
                f"{riot.REGIONAL}/tft/match/v1/matches/by-puuid/{owner}/ids",
                start=0, count=1)
            newest = ids[0] if ids else None
            if newest and newest not in riot.saved_match_ids():
                riot.backfill(owner, want=3, into=own_dir(owner))
                STATS_CACHE.clear()          # their numbers just changed
                with WATCH_LOCK:
                    WATCH["match_id"] = newest
            with WATCH_LOCK:
                WATCH["checked"] = int(time.time())
        except Exception:                    # noqa: BLE001
            pass        # a key that expired overnight must not kill the app
        time.sleep(WATCH_EVERY)


def recent_players():
    """Who has been looked up on this machine, newest first."""
    try:
        return json.loads(RECENT_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return []


def remember_player(puuid, label):
    """Put a player at the top of the recent list.

    Kept in a file rather than in memory so the list survives a restart -
    the whole point is not having to retype a friend's Riot ID, and the
    server gets restarted plenty.
    """
    if not puuid or not label:
        return
    with RECENT_LOCK:
        recents = [r for r in recent_players() if r.get("puuid") != puuid]
        recents.insert(0, {"puuid": puuid, "label": label})
        try:
            RECENT_FILE.write_text(json.dumps(recents[:RECENT_MAX], indent=2))
        except OSError:
            pass          # a convenience list is never worth a 500


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
        set_job(job_id, title="Looking up their games",
                status="Finding the account...")
        puuid, label = riot.resolve(riot_id)

        set_job(job_id, status=f"Found {label}. Checking saved games...",
                label=label)

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
        set_job(job_id, title="Looking for new games")

        def progress(i, total, have):
            set_job(job_id, status=f"Checking your recent games - {i} of {total}")

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
        set_job(job_id, title="Scouting your lobby")
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


BUILD_LOCK = threading.Lock()


def seed_job(job_id, puuid, label):
    """Pull ladder games into the pool, then land back on the pre-game
    screen with the sharper meta."""
    try:
        set_job(job_id, title="Seeding from the ladder")

        def progress(i, total, tier, lp):
            set_job(job_id, status=f"Player {i} of {total} - "
                                   f"a {tier} player at {lp} LP")

        seed_mod.seed(players=5, games_each=10, progress=progress)
        set_job(job_id, status=None,
                redirect=f"/pregame?p={quote(puuid)}&n={quote(label)}")
    except riot.RiotError as problem:
        set_job(job_id, status=None, error=str(problem))
    except Exception as problem:                      # noqa: BLE001
        set_job(job_id, status=None, error=f"{type(problem).__name__}: {problem}")


def build_stats(puuid, label):
    """The stats page for one player, rebuilt only when their games change.

    build_site.py is a top-to-bottom script: importing it runs it, and
    reloading it runs it again. That is exactly the behaviour wanted here,
    so the page is built in this process rather than by launching another
    Python. It used to be a subprocess, which was tidier in one way - a
    crash couldn't reach the server - but a packaged app has no separate
    Python to launch, and spawning one per lookup was slow besides.

    The script is configured by environment variables, which are global, so
    the lock is not optional: two lookups at once would otherwise build each
    other's pages into each other's folders.
    """
    games, _ = tft.load_games(puuid)
    newest = games[0]["_match_id"]
    cached = STATS_CACHE.get((puuid, label))
    if cached and cached[0] == newest:
        return cached[1]

    out = Path(tempfile.mkdtemp(prefix="tft-report-"))
    with BUILD_LOCK:
        previous = {k: os.environ.get(k) for k in
                    ("TFT_PUUID", "TFT_OUT", "TFT_NAME", "TFT_LINKS")}
        os.environ.update({"TFT_PUUID": puuid, "TFT_OUT": str(out),
                           "TFT_NAME": label, "TFT_LINKS": "1"})
        try:
            import build_site
            importlib.reload(build_site)
        finally:
            for key, was in previous.items():
                if was is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = was

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
:root[data-theme="dark"] {{ --bg:#0B0E14; --panel:#141A24; --line:#232C3A;
           --ink:#E4E8F0; --muted:#8792A8; --accent:#BE831A; --neg:#D25A61;
           --accent-solid:#C58A1E; --accent-ink:#0B0E14; }}
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
.recent-head {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
               text-transform:uppercase; color:var(--muted);
               margin:26px 0 10px; }}
.recent {{ display:flex; flex-wrap:wrap; gap:7px; }}
.recent a {{ font-family:var(--mono); font-size:12px; padding:8px 13px;
            color:var(--ink); text-decoration:none; background:var(--panel);
            box-shadow:inset 0 0 0 1px var(--line);
            clip-path:polygon(7px 0, 100% 0, 100% calc(100% - 7px),
                              calc(100% - 7px) 100%, 0 100%, 0 7px); }}
.recent a:hover {{ box-shadow:inset 0 0 0 1px var(--accent); }}
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
    recents = recent_players()
    if recents:
        chips = "".join(
            f'<a href="/pregame?p={quote(r["puuid"])}&amp;n={quote(r["label"])}">'
            f'{html.escape(r["label"])}</a>' for r in recents)
        note += f'<p class="recent-head">Recently looked up</p>' \
                f'<div class="recent">{chips}</div>'
    return page(f"""
<h1>Whose games?</h1>
<p>Type any Riot ID. Other sites tell you what the world is playing; this
one tells you how <em>you</em> play, measured against the seven other
people in your own lobbies. A player nobody has looked up yet takes about
a minute to download; after that it&rsquo;s instant.</p>
<form method="post" action="/go">
  <input name="riot_id" placeholder="Name#TAG" autofocus
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
                page_html = build_stats(puuid, label)
                remember_player(puuid, label)
                self.reply(page_html.encode())
            except SystemExit as problem:
                # load_games exits a CLI; here it just means "nobody by that
                # id is on disk", which the front page can say politely.
                self.reply(home(str(problem)), 404)
            except Exception as problem:              # noqa: BLE001
                self.reply(home(f"Couldn't build the report: {problem}"), 500)
            return

        if route.path == "/latest":
            with WATCH_LOCK:
                body = json.dumps(dict(WATCH)).encode()
            self.reply(body, content_type="application/json")
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
                out = pregame_page.render(games, set_number, label,
                                          games[0]["puuid"])
                remember_player(games[0]["puuid"], label)
                self.reply(out.encode())
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
                out = postgame_page.render(games, level_par, set_number, idx,
                                           label, games[0]["puuid"])
                remember_player(games[0]["puuid"], label)
                self.reply(out.encode())
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
                    # The heading comes from the job, so a refresh says it is
                    # looking for new games rather than "working on it". No
                    # rate-limit arithmetic on screen: that is our problem to
                    # handle, not something to explain to whoever is waiting.
                    f'<h1>{html.escape(job.get("title") or "One moment")}</h1>'
                    f'<p class="status">{html.escape(job.get("status") or "Starting...")}</p>'
                    f'<p>This page updates itself &mdash; no need to touch '
                    f'anything.</p>',
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
            set_job(job_id, title="Scouting your lobby",
                    status="Reading your saved games...")
            threading.Thread(target=scout_job,
                             args=(job_id, puuid, label, names),
                             daemon=True).start()
            self.send_response(303)
            self.send_header("Location", f"/job/{job_id}")
            self.end_headers()
            return

        if route.path == "/seed":
            puuid = (fields.get("p") or [""])[0]
            label = (fields.get("n") or [""])[0]
            job_id = uuid.uuid4().hex[:12]
            set_job(job_id, title="Seeding from the ladder",
                    status="Reading the ladder...")
            threading.Thread(target=seed_job, args=(job_id, puuid, label),
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
            set_job(job_id, title="Looking for new games",
                    status="Checking for anything new...")
            threading.Thread(target=refresh_job, args=(job_id, puuid, label),
                             daemon=True).start()
            self.send_response(303)
            self.send_header("Location", f"/job/{job_id}")
            self.end_headers()
            return

        riot_id = (fields.get("riot_id") or [""])[0].strip()
        if "#" not in riot_id:
            self.reply(home("A Riot ID needs the tag too, like Name#NA1"))
            return

        job_id = uuid.uuid4().hex[:12]
        set_job(job_id, title="Looking up their games",
                status="Starting...")
        threading.Thread(target=run_job, args=(job_id, riot_id), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", f"/job/{job_id}")
        self.end_headers()


def serve_forever():
    """Run the app. Split out of __main__ so the packaged launcher can
    call it too, rather than the two starting the server different ways."""
    threading.Thread(target=watch_for_games, daemon=True).start()
    host = bind_host()
    try:
        server = ThreadingHTTPServer((host, PORT), Handler)
    except OSError as problem:
        if host == "127.0.0.1":
            raise
        # Binding every interface needs macOS's local-network permission,
        # which a freshly rebuilt unsigned app never has - and dying here
        # took the whole app down and left windows pointing at a dead
        # port. Local-only always works; degrade to it and say so.
        print(f"LAN bind failed ({problem}); falling back to local-only.")
        host = "127.0.0.1"
        server = ThreadingHTTPServer((host, PORT), Handler)

    # The banner prints AFTER the bind now. It used to print before, which
    # wrote "MyTFT running at ..." into the log of a server that then
    # failed to exist - an actively misleading breadcrumb.
    if host == "127.0.0.1":
        print(f"MyTFT running at http://127.0.0.1:{PORT}")
        print("Local only - nothing outside this machine can reach it.")
    else:
        print(f"MyTFT running at http://{lan_address()}:{PORT}")
        print("LAN access is ON: anything on this network can use it, and "
              "there is no login. Delete the `lan-access` file to turn it off.")
    print("Ctrl-C to stop.")
    server.serve_forever()


def check_key():
    """Fail at startup, not on the first lookup. Returns an error string
    rather than exiting, because the packaged app has no terminal to exit
    into and needs to show this in the browser instead."""
    try:
        riot.api_key()
        return None
    except riot.RiotError as problem:
        return str(problem)


if __name__ == "__main__":
    problem = check_key()
    if problem:
        sys.exit(f"{problem}\n"
                 "It stays in this process and is never sent to the browser.")
    serve_forever()
