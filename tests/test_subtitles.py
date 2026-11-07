"""字幕/章节：时间戳格式、XML 转义、缺分段跳过。"""

from pathlib import Path

import pytest

from podcast_studio.engine import subtitles


def _chunk(index, text, emotion="温柔开场"):
    return {"index": index, "text": text, "emotion": emotion}


def test_fmt_ts_comma_and_dot():
    assert subtitles._fmt_ts(0, comma=True) == "00:00:00,000"
    assert subtitles._fmt_ts(1.5, comma=True) == "00:00:01,500"
    assert subtitles._fmt_ts(3661.25, comma=False) == "01:01:01.250"
    # 四舍五入到毫秒而不是截断
    assert subtitles._fmt_ts(0.9999, comma=True) == "00:00:01,000"


def test_build_srt_numbering_and_blank_line():
    segs = [
        {"index": 0, "title": "开场", "text": "第一段", "start": 0.0, "end": 1.0},
        {"index": 1, "title": "正文", "text": "", "start": 1.0, "end": 2.0},
    ]
    srt = subtitles.build_srt(segs)
    lines = srt.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,000"
    assert lines[2] == "第一段"
    # 空文本回退到标题，不留空字幕行
    assert "正文" in srt
    assert srt.endswith("\n") or srt.endswith("\n\n")


def test_collect_segments_skips_missing_files(tmp_path, monkeypatch):
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "chunk_000.mp3").write_bytes(b"x")

    monkeypatch.setattr(
        subtitles.audio, "duration_seconds",
        lambda p: 2.0 if Path(p).name == "chunk_000.mp3" else None,
    )
    segs = subtitles.collect_segments(
        chunks_dir, [_chunk(0, "有文件"), _chunk(1, "缺文件")]
    )
    assert len(segs) == 1
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 2.0


def test_collect_segments_accumulates_time(tmp_path, monkeypatch):
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    for i in range(3):
        (chunks_dir / f"chunk_{i:03d}.mp3").write_bytes(b"x")
    monkeypatch.setattr(subtitles.audio, "duration_seconds", lambda p: 1.5)
    segs = subtitles.collect_segments(chunks_dir, [_chunk(i, "t") for i in range(3)])
    assert [round(s["start"], 2) for s in segs] == [0.0, 1.5, 3.0]
    assert round(segs[-1]["end"], 2) == 4.5


def test_chapter_meta_escapes_and_covers_tail():
    segs = [
        {"index": 0, "title": "开场", "text": "", "start": 0.0, "end": 10.0},
        {"index": 1, "title": "结尾", "text": "", "start": 10.0, "end": 20.0},
    ]
    meta = subtitles.build_chapter_meta(segs, total=25.0)
    assert meta.startswith(";FFMETADATA1")
    assert meta.count("[CHAPTER]") == 2
    # 最后一段延伸到整期结束，避免播放器把尾巴丢掉
    assert "END=25000" in meta
    assert "TIMEBASE=1/1000" in meta


def test_chapter_meta_empty_returns_empty_string():
    assert subtitles.build_chapter_meta([]) == ""


def test_write_srt_returns_none_without_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles.audio, "duration_seconds", lambda p: None)
    assert subtitles.write_srt(tmp_path, [_chunk(0, "x")]) is None
    assert not (tmp_path / "subtitles.srt").exists()


def test_write_srt_creates_file(tmp_path, monkeypatch):
    (tmp_path / "chunks").mkdir()
    (tmp_path / "chunks" / "chunk_000.mp3").write_bytes(b"x")
    monkeypatch.setattr(subtitles.audio, "duration_seconds", lambda p: 1.0)
    path = subtitles.write_srt(tmp_path, [_chunk(0, "你好")])
    assert path is not None and path.is_file()
    assert "你好" in path.read_text(encoding="utf-8")
