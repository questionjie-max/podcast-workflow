"""字幕与章节:从分段 chunks 派生 SRT 与 ffmpeg 章节元数据。

分段时长用 ffprobe 对 chunks/*.mp3 实测,起点为前序分段时长累加。
这是竞品普遍具备、缺了显得业余的「成品交付物」之一(D 圆桌商业基准 #5)。
"""

from pathlib import Path

from . import audio


def _fmt_ts(seconds: float, *, comma: bool) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _get(c, key: str, default=None):
    """Chunk 对象与 dict 兼容取值(pipeline 传对象,API 层传 dict)。"""
    return c.get(key, default) if isinstance(c, dict) else getattr(c, key, default)


def collect_segments(chunks_dir: Path, chunks: list) -> list[dict]:
    """把 chunk 元数据与实测时长合并成 [{index,title,text,start,end}];缺文件跳过。"""
    segments: list[dict] = []
    cursor = 0.0
    for c in chunks:
        idx = int(_get(c, "index", len(segments)))
        mp3 = chunks_dir / f"chunk_{idx:03d}.mp3"
        dur = audio.duration_seconds(mp3)
        if dur is None:
            continue
        title = _get(c, "emotion") or f"第 {idx + 1} 段"
        segments.append(
            {"index": idx, "title": title, "text": _get(c, "text") or "",
             "start": cursor, "end": cursor + dur}
        )
        cursor += dur
    return segments


def build_srt(segments: list[dict]) -> str:
    out: list[str] = []
    for i, seg in enumerate(segments, start=1):
        out.append(str(i))
        out.append(f"{_fmt_ts(seg['start'], comma=True)} --> {_fmt_ts(seg['end'], comma=True)}")
        out.append(seg["text"].strip() or seg["title"])
        out.append("")
    return "\n".join(out)


def build_chapter_meta(segments: list[dict], *, total: float | None = None) -> str:
    """生成 ffmpeg ffmetadata 章节文件内容(写入 MP3 后播放器可章节跳转)。"""
    if not segments:
        return ""
    end_all = total if total else segments[-1]["end"] + 0.5
    lines = [";FFMETADATA1", "title=章节", ""]
    for i, seg in enumerate(segments):
        end = seg["end"] if i < len(segments) - 1 else end_all
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(seg['start'] * 1000)}",
            f"END={int(end * 1000)}",
            f"title={seg['title']}",
            "",
        ]
    return "\n".join(lines)


def write_srt(ep_dir: Path, chunks: list[dict]) -> Path | None:
    """生成并落盘 subtitles.srt;无可用分段返回 None。"""
    segments = collect_segments(ep_dir / "chunks", chunks)
    if not segments:
        return None
    srt = ep_dir / "subtitles.srt"
    srt.write_text(build_srt(segments), encoding="utf-8")
    return srt
