"""ORM 列类型的共用构造器 —— **取值域怎么落库，只在这里定义一次**。

为什么它住在 `app/core/` 而不是某个域里：`global_domain.models` 与
`session_domain.models` 都要把 StrEnum 落成列，而架构规则 ARCH-08 禁止
沙箱域 import `app.global_domain.models`。如果两边各抄一份，那么
「枚举存的是 `.value` 还是 `.name`」这件事就有两个答案 ——
而这类偏差不会报错，只会让同一个取值在两处查询里对不上
（例如 `R4A` 与 `R4a` 被当成两个不同的触发器）。
"""

from __future__ import annotations

import sqlalchemy as sa


def enum_col(enum_cls: type, name: str) -> sa.Enum:
    """把 StrEnum 落成 VARCHAR + CHECK（便于 SQLite/Postgres 双兼容）。

    关键：`values_callable` 保证存储的是 `.value` 而非 `.name`
    —— 否则 `R4A` 会被存成 `"R4A"` 而不是文档要求的 `"R4a"`。
    """
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
    )
