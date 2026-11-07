"""API 一键直连:内置 LLM 预设 + 本地网关探测(S2)。

- `LLM_PRESETS`:常见供应商的一键填充项,用户只需再填 API Key。
- `detect_local_gateways()`:只读扫描本机 CC Switch 与 Magpie 的配置文件,
  把里面已配好的供应商(base_url + key + 模型)读出来,供服务端一键导入
  本机 config(见 SPRINT.md S2)。

真机侦察结论(2026-10-30,本机实测):
- CC Switch 4.x 把供应商存在 ``~/.cc-switch/cc-switch.db``(SQLite)的
  ``providers`` 表里;第三方 OpenAI 兼容供应商的 ``settings_config`` 列是
  JSON,形如 ``{"name", "baseUrl", "apiKey", "models": [{"id", ...}]}``;
  官方占位行(空 ``env``/``auth``)与 Anthropic 型配置(只有
  ``env.ANTHROPIC_*``)没有顶层 baseUrl/apiKey,自然被过滤。
- Magpie 的供应商配置在 ``~/.config/magpie/providers.json``,结构为
  ``{"providers": [{"id", "name", "key", "chat", "responses", "models": [str]}]}``;
  ``chat`` 字段是 OpenAI Chat Completions 兼容 base_url,优先取它,
  缺失时退回 ``responses``;``models`` 是模型名字符串列表。

安全约定(红线):
- 探测器只读不写;任何读取失败/格式不符一律跳过该项,绝不抛异常;
- `detect_local_gateways` 返回值中的 ``api_key`` 是**明文**,仅供服务端写入
  本机配置;任何打印/日志/汇报/展示必须先用 `mask_key` 脱敏(前4+****+后4);
- 返回项里的 ``key_masked=True`` 是给前端的提示:该网关的 key 需脱敏展示。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

# 内置 LLM 预设:一键填充 base_url/model/auth_style,用户只剩填 key。
# auth_style 与 OpenAICompatLLM 对齐:"bearer"=Authorization 头,"api-key"=api-key 头。
LLM_PRESETS: list[dict[str, str]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek(深度求索)",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "auth_style": "bearer",
    },
    {
        "id": "qwen",
        "name": "Qwen(通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "auth_style": "bearer",
    },
    {
        "id": "kimi",
        "name": "Kimi(月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "auth_style": "bearer",
    },
    {
        "id": "mimo",
        "name": "MiMo(小米,现默认)",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "auth_style": "api-key",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "auth_style": "bearer",
    },
]

# 网关标识 → 中文展示名(给设置页「检测到本地网关」用)
_GATEWAY_LABELS: dict[str, str] = {
    "cc-switch": "CC Switch(本地供应商切换器)",
    "magpie": "Magpie(本地网关)",
}


def mask_key(key: str) -> str:
    """脱敏 API Key:前 4 位 + **** + 后 4 位;过短一律 ****,空值返回空串。

    与 config.Config.masked 的口径一致;任何日志/展示都必须先过这一道。
    """
    if not key:
        return ""
    return key[:4] + "****" + key[-4:] if len(key) > 8 else "****"


def _default_cc_switch_path(home: Path) -> Path:
    """CC Switch 4.x 的供应商数据库位置。"""
    return home / ".cc-switch" / "cc-switch.db"


def _default_magpie_path(home: Path) -> Path:
    """Magpie 的供应商配置位置。"""
    return home / ".config" / "magpie" / "providers.json"


def _read_json(path: Path) -> object | None:
    """读 JSON 文件,任何失败(缺失/损坏/无权限/编码错)返回 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _model_list(models: object, limit: int = 4) -> list[str]:
    """取 models 列表(最多 limit 个),CC Switch 是 [{"id":..}],Magpie 是 [str]。"""
    if not isinstance(models, list):
        return []
    out = []
    for m in models[:limit]:
        if isinstance(m, dict):
            v = str(m.get("id") or "").strip()
        else:
            v = str(m or "").strip()
        if v:
            out.append(v)
    return out


def _gateway_extra_headers(base_url: str) -> dict[str, str]:
    """OpenCode zen 网关要求 x-opencode-session 会话头(实测缺失时 400
    "MissingSessionID");其余网关不需要。会话 ID 每次探测/导入时新生成。"""
    if "opencode" in (base_url or "").lower():
        return {"x-opencode-session": uuid.uuid4().hex}
    return {}


