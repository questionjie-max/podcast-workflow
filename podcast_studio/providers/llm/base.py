"""LLM Provider 抽象。

统一接口：chat(task=..., system=..., user=...)。
- OpenAICompatLLM：任何 OpenAI 兼容接口（MiMo / DeepSeek / Qwen / Kimi / OpenAI...）
- MockLLM：演示模式，无需 API key，返回内置示例内容，可完整走通全流程
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
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
        """task: research | script | compliance —— 供 mock 等实现区分用途，真实实现可忽略。"""


def make_llm(cfg: dict) -> LLMProvider:
    from .mock import MockLLM
    from .openai_compat import OpenAICompatLLM

    provider = cfg.get("provider", "mock")
    if provider == "openai":
        return OpenAICompatLLM(cfg.get("openai", {}))
    return MockLLM()
