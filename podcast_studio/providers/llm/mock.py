"""演示模式 LLM：无需 API key，返回内置示例内容，让全流程可离线走通。

文稿内容是真实的情绪标签格式，合成走真实 TTS（如 edge-tts），
所以演示模式下产出的音频是真正可播放的。
"""

import json

from .base import LLMProvider

_BEDTIME_SCRIPT = """[情绪:温柔开场]
宝贝，躺好了吗？今天呀，我们要认识一位特别忙碌的旅行家。它小小的、亮亮的，还会从天上跳下来呢。你猜猜它是谁呀？

[情绪:小小惊奇]
对啦，就是小雨滴！你知道吗，小雨滴原本住在江河湖海里。太阳公公一晒，它就变得轻飘飘的，像魔法一样，慢慢飞到了天上。

[情绪:轻轻解释]
飞到天上的小雨滴们挤在一起，抱成了软软的云朵。抱得越多，云朵就越重越重。等到云朵抱不动了呀，小雨滴们就手拉着手，一滴滴落下来。落在地上的时候，我们就说——下雨啦。

[情绪:娓娓道来]
那打雷的声音呢？其实呀，那是云朵里的大家庭在玩碰碰车。不同的云块撞在一起，就发出了轰隆隆的响声。先看到闪电，再听到雷声，是因为光跑得比声音快得多。

[情绪:轻轻解释]
小雨滴落进泥土里，小花小草咕嘟咕嘟喝饱了水；落进小河里，鱼儿们就有了新家。所以呀，下雨不是天空在哭鼻子，是它给大地送水喝呢。

[情绪:安心收尾]
好啦，小雨滴的旅行讲完了。它们明天还要去新的地方旅行呢。你也像小雨滴一样，闭上眼睛，做一个软软的、湿漉漉的梦吧。晚安，宝贝。"""

_PODCAST_SCRIPT = """[情绪:好奇开场]
你知道吗，我最近发现一个特别有意思的事：每天早上叫醒你的闹钟声，为什么那么难听？而且这居然是故意的。

[情绪:展开分析]
我去查了一下，原来人脑对声音的反应分两条路。一条走理智，一条直接通到情绪。闹钟那种急促的、单调的哔哔声，走的是后一条路，它会在你睡得最沉的时候，硬把你拽出来。所以那种起床的烦躁，不是你脾气差，是声音的锅。

[情绪:若有所思]
更有意思的是，研究者发现，旋律性强一点的铃声，让人醒得更清醒。因为大脑得费点劲去"听懂"它，这个费劲的过程，恰恰把你的脑子转起来了。

[情绪:延伸思考]
说白了，我们一直用最糙的工具，干最精细的活——唤醒。也许该换一个思路：与其用惊吓把自己弹起来，不如用一段熟悉的旋律，把自己轻轻捞起来。

[情绪:轻松收尾]
明天早上，试试换一首你喜欢的慢歌当闹钟。说不定，起床气真的会小一点。你猜怎么着，我已经试了一周了，效果还行。"""


