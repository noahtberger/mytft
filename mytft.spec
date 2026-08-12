# PyInstaller recipe for the double-clickable MyTFT.
#
#   python -m PyInstaller mytft.spec
#
# Builds dist/MyTFT.app on a Mac and dist/MyTFT.exe on Windows - run it on
# the machine you want the app for, since a bundle is platform-specific.
#
# hiddenimports lists the modules serve.py reaches for at runtime rather
# than at the top of the file: PyInstaller finds imports by reading the
# source, so anything imported inside a function is invisible to it and
# has to be named here or the packaged app dies on first use.

import sys
from pathlib import Path

block_cipher = None

# A frozen app is platform-specific and so is this recipe. The two things
# that differ are the pywebview backend (PyInstaller cannot see it - the
# library picks one at runtime) and the macOS-only .app wrapper.
IS_MAC = sys.platform == "darwin"
BACKENDS = (["webview.platforms.cocoa"] if IS_MAC
            else ["webview.platforms.edgechromium", "webview.platforms.winforms"])

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=[],                      # data lives in the project folder, not here
    hiddenimports=[
        "serve", "build_site", "theme", "tftdata", "riot", "scout",
        "pregame", "postgame", "pregame_page", "postgame_page",
        "comps", "seed", "rank", "index", "items",
        "requests", "webview",
        # pywebview picks its backend at runtime, which PyInstaller's
        # source scan cannot see - without the right one the frozen app has
        # no backend and the window renders nothing. Cocoa on a Mac,
        # EdgeChromium (WebView2) on Windows with winforms as the fallback.
        *BACKENDS,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyInstaller"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MyTFT",
    debug=False,
    strip=False,
    upx=False,
    console=False,                 # no terminal window
    icon=("icon/MyTFT.icns" if IS_MAC else
          ("icon/MyTFT.ico" if Path("icon/MyTFT.ico").exists() else None)),
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="MyTFT",
)
# BUNDLE wraps the folder into MyTFT.app and only means anything on macOS.
# On Windows the deliverable is dist/MyTFT/MyTFT.exe from COLLECT above.
app = IS_MAC and BUNDLE(
    coll,
    name="MyTFT.app",
    icon="icon/MyTFT.icns",
    bundle_identifier="com.noahberger.mytft",
    info_plist={
        "CFBundleName": "MyTFT",
        "CFBundleDisplayName": "MyTFT",
        "LSBackgroundOnly": False,
        "NSHighResolutionCapable": True,
        # A bundled app gets App Transport Security by default, which can
        # refuse plain-http loads - including to 127.0.0.1, which is the
        # only thing this window ever loads. A bare python process never
        # enforces this, which is why the unfrozen app worked and the
        # bundle showed a white screen.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        # Without this, a bundled app is forced into the LIGHT appearance
        # regardless of the system setting - the media query resolves
        # light, and this app's light palette is near-white. The user saw
        # "all white with a RENDERED badge": our page, wrong theme.
        "NSRequiresAquaSystemAppearance": False,
    },
)
