"""时长校准:从真实节目学习「字符/秒」速率,动态修正写稿字数目标。

痛点:LLM 按"约 N 字"写稿,但实际语速因节目类型/文本风格浮动,
选 5 分钟可能只出 3 分钟。解法:每期完成后记录 实际时长 ÷ 纯文稿字数
= 字符/秒,按节目类型做 EMA 平滑,下一期用校准值反推字数目标。
"""

import json
from pathlib import Path

from ..styles import get_profile

CALIBRATION_FILE = "calibration.json"
_EMA_ALPHA = 0.4  # 新样本权重(样本少时快速收敛)
_MIN_SAMPLES = 1


def _path(cfg) -> Path:
    return cfg.data_dir / CALIBRATION_FILE


def load(cfg) -> dict:
    p = _path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def save(cfg, data: dict) -> None:
    from ..atomic import atomic_write_json

    cfg.ensure_dirs()
    atomic_write_json(_path(cfg), data)


def _default_cps(cfg, style: str) -> float:
    """无历史时的默认字符/秒:来自 profile 的 words_per_minute。"""
    profile = get_profile(style)
    return profile["words_per_minute"] / 60.0


def record(cfg, style: str, chars: int, duration_sec: float) -> dict:
    """记录一期真实数据(EMA 平滑),返回更新后的该类型校准。"""
    if duration_sec <= 5 or chars <= 20:
        return load(cfg).get(style, {})
    data = load(cfg)
    entry = data.get(style, {"cps": _default_cps(cfg, style), "samples": 0})
    actual = chars / duration_sec
    entry["cps"] = round(entry["cps"] * (1 - _EMA_ALPHA) + actual * _EMA_ALPHA, 3)
    entry["samples"] = entry.get("samples", 0) + 1
    entry["last"] = {"chars": chars, "duration_sec": round(duration_sec, 1),
                     "cps": round(actual, 3)}
    data[style] = entry
    save(cfg, data)
    return entry


def has_samples(cfg, style: str) -> bool:
    """该节目类型是否已有真实校准样本(决定要不要展示「首期时长可能偏差大」)。"""
    entry = load(cfg).get(style) or {}
    return entry.get("samples", 0) > 0


def target_chars(cfg, style: str, minutes: int) -> int:
    """按校准速率反推字数目标(向上取整到 50,给 LLM 留自然空间)。"""
    data = load(cfg)
    entry = data.get(style)
    cps = entry["cps"] if entry and entry.get("cps") else _default_cps(cfg, style)
    return int(-(-minutes * 60 * cps // 50)) * 50  # ceil 到 50
