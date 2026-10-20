"""种子数据（随版本发布，不由任何人在运行时改写）。

ARCH-08 允许 `app.seeds` import 全局 ORM 模型——种子是「目录数据」而非
「LLM 写全局」，与 M2-B 策略库的定位一致（M0 §3 权限矩阵：写入方式 = 迁移脚本/版本发布）。

这批表（`m1_variable` / `m1_variable_channel` / `m2_strategy`）**豁免不变式 I1**
（不要求 RouterLog），理由与豁免清单见 M0 §4.4。
"""

from __future__ import annotations

import json
import pathlib

from sqlalchemy.orm import Session

from app.seeds.m1_variables import CHANNELS as M1_CHANNELS
from app.seeds.m1_variables import VARIABLES as M1_VARIABLES
from app.seeds.m2_strategies import STRATEGIES as M2_STRATEGIES

#: 主题表的数据文件。**它是唯一来源**（`app.benchmark.topics` 也读这一份），
#: 本模块只是把它物化成一张表。
#:
#: 为什么直接读本包内的文件、而不是 `from app.benchmark.topics import ...`：
#: ARCH-05 规定 `app.global_domain` 不得依赖 `app.benchmark`，而
#: `seeds.loader` 把行写进 `domain_topic` —— 那张表正是写入层用来校验的东西。
#: 如果这里绕道 `app.benchmark`，就等于让"校验用的表"依赖"被校验方的邻居"。
#: 让两个读取器各自读**同一个文件**，比让其中一个依赖另一个更稳。
_TOPICS_FILE = pathlib.Path(__file__).with_name("domain_topics.json")


def load_all(session: Session) -> dict[str, int]:
    """幂等加载全部种子数据，返回各表新增行数。

    已存在的行**不覆盖**——种子代表某一发布版本的目录，运行期产生的数据
    （如 M1 基线版本）不属于种子范围。
    """
    from app.global_domain.models import (
        DomainTopic,
        M1Variable,
        M1VariableChannel,
        M2Strategy,
    )

    created = {
        "m1_variable": 0,
        "m1_variable_channel": 0,
        "m2_strategy": 0,
        "domain_topic": 0,
    }

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

    created["domain_topic"] = _load_domain_topics(session, DomainTopic)

    session.flush()
    return created


def _load_domain_topics(session: Session, model: type) -> int:
    """把主题表 JSON 物化进 `domain_topic`。

    `seq` 按**大类顺序 + 组内顺序**递增，所以清单的显示次序由数据文件决定，
    不由 id 的字母序决定 —— 后者会让「文学」排在「生物」后面，
    只因为 `LIT` > `BIO`。
    """
    payload = json.loads(_TOPICS_FILE.read_text(encoding="utf-8"))
    created = 0
    seq = 0
    for group in payload["groups"]:
        for topic in group["topics"]:
            seq += 1
            if session.get(model, topic["topic_id"]) is not None:
                continue
            session.add(
                model(
                    topic_id=topic["topic_id"],
                    group_id=group["group_id"],
                    group_label=group["label"],
                    label=topic["label"],
                    covers=topic["covers"],
                    seq=seq,
                )
            )
            created += 1
    return created


__all__ = ["load_all", "M1_VARIABLES", "M1_CHANNELS", "M2_STRATEGIES"]
