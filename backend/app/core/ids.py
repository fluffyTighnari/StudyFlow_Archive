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
#: 假说（P7）。**独立的序列**，不与证据共用 —— 共用一个计数器会让
#: 「`E-0007` 和 `H-0007` 是不是同一件事的两面」变成一个要解释的问题。
SCOPE_HYPOTHESIS = "hypothesis"

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

#: 领域勾选档案的版本：`DP-0001`。**按版本递增，不按用户递增** ——
#: 目前是单用户产品，而"第几版清单"比"谁的清单"更能说明一条勾选的来历。
SCOPE_DOMAIN_PROFILE = "domain_profile"

#: 一次对话压缩：`CX-0001`。**全局一个序列** —— 见 `next_compact_id`。
SCOPE_CONTEXT_COMPACT = "context_compact"

#: 侧边栏常驻模型的一条消息：`SM-0001`。
#: 与 `SCOPE_CHAT`（`CM-`）分开，因为它们是两张表、两种归属 —— 见 `SidebarMessage`。
SCOPE_SIDEBAR_MESSAGE = "sidebar_message"

#: 澄清问题（"任务不明确时反问"）：`CL-0001`。
#: 与 `SCOPE_CHAT` 分开：一条对话消息可以被删被压缩，
#: 而一个问题**问过就是问过** —— 它是"我什么时候说过我不知道"的证据。
SCOPE_CLARIFICATION = "clarification"

#: 句子标注（"输出可标注某句并看推断理由"，P15）：`AN-0001`。
#: 与 `SCOPE_CHAT` 分开：标注的生命周期**长于**被标注的那句话吗？不 ——
#: 它随消息一起走（外键 CASCADE）。分开的理由是**计数**：
#: "我给助手的话做了几次追问"是一个独立读数，混进消息序列就查不出来了。
SCOPE_ANNOTATION = "annotation"

#: 复盘之后的感想（P15）：`RF-0001`。
SCOPE_REFLECTION = "reflection"


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


def next_hypothesis_id(store: SequenceStore) -> str:
    """假说 id：`H-0001`。

    ★ 用 `H` 而不是 `C` 前缀：证据是 `E-0001`。两者在同一张日志里并排出现时，
    `E`（evidence，已试过）与 `H`（hypothesis，想试）一眼分得开 ——
    而 `C` 前缀会让人以为它是"条件和效果"的意思，与可信度无关。
    """
    return format_seq("H", store.next(SCOPE_HYPOTHESIS), 4)


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


def next_domain_profile_id(store: SequenceStore) -> str:
    """`DP-0001`。领域勾选档案的一版。

    与 `next_benchmark_run_id` 同族：**每次改动都是一行新的**，旧的留着。
    「我当时勾了哪些」因此永远可回看。
    """
    return format_seq("DP", store.next(SCOPE_DOMAIN_PROFILE), 4)


def next_compact_id(store: SequenceStore) -> str:
    """`CX-0001`。一次对话压缩。

    `X` = 压缩（eXcerpt）。**全局一个序列**，不按 scope 分段：
    压缩次数是个位数量级，而分段序列会让"总共压过几次"需要跨序列相加。
    """
    return format_seq("CX", store.next(SCOPE_CONTEXT_COMPACT), 4)


def next_sidebar_msg_id(store: SequenceStore) -> str:
    """`SM-0001`。侧边栏的一条消息。

    一个序列、不按会话分段：侧边栏**只有一个**（它就是那块常驻面板），
    所以"第几条"本身就是它的顺序。
    """
    return format_seq("SM", store.next(SCOPE_SIDEBAR_MESSAGE), 4)


def next_clarification_id(store: SequenceStore) -> str:
    """`CL-0001`。澄清问题（P13）。

    ★ 问题**不是**一条对话消息：对话消息会被压缩、会被清掉（侧边栏那一路），
    而"我问过、他还没答"这个事实必须活到答案回来为止 ——
    它挂在计划闸门上，被清掉就意味着闸门自己解开了。
    """
    return format_seq("CL", store.next(SCOPE_CLARIFICATION), 4)


def next_reflection_id(store: SequenceStore) -> str:
    """`RF-0001`。复盘之后的感想（P15）。

    与 `S-`（会话总结 `summary`）分开计数：一份是**复盘产物**，
    一份是**对它的补充**，两者在 `V-PAR` 的测量里不是一回事 ——
    混在一个序列里，"他复盘了几次"与"他回看了几次"就分不开了。
    """
    return format_seq("RF", store.next(SCOPE_REFLECTION), 4)


def next_annotation_id(store: SequenceStore) -> str:
    """`AN-0001`。句子标注（P15）。

    用 `AN-` 而不是复用 `N-`（笔记）：一条笔记是**用户自己写的**，
    一条标注是**用户对助手某句话的追问** —— 两者在界面上的位置、
    生命周期、以及"它算不算内容"都不同。
    """
    return format_seq("AN", store.next(SCOPE_ANNOTATION), 4)


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
