"""gateways:内置 LLM 预设 + 本地网关探测(纯 tmp_path 注入,不读真实用户配置)。"""

import json
import sqlite3
from pathlib import Path

from podcast_studio.providers.gateways import (
    LLM_PRESETS,
    detect_local_gateways,
    mask_key,
)

# ---- 构造工具:全部写到 tmp_path,绝不触碰 ~/.cc-switch 或真实 magpie --------


def _pi_cfg(name: str, base_url: str, api_key: str, models=None) -> str:
    """构造 CC Switch providers.settings_config 的 JSON 文本(pi 型供应商)。"""
    cfg: dict = {"name": name, "baseUrl": base_url, "apiKey": api_key}
    if models is not None:
        cfg["models"] = models
    return json.dumps(cfg)


def _make_cc_switch_db(path: Path, rows: list[tuple]) -> Path:
    """在临时目录构造假的 CC Switch SQLite 配置。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, "
            "settings_config TEXT, is_current INTEGER, sort_index INTEGER)"
        )
        con.executemany("INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()
    return path


def _write_magpie(path: Path, providers: list[dict]) -> Path:
    """在临时目录构造假的 Magpie providers.json。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"providers": providers}, ensure_ascii=False), encoding="utf-8"
    )
    return path


# ---- LLM_PRESETS ------------------------------------------------------------


def test_llm_presets_builtin():
    """内置预设表:五个供应商齐全,deepseek 字段与 auth_style 正确。"""
    by_id = {p["id"]: p for p in LLM_PRESETS}
    assert {"deepseek", "qwen", "kimi", "mimo", "openai"} <= set(by_id)

    deepseek = by_id["deepseek"]
    assert deepseek["base_url"] == "https://api.deepseek.com/v1"
    assert deepseek["model"] == "deepseek-chat"
    assert deepseek["auth_style"] == "bearer"

    assert by_id["mimo"]["auth_style"] == "api-key"  # MiMo 用 api-key 头
    assert by_id["qwen"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    for p in LLM_PRESETS:  # 每项字段齐全,认证方式合法
        assert set(p) >= {"id", "name", "base_url", "model", "auth_style"}
        assert p["auth_style"] in ("bearer", "api-key")
        assert p["base_url"].startswith("https://")


# ---- CC Switch(SQLite)------------------------------------------------------


def test_detect_cc_switch_sqlite_providers(tmp_path):
    """假 CC Switch db:第三方供应商被解析,官方/Anthropic 占位行被跳过。"""
    home = tmp_path / "home"
    _make_cc_switch_db(
        home / ".cc-switch" / "cc-switch.db",
        [
            ("p1", "pi", "OpenCode Go",
             _pi_cfg("OpenCode Go", "https://gw.example.com/v1/",
                     "oc_saaaaaaaa_b9",
                     [{"id": "glm-5.3-flash", "name": "glm-5.3-flash"}]),
             0, 0),
            ("p2", "pi", "Second",
             _pi_cfg("Second", "https://b.example.com/v1", "k2-1234567890abcd"),
             0, 1),
            ("p3", "claude", "Claude Official", json.dumps({"env": {}}), 0, 2),
            ("p4", "claude", "Anthropic 第三方",
             json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://a.example.com",
                                 "ANTHROPIC_AUTH_TOKEN": "tok-1234567890"}}),
             0, 3),
            ("p5", "pi", "坏行", "不是 JSON{{{", 0, 4),
        ],
    )

    gateways = detect_local_gateways(home=home)
    assert len(gateways) == 1
    gw = gateways[0]
    assert gw["gateway"] == "cc-switch"
    assert gw["name"] == "CC Switch(本地供应商切换器)"
    assert gw["key_masked"] is True

    presets = gw["presets"]
    assert len(presets) == 2  # 官方/Anthropic/坏行都被跳过
    first, second = presets
    assert first["name"] == "OpenCode Go"
    assert first["base_url"] == "https://gw.example.com/v1"  # 尾部斜杠已去掉
    assert first["api_key"] == "oc_saaaaaaaa_b9"  # 明文,供服务端导入写配置
    assert first["model"] == "glm-5.3-flash"
    assert second["base_url"] == "https://b.example.com/v1"
    assert second["api_key"] == "k2-1234567890abcd"
    assert second["model"] == ""  # 无 models 时为空串


