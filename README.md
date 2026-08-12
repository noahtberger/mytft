# MyTFT

A TFT tracker that measures **you**, not the meta. Other sites tell you
what the world is playing; this one tells you how you play, judged against
the seven other people in your own lobbies — and it refuses to claim
anything its sample size can't back up.

Built for `orobas#tomo` (NA) and friends, but any Riot ID works. All
analysis runs locally in Python over end-of-game data from Riot's public
API. (The repo folder is still `tft-tracker`.)

(`overwolf-logger/` was an attempt at live round-by-round capture; retired
Aug 2026 — Overwolf only whitelists public, ad-monetized store apps, so a
private headless logger can't be loaded. There is currently no live data
source; everything below works from saved match files.)

**Sets:** every script reports on the newest set found in `matches/`, so
when a new set launches nothing needs editing — fetch and play. To look
back at an older set: `TFT_SET=17 .venv/bin/python stats.py`

## Where this is right now (12 Aug 2026)

Set 18 lands **26 August**, which is the deadline that matters: comp data
resets to zero for everyone, and playstyle is the only thing that carries
over. Day one needs exactly one command, `python fetch_names.py`.

Working and shipped: the pre-game and post-game views, the "What wins you
games" card (your own record per plan, ranked by rate of firsts rather than
average placement), "How you play" (never blank — it states what you do and
only marks a habit when it clears the bar), the comp model with Fast 8, a
SQLite index over every board, and rank recording.

Known-open, in rough priority order:

- **The dev key expires every 24h.** Nothing live works without it, and
  every day it lapses is rank history that Set 18 will never have.
  A personal key was applied for on 8 Aug — chase it.
- **The League client test.** `curl -k https://127.0.0.1:2999/liveclientdata/playerlist`
  while actually in a TFT game. If it returns the eight Riot IDs, auto-scout
  is possible and the lobby box stops being manual. Untested, and it is the
  last unknown blocking a feature that was asked for.
- **Item names** are not in `names.json`; `items.pretty()` un-camel-cases
  raw API ids. Data Dragon has them — a small `fetch_names.py` addition.
- **Items carry no measurable signal yet** at 4,102 boards. That is a
  finding, not a bug: 2 of 33 clear the ordinary bar and chance predicts
  1.7, and zero survive correction. More boards, not more statistics.
- **`postgame.SOUP_COST`** duplicates `comps.FAST9_COST` without being
  linked to it.

## Running it on another computer

The repo lives at `github.com/noahtberger/mytft` (private). To set it up
on a second machine — Windows included, nothing here is Mac-specific:

```bash
git clone https://github.com/noahtberger/mytft.git
cd mytft
python -m pip install requests
```

That is the whole install. `requests` is the only thing the app needs —
everything else (reports, web app, SQLite index, every chart) is standard
library, so a fresh clone runs `python stats.py` with nothing installed at
all. `requirements.txt` exists but also pulls pywebview and pyinstaller,
which are only for building the double-click app; `pip install -r
requirements.txt` is the slow way to get here unless you want that.

A virtualenv is optional and on Windows it has caused more trouble than it
solved. If you use one, `python -m venv .venv` must be its **own command** —
pasting it and the install on one line makes venv's argument parser report
`-r requirements.txt is an unrecognized argument`, which reads like a pip
error and isn't one. Then use `.venv\Scripts\python -m pip install requests`
rather than `.venv\Scripts\pip`, because PowerShell refuses to run an
executable by relative path without a `.\` prefix. Whatever you pick, use
the **same** python to install and to run, or you get
`ModuleNotFoundError: No module named 'requests'` from a machine that
definitely has requests.

Then put that day's key in `.riot_key`. On Windows, do not use Notepad —
it saves `.riot_key.txt` unless you pick "All Files". In PowerShell:

```powershell
Set-Content -Path .riot_key -Value "RGAPI-..." -Encoding ascii
```

`-Encoding ascii` is load-bearing: PowerShell otherwise writes a UTF-8
byte-order mark, and those three invisible bytes end up glued to the front
of the key. `.strip()` will not remove them because they are not
whitespace, so Riot rejects a key that looks perfect on screen. Check it
with `python -c "import riot; print(riot.api_key())"` — no network needed,
and anything before the `R` is the BOM.

Your `matches/`, `me.json`, `names.json` and `rank_history.jsonl` come with
the clone, so every report works immediately with no downloading. Two
things do **not** clone, both on purpose: `pool/` (other players' games —
re-downloadable, press Seed) and `boards.db` (the SQLite index — run
`python index.py`, about a second).

**Only run the app on one machine at a time.** `rank_history.jsonl` is
committed because it cannot be regenerated, so two machines recording
independently will both append and collide in git. Whichever machine you
play on should be the one that records.

Keeping two machines in sync is `git pull` before you start and
`git add matches && git commit && git push` after you fetch new games —
`matches/` is committed precisely so it survives and travels.

## Daily use

```bash
echo 'RGAPI-...' > .riot_key           # today's dev key, expires every 24h:
                                       # https://developer.riotgames.com
                                       # (gitignored; export RIOT_API_KEY
                                       #  still works and wins if both exist)

