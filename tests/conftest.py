"""pytest 公共夹具:隔离的配置目录、假 TTS、假声音注册。"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _no_socks_all_proxy(monkeypatch):
    """测试环境不继承 ALL_PROXY。

    httpx 不认常见的 ``socks://`` scheme，会让 42 个 provider 测试在构造客户端时
    直接 ValueError；CI 没有这个变量，本地开发环境却经常有。清掉以保证测试
    结果只反映代码本身（需要测代理行为的用例可自行再设置）。
    """
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    # 测试不触发自动备份，避免每个 TestClient 都写 zip
    monkeypatch.setenv("PODCAST_STUDIO_NO_AUTO_BACKUP", "1")


@pytest.fixture()
def studio_home(tmp_path, monkeypatch):
    """把整个数据目录隔离到临时路径。"""
    home = tmp_path / ".podcast-studio"
    monkeypatch.setenv("PODCAST_STUDIO_HOME", str(home))
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    return home


def make_tone_wav(path: Path, seconds: float = 0.4) -> Path:
    """用 ffmpeg 生成确定长度的正弦 wav,作为合成音频的替身。"""
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


class FakeTTS:
    """假 TTS:产出真实(但极短)的 wav,可按需抛错或按文本哈希决定成败。"""

    name = "fake"
    supports_emotion = True
    supports_clone = False

    def __init__(self, fail_indices: set[int] | None = None):
        self.fail_indices = fail_indices or set()
        self.calls: list[str] = []

    async def synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
        from podcast_studio.providers.tts.base import VoiceSpec

        assert isinstance(voice, VoiceSpec)
        self.calls.append(text)
        index = int(Path(out).stem.split("_")[1])
        if index in self.fail_indices:
            raise RuntimeError("fake tts boom")
        make_tone_wav(Path(str(out) + ".tmp.wav"), seconds=0.3)
        Path(str(out) + ".tmp.wav").replace(out)


@pytest.fixture()
def fake_tts(monkeypatch):
    """让 resolve_voice 永远返回 FakeTTS;返回夹具便于断言/注入失败。"""
    from podcast_studio.engine import pipeline as pipeline_mod
    from podcast_studio.providers import voices as voice_reg
    from podcast_studio.providers.tts.base import VoiceSpec

    holder = {"tts": FakeTTS()}

    def fake_resolve(cfg, llm_cfg, voice_selector):
        return holder["tts"], VoiceSpec(provider="fake", voice_id="fake", label="测试声音")

    # pipeline 与 voices 各持有一份 resolve_voice 引用,两处都要替换
    monkeypatch.setattr(voice_reg, "resolve_voice", fake_resolve)
    monkeypatch.setattr(pipeline_mod, "resolve_voice", fake_resolve)
    return holder
