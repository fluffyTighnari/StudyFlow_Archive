"""对比两个 sqlite 库的表结构，把"旧库缺哪些列"直接点出来。

新代码 + 旧库的症状是 500，而 500 的响应体只有一个 `Internal Server Error`。
这个脚本回答的是"缺哪一列" —— 没有它，唯一的选择就是无脑 `--reset`
（把库里的东西全扔掉）。
"""

from __future__ import annotations

import sqlite3
import sys

BASE = r"D:\Workspace\Code\phy\personal_learning_flow\backend\var"


def schema(path: str) -> dict[str, list[str]]:
    connection = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        ]
        return {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
    finally:
        connection.close()


def main(argv: list[str]) -> int:
    fresh = schema(f"{BASE}\\{argv[1]}")
    old = schema(f"{BASE}\\{argv[2]}")
    differences = 0
    for table in sorted(set(fresh) | set(old)):
        if table not in fresh:
            print(f"· 只有旧库有表 {table}")
            continue
        if table not in old:
            print(f"· 只有新库有表 {table}")
            differences += 1
            continue
        missing = [column for column in fresh[table] if column not in old[table]]
        extra = [column for column in old[table] if column not in fresh[table]]
        if missing or extra:
            differences += 1
            print(f"⚠ {table}: 旧库缺 {missing}；旧库多 {extra}")
    if differences == 0:
        print("两个库的表结构一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
