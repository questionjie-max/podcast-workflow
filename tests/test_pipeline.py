"""pipeline:状态机全链路(假 LLM + 假 TTS)、断点续跑、错误路径。"""

import time
from pathlib import Path

import pytest

from podcast_studio.config import Config
from podcast_studio.engine.pipeline import Episode, list_episodes, load_episode


def make_ep(cfg: Config, mode="topic", **kw) -> Episode:
    return Episode.create(
        cfg, mode=mode, input_text=kw.pop("input_text", "为什么会下雨"),
        style=kw.pop("style", "bedtime"), voice_id=kw.pop("voice_id", "edge:test"),
        minutes=kw.pop("minutes", 2), **kw,
    )


def wait_state(ep: Episode, states: set[str], timeout: float = 30) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ep.state["state"] in states:
            return ep.state["state"]
        time.sleep(0.1)
    raise TimeoutError(f"状态未到达 {states},当前 {ep.state['state']}: {ep.state.get('error')}")


def test_topic_mode_full_pipeline(studio_home, fake_tts):
    cfg = Config.load()
    ep = make_ep(cfg)
    assert ep.state["state"] == "created"

    ep.run_research()
    assert ep.state["state"] == "awaiting_topic"
    assert len(ep.state["candidates"]) >= 3

    ep.select_topic(0)
    wait_state(ep, {"awaiting_review"})
    assert ep.state["title"] == "小雨滴的旅行：为什么会下雨？"
    assert "[情绪:" in ep.state["script"]
    assert ep.state["compliance"]["pass"] is True
    assert (ep.dir / "script.md").exists()

    ep.approve()
    ep.run_synthesis()
    wait_state(ep, {"done"})
    audio = ep.dir / "audio.mp3"
    assert audio.exists() and audio.stat().st_size > 5000
    assert (ep.state.get("duration_sec") or 0) > 0.2, "完成后应记录音频时长"
    assert (ep.dir / "script_clean.txt").exists()
    assert "情绪" not in (ep.dir / "script_clean.txt").read_text(encoding="utf-8")
    # 分段数与合成调用数一致
    assert len(list((ep.dir / "chunks").glob("*.mp3"))) == len(fake_tts["tts"].calls)


def test_material_mode_skips_topic(studio_home, fake_tts):
    cfg = Config.load()
    ep = make_ep(cfg, mode="material", input_text="彩虹是折射形成的。\n七色光。")
    ep.run_material_title()
    wait_state(ep, {"awaiting_review"})
    assert ep.state["title"] == "小雨滴的旅行"  # mock 的 title 任务
    assert ep.state["candidates"] == []


def test_update_script_resets_compliance(studio_home, fake_tts):
    cfg = Config.load()
    ep = make_ep(cfg)
    ep.run_research()
    ep.select_topic(0)
    wait_state(ep, {"awaiting_review"})

    ep.update_script("[情绪:温柔开场]\n我自己改的稿子。")
    assert ep.state["compliance"] is None
    assert ep.state["state"] == "awaiting_review"
    assert (ep.dir / "script.md").read_text(encoding="utf-8").startswith("[情绪:温柔开场]")


def test_synthesis_failure_and_resume(studio_home, monkeypatch):
    """第 2 段失败 → error;修好后从失败段续跑,已完成的段不重做。"""
    cfg = Config.load()

    tts = FakeTTSHolder(monkeypatch, fail_at=2)  # 0-based:第 3 段失败
    ep = make_ep(cfg)
    ep.run_research()
    ep.select_topic(0)
    wait_state(ep, {"awaiting_review"})
    ep.approve()
    ep.run_synthesis()
    wait_state(ep, {"error"})
    assert "第 3 段合成失败" in ep.state["error"]
    assert ep.state["failed_step"] == "synthesis"
    done_before = ep.state["synthesis"]["done"]
    assert done_before >= 2  # 并行合成下,失败瞬间可能已有更多段完成(进度只多不少)

    # 修复后重试:只补缺失的段(并行下缺失集合以磁盘为准)
    total = ep.state["synthesis"]["total"]
    on_disk = {int(f.stem.split("_")[1]) for f in (ep.dir / "chunks").glob("chunk_*.mp3")}
    truly_missing = set(range(total)) - on_disk
    from collections import Counter

    before = Counter(tts.calls)
    tts.unfail()
    ep.approve()
    ep.run_synthesis()
    wait_state(ep, {"done"})
    assert (ep.dir / "audio.mp3").exists()
    after = Counter(tts.calls)
    retried = sorted(i for i, cnt in after.items() if cnt > before.get(i, 0))
    assert retried == sorted(truly_missing)  # 恰好补齐缺失段(含失败段本身),已完成的段绝不重做


