"""定时生成:should_run 纯函数 + run_scheduled_episode 全链路。"""

from datetime import datetime, timedelta

from podcast_studio.config import Config
from podcast_studio.server.scheduler import (
    SCHEDULE_DEFAULT,
    load_state,
    run_scheduled_episode,
    should_run,
)

NOW = datetime(2026, 10, 30, 20, 31)  # 20:31,过了 20:30 的点


def test_should_run_basic():
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "20:30",
             "mode": "topic", "topics": ["为什么会下雨"]}
    state = {"last_date": "2026-10-29"}
    assert should_run(NOW, sched, state)
    # 今天已跑过
    assert not should_run(NOW, sched, {**state, "last_date": "2026-10-30"})
    # 未到时间
    assert not should_run(NOW - timedelta(hours=1), sched, state)
    # 未启用
    assert not should_run(NOW, {**sched, "enabled": False}, state)
    # topic 模式但话题池为空
    assert not should_run(NOW, {**sched, "topics": []}, state)
    # material 模式有素材即可
    assert should_run(NOW, {**sched, "mode": "material", "material": "彩虹"}, state)
    assert not should_run(NOW, {**sched, "mode": "material", "material": ""}, state)


def test_scheduled_episode_topic_mode(studio_home, fake_tts):
    """topic 模式:同步跑完选题并自动选第一个候选。"""
    cfg = Config.load()
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "20:30",
             "style": "bedtime", "minutes": 2, "voice_id": "edge:x",
             "mode": "topic", "topics": ["为什么会下雨", "月亮为什么变弯"]}
    state = load_state(cfg)

    ep_id = run_scheduled_episode(cfg, sched, state)
    from podcast_studio.engine.pipeline import load_episode

    ep = load_episode(cfg, ep_id)
    assert ep.state["title"] == "小雨滴的旅行：为什么会下雨？"  # 第一个候选
    assert any("定时任务创建" in l["msg"] for l in ep.state["log"])
    assert state["last_date"] == datetime.now().strftime("%Y-%m-%d")  # 跨午夜安全

    # 第二次触发:话题轮换到下一个
    ep_id2 = run_scheduled_episode(cfg, sched, state)
    ep2 = load_episode(cfg, ep_id2)
    assert ep2.state["input"] == "月亮为什么变弯"


def test_scheduled_episode_material_mode(studio_home, fake_tts):
    cfg = Config.load()
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "mode": "material",
             "material": "彩虹是折射。", "style": "bedtime", "minutes": 2}
    state = load_state(cfg)
    ep_id = run_scheduled_episode(cfg, sched, state)
    from podcast_studio.engine.pipeline import load_episode

    ep = load_episode(cfg, ep_id)
    assert ep.state["mode"] == "material"


def test_should_run_unpadded_time():
    """'9:30' 必须等价于 '09:30'(未补零曾导致 08:31 就误触发)。"""
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "9:30",
             "mode": "topic", "topics": ["测试"]}
    state = {"last_date": "2026-10-29"}
    assert not should_run(datetime(2026, 10, 30, 8, 31), sched, state)
    assert should_run(datetime(2026, 10, 30, 9, 31), sched, state)


def test_should_run_invalid_time_never_fires():
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "mode": "topic", "topics": ["测试"]}
    state = {"last_date": "2026-10-29"}
    for bad in ("abc", "", None, "25:99", "9点30"):
        assert not should_run(NOW, {**sched, "time": bad}, state), bad


def test_norm_hhmm():
    from podcast_studio.server.scheduler import norm_hhmm

    assert norm_hhmm("9:30") == "09:30"
    assert norm_hhmm(" 20:5 ") == "20:05"
    assert norm_hhmm("bad") is None
    assert norm_hhmm("") is None


# ---- R2 回归:scheduler 素材模式从未接线(素材期永远停在 created) ----

def test_scheduled_material_reaches_review(studio_home, fake_tts):
    """素材定时模式必须推进到 awaiting_review(人工闸),不能停在 created。"""
    cfg = Config.load()
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "mode": "material",
             "material": "彩虹是折射。", "style": "bedtime", "minutes": 2}
    ep_id = run_scheduled_episode(cfg, sched, load_state(cfg))
    from podcast_studio.engine.pipeline import load_episode

    ep = load_episode(cfg, ep_id)
    assert ep.state["state"] == "awaiting_review"
    assert ep.state.get("title")  # 标题已起


def test_should_run_blank_topics_never_fires():
    """全空白话题:truthy 但应视为空(曾触发 %0 ZeroDivisionError)。"""
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "20:30",
             "mode": "topic", "topics": ["  ", "   "]}
    assert not should_run(NOW, sched, {"last_date": "2026-10-29"})


def test_scheduled_blank_topics_raises_clean(studio_home, fake_tts):
    import pytest

    sched = {**SCHEDULE_DEFAULT, "mode": "topic", "topics": ["  "],
             "style": "bedtime", "minutes": 2}
    with pytest.raises(ValueError):
        run_scheduled_episode(Config.load(), sched, load_state(Config.load()))


# ---- R8:调度边界补强 ----

def test_should_run_cross_midnight_uses_dates_not_timestamps():
    """23:59 触发后,00:01 不该重复触发(按日期而非时间窗口)。"""
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "23:59",
             "mode": "topic", "topics": ["夜间话题"]}
    state = {"last_date": "2026-10-29"}
    assert should_run(datetime(2026, 10, 30, 23, 59), sched, state)
    state["last_date"] = "2026-10-30"
    assert not should_run(datetime(2026, 10, 31, 0, 1), sched, state)


def test_should_run_exactly_at_target_time():
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "time": "20:30",
             "mode": "topic", "topics": ["t"]}
    assert should_run(datetime(2026, 10, 30, 20, 30), sched, {"last_date": ""})
    assert not should_run(datetime(2026, 10, 30, 20, 29), sched, {"last_date": ""})


def test_load_state_tolerates_corrupt_file(studio_home):
    from podcast_studio.server.scheduler import schedule_state_path

    cfg = Config.load()
    cfg.ensure_dirs()
    p = schedule_state_path(cfg)
    p.write_text("{broken", encoding="utf-8")
    state = load_state(cfg)
    assert state["last_date"] == "" and state["last_index"] == -1


def test_scheduled_episode_registers_busy_and_cleans_up(studio_home, fake_tts):
    """调度线程执行期间必须占用 _busy，结束后释放（防与 UI 并发）。"""
    import podcast_studio.server.app as app_mod

    cfg = Config.load()
    sched = {**SCHEDULE_DEFAULT, "enabled": True, "mode": "material",
             "material": "彩虹", "style": "bedtime", "minutes": 2}
    seen = {}
    real_run = None

    import podcast_studio.engine.pipeline as pipe

    real_run = pipe.Episode.run_material_title

    def spy(self):
        seen["busy"] = self.id in app_mod._busy
        return real_run(self)

    pipe.Episode.run_material_title = spy
    try:
        ep_id = run_scheduled_episode(cfg, sched, load_state(cfg))
    finally:
        pipe.Episode.run_material_title = real_run

    assert seen.get("busy") is True
    assert ep_id not in app_mod._busy
