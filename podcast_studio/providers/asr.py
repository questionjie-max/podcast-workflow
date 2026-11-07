"""火山引擎豆包「一句话识别」(语音转文字)。

接口:POST https://openspeech.bytedance.com/api/v1/asr(与 TTS 同域不同路径)。
认证:``Authorization: Bearer;{token}``(注意 Bearer 后是分号,与火山 TTS 同款)。
音频:先在本机用 ffmpeg 统一转 16kHz/16bit/mono WAV(_ensure_16k_mono),
再 base64 内联进请求体 audio.data —— 浏览器 MediaRecorder 的 webm 由此在服务端转码。

【待人工联调】接口字段依据(知识/推理,未经真实 key 验证,联调时以官方文档为准):
- 确定度较高:URL 与路径、Bearer; 分号认证头、app/user/audio/request 四段请求体
  结构、audio.data 承载 base64 音频、code=1000 表示成功、result.text 承载识别文本。
  (与火山 TTS v1 接口族同构,公开示例多来源一致)
- 推断(记忆可能有偏差):
  * audio 子字段名 format/rate/bits/channel 与取值 wav/16000/16/1;
  * request.workflow 固定串 "audio_in,resample,partition,vad,fe,decode"、
    sequence=-1(源自全双工流式协议"一次性提交"约定,HTTP 端点大概率忽略,
    按冲刺规范保留)、show_language/show_utterances/result_show_type;
  * 非 1000 错误码的具体语义:1002/1101/1102 视为鉴权类、1003-1006/1501/1502
    视为音频(时长/解码)类仅凭文档印象,故另留"消息含 token/auth 判鉴权"兜底;
  * 官方限制一句话识别音频 ≤60s,本实现以「转码后 >10MB」近似护栏
    (16kHz/16bit/mono 约 32KB/s,10MB ≈ 5 分钟,远宽于 60s,只兜极端误用)。

状态:实现 + mock 单测(含真实 ffmpeg webm→wav 转码用例),**待人工联调**
(需要 app_id + access token)。
"""

import base64
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx

ASR_API_URL = "https://openspeech.bytedance.com/api/v1/asr"
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 一句话识别护栏:转码后 >10MB 直接拒绝

# 非 1000 错误码分类(依据见文件头「待人工联调」,数值未经联调核实)
_AUTH_CODES = {1002, 1101, 1102}                       # token 校验/鉴权类
_AUDIO_CODES = {1003, 1004, 1005, 1006, 1501, 1502}    # 音频过长/解码失败类


class ASRError(RuntimeError):
    """ASR 调用错误;kind 用于前端给出可行动的提示(与 LLMError 分类风格一致)。

    kind ∈ config | auth | network | http | audio
    """

    def __init__(self, msg: str, kind: str = "http", *, code: int | None = None):
        super().__init__(msg)
        self.kind = kind
        self.code = code


def _classify_code(code: int | None, message: str) -> str:
    """业务错误码 → kind;未知码兜底 http,消息含鉴权字样兜底 auth。"""
    if code in _AUTH_CODES:
        return "auth"
    if code in _AUDIO_CODES:
        return "audio"
    m = (message or "").lower()
    if "token" in m or "auth" in m or "认证" in (message or "") or "鉴权" in (message or ""):
        return "auth"
    return "http"


def _run_ffmpeg(args: list[str]) -> None:
    """与 engine.audio._run 同款调用方式(本文件自带,避免反向依赖)。"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:{proc.stderr[-400:]}")


def _ensure_16k_mono(src: Path, dst: Path) -> Path:
    """任意输入音频(webm/m4a/mp3/wav…)统一转 16kHz/16bit/mono WAV。

    参照 engine.audio.ensure_wav_24k_mono(那边是 24kHz,ASR 侧要求 16kHz)。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(["-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    return dst


class VolcASR:
    """火山「一句话识别」provider:一次 POST,整段音频 base64 内联(≤60s)。"""

    name = "volc"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.api_url = cfg.get("api_url") or ASR_API_URL

    async def transcribe(
        self,
        audio_path: Path,
        *,
        app_id: str,
        token: str,
        cluster: str = "volcano_asr",
    ) -> str:
        """转写一段音频,返回识别文本;失败抛 ASRError(kind 见类 docstring)。"""
        if not (app_id or "").strip() or not (token or "").strip():
            raise ASRError(
                "未配置火山 ASR(设置 → 语音识别:app_id + access token)", kind="config"
            )
        if shutil.which("ffmpeg") is None:
            raise ASRError("本机未找到 ffmpeg,无法转换录音音频", kind="audio")

        with tempfile.TemporaryDirectory(prefix="ps_asr_") as td:
            wav = Path(td) / "in_16k.wav"
            try:
                _ensure_16k_mono(Path(audio_path), wav)
            except RuntimeError as e:
                raise ASRError(f"音频转码失败(支持 webm/mp3/m4a/wav 等):{e}", kind="audio") from e

            size = wav.stat().st_size
            if size > MAX_AUDIO_BYTES:
                raise ASRError(
                    f"音频过大({size // 1024 // 1024}MB),超过一句话识别上限,请说 60 秒以内",
                    kind="audio",
                )

            body = {
                "app": {"appid": app_id, "token": token, "cluster": cluster},
                "user": {"uid": "podcast-studio"},
                "audio": {
                    "format": "wav",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1,
                    "data": base64.b64encode(wav.read_bytes()).decode(),
                },
                "request": {
                    "reqid": uuid.uuid4().hex,
                    "sequence": -1,
                    "nbest": 1,
                    "workflow": "audio_in,resample,partition,vad,fe,decode",
                    "show_language": False,
                    "show_utterances": False,
                    "result_show_type": 0,
                },
            }
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        self.api_url,
                        headers={
                            "Authorization": f"Bearer;{token}",  # 火山特色:分号
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
            except httpx.ConnectError as e:
                raise ASRError(f"连不上识别服务:{e}", kind="network") from e
            except httpx.TimeoutException as e:
                raise ASRError("识别请求超时(60s),服务可能不可用", kind="network") from e
            except httpx.HTTPError as e:
                raise ASRError(f"识别请求失败:{e}", kind="network") from e

        if resp.status_code != 200:
            detail = resp.text[:300]
            if resp.status_code in (401, 403):
                raise ASRError(
                    f"认证失败({resp.status_code}):token 无效或无权限 — {detail}", kind="auth"
                )
            if resp.status_code == 429:
                raise ASRError(f"触发限流(429),请稍后再试 — {detail}", kind="http")
            raise ASRError(f"火山 ASR 返回 {resp.status_code}:{detail}", kind="http")

        try:
            data = resp.json()
        except ValueError as e:
            raise ASRError(f"火山 ASR 响应非 JSON:{resp.text[:300]}", kind="http") from e

        code = data.get("code")
        message = str(data.get("message", ""))
        if code != 1000:
            raise ASRError(
                f"火山 ASR 错误 {code}:{message[:200]}",
                kind=_classify_code(code, message),
                code=code,
            )

        text = (data.get("result") or {}).get("text")
        if not isinstance(text, str):
            raise ASRError(
                f"火山 ASR 响应缺少 result.text:{str(data)[:300]}", kind="http", code=code
            )
        return text