class FakeTTSHolder:
    """可控失败的假 TTS(独立于 conftest 的简单夹具,便于中途切换行为)。"""

    def __init__(self, monkeypatch, fail_at: int | None):
        from podcast_studio.engine import pipeline as pipeline_mod
        from podcast_studio.providers import voices as voice_reg
        from podcast_studio.providers.tts.base import VoiceSpec

        self.calls: list[int] = []
        self.fail_at = fail_at
        holder = self

        class T:
            name = "fake"
            supports_emotion = True
            supports_clone = False

            async def synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
                from conftest import make_tone_wav

                idx = int(str(out).split("_")[-1].split(".")[0])
                holder.calls.append(idx)
                if holder.fail_at is not None and idx == holder.fail_at:
                    raise RuntimeError("boom")
                make_tone_wav(Path(str(out) + ".t.wav"), 0.3)
                Path(str(out) + ".t.wav").replace(out)

        # pipeline 与 voices 各持有一份 resolve_voice 引用,两处都要替换
        monkeypatch.setattr(voice_reg, "resolve_voice",
                            lambda cfg, llm_cfg, sel: (T(), VoiceSpec(provider="fake", voice_id="f", label="t")))
        monkeypatch.setattr(pipeline_mod, "resolve_voice",
                            lambda cfg, llm_cfg, sel: (T(), VoiceSpec(provider="fake", voice_id="f", label="t")))

    def unfail(self):
        self.fail_at = None


def test_list_and_delete_episodes(studio_home, fake_tts):
    cfg = Config.load()
    ep = make_ep(cfg)
    eps = list_episodes(cfg)
    assert len(eps) == 1 and eps[0]["id"] == ep.id
    assert eps[0]["has_audio"] is False

    import shutil

    shutil.rmtree(ep.dir)
    assert list_episodes(cfg) == []


def test_load_episode_missing(studio_home):
    cfg = Config.load()
    with pytest.raises(FileNotFoundError):
        load_episode(cfg, "99990101-0000-zzzz")


# ---- R2:异步错误双通道人话化(error 用户读,error_raw 留排查) ----

def test_friendly_error_translation():
    from podcast_studio.engine.pipeline import Episode

    f = Episode._friendly_error
    assert "brew install ffmpeg" in f(FileNotFoundError("[Errno 2] No such file or directory: 'ffmpeg'"))
    assert "超时" in f(TimeoutError("https://x timed out after 300s"))
    assert "磁盘" in f(OSError("[Errno 28] No space left on device"))
    assert "网络" in f(ConnectionError("Connection refused by peer"))
    # 已经是友好中文的原文不动(不被二次包装破坏)
    assert f(RuntimeError("未配置 MiMo API Key（设置 → 接口配置）")) == "未配置 MiMo API Key（设置 → 接口配置）"


# ---- R2/R3:origin 元数据(示例/真实隔离,D 圆桌四规则) ----

def test_origin_defaults_real_and_demo_flows_through_list(studio_home):
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode, list_episodes

    cfg = Config.load()
    real = Episode.create(cfg, mode="material", input_text="真实素材", style="bedtime",
                          voice_id="edge:x", minutes=2)
    demo = Episode.create(cfg, mode="material", input_text="示例素材", style="bedtime",
                          voice_id="edge:x", minutes=2, origin="demo")
    assert real.state["origin"] == "real" and demo.state["origin"] == "demo"
    rows = {e["id"]: e for e in list_episodes(cfg)}
    assert rows[real.id]["origin"] == "real" and rows[demo.id]["origin"] == "demo"


def test_demo_episode_excluded_from_rss(studio_home):
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode
    from podcast_studio.server.feed import build_feed

    cfg = Config.load()
    demo = Episode.create(cfg, mode="material", input_text="示例素材", style="bedtime",
                          voice_id="edge:x", minutes=2, origin="demo")
    real = Episode.create(cfg, mode="material", input_text="真实素材", style="bedtime",
                          voice_id="edge:x", minutes=2)
    for ep in (demo, real):  # 两者都做成 done+有音频,只差 origin
        ep.state["state"] = "done"
        ep.save()
        (ep.dir / "audio.mp3").write_bytes(b"fake-mp3")
    rss = build_feed(cfg, "http://127.0.0.1:8686")
    assert real.id in rss
    assert demo.id not in rss  # 示例绝不进公开订阅面


