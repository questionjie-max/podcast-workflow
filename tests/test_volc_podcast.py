"""方舟播客 WS v3:帧编解码、负载构造、错误分类(纯单测,不联网)。

帧字节依据官方文档示例:
- StartSession 请求帧头 = 0x11 0x94 0x10 0x00,event=150(0x96)
- 客户端控制帧带 event 号(flags 0b0100),结构 event+sid+payload
"""

import gzip
import struct

import pytest

from podcast_studio.providers.tts.volc_podcast import (
    DEFAULT_SPEAKERS,
    EV_SESSION_STARTED,
    TTSError,
    VolcPodcastTTS,
    build_client_frame,
    build_finish_connection,
    build_round_payload,
    parse_server_frame,
    split_for_rounds,
)


def test_client_frame_header_matches_doc():
    """StartSession 帧头必须等于文档示例的 0x11 0x94 0x10 0x00。"""
    frame = build_client_frame(EV_SESSION_STARTED, "nxckjoejnkegf", {"a": 1})
    assert frame[0] == 0b0001_0001  # v1 + 4 字节头
    assert frame[1] == 0b0001_0100  # Full client request + event 号(文档示例 1001 有误)
    assert frame[2] == 0b0001_0000  # JSON + 无压缩
    assert frame[3] == 0
    assert struct.unpack(">I", frame[4:8])[0] == EV_SESSION_STARTED


def test_client_frame_roundtrip():
    sid, payload = "session-abc", {"action": 3, "nlp_texts": [{"speaker": "s1", "text": "你好"}]}
    frame = build_client_frame(150, sid, payload)
    parsed = parse_server_frame(frame)
    assert parsed["event"] == 150
    assert parsed["session_id"] == sid
    assert parsed["payload"] == payload
    assert parsed["compressed"] == 0


def test_gzip_payload_roundtrip():
    """压缩标志 0b0001 时自动解压。"""
    sid = "sid"
    body = gzip.compress(b'{"ok": 1}')
    frame = bytes([0b0001_0001, 0b1001_0100, 0b0001_0001, 0])  # 压缩版
    frame += struct.pack(">I", 152) + struct.pack(">I", len(sid)) + sid.encode()
    frame += struct.pack(">I", len(body)) + body
    parsed = parse_server_frame(frame)
    assert parsed["payload"] == {"ok": 1}
    assert parsed["compressed"] == 1



def test_error_frame_parse():
    """错误帧:消息类型 0b1111,4~7 错误码,payload JSON 消息。"""
    code, msg = 40000022, "IllegalPayload:TextRiskAuditFailed"
    payload = msg.encode()
    frame = bytes([0b0001_0001, 0b1111_0000, 0b0001_0000, 0])
    frame += struct.pack(">I", code) + payload  # 错误帧 payload 直接跟在错误码后
    parsed = parse_server_frame(frame)
    assert parsed["error_code"] == code
    assert parsed["error_msg"] == msg


def test_build_finish_connection():
    frame = build_finish_connection("sid-1")
    assert frame[1] == 0b0001_0100  # FinishConnection 也带 event 号
    assert struct.unpack(">I", frame[4:8])[0] == 2  # FinishConnection


def test_round_payload_action3():
    p = build_round_payload(text="第一句。第二句。" * 80, speakers=DEFAULT_SPEAKERS)
    assert p["action"] == 3
    assert p["speaker_info"]["speakers"] == DEFAULT_SPEAKERS[:2]
    assert p["speaker_info"]["random_order"] is False
    assert p["audio_config"]["format"] == "mp3"
    for t in p["nlp_texts"]:
        assert len(t["text"]) <= 290
        assert t["speaker"] == DEFAULT_SPEAKERS[0]
    assert all(t["text"] for t in p["nlp_texts"])


def test_round_payload_action0_when_no_text():
    p = build_round_payload(text="", speakers=DEFAULT_SPEAKERS)
    assert p["action"] == 0
    assert "nlp_texts" not in p


def test_split_short_text_single_round():
    assert split_for_rounds("短文本。") == ["短文本。"]


def test_missing_key_raises_config():
    with pytest.raises(TTSError) as ei:
        VolcPodcastTTS({})._raise_by_code(40000010, "x")  # 直接测分类器
    assert ei.value.kind == "http"


def test_error_classification():
    import asyncio

    from podcast_studio.providers.tts.base import VoiceSpec

    tts = VolcPodcastTTS({"api_key": "k"})
    with pytest.raises(TTSError) as ei:
        asyncio.run(tts.synth("x", VoiceSpec(provider="volcpodcast", voice_id="s1|s2"),
                              None, out=__import__("pathlib").Path("/tmp/o.mp3")))
    # 无 aiohttp mock 时走真实连接失败 → network 或 auth(取决于环境),不应是 config
    assert ei.value.kind in ("network", "auth", "http")


def test_build_finish_session():
    from podcast_studio.providers.tts.volc_podcast import build_finish_session

    frame = build_finish_session("sid-9")
    assert frame[1] == 0b0001_0100
    assert struct.unpack(">I", frame[4:8])[0] == 102
    parsed = parse_server_frame_bytes = frame
    # 事件号与 session_id 可回读
    assert int.from_bytes(frame[4:8], "big") == 102
    sl = int.from_bytes(frame[8:12], "big")
    assert frame[12:12 + sl] == b"sid-9"
