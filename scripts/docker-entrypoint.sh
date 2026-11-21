#!/usr/bin/env bash
# 容器入口：按需准备数据库，再起服务。
#
# ## 默认：**空档案**
#
# 容器第一次起来时界面应该是空的 —— 没有文件夹、没有会话、没有模型档案。
# 那是最接近事实的样子：这是一个刚装好的应用，还没有任何东西。
#
# 以前这里"库文件不存在就 seedui"，于是每次重建卷之后界面里都会冒出两条
# 从没见过的会话（"背完考研核心 1500 词" / "技术文档精读"）。那不是旧数据
# 残留，是入口脚本主动写进去的 —— 但用户看到的永远是"为什么有别人的数据"。
#
# ## 需要演示数据时显式要
#
#   MOUXUE_SEED_DEMO=1   铺完整的两条演示会话（开发/截图用）
#   MOUXUE_SEED_MINIMAL=1 只铺一份最小计划（一个会话一个步骤，给替身改计划用）
#
# 两个都不给 → 什么都不铺。
set -euo pipefail

DB="${MOUXUE_DB:-/data/mouxue.db}"
PROVIDER="${MOUXUE_LLM_PROVIDER:-fake}"

db_is_empty() {
  python - "$DB" <<'PY'
import pathlib, sqlite3, sys
path = pathlib.Path(sys.argv[1])
if not path.is_file():
    print("yes")
    raise SystemExit
try:
    with sqlite3.connect(path) as conn:
        n = conn.execute("select count(*) from session").fetchone()[0]
except sqlite3.DatabaseError:
    print("yes")
    raise SystemExit
print("yes" if n == 0 else "no")
PY
}

if [ "$(db_is_empty)" = "yes" ]; then
  if [ "${MOUXUE_SEED_DEMO:-0}" = "1" ]; then
    echo "→ 空库，按 MOUXUE_SEED_DEMO=1 铺演示数据 $DB"
    python -m tools.seedui --db "$DB"
  elif [ "${MOUXUE_SEED_MINIMAL:-0}" = "1" ]; then
    echo "→ 空库，按 MOUXUE_SEED_MINIMAL=1 铺最小计划 $DB"
    python -m tools.seedui --db "$DB" --minimal
  else
    echo "→ 空库 $DB（不铺任何数据；界面从「还没有会话」开始）"
  fi
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