# ---- R2 接线审计 E3:pace_hint 用户配置优先,pipeline 不覆盖 ----

def test_volcspeech_pace_hint_user_config_wins(studio_home):
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode
    from podcast_studio.providers.voices import resolve_voice

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_speech"] = {"resource_id": "seed-tts-2.0", "pace_hint": "用户自定义语速"}
    cfg.data["tts"]["volc_podcast"] = {"api_key": "k", "resource_id": "volc.service_type.10050"}
    provider, _ = resolve_voice(cfg, {}, "volcspeech:xiaohe_uranus")
    assert provider.pace_hint == "用户自定义语速"  # resolve_voice 透传(E3:此前是死配置)

    ep = Episode.create(cfg, mode="material", input_text="素材", style="bedtime",
                        voice_id="volcspeech:xiaohe_uranus", minutes=2)
    ep._apply_pace(provider)
    assert provider.pace_hint == "用户自定义语速"  # pipeline 不覆盖用户配置


def test_volcspeech_pace_hint_default_when_unset(studio_home):
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode
    from podcast_studio.providers.voices import resolve_voice

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_speech"] = {"resource_id": "seed-tts-2.0", "pace_hint": ""}
    cfg.data["tts"]["volc_podcast"] = {"api_key": "k", "resource_id": "volc.service_type.10050"}
    provider, _ = resolve_voice(cfg, {}, "volcspeech:xiaohe_uranus")
    ep = Episode.create(cfg, mode="material", input_text="素材", style="bedtime",
                        voice_id="volcspeech:xiaohe_uranus", minutes=2)
    rate = ep._apply_pace(provider)
    assert rate == "+0%" and "平稳" in provider.pace_hint  # 未配置时给节目类型默认


def test_parallel_chunk_synthesis_overlaps(studio_home, fake_tts):
    """多段并行合成:FakeTTS 记录并发重叠,断言至少 2 路同时跑过。"""
    import time as _t

    from podcast_studio.config import Config
    from podcast_studio.engine import pipeline as pipeline_mod
    from podcast_studio.engine.pipeline import Episode

    holder = {"tts": None}

    from pathlib import Path

    class SlowFakeTTS:
        name = "fake"
        supports_emotion = True
        supports_clone = False

        def __init__(self):
            self.calls = []
            self.active = 0
            self.max_active = 0

        async def synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
            from conftest import make_tone_wav

            self.active += 1
            self.max_active = max(self.max_active, self.active)
            _t.sleep(0.15)
            self.active -= 1
            make_tone_wav(Path(str(out) + ".t.wav"), 0.3)
            Path(str(out) + ".t.wav").replace(out)

    slow = SlowFakeTTS()
    holder["tts"] = slow

    from podcast_studio.providers.tts.base import VoiceSpec

    def fake_resolve(cfg, llm_cfg, voice_selector):
        return slow, VoiceSpec(provider="fake", voice_id="fake", label="测试声音")

    monkeypatch_targets = (pipeline_mod, "resolve_voice")
    from podcast_studio.providers import voices as voice_reg
    original = (voice_reg.resolve_voice, pipeline_mod.resolve_voice)
    voice_reg.resolve_voice = fake_resolve
    pipeline_mod.resolve_voice = fake_resolve
    try:
        cfg = Config.load()
        ep = Episode.create(cfg, mode="material", input_text="第一段。第二段。第三段。第四段。",
                            style="bedtime", voice_id="fake:x", minutes=2)
        ep.state["script"] = "[情绪:温柔]" + "一段内容。" * 200  # 足够切成多段
        ep.state["state"] = "awaiting_review"  # 走合法状态闸(Q4:approve 不再接受任意状态)
        ep.save()
        ep.approve()
        ep.run_synthesis()
    finally:
        voice_reg.resolve_voice, pipeline_mod.resolve_voice = original
    assert ep.state["state"] == "done", ep.state.get("error")
    assert slow.max_active >= 2, f"未观察到并行(最大并发 {slow.max_active})"


def test_archive_keeps_files_and_logs(studio_home, fake_tts):
    cfg = Config.load()
    ep = make_ep(cfg, mode="material", input_text="素材")
    ep.run_material_title()
    wait_state(ep, {"awaiting_review"})

    ep.archive(True)
    reloaded = load_episode(cfg, ep.id)
    assert reloaded.state["archived"] is True
    assert reloaded.state["state"] == "awaiting_review"  # 状态不变，只加标记
    assert (reloaded.dir / "script.md").exists()

    reloaded.archive(False)
    assert "archived" not in load_episode(cfg, ep.id).state
