"""建一个空的、带种子的库，好让不变式自检有东西可查。

（`tools.invariantcheck` 退出码 2 = "检查没能执行"，与 1 = "发现违规" 是两件事 ——
 库不存在时它必须报 2，否则"没法检查"会被读成"检查通过"。）
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

url = sys.argv[1] if len(sys.argv) > 1 else "sqlite+pysqlite:///var/invariant_check.db"
app = create_app(Settings(database_url=url, seed_on_startup=True))
with TestClient(app):
    pass
print(f"已建库并写入种子：{url}")
