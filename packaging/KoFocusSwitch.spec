# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — 메뉴바 .app 빌드
#   빌드: pyinstaller --noconfirm packaging/KoFocusSwitch.spec
#   결과: dist/KO Focus Switch.app
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = "0.2.1"

a = Analysis(
    [os.path.join(ROOT, "menubar_app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    # ko_focus_switch 는 같은 폴더 모듈, rumps/pyobjc 는 GUI 의존성
    hiddenimports=[
        "ko_focus_switch",
        "rumps",
        "objc",
        "Foundation",
        "AppKit",
        "PyObjCTools",
        "PyObjCTools.AppHelper",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KO Focus Switch",
    debug=False,
    strip=False,
    upx=False,
    console=False,            # GUI(windowed)
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KO Focus Switch",
)
app = BUNDLE(
    coll,
    name="KO Focus Switch.app",
    icon=None,
    bundle_identifier="dev.blender-ko.menubar",
    info_plist={
        "LSUIElement": True,                 # 메뉴바 전용(Dock 아이콘 없음)
        "CFBundleName": "KO Focus Switch",
        "CFBundleDisplayName": "KO Focus Switch",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.13",
        "NSHumanReadableCopyright": "MIT",
    },
)
