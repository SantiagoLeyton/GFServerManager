# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH)
ICON_PATH = ROOT / "assets" / "app.ico"
VERSION_FILE = ROOT / "packaging" / "version_info.txt"

datas = []
if (ROOT / "assets").exists():
    datas.append((str(ROOT / "assets"), "assets"))

hiddenimports = (
    collect_submodules("psycopg2")
    + collect_submodules("google")
    + collect_submodules("google_auth_oauthlib")
    + collect_submodules("googleapiclient")
    + collect_submodules("httplib2")
    + [
        "psutil",
        "waitress",
        "tkinter",
        "tkinter.ttk",
        "tkinter.scrolledtext",
    ]
)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GFServerManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    version=str(VERSION_FILE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GFServerManager",
)
