"""输入注入：鼠标 / 键盘 / 宏。优先 pydirectinput（扫描码，游戏兼容），回退 pyautogui。"""
import sys
import time

USE_PDI = False
if sys.platform == "win32":
    try:
        import pydirectinput
        pydirectinput.PAUSE = 0
        pydirectinput.FAILSAFE = False
        USE_PDI = True
    except ImportError:
        pass

import pyautogui
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# 前端友好键名 -> 后端键名
ALIASES = {
    "win": "winleft", "cmd": "winleft", "meta": "winleft", "super": "winleft",
    "escape": "esc", "return": "enter", "backspace": "backspace", "del": "delete",
    "pgup": "pgup", "pgdn": "pgdn", "pageup": "pgup", "pagedown": "pgdn",
    "ctrl": "ctrl", "control": "ctrl", "option": "alt", "cmdorctrl": "ctrl",
    "capslock": "capslock", "printscreen": "printscreen",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    " ": "space", "plus": "+", "minus": "-",
}


def _k(name):
    name = str(name).lower().strip()
    return ALIASES.get(name, name)


# VK_PACKET：Windows Unicode 按键包（scan=0xE7+字符码），逐字符注入全角标点/中文。
# 不占剪贴板、不经远端输入法候选框，typewrite 打不了的字符走这条路。
VK_TEXT_OK = False
if sys.platform == "win32":
    import ctypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    def _vk_send(code, down):
        inp = _INPUT(type=1)   # INPUT_KEYBOARD
        inp.ki = _KEYBDINPUT(0, code, 0x0004 if down else (0x0004 | 0x0002), 0, None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    VK_TEXT_OK = True


class InputController:
    def __init__(self):
        self.pos = [0, 0]
        self._pt = None
        self._GetKeyState = None
        if sys.platform == "win32":
            import ctypes
            import ctypes.wintypes
            self._pt = ctypes.wintypes.POINT()
            self._GetCursorPos = ctypes.windll.user32.GetCursorPos
            self._GetKeyState = ctypes.windll.user32.GetKeyState

    def set_ime(self, cn):
        """切换系统输入法语言：cn=True → 首个中文(zh-CN)键盘布局/输入法，
        False → 英语(美国)键盘。广播 WM_INPUTLANGCHANGEREQUEST 跨进程请求切换，
        对微软拼音/搜狗等 TSF 输入法有效。找不到目标布局返回 False（尽力而为，不回读）。"""
        if self._GetKeyState is None:
            return False
        import ctypes
        u = ctypes.windll.user32
        langid = 0x0804 if cn else 0x0409       # 主语言：中文(中国) / 英语(美国)
        n = u.GetKeyboardLayoutList(0, None)
        if n <= 0:
            return False
        arr = (ctypes.c_ulong * n)()
        u.GetKeyboardLayoutList(n, ctypes.byref(arr))
        hkl = None
        for h in arr:
            lid = h & 0xFFFF
            if (langid == 0x0804 and (lid & 0x3FF) == 0x04) or (langid == 0x0409 and lid == 0x0409):
                hkl = h
                break
        if hkl is None:
            return False
        # HWND_BROADCAST=0xFFFF, WM_INPUTLANGCHANGEREQUEST=0x0050, lParam=目标布局
        u.PostMessageW(0xFFFF, 0x0050, 0, hkl)
        return True

    def get_caps(self):
        """远端 Caps Lock 是否点亮（VK_CAPITAL 切换状态低位）；非 Windows 返回 None。"""
        if self._GetKeyState is None:
            return None
        return bool(self._GetKeyState(0x14) & 1)

    def get_pos(self):
        """系统真实光标位置（mss 截屏不含光标，需回传给前端绘制）。"""
        if self._pt is not None:
            import ctypes
            if self._GetCursorPos(ctypes.byref(self._pt)):
                return self._pt.x, self._pt.y
        return pyautogui.position()

    # ---------- 鼠标 ----------
    def move(self, x, y):
        x, y = int(x), int(y)
        self.pos = [x, y]
        if USE_PDI:
            pydirectinput.moveTo(x, y)
        else:
            pyautogui.moveTo(x, y)

    def move_rel(self, dx, dy):
        if USE_PDI:
            pydirectinput.moveRel(int(dx), int(dy), relative=True)
        else:
            pyautogui.moveRel(int(dx), int(dy))

    def mouse_down(self, btn="left", x=None, y=None):
        if x is not None and y is not None:
            self.move(x, y)
        if USE_PDI:
            pydirectinput.mouseDown(button=btn)
        else:
            pyautogui.mouseDown(button=btn)

    def mouse_up(self, btn="left"):
        if USE_PDI:
            pydirectinput.mouseUp(button=btn)
        else:
            pyautogui.mouseUp(button=btn)

    def click(self, btn="left", dbl=False, x=None, y=None):
        if x is not None and y is not None:
            self.move(x, y)
        if USE_PDI:
            pydirectinput.click(button=btn, clicks=2 if dbl else 1)
        else:
            pyautogui.click(button=btn, clicks=2 if dbl else 1)

    def wheel(self, dy):
        # dy: 正数向下（与前端一致）。pydirectinput 没有 scroll——滚轮不存在
        # 扫描码兼容问题，统一走 pyautogui。
        try:
            n = -int(float(dy or 0))
        except (TypeError, ValueError):
            return
        if n:
            pyautogui.scroll(n)

    # ---------- 键盘 ----------
    def key_down(self, key):
        key = _k(key)
        try:
            if USE_PDI:
                pydirectinput.keyDown(key)
            else:
                pyautogui.keyDown(key)
        except Exception:
            pass

    def key_up(self, key):
        key = _k(key)
        try:
            if USE_PDI:
                pydirectinput.keyUp(key)
            else:
                pyautogui.keyUp(key)
        except Exception:
            pass

    def tap_key(self, key):
        self.key_down(key)
        self.key_up(key)

    def combo(self, keys):
        """同时按下组合键，如 ['ctrl','c']。"""
        keys = [_k(k) for k in keys]
        for k in keys:
            self.key_down(k)
        for k in reversed(keys):
            self.key_up(k)

    def text(self, s):
        s = str(s)
        if not s:
            return
        if VK_TEXT_OK:
            # 全角标点/中文走 VK_PACKET（BMP 内逐字符；增补平面字符跳过）
            for ch in s:
                code = ord(ch)
                if 0 < code <= 0xFFFF:
                    _vk_send(code, True)
                    _vk_send(code, False)
            return
        try:
            pyautogui.typewrite(s, interval=0.005)
        except Exception:
            pass

    # ---------- 宏 ----------
    def macro(self, steps):
        """steps: [{k:'w',d:'down'|'up'|'tap'} | {'ms':50} | {'t':'text','s':'hi'}
        | {'t':'click','btn':'left','x':..,'y':..} | {'t':'move','x':..,'y':..}]"""
        for st in steps:
            if not isinstance(st, dict):
                continue
            if "ms" in st:
                time.sleep(max(0, min(5000, int(st["ms"]))) / 1000.0)
                continue
            t = st.get("t")
            if t == "text":
                self.text(st.get("s", ""))
            elif t == "click":
                self.click(st.get("btn", "left"), bool(st.get("dbl")), st.get("x"), st.get("y"))
            elif t == "move":
                self.move(st.get("x", 0), st.get("y", 0))
            elif t == "wheel":
                self.wheel(st.get("dy", 0))
            elif "k" in st:
                d = st.get("d", "tap")
                if d == "down":
                    self.key_down(st["k"])
                elif d == "up":
                    self.key_up(st["k"])
                else:
                    self.tap_key(st["k"])
