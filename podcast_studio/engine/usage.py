"""用量账本：记录每期节目实际消耗的写作/语音额度。

产品价值之一是可自托管的成本可见性：用户想知道「这一期花了多少」。
这里只统计**可确定性计量**的字符数与调用次数，不猜价格；价格换算在
`pricing.py`（可选配置）里做。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageMeter:
    """累计一次节目生产过程中的用量。

    llm_calls: 写作引擎调用次数（选题/写稿/合规/shownotes/改写对话…）
    llm_in_chars / llm_out_chars: 提示词与返回内容的字符数
    tts_chars: 送去语音合成的字符数（按分段实际文本计）
    """

    llm_calls: int = 0
    llm_in_chars: int = 0
    llm_out_chars: int = 0
    tts_chars: int = 0
    steps: dict[str, int] = field(default_factory=dict)

    def add_llm(self, task: str, prompt_chars: int, output_chars: int) -> None:
        self.llm_calls += 1
        self.llm_in_chars += max(0, prompt_chars)
        self.llm_out_chars += max(0, output_chars)
        self.steps[task] = self.steps.get(task, 0) + 1

    def add_tts(self, chars: int) -> None:
        self.tts_chars += max(0, chars)

    def merge(self, other: UsageMeter) -> None:
        self.llm_calls += other.llm_calls
        self.llm_in_chars += other.llm_in_chars
        self.llm_out_chars += other.llm_out_chars
        self.tts_chars += other.tts_chars
        for k, v in other.steps.items():
            self.steps[k] = self.steps.get(k, 0) + v

    def to_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "llm_in_chars": self.llm_in_chars,
            "llm_out_chars": self.llm_out_chars,
            "tts_chars": self.tts_chars,
            "steps": dict(self.steps),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> UsageMeter:
        data = data or {}
        return cls(
            llm_calls=int(data.get("llm_calls") or 0),
            llm_in_chars=int(data.get("llm_in_chars") or 0),
            llm_out_chars=int(data.get("llm_out_chars") or 0),
            tts_chars=int(data.get("tts_chars") or 0),
            steps=dict(data.get("steps") or {}),
        )


class CountingLLM:
    """LLM 包装器：转发 chat 调用并记账（对调用方完全透明）。"""

    def __init__(self, inner, meter: UsageMeter):
        self._inner = inner
        self._meter = meter
        self.name = getattr(inner, "name", "wrapped")

    def chat(self, task: str, system: str, user: str, **kwargs) -> str:
        out = self._inner.chat(task, system, user, **kwargs)
        self._meter.add_llm(
            task or "other", len(system or "") + len(user or ""), len(out or "")
        )
        return out

    def __getattr__(self, item):  # 其余属性(如 base_url)透传
        return getattr(self._inner, item)


def estimate_cost(usage: dict, pricing: dict | None) -> dict | None:
    """按可选定价估算费用。

    pricing 结构：
      {"llm_per_million_in": 0, "llm_per_million_out": 0, "tts_per_kchar": 0,
       "currency": "CNY"}
    未配置或全为 0 → 返回 None（前端只显示字符数，不显示金额）。
    """
    pricing = pricing or {}
    in_price = float(pricing.get("llm_per_million_in") or 0)
    out_price = float(pricing.get("llm_per_million_out") or 0)
    tts_price = float(pricing.get("tts_per_kchar") or 0)
    if not (in_price or out_price or tts_price):
        return None
    usage = usage or {}
    llm_cost = (
        float(usage.get("llm_in_chars") or 0) / 1_000_000 * in_price
        + float(usage.get("llm_out_chars") or 0) / 1_000_000 * out_price
    )
    tts_cost = float(usage.get("tts_chars") or 0) / 1000 * tts_price
    return {
        "llm": round(llm_cost, 4),
        "tts": round(tts_cost, 4),
        "total": round(llm_cost + tts_cost, 4),
        "currency": pricing.get("currency") or "CNY",
    }