.venv/bin/python fetch_matches.py      # pull your new ranked matches -> matches/
.venv/bin/python fetch_matches.py Name#NA1   # somebody else's -> pool/
.venv/bin/python stats.py              # full performance report
.venv/bin/python ask.py                # pre-game companion (interactive)
.venv/bin/python ask.py best           # proven strengths
.venv/bin/python ask.py traps          # proven leaks
.venv/bin/python ask.py stargazer boar # your record with any comp
.venv/bin/python contested.py          # alone-vs-shared record per comp
.venv/bin/python coverage.py           # what you've tested enough to judge
.venv/bin/python playstyle.py          # how you play, vs your own lobbies
.venv/bin/python pregame.py            # the one screen to read before you queue
.venv/bin/python postgame.py           # your board vs the boards that beat you
```

`pregame.py` folds the coaching into one screen: what wins in your own
lobbies (all 8 boards of every game, so it reaches verdicts your personal
per-comp record can't), where your build rates disagree with that meta,
what your own record has actually proven, and your current-era playstyle
sentence. Add `name#tag ...` to scout people at the same time.

`postgame.py` is the debrief: your final board against the boards that
beat you in that same lobby — same patch, same eight players, so the
comparison is naturally controlled. Per game it states facts only; its
PATTERN section averages the comparison over your whole current era and
only speaks where a gap clears its error bar.

`playstyle.py` is the one that survives a new set. Comps and augments reset
to zero every September; "expensive board", "rarely rerolls", "splashes
instead of committing" mean the same thing in Set 18 as in Set 17, so a
profile carries over on day one when nobody has comp data yet.

It reports on your **current era, not the whole set**. Playstyle is supposed
to change — a new set is exactly when it should — so it scans for the biggest
shift and profiles you from there. In Set 17 that split lands at game 83:
level-5 rerolls before it, fast 8 after. Averaged together those describe
neither player, and the blended numbers understated the real gaps by half.

`coverage.py` is the one to run first in a new set: it answers "which of
these numbers can I trust yet", which early on is almost none of them.

The app — type any Riot ID, get that player's report:

```bash
export RIOT_API_KEY='RGAPI-...'
.venv/bin/python serve.py         # then open http://127.0.0.1:8000
```

### The double-clickable version

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller mytft.spec     # -> dist/MyTFT.app
```

`MyTFT.app` runs in its own window with its own Dock icon — no browser,
no address bar — and closing the window quits the server too. (It falls
back to opening your browser if pywebview isn't installed.) The icon is
built from `icon/icon.svg` by rendering it to PNG and running
`iconutil`; `icon/MyTFT.icns` is committed so a rebuild needs nothing.

It starts the server — no terminal, no
commands. Build it on the machine you want it for: a Mac bundle won't run
on Windows, so the laptop needs its own `dist/MyTFT.exe` from the same
command. Roughly 24 MB, and it contains Python itself, so the machine
running it needs nothing installed.

The bundle carries the **code, not the games**. Matches, `names.json` and
`.riot_key` stay in this project folder — the things you keep, sync with
git and back up — and the app finds that folder and works inside it. Set
`MYTFT_HOME` if you keep it somewhere unusual. If it can't find the folder
or the key, it opens a browser page saying so rather than failing
silently, since there's no terminal to print to.

The app serves three views per player, linked by tabs at the top:

- **Pre-game** — your leaks as blunt tips with the numbers under them, a
  **lobby box** (type the names from the loading screen, one per line, and
  get each player's comps, carries and forced lines — instant for anyone
  already in your saved games), the meta measured in your own lobbies, and
  what your record has proven.
- **Post-game** — the debrief of any recent game: all eight boards with
  player names, where the margin lived, and blunt advice backed by the
  era-wide pattern. The refresh button (↻) fetches whatever
  you just played (a finished game reaches Riot's history within a minute
  or two), so the loop is: play, alt-tab, click, read.
- **Stats** — the full report page.

While the app is open it checks every few minutes whether you have
finished a game, and a banner offers the debrief when you have — one
request per check, so it costs nothing against the rate limit. It watches
only the owner of this copy, not everyone ever looked up, and it links
rather than redirecting so nothing yanks the page while you are reading.

**Switch player** in the header takes you back to the search box, which
lists everyone looked up recently so you can flip between yourself and
friends without retyping Riot IDs. (Nobody logs in — any Riot ID works,
which is why it isn't called a logout.)

Your own new games land in `matches/` (the committed copy); everyone
else's land in `pool/`.

### Using it from another device on your wifi

```bash
touch lan-access        # then restart the app
```

The server normally binds `127.0.0.1`, which means this machine only. With
that file present it binds every interface instead, and another device on
the same network opens `http://<your-mac-ip>:8000` — the address is
printed on startup. Delete the file to turn it off.

