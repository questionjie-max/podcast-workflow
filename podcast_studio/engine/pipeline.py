"""节目流水线：状态落盘、断点续跑、后台线程执行。

状态机：
  topic 模式:   created → researching → awaiting_topic → scripting → compliance
                → awaiting_review → synthesizing → done
  material 模式: created → scripting → compliance → awaiting_review → ...
任何一步失败进入 error（保留现场，可从失败处重试）。
"""

import asyncio
import datetime
import json
import re
import threading
import time
import uuid
from pathlib import Path

from ..config import Config
from ..logging_setup import get_logger
from ..providers.llm.base import LLMProvider
from ..providers.voices import resolve_voice
from ..styles import edge_params, get_profile
from . import audio, steps
from .chunker import chunk_script, strip_tags
from .usage import UsageMeter

# 需要用户确认的中间状态
GATE_STATES = {"awaiting_topic", "awaiting_review"}

logger = get_logger("pipeline")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Episode:
    def __init__(self, cfg: Config, ep_dir: Path):
        self.cfg = cfg
        self.dir = ep_dir
        self.lock = threading.Lock()
        self.state: dict = {}
        self._load()

    # ---- 持久化 -------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def state_file(self) -> Path:
        return self.dir / "state.json"

    def _load(self) -> None:
        if self.state_file.exists():
            self.state = _json_load(self.state_file)
        else:
            raise FileNotFoundError(f"节目不存在：{self.dir}")

    def save(self) -> None:
        """原子写 state.json（用量账本一并落盘）。

        后台线程持续更新状态、前端同时轮询；直接 write_text 会让读端偶发
        读到半截 JSON（测试里表现为 json.decoder.JSONDecodeError）。
        """
        meter = getattr(self, "_usage_meter", None)
        if meter is not None:
            self.state["usage"] = meter.to_dict()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(f"state.json.{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(_json_dump(self.state), encoding="utf-8")
        tmp.replace(self.state_file)

    def _set(self, state: str, log: str | None = None, **extra) -> None:
        with self.lock:
            self.state["state"] = state
            self.state["error"] = None
            for k, v in extra.items():
                self.state[k] = v
            if log:
                self.state.setdefault("log", []).append({"t": _now(), "msg": log})
            self.save()

    @staticmethod
    def _sanitize_raw(text: str) -> str:
        """原始异常回显前做密钥特征脱敏(Q7:provider 错误串可能拼进上游响应体)。"""
        import re

        def _mask(m):
            v = m.group(0)
            return v[:4] + "****" + v[-4:] if len(v) > 12 else "****"

        return re.sub(
            r"(?:sk-[A-Za-z0-9_-]{8,}|ark-[A-Za-z0-9-]{8,}|oc_sk_[A-Za-z0-9_-]{8,}"
            r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
            _mask, text)

    def _fail(self, step: str, err: Exception) -> None:
        with self.lock:
            self.state["state"] = "error"
            # 双通道人话化(异步任务侧):error 给用户看,error_raw 留原始异常给排查
            self.state["error"] = f"[{step}] {self._friendly_error(err)}"
            self.state["error_raw"] = self._sanitize_raw(f"{type(err).__name__}: {err}")
            self.state["failed_step"] = step
            self.state.setdefault("log", []).append({"t": _now(), "msg": f"❌ [{step}] {err}"})
            self.save()

    @staticmethod
    def _friendly_error(err: Exception) -> str:
        """把常见裸异常翻译成「人话+下一步」;本身就是友好中文的原文保留。"""
        s = str(err)
        low = s.lower()
        if "ffmpeg" in low and ("no such file" in low or "not found" in low or "is not recognized" in low):
            return ("没有找到 ffmpeg(合成音频的必备组件)。"
                    "请安装后重试:macOS `brew install ffmpeg` / Ubuntu `sudo apt install ffmpeg` / Windows `winget install ffmpeg`")
        if "timed out" in low or "timeout" in low:
            return "网络请求超时了。检查本机网络后重试;如果走代理,确认代理工作正常。"
        if "no space left" in low or "disk quota" in low:
            return "磁盘空间不足。清理一些空间后重试。"
        if "connection" in low and ("refused" in low or "reset" in low or "unreachable" in low):
            return "连不上服务(网络被拒)。检查接口地址、代理或本机网络后重试。"
        return s

    def _apply_pace(self, provider) -> str:
        """语速选择(慢/标准/快)映射到 rate;豆包音色附带语速稳定指令(E6 两处共用)。

        pace_hint 三级来源:config 用户配置(resolve_voice 透传) > 节目类型默认 >
        不设置。pipeline 只在 provider 还没有 pace_hint 时给默认值,不覆盖用户配置(E3)。
        """
        pace = self.state.get("pace") or "标准"
        pace_rate = {"慢": "-15%", "标准": "+0%", "快": "+12%"}.get(pace, "+0%")
        if provider.name == "volcspeech" and not getattr(provider, "pace_hint", ""):
            if self.state["style"] == "bedtime":
                provider.pace_hint = "语速放慢,平稳温柔,不要忽快忽慢"
            else:
                provider.pace_hint = "语速平稳,不要忽快忽慢"
            if pace == "慢":
                provider.pace_hint += ",可以说得再慢一点"
        return pace_rate

    # ---- 创建 ---------------------------------------------------------------
    @classmethod
    def create(cls, cfg: Config, *, mode: str, input_text: str, style: str,
               voice_id: str, minutes: int, title: str | None = None,
               format: str = "solo", structure: str | None = None,
               mood: str | None = None, pace: str | None = None,
               origin: str = "real") -> "Episode":
        cfg.ensure_dirs()
        ep_id = time.strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:4]
        ep_dir = cfg.episodes_dir / ep_id
        ep_dir.mkdir(parents=True, exist_ok=True)
        ep = cls.__new__(cls)
        ep.cfg = cfg
        ep.dir = ep_dir
        ep.lock = threading.Lock()
        ep.state = {
            "id": ep_id,
            "created": _now(),
            "mode": mode,  # topic | material
            "input": input_text.strip(),
            "style": style,
            "voice_id": voice_id,
            "minutes": int(minutes),
            "title": title,
            "target_chars": None,
            "structure": None,
            "mood": None,
            "pace": None,
            "format": format if format in ("solo", "dual") else "solo",
            "state": "created",
            "origin": origin if origin in ("real", "demo") else "real",
            "error": None,
            "candidates": [],
            "script": None,
            "compliance": None,
            "synthesis": None,
            "log": [],
        }
        from . import calibration

        ep.state["target_chars"] = calibration.target_chars(cfg, style, int(minutes))
        ep.state["calibrated"] = calibration.has_samples(cfg, style)  # False=首期,前端提示「越用越准」
        if structure:
            ep.state["structure"] = structure
        if mood:
            ep.state["mood"] = mood
        if pace:
            ep.state["pace"] = pace
        ep.save()
        return ep

    # ---- 各步骤（同步方法，供线程调用） ----------------------------------------
    def _usage(self) -> UsageMeter:
        """惰性创建用量账本;旧节目无 usage 字段时从 0 开始。"""
        meter = getattr(self, "_usage_meter", None)
        if meter is None:
            from .usage import UsageMeter

            meter = UsageMeter.from_dict(self.state.get("usage"))
            self._usage_meter = meter
        return meter

    def _llm(self) -> LLMProvider:
        from ..providers.llm.base import make_llm
        from .usage import CountingLLM

        # 记账包装对调用方透明:所有步骤共用同一份 meter，随时可落盘
        return CountingLLM(make_llm(self.cfg.llm), self._usage())

    def _profile(self) -> dict:
        return get_profile(self.state["style"])

    def _search_setup(self):
        """搜索配置三件套:(provider, max_rounds, kid_safe)。无 provider 返回 None。"""

        from ..providers.search import MAX_ROUNDS_LIMIT, make_search

        search_cfg = self.cfg.data.get("search", {})
        provider = make_search(search_cfg)
        if not provider:
            return None
        try:
            max_rounds = max(1, min(MAX_ROUNDS_LIMIT, int(search_cfg.get("max_rounds", 2))))
        except (TypeError, ValueError):
            max_rounds = 2
        kid_cfg = search_cfg.get("kid_safe")
        kid_safe = bool(kid_cfg) if kid_cfg is not None else self.state["style"] == "bedtime"
        return provider, max_rounds, kid_safe

    def run_research(self) -> None:
        try:
            self._set("researching", f"🔍 正在为「{self.state['input']}」寻找选题…")
            setup = self._search_setup()
            sources: list[dict] = []
            results = None
            candidates = None
            if setup:
                import asyncio

                from ..providers.search import sanitize_results

                provider, max_rounds, kid_safe = setup
                try:
                    raw = asyncio.run(provider.search(self.state["input"], max_results=6))
                    results = sanitize_results(raw, kid_safe=kid_safe)
                    sources = [
                        {"title": r.get("title", ""),
                         "domain": (r.get("url") or "").split("/")[2] if (r.get("url") or "").count("/") >= 2 else "",
                         "snippet": (r.get("snippet") or "")[:100]}
                        for r in results[:5]
                    ]
                    if kid_safe and raw and len(results) < len(raw):
                        self.state.setdefault("log", []).append(
                            {"t": _now(), "msg": f"🛡️ 儿童模式:过滤了 {len(raw) - len(results)} 条不合适的结果"}
                        )
                except Exception as e:
                    self.state.setdefault("log", []).append(
                        {"t": _now(), "msg": f"⚠️ 搜索失败,改用模型知识:{e}"}
                    )
                candidates = steps.research(
                    self._llm(), self._profile(), self.state["input"],
                    search_results=results,
                )
                # 事实核查轮:预算 = 总轮次 - 初搜 1 轮
                if max_rounds >= 2 and candidates:
                    def search_fn(q: str) -> list[dict]:
                        return sanitize_results(
                            asyncio.run(provider.search(q, max_results=6)),
                            kid_safe=kid_safe,
                        )

                    candidates = steps.factcheck_candidates(
                        self._llm(), candidates, search_fn, max_rounds=max_rounds,
                    )
            else:
                candidates = steps.research(self._llm(), self._profile(), self.state["input"])

            extra = {"candidates": candidates}
            if sources:
                extra["sources"] = sources
            self._set("awaiting_topic", f"✅ 生成 {len(candidates)} 个选题", **extra)
        except Exception as e:
            self._fail("research", e)

    def run_material_title(self) -> None:
        """素材模式：从素材里起标题，然后直接写稿。"""
        try:
            self._set("scripting", "📝 正在根据素材写稿…")
            if not self.state.get("title"):
                material = self.state["input"]
                title = material.strip().splitlines()[0][:40] if material.strip() else "我的节目"
                try:
                    out = self._llm().chat(
                        task="title",
                        system="你是标题编辑，只输出标题本身。",
                        user=f"给以下素材起的节目标题（15字以内，直接输出标题本身，不要任何解释）：\n{material[:800]}",
                        temperature=0.5,
                    )
                    title = out.strip().strip('"“”')[:40] or title
                except Exception:
                    pass  # 标题生成失败不阻塞，用第一行兜底
                self.state["title"] = title
                self.save()
            self._run_script()
        except Exception as e:
            self._fail("material_title", e)

    def select_topic(self, index: int) -> None:
        cands = self.state.get("candidates") or []
        if not (0 <= index < len(cands)):
            raise ValueError("选题序号无效")
        c = cands[index]
        self.state["title"] = c["title"]
        self.save()
        self._set("scripting", f"📝 已选：{c['title']}，开始写稿…")
        self._run_script()

    def _content_block(self) -> str:
        if self.state["mode"] == "material":
            return f"素材如下（以此为准改写，不要编造素材外的事实）：\n{self.state['input']}"
        return f"选题：{self.state.get('title') or self.state['input']}"

    def _run_script(self) -> None:
        try:
            profile = self._profile()
            from . import structures

            beats = None
            structure_id = self.state.get("structure")
            if structure_id:
                beats = structures.beat_plan(structure_id, self.state.get("target_chars") or 0)
            if not beats and profile.get("emotion_arc", True):
                mood_obj = next((m for m in profile.get("moods", [])
                                 if m["id"] == self.state.get("mood")), None)
                arc = steps.plan_emotion_arc(
                    self._llm(), profile, self._content_block(),
                    self.state["minutes"], mood=mood_obj)
                if arc:
                    self.state["emotion_arc"] = arc
                    total = self.state.get("target_chars") or 0
                    beats = [{"name": a["beat"], "emotion": a["emotion"],
                              "chars": int(total * a["share"]),
                              "desc": a.get("intent", "")} for a in arc]
                    self.state.setdefault("log", []).append(
                        {"t": _now(), "msg": "🎭 情绪弧线:" + " → ".join(
                            f"{a['beat']}({a['emotion']})" for a in arc)})
            mood_obj = next((m for m in profile.get("moods", [])
                             if m["id"] == self.state.get("mood")), None)
            script = steps.write_script(
                self._llm(), self._profile(), self.state["minutes"], self._content_block(),
                target_chars=self.state.get("target_chars"), beats=beats,
                mood=mood_obj,
            )
            self._set("compliance", "🛡️ 文稿完成，正在合规审查…", script=script)
            (self.dir / "script.md").write_text(script, encoding="utf-8")
            self._run_compliance()
        except Exception as e:
            self._fail("script", e)

    def regenerate_script(self) -> None:
        self._set("scripting", "📝 重新生成文稿…")
        self._run_script()

    def update_script(self, text: str) -> None:
        with self.lock:
            self.state["script"] = text.strip()
            self.state["compliance"] = None  # 改稿后需重新审查
            self.save()
        (self.dir / "script.md").write_text(self.state["script"], encoding="utf-8")

    def run_compliance(self) -> None:
        try:
            self._set("compliance", "🛡️ 正在合规审查…")
            self._run_compliance()
        except Exception as e:
            self._fail("compliance", e)

    def _run_compliance(self) -> None:
        report = steps.compliance_check(self._llm(), self._profile(), self.state["script"])
        passed = report.get("pass")
        msg = "✅ 合规审查通过，请确认文稿" if passed else "⚠️ 合规审查有问题，请查看报告后修改"
        self._set("awaiting_review", msg, compliance=report)

    def approve(self) -> None:
        # 内部状态闸(Q4):即使绕过端点检查,也不能从任意状态直接进合成
        if self.state["state"] not in ("awaiting_review", "error"):
            raise RuntimeError(f"当前状态 {self.state['state']} 不可开始合成")
        self._set("synthesizing", "🎙️ 开始语音合成…")
        self.state["synthesis"] = {"total": 0, "done": 0, "current": ""}
        self.save()

    def _precheck_synthesis(self) -> None:
        """合成前置体检:ffmpeg 在不在、磁盘够不够,给人话报错而非裸 Errno。"""
        import shutil

        if not shutil.which("ffmpeg"):
            raise RuntimeError("没有找到 ffmpeg(合成音频的必备组件)")
        free = shutil.disk_usage(self.dir).free
        if free < 200 * 1024 * 1024:
            raise RuntimeError(f"磁盘剩余空间不足 200MB(剩 {free // 1024 // 1024}MB),请清理后重试")

    def run_synthesis(self) -> None:
        """TTS 合成全部分段 → 拼接 → 成品 MP3。支持从失败分段续跑。"""
        try:
            self._precheck_synthesis()
            profile = self._profile()
            chunks = chunk_script(self.state["script"], profile)
            if not chunks:
                raise RuntimeError("文稿为空，无法合成")
            provider, voice = resolve_voice(
                self.cfg, self.cfg.llm, self.state["voice_id"]
            )
            fallback = profile.get("edge_fallback", {"rate": "+0%", "pitch": "+0Hz"})
            pace_rate = self._apply_pace(provider)

            # 双人对谈:文稿改写成 A/B 轮次,整期合成走方舟播客(配了 Key)或
            # edge 双音色轮替(免费降级)
            if self.state.get("format") == "dual":
                self._set("synthesizing", "👥 正在把文稿改写成 A/B 对话…")
                turns = steps.dialogize_script(
                    self._llm(), profile, self.state["script"])
                self.state["dialogue"] = turns
                self.save()
                self._run_dual_synthesis(turns)
                return

            with self.lock:
                self.state["synthesis"] = {
                    "total": len(chunks),
                    "done": 0,
                    "current": "",
                    "voice": voice.label,
                    "provider": provider.name,
                }
                self.state.setdefault("log", []).append(
                    {"t": _now(),
                     "msg": f"🎙️ 共 {len(chunks)} 段，声音：{voice.label}（{provider.name}，"
                            + ("支持情绪" if provider.supports_emotion else "该音色不支持情绪，已按语速/音调降级") + "）"}
                )
                self.save()

            chunk_dir = self.dir / "chunks"
            chunk_dir.mkdir(exist_ok=True)
            # 拼接顺序固定按分段序号;已完成的分段跳过 → 断点续跑
            files: list[Path] = [chunk_dir / f"chunk_{c.index:03d}.mp3" for c in chunks]
            todo = [c for c in chunks if not files[chunks.index(c)].exists()]

            def _synth_one(ch) -> None:
                out_raw = chunk_dir / f"chunk_{ch.index:03d}.raw"
                out_mp3 = chunk_dir / f"chunk_{ch.index:03d}.mp3"
                style = ch.style if provider.supports_emotion else None
                params = fallback if provider.supports_emotion else edge_params(profile, ch.emotion_raw)
                eff_rate = pace_rate if provider.name in ("volcspeech", "edge") else params.get("rate", "+0%")
                try:
                    asyncio.run(
                        provider.synth(
                            ch.text,
                            voice,
                            style,
                            rate=eff_rate,
                            pitch=params.get("pitch", "+0Hz"),
                            out=out_raw,
                        )
                    )
                    note = getattr(provider, "last_fallback", None)
                    if note and not self.state.get("voice_note"):
                        with self.lock:
                            self.state["voice_note"] = note
                            self.save()
                    audio.to_mp3(out_raw, out_mp3)
                    self._usage().add_tts(len(ch.text))
                except Exception as e:
                    out_raw.unlink(missing_ok=True)
                    raise RuntimeError(f"第 {ch.index + 1} 段合成失败：{e}") from e
                finally:
                    out_raw.unlink(missing_ok=True)

            if len(todo) <= 1:
                if todo:
                    with self.lock:
                        self.state["synthesis"]["current"] = (
                            f"第 {todo[0].index + 1}/{len(chunks)} 段 · {todo[0].emotion_raw or '正文'}"
                        )
                        self.save()
                    _synth_one(todo[0])
                    with self.lock:
                        self.state["synthesis"]["done"] = len(chunks)
                        self.state["synthesis"]["current"] = ""
                        self.save()
            else:
                # 多段并行合成(≤3 路):整期合成时间约降一半;拼接顺序不受影响
                from concurrent.futures import ThreadPoolExecutor, as_completed

                failed: Exception | None = None
                done_count = len(chunks) - len(todo)  # 断点续跑时计入已有分段
                with ThreadPoolExecutor(max_workers=min(3, len(todo)),
                                        thread_name_prefix="ps-chunk") as pool:
                    futures = {pool.submit(_synth_one, c): c for c in todo}
                    try:
                        for fut in as_completed(futures):
                            fut.result()  # 任一段失败 → 取消未启动的在途任务
                            done_count += 1
                            with self.lock:
                                self.state["synthesis"]["done"] = done_count
                                self.state["synthesis"]["current"] = ""
                                self.save()
                    except Exception as e:
                        for f2 in futures:
                            f2.cancel()
                        failed = e
                # 走到这里 = 池已等完全部在途任务;计数以磁盘实况对账(UI 进度真实)
                with self.lock:
                    self.state["synthesis"]["done"] = len(list(chunk_dir.glob("chunk_*.mp3")))
                    self.save()
                if failed:
                    raise failed

            with self.lock:
                self.state["synthesis"]["current"] = "拼接与响度归一化…"
                self.save()
            self._build_final(files)
            self.state["duration_sec"] = audio.duration_seconds(self.dir / "audio.mp3")
            try:
                from . import calibration

                if self.state.get("origin") == "demo":
                    # 示例期不参与时长校准:演示音色/参数与用户真实声音无关,学了就是污染
                    self.state.setdefault("log", []).append(
                        {"t": _now(), "msg": "📏 示例节目,不参与时长校准"})
                else:
                    clean = strip_tags(self.state["script"])
                    entry = calibration.record(self.cfg, self.state["style"],
                                               len(clean.replace("\n", "")),
                                               self.state["duration_sec"] or 0)
                    self.state.setdefault("log", []).append(
                        {"t": _now(),
                         "msg": f"📏 时长校准:实测 {entry.get('last', {}).get('cps', 0)} 字符/秒"
                                f"(样本 {entry.get('samples', 0)} 期)"})
            except Exception:
                pass
            (self.dir / "script_clean.txt").write_text(
                strip_tags(self.state["script"]), encoding="utf-8"
            )
            # shownotes:尽力而为,失败不阻塞交付
            try:
                notes = steps.generate_shownotes(
                    self._llm(), profile, title=self.state.get("title") or "未命名节目",
                    script=self.state["script"], minutes=self.state["minutes"],
                )
                self.state["shownotes"] = notes
                (self.dir / "shownotes.md").write_text(notes, encoding="utf-8")
            except Exception as e:
                self.state.setdefault("log", []).append(
                    {"t": _now(), "msg": f"⚠️ shownotes 生成失败(不影响音频):{e}"}
                )
            self._set("done", "🎉 完成！可以试听和下载了")
        except Exception as e:
            logger.exception("合成失败 episode=%s", self.id)
            self._fail("synthesis", e)

    # ---- 单段重合成 ------------------------------------------------------------
    def chunks_info(self) -> list[dict]:
        """按当前文稿重新解析分段信息(与合成时同一确定性算法),供前端展示。"""
        chunks = chunk_script(self.state["script"] or "", self._profile())
        chunk_dir = self.dir / "chunks"
        return [
            {
                "index": c.index,
                "emotion": c.emotion_raw,
                "text": c.text[:60] + ("…" if len(c.text) > 60 else ""),
                "chars": len(c.text),
                "has_file": (chunk_dir / f"chunk_{c.index:03d}.mp3").exists(),
            }
            for c in chunks
        ]

    def _dual_speakers(self) -> tuple[str, str]:
        """双人对谈的两个音色:方舟用配置对;免费降级用 edge 男女声。"""
        if self.state["style"] == "bedtime":
            return ("zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural")
        return ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural")

    def _run_dual_synthesis(self, turns: list[dict]) -> None:
        """双人对谈合成:优先方舟播客(一次会话整期),否则 edge 双音色轮替。"""
        import asyncio

        # 失败时清理 dual_raw 残留(成功路径已有 unlink)
        def _cleanup_dual_raw() -> None:
            (self.dir / "dual_raw.mp3").unlink(missing_ok=True)

        from ..providers.search import MAX_ROUNDS_LIMIT  # noqa: F401 保持 import 风格统一
        from ..providers.tts.base import VoiceSpec
        from ..providers.tts.volc_podcast import (
            VolcPodcastTTS,
        )

        vp_cfg = self.cfg.data.get("tts", {}).get("volc_podcast", {})
        a_voice, b_voice = self._dual_speakers()
        total = len(turns)

        def log(msg: str) -> None:
            with self.lock:
                self.state["synthesis"] = {
                    "total": total, "done": self.state["synthesis"].get("done", 0)
                    if self.state.get("synthesis") else 0,
                    "current": msg, "voice": "A/B 双人", "provider": "dual"}
                self.save()

        # ---- 路径 1:方舟播客(配了 X-Api-Key)----
        if vp_cfg.get("api_key"):
            log("🎙️ 方舟播客:一次会话合成整期双人播客…")
            tts = VolcPodcastTTS(vp_cfg)
            from ..providers.tts.volc_podcast import DEFAULT_SPEAKERS

            voice = VoiceSpec(provider="volcpodcast",
                              voice_id="|".join(vp_cfg.get("speakers") or DEFAULT_SPEAKERS),
                              label="方舟双人播客")
            try:
                joined = " ".join(t["text"] for t in turns)
                asyncio.run(tts.synth(
                    joined, voice, None,
                    out=self.dir / "dual_raw.mp3"))
                self._usage().add_tts(len(joined))
                audio.to_mp3(self.dir / "dual_raw.mp3", self.dir / "audio.mp3")
                (self.dir / "dual_raw.mp3").unlink(missing_ok=True)
                self.state["duration_sec"] = audio.duration_seconds(self.dir / "audio.mp3")
                self._set("done", "🎉 双人播客完成(方舟)")
                return
            except Exception as e:
                _cleanup_dual_raw()
                self.state.setdefault("log", []).append(
                    {"t": _now(), "msg": f"⚠️ 方舟播客失败,自动降级免费双音色:{e}"})
                self.save()

        # ---- 路径 2:免费降级,edge 双音色轮替 ----
        with self.lock:
            self.state["synthesis"] = {
                "total": total, "done": 0, "current": "", "voice": "A/B 双音色(免费)",
                "provider": "edge"}
            self.save()
        from ..providers.tts.base import VoiceSpec
        from ..providers.tts.edge import EdgeTTS

        edge = EdgeTTS()
        chunk_dir = self.dir / "chunks"
        chunk_dir.mkdir(exist_ok=True)
        files: list[Path] = []
        for i, turn in enumerate(turns):
            out_mp3 = chunk_dir / f"dual_{i:03d}.mp3"
            if not out_mp3.exists():
                with self.lock:
                    self.state["synthesis"]["current"] = f"第 {i + 1}/{total} 轮 · {turn['speaker']} 说"
                    self.save()
                vid = a_voice if turn["speaker"] == "A" else b_voice
                try:
                    asyncio.run(edge.synth(
                        turn["text"],
                        VoiceSpec(provider="edge", voice_id=vid, label=vid),
                        None, rate="-5%", pitch="+0Hz", out=out_mp3))
                    self._usage().add_tts(len(turn["text"]))
                except Exception as e:
                    raise RuntimeError(f"第 {i + 1} 轮合成失败:{e}") from e
            files.append(out_mp3)
            with self.lock:
                self.state["synthesis"]["done"] = i + 1
                self.save()

        self._build_final(files)
        self._set("done", "🎉 双人播客完成(免费双音色)")

    def _build_final(self, chunk_files: list[Path]) -> Path:
        """分段 → 拼接 → 响度归一化+ID3 → 封面生成并嵌入。封面失败不影响音频。"""
        merged = audio.concat_mp3(chunk_files, self.dir / "merged.mp3")
        title = self.state.get("title") or "未命名节目"
        final = audio.finalize(merged, self.dir / "audio.mp3", title=title)
        merged.unlink(missing_ok=True)
        # 垫乐混音槽(可选):用户自备音乐文件,R0 决议不下载素材
        bgm_cfg = self.cfg.data.get("audio") or {}
        bgm_path = (bgm_cfg.get("bgm_path") or "").strip()
        if bgm_path:
            try:
                bgm = Path(bgm_path).expanduser()
                if bgm.exists():
                    audio.mix_bgm(final, bgm, volume=float(bgm_cfg.get("bgm_volume") or 0.12))
                    self.state.setdefault("log", []).append(
                        {"t": _now(), "msg": "🎵 垫乐已混入(音量 "
                         + str(bgm_cfg.get("bgm_volume")) + ")"})
                else:
                    self.state.setdefault("log", []).append(
                        {"t": _now(), "msg": f"⚠️ 垫乐文件不存在,已跳过:{bgm_path}"})
            except Exception as e:
                self.state.setdefault("log", []).append(
                    {"t": _now(), "msg": f"⚠️ 垫乐混音失败(不影响人声成品):{e}"})
        try:
            from .cover import generate_cover, to_jpg

            cover_png = generate_cover(
                self.dir / "cover.png",
                title=title,
                subtitle="睡前故事 · 播客工坊" if self.state["style"] == "bedtime" else "播客工坊",
                style_id=self.state["style"],
            )
            if cover_png:
                audio.embed_cover(final, to_jpg(cover_png, self.dir / "cover.jpg"))
        except Exception as e:
            self.state.setdefault("log", []).append(
                {"t": _now(), "msg": f"⚠️ 封面生成失败(不影响音频):{e}"}
            )
        # 章节与字幕(商业基准 #5):分段实测时长 → ID3 章节 + SRT;失败不影响成品
        try:
            self._write_chapters_and_srt()
        except Exception as e:
            self.state.setdefault("log", []).append(
                {"t": _now(), "msg": f"⚠️ 章节/字幕生成失败(不影响音频):{e}"}
            )
        return final

    def _write_chapters_and_srt(self) -> None:
        from . import subtitles

        chunks = chunk_script(self.state["script"], self._profile())
        segs = subtitles.collect_segments(self.dir / "chunks", chunks)
        if not segs:
            return
        subtitles.write_srt(self.dir, chunks)
        total = audio.duration_seconds(self.dir / "audio.mp3")
        meta = subtitles.build_chapter_meta(segs, total=total)
        if meta and total and (self.dir / "audio.mp3").exists():
            audio.write_chapters(self.dir / "audio.mp3", meta)  # 无成品时只出字幕

    def resynth_chunk(self, index: int) -> None:
        """重录单段并重新拼接成品;失败回滚原分段。"""
        try:
            self._precheck_synthesis()
            if self.state["state"] != "done":
                raise RuntimeError("节目未完成,无法单段重录")
            chunks = chunk_script(self.state["script"], self._profile())
            if not (0 <= index < len(chunks)):
                raise ValueError(f"分段序号无效:{index}")
            ch = chunks[index]
            provider, voice = resolve_voice(self.cfg, self.cfg.llm, self.state["voice_id"])
            profile = self._profile()
            fallback = profile.get("edge_fallback", {"rate": "+0%", "pitch": "+0Hz"})
            pace_rate = self._apply_pace(provider)  # 重录与整期同一套语速逻辑(E6/Q3)

            chunk_dir = self.dir / "chunks"
            out_raw = chunk_dir / f"chunk_{index:03d}.raw"
            out_mp3 = chunk_dir / f"chunk_{index:03d}.mp3"
            backup = chunk_dir / f"chunk_{index:03d}.bak"
            out_mp3.replace(backup)
            try:
                self._set("synthesizing", f"🎙️ 正在重录第 {index + 1} 段…")
                params = fallback if provider.supports_emotion else edge_params(profile, ch.emotion_raw)
                # 语速与整期同一套逻辑(Q3:此前重录丢 pace_rate,慢/快语速重录段与全片不一致)
                eff_rate = pace_rate if provider.name in ("volcspeech", "edge") else params.get("rate", "+0%")
                asyncio.run(
                    provider.synth(
                        ch.text, voice,
                        ch.style if provider.supports_emotion else None,
                        rate=eff_rate,
                        pitch=params.get("pitch", "+0Hz"),
                        out=out_raw,
                    )
                )
                audio.to_mp3(out_raw, out_mp3)
            except Exception as e:
                backup.replace(out_mp3)  # 回滚
                raise RuntimeError(f"第 {index + 1} 段重录失败:{e}") from e
            finally:
                out_raw.unlink(missing_ok=True)

            with self.lock:
                self.state["synthesis"]["current"] = "重新拼接成品…"
                self.save()
            files = [chunk_dir / f"chunk_{i:03d}.mp3" for i in range(len(chunks))]
            self._build_final(files)
            backup.unlink(missing_ok=True)
            self.state["synthesis"]["done"] = len(chunks)
            self._set("done", f"✅ 第 {index + 1} 段已重录并更新成品")
        except Exception as e:
            with self.lock:
                self.state["failed_step"] = "resynth"
                self.state["failed_chunk"] = index
                self.state["error"] = str(e)
                self.state["state"] = "error"
                self.state.setdefault("log", []).append({"t": _now(), "msg": f"❌ 重录失败:{e}"})
                self.save()

    def archive(self, value: bool = True) -> None:
        """归档/取消归档。只动元数据，不删任何文件（删除是另一个动作）。"""
        with self.lock:
            if value:
                self.state["archived"] = True
                self.state.setdefault("log", []).append(
                    {"t": _now(), "msg": "🗃️ 已归档（列表默认隐藏，可随时取消归档）"})
            else:
                self.state.pop("archived", None)
                self.state.setdefault("log", []).append(
                    {"t": _now(), "msg": "📤 已取消归档"})
            self.save()

    # ---- 状态读取 -------------------------------------------------------------
    def public_state(self) -> dict:
        return self.state


