"""给**已经存在**的 dev 库补上后来新增的列（本项目没有迁移框架）。

## 为什么需要这个脚本

本项目没有 Alembic（P12 之前刻意不做迁移）。`create_all` 只建新表、
**不会给已存在的表加列**，于是"新代码 + 旧库"的症状是
`sqlite3.OperationalError: no such column: ...` → 接口 500 →
界面上那一块变成"数据读取失败：HTTP_500"，看起来像前端坏了。

旧做法是 `seedui --reset`（删库重建）。但**预览库里有用户自己点出来的东西**，
重建等于把它们扔掉。所以这里只做 `ADD COLUMN`：

- 保留所有行；
- 新列都有默认值或可空，旧行不需要改写；
- `sort_order` **不回填**：`reads.list_folders` 的排序是
  `(sort_order, created_at, folder_id)`，全 0 时自然按创建时间排 ——
  这正是默认预期的顺序，第一次点"上移/下移"时服务端会整体重编号。

## 一个已知的取舍

SQLite 的 `ALTER TABLE ADD COLUMN` 加不了外键约束。`anchor_step_id`
在模型里是有 `ForeignKey` 的，补出来的这一列**没有那个约束**。
对 dev 库可以接受（真库走 `--reset` 重建）；这一点写在这里，
而不是让它变成一个"以后某天奇怪地不生效"的悬念。
"""

from __future__ import annotations

import sqlite3
import sys

#: (表, 列, 定义, 备注)
ADDITIONS: list[tuple[str, str, str, str]] = [
    (
        "folder",
        "sort_order",
        "INTEGER NOT NULL DEFAULT 0",
        "侧栏手动顺序",
    ),
    (
        "folder",
        "archived_at",
        "DATETIME",
        "归档时刻（≠ 状态）",
    ),
    (
        "session",
        "sort_order",
        "INTEGER NOT NULL DEFAULT 0",
        "侧栏手动顺序",
    ),
    (
        "session",
        "archived_at",
        "DATETIME",
        "归档时刻（≠ 状态）",
    ),
    (
        "chat_message",
        "anchor_step_id",
        "VARCHAR(32)",
        "这句话是针对哪一步说的（M7 v0.4）",
    ),
    (
        "chat_message",
        "cache_hit_tokens",
        "INTEGER",
        "命中提示缓存的 prompt token（DeepSeek 的 usage 里给）",
    ),
    (
        "chat_message",
        "cache_miss_tokens",
        "INTEGER",
        "未命中的 prompt token",
    ),
]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python var_ref/add_missing_columns.py <db 路径>")
        return 2
    path = argv[1]
    connection = sqlite3.connect(path)
    added = 0
    try:
        for table, column, definition, note in ADDITIONS:
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not existing:
                print(f"⚠ 表 {table} 不存在 —— 跳过（这个库可能是个空文件）")
                continue
            if column in existing:
                print(f"· {table}.{column} 已经有")
                continue
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            added += 1
            print(f"✅ 加了 {table}.{column} —— {note}")
        connection.commit()
    finally:
        connection.close()
    print(f"\n完成：新加 {added} 列（库：{path}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
