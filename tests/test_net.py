"""代理环境兼容:httpx 不支持的 ALL_PROXY scheme 必须被安全忽略。"""

from podcast_studio import net


def _reset(monkeypatch):
    monkeypatch.setattr(net, "proxy_warnings", [])
    monkeypatch.setattr(net, "_printed", set())


def test_invalid_socks_scheme_is_removed(monkeypatch, capsys):
    _reset(monkeypatch)
    monkeypatch.setenv("ALL_PROXY", "socks://user:secret@127.0.0.1:7897")
    removed = net.sanitize_proxy_env()

    assert "ALL_PROXY" not in __import__("os").environ
    assert removed and "socks://" in removed[0]
    assert net.proxy_warnings
    err = capsys.readouterr().err
    assert "user:secret" not in err  # 不泄露代理账号密码


def test_valid_socks5_proxy_is_kept(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7897")
    removed = net.sanitize_proxy_env()

    assert removed == []
    assert __import__("os").environ["ALL_PROXY"] == "socks5://127.0.0.1:7897"


def test_config_load_sanitizes_proxy(monkeypatch, tmp_path, capsys):
    _reset(monkeypatch)
    monkeypatch.setenv("PODCAST_STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7897")

    from podcast_studio.config import Config

    Config.load()

    assert "ALL_PROXY" not in __import__("os").environ
    assert net.proxy_warnings
    assert "socks://" in capsys.readouterr().err
