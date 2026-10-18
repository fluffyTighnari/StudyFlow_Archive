"""Session 域的取值域（M7 v0.4）。

与 `global_domain.enums` 的分工：本模块只放**沙箱域自己的**取值
（会话状态、stage 条目类型、聊天角色……）；凡是全局也用的取值
（`Basis`、`TriggerType`……）**一律从 `global_domain.enums` 引入**，
绝不在这里重定义 —— 两张表里出现两种拼法的同一个概念，是这类系统里最难查的 bug。

一条约定：**枚举的 `.value` 就是落库与 JSON 线上的值**（见 `core.sqltypes.enum_col`）。
因此 `ObstacleType` 的 value 是中文 —— M7 §4 的 JSON 里写的就是中文，
而 `name` 是 ASCII，这样代码可读、线格式与文档一致。**不要**为了"整洁"把它们改成英文：
那会让已经写进文档的示例与库里的取值分叉。
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    """M7 §8 的状态机。转移表见 `session_domain.guards.TRANSITIONS`。"""

    DRAFT = "draft"
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ChangedBy(StrEnum):
    """侧页手动改 vs Agent 改（M7 §3/§4 的 `by` 字段）。"""

    USER = "user"
    AGENT = "agent"


class InputConfidence(StrEnum):
    """M7 §3 `intent_confidence`：输入层由「用户 prompt 分析 LLM」解析，只提取不编造。

    `missing` 是**第三态**而不是空字符串：它表示「问了但用户没说」，
    与「还没问」需要在界面上分开呈现（前者要回显确认，后者要补问）。
    """

    STATED = "stated"
    INFERRED = "inferred"
    MISSING = "missing"


class MotivationType(StrEnum):
    INTRINSIC = "intrinsic"
    EXTRINSIC = "extrinsic"
    MIXED = "mixed"


class Level(StrEnum):
    """精力 / 压力 / 动机强度的共用三档。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ObstacleType(StrEnum):
    """M7 §4 的障碍类型。**value 是中文** —— 见模块文档。"""

    STARTUP_FRICTION = "启动阻力"
    PREREQUISITE_GAP = "前置知识缺口"
    TIME_CONSTRAINT = "时间约束"
    EMOTIONAL_STATE = "情绪状态"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    DOING = "doing"
    DONE = "done"
    SKIPPED = "skipped"


class CheckpointResult(StrEnum):
    """M2-A4 的检查点结果。由 **UI 一键记录**（M0 §11-A2，不新增 LLM 工具）。"""

    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"


class StageItemType(StrEnum):
    """M7 §7.1 的三种暂存条目。"""

    TEMP_PREF = "temp_pref"
    IDEA = "idea"
    PLAN_OVERRIDE = "plan_override"


class StageItemStatus(StrEnum):
    """stage 条目的归宿（**用户裁定，P4**）。

    M7 §7.1/§8 的字面写法是「结束即弃」「其余丢弃」。用户裁定改为
    **保留行 + 标状态**，理由与「归档不是删除」同一套：
    你永远不会知道自己当时跳过了什么，而「我当时想试试…」本身是信息。

    因此**没有** `DELETED`：这张表只增不改终态，一条路都通向"留下痕迹"。
    """

    ACTIVE = "active"
    #: 已沉淀为全局写入（`result_ref` 指向那次提案）。
    DEPOSITED = "deposited"
    #: 用户明确不要了。
    DISCARDED = "discarded"
    #: 会话结束时既没沉淀也没被明确丢弃 —— 默认归宿。
    ARCHIVED = "archived"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SessionOperation(StrEnum):
    """**能被状态机拒绝的写操作**。

    枚举而不是散落的字符串，是因为这些取值要出现在
    `guards.WRITE_GUARDS` 这张表里，而表要能被测试逐行穷举 ——
    校验逻辑写成 `if state == "completed": raise` 就穷举不了了。
    """

    UPDATE_INPUT = "update_input"
    UPDATE_MOTIVATION = "update_motivation"
    UPDATE_SNAPSHOT = "update_snapshot"
    CREATE_PLAN = "create_plan"
    REVISE_PLAN = "revise_plan"
    MARK_STEP = "mark_step"
    RECORD_CHECKPOINT = "record_checkpoint"
    ADD_STAGE_ITEM = "add_stage_item"
    SETTLE_STAGE_ITEM = "settle_stage_item"
    ADD_CHAT = "add_chat"
    ADD_NOTE = "add_note"
    #: 上传会话资料（P6+.5）。**与 ADD_CHAT 分开口子**：资料是"讨论的输入"，
    #: 而起草阶段（还没有计划）正是最想先把它传上来的时候。
    ADD_ATTACHMENT = "add_attachment"
    #: 删掉一份资料。与上传同域 —— 它是"整理材料"，不是"补一句笔记"。
    ATTACHMENT_REMOVE = "attachment_remove"
    ADD_MILESTONE = "add_milestone"
    COMPLETE_MILESTONE = "complete_milestone"
    WRITE_SUMMARY = "write_summary"
    TRANSITION = "transition"


#: 终态。终态**不是**「什么都不能做」—— `COMPLETED` 仍允许写笔记（用户裁定）。
TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {SessionState.COMPLETED, SessionState.ABANDONED}
)

#: 允许「沉淀」（R4c）的状态。`ABANDONED` 也在内：M7 §8 明确
#: 「abandoned（部分条目可沉淀，其余丢弃）」。
SEDIMENTABLE_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.ACTIVE,
        SessionState.REVIEW,
        SessionState.ABANDONED,
    }
)
