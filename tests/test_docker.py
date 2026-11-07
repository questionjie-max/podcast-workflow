"""Docker 交付物静态检查（本机无 docker，只能守结构不跑真机）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_runs_as_non_root_and_has_healthcheck():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "useradd" in text and "USER app" in text
    assert "HEALTHCHECK" in text and "/api/health" in text
    # 必须装 ffmpeg（合成依赖）与中文字体（封面依赖）
    assert "ffmpeg" in text and "fonts-noto-cjk" in text
    # 必须带上 MANIFEST.in，否则 wheel 数据文件缺失（0.5.2 修复的回归点）
    assert "MANIFEST.in" in text
    assert "PODCAST_STUDIO_HOME=/data" in text


def test_compose_has_healthcheck_and_volume():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "healthcheck:" in text and "api/health" in text
    assert "./podcast-data:/data" in text
    assert "restart:" in text
    # 默认只绑本机回环，避免无意把无鉴权 API 暴露到局域网
    assert "127.0.0.1:8686:8686" in text


def test_dockerignore_excludes_heavy_and_secret_paths():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".venv", ".git", "data", "podcast-data", "tests", "docs"):
        assert pattern in text
    # 不能忽略 README（pyproject readme 字段需要它构建）
    assert "!README.md" in text
