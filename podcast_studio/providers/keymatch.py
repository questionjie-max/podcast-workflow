"""粘贴 Key 自动匹配:用户不知道厂商,产品替他认。

原理:API Key 是"万能通行证"——把它拿到每个已知厂商的模型列表接口上
轻量试一次(GET /models,1 个请求),谁认这把 Key 就是哪家;认下的自动
填充 base_url/model 并保存,再做一次真实对话验证。

火山方舟特殊:Ark 的 Key 是 UUID 格式(8-4-4-4-12),且方舟同时是
**豆包播客 TTS** 的凭据(X-Api-Key)。识别为方舟时:
- 写入 tts.volc_podcast.api_key → 双人对谈立即升级豆包官方播客音色
- LLM(方舟 chat)需要模型接入点,无法盲测——如实提示用户在方舟控制台
  复制模型接入点/模型名,不瞎猜
"""

import re
import time

import httpx

# 探测超时与请求预算
_PROBE_TIMEOUT = 8
_ARK_KEY_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# 已知厂商探测表(OpenAI 兼容,GET models 鉴权即验真)
KEY_PROVIDERS: list[dict] = [
    {"id": "deepseek", "name": "DeepSeek(深度求索)",
     "models_url": "https://api.deepseek.com/models",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "auth": "bearer"},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "models_url": "https://api.moonshot.cn/v1/models",
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "auth": "bearer"},
    {"id": "qwen", "name": "阿里通义 Qwen",
     "models_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-max", "auth": "bearer"},
    {"id": "hunyuan", "name": "腾讯混元",
     "models_url": "https://api.hunyuan.cloud.tencent.com/v1/models",
     "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
     "model": "hunyuan-lite", "auth": "bearer"},
    {"id": "openai", "name": "OpenAI",
     "models_url": "https://api.openai.com/v1/models",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "auth": "bearer"},
]

# 火山方舟(豆包):LLM 与播客 TTS 同一把 X-Api-Key;LLM 需要模型名,单独分支处理
ARK_PROVIDER = {
    "id": "ark", "name": "火山方舟(豆包)",
    "chat_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "models_url": "https://ark.cn-beijing.volces.com/api/v3/models",
}

PODCAST_RESOURCE_ID = "volc.service_type.10050"


def _looks_like_ark(key: str) -> bool:
    """方舟 Key 两种形态:ark-前缀(控制台复制)或裸 UUID;用户实测前者。"""
    k = (key or "").strip()
    if k.lower().startswith("ark-"):
        return True
    return bool(_ARK_KEY_RE.match(k))


def _probe_models(provider: dict, key: str) -> tuple[bool, int, str]:
    """GET models:200 且形如模型列表 → 该厂商认这把 Key。"""
    t0 = time.time()
    try:
        r = httpx.get(
            provider["models_url"],
            headers={"Authorization": f"Bearer {key}"},
            timeout=_PROBE_TIMEOUT,
        )
    except httpx.TimeoutException:
        return False, int((time.time() - t0) * 1000), "超时"
    except httpx.HTTPError as e:
        return False, int((time.time() - t0) * 1000), f"{type(e).__name__}"
    ms = int((time.time() - t0) * 1000)
    if r.status_code == 200:
        try:
            data = r.json()
            if isinstance(data.get("data"), list) and data["data"]:
                return True, ms, f"{len(data['data'])} 个模型"
            return False, ms, "200 但响应不是模型列表"
        except ValueError:
            return False, ms, "200 但非 JSON"
    if r.status_code in (401, 403):
        return False, ms, f"认证失败(HTTP {r.status_code})"
    return False, ms, f"HTTP {r.status_code}"


def _probe_speech_podcast(key: str) -> tuple[str, str]:
    """语音技术播客 TTS 探测:区分 Key 无效(401)与服务未开通(403 not granted)。

    返回 (state, info),state ∈ "ok"(鉴权通过,GET 被拒是端点只收 WS 属正常)
    | "pending"(Key 有效但服务未开通) | "invalid"(Key 无效)。
    """
    try:
        r = httpx.get(
            "https://openspeech.bytedance.com/api/v3/sami/podcasttts",
            headers={"X-Api-Key": key, "X-Api-Resource-Id": PODCAST_RESOURCE_ID},
            timeout=_PROBE_TIMEOUT,
        )
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        return "invalid", f"连接失败:{type(e).__name__}"
    body = r.text[:150]
    if r.status_code == 401:
        return "invalid", f"Key 无效(HTTP 401):{body}"
    if r.status_code == 403 and "not granted" in body:
        return "pending", f"Key 有效,但「播客语音合成」未开通:{body}"
    return "ok", f"鉴权通过(HTTP {r.status_code},该端点仅接受 WebSocket 升级)"


