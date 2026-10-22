"""表名到底是什么？S-0020 在库里吗？"""

from __future__ import annotations

import pathlib
import sqlite3

backend = pathlib.Path(__file__).resolve().parent.parent / "backend"
path = backend / "var" / "behavior.db"
conn = sqlite3.connect(path)
tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("表：", tables)

for table in tables:
    if "session" in table.lower():
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} 行")
        try:
            ids = [r[0] for r in conn.execute(f"SELECT session_id FROM {table} ORDER BY rowid DESC LIMIT 6")]
            print("    最近的：", ids)
        except sqlite3.Error as exc:
            print(f"    （查 id 时：{exc}）")
conn.close()
