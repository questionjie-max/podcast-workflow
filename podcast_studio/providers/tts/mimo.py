"""小米 MiMo TTS。

支持情绪风格指令（作为 user 消息传入）与声音克隆（采样文件 base64 内联）。
输出 wav，由引擎统一转码为 mp3。

注意：MiMo 使用 api-key 认证头；如遇 SSL 证书问题，请在设置里确认
ssl_verify 配置（建议先尝试修复系统证书链，而不是直接关闭校验）。
"""

import base64
from pathlib import Path

import httpx

from .base import TTSProvider, VoiceSpec


class TTSError(RuntimeError):
    pass


class MimoTTS(TTSProvider):
    name = "mimo"
    supports_emotion = True
    supports_clone = True

    BUILTIN_VOICES = [
        {"ShortName": "mimo_default", "Friendly": "MiMo 默认音色"},
        {"ShortName": "default_zh", "Friendly": "MiMo 中文音色"},
        {"ShortName": "default_en", "Friendly": "MiMo 英文音色"},
    ]

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.base_url = (cfg.get("base_url") or "https://token-plan-cn.xiaomimimo.com/v1").rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.ssl_verify = cfg.get("ssl_verify", True)
        self.auth_style = cfg.get("auth_style", "api-key")
        # 本地网关(OpenCode/Magpie 等)常要求额外会话头;MiMo 与 LLM 共用同一份
        # 配置,之前只传了 api-key,导致 TTS 在网关上 400 MissingSessionID。
        self.extra_headers = cfg.get("extra_headers") or {}

    def _headers(self) -> dict:
        if self.auth_style == "api-key":
            headers = {"api-key": self.api_key}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}"}
        headers.update(self.extra_headers)
        return headers

    def _voice_payload(self, voice: VoiceSpec) -> str:
        if voice.clone_sample:
            audio = base64.b64encode(voice.clone_sample.read_bytes()).decode()
            return f"data:audio/wav;base64,{audio}"
        return voice.voice_id

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
            raise TTSError("未配置 MiMo API Key（设置 → 接口配置），或改用 edge-tts 免费音色")

        model = "mimo-v2.5-tts-voiceclone" if voice.clone_sample else "mimo-v2.5-tts"
        messages = [
            {"role": "user", "content": style or "用自然、亲切的声音朗读。"},
            {"role": "assistant", "content": text},
        ]
        body = {
            "model": model,
            "messages": messages,
            "audio": {"format": "wav", "voice": self._voice_payload(voice)},
        }
        async with httpx.AsyncClient(verify=self.ssl_verify, timeout=300) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=body
            )
        if resp.status_code != 200:
            raise TTSError(f"MiMo TTS 返回 {resp.status_code}：{resp.text[:300]}")

        data = resp.json()
        b64 = self._extract_audio_b64(data)
        if not b64:
            raise TTSError(f"MiMo TTS 响应中未找到音频数据：{str(data)[:300]}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(b64))

    @staticmethod
    def _extract_audio_b64(data: dict) -> str | None:
        """尽量兼容多种响应结构。"""
        try:
            msg = data["choices"][0]["message"]
            audio = msg.get("audio") or {}
            for k in ("data", "b64_json"):
                if audio.get(k):
                    return audio[k]
            content = msg.get("content")
            if isinstance(content, str):
                if content.startswith("data:audio"):
                    return content.split("base64,", 1)[1]
                if content and len(content) > 1000 and "=" in content[-8:] + content[:4]:
                    return content  # 可能是纯 base64
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        for k in ("audio", "b64_json", "data"):
            v = data.get(k)
            if isinstance(v, str) and len(v) > 100:
                return v.split("base64,", 1)[1] if "base64," in v else v
        return None
