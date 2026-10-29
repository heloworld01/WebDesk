# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static')]
binaries = []
hiddenimports = ['mss.windows', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
# 托盘已改 QSystemTrayIcon（PyInstaller 自带 PyQt5 hooks，无需额外 hiddenimports）
hiddenimports += collect_submodules('pydirectinput')
tmp_ret = collect_all('aiortc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('av')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyautogui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyaudiowpatch')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['webdesk_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # cv2：pyscreeze 顶层 try-import 误拉（项目不用）；其余为环境里的开发/无关包，
    # 均为依赖的可选导入（缺失时走 ImportError 分支），排除不影响运行。
    excludes=['matplotlib', 'scipy', 'cv2', 'lxml',
              'bs4', 'IPython', 'Cython', 'cython',
              'pygments', 'prompt_toolkit', 'jedi', 'parso', 'rich',
              '_pytest', 'pytest', 'setuptools', 'distutils', 'trio', 'pydoc_data'],
    noarchive=False,
    # 注意：不要加 optimize=…——那是 PyInstaller 6.x 才有的 Analysis 参数
    # （本机 6.16 生成 spec 时会写入），Win7 离线线锁 5.13.2，带了会
    # TypeError 直接崩构建。2026-10-29 Win7 打包失败实证。
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WebDesk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
