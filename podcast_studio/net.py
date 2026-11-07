"""网络环境兼容层。

httpx 只接受 http/https/socks5/socks5h 代理 scheme；系统里常见的
``ALL_PROXY=socks://...`` 会让所有 httpx 客户端在构造时直接抛
``ValueError: Unknown scheme for proxy URL``。这不是项目 bug，但会表现为
“测试全红 / 真实 API 全部连不上”，必须给出可行动的提示而不是裸异常。

策略：启动时静默移除不受支持的 ``ALL_PROXY``（不影响 HTTP_PROXY/HTTPS_PROXY），
并把原因记录到 ``proxy_warnings`` 供 doctor 展示。绝不打印完整代理 URL
（可能含账号密码）。
"""

from __future__ import annotations

import os
import sys

_SUPPORTED_PREFIXES = ("http://", "https://", "socks5://", "socks5h://")
_NAMES = ("ALL_PROXY", "all_proxy")

# 最近一次 sanitize 的告警（供 doctor 等展示；不重复打印）
proxy_warnings: list[str] = []
_printed: set[str] = set()


def _scheme(value: str) -> str:
    return value.split("://", 1)[0] if "://" in value else "unknown"


def sanitize_proxy_env() -> list[str]:
    """移除 httpx 无法解析的代理变量，返回本次移除项的人话说明。"""
    removed: list[str] = []
    for name in _NAMES:
        value = os.environ.get(name)
        if not value:
            continue
        if value.lower().startswith(_SUPPORTED_PREFIXES):
            continue
        os.environ.pop(name, None)
        msg = (
            f"{name} 使用了 httpx 不支持的代理 scheme（{_scheme(value)}://），"
            "已忽略该变量"
        )
        removed.append(msg)
        if msg not in proxy_warnings:
            proxy_warnings.append(msg)
        if msg not in _printed:
            _printed.add(msg)
            print(
                f"⚠️ {msg}；如确需 SOCKS 代理，请改用 socks5:// 并安装 httpx[socks]，"
                "或设置 HTTP_PROXY/HTTPS_PROXY",
                file=sys.stderr,
            )
    return removed
