#!/usr/bin/env bash
# 一条命令起本地开发服务：建虚拟环境 → 装依赖 → 铺演示数据 → 起服务。
#
# 用法：
#   scripts/dev.sh              # 假模型（不联网，链路可复现）
#   scripts/dev.sh real         # 用 .env 里的真模型
#   PORT=8126 scripts/dev.sh    # 换端口
#
# 环境变量：
#   PORT         服务端口（默认 8125）
#   PYTHON       解释器（默认 python3，Windows 上用 python）
#   SKIP_SEED=1  不重铺演示数据（保留当前库）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PORT="${PORT:-8125}"
PYTHON="${PYTHON:-python3}"
MODE="${1:-fake}"

VENV="$BACKEND/.venv"
if [ ! -x "$VENV/bin/python" ] && [ ! -x "$VENV/Scripts/python.exe" ]; then
  echo "→ 建虚拟环境 $VENV"
  "$PYTHON" -m venv "$VENV"
fi
if [ -x "$VENV/Scripts/python.exe" ]; then
  PY="$VENV/Scripts/python.exe"
else
  PY="$VENV/bin/python"
fi

# 用 -e：非 editable 安装会在这里落一份 build/ 副本，并把 app/tools 复制进
# site-packages —— 改了源码却跑的是旧拷贝，而且 cwd 一换就少一个包。
# 在 backend/ 里用 `.` 安装：Git Bash 的 /d/... 形式不是 pip 认的本地路径。
echo "→ 安装依赖"
(cd "$BACKEND" && "$PY" -m pip install -q -e ".[dev]")
export PYTHONIOENCODING=utf-8
export MOUXUE_SKIP_ENV_FILE=0

cd "$BACKEND"
mkdir -p var

if [ "${SKIP_SEED:-0}" != "1" ]; then
  echo "→ 铺演示数据 var/ui_live.db"
  "$PY" -m tools.seedui --db var/ui_live.db --reset
fi

if [ "$MODE" = "real" ]; then
  LLM_ARGS=()
else
  LLM_ARGS=(--llm fake --script-session S-0001 --frame-delay 0.01)
fi

echo "→ 打开 http://127.0.0.1:$PORT/"
exec "$PY" -m tools.serveui \
  --db var/ui_live.db \
  --port "$PORT" \
  --dist "$ROOT/frontend/redesign" \
  --items-dir tests/fixtures/bank \
  "${LLM_ARGS[@]}"