Worth knowing before you leave it on: **there is no login**, so anything
on that network can use the app and spend your API key. Fine on your own
wifi; not something to run on café or campus networks. Your Mac also has
to be awake with the app running.

**Why this needs a server.** A static page can't fetch anyone's history:
Riot's API sends no CORS headers so browser JavaScript can't call it at all,
your key would have to travel to the browser to try, and the published
claude.ai copy runs under a CSP that blocks every external host. So the key
stays in the `serve.py` process, which binds to `127.0.0.1` — nothing off
this machine can reach it. A first lookup takes about a minute (100 requests
per 2 minutes); after that the player is cached and it's instant.

The page on its own:

```bash
.venv/bin/python build_site.py    # writes site/index.html
open site/index.html              # plain HTML, no server, works offline

# any player, if their games are on disk:
TFT_PUUID=<puuid> TFT_OUT=out TFT_NAME='name#tag' .venv/bin/python build_site.py
```

`site/index.html` is one self-contained file — styles, charts and hover
behaviour are all inlined, so you can mail it to someone and it still
works. Charts are SVG built in Python, so they render with JavaScript off.
`site/artifact.html` is the same page as a fragment, for a host that
supplies its own `<head>`. Rebuild after every fetch; the numbers come from
`tftdata.coverage_data`, the same function `coverage.py` prints from, so the
page and the terminal can't drift apart.

Scouting someone before you queue (needs the API key):

```bash
.venv/bin/python scout.py name#tag ...   # what these players actually build
.venv/bin/python scout.py <puuid> --offline   # from saved matches, no network
```

Occasional:

```bash
.venv/bin/python fetch_names.py        # refresh ID->name tables (new set/patch)
.venv/bin/python peek_live.py          # inventory Overwolf logs in live/
```

## Files

| file | role | needs |
|---|---|---|
| `fetch_matches.py` | download ranked matches, one JSON per game | API key |
| `fetch_names.py`   | build `names.json` from Data Dragon + CDragon | network only |
| `tftdata.py`       | shared lib: loading, naming, lobby comparisons | – |
| `me.json`          | the puuid reports default to (see "More than one player") | |
| `stats.py`         | full report over `matches/` | – |
| `ask.py`           | pre-game companion, plain-English queries | – |
| `contested.py`     | placement when a comp/carry is contested vs alone | – |
| `coverage.py`      | which comps have the games to be judged, and which are blank | – |
| `scout.py`         | what other players build, from public match history | API key |
| `playstyle.py`     | how you play vs your lobbies; set-independent | – |
| `pregame.py`       | pre-queue screen: lobby meta, your leaks, proven reads | – |
| `postgame.py`      | debrief: your board vs the boards that beat you | – |
| `build_site.py`    | renders the whole analysis to `site/index.html` | – |
| `theme.py`         | shared stylesheet, tooltip script, text helpers for every page | – |
| `postgame_page.py` | the post-game debrief as a page in the app | – |
| `pregame_page.py`  | the pre-game screen (leaks, lobby box, meta) as a page | – |
| `serve.py`         | local web app: stats + post-game views per player | API key |
| `riot.py`          | the API client: key, HTTP, match downloading | API key |
| `peek_live.py`     | inventory of Overwolf live logs in `live/` | – |
| `matches/`         | **your** raw match JSON, named by match ID (committed) | |
| `pool/`            | other players' matches, downloaded (gitignored) | |
| `live/`            | round-by-round logs copied from Windows | |
| `me.json`          | my PUUID | |
| `names.json`       | internal ID -> in-game name tables | |

## More than one player

`tftdata.load_games(puuid)` takes a player. With no argument it uses the
puuid in `me.json`, so every script here keeps working unchanged — but the
analysis itself is not tied to one person:

```python
import tftdata as tft
games, level_par = tft.load_games(some_puuid)     # anyone on disk
data = tft.coverage_data(games, games[0]["_set"])
```

