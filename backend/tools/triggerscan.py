"""探针：**模型能自己声称哪些触发规则、并能因此直接生效？**

## 为什么要有这个探针

`handoff.py` 的 `ALLOWED_TRIGGERS` 让**模型自己挑** `trigger_type`，
而判定表里有些组合是 **AUTO_APPLY**，理由是"用户已经自己给出或确认了这个值"
（`USER_CONFIRMED_GESTURES`）。

一次带上传文件的真模型运行（`tools.livedeepseek --inject`）里出现了
`submit_global_update → 提案 UP-0004，状态 applied` —— 也就是**直接生效**。
于是必须回答一个精确的问题：

> 在没有用户手势的情况下，模型能否靠自己填的 `trigger_type` + `basis`
> 让一条全局写入**直接生效**？

这不是靠读代码猜的（判定表与白名单分别都对，错误只会出现在两者的组合上），
所以这里**穷举地跑**：白名单里的每个触发 × 每个 basis × 每个 action 面，
逐条看裁定结果，并统计"生效 / 待确认 / 被拒"。

## 它已经响过一次（缺口 G-01）

第一版跑出来 **5 个组合**在没有任何用户手势时直接生效，其中
`R4c + update_parameter + experienced` 直接写 M1 认知变量 ——
而 R4c 的自动生效理由是"用户勾选过"，那件事**没人核实**。
用户裁定「甲案」，`R4c` 已从 `ALLOWED_TRIGGERS` 移除。

**这个探针要一直留着**：白名单是一个元组，判定表是一张表，
它们下次还会各自被改得很合理，而洞只会出现在叉乘上。

用法（不联网、不花额度，几秒跑完）：

    python -m tools.triggerscan
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.handoff import ALLOWED_TRIGGERS, submit_global_update  # noqa: E402
from app.agent.tools import ToolContext  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Base, make_engine, make_session_factory, session_scope  # noqa: E402
from app.main import create_app  # noqa: E402
from app.router.contracts import (  # noqa: E402
    CEvidenceValue,
    M1ParameterValue,
    M2PreferenceValue,
)
from app.session_domain import reads as session_reads  # noqa: E402
from app.session_domain import writes as session_writes  # noqa: E402

import app.llm_config  # noqa: E402,F401
import app.session_domain.models  # noqa: E402

#: 一次 intent 提交用的值。形状取自 `router/contracts.py`：
#: `target` 是**规范化的全局地址字符串**（不是对象），
#: `value` 的形状随 target 变（`M2.preferences` 用声明值、`M1.*` 用单通道参数值）。
#:
#: **值一律由契约模型构造，不手写。** 第一版探针手写了形状，于是 36 行全被
#: "value 结构不合法"挡下 —— 而"全被拒"看起来**恰好像**"没有洞"。
#: 一个说不清的失败会被误读成一个好消息，这是本项目的头号教训。
#: 用 `model_dump()` 生成，探针就永远与契约同步。
PREF_TARGET = "M2.preferences"
PARAM_TARGET = "M1.V-SMP"
EVIDENCE_TARGET = "C.evidence"


def _value(action: str) -> dict:
    if action == "add_preference":
        return M2PreferenceValue(
            statement="探针写入：我更喜欢先看例子再看原理，因为抽象解释我记不住",
            priority=5,
        ).model_dump()
    if action == "update_parameter":
        # `channel` 只能是 measured / experienced / stated —— 它描述**证据来自哪个通道**，
        # 不是"谁报的"。参数值借 `stated` 通道提交，正是"用户说的话被当成参数证据"。
        return M1ParameterValue(
            channel="stated",
            score=6.0,
            completeness="partial",
        ).model_dump()
    return CEvidenceValue(
        statement="探针：我在有立刻可用的场景里才记得住",
        condition="当天能用上一次",
        effect="记得更久",
        evidence_strength="medium",
        source="session_derived",
    ).model_dump()


def _intent(action: str, basis: str) -> dict:
    target = {
        "add_preference": PREF_TARGET,
        "update_parameter": PARAM_TARGET,
        "add_evidence": EVIDENCE_TARGET,
    }[action]
    return {
        "action": "add_preference" if action == "add_evidence" else action,
        "target": target,
        "value": _value(action),
        "basis": basis,
        "reasoning": "探针：不来自任何用户手势",
        "confidence": "medium",
    }


#: 三个面：偏好、参数、证据。覆盖面比"能跑通"重要 —— 洞藏在不常走的那条路上。
ACTIONS = ("add_preference", "update_parameter", "add_evidence")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="triggerscan-"))
    db_path = tmp / "scan.db"
    app = create_app(
        Settings(
            database_url=f"sqlite+pysqlite:///{db_path}",
            seed_on_startup=True,
            check_invariants=False,
            llm_provider="fake",
        )
    )
    with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app):
        engine = make_engine(f"sqlite+pysqlite:///{db_path}")
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        with session_scope(factory) as db:
            folder = session_writes.create_folder(db, name="探针", goal=None)
            session = session_writes.create_session(
                db,
                folder_id=folder.folder_id,
                title="探针会话",
                input_goal="探针",
            )
            session_id = session.session_id

        print("=" * 78)
        print("模型自己能声称的触发规则：", "、".join(ALLOWED_TRIGGERS))
        print("**没有任何用户手势**（没有勾选、没有点计划项、没有 UI 直写）")
        print("=" * 78)
        header = f"{'trigger':8} {'action':16} {'basis':12} {'结果':10} 说明"
        print(header)
        print("-" * 78)

        applied_rows: list[str] = []
        pending_rows: list[str] = []
        rejected_rows: list[str] = []
        downgraded_rows: list[str] = []
        for trigger in ALLOWED_TRIGGERS:
            for action in ACTIONS:
                for basis in ("stated", "experienced", "inferred"):
                    with session_scope(factory) as db:
                        ctx = ToolContext(db=db, session_id=session_id)
                        result = submit_global_update(
                            ctx,
                            {
                                "trigger_type": trigger,
                                "intents": [_intent(action, basis)],
                            },
                        )
                    data = result.data or {}
                    results = data.get("results") or [{}]
                    status = str(results[0].get("status"))
                    verdict = str(results[0].get("verdict"))
                    reason = str(results[0].get("reason"))
                    proposal_status = str(data.get("proposal_status"))
                    # 第二道门（`R3 + stated` 的引用核对）有没有动手 ——
                    # 它把 stated 降级成 experienced，于是"没给引用"这一格
                    # 会与"本来就该确认"看起来一样。分开数，否则这次修复
                    # 的效果在报告里不可见。
                    downgraded = "降级" in result.message
                    key = f"{trigger:8} {action:16} {basis:12}"
                    if downgraded:
                        downgraded_rows.append(key)
                    if status == "applied":
                        applied_rows.append(key)
                        note = f"**直接生效**（提案 {proposal_status}）"
                    elif data.get("pending_confirmation") or status == "pending_confirmation":
                        pending_rows.append(key)
                        note = "进待确认队列" + ("（被引用核对降级）" if downgraded else "")
                    else:
                        rejected_rows.append((key, verdict, reason))
                        note = f"被拒（{verdict}）"
                    print(f"{key} {status:22} {note}")
                    # **驳回理由必须打出来。** 只打"被拒"的话，
                    # 一次形状写错的探针与一次真的被规则挡住的调用长得一模一样 ——
                    # 而第一版探针正是这样：24 行全"被拒"，我差点读成"没有洞"。
                    if reason and status != "applied":
                        print(f"{'':8} ↳ {reason[:150]}")

        print("-" * 78)
        print(f"**没有用户手势却能直接生效的组合：{len(applied_rows)} 个**")
        for row in applied_rows:
            print("   ", row)
        print(f"进待确认队列的组合：{len(pending_rows)} 个")
        for row in pending_rows:
            print("   ", row)
        print(f"被拒的组合：{len(rejected_rows)} 个（未注册或红线拦截）")
        print(f"被**引用核对**降级的组合：{len(downgraded_rows)} 个")
        for row in downgraded_rows:
            print("   ", row)
        # 把**互不相同的**驳回理由去重列出来：36 行里通常只有两三种理由，
        # 而"哪一种理由"决定了该改哪里。
        seen: dict[str, str] = {}
        for key, verdict, reason in rejected_rows:
            seen.setdefault(reason[:200] or "（没有理由）", key)
        print("\n驳回理由去重（这是下一步该看的东西）：")
        for reason, sample in seen.items():
            print(f"  · [{sample.strip()}] {reason}")

        # ------------------------------------------------------------------ #
        # ★ 反事实校验：**"0 个"必须证明不是"探针变弱了造成的 0"**
        # ------------------------------------------------------------------ #
        #
        # 引用核对上线之后，上表的 `stated` 全部因为"没带引用"被降级，
        # 于是 applied 变成 0。但那可能是因为**这条路被彻底堵死了**
        # （那是个缺陷：用户真的说过的话也不再算数），
        # 也可能是因为**探针没有提供用户原话**（那是设计如此）。
        # 两种情形在上表里长得一模一样 —— 所以这里**真的让用户说一句话**，
        # 再引它一次，看 `stated` 还能不能自动生效。
        print("=" * 78)
        print("反事实校验：**用户真的说过那句话时，`stated` 还自动生效吗？**")
        proof = "我一次记不住 50 个词，太多了"
        with session_scope(factory) as db:
            # 先把会话推到 active —— `add_chat` 的守卫要求它在执行中
            # （状态机在这里也拦了我一次，理由写在 `guards.py` 里：
            #  "对话只能发生在执行中……归档之后还能继续聊，聊天就会变成漫谈"）。
            current = session_reads.get_session(db, session_id)
            session_writes.transition(
                db,
                session_id,
                target="active",
                expected_version=current.version,
                reason="探针：让用户能说话，以便反事实校验 stated 这条路",
            )
            session_writes.add_chat_message(
                db, session_id, role="user", content=proof
            )
        with session_scope(factory) as db:
            intent = _intent("add_preference", "stated")
            intent["basis_quote"] = proof
            intent["value"] = {
                "statement": "一次 50 个词太多，因为记不住",
                "target_field": None,
                "priority": 5,
            }
            control = submit_global_update(
                ToolContext(db=db, session_id=session_id),
                {"trigger_type": "R3", "intents": [intent]},
            )
        control_data = control.data or {}
        if control_data.get("applied_count") == 1:
            print("   ✅ 生效了 —— 说明那道门拦的是「**编造**用户说过的话」，")
            print("      不是「用户说过的话」本身。上表的 0 是有意义的 0。")
        else:
            print("   ❌ **没生效** —— 上表那个 0 是假的：门太紧，")
            print("      连用户真的说过的话也进不来了。**这是一个缺陷，不是修好了。**")
            print("      工具返回：", control.message[:300])

        print("=" * 78)
        print(
            "判据提醒：`applied` 是「这次写入已经落进全局认知」的意思。\n"
            "上表里出现 applied，就说明该组合的『用户已确认』理由是**模型自己填的**，\n"
            "而不是任何真实发生过的用户动作。\n"
            "而 `R3 + stated` 的『用户已确认』现在**必须由用户的原话证明** ——\n"
            "所以这一格能不能生效，取决于那张表里有没有他真的说过的话。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
