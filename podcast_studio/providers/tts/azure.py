"""Azure Speech TTS(国际,情绪能力最完整)。

POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1
认证头:Ocp-Apim-Subscription-Key + SSML 请求体(text/plain)。
情绪走 SSML <mstts:express-as style="cheerful" styledegree="1.2">,
是最标准的情绪能力实现 —— 能力矩阵里与 MiMo(风格指令)、edge(无)互补。

克隆:Azure Custom Voice 是独立付费流程,voice 参数直接支持自定义音色名。
状态:实现 + mock 单测,**待人工联调**(需要 region + subscription key)。
"""

from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec


class TTSError(RuntimeError):
    def __init__(self, msg: str, kind: str = "other", code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


# 文稿情绪标签 → Azure SSML style 名
SSML_STYLES = {
    "好奇开场": ("cheerful", 1.0),
    "展开分析": ("assistant", 1.0),
    "若有所思": ("calm", 1.0),
    "略带调侃": ("humorous", 0.9),
    "认真探讨": ("serious", 1.0),
    "延伸思考": ("calm", 0.8),
    "回到现实": ("gentle", 1.0),
    "轻松收尾": ("cheerful", 0.6),
    "温柔开场": ("gentle", 1.0),
    "娓娓道来": ("calm", 1.0),
    "小小惊奇": ("cheerful", 0.8),
    "轻轻解释": ("gentle", 1.0),
    "安心收尾": ("calm", 0.6),
}

class AzureTTS(TTSProvider):
    BUILTIN_VOICES = [
        {"ShortName": "zh-CN-XiaoxiaoMultilingualNeural", "Friendly": "晓晓 · 女声 · 多语言(支持情绪)"},
        {"ShortName": "zh-CN-XiaoyiNeural", "Friendly": "晓伊 · 女声 · 活泼(支持情绪)"},
        {"ShortName": "zh-CN-YunxiNeural", "Friendly": "云希 · 男声 · 阳光(支持情绪)"},
        {"ShortName": "zh-CN-YunjianNeural", "Friendly": "云健 · 男声 · 沉稳(支持情绪)"},
        {"ShortName": "en-US-AriaNeural", "Friendly": "Aria · Female · emotional"},
        {"ShortName": "en-US-DavisNeural", "Friendly": "Davis · Male · confident"},
    ]
    name = "azure"
    supports_emotion = True   # 真情绪:SSML express-as
    supports_clone = True     # Custom Voice 音色名直传

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.region = cfg.get("region") or "eastasia"

    @staticmethod
    def _rate_azure(rate: str) -> str:
        """'+10%' → SSML '+10.00%'。"""
        try:
            v = float(rate.strip().rstrip("%"))
            return f"{v:+.2f}%"
        except (ValueError, AttributeError):
            return "+0.00%"

    def _ssml(self, text: str, voice: VoiceSpec, style: str | None, rate: str) -> str:
        from xml.sax.saxutils import escape

        style_name, degree = SSML_STYLES.get((style or "").strip(), ("", 0))
        prosody = f'<prosody rate="{self._rate_azure(rate)}">' if rate else ""
        prosody_end = "</prosody>" if rate else ""
        # 正文与属性一律转义:文稿含 & < > 曾直接产出畸形 XML(Azure 400/SSML 注入面)
        safe_text = escape(text)
        safe_voice = escape(voice.voice_id, {'"': "&quot;"})
        inner = safe_text
        if style_name:
            safe_style = escape(style_name, {'"': "&quot;"})
            inner = (
                f'<mstts:express-as style="{safe_style}" styledegree="{degree}">'
                f"{safe_text}</mstts:express-as>"
            )
        return (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
            f'<voice name="{safe_voice}">{prosody}{inner}{prosody_end}</voice></speak>'
        )

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
        if not (self.api_key and self.region):
            raise TTSError("未配置 Azure Speech(region + subscription key,设置 → TTS 接口配置),或改用免费 edge-tts")
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": self.api_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                    "User-Agent": "podcast-studio",
                },
                content=self._ssml(text, voice, style, rate).encode("utf-8"),
            )
        if resp.status_code != 200:
            raise TTSError(f"Azure TTS 返回 {resp.status_code}：{resp.text[:300]}")
        if not resp.content:
            raise TTSError("Azure TTS 返回空音频")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(resp.content)
