"""WebDesk：远程访问本机桌面的 Web 服务。

协议（WebSocket /ws，JSON 文本消息；视频帧为二进制 JPEG）：
  客户端 -> 服务端:
    {t:"auth",pin}                      验证验证码（固定 PIN 或本次启动随机生成）
    {t:"cfg",fps,quality,scale}          运行时调节串流参数
    {t:"mmove",x,y} {t:"mrel",dx,dy}     鼠标移动（绝对/相对，桌面像素坐标）
    {t:"mdown",btn,x?,y?} {t:"mup",btn}  鼠标按下/抬起
    {t:"click",btn,x?,y?,dbl?}           点击
    {t:"wheel",dy}                       滚轮
    {t:"key",key,d:"down"|"up"|"tap"}    按键
    {t:"combo",keys:[...]}               组合键
    {t:"text",s}                         输入文本
    {t:"macro",steps:[...]}              宏序列
  服务端 -> 客户端:
    {t:"hello",w,h,need_auth}            连接后首条消息（桌面尺寸）
    {t:"cur",x,y}                        真实光标位置（~8Hz，仅移动时）
    {t:"caps",on}                        远端 Caps Lock 状态变化时推送
    {t:"ok"} {t:"err",msg}
"""
import asyncio
import copy
import json
import os
import secrets
import sys
import time
import uuid

# 必须在导入其他模块前设置 DPI 感知，保证截屏/输入/光标坐标全部为物理像素
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import webrtc
from capture import ScreenCapture
from input_ctrl import InputController

# 打包(PyInstaller)后：可写文件（config/layout/runtime/log）放 exe 同目录；
# 只读资源（static）在解包临时目录 _MEIPASS 中
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = BASE_DIR


def load_config():
    cfg = {"pin": "", "port": 8898, "monitor": "primary", "fps": 30,
           "quality": 60, "scale": 0.6, "bitrate": 6000000, "webrtc": True,
           "default_view": "remote"}
    path = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


LAYOUT_FILE = os.path.join(BASE_DIR, "layout.json")
RUNTIME_FILE = os.path.join(BASE_DIR, "runtime.json")


def load_layout():
    """读取服务端保存的控件布局；无/损坏时返回 None。
    新格式按方向分仓 {landscape:[...], portrait:[...]}；旧格式纯数组视为两方向共用一份。"""
    try:
        with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, list):
            return {"landscape": d, "portrait": copy.deepcopy(d)}
        if (isinstance(d, dict) and isinstance(d.get("landscape"), list)
                and isinstance(d.get("portrait"), list)):
            return d
        return None
    except (OSError, ValueError):
        return None


def save_layout(layout):
    """原子写入控件布局（客户端每次编辑后自动推送）。
    收到旧版客户端的单数组时，升级为两方向各存一份。"""
    if isinstance(layout, list):
        layout = {"landscape": layout, "portrait": copy.deepcopy(layout)}
    if not (isinstance(layout, dict) and isinstance(layout.get("landscape"), list)
            and isinstance(layout.get("portrait"), list)):
        return
    tmp = LAYOUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False)
    os.replace(tmp, LAYOUT_FILE)


CONFIG = load_config()


def make_auth_code():
    """本次服务使用的验证码：config 里设了固定 PIN 就用它，否则随机 6 位数字。
    随机码只在内存与本机 runtime.json 中，不通过任何 HTTP 接口暴露。"""
    fixed = str(CONFIG.get("pin") or "").strip()
    return fixed if fixed else f"{secrets.randbelow(1000000):06d}"


AUTH_CODE = make_auth_code()


def publish_runtime():
    """把验证码/端口写入本机 runtime.json，供控制面板读取展示并生成二维码。"""
    try:
        tmp = RUNTIME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"code": AUTH_CODE, "port": int(CONFIG["port"]),
                       "fixed": bool(str(CONFIG.get("pin") or "").strip()),
                       "pid": os.getpid()}, f, ensure_ascii=False)
        os.replace(tmp, RUNTIME_FILE)
    except OSError:
        pass


app = FastAPI(title="WebDesk")
capture = ScreenCapture(CONFIG)
inputc = InputController()

STATIC_DIR = os.path.join(RES_DIR, "static")
if not os.path.isdir(STATIC_DIR):                     # 兼容：exe 旁放一份 static 也可
    STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class SessionGate:
    """全局唯一远程会话闸门：同一时刻只允许 1 个远程连接。
    WS 连接授予 sid；/rtc/offer（视频+音频）必须携带同一 sid，
    防止第二个设备绕过 WS 直接拉流。事件循环单线程，无需加锁。"""

    def __init__(self):
        self.sid = None

    def acquire(self):
        if self.sid is not None:
            return None
        self.sid = uuid.uuid4().hex
        return self.sid

    def release(self, sid):
        if self.sid == sid:
            self.sid = None

    def claims(self, sid):
        return self.sid is not None and sid == self.sid


