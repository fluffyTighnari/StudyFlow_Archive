"""Session 域（L3 沙箱，M0 §3 / M7 v0.4）。

## 这个域是什么

`folder`（长期领域）→ `session`（一个主题）。会话内部的一切 ——
任务定义区、动机、计划、stage、聊天、笔记、里程碑、总结、状态快照 ——
**只在会话里生效**，不写全局认知。

## 它与全局域的分界线（一句话）

**会话内发生的事，要变成个人认知，必须经过「沉淀」这一道手续**：
沙箱产出**候选条目** → `app/api/` 把它们映射成 `UpdateIntent` →
`Router` 按判定表裁定 → 落 `router_log`。

沙箱域**不认识 Router**（架构规则 ARCH-11）：
`sedimentation.build_candidates()` 返回的是本域自己的数据类，
映射发生在 `app/api/`。这样"顺手写一下全局"在代码结构上就不存在。

## 模块

| 模块 | 职责 | 不许做什么 |
|---|---|---|
| `enums` | 本域取值域 | 不重定义全局取值（`Basis` 等从全局域引入） |
| `models` | 16 张表 | 不含任何写入逻辑 |
| `guards` | 状态机 + 写准入表（数据） | 不认识具体表 |
| `reads` | 读路径（无需令牌） | 不写 |
| `writes` | 写路径（三条约束） | 不 commit、不碰全局 |
| `schemas` | DTO（ORM → HTTP 的唯一转换层） | 不含业务判断 |
| `state` | 侧页全量状态（单一状态源） | 不做权限判断 |
| `context` | 讨论上下文包（确定性组装） | **不调 LLM**、不排序之外的裁剪 |
| `sedimentation` | 沉淀候选条目（纯函数） | 不认识 Router、不写任何表 |
"""

from __future__ import annotations

from app.session_domain.enums import (
    ChatRole,
    ChangedBy,
    CheckpointResult,
    InputConfidence,
    Level,
    MilestoneStatus,
    MotivationType,
    ObstacleType,
    PlanStepStatus,
    SessionOperation,
    SessionState,
    StageItemStatus,
    StageItemType,
)

__all__ = [
    "ChatRole",
    "ChangedBy",
    "CheckpointResult",
    "InputConfidence",
    "Level",
    "MilestoneStatus",
    "MotivationType",
    "ObstacleType",
    "PlanStepStatus",
    "SessionOperation",
    "SessionState",
    "StageItemStatus",
    "StageItemType",
]
