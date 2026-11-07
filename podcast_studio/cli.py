"""命令行入口。

  podcast-studio serve   启动 Web 服务（浏览器打开 http://localhost:8686）
  podcast-studio make    命令行直接做一期（无界面）
"""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="podcast-studio", description="播客工坊 — 本地播客/故事电台工作室"
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动 Web 服务")
    p_serve.add_argument("--host", default=None, help="默认 127.0.0.1")
    p_serve.add_argument("--port", type=int, default=None, help="默认 8686")

    p_make = sub.add_parser("make", help="命令行做一期节目")
    p_make.add_argument("topic", help="选题范围/问题，或素材文件路径")
    p_make.add_argument("--material", action="store_true", help="把 topic 当作素材文件路径")
    p_make.add_argument("--style", default="podcast", choices=["podcast", "bedtime"])
    p_make.add_argument("--minutes", type=int, default=None)
    p_make.add_argument("--voice", default="edge:zh-CN-XiaoxiaoNeural", help="如 edge:zh-CN-XiaoxiaoNeural / mimo:mimo_default")
    p_make.add_argument("--out", default=None, help="成品复制到的目录")
    p_make.add_argument("--yes", action="store_true", help="全自动：自动选第一个选题、审查通过即合成")

    p_doctor = sub.add_parser("doctor", help="自检:环境/配置/凭据逐项体检,给下一步指引")
    p_doctor.add_argument("--bundle", action="store_true",
                          help="生成诊断包（脱敏报告+配置+日志，便于交给开发者）")
    p_doctor.add_argument("--probe", action="store_true",
                          help="发起最小真实请求验证已配置 provider（可能产生少量费用）")

    p_backup = sub.add_parser("backup", help="备份数据目录（节目/声音/校准）")
    p_backup.add_argument("--out", default=None, help="备份输出目录（默认 ~/.podcast-studio/backups）")
    p_backup.add_argument("--include-config", action="store_true",
                          help="同时备份 config.json（含 API Key 明文，请妥善保管）")

    p_clean = sub.add_parser("clean", help="清理可再生缓存（试听缓存/合成残留/旧备份）")
    p_clean.add_argument("--yes", action="store_true", help="真正删除（默认只预览）")
    p_clean.add_argument("--keep-backups", type=int, default=7, help="保留最近 N 份备份（默认 7）")

    p_restore = sub.add_parser("restore", help="从备份恢复数据目录")
    p_restore.add_argument("archive", help="备份 zip 路径")
    p_restore.add_argument("--yes", action="store_true", help="跳过确认（脚本/自动化用）")
    p_restore.add_argument("--config", action="store_true",
                           help="同时恢复 config.json（会覆盖当前 Key）")

    args = parser.parse_args(argv)
    if args.command == "serve":
        _serve(args)
    elif args.command == "make":
        _make(args)
    elif args.command == "doctor":
        _doctor(args)
    elif args.command == "backup":
        _backup(args)
    elif args.command == "clean":
        _clean(args)
    elif args.command == "restore":
        _restore(args)
    else:
        parser.print_help()


def _port_available(host: str, port: int) -> bool:
    """尝试 bind 判断端口是否可用（比 connect 更接近 uvicorn 的真实行为）。"""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port(host: str, start: int, attempts: int = 20) -> int | None:
    for port in range(start, start + attempts):
        if _port_available(host, port):
            return port
    return None


def _existing_instance(host: str, port: int) -> str | None:
    """端口被占时探测是不是本产品：返回运行中的版本号，否则 None。"""
    import http.client
    import json

    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    conn = http.client.HTTPConnection(probe_host, port, timeout=1.5)
    try:
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read())
        return data.get("version")
    except Exception:  # 探测失败只代表不是本产品
        return None
    finally:
        conn.close()


