"""OpenAI 兼容 LLM 客户端:认证方式、错误分类、响应解析(respx mock)。"""

import httpx
import pytest
import respx

from podcast_studio.providers.llm.base import make_llm
from podcast_studio.providers.llm.openai_compat import LLMError, OpenAICompatLLM

URL = "https://api.example.com/v1/chat/completions"


def make(**kw):
    base = {"base_url": "https://api.example.com/v1", "api_key": "sk-t", "model": "test-model"}
    base.update(kw)
    return OpenAICompatLLM(base)


def test_make_llm_factory():
    assert isinstance(make_llm({"provider": "openai", "openai": {}}), OpenAICompatLLM)
    assert make_llm({"provider": "mock"}).name == "mock"


@respx.mock
@pytest.mark.asyncio
async def test_api_key_header_style():
    route = respx.post(URL).mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "你好"}}]}))
    out = make(auth_style="api-key").chat("test", "sys", "user")
    assert out == "你好"
    assert route.calls.last.request.headers["api-key"] == "sk-t"
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
@pytest.mark.asyncio
async def test_bearer_header_style():
    route = respx.post(URL).mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}}]}))
    make(auth_style="bearer").chat("test", "sys", "user")
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-t"


@respx.mock
@pytest.mark.asyncio
async def test_request_body_shape():
    route = respx.post(URL).mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": "x"}}]}))
    llm = make(extra={"thinking": {"type": "disabled"}})
    llm.chat("test", "系统提示", "用户提示", temperature=0.5, max_tokens=123)
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "用户提示"},
    ]
    assert body["temperature"] == 0.5 and body["max_tokens"] == 123
    assert body["thinking"] == {"type": "disabled"}  # extra 透传


@respx.mock
@pytest.mark.asyncio
async def test_error_kinds():
    cases = [
        (401, "auth"), (403, "auth"), (404, "http"), (429, "rate_limit"), (500, "http"),
    ]
    for status, kind in cases:
        with respx.mock:
            respx.post(URL).mock(return_value=httpx.Response(status, text="boom"))
            with pytest.raises(LLMError) as ei:
                make().chat("test", "s", "u")
            assert ei.value.kind == kind, f"{status} 应分类为 {kind}"

    # 网络不通
    with respx.mock:
        respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(LLMError) as ei:
            make().chat("test", "s", "u")
        assert ei.value.kind == "network"

    # 响应格式异常
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(200, json={"nope": 1}))
        with pytest.raises(LLMError) as ei:
            make().chat("test", "s", "u")
        assert ei.value.kind == "other"


def test_config_errors_have_kind():
    with pytest.raises(LLMError) as ei:
        make(api_key="").chat("test", "s", "u")
    assert ei.value.kind == "config"
    with pytest.raises(LLMError) as ei:
        OpenAICompatLLM({"api_key": "k", "model": "m"}).chat("test", "s", "u")
    assert ei.value.kind == "config"
