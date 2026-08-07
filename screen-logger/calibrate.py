"""One-time setup: tell the logger where the numbers live on YOUR screen.

  python calibrate.py

Why this exists: the logger reads gold/level/stage/health by cropping small
boxes out of a screenshot and running OCR on them. Those box positions depend
on your monitor resolution and TFT's UI scale, so they can't be hardcoded -
they have to be measured once, on your machine.

This grabs a screenshot, shows it to you, and asks you to drag a box around
each number in turn. Drag, press ENTER, repeat. Coordinates go to
regions.json, which logger.py reads on every run.

Run this while a TFT game is actually on screen (any point mid-game), so the
numbers are visible to draw boxes around. Stage 2-1 or later is ideal - by
then gold, level, stage and health are all populated with realistic values.
"""

import json
from pathlib import Path

import cv2
import mss
import numpy as np

HERE = Path(__file__).parent
REGIONS_FILE = HERE / "regions.json"

# The four numbers worth capturing, with a hint shown while you drag.
FIELDS = [
    ("gold",   "your GOLD (the number next to the coin icon)"),
    ("level",  "your LEVEL (next to the XP bar, bottom-left)"),
    ("stage",  "the STAGE-ROUND counter, e.g. 3-2 (top of screen)"),
    ("health", "your HEALTH (on your own portrait in the player list)"),
]

# Big monitors don't fit on screen while you're drawing boxes, so the preview
# is shrunk to fit and the coordinates scaled back up afterwards.
PREVIEW_MAX_WIDTH = 1600


def grab_screen():
    """Return (BGR screenshot of the primary monitor, monitor dict)."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]        # [0] is "all monitors glued together"
        shot = np.array(sct.grab(monitor))
        return shot[:, :, :3], monitor   # drop the alpha channel mss includes


def main():
    print(__doc__)
    input("Get a TFT game on screen, then come back here and press ENTER... ")

    screen, monitor = grab_screen()
    height, width = screen.shape[:2]
    print(f"\nCaptured {width}x{height} from monitor at "
          f"({monitor['left']}, {monitor['top']}).")

    # Scale the preview down if needed, remembering the factor so the boxes
    # you draw can be converted back to true screen pixels.
    scale = min(1.0, PREVIEW_MAX_WIDTH / width)
    preview = cv2.resize(screen, None, fx=scale, fy=scale) if scale < 1 else screen
    if scale < 1:
        print(f"Preview shown at {scale:.0%} to fit your display.")

    regions = {}
    for name, hint in FIELDS:
        print(f"\n--- {name.upper()} ---")
        print(f"Drag a tight box around {hint}, then press ENTER.")
        print("Include a little padding but no neighbouring icons or text.")
        print("Press C to skip this field.")

        box = cv2.selectROI(f"Select: {name}", preview, showCrosshair=False)
        cv2.destroyAllWindows()

        x, y, w, h = box
        if w == 0 or h == 0:
            print(f"  skipped {name}")
            continue

        # Back to true screen pixels, and into the {top,left,width,height}
        # shape that mss.grab() wants directly.
        regions[name] = {
            "left":   int(x / scale) + monitor["left"],
            "top":    int(y / scale) + monitor["top"],
            "width":  int(w / scale),
            "height": int(h / scale),
        }
        print(f"  {name}: {regions[name]}")

    if not regions:
        print("\nNothing selected - regions.json not written.")
        return

    REGIONS_FILE.write_text(json.dumps(regions, indent=2))
    print(f"\nSaved {len(regions)} regions to {REGIONS_FILE}")
    print("\nNow check the crops are readable before trusting them:")
    print("    python logger.py --test")


if __name__ == "__main__":
    main()
