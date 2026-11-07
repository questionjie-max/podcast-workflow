"""诊断包：把 doctor 报告 + 脱敏配置 + 日志打包，便于用户交给开发者。

红线：包内绝不出现 config.json 明文、节目音频/文稿、API Key。
"""

from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from . import __version__
from .config import Config, home_dir
from .logging_setup import redact


def build_bundle(cfg: Config, report: str, *, out_dir: Path | None = None) -> Path:
    """生成诊断 zip 并返回路径。"""
    target_dir = Path(out_dir).expanduser() if out_dir else home_dir() / "diagnostics"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"podcast-studio-diagnostics-{stamp}.zip"

    episodes = 0
    try:
        episodes = sum(1 for _ in cfg.episodes_dir.glob("*/state.json"))
    except OSError:
        pass

    environment = {
        "app_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "episodes": episodes,
        "data_dir": str(cfg.data_dir),
        "allow_lan": bool((cfg.data.get("server") or {}).get("allow_lan")),
    }

    log_dir = cfg.data_dir / "logs"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.txt", redact(report))
        zf.writestr(
            "config.masked.json",
            json.dumps(cfg.masked(), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "environment.json",
            json.dumps(environment, ensure_ascii=False, indent=2),
        )
        if log_dir.is_dir():
            for log_file in sorted(log_dir.glob("app.log*")):
                if log_file.is_file():
                    text = log_file.read_text(encoding="utf-8", errors="replace")
                    zf.writestr(f"logs/{log_file.name}", redact(text))
    return target
