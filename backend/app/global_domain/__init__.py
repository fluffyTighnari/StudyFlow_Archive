"""全局认知域（权威数据）。

- `enums.py`   契约枚举（DB / Pydantic / 路由判定共用）
- `models.py`  ORM 模型（ARCH-08：只对 global_domain / router / seeds 开放）
- `reads.py`   只读接口（ARCH-07：不得持有写入口）
- `writes.py`  ★ 写原语（ARCH-01：只有 app.router.* 可 import）
"""
