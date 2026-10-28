"""WebDesk 控制面板：启动/停止远程服务、查看连接信息、编辑远程桌面配置。

架构：服务是完全独立的进程（脱离面板存活，关面板不断服务）。
  - 启动：以分离进程运行 main.py，日志追加写 webdesk.log，面板实时尾读
  - 状态：/health 探活（因此手动 python main.py 起的实例也能识别/接管）
  - 停止：netstat 查端口占用 PID → taskkill
仅依赖标准库 tkinter；二维码为可选增强（pip install qrcode）。
建议用 pythonw 或「启动控制面板.bat」打开（无黑窗）。
"""
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 与 main.py 一致，防高分屏模糊
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# 打包后：可写文件放 exe 同目录；服务由本 exe 以 --service 参数自举（见 Controller.start）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STATE_FILE = os.path.join(BASE_DIR, "panel_state.json")
LOG_FILE = os.path.join(BASE_DIR, "webdesk.log")
RUNTIME_FILE = os.path.join(BASE_DIR, "runtime.json")   # 服务启动时写入：当前验证码

HIDE_WIN = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
DETACH = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0  # DETACHED|NEW_GROUP

DEFAULTS = {"pin": "", "port": 8898, "monitor": "primary", "fps": 30,
            "quality": 60, "scale": 0.6, "bitrate": 6000000, "webrtc": True}

GREEN, RED, AMBER, GRAY = "#2eaa5e", "#e05252", "#e0a13a", "#8a93a5"


# ---------------------------------------------------------------------------
# 配置 / 面板状态文件
# ---------------------------------------------------------------------------
def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else fallback
    except (OSError, ValueError):
        return fallback


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CONFIG_FILE, {}))
    return cfg


# ---------------------------------------------------------------------------
# 网络 / 系统信息
# ---------------------------------------------------------------------------
def lan_ips():
    """本机局域网 IPv4：默认路由网卡优先，其次其余非回环地址。"""
    primary, others = None, []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip == primary or ip in others:
                continue
            others.append(ip)
    except OSError:
        pass
    return ([primary] if primary else []) + others


def monitor_options():
    """显示器下拉选项：显示真实分辨率（mss 不可用时给通用项）。"""
    opts = [("主屏", "primary"), ("全部屏幕拼合", "all")]
    try:
        import mss
        with mss.mss() as sct:
            for i, mm in enumerate(sct.monitors):
                if i:  # monitors[0] 是所有屏的拼合虚拟屏
                    opts.append((f"屏幕{i}  {mm['width']}x{mm['height']}", str(i)))
    except Exception:
        opts += [("屏幕1", "1"), ("屏幕2", "2")]
    return opts


