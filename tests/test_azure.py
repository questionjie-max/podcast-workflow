"""Azure TTS:SSML 构造(express-as 情绪)、认证头、错误分支(respx mock)。"""

import httpx
import pytest
import respx

from podcast_studio.providers.tts.azure import AzureTTS
from podcast_studio.providers.tts.base import VoiceSpec

OUT = "https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1"
RAW = b"fake-azure-mp3"


def voice(vid="zh-CN-XiaoxiaoMultilingualNeural"):
    return VoiceSpec(provider="azure", voice_id=vid, label=vid)


def test_ssml_with_emotion():
    ssml = AzureTTS()._ssml("你好呀", voice(), "温柔开场", rate="-20%")
    assert '<mstts:express-as style="gentle" styledegree="1.0">' in ssml
    assert '<prosody rate="-20.00%">' in ssml
    assert '<voice name="zh-CN-XiaoxiaoMultilingualNeural">' in ssml
    assert "你好呀" in ssml


def test_ssml_without_emotion_or_unknown_tag():
    ssml = AzureTTS()._ssml("正文", voice(), None, rate="+0%")
    assert "express-as" not in ssml
    assert '<prosody rate="+0.00%">' in ssml
    ssml2 = AzureTTS()._ssml("正文", voice(), "不认识的情绪", rate="+0%")
    assert "express-as" not in ssml2  # 未映射标签不硬造 style


@respx.mock
@pytest.mark.asyncio
async def test_request_shape_and_bytes():
    route = respx.post(OUT).mock(return_value=httpx.Response(200, content=RAW))
    tts = AzureTTS({"api_key": "az-key", "region": "eastasia"})
    from pathlib import Path

    out = Path("/tmp/ps_test_az/out.mp3")
    await tts.synth("你好", voice(), "轻松收尾", out=out)
    req = route.calls.last.request
    assert req.headers["Ocp-Apim-Subscription-Key"] == "az-key"
    assert req.headers["Content-Type"] == "application/ssml+xml"
    assert "mp3" in req.headers["X-Microsoft-OutputFormat"]
    assert out.read_bytes() == RAW


@respx.mock
@pytest.mark.asyncio
async def test_error_branches():
    from pathlib import Path

    with pytest.raises(Exception, match="未配置 Azure"):
        await AzureTTS({}).synth("x", voice(), None, out=Path("/tmp/o.mp3"))
    with respx.mock:
        respx.post(OUT).mock(return_value=httpx.Response(403, text="denied"))
        with pytest.raises(Exception, match="403"):
            await AzureTTS({"api_key": "k", "region": "eastasia"}).synth(
                "x", voice(), None, out=Path("/tmp/o.mp3"))
