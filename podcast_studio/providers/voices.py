"""声音库管理：内置音色 + 克隆声音注册。

克隆声音用于支持克隆的 provider（如 MiMo）：采样文件存本地，
合成时转 base64 内联。声音库记录在 <data_dir>/voices.json。
"""

import json
import time
import uuid
from pathlib import Path

from ..config import Config
from ..engine.audio import ensure_wav_24k_mono, ffmpeg_available
from .tts.edge import EdgeTTS
from .tts.mimo import MimoTTS

_CLONE_SUFFIX = "clone"


def _registry_path(cfg: Config) -> Path:
    return cfg.data_dir / "voices.json"


def _load_registry(cfg: Config) -> dict:
    path = _registry_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"clones": []}


def _save_registry(cfg: Config, reg: dict) -> None:
    from ..atomic import atomic_write_json

    cfg.ensure_dirs()
    atomic_write_json(_registry_path(cfg), reg)


def register_clone(cfg: Config, name: str, sample_path: Path) -> dict:
    """注册克隆声音：采样统一转为 24kHz/16bit/mono WAV。"""
    if not ffmpeg_available():
        raise RuntimeError("未找到 ffmpeg，无法处理声音采样；请先安装 ffmpeg")
    cfg.ensure_dirs()
    voice_id = f"{_CLONE_SUFFIX}-{uuid.uuid4().hex[:8]}"
    dst = cfg.voices_dir / f"{voice_id}.wav"
    ensure_wav_24k_mono(sample_path, dst)
    entry = {
        "id": voice_id,
        "name": name.strip() or "我的声音",
        "provider": "mimo",  # 克隆目前走 MiMo
        "file": str(dst),
        "created": time.strftime("%Y-%m-%d %H:%M"),
    }
    reg = _load_registry(cfg)
    reg["clones"].append(entry)
    _save_registry(cfg, reg)
    return entry


def mimo_ready(cfg: Config, llm_cfg: dict) -> bool:
    """克隆声音依赖 MiMo 引擎:环境变量 Key,或 LLM 配置的 Key+MiMo 端点同时可用。

    注意:克隆走的是 MimoTTS(llm.openai 配置)。若用户把 LLM 配到了非 MiMo 的
    网关,即使有 Key 也会在合成时失败——入口处就拦,不等两分钟后报错。
    """
    import os

    if os.environ.get("MIMO_API_KEY"):
        return True
    oa = llm_cfg.get("openai", {}) or {}
    return bool(oa.get("api_key")) and "mimo" in (oa.get("base_url") or "").lower()


def list_clones(cfg: Config) -> list[dict]:
    return _load_registry(cfg).get("clones", [])


def delete_clone(cfg: Config, voice_id: str) -> bool:
    reg = _load_registry(cfg)
    before = len(reg["clones"])
    reg["clones"] = [c for c in reg["clones"] if c["id"] != voice_id]
    if len(reg["clones"]) == before:
        return False
    _save_registry(cfg, reg)
    sample = cfg.voices_dir / f"{voice_id}.wav"
    if sample.exists():
        sample.unlink(missing_ok=True)
    return True


