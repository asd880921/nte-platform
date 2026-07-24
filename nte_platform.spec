# -*- mode: python ; coding: utf-8 -*-
# NTE 自動化平台 - PyInstaller 打包設定 (單一 exe，scripts 與 web 一起打包)

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 一起打包進 exe 的資料檔
datas = [
    ('launcher/web', 'web'),       # 前端 Apple 風格介面
    ('scripts', 'scripts'),        # 內附預設 scripts/，首次執行時自動解壓到 exe 旁
]
binaries = []

# 腳本是動態載入的，PyInstaller 掃不到它們的 import，需明列
hiddenimports = [
    'win32api', 'win32con', 'win32gui', 'win32ui', 'win32process',
    'pywintypes', 'pythoncom',
    'clr',
    'webview.platforms.edgechromium',
]

# 這些套件 (含 C 擴充 / 大量子模組) 需完整收集，避免子模組缺漏
# 例如 numpy 只列 hiddenimport 會漏掉 numpy._core._exceptions 導致載入失敗
for pkg in ('webview', 'clr_loader', 'pythonnet',
            'numpy', 'cv2', 'keyboard', 'psutil'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ['launcher/app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # pywebview 用 WebView2(edgechromium)，完全不需要這些 GUI 後端
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'sip', 'shiboken2', 'shiboken6',
        'tkinter', '_tkinter',
        # 我們的程式沒用到，卻常被間接拉進來的大套件
        'cryptography', 'matplotlib', 'pandas', 'scipy', 'PIL', 'IPython',
        'notebook', 'pytest', 'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 進一步剔除 cv2 用不到的大檔：影片 I/O 的 ffmpeg dll、人臉偵測 XML 資料
# (我們只用 imread / matchTemplate / minMaxLoc，這些都不需要)
import os as _os
a.binaries = [b for b in a.binaries
              if 'opencv_videoio_ffmpeg' not in _os.path.basename(b[0]).lower()]
a.datas = [d for d in a.datas
           if not _os.path.normpath(d[0]).lower().startswith(_os.path.normpath('cv2/data'))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir (資料夾模式)：啟動不需解壓、幾乎秒開；整個資料夾壓成 zip 交付
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NTE Platform',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,          # 視窗程式，完全不出現黑色主控台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NTE Platform',
)
