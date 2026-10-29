"""WebDesk 控制面板：启动/停止远程服务、查看连接信息、编辑远程桌面配置。

架构：服务是完全独立的进程（脱离面板存活，关面板不断服务）。
  - 启动：以分离进程运行 main.py，日志追加写 webdesk.log，面板实时尾读
  - 状态：/health 探活（因此手动 python main.py 起的实例也能识别/接管）
  - 停止：netstat 查端口占用 PID → taskkill
UI 为 PyQt5（Win7 兼容线钉死 PyQt5==5.15.7 / Qt 5.15.2，勿 pip install -U PyQt5）；
二维码为可选增强（pip install qrcode）。QSystemTrayIcon 托盘；关窗=缩托盘。
建议用「启动控制面板.bat」或打包 exe 打开（无黑窗）。
"""
import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                             QRadioButton, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget)

import i18n
from i18n import t

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
GRAY_TXT, BAD_TXT = "#666666", "#c0392b"


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
    """显示器下拉选项：显示真实分辨率（mss 不可用时给通用项）。标签随当前语言生成。"""
    opts = [(t("mon_primary"), "primary"), (t("mon_all"), "all")]
    try:
        import mss
        with mss.mss() as sct:
            for i, mm in enumerate(sct.monitors):
                if i:  # monitors[0] 是所有屏的拼合虚拟屏
                    opts.append((t("mon_n", n=i, w=mm["width"], h=mm["height"]), str(i)))
    except Exception:
        opts += [(t("mon_n_plain", n=1), "1"), (t("mon_n_plain", n=2), "2")]
    return opts


def make_tray_icon(color):
    """托盘图标：透明底 + 状态色圆点（复用面板灯色）。QPainter 直绘，无 PIL 依赖。"""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QPen(QColor(255, 255, 255, 220), 2))
    p.drawEllipse(3, 3, 26, 26)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 开机自启（HKCU Run 键，无需管理员；Win7/Win11 通用）
# ---------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "WebDesk"


