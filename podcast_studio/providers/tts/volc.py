"""火山引擎豆包 TTS。

走 openspeech HTTP 接口(稳定、文档齐全):
POST https://openspeech.bytedance.com/api/v1/tts
认证头是 `Authorization: Bearer;{token}`(注意 Bearer 后是分号,火山特色)。
返回 JSON,音频 base64 在 data 字段。

支持 speed_ratio(语速);emotion 参数依赖音色,默认不传。
声音克隆走声音复刻 2.0(独立流程),暂不接入 —— supports_clone=False。

状态:实现 + mock 单测,**待人工联调**(需要 app_id + access token)。
"""

import base64
import uuid
from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec


class TTSError(RuntimeError):
    def __init__(self, msg: str, kind: str = "other", code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


class VolcTTS(TTSProvider):
    name = "volc"
    supports_emotion = False
    supports_clone = False

    BUILTIN_VOICES = [
        {"ShortName": "BV001_streaming", "Friendly": "通用女声"},
        {"ShortName": "BV002_streaming", "Friendly": "通用男声"},
        {"ShortName": "BV406_streaming", "Friendly": "童声"},
        {"ShortName": "BV001_V2_streaming", "Friendly": "通用女声 V2"},
        {"ShortName": "BV002_V2_streaming", "Friendly": "通用男声 V2"},
    ]

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.app_id = cfg.get("app_id") or ""
        self.token = cfg.get("token") or ""
        self.cluster = cfg.get("cluster") or "volcano_tts"
        self.api_url = cfg.get("api_url") or "https://openspeech.bytedance.com/api/v1/tts"

    @staticmethod
    def _speed_ratio(rate: str) -> float:
        """edge 风格的 '+10%'/'-10%' → 火山 speed_ratio(0.2-3.0)。"""
        try:
            return max(0.2, min(3.0, 1.0 + float(rate.strip().rstrip("%")) / 100))
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
        if not (self.app_id and self.token):
            raise TTSError(
                "未配置火山引擎 TTS(设置 → TTS 接口配置:app_id + access token),或改用免费 edge-tts"
            )
        body = {
            "app": {"appid": self.app_id, "token": self.token, "cluster": self.cluster},
            "user": {"uid": "podcast-studio"},
            "audio": {
                "voice_type": voice.voice_id,
                "encoding": "mp3",
                "speed_ratio": self._speed_ratio(rate),
            },
            "request": {
                "reqid": uuid.uuid4().hex,
                "text": text,
                "operation": "query",
            },
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer;{self.token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code == 401:
            raise TTSError("火山 TTS 认证失败：检查 app_id 与 token 是否配对", kind="auth") from None
        if resp.status_code != 200:
            raise TTSError(f"火山 TTS 返回 {resp.status_code}：{resp.text[:300]}", kind="http")
        data = resp.json()
        # 官方成功码是 3000(文档 6561/79823);0 亦视为成功以兼容历史 mock 契约
        if data.get("code") not in (0, 3000):
            raise TTSError(f"火山 TTS 错误 {data.get('code')}：{data.get('message', '')[:200]}", kind="http")
        b64 = data.get("data")
        if not b64:
            raise TTSError("火山 TTS 响应中无音频数据")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(b64))
