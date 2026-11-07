"""节目类型 profiles：播客 / 睡前故事。

每个 profile 定义写稿提示词、情绪标签→TTS 风格指令映射、合规维度、
以及不支持情绪的 TTS（如 edge-tts）的降级参数（语速/音调）。
新增节目类型 = 在 PROFILES 里加一个条目。
"""

PROFILES: dict[str, dict] = {
    "podcast": {
        "id": "podcast",
        "emoji": "🎙️",
        "name": "播客 · 成人",
        "desc": "像跟朋友聊天，分享一个发现。适合热点解读、知识分享。",
        "default_minutes": 8,
        "words_per_minute": 220,
        "script_system": """You are a podcast scriptwriter who writes as if telling a friend about something interesting you just discovered.

Tone: Like chatting at a café with a good friend — sharing an interesting finding, not giving a lecture.

Rules:
1. Short sentences, occasional long ones for rhythm variation
2. Natural conversational language (你知道吗, 说白了, 其实, 关键是)
3. Emotion should be restrained but genuine — curious, slightly surprised, thoughtful, NOT excited or loud
4. Tag each paragraph with emotion, tags MUST be chosen from: {emotion_tags}
5. 每段以标签开头，格式如 [情绪:好奇开场]，标签单独成段或紧跟段落均可
6. Use …… for natural pauses, ！ for mild emphasis, ？ for curiosity
7. No markdown, no sound effect cues in brackets
8. No prompt words or technical markers""",
        "script_user": """Write a podcast script for:

{content_block}

Requirements:
- Like sharing a discovery with a good friend — natural, restrained, genuine
- Not too excited, not too flat
- Duration: ~{minutes} minutes (~{words} 字)
- Each paragraph starts with emotion tag, e.g., [情绪:好奇开场]
- Natural opening, no pleasantries
- Thoughtful ending with lingering resonance

Output the script directly, no preamble or postscript.""",
        "emotions": {
            "好奇开场": "Excited but restrained, like sharing an interesting discovery",
            "展开分析": "Calm, organized, with a sense of realization",
            "若有所思": "Steady, contemplative, pondering deeper meaning",
            "略带调侃": "Light-hearted teasing, but fundamentally serious",
            "认真探讨": "Earnest but not heavy",
            "延伸思考": "Measured, insightful, forward-looking",
            "回到现实": "Pragmatic, slightly wistful",
            "轻松收尾": "Relaxed, with a sense of anticipation",
            "default": "Natural, calm conversational tone",
        },
        "research_system": "你是一名播客选题策划，熟悉中文互联网的热门话题。",
        "research_user": """围绕主题范围「{input}」，给出 {n} 个适合做成单期播客的选题。

要求：角度具体、有讨论空间、贴近普通人生活。

只输出 JSON 数组，不要其他内容，格式：
[{{"title": "选题标题(具体、口语化)", "angle": "切入角度", "heat": "高/中高/中/低", "reason": "一句话理由"}}]""",
        "compliance_dimensions": [
            ["敏感词", "政治敏感、歧视、暴力等内容"],
            ["虚假信息", "数据造假、夸大、误导性表述"],
            ["格式问题", "提示词残留、技术标记、markdown 符号"],
            ["法律风险", "隐私、诽谤、版权"],
            ["广告嫌疑", "未声明的商业推广"],
        ],
        "moods": [
            {"id": "curious", "name": "冷静求知", "desc": "像懂你的朋友讲清楚一件事",
             "anchor": "冷静克制:好奇但不夸张,分析有层次,结论清晰不煽情"},
            {"id": "chatty", "name": "轻松聊友", "desc": "像跟朋友聊天,略带调侃",
             "anchor": "轻松口语化:允许调侃和小幽默,但内核认真,收尾留余韵"},
            {"id": "warm", "name": "热心分享", "desc": "真诚,有温度,实用为主",
             "anchor": "真诚有温度:多用『你』的视角,实用建议给得具体,情绪真诚不表演"},
        ],
        "edge_fallback": {"rate": "+0%", "pitch": "+0Hz"},
        # 不支持情绪的 provider(如 edge-tts)按情绪做语速/音调微调,制造节奏差
        "edge_by_emotion": {
            "好奇开场": {"rate": "+4%", "pitch": "+1Hz"},
            "展开分析": {"rate": "+0%", "pitch": "+0Hz"},
            "若有所思": {"rate": "-6%", "pitch": "-1Hz"},
            "略带调侃": {"rate": "+2%", "pitch": "+0Hz"},
            "认真探讨": {"rate": "-2%", "pitch": "+0Hz"},
            "延伸思考": {"rate": "-6%", "pitch": "-1Hz"},
            "回到现实": {"rate": "-4%", "pitch": "+0Hz"},
            "轻松收尾": {"rate": "+0%", "pitch": "+1Hz"},
        },
        "tts_default_style": "用自然、亲切、有节奏感的播客风格朗读。",
    },
    "bedtime": {
        "id": "bedtime",
        "emoji": "🌙",
        "name": "睡前故事 · 儿童科普",
        "desc": "讲给小朋友听的科学小故事，温柔不刺激，适合 3-8 岁。",
        "default_minutes": 5,
        "words_per_minute": 180,
        "script_system": """你是一位擅长给孩子讲睡前故事的讲述者，正在为 3-8 岁的小朋友创作一个科学小故事。

规则：
1. 把科学知识变成温柔的小故事，可以用拟人化（小水滴、云朵妈妈），但科学事实必须正确
2. 语言简单、句子短，多用孩子听得懂的比喻
3. 中间可以轻轻提问互动（你猜怎么着？），但不要吓人、不要夸张刺激
4. 语气温和、安静，像睡前在床边讲故事
5. 每段以情绪标签开头，标签必须从以下选择：{emotion_tags}
6. 标签格式如 [情绪:温柔开场]，标签单独成段或紧跟段落均可
7. 结尾平静、有困意，引导孩子安心入睡
8. 不用 markdown，不加音效提示，不出现提示词痕迹""",
        "script_user": """给小朋友写一个睡前故事。

{content_block}

时长：约 {minutes} 分钟（约 {words} 字）
每段开头标情绪标签，例如：[情绪:温柔开场]
直接输出故事，不要前言后语。""",
        "emotions": {
            "温柔开场": "温柔、缓慢、亲切，像妈妈在床边轻声开口",
            "娓娓道来": "平稳柔和，慢慢讲述，有画面感",
            "小小惊奇": "轻轻的、有点惊喜但不刺激，音量略抬又落下",
            "轻轻解释": "耐心、清晰，像回答孩子的问题一样温柔",
            "安心收尾": "非常轻、非常慢，越来越安静，带困意",
            "default": "Gentle, warm, slow storytelling voice",
        },
        "research_system": "你是一名儿童科普内容策划，擅长把科学知识变成孩子爱听的故事选题。",
        "research_user": """孩子想听关于「{input}」的故事。给出 {n} 个适合讲给 3-8 岁小朋友听的科普故事选题。

要求：具体的科学问题（比如"为什么会下雨"），答案能用拟人化故事讲清楚，内容温柔不吓人。

只输出 JSON 数组，不要其他内容，格式：
[{{"title": "故事选题(如:小雨滴的旅行)", "angle": "科学知识点", "heat": "高/中高/中/低", "reason": "一句话理由"}}]""",
        "compliance_dimensions": [
            ["儿童适宜性", "无恐怖/暴力/悲伤过度/成人话题，无商业推广，情绪引导健康"],
            ["科学准确性", "科学事实正确，拟人化不失真，不会造成误解"],
            ["敏感词", "政治敏感、歧视、暴力等内容"],
            ["格式问题", "提示词残留、技术标记、markdown 符号"],
            ["法律风险", "隐私、诽谤、版权"],
        ],
        "moods": [
            {"id": "calm", "name": "温柔安抚", "desc": "全程低刺激,适合入睡",
             "anchor": "整期温柔低刺激:语速偏慢,情绪起伏不超过轻轻抬起又放下的量级,结尾必须归于安宁"},
            {"id": "gentle_explore", "name": "温柔探险", "desc": "有小惊奇但不吓人",
             "anchor": "整体温柔,中段允许一两次轻轻的惊奇(音量略抬即落),结尾回归安宁困意"},
            {"id": "classic", "name": "经典童话", "desc": "传统童话讲述感",
             "anchor": "经典童话讲述感:叙事性更强,可有一点点悬念,但收尾必须温暖安心"},
        ],
        "edge_fallback": {"rate": "-10%", "pitch": "-2Hz"},
        "edge_by_emotion": {
            "温柔开场": {"rate": "-12%", "pitch": "-2Hz"},
            "娓娓道来": {"rate": "-10%", "pitch": "-1Hz"},
            "小小惊奇": {"rate": "+2%", "pitch": "+0Hz"},
            "轻轻解释": {"rate": "-10%", "pitch": "-1Hz"},
            "安心收尾": {"rate": "-20%", "pitch": "-3Hz"},
        },
        "tts_default_style": "用温柔、缓慢、亲切的讲故事声音朗读，像睡前在床边讲故事。",
    },
}


def get_profile(style_id: str) -> dict:
    return PROFILES.get(style_id, PROFILES["podcast"])


def map_emotion(raw: str, profile: dict) -> str:
    """把文稿里的情绪标签（允许自由变体）映射到 TTS 风格指令。"""
    emotions = profile["emotions"]
    raw = (raw or "").strip()
    if raw in emotions:
        return emotions[raw]
    # 去掉自由发挥的后缀，如 "展开分析，带点恍然大悟" → "展开分析"
    for sep in ("，", ",", "、", " ", "·", "-"):
        if sep in raw:
            head = raw.split(sep)[0].strip()
            if head in emotions:
                return emotions[head]
    return emotions.get("default", "Natural conversational tone")


def edge_params(profile: dict, emotion_raw: str) -> dict:
    """不支持情绪的 provider 用的降级参数:按情绪微调,无映射时用整体兜底。"""
    by_emotion = profile.get("edge_by_emotion") or {}
    return by_emotion.get((emotion_raw or "").strip()) or profile.get(
        "edge_fallback", {"rate": "+0%", "pitch": "+0Hz"}
    )
