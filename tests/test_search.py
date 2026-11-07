"""搜索 provider:Tavily/博查请求构造与响应解析 + S1 净化/事实核查(respx/桩)。"""

import asyncio
import json

import httpx
import pytest
import respx

from podcast_studio.engine import steps
from podcast_studio.providers.search import (
    MAX_ROUNDS_LIMIT,
    SUSPICIOUS_KEYWORDS,
    BochaSearch,
    TavilySearch,
    make_search,
    sanitize_results,
)

TAVILY = "https://api.tavily.com/search"
BOCHA = "https://api.bochaai.com/v1/web-search"


def test_make_search_factory():
    assert make_search({"provider": "off"}) is None
    assert make_search({}) is None
    assert isinstance(make_search({"provider": "tavily", "tavily": {"api_key": "k"}}), TavilySearch)
    assert isinstance(make_search({"provider": "bocha", "bocha": {"api_key": "k"}}), BochaSearch)


@respx.mock
@pytest.mark.asyncio
async def test_tavily_shape():
    route = respx.post(TAVILY).mock(return_value=httpx.Response(200, json={
        "results": [
            {"title": "热点A", "content": "内容A" * 200, "url": "https://a.com"},
            {"title": "热点B", "content": "内容B", "url": "https://b.com"},
        ]}))
    out = await TavilySearch({"api_key": "tvly-k"}).search("上海房产", max_results=2)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tvly-k"
    assert len(out) == 2
    assert out[0]["title"] == "热点A"
    assert len(out[0]["snippet"]) <= 300  # 截断


@respx.mock
@pytest.mark.asyncio
async def test_tavily_error():
    with respx.mock:
        respx.post(TAVILY).mock(return_value=httpx.Response(401, text="bad"))
        with pytest.raises(Exception, match="401"):
            await TavilySearch({"api_key": "k"}).search("x")
    with pytest.raises(Exception, match="未配置"):
        await TavilySearch({}).search("x")


@respx.mock
@pytest.mark.asyncio
async def test_bocha_shape():
    route = respx.post(BOCHA).mock(return_value=httpx.Response(200, json={
        "data": {"webPages": {"value": [
            {"name": "国内热点", "summary": "摘要", "url": "https://c.com"},
        ]}}}))
    out = await BochaSearch({"api_key": "bk"}).search("为什么会下雨", max_results=5)
    assert route.calls.last.request.headers["Authorization"] == "Bearer bk"
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["query"] == "为什么会下雨" and body["summary"] is True
    assert out[0]["title"] == "国内热点" and out[0]["url"] == "https://c.com"


@respx.mock
@pytest.mark.asyncio
async def test_bocha_missing_key():
    with pytest.raises(Exception, match="未配置"):
        await BochaSearch({}).search("x")


# ---- S1:结果净化 sanitize_results ---------------------------------------------

def _result(title: str = "标题", snippet: str = "摘要", url: str = "https://a.com/x") -> dict:
    return {"title": title, "snippet": snippet, "url": url}


def test_sanitize_blocked_domains_kid_safe():
    """kid_safe:黑名单域名(含子域名)被过滤,正常来源保留;kid_safe=False 不按域名过滤。"""
    results = [
        _result("正经科学新闻", url="https://news.example.com/a"),
        _result("明星大瓜", url="https://tmz.com/post"),
        _result("手机版小报", url="https://m.dailymail.co.uk/post"),  # 子域名一并命中
    ]
    out = sanitize_results(results, kid_safe=True)
    assert [r["url"] for r in out] == ["https://news.example.com/a"]
    assert len(sanitize_results(results, kid_safe=False)) == 3


def test_sanitize_suspicious_keywords_kid_safe():
    """kid_safe:标题或摘要命中敏感词丢弃;kid_safe=False 不按词过滤。"""
    kw = SUSPICIOUS_KEYWORDS[0]  # "赌博"
    results = [
        _result(title=f"孩子问：什么是{kw}？", url="https://a.com/1"),
        _result(title="为什么天是蓝的", snippet=f"里面提到了{kw}", url="https://a.com/2"),
        _result(title="为什么天是蓝的", snippet="正常的科学摘要", url="https://a.com/3"),
    ]
    out = sanitize_results(results, kid_safe=True)
    assert [r["url"] for r in out] == ["https://a.com/3"]
    # 非 kid_safe:只去重,不按敏感词过滤(此处 url 互不相同,全保留)
    assert len(sanitize_results(results, kid_safe=False)) == 3


