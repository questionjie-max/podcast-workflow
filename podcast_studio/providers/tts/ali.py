"""阿里云 CosyVoice TTS(DashScope)。

POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
认证:Authorization: Bearer {api_key}
body: {"model": "cosyvoice-v2", "input": {"text": ..., "voice": ...},
       "parameters": {"rate": 0.5-2.0, "volume": 0-100, "pitch": 半音}}
响应音频:output.audio.data(base64)或 output.audio.url。

克隆:用官方声音复刻(enrollment)得到的音色 ID 直接作为 voice 传入。
状态:实现 + mock 单测,**待人工联调**(需要 DashScope API Key)。
"""

import base64
from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec


class TTSError(RuntimeError):
    def __init__(self, msg: str, kind: str = "other", code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


class AliTTS(TTSProvider):
    name = "ali"
    supports_emotion = False
    supports_clone = True

    BUILTIN_VOICES = [
        {"ShortName": "longwan_v2", "Friendly": "龙婉 · 女声 · 温柔"},
        {"ShortName": "longxiaochun_v2", "Friendly": "龙小淳 · 女声 · 甜美"},
        {"ShortName": "longcheng_v2", "Friendly": "龙橙 · 男声 · 沉稳"},
        {"ShortName": "longshu_v2", "Friendly": "龙书 · 男声 · 知性"},
        {"ShortName": "loongbella_v2", "Friendly": "Bella · 女声 · 英文流利"},
    ]

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or "cosyvoice-v2"
        self.base = (cfg.get("base_url") or "https://dashscope.aliyuncs.com").rstrip("/")

    @staticmethod
    def _rate_ratio(rate: str) -> float:
        try:
            return max(0.5, min(2.0, 1.0 + float(rate.strip().rstrip("%")) / 100))
        except (ValueError, AttributeError):
            return 1.0

    @staticmethod
    def _pitch_semitone(pitch: str) -> float:
        try:
            return max(-6.0, min(6.0, float(pitch.strip().rstrip("Hz").replace("Hz", "").strip() or 0) / 100))
        except (ValueError, AttributeError):
            return 0.0

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
            raise TTSError(
                "未配置阿里云 DashScope API Key(设置 → TTS 接口配置),或改用免费 edge-tts"
            )
        body = {
            "model": self.model,
            "input": {"text": text, "voice": voice.voice_id},
            "parameters": {
                "rate": self._rate_ratio(rate),
                "pitch": self._pitch_semitone(pitch),
            },
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{self.base}/api/v1/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code != 200:
            raise TTSError(f"CosyVoice 返回 {resp.status_code}：{resp.text[:300]}")
        data = resp.json()
        out_data = (data.get("output") or {}).get("audio") or {}
        b64 = out_data.get("data")
        if b64:
            audio = base64.b64decode(b64)
        elif out_data.get("url"):
            # 部分模式返回临时 URL,需要再取一次(加类型/大小护栏,防 HTML 伪装或超大响应)
            async with httpx.AsyncClient(timeout=60) as client:
                dl = await client.get(out_data["url"])
            if dl.status_code != 200:
                raise TTSError(f"下载音频失败：HTTP {dl.status_code}", kind="http")
            ctype = (dl.headers.get("content-type") or "").lower()
            if ctype and "audio" not in ctype and "octet-stream" not in ctype:
                raise TTSError(f"下载的不是一个音频文件(Content-Type: {ctype})", kind="audio")
            if len(dl.content) > 50 * 1024 * 1024:
                raise TTSError("下载的音频超过 50MB,已放弃", kind="audio")
            audio = dl.content
        else:
            raise TTSError(f"CosyVoice 响应中无音频数据：{str(data)[:200]}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
