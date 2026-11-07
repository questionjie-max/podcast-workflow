"""流程步骤：选题 / 写稿 / 合规审查 / 事实核查。"""

import json
import re
from collections.abc import Callable

from ..providers.llm.base import LLMProvider
from ..providers.search import MAX_ROUNDS_LIMIT

# 事实核查的系统提示词：立场保守，宁枉勿纵。
FACTCHECK_SYSTEM = (
    "你是严谨的事实核查员。判定立场保守：证据充分才判 reliable，"
    "拿不准就判 uncertain，疑似编造或与已知事实冲突就判 unverifiable。"
)


def _extract_json(text: str):
    """从模型输出里鲁棒地抠出 JSON（容忍代码围栏、前后缀文本）。"""
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 找第一个 [...] 或 {...}
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def research(llm: LLMProvider, profile: dict, topic: str, n: int = 5,
             search_results: list[dict] | None = None) -> list[dict]:
    """选题：返回候选列表。"""
    user_prompt = profile["research_user"].format(input=topic, n=n)
    if search_results:
        refs = "\n".join(
            f"- {r['title']}：{r['snippet'][:120]}" for r in search_results if r.get("title")
        )
        user_prompt += f"\n\n以下是刚刚的网络搜索结果，选题请优先从这里找角度（须为近期热点）：\n{refs}"
    out = llm.chat(
        task="research",
        system=profile["research_system"],
        user=user_prompt,
        temperature=0.8,
    )
    data = _extract_json(out)
    if isinstance(data, dict):
        data = data.get("candidates") or data.get("items") or []
    if not isinstance(data, list):
        raise RuntimeError(f"选题输出无法解析：{out[:200]}")
    candidates = []
    for i, item in enumerate(data[:n]):
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "index": len(candidates),
                "title": str(item.get("title", f"选题 {i+1}")).strip(),
                "angle": str(item.get("angle", "")).strip(),
                "heat": str(item.get("heat", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
            }
        )
    if not candidates:
        raise RuntimeError("没有生成有效选题，请重试或换个说法")
    return candidates


def factcheck_candidates(
    llm: LLMProvider,
    candidates: list[dict],
    search_fn: Callable[[str], list[dict]],
    *,
    max_rounds: int = 2,
) -> list[dict]:
    """对候选选题做事实核查，返回带 "factcheck" 字段的候选列表。

    预算规则：max_rounds 是流水线搜索总轮次（初搜占 1 轮），本函数内
    search_fn 的调用总次数不超过 max_rounds - 1，轮数钳制到 MAX_ROUNDS_LIMIT。
    当前实现每轮核查只做一次「合并查询」——把所有候选标题拼成一个 query
    一次搜完，控制成本；剩余预算留给后续迭代的补查轮。

    判定与处理：LLM 逐条输出 reliable / uncertain / unverifiable；
    uncertain 的候选保留并打标，unverifiable 的直接丢弃（宁可少选题）。
    LLM 输出无法解析、verdict 取值未知或未覆盖某条时，宁枉勿纵，
    一律按 uncertain 处理。

    search_fn(query) -> [{title, snippet, url}]：由调用方注入的同步搜索
    函数（engine 步骤均为同步，异步 provider 由调用方自行桥接），便于
    测试桩注入与调用计数。
    """
    if not candidates:
        return []
    rounds = max(0, min(int(max_rounds), MAX_ROUNDS_LIMIT))
    budget = rounds - 1  # 初搜占 1 轮，核查可用轮次 = 总轮次 - 1
    evidence: list[dict] = []
    if budget >= 1:
        query = " ".join(
            str(c.get("title", "")).strip() for c in candidates if c.get("title")
        )[:200]
        try:
            evidence = search_fn(query) or []
        except Exception:  # 搜索失败不阻塞流程：退化为凭模型知识判定
            evidence = []
        if not isinstance(evidence, list):
            evidence = []

    cand_lines = "\n".join(
        f"{i + 1}. {c.get('title', '')}（角度：{c.get('angle', '') or '无'}）"
        for i, c in enumerate(candidates)
    )
    ev_lines = "\n".join(
        f"- {r.get('title', '')}：{str(r.get('snippet', ''))[:150]}"
        for r in evidence
        if r.get("title") or r.get("snippet")
    ) or "（搜索无可用证据，仅凭你自己的知识判断）"

    user_prompt = f"""请对以下 {len(candidates)} 个播客选题候选做事实核查，判断各自依赖的关键事实是否站得住脚。

候选选题：
{cand_lines}

核查参考（最新搜索结果）：
{ev_lines}

逐条判定，verdict 只允许取以下三个值：
- reliable：关键事实有核查参考支撑，或与公认知识一致
- uncertain：部分细节存疑、来源可靠性不足或证据相互矛盾
- unverifiable：无从核实、疑似编造或与已知事实冲突

只输出 JSON，不要输出其他内容，格式：
{{"verdicts": [{{"title": "候选标题，原样照抄", "verdict": "reliable", "reason": "一句话依据"}}]}}"""

    try:
        out = llm.chat(
            task="factcheck", system=FACTCHECK_SYSTEM, user=user_prompt, temperature=0.2
        )
        data = _extract_json(out)
    except Exception:  # LLM 挂了同样宁枉勿纵
        data = None

    parse_failed = True
    verdict_map: dict[str, tuple[str, str]] = {}
    # 归一输出形状:{"verdicts": [...]} 取字段;裸数组(LLM 省略包装)直接当列表
    verdict_items = None
    if isinstance(data, dict):
        verdict_items = data.get("verdicts")
    elif isinstance(data, list):
        verdict_items = data
    if isinstance(verdict_items, list):
        parse_failed = False
        for item in verdict_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            verdict = str(item.get("verdict", "")).strip().lower()
            if verdict not in ("reliable", "uncertain", "unverifiable"):
                verdict = "uncertain"  # 未知取值宁枉勿纵
            verdict_map[title] = (verdict, str(item.get("reason", "")).strip())

    survivors: list[dict] = []
    for c in candidates:
        title = str(c.get("title", "")).strip()
        if title in verdict_map:
            verdict, reason = verdict_map[title]
        else:
            verdict = "uncertain"
            reason = (
                "核查输出无法解析，宁枉勿纵按存疑处理"
                if parse_failed
                else "核查结果未覆盖该条，按存疑处理"
            )
        if verdict == "unverifiable":
            continue  # 无从核实：直接丢弃，宁可少选题
        checked = dict(c)
        checked["factcheck"] = {"verdict": verdict, "reason": reason}
        survivors.append(checked)
    return survivors


