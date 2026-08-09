"""The double-clickable version of MyTFT.

Build it with:  python -m PyInstaller mytft.spec
Then:           open dist/MyTFT.app        (Windows: dist/MyTFT.exe)

What this adds over `python serve.py` is only convenience: it starts the
same server, waits for it to answer, and opens your browser on it. No
terminal, no commands, no remembering the address.

Where the data lives
--------------------
The bundle carries the code, not the games. Match files, names.json and
your API key stay in the project folder, because they are the things you
keep, sync between machines with git, and back up. So the app finds that
folder and works inside it, rather than hiding a second copy somewhere in
Application Support where git would never see it.

It looks in $MYTFT_HOME first, then the usual clone locations. If it finds
nothing it says so in the browser instead of dying silently, which is the
one failure a windowless app must never do quietly.
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

CANDIDATES = ("tft-tracker", "mytft", "MyTFT")


def find_home():
    """The project folder holding matches/ and names.json."""
    named = os.environ.get("MYTFT_HOME")
    if named and (Path(named) / "names.json").exists():
        return Path(named)

    here = Path(__file__).resolve().parent
    roots = [here, here.parent, Path.home()]
    # A .app bundle sits several folders deep inside itself; the project is
    # near where the bundle was put, not where its executable ended up.
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parents[3])

    for root in roots:
        if (root / "names.json").exists() and (root / "serve.py").exists():
            return root
        for name in CANDIDATES:
            if (root / name / "names.json").exists():
                return root / name
    return None


def complain(message):
    """Show a problem in the browser - a windowless app has nowhere else."""
    import tempfile
    import html as html_mod
    page = f"""<!doctype html><meta charset="utf-8"><title>MyTFT</title>
<style>body{{background:#0B0E14;color:#E4E8F0;font:16px/1.6 -apple-system,
system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0;
padding:24px}}div{{max-width:34rem}}h1{{font-size:24px;margin:0 0 12px}}
code{{background:#1D2534;padding:2px 6px;border-radius:3px;font-size:14px}}
</style><div><h1>MyTFT can&rsquo;t start</h1>
<p>{html_mod.escape(message)}</p></div>"""
    path = Path(tempfile.mkdtemp(prefix="mytft-")) / "problem.html"
    path.write_text(page)
    webbrowser.open(path.as_uri())


def open_when_ready(url, timeout=20):
    """Open the browser once the server actually answers, so the first
    click never lands on a connection-refused page."""
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except urllib.error.HTTPError:
            break                      # answering, even if with an error
        except Exception:
            time.sleep(0.25)
    webbrowser.open(url)


def main():
    home = find_home()
    if home is None:
        complain("I couldn't find the MyTFT project folder - the one holding "
                 "names.json and your matches. Put this app beside it, or set "
                 "MYTFT_HOME to its path.")
        return 1
    os.chdir(home)

    sys.path.insert(0, str(home))
    import serve

    problem = serve.check_key()
    if problem:
        complain(problem)
        return 1

    url = f"http://{serve.HOST}:{serve.PORT}/"
    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    try:
        serve.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
