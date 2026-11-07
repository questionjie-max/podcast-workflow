"""搜索 Provider 抽象:给选题步骤提供实时热点参考。

- off:不搜索(默认,用模型知识)
- tavily:https://api.tavily.com/search(Bearer 认证)
- bocha 博查:https://api.bochaai.com/v1/web-search(国内可用)

统一返回 [{title, snippet, url}]。失败抛 SearchError,由调用方兜底忽略。
结果净化(sanitize_results)由调用方在拿到结果后自行调用,provider 保持无过滤。
"""

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx


class SearchError(RuntimeError):
    pass


# 搜索轮次配置上限:初搜 + 事实核查的搜索调用合计不得超过此值(防配置失误烧钱)。
MAX_ROUNDS_LIMIT = 3

# 儿童模式域名黑名单:八卦狗仔 / 海外小报标题党 / 中文低质内容农场 / 色情论坛。
# curated 列表,只收置信度高的真实域名,子域名一并命中(如 m.tmz.com)。
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # —— 海外小报 / 耸动标题党 ——
    "dailymail.co.uk",   # 每日邮报:耸动标题党
    "thesun.co.uk",      # 英国太阳报:小报
    "the-sun.com",       # 太阳报美版
    "dailystar.co.uk",   # 每日星报:小报
    "mirror.co.uk",      # 每日镜报:小报
    "express.co.uk",     # 每日快报:小报
    "nypost.com",        # 纽约邮报:标题党
    "nydailynews.com",   # 纽约每日新闻:小报
    # —— 名人八卦 / 狗仔 ——
    "tmz.com",           # TMZ:狗仔八卦
    "pagesix.com",       # Page Six:名人八卦
    "perezhilton.com",   # 名人八卦博客
    "radaronline.com",   # 八卦爆料站
    "mediatakeout.com",  # 名人八卦站
    "usmagazine.com",    # 名人八卦杂志
    # —— 中文低质内容农场 ——
    "qutoutiao.net",     # 趣头条:标题党内容农场
    # —— 色情论坛 ——
    "t66y.com",          # 草榴:色情论坛
    "sexinsex.net",      # 色中色:色情论坛
})

# 儿童模式敏感词:标题/摘要命中即丢弃(赌/性/暴力/惊悚类),纯包含匹配、不做分词。
SUSPICIOUS_KEYWORDS: tuple[str, ...] = (
    # 赌
    "赌博", "博彩", "赌场", "六合彩",
    # 性
    "色情", "情色", "裸聊", "约炮", "援交",
    # 暴力
    "暴力", "血腥", "凶杀", "虐待", "自杀",
    # 惊悚 / 恐慌
    "恐怖", "灵异", "惊悚",
    # 其他少儿不宜
    "毒品",
)


def _host_of(url: str) -> str:
    """从 URL 提取小写主机名并去掉 www. 前缀;解析失败返回空串。"""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def sanitize_results(results: list[dict] | None, kid_safe: bool = False) -> list[dict]:
    """净化搜索结果:所有模式按 url 去重;儿童模式再加黑名单与敏感词过滤。

    - 去重:相同 url(含空 url 视为同一键)只保留首次出现。
    - kid_safe=True:命中 BLOCKED_DOMAINS(含子域名)或标题/摘要命中
      SUSPICIOUS_KEYWORDS 的结果一律丢弃——给孩子的内容宁缺毋滥。
    - kid_safe=False:仅去重,不做内容过滤(成人节目自行把关)。
    """
    seen: set[str] = set()
    kept: list[dict] = []
    for r in results or []:
        url = str(r.get("url") or "").strip()
        if url in seen:
            continue
        seen.add(url)
        if kid_safe:
            host = _host_of(url)
            if host and any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS):
                continue
            text = f"{r.get('title', '')}\n{r.get('snippet', '')}"
            if any(kw in text for kw in SUSPICIOUS_KEYWORDS):
                continue
        kept.append(r)
    return kept


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """返回 [{title, snippet, url}],失败抛 SearchError。"""


class TavilySearch(SearchProvider):
    name = "tavily"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.base = (cfg.get("base_url") or "https://api.tavily.com").rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        if not self.api_key:
            raise SearchError("未配置 Tavily API Key")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/search",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"query": query, "max_results": max_results},
            )
        if resp.status_code != 200:
            raise SearchError(f"Tavily 返回 {resp.status_code}：{resp.text[:200]}")
        data = resp.json()
        return [
            {"title": r.get("title", ""), "snippet": (r.get("content") or "")[:300],
             "url": r.get("url", "")}
            for r in (data.get("results") or [])[:max_results]
        ]


class BochaSearch(SearchProvider):
    name = "bocha"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_key = cfg.get("api_key") or ""
        self.base = (cfg.get("base_url") or "https://api.bochaai.com").rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        if not self.api_key:
            raise SearchError("未配置博查 API Key")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/v1/web-search",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"query": query, "summary": True, "count": max_results},
            )
        if resp.status_code != 200:
            raise SearchError(f"博查返回 {resp.status_code}：{resp.text[:200]}")
        pages = ((resp.json().get("data") or {}).get("webPages") or {}).get("value") or []
        return [
            {"title": r.get("name", ""), "snippet": (r.get("summary") or "")[:300],
             "url": r.get("url", "")}
            for r in pages[:max_results]
        ]


def make_search(cfg: dict) -> SearchProvider | None:
    """按 config['search'] 构建搜索 provider;off/未知返回 None。"""
    provider = (cfg or {}).get("provider", "off")
    if provider == "tavily":
        return TavilySearch(cfg.get("tavily", {}))
    if provider == "bocha":
        return BochaSearch(cfg.get("bocha", {}))
    return None
