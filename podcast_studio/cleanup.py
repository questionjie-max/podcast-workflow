"""磁盘清理：只清可再生缓存，绝不碰节目/声音/配置。

产品里会持续增长的三类东西：
- `data/previews/`：音色试听缓存（可随时重新合成）
- `data/episodes/*/chunks/*.raw`：合成中间产物（失败时可能残留）
- `~/.podcast-studio/backups/`：自动快照（保留最近 N 份）

`clean` 默认 **dry-run**，只有显式 `--yes` 才真正删除。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .backup import backup_dir
from .config import Config

TARGETS = ("previews", "chunks", "backups")


@dataclass
class CleanupPlan:
    previews: list[Path] = field(default_factory=list)
    chunks: list[Path] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)

    def all_files(self) -> list[Path]:
        return [*self.previews, *self.chunks, *self.backups]

    def bytes_for(self, target: str) -> int:
        return sum(_size(p) for p in getattr(self, target))

    def total_bytes(self) -> int:
        return sum(_size(p) for p in self.all_files())

    def summary(self) -> dict:
        return {
            "previews": {"files": len(self.previews), "bytes": self.bytes_for("previews")},
            "chunks": {"files": len(self.chunks), "bytes": self.bytes_for("chunks")},
            "backups": {"files": len(self.backups), "bytes": self.bytes_for("backups")},
            "total_bytes": self.total_bytes(),
        }


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _dir_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += _size(p)
    return total


def plan_cleanup(cfg: Config, *, keep_backups: int = 7) -> CleanupPlan:
    """计算可清理清单（不删除任何东西）。"""
    plan = CleanupPlan()

    previews = cfg.data_dir / "previews"
    if previews.is_dir():
        plan.previews = [p for p in sorted(previews.rglob("*")) if p.is_file()]

    if cfg.episodes_dir.is_dir():
        for ep in cfg.episodes_dir.iterdir():
            chunk_dir = ep / "chunks"
            if not chunk_dir.is_dir():
                continue
            for p in chunk_dir.glob("*.raw"):
                plan.chunks.append(p)

    backups = backup_dir(cfg)
    if backups.is_dir():
        archives = sorted(
            [p for p in backups.glob("*.zip") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
        if keep_backups > 0:
            plan.backups = archives[:-keep_backups]
        else:
            plan.backups = archives

    return plan


def execute(plan: CleanupPlan, *, dry_run: bool = True) -> dict:
    removed = 0
    freed = 0
    for path in plan.all_files():
        size = _size(path)
        if not dry_run:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
        removed += 1
        freed += size
    return {
        "dry_run": dry_run,
        "files": removed,
        "bytes": freed,
        **plan.summary(),
    }


def disk_report(cfg: Config) -> dict:
    """给 doctor 用：数据目录各项占用。"""
    data = cfg.data_dir
    return {
        "data_dir": str(data),
        "total_bytes": _dir_bytes(data),
        "previews_bytes": _dir_bytes(data / "previews"),
        "logs_bytes": _dir_bytes(data / "logs"),
        "backups_bytes": _dir_bytes(backup_dir(cfg)),
        "episodes_bytes": _dir_bytes(cfg.episodes_dir),
        "episodes": len(list(cfg.episodes_dir.glob("*/state.json")))
        if cfg.episodes_dir.is_dir()
        else 0,
    }


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover - 循环内已 return


def format_plan(plan: CleanupPlan) -> str:
    s = plan.summary()
    lines = [
        (
            f"可清理：试听缓存 {s['previews']['files']} 个 / {human_bytes(s['previews']['bytes'])}，"
            f"合成残留 {s['chunks']['files']} 个 / {human_bytes(s['chunks']['bytes'])}，"
            f"旧备份 {s['backups']['files']} 个 / {human_bytes(s['backups']['bytes'])}"
        ),
        f"合计 {human_bytes(s['total_bytes'])}",
    ]
    return "\n".join(lines)


def last_run_marker(cfg: Config) -> Path:
    return cfg.data_dir / "logs" / ".last-clean"


def touch_marker(cfg: Config) -> None:
    marker = last_run_marker(cfg)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
