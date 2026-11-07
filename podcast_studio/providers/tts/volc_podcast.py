"""方舟播客 TTS(火山豆包播客语音合成,WebSocket v3 二进制协议)。

文档:https://docs.volcengine.com/docs/doubao-voice/podcast-api-websocket-v3protocol
- 端点:wss://openspeech.bytedance.com/api/v3/sami/podcasttts
- 认证(建连 Header):X-Api-Key(控制台 API Key)+ X-Api-Resource-Id
  (播客合成固定 volc.service_type.10050)+ X-Api-Request-Id(可选 uuid)
- 与豆包 TTS(volc.py,HTTP app_id/token)是**两个不同的服务**,凭据互不通用
- 直接产出双人对话播客音频:action=3 传对话轮次(nlp_texts,单轮 ≤300 字符),
  speaker_info.speakers 必须给 2 个发音人;音频分轮次流式返回(event 361)

协议要点(整数均大端):
- 帧头 4 字节:[0]=版本<<4|头长(恒 0x11) [1]=消息类型<<4|标志
  [2]=序列化<<4|压缩 [3]=保留 0
- 客户端控制帧:类型 0b1001、标志 0b0100(带 event 号):帧头后 uint32(event),
  uint32(len(session_id))+session_id, uint32(len(payload))+payload
- 下行事件:150 会话开始 / 360 轮开始 / 361 音频负载 / 362 轮结束 /
  363 播客结束(含 audio_url) / 152 会话结束 / 52 连接结束确认
- 错误帧:类型 0b1111,4~7 字节为错误码,payload 为 JSON 消息
- payload 可能 gzip 压缩(压缩标志 0b0001)

状态:帧编解码/负载构造已单测;WS 会话**待人工联调**(需要控制台 API Key)。
"""

import asyncio
import gzip
import struct
import uuid
from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec

WS_URL = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
RESOURCE_ID = "volc.service_type.10050"

# 默认发音人配对(官方推荐同系列搭配:黑猫侦探社 大先生 & 咪仔)
DEFAULT_SPEAKERS = [
    "zh_male_dayixiansheng_v2_saturn_bigtts",
    "zh_female_mizaitongxue_v2_saturn_bigtts",
]

# 事件码(连接生命周期:StartConnection 必须先于 StartSession,否则服务端静默丢帧)
EV_START_CONNECTION = 1
EV_CONNECTION_STARTED = 50
EV_START_SESSION = 100   # 上行;150 是下行 SessionStarted 确认(文档勘误:示例未标注上行号)
EV_SESSION_STARTED = 150
EV_ROUND_START = 360
EV_ROUND_AUDIO = 361
EV_ROUND_END = 362
EV_PODCAST_END = 363
EV_SESSION_FINISHED = 152
EV_FINISH_CONNECTION = 2
EV_CONNECTION_FINISHED = 52


