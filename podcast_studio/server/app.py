"""FastAPI 服务：静态页面 + REST API。

长任务（写稿/合成等）提交到线程池，前端轮询节目状态。
"""

import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import Config
from ..engine import audio
from ..engine.pipeline import Episode, list_episodes, load_episode
from ..logging_setup import get_logger, setup_logging
from ..providers import voices as voice_reg
from ..providers.keymatch import match_key as keys_match_engine
from ..providers.llm.base import make_llm
from ..styles import PROFILES
from .uploads import (
    ASR_MAX_SECONDS,
    ASR_MIN_SECONDS,
    CLONE_MAX_SECONDS,
    CLONE_MIN_SECONDS,
    check_duration,
    save_upload,
)


def import_builtin_demos(cfg: Config | None = None) -> int:
    """把随包示例期(static/demo/)导入用户节目库,origin 强制 demo。

    已存在同 id 则跳过(老用户/重复启动);导入失败清理干净不阻塞启动。
    示例期与真实节目的隔离规则见 R0 决议2:不进 RSS、只读、不参与时长校准。
    """
    import shutil

    c = cfg or _cfg()
    demo_src = STATIC_DIR / "demo"
    if not demo_src.is_dir():
        return 0
    n = 0
    for src in sorted(demo_src.iterdir()):
        if not src.is_dir() or not (src / "state.json").exists():
            continue
        dst = c.episodes_dir / src.name
        if dst.exists():
            continue
        try:
            shutil.copytree(src, dst)
            ep = load_episode(c, src.name)
            ep.state["origin"] = "demo"
            ep.save()
            n += 1
        except Exception:
            shutil.rmtree(dst, ignore_errors=True)
    return n