def _serve(args) -> None:
    from .config import Config
    from .engine import audio as audio_mod

    cfg = Config.load()
    host = args.host or cfg.server.get("host", "127.0.0.1")
    port = args.port or int(cfg.server.get("port", 8686))
    cfg.ensure_dirs()

    if not _port_available(host, port):
        running = _existing_instance(host, port)
        if running:
            print(f"  ✅ 已有播客工坊在运行：http://localhost:{port}（v{running}）")
            print("     无需重复启动；如需重启，请先停止旧进程再运行本命令。")
            return
        if args.port is not None:
            print(f"  ❌ 端口 {port} 被其他程序占用，请换一个：--port {port + 1}")
            sys.exit(1)
        new_port = _find_free_port(host, port + 1)
        if new_port is None:
            print(f"  ❌ 端口 {port} 被占用，且附近 20 个端口都不可用；请手动指定 --port")
            sys.exit(1)
        print(f"  ⚠️ 端口 {port} 被其他程序占用，本次改用 {new_port}")
        port = new_port

    print()
    print("  🎙️  播客工坊 Podcast Studio v" + __version__())
    print(f"  数据目录：{cfg.data_dir}")
    if not audio_mod.ffmpeg_available():
        print("  ⚠️  未找到 ffmpeg，音频合成将失败；请先安装：apt/brew install ffmpeg")
    if host not in ("127.0.0.1", "localhost", "::1") or cfg.server.get("allow_lan"):
        print("  ⚠️ 正在允许非本机访问：本产品没有账号体系，请只在可信网络使用。")
    print(f"  ➡️  打开 http://{'localhost' if host == '127.0.0.1' else host}:{port}")
    print("  Ctrl+C 停止")
    print()

    import uvicorn

    from .server.app import app

    uvicorn.run(app, host=host, port=port, log_level="warning")


def __version__() -> str:
    from . import __version__ as v

    return v


