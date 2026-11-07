#!/usr/bin/env bash
# 播客工坊 Podcast Studio — 一键安装
# 幂等:可重复执行。用法: bash install.sh
set -euo pipefail

cd "$(dirname "$0")"
echo "🎙️ 播客工坊 Podcast Studio — 安装程序"

# ---- 1. Python 检查 ---------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3,请先安装 Python ≥ 3.10"; exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
echo "✓ Python $PYVER"

# ---- 2. ffmpeg 检查(音视频处理必需) ------------------------------------------
if command -v ffmpeg >/dev/null 2>&1; then
    echo "✓ ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
else
    echo "⚠️  未找到 ffmpeg —— 没有它无法合成音频。请先安装:"
    echo "    Ubuntu/Debian: sudo apt install ffmpeg"
    echo "    macOS:         brew install ffmpeg"
    echo "    Fedora:        sudo dnf install ffmpeg"
    echo "(可以先继续安装,但合成会失败)"
fi

# ---- 3. venv + 依赖 ----------------------------------------------------------
VENV=".venv"
if [ ! -x "$VENV/bin/python" ]; then
    echo "→ 创建虚拟环境 $VENV ..."
    if ! python3 -m venv "$VENV" 2>/dev/null; then
        echo "  (venv 模块不完整,改用 without-pip + get-pip 引导)"
        python3 -m venv --without-pip "$VENV"
        curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        "$VENV/bin/python" /tmp/get-pip.py
    fi
fi
echo "→ 安装依赖(首次较慢)..."
"$VENV/bin/pip" install -q -e .
"$VENV/bin/pip" install -q pytest || true

# ---- 4. 生成启动脚本 ----------------------------------------------------------
START_SH="$PWD/start.sh"
cat > "$START_SH" <<EOF
#!/usr/bin/env bash
cd "$PWD"
exec "$VENV/bin/podcast-studio" serve "\$@"
EOF
chmod +x "$START_SH"
echo "✓ 启动脚本 $START_SH"

# ---- 5. 桌面快捷方式(Linux/macOS 尽力而为) -------------------------------------
PORT="${PODCAST_STUDIO_PORT:-8686}"
if [ -d "$HOME/.local/share/applications" ] && command -v python3 >/dev/null; then
    DESKTOP="$HOME/.local/share/applications/podcast-studio.desktop"
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=播客工坊 Podcast Studio
Comment=本地播客/故事电台工作室
Exec=bash -c "cd '$PWD' && $VENV/bin/podcast-studio serve >/dev/null 2>&1 & sleep 2; xdg-open http://localhost:$PORT"
Icon=audio-x-generic
Terminal=false
Categories=Audio;AudioVideo;
EOF
    echo "✓ 桌面快捷方式 $DESKTOP(应用列表里会出现「播客工坊」)"
fi

# ---- 6. 冒烟自检 ----------------------------------------------------------------
echo "→ 自检..."
if "$VENV/bin/python" -c "import podcast_studio, fastapi, edge_tts, httpx" 2>/dev/null; then
    echo "✓ 依赖完整"
else
    echo "❌ 依赖异常,请重试或查看上方报错"; exit 1
fi

echo
echo "✅ 安装完成!"
echo "   启动:  bash $START_SH   (或 $VENV/bin/podcast-studio serve)"
echo "   使用:  浏览器打开 http://localhost:$PORT"
echo "   默认演示模式无需 API key;正式使用请在网页「设置」里配置。"
