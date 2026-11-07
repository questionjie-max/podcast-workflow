"""豆包语音合成大模型 TTS(单人朗读用,WebSocket 双向流式 v3)。

与方舟播客(volc_podcast.py)同一控制台、同一把 X-Api-Key,但资源不同:
- X-Api-Resource-Id: seed-tts-2.0(豆包语音合成模型 2.0)或 seed-tts-1.0
- 端点:wss://openspeech.bytedance.com/api/v3/tts/bidirection
- 会话流程:StartConnection(1) → ConnectionStarted(50) → StartSession(100,
  req_params 携带 text/speaker/audio_params) → SessionStarted(150) →
  FinishSession(102) → TTSResponse(352,音频) → SessionFinished(152) →
  FinishConnection(2) → ConnectionFinished(52)

帧编解码与 volc_podcast 共用同一套二进制协议(本文件复用其工具函数)。
状态:协议按文档实现 + 编解码单测;**真实合成待用户在控制台开通「语音合成
大模型」后联调**。
"""

import asyncio
from pathlib import Path

from .base import TTSProvider, VoiceSpec
from .volc_podcast import (
    EV_CONNECTION_STARTED,
    EV_FINISH_CONNECTION,
    EV_SESSION_FINISHED,
    EV_SESSION_STARTED,
    EV_START_CONNECTION,
    EV_START_SESSION,
    TTSError,
    build_client_frame,
    build_connection_frame,
    build_finish_session,
    parse_server_frame,
)

WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