def resolve_voice(cfg: Config, llm_cfg: dict, voice_selector: str) -> tuple:
    """把 UI 的声音选择器解析为 (TTSProvider, VoiceSpec)。

    选择器格式(前缀 ≠ 组 id,如组 id "elevenlabs" 的前缀是 "eleven"):
    "edge:xxx" / "mimo:xxx" / "clone:xxx" / "volc:xxx" / "ali:xxx" /
    "azure:xxx" / "volcpodcast:xxx" / "volcspeech:xxx" / "eleven:xxx"
    各家密钥取自 config['tts'] 对应小节(mimo 取 llm.openai)。
    """
    from .tts.base import VoiceSpec

    kind, _, rest = voice_selector.partition(":")
    rest = rest or "mimo_default"
    tts_cfg = cfg.data.get("tts", {})

    if kind == "edge":
        return EdgeTTS(), VoiceSpec(provider="edge", voice_id=rest, label=rest)

    if kind == "mimo":
        mimo = MimoTTS(llm_cfg.get("openai", {}))
        return mimo, VoiceSpec(provider="mimo", voice_id=rest, label=rest)

    if kind == "volc":
        from .tts.volc import VolcTTS

        return VolcTTS(tts_cfg.get("volc", {})), VoiceSpec(provider="volc", voice_id=rest, label=rest)

    if kind == "ali":
        from .tts.ali import AliTTS

        return AliTTS(tts_cfg.get("ali", {})), VoiceSpec(provider="ali", voice_id=rest, label=rest)

    if kind == "azure":
        from .tts.azure import AzureTTS

        return AzureTTS(tts_cfg.get("azure", {})), VoiceSpec(
            provider="azure", voice_id=rest, label=rest
        )

    if kind == "volcpodcast":
        from .tts.volc_podcast import VolcPodcastTTS

        return VolcPodcastTTS(tts_cfg.get("volc_podcast", {})), VoiceSpec(
            provider="volcpodcast", voice_id=rest, label="方舟双人播客"
        )

    if kind == "volcspeech":
        from .tts.volc_speech import VolcSpeechTTS

        # 豆包音色与方舟播客共用同一把语音技术 X-Api-Key;
        # pace_hint 是用户可配置字段(E3:此前被 pipeline 无条件覆盖成死配置)
        return VolcSpeechTTS({
            "api_key": tts_cfg.get("volc_podcast", {}).get("api_key", ""),
            "resource_id": tts_cfg.get("volc_speech", {}).get("resource_id", "seed-tts-2.0"),
            "pace_hint": tts_cfg.get("volc_speech", {}).get("pace_hint", ""),
        }), VoiceSpec(provider="volcspeech", voice_id=rest, label=rest)

    if kind == "eleven":
        from .tts.elevenlabs import ElevenLabsTTS

        return ElevenLabsTTS(tts_cfg.get("elevenlabs", {})), VoiceSpec(
            provider="elevenlabs", voice_id=rest, label=rest
        )

    if kind == _CLONE_SUFFIX:
        for c in list_clones(cfg):
            if c["id"] == rest:
                mimo = MimoTTS(llm_cfg.get("openai", {}))
                return mimo, VoiceSpec(
                    provider="mimo",
                    voice_id="clone",
                    clone_sample=Path(c["file"]),
                    label=c["name"],
                )
        raise RuntimeError("克隆声音不存在，可能已被删除")

    raise RuntimeError(f"无法识别的声音选择：{voice_selector}")


# 分组 → 解锁所需凭据(None = 永远可用)
GROUP_REQUIREMENTS = {
    "edge": None,
    "mimo": ("llm", "openai", "api_key"),
    "volcspeech": ("tts", "volc_podcast", "api_key"),   # 豆包音色与播客同把语音 Key
    "volcpodcast": ("tts", "volc_podcast", "api_key"),
    "volc": ("tts", "volc", "token"),
    "ali": ("tts", "ali", "api_key"),
    "elevenlabs": ("tts", "elevenlabs", "api_key"),
    "azure": ("tts", "azure", "api_key"),
}

GROUP_NAMES = {
    "mimo": "MiMo 音色", "volcspeech": "豆包音色(语音合成大模型)",
    "volcpodcast": "方舟双人播客", "volc": "豆包 TTS(旧版)",
    "ali": "阿里 CosyVoice", "elevenlabs": "ElevenLabs", "azure": "Azure",
}


def _group_unlocked(cfg: Config, group: str) -> bool:
    req = GROUP_REQUIREMENTS.get(group)
    if req is None:
        return True
    section = cfg.llm if req[0] == "llm" else cfg.data.get("tts", {})
    node = section.get(req[1], {})
    return bool((node.get(req[2]) or "").strip()) if isinstance(node, dict) else False


_GRANT_CACHE: dict = {"resources": None, "ts": 0.0}
_PROBE_TIMEOUT = 8