def _probe_ark_llm(key: str) -> tuple[str, dict]:
    """方舟:验证 Key(模型目录)并尝试目录里的 LLM 模型。

    返回 (state, {info, model, hint}),state ∈ "ok" | "invalid"(Key 无效)
    | "pending"(Key 有效但账号无可调用模型)。Ark 规则:账号须先开通
    模型或创建推理接入点(ep-xxx)才能调用,Key 有效不代表能调任何模型。
    """
    try:
        r = httpx.get(ARK_PROVIDER["models_url"],
                      headers={"Authorization": f"Bearer {key}"}, timeout=_PROBE_TIMEOUT)
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        return "invalid", {"info": f"方舟连接失败:{type(e).__name__}", "hint": ""}
    if r.status_code in (401, 403):
        return "invalid", {"info": f"Key 无效(HTTP {r.status_code})", "hint": ""}
    if r.status_code != 200:
        return "invalid", {"info": f"HTTP {r.status_code}", "hint": ""}

    try:
        data = r.json().get("data", [])
    except ValueError:
        return "invalid", {"info": "模型目录响应异常", "hint": ""}

    llm = [m for m in data
           if m.get("domain") == "LLM"
           and (m.get("status") or "").lower() not in ("shutdown", "retiring")]
    if not llm:
        return "pending", {"info": f"目录 {len(data)} 个模型里没有可用 LLM",
                           "hint": "请到方舟控制台开通一个对话模型"}

    # 取版本号日期最新的几个优先试
    def version_key(m):
        tail = re.findall(r"(\d{8})$", m["id"])
        return tail[0] if tail else ""

    llm.sort(key=version_key, reverse=True)
    candidates = [m["id"] for m in llm[:3]]
    last = ""
    for mid in candidates:
        try:
            rr = httpx.post(ARK_PROVIDER["chat_url"],
                            headers={"Authorization": f"Bearer {key}"},
                            json={"model": mid,
                                  "messages": [{"role": "user", "content": "收到请只回复两个字母:OK"}],
                                  "max_tokens": 8},
                            timeout=_PROBE_TIMEOUT + 7)
            if rr.status_code == 200:
                return "ok", {"info": f"模型 {mid} 真实对话通过", "model": mid}
            last = f"{mid}:HTTP {rr.status_code}"
        except Exception as e:
            last = f"{mid}:{type(e).__name__}"
    return "pending", {"info": f"Key 有效,但目录里的 LLM 均未开通({last})",
                       "hint": "、".join(candidates[:2])}


def match_key(key: str) -> dict:
    """匹配主循环:逐厂商探测 → 命中即返回配置;全失败时给出可读结论。

    返回 {matched, name, base_url, model, auth, attempts, ark_tts}。
    """
    key = (key or "").strip()
    attempts: list[dict] = []
    if not key:
        return {"matched": None, "attempts": attempts, "advice": "请先粘贴 API Key"}

    ark_suspect = _looks_like_ark(key)
    for provider in KEY_PROVIDERS:
        ok, ms, info = _probe_models(provider, key)
        attempts.append({"provider": provider["name"], "ok": ok, "ms": ms, "info": info})
        if ok:
            from .gateways import _gateway_extra_headers

            return {"matched": provider["id"], "name": provider["name"],
                    "base_url": provider["base_url"], "model": provider["model"],
                    "auth": provider["auth"], "attempts": attempts,
                    "ark_tts": None,
                    "extra_headers": _gateway_extra_headers(provider["base_url"]) or None}

    # 全部失败:方舟 Key 分支——Key 有效性用模型目录验证,再从目录里找 LLM 候选
    if ark_suspect:
        # 语音技术分支:方舟 401 时,UUID Key 很可能是语音控制台的(播客 TTS 凭据)
        speech_state, speech_info = _probe_speech_podcast(key)
        attempts.append({"provider": "语音技术·播客 TTS", "ok": speech_state == "ok",
                         "ms": 0, "info": speech_info})
        if speech_state in ("ok", "pending"):
            return {"matched": "volc_speech" if speech_state == "ok" else "volc_speech_pending",
                    "name": "火山语音(豆包播客 TTS)",
                    "attempts": attempts, "base_url": None, "model": None, "auth": None,
                    "ark_tts": {"api_key": key, "resource_id": PODCAST_RESOURCE_ID},
                    "speech_key_valid": True,
                    "advice": ("✅ 播客 TTS 已接入:双人对谈将使用豆包官方播客音色。"
                               if speech_state == "ok" else
                               "Key 有效,但「播客语音合成」还没在你的账号开通——"
                               "火山引擎控制台 → 语音技术 → 开通管理,开启「播客语音合成」即可,开通后无需重填。")}

        state, ark_state = _probe_ark_llm(key)
        attempts.append({"provider": "火山方舟(豆包)·对话模型",
                         "ok": state == "ok", "ms": 0, "info": ark_state["info"]})
        if state == "ok":
            return {"matched": "ark", "name": ARK_PROVIDER["name"],
                    "base_url": ARK_PROVIDER["chat_url"], "model": ark_state["model"],
                    "auth": "bearer", "attempts": attempts, "ark_tts": None,
                    "advice": f"已自动选中方舟模型 {ark_state['model']},文稿写作可用。"}
        if state == "invalid":
            return {"matched": None, "attempts": attempts,
                    "advice": "方舟 Key 无效:" + ark_state["info"] +
                              "。注意:方舟 Key(ark-开头)与豆包播客 TTS 的 API Key 是两套凭据。"}
        return {"matched": "ark_llm_pending", "name": ARK_PROVIDER["name"],
                "attempts": attempts, "base_url": None, "model": None, "auth": None,
                "ark_tts": None, "ark_key_valid": True, "model_hint": ark_state.get("hint", ""),
                "advice": "方舟 Key 有效,但账号下还没有可调用的对话模型。"
                          "二选一:①方舟控制台「开通管理」里开通任一对话模型;"
                          "②「在线推理」创建推理接入点,把接入点 ID(ep-开头)或模型 ID 粘到下面。"}

    attempts.append({"provider": "火山方舟(豆包)·播客 TTS", "ok": False, "ms": 0,
                     "info": "Key 不是方舟格式,跳过"})
    return {"matched": None, "attempts": attempts,
            "advice": "所有已知厂商都不认这把 Key:检查是否复制完整(前后无空格)、"
                      "key 是否已开通对应服务。如果是网关 Key(如 Magpie),请用"
                      "「本地网关检测」入口。"}
