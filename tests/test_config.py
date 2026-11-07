"""config:深合并、环境变量覆盖、目录派生。"""

import json

from podcast_studio.config import DEFAULT_CONFIG, Config, home_dir


def test_deep_merge_partial_override(studio_home):
    cfg = Config({"llm": {"openai": {"api_key": "sk-x"}}})
    # 局部覆盖不丢默认值
    assert cfg.llm["openai"]["base_url"] == DEFAULT_CONFIG["llm"]["openai"]["base_url"]
    assert cfg.llm["provider"] == "mock"
    assert cfg.llm["openai"]["api_key"] == "sk-x"


def test_load_persists_and_env_key_priority(studio_home, monkeypatch):
    cfg = Config()
    cfg.llm["openai"]["api_key"] = "from-file"
    cfg.save()
    assert (home_dir() / "config.json").exists()

    monkeypatch.setenv("MIMO_API_KEY", "from-env")
    loaded = Config.load()
    assert loaded.llm["openai"]["api_key"] == "from-env"

    monkeypatch.delenv("MIMO_API_KEY")
    assert Config.load().llm["openai"]["api_key"] == "from-file"


def test_dirs_derived(studio_home):
    cfg = Config()
    cfg.ensure_dirs()
    assert (home_dir() / "data" / "episodes").is_dir()
    assert (home_dir() / "data" / "voices").is_dir()
    assert str(home_dir()) in str(cfg.data_dir)


def test_corrupt_config_falls_back(studio_home):
    (home_dir()).mkdir(parents=True, exist_ok=True)
    (home_dir() / "config.json").write_text("{不是 JSON", encoding="utf-8")
    cfg = Config.load()
    assert cfg.llm["provider"] == "mock"


def test_masked_key(studio_home):
    cfg = Config()
    cfg.llm["openai"]["api_key"] = "sk-abcdefgh12345678"
    masked = cfg.masked()["llm"]["openai"]["api_key"]
    assert "abcdefgh12345678" not in masked
    assert masked.startswith("sk-a") and "****" in masked


def test_saved_config_is_0600(studio_home):
    import os

    cfg = Config()
    cfg.save()
    assert (home_dir() / "config.json").exists()
    mode = os.stat(home_dir() / "config.json").st_mode & 0o777
    assert mode == 0o600, f"config.json 权限应为 600,实际 {oct(mode)}"


# ---- R1:配置安全与持久化(回归:写坏即静默清空 Key / save 非原子) ----

def test_corrupt_config_recovers_from_bak(studio_home):
    cfg = Config()
    cfg.llm["openai"]["api_key"] = "sk-good-key-12345678"
    cfg.save()
    (home_dir() / "config.json").write_text("{broken json!!", encoding="utf-8")
    recovered = Config.load()
    assert recovered.llm["openai"]["api_key"] == "sk-good-key-12345678"
    assert list(home_dir().glob("config.json.corrupt-*"))  # 损坏原件隔离留存


def test_corrupt_config_without_bak_quarantines(studio_home):
    home_dir().mkdir(parents=True, exist_ok=True)
    (home_dir() / "config.json").write_text("not json at all", encoding="utf-8")
    cfg = Config.load()
    assert cfg.llm["openai"]["api_key"] == ""  # 以默认配置启动,不崩溃
    assert list(home_dir().glob("config.json.corrupt-*"))  # 原件保留可人工抢救


def test_save_is_atomic_and_keeps_bak(studio_home):
    cfg = Config()
    cfg.save()
    assert (home_dir() / "config.json.bak").exists()
    assert not (home_dir() / "config.json.tmp").exists()  # 无残留临时文件


def test_masked_covers_search_and_generic_key_names(studio_home):
    cfg = Config()
    cfg.data["search"]["tavily"]["api_key"] = "tvly-secret-123456789"
    cfg.data["tts"]["volc"]["token"] = "tok-abcdef123456"
    m = json.dumps(cfg.masked(), ensure_ascii=False)
    assert "tvly-secret-123456789" not in m and "tok-abcdef123456" not in m
    assert "tvly" in m  # 首尾保留,便于人工核对是哪把 Key
