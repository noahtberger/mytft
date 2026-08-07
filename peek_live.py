"""Inventory what the Overwolf logger actually captured.

  python peek_live.py            reads live/
  python peek_live.py <folder>   reads somewhere else

This is the my_match.json moment all over again: before building analysis on
the live logs, look at what's really in them. It walks every event in every
log file and reports which features fired, which keys exist, and example
values - so the econ analysis gets built on real payload shapes, not guesses.

Works no matter what the payloads turn out to look like, because it assumes
nothing: it just counts paths and shows samples.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

TRACKED = ("gold", "level", "round", "health", "xp", "stage")  # keys worth spotlighting
MAX_ROWS = 40


def unwrap(value):
    """Overwolf often double-encodes: values arrive as strings holding JSON
    (e.g. the string '{"gold":"43"}'). Unwrap until it stops being JSON."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[\"" or s.lstrip("-").replace(".", "", 1).isdigit():
            try:
                return unwrap(json.loads(s))
            except (json.JSONDecodeError, ValueError):
                return value
    return value


def leaves(obj, path=""):
    """Yield (dotted.path, scalar_value) for every leaf in a nested payload."""
    obj = unwrap(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from leaves(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for value in obj:
            yield from leaves(value, f"{path}[]")
    else:
        yield path, obj


def load_events(path):
    """Return the event list from one log file, tolerating shape variations."""
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return data.get("events", [])
    return data if isinstance(data, list) else []


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "live")
    files = sorted(folder.glob("*.json"))
    files = [f for f in files if not f.name.startswith("logger-started")]

    if not files:
        print(f"No log files in {folder}/ yet.")
        print("Play a TFT game on the Windows laptop with the logger running,")
        print(f"then copy the tft_*.json files into {folder}/ and rerun this.")
        return

    kinds = Counter()
    event_names = Counter()
    paths = Counter()
    first_example = {}
    last_example = {}
    by_match = defaultdict(list)   # stamp -> all events, ordered

    for path in files:
        events = load_events(path)
        stamp = path.name.split("-part")[0]
        by_match[stamp].extend(events)
        for ev in events:
            kind = ev.get("kind", "?")
            kinds[kind] += 1
            data = ev.get("data", {})
            if kind == "event":
                for sub in (data.get("events") or []) if isinstance(data, dict) else []:
                    event_names[sub.get("name", "?")] += 1
            for leaf_path, value in leaves(data):
                key = f"{kind}:{leaf_path}"
                paths[key] += 1
                first_example.setdefault(key, value)
                last_example[key] = value

    print(f"{len(files)} files, {sum(kinds.values())} events, "
          f"{len(by_match)} match(es)\n")

    print("EVENT KINDS")
    for kind, count in kinds.most_common():
        print(f"  {kind:<22} {count}")

    if event_names:
        print("\nGAME EVENT NAMES (these mark moments: rounds, combats, deaths)")
        for name, count in event_names.most_common():
            print(f"  {name:<28} {count}")

    print(f"\nDATA PATHS (top {MAX_ROWS} of {len(paths)}) - kind:path, count, "
          f"first seen -> last seen")
    for key, count in paths.most_common(MAX_ROWS):
        first, last = repr(first_example[key])[:34], repr(last_example[key])[:34]
        print(f"  {key:<52} {count:>6}  {first} -> {last}")

    # Spotlight the keys the econ analysis will live on.
    print("\nTRACKED KEYS (the ones the econ analysis needs)")
    for word in TRACKED:
        hits = [(k, c) for k, c in paths.items() if word in k.lower()]
        if hits:
            best = max(hits, key=lambda h: h[1])
            print(f"  {word:<8} FOUND  e.g. {best[0]}  ({best[1]} samples, "
                  f"latest {repr(last_example[best[0]])[:30]})")
        else:
            print(f"  {word:<8} not seen yet")

    # If gold is present, sketch its timeline for the newest match - the
    # first real glimpse of an econ curve.
    gold_paths = [k for k in paths if "gold" in k.lower() and k.startswith("info")]
    if gold_paths and by_match:
        newest = max(by_match)
        series = []
        for ev in by_match[newest]:
            for leaf_path, value in leaves(ev.get("data", {})):
                if f"{ev.get('kind')}:{leaf_path}" in gold_paths:
                    try:
                        series.append((ev.get("t", 0), float(value)))
                    except (TypeError, ValueError):
                        pass
        if series:
            print(f"\nGOLD TIMELINE for {newest} ({len(series)} samples)")
            t0 = series[0][0]
            step = max(1, len(series) // 12)
            for t, gold in series[::step]:
                mins = (t - t0) / 60000
                print(f"  {mins:5.1f} min   {int(gold):>3} gold  {'#' * int(gold // 4)}")


if __name__ == "__main__":
    main()
