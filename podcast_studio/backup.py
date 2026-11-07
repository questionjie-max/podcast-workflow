"""数据备份 / 恢复。

用户最值钱的资产不是代码，而是 ``~/.podcast-studio`` 里的节目、克隆声音、
校准数据和 Key。本模块提供不依赖外部工具的 zip 备份：

- ``create_backup``：把 data 目录打包（排除可再生的 previews 缓存），可选带 config
- ``restore_backup``：先自动做一份“恢复前快照”，再解压回 data 目录
- 默认保留最近 7 份自动备份

设计红线：绝不删除用户数据；恢复前一定先备份当前状态。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from . import __version__
from .config import Config, home_dir

BACKUP_FORMAT = 1
DEFAULT_KEEP = 7
_SKIP_DIRS = {"previews", "logs"}
_SKIP_SUFFIXES = {".tmp", ".part"}


def backup_dir(cfg: Config | None = None) -> Path:
    return home_dir() / "backups"


def _iter_payload(cfg: Config):
    """产出 (源文件, zip 内路径)，跳过缓存/临时文件。"""
    data_dir = cfg.data_dir
    if not data_dir.is_dir():
        return
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_dir)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES or path.name.endswith(".tmp"):
            continue
        yield path, Path("data") / rel


def _prune(out_dir: Path, keep: int) -> None:
    archives = sorted(out_dir.glob("podcast-studio-backup-*.zip"))
    for old in archives[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def maybe_auto_backup(cfg: Config, interval_hours: float = 24.0) -> Path | None:
    """有节目且距上次自动备份超过 interval 时创建一份；否则跳过。

    测试/自动化可用 PODCAST_STUDIO_NO_AUTO_BACKUP=1 关闭。
    """
    import os

    if os.environ.get("PODCAST_STUDIO_NO_AUTO_BACKUP"):
        return None
    backup_cfg = cfg.data.get("backup") or {}
    if not backup_cfg.get("auto", True):
        return None
    if not cfg.episodes_dir.is_dir() or not any(cfg.episodes_dir.glob("*/state.json")):
        return None
    out_dir = backup_dir(cfg)
    newest = None
    if out_dir.is_dir():
        existing = sorted(out_dir.glob("podcast-studio-backup-*.zip"))
        newest = existing[-1] if existing else None
    if newest and time.time() - newest.stat().st_mtime < interval_hours * 3600:
        return None
    return create_backup(cfg, keep=int(backup_cfg.get("keep", DEFAULT_KEEP)))


def create_backup(
    cfg: Config,
    *,
    out_dir: Path | None = None,
    include_config: bool = False,
    keep: int = DEFAULT_KEEP,
    prefix: str = "podcast-studio-backup",
) -> Path:
    """创建 zip 备份并返回路径。

    include_config=True 时会把 config.json 一起打包（内含 API Key 明文），
    只建议备份到可信位置。
    """
    cfg.ensure_dirs()
    out_dir = Path(out_dir).expanduser() if out_dir else backup_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{prefix}-{stamp}.zip"

    episodes = sum(1 for p in cfg.episodes_dir.glob("*/state.json")) if cfg.episodes_dir.is_dir() else 0
    manifest = {
        "format": BACKUP_FORMAT,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version": __version__,
        "episodes": episodes,
        "include_config": include_config,
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for src, arc in _iter_payload(cfg):
            zf.write(src, arc.as_posix())
        if include_config:
            config_file = home_dir() / "config.json"
            if config_file.exists():
                zf.write(config_file, "config.json")

    if out_dir == backup_dir(cfg):
        _prune(out_dir, keep)
    return target


def _safe_members(zf: zipfile.ZipFile) -> list[str]:
    """拒绝绝对路径 / .. / 符号链接 / 非 data|config|manifest 的成员。"""
    import stat

    names = zf.namelist()
    for info in zf.infolist():
        name = info.filename
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"备份包含非法路径：{name}")
        if p.parts and p.parts[0] not in {"data", "config.json", "manifest.json"}:
            raise ValueError(f"备份包含未知条目：{name}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"备份包含符号链接：{name}")
    return names


def restore_backup(
    cfg: Config,
    archive: Path,
    *,
    restore_config: bool = False,
    keep: int = DEFAULT_KEEP,
) -> dict:
    """恢复备份。默认只恢复 data；restore_config=True 才覆盖 config.json。"""
    archive = Path(archive).expanduser()
    if not archive.is_file():
        raise FileNotFoundError(f"备份文件不存在：{archive}")

    with zipfile.ZipFile(archive) as zf:
        names = _safe_members(zf)
        try:
            manifest = json.loads(zf.read("manifest.json"))
        except (KeyError, ValueError) as e:
            raise ValueError("不是有效的播客工坊备份（缺少 manifest.json）") from e
        if manifest.get("format") != BACKUP_FORMAT:
            raise ValueError(f"不支持的备份格式：{manifest.get('format')}")

        # 恢复前先快照当前数据，误操作可回滚
        safety = None
        if cfg.data_dir.is_dir() and any(cfg.data_dir.iterdir()):
            safety = create_backup(cfg, prefix="pre-restore", keep=keep)

        with tempfile.TemporaryDirectory(prefix="ps-restore-") as td:
            tmp = Path(td)
            zf.extractall(tmp, members=names)
            src_data = tmp / "data"
            if src_data.is_dir():
                cfg.ensure_dirs()
                shutil.copytree(src_data, cfg.data_dir, dirs_exist_ok=True)
            if restore_config and (tmp / "config.json").is_file():
                home_dir().mkdir(parents=True, exist_ok=True)
                target = home_dir() / "config.json"
                shutil.copy2(tmp / "config.json", target)
                target.chmod(0o600)

    return {
        "ok": True,
        "archive": str(archive),
        "safety_backup": str(safety) if safety else None,
        "episodes": manifest.get("episodes", 0),
        "restored_config": bool(restore_config and "config.json" in names),
    }
