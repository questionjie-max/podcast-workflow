"""数据备份/恢复:不丢用户节目、不带缓存、拒绝路径穿越。"""

import json
import zipfile
from pathlib import Path

import pytest

from podcast_studio import backup as backup_mod
from podcast_studio.config import Config


def _seed(studio_home: Path) -> Config:
    cfg = Config.load()
    cfg.ensure_dirs()
    ep = cfg.episodes_dir / "ep-1"
    ep.mkdir(parents=True)
    (ep / "state.json").write_text(json.dumps({"state": "done", "title": "一期"}), encoding="utf-8")
    (ep / "script.md").write_text("文稿", encoding="utf-8")
    (cfg.data_dir / "voices.json").write_text('{"clones": []}', encoding="utf-8")
    cache = cfg.data_dir / "previews"
    cache.mkdir()
    (cache / "x.mp3").write_bytes(b"cache")
    cfg.data.setdefault("llm", {}).setdefault("openai", {})["api_key"] = "sk-secret-12345678"
    cfg.save()
    return cfg


def test_backup_contains_data_but_not_cache_or_config(studio_home):
    cfg = _seed(studio_home)
    path = backup_mod.create_backup(cfg)
    assert path.is_file()
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert "data/episodes/ep-1/state.json" in names
    assert "data/episodes/ep-1/script.md" in names
    assert "data/voices.json" in names
    assert not any(n.startswith("data/previews/") for n in names)
    assert "config.json" not in names
    assert manifest["episodes"] == 1
    assert manifest["include_config"] is False


def test_backup_can_include_config_with_warning(studio_home):
    cfg = _seed(studio_home)
    path = backup_mod.create_backup(cfg, include_config=True)
    with zipfile.ZipFile(path) as zf:
        assert "config.json" in zf.namelist()
        assert json.loads(zf.read("manifest.json"))["include_config"] is True


def test_restore_data_and_optional_config(studio_home):
    cfg = _seed(studio_home)
    path = backup_mod.create_backup(cfg, include_config=True)

    # 模拟数据丢失 + Key 被改
    import shutil

    shutil.rmtree(cfg.data_dir)
    cfg2 = Config.load()
    cfg2.data["llm"]["openai"]["api_key"] = "changed-key"
    cfg2.save()

    result = backup_mod.restore_backup(cfg2, path, restore_config=False)
    assert result["episodes"] == 1
    assert (cfg2.episodes_dir / "ep-1/state.json").is_file()
    assert Config.load().data["llm"]["openai"]["api_key"] == "changed-key"

    result = backup_mod.restore_backup(cfg2, path, restore_config=True)
    assert result["restored_config"] is True
    assert Config.load().data["llm"]["openai"]["api_key"] == "sk-secret-12345678"


def test_restore_rejects_path_traversal(studio_home, tmp_path):
    cfg = Config.load()
    cfg.ensure_dirs()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "episodes": 0}))
        zf.writestr("../evil.txt", "pwn")
    with pytest.raises(ValueError, match="非法路径"):
        backup_mod.restore_backup(cfg, evil)


def test_backup_cli_roundtrip(studio_home, capsys):
    from podcast_studio.cli import main

    _seed(studio_home)
    main(["backup"])
    out = capsys.readouterr().out
    assert "备份完成" in out
    archives = list((studio_home / "backups").glob("podcast-studio-backup-*.zip"))
    assert archives

    main(["restore", str(archives[0]), "--yes"])
    out = capsys.readouterr().out
    assert "恢复完成" in out
    assert (Config.load().episodes_dir / "ep-1/state.json").is_file()


def test_auto_backup_skips_without_episodes(studio_home, monkeypatch):
    monkeypatch.delenv("PODCAST_STUDIO_NO_AUTO_BACKUP", raising=False)
    cfg = Config.load()
    cfg.ensure_dirs()
    assert backup_mod.maybe_auto_backup(cfg) is None


def test_auto_backup_creates_once_then_skips(studio_home, monkeypatch):
    monkeypatch.delenv("PODCAST_STUDIO_NO_AUTO_BACKUP", raising=False)
    cfg = _seed(studio_home)
    first = backup_mod.maybe_auto_backup(cfg)
    assert first is not None and first.is_file()
    assert backup_mod.maybe_auto_backup(cfg) is None  # 24h 内不重复


def test_auto_backup_can_be_disabled(studio_home, monkeypatch):
    cfg = _seed(studio_home)
    cfg.data["backup"]["auto"] = False
    monkeypatch.delenv("PODCAST_STUDIO_NO_AUTO_BACKUP", raising=False)
    assert backup_mod.maybe_auto_backup(cfg) is None


def test_restore_rejects_symlink_entries(studio_home, tmp_path):
    import stat

    cfg = Config.load()
    cfg.ensure_dirs()
    evil = tmp_path / "link.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "episodes": 0}))
        info = zipfile.ZipInfo("data/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match="符号链接"):
        backup_mod.restore_backup(cfg, evil)
