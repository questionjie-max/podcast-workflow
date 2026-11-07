"""steps:JSON 鲁棒解析、选题/写稿/合规对 mock LLM 的调用。"""

import pytest

from podcast_studio.engine import steps
from podcast_studio.providers.llm.base import make_llm
from podcast_studio.providers.llm.mock import MockLLM
from podcast_studio.styles import PROFILES

PODCAST = PROFILES["podcast"]


def test_mock_llm_pipeline_outputs():
    llm = make_llm({"provider": "mock"})
    cands = steps.research(llm, PODCAST, "睡眠")
    assert 1 <= len(cands) <= 5
    assert all({"title", "angle", "heat", "reason"} <= set(c) for c in cands)

    script = steps.write_script(llm, PODCAST, minutes=3, content_block="选题:测试")
    assert "[情绪:" in script and len(script) > 100

    report = steps.compliance_check(llm, PODCAST, script)
    assert report["pass"] is True


def test_extract_json_fenced_and_prefixed():
    assert steps._extract_json('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert steps._extract_json('好的,结果如下:[{"a":1}] 请查收') == [{"a": 1}]
    assert steps._extract_json('{"pass": true}') == {"pass": True}
    assert steps._extract_json("完全不是 JSON") is None


class JunkLLM(MockLLM):
    """research 返回垃圾的研究桩。"""

    def chat(self, task, system, user, **kw):
        if task == "research":
            return "我实在给不出 JSON 呢"
        return super().chat(task, system, user, **kw)


def test_research_unparseable_raises():
    with pytest.raises(RuntimeError, match="无法解析"):
        steps.research(JunkLLM(), PODCAST, "测试")


class DictLLM(MockLLM):
    """返回 {"candidates": [...]} 包装的桩。"""

    def chat(self, task, system, user, **kw):
        import json

        if task == "research":
            return json.dumps(
                {"candidates": [{"title": "包装过的", "angle": "", "heat": "高", "reason": ""}]},
                ensure_ascii=False,
            )
        return super().chat(task, system, user, **kw)


def test_research_accepts_dict_wrapper():
    cands = steps.research(DictLLM(), PODCAST, "测试")
    assert cands[0]["title"] == "包装过的"
    assert cands[0]["index"] == 0


def test_compliance_reviews_clean_text():
    """合规审查应看到剥离情绪标签后的纯文稿(O11 假阳性修复)。"""
    captured = {}

    class CaptureLLM(MockLLM):
        def chat(self, task, system, user, **kw):
            if task == "compliance":
                captured["user"] = user
            return super().chat(task, system, user, **kw)

    script = "[情绪:温柔开场]\n第一段内容。\n[情绪:安心收尾]\n最后一段。"
    report = steps.compliance_check(CaptureLLM(), PROFILES["bedtime"], script)
    assert report["pass"] is True
    assert "[情绪" not in captured["user"]
    assert "第一段内容" in captured["user"]