def plan_emotion_arc(llm: LLMProvider, profile: dict, content_block: str,
                     minutes: int, mood: dict | None = None) -> list[dict]:
    """内容感知的情绪弧线:先分析内容,再规划节拍与情绪(写稿前执行)。

    返回 [{"beat": 节拍名, "emotion": 情绪词, "share": 时长占比 0-1}],
    share 合计为 1。解析失败时返回空列表(写稿退化为无弧线模式)。
    """
    tags = " / ".join(k for k in profile["emotions"] if k != "default")
    mood_line = ""
    if mood:
        mood_line = (f"\n整期基调已由用户选定:「{mood.get('name', '')}」——"
                     f"{mood.get('anchor', '')}弧线必须在该基调内起伏。")
    prompt = f"""你将担任这期节目的「情绪导演」。先分析内容讲的是什么、听众是谁,
再规划一条情绪弧线:把节目切成 4-6 个节拍,每个节拍指定情绪。

可用情绪词:{tags}

{mood_line}
只输出 JSON 数组(不要其他内容):
[{{"beat": "节拍名", "emotion": "情绪词(必须来自可用情绪词)", "share": 0.25, "intent": "这个节拍要达成什么"}}]

share 合计必须等于 1。情绪要贴合内容本身的变化,不要机械套模板。

内容:
---
{content_block[:2500]}
---

时长:约 {minutes} 分钟"""
    out = llm.chat(task="arc", system="你是资深音频内容导演,擅长设计情绪节奏。",
                   user=prompt, temperature=0.4)
    data = _extract_json(out)
    if not isinstance(data, list):
        return []
    arc = []
    for item in data:
        if not isinstance(item, dict):
            continue
        emotion = str(item.get("emotion", "")).strip()
        if emotion not in profile["emotions"]:
            continue
        try:
            share = max(0.05, min(0.6, float(item.get("share", 0.2))))
        except (TypeError, ValueError):
            share = 0.2
        arc.append({"beat": str(item.get("beat", "")).strip()[:30],
                    "emotion": emotion, "share": round(share, 3),
                    "intent": str(item.get("intent", "")).strip()[:120]})
    total = sum(a["share"] for a in arc) or 1
    for a in arc:  # 归一化
        a["share"] = round(a["share"] / total, 3)
    return arc


def write_script(llm: LLMProvider, profile: dict, minutes: int, content_block: str,
                 target_chars: int | None = None, beats: list[dict] | None = None,
                 mood: dict | None = None) -> str:
    """写稿。content_block：选题模式下是「选题：xxx\n角度：xxx」，素材模式下是素材正文。

    target_chars:时长校准给出的字数目标(实测字符/秒 × 时长),优先于
    minutes × words_per_minute 的粗估。LLM 常写不到字数,提示词按目标
    上浮并要求自查。mood:用户选定的整期基调(锚定情绪导演与文风)。
    """
    words = target_chars or int(minutes * profile["words_per_minute"])
    tags = " / ".join(k for k in profile["emotions"] if k != "default")
    mood_line = (f"\n整期基调:「{mood.get('name', '')}」——{mood.get('anchor', '')}"
                 if mood else "")
    user = profile["script_user"].format(
        content_block=content_block, minutes=minutes, words=words
    )
    if mood_line:
        user += mood_line
    if beats:
        plan = "\n".join(
            f"{i + 1}. 【{b['name']}】情绪:{b['emotion']} · 约 {b['chars']} 字 — {b['desc']}"
            for i, b in enumerate(beats))
        user += (f"\n\n按以下节拍计划写作,严格按顺序覆盖全部节拍,"
                 f"每个节拍的情感按提示演绎,字数尽量贴齐:\n{plan}")
    user += (
        f"\n\n硬性要求:全文(不含情绪标签)不少于 {int(words * 0.95)} 字、"
        f"不超过 {int(words * 1.15)} 字。写完自己数字数,不够就继续展开细节。"
    )
    return llm.chat(
        task="script",
        system=profile["script_system"].format(emotion_tags=tags),
        user=user,
        temperature=0.85,
        max_tokens=12000,
    ).strip()


