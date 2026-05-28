# Podcast Workflow — AI Podcast Generator

> **One-click podcast production powered by Xiaomi MiMo TTS**
>
> Topic Research → Script Writing → Compliance Review → Voice Synthesis

[English](#english) | [中文](#中文)

---

## English

### What is Podcast Workflow?

Podcast Workflow is a [Claude Code](https://claude.ai/code) skill that automates the entire podcast production pipeline. Give it a topic domain (e.g., "Shanghai real estate", "AI trends", "healthcare reform"), and it will:

1. **Research hot topics** — Search for trending discussions in your domain
2. **Generate podcast script** — Write conversational, listener-friendly content with emotion tags
3. **Compliance check** — Review for sensitive content, misinformation, and formatting issues
4. **Human review** — You approve the script before synthesis
5. **TTS synthesis** — Convert to natural speech using Xiaomi MiMo TTS

### Key Features

- **Voice Cloning** — Record 30-60 seconds of your voice, and the podcast sounds like you
- **Emotion Matching** — Each paragraph gets a matching tone (curious, thoughtful, playful, serious...)
- **Built-in Voices** — Multiple high-quality Chinese voices, zero setup needed
- **Compliance Engine** — Automatic content safety review before publishing
- **Conversational Tone** — Scripts sound like chatting with a friend, not reading a report

### Two Modes

| Mode | Description | Best For |
|------|-------------|----------|
| **Built-in Voice** | Use MiMo's pre-built voices | Quick testing, no recording needed |
| **Voice Clone** | Clone your own voice from a sample | Personal brand, consistent identity |

### Installation

```bash
# Clone into Claude Code skills directory
git clone https://github.com/questionjie-max/podcast-workflow.git ~/.claude/skills/podcast-workflow
```

**Prerequisites:**
- [Claude Code](https://claude.ai/code) installed
- [Xiaomi MiMo API Key](https://platform.xiaomimimo.com) (TTS is currently free)

### Usage

In Claude Code, simply say:

```
/podcast-workflow
```

Or naturally:
- "Make a podcast about Shanghai real estate"
- "做一期关于AI趋势的播客"
- "Generate a podcast using my voice clone"

### Workflow Diagram

```
┌─────────────────┐
│  Topic Domain    │  e.g., "Shanghai real estate"
└────────┬────────┘
         ▼
┌─────────────────┐
│  Hot Topic Search│  MiMo Web Search or model knowledge
└────────┬────────┘
         ▼
┌─────────────────┐
│  Select Topic    │  3-5 candidates with angles & heat scores
└────────┬────────┘
         ▼
┌─────────────────┐
│  Script Writing  │  Conversational style + emotion tags
└────────┬────────┘
         ▼
┌─────────────────┐
│  Compliance Check│  Sensitive words, misinformation, format
└────────┬────────┘
         ▼
┌─────────────────┐
│  Human Review    │  You approve or request changes
└────────┬────────┘
         ▼
┌─────────────────┐
│  TTS Synthesis   │  Per-paragraph emotion-matched voice
└────────┬────────┘
         ▼
┌─────────────────┐
│  Output: WAV/MP3 │  Ready to publish on 小宇宙/Ximalaya/Spotify
└─────────────────┘
```

### Output Files

| File | Description |
|------|-------------|
| `podcast_script_{topic}.txt` | Clean script (no tags) |
| `podcast_{topic}.wav` | Built-in voice version |
| `podcast_{topic}_my_voice.wav` | Voice clone version |

### Publishing Platforms

The generated audio can be published on:

- **小宇宙** (Xiaoyuzhou) — #1 Chinese podcast community
- **喜马拉雅** (Ximalaya) — Largest Chinese audio platform
- **Apple Podcasts** / **Spotify** — Global distribution
- **网易云音乐** / **QQ音乐** — Music platforms with podcast sections

---

## 中文

### 什么是 Podcast Workflow？

Podcast Workflow 是一个 [Claude Code](https://claude.ai/code) Skill，能自动化播客制作全流程。给它一个选题范围（比如"上海房产"、"AI趋势"、"医疗改革"），它会自动：

1. **搜索热点** — 搜索该领域的热门话题
2. **撰写文稿** — 生成口语化、适合收听的播客文稿，每段标注情绪基调
3. **合规审查** — 检查敏感词、虚假信息、格式问题
4. **人工确认** — 你审查通过后再合成
5. **TTS合成** — 用小米 MiMo TTS 转为自然语音

### 核心特性

- **声音克隆** — 录音30-60秒，播客就用你自己的声音
- **情绪匹配** — 每段自动匹配语气（好奇、沉稳、调侃、认真……）
- **内置音色** — 多种高质量中文音色，开箱即用
- **合规引擎** — 发布前自动内容安全审查
- **聊天语气** — 文稿像跟朋友聊天，不像念报告

### 两种模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **内置音色** | 使用 MiMo 预设音色 | 快速测试，无需录音 |
| **声音克隆** | 用你的录音克隆音色 | 个人品牌，声音一致性 |

### 安装

```bash
git clone https://github.com/questionjie-max/podcast-workflow.git ~/.claude/skills/podcast-workflow
```

**前置条件：**
- [Claude Code](https://claude.ai/code) 已安装
- [小米 MiMo API Key](https://platform.xiaomimimo.com)（TTS 限时免费）

### 使用方式

在 Claude Code 中输入：

```
/podcast-workflow
```

或自然语言：
- "做一期关于上海房产的播客"
- "用我的声音克隆生成播客"
- "Make a podcast about AI trends"

### 技术架构

| 组件 | 技术 | 说明 |
|------|------|------|
| 热点搜索 | MiMo Web Search | 实时搜索全网热点 |
| 文稿生成 | MiMo-V2.5-Pro | 大模型生成播客文稿 |
| 合规审查 | MiMo-V2.5-Pro | 内容安全审核 |
| 语音合成 | MiMo-V2.5-TTS | 内置音色 TTS |
| 声音克隆 | MiMo-V2.5-TTS-VoiceClone | 少量样本克隆音色 |
| 情绪控制 | Style Instructions | 每段匹配独立风格指令 |

### 输出文件

| 文件 | 说明 |
|------|------|
| `播客文稿_{主题}.txt` | 纯文稿 |
| `播客_{主题}.wav` | 内置音色版 |
| `播客_{主题}_我的声音.wav` | 声音克隆版 |

### 发布平台

生成的音频可发布到：

- **小宇宙** — 国内最活跃的播客社区
- **喜马拉雅** — 最大的中文音频平台
- **Apple Podcasts** / **Spotify** — 全球分发
- **网易云音乐** / **QQ音乐** — 音乐平台播客专区

---

## API Reference

### TTS Models

| Model | ID | Capability |
|-------|----|-----------|
| MiMo-V2.5-TTS | `mimo-v2.5-tts` | Built-in high-quality voices |
| MiMo-V2.5-TTS-VoiceDesign | `mimo-v2.5-tts-voicedesign` | Create new voices from text description |
| MiMo-V2.5-TTS-VoiceClone | `mimo-v2.5-tts-voiceclone` | Clone any voice from audio sample |

### Endpoint

```
POST https://token-plan-cn.xiaomimimo.com/v1/chat/completions
Header: api-key: {YOUR_API_KEY}
```

### Emotion Mapping

Each paragraph in the script has an emotion tag. The TTS engine maps it to a style instruction:

| Emotion Tag | TTS Style |
|-------------|-----------|
| 好奇开场 / Curious Opening | Excited but restrained, like sharing an interesting discovery |
| 展开分析 / Analysis | Calm, organized, with a sense of realization |
| 若有所思 / Thoughtful | Steady, contemplative, pondering deeper meaning |
| 略带调侃 / Playful | Light-hearted teasing, but fundamentally serious |
| 认真探讨 / Serious Discussion | Earnest but not heavy |
| 延伸思考 / Reflective | Measured, insightful, forward-looking |
| 回到现实 / Back to Reality | Pragmatic, slightly wistful |
| 轻松收尾 / Light Closing | Relaxed, with a sense of anticipation |

---

## Contributing

Issues and PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE)

---

## Keywords / 关键词

`podcast` `tts` `voice-cloning` `xiaomi-mimo` `ai-podcast` `claude-code` `claude-skill` `text-to-speech` `voice-synthesis` `podcast-generator` `podcast-workflow` `content-creation` `audio-generation` `hot-topics` `compliance-check` `emotion-matching` `mimo-tts` `voice-design` `播客` `语音合成` `声音克隆` `小米` `做播客` `生成播客` `播客文稿` `热点选题` `合规审查` `情绪匹配` `音频生成` `内容创作`