def _first_model(models: object) -> str:
    """取 models 列表第一项的模型名。

    CC Switch 的 models 是 [{"id": ...}, ...],Magpie 的是 ["模型名", ...];
    缺失或格式不符返回空串(由用户导入后自行补)。
    """
    if not isinstance(models, list) or not models:
        return ""
    first = models[0]
    if isinstance(first, dict):
        return str(first.get("id") or "").strip()
    return str(first or "").strip()


def _probe_cc_switch(db_path: Path) -> list[dict[str, str]]:
    """解析 CC Switch 的 SQLite 配置(cc-switch.db),返回供应商预设。

    只读打开(mode=ro);文件不存在/损坏/没有 providers 表/行格式不符
    都返回空列表或跳过该行,绝不抛异常。
    """
    if not db_path.is_file():
        return []
    rows: list[tuple[object, object]] = []
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"  # 只读,探测器不写
        con = sqlite3.connect(uri, uri=True)
        try:
            rows = con.execute(
                "SELECT name, settings_config FROM providers"
            ).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []

    presets: list[dict[str, str]] = []
    for row_name, raw in rows:
        try:
            cfg = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        except ValueError:
            continue
        if not isinstance(cfg, dict):
            continue
        base_url = str(cfg.get("baseUrl") or "").strip()
        api_key = str(cfg.get("apiKey") or "").strip()
        # 官方占位行(空 env/auth)与 Anthropic 型配置没有顶层 baseUrl/apiKey → 跳过
        if not base_url or not api_key:
            continue
        presets.append(
            {
                "name": str(row_name or cfg.get("name") or base_url).strip(),
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "model": _first_model(cfg.get("models")),
                "models": _model_list(cfg.get("models")),
                "extra_headers": _gateway_extra_headers(base_url),
            }
        )
    return presets


def _probe_magpie(path: Path) -> list[dict[str, str]]:
    """解析 Magpie 的 providers.json,返回供应商预设。

    结构不符(非 dict、providers 非列表、条目缺 chat/responses 或 key)
    一律跳过;base_url 优先取 chat 字段,缺失时退回 responses。
    """
    data = _read_json(path)
    if not isinstance(data, dict):
        return []
    providers = data.get("providers")
    if not isinstance(providers, list):
        return []

    presets: list[dict[str, str]] = []
    for item in providers:
        if not isinstance(item, dict):
            continue
        base_url = str(item.get("chat") or item.get("responses") or "").strip()
        api_key = str(item.get("key") or "").strip()
        if not base_url or not api_key:
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        presets.append(
            {
                "name": name or base_url,
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
                "model": _first_model(item.get("models")),
                "models": _model_list(item.get("models")),
                "extra_headers": _gateway_extra_headers(base_url),
            }
        )
    return presets


def detect_local_gateways(
    cc_switch_path: str | Path | None = None,
    magpie_path: str | Path | None = None,
    home: str | Path | None = None,
) -> list[dict]:
    """探测本机 CC Switch 与 Magpie 网关已配置的供应商(只读,失败即跳过)。

    参数(便于测试注入假 HOME,绝不读取真实用户配置以外的路径):
    - ``cc_switch_path``:CC Switch 配置文件路径;缺省从 home 推导
      ``<home>/.cc-switch/cc-switch.db``
    - ``magpie_path``:Magpie 配置文件路径;缺省从 home 推导
      ``<home>/.config/magpie/providers.json``
    - ``home``:推导默认路径的用户目录,缺省 ``Path.home()``

    返回::

        [
            {
                "gateway": "cc-switch" | "magpie",
                "name": "<中文展示名>",
                "key_masked": True,   # 提示前端:key 需脱敏展示
                "presets": [          # api_key 为明文,仅供服务端导入写配置
                    {"name", "base_url", "api_key", "model"},
                ],
            },
        ]

    某个网关文件缺失/损坏/格式不符 → 跳过该项;两者都探测不到 → 空列表。
    """
    base = Path(home).expanduser() if home is not None else Path.home()
    cc_path = (
        Path(cc_switch_path) if cc_switch_path is not None
        else _default_cc_switch_path(base)
    )
    mg_path = (
        Path(magpie_path) if magpie_path is not None
        else _default_magpie_path(base)
    )

    gateways: list[dict] = []
    for gateway, path, probe in (
        ("cc-switch", cc_path, _probe_cc_switch),
        ("magpie", mg_path, _probe_magpie),
    ):
        try:
            presets = probe(path)
        except Exception:  # 兜底红线:探测器自身绝不让调用方崩
            presets = []
        if not presets:
            continue
        gateways.append(
            {
                "gateway": gateway,
                "name": _GATEWAY_LABELS[gateway],
                "presets": presets,
                "key_masked": True,
            }
        )
    return gateways
