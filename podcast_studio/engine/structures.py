"""结构模板库:按节目类型提供写稿节拍计划(来源见 docs/structure-templates.md)。"""

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).parent.parent / "styles" / "structures.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def templates_for(category: str) -> list[dict]:
    return [t for t in _data()["templates"] if t["category"] == category]


def find(structure_id: str) -> dict | None:
    return next((t for t in _data()["templates"] if t["id"] == structure_id), None)


def beat_plan(structure_id: str, target_chars: int) -> list[dict] | None:
    """把模板节拍换算成带字数预算的写稿计划。"""
    t = find(structure_id)
    if not t:
        return None
    plan = []
    for b in t["beats"]:
        plan.append({"name": b["name"], "emotion": b["emotion"],
                     "desc": b["desc"], "chars": int(target_chars * b["share"])})
    return plan
