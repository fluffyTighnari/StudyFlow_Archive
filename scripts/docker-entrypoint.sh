#!/usr/bin/env bash
# 容器入口：先确保库里有一份计划，再起服务。
#
# 为什么需要这一步：`--llm fake`（以及默认的 fake provider）会装一个
# "按库里真实 step_id 改计划"的确定性替身；库里没有计划时它会打印原因并
# 以 exit 2 退出。空数据目录上的第一次启动正好就是这种情况 —— 那样容器会
# 起不来，而错误信息看起来像"配置错了"。
#
# 只在库**没有**会话时播种一次；已有数据的卷不会被覆盖。
set -euo pipefail

DB="${MOUXUE_DB:-/data/mouxue.db}"
PROVIDER="${MOUXUE_LLM_PROVIDER:-fake}"

if [ ! -f "$DB" ]; then
  echo "→ 空库，铺演示数据 $DB"
  python -m tools.seedui --db "$DB"
fi

if [ "$PROVIDER" = "fake" ] || [ "$PROVIDER" = "bridge" ]; then
  LLM_ARGS=(--llm fake)
else
  LLM_ARGS=()
fi

echo "→ 起服务（provider=$PROVIDER）"
exec python -m tools.serveui \
  --host 0.0.0.0 \
  --port "${MOUXUE_PORT:-8125}" \
  --db "$DB" \
  --dist "${MOUXUE_DIST:-/app/frontend/redesign}" \
  --items-dir "${MOUXUE_ITEMS_DIR:-tests/fixtures/bank}" \
  "${LLM_ARGS[@]}"
