"""chunker:标签解析、自由变体映射、硬切、纯文本提取。"""

from podcast_studio.engine.chunker import chunk_script, parse_script, strip_tags
from podcast_studio.styles import PROFILES

BEDTIME = PROFILES["bedtime"]
PODCAST = PROFILES["podcast"]


def test_tag_on_own_line_and_inline():
    script = "[情绪:温柔开场]\n第一段内容。\n\n[情绪:安心收尾]结尾这一段标签跟文字同行。"
    sections = parse_script(script)
    assert [s.emotion_raw for s in sections] == ["温柔开场", "安心收尾"]
    assert sections[0].texts == ["第一段内容。"]
    assert sections[1].texts == ["结尾这一段标签跟文字同行。"]


def test_fullwidth_brackets_and_english_tag():
    script = "【情绪：娓娓道来】中文括号。\n[Emotion: Playful]英文标签。"
    sections = parse_script(script)
    assert [s.emotion_raw for s in sections] == ["娓娓道来", "Playful"]


def test_chunks_map_style_from_profile():
    script = "[情绪:温柔开场]\n你好呀。\n\n[情绪:小小惊奇]\n你看!"
    chunks = chunk_script(script, BEDTIME)
    assert [c.emotion_raw for c in chunks] == ["温柔开场", "小小惊奇"]
    assert chunks[0].style == BEDTIME["emotions"]["温柔开场"]
    assert chunks[1].style == BEDTIME["emotions"]["小小惊奇"]


def test_unmatched_tag_falls_back_to_default():
    script = "[情绪:展开分析，带点恍然大悟的感觉]\n自由变体标签。"
    chunks = chunk_script(script, PODCAST)
    assert chunks[0].style == PODCAST["emotions"]["展开分析"]  # 前缀匹配

    script2 = "[情绪:完全不认识的情绪]\n没人认识我。"
    chunks2 = chunk_script(script2, BEDTIME)
    assert chunks2[0].style == BEDTIME["emotions"]["default"]


def test_no_tags_single_default_section():
    chunks = chunk_script("没有标签的一段话。第二句。", PODCAST)
    assert len(chunks) == 1
    assert chunks[0].emotion_raw == ""
    assert chunks[0].style == PODCAST["emotions"]["default"]


def test_long_paragraph_split_under_limit():
    body = "这是一个很长的句子，" * 80
    chunks = chunk_script(f"[情绪:娓娓道来]\n{body}", BEDTIME)
    assert len(chunks) > 1
    assert all(len(c.text) <= 350 for c in chunks)
    assert "".join(c.text for c in chunks) == body  # 内容无丢失


def test_untagged_lines_attach_to_current_section():
    """标签后的无标签段落归入当前小节(模型偶尔漏标签的兜底)。"""
    script = "[情绪:温柔开场]\n有标签的。\n\n这句没标签,也算数。"
    sections = parse_script(script)
    assert len(sections) == 1
    assert sections[0].emotion_raw == "温柔开场"
    assert "有标签的。" in sections[0].texts and "这句没标签,也算数。" in sections[0].texts


def test_strip_tags_removes_all_markers():
    script = "[情绪:温柔开场]\n第一段。\n【情绪:安心收尾】\n最后一段。"
    clean = strip_tags(script)
    assert "情绪" not in clean
    assert "第一段。" in clean and "最后一段。" in clean
