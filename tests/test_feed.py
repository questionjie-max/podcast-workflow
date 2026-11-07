"""RSS feed：只收真实已完成节目、XML 转义、RFC2822 时区。"""

import json
import xml.etree.ElementTree as ET

import pytest

from podcast_studio.config import Config
from podcast_studio.server import feed as feed_mod


def _make_episode(cfg, ep_id, *, state="done", origin="real", title="节目", audio=True,
                  created="2026-10-30T01:18:40"):
    ep_dir = cfg.episodes_dir / ep_id
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "state.json").write_text(json.dumps({
        "id": ep_id, "title": title, "state": state, "style": "bedtime",
        "created": created, "minutes": 3, "origin": origin,
    }), encoding="utf-8")
    if audio:
        (ep_dir / "audio.mp3").write_bytes(b"\xff\xfb" + b"0" * 100)
    return ep_dir


def test_feed_excludes_demo_and_unfinished(studio_home, monkeypatch):
    cfg = Config.load()
    cfg.ensure_dirs()
    _make_episode(cfg, "ep-real", title="真实节目")
    _make_episode(cfg, "ep-demo", origin="demo", title="示例节目")
    _make_episode(cfg, "ep-draft", state="awaiting_review", title="草稿")
    _make_episode(cfg, "ep-noaudio", audio=False, title="没音频")

    monkeypatch.setattr(feed_mod, "_audio_duration", lambda path, fallback=0: 90.0)
    xml = feed_mod.build_feed(cfg, "http://localhost:8686")

    assert "真实节目" in xml
    assert "示例节目" not in xml
    assert "草稿" not in xml
    assert "没音频" not in xml
    # 必须是可解析的 XML
    root = ET.fromstring(xml)
    items = root.findall("./channel/item")
    assert len(items) == 1


def test_feed_escapes_special_characters(studio_home, monkeypatch):
    cfg = Config.load()
    cfg.ensure_dirs()
    _make_episode(cfg, "ep-x", title="A&B <test> \"quoted\"")
    monkeypatch.setattr(feed_mod, "_audio_duration", lambda path, fallback=0: 60.0)
    xml = feed_mod.build_feed(cfg, "http://localhost:8686")

    ET.fromstring(xml)  # 不转义会在这里炸
    assert "A&amp;B" in xml
    assert "<test>" not in xml


def test_rfc2822_has_timezone():
    out = feed_mod._rfc2822("2026-10-30T01:18:40:00")
    assert out.endswith("+0000") or "+" in out or "-" in out
    assert out != "2026-10-30T01:18:40:00"


def test_rfc2822_handles_garbage():
    assert feed_mod._rfc2822("not-a-date") == "not-a-date"
    assert feed_mod._rfc2822("2026-10-30 01:18:40").startswith("2026-10-30")


def test_itunes_duration_format():
    assert feed_mod._itunes_duration(0) == "0:00"
    assert feed_mod._itunes_duration(65) == "1:05"
    assert feed_mod._itunes_duration(3725) == "1:02:05"


def test_feed_route_returns_rss(studio_home, monkeypatch):
    from fastapi.testclient import TestClient

    from podcast_studio.server.app import app

    cfg = Config.load()
    cfg.ensure_dirs()
    _make_episode(cfg, "ep-route", title="路由节目")
    monkeypatch.setattr(feed_mod, "_audio_duration", lambda path, fallback=0: 60.0)

    client = TestClient(app)
    r = client.get("/api/feed.rss")
    assert r.status_code == 200
    assert "application/rss+xml" in r.headers["content-type"]
    assert "路由节目" in r.text
