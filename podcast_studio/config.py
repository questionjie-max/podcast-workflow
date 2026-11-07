"""配置与数据目录管理。

配置文件位于 ~/.podcast-studio/config.json（可用环境变量 PODCAST_STUDIO_HOME 覆盖根目录），
节目与声音等数据默认也放在该目录下。
"""

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "data_dir": None,  # 相对/绝对路径，None 表示 <home>/data
    "server": {"host": "127.0.0.1", "port": 8686, "allow_lan": False},
    "llm": {
        "provider": "mock",  # "mock" 演示模式 | "openai" OpenAI 兼容接口
        "openai": {
            "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "api_key": "",
            "model": "mimo-v2.5-pro",
            "auth_style": "api-key",  # "api-key" 头 | "bearer"
            "ssl_verify": True,
            "extra": {},
        },
    },
    "search": {"provider": "off", "max_rounds": 2, "kid_safe": None,
               "tavily": {"api_key": ""}, "bocha": {"api_key": ""}},
    "asr": {"volc": {"app_id": "", "token": ""}},
    "tts": {
        "volc": {"app_id": "", "token": "", "cluster": "volcano_tts"},
        "ali": {"api_key": "", "model": "cosyvoice-v2"},
        "elevenlabs": {"api_key": "", "model": "eleven_multilingual_v2"},
        "azure": {"api_key": "", "region": "eastasia"},
        "volc_podcast": {"api_key": "", "resource_id": "volc.service_type.10050"},
        "volc_speech": {"resource_id": "seed-tts-2.0", "pace_hint": ""},
    },
    "schedule": {"enabled": False},
    "backup": {"auto": True, "keep": 7},  # 每日自动快照(有节目时),保留份数
    # 用量费用估算(可选):填 0 表示不估算。单位见 engine/usage.estimate_cost
    "pricing": {
        "currency": "CNY",
        "llm_per_million_in": 0,
        "llm_per_million_out": 0,
        "tts_per_kchar": 0,
    },
    "audio": {"bgm_path": "", "bgm_volume": 0.12},  # 垫乐混音槽:用户自备音乐文件
}


def home_dir() -> Path:
    env = os.environ.get("PODCAST_STUDIO_HOME")
    return Path(env).expanduser() if env else Path.home() / ".podcast-studio"


def _deep_merge(base: dict, override: dict) -> dict:
    """深合并:必须深拷贝 base,否则嵌套层会回写污染 DEFAULT_CONFIG。"""
    import copy

    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


_SECRET_NAME_HINTS = ("api_key", "apikey", "token", "secret", "password")


def mask_secret(v: str) -> str:
    """单个密钥脱敏:保留首尾各 4 位便于人工核对,中段打码。"""
    if not v:
        return ""
    return v[:4] + "****" + v[-4:] if len(v) > 8 else "****"


def _mask_tree(node):
    """递归脱敏:凡是字段名命中密钥特征的非空字符串一律打码。

    按「字段名」而非「位置」脱敏——新增 provider 的 Key 自动被保护,
    不再依赖逐处手写清单(漏一处就是明文泄漏)。
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            name = str(k).lower()
            if isinstance(v, str) and v and any(h in name for h in _SECRET_NAME_HINTS):
                out[k] = mask_secret(v)
            else:
                out[k] = _mask_tree(v)
        return out
    if isinstance(node, list):
        return [_mask_tree(x) for x in node]
    return node


class Config:
    def __init__(self, data: dict | None = None):
        self.data = _deep_merge(DEFAULT_CONFIG, data or {})

    # ---- 文件读写 -----------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        """加载配置;主文件损坏时自动尝试备份,绝不静默丢弃用户 Key。

        顺序:config.json → config.json.bak(上次保存的副本)→ 隔离损坏文件
        后以默认配置启动。损坏原件永远保留在 config.json.corrupt-<时间戳>,
        供人工抢救,不做任何删除。
        """
        import time as _time

        from .net import sanitize_proxy_env

        # 先清理 httpx 无法解析的代理变量（如 ALL_PROXY=socks://），
        # 否则所有真实 provider 调用都会在构造客户端时直接失败。
        sanitize_proxy_env()

        path = home_dir() / "config.json"
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except OSError as e:
                # 瞬时 IO 错误(EBUSY/EACCES):降级默认/备份启动,绝不隔离好文件(Q9)
                print(f"⚠️ 配置读取 IO 错误({e}),本次以默认配置启动;原件未动")
                data = {}
                bak = home_dir() / "config.json.bak"
                if bak.exists():
                    try:
                        data = json.loads(bak.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        data = {}
            except json.JSONDecodeError:
                data = {}
                # 主文件损坏:先试上次保存的备份,再不行就以默认配置启动。
                # 损坏原件一律隔离为 config.json.corrupt-<时间戳>,绝不删除。
                bak = home_dir() / "config.json.bak"
                if bak.exists():
                    try:
                        data = json.loads(bak.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        data = {}
                quarantine = home_dir() / f"config.json.corrupt-{_time.strftime('%Y%m%d-%H%M%S')}"
                try:
                    path.replace(quarantine)
                except OSError:
                    pass
        cfg = cls(data)
        # 环境变量优先：MIMO_API_KEY
        env_key = os.environ.get("MIMO_API_KEY")
        if env_key:
            cfg.data["llm"]["openai"]["api_key"] = env_key
        return cfg

    def save(self) -> None:
        """原子写盘:先写临时文件再 os.replace,并把上一份保留为 .bak。

        直接 write_text 曾有两类事故:写一半崩溃留下半截 JSON(load 时
        静默清空全部 Key),以及覆盖后无回滚手段。
        """
        import shutil

        home_dir().mkdir(parents=True, exist_ok=True)
        path = home_dir() / "config.json"
        import uuid as _uuid

        tmp = home_dir() / f"config.{os.getpid()}.{_uuid.uuid4().hex[:6]}.tmp"  # 唯一名,并发保存不互踩(Q9)
        bak = home_dir() / "config.json.bak"
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shutil.copy2(tmp, bak)  # 每次成功保存都留下回滚点(含首次)
        os.replace(tmp, path)
        os.chmod(path, 0o600)  # 内含 API Key,仅本人可读
        os.chmod(bak, 0o600)

    # ---- 目录 ---------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        d = self.data.get("data_dir")
        if d:
            return Path(d).expanduser()
        return home_dir() / "data"

    @property
    def episodes_dir(self) -> Path:
        return self.data_dir / "episodes"

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    def ensure_dirs(self) -> None:
        import os

        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)  # 文稿/音频是用户隐私,目录仅本人可读
        except OSError:
            pass  # 特殊文件系统/权限受限时不阻塞启动

    # ---- 便捷访问 -----------------------------------------------------------
    @property
    def llm(self) -> dict:
        return self.data["llm"]

    @property
    def server(self) -> dict:
        return self.data["server"]

    def masked(self) -> dict:
        """给前端展示用的配置(按字段名递归隐藏各类密钥,见 _mask_tree)。"""
        import copy

        return _mask_tree(copy.deepcopy(self.data))
