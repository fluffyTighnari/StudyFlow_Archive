"""种子数据（随版本发布，不由任何人在运行时改写）。

ARCH-08 允许 `app.seeds` import 全局 ORM 模型——种子是「目录数据」而非
「LLM 写全局」，与 M2-B 策略库的定位一致（M0 §3 权限矩阵：写入方式 = 迁移脚本/版本发布）。

这批表（`m1_variable` / `m1_variable_channel` / `m2_strategy`）**豁免不变式 I1**
（不要求 RouterLog），理由与豁免清单见 M0 §4.4。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.seeds.m1_variables import CHANNELS as M1_CHANNELS
from app.seeds.m1_variables import VARIABLES as M1_VARIABLES
from app.seeds.m2_strategies import STRATEGIES as M2_STRATEGIES


def load_all(session: Session) -> dict[str, int]:
    """幂等加载全部种子数据，返回各表新增行数。

    已存在的行**不覆盖**——种子代表某一发布版本的目录，运行期产生的数据
    （如 M1 基线版本）不属于种子范围。
    """
    from app.global_domain.models import (
        M1Variable,
        M1VariableChannel,
        M2Strategy,
    )

    created = {"m1_variable": 0, "m1_variable_channel": 0, "m2_strategy": 0}

    for spec in M1_VARIABLES:
        if session.get(M1Variable, spec["variable_id"]) is None:
            session.add(M1Variable(**spec))
            created["m1_variable"] += 1
    # 先落变量行，通道行有指向它的外键。
    session.flush()

    for spec in M1_CHANNELS:
        key = (spec["variable_id"], spec["channel"])
        if session.get(M1VariableChannel, key) is None:
            session.add(M1VariableChannel(**spec))
            created["m1_variable_channel"] += 1

    for spec in M2_STRATEGIES:
        if session.get(M2Strategy, spec["strategy_id"]) is None:
            session.add(M2Strategy(**spec))
            created["m2_strategy"] += 1

    session.flush()
    return created


__all__ = ["load_all", "M1_VARIABLES", "M1_CHANNELS", "M2_STRATEGIES"]
