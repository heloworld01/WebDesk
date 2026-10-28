"""WebRTC 视频通道：屏幕画面经 H.264 (libx264) 编码推流到浏览器。

设计：
  - 视频走 WebRTC（浏览器自适应带宽估计），输入控制仍走原 /ws WebSocket
  - 信令用简单 HTTP：POST /rtc/offer {sdp,type} -> {sdp,type}
  - aiortc 作为被叫(answerer)，仅一个 recvonly 视频上行轨道
"""
import asyncio
import fractions
import itertools
import time

import av
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.codecs import h264 as h264mod
from starlette.requests import Request
from starlette.responses import JSONResponse

import aiortc as _aiortc

# ---------------------------------------------------------------------------
# 补丁：aiortc 硬编码的 x264 参数缺少 preset，默认 medium 在 1080p 实时编码太慢。
# 替换 _encode_frame，仅在 options 中加入 preset/slice-threading。
# ---------------------------------------------------------------------------
_X264_OPTIONS = {
    "preset": "ultrafast",
    "tune": "zerolatency",
    "level": "31",
    "slice-threading": "1",
    # 花屏缓解：aiortc 无 NACK/FEC 重传，丢包造成的坏块会一直持续到下一个关键帧。
    # 默认 gop=12 但 scenecut 可能拉长；显式钉住关键帧间隔≈1s，坏块最多存活 1 秒。
    "keyint": "24",
    "min-keyint": "12",
}


_PATCHED_VERSIONS = ("1.15", "1.9")  # 1.9.x = Win7 构建（Py3.8 上限），_encode_frame 签名与 1.15 一致


def _patch_h264_encoder():
    if not _aiortc.__version__.startswith(_PATCHED_VERSIONS):
        print(f"[WebDesk] aiortc {_aiortc.__version__} 非 {_PATCHED_VERSIONS}，跳过编码器补丁（速度可能偏低）")
        return

    enc_cls = h264mod.H264Encoder

    def _encode_frame(self, frame, force_keyframe):
        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate > 0.1
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I
        else:
            frame.pict_type = av.video.frame.PictureType.NONE

        if self.codec is None:
            self.codec = av.CodecContext.create("libx264", "w")
            self.codec.width = frame.width
            self.codec.height = frame.height
            self.codec.bit_rate = self.target_bitrate
            self.codec.pix_fmt = "yuv420p"
            self.codec.framerate = fractions.Fraction(h264mod.MAX_FRAME_RATE, 1)
            self.codec.time_base = fractions.Fraction(1, h264mod.MAX_FRAME_RATE)
            self.codec.options = dict(_X264_OPTIONS)
            self.codec.profile = "Baseline"

        data_to_send = b""
        for package in self.codec.encode(frame):
            data_to_send += bytes(package)
        if data_to_send:
            yield from self._split_bitstream(data_to_send)

    enc_cls._encode_frame = _encode_frame


_patch_h264_encoder()


# ---------------------------------------------------------------------------
# 屏幕视频轨道
# ---------------------------------------------------------------------------
class ScreenTrack:
    """由外部构造：asyncio 侧按帧序号从 ScreenCapture 拉原始帧。"""

    def __new__(cls, capture, fps=20):
        from aiortc import MediaStreamTrack

        class _Track(MediaStreamTrack):
            kind = "video"

            def __init__(self):
                super().__init__()
                self._cap = capture
                self._fps = max(5, min(30, int(fps)))
                self._t0 = time.monotonic()
                self._last_seq = -1
                self._released = False
                capture.add_raw_listener()

            def release(self):
                """幂等释放（aiortc 不会 await track.stop()，清理必须由外部驱动）。"""
                if not self._released:
                    self._released = True
                    self._cap.remove_raw_listener()

            async def recv(self):
                pts = int((time.monotonic() - self._t0) * 90000)
                while True:
                    seq, arr = self._cap.get_raw()
                    if arr is not None and seq != self._last_seq:
                        self._last_seq = seq
                        break
                    await asyncio.sleep(1.0 / self._fps)
                frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
                frame.pts = pts
                frame.time_base = fractions.Fraction(1, 90000)
                return frame

            def stop(self):
                # aiortc 同步调用 track.stop()：这里绝不能用 async 覆写
                # （协程不会被 await → RuntimeWarning，且基类 stop 也漏跑）
                super().stop()
                self.release()

        return _Track()


