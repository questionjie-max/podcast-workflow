"""OpenAI 兼容 LLM 客户端。

一个实现通吃 MiMo / DeepSeek / Qwen / Kimi / OpenAI 等兼容接口。
认证方式可配置：MiMo 用 api-key 头，其他多为 Authorization: Bearer。
"""

import json

import httpx

from .base import LLMProvider


class LLMError(RuntimeError):
    """LLM 调用错误;kind 用于前端给出可行动的提示。

    kind ∈ config | network | auth | rate_limit | http | other
    """

    def __init__(self, msg: str, kind: str = "other"):
        super().__init__(msg)
        self.kind = kind


class OpenAICompatLLM(LLMProvider):
    name = "openai"

    def __init__(self, cfg: dict):
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or ""
        self.auth_style = cfg.get("auth_style", "bearer")
        self.ssl_verify = cfg.get("ssl_verify", True)
        self.extra = cfg.get("extra") or {}
        self.extra_headers = cfg.get("extra_headers") or {}  # 网关特例头(如 OpenCode 会话头)

    def _headers(self) -> dict:
        if self.auth_style == "api-key":
            h = {"api-key": self.api_key}
        else:
            h = {"Authorization": f"Bearer {self.api_key}"}
        h.update(self.extra_headers)
        return h

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
        if not self.base_url:
            raise LLMError("未配置 LLM base_url，请在设置里填写", kind="config")
        if not self.api_key:
            raise LLMError("未配置 API Key，请在设置里填写（或用演示模式）", kind="config")
        if not self.model:
            raise LLMError("未配置模型名称", kind="config")

        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(self.extra)

        # 长文生成走流式:网关/代理对非流式长请求常掐空闲连接(实测本地网关
        # 写长稿 180s 超时),流式的持续数据块可保活;不支持流式时回退非流式。
        # 限流(429)与网络瞬断做指数退避重试(1s/4s),auth/config 不重试
        import time as _time

        for attempt in range(3):  # 首发 + 2 次退避
            try:
                try:
                    return self._chat_stream(url, body, timeout)
                except LLMError as e:
                    if e.kind in ("auth", "config", "rate_limit"):
                        raise
                    return self._chat_plain(url, body, timeout)
            except LLMError as e:
                if e.kind not in ("rate_limit", "network") or attempt == 2:
                    raise
                _time.sleep((1, 4)[attempt])

    def _chat_plain(self, url: str, body: dict, timeout: float = 300) -> str:
        body = {**body, "stream": False}
        try:
            resp = httpx.post(
                url, headers=self._headers(), json=body,
                timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                verify=self.ssl_verify,
            )
        except httpx.ConnectError as e:
            raise LLMError(f"连不上接口地址：{e}", kind="network") from e
        except httpx.TimeoutException as e:
            raise LLMError(f"请求超时（{timeout:.0f}s），接口可能不可用", kind="network") from e
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 请求失败：{e}", kind="network") from e
        return self._parse_response(resp)

    def _chat_stream(self, url: str, body: dict, timeout: float = 300) -> str:
        body = {**body, "stream": True}
        parts: list[str] = []
        try:
            with httpx.stream(
                "POST", url, headers=self._headers(), json=body,
                timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                verify=self.ssl_verify,
            ) as resp:
                if resp.status_code != 200:
                    detail = resp.read().decode("utf-8", "replace")[:300]
                    self._raise_for_status(resp.status_code, detail)
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    try:
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        parts.append(delta)
        except httpx.ConnectError as e:
            raise LLMError(f"连不上接口地址：{e}", kind="network") from e
        except httpx.TimeoutException as e:
            raise LLMError(f"请求超时（{timeout:.0f}s），接口可能不可用", kind="network") from e
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 请求失败：{e}", kind="network") from e
        text = "".join(parts).strip()
        if not text:
            raise LLMError("流式响应为空", kind="other")
        return text

    @staticmethod
    def _raise_for_status(status: int, detail: str) -> None:
        if status in (401, 403):
            raise LLMError(f"认证失败（{status}）：API Key 无效或无权限 — {detail}", kind="auth")
        if status == 404:
            raise LLMError(f"接口或模型不存在（404）：检查 base_url 是否带 /v1、模型名是否正确 — {detail}", kind="http")
        if status == 429:
            raise LLMError(f"触发限流（429），请稍后再试 — {detail}", kind="rate_limit")
        raise LLMError(f"LLM 返回 {status}：{detail}", kind="http")

    def _parse_response(self, resp: httpx.Response) -> str:
        if resp.status_code != 200:
            self._raise_for_status(resp.status_code, resp.text[:300])
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"LLM 响应格式异常：{resp.text[:300]}", kind="other") from e
