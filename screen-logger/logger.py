"""Round-by-round TFT logger that reads the screen instead of the game.

  python logger.py           log games until you press Ctrl-C
  python logger.py --test    one read, printed - use this to check calibration

This replaces the Overwolf logger, which is a dead end: loading an unpacked
Overwolf app needs developer whitelisting, and Overwolf only whitelists apps
that are public, monetized and windowed. A personal headless logger fails all
three. Reading your own screen needs nobody's permission.

How it works: every second it crops four small boxes out of a screenshot (the
boxes you drew in calibrate.py), OCRs each one as digits, and appends an event
whenever a value actually changes. Between changes it writes nothing, so a
40-minute game is a few hundred events, not thousands of duplicates.

Output matches the Overwolf logger's envelope exactly:

    {"events": [{"kind": "info", "t": <ms>, "data": {"gold": 43, ...}}, ...]}

which is why peek_live.py, and anything built on it, works unchanged - it
already looks for gold under the "info:gold" path.

WHAT THIS CANNOT SEE, and Overwolf could: shop contents, your bench, unit
positions, opponent boards. Those aren't fixed single numbers on screen, so
OCR can't reach them. What's left - the gold curve, level timing, health
trajectory, when you win-streaked - is the econ analysis, which was the point.

Riot policy note: this only records. It shows nothing during the game and
gives no real-time advice, which is the line Riot's third-party rules draw.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import mss
import numpy as np
import pytesseract

HERE = Path(__file__).parent
REGIONS_FILE = HERE / "regions.json"
OUT_DIR = HERE / "logs"

POLL_SECONDS = 1.0      # once a second is plenty; gold changes at most that fast
IDLE_POLLS_TO_END = 20  # ~20s of unreadable screen means the game is over

# OCR noise is the main failure mode, and it's mostly implausible values -
# a misread "8" as "88", or stray pixels becoming a digit. Range checks throw
# those away for free. A value outside these bounds is treated as "no read".
BOUNDS = {
    "gold":   (0, 200),   # gold can climb past 100 on a long win streak
    "level":  (1, 11),
    "health": (0, 100),
}
STAGE_PATTERN = re.compile(r"^([1-7])-([1-7])$")


def find_tesseract():
    """Point pytesseract at the Windows binary if it isn't on PATH.

    The Windows installer doesn't add itself to PATH by default, which is the
    single most common reason this script fails on a fresh machine.
    """
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            pytesseract.pytesseract.tesseract_cmd = path
            return
    # Not found in the usual places - assume PATH, and let the first OCR call
    # produce the real error if it isn't there.


def ocr_digits(image, whitelist="0123456789"):
    """OCR one small crop, tuned for a few large digits on a dark HUD."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Tesseract wants text roughly 30px tall; HUD numbers are smaller than
    # that, so upscale before thresholding rather than after (interpolating
    # a clean binary image reintroduces the soft edges we just removed).
    gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

    # TFT draws light text on dark panels; Tesseract is trained on dark text
    # on light paper. THRESH_BINARY_INV flips it, and OTSU picks the cutoff
    # per-crop so it survives different backgrounds behind the HUD.
    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # psm 7 = "this image is a single line of text", which is exactly true
    # here and stops Tesseract hunting for page layout that doesn't exist.
    config = f"--psm 7 -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(binary, config=config).strip()


def parse_int(text, field):
    """Return an int if the OCR text is plausible for this field, else None."""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    value = int(digits)
    low, high = BOUNDS[field]
    return value if low <= value <= high else None


def parse_stage(text):
    """Return 'stage-round' if it looks like one, else None."""
    cleaned = re.sub(r"[^\d-]", "", text)
    return cleaned if STAGE_PATTERN.match(cleaned) else None


