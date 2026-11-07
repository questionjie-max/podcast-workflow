"""原子写：中途失败不能破坏旧文件；用户资产类 JSON 都走它。"""

import json
import os

import pytest

from podcast_studio.atomic import atomic_write_json, atomic_write_text, read_json_or


def test_atomic_write_replaces_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "a.json"
    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_failure_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / "b.json"
    atomic_write_text(target, "old-content")

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new-content")
    monkeypatch.setattr(os, "replace", real_replace)

    assert target.read_text(encoding="utf-8") == "old-content"
    assert list(tmp_path.glob(".*.tmp")) == []  # 临时文件被清理


def test_atomic_write_mode(tmp_path):
    target = tmp_path / "c.json"
    atomic_write_json(target, {"k": "v"}, mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_read_json_or_handles_missing_and_corrupt(tmp_path):
    assert read_json_or(tmp_path / "none.json", {"d": 1}) == {"d": 1}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_json_or(bad, []) == []


def test_user_asset_stores_are_atomic(studio_home, monkeypatch):
    """voices.json / schedule_state.json / calibration.json 必须走原子写。"""
    from podcast_studio.config import Config
    from podcast_studio.engine import calibration
    from podcast_studio.providers import voices as vr
    from podcast_studio.server import scheduler

    cfg = Config.load()
    cfg.ensure_dirs()

    calls = []
    import podcast_studio.atomic as atomic_mod

    real = atomic_mod.atomic_write_json

    def spy(path, data, **kw):
        calls.append(os.path.basename(str(path)))
        return real(path, data, **kw)

    monkeypatch.setattr(atomic_mod, "atomic_write_json", spy)
    monkeypatch.setattr(vr, "_save_registry", vr._save_registry)  # 保持引用

    # 直接调用三处保存函数（内部 import 的是 atomic 模块属性，spy 生效）
    vr._save_registry(cfg, {"clones": []})
    scheduler.save_state(cfg, {"last_date": "", "last_index": -1})
    calibration.save(cfg, {"bedtime": {"cps": 1.0}})

    assert "voices.json" in calls
    assert "schedule_state.json" in calls
    assert "calibration.json" in calls
