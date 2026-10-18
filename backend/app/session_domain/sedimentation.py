"""沉淀候选条目（P4 两条端到端通路之一）—— **纯函数，不认识 Router、不写任何表**。

## 「沉淀」到底是什么

会话里发生了一堆事。其中有些事是**关于你这个人的**（「我发现我背单词必须出声」），
有些事**只关于这次任务**（「第 3 步应该拆成两步」）。前者要进个人认知体系，
后者只改计划。把两者混在一起，是这类系统最常见的腐烂方式：
计划调整会被渲染成"我的认知变了"，而真正的认知变化被埋在计划修订里。

所以本模块做第一件事就是**分类**，并且把不能沉淀的东西**连理由一起返回**。

## 三个触发（M7 §7.2）

| 触发 | 原料 | basis | 为什么 |
|---|---|---|---|
| R4a 完成总结 | `session_summary.findings` | 各条自带，缺省 `inferred` | 总结是**推出来的**，不是用户说的 |
| R4b 里程碑 | 已达成的 `milestone` | `experienced` | 「在 X 上达成 Y」是一次观察 |
| R4c 手动沉淀 | 用户在 stage 里**勾选**的条目 | `experienced` | 用户勾选本身是确认动作（A21） |

**缺省 `inferred` 而不是 `experienced`**：`inferred` 永远需要确认（A21），
所以"少声明一点"是安全方向。反过来把推出来的东西标成 `experienced`，
会在 R4c 那条路上触发自动生效 —— 而它根本不是用户选的。

## 只有 `temp_pref` 能沉淀

`stage_item.type` 有三个取值（M7 §7.1）：

- `temp_pref`（临时偏好）→ **是**关于你自己的，「我这次想试试出声背」。可沉淀。
- `idea`（想法）→ 关于这次任务的做法。改计划，不是沉淀。
- `plan_override`（计划调整）→ 同上，且更明确：它就是计划修订。

于是 R4c 里勾选一个 `idea` 不会写任何全局状态 —— 它会被**明确排除**并说明理由，
而不是悄悄变成一个"关于你的认知"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.core.errors import InvalidField, MissingPrerequisite
from app.global_domain.enums import Basis, TriggerType
from app.global_domain.targets import target_for_variable
from app.session_domain import reads
from app.session_domain.enums import MilestoneStatus, StageItemStatus, StageItemType

#: 目标常量（与 `api.preferences` / `api.evidence` 用的是同一个字符串）。
TARGET_PREFERENCES = "M2.preferences"
TARGET_EVIDENCE = "C.evidence"


@dataclass(frozen=True)
class SedimentationCandidate:
    """一条**可以**沉淀的候选。它不是 `UpdateIntent` —— 映射在 `app/api/` 里做，
    因为架构规则 ARCH-11 禁止沙箱域认识 Router。

    `proposed_basis` 是**建议**，不是结论：真正的 `basis` 由 Router 按触发与
    手势裁定（`precheck.USER_CONFIRMED_GESTURES`）。
    """

    source_kind: str
    source_ref: str
    statement: str
    target: str
    value: dict[str, Any]
    proposed_basis: Basis
    action: str = "add_preference"


@dataclass(frozen=True)
class ExcludedItem:
    """一条**不能**沉淀的东西 + 理由。

    这个类型是刻意的产品决定：**沉默地少沉淀一条，比沉淀错一条更难发现**。
    界面必须能回答「我勾了 5 条，为什么只看到 3 条」。
    """

    source_kind: str
    source_ref: str
    content: str
    reason: str


@dataclass(frozen=True)
class SedimentationPlan:
    """一次沉淀的全部产出。`included` 会变成提案，`excluded` 只是解释。"""

    trigger: TriggerType
    included: list[SedimentationCandidate] = field(default_factory=list)
    excluded: list[ExcludedItem] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.included

    def summary(self) -> str:
        return (
            f"可沉淀 {len(self.included)} 条，排除 {len(self.excluded)} 条"
        )


def _pref_payload(
    statement: str,
    *,
    priority: Any = None,
    target_field: Any = None,
    value: Any = None,
) -> dict[str, Any]:
    """`M2.preferences` 的 value 载荷。

    **`None` 的键一个都不能带**：`M2PreferenceValue.priority` 不是
    `int | None`（它是「不传就用默认值」），显式传 `null` 会被契约拒成
    `priority —— Input should be a valid integer`。

    这个坑值得记一笔：它在测试里表现为"沉淀了但全局没东西"，
    而 `build_candidates` 返回的候选看起来完全正确 ——
    因为错误发生在**映射之后**的那一层。
    """
    payload: dict[str, Any] = {"statement": statement}
    if priority is not None:
        payload["priority"] = priority
    if target_field is not None:
        payload["target_field"] = target_field
    if value is not None:
        payload["value"] = value
    return payload


def _m1_payload(
    *,
    channel: str,
    score: Any = None,
    completeness: str,
    confidence: Any = None,
    boundary: Any = None,
) -> dict[str, Any]:
    """`M1.<变量>` 的 value 载荷。同 `_pref_payload`：`None` 的键不带。

    `score=None` 是有意义的（"测了，但这次没得到分数"，此时 `completeness`
    会说明缺了什么），因此**不是**把它删掉，而是不传 —— 契约里 `score` 是可选的。
    """
    payload: dict[str, Any] = {"channel": channel, "completeness": completeness}
    if score is not None:
        payload["score"] = score
    if confidence is not None:
        payload["confidence"] = confidence
    if boundary is not None:
        payload["boundary"] = boundary
    return payload


def _stage_candidates(db: DbSession, session_id: str, item_ids: list[str]) -> SedimentationPlan:
    """R4c：用户勾选的 stage 条目。"""
    included: list[SedimentationCandidate] = []
    excluded: list[ExcludedItem] = []

    for item_id in item_ids:
        item = reads.get_stage_item(db, item_id)
        if item.session_id != session_id:
            raise InvalidField(
                f"条目 {item_id} 不属于会话 {session_id}",
                detail={"item_id": item_id, "session_id": session_id},
            )
        if item.status is not StageItemStatus.ACTIVE:
            raise MissingPrerequisite(
                f"条目 {item_id} 已经是 {item.status.value}，不能重复沉淀",
                detail={"item_id": item_id, "status": item.status.value},
            )

        if item.type is not StageItemType.TEMP_PREF:
            excluded.append(
                ExcludedItem(
                    source_kind="stage_item",
                    source_ref=item_id,
                    content=item.content,
                    reason=(
                        f"它是 {item.type.value}（关于这次任务的做法），"
                        "不是关于你自己的认知。要改计划请走计划修订 —— "
                        "把计划调整沉淀成「我的特质」会让认知基线慢慢变成一个错误的自我描述"
                    ),
                )
            )
            continue

        included.append(
            SedimentationCandidate(
                source_kind="stage_item",
                source_ref=item_id,
                statement=item.content,
                target=TARGET_PREFERENCES,
                value=_pref_payload(item.content),
                # 用户在 stage 里写下的、并亲手勾选了它 —— A21 里
                # (R4C, ADD_PREFERENCE, EXPERIENCED) 正是"用户已确认"的手势之一。
                proposed_basis=Basis.EXPERIENCED,
            )
        )

    return SedimentationPlan(
        trigger=TriggerType.R4C, included=included, excluded=excluded
    )


def _summary_candidates(db: DbSession, session_id: str, summary_id: str | None) -> SedimentationPlan:
    """R4a：完成总结里的 findings。**每条的 basis 缺省为 `inferred`**（见模块文档）。"""
    summary = (
        reads.latest_summary(db, session_id)
        if summary_id is None
        else _get_summary(db, summary_id)
    )
    if summary is None:
        raise MissingPrerequisite(
            "还没有完成总结，没有可沉淀的原料。请先写总结（WRITE_SUMMARY）。",
            detail={"session_id": session_id},
        )

    included: list[SedimentationCandidate] = []
    excluded: list[ExcludedItem] = []

    for index, finding in enumerate(summary.findings or []):
        ref = f"{summary.summary_id}#{index}"
        statement = (finding.get("statement") or "").strip()
        if not statement:
            excluded.append(
                ExcludedItem(
                    source_kind="summary_finding",
                    source_ref=ref,
                    content="",
                    reason="这条 finding 没有 statement，无法成为一条可审阅的认知",
                )
            )
            continue

        basis = _basis_from(finding.get("basis"), default=Basis.INFERRED)
        variable_id = finding.get("variable_id")

        if variable_id:
            channel = finding.get("channel")
            if not channel:
                excluded.append(
                    ExcludedItem(
                        source_kind="summary_finding",
                        source_ref=ref,
                        content=statement,
                        reason=(
                            f"它指向 {variable_id} 但没有指定证据通道。"
                            "M0 §6.5 要求每个分数都归属一条通道 —— "
                            "没有通道的分数会被迫在展示时合成一个平均值，那正是被禁止的事"
                        ),
                    )
                )
                continue
            included.append(
                SedimentationCandidate(
                    source_kind="summary_finding",
                    source_ref=ref,
                    statement=statement,
                    target=target_for_variable(variable_id),
                    action="update_parameter",
                    value=_m1_payload(
                        channel=channel,
                        score=finding.get("score"),
                        # 单次总结给出的读数**不完整**：它只覆盖一个通道的一个侧面。
                        completeness=finding.get("completeness") or "partial",
                        confidence=finding.get("confidence"),
                        boundary=finding.get("boundary"),
                    ),
                    proposed_basis=basis,
                )
            )
            continue

        included.append(
            SedimentationCandidate(
                source_kind="summary_finding",
                source_ref=ref,
                statement=statement,
                target=TARGET_PREFERENCES,
                value=_pref_payload(
                    statement,
                    priority=finding.get("priority"),
                    target_field=finding.get("target_field"),
                    value=finding.get("value"),
                ),
                proposed_basis=basis,
            )
        )

    return SedimentationPlan(
        trigger=TriggerType.R4A, included=included, excluded=excluded
    )


def _milestone_candidates(
    db: DbSession, session_id: str, milestone_ids: list[str]
) -> SedimentationPlan:
    """R4b：已达成的里程碑 → 一条 `experienced` 观察。"""
    included: list[SedimentationCandidate] = []
    excluded: list[ExcludedItem] = []

    for milestone_id in milestone_ids:
        milestone = reads.get_milestone(db, milestone_id)
        if milestone.session_id != session_id:
            raise InvalidField(
                f"里程碑 {milestone_id} 不属于会话 {session_id}",
                detail={"milestone_id": milestone_id, "session_id": session_id},
            )
        if milestone.status is not MilestoneStatus.DONE:
            excluded.append(
                ExcludedItem(
                    source_kind="milestone",
                    source_ref=milestone_id,
                    content=milestone.title,
                    reason="里程碑还没达成 —— 没发生的事不能成为证据",
                )
            )
            continue

        statement = milestone.title
        if milestone.results:
            statement = f"{milestone.title}（成果：{milestone.results}）"
        included.append(
            SedimentationCandidate(
                source_kind="milestone",
                source_ref=milestone_id,
                statement=statement,
                target=TARGET_PREFERENCES,
                value=_pref_payload(statement),
                proposed_basis=Basis.EXPERIENCED,
            )
        )

    return SedimentationPlan(
        trigger=TriggerType.R4B, included=included, excluded=excluded
    )


def _get_summary(db: DbSession, summary_id: str):
    from app.session_domain.models import SessionSummary

    row = db.get(SessionSummary, summary_id)
    if row is None:
        raise MissingPrerequisite(
            f"总结不存在：{summary_id}", detail={"summary_id": summary_id}
        )
    return row


def _basis_from(raw: str | None, *, default: Basis) -> Basis:
    if raw is None:
        return default
    try:
        return Basis(raw)
    except ValueError as exc:
        raise InvalidField(
            f"未知的 basis：{raw}", detail={"basis": raw}
        ) from exc


def build_candidates(
    db: DbSession,
    session_id: str,
    *,
    trigger: TriggerType,
    stage_item_ids: list[str] | None = None,
    milestone_ids: list[str] | None = None,
    summary_id: str | None = None,
) -> SedimentationPlan:
    """按触发类型组装沉淀计划。**不写任何表** —— 调用方决定要不要提交。"""
    reads.get_session(db, session_id)  # 不存在就 404，先于任何分支

    if trigger is TriggerType.R4C:
        if not stage_item_ids:
            raise MissingPrerequisite(
                "R4c 手动沉淀要指明勾选了哪些 stage 条目",
                detail={"session_id": session_id},
            )
        return _stage_candidates(db, session_id, list(stage_item_ids))

    if trigger is TriggerType.R4A:
        return _summary_candidates(db, session_id, summary_id)

    if trigger is TriggerType.R4B:
        if not milestone_ids:
            raise MissingPrerequisite(
                "R4b 里程碑沉淀要指明是哪个里程碑",
                detail={"session_id": session_id},
            )
        return _milestone_candidates(db, session_id, list(milestone_ids))

    raise InvalidField(
        f"沉淀不接受触发类型 {trigger.value}：只有 R4a / R4b / R4c 会产生沉淀",
        detail={"trigger": trigger.value},
    )


__all__ = [
    "ExcludedItem",
    "SedimentationCandidate",
    "SedimentationPlan",
    "TARGET_EVIDENCE",
    "TARGET_PREFERENCES",
    "build_candidates",
]
