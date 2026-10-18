"""顺序 ID 分配（人类可读 ID，如 `RL-0001`、`M1-SE-001`、`E-0001`）。

纯逻辑 + 协议；持久化实现由 `app.db.DbSequenceStore` 提供，因此本模块
不依赖任何上层域（架构规则 ARCH-04）。
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_SEQ_RE = re.compile(r"^(?P<prefix>.+?)-(?P<num>\d+)$")


@runtime_checkable
class SequenceStore(Protocol):
    """顺序号来源。"""

    def next(self, scope: str) -> int:
        """为 scope 分配下一个正整数（从 1 开始）。"""
        ...


def format_seq(prefix: str, n: int, width: int = 3) -> str:
    """`("M1-SE", 1)` → `"M1-SE-001"`。"""
    if n < 1:
        raise ValueError(f"顺序号必须 >= 1，收到 {n}")
    return f"{prefix}-{str(n).zfill(width)}"


def parse_seq(value: str) -> tuple[str, int]:
    """`"M1-SE-001"` → `("M1-SE", 1)`。用于反向解析与审计。"""
    match = _SEQ_RE.match(value)
    if not match:
        raise ValueError(f"不是合法的顺序 ID：{value!r}")
    return match.group("prefix"), int(match.group("num"))


# --------------------------------------------------------------------------- #
# 各 ID 族的分配函数：scope 决定计数器分组，prefix 决定可读前缀
# --------------------------------------------------------------------------- #

#: RouterLog：RL-0001（路由层 §7）
SCOPE_ROUTER_LOG = "router_log"

#: UpdateProposal：UP-0001（M0 §5）
SCOPE_PROPOSAL = "update_proposal"

#: UpdateIntent：UI-0001（M0 §7.2）
SCOPE_INTENT = "update_intent"

#: C 个性化证据：E-0001（M2 §4.2）
SCOPE_C_EVIDENCE = "c_evidence"

#: M1 基线版本：M1-<变量缩写>-<3位>（M1 §3.3 的 log_id 格式）
SCOPE_M1_BASELINE_PREFIX = "m1_baseline"

#: M1 通道观测：OB-0001
SCOPE_M1_OBS = "m1_obs"

#: M2 偏好：P-0001
SCOPE_M2_PREFERENCE = "m2_preference"

# --- Session 域（P4） ------------------------------------------------------ #

#: 领域文件夹：F-0001
SCOPE_FOLDER = "folder"

#: 会话：S-0001
SCOPE_SESSION = "session"

#: stage 条目：STG-0001
SCOPE_STAGE_ITEM = "stage_item"

#: 计划对象：PL-0001
SCOPE_PLAN = "task_plan"

#: 任务定义区修订：IR-0001
SCOPE_INPUT_REV = "input_revision"

#: 动机修订：MR-0001
SCOPE_MOTIVATION_REV = "motivation_revision"

#: 计划修订：PR-0001
SCOPE_PLAN_REV = "plan_revision"

#: 会话笔记：N-0001
SCOPE_NOTE = "note"

#: 对话消息：CM-0001
SCOPE_CHAT = "chat_message"

#: 里程碑：MS-0001
SCOPE_MILESTONE = "milestone"

#: 会话资料（上传的附件）：AT-0001（P6+.5）
SCOPE_ATTACHMENT = "chat_attachment"

#: 完成总结：SS-0001
SCOPE_SUMMARY = "session_summary"

#: 计划步骤：`ST-001`（**按计划各自计数**，跨版本稳定）
SCOPE_PLAN_STEP_PREFIX = "plan_step"

#: 检查点：`CP-001`（按计划各自计数）
SCOPE_PLAN_CP_PREFIX = "plan_checkpoint"

#: 基准运行：`BR-0001`
SCOPE_BENCHMARK_RUN = "benchmark_run"

#: 基准作答：`BS-0001`
SCOPE_BENCHMARK_RESPONSE = "benchmark_response"


def _variable_abbrev(variable_id: str) -> str:
    """`"V-SE"` → `"SE"`（M1 §3.3 日志格式用变量缩写）。"""
    return variable_id[2:] if variable_id.startswith("V-") else variable_id


def next_router_log_id(store: SequenceStore) -> str:
    return format_seq("RL", store.next(SCOPE_ROUTER_LOG), 4)


def next_proposal_id(store: SequenceStore) -> str:
    return format_seq("UP", store.next(SCOPE_PROPOSAL), 4)


def next_intent_id(store: SequenceStore) -> str:
    return format_seq("UI", store.next(SCOPE_INTENT), 4)


def next_evidence_id(store: SequenceStore) -> str:
    return format_seq("E", store.next(SCOPE_C_EVIDENCE), 4)


def next_preference_id(store: SequenceStore) -> str:
    return format_seq("P", store.next(SCOPE_M2_PREFERENCE), 4)


def next_m1_obs_id(store: SequenceStore) -> str:
    return format_seq("OB", store.next(SCOPE_M1_OBS), 4)


def next_m1_baseline_id(store: SequenceStore, variable_id: str) -> str:
    """M1 §3.3：`M1-<变量缩写>-<3位序号>`，按变量各自计数。"""
    abbrev = _variable_abbrev(variable_id)
    n = store.next(f"{SCOPE_M1_BASELINE_PREFIX}:{variable_id}")
    return format_seq(f"M1-{abbrev}", n, 3)


# --------------------------------------------------------------------------- #
# Session 域（P4）
# --------------------------------------------------------------------------- #


def next_folder_id(store: SequenceStore) -> str:
    return format_seq("F", store.next(SCOPE_FOLDER), 4)


def next_session_id(store: SequenceStore) -> str:
    return format_seq("S", store.next(SCOPE_SESSION), 4)


def next_stage_item_id(store: SequenceStore) -> str:
    return format_seq("STG", store.next(SCOPE_STAGE_ITEM), 4)


def next_plan_id(store: SequenceStore) -> str:
    return format_seq("PL", store.next(SCOPE_PLAN), 4)


def next_input_rev_id(store: SequenceStore) -> str:
    return format_seq("IR", store.next(SCOPE_INPUT_REV), 4)


def next_motivation_rev_id(store: SequenceStore) -> str:
    return format_seq("MR", store.next(SCOPE_MOTIVATION_REV), 4)


def next_plan_rev_id(store: SequenceStore) -> str:
    return format_seq("PR", store.next(SCOPE_PLAN_REV), 4)


def next_note_id(store: SequenceStore) -> str:
    return format_seq("N", store.next(SCOPE_NOTE), 4)


def next_chat_msg_id(store: SequenceStore) -> str:
    return format_seq("CM", store.next(SCOPE_CHAT), 4)


def next_milestone_id(store: SequenceStore) -> str:
    return format_seq("MS", store.next(SCOPE_MILESTONE), 4)


def next_attachment_id(store: SequenceStore) -> str:
    """`AT-0001`。附件的身份由它决定，**不由文件名决定** ——
    两份重名的文件是两份资料，而同一个文件的两次上传应该被认出来（靠 `sha256`）。
    """
    return format_seq("AT", store.next(SCOPE_ATTACHMENT), 4)


def next_summary_id(store: SequenceStore) -> str:
    return format_seq("SS", store.next(SCOPE_SUMMARY), 4)


def next_benchmark_run_id(store: SequenceStore) -> str:
    """`BR-0001`。一次运行的**身份** —— `M1BaselineVersion.source_ref` 存的就是它。

    因此它必须在运行**开始时**就分配好：分数是在结束时才算出来的，
    而"这次运行的作答"要在中途就落库（否则中断的运行留不下任何东西）。
    """
    return format_seq("BR", store.next(SCOPE_BENCHMARK_RUN), 4)


def next_benchmark_response_id(store: SequenceStore) -> str:
    """`BS-0001`。逐题一行。"""
    return format_seq("BS", store.next(SCOPE_BENCHMARK_RESPONSE), 4)


def next_plan_step_id(store: SequenceStore, plan_id: str) -> str:
    """`ST-001`。按计划各自计数，因此**跨版本稳定** —— 一个步骤改几次措辞，
    它在界面上的身份不变，它自己的进度也就不会随版本漂移。
    """
    n = store.next(f"{SCOPE_PLAN_STEP_PREFIX}:{plan_id}")
    return format_seq("ST", n, 3)


def next_plan_cp_id(store: SequenceStore, plan_id: str) -> str:
    """`CP-001`（M0 §11-A7 统一用 `cp_id`，不叫 `checkpoint_id`）。"""
    n = store.next(f"{SCOPE_PLAN_CP_PREFIX}:{plan_id}")
    return format_seq("CP", n, 3)


def plan_step_row_id(plan_id: str, version_no: int, step_id: str) -> str:
    """`PL-0001-v2-ST-003` —— 复合主键的确定性拼法。

    **刻意不用计数器**：这一行是「步骤 X 在版本 v 的样子」，
    它的身份由三件事唯一决定，交给计数器反而会造出两个不同的 row_id 去描述同一件事。
    """
    return f"{plan_id}-v{version_no}-{step_id}"


def plan_cp_row_id(plan_id: str, version_no: int, cp_id: str) -> str:
    return f"{plan_id}-v{version_no}-{cp_id}"