def test_cc_switch_db_without_providers_table_skipped(tmp_path):
    """SQLite 文件存在但没有 providers 表 → 该网关跳过,不抛异常。"""
    home = tmp_path / "home"
    db = home / ".cc-switch" / "cc-switch.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE other (x INTEGER)")
        con.commit()
    finally:
        con.close()
    assert detect_local_gateways(home=home) == []


# ---- Magpie -----------------------------------------------------------------


def test_detect_magpie_providers(tmp_path):
    """假 Magpie providers.json:chat 优先、responses 兜底、无效条目跳过。"""
    home = tmp_path / "home"
    _write_magpie(
        home / ".config" / "magpie" / "providers.json",
        [
            {"id": "opencode-go", "name": "OpenCode Go", "key": "oc_saaaaaaaa_b9",
             "chat": "https://go.example.com/v1",
             "responses": "https://go.example.com/v1",
             "anthropic": "https://go.example.com/anthropic",
             "models": ["glm-5.3-flash", "deepseek-v3-flash"]},
            {"id": "zen", "name": "OpenCode Zen", "key": "public",
             "responses": "https://zen.example.com/v1"},  # 无 chat → 退回 responses
            {"id": "empty", "name": "", "key": ""},  # 无 URL 无 key → 跳过
            "不是字典的一行",  # 结构不符 → 跳过
        ],
    )

    gateways = detect_local_gateways(home=home)
    assert len(gateways) == 1
    gw = gateways[0]
    assert gw["gateway"] == "magpie"
    assert gw["name"] == "Magpie(本地网关)"
    assert gw["key_masked"] is True

    presets = gw["presets"]
    assert len(presets) == 2
    assert presets[0]["name"] == "OpenCode Go"
    assert presets[0]["base_url"] == "https://go.example.com/v1"
    assert presets[0]["api_key"] == "oc_saaaaaaaa_b9"
    assert presets[0]["model"] == "glm-5.3-flash"
    assert presets[1]["base_url"] == "https://zen.example.com/v1"
    assert presets[1]["api_key"] == "public"
    assert presets[1]["model"] == ""


def test_magpie_wrong_shape_skipped(tmp_path):
    """providers.json 是合法 JSON 但结构不对(providers 非列表)→ 跳过。"""
    home = tmp_path / "home"
    path = home / ".config" / "magpie" / "providers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": "应该是个列表"}), encoding="utf-8")
    assert detect_local_gateways(home=home) == []


# ---- 综合情形 -----------------------------------------------------------------


def test_both_missing_returns_empty(tmp_path):
    """两个网关都不存在 → 空列表(UI 不显示入口)。"""
    assert detect_local_gateways(home=tmp_path / "nothing-here") == []


def test_corrupt_inputs_skipped_silently(tmp_path):
    """SQLite 文件损坏 + JSON 损坏 → 全部跳过,返回空列表且不抛异常。"""
    home = tmp_path / "home"
    db = home / ".cc-switch" / "cc-switch.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is definitely not a sqlite database file")
    bad = home / ".config" / "magpie" / "providers.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{这不是完整 JSON", encoding="utf-8")
    assert detect_local_gateways(home=home) == []


def test_explicit_paths_override_home(tmp_path):
    """显式传 cc_switch_path/magpie_path 时用传入文件(测试注入通道)。"""
    home = tmp_path / "home"  # home 下什么都不放
    work = tmp_path / "inject"
    work.mkdir()
    db = _make_cc_switch_db(
        work / "custom.db",
        [("p1", "pi", "X",
          _pi_cfg("X", "https://x.example.com/v1", "key-1234567890"), 0, 0)],
    )
    mg = _write_magpie(
        work / "custom-providers.json",
        [{"id": "m", "name": "M", "key": "mk-12345678",
          "chat": "https://m.example.com/v1"}],
    )

    gateways = detect_local_gateways(
        cc_switch_path=db, magpie_path=mg, home=home
    )
    assert [g["gateway"] for g in gateways] == ["cc-switch", "magpie"]
    assert gateways[0]["presets"][0]["base_url"] == "https://x.example.com/v1"
    assert gateways[0]["presets"][0]["api_key"] == "key-1234567890"
    assert gateways[1]["presets"][0]["api_key"] == "mk-12345678"


def test_mask_key_helper():
    """脱敏工具:前4+****+后4;过短一律 ****;空值返回空串。"""
    assert mask_key("sk-abcdefgh12345678") == "sk-a****5678"
    assert mask_key("short") == "****"
    assert mask_key("") == ""
