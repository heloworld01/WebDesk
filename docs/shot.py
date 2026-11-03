# -*- coding: utf-8 -*-
"""README 截图生成：无头 Chrome + CDP 触屏仿真拍手机界面，Win32 API 拍控制面板。
用法：python docs/shot.py <base_url_with_pin> [--panel-only]
     例：python docs/shot.py "http://127.0.0.1:8898/?pin=123456"
产物：docs/images/*.png
"""
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request

import asyncio
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

_msg = 0
class Session:
    def __init__(self, ws):
        self.ws = ws
    async def cmd(self, method, **params):
        global _msg
        _msg += 1
        mid = _msg
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

async def new_tab():
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req) as r:
        tab = json.load(r)
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
    return Session(ws), ws, tab["id"]

async def shot(name, w, h, url, js=None, timeout=25):
    # 每拍新开一个标签页：无头 Chrome 同标签 navigate 不会断开旧 WebSocket，
    # 旧连接占住"同一时刻仅限 1 台"名额，新页面会被 busy 拒之门外。
    # 关标签页则强杀渲染进程 → 服务端立即释放名额。
    sess, ws, tab_id = await new_tab()
    try:
        await sess.cmd("Emulation.setDeviceMetricsOverride",
                       width=w, height=h, deviceScaleFactor=2, mobile=True)
        await sess.cmd("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
        await sess.cmd("Network.setUserAgentOverride", userAgent=UA)
        await sess.cmd("Page.navigate", url=url)
        # 轮询等"已连接"（远程视图 #status / 键盘视图 #st），拿到帧再拍
        for _ in range(timeout):
            r = await sess.cmd("Runtime.evaluate", expression=
                "document.getElementById('status').textContent + '|' + "
                "document.getElementById('st').textContent")
            txt = r.get("result", {}).get("value", "")
            if "已连接" in txt:
                break
            await asyncio.sleep(1)
        await asyncio.sleep(2.5)                 # 再等几帧 JPEG 上屏
        if js:
            await sess.cmd("Runtime.evaluate", expression=js)
            await asyncio.sleep(4)               # 切视图后重排 + 重新推流
        r = await sess.cmd("Page.captureScreenshot", format="png")
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        print("saved", path, "| status:", txt)
    finally:
        await ws.close()
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tab_id}")
        await asyncio.sleep(1.5)                 # 给服务端一点时间处理断开

def shot_panel():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 必须最先设置：本机 125% 缩放下
    #                                                 非感知进程拿到的窗口矩形是虚拟化的，截图会错位成黑块
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, "WebDesk 控制面板")
    if not hwnd:
        print("panel window not found"); return
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)                        # SW_RESTORE：最小化先还原
    u.SwitchToThisWindow(hwnd, True)                 # 比 SetForegroundWindow 可靠（跨进程前台锁）
    time.sleep(0.8)
    class R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = R(); u.GetWindowRect(hwnd, ctypes.byref(rc))
    from PIL import ImageGrab
    img = ImageGrab.grab((rc.l, rc.t, rc.r, rc.b), all_screens=True)
    path = os.path.join(OUT, "panel.png")
    img.save(path)
    print("saved", path)

async def main():
    os.makedirs(OUT, exist_ok=True)
    if "--panel-only" in sys.argv:
        shot_panel()
        return
    base = sys.argv[1]
    proc = subprocess.Popen(
        [CHROME, f"--headless=new", f"--remote-debugging-port={PORT}",
         "--no-first-run", "--no-default-browser-check", "--disable-gpu",
         "--disable-background-timer-throttling",
         f"--user-data-dir={os.environ['TEMP']}\\wd-shot-profile", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        # 1) 竖屏键盘模式（触屏手机竖屏默认即键盘视图）
        await shot("keyboard-portrait.png", 412, 915, base)
        # 2) 竖屏触屏模式：点「触屏模式」→ 画面 + 底栏键盘停靠
        await shot("screen-portrait.png", 412, 915, base,
                   js="document.getElementById('go-screen').click()")
        # 3) 横屏触屏模式：重开页面（横屏默认画面视图，右上有全屏按钮）
        await shot("screen-landscape.png", 915, 412, base)
        # 4) 横屏键盘模式：点「键盘模式」
        await shot("keyboard-landscape.png", 915, 412, base,
                   js="document.getElementById('btn-kbpage').click()")
    finally:
        proc.terminate()
    shot_panel()

asyncio.run(main())
