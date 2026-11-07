"""用量账本：LLM 调用/字符、TTS 字符、可选费用估算。"""

import pytest

from podcast_studio.config import Config
from podcast_studio.engine.pipeline import Episode
from podcast_studio.engine.usage import CountingLLM, UsageMeter, estimate_cost


class _Inner:
    name = "inner"

    def __init__(self):
        self.calls = []

    def chat(self, task, system, user, **kwargs):
        self.calls.append((task, system, user))
        return "x" * 10


def test_counting_llm_records_chars_and_tasks():
    meter = UsageMeter()
    llm = CountingLLM(_Inner(), meter)
    llm.chat("script", "系统提示", "用户输入内容")
    assert meter.llm_calls == 1
    assert meter.llm_in_chars == len("系统提示") + len("用户输入内容")
    assert meter.llm_out_chars == 10
    assert meter.steps == {"script": 1}


def test_counting_llm_transparently_forwards_attributes():
    inner = _Inner()
    inner.base_url = "https://x/v1"
    llm = CountingLLM(inner, UsageMeter())
    assert llm.base_url == "https://x/v1"


def test_usage_roundtrip_dict():
    meter = UsageMeter(llm_calls=2, llm_in_chars=100, llm_out_chars=200, tts_chars=300,
                       steps={"script": 2})
    restored = UsageMeter.from_dict(meter.to_dict())
    assert restored.to_dict() == meter.to_dict()
    assert UsageMeter.from_dict(None).to_dict()["llm_calls"] == 0


def test_estimate_cost_none_when_unpriced():
    assert estimate_cost({"tts_chars": 1000}, {}) is None
    assert estimate_cost({"tts_chars": 1000}, None) is None


def test_estimate_cost_computes():
    cost = estimate_cost(
        {"llm_in_chars": 1_000_000, "llm_out_chars": 500_000, "tts_chars": 2000},
        {"llm_per_million_in": 2, "llm_per_million_out": 8, "tts_per_kchar": 0.5,
         "currency": "CNY"},
    )
    assert cost["llm"] == pytest.approx(2 + 4)
    assert cost["tts"] == pytest.approx(1.0)
    assert cost["total"] == pytest.approx(7.0)


def test_pipeline_records_usage_end_to_end(studio_home, fake_tts):
    cfg = Config.load()
    ep = Episode.create(cfg, mode="material", input_text="彩虹是折射形成的。",
                        style="bedtime", voice_id="edge:test", minutes=2)
    ep.run_material_title()
    assert ep.state["state"] == "awaiting_review"

    ep.approve()
    ep.run_synthesis()
    assert ep.state["state"] == "done", ep.state.get("error")

    usage = ep.state["usage"]
    assert usage["llm_calls"] >= 1          # 起标题 + 写稿 + 合规 + shownotes
    assert usage["llm_out_chars"] > 0
    assert usage["tts_chars"] > 0
    # 落盘后重新加载仍在
    from podcast_studio.engine.pipeline import load_episode

    reloaded = load_episode(cfg, ep.id)
    assert reloaded.state["usage"]["tts_chars"] == usage["tts_chars"]


def test_episode_api_exposes_usage_and_optional_cost(studio_home, fake_tts):
    from fastapi.testclient import TestClient

    from podcast_studio.server.app import app

    cfg = Config.load()
    cfg.data["pricing"] = {"llm_per_million_in": 1, "llm_per_million_out": 2,
                           "tts_per_kchar": 0.1, "currency": "CNY"}
    cfg.save()

    client = TestClient(app)
    r = client.post("/api/episodes", json={"mode": "material", "input": "素材",
                                           "style": "bedtime", "voice_id": "edge:test",
                                           "minutes": 2})
    ep_id = r.json()["id"]

    import time as _t

    for _ in range(120):
        st = client.get(f"/api/episodes/{ep_id}").json()
        if st["state"] in ("awaiting_review",):
            break
        _t.sleep(0.1)
    assert st["state"] == "awaiting_review", st
    client.post(f"/api/episodes/{ep_id}/approve")
    for _ in range(300):
        st = client.get(f"/api/episodes/{ep_id}").json()
        if st["state"] in ("done", "error"):
            break
        _t.sleep(0.1)
    assert st["state"] == "done", st
    assert st["usage"]["llm_calls"] >= 1
    assert st["usage_cost"] is not None and st["usage_cost"]["total"] >= 0


# ---- R9:定价设置端点 ----

def test_pricing_api_saves_and_validates(studio_home):
    from fastapi.testclient import TestClient

    from podcast_studio.server.app import app

    client = TestClient(app)
    r = client.post("/api/config/pricing", json={
        "currency": "CNY", "llm_per_million_in": 2,
        "llm_per_million_out": 8, "tts_per_kchar": 0.5,
    })
    assert r.status_code == 200
    assert r.json()["pricing"]["llm_per_million_out"] == 8

    got = client.get("/api/config").json()["pricing"]
    assert got["currency"] == "CNY"
    assert got["tts_per_kchar"] == 0.5


def test_pricing_api_rejects_bad_values(studio_home):
    from fastapi.testclient import TestClient

    from podcast_studio.server.app import app

    client = TestClient(app)
    assert client.post("/api/config/pricing", json={"tts_per_kchar": "abc"}).status_code == 400
    assert client.post("/api/config/pricing", json={"tts_per_kchar": -1}).status_code == 400
    assert client.post("/api/config/pricing", json={"tts_per_kchar": 1e9}).status_code == 400


def test_pricing_blank_resets_to_zero(studio_home):
    from fastapi.testclient import TestClient

    from podcast_studio.server.app import app

    client = TestClient(app)
    client.post("/api/config/pricing", json={"tts_per_kchar": 0.5})
    r = client.post("/api/config/pricing", json={"tts_per_kchar": ""})
    assert r.status_code == 200
    assert r.json()["pricing"]["tts_per_kchar"] == 0
