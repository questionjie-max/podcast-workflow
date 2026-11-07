"""真实连通性探测（doctor --probe）。

`doctor` 默认只检查“配置有没有填”，但“填了”不等于“能用”。本模块对已配置的
provider 发最小真实请求：LLM 一句短对话、TTS 两个字的音频、搜索一次单条查询。
只有显式 `--probe` 才执行，避免无意中产生费用。
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .logging_setup import redact
from .providers.llm.base import make_llm


@dataclass
class ProbeResult:
    name: str
    status: str  # ok | fail | skip
    detail: str
    ms: int = 0

    @property
    def icon(self) -> str:
        return {"ok": "✅", "fail": "❌", "skip": "⭕"}.get(self.status, "•")


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def probe_llm(cfg: Config) -> ProbeResult:
    start = time.perf_counter()
    if cfg.llm.get("provider") == "mock":
        return ProbeResult("写作引擎", "skip", "演示模式，无需联网")
    llm = make_llm(cfg.llm)
    model = (cfg.llm.get("openai") or {}).get("model", "?")
    try:
        text = llm.chat(
            "probe", "你是连通性测试助手。", "只回复 OK 两个字母。",
            temperature=0, max_tokens=8, timeout=20,
        )
        return ProbeResult(
            "写作引擎", "ok", f"{model} 返回 {text.strip()[:20]!r}", _ms(start)
        )
    except Exception as e:
        return ProbeResult("写作引擎", "fail", f"{type(e).__name__}: {e}", _ms(start))


def _probe_tts_one(label: str, provider, voice_id: str, timeout: float = 30) -> ProbeResult:
    from .providers.tts.base import VoiceSpec

    start = time.perf_counter()

    async def _run() -> None:
        with tempfile.TemporaryDirectory(prefix="ps_probe_") as td:
            out = Path(td) / "probe.mp3"
            await provider.synth(
                "你好",
                VoiceSpec(provider=provider.name, voice_id=voice_id, label=label),
                None,
                out=out,
            )
            if not out.is_file() or out.stat().st_size == 0:
                raise RuntimeError("返回空音频")

    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
        return ProbeResult(label, "ok", "最小合成成功", _ms(start))
    except Exception as e:
        return ProbeResult(label, "fail", f"{type(e).__name__}: {e}", _ms(start))


def probe_tts(cfg: Config) -> list[ProbeResult]:
    from .providers import voices as voice_reg
    from .providers.tts.ali import AliTTS
    from .providers.tts.azure import AzureTTS
    from .providers.tts.edge import EdgeTTS
    from .providers.tts.elevenlabs import ElevenLabsTTS
    from .providers.tts.mimo import MimoTTS
    from .providers.tts.volc import VolcTTS
    from .providers.tts.volc_podcast import probe_connection as podcast_probe

    tts = cfg.data.get("tts", {})
    results: list[ProbeResult] = []

    # 免费 edge：永远可探
    results.append(_probe_tts_one("edge-tts", EdgeTTS(), "zh-CN-XiaoxiaoNeural"))

    llm_key = (cfg.llm.get("openai") or {}).get("api_key", "")
    if llm_key:
        mimo = MimoTTS(cfg.llm.get("openai", {}))
        results.append(
            _probe_tts_one("MiMo TTS", mimo, mimo.BUILTIN_VOICES[0]["ShortName"])
        )

    volc_cfg = tts.get("volc") or {}
    if volc_cfg.get("app_id") and volc_cfg.get("token"):
        volc = VolcTTS(volc_cfg)
        results.append(
            _probe_tts_one("豆包 TTS(旧版)", volc, volc.BUILTIN_VOICES[0]["ShortName"])
        )

    ali_cfg = tts.get("ali") or {}
    if ali_cfg.get("api_key"):
        ali = AliTTS(ali_cfg)
        results.append(
            _probe_tts_one("阿里 CosyVoice", ali, ali.BUILTIN_VOICES[0]["ShortName"])
        )

    eleven_cfg = tts.get("elevenlabs") or {}
    if eleven_cfg.get("api_key"):
        eleven = ElevenLabsTTS(eleven_cfg)
        results.append(
            _probe_tts_one(
                "ElevenLabs", eleven, eleven.BUILTIN_VOICES[0]["ShortName"]
            )
        )

    azure_cfg = tts.get("azure") or {}
    if azure_cfg.get("api_key"):
        azure = AzureTTS(azure_cfg)
        results.append(
            _probe_tts_one("Azure", azure, azure.BUILTIN_VOICES[0]["ShortName"])
        )

    # 方舟播客：只做建连探测（真实合成是一整期，成本高）
    podcast_cfg = tts.get("volc_podcast") or {}
    if podcast_cfg.get("api_key"):
        start = time.perf_counter()
        ok, detail = podcast_probe(
            podcast_cfg["api_key"], podcast_cfg.get("resource_id") or "volc.service_type.10050"
        )
        results.append(
            ProbeResult(
                "方舟双人播客",
                "ok" if ok else "fail",
                detail,
                _ms(start),
            )
        )

    # 豆包音色：只探测已授权资源（GET，不产生合成费用）
    if podcast_cfg.get("api_key"):
        start = time.perf_counter()
        try:
            granted = asyncio.run(voice_reg._granted_seed_resources(cfg))
            results.append(
                ProbeResult(
                    "豆包音色授权",
                    "ok" if granted else "fail",
                    "已授权：" + "、".join(sorted(granted)) if granted else "未探测到已授权资源",
                    _ms(start),
                )
            )
        except Exception as e:
            results.append(
                ProbeResult("豆包音色授权", "fail", f"{type(e).__name__}: {e}", _ms(start))
            )

    return results


def probe_search(cfg: Config) -> list[ProbeResult]:
    from .providers.search import BochaSearch, TavilySearch

    search = cfg.data.get("search", {})
    results: list[ProbeResult] = []
    for name, cls in (("tavily", TavilySearch), ("bocha", BochaSearch)):
        section = search.get(name) or {}
        if not section.get("api_key"):
            continue
        start = time.perf_counter()
        try:
            hits = asyncio.run(
                asyncio.wait_for(cls(section).search("播客", max_results=1), timeout=20)
            )
            results.append(
                ProbeResult(f"搜索 {name}", "ok", f"返回 {len(hits)} 条", _ms(start))
            )
        except Exception as e:
            results.append(
                ProbeResult(f"搜索 {name}", "fail", f"{type(e).__name__}: {e}", _ms(start))
            )
    return results


def run_probes(cfg: Config) -> list[ProbeResult]:
    results = [probe_llm(cfg)]
    results.extend(probe_tts(cfg))
    results.extend(probe_search(cfg))

    asr = (cfg.data.get("asr") or {}).get("volc") or {}
    if asr.get("app_id") and asr.get("token"):
        results.append(
            ProbeResult(
                "豆包 ASR",
                "skip",
                f"已配置（app_id {str(asr['app_id'])[:4]}****）；语音识别需要真实录音，doctor 不代发",
            )
        )
    else:
        results.append(ProbeResult("豆包 ASR", "skip", "未配置（语音输入不可用）"))

    return results


def format_results(results: list[ProbeResult]) -> str:
    lines = []
    for r in results:
        suffix = f"（{r.ms}ms）" if r.ms else ""
        lines.append(f"{r.icon} {r.name}: {redact(r.detail)}{suffix}")
    return "\n".join(lines)
