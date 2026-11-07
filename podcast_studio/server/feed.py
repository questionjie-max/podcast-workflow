"""RSS feed 生成(RSS 2.0 + iTunes 标签)。

把「已完成」的节目输出为标准播客 RSS,可直接提交到 Apple Podcasts / Spotify 等
(需要服务可被公网访问,例如用内网穿透)。音频 URL 用请求的 base_url 拼接,
本机访问时就是 http://localhost:8686/...
"""

from xml.sax.saxutils import escape

from fastapi import Request, Response

from ..config import Config
from ..engine.pipeline import list_episodes

CHANNEL_DESC = "用播客工坊本地生成的播客与睡前故事。"


def _itunes_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _audio_duration(path, fallback: int = 0) -> float:
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return float(fallback * 60)


def build_feed(cfg: Config, base_url: str) -> str:
    # 示例期(demo)不进公开订阅面:订阅者收到样例=不专业
    episodes = [e for e in list_episodes(cfg)
                if e["state"] == "done" and e["has_audio"] and e.get("origin") != "demo"]
    items = []
    for ep in episodes:
        ep_dir = cfg.episodes_dir / ep["id"]
        audio_path = ep_dir / "audio.mp3"
        url = f"{base_url.rstrip('/')}/api/episodes/{ep['id']}/audio"
        cover_url = f"{base_url.rstrip('/')}/api/episodes/{ep['id']}/cover"
        has_cover = (ep_dir / "cover.jpg").exists() or (ep_dir / "cover.png").exists()
        image_line = f"\n      <itunes:image href=\"{escape(cover_url)}\"/>" if has_cover else ""
        duration = _audio_duration(audio_path, ep.get("minutes") or 0)
        pub = (ep.get("created") or "").replace(" ", "T") + ":00"
        notes = ""
        notes_file = ep_dir / "shownotes.md"
        if notes_file.exists():
            notes = notes_file.read_text(encoding="utf-8")[:2000]
        emoji = "🌙" if ep.get("style") == "bedtime" else "🎙️"
        items.append(f"""    <item>
      <title>{escape(emoji + " " + str(ep['title']))}</title>
      <guid isPermaLink="false">{escape('podcast-studio-' + ep['id'])}</guid>
      <pubDate>{escape(_rfc2822(pub))}</pubDate>
      <description>{escape(notes or ep['title'])}</description>
      <enclosure url="{escape(url)}" length="{audio_path.stat().st_size}" type="audio/mpeg"/>
      <itunes:duration>{_itunes_duration(duration)}</itunes:duration>{image_line}
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>我的播客工坊</title>
    <link>{escape(base_url)}</link>
    <description>{escape(CHANNEL_DESC)}</description>
    <language>zh-cn</language>
    <itunes:author>播客工坊 Podcast Studio</itunes:author>
    <itunes:summary>{escape(CHANNEL_DESC)}</itunes:summary>
    <itunes:image href="{escape(base_url.rstrip('/') + '/static/icon.png')}"/>
{chr(10).join(items)}
  </channel>
</rss>
"""


def _rfc2822(iso_like: str) -> str:
    """把 '2026-10-30T01:18:40:00' 之类的本地时间转成 RFC2822(尽力而为)。"""
    from datetime import datetime
    from email.utils import format_datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S:00", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(iso_like, fmt)
            # RFC2822 要求带时区;naive datetime.astimezone() 附加系统本地时区
            return format_datetime(dt.astimezone())
        except ValueError:
            continue
    return iso_like


def register_feed_route(app) -> None:
    @app.get("/api/feed.rss", response_class=Response)
    def feed(request: Request):
        cfg = Config.load()
        cfg.ensure_dirs()
        base = str(request.base_url).rstrip("/")
        xml = build_feed(cfg, base)
        return Response(content=xml, media_type="application/rss+xml; charset=utf-8")
