"""VolcASR 单测:请求形状 / 响应解析 / 错误分类 / 音频护栏。

respx mock 全程不联网;webm→wav 转码用真实 ffmpeg(与 conftest 的
make_tone_wav 同思路的 webm 版)。真实火山接口待人工联调 —— 这里兜底的是
分号认证头、请求体形状、code!=1000 分类、HTTP/网络错误分类、大小护栏。
"""

import base64
import json as jsonlib
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from podcast_studio.providers import asr as asr_mod
from podcast_studio.providers.asr import ASRError, VolcASR

ASR_URL = "https://openspeech.bytedance.com/api/v1/asr"


def make_tone_wav(path: Path, seconds: float = 0.3) -> Path:
    """ffmpeg 生成短正弦 wav(转码输入替身)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def make_tone_webm(path: Path, seconds: float = 0.5) -> Path:
    """ffmpeg 生成 opus 编码的 webm(模拟浏览器 MediaRecorder 录音)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:a", "libopus", "-b:a", "32k",
            str(path),
        ],
        check=True, capture_output=True,
    )
    return path


# ---- 配置 --------------------------------------------------------------------

async def test_missing_config_raises():
    """缺 app_id / token → kind=config,且不发请求。"""
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(Path("x.webm"), app_id="", token="TOK")
    assert ei.value.kind == "config"

    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(Path("x.webm"), app_id="APP", token="   ")
    assert ei.value.kind == "config"

    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(Path("x.webm"), app_id=None, token=None)  # type: ignore[arg-type]
    assert ei.value.kind == "config"


# ---- 请求形状 + 成功解析 --------------------------------------------------------

@respx.mock
async def test_request_shape_and_auth(tmp_path):
    """Bearer;token 分号头、请求体四段结构、base64 音频字段真实存在。"""
    route = respx.post(ASR_URL).mock(
        return_value=httpx.Response(
            200, json={"code": 1000, "message": "Success", "result": {"text": "今天讲恐龙"}}
        )
    )
    wav = make_tone_wav(tmp_path / "in.wav")
    text = await VolcASR().transcribe(wav, app_id="APP", token="TOK")
    assert text == "今天讲恐龙"

    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer;TOK"  # 火山特色:分号
    payload = jsonlib.loads(httpx.Request("POST", "http://x", content=req.content).read())
    assert payload["app"] == {"appid": "APP", "token": "TOK", "cluster": "volcano_asr"}
    assert payload["user"] == {"uid": "podcast-studio"}
    audio = payload["audio"]
    assert audio["format"] == "wav"
    assert audio["rate"] == 16000
    assert audio["bits"] == 16
    assert audio["channel"] == 1
    assert base64.b64decode(audio["data"])[:4] == b"RIFF"  # 真实转码产物
    request = payload["request"]
    assert request["nbest"] == 1
    assert request["workflow"] == "audio_in,resample,partition,vad,fe,decode"
    assert request["reqid"]  # uuid


@respx.mock
async def test_success_with_utterances_still_returns_text(tmp_path):
    """result 里带 utterances 等附加字段时,仍取 result.text。"""
    respx.post(ASR_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1000,
                "message": "Success",
                "result": {"text": "小兔子迷路了", "utterances": [{"text": "小兔子迷路了"}]},
            },
        )
    )
    wav = make_tone_wav(tmp_path / "in.wav")
    assert await VolcASR().transcribe(wav, app_id="A", token="T") == "小兔子迷路了"


@respx.mock
async def test_success_missing_result_text_raises(tmp_path):
    """code=1000 但缺 result.text → 视为响应异常(kind=http)。"""
    respx.post(ASR_URL).mock(return_value=httpx.Response(200, json={"code": 1000}))
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "http"


# ---- 业务错误码分类 -------------------------------------------------------------

@pytest.mark.parametrize(
    "code,expected",
    [(1002, "auth"), (1101, "auth"), (1003, "audio"), (1005, "audio"), (2001, "http")],
)
@respx.mock
async def test_error_code_classification(tmp_path, code, expected):
    """非 1000 code → 按 _AUTH_CODES/_AUDIO_CODES 分类,未知码兜底 http。"""
    respx.post(ASR_URL).mock(
        return_value=httpx.Response(200, json={"code": code, "message": "boom"})
    )
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == expected
    assert ei.value.code == code


@respx.mock
async def test_unknown_code_auth_message_fallback(tmp_path):
    """未知 code 但消息含 token → 兜底判为 auth。"""
    respx.post(ASR_URL).mock(
        return_value=httpx.Response(
            200, json={"code": 9999, "message": "check your token please"}
        )
    )
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "auth"


# ---- HTTP / 网络错误 ------------------------------------------------------------

@respx.mock
async def test_http_401_is_auth(tmp_path):
    respx.post(ASR_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "auth"


@respx.mock
async def test_http_429_is_http_with_hint(tmp_path):
    respx.post(ASR_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "http"
    assert "限流" in str(ei.value)


@respx.mock
async def test_connect_error_is_network(tmp_path):
    respx.post(ASR_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "network"


# ---- 音频护栏 -------------------------------------------------------------------

@respx.mock
async def test_oversize_audio_guard(tmp_path, monkeypatch):
    """转码后 >10MB → kind=audio,提示 60 秒上限;且不发起 HTTP 请求。"""
    route = respx.post(ASR_URL).mock(
        return_value=httpx.Response(200, json={"code": 1000, "result": {"text": "x"}})
    )

    def fake_big_transcode(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"\0" * (10 * 1024 * 1024 + 1))
        return dst

    monkeypatch.setattr(asr_mod, "_ensure_16k_mono", fake_big_transcode)
    wav = make_tone_wav(tmp_path / "in.wav")
    with pytest.raises(ASRError) as ei:
        await VolcASR().transcribe(wav, app_id="A", token="T")
    assert ei.value.kind == "audio"
    assert "超过一句话识别上限" in str(ei.value)
    assert "60 秒" in str(ei.value)
    assert not route.called


# ---- webm→wav 真实转码 -----------------------------------------------------------

def test_webm_to_wav_16k_mono_real_ffmpeg(tmp_path):
    """MediaRecorder 风格 webm(opus)→ 16kHz/16bit/mono pcm_s16le wav。"""
    webm = make_tone_webm(tmp_path / "rec.webm")
    out = asr_mod._ensure_16k_mono(webm, tmp_path / "out.wav")
    assert out.read_bytes()[:4] == b"RIFF"
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=sample_rate,channels,codec_name",
            "-of", "default=noprint_wrappers=1", str(out),
        ],
        check=True, capture_output=True, text=True,
    )
    assert "sample_rate=16000" in probe.stdout
    assert "channels=1" in probe.stdout
    assert "codec_name=pcm_s16le" in probe.stdout