GATE = SessionGate()

try:
    webrtc.register(app, capture, CONFIG, GATE)
    WEBRTC_OK = True
except Exception as _e:  # aiortc 缺失等场景：自动退回 JPEG-only
    WEBRTC_OK = False
    print(f"[WebDesk] WebRTC 不可用，仅使用 JPEG 串流: {_e}")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/kb")
def kb_page():
    """键盘视图深链：与 / 是同一个单页应用，仅前端初始视图不同（键盘优先，不拉画面）。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
def health():
    w, h = capture.size
    from input_ctrl import USE_PDI
    return {"ok": True, "screen": [w, h], "frame": capture.seq,
            "clients": 1 if GATE.sid else 0,
            "input": "pydirectinput" if USE_PDI else "pyautogui"}


def run_msg(m):
    """处理一条客户端消息。"""
    t = m.get("t")
    if t in ("mmove",):
        inputc.move(m["x"], m["y"])
    elif t == "mrel":
        inputc.move_rel(m.get("dx", 0), m.get("dy", 0))
    elif t == "mdown":
        inputc.mouse_down(m.get("btn", "left"), m.get("x"), m.get("y"))
    elif t == "mup":
        inputc.mouse_up(m.get("btn", "left"))
    elif t == "click":
        inputc.click(m.get("btn", "left"), bool(m.get("dbl")), m.get("x"), m.get("y"))
    elif t == "wheel":
        inputc.wheel(m.get("dy", 0))
    elif t == "key":
        d = m.get("d", "tap")
        if d == "down":
            inputc.key_down(m.get("key", ""))
        elif d == "up":
            inputc.key_up(m.get("key", ""))
        else:
            inputc.tap_key(m.get("key", ""))
    elif t == "combo":
        inputc.combo(m.get("keys", []))
    elif t == "text":
        inputc.text(m.get("s", ""))
    elif t == "macro":
        inputc.macro(m.get("steps", []))
    elif t == "cfg":
        capture.set(
            **{k: m[k] for k in ("fps", "quality", "scale") if k in m}
        )


async def stream_frames(ws: WebSocket, state: dict):
    """有新帧且已鉴权时推送 JPEG 二进制帧；另以 ~8Hz 回传真实光标位置。
    仅在「已鉴权 且 jpeg 模式」期间向抓屏线程登记消费者——键盘视图/未鉴权/
    纯 WebRTC 时不登记，无任何消费者时抓屏线程整体休眠（零连接≈零 CPU）。"""
    last = -1
    last_cur = None
    last_caps = None                  # 远端 Caps Lock 状态（None=尚未发送过初始值）
    t_cur = 0.0
    t_ping = 0.0
    watching = False                      # 当前是否已登记 jpeg 消费者
    try:
        while True:
            need = bool(state.get("auth")) and state.get("video_mode", "jpeg") == "jpeg"
            if need and not watching:
                capture.add_jpeg_listener()
                watching = True
            elif not need and watching:
                capture.remove_jpeg_listener()
                watching = False
            if state.get("auth"):
                # 仅 jpeg 模式推 JPEG；webrtc（另走 RTC 通道）与 none（键盘页纯输入）都不推
                if need:
                    seq, data = capture.get_frame()
                    if seq != last and data is not None:
                        last = seq
                        try:
                            await ws.send_bytes(data)
                        except Exception:
                            return
                now = time.monotonic()
                if now - t_cur > 0.12:
                    t_cur = now
                    pos = inputc.get_pos()
                    if pos != last_cur:
                        last_cur = pos
                        try:
                            await ws.send_json({"t": "cur", "x": pos[0], "y": pos[1]})
                        except Exception:
                            return
                    # Caps Lock 同步：远端灯变化才推一条（首次鉴权即发初始值），
                    # 键盘页据此点亮 Caps 键并切换字母大小写显示
                    caps = inputc.get_caps()
                    if caps is not None and caps != last_caps:
                        last_caps = caps
                        try:
                            await ws.send_json({"t": "caps", "on": caps})
                        except Exception:
                            return
                # 保活心跳：webrtc 模式/静止桌面下可能长时间无任何消息下发，
                # 客户端看门狗靠它区分「连接健康但闲」与「半开僵尸连接」
                if now - t_ping > 3.0:
                    t_ping = now
                    try:
                        await ws.send_json({"t": "ping"})
                    except Exception:
                        return
            await asyncio.sleep(0.025)
    except Exception as e:
        # 推送协程猝死会让连接「看似正常却永不来帧」，务必留痕
        print(f"[WebDesk] {time.strftime('%H:%M:%S')} 帧推送协程异常退出: {type(e).__name__}: {e}", flush=True)
    finally:
        if watching:
            capture.remove_jpeg_listener()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ts = lambda: time.strftime("%H:%M:%S")
    peer = ws.client.host if ws.client else "?"
    sid = GATE.acquire()
    if sid is None:                      # 已有远程连接在用：拒绝并断开
        print(f"[WebDesk] {ts()} {peer} 连接被拒：已有设备占用（busy）", flush=True)
        await ws.send_json({"t": "busy"})
        await ws.close(code=4403)
        return
    print(f"[WebDesk] {ts()} {peer} 已连接，等待验证码", flush=True)
    # 验证码永远存在（固定 PIN 或随机 6 位）：所有连接都必须先过鉴权。
    # 注意：sid 不在 hello 里下发——否则未鉴权的页面可拿 sid 直接走 /rtc/offer
    # 拉视频流绕过验证码；鉴权通过后才随 ok 下发。
    state = {"auth": False, "video_mode": "jpeg"}
    w, h = capture.size
    # default_view 每次现读 config.json：面板改完无需重启，下一个新连接即生效
    await ws.send_json({"t": "hello", "w": w, "h": h,
                        "need_auth": True, "webrtc": WEBRTC_OK,
                        "default_view": str(load_config().get("default_view", "remote"))})

    async def push_layout():
        lay = load_layout()
        if lay is not None:
            await ws.send_json({"t": "layout", "layout": lay})

    # 握手后鉴权超时：迟迟不输码就断开（输码后自动重连）
    async def auth_timeout():
        await asyncio.sleep(30)
        if state["auth"]:
            return
        try:
            await ws.close(code=4401, reason="auth timeout")
        except Exception:
            pass    # 客户端已断开：对已关闭连接再 close 会抛 ASGI 错，忽略即可
    auth_task = asyncio.create_task(auth_timeout())

    sender = asyncio.create_task(stream_frames(ws, state))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = m.get("t")
            if t == "auth":
                if str(m.get("pin", "")).strip() == AUTH_CODE:
                    state["auth"] = True
                    print(f"[WebDesk] {ts()} {peer} 验证码通过", flush=True)
                    await ws.send_json({"t": "ok", "sid": sid})
                    await push_layout()
                else:
                    print(f"[WebDesk] {ts()} {peer} 验证码错误", flush=True)
                    await ws.send_json({"t": "err", "msg": "验证码错误"})
                continue
            if not state["auth"]:
                continue
            if t == "stream":
                state["video_mode"] = m.get("mode", "jpeg")
                continue
            if t == "layout":
                await asyncio.get_running_loop().run_in_executor(None, save_layout, m.get("layout"))
                continue
            try:
                # 输入注入是同步阻塞调用，放到线程池避免卡事件循环
                await asyncio.get_running_loop().run_in_executor(None, run_msg, m)
            except Exception as e:
                await ws.send_json({"t": "err", "msg": f"{type(e).__name__}: {e}"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        auth_task.cancel()              # 连接已结束，撤掉还在倒计时的鉴权超时任务
        sender.cancel()
        GATE.release(sid)                # 无论正常断开还是异常，都归还名额
        print(f"[WebDesk] {ts()} {peer} 连接已结束（已鉴权={state['auth']}），名额已释放", flush=True)


def main():
    publish_runtime()
    fixed = bool(str(CONFIG.get("pin") or "").strip())
    print(f"WebDesk 启动: http://0.0.0.0:{CONFIG['port']}  (桌面 {capture.size[0]}x{capture.size[1]})")
    print("手机/平板与本机同一局域网，浏览器访问  http://<本机IP>:%d" % CONFIG["port"])
    print("连接验证码: %s（%s）" % (AUTH_CODE, "config 固定" if fixed else "本次启动随机"))
    # ws 保活：每 10 秒 ping、10 秒无 pong 即判定断开并回收会话名额。
    # 手机 Wi-Fi 休眠/漫游产生的半开 TCP 靠它兜底，否则僵尸连接会一直占住唯一名额
    uvicorn.run(app, host="0.0.0.0", port=int(CONFIG["port"]), log_level="warning",
                ws_ping_interval=10, ws_ping_timeout=10)


if __name__ == "__main__":
    main()