logger = get_logger("server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _ALLOW_LAN
    cfg = _cfg()
    try:
        setup_logging(cfg)
    except Exception:  # 日志不可写不应阻塞服务启动
        pass
    _ALLOW_LAN = bool((cfg.data.get("server") or {}).get("allow_lan"))
    logger.info(
        "启动 v%s host=%s allow_lan=%s data=%s",
        __version__, cfg.server.get("host"), _ALLOW_LAN, cfg.data_dir,
    )
    try:
        from ..backup import maybe_auto_backup

        snapshot = maybe_auto_backup(cfg)
        if snapshot:
            logger.info("自动备份完成：%s", snapshot)
    except Exception:
        logger.exception("自动备份失败（不阻塞启动）")
    try:
        swept = sweep_stale_episodes(cfg)
        if swept:
            logger.warning("启动清扫：%d 期中断节目已复位为可重试 error", len(swept))
    except Exception:
        logger.exception("启动清扫失败（不阻塞启动）")
    try:
        imported = import_builtin_demos(cfg)
        if imported:
            logger.info("已导入 %d 期随包示例节目", imported)
    except Exception:
        logger.exception("示例导入失败（不阻塞启动）")
    yield
    logger.info("服务退出")


app = FastAPI(title="播客工坊 Podcast Studio", version=__version__, lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ps-job")
_busy: set[str] = set()


def _cfg() -> Config:
    cfg = Config.load()
    cfg.ensure_dirs()
    return cfg


def _ensure_writable(ep: Episode) -> None:
    """示例期(origin=demo)服务端只读:写操作一律 409(Q5:此前仅前端隐藏按钮)。"""
    if (ep.state or {}).get("origin") == "demo":
        raise HTTPException(409, "示例节目是只读的——去「新建节目」做你自己的第一期吧")


def _get_ep(ep_id: str) -> Episode:
    try:
        return load_episode(_cfg(), ep_id)
    except FileNotFoundError:
        raise HTTPException(404, f"节目不存在：{ep_id}")


_busy_lock = __import__("threading").Lock()


def _submit(ep: Episode, fn) -> None:
    """提交后台任务；同一节目同时只允许一个任务。

    「查 _busy + add」用锁原子化(Q4:check-then-add 竞态曾留下双合成窗口)。
    """
    with _busy_lock:
        if ep.id in _busy:
            raise HTTPException(409, "该节目正在执行任务，请稍候")
        _busy.add(ep.id)

    def runner():
        try:
            fn()
        finally:
            _busy.discard(ep.id)

    _pool.submit(runner)


# ---- DNS rebinding 防护:浏览器可被恶意网页诱导访问 127.0.0.1,Host 必须像本机 ----
_ALLOW_LAN = False  # 启动时读 server.allow_lan(Q10:默认仅本机,私网放行需显式开启)


def _host_allowed(host: str) -> bool:
    import ipaddress

    if not host:
        return True
    h = host.strip().lower()
    if h.startswith("["):  # IPv6 字面量:[::1]:8686 → ::1(Q2:此前 split(":") 撕碎导致一律 403)
        h = h[1:h.index("]")] if "]" in h else h[1:].split(":")[0]
    else:
        h = h.split(":")[0]
    if h in {"localhost", "testserver"} or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback:
            return True
        return bool(_ALLOW_LAN) and ip.is_private  # Q10:局域网访问需显式 server.allow_lan
    except ValueError:
        return False


@app.middleware("http")
async def _host_guard(request: Request, call_next):
    if not _host_allowed(request.headers.get("host") or ""):
        logger.warning(
            "已拒绝非本机 Host 访问: host=%s path=%s",
            request.headers.get("host"), request.url.path,
        )
        return JSONResponse({"detail": "播客工坊是本机服务,仅接受本机访问"}, status_code=403)
    return await call_next(request)


# ---- 崩溃残留清扫 -------------------------------------------------------------
_STALE_STATE_TO_STEP = {
    "researching": "research",
    "scripting": "script",
    "compliance": "compliance",
    "synthesizing": "synthesis",
}


def sweep_stale_episodes(cfg: Config | None = None) -> list[str]:
    """服务启动时把「执行中」状态的崩溃残留复位为可重试的 error。

    流水线随进程一起死;重启后残存的 synthesizing/scripting 既不能 approve
    也不能 retry(409),用户永久卡死。这里统一标记为 error + failed_step,
    让既有 /retry 通道接手。
    """
    c = cfg or _cfg()
    swept: list[str] = []
    import time as _time

    try:
        rows = list_episodes(c)
    except Exception:
        return swept
    for row in rows:
        step = _STALE_STATE_TO_STEP.get((row or {}).get("state") or "")
        if not step:
            continue
        try:
            ep = load_episode(c, row["id"])
        except Exception:  # noqa: S112 — 单期损坏不应阻塞整体清扫
            continue
        # 最近 90 秒内有落盘动作 → 可能是另一实例/调度线程在跑,不动它(Q8)
        try:
            if _time.time() - (c.episodes_dir / row["id"] / "state.json").stat().st_mtime < 90:
                continue
        except OSError:
            pass
        ep.state["state"] = "error"
        ep.state["failed_step"] = step
        ep.state["error"] = "上次运行被中断(服务重启或退出),点「重试」从失败步骤继续"
        ep.save()
        swept.append(row["id"])
    return swept


# ---- 页面 ------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
def index():
    """首页:服务端注入版本号(单一事实源),静态资源缓存随版本失效。

    版本号曾经散落在 pyproject/__init__/index.html×4/app.js 里靠脚本同步,
    一次 sed 事故把 regex 字面量写进了 HTML,首页全资产 404(死页)。
    现在 HTML 里只写 __PS_VERSION__ 占位符,由后端注入,杜绝再犯。
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return Response(
        html.replace("__PS_VERSION__", __version__),
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


# ---- 元信息 ------------------------------------------------------------------
@app.get("/api/health")
def health():
    cfg = _cfg()
    return {
        "version": __version__,
        "ffmpeg": audio.ffmpeg_available(),
        "llm_provider": cfg.llm.get("provider"),
        "onboarded": bool(cfg.data.get("onboarded")),
        # 不返回 data_dir 绝对路径（含用户名等本机信息）；需要看路径用 CLI doctor
        "data_dir_hint": "~/.podcast-studio/data",
        "allow_lan": bool(_ALLOW_LAN),
    }


@app.get("/api/profiles")
def profiles():
    return [
        {
            "id": p["id"],
            "emoji": p["emoji"],
            "name": p["name"],
            "desc": p["desc"],
            "default_minutes": p["default_minutes"],
            "moods": p.get("moods", []),
        }
        for p in PROFILES.values()
    ]


# ---- 配置 ------------------------------------------------------------------
@app.post("/api/config/audio")
def set_audio_config(body: dict):
    """垫乐混音槽配置:bgm_path(本地音乐文件路径)+ bgm_volume(0-0.5)。"""
    body = body or {}
    cfg = _cfg()
    audio_cfg = cfg.data.setdefault("audio", {})
    path = str(body.get("bgm_path") or "").strip()
    if path:
        from pathlib import Path as _P

        if not _P(path).expanduser().exists():
            raise HTTPException(400, f"音乐文件不存在:{path}")
    audio_cfg["bgm_path"] = path
    try:
        vol = float(body.get("bgm_volume", audio_cfg.get("bgm_volume", 0.12)))
    except (TypeError, ValueError):
        raise HTTPException(400, "bgm_volume 必须是数字")
    audio_cfg["bgm_volume"] = max(0.0, min(0.5, vol))
    cfg.save()
    return {"ok": True, "audio": audio_cfg}


@app.post("/api/config/pricing")
def set_pricing(body: dict):
    """用量费用估算定价（可选）。全 0 表示只统计字符、不估算金额。"""
    cfg = _cfg()
    pricing = cfg.data.setdefault("pricing", {})
    currency = str(body.get("currency") or pricing.get("currency") or "CNY").strip()[:8]
    pricing["currency"] = currency or "CNY"
    for key, label in (
        ("llm_per_million_in", "输入 token 单价"),
        ("llm_per_million_out", "输出 token 单价"),
        ("tts_per_kchar", "语音每千字单价"),
    ):
        if key not in body:
            continue
        raw = body.get(key)
        if raw in (None, ""):
            pricing[key] = 0
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{label}必须是数字")
        if value < 0 or value > 100000:
            raise HTTPException(400, f"{label}超出合理范围（0-100000）")
        pricing[key] = value
    cfg.save()
    return {"ok": True, "pricing": pricing}


@app.get("/api/config")
def get_config():
    return _cfg().masked()


@app.post("/api/config")
def set_config(body: dict):
    cfg = _cfg()
    llm = body.get("llm") or {}
    cur = cfg.llm
    if "provider" in llm:
        cur["provider"] = llm["provider"]
    oa = llm.get("openai") or {}
    for k in ("base_url", "model", "auth_style"):
        if k in oa:
            cur["openai"][k] = oa[k].strip() if isinstance(oa[k], str) else oa[k]
    if "ssl_verify" in oa:
        cur["openai"]["ssl_verify"] = bool(oa["ssl_verify"])
    # api_key:前端传 "****" 表示未修改
    key = oa.get("api_key")
    if key is not None and "****" not in key:
        cur["openai"]["api_key"] = key.strip()
    # TTS 密钥(火山/阿里),同样支持 "****" 占位
    tts_in = body.get("tts") or {}
    tts = cfg.data.setdefault("tts", {})
    if "volc" in tts_in:
        v = tts.setdefault("volc", {})
        for k in ("app_id", "cluster"):
            if k in tts_in["volc"]:
                v[k] = tts_in["volc"][k].strip() if isinstance(tts_in["volc"][k], str) else tts_in["volc"][k]
        token = tts_in["volc"].get("token")
        if token is not None and "****" not in token:
            v["token"] = token.strip()
    if "ali" in tts_in:
        a = tts.setdefault("ali", {})
        if "model" in tts_in["ali"]:
            a["model"] = tts_in["ali"]["model"].strip()
        akey = tts_in["ali"].get("api_key")
        if akey is not None and "****" not in akey:
            a["api_key"] = akey.strip()
    if "volc_podcast" in tts_in:
        vp = tts.setdefault("volc_podcast", {})
        if "resource_id" in tts_in["volc_podcast"]:
            vp["resource_id"] = tts_in["volc_podcast"]["resource_id"].strip()
        vkey = tts_in["volc_podcast"].get("api_key")
        if vkey is not None and "****" not in vkey:
            vp["api_key"] = vkey.strip()
    if "azure" in tts_in:
        az = tts.setdefault("azure", {})
        if "region" in tts_in["azure"]:
            az["region"] = tts_in["azure"]["region"].strip()
        azkey = tts_in["azure"].get("api_key")
        if azkey is not None and "****" not in azkey:
            az["api_key"] = azkey.strip()
    if "elevenlabs" in tts_in:
        el = tts.setdefault("elevenlabs", {})
        if "model" in tts_in["elevenlabs"]:
            el["model"] = tts_in["elevenlabs"]["model"].strip()
        ekey = tts_in["elevenlabs"].get("api_key")
        if ekey is not None and "****" not in ekey:
            el["api_key"] = ekey.strip()
    # 搜索配置
    if "search" in body and isinstance(body["search"], dict):
        sc = body["search"]
        cur_search = cfg.data.setdefault("search", {})
        if "provider" in sc:
            cur_search["provider"] = sc["provider"]
        for p_name in ("tavily", "bocha"):
            if p_name in sc and isinstance(sc[p_name], dict):
                k = sc[p_name].get("api_key")
                if k is not None and "****" not in k:
                    cur_search.setdefault(p_name, {})["api_key"] = k.strip()

    # 定时生成配置
    if "schedule" in body and isinstance(body["schedule"], dict):
        cur_sched = cfg.data.setdefault("schedule", {})
        from .scheduler import norm_hhmm

        for k in ("enabled", "time", "style", "voice_id", "minutes", "mode", "topics", "material"):
            if k in body["schedule"]:
                cur_sched[k] = body["schedule"][k]
        if "time" in body["schedule"]:
            cur_sched["time"] = norm_hhmm(cur_sched["time"]) or "20:30"

    # ASR 配置(火山豆包语音识别)
    if "asr" in body and isinstance(body["asr"], dict):
        av = body["asr"].get("volc") or {}
        cur_asr = cfg.data.setdefault("asr", {}).setdefault("volc", {})
        if "app_id" in av:
            cur_asr["app_id"] = av["app_id"].strip()
        token = av.get("token")
        if token is not None and "****" not in token:
            cur_asr["token"] = token.strip()

    # 首次运行向导:保存过配置即视为已引导
    if body.get("onboarded"):
        cfg.data["onboarded"] = True
    cfg.save()
    return {"ok": True, "config": cfg.masked()}


# ---- LLM 连接测试 -----------------------------------------------------------
@app.post("/api/llm/test")
def test_llm(body: dict | None = None):
    """用已保存配置(或 body 里传入的临时配置)发一个最小请求,返回结构化结果。"""
    import time as _time

    cfg = _cfg()
    llm_cfg = cfg.llm
    if body and isinstance(body.get("llm"), dict):
        import copy

        llm_cfg = copy.deepcopy(llm_cfg)
        incoming = body["llm"]
        if "provider" in incoming:
            llm_cfg["provider"] = incoming["provider"]
        oa = incoming.get("openai") or {}
        for k in ("base_url", "model", "auth_style"):
            if oa.get(k):
                llm_cfg.setdefault("openai", {})[k] = oa[k]
        key = oa.get("api_key")
        if key and "****" not in key:
            llm_cfg.setdefault("openai", {})["api_key"] = key

    llm = make_llm(llm_cfg)
    if getattr(llm, "name", "") == "mock":
        return {"ok": True, "mock": True, "message": "演示模式无需测试，合成走真实 edge-tts。"}
    t0 = _time.time()
    try:
        reply = llm.chat(
            task="test",
            system="你是连通性探测器。",
            user="收到请只回复两个字母:OK",
            temperature=0,
            max_tokens=16,
        )
        return {
            "ok": True,
            "model": llm_cfg.get("openai", {}).get("model", ""),
            "latency_ms": int((_time.time() - t0) * 1000),
            "reply": (reply or "").strip()[:50],
        }
    except Exception as e:

        return {"ok": False, "kind": getattr(e, "kind", "other"), "error": str(e)}


# ---- 声音 ------------------------------------------------------------------
@app.get("/api/voices")
async def list_voices(request: Request):
    cfg = _cfg()
    fmt = {"solo": "solo", "dual": "dual"}.get(request.query_params.get("format"))
    voices = await voice_reg.list_builtin_voices(cfg, format=fmt)
    clones = [
        {"id": c["id"], "name": c["name"], "selector": f"clone:{c['id']}", "created": c["created"]}
        for c in voice_reg.list_clones(cfg)
    ]
    return {"builtin": voices["builtin"], "locked": voices.get("locked", []),
            "clones": clones}


@app.post("/api/keys/ark-save")
def ark_save(body: dict):
    """方舟 Key + 模型接入点/模型 ID:真实对话验证通过后保存为写作引擎。"""
    import httpx as _httpx

    key = (body.get("key") or "").strip()
    model = (body.get("model") or "").strip()
    if not key or not model:
        raise HTTPException(400, "需要 Key 和模型接入点 ID(ep-开头)或模型 ID")
    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    try:
        r = _httpx.post(url, headers={"Authorization": f"Bearer {key}"},
                        json={"model": model,
                              "messages": [{"role": "user", "content": "收到请只回复两个字母:OK"}],
                              "max_tokens": 8}, timeout=30)
    except _httpx.TimeoutException:
        raise HTTPException(400, "验证超时,请重试")
    except _httpx.HTTPError as e:
        raise HTTPException(400, f"连接失败:{e}")
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:150])
        except ValueError:
            msg = r.text[:150]
        raise HTTPException(400, f"验证失败(HTTP {r.status_code}):{msg}")

    cfg = _cfg()
    cfg.llm["provider"] = "openai"
    cfg.llm["openai"].update({
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": model, "api_key": key, "auth_style": "bearer",
    })
    cfg.save()
    return {"ok": True, "model": model, "message": "已连接,可以生产了!"}


@app.post("/api/keys/match")
def keys_match(body: dict):
    """粘贴 Key 自动匹配:拿 Key 逐厂商探测,认下即自动保存(LLM;方舟 Key 则接播客 TTS)。"""
    key = (body.get("key") or "").strip()
    result = keys_match_engine(key)
    if result.get("matched") in ("ark_llm_pending", "volc_speech", "volc_speech_pending") \
            and result.get("ark_tts"):
        fresh = _cfg()
        fresh.data.setdefault("tts", {})["volc_podcast"] = {
            "api_key": result["ark_tts"]["api_key"],
            "resource_id": result["ark_tts"]["resource_id"],
        }
        fresh.save()
        result["ark_tts_saved"] = True
    elif result.get("matched") and result.get("base_url"):
        fresh = _cfg()
        fresh.llm["provider"] = "openai"
        fresh.llm["openai"].update({
            "base_url": result["base_url"], "model": result["model"],
            "api_key": key, "auth_style": result.get("auth", "bearer"),
        })
        if result.get("extra_headers"):
            fresh.llm["openai"]["extra_headers"] = result["extra_headers"]
        fresh.save()
        result["llm_saved"] = True
    result["config_masked"] = _cfg().masked()
    return result


@app.post("/api/llm/autoconnect")
def llm_autoconnect():
    """自动连线修复 loop:收集所有候选(已存配置 + 本机网关预设×模型),逐个探测,
    第一个能真实对话的写入配置。返回完整尝试记录,失败原因逐条可见。"""
    import time as _time

    import httpx

    def mask(k: str) -> str:
        return (k[:4] + "****" + k[-4:]) if k and len(k) > 8 else ("(空)" if not k else "****")

    cfg = _cfg()
    cur = cfg.llm.get("openai", {})
    candidates: list[dict] = []
    if cur.get("base_url") and cur.get("api_key"):
        candidates.append({"name": "当前已存配置", "base_url": cur["base_url"],
                           "api_key": cur["api_key"], "model": cur.get("model") or "",
                           "auth_style": cur.get("auth_style", "bearer"),
                           "extra_headers": cur.get("extra_headers") or {}})
    try:
        from ..providers.gateways import detect_local_gateways

        for g in detect_local_gateways():
            for p in g["presets"]:
                tried = []
                if p.get("model"):
                    tried.append(p["model"])
                for m in (p.get("models") or []):
                    if m not in tried:
                        tried.append(m)
                for m in (tried or [""])[:2]:  # 每供应商最多试 2 个模型,控总时长
                    candidates.append({
                        "name": f"{g['name']}·{p['name']}" + (f"({m})" if m else "(无模型名)"),
                        "base_url": p["base_url"], "api_key": p["api_key"], "model": m,
                        "auth_style": "bearer", "extra_headers": p.get("extra_headers") or {},
                    })
    except Exception as e:
        attempts_err = f"网关探测失败:{e}"
    else:
        attempts_err = None

    def probe(c: dict) -> tuple[bool, int, str]:
        url = c["base_url"].rstrip("/") + "/chat/completions"
        headers = ({"api-key": c["api_key"]} if c["auth_style"] == "api-key"
                   else {"Authorization": f"Bearer {c['api_key']}"})
        headers.update(c.get("extra_headers") or {})
        body = {"model": c["model"] or "default",
                "messages": [{"role": "user", "content": "收到请只回复两个字母:OK"}],
                "max_tokens": 8, "stream": False}
        t0 = _time.time()
        for attempt in (1, 2):  # 超时重试一次:本地网关常有冷启动
            try:
                r = httpx.post(url, headers=headers, json=body, timeout=20)
                ms = int((_time.time() - t0) * 1000)
                if r.status_code == 200:
                    try:
                        txt = r.json()["choices"][0]["message"]["content"]
                    except Exception:
                        return False, ms, f"200 但响应格式异常:{r.text[:100]!r}"
                    return True, ms, f"真实回复:{(txt or '').strip()[:24]!r}"
                if r.status_code in (401, 403):
                    return False, ms, f"认证失败(HTTP {r.status_code}),key 可能不对"
                return False, ms, f"HTTP {r.status_code}:{r.text[:100]!r}"
            except httpx.TimeoutException:
                if attempt == 2:
                    return False, int((_time.time() - t0) * 1000), "超时(重试后仍超时,本地网关可能没在运行)"
                continue
            except httpx.ConnectError:
                return False, int((_time.time() - t0) * 1000), "连接被拒(该地址没有服务在跑)"
            except Exception as e:
                return False, int((_time.time() - t0) * 1000), f"{type(e).__name__}:{str(e)[:80]}"

    attempts: list[dict] = []
    if attempts_err:
        attempts.append({"name": "网关探测", "ok": False, "ms": 0, "info": attempts_err})
    chosen = None
    for c in candidates[:10]:
        ok, ms, info = probe(c)
        attempts.append({"name": c["name"], "model": c["model"], "base_url": c["base_url"],
                         "ok": ok, "ms": ms, "info": info})
        if ok:
            chosen = c
            break

    if not chosen:
        return {"fixed": False, "attempts": attempts,
                "advice": "所有候选都失败。最常见原因:①本地网关(Magpie/CC Switch)没有在运行;"
                          "②网关里的供应商没配模型名;③key 失效。逐条看上方失败原因。"}

    cur.update({
        "base_url": chosen["base_url"], "api_key": chosen["api_key"],
        "model": chosen["model"], "auth_style": chosen["auth_style"],
        "extra_headers": chosen.get("extra_headers") or {},
    })
    cfg.llm["provider"] = "openai"
    cfg.save()
    return {
        "fixed": True,
        "chosen": {"name": chosen["name"], "base_url": chosen["base_url"],
                   "model": chosen["model"], "key_masked": mask(chosen["api_key"])},
        "attempts": attempts,
    }


@app.get("/api/structures")
def structures(style: str = "bedtime"):
    from ..engine import structures as st

    return [{"id": t["id"], "name": t["name"], "desc": t.get("desc", "")}
            for t in st.templates_for(style)]


@app.get("/api/llm/presets")
def llm_presets():
    """内置写作引擎预设(一键填充 base_url/model/auth)。"""
    from ..providers.gateways import LLM_PRESETS

    return LLM_PRESETS


@app.get("/api/gateways")
def gateways():
    """探测本机 CC Switch / Magpie 网关已配置的供应商。

    响应里的 Key 一律脱敏为 key_preview——明文 Key 永远不出服务端,
    导入动作走 POST /api/gateways/import 由后端写配置。
    """
    try:
        from ..config import mask_secret
        from ..providers.gateways import detect_local_gateways

        out = []
        for g in detect_local_gateways():
            presets = []
            for p in g.get("presets", []):
                q = dict(p)
                q["key_preview"] = mask_secret(q.pop("api_key", "") or "")
                presets.append(q)
            out.append({**g, "presets": presets})
        return {"gateways": out}
    except Exception as e:
        return {"gateways": [], "error": str(e)}


@app.post("/api/gateways/import")
def gateways_import(body: dict):
    """把网关里的某家供应商直接写入 LLM 配置(明文 Key 只在服务端流转)。"""
    from ..providers.gateways import detect_local_gateways

    body = body or {}
    gw, name = str(body.get("gateway") or ""), str(body.get("name") or "")
    if not gw or not name:
        raise HTTPException(400, "缺少 gateway 或 name 参数")
    for g in detect_local_gateways():
        if g.get("gateway") != gw:
            continue
        for p in g.get("presets", []):
            if p.get("name") != name:
                continue
            if not p.get("api_key"):
                raise HTTPException(400, "该供应商没有配置 Key,无法导入")
            cfg = _cfg()
            oa = cfg.data.setdefault("llm", {}).setdefault("openai", {})
            oa["base_url"] = p.get("base_url") or oa.get("base_url", "")
            oa["model"] = p.get("model") or oa.get("model", "")
            oa["api_key"] = p["api_key"]
            oa["auth_style"] = "bearer"
            cfg.data["llm"]["provider"] = "openai"
            cfg.save()
            return {"ok": True, "llm": cfg.masked()["llm"]}
    raise HTTPException(404, f"网关里没有这家供应商:{gw} / {name}")


@app.post("/api/asr/transcribe")
async def asr_transcribe(file: UploadFile = File(...)):
    """语音转文字(火山豆包一句话识别):新建页「🎙️ 说一句」用。"""
    from ..providers.asr import ASRError, VolcASR

    cfg = _cfg()
    volc = (cfg.data.get("asr") or {}).get("volc") or {}
    if not (volc.get("app_id") and volc.get("token")):
        raise HTTPException(400, "未配置语音识别:设置 → TTS 接口配置 → 语音识别(豆包 ASR)填 app_id 与 token")
    suffix = Path(file.filename or "rec.webm").suffix or ".webm"
    tmp_path, _size = await save_upload(file, suffix)
    try:
        check_duration(
            tmp_path, minimum=ASR_MIN_SECONDS, maximum=ASR_MAX_SECONDS, label="录音"
        )
        text = await VolcASR().transcribe(
            tmp_path, app_id=volc["app_id"], token=volc["token"],
            cluster=volc.get("cluster", "volcano_asr"),
        )
        return {"ok": True, "text": text}
    except ASRError as e:
        kind = getattr(e, "kind", "other")
        hints = {
            "config": "请先在设置里配置豆包 ASR 的 app_id 与 token",
            "auth": "认证失败:检查 app_id / token 是否正确",
            "audio": str(e),
            "network": "网络不通,检查本机网络",
        }
        raise HTTPException(400, hints.get(kind, str(e)))
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/voices/preview")
async def preview_voice(body: dict):
    """试听内置音色(免费 edge 音色即时合成;🔑 音色需要配置 Key,不支持在线试听)。"""
    import hashlib

    selector = body.get("selector") or ""
    kind, _, rest = selector.partition(":")
    cfg = _cfg()
    cache = cfg.data_dir / "previews"
    cache.mkdir(parents=True, exist_ok=True)
    text = "你好，这是我的声音。今天我们聊点有意思的事。"
    mp3 = cache / f"{hashlib.md5(f'{kind}:{rest}'.encode()).hexdigest()[:16]}.mp3"

    if kind == "edge":
        if not mp3.exists():
            from ..providers.tts.base import VoiceSpec
            from ..providers.tts.edge import EdgeTTS

            await EdgeTTS().synth(
                text, VoiceSpec(provider="edge", voice_id=rest, label=rest), None, out=mp3,
            )
    elif kind == "mimo":
        from ..providers.tts.base import VoiceSpec
        from ..providers.tts.mimo import MimoTTS

        mimo = MimoTTS(cfg.llm.get("openai", {}))
        if not mimo.api_key:
            raise HTTPException(400, "MiMo 试听需要 API Key(设置 → 接口配置);或先用 🆓 免费音色")
        if not mp3.exists():
            from ..styles import get_profile

            style = get_profile("podcast")["tts_default_style"]
            try:
                await mimo.synth(text, VoiceSpec(provider="mimo", voice_id=rest, label=rest),
                                 style, out=mp3)
            except Exception as e:
                mp3.unlink(missing_ok=True)
                raise HTTPException(400, f"MiMo 试听失败:{e}")
    else:
        raise HTTPException(400, "在线试听支持 🆓 edge-tts 与 MiMo(需 Key);其余音色请配置 Key 后直接做一期体验")
    return FileResponse(mp3, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/episodes/{ep_id}/export")
def export_episode(ep_id: str):
    """整期打包下载:音频/文稿/shownotes/封面 zip。"""
    import io
    import zipfile

    ep = _get_ep(ep_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("audio.mp3", "script.md", "script_clean.txt", "shownotes.md",
                     "subtitles.srt", "cover.jpg", "cover.png"):
            f = ep.dir / name
            if f.exists():
                zf.writestr(name, f.read_bytes())
    buf.seek(0)
    title = (ep.state.get("title") or ep_id).replace('"', "")
    from urllib.parse import quote

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(title + '.zip')}"
        },
    )


@app.post("/api/voices/clone")
async def clone_voice(name: str = Form(...), file: UploadFile = File(...)):
    suffix = Path(file.filename or "sample.wav").suffix or ".wav"
    tmp_path, _size = await save_upload(file, suffix)
    cfg = _cfg()
    try:
        check_duration(
            tmp_path,
            minimum=CLONE_MIN_SECONDS,
            maximum=CLONE_MAX_SECONDS,
            label="克隆采样",
        )
        entry = voice_reg.register_clone(cfg, name, tmp_path)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    # 入口处预告(产品原则:会延迟失败的操作必须在入口拦截或预告)
    from ..providers.voices import mimo_ready

    warning = None
    if not mimo_ready(cfg, cfg.llm):
        warning = "录好了！但克隆声音需要 MiMo 声音引擎：去设置 → 接口配置粘贴 MiMo 的 API Key 才能用它合成"
    return {"ok": True, "warning": warning, "voice": {**entry, "selector": f"clone:{entry['id']}"}}


@app.delete("/api/voices/clone/{voice_id}")
def delete_clone_voice(voice_id: str):
    if not voice_reg.delete_clone(_cfg(), voice_id):
        raise HTTPException(404, "声音不存在")
    return {"ok": True}


# ---- 节目 ------------------------------------------------------------------
@app.post("/api/episodes")
def create_episode(body: dict):
    mode = body.get("mode", "topic")
    if mode not in ("topic", "material"):
        raise HTTPException(400, "mode 必须是 topic 或 material")
    input_text = (body.get("input") or "").strip()
    if not input_text:
        raise HTTPException(400, "请输入选题或素材")
    style = body.get("style", "podcast")
    if style not in PROFILES:
        raise HTTPException(400, f"未知节目类型：{style}")
    voice_id = body.get("voice_id") or "edge:zh-CN-XiaoxiaoNeural"
    minutes = max(1, min(15, int(body.get("minutes") or PROFILES[style]["default_minutes"])))
    if voice_id.startswith("clone:"):
        from ..providers.voices import mimo_ready

        cfg = _cfg()
        if not mimo_ready(cfg, cfg.llm):
            raise HTTPException(400, "这个声音需要先配置 MiMo 声音引擎：设置 → 接口配置粘贴 MiMo 的 API Key;或改用免费 edge 音色")

    ep = Episode.create(
        _cfg(),
        mode=mode,
        input_text=input_text,
        style=style,
        voice_id=voice_id,
        minutes=minutes,
        format="dual" if body.get("format") == "dual" else "solo",
        structure=body.get("structure") or None,
        mood=body.get("mood") or None,
        pace=body.get("pace") or None,
    )
    if mode == "topic":
        _submit(ep, ep.run_research)
    else:
        _submit(ep, ep.run_material_title)
    return {"ok": True, "id": ep.id}


@app.get("/api/episodes")
def episodes():
    return list_episodes(_cfg())


@app.get("/api/episodes/{ep_id}")
def episode_state(ep_id: str):
    ep = _get_ep(ep_id)
    st = ep.public_state()
    # 旧节目没有 duration_sec:已完成且有音频时懒回填一次
    if (st.get("state") == "done" and st.get("duration_sec") is None
            and (ep.dir / "audio.mp3").exists()):
        from ..engine import audio as _audio

        with ep.lock:
            st["duration_sec"] = _audio.duration_seconds(ep.dir / "audio.mp3")
            ep.state["duration_sec"] = st["duration_sec"]
            ep.save()
    # 用量账本 + 可选费用估算（未配置定价时 cost 为 None，前端只显示字符数）
    if st.get("usage"):
        from ..engine.usage import estimate_cost

        st["usage_cost"] = estimate_cost(
            st["usage"], (ep.cfg.data.get("pricing") or {})
        )
    return st


@app.post("/api/episodes/{ep_id}/select")
def select_topic(ep_id: str, body: dict):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.state["state"] != "awaiting_topic":
        raise HTTPException(409, "当前状态不可选题")
    try:
        idx = int(body.get("index"))
    except (TypeError, ValueError):
        raise HTTPException(400, "选题序号无效,请刷新页面重新选择")
    cands = ep.state.get("candidates") or []
    if idx < 0 or idx >= len(cands):
        raise HTTPException(400, f"没有第 {idx + 1} 个候选选题,请刷新页面重新选择")
    _submit(ep, lambda: ep.select_topic(idx))
    return {"ok": True}


@app.post("/api/episodes/{ep_id}/script")
def update_script(ep_id: str, body: dict):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "文稿不能为空")
    # 状态闸:合成进行中改稿会导致分段与文件错位;其余状态限制在审稿/错误两态
    if ep.id in _busy or ep.state["state"] == "synthesizing":
        raise HTTPException(409, "正在合成,暂不能改稿;等完成或失败后再改")
    if ep.state["state"] not in ("awaiting_review", "error"):
        raise HTTPException(409, "当前状态不可改稿(选题/写稿阶段的稿件会自动更新)")
    ep.update_script(text)
    return {"ok": True}


@app.post("/api/episodes/{ep_id}/regenerate")
def regenerate_script(ep_id: str):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.state["state"] not in ("awaiting_review", "error"):
        raise HTTPException(409, "当前状态不可重新写稿")
    _submit(ep, ep.regenerate_script)
    return {"ok": True}


@app.post("/api/episodes/{ep_id}/approve")
def approve(ep_id: str):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.state["state"] != "awaiting_review":
        raise HTTPException(409, "请先完成文稿与合规审查")
    with _busy_lock:  # 与 _submit 同锁(Q4):查状态与占位原子化
        if ep.id in _busy:
            raise HTTPException(409, "该节目正在执行任务，请稍候")
        _busy.add(ep.id)
    try:
        ep.approve()
    except Exception:
        with _busy_lock:
            _busy.discard(ep.id)
        raise
    _pool.submit(_run_guarded, ep, ep.run_synthesis)  # approve 已占位,直接执行
    return {"ok": True}


def _run_guarded(ep: Episode, fn) -> None:
    """已在 _busy 占位时直接执行,结束后释放(approve 已占位的路径专用)。"""
    try:
        fn()
    finally:
        with _busy_lock:
            _busy.discard(ep.id)


@app.post("/api/episodes/{ep_id}/retry")
def retry(ep_id: str):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    state = ep.state["state"]
    if state != "error":
        raise HTTPException(409, "节目不在错误状态")

    step = ep.state.get("failed_step")
    if step == "research":
        _submit(ep, ep.run_research)
    elif step == "script":
        _submit(ep, ep.regenerate_script)
    elif step == "compliance":
        _submit(ep, ep.run_compliance)
    elif step == "synthesis":
        ep.approve()
        _submit(ep, ep.run_synthesis)
    elif step == "resynth":
        idx = int(ep.state.get("failed_chunk", 0))
        _submit(ep, lambda: ep.resynth_chunk(idx))
    elif step == "material_title":
        _submit(ep, ep.run_material_title)
    else:
        raise HTTPException(400, f"无法从步骤 {step} 重试")
    return {"ok": True}


@app.post("/api/episodes/{ep_id}/archive")
def archive_episode(ep_id: str, body: dict | None = None):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.id in _busy or ep.state["state"] in (
        "researching", "scripting", "compliance", "synthesizing"
    ):
        raise HTTPException(409, "节目正在执行任务，等完成后再归档")
    archived = True if body is None else bool(body.get("archived", True))
    ep.archive(archived)
    return {"ok": True, "archived": archived}


@app.post("/api/episodes/{ep_id}/rename")
def rename_episode(ep_id: str, body: dict):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    with ep.lock:
        ep.state["title"] = title[:60]
        ep.save()
    return {"ok": True, "title": ep.state["title"]}


@app.post("/api/episodes/{ep_id}/shownotes")
def update_shownotes(ep_id: str, body: dict):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "shownotes 不能为空")
    with ep.lock:
        ep.state["shownotes"] = text
        ep.save()
    (ep.dir / "shownotes.md").write_text(text, encoding="utf-8")
    return {"ok": True}


@app.get("/api/episodes/{ep_id}/subtitles")
def episode_subtitles(ep_id: str):
    """SRT 字幕下载(剪映/播放器通用);成品完成后可用。"""
    from urllib.parse import quote

    from ..engine import subtitles as subs

    ep = _get_ep(ep_id)
    if not ep.state.get("script"):
        raise HTTPException(409, "文稿尚未生成")
    srt = subs.write_srt(ep.dir, ep.chunks_info())
    if srt is None:
        raise HTTPException(409, "没有可用的分段音频,无法生成字幕")
    title = (ep.state.get("title") or ep_id).replace('"', "")
    return Response(
        content=srt.read_text(encoding="utf-8"),
        media_type="application/x-subrip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title + '.srt')}"},
    )


@app.get("/api/episodes/{ep_id}/chunks")
def episode_chunks(ep_id: str):
    ep = _get_ep(ep_id)
    if not ep.state.get("script"):
        raise HTTPException(409, "文稿尚未生成")
    return ep.chunks_info()


@app.post("/api/episodes/{ep_id}/resynth")
def resynth_chunk(ep_id: str, body: dict):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.state["state"] not in ("done", "error"):
        raise HTTPException(409, "节目完成后才能单段重录")
    _submit(ep, lambda: ep.resynth_chunk(int(body.get("index", 0))))
    return {"ok": True}


@app.delete("/api/episodes/{ep_id}")
def delete_episode(ep_id: str):
    ep = _get_ep(ep_id)
    _ensure_writable(ep)
    if ep.id in _busy:
        raise HTTPException(409, "任务执行中，无法删除")
    shutil.rmtree(ep.dir, ignore_errors=True)
    return {"ok": True}


@app.get("/api/episodes/{ep_id}/audio")
def episode_audio(ep_id: str):
    ep = _get_ep(ep_id)
    path = ep.dir / "audio.mp3"
    if not path.exists():
        raise HTTPException(404, "音频尚未生成")
    return FileResponse(
        path, media_type="audio/mpeg", filename=f"{ep.state.get('title') or ep_id}.mp3"
    )


@app.get("/api/episodes/{ep_id}/cover")
def episode_cover(ep_id: str):
    ep = _get_ep(ep_id)
    for name in ("cover.jpg", "cover.png"):
        path = ep.dir / name
        if path.exists():
            return FileResponse(path, media_type="image/jpeg" if name.endswith("jpg") else "image/png")
    raise HTTPException(404, "封面尚未生成")


@app.exception_handler(Exception)
def on_error(request, exc: Exception):
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    # 双通道之一(同步 500):人话给 detail(保持字符串),原始异常平级放 log,UI 不展示
    return JSONResponse(status_code=500, content={
        "detail": "服务内部开小差了。请重试;若反复出现,请重启服务(podcast-studio serve),并把终端日志提供给开发者。",
        "log": Episode._sanitize_raw(f"{type(exc).__name__}: {exc}"),
    })


from .feed import register_feed_route
from .scheduler import start_scheduler

register_feed_route(app)
start_scheduler(app)


# 静态资源（js/css）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