def listener_pid(port):
    """占用该 TCP 端口并处于 LISTENING 的进程 PID（netstat 解析）。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True,
                             errors="replace", creationflags=HIDE_WIN,
                             timeout=6).stdout
    except Exception:
        return None
    for line in out.splitlines():
        p = line.split()
        if (len(p) >= 5 and p[0].upper() == "TCP" and p[3].upper() == "LISTENING"
                and p[1].rsplit(":", 1)[-1] == str(port)):
            try:
                return int(p[4])
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------------------
# 服务控制（完全脱离面板的独立进程）
# ---------------------------------------------------------------------------
class Controller:
    def __init__(self, q: queue.Queue):
        self.q = q
        self._stop = threading.Event()
        self._fail_round = 0

    def start(self, port):
        if self.alive(port):
            return
        try:
            logf = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 面板启动服务 =====\n")
            logf.flush()
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            kwargs = dict(cwd=BASE_DIR, stdin=subprocess.DEVNULL,
                          stdout=logf, stderr=logf, env=env, close_fds=True)
            if sys.platform == "win32":
                kwargs["creationflags"] = DETACH
            else:
                kwargs["start_new_session"] = True
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--service"]       # 单文件 exe：以服务模式自举
            else:
                # -u：无缓冲，重定向到日志文件时也能逐行实时出现
                cmd = [sys.executable, "-u", os.path.join(BASE_DIR, "main.py")]
            subprocess.Popen(cmd, **kwargs)
            logf.close()
        except OSError as e:
            self.q.put(("log", f"[面板] 无法启动服务: {e}"))

    def stop(self, port):
        pid = listener_pid(port)
        if pid is None:
            return False
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, creationflags=HIDE_WIN, timeout=8)
            return not self.alive(port)
        except Exception:
            return False

    @staticmethod
    def health(port):
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                        timeout=2) as r:
                info = json.loads(r.read().decode("utf-8"))
            return info if info.get("ok") else None
        except Exception:
            return None

    def alive(self, port):
        return self.health(port) is not None

    def watch(self, port_provider):
        """探活线程：定期上报 health；连续失败才判定已停止（防抖动）。"""
        self._stop.clear()
        self._fail_round = 0
        while not self._stop.wait(2.0):
            port = port_provider()
            info = self.health(port)
            if info:
                self._fail_round = 0
                self.q.put(("health", info))
            else:
                self._fail_round += 1
                if self._fail_round >= 2:   # 4 秒无响应 = 已停止
                    self.q.put(("down", None))

    def stop_watch(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# 面板 UI
# ---------------------------------------------------------------------------
class Panel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q = queue.Queue()
        self.ctrl = Controller(self.q)
        self.cfg = load_config()
        self.state = load_json(STATE_FILE, {})
        self.ips = lan_ips()
        self.monitors = monitor_options()
        self._qr_img = None
        self._health = None
        self._hb_until = 0.0        # 主动停止后的短暂 health 屏蔽窗（吞掉在途旧探测结果）
        self._last_code = None

        root.title("WebDesk 控制面板")
        root.minsize(680, 100)      # 高度下限由 _fit_window 按内容自然高度实测钉死
        if sys.platform == "win32":
            try:
                dpi = ctypes.windll.user32.GetDpiForSystem()
                root.tk.call("tk", "scaling", dpi / 72.0)
            except Exception:
                pass

        self._build()
        self._refresh_info()
        self._fit_window()          # 初始（日志折叠态）：高度=完整展示所有元素的自然高度
        self._start_tail()          # 尾读 webdesk.log
        threading.Thread(target=self.ctrl.watch,
                         args=(lambda: int(self.cfg["port"]),), daemon=True).start()
        self._drain_events()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.state.get("auto_start", True):
            root.after(400, self._start_server)

    # ---------- 布局 ----------
    def _build(self):
        root = self.root

        top = ttk.Frame(root, padding=(14, 10, 14, 4))
        top.pack(fill="x")
        self.lamp = tk.Label(top, text="●", fg=GRAY, font=("", 15))
        self.lamp.pack(side="left")
        self.lbl_status = tk.Label(top, text="检测中…", font=("", 12, "bold"))
        self.lbl_status.pack(side="left", padx=(6, 14))
        self.btn_toggle = ttk.Button(top, text="启动服务", width=10, command=self._toggle)
        self.btn_toggle.pack(side="left")
        self.btn_restart = ttk.Button(top, text="重启服务", width=10, command=self._restart_clicked,
                                      state="disabled")
        self.btn_restart.pack(side="left", padx=(8, 0))
        ttk.Label(top, text="修改内容后点击重启服务才会生效",
                  foreground="#666").pack(side="left", padx=(8, 0))

        f_info = ttk.LabelFrame(root, padding=10, text=(
            "连接信息（手机/平板需与本机在同一 Wi-Fi/局域网）"))
        f_info.pack(fill="x", padx=12, pady=(6, 4))
        # 上行：二维码居左；右侧=提示信息+地址灰框；下行：远程连接占用与限制
        row = ttk.Frame(f_info)
        row.pack(fill="x")
        qrbox = ttk.Frame(row)
        qrbox.pack(side="left")
        self.lbl_qr = ttk.Label(qrbox, foreground=GRAY)
        self.lbl_qr.pack()
        right = ttk.Frame(row)
        self.right = right          # _make_qr 按其所需高度自适应二维码尺寸
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))
        hrow = ttk.Frame(right)
        hrow.pack(anchor="w")
        ttk.Label(hrow, text="推荐扫码直连，也可以输入下方地址连接",
                  foreground="#666").pack(side="left")
        ttk.Label(hrow, text="同一时刻仅限一台", foreground="#999").pack(side="left", padx=(10, 0))
        # 灰框高度贴合 4 行地址（不再纵向拉满），只横向吸收剩余宽度并左上对齐
        self.txt_info = tk.Text(right, height=4, width=44, font=("Consolas", 10), relief="flat",
                                background="#f4f6fa", state="disabled", wrap="none")
        self.txt_info.pack(fill="x", anchor="n", pady=(2, 0))
        # 验证码 + 登录后默认：贴着地址框放——这两项本来就是"连接"要用的设置，
        # 比藏在下方"远程桌面配置"里直观；改动一律实时写盘
        pin = str(self.cfg.get("pin") or "").strip()
        f_link = ttk.Frame(right)
        f_link.pack(anchor="w", pady=(6, 0))
        self.e_pin = self._field(f_link, 0, "验证码", pin, 10)
        # 固定勾选紧贴输入框，灰字提示跟在勾选后
        pinrow = ttk.Frame(f_link)
        pinrow.grid(row=0, column=2, sticky="w", padx=(2, 0))
        self.chk_pinfixed = ttk.Checkbutton(pinrow, text="固定", command=self._toggle_fixed)
        self.chk_pinfixed._var = tk.BooleanVar(value=bool(pin))
        self.chk_pinfixed.configure(variable=self.chk_pinfixed._var)
        self.chk_pinfixed.pack(side="left")
        ttk.Label(pinrow, foreground="#666", text="不勾固定，每次启动随机6位数字").pack(
            side="left", padx=(6, 0))
        self.v_view = tk.StringVar()
        cb_view = ttk.Combobox(f_link, textvariable=self.v_view, width=14, state="readonly",
                               values=["触屏模式（画面）", "键盘模式"])
        self._row(f_link, 1, "登录后默认", cb_view)
        self.v_view.set("键盘模式" if str(self.cfg.get("default_view", "remote")) == "kb"
                        else "触屏模式（画面）")
        # 端口 + 自动启动 同行：都是"这台服务怎么跑"的开关，集中一处
        self.e_port = self._field(f_link, 2, "端口", str(self.cfg["port"]), 8)
        self.chk_autostart = ttk.Checkbutton(f_link, text="打开面板时自动启动服务")
        self.chk_autostart._var = tk.BooleanVar(value=self.state.get("auto_start", True))
        self.chk_autostart.configure(variable=self.chk_autostart._var, command=self._save_state_now)
        self.chk_autostart.grid(row=2, column=2, columnspan=2, sticky="w", pady=4)
        self._sync_code_display()
        self._apply_pin_look()

        # 远程桌面配置：折叠窗（与服务日志同款式）。多屏时要在屏间切换、验证选择，
        # 默认展开；单屏没这些操作，默认折叠省一屏高度
        cfgbar = ttk.Frame(root)
        cfgbar.pack(fill="x", padx=12, pady=(4, 0))
        self._CFG_TITLE = "远程桌面配置"
        self._cfg_open = len(self.monitors) > 1
        self.btn_cfgtoggle = ttk.Button(cfgbar, command=self._toggle_cfg)
        self.btn_cfgtoggle.pack(side="left")
        self.f_cfg = f_cfg = ttk.Frame(root)
        g = ttk.Frame(f_cfg)
        g.pack(fill="x")

        self.v_mon = tk.StringVar()
        cb = ttk.Combobox(g, textvariable=self.v_mon, width=20, state="readonly",
                          values=[label for label, _ in self.monitors])
        self._row(g, 0, "屏幕", cb)
        cur = str(self.cfg["monitor"])
        for label, val in self.monitors:
            if val == cur:
                self.v_mon.set(label)
                break
        else:
            self.v_mon.set(self.monitors[0][0])
        self.v_rtc = tk.BooleanVar(value=bool(self.cfg["webrtc"]))
        ttk.Checkbutton(g, text="启用 WebRTC（H.264 视频 + 系统音频通道）",
                        variable=self.v_rtc).grid(row=0, column=3, columnspan=3,
                                                  sticky="w", padx=12)

        self.v_fps = tk.StringVar(value=str(self.cfg["fps"]))
        self._row(g, 1, "帧率 fps", ttk.Spinbox(g, from_=5, to=30, increment=5,
                                                textvariable=self.v_fps, width=6))
        self.v_bit = tk.StringVar(value=str(round(int(self.cfg["bitrate"]) / 1e6, 1)))
        self._row(g, 1, "H.264 码率 Mbps", ttk.Spinbox(g, from_=1, to=20, increment=0.5,
                                                       textvariable=self.v_bit, width=6), col=1)
        self.v_qual = tk.StringVar(value=str(self.cfg["quality"]))
        self._row(g, 1, "JPEG 质量", ttk.Spinbox(g, from_=20, to=95, increment=5,
                                                 textvariable=self.v_qual, width=6), col=2)
        self.v_scale = tk.StringVar(value=str(self.cfg["scale"]))
        self._row(g, 2, "JPEG 缩放", ttk.Spinbox(g, from_=0.3, to=1.0, increment=0.05,
                                                 textvariable=self.v_scale, width=6))
        ttk.Label(g, foreground="#666", text="（帧率/质量/缩放作用于 JPEG 兼容通道）").grid(
            row=2, column=2, columnspan=3, sticky="w", padx=12)

        # 提示条：有内容才出现，平时不给区域下方垫出一截空白
        self._cfgnote_bar = ttk.Frame(f_cfg)
        self._cfgnote_visible = False
        self.lbl_cfgnote = ttk.Label(self._cfgnote_bar, text="", foreground=AMBER)
        self.lbl_cfgnote.pack(side="left")

        # 实时保存：任何配置变量一变更就校验并写盘，不再有"保存配置"按钮。
        # 校验不过只在灰条上红字提醒（不打断输入），磁盘上始终留着最后一次合法值。
        for _var in (self.e_port._var, self.e_pin._var, self.chk_pinfixed._var,
                     self.v_mon, self.v_rtc, self.v_fps, self.v_bit, self.v_qual,
                     self.v_scale, self.v_view):
            _var.trace_add("write", self._on_cfg_change)
        # 初始折叠态（初始高度由 __init__ 末尾的 _fit_window 统一实测，这里不必再调）
        self.btn_cfgtoggle.configure(text=("▾ " if self._cfg_open else "▸ ") + self._CFG_TITLE)
        if self._cfg_open:
            f_cfg.pack(fill="x", padx=12, pady=(2, 0))

        # 服务日志：折叠窗，默认折叠；折叠时后台尾读照常更新，展开即见最新
        logbar = ttk.Frame(root)
        logbar.pack(fill="x", padx=12, pady=(4, 0))
        self._log_open = False
        self.btn_logtoggle = ttk.Button(logbar, text="▸ " + self._LOG_TITLE,
                                        command=self._toggle_log)
        self.btn_logtoggle.pack(side="left")
        self.f_log = ttk.Frame(root)
        self.txt_log = tk.Text(self.f_log, height=7, font=("Consolas", 9), relief="flat",
                               state="disabled", wrap="none")
        sb = ttk.Scrollbar(self.f_log, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_log.pack(fill="both", expand=True)

    _LOG_TITLE = "服务日志"

    def _toggle_log(self, force=None):
        self._log_open = (not self._log_open) if force is None else bool(force)
        if self._log_open:
            self.btn_logtoggle.configure(text="▾ " + self._LOG_TITLE)
            self.f_log.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        else:
            self.btn_logtoggle.configure(text="▸ " + self._LOG_TITLE)
            self.f_log.pack_forget()
        self._fit_window()

    def _toggle_cfg(self, force=None):
        self._cfg_open = (not self._cfg_open) if force is None else bool(force)
        self.btn_cfgtoggle.configure(text=("▾ " if self._cfg_open else "▸ ") + self._CFG_TITLE)
        if self._cfg_open:
            self.f_cfg.pack(fill="x", padx=12, pady=(2, 0))
        else:
            self.f_cfg.pack_forget()
        self._fit_window()

    def _fit_window(self):
        """把窗口高度设为当前布局「完整展示所有元素」的自然高度，并钉为最小高度：
        用户再怎么缩，也不会把元素裁掉（日志折叠/展开两态各自实测）。"""
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        self.root.minsize(680, h)
        self.root.geometry("740x%d" % h)

    @staticmethod
    def _row(parent, r, label, widget, col=0):
        ttk.Label(parent, text=label).grid(row=r, column=col * 2, sticky="e",
                                           padx=(0 if col == 0 else 10, 5), pady=4)
        widget.grid(row=r, column=col * 2 + 1, sticky="w", pady=4)
        return widget

    @staticmethod
    def _field(parent, r, label, value, width, col=0):
        ttk.Label(parent, text=label).grid(row=r, column=col * 2, sticky="e",
                                           padx=(0 if col == 0 else 10, 5), pady=4)
        e = ttk.Entry(parent, width=width)
        e._var = tk.StringVar(value=value)   # 挂在控件上防 GC（Tk 变量被回收会清空输入框）
        e.configure(textvariable=e._var)
        e.grid(row=r, column=col * 2 + 1, sticky="w", pady=4)
        return e

    # ---------- 连接信息 ----------
    def _base_urls(self):
        port = self.cfg["port"]
        return [f"http://{ip}:{port}" for ip in self.ips] or [f"http://<本机IP>:{port}"]

    def _auth_code(self):
        """当前生效验证码：读服务写的 runtime.json；服务没起过/旧版服务则回退配置值。"""
        rt = load_json(RUNTIME_FILE, {})
        if rt.get("port") == int(self.cfg["port"]) and rt.get("code"):
            return str(rt["code"]), bool(rt.get("fixed"))
        pin = str(self.cfg.get("pin") or "").strip()
        return (pin, True) if pin else ("", False)

    def _info_text(self):
        # 只列地址：端口含在 URL 里；验证码在下方配置区只读展示；
        # 桌面/引擎/连接数等状态灯悬停或日志可见，不再占信息区。
        # 服务未运行：隐藏地址（留着旧地址只会让人去连已关闭的端口）
        if not self._health:
            return "  （服务未启动，无可用地址）"
        return "\n".join(f"  {u}" for u in self._base_urls())

    def _refresh_info(self):
        self.ips = lan_ips()
        self.txt_info.configure(state="normal")
        self.txt_info.delete("1.0", "end")
        self.txt_info.insert("1.0", self._info_text())
        self.txt_info.configure(state="disabled")
        self._make_qr()
        self._sync_code_display()      # 随机码变了（重启服务后）实时跟随进输入框

    def _status_text(self):
        """左上角状态文字：运行中直接报告有没有设备连着，不再显示分辨率。"""
        if not self._health:
            return "已停止"
        conn = "设备已连接" if self._health.get("clients", 0) else "设备未连接"
        return f"运行中 · {conn} · 端口 {self.cfg['port']}"

    def _make_qr(self):
        # 自检门控：服务未就绪（/health 不通）时不展示可扫二维码。
        # 否则用户扫到的是浏览器"无法访问"错误页——那里没有我们的 JS，
        # 自动重连帮不上忙，只能手动反复刷新。就绪后此区域自动替换成二维码。
        if not self._health:
            self._qr_img = None
            self.lbl_qr.configure(image="", text="服务未运行\n启动后此处自动显示二维码\n（扫一次即可直连）")
            return
        url = self._base_urls()[0]
        code, _fixed = self._auth_code()
        if code:                            # 扫码 = 自动带验证码直连，免输入
            url += f"/?pin={code}"
        try:
            import qrcode
            from PIL import Image, ImageTk
            # 自适应：与右侧信息列（提示+地址框+配置行）的实际所需等高，
            # 二维码刚好占满连接信息区高度；上下限防止极端布局过大/过小
            self.right.update_idletasks()
            size = max(140, min(320, self.right.winfo_reqheight()))
            img = qrcode.make(url, box_size=5, border=2)
            img = img.resize((size, size), Image.NEAREST)   # NEAREST：保持模块硬边，扫码更稳
            self._qr_img = ImageTk.PhotoImage(img)
            self.lbl_qr.configure(image=self._qr_img, text="")
        except ImportError:
            self._qr_img = None
            self.lbl_qr.configure(image="", text="二维码需 qrcode 库:\npip install qrcode")
        except Exception as e:
            self._qr_img = None
            self.lbl_qr.configure(image="", text=f"二维码生成失败: {e}")

    def _toggle_fixed(self):
        if self.chk_pinfixed._var.get():
            cur = self.e_pin._var.get()
            if not cur or cur.startswith("（"):     # 勾选固定：先带上当前随机码，允许改成自定义
                self.e_pin._var.set(self._auth_code()[0])
        else:
            self._sync_code_display()               # 取消固定：回到只读展示当前随机码
        self._apply_pin_look()

    def _sync_code_display(self):
        """随机（未勾选固定）模式下，把当前生效验证码同步进只读输入框。"""
        if self.chk_pinfixed._var.get():
            return
        code = self._auth_code()[0] if self._health else ""
        self.e_pin._var.set(code or "（启动后自动生成）")

    def _apply_pin_look(self):
        # 随机码只读展示时本来就要给人看清；固定码是自己输入的——都没有打码的必要
        self.e_pin.configure(
            state="normal" if bool(self.chk_pinfixed._var.get()) else "readonly",
            show="")

    # ---------- 服务生命周期 ----------
    def _toggle(self):
        if self._health:
            self._stop_server()
        else:
            self._start_server()

    def _set_pending(self, text):
        """启动/停止进行中的即时视觉反馈（等待异步结果期间）。"""
        self.lamp.configure(fg=AMBER)
        self.lbl_status.configure(text=text)
        self.btn_toggle.configure(state="disabled", text=text)
        self.btn_restart.configure(state="disabled")   # 进行中禁止再点重启

    def _start_server(self):
        port = int(self.cfg["port"])
        if self.ctrl.alive(port):
            self._note("服务已在运行（可能是手动启动的实例，面板已接管）")
            return
        # 不在配置区发"正在启动服务…"：琥珀灯"启动中…"已表达，界面只说一遍
        self._hide_note()           # 顺带收起上一条遗留提示条
        self._hb_until = 0.0        # 新启动：取消残留的 health 屏蔽
        self._set_pending("启动中…")
        threading.Thread(target=self._start_worker, args=(port,), daemon=True).start()

    def _start_worker(self, port):
        self.ctrl.start(port)
        for _ in range(40):                 # 0.3s × 40 ≈ 12 秒（旧版 1s 一轮，成功也要干等凑秒）
            time.sleep(0.3)
            info = self.ctrl.health(port)
            if info:
                self.q.put(("health", info))
                return                       # 不再发"服务已启动"：绿灯"运行中"已表达，配置区左下角不留冗余提示
        self.q.put(("down", None))          # 超时：灯归灰、按钮恢复可点
        self.q.put(("note", "启动超时：端口可能被占用，详见下方日志"))

    def _stop_server(self):
        port = int(self.cfg["port"])
        pid = listener_pid(port)
        if pid is None:
            self._note("没有找到监听该端口的进程")
            return
        if not messagebox.askyesno("停止服务", f"将结束监听端口 {port} 的进程（PID {pid}），"
                                   "所有远程连接会断开。确定？"):
            return
        self._set_pending("停止中…")        # taskkill 挪到线程：不卡确认框后的界面
        threading.Thread(target=self._stop_worker, args=(port,), daemon=True).start()

    def _stop_worker(self, port):
        ok = self.ctrl.stop(port)
        if ok:
            # 立即上报（不等 watch 线程连续 2 轮失败 ≈6s 才反应过来）；
            # 开 2.5s 屏蔽窗吞掉停止指令前就已在途的旧 health 结果
            self._hb_until = time.monotonic() + 2.5
            self.q.put(("down", None))
            self.q.put(("note", "服务已停止"))
        else:
            self.q.put(("note", "停止失败，可在任务管理器结束该 PID"))
            # 服务其实还活着：watch 下一轮 health 会把灯/按钮自动恢复

    def _restart_clicked(self):
        """「重启服务」按钮入口：仅在运行中可用（停止态重启==启动，交给左侧按钮）。"""
        if not self._health:
            self._note("服务当前未运行，无需重启")
            return
        port = int(self.cfg["port"])
        if not messagebox.askyesno("重启服务", f"将重启服务（端口 {port}），所有远程连接会断开。确定？"):
            return
        self._restart_server()

    def _restart_server(self):
        self._note("正在重启服务…")
        self._hb_until = time.monotonic() + 3.5   # 覆盖 stop→start 期间的在途旧 health
        self._set_pending("重启中…")
        threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        port = int(self.cfg["port"])
        self.ctrl.stop(port)
        time.sleep(0.6)                     # 等端口释放
        self.ctrl.start(port)
        for _ in range(40):                 # 0.3s 快轮询
            time.sleep(0.3)
            info = self.ctrl.health(port)
            if info:
                self.q.put(("health", info))
                self.q.put(("note", "服务已重启"))
                return
        self.q.put(("down", None))
        self.q.put(("note", "重启后未响应，详见日志"))

    def _set_lamp(self, color, text):
        self.lamp.configure(fg=color)
        self.lbl_status.configure(text=text)
        # 启动流程里按钮被临时禁用（_start_server），状态灯每次更新都必须恢复可点，
        # 否则启动成功后「停止服务」永远置灰（健康轮询/停止事件都会走到这里）
        self.btn_toggle.configure(state="normal",
                                  text="停止服务" if color == GREEN else "启动服务")
        self.btn_restart.configure(state="normal" if color == GREEN else "disabled")

    def _note(self, msg):
        if "日志" in msg and not self._log_open:
            self._toggle_log(True)      # 提示"详见日志"时自动展开，免得用户找不到
        self._show_note(msg, AMBER)

    def _note_bad(self, msg):
        if not self._cfg_open:
            self._toggle_cfg(True)      # 配置窗折叠着时校验失败：展开才看得到红字原因
        self._show_note(msg, "#c0392b")

    def _show_note(self, text, color):
        self.lbl_cfgnote.configure(text=text, foreground=color)
        if not self._cfgnote_visible:
            self._cfgnote_bar.pack(fill="x", pady=(6, 2))
            self._cfgnote_visible = True
            self._fit_window()

    def _hide_note(self):
        self.lbl_cfgnote.configure(text="")
        if self._cfgnote_visible:
            self._cfgnote_bar.pack_forget()
            self._cfgnote_visible = False
            self._fit_window()

    # ---------- 配置 ----------
    def _collect_cfg(self):
        try:
            port = int(self.e_port.get().strip())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("端口需为 1024~65535 的整数")
        try:
            fps = int(float(self.v_fps.get()))
            qual = int(float(self.v_qual.get()))
            scale = round(float(self.v_scale.get()), 2)
            bit = round(float(self.v_bit.get()) * 1e6)
            if not (5 <= fps <= 30 and 20 <= qual <= 95
                    and 0.3 <= scale <= 1.0 and 1e6 <= bit <= 20e6):
                raise ValueError
        except ValueError:
            raise ValueError("帧率 5-30 / 质量 20-95 / 缩放 0.3-1 / 码率 1-20 Mbps")
        fixed = bool(self.chk_pinfixed._var.get())
        pin = self.e_pin.get().strip()
        if fixed and not pin:
            raise ValueError("勾选「固定」时验证码不能留空（想每次随机请取消勾选）")
        mon = dict(self.monitors).get(self.v_mon.get(), "primary")
        return {"pin": pin if fixed else "", "port": port, "monitor": mon,
                "fps": fps, "quality": qual, "scale": scale,
                "bitrate": bit, "webrtc": bool(self.v_rtc.get()),
                "default_view": "kb" if self.v_view.get() == "键盘模式" else "remote"}

    def _save_state_now(self):
        """自动启动勾选即时落盘（原为关面板时才存，现在改了立刻生效）。"""
        self.state["auto_start"] = bool(self.chk_autostart._var.get())
        save_json(STATE_FILE, self.state)

    def _on_cfg_change(self, *_):
        """配置变量一变更就实时保存：校验通过即写盘；
        不通过只在灰条红字提醒（不打断输入），磁盘上始终保留最后一次合法值。"""
        try:
            new = self._collect_cfg()
        except ValueError as e:
            self._note_bad(str(e))
            return
        old = self.cfg
        if new == old:
            return                      # 程序性回写（如随机码展示同步）触发，值没变不动盘
        self.cfg = new
        save_json(CONFIG_FILE, new)
        self._refresh_info()            # 验证码变了二维码要跟着变
        keys = ("port", "pin", "monitor", "webrtc", "fps", "quality", "scale", "bitrate")
        if all(old.get(k) == new.get(k) for k in keys):
            self._note("已自动保存 · 新连接即时生效")
        else:
            self._note("已自动保存 · 点上方「重启服务」后生效")

    # ---------- 日志尾读 ----------
    def _start_tail(self):
        def tail():
            offset = 0
            try:
                size = os.path.getsize(LOG_FILE)
                offset = max(0, size - 4096)     # 初始只回看 4KB
            except OSError:
                pass
            while True:
                try:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(offset)
                        chunk = f.read()
                        offset = f.tell()
                    if chunk:
                        for line in chunk.splitlines():
                            if line.strip():
                                self.q.put(("log", line))
                except OSError:
                    pass
                time.sleep(1.0)
        threading.Thread(target=tail, daemon=True).start()

    def _log(self, line):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line + "\n")
        if int(self.txt_log.index("end").split(".")[0]) > 600:
            self.txt_log.delete("1.0", "150.0")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    # ---------- 事件泵 ----------
    def _drain_events(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "health":
                    if self._health is None and time.monotonic() < self._hb_until:
                        continue    # 停止指令前已发出的 health 探测在途返回，吞掉，防止灯回绿闪一下
                    came_up = self._health is None
                    self._health = payload
                    if came_up:                                   # 刚起来：清掉"正在启动服务…"
                        self._hide_note()                         # 之类的过渡提示，连带提示条收回
                    self._set_lamp(GREEN, self._status_text())
                    # 设备连接/断开不一定伴随其它事件，每次轮询都校准状态文字
                    self.lbl_status.configure(text=self._status_text())
                    code, _ = self._auth_code()
                    if came_up or code != self._last_code:   # 服务起来/换了随机码 → 重画信息与二维码
                        self._last_code = code
                        self._refresh_info()
                elif kind == "log":
                    self._log(payload)
                elif kind == "down":
                    was_up = self._health is not None
                    self._health = None          # 必须先置 None 再刷新：
                    if was_up:                   # _make_qr/_info_text 靠它门控，停止时隐藏二维码与地址
                        self._refresh_info()
                    self._set_lamp(GRAY, self._status_text())   # "已停止"
                elif kind == "note":
                    self._note(payload)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    # ---------- 关闭 ----------
    def _on_close(self):
        # 注意：此回调里任何未捕获异常都会导致窗口永远关不掉（X 无反应），
        # 因此全部包在 try 里，finally 保证 destroy 必达。
        try:
            self.state["auto_start"] = bool(self.chk_autostart._var.get())
            save_json(STATE_FILE, self.state)
            if self._health:
                # 关面板 = 先停服务再退出，不留后台（服务与面板共存亡）
                try:
                    self.ctrl.stop(int(self.cfg["port"]))
                except Exception:
                    pass
            self.ctrl.stop_watch()
        except Exception:
            pass
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