This costs nothing extra to run, because **a match file holds all 8 boards**.
Games saved for you are already a full record of the ~1200 other players who
were in your lobbies, and `matches/` is keyed by Riot's global match ID, so
several players' histories mix into one folder with no collisions — a game
you both played is one file, not two. `load_games` skips files the requested
player isn't in, before computing `level_par`, so nobody else's lobbies can
shift the yardstick your numbers are measured against.

`serve.py` and `build_site.py` are fully multi-player — the web app resolves
any Riot ID, backfills what's missing, and renders that person's page.

What's still single-player: `fetch_matches.py` hardcodes the Riot ID to
download, and the terminal reports (`stats.py`, `coverage.py`, `playstyle.py`,
`contested.py`) run at import with no argument parsing, so from the command
line they always report on `me.json`. The analysis underneath them already
takes a puuid.

**Two folders, one logical pool.** `matches/` is exclusively your games —
written only by `fetch_matches.py`, and committed, because Riot deletes old
history and nobody else keeps it for you. `pool/` is everything downloaded
for other players by `serve.py` and `scout.py`; it's gitignored and
re-downloadable.

Readers take the **union** (`tftdata.match_files()`, deduped by match id,
yours first), so the shared pool already contains your games without a second
copy existing anywhere. That's what makes lookups get cheaper over time: a
match file is one player's game and seven other people's game at once, so
every backfill fills in strangers you'll meet later. Right now that's 196
files of yours plus 58 in the pool — 2,000 boards across ~1,600 players.

**Rate limits are the real ceiling, not the code.** A personal key carries
the same limits as a dev key (100 requests / 2 min), and backfilling one new
player's 200 games costs ~202 requests — about 4 minutes of queue each, shared
across everyone. Caching doesn't rescue this: of the 1237 distinct players in
these 196 matches, only 2 appear in 30+ games, so lobbies essentially never
repeat and every new player is a genuine cold start. Backfill the most recent
~50 games first (~1 min, already past the 30-game bar) and deepen in the
background.

## Rules learned the hard way

- **~30 games minimum** before a number is actionable; under that it's a
  hint. Margin of error on avg placement ≈ `4.5/sqrt(n)` places.
- **Compare with-vs-without**, not comp-vs-overall-average (the average
  already contains the comp).
- **Normalize against the lobby** (all 8 players are in every match file);
  that self-corrects for rank and patch.
- **gold_left is not an econ signal** — it inverts (winners hold gold).
- **API names lie**: `TFT17_DRX` is N.O.V.A., `_Wolf` is The Boar. Never
  show a raw ID suffix as if it were an in-game name.
- **Patches swing hard** (1.8 avg places between 16.10 and 16.12) — flag
  windows that span patches.
- **Break sort ties on the name.** These tables are built by iterating sets
  of strings, and Python randomises string hashing per process, so two rows
  on the same average silently swap places — or swap which one makes a
  top-6 cut — between runs on identical data.
- **Never index a `defaultdict` you are about to count.** Reading
  `built_games[line]` for a line you never built inserts an empty entry, so
  "spread across N lines" quietly became the size of the whole set roster.
  `Counter` is safe (its `__missing__` returns 0 without inserting); `dict`
  with `.get()` is the fix for the rest.
- **"Active" is not "built".** A trait at `tier_current > 0` can be a 2-unit
  splash: Bastion was on 119 boards and truly built zero times. Any table
  that says "comp direction" has to use `committed_lines`, or say plainly
  that it is counting board presence.
- **Test every report at n=1.** A new set starts empty, and the sparse path
  is where the crashes live — an empty slice into `summarise`, a `max()` over
  no rows, a comparison group that covers every game so there is no
  "without".
- **Don't hardcode a conclusion into copy.** Sentences like "your
  distribution is flat" or "nothing has reached the bar" are claims about the
  data and have to be computed from it, or they turn into confident lies.
- **Riot policy**: pre-game/static advice ok, post-game analysis
  encouraged, real-time adaptive advice forbidden. No Legend/Augment
  win-rate display.
- **A personal key can't read live lobbies.** `spectator-tft-v5` returns
  403 on a dev key — verified Aug 7 2026, and it's a permission rather
  than "you're not in a game": `featured-games` 403s too (it takes no
  player and can't 404) while `tft/league` and `tft/summoner` on the same
  host and key return 200. `scout.py lobby` needs the production key.
- **401 and 403 are different.** 401 is a bad key; 403 is a good key
  Riot refused. Reporting the second as "key invalid" sends you off to
  generate a fresh key that fails identically.
