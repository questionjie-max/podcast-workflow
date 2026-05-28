# Contributing to Podcast Workflow

Thank you for your interest in contributing!

## How to Contribute

### Report Issues / 报告问题

- Use [GitHub Issues](https://github.com/questionjie-max/podcast-workflow/issues)
- Describe the problem clearly
- Include your Claude Code version and OS

### Suggest Features / 建议功能

- Open an Issue with the `enhancement` label
- Describe the use case and expected behavior

### Submit Code / 提交代码

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## Development / 开发

### Project Structure

```
podcast-workflow/
├── skill.json      # Skill metadata & keywords
├── SKILL.md        # Workflow documentation (bilingual)
├── README.md       # Project overview (bilingual)
├── LICENSE         # MIT License
└── CONTRIBUTING.md # This file
```

### Testing / 测试

Test the workflow in Claude Code:

```bash
# Install locally
cp -r . ~/.claude/skills/podcast-workflow

# Trigger in Claude Code
/podcast-workflow
```

## Code of Conduct / 行为准则

- Be respectful and constructive
- Focus on what is best for the community
- Show empathy towards other contributors

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