def test_sanitize_dedupe_by_url():
    """所有模式都按 url 去重(保留首次出现);kid_safe=False 对黑名单域名/敏感词视而不见。"""
    results = [
        _result(f"{SUSPICIOUS_KEYWORDS[0]}新闻", url="https://tmz.com/a"),
        _result(f"{SUSPICIOUS_KEYWORDS[0]}新闻", url="https://tmz.com/a"),  # 重复 url
        _result(f"{SUSPICIOUS_KEYWORDS[0]}新闻", url="https://news.com/b"),  # 敏感词+黑名单域名
    ]
    out = sanitize_results(results, kid_safe=False)
    assert [r["url"] for r in out] == ["https://tmz.com/a", "https://news.com/b"]
    assert sanitize_results(results, kid_safe=True) == []  # kid_safe 下域名+敏感词全拦
    assert sanitize_results([]) == [] and sanitize_results(None) == []


def test_max_rounds_limit_constant():
    """轮次配置上限为 3(SPRINT 约定:初搜+核查合计不超过 3)。"""
    assert MAX_ROUNDS_LIMIT == 3


# ---- S1:事实核查 factcheck_candidates -----------------------------------------

class _StubLLM:
    """返回预设回复并记录调用的假 LLM(duck typing,对 factcheck 够用)。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, task, system, user, **kwargs):
        self.calls.append({"task": task, "system": system, "user": user})
        return self.reply


class _CountingSearch:
    """记录 query、返回固定结果(或抛错)的搜索桩,用于计数调用次数。"""

    def __init__(self, results: list[dict] | None = None, error: Exception | None = None):
        self.results = results if results is not None else [
            {"title": "参考新闻", "snippet": "支撑证据", "url": "https://src.example.com/1"}
        ]
        self.error = error
        self.calls: list[str] = []

    def __call__(self, query: str) -> list[dict]:
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.results


def _verdict_llm(verdicts: list[dict]) -> _StubLLM:
    """返回 verdicts JSON 的 LLM 桩(带围栏外文本,顺带验证鲁棒解析)。"""
    return _StubLLM("核查结果如下：" + json.dumps({"verdicts": verdicts}, ensure_ascii=False))


def _cands() -> list[dict]:
    return [
        {"index": 0, "title": "小雨滴的旅行", "angle": "水循环", "heat": "高", "reason": "r1"},
        {"index": 1, "title": "云朵从哪来", "angle": "蒸发成云", "heat": "中", "reason": "r2"},
        {"index": 2, "title": "为什么会打雷", "angle": "放电", "heat": "中", "reason": "r3"},
    ]


def _all_reliable() -> list[dict]:
    return [{"title": t, "verdict": "reliable", "reason": "有据"}
            for t in ("小雨滴的旅行", "云朵从哪来", "为什么会打雷")]


def test_factcheck_budget_and_merged_query():
    """search_fn 调用次数 ≤ 预算(max_rounds-1);所有候选拼成一次合并查询。"""
    llm = _verdict_llm(_all_reliable())
    search = _CountingSearch()
    out = steps.factcheck_candidates(llm, _cands(), search, max_rounds=2)
    assert len(search.calls) == 1  # 预算 2-1=1,一次合并查询,未超
    assert "小雨滴的旅行" in search.calls[0] and "为什么会打雷" in search.calls[0]
    assert len(out) == 3 and len(llm.calls) == 1

    # 超配轮数被钳制到 MAX_ROUNDS_LIMIT,调用数仍不超过预算
    search3 = _CountingSearch()
    steps.factcheck_candidates(_verdict_llm(_all_reliable()), _cands(), search3,
                               max_rounds=MAX_ROUNDS_LIMIT + 10)
    assert len(search3.calls) <= MAX_ROUNDS_LIMIT - 1


def test_factcheck_all_reliable_kept():
    """verdict 全 reliable → 全部保留且带 reliable 标记。"""
    out = steps.factcheck_candidates(_verdict_llm(_all_reliable()), _cands(), _CountingSearch())
    assert [c["title"] for c in out] == ["小雨滴的旅行", "云朵从哪来", "为什么会打雷"]
    assert all(c["factcheck"]["verdict"] == "reliable" for c in out)


def test_factcheck_unverifiable_dropped_uncertain_marked():
    """unverifiable 丢弃且不出现;uncertain 保留并打标;顺序保持。"""
    llm = _verdict_llm([
        {"title": "小雨滴的旅行", "verdict": "reliable", "reason": "有据"},
        {"title": "云朵从哪来", "verdict": "uncertain", "reason": "来源可靠性不足"},
        {"title": "为什么会打雷", "verdict": "unverifiable", "reason": "无从核实"},
    ])
    out = steps.factcheck_candidates(llm, _cands(), _CountingSearch(), max_rounds=2)
    assert [c["title"] for c in out] == ["小雨滴的旅行", "云朵从哪来"]
    assert out[0]["factcheck"] == {"verdict": "reliable", "reason": "有据"}
    assert out[1]["factcheck"] == {"verdict": "uncertain", "reason": "来源可靠性不足"}


def test_factcheck_invalid_json_all_uncertain():
    """LLM 输出非法 JSON:宁枉勿纵,全部保留并标记 uncertain。"""
    out = steps.factcheck_candidates(_StubLLM("抱歉我说不清楚"), _cands(), _CountingSearch())
    assert len(out) == 3
    assert all(c["factcheck"]["verdict"] == "uncertain" for c in out)


def test_factcheck_partial_coverage_fills_uncertain():
    """LLM 只覆盖部分候选:未覆盖的按 uncertain 处理。"""
    out = steps.factcheck_candidates(
        _verdict_llm([{"title": "小雨滴的旅行", "verdict": "reliable", "reason": "有据"}]),
        _cands(), _CountingSearch(), max_rounds=2)
    assert out[0]["factcheck"]["verdict"] == "reliable"
    assert [c["factcheck"]["verdict"] for c in out[1:]] == ["uncertain", "uncertain"]


def test_factcheck_zero_budget_skips_search():
    """max_rounds=1(初搜占 1 轮):核查零预算,一次都不搜,但仍保守打标。"""
    search = _CountingSearch()
    out = steps.factcheck_candidates(
        _verdict_llm([{"title": "小雨滴的旅行", "verdict": "reliable", "reason": "有据"}]),
        _cands(), search, max_rounds=1)
    assert search.calls == []
    assert [c["factcheck"]["verdict"] for c in out] == ["reliable", "uncertain", "uncertain"]


def test_factcheck_search_failure_still_judges():
    """search_fn 抛错不阻塞:证据为空、提示词注明无可用证据,LLM 照常判定。"""
    llm = _verdict_llm([
        {"title": "小雨滴的旅行", "verdict": "reliable", "reason": "公认知识"},
        {"title": "云朵从哪来", "verdict": "uncertain", "reason": "证据不足"},
        {"title": "为什么会打雷", "verdict": "uncertain", "reason": "证据不足"},
    ])
    search = _CountingSearch(error=RuntimeError("搜索服务炸了"))
    out = steps.factcheck_candidates(llm, _cands(), search, max_rounds=2)
    assert len(out) == 3  # 不抛错
    assert "搜索无可用证据" in llm.calls[0]["user"]
    assert out[0]["factcheck"]["verdict"] == "reliable"


@respx.mock
def test_factcheck_real_provider_http_calls_within_budget():
    """respx 断言:接真实 provider 时 HTTP 搜索调用次数 ≤ 预算。"""
    route = respx.post(TAVILY).mock(return_value=httpx.Response(200, json={
        "results": [{"title": "证据A", "content": "支撑", "url": "https://src.example.com/a"}]}))
    provider = TavilySearch({"api_key": "tvly-test"})

    def search_fn(query: str) -> list[dict]:
        return asyncio.run(provider.search(query))  # 同步桥接(engine 步骤为同步)

    out = steps.factcheck_candidates(
        _verdict_llm(_all_reliable()), _cands(), search_fn, max_rounds=2)
    assert len(route.calls) == 1 <= MAX_ROUNDS_LIMIT - 1
    assert len(out) == 3
