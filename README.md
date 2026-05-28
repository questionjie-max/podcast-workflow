# Podcast Workflow — AI播客制作工作流

一个基于 Xiaomi MiMo TTS 的 Claude Code Skill，输入选题范围即可自动完成播客制作全流程。

## 功能

- **热点搜索** — 自动搜索指定领域的热门话题，生成候选选题
- **文稿撰写** — AI生成口语化播客文稿，每段标注情绪基调
- **合规审查** — 自动检查敏感词、虚假信息、格式问题
- **TTS语音合成** — 支持MiMo内置音色和声音克隆两种模式
- **情绪匹配** — 根据每段内容自动匹配对应的语气和节奏

## 工作流

```
[选题范围] → [热点搜索] → [选择选题] → [文稿撰写] → [合规检查] → [确认] → [TTS合成]
```

## 安装

### 前置条件

- [Claude Code](https://claude.ai/code) 已安装
- [Xiaomi MiMo API Key](https://platform.xiaomimimo.com) — TTS限时免费

### 安装步骤

```bash
# 克隆到 Claude Code skills 目录
git clone https://github.com/questionjie-max/podcast-workflow.git ~/.claude/skills/podcast-workflow
```

## 使用

在 Claude Code 中触发：

```
/podcast-workflow
```

或直接说：
- "做一期播客"
- "帮我生成播客"
- "播客工作流"

## 两种模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **内置音色** | 使用MiMo预设音色 | 快速测试，无需录音 |
| **声音克隆** | 用你的录音克隆音色 | 个人播客，品牌一致性 |

### 声音克隆录音要求

- 安静环境，无背景噪音
- 正常说话，语速适中
- 时长30-60秒
- 格式：WAV 或 M4A

## API配置

在 `SKILL.md` 中配置你的 MiMo API Key：

```
端点：https://token-plan-cn.xiaomimimo.com/v1
认证头：api-key: {YOUR_API_KEY}
```

## 情绪匹配系统

文稿的每段会标注情绪基调，TTS会根据情绪自动调整语气：

| 情绪 | 语气 |
|------|------|
| 好奇开场 | 像发现有趣事情要分享 |
| 展开分析 | 帮朋友理清思路 |
| 若有所思 | 沉稳、思考深层含义 |
| 略带调侃 | 轻松但认真 |
| 认真探讨 | 认真但不沉重 |
| 延伸思考 | 从容、有深度 |
| 回到现实 | 务实、略带感慨 |
| 轻松收尾 | 带点期待 |

## 输出文件

| 文件 | 说明 |
|------|------|
| `播客文稿_{主题}.txt` | 播客文稿 |
| `播客_{主题}.wav` | 内置音色版音频 |
| `播客_{主题}_我的声音.wav` | 声音克隆版音频 |

## 依赖

- Python 3.x（标准库即可，无需额外安装）
- macOS（用于 `afconvert` 音频转换）

## License

MIT
