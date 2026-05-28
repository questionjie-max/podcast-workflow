---
name: podcast-workflow
description: "AI播客制作工作流：选题搜索 → 文稿撰写 → 合规审查 → 人工确认 → TTS语音合成。支持MiMo内置音色和声音克隆，自动匹配情绪节奏。"
---

# Podcast Workflow — AI播客制作工作流

## 一句话定位

输入选题范围（如"上海房产"），自动完成热点搜索 → 播客文稿 → 合规检查 → TTS语音合成，输出可直接发布的播客音频。

## API配置

- **平台：** Xiaomi MiMo API Open Platform
- **端点：** `https://token-plan-cn.xiaomimimo.com/v1`
- **认证头：** `api-key: {MIMO_API_KEY}`
- **TTS模型：** `mimo-v2.5-tts`（内置音色）/ `mimo-v2.5-tts-voiceclone`（声音克隆）
- **文稿生成模型：** `mimo-v2.5-pro`
- **Web Search：** 需在控制台激活插件 https://platform.xiaomimimo.com/#/console/plugin
- **SSL注意：** Python请求需禁用SSL验证（`ssl.CERT_NONE`）

## 两种模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **内置音色模式** | 使用MiMo预设音色（mimo_default等） | 快速测试，无需录音 |
| **声音克隆模式** | 用用户录音克隆音色 | 个人播客，品牌一致性 |

## 完整工作流

```
[用户给定选题范围]
        ↓
[Step 1: 热点搜索] — MiMo Web Search 或模型知识
        ↓
[Step 2: 用户选题] — 展示3-5个候选，用户选择
        ↓
[Step 3: 文稿撰写] — MiMo Pro生成播客文稿（带情绪标注）
        ↓
[Step 4: 合规检查] — MiMo Pro审查敏感词/虚假信息/格式
        ↓
[Step 5: 用户确认] — 展示文稿+审查报告，等待批准
        ↓
[Step 6: TTS生成] — 分段生成 → 合并 → 输出WAV
```

## Step 1: 热点搜索

优先使用MiMo Web Search（需插件已激活），格式：

```json
{
  "model": "mimo-v2.5-pro",
  "messages": [{"role": "user", "content": "搜索{领域}的最新热点..."}],
  "tools": [{"type": "web_search", "max_keyword": 5, "force_search": true}],
  "max_completion_tokens": 2048,
  "temperature": 1.0,
  "stream": false,
  "thinking": {"type": "disabled"}
}
```

若Web Search未激活，退化为模型知识生成候选选题。

输出：3-5个候选选题，每个包含标题、切入角度、预估热度、理由。

## Step 2: 文稿撰写

### System Prompt

```
你是一位播客撰稿人，专门为一个人向朋友讲述一件新鲜事的场景写稿。

语气基调：不是在做报告，不是在演讲，而是在咖啡馆跟好朋友聊天，分享一个你最近发现的有趣现象。

写作规则：
1. 短句为主，偶尔用长句制造节奏变化
2. 像说话一样自然，用口语：你知道吗、说白了、其实、关键是
3. 情绪要克制但真实——好奇、有点惊讶、若有所思，而不是大喊大叫
4. 每段标注情绪基调，格式：[情绪：xxx]
5. 用……表示自然停顿，用！表示轻度感叹，用？表示好奇
6. 不要用markdown格式，不要用括号音效提示
7. 不要出现任何提示词或技术标记
```

### User Prompt模板

```
请为以下选题撰写播客文稿：

选题：{用户选定的标题}

要求：
- 像跟好朋友分享一个发现，语气自然、克制、真诚
- 不要过于激动，也不要过于平淡
- 时长约{N}分钟（约{字数}字）
- 每段开头标注情绪基调，例如：[情绪：好奇]、[情绪：若有所思]
- 开头自然切入，不要客套
- 结尾有回味感

请直接输出文稿内容。
```

### 情绪标注规范

每段开头必须有 `[情绪：xxx]` 标签，用于后续TTS风格匹配。常用情绪：

| 情绪 | 风格指令 |
|------|----------|
| 好奇开场 | 语气好奇、有点兴奋但克制，像发现有趣事情要分享 |
| 展开分析 | 语气平稳、有条理，带点恍然大悟 |
| 若有所思 | 语气沉稳、略带思考，像在琢磨深层含义 |
| 略带调侃 | 语气轻松、带点调侃，但本质上认真 |
| 认真探讨 | 语气认真但不沉重 |
| 延伸思考 | 语气从容、有深度，带点若有所思 |
| 回到现实 | 语气务实、略带感慨 |
| 轻松收尾 | 语气轻松、带点期待 |

## Step 3: 合规检查

用MiMo Pro审查文稿，检查维度：
1. 敏感词/违禁词
2. 虚假信息风险
3. 格式问题（提示词残留、技术标记）
4. 法律风险（隐私、诽谤）
5. 广告/营销嫌疑

输出：总体评估（通过/需修改）+ 各维度结果 + 具体修改建议。

## Step 4: 用户确认

将文稿保存到桌面，展示审查报告，等待用户确认。用户可能要求：
- 删除某些内容（如音效提示）
- 调整风格或语气
- 修改具体表述

## Step 5: TTS语音合成

### 音频格式

- **输出格式：** WAV（24kHz, 16bit, mono）
- **最终交付：** 可转MP3减小体积

### 内置音色模式

```json
{
  "model": "mimo-v2.5-tts",
  "messages": [
    {"role": "user", "content": "用自然、亲切、有节奏感的播客风格朗读以下内容。"},
    {"role": "assistant", "content": "{文稿内容}"}
  ],
  "audio": {"format": "wav", "voice": "mimo_default"}
}
```

### 声音克隆模式

```json
{
  "model": "mimo-v2.5-tts-voiceclone",
  "messages": [
    {"role": "user", "content": "{情绪对应的风格指令}"},
    {"role": "assistant", "content": "{文稿内容}"}
  ],
  "audio": {
    "format": "wav",
    "voice": "data:audio/wav;base64,{用户录音的base64}"
  }
}
```

### 分段策略

1. 按 `[情绪：xxx]` 标签分段
2. 每段内部再按300-400字切分
3. 为每个chunk匹配对应的风格指令
4. 分段生成后用Python wave模块合并

### 录音要求（声音克隆）

- 安静环境，无背景噪音
- 正常说话，语速适中
- 覆盖各种声母韵母和声调
- 时长30-60秒即可
- WAV或M4A格式（M4A需用`afconvert`转WAV）

### M4A转WAF命令

```bash
afconvert -f WAVE -d LEI16@24000 -c 1 input.m4a output.wav
```

### 合并音频脚本

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

## 文件输出

所有文件保存到用户桌面：

| 文件 | 说明 |
|------|------|
| `播客文稿_{主题}.txt` | 纯文稿（删除情绪标注后） |
| `播客_{主题}.wav` | 内置音色版音频 |
| `播客_{主题}_我的声音.wav` | 声音克隆版音频 |
| `声音录制稿.txt` | 声音克隆用的录音文本 |

## 注意事项

1. **Web Search需激活：** 如未激活，退化为模型知识生成选题
2. **TTS限时免费：** MiMo TTS当前免费，后续关注定价变化
3. **分段不宜过长：** 单chunk不超过400字，否则TTS质量下降
4. **情绪匹配是关键：** 每段必须根据内容匹配对应风格，不能全程一个调
5. **文稿要口语化：** 避免书面语、长句、专业术语，像说话一样写