# 精选音色(官方大模型音色列表;均可待联调后按音色列表文档扩充)
class VolcSpeechTTS(TTSProvider):
    BUILTIN_VOICES = [
        {"ShortName": "zh_female_xiaohe_uranus_bigtts", "Friendly": "小何 · 女声 · 2.0(推荐)"},
        {"ShortName": "zh_female_vv_uranus_bigtts", "Friendly": "Vv · 女声 · 2.0(推荐)"},
        {"ShortName": "zh_female_shuangkuaisisi_moon_bigtts", "Friendly": "爽快思思 · 女声 · 1.0"},
        {"ShortName": "zh_female_cancan_mars_bigtts", "Friendly": "灿灿 · 女声 · 1.0"},
        {"ShortName": "zh_male_ahu_conversation_wvae_bigtts", "Friendly": "阿虎 · 男声 · 1.0"},
    ]
    name = "volcspeech"
    supports_emotion = False
    supports_clone = False

    def __init__(self, cfg: dict | None = None):
        # 与方舟播客共用同一把语音技术 X-Api-Key;资源 ID 独立可配
        # resource_id="auto"(默认):按音色版本自动选择(2.0/1.0 音色对应不同资源)
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.resource_id = cfg.get("resource_id") or "auto"
        self.ws_url = cfg.get("ws_url") or WS_URL
        # 语速稳定指令(2.0 音色自带表现力,语速会随内容起伏;按基调给出锚定)
        self.pace_hint = cfg.get("pace_hint") or ""
        self.last_fallback: str | None = None

    @staticmethod
    def _context_texts(pace_hint: str) -> list[str] | None:
        """语速稳定指令(文档:context_texts 仅 2.0 音色支持)。"""
        return [pace_hint] if pace_hint else None

    def _resource_candidates(self, voice_id: str) -> list[str]:
        """资源候选顺序:音色名映射优先,其次显式配置,最后两个版本都试。

        豆包音色分两代:uranus/v3 系为 2.0 音色;moon/mars/saturn 系为 1.0 音色。
        资源与音色版本不匹配时服务端报 55000000 "resource ID is mismatched"。
        """
        configured = self.resource_id if self.resource_id != "auto" else None
        low = (voice_id or "").lower()
        auto = "seed-tts-2.0" if ("uranus" in low or "v3" in low) else "seed-tts-1.0"
        cands: list[str] = []
        for r in (auto, configured, "seed-tts-2.0", "seed-tts-1.0"):
            if r and r not in cands:
                cands.append(r)
        return cands

    async def synth(
        self,
        text: str,
        voice: VoiceSpec,
        style: str | None,
        *,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        out: Path,
    ) -> None:
        last_err: Exception | None = None
        for rid in self._resource_candidates(voice.voice_id):
            try:
                return await self._synth_once(text, voice, style, rate=rate, pitch=pitch,
                                              out=out, resource_id=rid)
            except TTSError as e:
                # 资源/音色版本不匹配 → 换下一资源版本重试;其余错误直接抛出
                marker = str(e)
                if any(k in marker for k in ("mismatched", "not granted", "未授权")):
                    last_err = e
                    continue
                raise
        # 候选资源耗尽:若失败原因是"音色版本与资源不匹配",自动回退到
        # 已授权资源对应的默认音色(用户选的音色暂不可用 → 换可用的,不阻塞出片)
        if last_err and any(k in str(last_err) for k in ("mismatched", "not granted", "未授权")):
            fallback = next((v["ShortName"] for v in self.BUILTIN_VOICES
                             if "uranus" in v["ShortName"]), None)
            if fallback and fallback != voice.voice_id:
                try:
                    rid = self._resource_candidates(fallback)[0]
                    await self._synth_once(text, VoiceSpec(provider="volcspeech",
                                                           voice_id=fallback),
                                           style, rate=rate, pitch=pitch,
                                           out=out, resource_id=rid)
                    self.last_fallback = f"所选音色资源未授权,已自动换用 {fallback}"
                    return  # 回退成功(调用方可在日志中看到替换说明)
                except TTSError:
                    pass
            raise TTSError(
                f"所选音色与已开通的合成服务不匹配,且自动回退失败:{last_err}",
                kind="auth") from last_err
        if last_err:
            raise last_err
        raise TTSError("合成失败:无可用资源", kind="other")

    async def _synth_once(
        self,
        text: str,
        voice: VoiceSpec,
        style: str | None,
        *,
        rate: str,
        pitch: str,
        out: Path,
        resource_id: str,
    ) -> None:
        import aiohttp

        if not self.api_key:
            raise TTSError(
                "未配置语音技术 API Key(设置 → TTS 接口配置 → 方舟播客,同一把 Key)",
                kind="config")

        # 语速换算:edge 风格 "+10%" → 文档取值 [-50, 100]
        try:
            speech_rate = max(-50, min(100, int(float(rate.strip().rstrip("%")))))
        except (ValueError, AttributeError):
            speech_rate = 0

        session_id = uuid_str()
        # 双向流式协议:StartSession 只带会话参数(音色/音频参数),
        # 文本通过 TaskRequest(200) 单独发送(文档最佳实践)
        req_params = {
            "speaker": voice.voice_id,
            "audio_params": {"format": "mp3", "sample_rate": 24000,
                             "speech_rate": speech_rate},
        }
        ctx = self._context_texts(self.pace_hint)
        if ctx:
            # 文档:type 是 jsonstring——必须序列化为字符串,不能传对象
            import json as _json

            req_params["additions"] = _json.dumps({"context_texts": ctx})
        task_payload = {"event": 200, "req_params": {"text": text}}

        headers = {"X-Api-Key": self.api_key, "X-Api-Resource-Id": resource_id}
        audio = bytearray()
        timeout = aiohttp.ClientTimeout(total=300, connect=15)
        loop = asyncio.get_event_loop()
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, \
                    session.ws_connect(self.ws_url, headers=headers) as ws:
                # 连接阶段
                await ws.send_bytes(build_connection_frame(EV_START_CONNECTION))
                conn_ok = False
                dl = loop.time() + 30
                while not conn_ok:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=max(1, dl - loop.time()))
                    if msg.type != aiohttp.WSMsgType.BINARY:
                        continue
                    frame = parse_server_frame(msg.data)
                    if frame["error_code"] is not None:
                        self._raise_by_code(frame["error_code"], frame["error_msg"])
                    if frame["event"] == EV_CONNECTION_STARTED:
                        conn_ok = True
                # 会话阶段:StartSession(100) → SessionStarted(150)
                # → TaskRequest(200,文本) → FinishSession(102) → 收音频(352)
                await ws.send_bytes(build_client_frame(
                    EV_START_SESSION, session_id,
                    {"user": {"uid": "podcast-studio"}, "event": EV_START_SESSION,
                     "req_params": req_params}))
                started = False
                dl = loop.time() + 30
                while not started:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=max(1, dl - loop.time()))
                    if msg.type != aiohttp.WSMsgType.BINARY:
                        continue
                    frame = parse_server_frame(msg.data)
                    if frame["error_code"] is not None:
                        self._raise_by_code(frame["error_code"], frame["error_msg"])
                    if frame["event"] == EV_SESSION_STARTED:
                        started = True
                # 文本经 TaskRequest 发送
                await ws.send_bytes(build_client_frame(
                    200, session_id, task_payload))
                # 声明输入结束,服务端开始收尾合成
                await ws.send_bytes(build_finish_session(session_id))
                # 收音频:TTSResponse(352),直到 SessionFinished(152)
                dl = loop.time() + 300
                while True:
                    if loop.time() > dl:
                        raise TTSError("豆包 TTS 会话超时(300s)", kind="network")
                    try:
                        msg = await asyncio.wait_for(
                            ws.receive(), timeout=min(60, max(1, dl - loop.time())))
                    except (asyncio.TimeoutError, TimeoutError) as e:
                        raise TTSError("服务端 60s 内无数据,会话中断", kind="network") from e
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    if msg.type != aiohttp.WSMsgType.BINARY:
                        continue
                    frame = parse_server_frame(msg.data)
                    if frame["error_code"] is not None:
                        self._raise_by_code(frame["error_code"], frame["error_msg"])
                    ev = frame["event"]
                    if ev == 352 and isinstance(frame["payload"], bytes):
                        audio.extend(frame["payload"])
                    elif ev == EV_SESSION_FINISHED:
                        await ws.send_bytes(build_connection_frame(EV_FINISH_CONNECTION))
                        break
        except TTSError:
            raise
        except aiohttp.WSServerHandshakeError as e:
            if e.status == 403:
                # 带 "not granted" 标记: synth 的资源重试逻辑据此换版本再试
                raise TTSError(
                    "语音合成资源未授权(not granted):请在控制台 → 语音技术 → 开通管理,"
                    "开通与所选音色版本对应的字符版商品(2.0 音色配 2.0 字符版,1.0 同理),"
                    "开通后无需重填直接重试", kind="auth")
            if e.status == 401:
                raise TTSError("API Key 无效(HTTP 401)", kind="auth")
            kind = "http"  # 401 已在上面抛出,这里只剩 4xx/5xx 其余状态
            raise TTSError(f"豆包 TTS 建连失败(HTTP {e.status}):{e.message[:150]}", kind=kind)
        except aiohttp.ClientConnectionError as e:
            raise TTSError(f"连不上豆包 TTS 服务:{e}", kind="network")

        if not audio:
            raise TTSError("豆包 TTS 未返回音频数据", kind="audio")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(audio))


def uuid_str() -> str:
    import uuid

    return uuid.uuid4().hex
