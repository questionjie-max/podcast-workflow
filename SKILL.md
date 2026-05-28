---
name: podcast-workflow
description: "AI Podcast Generator: Topic Research → Script Writing → Compliance Review → TTS Voice Synthesis. Supports Xiaomi MiMo built-in voices and voice cloning with per-paragraph emotion matching. AI播客制作：选题搜索 → 文稿撰写 → 合规审查 → TTS语音合成，支持声音克隆和情绪节奏匹配。"
---

# Podcast Workflow — AI Podcast Generator

> 输入选题范围 / Give it a topic domain → 自动产出可发布的播客音频 / Get publish-ready podcast audio

## Quick Start / 快速开始

**English:** Give Claude a topic (e.g., "Shanghai real estate"), and it will research hot topics, write a podcast script, run compliance checks, and synthesize speech — all in one flow.

**中文：** 给 Claude 一个选题范围（如"上海房产"），它会自动搜索热点、撰写播客文稿、合规审查、语音合成，一站完成。

## Trigger Words / 触发词

Say any of these to activate the workflow:

- `/podcast-workflow`
- "做一期播客" / "Make a podcast"
- "帮我生成播客" / "Generate a podcast"
- "播客工作流" / "Podcast workflow"
- "用我的声音做播客" / "Clone my voice for podcast"
- "podcast about {topic}"
- "关于{主题}的播客"

## API Configuration / API 配置

```
Platform:     Xiaomi MiMo API Open Platform
Endpoint:     https://token-plan-cn.xiaomimimo.com/v1
Auth Header:  api-key: {MIMO_API_KEY}
SSL Note:     Python requests need ssl.CERT_NONE
Web Search:   Activate at https://platform.xiaomimimo.com/#/console/plugin
```

### Models Used

| Purpose | Model ID | Notes |
|---------|----------|-------|
| Script Writing | `mimo-v2.5-pro` | Generates podcast scripts with emotion tags |
| Compliance Check | `mimo-v2.5-pro` | Content safety review |
| TTS (Built-in) | `mimo-v2.5-tts` | Pre-built high-quality voices |
| TTS (Clone) | `mimo-v2.5-tts-voiceclone` | Clone from 30-60s audio sample |
| TTS (Design) | `mimo-v2.5-tts-voicedesign` | Create new voice from text description |
| Web Search | `mimo-v2.5-pro` + web_search tool | Real-time topic research |

## Two Modes / 两种模式

| Mode / 模式 | Description / 说明 | Best For / 适用 |
|-------------|-------------------|-----------------|
| **Built-in Voice / 内置音色** | Use MiMo's pre-built voices | Quick testing / 快速测试 |
| **Voice Clone / 声音克隆** | Clone your voice from a sample | Personal brand / 个人品牌 |

## Complete Workflow / 完整流程

```
[User provides topic domain / 用户给定选题范围]
                    ↓
[Step 1: Topic Research / 热点搜索]
  → MiMo Web Search (if activated) or model knowledge
  → Output: 3-5 candidates with title, angle, heat score
                    ↓
[Step 2: User Selects / 用户选题]
  → Display candidates, user picks one
                    ↓
[Step 3: Script Writing / 文稿撰写]
  → MiMo-V2.5-Pro generates conversational script
  → Each paragraph tagged with [Emotion: xxx]
                    ↓
[Step 4: Compliance Check / 合规检查]
  → Sensitive words, misinformation, format, legal risk
  → Output: pass/fail report with specific suggestions
                    ↓
[Step 5: Human Review / 用户确认]
  → Save script to Desktop, show report
  → User approves or requests changes
                    ↓
[Step 6: TTS Synthesis / TTS语音合成]
  → Split by emotion tags → Generate per-chunk → Merge
  → Output: WAV file on Desktop
```

---

## Step 1: Topic Research / 热点搜索

### With Web Search Plugin / 使用 Web Search

```json
{
  "model": "mimo-v2.5-pro",
  "messages": [
    {"role": "user", "content": "搜索{领域}的最新热点，列出5个最热门的播客选题..."}
  ],
  "tools": [{"type": "web_search", "max_keyword": 5, "force_search": true}],
  "max_completion_tokens": 2048,
  "temperature": 1.0,
  "stream": false,
  "thinking": {"type": "disabled"}
}
```

