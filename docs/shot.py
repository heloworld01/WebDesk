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

def shot_panel(name="panel.png", hwnd=None):
    import ctypes.wintypes  # noqa: F401  click_panel_lang 的 POINT
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 必须最先设置：本机 125% 缩放下
    #                                                 非感知进程拿到的窗口矩形是虚拟化的，截图会错位成黑块
    u = ctypes.windll.user32
    hwnd = hwnd or u.FindWindowW(None, "WebDesk 控制面板") or u.FindWindowW(None, "WebDesk Control Panel")
    if not hwnd:
        print("panel window not found"); return
    # 注意：HWND_TOPMOST/NOTOPMOST 必须用 c_void_p 传——64 位下 ctypes 默认按
    # 32 位 int 传 -1，不符号扩展指针高位 → 句柄变 0x00000000FFFFFFFF，置顶静默失效
    TOP, NOTOP = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
    SWP_NOSIZE, SWP_SHOWWINDOW = 0x0001, 0x0040
    u.SetWindowPos(hwnd, TOP, 0, 0, 0, 0, SWP_NOSIZE | SWP_SHOWWINDOW)   # 置顶绕前台锁
    # 截屏法抓窗口矩形（Qt 窗口走 GPU 合成，PrintWindow 会得到全黑图，实测踩坑；
    # 置 TOPMOST 后窗口必然在最上，矩形内即窗口本体）
    from PIL import ImageGrab
    l, t, r, b = _rect(u, hwnd)
    img = None
    for _ in range(8):                       # 刚重启的面板可能还在首绘（整窗纯白）：等内容出现
        time.sleep(0.75)
        img = ImageGrab.grab((l, t, r, b), all_screens=True)
        px = img.convert("RGB").load()
        w0, h0 = img.size
        samples = [px[x, y] for x in (w0 // 4, w0 // 2, 3 * w0 // 4)
                            for y in (h0 // 3, h0 // 2, 2 * h0 // 3)]
        if any(p != (255, 255, 255) and sum(p) > 30 for p in samples):
            break                            # 有内容且非全黑：可以拍
    u.SetWindowPos(hwnd, NOTOP, 0, 0, 0, 0, SWP_NOSIZE)                  # 还原层级
    path = os.path.join(OUT, name)
    img.save(path)
    print("saved", path)

def _rect(u, hwnd):
    class R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = R(); u.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.l, rc.t, rc.r, rc.b

def click_panel_lang(u, hwnd, x_rel):
    """鼠标直接点面板右上角语言单选钮的圆圈（EN≈900 / 中文≈772，y≈60，窗口相对坐标）。
    比"改 state+重启"可靠：不经历进程重启的首绘竞态与单实例互斥，点击后即时重建 UI。"""
    l, t, r, b = _rect(u, hwnd)
    pt = ctypes.wintypes.POINT(l + x_rel, t + 60)
    u.SetCursorPos(pt.x, pt.y)
    time.sleep(0.25)
    u.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    u.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    # 语言切换 = 整个窗口销毁重建（标题也会变）：等目标标题的窗口出现，返回新句柄。
    # 直接继续用旧句柄会拍到一个已销毁窗口的矩形位置（截出来是残影/空白）。
    for title in ("WebDesk Control Panel", "WebDesk 控制面板"):
        for _ in range(24):
            time.sleep(0.5)
            h = u.FindWindowW(None, title)
            if h and h != hwnd:
                time.sleep(1.2)         # 首绘缓冲
                u.SetWindowPos(h, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001)
                return h
    time.sleep(1.0)
    return hwnd                          # 兜底：Qt 若原地换语言不换句柄

def set_panel_lang(lang):
    """（备用）改面板语言并重启面板"""
    import json as _json
    p = os.path.join(os.path.dirname(HERE), "dist", "panel_state.json")
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(HERE), "panel_state.json")
    st = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            st = _json.load(f)
    if st.get("lang") == lang:
        return p, st
    old = st.get("lang")
    st["lang"] = lang
    with open(p, "w", encoding="utf-8") as f:
        _json.dump(st, f, ensure_ascii=False, indent=2)
    subprocess.run(["taskkill", "/IM", "WebDesk.exe", "/FI", "WINDOWTITLE eq WebDesk*"],
                   capture_output=True)
    exe = os.path.join(os.path.dirname(HERE), "dist", "WebDesk.exe")
    subprocess.Popen([exe], creationflags=0x8)   # DETACHED
    want = "WebDesk Control Panel" if lang == "en" else "WebDesk 控制面板"
    u = ctypes.windll.user32
    for _ in range(30):                       # 等新语言标题的窗口出现（旧窗口已被 taskkill）
        time.sleep(0.5)
        if u.FindWindowW(None, want):
            break
    time.sleep(1.5)                           # 首绘缓冲（shot_panel 内还有白屏重试兜底）
    return p, {"lang": old}

async def main():
    os.makedirs(OUT, exist_ok=True)
    if "--panel-only" in sys.argv:
        shot_panel(sys.argv[2] if len(sys.argv) > 2 else "panel.png")
        return
    if "--panel-pair" in sys.argv:               # 只重拍中英文两张面板图（手机页不受影响）
        shot_panel("panel.png")                  # 当前语言（约定为中文）
        u = ctypes.windll.user32
        hwnd = (u.FindWindowW(None, "WebDesk 控制面板") or
                u.FindWindowW(None, "WebDesk Control Panel"))
        if not hwnd:
            print("panel window not found"); return
        en_hwnd = click_panel_lang(u, hwnd, 900)   # 点 EN 单选钮（圆圈中心），拿重建后的新句柄
        shot_panel("panel-en.png", en_hwnd)
        click_panel_lang(u, en_hwnd, 772)          # 还原：点回「中文」
        print("panel lang restored to zh")
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
        # 两套语言各拍 4 张手机界面：中文供 README.md，英文供 README.en.md
        # （i18n 语言解析：URL ?lang= 优先于浏览器语言，headless 首启即生效）
        # 控件按钮的 label 属用户数据（layout.json），界面切英文也不会翻译存量标签；
        # 英文截图前现场改名重绘（仅内存，不触发 Widgets.save，不污染服务端布局）
        relabel = ("var m={'左键':'Left','右键':'Right','双击':'Double'};"
                   "Widgets.layout.forEach(function(w){if(m[w.label])w.label=m[w.label]});"
                   "Widgets.render();")
        for lang, suf in (("zh", ""), ("en", "-en")):
            url = base + "&lang=" + lang
            rj = relabel if lang == "en" else None
            # 1) 竖屏键盘模式（触屏手机竖屏默认即键盘视图）
            await shot("keyboard-portrait" + suf + ".png", 412, 915, url, js=rj)
            # 2) 竖屏触屏模式：点「触屏模式」→ 画面 + 底栏键盘停靠
            await shot("screen-portrait" + suf + ".png", 412, 915, url,
                       js=(rj + " document.getElementById('go-screen').click()") if rj
                          else "document.getElementById('go-screen').click()")
            # 3) 横屏触屏模式：重开页面（横屏默认画面视图，右上有全屏按钮）
            await shot("screen-landscape" + suf + ".png", 915, 412, url, js=rj)
            # 4) 横屏键盘模式：点「键盘模式」
            await shot("keyboard-landscape" + suf + ".png", 915, 412, url,
                       js=(rj + " document.getElementById('btn-kbpage').click()") if rj
                          else "document.getElementById('btn-kbpage').click()")
    finally:
        proc.terminate()
    shot_panel("panel.png")                       # 当前面板语言（约定：跑脚本前面板保持中文）
    _, restore = set_panel_lang("en")             # 英文面板：写 state → 重启面板 → 拍
    shot_panel("panel-en.png")
    if restore.get("lang"):                       # 还原原语言并重启回面板，不留英文态
        set_panel_lang(restore["lang"])
        print("panel lang restored to", restore["lang"])

asyncio.run(main())
