"""SQLite 并发：一次慢的模型调用不该让别的写入报 500。

现场：讨论正在跑（本地模型，一次调用几十秒），此时在设置里新建一个模型档案 →
`sqlalchemy.exc.OperationalError: database is locked` → **500**。
那句话对用户毫无意义，而它其实是"另一个请求正占着写锁"。

两处改动都在连接层，不改任何业务逻辑：

1. **WAL**：读写不再互相阻塞（读事务看不到半截写入，写仍然串行）。
   默认的 rollback journal 下，一个写事务会挡住所有其它写；
   而这个应用有一类特别长的写事务：**讨论等着模型回答**的那几十秒。
2. **busy_timeout**：拿不到锁时等一会儿再试，而不是立刻报错。
   默认是 0 —— 也就是"别人正在写就直接失败"。给 5 秒，绝大多数竞争会自己消失。

（这两条都不解决"长事务"本身；真正的解法是别把模型调用放在写事务里，
但那是 discussion 流程的结构性改动，不在这一轮。）
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "app" / "db.py"
text = PATH.read_text(encoding="utf-8")

OLD = '''    if url.startswith("sqlite"):
        # SQLite 默认不强制外键，必须显式打开——否则 RESTRICT 形同虚设。
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
'''

NEW = '''    if url.startswith("sqlite"):
        # SQLite 默认不强制外键，必须显式打开——否则 RESTRICT 形同虚设。
        #
        # 另外两条是**并发**用的，都是被现场逼出来的：
        # - `journal_mode=WAL`：读写不再互相阻塞。默认的 rollback journal 下，
        #   一个写事务会挡住所有其它写，而这里有一类特别长的写事务 ——
        #   **讨论在等模型回答的那几十秒**。于是"讨论跑着的时候改设置"会拿到
        #   `database is locked`，界面上是一次 500。
        # - `busy_timeout=5000`：拿不到锁先等 5 秒再报错。默认是 0，即"别人正在写就
        #   立刻失败"，而那种失败在用户眼里就是"这台机器随机报错"。
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            # 内存库不支持 WAL（也没有意义），只在文件库上开。
            if ":memory:" not in url:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
'''

assert text.count(OLD) == 1, f"锚点出现 {text.count(OLD)} 次"
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("✅ db: 打开 WAL 与 busy_timeout")
