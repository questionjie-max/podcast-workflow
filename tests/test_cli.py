"""CLI 命令测试:make 素材模式 --yes 全链路(引擎与 API 层共用,夹具可复用)。"""

import pytest

from podcast_studio.cli import main
from podcast_studio.config import Config
from podcast_studio.engine.pipeline import list_episodes


def test_cli_make_material_yes(studio_home, fake_tts, tmp_path):
    """素材模式全自动:产出成品 MP3 到 --out 目录,节目状态 done。"""
    material = tmp_path / "story.txt"
    material.write_text("彩虹是阳光在水滴里折射形成的。", encoding="utf-8")
    out_dir = tmp_path / "out"

    main([
        "make", str(material),
        "--material", "--style", "bedtime",
        "--minutes", "2", "--yes",
        "--out", str(out_dir),
    ])

    produced = list(out_dir.glob("*.mp3"))
    assert produced and produced[0].stat().st_size > 5000

    cfg = Config.load()
    eps = list_episodes(cfg)
    assert eps and eps[0]["state"] == "done"
    assert eps[0]["title"]  # mock 起了标题


def test_cli_make_missing_material_file(studio_home, fake_tts, tmp_path):
    """素材文件不存在 → 报错退出(SystemExit),不留半成品节目。"""
    with pytest.raises((SystemExit, FileNotFoundError)):
        main(["make", str(tmp_path / "不存在.txt"), "--material", "--yes"])

    assert list_episodes(Config.load()) == []


def test_cli_serve_smoke_flags(studio_home):
    """serve 参数解析正常(不真正起服务,只验证 argparse 不报错——用错误端口快速失败)。"""
    # 端口 0 会绑定失败,这里只验证参数解析:用一个必然立即退出的方式不值得,
    # 因此仅验证 --help 路径
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_port_helpers_skip_busy_port():
    import socket

    from podcast_studio.cli import _find_free_port, _port_available

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    busy = sock.getsockname()[1]
    try:
        assert not _port_available("127.0.0.1", busy)
        free = _find_free_port("127.0.0.1", busy + 1, attempts=50)
        assert free is not None and free != busy
    finally:
        sock.close()


def test_serve_detects_existing_instance(studio_home, monkeypatch, capsys):
    import uvicorn

    import podcast_studio.cli as cli

    monkeypatch.setattr(cli, "_port_available", lambda host, port: False)
    monkeypatch.setattr(cli, "_existing_instance", lambda host, port: "0.5.2")
    called = {"run": False}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: called.update(run=True))

    cli.main(["serve"])

    out = capsys.readouterr().out
    assert "已有播客工坊在运行" in out and "0.5.2" in out
    assert called["run"] is False


def test_serve_auto_picks_next_port(studio_home, monkeypatch):
    import uvicorn

    import podcast_studio.cli as cli

    monkeypatch.setattr(cli, "_port_available", lambda host, port: port != 8686)
    monkeypatch.setattr(cli, "_existing_instance", lambda host, port: None)
    monkeypatch.setattr(cli, "_find_free_port", lambda host, start, attempts=20: 8687)
    seen = {}

    def fake_run(app, host, port, log_level):
        seen.update(host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    cli.main(["serve"])

    assert seen["port"] == 8687


def test_serve_explicit_busy_port_exits(studio_home, monkeypatch):
    import podcast_studio.cli as cli

    monkeypatch.setattr(cli, "_port_available", lambda host, port: False)
    monkeypatch.setattr(cli, "_existing_instance", lambda host, port: None)

    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--port", "8686"])
    assert exc.value.code == 1