class TTSError(RuntimeError):
    """kind ∈ config|auth|network|http|audio|limit。"""

    def __init__(self, msg: str, kind: str = "http", code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


# ---- 帧编解码(纯函数) ------------------------------------------------------

def build_client_frame(event: int, session_id: str, payload: dict) -> bytes:
    """构造客户端控制帧:0x11 0x14 0x10 0x00 + event + sid + payload(JSON 无压缩)。

    ⚠️ 文档勘误:官方 StartSession 示例把请求帧写成 0b1001(Full server
    response),但服务端实测拒绝 "unsupported message type (9)"——按火山
    二进制协议规范,客户端请求的消息类型是 0b0001,标志 0b0100(带 event 号)。
    """
    sid = session_id.encode("utf-8")
    body = json_dumps(payload).encode("utf-8")
    return b"".join([
        bytes([0b0001_0001, 0b0001_0100, 0b0001_0000, 0b0000_0000]),
        struct.pack(">I", event),
        struct.pack(">I", len(sid)), sid,
        struct.pack(">I", len(body)), body,
    ])


def build_finish_session(session_id: str) -> bytes:
    """FinishSession(102):声明输入结束。

    ⚠️ 文档勘误(实测):播客 API 的输入随 StartSession 一次性给出,但服务端
    仍会等 FinishSession 才收尾——不发它,所有轮次合成完后服务端静默挂起
    (实测 150s+ 无任何帧)。双向流式文档最佳实践:"在没有文本可以发送时,
    需立即发送 finish session"。
    """
    sid = session_id.encode("utf-8")
    return b"".join([
        bytes([0b0001_0001, 0b0001_0100, 0b0001_0000, 0b0000_0000]),
        struct.pack(">I", 102),
        struct.pack(">I", len(sid)), sid,
        struct.pack(">I", 2), b"{}",
    ])


def build_connection_frame(event: int) -> bytes:
    """连接类帧(StartConnection/FinishConnection):无 session_id,payload 仅 "{}"。"""
    body = b"{}"
    return b"".join([
        bytes([0b0001_0001, 0b0001_0100, 0b0001_0000, 0b0000_0000]),
        struct.pack(">I", event),
        struct.pack(">I", len(body)), body,
    ])


def build_finish_connection(session_id: str) -> bytes:
    """FinishConnection:标志位带 no-serialize(0b0000),payload 为空 JSON。"""
    sid = session_id.encode("utf-8")
    body = b"{}"
    return b"".join([
        bytes([0b0001_0001, 0b0001_0100, 0b0001_0000, 0b0000_0000]),
        struct.pack(">I", EV_FINISH_CONNECTION),
        struct.pack(">I", len(sid)), sid,
        struct.pack(">I", len(body)), body,
    ])


def parse_server_frame(data: bytes) -> dict:
    """解析下行帧。返回 {msg_type, flags, serialization, compressed, event,
    session_id, payload, error_code, error_msg}。字段缺失为 None。"""
    if len(data) < 4:
        raise ValueError("帧过短")
    msg_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compressed = data[2] & 0x0F
    out = {"msg_type": msg_type, "flags": flags,
           "serialization": serialization, "compressed": compressed,
           "event": None, "session_id": None, "payload": None,
           "error_code": None, "error_msg": None}
    pos = 4
    if msg_type == 0b1111:  # 错误帧:4~7 错误码 + JSON 消息
        if len(data) >= 8:
            out["error_code"] = struct.unpack(">I", data[4:8])[0]
        raw = data[8:]
        if compressed:
            raw = gzip.decompress(raw)
        out["error_msg"] = raw.decode("utf-8", "replace")
        return out
    if flags & 0b0100 and len(data) >= 8:  # 带 event 号
        out["event"] = struct.unpack(">I", data[4:8])[0]
        pos = 8
    else:
        return out
    if len(data) >= pos + 4:
        sid_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        out["session_id"] = data[pos:pos + sid_len].decode("utf-8", "replace")
        pos += sid_len
    if len(data) >= pos + 4:
        p_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        raw = data[pos:pos + p_len]
        if compressed:
            raw = gzip.decompress(raw)
        if serialization == 0b0001:
            try:
                out["payload"] = json_loads(raw)
            except ValueError:
                out["payload"] = raw.decode("utf-8", "replace")
        else:
            out["payload"] = raw  # 原始音频字节
    return out


def build_round_payload(*, text: str | None = None, speakers: list[str],
                        mp3: bool = True) -> dict:
    """构造 StartSession 负载。

    - 有 text:action=3,把文本按 ≤290 字符切成对话轮次(全部用第一个发音人)
    - 无 text:action=0,长文本总结生成双人播客(内容会被模型改写)
    """

    payload: dict = {"use_head_music": False,
                     "audio_config": {"format": "mp3" if mp3 else "pcm",
                                      "sample_rate": 24000, "speech_rate": 0}}
    if text:
        payload["action"] = 3
        payload["nlp_texts"] = [
            {"speaker": speakers[0], "text": piece}
            for piece in split_for_rounds(text, 290)
        ]
    else:
        payload["action"] = 0
    payload["speaker_info"] = {"random_order": False, "speakers": list(speakers[:2])}
    return payload


def split_for_rounds(text: str, max_chars: int = 290) -> list[str]:
    """按句末标点把文本切到单轮上限内(文档要求单轮 ≤300 字符)。"""
    import re

    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    buf = ""
    for part in re.split(r"(?<=[。！？!?…；;\n])", text):
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) <= max_chars:
            buf += part
            continue
        if buf:
            pieces.append(buf)
        buf = ""
        while len(part) > max_chars:
            pieces.append(part[:max_chars])
            part = part[max_chars:]
        buf = part
    if buf:
        pieces.append(buf)
    return pieces


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def json_loads(raw: bytes):
    import json

    return json.loads(raw.decode("utf-8"))


# ---- Provider ---------------------------------------------------------------

