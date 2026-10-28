"""屏幕捕获：独立线程按 fps 抓屏，JPEG 编码，变化检测跳帧。"""
import io
import threading
import time

import mss
import numpy as np
from PIL import Image, ImageChops


class ScreenCapture:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._latest = None          # jpeg bytes
        self.seq = 0                 # 帧序号，客户端据此判断是否有新帧
        self.size = (0, 0)           # 桌面像素尺寸 (w, h)
        self._raw = None             # 原始 RGB ndarray（全分辨率，供 WebRTC）
        self._raw_seq = -1
        self._raw_listeners = 0      # >0 时才发布原始帧，避免无谓拷贝
        self._jpeg_listeners = 0     # 两类监听都为 0 时抓屏线程休眠（零连接≈零占用）
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ready.wait(10)

    def get_frame(self):
        with self._lock:
            return self.seq, self._latest

    def get_raw(self):
        with self._lock:
            return self._raw_seq, self._raw

    def add_raw_listener(self):
        with self._lock:
            self._raw_listeners += 1

    def remove_raw_listener(self):
        with self._lock:
            self._raw_listeners = max(0, self._raw_listeners - 1)

    def add_jpeg_listener(self):
        with self._lock:
            self._jpeg_listeners += 1

    def remove_jpeg_listener(self):
        with self._lock:
            self._jpeg_listeners = max(0, self._jpeg_listeners - 1)

    def set(self, **kw):
        """运行时调整 fps / quality / scale。"""
        for k in ("fps", "quality", "scale"):
            if k in kw:
                self.cfg[k] = kw[k]

    def stop(self):
        self._stop.set()

    def _pick_monitor(self, sct):
        """cfg["monitor"]: "primary"=主屏(左上角为0,0) / "all"=全部拼合 / 数字=序号."""
        monitors = sct.monitors
        m = self.cfg.get("monitor", "primary")
        if m in ("primary", None):
            for i, mm in enumerate(monitors):
                if i and mm["left"] == 0 and mm["top"] == 0:
                    return mm
            return monitors[1]
        if m == "all":
            return monitors[0]
        idx = int(m)
        return monitors[idx] if 0 <= idx < len(monitors) else monitors[1]

    def _loop(self):
        prev_scaled = None
        prev_scale = None
        grab_fails = 0
        with mss.mss() as sct:
            mon = self._pick_monitor(sct)
            self.size = (mon["width"], mon["height"])
            self._ready.set()   # 尺寸就绪即可放行启动（无消费者休眠态下不会立刻抓帧）
            while not self._stop.is_set():
                t0 = time.perf_counter()
                with self._lock:
                    want = self._raw_listeners > 0 or self._jpeg_listeners > 0
                if not want:
                    # 无任何消费者（设备未连接/键盘视图/纯 RTC 之外的空档）：休眠不抓屏。
                    # 清掉差分基准，保证恢复后第一帧必定编码 → 新客户端立刻拿到首帧
                    prev_scaled = None
                    self._stop.wait(0.25)
                    continue
                try:
                    tg = time.perf_counter()
                    shot = sct.grab(mon)
                    dt_grab = time.perf_counter() - tg
                    if dt_grab > 2:
                        # 冷启动/显示器休眠时 grab 可能长时间卡顿 → 用户端表现为黑屏，
                        # 留痕便于定位；正常情况此日志不出现
                        print(f"[capture] grab 耗时 {dt_grab:.1f}s（异常缓慢）", flush=True)
                    grab_fails = 0
                except Exception as e:
                    grab_fails += 1
                    if grab_fails in (1, 20, 60):
                        print(f"[capture] grab 连续失败第 {grab_fails} 次: {type(e).__name__}: {e}", flush=True)
                    time.sleep(0.5)
                    continue
                pil = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                self.size = shot.size
                self._ready.set()

                # WebRTC 原始帧（有活跃轨道时才生成，全分辨率）
                with self._lock:
                    want_raw = self._raw_listeners > 0
                if want_raw:
                    with self._lock:
                        self._raw = np.asarray(pil)
                        self._raw_seq += 1

                scale = max(0.1, min(1.0, float(self.cfg.get("scale", 0.6))))
                if scale < 1.0:
                    pil = pil.resize(
                        (max(1, round(pil.width * scale)), max(1, round(pil.height * scale))),
                        Image.BILINEAR,
                    )

                # 同尺度全分辨率差分：画面无变化则跳过编码与发送
                if prev_scaled is not None and scale == prev_scale \
                        and ImageChops.difference(pil, prev_scaled).getbbox() is None:
                    delay = 1.0 / max(1, int(self.cfg.get("fps", 15))) - (time.perf_counter() - t0)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                prev_scaled = pil.copy()
                prev_scale = scale
                quality = max(10, min(95, int(self.cfg.get("quality", 60))))
                buf = io.BytesIO()
                pil.save(buf, "JPEG", quality=quality, optimize=False)
                data = buf.getvalue()

                with self._lock:
                    self._latest = data
                    self.seq += 1

                delay = 1.0 / max(1, int(self.cfg.get("fps", 15))) - (time.perf_counter() - t0)
                if delay > 0:
                    time.sleep(delay)
