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
| `stats.py`         | full report over `matches/` | – |
| `ask.py`           | pre-game companion, plain-English queries | – |
| `contested.py`     | placement when a line/carry is contested vs alone | – |
| `peek_live.py`     | inventory of Overwolf live logs in `live/` | – |
| `matches/`         | raw match JSON, named by match ID | |
| `live/`            | round-by-round logs copied from Windows | |
| `me.json`          | my PUUID | |
| `names.json`       | internal ID -> in-game name tables | |

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
- **Riot policy**: pre-game/static advice ok, post-game analysis
  encouraged, real-time adaptive advice forbidden. No Legend/Augment
  win-rate display.
