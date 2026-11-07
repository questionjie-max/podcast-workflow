"""doctor --probe:真实探测的最小化、可 mock、不泄露密钥。"""

from pathlib import Path

from podcast_studio import probes
from podcast_studio.config import Config


class FakeLLM:
    def chat(self, *a, **k):
        assert k["timeout"] == 20
        return "OK"


class FakeTTS:
    name = "fake"

    async def synth(self, text, voice, style, *, out, **kwargs):
        Path(out).write_bytes(b"fake-audio")


class FailTTS:
    name = "fail"

    async def synth(self, *a, **k):
        raise RuntimeError("boom")


def test_probe_llm_ok_and_fail(studio_home, monkeypatch):
    cfg = Config.load()
    cfg.data["llm"]["provider"] = "openai"
    cfg.data["llm"]["openai"]["api_key"] = "test-key"
    monkeypatch.setattr(probes, "make_llm", lambda _cfg: FakeLLM())
    result = probes.probe_llm(cfg)
    assert result.status == "ok" and "OK" in result.detail

    class BadLLM:
        def chat(self, *a, **k):
            raise RuntimeError("auth failed")

    monkeypatch.setattr(probes, "make_llm", lambda _cfg: BadLLM())
    result = probes.probe_llm(cfg)
    assert result.status == "fail" and "auth failed" in result.detail


def test_probe_tts_one_success_and_failure(tmp_path):
    ok = probes._probe_tts_one("fake", FakeTTS(), "v1")
    assert ok.status == "ok"
    bad = probes._probe_tts_one("fail", FailTTS(), "v1")
    assert bad.status == "fail" and "boom" in bad.detail


def test_run_probes_marks_unconfigured_asr(studio_home, monkeypatch):
    cfg = Config.load()
    monkeypatch.setattr(probes, "probe_llm", lambda c: probes.ProbeResult("LLM", "skip", "mock"))
    monkeypatch.setattr(probes, "probe_tts", lambda c: [])
    monkeypatch.setattr(probes, "probe_search", lambda c: [])
    results = probes.run_probes(cfg)
    assert results[0].name == "LLM"
    assert any(r.name == "豆包 ASR" and r.status == "skip" for r in results)


def test_doctor_probe_prints_results_without_network(studio_home, monkeypatch, capsys):
    from podcast_studio.cli import main

    monkeypatch.setattr(
        probes, "run_probes",
        lambda cfg: [probes.ProbeResult("写作引擎", "ok", "返回 OK", 12)],
    )
    main(["doctor", "--probe"])
    out = capsys.readouterr().out
    assert "真实连通性探测" in out
    assert "写作引擎: 返回 OK" in out