### Without Web Search / 无 Web Search

Fall back to model knowledge. Still generates 3-5 candidates with:
- Title / 标题
- Narrative angle / 切入角度
- Heat score (High/Medium/Low) / 预估热度
- One-line rationale / 一句话理由

---

## Step 2: Script Writing / 文稿撰写

### System Prompt

```
You are a podcast scriptwriter who writes as if telling a friend about something interesting you just discovered.

Tone: Like chatting at a café with a good friend — sharing an interesting finding, not giving a lecture.

Rules:
1. Short sentences, occasional long ones for rhythm variation
2. Natural conversational language (你知道吗, 说白了, 其实, 关键是)
3. Emotion should be restrained but genuine — curious, slightly surprised, thoughtful, NOT excited or loud
4. Tag each paragraph with emotion: [情绪：xxx] / [Emotion: xxx]
5. Use …… for natural pauses, ！ for mild emphasis, ？ for curiosity
6. No markdown, no sound effect cues in brackets
7. No prompt words or technical markers
```

### User Prompt Template

```
Write a podcast script for:

Topic: {selected title}

Requirements:
- Like sharing a discovery with a good friend — natural, restrained, genuine
- Not too excited, not too flat
- Duration: ~{N} minutes (~{word count} words)
- Each paragraph starts with emotion tag, e.g., [情绪：好奇] / [Emotion: Curious]
- Natural opening, no pleasantries
- Thoughtful ending with lingering resonance

Output the script directly, no preamble or postscript.
```

### Emotion Tags / 情绪标注

Every paragraph MUST start with an emotion tag. This is critical for TTS style matching.

| Tag (CN) | Tag (EN) | TTS Style Instruction |
|----------|----------|----------------------|
| 好奇开场 | Curious Opening | Excited but restrained, like sharing an interesting discovery |
| 展开分析 | Analysis | Calm, organized, with a sense of realization |
| 若有所思 | Thoughtful | Steady, contemplative, pondering deeper meaning |
| 略带调侃 | Playful | Light-hearted teasing, but fundamentally serious |
| 认真探讨 | Serious Discussion | Earnest but not heavy |
| 延伸思考 | Reflective | Measured, insightful, forward-looking |
| 回到现实 | Back to Reality | Pragmatic, slightly wistful |
| 轻松收尾 | Light Closing | Relaxed, with a sense of anticipation |

---

## Step 3: Compliance Check / 合规审查

Review dimensions:
1. **Sensitive words / 敏感词** — Political, discriminatory, violent content
2. **Misinformation / 虚假信息** — False data, misleading claims
3. **Format issues / 格式问题** — Prompt residue, technical markers
4. **Legal risk / 法律风险** — Privacy, defamation, copyright
5. **Marketing bias / 广告嫌疑** — Undisclosed commercial promotion

Output: Structured report with pass/fail per dimension and specific fix suggestions.

---

## Step 4: User Review / 用户确认

- Save script to Desktop: `播客文稿_{主题}.txt`
- Display compliance report
- Wait for user approval
- User may request: delete content, adjust tone, modify specific wording

---

## Step 5: TTS Synthesis / TTS语音合成

### Audio Format
- Output: WAV (24kHz, 16bit, mono)
- Delivery: Can convert to MP3 for smaller size

### Built-in Voice Mode / 内置音色

```json
{
  "model": "mimo-v2.5-tts",
  "messages": [
    {"role": "user", "content": "用自然、亲切、有节奏感的播客风格朗读以下内容。"},
    {"role": "assistant", "content": "{script content}"}
  ],
  "audio": {"format": "wav", "voice": "mimo_default"}
}
```

