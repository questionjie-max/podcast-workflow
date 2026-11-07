"""封面图生成:ffmpeg drawtext + 系统中文字体。

条件执行:找不到 CJK 字体时返回 None,调用方优雅跳过(封面是加分项,不是必需)。
输出:1400x1400 PNG(UI 展示)+ JPG(嵌入 MP3 ID3 附图)。
"""

import shutil
import subprocess
from pathlib import Path

# 节目类型 → 底色/点缀色
STYLE_COLORS = {
    "bedtime": {"bg": "0x2f3560", "accent": "0x8f9bd9"},
    "podcast": {"bg": "0xb8542e", "accent": "0xffd9c2"},
}
DEFAULT_COLORS = STYLE_COLORS["podcast"]

_FONT_CANDIDATES = [
    "Noto Serif CJK SC", "Noto Sans CJK SC", "AR PL UMing", "WenQuanYi",
]


def _find_cjk_font() -> str | None:
    """fc-list 找一个支持中文的字体文件路径。"""
    if not shutil.which("fc-list"):
        return None
    try:
        out = subprocess.run(["fc-list", ":lang=zh", "file"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [l.strip().rstrip(":") for l in out.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    # 优先 Noto CJK(粗体优先,封面标题用粗体好看)
    lines.sort(key=lambda p: (0 if "Bold" in p else 1, 0 if "Noto" in p else 1))
    return lines[0] or None


def _wrap_title(title: str, per_line: int = 9, max_lines: int = 3) -> str:
    """CJK 标题按字数硬折行,超长截断加省略号。"""
    title = title.strip()
    lines = [title[i : i + per_line] for i in range(0, len(title), per_line)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: per_line - 1] + "…"
    return "\n".join(lines)


def generate_cover(out_png: Path, *, title: str, subtitle: str = "",
                   style_id: str = "podcast") -> Path | None:
    """生成封面;字体不可用时返回 None(调用方跳过即可)。"""
    font = _find_cjk_font()
    if not font:
        return None
    colors = STYLE_COLORS.get(style_id, DEFAULT_COLORS)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\\\'").replace("%", "\\%")

    title_text = esc(_wrap_title(title))
    sub_text = esc(subtitle[:20])

    vf = (
        f"drawtext=fontfile='{font}':text='{title_text}'"
        f":fontcolor=white:fontsize=120:line_spacing=40"
        f":x=(w-text_w)/2:y=(h-text_h)/2-80"
        f",drawtext=fontfile='{font}':text='{sub_text}'"
        f":fontcolor={colors['accent']}:fontsize=52"
        f":x=(w-text_w)/2:y=(h-text_h)/2+180"
    )
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={colors['bg']}:s=1400x1400:d=1",
         "-frames:v", "1", "-vf", vf, str(out_png)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out_png.exists():
        return None
    return out_png


def to_jpg(src_png: Path, dst_jpg: Path) -> Path:
    dst_jpg.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src_png), "-q:v", "4", str(dst_jpg)],
        check=True, capture_output=True,
    )
    return dst_jpg