def _json_load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _json_dump(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2)


_EP_ID_RE = re.compile(r"^[0-9a-z][0-9a-z-]{0,63}$")


def load_episode(cfg: Config, ep_id: str) -> Episode:
    # ep_id 白名单:目录名只可能是我们生成的 id,防路径注入(路由段已挡 "/",这里兜底 ".." 等)
    if not _EP_ID_RE.fullmatch(ep_id or ""):
        raise FileNotFoundError(f"节目不存在：{ep_id}")
    ep_dir = cfg.episodes_dir / ep_id
    if not ep_dir.is_dir() or not (ep_dir / "state.json").exists():
        raise FileNotFoundError(f"节目不存在：{ep_id}")
    return Episode(cfg, ep_dir)


def list_episodes(cfg: Config) -> list[dict]:
    out = []
    if not cfg.episodes_dir.exists():
        return out
    for ep_dir in sorted(cfg.episodes_dir.iterdir(), reverse=True):
        sf = ep_dir / "state.json"
        if not sf.exists():
            continue
        try:
            st = _json_load(sf)
            out.append(
                {
                    "id": st["id"],
                    "title": st.get("title") or st.get("input", "")[:30],
                    "style": st.get("style"),
                    "state": st.get("state"),
                    "created": st.get("created"),
                    "minutes": st.get("minutes"),
                    "origin": st.get("origin", "real"),
                    "archived": bool(st.get("archived")),
                    "has_audio": (ep_dir / "audio.mp3").exists(),
                    "has_cover": (ep_dir / "cover.jpg").exists() or (ep_dir / "cover.png").exists(),
                }
            )
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return out
