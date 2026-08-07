# TFT Round Logger — Windows install

A headless Overwolf app that records round-by-round TFT data (gold, level,
boards, shop, rounds) to JSON files while you play. It shows **nothing** in
game — Riot's third-party policy forbids real-time adaptive advice, so this
app only collects; all analysis happens afterwards in the Python tracker.

## One-time setup (on the Windows laptop)

1. **Install Overwolf** from https://www.overwolf.com and create an account.

2. **Get developer access** — sign up at https://dev.overwolf.com with the
   same account. This is what unlocks loading your own unpacked app in the
   client.

3. **Copy this folder** (`overwolf-logger/`) onto the Windows laptop.
   Anywhere is fine, e.g. `C:\Users\<you>\overwolf-logger`.

4. **Pick where logs go.** Default is `C:\tft-logs` — create that folder.
   Better: use a cloud-synced folder so files reach the Mac by themselves,
   e.g. create `OneDrive\tft-logs` and edit `LOG_DIR` at the top of
   `background.js` to match (double the backslashes:
   `"C:\\Users\\<you>\\OneDrive\\tft-logs"`).

5. **Load the app**: Overwolf tray icon → Settings → About →
   **Development options** → **Load unpacked extension** → select the
   `overwolf-logger` folder. (If that button is greyed out, make sure the
   client is logged in with the account from step 2.)

6. **Check the smoke test**: the moment the app loads it writes
   `logger-started-<time>.json` into your log folder. If that file appears,
   everything on the file side works. If not, see Troubleshooting.

## Playing

Just play. The app auto-starts when the League client launches (it targets
the client, which is what TFT runs inside) and writes files named:

    tft_2026-08-06_19-45-12-part000.json
    tft_2026-08-06_19-45-12-part001.json
    ...

One timestamp per match (files rotate on `match_start`), several small
part-files each. Every event is stored raw with a timestamp and a sequence
number — the Python side reassembles them.

## Getting files to the Mac

Whatever is easiest:
- cloud-synced folder (recommended, step 4) — then copy or symlink into
  `~/tft-tracker/live/` on the Mac, or
- copy the files over manually into `~/tft-tracker/live/`.

Then on the Mac:

    cd ~/tft-tracker
    .venv/bin/python peek_live.py

That prints an inventory of what the logs actually contain. After your
first real game, that inventory is what we build the econ analysis from.

## Troubleshooting

- **No smoke-test file**: LOG_DIR doesn't exist or isn't writable. Create
  the folder, or fix the path at the top of `background.js` (remember
  doubled backslashes), then remove + re-load the unpacked app.
- **Smoke test works but no tft_ files during a game**: open the app's
  console — Settings → Development options → click *TFT Round Logger* →
  inspect *background* — and look for `features_retry` or errors. The TFT
  events provider sometimes breaks for a few days right after a big patch;
  Overwolf tracks TFT's event health separately under game id 21570.
- **Windows Defender / UAC prompt on first write**: allow it once
  (`triggerUacIfRequired` is set for exactly this).

## Rules note

Keep this app display-free. Logging your own game for later analysis is the
allowed side of Riot's policy; surfacing anything from it *during* the game
is not. If you ever distribute the tracker, it needs Riot's third-party app
approval and a "not endorsed by Riot Games" disclaimer.