def autostart_command():
    """当前形态下应写入 Run 键的命令行：静默进托盘启动。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):        # 开发态用 pythonw，免黑窗
        cand = os.path.join(os.path.dirname(pyw), "pythonw.exe")
        if os.path.exists(cand):
            pyw = cand
    script = os.path.join(BASE_DIR, "panel.py")
    return '"%s" "%s" --minimized' % (pyw, script)


def autostart_get():
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, RUN_NAME)
            return str(v)
    except OSError:
        return None


def autostart_set(on):
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def autostart_selfheal():
    """exe 搬过家 → Run 键还指旧路径：勾选着就改写为当前路径（仅打包态，
    避免开发态运行把用户装的 exe 自启项覆盖成 pythonw 命令行）。"""
    if not getattr(sys, "frozen", False):
        return
    cur = autostart_get()
    if cur and cur != autostart_command():
        autostart_set(True)


def acquire_single_instance():
    """命名互斥体防双实例（开机自启 + 手动双击）。返回 None = 已有实例。
    开发态与打包态各自一把锁：跑源码不会把用户装的 exe 面板挡在门外。"""
    if sys.platform != "win32":
        return True
    import ctypes
    name = "Local\\WebDeskPanelMutex" if getattr(sys, "frozen", False) else "Local\\WebDeskPanelDevMutex"
    h = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if h and ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        return None
    return h or True      # 句柄不 close，随进程存活


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
            self.q.put(("log", t("note_panel_spawn_fail", e=e)))

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
# 面板 UI（PyQt5）
# ---------------------------------------------------------------------------
def _gray(w):
    w.setStyleSheet("color:%s" % GRAY_TXT)
    return w


class Panel(QWidget):
    def __init__(self, minimized=False):
        super().__init__()
        self._tray = None           # QSystemTrayIcon（None = 托盘不可用，X 退回“退出”语义）
        self._quitting = False
        self.q = queue.Queue()
        self.ctrl = Controller(self.q)
        self.cfg = load_config()
        self.state = load_json(STATE_FILE, {})
        self._retranslating = False   # 语言重刷期间屏蔽配置回写
        autostart_selfheal()        # exe 搬过家：先修正 Run 键，再读勾选态
        i18n.set_lang(self.state.get("lang") or i18n.system_lang())   # 必须最先定语言：
        self.ips = lan_ips()                                          # monitors 等按语言生成
        self.monitors = monitor_options()
        self._health = None
        self._hb_until = 0.0        # 主动停止后的短暂 health 屏蔽窗（吞掉在途旧探测结果）
        self._last_code = None

        self.setWindowTitle(t("title"))
        self._build()
        self._refresh_info()
        self.setMinimumWidth(self._base_w())
        self.adjustSize()
        self._start_tail()          # 尾读 webdesk.log
        threading.Thread(target=self.ctrl.watch,
                         args=(lambda: int(self.cfg["port"]),), daemon=True).start()
        self._pump = QTimer(self)   # 工作线程 → GUI 线程的事件泵（原 tkinter after 循环）
        self._pump.timeout.connect(self._drain_events)
        self._pump.start(200)
        self._start_tray()
        # 开机自启（--minimized）：不调 show()，托盘照常工作，无闪窗
        if self.state.get("auto_start", True):
            QTimer.singleShot(400, self._start_server)

    # ---------- 布局 ----------
    def _build(self):
        root = QVBoxLayout(self)

        # 顶栏：状态灯 + 状态文字 + 启停/重启 + 提示 + 语言切换
        top = QHBoxLayout()
        self.lamp = QLabel("●")
        f = QFont()
        f.setPointSize(14)
        self.lamp.setFont(f)
        self._lamp_color(GRAY)
        top.addWidget(self.lamp)
        self.lbl_status = QLabel(t("status_checking"))
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        self.lbl_status.setFont(f)
        top.addWidget(self.lbl_status)
        top.addSpacing(12)
        self.btn_toggle = QPushButton(t("btn_start"))
        self.btn_toggle.setMinimumWidth(92)
        self.btn_toggle.clicked.connect(self._toggle)
        top.addWidget(self.btn_toggle)
        self.btn_restart = QPushButton(t("btn_restart"))
        self.btn_restart.setMinimumWidth(92)
        self.btn_restart.setEnabled(False)      # 停止态重启==启动，交给左侧按钮
        self.btn_restart.clicked.connect(self._restart_clicked)
        top.addWidget(self.btn_restart)
        top.addSpacing(8)
        self.lbl_hint = _gray(QLabel(t("hint_restart")))
        top.addWidget(self.lbl_hint)
        top.addStretch(1)
        # 语言切换：中文/EN 二选一，切换即重刷全部文案（无需重启）
        self.rb_lang = {}
        for code, label in (("zh", "中文"), ("en", "EN")):
            rb = QRadioButton(label)
            rb.setChecked(i18n.get_lang() == code)
            rb.clicked.connect(lambda _, c=code: self._set_lang(c))
            self.rb_lang[code] = rb
            top.addWidget(rb)
        root.addLayout(top)

        # 连接信息：左二维码，右提示+地址+连接相关设置
        self.f_info = QGroupBox(t("info_frame"))
        vi = QVBoxLayout(self.f_info)
        row = QHBoxLayout()
        self.lbl_qr = QLabel(t("qr_off"))
        self.lbl_qr.setStyleSheet("color:%s" % GRAY)
        self.lbl_qr.setAlignment(Qt.AlignCenter)
        row.addWidget(self.lbl_qr, 0, Qt.AlignTop)
        self.right = QWidget()    # _make_qr 按其 sizeHint 高度自适应二维码尺寸
        rv = QVBoxLayout(self.right)
        rv.setContentsMargins(0, 0, 0, 0)
        hrow = QHBoxLayout()
        self.lbl_info_hint = _gray(QLabel(t("info_hint")))
        self.lbl_one_client = _gray(QLabel(t("info_one_client")))
        hrow.addWidget(self.lbl_info_hint)
        hrow.addWidget(self.lbl_one_client)
        hrow.addStretch(1)
        rv.addLayout(hrow)
        # 灰框高度贴合 4 行地址（不再纵向拉满），只横向吸收剩余宽度
        self.txt_info = QPlainTextEdit()
        self.txt_info.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.txt_info.setFont(mono)
        self.txt_info.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_info.setStyleSheet("background:#f4f6fa;")
        self.txt_info.setFixedHeight(4 * self.txt_info.fontMetrics().lineSpacing() + 14)
        rv.addWidget(self.txt_info)
        # 验证码 + 登录后默认 + 端口：这几项本来就是"连接/这台服务怎么跑"的设置，
        # 比藏在下方"远程桌面配置"里直观；改动一律实时写盘
        pin = str(self.cfg.get("pin") or "").strip()
        f_link = QGridLayout()
        f_link.setHorizontalSpacing(8)
        self.lbl_pin = QLabel(t("lbl_pin"))
        self.e_pin = QLineEdit(pin)
        self.e_pin.setFixedWidth(140)
        f_link.addWidget(self.lbl_pin, 0, 0)
        f_link.addWidget(self.e_pin, 0, 1)
        pinrow = QHBoxLayout()      # 固定勾选紧贴输入框，灰字提示跟在勾选后
        self.chk_pinfixed = QCheckBox(t("chk_fixed"))
        self.chk_pinfixed.setChecked(bool(pin))
        pinrow.addWidget(self.chk_pinfixed)
        self.lbl_pin_hint = _gray(QLabel(t("hint_pin")))
        pinrow.addWidget(self.lbl_pin_hint)
        pinrow.addStretch(1)
        f_link.addLayout(pinrow, 0, 2)
        self.lbl_view = QLabel(t("lbl_default_view"))
        self.cb_view = QComboBox()
        f_link.addWidget(self.lbl_view, 1, 0)
        f_link.addWidget(self.cb_view, 1, 1)
        self.chk_autostart = QCheckBox(t("chk_auto_service"))
        self.chk_autostart.setChecked(bool(self.state.get("auto_start", True)))
        f_link.addWidget(self.chk_autostart, 1, 2)
        self.lbl_port = QLabel(t("lbl_port"))
        self.e_port = QLineEdit(str(self.cfg["port"]))
        self.e_port.setFixedWidth(70)
        f_link.addWidget(self.lbl_port, 2, 0)
        f_link.addWidget(self.e_port, 2, 1, Qt.AlignLeft)
        # 开机自启：写 HKCU Run 键，勾选态直接以注册表为准（外部删了也能如实显示）
        self.chk_bootlogin = QCheckBox(t("chk_autostart_os"))
        self.chk_bootlogin.setChecked(bool(autostart_get()))
        f_link.addWidget(self.chk_bootlogin, 2, 2)
        f_link.setColumnStretch(3, 1)
        rv.addLayout(f_link)
        row.addWidget(self.right, 1)
        vi.addLayout(row)
        root.addWidget(self.f_info)

        # 远程桌面配置：折叠窗（与服务日志同款式）。多屏时要在屏间切换、验证选择，
        # 默认展开；单屏没这些操作，默认折叠省一屏高度
        cfgbar = QHBoxLayout()
        self.btn_cfgtoggle = QPushButton()
        self.btn_cfgtoggle.clicked.connect(self._toggle_cfg)
        cfgbar.addWidget(self.btn_cfgtoggle)
        cfgbar.addStretch(1)
        root.addLayout(cfgbar)
        self._cfg_open = len(self.monitors) > 1
        self.f_cfg = QWidget()
        g = QGridLayout(self.f_cfg)
        self.lbl_mon = QLabel(t("lbl_monitor"))
        self.cb_mon = QComboBox()
        g.addWidget(self.lbl_mon, 0, 0)
        g.addWidget(self.cb_mon, 0, 1)
        self.chk_webrtc = QCheckBox(t("chk_webrtc"))
        g.addWidget(self.chk_webrtc, 0, 2, 1, 4)
        self.lbl_fps = QLabel(t("lbl_fps"))
        self.sp_fps = QSpinBox()
        self.sp_fps.setRange(5, 30)
        self.sp_fps.setSingleStep(5)
        g.addWidget(self.lbl_fps, 1, 0)
        g.addWidget(self.sp_fps, 1, 1)
        self.lbl_bit = QLabel(t("lbl_bitrate"))
        self.sp_bit = QDoubleSpinBox()
        self.sp_bit.setRange(1, 20)
        self.sp_bit.setSingleStep(0.5)
        self.sp_bit.setDecimals(1)
        g.addWidget(self.lbl_bit, 1, 2)
        g.addWidget(self.sp_bit, 1, 3)
        self.lbl_qual = QLabel(t("lbl_quality"))
        self.sp_qual = QSpinBox()
        self.sp_qual.setRange(20, 95)
        self.sp_qual.setSingleStep(5)
        g.addWidget(self.lbl_qual, 1, 4)
        g.addWidget(self.sp_qual, 1, 5)
        self.lbl_scale = QLabel(t("lbl_scale"))
        self.sp_scale = QDoubleSpinBox()
        self.sp_scale.setRange(0.3, 1.0)
        self.sp_scale.setSingleStep(0.05)
        self.sp_scale.setDecimals(2)
        g.addWidget(self.lbl_scale, 2, 0)
        g.addWidget(self.sp_scale, 2, 1)
        self.lbl_jpeg_hint = _gray(QLabel(t("hint_jpeg")))
        g.addWidget(self.lbl_jpeg_hint, 2, 2, 1, 4)
        # 提示条：有内容才出现，平时不给区域下方垫出一截空白
        self.lbl_cfgnote = QLabel("")
        g.addWidget(self.lbl_cfgnote, 3, 0, 1, 6)
        self.lbl_cfgnote.setVisible(False)
        g.setColumnStretch(6, 1)
        self.f_cfg.setVisible(self._cfg_open)
        root.addWidget(self.f_cfg)

        # 服务日志：折叠窗，默认折叠；折叠时后台尾读照常更新，展开即见最新
        logbar = QHBoxLayout()
        self.btn_logtoggle = QPushButton("▸ " + t("log_title"))
        self.btn_logtoggle.clicked.connect(self._toggle_log)
        logbar.addWidget(self.btn_logtoggle)
        logbar.addStretch(1)
        root.addLayout(logbar)
        self._log_open = False
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        mono9 = QFont(mono)
        mono9.setPointSize(9)
        self.txt_log.setFont(mono9)
        self.txt_log.setMaximumBlockCount(600)
        self.txt_log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_log.setStyleSheet("background:#f4f6fa;")
        self.txt_log.setMinimumHeight(150)
        self.txt_log.setVisible(False)
        root.addWidget(self.txt_log)

        # 下拉初始值与实时保存接线（先填值后连接，避免建界面时触发回写）
        self.cb_mon.activated.connect(self._on_cfg_change)   # activated=仅用户选择
        self.cb_view.activated.connect(self._on_cfg_change)
        self.chk_webrtc.toggled.connect(self._on_cfg_change)
        for sp in (self.sp_fps, self.sp_bit, self.sp_qual, self.sp_scale):
            sp.valueChanged.connect(self._on_cfg_change)
        # textEdited=仅用户键入（setText 展示随机码不算改动）
        self.e_pin.textEdited.connect(self._on_cfg_change)
        self.e_port.textEdited.connect(self._on_cfg_change)
        self.chk_pinfixed.toggled.connect(self._on_fixed_toggled)
        self.chk_autostart.toggled.connect(self._save_state_now)
        self.chk_bootlogin.toggled.connect(self._toggle_bootlogin)
        self._load_cfg_into_widgets()
        self.btn_cfgtoggle.setText(self._cfg_btn_text())
        self._apply_pin_look()

    def _load_cfg_into_widgets(self):
        """配置 → 控件（下拉用 addItem 的 userData 定位，语言无关）。"""
        self.cb_mon.blockSignals(True)
        self.cb_mon.clear()
        for label, val in self.monitors:
            self.cb_mon.addItem(label, val)
        idx = self.cb_mon.findData(str(self.cfg["monitor"]))
        self.cb_mon.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_mon.blockSignals(False)

        self.cb_view.blockSignals(True)
        self.cb_view.clear()
        self.cb_view.addItem(t("view_touch"), "remote")
        self.cb_view.addItem(t("view_kb"), "kb")
        self.cb_view.setCurrentIndex(
            self.cb_view.findData(str(self.cfg.get("default_view", "remote"))))
        if self.cb_view.currentIndex() < 0:
            self.cb_view.setCurrentIndex(0)
        self.cb_view.blockSignals(False)

        self.sp_fps.setValue(int(self.cfg["fps"]))
        self.sp_bit.setValue(round(int(self.cfg["bitrate"]) / 1e6, 1))
        self.sp_qual.setValue(int(self.cfg["quality"]))
        self.sp_scale.setValue(round(float(self.cfg["scale"]), 2))
        self.chk_webrtc.setChecked(bool(self.cfg["webrtc"]))
        self._sync_code_display()

    def _cfg_btn_text(self):
        return ("▾ " if self._cfg_open else "▸ ") + t("cfg_title")

    def _toggle_log(self, force=None):
        self._log_open = (not self._log_open) if force is None else bool(force)
        self.btn_logtoggle.setText(("▾ " if self._log_open else "▸ ") + t("log_title"))
        self.txt_log.setVisible(self._log_open)
        if self._log_open:
            self.txt_log.verticalScrollBar().setValue(
                self.txt_log.verticalScrollBar().maximum())   # 展开即见最新

    def _toggle_cfg(self, force=None):
        self._cfg_open = (not self._cfg_open) if force is None else bool(force)
        self.btn_cfgtoggle.setText(self._cfg_btn_text())
        self.f_cfg.setVisible(self._cfg_open)

    @staticmethod
    def _base_w():
        """内容基准宽：英文串普遍比中文长 30-60%，切语言要整体加宽。"""
        return 680 if i18n.get_lang() == "zh" else 900

    # ---------- 语言 ----------
    def _set_lang(self, lang):
        if lang == i18n.get_lang():
            return
        i18n.set_lang(lang)
        self.state["lang"] = lang
        save_json(STATE_FILE, self.state)
        self._retranslate()

    def _retranslate(self):
        """运行时切语言：Qt 没有 Designer 的 retranslateUi，逐控件用引用重刷；
        动态串（状态/二维码/验证码占位）按新语言重新生成；瞬态提示条直接清掉。"""
        self._retranslating = True
        try:
            self.setWindowTitle(t("title"))
            self.lbl_status.setText(self._status_text())
            self.btn_toggle.setText(t("btn_stop") if self._health else t("btn_start"))
            self.btn_restart.setText(t("btn_restart"))
            self.lbl_hint.setText(t("hint_restart"))
            self.f_info.setTitle(t("info_frame"))
            self.lbl_info_hint.setText(t("info_hint"))
            self.lbl_one_client.setText(t("info_one_client"))
            self.lbl_pin.setText(t("lbl_pin"))
            self.chk_pinfixed.setText(t("chk_fixed"))
            self.lbl_pin_hint.setText(t("hint_pin"))
            self.lbl_view.setText(t("lbl_default_view"))
            self.lbl_port.setText(t("lbl_port"))
            self.chk_autostart.setText(t("chk_auto_service"))
            self.chk_bootlogin.setText(t("chk_autostart_os"))
            self.btn_cfgtoggle.setText(self._cfg_btn_text())
            self.btn_logtoggle.setText(
                ("▾ " if self._log_open else "▸ ") + t("log_title"))
            # 屏幕下拉：只改条目文字，userData 不变 → 选中项自动保持；
            # 显示器数量变了（换屏/插拔）才整体重建
            self.monitors = monitor_options()
            self.lbl_mon.setText(t("lbl_monitor"))
            if self.cb_mon.count() == len(self.monitors):
                for i, (label, _val) in enumerate(self.monitors):
                    self.cb_mon.setItemText(i, label)
            else:
                self._load_cfg_into_widgets()
            self.cb_view.setItemText(0, t("view_touch"))
            self.cb_view.setItemText(1, t("view_kb"))
            self.chk_webrtc.setText(t("chk_webrtc"))
            self.lbl_fps.setText(t("lbl_fps"))
            self.lbl_bit.setText(t("lbl_bitrate"))
            self.lbl_qual.setText(t("lbl_quality"))
            self.lbl_scale.setText(t("lbl_scale"))
            self.lbl_jpeg_hint.setText(t("hint_jpeg"))
            self._hide_note()         # 瞬态提示不驻留旧语言文本
            self._refresh_info()      # 地址框/二维码/验证码占位按新语言重出
            if self._tray:
                self._act_show.setText(t("tray_show"))
                self._act_toggle.setText(t("btn_stop") if self._health else t("btn_start"))
                self._act_quit.setText(t("tray_quit"))
            # 英文整体更宽：按新内容重排窗口（顶栏/信息区的静态灰字提示也在其中）
            self.setMinimumWidth(self._base_w())
            self.adjustSize()
            self.resize(max(self.width(), self.sizeHint().width()),
                        max(self.height(), self.sizeHint().height()))
        finally:
            self._retranslating = False

    # ---------- 连接信息 ----------
    def _base_urls(self):
        port = self.cfg["port"]
        return [f"http://{ip}:{port}" for ip in self.ips] or [t("info_no_ip", port=port)]

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
            return t("info_off")
        return "\n".join(f"  {u}" for u in self._base_urls())

    def _refresh_info(self):
        self.ips = lan_ips()
        self.txt_info.setPlainText(self._info_text())
        self._make_qr()
        self._sync_code_display()      # 随机码变了（重启服务后）实时跟随进输入框

    def _status_text(self):
        """左上角状态文字：运行中直接报告有没有设备连着，不再显示分辨率。"""
        if not self._health:
            return t("st_stopped")
        conn = t("st_connected") if self._health.get("clients", 0) else t("st_disconnected")
        return t("st_running", conn=conn, port=self.cfg["port"])

    def _make_qr(self):
        # 自检门控：服务未就绪（/health 不通）时不展示可扫二维码。
        # 否则用户扫到的是浏览器"无法访问"错误页——那里没有我们的 JS，
        # 自动重连帮不上忙，只能手动反复刷新。就绪后此区域自动替换成二维码。
        if not self._health:
            self.lbl_qr.setPixmap(QPixmap())
            self.lbl_qr.setText(t("qr_off"))
            return
        url = self._base_urls()[0]
        code, _fixed = self._auth_code()
        if code:                            # 扫码 = 自动带验证码直连，免输入
            url += f"/?pin={code}"
        try:
            import qrcode
            from PIL import Image
            # 自适应：与右侧信息列（提示+地址框+配置行）的所需高度大致等高，
            # 二维码刚好占满连接信息区高度；上下限防止极端布局过大/过小
            size = max(140, min(320, self.right.sizeHint().height() + 16))
            img = qrcode.make(url, box_size=5, border=2)
            img = img.resize((size, size), Image.NEAREST)   # NEAREST：保持模块硬边，扫码更稳
            buf = io.BytesIO()
            img.save(buf, "PNG")                            # PIL → Qt：走 PNG 内存中转
            pm = QPixmap()
            pm.loadFromData(buf.getvalue())
            self.lbl_qr.setPixmap(pm)
            self.lbl_qr.setText("")
        except ImportError:
            self.lbl_qr.setPixmap(QPixmap())
            self.lbl_qr.setText(t("qr_need_lib"))
        except Exception as e:
            self.lbl_qr.setPixmap(QPixmap())
            self.lbl_qr.setText(t("qr_fail", e=e))

    def _on_fixed_toggled(self, on):
        self._toggle_fixed()
        self._on_cfg_change()             # 勾选改变 pin 的语义（固定/随机），也算配置改动

    def _toggle_fixed(self):
        if self.chk_pinfixed.isChecked():
            cur = self.e_pin.text()
            if not cur or cur.startswith("（"):     # 勾选固定：先带上当前随机码，允许改成自定义
                self.e_pin.setText(self._auth_code()[0])
        else:
            self._sync_code_display()               # 取消固定：回到只读展示当前随机码
        self._apply_pin_look()

    def _sync_code_display(self):
        """随机（未勾选固定）模式下，把当前生效验证码同步进只读输入框。"""
        if self.chk_pinfixed.isChecked():
            return
        code = self._auth_code()[0] if self._health else ""
        self.e_pin.setText(code or t("pin_pending"))

    def _apply_pin_look(self):
        # 随机码只读展示时本来就要给人看清；固定码是自己输入的——都没有打码的必要
        self.e_pin.setReadOnly(not self.chk_pinfixed.isChecked())
        self.e_pin.setEchoMode(QLineEdit.Normal)

    # ---------- 服务生命周期 ----------
    def _toggle(self):
        if self._health:
            self._stop_server()
        else:
            self._start_server()

    def _lamp_color(self, color):
        self.lamp.setStyleSheet("color:%s" % color)

    def _ask(self, title, body):
        return QMessageBox.question(self, title, body,
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _set_pending(self, text):
        """启动/停止进行中的即时视觉反馈（等待异步结果期间）。"""
        self._lamp_color(AMBER)
        self.lbl_status.setText(text)
        self.btn_toggle.setEnabled(False)
        self.btn_toggle.setText(text)
        self.btn_restart.setEnabled(False)     # 进行中禁止再点重启

    def _start_server(self):
        port = int(self.cfg["port"])
        if self.ctrl.alive(port):
            self._note(t("note_taken"))
            return
        # 不在配置区发"正在启动服务…"：琥珀灯"启动中…"已表达，界面只说一遍
        self._hide_note()           # 顺带收起上一条遗留提示条
        self._hb_until = 0.0        # 新启动：取消残留的 health 屏蔽
        self._set_pending(t("starting"))
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
        self.q.put(("note", (t("note_start_timeout"), True)))

    def _stop_server(self):
        port = int(self.cfg["port"])
        pid = listener_pid(port)
        if pid is None:
            self._note(t("note_no_listener"))
            return
        if not self._ask(t("mb_stop_title"), t("mb_stop_body", port=port, pid=pid)):
            return
        self._set_pending(t("stopping"))    # taskkill 挪到线程：不卡确认框后的界面
        threading.Thread(target=self._stop_worker, args=(port,), daemon=True).start()

    def _stop_worker(self, port):
        ok = self.ctrl.stop(port)
        if ok:
            # 立即上报（不等 watch 线程连续 2 轮失败 ≈6s 才反应过来）；
            # 开 2.5s 屏蔽窗吞掉停止指令前就已在途的旧 health 结果
            self._hb_until = time.monotonic() + 2.5
            self.q.put(("down", None))
            self.q.put(("note", t("note_stopped")))
        else:
            self.q.put(("note", t("note_stop_failed")))
            # 服务其实还活着：watch 下一轮 health 会把灯/按钮自动恢复

    def _restart_clicked(self):
        """「重启服务」按钮入口：仅在运行中可用（停止态重启==启动，交给左侧按钮）。"""
        if not self._health:
            self._note(t("note_not_running"))
            return
        port = int(self.cfg["port"])
        if not self._ask(t("mb_restart_title"), t("mb_restart_body", port=port)):
            return
        self._restart_server()

    def _restart_server(self):
        self._note(t("note_restarting"))
        self._hb_until = time.monotonic() + 3.5   # 覆盖 stop→start 期间的在途旧 health
        self._set_pending(t("restarting"))
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
                self.q.put(("note", t("note_restarted")))
                return
        self.q.put(("down", None))
        self.q.put(("note", (t("note_restart_unresponsive"), True)))

    def _set_lamp(self, color, text):
        self._lamp_color(color)
        self.lbl_status.setText(text)
        self._tray_set_color(color)       # 托盘图标颜色跟随服务状态
        # 启动流程里按钮被临时禁用（_start_server），状态灯每次更新都必须恢复可点，
        # 否则启动成功后「停止服务」永远置灰（健康轮询/停止事件都会走到这里）
        self.btn_toggle.setEnabled(True)
        self.btn_toggle.setText(t("btn_stop") if color == GREEN else t("btn_start"))
        self.btn_restart.setEnabled(color == GREEN)

    def _note(self, msg, want_log=False):
        if want_log and not self._log_open:
            self._toggle_log(True)      # 提示"详见日志"时自动展开，免得用户找不到
        self._show_note(msg, AMBER)

    def _note_bad(self, msg):
        if not self._cfg_open:
            self._toggle_cfg(True)      # 配置窗折叠着时校验失败：展开才看得到红字原因
        self._show_note(msg, BAD_TXT)

    def _show_note(self, text, color):
        self.lbl_cfgnote.setText(text)
        self.lbl_cfgnote.setStyleSheet("color:%s" % color)
        self.lbl_cfgnote.setVisible(bool(text))

    def _hide_note(self):
        self.lbl_cfgnote.setVisible(False)

    # ---------- 配置 ----------
    def _collect_cfg(self):
        try:
            port = int(self.e_port.text().strip())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError(t("err_port"))
        fps = self.sp_fps.value()
        qual = self.sp_qual.value()
        scale = round(self.sp_scale.value(), 2)
        bit = round(self.sp_bit.value() * 1e6)
        if not (5 <= fps <= 30 and 20 <= qual <= 95
                and 0.3 <= scale <= 1.0 and 1e6 <= bit <= 20e6):
            raise ValueError(t("err_ranges"))      # SpinBox 已限位，此为兜底
        fixed = self.chk_pinfixed.isChecked()
        pin = self.e_pin.text().strip()
        if fixed and not pin:
            raise ValueError(t("err_pin_empty"))
        return {"pin": pin if fixed else "", "port": port,
                "monitor": self.cb_mon.currentData() or "primary",
                "fps": fps, "quality": qual, "scale": scale,
                "bitrate": bit, "webrtc": self.chk_webrtc.isChecked(),
                "default_view": self.cb_view.currentData() or "remote"}

    def _toggle_bootlogin(self, on):
        if autostart_set(on):
            self._note(t("note_boot_on") if on else t("note_boot_off"))
        else:
            self.chk_bootlogin.blockSignals(True)
            self.chk_bootlogin.setChecked(not on)   # 写注册表失败：勾选弹回
            self.chk_bootlogin.blockSignals(False)
            self._note_bad(t("err_boot_reg"))

    def _save_state_now(self):
        """自动启动勾选即时落盘（原为关面板时才存，现在改了立刻生效）。"""
        self.state["auto_start"] = bool(self.chk_autostart.isChecked())
        save_json(STATE_FILE, self.state)

    def _on_cfg_change(self, *_):
        """配置控件一变更就实时保存：校验通过即写盘；
        不通过只在配置区红字提醒（不打断输入），磁盘上始终保留最后一次合法值。"""
        if self._retranslating:
            return                      # 语言重刷时的程序性回写，忽略
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
        self._hide_note()
        self._refresh_info()            # 验证码变了二维码要跟着变
        keys = ("port", "pin", "monitor", "webrtc", "fps", "quality", "scale", "bitrate")
        if all(old.get(k) == new.get(k) for k in keys):
            self._note(t("note_saved_live"))
        else:
            self._note(t("note_saved_restart"))

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
        self.txt_log.appendPlainText(line)   # 超长自动截断（maximumBlockCount）

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
                        self._hide_note()                         # 之类的过渡提示
                    self._set_lamp(GREEN, self._status_text())
                    # 设备连接/断开不一定伴随其它事件，每次轮询都校准状态文字
                    self.lbl_status.setText(self._status_text())
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
                    if isinstance(payload, tuple):   # (消息, want_log)：提示"详见日志"时自动展开
                        self._note(*payload)
                    else:
                        self._note(payload)
        except queue.Empty:
            pass

    # ---------- 托盘 ----------
    def _start_tray(self):
        """创建托盘图标；不可用时 self._tray=None，X 退回退出语义。"""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self._tray = None
                return
            menu = QMenu(self)
            self._act_show = menu.addAction(t("tray_show"))
            self._act_show.triggered.connect(self._show_from_tray)
            self._act_toggle = menu.addAction(t("btn_stop") if self._health else t("btn_start"))
            self._act_toggle.triggered.connect(self._toggle)
            menu.addSeparator()
            self._act_quit = menu.addAction(t("tray_quit"))
            self._act_quit.triggered.connect(self._quit)
            self._tray = QSystemTrayIcon(make_tray_icon(GRAY), "WebDesk", self)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        except Exception:
            self._tray = None       # 托盘失败不影响主流程，X 退回退出语义

    def _on_tray_activated(self, reason):
        # 单击/双击/中键都唤起面板（Windows 常见习惯是双击，触发一次即可见）
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick,
                      QSystemTrayIcon.MiddleClick):
            self._show_from_tray()

    def _tray_set_color(self, color):
        if self._tray:
            try:
                self._tray.setIcon(make_tray_icon(color))
                self._act_toggle.setText(t("btn_stop") if self._health else t("btn_start"))
            except Exception:
                pass

    def _hide_to_tray(self):
        self.hide()
        if self._tray and not self.state.get("tray_hint_shown"):
            self._tray.showMessage("WebDesk", t("tray_notify"),
                                   QSystemTrayIcon.Information, 5000)
            self.state["tray_hint_shown"] = True
            save_json(STATE_FILE, self.state)

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---------- 关闭 / 退出 ----------
    def closeEvent(self, event):
        """点 X：缩到托盘，服务继续跑（远程会话不断线）。托盘不可用时退回直接退出。"""
        if self._quitting:
            event.accept()
            return
        event.ignore()
        if self._tray:
            self._hide_to_tray()
        else:
            self._quit()

    def _quit(self):
        # 注意：此路径上任何未捕获异常都会导致进程永远退不掉（托盘退出无反应），
        # 因此全部包在 try 里，finally 保证应用必退。
        if self._quitting:
            return
        if self._health and not self._ask(t("mb_quit_title"), t("mb_quit_body")):
            return
        self._quitting = True
        try:
            self.state["auto_start"] = bool(self.chk_autostart.isChecked())
            save_json(STATE_FILE, self.state)
            if self._health:
                # 退出 = 先停服务再走，不留后台（服务与面板共存亡）
                try:
                    self.ctrl.stop(int(self.cfg["port"]))
                except Exception:
                    pass
            self.ctrl.stop_watch()
        except Exception:
            pass
        finally:
            if self._tray:
                self._tray.hide()
            QApplication.instance().quit()


_single_instance = None     # 互斥体句柄：持有到进程退出，勿回收


def main():
    global _single_instance
    _single_instance = acquire_single_instance()
    if _single_instance is None:
        return                      # 已有面板在跑：静默退出
    minimized = "--minimized" in sys.argv
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 缩到托盘（窗口隐藏）≠ 退出
    panel = Panel(minimized=minimized)
    if not minimized:
        panel.show()                # --minimized：不 show，托盘照常工作，无闪窗
    app.exec_()


if __name__ == "__main__":
    main()