class VolcPodcastTTS(TTSProvider):
    name = "volcpodcast"
    supports_emotion = False   # 播客 API 无逐段风格指令;情绪由对话文本自身承载
    supports_clone = False

    BUILTIN_VOICES = [
        {"ShortName": "|".join(DEFAULT_SPEAKERS),
         "Friendly": "双人播客 · 大先生 & 咪仔(方舟,整期对话)"},
    ]

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.resource_id = cfg.get("resource_id") or RESOURCE_ID
        self.ws_url = cfg.get("ws_url") or WS_URL

    def _speakers(self, voice: VoiceSpec) -> list[str]:
        raw = (voice.voice_id or "").strip()
        if "|" in raw:
            return [x for x in raw.split("|") if x][:2]
        return list(DEFAULT_SPEAKERS[:2])

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
        import aiohttp

        if not self.api_key:
            raise TTSError(
                "未配置方舟播客 API Key(设置 → TTS 接口配置 → 方舟播客)",
                kind="config")

        session_id = uuid.uuid4().hex
        speakers = self._speakers(voice)
        payload = build_round_payload(text=text, speakers=speakers, mp3=True)

        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        audio = bytearray()
        finished = False
        timeout = aiohttp.ClientTimeout(total=600, connect=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, \
                    session.ws_connect(self.ws_url, headers=headers) as ws:
                    # 连接阶段:StartConnection → ConnectionStarted(50)后才能开会话
                    await ws.send_bytes(build_connection_frame(EV_START_CONNECTION))
                    conn_ok = False
                    deadline = asyncio.get_event_loop().time() + 30
                    while not conn_ok:
                        remaining = deadline - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            raise TTSError("连接握手超时(30s 内未收到 ConnectionStarted)", kind="network")
                        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                        if msg.type != aiohttp.WSMsgType.BINARY:
                            continue
                        frame = parse_server_frame(msg.data)
                        if frame["error_code"] is not None:
                            self._raise_by_code(frame["error_code"], frame["error_msg"])
                        if frame["event"] == EV_CONNECTION_STARTED:
                            conn_ok = True

                    await ws.send_bytes(build_client_frame(EV_START_SESSION, session_id, payload))

                    # 等 SessionStarted(150),随后立即 FinishSession(102)声明输入结束
                    started = False
                    dl2 = asyncio.get_event_loop().time() + 30
                    while not started:
                        remaining = dl2 - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            raise TTSError("会话启动超时(30s 内未收到 SessionStarted)", kind="network")
                        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                        if msg.type != aiohttp.WSMsgType.BINARY:
                            continue
                        frame = parse_server_frame(msg.data)
                        if frame["error_code"] is not None:
                            self._raise_by_code(frame["error_code"], frame["error_msg"])
                        if frame["event"] == EV_SESSION_STARTED:
                            started = True
                    await ws.send_bytes(build_finish_session(session_id))

                    deadline = asyncio.get_event_loop().time() + 570  # 总时长兜底
                    # 收包必须全程用 receive()(混用 async for 会让迭代器失效而静默挂起)
                    while True:
                        if asyncio.get_event_loop().time() > deadline:
                            raise TTSError("方舟播客会话超时(570s 无完成信号)", kind="network")
                        try:
                            # 120s 宽限:轮次间服务端可能有合成间隙;超时转可读错误
                            # (不能让裸 TimeoutError 逃逸——它的 str 是空的)
                            msg = await asyncio.wait_for(
                                ws.receive(), timeout=min(120, deadline - asyncio.get_event_loop().time()))
                        except (asyncio.TimeoutError, TimeoutError) as e:
                            raise TTSError("服务端 120s 内无数据,会话中断", kind="network") from e
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        if msg.type != aiohttp.WSMsgType.BINARY:
                            continue
                        frame = parse_server_frame(msg.data)
                        if frame["error_code"] is not None:
                            self._raise_by_code(frame["error_code"], frame["error_msg"])
                        ev = frame["event"]
                        if ev == EV_ROUND_AUDIO and isinstance(frame["payload"], bytes):
                            audio.extend(frame["payload"])
                        elif ev == EV_SESSION_FINISHED:
                            await ws.send_bytes(build_connection_frame(EV_FINISH_CONNECTION))
                            finished = True
                        elif ev == EV_CONNECTION_FINISHED:
                            break
                    if not finished:
                        await ws.send_bytes(build_connection_frame(EV_FINISH_CONNECTION))  # 提前断开也要优雅关闭
        except TTSError:
            raise
        except aiohttp.WSServerHandshakeError as e:
            kind = "auth" if e.status in (401, 403) else "http"
            raise TTSError(f"方舟播客建连失败(HTTP {e.status}):{e.message[:150]}", kind=kind)
        except aiohttp.ClientConnectionError as e:
            raise TTSError(f"连不上方舟播客服务:{e}", kind="network")
        except aiohttp.ClientError as e:
            raise TTSError(f"方舟播客请求失败:{e}", kind="http")

        if not audio:
            raise TTSError("方舟播客未返回任何音频数据", kind="audio")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(audio))

def probe_connection(api_key: str, resource_id: str = RESOURCE_ID) -> tuple[bool, str]:
    """轻量连通性探测:只建连拿 HTTP 状态,不发起合成(供设置页测试)。

    建连 200=Key/资源有效;401/403=认证失败;其他状态或异常如实返回。
    """
    try:
        r = httpx.get(
            "https://openspeech.bytedance.com/api/v3/sami/podcasttts",
            headers={"X-Api-Key": api_key, "X-Api-Resource-Id": resource_id},
            timeout=15,
        )
        # 对 GET 任意响应,只要不是 401/403 就说明网络与认证头被服务接受(GET 语义未定义)
        if r.status_code in (401, 403):
            return False, f"认证失败(HTTP {r.status_code}):{r.text[:120]}"
        return True, f"网络可达(HTTP {r.status_code},GET 语义未定义,真实合成请做一期)"
    except Exception as e:
        return False, f"{type(e).__name__}:{str(e)[:120]}"