def generate_shownotes(llm: LLMProvider, profile: dict, *, title: str,
                       script: str, minutes: int) -> str:
    """根据文稿生成 shownotes(markdown)。失败时返回纯文本兜底。"""
    prompt = f"""根据以下播客文稿写 shownotes(markdown),用于发布时展示。

要求:
1. 开头一句话推荐(吸引点开)
2. 「本期要点」3-5 条,每条一句话
3. 适合直接粘贴到小宇宙/喜马拉雅
4. 不要编造文稿里没有的内容

节目标题:{title}
时长:约 {minutes} 分钟

文稿:
---
{script[:3000]}
---"""
    out = llm.chat(task="shownotes", system="你是播客运营编辑。", user=prompt, temperature=0.5).strip()
    return out or f"# {title}\n\n{minutes} 分钟,开听。"


def dialogize_script(llm: LLMProvider, profile: dict, script: str,
                     max_turn_chars: int = 290) -> list[dict]:
    """单人文稿 → A/B 双人对话轮次。

    返回 [{"speaker": "A"|"B", "text": "..."}],单轮 ≤ max_turn_chars。
    解析失败或轮次为空时返回 [{"speaker": "A", "text": 原文}] 兜底(宁可用
    单人轮次也别让合成失败)。
    """
    prompt = f"""把下面的文稿改写成两位主持人(A 和 B)的对谈轮次。

要求:
1. 内容忠实于原稿,不新增事实;把讲解自然拆成两人一来一回
2. A 偏主讲,B 偏追问/接话/补充;轮流开口,每轮 1-3 句话
3. 只输出 JSON 数组,不要其他内容:
[{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}]
4. 每轮不超过 {max_turn_chars} 字符;情绪标签([情绪:xx])去掉,不要输出

文稿:
---
{script[:4000]}
---"""
    out = llm.chat(task="dialogize", system="你是对谈编排师。", user=prompt,
                   temperature=0.6, max_tokens=8000)

    data = _extract_json(out)
    if not isinstance(data, list):
        return [{"speaker": "A", "text": script}]
    turns = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        speaker = "B" if str(item.get("speaker", "A")).strip().upper().startswith("B") else "A"
        for piece in _split_round(text, max_turn_chars):
            turns.append({"speaker": speaker, "text": piece})
    if not turns:
        return [{"speaker": "A", "text": script}]
    return turns


def _split_round(text: str, limit: int) -> list[str]:
    """单轮超长时按句切分,保持同说话人。"""
    import re

    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for part in re.split(r"(?<=[。！？!?…；;\n])", text):
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) <= limit:
            buf += part
            continue
        if buf:
            out.append(buf)
        buf = ""
        while len(part) > limit:
            out.append(part[:limit])
            part = part[limit:]
        buf = part
    if buf:
        out.append(buf)
    return out


def compliance_check(llm: LLMProvider, profile: dict, script: str) -> dict:
    """合规审查：返回 {"pass": bool, "items": [...], "summary": str}。

    送审前剥离情绪标签——标签是给 TTS 的设计产物，审它们只会产生假阳性
    （实测 glm 审稿把 [情绪:xx] 当成"格式问题"）。
    """
    from .chunker import strip_tags

    script = strip_tags(script)
    dims = "\n".join(f"{i+1}. {name}：{desc}" for i, (name, desc) in enumerate(profile["compliance_dimensions"]))
    prompt = f"""请对以下文稿做发布前审查，逐项检查这些维度：

{dims}

输出 JSON（不要其他内容）：
{{"pass": true或false, "items": [{{"dimension": "维度名", "pass": true或false, "issues": "具体问题(无则空)", "suggestion": "修改建议(无则空)"}}], "summary": "一句话总评"}}

文稿：
---
{script}
---"""
    out = llm.chat(task="compliance", system="你是内容安全审查员，严格但务实。", user=prompt, temperature=0.2)
    data = _extract_json(out)
    if not isinstance(data, dict):
        # 无法结构化时，宁可保守：给出原文让人工判断
        return {"pass": None, "items": [], "summary": out[:500], "parse_failed": True}
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    return {
        "pass": bool(data.get("pass", True)),
        "items": [i for i in items if isinstance(i, dict)],
        "summary": str(data.get("summary", "")),
    }
