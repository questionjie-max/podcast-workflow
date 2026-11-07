"""定时生成:每晚指定时间自动做一期节目(如睡前故事)。

纯线程实现(每 30 秒检查一次),不引入调度依赖。
状态存 data/schedule_state.json,保证同一天只触发一次、话题轮换。
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

SCHEDULE_DEFAULT = {
    "enabled": False,
    "time": "20:30",
    "style": "bedtime",
    "voice_id": "edge:zh-CN-XiaoxiaoNeural",
    "minutes": 5,
    "mode": "topic",  # topic:按话题列表轮换 | material:固定素材
    "topics": [],     # mode=topic 时的话题池,如 ["为什么会下雨", "月亮为什么变形状"]
    "material": "",   # mode=material 时的固定素材
}


def schedule_state_path(cfg) -> Path:
    return cfg.data_dir / "schedule_state.json"


def load_state(cfg) -> dict:
    p = schedule_state_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_date": "", "last_index": -1}


def save_state(cfg, state: dict) -> None:
    from ..atomic import atomic_write_json

    cfg.ensure_dirs()
    atomic_write_json(schedule_state_path(cfg), state, indent=None)


def norm_hhmm(v) -> str | None:
    """时间归一化:'9:30' → '09:30';非法输入返回 None(永不触发)。"""
    try:
        h, m = str(v).strip().split(":")
        return f"{int(h):02d}:{int(m):02d}"
    except (ValueError, AttributeError):
        return None


def should_run(now: datetime, sched: dict, state: dict) -> bool:
    """纯函数:是否该触发(时间到 + 今天没跑过 + 有可用内容)。"""
    if not sched.get("enabled"):
        return False
    today = now.strftime("%Y-%m-%d")
    if state.get("last_date") == today:
        return False
    target = norm_hhmm(sched.get("time"))
    if target is None or now.strftime("%H:%M") < target:  # 每分钟检查,到点即触发
        return False
    if sched.get("mode") == "material":
        return bool((sched.get("material") or "").strip())
    return bool([t for t in (sched.get("topics") or []) if t and t.strip()])


def run_scheduled_episode(cfg, sched: dict, state: dict) -> str:
    """同步做一期(阻塞在调度线程里);返回 ep_id。

    执行段与 UI 任务共用 _busy 登记(C15):同一期不允许调度线程与浏览器并发操作。
    """
    from ..engine.pipeline import Episode
    from ..styles import PROFILES

    mode = sched.get("mode", "topic")
    style = sched.get("style", "bedtime")
    if style not in PROFILES:
        style = "bedtime"
    minutes = max(1, min(15, int(sched.get("minutes") or PROFILES[style]["default_minutes"])))

    if mode == "material":
        ep = Episode.create(
            cfg, mode="material", input_text=sched["material"],
            style=style, voice_id=sched.get("voice_id", ""), minutes=minutes,
            title=f"定时节目 {datetime.now().strftime('%m-%d')}",
        )
    else:
        topics = [t for t in (sched.get("topics") or []) if t and t.strip()]
        if not topics:
            raise ValueError("定时话题列表为空(全是空白),本期跳过")
        idx = (state.get("last_index", -1) + 1) % len(topics)  # 轮换话题
        ep = Episode.create(
            cfg, mode="topic", input_text=topics[idx],
            style=style, voice_id=sched.get("voice_id", ""), minutes=minutes,
        )

    from .app import _busy  # 函数级导入避免循环依赖(app → scheduler)

    if ep.id in _busy:
        raise RuntimeError("该节目正在执行任务,跳过本期")
    _busy.add(ep.id)
    try:
        if mode == "material":
            ep.run_material_title()  # 素材期也要跑到 awaiting_review(此前从未接线)
        else:
            ep.run_research()  # 同步跑完选题
            if ep.state["state"] == "awaiting_topic" and ep.state.get("candidates"):
                ep.select_topic(0)  # 无人值守:自动选第一个候选
                # 空候选:留在 awaiting_topic 等用户人工处理,不硬抛

        state["last_date"] = datetime.now().strftime("%Y-%m-%d")
        state["last_index"] = state.get("last_index", -1) + 1
        save_state(cfg, state)
        ep.state.setdefault("log", []).append(
            {"t": datetime.now().isoformat(timespec="seconds"), "msg": "⏰ 定时任务创建"}
        )
        ep.save()
    finally:
        _busy.discard(ep.id)
    return ep.id


def start_scheduler(app) -> threading.Thread:
    """启动守护线程;返回线程对象。"""

    def loop():
        from ..config import Config

        while True:
            try:
                cfg = Config.load()
                sched = {**SCHEDULE_DEFAULT, **(cfg.data.get("schedule") or {})}
                state = load_state(cfg)
                now = datetime.now()
                if should_run(now, sched, state):
                    try:
                        ep_id = run_scheduled_episode(cfg, sched, state)
                        app.state.last_scheduled = ep_id
                    except Exception as e:
                        cfg.ensure_dirs()
                        (cfg.data_dir / "scheduler.log").open("a", encoding="utf-8").write(
                            f"{now.isoformat()} 失败:{e}\n"
                        )
                        state["last_date"] = now.strftime("%Y-%m-%d")
                        save_state(cfg, state)
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=loop, name="ps-scheduler", daemon=True)
    t.start()
    return t
