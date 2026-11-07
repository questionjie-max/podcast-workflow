"""打包回归:运行时数据文件必须存在于源码且被 package-data 覆盖。

真正“装出来的 wheel 是否缺文件”由 `scripts/check_wheel.py` 在 CI 里构建验证；
这里做快速守门，避免有人删掉数据文件或把 package-data 改回只匹配一层。
"""

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _package_data() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["podcast_studio"]


def test_runtime_data_files_exist():
    assert (ROOT / "podcast_studio/styles/structures.json").is_file()
    demos = list((ROOT / "podcast_studio/server/static/demo").glob("*/state.json"))
    assert len(demos) >= 2, "随包示例电台至少要有两期"


def test_package_data_covers_styles_and_demo():
    patterns = _package_data()
    joined = "\n".join(patterns)
    assert "styles/*.json" in joined
    assert "server/static/demo/*/*" in joined


def test_structures_json_can_be_loaded():
    from podcast_studio.engine import structures

    templates = structures.templates_for("bedtime")
    assert templates, "bedtime 结构模板不应为空"
    assert all("beats" in t for t in templates)