class MockLLM(LLMProvider):
    name = "mock"

    def chat(
        self,
        task: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.9,
        max_tokens: int = 4096,
        timeout: float = 300,
    ) -> str:
        if task == "research":
            bedtime = any(
                m in (system + user) for m in ("小朋友", "睡前", "孩子", "3-8")
            )
            if bedtime:
                return json.dumps(
                    [
                        {
                            "title": "小雨滴的旅行：为什么会下雨？",
                            "angle": "水循环的拟人化故事",
                            "heat": "高",
                            "reason": "经典问题，画面感强，最容易讲成温柔的故事",
                        },
                        {
                            "title": "云朵大家庭的碰碰车：为什么会打雷？",
                            "angle": "雷电的声音原理",
                            "heat": "中高",
                            "reason": "孩子对雷声又怕又好奇，讲清了就不怕了",
                        },
                        {
                            "title": "月亮为什么有时候圆圆有时候弯弯？",
                            "angle": "月相变化",
                            "heat": "中",
                            "reason": "睡前抬头就能看到，很应景",
                        },
                    ],
                    ensure_ascii=False,
                )
            return json.dumps(
                [
                    {
                        "title": "闹钟声为什么那么难听？其实是故意的",
                        "angle": "声音如何唤醒大脑",
                        "heat": "高",
                        "reason": "人人有感，反常识，容易展开",
                    },
                    {
                        "title": "为什么越想睡越睡不着？",
                        "angle": "睡眠压力与焦虑循环",
                        "heat": "高",
                        "reason": "共鸣强，可给实用建议",
                    },
                    {
                        "title": "午睡到底该睡多久？",
                        "angle": "睡眠周期科普",
                        "heat": "中",
                        "reason": "实用，有具体可执行结论",
                    },
                ],
                ensure_ascii=False,
            )
        if task == "arc":
            bedtime = any(m in (system + user) for m in ("小朋友", "睡前", "孩子"))
            if bedtime:
                return json.dumps([
                    {"beat": "温柔开场", "emotion": "温柔开场", "share": 0.15,
                     "intent": "建立安全感,把听众拉进故事"},
                    {"beat": "引入主角", "emotion": "娓娓道来", "share": 0.25,
                     "intent": "介绍小雨滴,埋下好奇"},
                    {"beat": "展开冒险", "emotion": "小小惊奇", "share": 0.3,
                     "intent": "旅程的惊奇点,轻轻抬情绪"},
                    {"beat": "解释知识点", "emotion": "轻轻解释", "share": 0.2,
                     "intent": "把科学讲清楚"},
                    {"beat": "安心收尾", "emotion": "安心收尾", "share": 0.1,
                     "intent": "情绪落地,引导入睡"},
                ], ensure_ascii=False)
            return json.dumps([
                {"beat": "钩子", "emotion": "好奇开场", "share": 0.2, "intent": "抛出反常识"},
                {"beat": "展开", "emotion": "展开分析", "share": 0.35, "intent": "拆解原理"},
                {"beat": "转折", "emotion": "若有所思", "share": 0.25, "intent": "换个角度"},
                {"beat": "收尾", "emotion": "轻松收尾", "share": 0.2, "intent": "留余韵"},
            ], ensure_ascii=False)
        if task == "title":
            return "小雨滴的旅行"
        if task == "dialogize":
            return json.dumps([
                {"speaker": "A", "text": "宝贝，躺好了吗？今天我们要认识一位旅行家——小雨滴。"},
                {"speaker": "B", "text": "小雨滴？它住在哪里呀？"},
                {"speaker": "A", "text": "它住在大海里。太阳一晒，它就轻飘飘地飞到了天上。"},
                {"speaker": "B", "text": "然后呢然后呢？"},
                {"speaker": "A", "text": "云朵抱不动它了，它就落下来——这就是雨。晚安，宝贝。"},
            ], ensure_ascii=False)
        if task == "shownotes":
            return """小雨滴去哪儿了?一个温柔的科学小故事,讲给孩子听。

**本期要点**
- 小雨滴住在江河湖海里,太阳一晒就飞上天
- 水蒸气抱在一起变成云,抱不动了就变成雨落下来
- 打雷是云朵在"碰碰车",闪电比雷声先到是因为光跑得快
- 雨水是大自然给大地送水喝

适合 3-8 岁小朋友,睡前播放刚刚好。晚安,宝贝。"""
        if task == "script":
            bedtime = any(m in (system + user) for m in ("小朋友", "睡前故事", "孩子"))
            return _BEDTIME_SCRIPT if bedtime else _PODCAST_SCRIPT
        if task == "compliance":
            return json.dumps(
                {
                    "pass": True,
                    "items": [],
                    "summary": "演示模式：示例文稿通过全部审查维度。",
                },
                ensure_ascii=False,
            )
        return "（演示模式）"