def _doctor_body(probe: bool = False):
    """只读自检(C 提案 P8):环境/配置/凭据逐项体检,输出「可联调/缺什么/下一步」。

    绝不打印任何 Key 明文;不做真实合成;网络探测仅限既有 GET 探测端点语义。
    """
    import shutil
    import time as _time

    from .config import Config, mask_secret

    cfg = Config.load()
    cfg.ensure_dirs()
    print(f"🎙️ 播客工坊自检 v{__version__()}")
    print(f"   数据目录:{cfg.data_dir}")
    print(f"   配置文件:{'正常' if cfg.data else '空(默认配置)'}")

    # 代理环境：ALL_PROXY=socks:// 这类 scheme 会让 httpx 全体调用直接失败
    from .net import proxy_warnings

    for warning in proxy_warnings:
        print(f"⚠️  {warning}(本次进程已忽略;真实调用不受影响)")

    # ---- 环境 ----
    ffmpeg = bool(shutil.which("ffmpeg"))
    print(f"{'✅' if ffmpeg else '❌'} ffmpeg:{'已安装' if ffmpeg else '未找到 — 合成会失败,请安装:apt/brew install ffmpeg'}")
    free = shutil.disk_usage(cfg.data_dir).free
    disk_ok = free >= 200 * 1024 * 1024
    print(f"{'✅' if disk_ok else '⚠️ '} 磁盘剩余:{free // 1024 // 1024}MB({'充足' if disk_ok else '合成需要 ≥200MB,请清理'})")

    # ---- LLM ----
    llm = cfg.llm
    oa = llm.get("openai", {})
    if llm.get("provider") == "mock":
        print("ℹ️ 写作引擎:演示模式(内置示例文稿,无需 Key)")
    elif oa.get("api_key"):
        print(f"✅ 写作引擎:{llm.get('provider')} · 模型 {oa.get('model', '?')} · Key {mask_secret(oa['api_key'])}")
        print("   验证:设置页点「测试连接」,或curl http://localhost:8686/api/llm/test")
    else:
        print("❌ 写作引擎:未配置 Key — 设置 → 粘贴 API Key 一键连接")

    # ---- TTS 各家 ----
    tts = cfg.data.get("tts", {})
    checks = [
        ("edge-tts(免费)", None),  # 无需凭据
        ("豆包播客 TTS(方舟)", (tts.get("volc_podcast") or {}).get("api_key")),
        ("豆包音色(大模型)", (tts.get("volc_podcast") or {}).get("api_key")),  # 共用语音 Key
        ("豆包 TTS(旧版)", (tts.get("volc") or {}).get("token")),
        ("阿里 CosyVoice", (tts.get("ali") or {}).get("api_key")),
        ("ElevenLabs", (tts.get("elevenlabs") or {}).get("api_key")),
        ("Azure", (tts.get("azure") or {}).get("api_key")),
    ]
    print("---- TTS ----")
    for label, key in checks:
        if key is None:
            print(f"✅ {label}:免 Key 可用")
        elif key:
            print(f"✅ {label}:已配置(Key {mask_secret(key)})")
        else:
            print(f"⭕ {label}:未配置 — 设置 → TTS 接口配置填 Key 后解锁")

    ark_key = (tts.get("volc_podcast") or {}).get("api_key", "")
    eleven_key = (tts.get("elevenlabs") or {}).get("api_key", "")
    if eleven_key and ark_key and eleven_key == ark_key:
        print("⚠️  ElevenLabs 与方舟播客填了同一把 Key —— ElevenLabs 需要独立的 xi-api-key，请核对(可用 --probe 验证)")

    # ---- 豆包授权探测(已有 GET 探测语义:400=已授权,403=未开通,401=Key 无效) ----
    if ark_key:
        import httpx

        for rid in ("seed-tts-2.0", "seed-tts-1.0"):
            try:
                r = httpx.get(
                    "https://openspeech.bytedance.com/api/v3/tts/bidirection",
                    headers={"X-Api-Key": ark_key, "X-Api-Resource-Id": rid},
                    timeout=8,
                )
                verdict = {200: "已授权", 400: "已授权", 401: "Key 无效", 403: "控制台未开通该资源"}.get(
                    r.status_code, f"HTTP {r.status_code}")
                print(f"{'✅' if r.status_code in (200, 400) else '⚠️ '} 豆包授权 {rid}:{verdict}")
            except httpx.HTTPError as e:
                print(f"⚠️  豆包授权 {rid}:探测失败({type(e).__name__})— 网络问题,不作为未授权结论")
        _time.sleep(0)  # 探测节奏留白,便于后续加节流

    # ---- 搜索/ASR ----
    search = cfg.data.get("search", {})
    for prov in ("tavily", "bocha"):
        k = (search.get(prov) or {}).get("api_key")
        print(f"{'✅' if k else '⭕'} 搜索 {prov}:{'已配置' if k else '未配置(选题热点不可用,不影响主流程)'}")
    asr = (cfg.data.get("asr") or {}).get("volc") or {}
    print(f"{'✅' if asr.get('app_id') and asr.get('token') else '⭕'} 语音识别(豆包 ASR):{'已配置' if asr.get('app_id') and asr.get('token') else '未配置(语音输入不可用)'}")

    # ---- 数据 ----
    try:
        from .engine.pipeline import list_episodes

        eps = list_episodes(cfg)
        states: dict[str, int] = {}
        for e in eps:
            states[e["state"]] = states.get(e["state"], 0) + 1
        summary = "、".join(f"{k}×{v}" for k, v in states.items()) or "空"
        print(f"✅ 节目库:{len(eps)} 期({summary})")
    except Exception as e:
        print(f"⚠️  节目库读取失败:{e}")

    if probe:
        from .probes import format_results, run_probes

        print("---- 真实连通性探测（最小请求，可能产生少量费用）----")
        try:
            print(format_results(run_probes(cfg)))
        except Exception as e:
            print(f"⚠️  探测过程异常：{type(e).__name__}: {e}")

    # ---- 磁盘占用 ----
    try:
        from .cleanup import disk_report, human_bytes

        report = disk_report(cfg)
        print(
            "---- 磁盘 ----\n"
            f"📦 数据 {human_bytes(report['total_bytes'])}"
            f"（节目 {report['episodes']} 期 {human_bytes(report['episodes_bytes'])}，"
            f"试听缓存 {human_bytes(report['previews_bytes'])}，"
            f"日志 {human_bytes(report['logs_bytes'])}，"
            f"备份 {human_bytes(report['backups_bytes'])}）"
        )
        if report["previews_bytes"] or report["backups_bytes"]:
            print("   可运行 `podcast-studio clean` 查看可清理项（默认只预览，加 --yes 才删除）")
    except Exception as e:
        print(f"⚠️  磁盘占用统计失败：{e}")

    print("---- 结论 ----")
    print("全部 ✅ 即可生产。⭕ 为可选增强,❌ 需要处理;详细指引见 docs/USAGE.md 或设置页。")
    return cfg