async def _granted_seed_resources(cfg: Config) -> set[str]:
    """探测语音 Key 已授权的 seed-tts 资源版本(2.0/1.0)。

    探测法:GET 端点,400(升级要求)=已授权,200=已授权,401/403/404=未授权。
    三个约束(E4):①探测走线程池,不阻塞事件循环(同步 GET 曾让整个服务停摆 ~16s);
    ②只缓存非空结果——网络抖动导致的空探测不缓存,下次再试(空结果缓存 10 分钟
    曾让豆包音色组「静默消失」);③非 200/400 一律视为未授权,不做假阳性放行。
    """
    import asyncio
    import time as _time

    if _GRANT_CACHE["resources"] and _time.time() - _GRANT_CACHE["ts"] < 600:
        return _GRANT_CACHE["resources"]
    key = cfg.data.get("tts", {}).get("volc_podcast", {}).get("api_key", "")
    granted: set[str] = set()
    if key:
        def _probe() -> set[str]:
            import httpx

            found: set[str] = set()
            for rid in ("seed-tts-2.0", "seed-tts-1.0"):
                try:
                    r = httpx.get(
                        "https://openspeech.bytedance.com/api/v3/tts/bidirection",
                        headers={"X-Api-Key": key, "X-Api-Resource-Id": rid},
                        timeout=_PROBE_TIMEOUT,
                    )
                    if r.status_code in (200, 400):  # 400=upgrade required,即已授权
                        found.add(rid)
                except httpx.HTTPError:
                    continue  # 探测失败不算未授权,也不缓存
            return found

        granted = await asyncio.to_thread(_probe)
    if granted:  # 只缓存非空结果
        _GRANT_CACHE["resources"] = granted
        _GRANT_CACHE["ts"] = _time.time()
    return granted


async def _filter_volcspeech(voices: list[dict], cfg: Config) -> list[dict]:
    """豆包音色按已授权资源过滤:2.0 音色(uranus 系)需 seed-tts-2.0,其余需 1.0。"""
    granted = await _granted_seed_resources(cfg)
    if "seed-tts-2.0" in granted and "seed-tts-1.0" in granted:
        return voices
    if "seed-tts-2.0" in granted:
        keep = ("uranus",)
    elif "seed-tts-1.0" in granted:
        keep = ("moon", "mars", "saturn")
    else:
        return []  # 一个版本都没授权 → 全部隐藏(设置页有开通指引)
    return [v for v in voices if any(k in v["ShortName"].lower() for k in keep)]


async def list_builtin_voices(cfg: Config, format: str | None = None) -> dict:
    """按已配置凭据返回可用音色组;未配置的归入 locked(前端展示锁定提示)。

    format: "solo" 排除双人对谈组;"dual" 只返回双人对谈组;None = 全部。
    """
    edge_voices = await EdgeTTS.list_voices_cached(cfg.data_dir / "edge_voices.json")
    from .tts.ali import AliTTS
    from .tts.azure import AzureTTS
    from .tts.elevenlabs import ElevenLabsTTS
    from .tts.volc import VolcTTS
    from .tts.volc_podcast import VolcPodcastTTS
    from .tts.volc_speech import VolcSpeechTTS

    all_groups = {
        "edge": edge_voices,
        "mimo": MimoTTS.BUILTIN_VOICES,
        "volc": VolcTTS.BUILTIN_VOICES,
        "ali": AliTTS.BUILTIN_VOICES,
        "elevenlabs": ElevenLabsTTS.BUILTIN_VOICES,
        "azure": AzureTTS.BUILTIN_VOICES,
        "volcpodcast": VolcPodcastTTS.BUILTIN_VOICES,
        "volcspeech": await _filter_volcspeech(VolcSpeechTTS.BUILTIN_VOICES, cfg),
    }
    usable, locked = {}, []
    for group, voices in all_groups.items():
        if format == "dual" and group not in ("volcpodcast",):
            continue  # 双人对谈只用方舟播客音色对
        if format == "solo" and group == "volcpodcast":
            continue  # 单人朗读不显示双人对谈组
        if _group_unlocked(cfg, group):
            usable[group] = voices
        elif group in GROUP_NAMES:
            locked.append({"id": group, "name": GROUP_NAMES[group]})
    return {"builtin": usable, "locked": locked}