def read_state(sct, regions):
    """One pass over every calibrated region. Missing/implausible -> absent."""
    state = {}
    for name, box in regions.items():
        raw = np.array(sct.grab(box))[:, :, :3]
        if name == "stage":
            value = parse_stage(ocr_digits(raw, whitelist="0123456789-"))
        else:
            value = parse_int(ocr_digits(raw), name)
        if value is not None:
            state[name] = value
    return state


class MatchLog:
    """Accumulates one game's events and keeps its file on disk current.

    Rewriting the whole file on every change is cheap at this scale (a few
    hundred small events) and means a crash mid-game costs nothing.
    """

    def __init__(self, out_dir):
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / f"tft_{stamp}.json"
        self.events = []

    def add(self, state):
        self.events.append({
            "kind": "info",
            "t": int(time.time() * 1000),
            "data": dict(state),
        })
        self.path.write_text(json.dumps({"events": self.events}, indent=1))

    def summary(self):
        stages = [e["data"].get("stage") for e in self.events if e["data"].get("stage")]
        golds = [e["data"].get("gold") for e in self.events if e["data"].get("gold") is not None]
        parts = [f"{len(self.events)} events -> {self.path.name}"]
        if stages:
            parts.append(f"reached {stages[-1]}")
        if golds:
            parts.append(f"peak {max(golds)}g")
        return ", ".join(parts)


def run_test(regions):
    """Read once and print what each region produced, plus saved crops.

    Calibration is the step most likely to be subtly wrong (a box one pixel
    into a neighbouring icon is enough), so make it inspectable rather than
    asking anyone to trust it.
    """
    debug_dir = HERE / "debug-crops"
    debug_dir.mkdir(exist_ok=True)

    with mss.mss() as sct:
        print(f"{'field':<8} {'raw OCR':<14} parsed")
        print("-" * 36)
        for name, box in regions.items():
            raw = np.array(sct.grab(box))[:, :, :3]
            cv2.imwrite(str(debug_dir / f"{name}.png"), raw)
            text = ocr_digits(raw, "0123456789-" if name == "stage" else "0123456789")
            parsed = parse_stage(text) if name == "stage" else parse_int(text, name)
            flag = "" if parsed is not None else "   <-- no reading"
            print(f"{name:<8} {text!r:<14} {parsed}{flag}")

    print(f"\nCrops saved to {debug_dir}/ - open them and check each one shows "
          f"the number cleanly,\nwith no neighbouring icons. If a field reads "
          f"wrong, rerun: python calibrate.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true",
                        help="read once and print the result, then exit")
    args = parser.parse_args()

    find_tesseract()

    if not REGIONS_FILE.exists():
        sys.exit(f"No {REGIONS_FILE.name} yet. Run: python calibrate.py")
    regions = json.loads(REGIONS_FILE.read_text())

    if args.test:
        run_test(regions)
        return

    print(f"Watching {len(regions)} regions every {POLL_SECONDS}s. Ctrl-C to stop.")
    print(f"Logs -> {OUT_DIR}/\n")

    match = None
    last_state = {}
    idle_polls = 0

    try:
        with mss.mss() as sct:
            while True:
                state = read_state(sct, regions)

                if state:
                    idle_polls = 0
                    if match is None:
                        match = MatchLog(OUT_DIR)
                        print(f"Game started -> {match.path.name}")
                    if state != last_state:
                        match.add(state)
                        last_state = state
                        line = "  ".join(f"{k}={v}" for k, v in sorted(state.items()))
                        print(f"  {line}")
                else:
                    idle_polls += 1
                    # Nothing readable for a while: either the game ended or
                    # you alt-tabbed. Close the file either way - a new one
                    # opens as soon as readings come back.
                    if match is not None and idle_polls >= IDLE_POLLS_TO_END:
                        print(f"Game ended - {match.summary()}\n")
                        match, last_state = None, {}

                time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        if match is not None:
            print(f"\nStopped - {match.summary()}")
        else:
            print("\nStopped.")


if __name__ == "__main__":
    main()
