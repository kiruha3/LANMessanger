# -*- mode: python ; coding: utf-8 -*-
"""Native macOS bundle; Windows retains its existing onefile build command."""

from pathlib import Path
import runpy

root = Path(SPECPATH).resolve().parents[1]
version = runpy.run_path(str(root / "app" / "__init__.py"))["__version__"]

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    name="LANMessenger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas,
                     strip=False, upx=False, name="LANMessenger")
app = BUNDLE(
    collection,
    name="LANMessenger.app",
    icon=None,
    bundle_identifier="io.github.kiruha3.lanmessenger",
    version=version,
    info_plist={
        "CFBundleShortVersionString": version,
        "NSHighResolutionCapable": True,
        "NSLocalNetworkUsageDescription": (
            "LAN Messenger discovers nearby computers and exchanges messages "
            "over your local network."
        ),
    },
)
