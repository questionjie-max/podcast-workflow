"""磁盘清理：只清可再生缓存，永不碰节目/声音/配置。"""

import time

from podcast_studio import backup as backup_mod
from podcast_studio import cleanup
from podcast_studio.config import Config


def _seed(cfg):
    cfg.ensure_dirs()
    ep = cfg.episodes_dir / "ep-1"
    (ep / "chunks").mkdir(parents=True)
    (ep / "state.json").write_text('{"state":"done"}', encoding="utf-8")
    (ep / "audio.mp3").write_bytes(b"audio")
    (ep / "chunks" / "chunk_000.mp3").write_bytes(b"chunk")
    (ep / "chunks" / "chunk_001.raw").write_bytes(b"raw")
    previews = cfg.data_dir / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / "a.mp3").write_bytes(b"p" * 100)
    (cfg.data_dir / "voices.json").write_text("{}", encoding="utf-8")


def test_plan_lists_only_regenerable_files(studio_home):
    cfg = Config.load()
    _seed(cfg)
    plan = cleanup.plan_cleanup(cfg, keep_backups=7)

    names = {p.name for p in plan.all_files()}
    assert "a.mp3" in names          # 试听缓存
    assert "chunk_001.raw" in names  # 合成残留
    # 绝不能出现在清理清单里
    assert "state.json" not in names
    assert "audio.mp3" not in names
    assert "voices.json" not in names
    assert "chunk_000.mp3" not in names  # 成品分段保留（断点续跑依赖）


def test_dry_run_does_not_delete(studio_home):
    cfg = Config.load()
    _seed(cfg)
    plan = cleanup.plan_cleanup(cfg)
    result = cleanup.execute(plan, dry_run=True)

    assert result["dry_run"] is True
    assert result["files"] >= 2
    assert (cfg.data_dir / "previews" / "a.mp3").exists()
    assert (cfg.episodes_dir / "ep-1" / "chunks" / "chunk_001.raw").exists()


def test_execute_deletes_but_keeps_episodes(studio_home):
    cfg = Config.load()
    _seed(cfg)
    plan = cleanup.plan_cleanup(cfg)
    result = cleanup.execute(plan, dry_run=False)

    assert result["files"] >= 2
    assert not (cfg.data_dir / "previews" / "a.mp3").exists()
    assert not (cfg.episodes_dir / "ep-1" / "chunks" / "chunk_001.raw").exists()
    # 节目本体完好
    assert (cfg.episodes_dir / "ep-1" / "state.json").exists()
    assert (cfg.episodes_dir / "ep-1" / "audio.mp3").exists()
    assert (cfg.data_dir / "voices.json").exists()


def test_old_backups_pruned_but_recent_kept(studio_home):
    cfg = Config.load()
    _seed(cfg)
    now = time.time()
    for i in range(10):
        path = backup_mod.backup_dir(cfg) / f"podcast-studio-backup-2026010{i}-000000.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"z")
        # 让时间顺序确定：越早的 mtime 越小
        time.sleep(0.01)
        import os

        os.utime(path, (now - 1000 + i, now - 1000 + i))

    plan = cleanup.plan_cleanup(cfg, keep_backups=3)
    assert len(plan.backups) == 7
    removed = {p.name for p in plan.backups}
    assert "podcast-studio-backup-20260100-000000.zip" in removed
    assert "podcast-studio-backup-20260109-000000.zip" not in removed


def test_disk_report_and_human_bytes(studio_home):
    cfg = Config.load()
    _seed(cfg)
    report = cleanup.disk_report(cfg)
    assert report["episodes"] == 1
    assert report["episodes_bytes"] > 0
    assert report["previews_bytes"] == 100
    assert cleanup.human_bytes(0) == "0 B"
    assert cleanup.human_bytes(1536).endswith("KB")


def test_clean_cli_dry_run_and_yes(studio_home, capsys):
    from podcast_studio.cli import main

    cfg = Config.load()
    _seed(cfg)

    main(["clean"])
    out = capsys.readouterr().out
    assert "预览" in out
    assert (cfg.data_dir / "previews" / "a.mp3").exists()

    main(["clean", "--yes"])
    out = capsys.readouterr().out
    assert "已删除" in out
    assert not (cfg.data_dir / "previews" / "a.mp3").exists()
