"""上传文件护栏：大小上限、音频魔数、时长校验。

浏览器录音 / 克隆采样此前直接 `await file.read()` 全量进内存，既没有大小上限，
也不校验内容是不是音频。本模块统一把上传流式落到临时文件，超限立即中止，
并在入口处把“不是音频 / 太短 / 太长”变成人话错误。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB：足够 60 分钟 128k mp3，也挡住误传大文件
_CHUNK = 1024 * 1024

# 克隆采样建议 30-60 秒；放宽到 5-120 秒，兼容已有素材
CLONE_MIN_SECONDS = 5.0
CLONE_MAX_SECONDS = 120.0
# 一句话识别官方限制 ≤60 秒
ASR_MAX_SECONDS = 60.0
ASR_MIN_SECONDS = 0.1


def _is_audio_header(head: bytes) -> bool:
    """按魔数识别常见音频容器：wav/mp3/ogg/webm/m4a/flac/aac。"""
    if len(head) < 12:
        return False
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return True
    if head.startswith((b"ID3", b"OggS", b"fLaC", b"\x1aE\xdf\xa3")):
        return True
    if head[4:8] == b"ftyp":  # mp4/m4a
        return True
    # mp3 / aac 帧同步字
    return head[:2] in {
        b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1", b"\xff\xf9",
    }


async def save_upload(
    file: UploadFile,
    suffix: str,
    *,
    limit: int | None = None,
) -> tuple[Path, int]:
    """流式保存上传文件到临时路径，返回 (path, size)。

    - 超过 limit（默认 20MB）→ 413，且不把文件留在磁盘
    - 首 16 字节不像音频 → 400
    """
    limit = limit or MAX_UPLOAD_BYTES
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > limit:
        raise HTTPException(413, f"文件太大（{declared // 1024 // 1024}MB），上限 {limit // 1024 // 1024}MB")

    fd, name = tempfile.mkstemp(suffix=suffix or ".bin")
    path = Path(name)
    total = 0
    head = b""
    try:
        # 边写边记首 16 字节魔数，避免写完再同步 open（async 端点里阻塞 IO）
        with os.fdopen(fd, "wb") as tmp:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件太大，上限 {limit // 1024 // 1024}MB")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                tmp.write(chunk)

        if not _is_audio_header(head):
            raise HTTPException(
                400,
                "这个文件不像音频（支持 wav/mp3/m4a/webm/ogg/flac）。"
                "请重新录制或换一个文件。",
            )
        return path, total
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def audio_duration(path: Path) -> float | None:
    """ffprobe 取时长；失败返回 None。"""
    from ..engine.audio import duration_seconds

    return duration_seconds(path)


def check_duration(
    path: Path,
    *,
    minimum: float,
    maximum: float,
    label: str,
) -> float:
    """校验音频时长，超出范围抛 400 人话错误；无法读取也拒绝。"""
    duration = audio_duration(path)
    if duration is None:
        raise HTTPException(400, f"读不出{label}的时长，文件可能已损坏，请换一个音频")
    if duration < minimum:
        raise HTTPException(400, f"{label}太短（{duration:.1f}s），至少 {minimum:.0f}s")
    if duration > maximum:
        raise HTTPException(400, f"{label}太长（{duration:.1f}s），最多 {maximum:.0f}s")
    return duration
