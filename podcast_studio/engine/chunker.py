"""文稿解析与分段。

把带情绪标签的文稿切成 TTS 分段：
- 标签行（[情绪:xxx] / 【情绪：xxx】/ [Emotion: xxx]）可单独成行或紧跟段落
- 每个小节内部按句子边界再切到 max_chars 以内（TTS 质量最佳区间）
- 每段带索引、原始标签、映射后的 TTS 风格指令
"""

import re
from dataclasses import dataclass, field

from ..styles import map_emotion

TAG_RE = re.compile(
    r"^\s*[\[【]\s*(?:情绪|Emotion|emotion)\s*[:：]\s*([^\]】]+?)[\]】]\s*(.*)$"
)

MAX_CHARS = 350


@dataclass
class Chunk:
    index: int
    emotion_raw: str
    style: str
    text: str
    status: str = "pending"  # pending | done | failed
    file: str | None = None
    error: str | None = None


@dataclass
class Section:
    emotion_raw: str
    texts: list[str] = field(default_factory=list)


def _split_sentences(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """按句末标点切分，超过 max_chars 的长句再按逗号硬切。"""
    text = text.strip()
    if not text:
        return []
    pieces: list[str] = []
    buf = ""
    for part in re.split(r"(?<=[。！？!?…；;\n])", text):
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) <= max_chars:
            buf += part
            continue
        if buf:
            pieces.append(buf)
        buf = ""
        while len(part) > max_chars:
            # 长句按逗号、顿号找最近的切点
            cut = -1
            for sep in ("，", ",", "、"):
                pos = part.rfind(sep, 0, max_chars)
                cut = max(cut, pos)
            if cut <= 0:
                cut = max_chars
            pieces.append(part[: cut + 1])
            part = part[cut + 1 :]
        buf = part
    if buf:
        pieces.append(buf)
    return pieces


def parse_script(text: str, emotion_map: dict | None = None) -> list[Section]:
    """解析带情绪标签的文稿为小节列表。emotion_map 用于把原始标签归一化。"""
    sections: list[Section] = []
    current: Section | None = None
    plain: list[str] = []  # 无标签的散落文本

    def flush_plain():
        if plain:
            sections.append(Section(emotion_raw="", texts=list(plain)))
            plain.clear()

    for line in text.splitlines():
        m = TAG_RE.match(line)
        if m:
            flush_plain()
            current = Section(emotion_raw=m.group(1).strip())
            sections.append(current)
            rest = m.group(2).strip()
            if rest:
                current.texts.append(rest)
        else:
            if line.strip():
                if current is None:
                    plain.append(line.strip())
                else:
                    current.texts.append(line.strip())
    flush_plain()

    if emotion_map:
        for s in sections:
            if s.emotion_raw not in emotion_map:
                for sep in ("，", ",", "、", " "):
                    head = s.emotion_raw.split(sep)[0].strip()
                    if head in emotion_map:
                        s.emotion_raw = head
                        break
    return sections


def chunk_script(text: str, profile: dict) -> list[Chunk]:
    """文稿 → TTS 分段列表(每段已映射 TTS 风格指令,自由变体标签自动归一)。"""
    sections = parse_script(text)
    chunks: list[Chunk] = []
    idx = 0
    for sec in sections:
        style = map_emotion(sec.emotion_raw, profile)
        for piece in _split_sentences("".join(sec.texts)):
            chunks.append(Chunk(index=idx, emotion_raw=sec.emotion_raw, style=style, text=piece))
            idx += 1
    return chunks


def strip_tags(text: str) -> str:
    """去掉情绪标签，得到纯文稿。"""
    out = []
    for line in text.splitlines():
        m = TAG_RE.match(line)
        out.append(m.group(2).strip() if m else line)
    return "\n".join(out).strip()