# ---------------------------------------------------------------------------
# 信令
# ---------------------------------------------------------------------------
class RtcHub:
    def __init__(self, capture, config, gate=None):
        self.capture = capture
        self.config = config
        self.gate = gate          # main.SessionGate：RTC 流必须属于当前唯一 WS 会话
        self.pcs = set()
        self._ids = itertools.count(1)

    async def handle_offer(self, request: Request):
        # 单连接闸门：sid 不属于当前会话则拒绝（防第二个设备绕过 WS 直接拉流/音频）
        if self.gate is not None:
            sid = request.query_params.get("sid", "")
            if not self.gate.claims(sid):
                if not sid or self.gate.sid is None:
                    # 页面还没通过验证码，或连接曾断开导致会话已作废
                    return JSONResponse({"error": "会话未验证或已过期，请先通过验证码后重试"},
                                        status_code=401)
                return JSONResponse({"error": "已有其他设备正在连接"}, status_code=403)
        try:
            body = await request.json()
            remote = RTCSessionDescription(sdp=body["sdp"], type=body["type"])
            assert remote.type == "offer"
        except Exception:
            return JSONResponse({"error": "offer 格式错误"}, status_code=400)

        import time as _t
        print(f"[WebDesk] {_t.strftime('%H:%M:%S')} /rtc/offer 收到: "
              f"audio={'m=audio' in remote.sdp} video={'m=video' in remote.sdp}", flush=True)

        bitrate = int(self.config.get("bitrate", 6000000))
        fps = int(self.config.get("fps", 15))

        pc = RTCPeerConnection()
        self.pcs.add(pc)
        tracks = []

        def release_tracks():
            # aiortc 关闭发件轨道时不 await track.stop()，这里统一兜底释放引用计数
            for t in tracks:
                try:
                    t.release()
                except Exception:
                    pass

        @pc.on("connectionstatechange")
        async def on_state():
            if pc.connectionState in ("failed", "closed"):
                await pc.close()
                self.pcs.discard(pc)
                release_tracks()

        want_audio = "m=audio" in remote.sdp
        want_video = "m=video" in remote.sdp
        if want_audio:
            import audio
            if not audio.audio_available():
                await pc.close()
                self.pcs.discard(pc)
                return JSONResponse({"error": "未找到系统音频回环设备"}, status_code=400)

        try:
            if want_audio:
                import audio
                atrack = audio.DesktopAudioTrack()
                tracks.append(atrack)
                pc.addTrack(atrack)
            if want_video:
                track = ScreenTrack(self.capture, fps=fps)
                tracks.append(track)
                sender = pc.addTrack(track)
                # aiortc 未实现 W3C getParameters；尝试私有属性设置初始最大码率，失败则由带宽估计自适应
                try:
                    params = getattr(sender, "_parameters", None)
                    if params is not None and getattr(params, "encodings", None):
                        params.encodings[0].maxBitrate = bitrate
                except Exception:
                    pass

            await pc.setRemoteDescription(remote)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # 等待 ICE 收集完成（LAN 场景 host 候选通常 <0.5s）
            for _ in range(60):
                if pc.iceGatheringState == "complete":
                    break
                await asyncio.sleep(0.05)

            return JSONResponse({
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
            })
        except Exception as e:
            print(f"[WebDesk] /rtc/offer 处理失败: {type(e).__name__}: {e}", flush=True)
            await pc.close()
            self.pcs.discard(pc)
            release_tracks()
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

    async def shutdown(self):
        for pc in list(self.pcs):
            try:
                await pc.close()
            except Exception:
                pass
        self.pcs.clear()


def register(app, capture, config, gate=None):
    hub = RtcHub(capture, config, gate)
    app.state.rtc = hub

    @app.post("/rtc/offer")
    async def offer(request: Request):
        return await hub.handle_offer(request)
