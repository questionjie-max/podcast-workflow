"""原子写工具。

用途：任何“用户资产”类的小文件（配置、声音注册表、调度状态、时长校准）
都不能直接 `write_text` —— 写到一半崩溃/断电会留下半截 JSON，读取方要么
静默清空，要么抛异常。

本模块统一走「同目录唯一临时文件 → os.replace」；`os.replace` 在 POSIX 与
Windows 上都是原子替换，读方永远看到完整旧文件或完整新文件。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> Path:
    """原子写入文本文件。mode 非空时对最终文件设置权限（如 0600）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if mode is not None:
            tmp.chmod(mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def atomic_write_json(path: Path, data, *, indent: int = 2, mode: int | None = None) -> Path:
    return atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=indent),
        mode=mode,
    )


def read_json_or(path: Path, default):
    """读 JSON；文件缺失/损坏/无权限时返回 default（调用方决定是否告警）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
