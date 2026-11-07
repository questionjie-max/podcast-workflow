"""火山/阿里 TTS provider:请求构造与响应解析(respx mock,不联网)。

真实接口留待人工联调 —— 这两个文件的精神是:
请求体形状、认证头、错误分支、音频提取都要有确定性测试兜底。
"""

import base64
from pathlib import Path

import httpx
import pytest
import respx

from podcast_studio.providers.tts.ali import AliTTS
from podcast_studio.providers.tts.base import VoiceSpec
from podcast_studio.providers.tts.volc import VolcTTS

FAKE_MP3 = base64.b64encode(b"fake-mp3-bytes").decode()


def voice(vid="BV001_streaming"):
    return VoiceSpec(provider="volc", voice_id=vid, label=vid)


# ---- 火山 --------------------------------------------------------------------

def test_volc_speed_ratio_mapping():
    assert VolcTTS._speed_ratio("+0%") == 1.0
    assert VolcTTS._speed_ratio("-10%") == pytest.approx(0.9)
    assert VolcTTS._speed_ratio("+50%") == 1.5
    assert VolcTTS._speed_ratio("garbage") == 1.0
    assert VolcTTS._speed_ratio("+500%") == 3.0  # 上限


@respx.mock
@pytest.mark.asyncio
async def test_volc_request_shape_and_auth():
    route = respx.post("https://openspeech.bytedance.com/api/v1/tts").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": FAKE_MP3})
    )
    tts = VolcTTS({"app_id": "APP", "token": "TOK", "cluster": "volcano_tts"})
    out = Path("/tmp/ps_test_volc/out.mp3")
    await tts.synth("你好", voice("BV002_streaming"), None, rate="-10%", out=out)

    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer;TOK"  # 火山特色:分号
    body = httpx.Request("POST", "http://x", content=req.content).read()
    import json

    payload = json.loads(body)
    assert payload["app"] == {"appid": "APP", "token": "TOK", "cluster": "volcano_tts"}
    assert payload["audio"]["voice_type"] == "BV002_streaming"
    assert payload["audio"]["encoding"] == "mp3"
    assert payload["audio"]["speed_ratio"] == pytest.approx(0.9)
    assert payload["request"]["operation"] == "query"
    assert out.read_bytes() == b"fake-mp3-bytes"


@respx.mock
@pytest.mark.asyncio
async def test_volc_error_branches():
    tts = VolcTTS({"app_id": "A", "token": "T"})
    with pytest.raises(Exception, match="未配置火山"):
        await VolcTTS({}).synth("x", voice(), None, out=Path("/tmp/o.mp3"))

    with respx.mock:
        respx.post("https://openspeech.bytedance.com/api/v1/tts").mock(
            return_value=httpx.Response(200, json={"code": 3001, "message": "invalid voice"})
        )
        with pytest.raises(Exception, match="3001"):
            await tts.synth("x", voice(), None, out=Path("/tmp/o.mp3"))


# ---- 阿里 --------------------------------------------------------------------

def test_ali_rate_pitch_mapping():
    assert AliTTS._rate_ratio("+0%") == 1.0
    assert AliTTS._rate_ratio("-20%") == pytest.approx(0.8)
    assert AliTTS._pitch_semitone("+0Hz") == 0.0
    assert AliTTS._pitch_semitone("-200Hz") == pytest.approx(-2.0)


@respx.mock
@pytest.mark.asyncio
async def test_ali_request_shape_and_b64_response():
    route = respx.post(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    ).mock(
        return_value=httpx.Response(200, json={"output": {"audio": {"data": FAKE_MP3}}})
    )
    tts = AliTTS({"api_key": "sk-ALI", "model": "cosyvoice-v2"})
    out = Path("/tmp/ps_test_ali/out.mp3")
    await tts.synth("你好", VoiceSpec(provider="ali", voice_id="longwan_v2"), None, out=out)

    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer sk-ALI"
    import json

    payload = json.loads(httpx.Request("POST", "http://x", content=req.content).read())
    assert payload["model"] == "cosyvoice-v2"
    assert payload["input"]["voice"] == "longwan_v2"
    assert payload["parameters"]["rate"] == 1.0
    assert out.read_bytes() == b"fake-mp3-bytes"


