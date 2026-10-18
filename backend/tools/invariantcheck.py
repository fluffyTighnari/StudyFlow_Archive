"""独立 CLI：对现有数据库跑一遍全局不变式自检（M0 §4.4）。

用法（在 `backend/` 下）：

    .venv\\Scripts\\python -m tools.seeddb                   # 先建一个带种子的库（新机器上必须先做这步）
    .venv\\Scripts\\python -m tools.invariantcheck           # 用默认库 backend/var/mouxue.db
    $env:MOUXUE_DATABASE_URL="sqlite+pysqlite:///..."; .venv\\Scripts\\python -m tools.invariantcheck

退出码：`0` = 全部通过；`1` = 有违规；`2` = **检查器本身没能跑起来**。

为什么把 `2` 与 `1` 分开（与 `tools/archcheck.py` 同样的理由）：
「检查没能执行」与「检查发现违规」是两件事。退出码若都是 1，
一个坏掉的检查器会被读成「发现了问题」，而一个数据库连不上的错误会被读成
「治理内核有问题」—— 两种误读都会把注意力引到错误的地方。
"""

from __future__ import annotations

import sys

from sqlalchemy.exc import SQLAlchemyError

from app.db import make_engine, make_session_factory, session_scope
from app.global_domain.invariants import (
    check_global_invariants,
    format_failures,
    iter_invariants,
)

# 沙箱域的表必须被 import 过，`Base.metadata` 才是完整的 —— 否则
# `PROVENANCE` 的「每张表都要表态」这条覆盖性检查会**只看全局域**，
# 而一个没有被 import 的模型不会报错，只会静默地不参与检查。
import app.session_domain.models  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    _ = argv

    from app.config import Settings

    settings = Settings.from_env()
    engine = None
    try:
        engine = make_engine(settings.database_url)
        with session_scope(make_session_factory(engine)) as session:
            failures = check_global_invariants(session)
    except SQLAlchemyError as exc:  # pragma: no cover - 需要坏库才能触发
        print(f"[InvariantCheckError] 无法读取数据库：{exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()

    database = settings.database_url
    if database.startswith("sqlite"):
        database = f"backend/var/{database.rsplit('/', 1)[-1]}"

    if failures:
        print(f"全局不变式自检失败：{database}")
        print(format_failures(failures))
        return 1

    # 从注册表**生成**这句话，而不是写死「I1–I6」——
    # 写死会在加了一条不变式之后继续说"全部成立"（P6 加 I7 时正是如此）。
    ids = "、".join(i.invariant_id for i in iter_invariants())
    print(f"全局不变式自检通过：{ids} 全部成立（{database}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
