"""统一日志：轮转文件 + 密钥脱敏。

产品定位是本地自托管，但“打不开/合成失败”时用户需要能把证据交给开发者。
日志写 `<data_dir>/logs/app.log`（2MB × 3 份轮转），所有落盘文本先过脱敏，
避免把 API Key、Authorization 头写进日志或诊断包。
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

from .config import Config

_PREFIX_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|ark-[A-Za-z0-9-]{8,}|oc_sk_[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,})"
)
_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|x-api-key|authorization|token|secret|password)"
    r"(\s*[:=]\s*)([\"']?)([^\s\"',}]{4,})"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer[;\s]+)([A-Za-z0-9._~+/=-]{8,})")


def _mask(value: str) -> str:
    return value[:4] + "****" + value[-4:] if len(value) > 12 else "****"


def redact(text: str) -> str:
    """把常见密钥形态替换为 ****；普通文本保持原样。"""
    if not text:
        return text
    # 先处理 Bearer，避免 Authorization: Bearer xxx 被赋值规则只吃掉 "Bearer"
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", text)
    out = _PREFIX_RE.sub(lambda m: _mask(m.group(0)), out)
    out = _ASSIGN_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_mask(m.group(4))}", out
    )
    return out


class RedactionFilter(logging.Filter):
    """在日志记录落盘前改写消息，防止 provider 异常携带密钥片段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact(message)
        if clean != message:
            record.msg = clean
            record.args = ()
        return True


def log_path(cfg: Config | None = None) -> Path:
    c = cfg or Config.load()
    return c.data_dir / "logs" / "app.log"


def setup_logging(cfg: Config | None = None) -> Path:
    """初始化文件日志；重复调用会切换到当前数据目录的日志文件。"""
    c = cfg or Config.load()
    path = log_path(c)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ps_handler", False):
            if Path(getattr(handler, "baseFilename", "")) == path:
                return path
            root.removeHandler(handler)
            handler.close()

    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler._ps_handler = True  # type: ignore[attr-defined]
    handler.addFilter(RedactionFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"podcast_studio.{name}")
