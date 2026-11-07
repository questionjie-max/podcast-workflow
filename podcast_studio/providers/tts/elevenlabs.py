"""ElevenLabs TTS(国际,质量标杆)。

POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
认证头:xi-api-key;响应是原始音频字节(mp3),不是 JSON。
voice_settings: stability / similarity_boost / style(表现力) / speed(0.7-1.2)。

克隆:官方声音库或用户克隆的 voice_id 直接作为路径参数传入(supports_clone=True,
克隆本身走官方界面/API,不在本项目内做)。
状态:实现 + mock 单测,**待人工联调**(需要 xi-api-key)。
"""

from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec


class TTSError(RuntimeError):
    def __init__(self, msg: str, kind: str = "other", code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    supports_emotion = False  # 用 voice_settings.style 近似,不做按段风格指令
    supports_clone = True

    # ElevenLabs 公共声音库的稳定默认音色
    BUILTIN_VOICES = [
        {"ShortName": "21m00Tcm4TlvDq8ikWAM", "Friendly": "Rachel · 女声 · 平静"},
        {"ShortName": "EXAVITQu4vr4xnSDxMaL", "Friendly": "Sarah · 女声 · 温软"},
        {"ShortName": "AZnzlk1XvdvUeBnXmlld", "Friendly": "Domi · 女声 · 有力"},
        {"ShortName": "ErXwobaYiN019PkySvjV", "Friendly": "Antoni · 男声 · 温和"},
        {"ShortName": "TxGEqnHWrfWFTfGW9XjX", "Friendly": "Josh · 男声 · 低沉"},
        {"ShortName": "pNInz6obpgDQGcFmaJgB", "Friendly": "Adam · 男声 · 深厚"},
    ]

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or "eleven_multilingual_v2"
        self.base = (cfg.get("base_url") or "https://api.elevenlabs.io").rstrip("/")

    @staticmethod
    def _speed(rate: str) -> float:
        try:
            return max(0.7, min(1.2, 1.0 + float(rate.strip().rstrip("%")) / 100))
        except (ValueError, AttributeError):
            return 1.0

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
        if not self.api_key:
            raise TTSError("未配置 ElevenLabs API Key（设置 → TTS 接口配置），或改用免费 edge-tts")
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                # 有风格指令时提高表现力,没有则保守
                "style": 0.45 if style else 0.15,
                "speed": self._speed(rate),
            },
        }
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self.base}/v1/text-to-speech/{voice.voice_id}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json",
                         "Accept": "audio/mpeg"},
                json=body,
            )
        if resp.status_code != 200:
            raise TTSError(f"ElevenLabs 返回 {resp.status_code}：{resp.text[:300]}")
        if not resp.content:
            raise TTSError("ElevenLabs 返回空音频")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
