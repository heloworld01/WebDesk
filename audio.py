"""系统音频回环捕获（WASAPI loopback）→ WebRTC 音频轨道（Opus 48k 立体声）。

设计：
  - 默认不启动。浏览器用独立的 RTCPeerConnection（仅 m=audio）发 offer，
    服务端检测到音频 m 线才挂上本轨道 —— 音频开关即建/拆这条独立连接，
    不影响视频通道（aiortc 不擅长 renegotiation）。
  - AudioHub 进程级单例：一个 PyAudio 回调流 + PCM 环形缓冲，多听众共享；
    听众引用计数归零即停流（默认关闭时零占用）。
  - 直播语义：缓冲超过约 330ms 丢最旧数据，不积压。
"""
import asyncio
import fractions
import threading
import time

import numpy as np
from aiortc import MediaStreamTrack

SAMPLE_RATE = 48000
CHANNELS = 2
CHUNK_SAMPLES = 960                          # 20ms
CHUNK_BYTES = CHUNK_SAMPLES * CHANNELS * 2   # 3840B
MAX_BUFFER = SAMPLE_RATE * CHANNELS * 2 // 3 * 2  # ≈666ms 上限，超出丢旧


class AudioHub:
    """单例：WASAPI loopback 回调流 → 48k/2ch/s16 交织 PCM 环形缓冲。"""

    def __init__(self):
        import pyaudiowpatch as pa
        p = pa.PyAudio()
        try:
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            # 回环设备名 = 播放设备名 + " [Loopback]" 后缀
            name = out["name"]
            dev = out if out.get("isLoopbackDevice") else next(
                (lb for lb in p.get_loopback_device_info_generator()
                 if lb["name"] == f"{name} [Loopback]"
                 or lb["name"].startswith(name)), None)
        except Exception:
            p.terminate()
            raise
        if dev is None:
            p.terminate()
            raise RuntimeError("未找到 WASAPI loopback 设备")
        self._pa = p
        self._dev = dev
        self._rate = int(dev["defaultSampleRate"])
        self._ch = max(1, min(2, int(dev["maxInputChannels"])))
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._users = 0
        self._stream = None
        self._open_ts = 0.0          # 本次开流时刻（看门狗用）
        self._got_data = False       # 本次开流后回调是否写入过数据
        self._reopened = False       # 本次冷启动是否已重开过流
        self._resampler = None
        if self._rate != SAMPLE_RATE or self._ch != CHANNELS:
            import av
            self._resampler = av.AudioResampler(
                format="s16", layout="stereo", rate=SAMPLE_RATE)

    # ---- PyAudio 回调线程（实时时钟驱动） ----
    def _callback(self, in_data, frame_count, time_info, status_flags):
        data = in_data
        if self._resampler is not None:
            import av
            f = av.AudioFrame.from_ndarray(
                np.frombuffer(in_data, np.int16).reshape(1, -1).copy(),
                format="s16", layout="stereo" if self._ch == 2 else "mono")
            f.sample_rate = self._rate
            try:
                data = b"".join(bytes(r.to_ndarray().tobytes())
                                for r in self._resampler.resample(f))
            except Exception:
                data = b""
        if data:
            with self._lock:
                self._got_data = True
                self._buf += data
                overflow = len(self._buf) - MAX_BUFFER
                if overflow > 0:
                    del self._buf[:overflow]      # 直播语义：丢旧不积压
        return (None, 0)

    # ---- 流生命周期（_open_stream 必须持锁调用；关闭务必放锁外，
    #      stop_stream 会等回调线程返回，而回调还要拿 _lock，持锁关闭会死锁） ----
    def _open_stream(self):
        self._stream = self._pa.open(
            format=self._pa.get_format_from_width(2),
            channels=self._ch, rate=self._rate,
            input=True, input_device_index=self._dev["index"],
            frames_per_buffer=CHUNK_SAMPLES,
            stream_callback=self._callback)
        self._stream.start_stream()
        self._open_ts = time.monotonic()
        self._got_data = False
        self._reopened = False

    def start(self):
        with self._lock:
            self._users += 1
            if self._stream is None:
                self._open_stream()

    def stop(self):
        with self._lock:
            self._users = max(0, self._users - 1)
            stream = None
            if self._users == 0 and self._stream is not None:
                stream, self._stream = self._stream, None
                self._buf.clear()
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def check_stream(self):
        """看门狗（轨道轮询时顺带调用）：刚开流 2.5 秒内回调从未写过数据
        且还没重试过 → 重开一次流。只在"从未收到数据"时触发，避免把
        桌面安静（loopback 本就暂停投递）误判成流故障。"""
        with self._lock:
            if (self._stream is None or self._got_data or self._reopened
                    or time.monotonic() - self._open_ts < 2.5):
                return
            self._reopened = True
            stream, self._stream = self._stream, None
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        with self._lock:
            if self._users > 0 and self._stream is None:
                self._open_stream()

    def read_chunk(self):
        with self._lock:
            if len(self._buf) < CHUNK_BYTES:
                return None
            data = bytes(self._buf[:CHUNK_BYTES])
            del self._buf[:CHUNK_BYTES]
            return data


_hub = None
_hub_lock = threading.Lock()


def get_hub():
    """懒初始化；无回环设备时返回 None（并记住失败，避免反复探测）。"""
    global _hub
    with _hub_lock:
        if _hub is None:
            try:
                _hub = AudioHub()
            except Exception as e:
                print("[WebDesk] 音频不可用:", e)
                _hub = False
        return _hub or None


class DesktopAudioTrack(MediaStreamTrack):
    """服务端发送轨道：20ms/帧，pts 按墙钟起算后顺序 +960。"""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._hub = get_hub()
        if self._hub is None:
            raise RuntimeError("音频设备不可用")
        self._hub.start()
        self._t0 = time.monotonic()
        self._pts = None
        self._released = False

    def release(self):
        """幂等释放（aiortc 不会 await track.stop()，清理必须由外部驱动）。"""
        if not self._released:
            self._released = True
            self._hub.stop()

    async def recv(self):
        import av
        starved = 0
        while True:
            data = self._hub.read_chunk()
            if data is not None:
                break
            await asyncio.sleep(0.005)
            starved += 1
            if starved % 50 == 0:          # 每 0.25s 顺带巡一次流健康
                self._hub.check_stream()
        if self._pts is None:
            self._pts = int((time.monotonic() - self._t0) * SAMPLE_RATE)
        else:
            self._pts += CHUNK_SAMPLES
        frame = av.AudioFrame.from_ndarray(
            np.frombuffer(data, np.int16).reshape(1, -1),
            format="s16", layout="stereo")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        return frame

    async def stop(self):
        await super().stop()
        self.release()


def audio_available():
    return get_hub() is not None
