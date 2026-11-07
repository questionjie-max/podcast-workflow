"""音频后处理：统一走 ffmpeg。

- 各分段统一转码为相同参数的 mp3（44.1kHz / mono / 128k），保证可无损拼接
- 拼接用 concat demuxer（-c copy）
- 成品做响度归一化（播客标准 -16 LUFS）并写 ID3 标签
"""

import shutil
import subprocess
from pathlib import Path

MP3_ARGS = ["-ar", "44100", "-ac", "1", "-b:a", "128k"]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{proc.stderr[-400:]}")


def to_mp3(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(src), *MP3_ARGS, str(dst)])
    return dst


def concat_mp3(chunks: list[Path], dst: Path) -> Path:
    if not chunks:
        raise RuntimeError("没有可拼接的音频分段")
    dst.parent.mkdir(parents=True, exist_ok=True)
    list_file = dst.parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in chunks), encoding="utf-8"
    )
    _run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dst)])
    list_file.unlink(missing_ok=True)
    return dst


def finalize(src: Path, dst: Path, *, title: str, artist: str = "播客工坊", album: str = "") -> Path:
    """响度归一化 + ID3 标签。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "-i", str(src),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "44100", "-ac", "1", "-b:a", "128k",
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}",
            "-metadata", f"album={album or '播客工坊 Podcast Studio'}",
            str(dst),
        ]
    )
    return dst


def embed_cover(mp3: Path, cover_jpg: Path) -> Path:
    """把封面图作为 ID3 附图嵌进 MP3(原位,经临时文件)。"""
    tmp = mp3.with_suffix(".tmp.mp3")
    _run(
        [
            "-i", str(mp3), "-i", str(cover_jpg),
            "-map", "0:a", "-map", "1:v",
            "-c", "copy", "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            str(tmp),
        ]
    )
    tmp.replace(mp3)
    return mp3


def duration_seconds(path: Path) -> float | None:
    """ffprobe 取音频时长(秒);失败返回 None。"""
    if not path.exists():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ensure_wav_24k_mono(src: Path, dst: Path) -> Path:
    """声音克隆采样统一转为 24kHz/16bit/mono WAV。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(src), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    return dst


def mix_bgm(voice_mp3: Path, bgm: Path, *, volume: float = 0.12) -> Path:
    """垫乐混音:BGM 循环铺底+首尾包络(2s 淡入/3s 淡出),与人声成品混合(原位)。

    人声保持主音量,BGM 按 volume(建议 0.05-0.2)缩放;混音不改变人声可懂度。
    音频短于 6s 不做包络直接跳过(包络比内容还长没有意义)。
    """
    dur = duration_seconds(voice_mp3) or 0
    if dur <= 6:
        return voice_mp3
    volume = max(0.0, min(0.5, volume))
    fade_st = max(0.0, dur - 3)
    tmp = voice_mp3.with_suffix(".bgm.tmp.mp3")
    _run(
        [
            "-i", str(voice_mp3),
            "-stream_loop", "-1", "-i", str(bgm),
            "-filter_complex",
            (f"[1:a]volume={volume:.3f},afade=t=in:st=0:d=2,afade=t=out:st={fade_st:.2f}:d=3[b];"
             f"[0:a][b]amix=inputs=2:duration=first:dropout_transition=0[a]"),
            "-map", "[a]",
            *MP3_ARGS,
            str(tmp),
        ]
    )
    tmp.replace(voice_mp3)
    return voice_mp3


def write_chapters(mp3: Path, meta: str) -> Path:
    """把 ffmetadata 章节写进 MP3(原位,经临时文件);失败由调用方兜底非致命。"""
    tmp_meta = mp3.with_suffix(".chapters.tmp.txt")
    tmp_out = mp3.with_suffix(".chapters.tmp.mp3")
    tmp_meta.write_text(meta, encoding="utf-8")
    try:
        _run(
            [
                "-i", str(mp3), "-i", str(tmp_meta),
                # 元数据以成品为准(标题/作者),只从 meta 取章节(Q1:此前 map_metadata 1 抹标题)
                "-map_metadata", "0", "-map_chapters", "1", "-codec", "copy",
                "-id3v2_version", "3",
                str(tmp_out),
            ]
        )
        tmp_out.replace(mp3)
    finally:
        tmp_meta.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
    return mp3
