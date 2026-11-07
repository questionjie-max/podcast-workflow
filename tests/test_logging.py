"""日志脱敏与诊断包:用户可以放心把证据交出来，不会带出 API Key。"""

import json
import logging
import zipfile

from podcast_studio.config import Config
from podcast_studio.logging_setup import get_logger, redact, setup_logging


def _close_ps_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ps_handler", False):
            root.removeHandler(handler)
            handler.close()


def test_redact_masks_common_key_shapes():
    text = (
        "api_key=sk-abcdefgh12345678 "
        "Authorization: Bearer abcdefgh12345678 "
        "x-api-key: arkVALUE12345678 "
        "normal message token budget=10"
    )
    clean = redact(text)
    assert "sk-abcdefgh12345678" not in clean
    assert "abcdefgh12345678" not in clean
    assert "arkVALUE12345678" not in clean
    assert "normal message" in clean
    assert "budget=10" in clean  # 非密钥字段不误伤


def test_logging_file_is_redacted(studio_home):
    cfg = Config.load()
    path = setup_logging(cfg)
    get_logger("test").info("provider failed key=%s", "sk-abcdefgh12345678")
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert "sk-abcdefgh12345678" not in text
    assert "****" in text
    _close_ps_handlers()


def test_doctor_bundle_excludes_plaintext_secrets(studio_home, capsys):
    cfg = Config.load()
    cfg.data.setdefault("llm", {}).setdefault("openai", {})["api_key"] = "sk-secret-12345678"
    cfg.save()
    log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "app.log").write_text(
        "error api_key=sk-log-secret-12345678\n", encoding="utf-8"
    )

    from podcast_studio.cli import main

    main(["doctor", "--bundle"])
    out = capsys.readouterr().out
    assert "诊断包已生成" in out

    bundles = list((studio_home / "diagnostics").glob("*.zip"))
    assert len(bundles) == 1
    with zipfile.ZipFile(bundles[0]) as zf:
        names = set(zf.namelist())
        assert {"report.txt", "config.masked.json", "environment.json", "logs/app.log"} <= names
        content = "\n".join(
            zf.read(n).decode("utf-8", "replace") for n in names
        )
    assert "sk-secret-12345678" not in content
    assert "sk-log-secret-12345678" not in content
    masked = json.loads(zipfile.ZipFile(bundles[0]).read("config.masked.json"))
    assert "****" in masked["llm"]["openai"]["api_key"]
