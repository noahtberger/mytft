/* TFT Round Logger - headless Overwolf background app.
 *
 * Records every game event and info update Overwolf's TFT provider emits,
 * raw and untouched, into JSON part-files on disk. No overlay, no UI,
 * nothing shown in game - all analysis happens later, in Python, on the
 * saved files. That split is deliberate twice over:
 *
 *   1. Riot's third-party policy forbids advice that reacts to live game
 *      state. Collecting data for post-game analysis is the allowed side
 *      of the line, so this app has no way to display anything.
 *   2. We don't yet know the exact shape of every payload. Logging raw
 *      means a wrong guess costs nothing - Python can re-interpret old
 *      logs forever; a logger that only kept what it understood could not.
 */

// ====== SETTINGS =========================================================
// Where log files go. CREATE THIS FOLDER YOURSELF once. To get files to
// the Mac automatically, point this inside a cloud-synced folder, e.g.:
//   "C:\\Users\\<you>\\OneDrive\\tft-logs"
// (Backslashes must be doubled in this string.)
const LOG_DIR = "C:\\tft-logs";

const LEAGUE_CLASS_ID = 5426;   // Overwolf's id for the League client; TFT runs inside it
const FLUSH_EVERY = 400;        // events per part-file (each part is written once, never rewritten)
const FLUSH_EVERY_MS = 10000;   // also flush at least this often, so a crash loses <=10s

// Every feature the TFT provider offers. We subscribe to all of them and
// decide what matters later, in Python, where changing our mind is cheap.
const FEATURES = ["gep_internal", "me", "match_info", "roster", "store",
                  "board", "bench", "carousel", "augments", "match_stats",
                  "game_info"];
// =========================================================================

let buffer = [];      // events waiting to be written
let seq = 0;          // global order, survives part-file boundaries
let stamp = null;     // filename timestamp for the current game session
let partNo = 0;

function nowStamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").replace("T", "_").slice(0, 19);
}

function record(kind, data) {
  buffer.push({ seq: seq++, t: Date.now(), kind: kind, data: data });
  if (buffer.length >= FLUSH_EVERY) flush("size");
}

function flush(reason) {
  if (buffer.length === 0 || stamp === null) return;
  const chunk = buffer;   // swap synchronously so events during the async write aren't lost
  buffer = [];
  const part = partNo++;
  const path = LOG_DIR + "\\tft_" + stamp + "-part" + String(part).padStart(3, "0") + ".json";
  const body = JSON.stringify({ stamp: stamp, part: part, reason: reason, events: chunk });
  overwolf.io.writeFileContents(path, body, "UTF8", true, function (res) {
    if (!res || res.success !== true) {
      console.error("write failed, keeping events in memory:", path, res);
      // Put the chunk back for the next flush. The part number is simply
      // skipped - gaps are fine, Python orders by seq, not by filename.
      buffer = chunk.concat(buffer);
    }
  });
}

// Close out the current match's files and start a fresh set.
function rotate(reason) {
  flush(reason);
  stamp = nowStamp();
  partNo = 0;
  record("rotated", { reason: reason });
}

// --- game events wiring ---------------------------------------------------

function registerFeatures(attempt) {
  overwolf.games.events.setRequiredFeatures(FEATURES, function (res) {
    if (res && res.success) {
      record("features_registered", res);
    } else {
      // The events provider often isn't ready in the first seconds after
      // the game launches - retrying is the officially recommended pattern.
      record("features_retry", { attempt: attempt, res: res });
      if (attempt < 60) setTimeout(function () { registerFeatures(attempt + 1); }, 3000);
    }
  });
}

overwolf.games.events.onNewEvents.addListener(function (e) {
  record("event", e);
  (e.events || []).forEach(function (ev) {
    // One League client session can hold several TFT matches - split files
    // per match so one log file == one game.
    if (ev.name === "match_start") rotate("match_start");
    if (ev.name === "match_end") flush("match_end");
  });
});

overwolf.games.events.onInfoUpdates2.addListener(function (i) {
  record("info", i);
});

overwolf.games.events.onError.addListener(function (err) {
  record("provider_error", err);
});

function isLeague(gameInfo) {
  return gameInfo &&
    (gameInfo.classId === LEAGUE_CLASS_ID ||
     Math.floor((gameInfo.id || 0) / 10) === LEAGUE_CLASS_ID);
}

function onGame(gameInfo) {
  if (!isLeague(gameInfo)) return;
  if (gameInfo.isRunning && stamp === null) {
    rotate("game_launched");
    record("game_info", gameInfo);
    registerFeatures(0);
  } else if (!gameInfo.isRunning && stamp !== null) {
    record("game_closed", {});
    flush("game_closed");
    stamp = null;
  }
}

overwolf.games.onGameInfoUpdated.addListener(function (u) {
  if (u && u.gameInfo) onGame(u.gameInfo);
});

// The app may start while a game is already running (launch_events does
// exactly that), so check once at startup too.
overwolf.games.getRunningGameInfo(function (info) {
  if (info) onGame(info);
});

setInterval(function () { flush("timer"); }, FLUSH_EVERY_MS);

// Smoke test: prove LOG_DIR exists and writing works the moment the app
// loads - so a wrong path is discovered immediately, not mid-game.
overwolf.io.writeFileContents(
  LOG_DIR + "\\logger-started-" + nowStamp() + ".json",
  JSON.stringify({ ok: true, note: "TFT Round Logger loaded. Safe to delete." }),
  "UTF8", true,
  function (res) {
    if (!res || res.success !== true) {
      console.error("SMOKE TEST FAILED - cannot write to " + LOG_DIR +
                    ". Create the folder, or fix LOG_DIR at the top of background.js.", res);
    } else {
      console.log("TFT Round Logger ready. Writing to " + LOG_DIR);
    }
  }
);
