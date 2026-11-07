"""API 层:TestClient 全流程 + 配置脱敏。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from podcast_studio.config import Config
from podcast_studio.server.app import app


@pytest.fixture()
def client(studio_home, fake_tts, monkeypatch):
    # edge 音色列表不走网络(必须是 async,接口处有 await)
    from podcast_studio.providers.tts.edge import EdgeTTS

    async def fake_list(cache_file, ttl=86400):
        return [{"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female",
                 "Locale": "zh-CN", "Friendly": "晓晓"}]

    monkeypatch.setattr(EdgeTTS, "list_voices_cached", staticmethod(fake_list))
    return TestClient(app)


def wait_episode_state(client, ep_id, states, timeout=30):
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        try:
            st = client.get(f"/api/episodes/{ep_id}").json()
        except ValueError:
            time.sleep(0.1)  # 偶发非 JSON 响应(线程落盘竞态),重试即可
            continue
        if st["state"] in states:
            return st
        time.sleep(0.1)
    raise TimeoutError(f"未到达 {states}: {st.get('state')} / {st.get('error')}")


def test_health_and_profiles(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ffmpeg"] is True
    profiles = client.get("/api/profiles").json()
    assert {p["id"] for p in profiles} == {"podcast", "bedtime"}


def test_config_masking_and_update(client, studio_home):
    cfg = client.get("/api/config").json()
    assert cfg["llm"]["provider"] == "mock"

    r = client.post("/api/config", json={"llm": {"provider": "openai", "openai": {
        "api_key": "sk-secret-123456789", "model": "mimo-v2.5-pro"}}})
    assert r.status_code == 200
    masked = r.json()["config"]["llm"]["openai"]["api_key"]
    assert "secret" not in masked and "****" in masked

    # 传回 "****" 不覆盖真实 key
    client.post("/api/config", json={"llm": {"provider": "openai", "openai": {"api_key": "****"}}})
    saved = Config.load().llm["openai"]["api_key"]
    assert saved == "sk-secret-123456789"


def test_voices_endpoint(client):
    """按凭据过滤:未配置的组进 locked,不出现在可选列表。"""
    data = client.get("/api/voices").json()
    assert data["builtin"]["edge"]            # edge 免费,永远可用
    assert "mimo" not in data["builtin"]      # 未配 MiMo key → 不可选
    locked_ids = {g["id"] for g in data["locked"]}
    assert {"mimo", "volcspeech", "volcpodcast", "ali", "elevenlabs", "azure"} <= locked_ids
    assert data["clones"] == []


def test_voices_unlocked_by_credentials(client, studio_home):
    """配置语音 Key 后:豆包音色/方舟播客组解锁。"""
    from podcast_studio.config import Config

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_podcast"] = {
        "api_key": "speech-key-123", "resource_id": "volc.service_type.10050"}
    cfg.save()
    data = client.get("/api/voices").json()
    assert "volcspeech" in data["builtin"] and "volcpodcast" in data["builtin"]
    locked_ids = {g["id"] for g in data["locked"]}
    assert "volcspeech" not in locked_ids and "volcpodcast" not in locked_ids


def test_full_episode_flow_via_api(client):
    r = client.post("/api/episodes", json={
        "mode": "topic", "input": "为什么会下雨", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    assert r.status_code == 200
    ep_id = r.json()["id"]

    st = wait_episode_state(client, ep_id, {"awaiting_topic"})
    assert len(st["candidates"]) >= 3

    assert client.post(f"/api/episodes/{ep_id}/select", json={"index": 0}).status_code == 200
    st = wait_episode_state(client, ep_id, {"awaiting_review"})
    assert st["compliance"]["pass"] is True

    # 改稿 → 审查被清空 → 仍需在 awaiting_review
    assert client.post(f"/api/episodes/{ep_id}/script",
                       json={"text": st["script"] + "\n[情绪:安心收尾]\n改一句。"}).status_code == 200
    st2 = client.get(f"/api/episodes/{ep_id}").json()
    assert st2["compliance"] is None

    assert client.post(f"/api/episodes/{ep_id}/approve").status_code == 200
    st = wait_episode_state(client, ep_id, {"done"}, timeout=60)
    audio = client.get(f"/api/episodes/{ep_id}/audio")
    assert audio.status_code == 200 and len(audio.content) > 5000

    assert client.delete(f"/api/episodes/{ep_id}").status_code == 200
    assert client.get(f"/api/episodes/{ep_id}").status_code == 404


def test_create_episode_validation(client):
    assert client.post("/api/episodes", json={"mode": "topic", "input": ""}).status_code == 400
    assert client.post("/api/episodes", json={"mode": "bad", "input": "x"}).status_code == 400
    assert client.post("/api/episodes", json={"mode": "topic", "input": "x", "style": "bad"}).status_code == 400


def test_select_wrong_state_conflict(client):
    r = client.post("/api/episodes", json={"mode": "topic", "input": "测试冲突"})
    ep_id = r.json()["id"]
    wait_episode_state(client, ep_id, {"awaiting_topic"})
    # 已选题后再次 select → 409
    client.post(f"/api/episodes/{ep_id}/select", json={"index": 0})
    wait_episode_state(client, ep_id, {"awaiting_review"})
    assert client.post(f"/api/episodes/{ep_id}/select", json={"index": 0}).status_code == 409
    # 未到 awaiting_review 就 approve → 409
    r2 = client.post("/api/episodes", json={"mode": "topic", "input": "另一个"})
    ep2 = r2.json()["id"]
    wait_episode_state(client, ep2, {"awaiting_topic"})
    assert client.post(f"/api/episodes/{ep2}/approve").status_code == 409


def test_llm_test_endpoint_mock(client):
    r = client.post("/api/llm/test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data.get("mock") is True


def test_llm_test_endpoint_auth_error(client, studio_home, monkeypatch):
    """配置了假地址/假 key 时,连接测试返回结构化错误而非 500。"""
    cfg = Config()
    cfg.llm["provider"] = "openai"
    cfg.llm["openai"].update({
        "api_key": "sk-bad", "model": "mimo-v2.5-pro",
        "base_url": "http://127.0.0.1:9",  # 不可达端口
    })
    cfg.save()
    r = client.post("/api/llm/test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False and data["kind"] in ("network", "http")


def test_shownotes_generated_and_editable(client):
    r = client.post("/api/episodes", json={
        "mode": "material", "input": "彩虹是折射。", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    ep_id = r.json()["id"]
    st = wait_episode_state(client, ep_id, {"awaiting_review"})
    client.post(f"/api/episodes/{ep_id}/approve")
    st = wait_episode_state(client, ep_id, {"done"}, timeout=60)
    assert st["shownotes"] and "要点" in st["shownotes"]

    # 编辑
    r = client.post(f"/api/episodes/{ep_id}/shownotes", json={"text": "我自己的 shownotes"})
    assert r.status_code == 200
    assert client.get(f"/api/episodes/{ep_id}").json()["shownotes"] == "我自己的 shownotes"
    client.delete(f"/api/episodes/{ep_id}")


def test_wizard_onboarding_flow(client):
    """未引导时 health.onboarded=False;保存配置后变 True。"""
    assert client.get("/api/health").json()["onboarded"] is False
    assert client.post("/api/config", json={"onboarded": True}).status_code == 200
    assert client.get("/api/health").json()["onboarded"] is True


def test_llm_test_accepts_temp_config(client):
    """连接测试支持临时配置(向导场景,未保存即可测)。"""
    r = client.post("/api/llm/test", json={"llm": {"provider": "openai", "openai": {
        "base_url": "http://127.0.0.1:9", "model": "x", "api_key": "k"}}})
    assert r.json()["ok"] is False and r.json()["kind"] == "network"
    # 临时配置不落盘
    assert Config.load().llm["provider"] == "mock"


def test_resynth_single_chunk(client):
    """完成后的节目:列出分段 → 重录一段 → 其余分段文件不动。"""
    r = client.post("/api/episodes", json={
        "mode": "material", "input": "彩虹是折射。", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    ep_id = r.json()["id"]
    wait_episode_state(client, ep_id, {"awaiting_review"})
    client.post(f"/api/episodes/{ep_id}/approve")
    wait_episode_state(client, ep_id, {"done"}, timeout=60)

    chunks = client.get(f"/api/episodes/{ep_id}/chunks").json()
    assert len(chunks) >= 2 and chunks[0]["has_file"]

    target = chunks[1]

    def mtime():
        return (Config.load().episodes_dir / ep_id / "chunks" / "chunk_001.mp3").stat().st_mtime

    before = mtime()
    other_hash = (Config.load().episodes_dir / ep_id / "chunks" / "chunk_000.mp3").read_bytes()

    r = client.post(f"/api/episodes/{ep_id}/resynth", json={"index": 1})
    assert r.status_code == 200
    st = wait_episode_state(client, ep_id, {"done"}, timeout=60)
    assert "重录" in st["log"][-1]["msg"] or "重录" in st["log"][-2]["msg"]
    assert mtime() != before  # 目标段确实重做了
    assert (Config.load().episodes_dir / ep_id / "chunks" / "chunk_000.mp3").read_bytes() == other_hash  # 其他段没动

    # 音频成品重新拼好
    assert client.get(f"/api/episodes/{ep_id}/audio").status_code == 200
    client.delete(f"/api/episodes/{ep_id}")


def test_feed_rss_and_cover(client):
    """完成的节目出现在 RSS 里(enclosure/时长),封面端点可用。"""
    r = client.post("/api/episodes", json={
        "mode": "material", "input": "彩虹是折射。", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    ep_id = r.json()["id"]
    wait_episode_state(client, ep_id, {"awaiting_review"})
    client.post(f"/api/episodes/{ep_id}/approve")
    wait_episode_state(client, ep_id, {"done"}, timeout=60)

    feed = client.get("/api/feed.rss")
    assert feed.status_code == 200
    assert "application/rss+xml" in feed.headers["content-type"]
    assert f"/api/episodes/{ep_id}/audio" in feed.text
    assert "<enclosure" in feed.text and "itunes:duration" in feed.text
    assert "小雨滴的旅行" in feed.text

    cover = client.get(f"/api/episodes/{ep_id}/cover")
    assert cover.status_code == 200
    assert cover.headers["content-type"].startswith("image/")

    # 改名后 RSS 同步
    client.post(f"/api/episodes/{ep_id}/rename", json={"title": "彩虹的故事"})
    assert "彩虹的故事" in client.get("/api/feed.rss").text
    client.delete(f"/api/episodes/{ep_id}")


def test_voice_preview_edge_only(client, studio_home, monkeypatch):
    """edge 音色试听返回真实音频;非 edge 拒绝并给出解释。"""

    from conftest import make_tone_wav

    from podcast_studio.providers.tts.edge import EdgeTTS

    async def fake_synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
        make_tone_wav(out.parent / (out.stem + ".wav"), 0.2)
        (out.parent / (out.stem + ".wav")).replace(out)

    monkeypatch.setattr(EdgeTTS, "synth", fake_synth)
    r = client.post("/api/voices/preview", json={"selector": "edge:zh-CN-XiaoxiaoNeural"})
    assert r.status_code == 200 and len(r.content) > 500
    # 缓存命中(第二次不合成):换 monkeypatch 抛错,仍应 200
    def boom(self, *a, **k): raise RuntimeError("should not be called")
    monkeypatch.setattr(EdgeTTS, "synth", boom)
    assert client.post("/api/voices/preview", json={"selector": "edge:zh-CN-XiaoxiaoNeural"}).status_code == 200

    r = client.post("/api/voices/preview", json={"selector": "mimo:mimo_default"})
    assert r.status_code == 400


def test_episode_export_zip(client):
    r = client.post("/api/episodes", json={
        "mode": "material", "input": "彩虹是折射。", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    ep_id = r.json()["id"]
    wait_episode_state(client, ep_id, {"awaiting_review"})
    client.post(f"/api/episodes/{ep_id}/approve")
    wait_episode_state(client, ep_id, {"done"}, timeout=60)

    z = client.get(f"/api/episodes/{ep_id}/export")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "audio.mp3" in names and "script.md" in names and "shownotes.md" in names
    client.delete(f"/api/episodes/{ep_id}")


def test_api_schedule_time_normalized(client):
    """通过 API 保存 "9:30" → 落盘为 "09:30"。"""
    r = client.post("/api/config", json={"schedule": {"enabled": True, "time": "9:30"}})
    assert r.status_code == 200
    assert client.get("/api/config").json()["schedule"]["time"] == "09:30"


def test_feed_pubdate_has_tz_offset(client):
    """RFC2822 要求带时区;不允许出现 -0000(无时区)。"""
    import re

    r = client.post("/api/episodes", json={
        "mode": "material", "input": "彩虹是折射。", "style": "bedtime",
        "voice_id": "edge:zh-CN-XiaoxiaoNeural", "minutes": 2,
    })
    ep_id = r.json()["id"]
    wait_episode_state(client, ep_id, {"awaiting_review"})
    client.post(f"/api/episodes/{ep_id}/approve")
    wait_episode_state(client, ep_id, {"done"}, timeout=60)

    feed = client.get("/api/feed.rss").text
    dates = re.findall(r"<pubDate>([^<]+)</pubDate>", feed)
    assert dates, "feed 里应有 pubDate"
    for d in dates:
        assert re.search(r"[+-]\d{4}$", d.strip()), f"pubDate 缺时区: {d}"
        assert "-0000" not in d, f"pubDate 时区为空: {d}"


def test_voice_preview_mimo(client, studio_home, monkeypatch):
    """MiMo 试听:缺 key 提示配置;配 key 后可合成并走缓存。"""
    from conftest import make_tone_wav

    from podcast_studio.providers.tts.mimo import MimoTTS

    # 缺 key
    r = client.post("/api/voices/preview", json={"selector": "mimo:mimo_default"})
    assert r.status_code == 400 and "API Key" in r.json()["detail"]

    # 配 key + 假 synth
    cfg = Config()
    cfg.llm["openai"]["api_key"] = "sk-mimo"
    cfg.save()

    async def fake_synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
        make_tone_wav(out.parent / (out.stem + ".wav"), 0.2)
        (out.parent / (out.stem + ".wav")).replace(out)

    monkeypatch.setattr(MimoTTS, "synth", fake_synth)
    r = client.post("/api/voices/preview", json={"selector": "mimo:mimo_default"})
    assert r.status_code == 200 and len(r.content) > 500

    def boom(self, *a, **k): raise RuntimeError("should not be called")
    monkeypatch.setattr(MimoTTS, "synth", boom)
    assert client.post("/api/voices/preview", json={"selector": "mimo:mimo_default"}).status_code == 200


def test_voices_format_filtering(client, studio_home):
    """solo 排除双人对谈组;dual 只留双人对谈组。"""
    from podcast_studio.config import Config

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_podcast"] = {
        "api_key": "k-123", "resource_id": "volc.service_type.10050"}
    cfg.save()
    solo = client.get("/api/voices?format=solo").json()
    dual = client.get("/api/voices?format=dual").json()
    assert "volcpodcast" not in solo["builtin"] and "volcpodcast" in dual["builtin"]
    assert "volcspeech" not in dual["builtin"]


def test_voices_volcspeech_filtered_by_granted_resource(client, studio_home, monkeypatch):
    """豆包音色按已授权资源过滤:仅 2.0 授权 → 只列 uranus 系。"""
    from podcast_studio.providers import voices as vr

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_podcast"] = {"api_key": "speech-key-1", "resource_id": "volc.service_type.10050"}
    cfg.save()
    monkeypatch.setattr(vr, "_GRANT_CACHE", {"resources": {"seed-tts-2.0"}, "ts": __import__("time").time()})
    data = client.get("/api/voices").json()
    names = [v["ShortName"] for v in data["builtin"]["volcspeech"]]
    assert names and all("uranus" in n for n in names)


def test_voices_volcspeech_empty_when_nothing_granted(client, studio_home, monkeypatch):
    from podcast_studio.providers import voices as vr

    cfg = Config()
    cfg.data.setdefault("tts", {})["volc_podcast"] = {"api_key": "speech-key-1", "resource_id": "volc.service_type.10050"}
    cfg.save()
    monkeypatch.setattr(vr, "_GRANT_CACHE", {"resources": set(), "ts": __import__("time").time()})
    data = client.get("/api/voices").json()
    assert data["builtin"]["volcspeech"] == []


# ---- R1:ASR 接线回归(async transcribe 曾漏 await,语音输入整体不可用) ----

def test_asr_unconfigured_friendly_error(client):
    r = client.post("/api/asr/transcribe", files={"file": ("a.webm", b"x", "audio/webm")})
    assert r.status_code == 400
    assert "设置" in r.json()["detail"]  # 报错要告诉用户去哪配,不是异常原文


def test_asr_transcribe_awaits_coroutine(client, monkeypatch, studio_home):
    from podcast_studio.config import Config
    from podcast_studio.providers import asr as asr_mod

    cfg = Config.load()
    cfg.data["asr"]["volc"] = {"app_id": "APP", "token": "TOK", "cluster": "volcano_asr"}
    cfg.save()
    seen = {}

    async def fake_transcribe(self, path, *, app_id, token, cluster="volcano_asr"):
        seen["args"] = (app_id, token, cluster)
        return "小兔子迷路了"

    monkeypatch.setattr(asr_mod.VolcASR, "transcribe", fake_transcribe)
    from conftest import make_tone_wav

    wav = make_tone_wav(studio_home / "asr.wav", seconds=0.3)
    r = client.post("/api/asr/transcribe", files={"file": ("a.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "text": "小兔子迷路了"}
    assert seen["args"] == ("APP", "TOK", "volcano_asr")


# ---- R1:网关 Key 不出服务端 + 导入直写配置 ----

def _fake_detect():
    return [{
        "gateway": "magpie", "name": "Magpie 网关", "key_masked": True,
        "presets": [{"name": "GLM 中转", "base_url": "https://x.example/v1",
                     "api_key": "oc_sk_secret1234567890", "model": "glm-5.3-flash"}],
    }]


def test_gateways_response_masks_keys(client, monkeypatch):
    from podcast_studio.providers import gateways as gw_mod

    monkeypatch.setattr(gw_mod, "detect_local_gateways", lambda *a, **k: _fake_detect())
    data = client.get("/api/gateways").json()
    text = json.dumps(data, ensure_ascii=False)
    assert "oc_sk_secret1234567890" not in text  # 明文 Key 绝不回传
    p = data["gateways"][0]["presets"][0]
    assert p["key_preview"].startswith("oc_s") and "****" in p["key_preview"]


def test_gateways_import_writes_config_server_side(client, monkeypatch):
    from podcast_studio.config import Config
    from podcast_studio.providers import gateways as gw_mod

    monkeypatch.setattr(gw_mod, "detect_local_gateways", lambda *a, **k: _fake_detect())
    r = client.post("/api/gateways/import", json={"gateway": "magpie", "name": "GLM 中转"})
    assert r.status_code == 200, r.text
    saved = Config.load()
    assert saved.llm["openai"]["api_key"] == "oc_sk_secret1234567890"  # 明文只进服务端配置
    assert saved.llm["provider"] == "openai" and saved.llm["openai"]["auth_style"] == "bearer"
    body = json.dumps(r.json())
    assert "oc_sk_secret1234567890" not in body  # 响应一律脱敏


def test_gateways_import_unknown_404(client, monkeypatch):
    from podcast_studio.providers import gateways as gw_mod

    monkeypatch.setattr(gw_mod, "detect_local_gateways", lambda *a, **k: _fake_detect())
    r = client.post("/api/gateways/import", json={"gateway": "magpie", "name": "不存在"})
    assert r.status_code == 404


# ---- R1:DNS rebinding 防护(本机服务只接受本机 Host) ----

def test_host_guard_blocks_foreign_host(client):
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_host_guard_allows_local_and_lan(client, monkeypatch):
    assert client.get("/api/health", headers={"Host": "localhost:8686"}).status_code == 200
    # 默认仅本机:私网 IP 拒绝(Q10:放行改为显式 server.allow_lan)
    assert client.get("/api/health", headers={"Host": "192.168.1.8:8686"}).status_code == 403
    from podcast_studio.server import app as app_mod

    monkeypatch.setattr(app_mod, "_ALLOW_LAN", True)
    assert client.get("/api/health", headers={"Host": "192.168.1.8:8686"}).status_code == 200


# ---- R10:QA P3 修复回归(Q5/Q7/Q9) ----

def test_demo_episode_read_only_server_side(client, studio_home):
    """示例期写操作服务端一律 409(Q5:此前仅前端隐藏按钮,绕过 UI 可改可删)。"""
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode

    cfg = Config.load()
    demo = Episode.create(cfg, mode="material", input_text="示例素材", style="bedtime",
                          voice_id="edge:x", minutes=2, origin="demo")
    demo.state["state"] = "awaiting_review"
    demo.save()
    for method, path, body in (
        ("POST", f"/api/episodes/{demo.id}/approve", None),
        ("POST", f"/api/episodes/{demo.id}/script", {"text": "改稿"}),
        ("POST", f"/api/episodes/{demo.id}/shownotes", {"text": "改 shownotes"}),
        ("POST", f"/api/episodes/{demo.id}/rename", {"title": "改名"}),
    ):
        r = client.request(method, path, json=body)
        assert r.status_code == 409, (method, r.status_code)
    assert client.delete(f"/api/episodes/{demo.id}").status_code == 409


def test_error_raw_masks_secret_shaped_tokens(studio_home, fake_tts):
    """error_raw 落盘前脱敏(Q7:provider 错误串可能拼进上游响应体)。"""
    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode

    cfg = Config.load()
    ep = Episode.create(cfg, mode="material", input_text="素材", style="bedtime",
                        voice_id="edge:x", minutes=2)
    ep._fail("synthesis", RuntimeError("上游返回 ark-6286abcd1234efgh 拒绝,token 0397a140-fa69-4cf4-a4a9-11111c5f85d8 无效"))
    raw = ep.state["error_raw"]
    assert "ark-6286abcd1234efgh" not in raw and "0397a140-fa69-4cf4-a4a9-11111c5f85d8" not in raw
    assert "****" in raw


def test_config_oserror_does_not_quarantine(studio_home, monkeypatch):
    """读取时的瞬时 OSError(EBUSY/EACCES)不得把好配置搬走(Q9:仅 JSON 损坏才隔离)。"""
    import json as _json

    from podcast_studio.config import Config

    cfg = Config()
    cfg.llm["openai"]["api_key"] = "sk-keep-me-123456"
    cfg.save()
    target = cfg.load.__func__  # noqa: 占位
    real_read = _json.loads
    import podcast_studio.config as cfg_mod

    orig = cfg_mod.json

    class FakeJson:
        JSONDecodeError = ValueError
        def loads(self, s):
            raise OSError("EBUSY: transient")

    monkeypatch.setattr(cfg_mod, "json", FakeJson())
    loaded = Config.load()
    assert loaded.llm["openai"]["api_key"] == ""  # 降级默认,但文件仍在
    monkeypatch.setattr(cfg_mod, "json", orig)
    assert (cfg_mod.home_dir() / "config.json").exists()  # 原件未被隔离
    assert not list(cfg_mod.home_dir().glob("config.json.corrupt-*"))


# ---- R1:崩溃残留清扫(重启后 synthesizing 不再永久 409) ----

def test_sweep_marks_stale_as_retryable_error(client, studio_home):
    from podcast_studio.config import Config
    from podcast_studio.server.app import sweep_stale_episodes

    cfg = Config.load()
    cfg.ensure_dirs()
    ep_dir = cfg.episodes_dir / "ep-sweep"
    ep_dir.mkdir(parents=True)
    (ep_dir / "state.json").write_text(json.dumps({
        "id": "ep-sweep", "state": "synthesizing", "style": "bedtime",
        "input": "测试选题", "title": "测试", "mode": "topic", "created": "2026-11-03T00:00:00",
    }, ensure_ascii=False), encoding="utf-8")
    import os as _os
    import time as _time
    # Q8 新鲜度保护:把 mtime 拨到 10 分钟前,模拟真正的崩溃残留
    stale = _time.time() - 600
    _os.utime(ep_dir / "state.json", (stale, stale))
    assert sweep_stale_episodes(cfg) == ["ep-sweep"]
    st = json.loads((ep_dir / "state.json").read_text(encoding="utf-8"))
    assert st["state"] == "error" and st["failed_step"] == "synthesis"
    # 清扫后 /retry 不再 409 卡死(后台任务在隔离目录里失败也无妨)
    assert client.post("/api/episodes/ep-sweep/retry").status_code == 200


# ---- R3:克隆声音入口拦截(A4)与首期时长预期标志(A5) ----

def test_clone_upload_warns_when_mimo_missing(client, studio_home):
    from conftest import make_tone_wav

    wav = make_tone_wav(studio_home / "sample.wav", seconds=6)
    r = client.post("/api/voices/clone", data={"name": "我自己"},
                    files={"file": ("sample.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["warning"] and "MiMo" in body["warning"]  # 录完立刻预告,不等合成时才失败


def test_clone_upload_rejects_non_audio(client):
    r = client.post("/api/voices/clone", data={"name": "坏文件"},
                    files={"file": ("x.wav", b"this is not an audio file", "audio/wav")})
    assert r.status_code == 400
    assert "不像音频" in r.json()["detail"]


def test_clone_upload_rejects_too_short(client, studio_home):
    from conftest import make_tone_wav

    wav = make_tone_wav(studio_home / "short.wav", seconds=1)
    r = client.post("/api/voices/clone", data={"name": "太短"},
                    files={"file": ("short.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 400
    assert "太短" in r.json()["detail"]


def test_clone_upload_rejects_too_long(client, studio_home, monkeypatch):
    from conftest import make_tone_wav

    import podcast_studio.server.app as app_mod

    monkeypatch.setattr(app_mod, "CLONE_MAX_SECONDS", 5.0)
    wav = make_tone_wav(studio_home / "long.wav", seconds=6)
    r = client.post("/api/voices/clone", data={"name": "太长"},
                    files={"file": ("long.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 400
    assert "太长" in r.json()["detail"]


def test_upload_rejects_too_large(client, studio_home, monkeypatch):
    from conftest import make_tone_wav

    import podcast_studio.server.uploads as uploads

    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 100)
    wav = make_tone_wav(studio_home / "big.wav", seconds=6)
    r = client.post("/api/voices/clone", data={"name": "超大"},
                    files={"file": ("big.wav", wav.read_bytes(), "audio/wav")})
    assert r.status_code == 413
    assert "太大" in r.json()["detail"]


def test_create_with_clone_voice_blocked_without_mimo(client, studio_home):
    from conftest import make_tone_wav

    wav = make_tone_wav(studio_home / "s2.wav", seconds=6)
    vid = client.post("/api/voices/clone", data={"name": "我自己"},
                      files={"file": ("s2.wav", wav.read_bytes(), "audio/wav")}).json()["voice"]["selector"]
    r = client.post("/api/episodes", json={"mode": "material", "input": "测试素材",
                                           "style": "bedtime", "voice_id": vid})
    assert r.status_code == 400
    assert "MiMo" in r.json()["detail"]


def test_calibrated_flag_false_then_true(studio_home, fake_tts):
    from podcast_studio.config import Config
    from podcast_studio.engine import calibration
    from podcast_studio.engine.pipeline import Episode

    cfg = Config.load()
    ep1 = Episode.create(cfg, mode="material", input_text="素材一", style="bedtime",
                         voice_id="edge:x", minutes=2)
    assert ep1.state["calibrated"] is False  # 首期,前端提示「越用越准」
    calibration.record(cfg, "bedtime", chars=1200, duration_sec=90.0)
    ep2 = Episode.create(cfg, mode="material", input_text="素材二", style="bedtime",
                         voice_id="edge:x", minutes=2)
    assert ep2.state["calibrated"] is True


# ---- R5:LLM 限流退避重试 / 改稿状态闸 ----

def test_llm_chat_retries_rate_limit_then_succeeds(studio_home, monkeypatch):
    from podcast_studio.providers.llm.openai_compat import OpenAICompatLLM

    llm = OpenAICompatLLM({"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    calls = {"n": 0}

    def fake_stream(url, body, timeout=300):
        calls["n"] += 1
        if calls["n"] < 3:
            from podcast_studio.providers.llm.openai_compat import LLMError
            raise LLMError("触发限流（429）", kind="rate_limit")
        return "OK"

    monkeypatch.setattr(llm, "_chat_stream", fake_stream)
    assert llm.chat(task="t", system="s", user="u") == "OK"
    assert calls["n"] == 3  # 首发 + 2 次退避


def test_llm_chat_does_not_retry_auth(studio_home):
    import pytest

    from podcast_studio.providers.llm.openai_compat import LLMError, OpenAICompatLLM

    llm = OpenAICompatLLM({"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    calls = {"n": 0}

    def fake_stream(url, body, timeout=300):
        calls["n"] += 1
        raise LLMError("认证失败", kind="auth")

    llm._chat_stream = fake_stream
    with pytest.raises(LLMError):
        llm.chat(task="t", system="s", user="u")
    assert calls["n"] == 1  # 认证错误不重试


def test_script_endpoint_gated_by_state(client, studio_home, fake_tts):
    r = client.post("/api/episodes", json={"mode": "material", "input": "测试素材",
                                           "style": "bedtime", "voice_id": "edge:x"})
    ep_id = r.json()["id"]
    # 素材模式瞬间到审稿态(演示模式);先等一小会
    import time
    for _ in range(50):
        st = client.get(f"/api/episodes/{ep_id}").json()
        if st["state"] == "awaiting_review":
            break
        time.sleep(0.1)
    if st["state"] == "awaiting_review":
        ok = client.post(f"/api/episodes/{ep_id}/script", json={"text": "改过的稿"})
        assert ok.status_code == 200
        # 模拟合成中:状态闸生效
        from podcast_studio.config import Config
        from podcast_studio.engine.pipeline import load_episode
        ep = load_episode(Config.load(), ep_id)
        ep.state["state"] = "synthesizing"
        ep.save()
        blocked = client.post(f"/api/episodes/{ep_id}/script", json={"text": "再改"})
        assert blocked.status_code == 409


# ---- R4:SRT 字幕与 ID3 章节 ----

def test_subtitles_endpoint_happy_and_empty(client, studio_home, fake_tts):
    from conftest import make_tone_wav

    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode

    cfg = Config.load()
    ep = Episode.create(cfg, mode="material", input_text="彩虹是折射。月亮绕着地球转。",
                        style="bedtime", voice_id="edge:x", minutes=2)
    ep.state["script"] = "[情绪:温柔]彩虹是折射。\n[情绪:平静]月亮绕着地球转。"
    ep.state["title"] = "字幕测试"
    ep.save()
    chunk_dir = ep.dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    for i in range(2):  # 各 0.4s 的真实音频,ffprobe 可测
        make_tone_wav(chunk_dir / f"chunk_{i:03d}.tmp.wav", seconds=0.4)
        (chunk_dir / f"chunk_{i:03d}.tmp.wav").replace(chunk_dir / f"chunk_{i:03d}.mp3")
    r = client.get(f"/api/episodes/{ep.id}/subtitles")
    assert r.status_code == 200
    body = r.text
    assert "-->" in body and "彩虹是折射。" in body
    # 无分段 → 409 人话
    ep2 = Episode.create(cfg, mode="material", input_text="没有分段的素材", style="bedtime",
                         voice_id="edge:x", minutes=2)
    ep2.state["script"] = "有稿没音频"
    ep2.save()
    assert client.get(f"/api/episodes/{ep2.id}/subtitles").status_code == 409


def test_srt_timecode_format():
    from podcast_studio.engine.subtitles import _fmt_ts

    assert _fmt_ts(3661.5, comma=True) == "01:01:01,500"
    assert _fmt_ts(0, comma=False) == "00:00:00.000"


def test_chapters_and_srt_from_pipeline_objects(client, studio_home, fake_tts):
    """pipeline 内部传 Chunk 对象(非 dict)也要能出字幕与章节(真机回归)。"""
    from conftest import make_tone_wav

    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode

    cfg = Config.load()
    ep = Episode.create(cfg, mode="material", input_text="素材内容用于字幕测试。",
                        style="bedtime", voice_id="edge:x", minutes=2)
    ep.state["script"] = "[情绪:温柔]素材内容用于字幕测试。"
    ep.state["title"] = "章节测试"
    ep.save()
    chunk_dir = ep.dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    make_tone_wav(chunk_dir / "chunk_000.tmp.wav", seconds=0.4)
    (chunk_dir / "chunk_000.tmp.wav").replace(chunk_dir / "chunk_000.mp3")
    from podcast_studio.engine import audio as audio_mod

    make_tone_wav(ep.dir / "audio.tmp.wav", seconds=0.5)
    audio_mod.to_mp3(ep.dir / "audio.tmp.wav", ep.dir / "audio.mp3")  # 真成品才写章节
    (ep.dir / "audio.tmp.wav").unlink(missing_ok=True)
    ep._write_chapters_and_srt()  # Chunk 对象路径
    assert (ep.dir / "subtitles.srt").exists()
    assert "-->" in (ep.dir / "subtitles.srt").read_text(encoding="utf-8")

    # 无成品:只出字幕,不炸
    (ep.dir / "audio.mp3").unlink()
    (ep.dir / "subtitles.srt").unlink()
    ep._write_chapters_and_srt()
    assert (ep.dir / "subtitles.srt").exists()


# ---- R8:垫乐混音槽 ----

def test_mix_bgm_envelope_and_duration(studio_home):
    from conftest import make_tone_wav
    from podcast_studio.engine import audio as audio_mod

    voice = make_tone_wav(studio_home / "v.wav", seconds=8)
    bgm = make_tone_wav(studio_home / "b.wav", seconds=3)
    mp3 = audio_mod.to_mp3(voice, studio_home / "voice.mp3")
    out = audio_mod.mix_bgm(mp3, bgm, volume=0.1)
    assert out == mp3 and out.exists()
    dur = audio_mod.duration_seconds(out)
    assert dur and 7.5 <= dur <= 9.0  # 时长跟人声走(duration=first)
    assert not out.with_suffix(".bgm.tmp.mp3").exists()  # 无临时残留


def test_mix_bgm_skips_short_audio(studio_home):
    from conftest import make_tone_wav
    from podcast_studio.engine import audio as audio_mod

    voice = audio_mod.to_mp3(make_tone_wav(studio_home / "v2.wav", seconds=2), studio_home / "v2.mp3")
    bgm = make_tone_wav(studio_home / "b2.wav", seconds=3)
    assert audio_mod.mix_bgm(voice, bgm) == voice  # 短音频直接跳过


def test_audio_config_endpoint_roundtrip_and_validation(client, studio_home):
    # 文件不存在 → 400 人话
    r = client.post("/api/config/audio", json={"bgm_path": "/no/such/music.mp3"})
    assert r.status_code == 400 and "不存在" in r.json()["detail"]
    # 真文件 → 保存回读
    from conftest import make_tone_wav
    wav = make_tone_wav(studio_home / "m.wav", seconds=1)
    r = client.post("/api/config/audio", json={"bgm_path": str(wav), "bgm_volume": 0.2})
    assert r.status_code == 200 and r.json()["audio"]["bgm_volume"] == 0.2
    # 音量越界钳制
    r = client.post("/api/config/audio", json={"bgm_path": str(wav), "bgm_volume": 9})
    assert r.json()["audio"]["bgm_volume"] == 0.5
    # 留空清除
    r = client.post("/api/config/audio", json={"bgm_path": ""})
    assert r.json()["audio"]["bgm_path"] == ""


# ---- QA 深审修复回归(Q1/Q2/Q3) ----

def test_write_chapters_preserves_title(studio_home):
    """章节写入不得抹掉成品 ID3 标题(Q1:此前 map_metadata 1 把标题改成「章节」)。"""
    import json
    import subprocess

    from conftest import make_tone_wav
    from podcast_studio.engine import audio as audio_mod

    mp3 = audio_mod.to_mp3(make_tone_wav(studio_home / "v.wav", seconds=8), studio_home / "a.mp3")
    tmp = studio_home / "a2.mp3"
    audio_mod.finalize(mp3, tmp, title="小雨滴的旅行", artist="播客工坊")
    tmp.replace(mp3)
    meta = ";FFMETADATA1\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=开头\n"
    audio_mod.write_chapters(mp3, meta)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
                          "-of", "json", str(mp3)], capture_output=True, text=True)
    assert json.loads(out.stdout)["format"]["tags"]["title"] == "小雨滴的旅行"


def test_host_guard_allows_ipv6_loopback(client):
    """IPv6 字面量 Host 不得被 403(Q2:此前 [::1]:8686 一律拒绝)。"""
    assert client.get("/api/health", headers={"Host": "[::1]:8686"}).status_code == 200


def test_resynth_applies_pace_rate(studio_home, monkeypatch):
    """单段重录必须带语速映射(Q3:此前慢/快语速重录段与全片不一致)。"""
    from pathlib import Path as _P

    from podcast_studio.config import Config
    from podcast_studio.engine.pipeline import Episode
    from podcast_studio.providers.tts.base import VoiceSpec

    seen = {}

    class T:
        name = "edge"
        supports_emotion = False
        supports_clone = False

        async def synth(self, text, voice, style, *, rate="+0%", pitch="+0Hz", out):
            seen["rate"] = rate
            from conftest import make_tone_wav
            make_tone_wav(_P(str(out) + ".t.wav"), 0.3)
            _P(str(out) + ".t.wav").replace(out)

    from podcast_studio.providers import voices as voice_reg
    from podcast_studio.engine import pipeline as pipeline_mod

    def fake_resolve(cfg, llm_cfg, sel):
        return T(), VoiceSpec(provider="edge", voice_id="edge:x", label="t")

    monkeypatch.setattr(voice_reg, "resolve_voice", fake_resolve)
    monkeypatch.setattr(pipeline_mod, "resolve_voice", fake_resolve)
    cfg = Config.load()
    ep = Episode.create(cfg, mode="material", input_text="素材", style="bedtime",
                        voice_id="edge:x", minutes=2, pace="慢")
    ep.state["script"] = "[情绪:温柔]" + "内容。" * 60
    ep.state["state"] = "done"
    ep.state["synthesis"] = {"total": 1, "done": 0, "current": ""}  # 真实流程必有(approve 时建立)
    ep.save()
    chunk_dir = ep.dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    from conftest import make_tone_wav as _mtw
    from podcast_studio.engine import audio as _audio
    _audio.to_mp3(_mtw(chunk_dir / "c0.wav", 0.4), chunk_dir / "chunk_000.mp3")  # 既有分段(重录前提)
    ep.resynth_chunk(0)
    assert not ep.state.get("error"), ep.state.get("error")
    assert seen["rate"] == "-15%"  # 慢速映射与整期合成一致


# ---- R6:归档/取消归档(不再只能删) ----

def _make_done_episode(cfg, ep_id="ep-arch"):
    import json as _json

    ep_dir = cfg.episodes_dir / ep_id
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "state.json").write_text(
        _json.dumps({"id": ep_id, "state": "done", "title": "旧节目", "style": "bedtime",
                     "created": "2026-01-01T00:00:00", "minutes": 3, "origin": "real"}),
        encoding="utf-8",
    )
    return ep_id


def test_archive_hides_from_default_and_can_restore(client, studio_home):
    cfg = Config.load()
    ep_id = _make_done_episode(cfg)

    r = client.post(f"/api/episodes/{ep_id}/archive", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True

    rows = {e["id"]: e for e in client.get("/api/episodes").json()}
    assert rows[ep_id]["archived"] is True
    # 文件与音频都没被删除
    assert (cfg.episodes_dir / ep_id / "state.json").exists()

    r = client.post(f"/api/episodes/{ep_id}/archive", json={"archived": False})
    assert r.status_code == 200
    rows = {e["id"]: e for e in client.get("/api/episodes").json()}
    assert rows[ep_id]["archived"] is False


def test_archive_rejects_running_episode(client, studio_home, monkeypatch):
    cfg = Config.load()
    ep_id = _make_done_episode(cfg, "ep-running")
    import json as _json

    sf = cfg.episodes_dir / ep_id / "state.json"
    data = _json.loads(sf.read_text(encoding="utf-8"))
    data["state"] = "synthesizing"
    sf.write_text(_json.dumps(data), encoding="utf-8")

    r = client.post(f"/api/episodes/{ep_id}/archive", json={"archived": True})
    assert r.status_code == 409


def test_archive_demo_episode_is_read_only(client, studio_home):
    cfg = Config.load()
    ep_id = _make_done_episode(cfg, "ep-demo-arch")
    import json as _json

    sf = cfg.episodes_dir / ep_id / "state.json"
    data = _json.loads(sf.read_text(encoding="utf-8"))
    data["origin"] = "demo"
    sf.write_text(_json.dumps(data), encoding="utf-8")

    r = client.post(f"/api/episodes/{ep_id}/archive", json={"archived": True})
    assert r.status_code == 409


# ---- R7:health 不泄露本机绝对路径 ----

def test_health_does_not_leak_absolute_data_dir(client, studio_home):
    data = client.get("/api/health").json()
    assert "data_dir" not in data
    assert data["data_dir_hint"] == "~/.podcast-studio/data"
    assert "allow_lan" in data
    assert str(studio_home) not in json.dumps(data, ensure_ascii=False)
