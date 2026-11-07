"""audio:真实 ffmpeg 的转码/拼接/归一化/ID3。"""

import subprocess
from pathlib import Path

from conftest import make_tone_wav

from podcast_studio.engine import audio


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def test_ffmpeg_available():
    assert audio.ffmpeg_available()


def test_to_mp3_converts_wav(tmp_path):
    src = make_tone_wav(tmp_path / "raw.wav", 0.5)
    dst = audio.to_mp3(src, tmp_path / "chunk.mp3")
    assert dst.exists() and dst.stat().st_size > 1000
    assert abs(_duration(dst) - 0.5) < 0.2


def test_concat_preserves_total_duration(tmp_path):
    chunks = []
    for i in range(3):
        raw = make_tone_wav(tmp_path / f"raw{i}.wav", 0.5)
        chunks.append(audio.to_mp3(raw, tmp_path / f"chunk{i:03d}.mp3"))
    merged = audio.concat_mp3(chunks, tmp_path / "merged.mp3")
    assert abs(_duration(merged) - 1.5) < 0.3


def test_finalize_normalizes_and_tags(tmp_path):
    raw = make_tone_wav(tmp_path / "raw.wav", 0.5)
    merged = audio.concat_mp3([audio.to_mp3(raw, tmp_path / "c.mp3")], tmp_path / "m.mp3")
    final = audio.finalize(merged, tmp_path / "final.mp3", title="测试节目", artist="测试员")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist",
         "-of", "default=noprint_wrappers=1", str(final)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "测试节目" in out and "测试员" in out
    assert abs(_duration(final) - 0.5) < 0.3


def test_ensure_wav_24k_mono(tmp_path):
    src = make_tone_wav(tmp_path / "in.wav", 0.3)
    dst = audio.ensure_wav_24k_mono(src, tmp_path / "voice.wav")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,codec_name",
         "-of", "default=noprint_wrappers=1", str(dst)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "sample_rate=24000" in out and "channels=1" in out and "codec_name=pcm_s16le" in out


def test_concat_empty_raises(tmp_path):
    import pytest

    with pytest.raises(RuntimeError):
        audio.concat_mp3([], tmp_path / "x.mp3")
