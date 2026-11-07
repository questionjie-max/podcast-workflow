"""粘贴 Key 自动匹配:厂商探测/方舟分支/自动保存(respx mock,不联网)。"""

import httpx
import respx

from podcast_studio.providers.keymatch import KEY_PROVIDERS, match_key


def test_providers_table_has_expected_families():
    ids = {p["id"] for p in KEY_PROVIDERS}
    assert {"deepseek", "moonshot", "qwen", "hunyuan", "openai"} <= ids


@respx.mock
def test_match_deepseek_by_models():
    respx.get("https://api.deepseek.com/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "deepseek-chat"}]}))
    r = match_key("sk-test-deepseek")
    assert r["matched"] == "deepseek" and r["name"].startswith("DeepSeek")
    assert r["base_url"] == "https://api.deepseek.com/v1"
    assert r["attempts"][0]["ok"] is True


@respx.mock
def test_match_skips_401_and_finds_later():
    for p in KEY_PROVIDERS[:1]:
        respx.get(p["models_url"]).mock(return_value=httpx.Response(401, text="no"))
    ok_provider = KEY_PROVIDERS[1]
    respx.get(ok_provider["models_url"]).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m"}]}))
    r = match_key("sk-kimi")
    assert r["matched"] == "moonshot"
    assert r["attempts"][0]["ok"] is False


def test_non_ark_key_never_matches_ark():
    r = match_key("sk-not-an-ark-key")
    assert r["matched"] is None


def test_ark_prefixed_key_detected_and_pending():
    """ark- 前缀 Key:目录 200 但无可用 LLM → pending 引导(用户真机场景)。"""
    ark_uuid = "0b5f3e2a-11aa-44bb-88cc-123456789abc"
    with respx.mock:
        respx.get("https://ark.cn-beijing.volces.com/api/v3/models").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "deepseek-v4-pro-260425", "domain": "LLM", "status": "Retiring"}]}))
        respx.post("https://ark.cn-beijing.volces.com/api/v3/chat/completions").mock(
            return_value=httpx.Response(404, json={"error": {"code": "InvalidEndpointOrModel.NotFound"}}))
        respx.get("https://openspeech.bytedance.com/api/v3/sami/podcasttts").mock(
            return_value=httpx.Response(401, json={"error": "Invalid X-Api-Key"}))
        for p in KEY_PROVIDERS:
            respx.get(p["models_url"]).mock(return_value=httpx.Response(401, text="no"))
        r = match_key("ark-" + ark_uuid)
    assert r["matched"] == "ark_llm_pending"
    assert r["ark_key_valid"] is True
    assert "开通" in r["advice"] or "接入点" in r["advice"]


def test_ark_key_with_active_llm_auto_saves():
    """方舟 Key + 目录里有已开通 LLM → 自动选中并返回配置。"""
    ark_uuid = "0b5f3e2a-11aa-44bb-88cc-123456789abc"
    with respx.mock:
        respx.get("https://ark.cn-beijing.volces.com/api/v3/models").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "deepseek-v4-1-flash-261017", "domain": "LLM", "status": None},
                {"id": "deepseek-v4-pro-260425", "domain": "LLM", "status": "Retiring"}]}))
        respx.post("https://ark.cn-beijing.volces.com/api/v3/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}))
        respx.get("https://openspeech.bytedance.com/api/v3/sami/podcasttts").mock(
            return_value=httpx.Response(401, json={"error": "Invalid X-Api-Key"}))
        for p in KEY_PROVIDERS:
            respx.get(p["models_url"]).mock(return_value=httpx.Response(401, text="no"))
        r = match_key("ark-" + ark_uuid)
    assert r["matched"] == "ark"
    assert r["model"] == "deepseek-v4-1-flash-261017"  # 版本最新的优先


def test_ark_key_models_401_is_invalid():
    with respx.mock:
        respx.get("https://ark.cn-beijing.volces.com/api/v3/models").mock(
            return_value=httpx.Response(401, text="no"))
        respx.get("https://openspeech.bytedance.com/api/v3/sami/podcasttts").mock(
            return_value=httpx.Response(401, json={"error": "Invalid X-Api-Key"}))
        for p in KEY_PROVIDERS:
            respx.get(p["models_url"]).mock(return_value=httpx.Response(401, text="no"))
        r = match_key("ark-00000000-0000-0000-0000-000000000000")
    assert r["matched"] is None


def test_empty_key_short_circuit():
    r = match_key("")
    assert r["matched"] is None and r["advice"]


def test_speech_key_pending_enables_podcast():
    """语音技术 UUID Key:方舟不认(401)+ 播客 403 未开通 → volc_speech_pending(用户真实场景)。"""
    with respx.mock:
        respx.get("https://openspeech.bytedance.com/api/v3/sami/podcasttts").mock(
            return_value=httpx.Response(403, json={"error": "[resource_id=10050] requested resource not granted"}))
        respx.get("https://ark.cn-beijing.volces.com/api/v3/models").mock(
            return_value=httpx.Response(401, text="no"))
        for p in KEY_PROVIDERS:
            respx.get(p["models_url"]).mock(return_value=httpx.Response(401, text="no"))
        r = match_key("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    assert r["matched"] == "volc_speech_pending"
    assert r["ark_tts"]["api_key"] == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert "开通" in r["advice"]
