"""ElevenLabs TTS:请求构造、原始字节响应、错误分支(respx mock)。"""

import httpx
import pytest
import respx

from podcast_studio.providers.tts.base import VoiceSpec
from podcast_studio.providers.tts.elevenlabs import ElevenLabsTTS

OUT = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
RAW = b"fake-elevenlabs-mp3"


def voice():
    return VoiceSpec(provider="elevenlabs", voice_id="21m00Tcm4TlvDq8ikWAM", label="Rachel")


@respx.mock
@pytest.mark.asyncio
async def test_request_shape_and_raw_bytes():
    route = respx.post(OUT).mock(return_value=httpx.Response(200, content=RAW))
    tts = ElevenLabsTTS({"api_key": "el-key"})
    out = __import__("pathlib").Path("/tmp/ps_test_el/out.mp3")
    await tts.synth("hello", voice(), "读得有表现力一点", rate="-10%", out=out)

    req = route.calls.last.request
    assert req.headers["xi-api-key"] == "el-key"
    assert req.headers["Accept"] == "audio/mpeg"
    import json

    body = json.loads(req.content)
    assert body["model_id"] == "eleven_multilingual_v2"
    assert body["voice_settings"]["speed"] == pytest.approx(0.9)
    assert body["voice_settings"]["style"] == 0.45  # 有风格指令 → 更高表现力
    assert out.read_bytes() == RAW


@respx.mock
@pytest.mark.asyncio
async def test_style_none_uses_conservative_settings():
    with respx.mock:
        route = respx.post(OUT).mock(return_value=httpx.Response(200, content=RAW))
        await ElevenLabsTTS({"api_key": "k"}).synth("x", voice(), None, out=OUT and __import__("pathlib").Path("/tmp/ps_test_el/o2.mp3"))
        import json

        assert json.loads(route.calls.last.request.content)["voice_settings"]["style"] == 0.15


@respx.mock
@pytest.mark.asyncio
async def test_error_branches():
    tts = ElevenLabsTTS({"api_key": "k"})
    with pytest.raises(Exception, match="未配置 ElevenLabs"):
        await ElevenLabsTTS({}).synth("x", voice(), None, out=__import__("pathlib").Path("/tmp/o.mp3"))
    with respx.mock:
        respx.post(OUT).mock(return_value=httpx.Response(401, text="bad key"))
        with pytest.raises(Exception, match="401"):
            await tts.synth("x", voice(), None, out=__import__("pathlib").Path("/tmp/o.mp3"))


def test_speed_clamp():
    assert ElevenLabsTTS._speed("+0%") == 1.0
    assert ElevenLabsTTS._speed("-50%") == 0.7   # 下限
    assert ElevenLabsTTS._speed("+99%") == 1.2   # 上限
    assert ElevenLabsTTS._speed("junk") == 1.0
