#!/usr/bin/env bash
# 一键冒烟:隔离实例 + 静态资产 + 核心只读 API + doctor。
# 用法:bash scripts/smoke.sh   (在仓库根目录执行)
# 产出:全部通过 → "SMOKE PASS";任何失败 → 非 0 退出并指出失败项。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SMOKE_PORT:-8791}"
HOME_DIR="$(mktemp -d /tmp/ps-smoke-XXXXXX)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi
FAIL=0

cleanup() {
  if [ -n "${SRV_PID:-}" ] && kill -0 "$SRV_PID" 2>/dev/null; then
    kill "$SRV_PID" 2>/dev/null
    wait "$SRV_PID" 2>/dev/null
  fi
  rm -rf "$HOME_DIR"
}
trap cleanup EXIT

check() { # check <名称> <命令...>
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ✅ $name"
  else
    echo "  ❌ $name"
    FAIL=1
  fi
}

echo "🎙️ 播客工坊冒烟测试(隔离目录 $HOME_DIR,端口 $PORT)"
printf '{"server":{"host":"127.0.0.1","port":%s}}' "$PORT" > "$HOME_DIR/config.json"

PODCAST_STUDIO_HOME="$HOME_DIR" "$PY" -m podcast_studio.cli serve &
SRV_PID=$!

for _ in $(seq 1 40); do # 最多等 10s
  curl -s --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.25
done

BASE="http://127.0.0.1:$PORT"
echo "  -- 服务 --"
check "health 可达且版本回显" bash -c "curl -s --max-time 3 $BASE/api/health | grep -q '\"version\"'"
check "首页不含坏 regex 字面量" bash -c "! curl -s --max-time 3 $BASE/ | grep -qF '\\\\?v='"
check "首页无未注入占位符" bash -c "! curl -s --max-time 3 $BASE/ | grep -qF '__PS_VERSION__'"
check "首页版本号已注入" bash -c "curl -s --max-time 3 $BASE/ | grep -qE '\\?v=[0-9]+\.[0-9]+\.[0-9]+'"
echo "  -- 静态资产 --"
for f in style.css app.js i18n.js manifest.json; do
  check "$f 200" bash -c "[ \$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 $BASE/static/$f) = 200 ]"
done
echo "  -- 只读 API --"
check "profiles 两种节目类型" bash -c "curl -s --max-time 3 $BASE/api/profiles | grep -q 'bedtime'"
check "episodes 列表可达" bash -c "curl -s --max-time 3 $BASE/api/episodes | grep -q '\['"
# voices 首次请求会冷取 edge 音色列表(实测可达 11s+),给足超时;其余检查保持 3s
check "voices 返回分组" bash -c "curl -s --max-time 30 $BASE/api/voices | grep -q 'builtin'"
check "RSS feed 200" bash -c "[ \$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 $BASE/api/feed.rss) = 200 ]"
check "恶意 Host 被 403" bash -c "[ \$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -H 'Host: evil.example.com' $BASE/api/health) = 403 ]"
echo "  -- CLI --"
check "doctor 退出码 0" bash -c "PODCAST_STUDIO_HOME=$HOME_DIR $PY -m podcast_studio.cli doctor"

if [ "$FAIL" = 0 ]; then
  echo "SMOKE PASS ✅"
else
  echo "SMOKE FAIL ❌ (逐项排查上面 ❌)"
fi
exit "$FAIL"
