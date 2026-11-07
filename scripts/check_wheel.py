#!/usr/bin/env python3
"""构建 wheel 并断言运行时数据文件真的被打进去了。

回归背景：`[tool.setuptools.package-data]` 曾只写 `server/static/*`，
导致 pip/Docker 安装后的 wheel 缺少 `styles/structures.json` 与示例电台
`server/static/demo/**`——源码模式测试全绿，分发版本却 500/示例消失。

用法：.venv/bin/python scripts/check_wheel.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "podcast_studio/styles/structures.json",
    "podcast_studio/server/static/index.html",
    "podcast_studio/server/static/app.js",
    "podcast_studio/server/static/style.css",
    "podcast_studio/server/static/i18n.js",
)
DEMO_PREFIX = "podcast_studio/server/static/demo/"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ps-wheel-") as td:
        out = Path(td) / "dist"
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            print("❌ 构建 wheel 失败：")
            print(proc.stdout[-2000:])
            print(proc.stderr[-2000:])
            return 1

        wheels = list(out.glob("*.whl"))
        if len(wheels) != 1:
            print(f"❌ 期望 1 个 wheel，实际 {len(wheels)} 个")
            return 1

        with zipfile.ZipFile(wheels[0]) as zf:
            names = set(zf.namelist())
            missing = [p for p in REQUIRED if p not in names]
            if missing:
                print("❌ wheel 缺少运行时数据文件：")
                for p in missing:
                    print("   -", p)
                return 1
            demos = [n for n in names if n.startswith(DEMO_PREFIX) and n.endswith("/state.json")]
            if not demos:
                print(f"❌ wheel 缺少示例电台：{DEMO_PREFIX}*/state.json")
                return 1

        print(f"✅ wheel 数据文件齐全（{len(demos)} 期示例，{len(names)} 个文件）")

    # 清理构建中间产物（仅本脚本产生的 build/ 目录）
    build_dir = ROOT / "build"
    if build_dir.is_dir():
        import shutil

        shutil.rmtree(build_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
