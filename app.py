"""The double-clickable version of MyTFT.

Build it with:  python -m PyInstaller mytft.spec
Then:           open dist/MyTFT.app        (Windows: dist/MyTFT.exe)

It starts the same server and shows it in a real application window - its
own Dock icon, no browser, no address bar - using pywebview, which wraps
the system's web view. If pywebview isn't installed it falls back to
opening your browser, so the app still works either way.

Closing the window quits everything, including the server.

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


def wait_for_server(url, timeout=20):
    """Block until the server answers, so nothing opens on a dead port."""
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.HTTPError:
            return True                # answering, even if with an error
        except Exception:
            time.sleep(0.25)
    return False


def open_when_ready(url):
    """Fallback display: the user's browser."""
    wait_for_server(url)
    webbrowser.open(url)


def probe():
    """MYTFT_PROBE=1: load the page in a hidden window, print what
    happened, exit. The only way to see inside the frozen app's webview
    without eyes on the screen."""
    import webview
    result = {"loaded": False, "title": None}

    def on_loaded(window):
        result["loaded"] = True
        try:
            result["title"] = window.evaluate_js("""
                JSON.stringify({
                  title: document.title,
                  url: location.href,
                  bodyBg: getComputedStyle(document.body).backgroundColor,
                  sheets: document.styleSheets.length,
                  sheetRules: (function(){try{return document.styleSheets[0]
                      .cssRules.length}catch(e){return 'blocked: '+e}})(),
                  htmlLen: document.body.innerHTML.length,
                  centre: (function(){var el=document.elementFromPoint(
                      innerWidth/2, innerHeight/2);
                      return el ? el.tagName+'.'+el.className : 'nothing'})(),
                  text: (document.body.innerText || '').slice(0, 80)
                })""")
        except Exception as e:                        # noqa: BLE001
            result["title"] = f"js failed: {e}"
        dump()
        time.sleep(2)
        window.destroy()

    w = webview.create_window("probe", "http://127.0.0.1:8000/",
                              width=900, height=600)
    # stdout goes nowhere when Finder launches the app - write the result
    # somewhere readable too.
    import json as json_mod
    def dump():
        Path("/tmp/mytft_probe.json").write_text(
            json_mod.dumps(result, indent=2))
    w.events.loaded += lambda: on_loaded(w)

    def bail():
        time.sleep(8)
        try:
            w.destroy()
        except Exception:                             # noqa: BLE001
            pass
    threading.Thread(target=bail, daemon=True).start()
    webview.start()
    print(f"PROBE loaded={result['loaded']} title={result['title']!r}")


def main():
    if os.environ.get("MYTFT_PROBE"):
        probe()
        return 0
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

    # The window always browses loopback. serve.HOST is the BIND address -
    # with LAN access on it is 0.0.0.0, a valid place to listen but not a
    # valid place to browse: WebKit refuses it as a URL host and leaves the
    # window on about:blank (Python's urllib connects to it fine, which is
    # why every "is it up?" check passed while the window sat white).
    url = f"http://127.0.0.1:{serve.PORT}/"

    # If a MyTFT server is already answering - an earlier window, or one
    # started from a terminal - use it instead of fighting over the port.
    # Two instances binding 8000 was how windows ended up pointed at dead
    # servers: each new one killed the last out from under an open window.
    if not wait_for_server(url, timeout=1):
        # The server has to run in the background: a webview window owns
        # the main thread, and on a Mac the UI must BE the main thread.
        threading.Thread(target=serve.serve_forever, daemon=True).start()

    try:
        import webview
    except ImportError:
        # No webview available - fall back to the browser rather than
        # refusing to start. The app still works, it just isn't its own
        # window.
        open_when_ready(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    if not wait_for_server(url):
        complain("The server didn't start in time. Try again, and if it "
                 "keeps happening run serve.py from a terminal to see why.")
        return 1

    # background_color paints the native window itself - if the web layer
    # ever fails to composite, the window shows dark instead of white and
    # tells us which layer broke.
    window = webview.create_window("MyTFT", url, width=1180, height=880,
                                   min_size=(720, 600),
                                   background_color="#0B0E14")

    def heal():
        """If the window ever ends up on WebKit's can't-connect page - the
        server restarted underneath it - reload once the server answers,
        instead of sitting on a white screen until a human notices."""
        while True:
            time.sleep(5)
            try:
                title = window.evaluate_js("document.title") or ""
            except Exception:                         # noqa: BLE001
                continue                # window closing; the loop dies with it
            if "MyTFT" not in title and wait_for_server(url, timeout=3):
                try:
                    window.load_url(url)
                except Exception:                     # noqa: BLE001
                    pass

    threading.Thread(target=heal, daemon=True).start()
    webview.start()        # returns when the window is closed, which quits
    return 0


if __name__ == "__main__":
    sys.exit(main())
