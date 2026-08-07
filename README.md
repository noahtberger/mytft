# tft-tracker

Personal TFT performance tracker for `orobas#tomo` (NA). All analysis runs
here on the Mac, in Python, over end-of-game data from Riot's public API.

(`overwolf-logger/` was an attempt at live round-by-round capture; retired
Aug 2026 — Overwolf only whitelists public, ad-monetized store apps, so a
private headless logger can't be loaded. There is currently no live data
source; everything below works from saved match files.)

**Sets:** every script reports on the newest set found in `matches/`, so
when a new set launches nothing needs editing — fetch and play. To look
back at an older set: `TFT_SET=17 .venv/bin/python stats.py`

## Daily use

```bash
export RIOT_API_KEY='RGAPI-...'        # dev key, expires every 24h:
                                       # https://developer.riotgames.com

.venv/bin/python fetch_matches.py      # pull new ranked matches -> matches/
.venv/bin/python stats.py              # full performance report
.venv/bin/python ask.py                # pre-game companion (interactive)
.venv/bin/python ask.py best           # proven strengths
.venv/bin/python ask.py traps          # proven leaks
.venv/bin/python ask.py stargazer boar # your record with any line
.venv/bin/python contested.py          # alone-vs-shared record per line
.venv/bin/python coverage.py           # what you've tested enough to judge
.venv/bin/python playstyle.py          # how you play, vs your own lobbies
```

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
| `contested.py`     | placement when a line/carry is contested vs alone | – |
| `coverage.py`      | which lines have the games to be judged, and which are blank | – |
| `scout.py`         | what other players build, from public match history | API key |
| `playstyle.py`     | how you play vs your lobbies; set-independent | – |
| `build_site.py`    | renders the whole analysis to `site/index.html` | – |
| `serve.py`         | local web app: enter a Riot ID, get their report | API key |
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
- **Compare with-vs-without**, not line-vs-overall-average (the average
  already contains the line).
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