Available voices: `mimo_default`, `default_zh`, `default_en`, and more at [MiMo Studio](https://aistudio.xiaomimimo.com/#/c)

### Voice Clone Mode / 声音克隆

```json
{
  "model": "mimo-v2.5-tts-voiceclone",
  "messages": [
    {"role": "user", "content": "{emotion-specific style instruction}"},
    {"role": "assistant", "content": "{script content}"}
  ],
  "audio": {
    "format": "wav",
    "voice": "data:audio/wav;base64,{base64-encoded voice sample}"
  }
}
```

### Chunking Strategy / 分段策略

1. Split by `[情绪：xxx]` / `[Emotion: xxx]` tags
2. Sub-split within each section to 300-400 chars max
3. Match each chunk with its emotion's style instruction
4. Generate TTS per chunk, then merge with Python `wave` module

### Voice Recording Requirements / 录音要求

- Quiet environment, no background noise
- Normal pace, conversational tone
- Cover various sounds and tones (for Chinese: zh/ch/sh, b/p/m/f, ü, nasal sounds)
- Duration: 30-60 seconds
- Format: WAV or M4A (M4A needs conversion)

### M4A to WAV Conversion / M4A 转 WAV

```bash
afconvert -f WAVE -d LEI16@24000 -c 1 input.m4a output.wav
```

### Audio Merge Script / 合并音频

```python
import wave, os

chunk_dir = '/tmp/tts_chunks'
chunk_files = sorted([f for f in os.listdir(chunk_dir) if f.endswith('.wav')])

with wave.open(os.path.join(chunk_dir, chunk_files[0]), 'rb') as w:
    params = w.getparams()

with wave.open('output.wav', 'wb') as out:
    out.setparams(params)
    for cf in chunk_files:
        with wave.open(os.path.join(chunk_dir, cf), 'rb') as w:
            out.writeframes(w.readframes(w.getnframes()))
```

---

## Output Files / 输出文件

| File / 文件 | Description / 说明 |
|-------------|-------------------|
| `播客文稿_{topic}.txt` | Clean script (emotion tags removed) |
| `播客_{topic}.wav` | Built-in voice version |
| `播客_{topic}_我的声音.wav` | Voice clone version |
| `声音录制稿.txt` | Recording script for voice cloning |

All files saved to user's Desktop.

---

## Publishing Platforms / 发布平台

| Platform | Region | Notes |
|----------|--------|-------|
| 小宇宙 (Xiaoyuzhou) | China | #1 podcast community |
| 喜马拉雅 (Ximalaya) | China | Largest audio platform |
| Apple Podcasts | Global | Requires RSS feed |
| Spotify | Global | Fastest growing |
| 网易云音乐 | China | Music + podcast |
| QQ音乐 | China | Music + podcast |

---

## Important Notes / 注意事项

1. **Web Search Plugin / Web Search 插件:** Must be activated at MiMo console. Without it, falls back to model knowledge.
2. **TTS Pricing / TTS 定价:** Currently free. Monitor [pricing page](https://platform.xiaomimimo.com/#/docs/pricing) for changes.
3. **Chunk Size / 分段长度:** Keep each chunk under 400 characters for best TTS quality.
4. **Emotion Matching / 情绪匹配:** This is the KEY differentiator. Each paragraph MUST have a matching style instruction. Never use a flat tone throughout.
5. **Conversational Tone / 口语化:** Avoid formal writing, long sentences, jargon. Write like you speak.
6. **SSL Issue / SSL 问题:** Python `urllib` needs `ssl.CERT_NONE` for MiMo endpoint.

---

## Example Output / 示例输出

### Topic Selection / 选题示例

```
1. 保租房"国家队"入场，对我们的租金和选择意味着什么？ [热度：高]
2. "豪宅税"取消后，上海高端市场迎来了春天还是虚火？ [热度：高]
3. 当"老破小"跌到心理价位，年轻人的第一套房该怎么选？ [热度：中高]
```

### Script Excerpt / 文稿示例

```
[情绪：好奇开场]
你知道吗，我最近刷手机，看到一条新闻，说好多城市开始搞一种叫"保租房"的东西，而且这次不是小打小闹，是"国家队"亲自下场了。

[情绪：展开分析，带点恍然大悟的感觉]
我以前租房，你知道的，就是跟中介斗智斗勇，要么就是押一付三，压力山大。但这次"国家队"入场，我感觉游戏规则要变了。
```

---

## License

MIT — See [LICENSE](LICENSE)
