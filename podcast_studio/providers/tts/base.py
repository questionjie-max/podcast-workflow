"""TTS Provider 抽象。

能力探测 + 优雅降级：
- supports_emotion=False 的 provider（如 edge-tts）忽略情绪风格指令，
  改用 profile 里的 rate/pitch 降级参数。
- supports_clone=False 的 provider 不接受克隆声音。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VoiceSpec:
    provider: str  # "edge" | "mimo"
    voice_id: str  # provider 内部音色 ID，如 "zh-CN-XiaoxiaoNeural" / "mimo_default"
    clone_sample: Path | None = None  # 声音克隆采样文件（仅支持克隆的 provider 使用）
    label: str = ""


class TTSProvider(ABC):
    name = "base"
    supports_emotion = False
    supports_clone = False

    @abstractmethod
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
        """合成一段语音，写入 out（mp3 或 wav，由 provider 决定）。"""

    def run(self, coro):
        """在线程里同步调用异步合成的辅助方法。"""
        return asyncio.run(coro)

    @staticmethod
    def _raise_by_code(code: int, msg: str) -> None:
        """火山语音错误码 → 分类 TTSError(子类共用)。"""
        hint = {
            45000000: ("并发超限,稍后再试", "limit"),
            40000010: ("参数错误", "http"),
            40000022: ("命中严格风控,请检查文本", "audio"),
            50302102: ("内容触发安全审核过滤", "audio"),
            55000000: ("服务端错误", "http"),
        }.get(code, ("服务返回错误", "http"))
        from .volc_podcast import TTSError

        raise TTSError(f"错误 {code}:{hint[0]} — {msg[:120]}", kind=hint[1], code=code)


def get_tts(provider_name: str, cfg: dict | None = None) -> TTSProvider:
    """按名字构建 TTS provider;cfg 是 config['tts'] 下对应小节。"""
    from .ali import AliTTS
    from .azure import AzureTTS
    from .edge import EdgeTTS
    from .elevenlabs import ElevenLabsTTS
    from .mimo import MimoTTS
    from .volc import VolcTTS
    from .volc_podcast import VolcPodcastTTS
    from .volc_speech import VolcSpeechTTS

    if provider_name == "edge":
        return EdgeTTS()
    if provider_name == "mimo":
        return MimoTTS(cfg)
    if provider_name == "volc":
        return VolcTTS(cfg)
    if provider_name == "ali":
        return AliTTS(cfg)
    if provider_name == "elevenlabs":
        return ElevenLabsTTS(cfg)
    if provider_name == "volcpodcast":
        return VolcPodcastTTS(cfg)
    if provider_name == "volcspeech":
        return VolcSpeechTTS(cfg)
    if provider_name == "azure":
        return AzureTTS(cfg)
    raise ValueError(f"未知 TTS provider: {provider_name}")