@respx.mock
@pytest.mark.asyncio
async def test_ali_url_response_download():
    with respx.mock:
        respx.post(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        ).mock(return_value=httpx.Response(200, json={"output": {"audio": {"url": "https://dl.example/a.mp3"}}}))
        respx.get("https://dl.example/a.mp3").mock(
            return_value=httpx.Response(200, content=b"url-audio")
        )
        tts = AliTTS({"api_key": "k"})
        out = Path("/tmp/ps_test_ali/out2.mp3")
        await tts.synth("hi", VoiceSpec(provider="ali", voice_id="longcheng_v2"), None, out=out)
        assert out.read_bytes() == b"url-audio"


@respx.mock
@pytest.mark.asyncio
async def test_ali_missing_key_raises():
    with pytest.raises(Exception, match="未配置阿里"):
        await AliTTS({}).synth("x", VoiceSpec(provider="ali", voice_id="v"), None, out=Path("/tmp/o.mp3"))


# ---- R2 接线审计修复的契约测试(E1/E2/E4) ----

def test_volc_accepts_official_success_code_3000(monkeypatch, tmp_path):
    """官方文档成功码是 3000(非 0);此前被当错误,真网必失败。"""
    from podcast_studio.providers.tts.base import VoiceSpec
    from podcast_studio.providers.tts.volc import VolcTTS

    calls = {}

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"code": 3000, "message": "Success", "data": "SGVsbG8="}

    async def fake_post(self, url, **kw):
        calls["url"] = url
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    import asyncio

    out = tmp_path / "a.mp3"
    asyncio.run(VolcTTS({"app_id": "A", "token": "T"}).synth(
        "你好", VoiceSpec(provider="volc", voice_id="v", label=""), None, out=out))
    assert out.read_bytes() == b"Hello"


def test_azure_ssml_escapes_markup_chars():
    """文稿含 & < > " 时 SSML 必须仍是合法 XML(此前直接内插,Azure 400/注入面)。"""
    from xml.dom import minidom

    from podcast_studio.providers.tts.azure import AzureTTS
    from podcast_studio.providers.tts.base import VoiceSpec

    tts = AzureTTS({"api_key": "k", "region": "eastasia"})
    ssml = tts._ssml('他说: "A&B" 1<2', VoiceSpec(provider="azure", voice_id='X"Y', label=""), None, "+0%")
    minidom.parseString(ssml)  # 不抛即合法


def test_failed_grant_probe_not_cached(studio_home, monkeypatch):
    """探测失败(网络抖动)不得缓存为空集——否则豆包音色组静默消失 10 分钟。"""
    import asyncio

    import httpx

    from podcast_studio.config import Config
    from podcast_studio.providers import voices as vr

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_podcast"] = {"api_key": "k", "resource_id": "volc.service_type.10050"}
    monkeypatch.setattr(vr, "_GRANT_CACHE", {"resources": None, "ts": 0.0})

    def boom(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "get", boom)
    out = asyncio.run(vr._granted_seed_resources(cfg))
    assert out == set()
    assert vr._GRANT_CACHE["resources"] is None  # 空结果不缓存,下次还探


# ---- MiMo(与 LLM 共用网关配置:必须转发认证方式与会话头) ----------------------

@respx.mock
@pytest.mark.asyncio
async def test_mimo_forwards_gateway_auth_and_session_headers(tmp_path):
    from podcast_studio.providers.tts.mimo import MimoTTS

    route = respx.post("https://gw.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"audio": {"data": FAKE_MP3}}}]}
        )
    )
    tts = MimoTTS({
        "base_url": "https://gw.example/v1",
        "api_key": "oc_sk_test",
        "auth_style": "bearer",
        "extra_headers": {"x-opencode-session": "sess-1"},
    })
    out = tmp_path / "mimo.wav"
    await tts.synth("你好", VoiceSpec(provider="mimo", voice_id="mimo_default"), None, out=out)

    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer oc_sk_test"
    assert req.headers["x-opencode-session"] == "sess-1"
    assert out.read_bytes() == b"fake-mp3-bytes"