def _doctor(args) -> None:
    """打印自检报告；--bundle 时额外生成脱敏诊断包。"""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cfg = _doctor_body(bool(getattr(args, "probe", False)))
    report = buf.getvalue()
    print(report, end="")

    if getattr(args, "bundle", False):
        from .diagnostics import build_bundle

        try:
            path = build_bundle(cfg, report)
        except OSError as e:
            print(f"⚠️  诊断包生成失败：{e}")
            return
        print(f"✅ 诊断包已生成：{path}")
        print("   内含脱敏配置、doctor 报告与日志；不含音频/文稿/API Key 明文。")


def _make(args) -> None:
    from pathlib import Path

    from .config import Config
    from .engine.pipeline import Episode
    from .styles import PROFILES

    cfg = Config.load()
    cfg.ensure_dirs()

    if args.material:
        text = Path(args.topic).read_text(encoding="utf-8")
        mode = "material"
    else:
        text = args.topic
        mode = "topic"

    minutes = args.minutes or PROFILES[args.style]["default_minutes"]
    ep = Episode.create(
        cfg, mode=mode, input_text=text, style=args.style,
        voice_id=args.voice, minutes=minutes,
    )
    print(f"📝 节目 {ep.id}")

    if mode == "topic":
        print("🔍 搜索选题…")
        ep.run_research()
        if ep.state["state"] == "error":
            sys.exit(f"❌ {ep.state['error']}")
        for c in ep.state["candidates"]:
            print(f"  [{c['index']}] {c['title']}  （热度 {c['heat']}）{c['reason']}")
        idx = 0 if args.yes else int(input("选哪个？(序号) ").strip() or 0)
        ep.select_topic(idx)
    else:
        ep.run_material_title()

    _wait(ep, {"scripting", "compliance"})
    if ep.state["state"] == "error":
        sys.exit(f"❌ {ep.state['error']}")
    print(f"🛡️ 合规审查：{'通过' if (ep.state.get('compliance') or {}).get('pass') else '有问题'}")
    print(f"📄 标题：{ep.state['title']}")

    ep.approve()
    print("🎙️ 合成中…")
    ep.run_synthesis()
    if ep.state["state"] == "error":
        sys.exit(f"❌ {ep.state['error']}")

    final = ep.dir / "audio.mp3"
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        dest = out / f"{ep.state['title']}.mp3"
        dest.write_bytes(final.read_bytes())
        final = dest
    print(f"✅ 完成：{final}")


def _backup(args) -> None:
    from .backup import create_backup
    from .config import Config

    cfg = Config.load()
    cfg.ensure_dirs()
    path = create_backup(
        cfg,
        out_dir=Path(args.out).expanduser() if args.out else None,
        include_config=args.include_config,
    )
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"✅ 备份完成：{path}（{size_mb:.1f} MB）")
    if args.include_config:
        print("⚠️ 备份包含 config.json（含 API Key 明文），请只放在可信位置。")
    print("   恢复：podcast-studio restore " + str(path))


def _clean(args) -> None:
    from .cleanup import execute, format_plan, human_bytes, plan_cleanup, touch_marker
    from .config import Config

    cfg = Config.load()
    cfg.ensure_dirs()
    plan = plan_cleanup(cfg, keep_backups=args.keep_backups)
    print(format_plan(plan))
    if not plan.all_files():
        print("✅ 已经干净，无需清理。")
        return
    if not args.yes:
        print("\n（这是预览；加 --yes 才会真正删除。节目/声音/配置永不删除。）")
        return
    result = execute(plan, dry_run=False)
    touch_marker(cfg)
    print(f"✅ 已删除 {result['files']} 个文件，释放 {human_bytes(result['bytes'])}")


def _restore(args) -> None:
    from .backup import restore_backup
    from .config import Config

    archive = Path(args.archive).expanduser()
    if not args.yes:
        answer = input(
            f"将用 {archive} 覆盖当前数据目录（会先自动备份当前状态）。\n"
            "确认请输入 yes： "
        ).strip().lower()
        if answer not in ("yes", "y", "是"):
            print("已取消。")
            return
    try:
        result = restore_backup(
            Config.load(), archive, restore_config=args.config
        )
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"❌ 恢复失败：{e}")
    print(f"✅ 恢复完成：{result['episodes']} 期节目")
    if result["safety_backup"]:
        print(f"   恢复前快照：{result['safety_backup']}")
    if args.config:
        print("⚠️ config.json 已覆盖，如 Key 不对请到设置页重新配置。")


def _wait(ep, active_states: set[str]) -> None:
    import time

    while ep.state["state"] in active_states:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
