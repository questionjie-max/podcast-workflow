"""edge-tts：微软 Edge 朗读接口。

免费、无需 API key、音色多。
不支持情绪风格指令和声音克隆 —— 引擎会自动降级为 rate/pitch 参数。
输出 mp3。
"""

import asyncio
import json
import time
from pathlib import Path

import edge_tts

from .base import TTSProvider, VoiceSpec

# 网络不可用时的内置兜底音色列表
FALLBACK_VOICES = [
    {"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female", "Locale": "zh-CN", "Friendly": "晓晓 · 女声 · 温暖"},
    {"ShortName": "zh-CN-XiaoyiNeural", "Gender": "Female", "Locale": "zh-CN", "Friendly": "晓伊 · 女声 · 活泼"},
    {"ShortName": "zh-CN-YunxiNeural", "Gender": "Male", "Locale": "zh-CN", "Friendly": "云希 · 男声 · 阳光"},
    {"ShortName": "zh-CN-YunyangNeural", "Gender": "Male", "Locale": "zh-CN", "Friendly": "云扬 · 男声 · 播音"},
    {"ShortName": "zh-CN-YunjianNeural", "Gender": "Male", "Locale": "zh-CN", "Friendly": "云健 · 男声 · 沉稳"},
    {"ShortName": "zh-CN-Liaoning-XiaobeiNeural", "Gender": "Female", "Locale": "zh-CN-liaoning", "Friendly": "晓北 · 女声 · 东北"},
    {"ShortName": "zh-TW-HsiaoChenNeural", "Gender": "Female", "Locale": "zh-TW", "Friendly": "曉臻 · 女声 · 台湾"},
    {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US", "Friendly": "Aria · Female"},
    {"ShortName": "en-US-GuyNeural", "Gender": "Male", "Locale": "en-US", "Friendly": "Guy · Male"},
]


class EdgeTTS(TTSProvider):
    name = "edge"
    supports_emotion = False
    supports_clone = False

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
        out.parent.mkdir(parents=True, exist_ok=True)
        cm = edge_tts.Communicate(text, voice=voice.voice_id, rate=rate, pitch=pitch)
        await cm.save(str(out))

    @staticmethod
    async def list_voices_cached(cache_file: Path, ttl: int = 86400) -> list[dict]:
        """拉取完整音色列表，缓存 24 小时；失败时返回内置兜底列表。"""
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        try:
            voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=15)
            # 只保留中英文，附加友好名
            out = []
            for v in voices:
                if not (v.get("Locale", "").startswith("zh") or v.get("Locale", "").startswith("en")):
                    continue
                friendly = v.get("FriendlyName", "").split(" - ")[-1].split("(Updated")[0].strip()
                out.append(
                    {
                        "ShortName": v["ShortName"],
                        "Gender": v.get("Gender", ""),
                        "Locale": v.get("Locale", ""),
                        "Friendly": friendly or v["ShortName"],
                    }
                )
            if out:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                from ...atomic import atomic_write_text

                atomic_write_text(cache_file, json.dumps(out, ensure_ascii=False))
                return out
        except (OSError, asyncio.TimeoutError, Exception):
            pass
        return FALLBACK_VOICES
