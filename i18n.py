"""面板中英双语文案。模块级当前语言：Panel 启动时 set_lang()，控件经 t() 取词；
运行时切换由 Panel._retranslate() 统一重刷（messagebox/托盘菜单等调用期取词自动跟随）。
服务日志（main.py 输出）保持中文，属原始日志不译。"""

STR = {
    "zh": {
        "title": "WebDesk 控制面板",
        "status_checking": "检测中…",
        "btn_start": "启动服务", "btn_stop": "停止服务", "btn_restart": "重启服务",
        "hint_restart": "修改内容后点击重启服务才会生效",
        "info_frame": "连接信息（手机/平板需与本机在同一 Wi-Fi/局域网）",
        "info_hint": "推荐扫码直连，也可以输入下方地址连接",
        "info_one_client": "同一时刻仅限一台",
        "lbl_pin": "验证码", "chk_fixed": "固定",
        "hint_pin": "不勾固定，每次启动随机6位数字",
        "lbl_default_view": "登录后默认", "view_touch": "触屏模式（画面）", "view_kb": "键盘模式",
        "lbl_port": "端口",
        "chk_auto_service": "打开面板时自动启动服务",
        "chk_autostart_os": "开机自动启动（静默进托盘）",
        "cfg_title": "远程桌面配置",
        "lbl_monitor": "屏幕", "mon_primary": "主屏", "mon_all": "全部屏幕拼合",
        "mon_n": "屏幕{n}  {w}x{h}", "mon_n_plain": "屏幕{n}",
        "chk_webrtc": "启用 WebRTC（H.264 视频 + 系统音频通道）",
        "lbl_fps": "帧率 fps", "lbl_bitrate": "H.264 码率 Mbps",
        "lbl_quality": "JPEG 质量", "lbl_scale": "JPEG 缩放",
        "hint_jpeg": "（帧率/质量/缩放作用于 JPEG 兼容通道）",
        "log_title": "服务日志",
        "qr_off": "服务未运行\n启动后此处自动显示二维码\n（扫一次即可直连）",
        "qr_need_lib": "二维码需 qrcode 库:\npip install qrcode",
        "qr_fail": "二维码生成失败: {e}",
        "info_off": "  （服务未启动，无可用地址）",
        "info_no_ip": "http://<本机IP>:{port}",
        "pin_pending": "（启动后自动生成）",
        "st_stopped": "已停止", "st_connected": "设备已连接", "st_disconnected": "设备未连接",
        "st_running": "运行中·{conn}",
        "note_taken": "服务已在运行（可能是手动启动的实例，面板已接管）",
        "starting": "启动中…", "stopping": "停止中…", "restarting": "重启中…",
        "note_start_timeout": "启动超时：端口可能被占用，详见下方日志",
        "note_stopped": "服务已停止",
        "note_stop_failed": "停止失败，可在任务管理器结束该 PID",
        "note_restarting": "正在重启服务…",
        "note_restarted": "服务已重启",
        "note_restart_unresponsive": "重启后未响应，详见日志",
        "note_no_listener": "没有找到监听该端口的进程",
        "note_not_running": "服务当前未运行，无需重启",
        "note_saved_live": "已自动保存 · 新连接即时生效",
        "note_saved_restart": "已自动保存 · 点上方「重启服务」后生效",
        "err_port": "端口需为 1024~65535 的整数",
        "err_ranges": "帧率 5-30 / 质量 20-95 / 缩放 0.3-1 / 码率 1-20 Mbps",
        "err_pin_empty": "勾选「固定」时验证码不能留空（想每次随机请取消勾选）",
        "mb_stop_title": "停止服务",
        "mb_stop_body": "将结束监听端口 {port} 的进程（PID {pid}），所有远程连接会断开。确定？",
        "mb_restart_title": "重启服务",
        "mb_restart_body": "将重启服务（端口 {port}），所有远程连接会断开。确定？",
        "mb_quit_title": "退出 WebDesk",
        "mb_quit_body": "服务正在运行，所有远程连接会断开。确定退出？",
        "tray_show": "显示面板", "tray_quit": "退出 WebDesk",
        "tray_notify": "已缩到托盘后台运行，双击图标恢复；远程连接不受影响。",
        "note_boot_on": "已设置开机自动启动，下次开机静默进托盘",
        "note_boot_off": "已取消开机自动启动",
        "err_boot_reg": "写入系统启动项失败（HKCU 注册表 Run 键）",
        "note_panel_spawn_fail": "[面板] 无法启动服务: {e}",
    },
    "en": {
        "title": "WebDesk Control Panel",
        "status_checking": "Checking…",
        "btn_start": "Start", "btn_stop": "Stop", "btn_restart": "Restart",
        "hint_restart": "Changes apply after you restart the service",
        "info_frame": "Connection info (phone/tablet must share this Wi-Fi/LAN)",
        "info_hint": "Scan the QR code, or enter a URL below",
        "info_one_client": "One device at a time",
        "lbl_pin": "Passcode", "chk_fixed": "Fixed",
        "hint_pin": "Uncheck Fixed for a random 6-digit code at each start",
        "lbl_default_view": "Default view", "view_touch": "Touch (screen)", "view_kb": "Keyboard",
        "lbl_port": "Port",
        "chk_auto_service": "Auto-start service when panel opens",
        "chk_autostart_os": "Start with Windows (silent, to tray)",
        "cfg_title": "Remote desktop settings",
        "lbl_monitor": "Screen", "mon_primary": "Primary", "mon_all": "All screens (combined)",
        "mon_n": "Screen {n}  {w}x{h}", "mon_n_plain": "Screen {n}",
        "chk_webrtc": "Enable WebRTC (H.264 video + system audio)",
        "lbl_fps": "Frame rate fps", "lbl_bitrate": "H.264 bitrate Mbps",
        "lbl_quality": "JPEG quality", "lbl_scale": "JPEG scale",
        "hint_jpeg": "(fps/quality/scale apply to the JPEG fallback channel)",
        "log_title": "Service log",
        "qr_off": "Service not running\nQR code appears here after start\n(one scan connects directly)",
        "qr_need_lib": "QR code needs the qrcode library:\npip install qrcode",
        "qr_fail": "QR generation failed: {e}",
        "info_off": "  (service stopped — no URLs available)",
        "info_no_ip": "http://<PC-IP>:{port}",
        "pin_pending": "(generated at startup)",
        "st_stopped": "Stopped", "st_connected": "device connected", "st_disconnected": "no device",
        "st_running": "Running·{conn}",
        "note_taken": "Service already running (a manually started instance was adopted)",
        "starting": "Starting…", "stopping": "Stopping…", "restarting": "Restarting…",
        "note_start_timeout": "Start timed out: port may be occupied — see the log below",
        "note_stopped": "Service stopped",
        "note_stop_failed": "Stop failed; kill the PID in Task Manager",
        "note_restarting": "Restarting service…",
        "note_restarted": "Service restarted",
        "note_restart_unresponsive": "No response after restart — see the log",
        "note_no_listener": "No process is listening on that port",
        "note_not_running": "Service is not running; nothing to restart",
        "note_saved_live": "Saved · applies to new connections",
        "note_saved_restart": "Saved · click Restart above to apply",
        "err_port": "Port must be an integer from 1024 to 65535",
        "err_ranges": "fps 5-30 / quality 20-95 / scale 0.3-1 / bitrate 1-20 Mbps",
        "err_pin_empty": "Passcode can't be empty while Fixed is checked (uncheck it for a random code)",
        "mb_stop_title": "Stop service",
        "mb_stop_body": "This kills the process listening on port {port} (PID {pid}); all remote sessions will drop. Continue?",
        "mb_restart_title": "Restart service",
        "mb_restart_body": "The service on port {port} will restart; all remote sessions will drop. Continue?",
        "mb_quit_title": "Quit WebDesk",
        "mb_quit_body": "The service is running; quitting stops it and drops all remote sessions. Quit?",
        "tray_show": "Show Panel", "tray_quit": "Quit WebDesk",
        "tray_notify": "Running in the tray — double-click the icon to restore. Remote sessions stay connected.",
        "note_boot_on": "Auto-start enabled; WebDesk will join the tray silently at boot",
        "note_boot_off": "Auto-start removed",
        "err_boot_reg": "Failed to write the autostart registry value (HKCU Run key)",
        "note_panel_spawn_fail": "[Panel] Failed to start service: {e}",
    },
}

_lang = "zh"


def system_lang():
    """首启默认语言：系统 UI 语言是中文→zh，否则 en。"""
    try:
        import ctypes
        if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04:  # LANG_CHINESE
            return "zh"
        return "en"
    except Exception:
        return "zh"


def set_lang(lang):
    global _lang
    if lang in STR:
        _lang = lang


def get_lang():
    return _lang


def t(key, **kw):
    s = STR[_lang].get(key) or STR["zh"].get(key) or key
    return s.format(**kw) if kw else s
