"""Router 测试夹具。

`session` 来自 `tests/conftest.py`（内存库 + `create_all` + 令牌注册表隔离）。
这里补两件事：

1. 种子数据 —— `m1_baseline_version.variable_id` 有指向 `m1_variable` 的外键，
   不种子化就连一条基线都写不进去；
2. `Router` 实例 —— 令牌在它内部签发，测试**拿不到**它（这正是要验证的性质之一）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.router import Router
from app.seeds.loader import load_all


@pytest.fixture
def seeded(session: Session) -> Session:
    """种入 M1 变量 / 通道设计 / M2 策略库（catalog 表，无人可写）。"""
    load_all(session)
    session.flush()
    return session


@pytest.fixture
def router(seeded: Session) -> Router:
    return Router(seeded)
