"""全局状态不变式自检（M0 §4.4 的四重保障④）。

## 这一层在防什么

前三重保障（依赖方向、能力令牌、判定表）都在**写入的当下**拦人。它们有一个共同的
盲区：**任何拿到 `session` 的代码都能直接构造 ORM 行**。`writes.py` 的令牌守卫只约束
「调用写函数」，不约束「自己 `session.add()`」——那件事由 ARCH-01/08 的 AST 断言在
**静态**层面拦，而 AST 只能看见 import，看不见 `session.execute(text("INSERT ..."))`。

自检就是这一层的兜底：**读一遍全局状态，问「有没有哪一行是没来路的」。**
它不阻止写入，它阻止「无痕写入之后没人发现」。

## 为什么模块在全局域，而 M0 §4.4 写的是 `core/`

文档把 `assert_global_invariants` 放在 `app/core/`。**这在架构上做不到**：
ARCH-04 明令 `app.core` 不得依赖任何上层域，而自检必须读全局表。

也不能放进 `app/router/`：那会让审计者住进被审计者里，而且 §4.4 的不变式
讲的是**全局域自身的状态**，不是路由策略。

因此它落在 `app.global_domain/`。连带的后果是 `M1.` 命名空间必须从路由层
下沉到 `app.global_domain/targets.py`（I2 要用它，而 ARCH-05 禁止全局域反向
依赖路由层）—— 见该模块的说明。这是 P5 期的一处**文档修正**，不是实现偷懒。

## 覆盖完整性由代码自己保证

`PROVENANCE` 必须**穷举** `Base.metadata` 的每一张表。新增一张表却没说清它的
可追溯机制，`I1` 会直接失败 —— 不允许「这张表反正没人写」式的默认豁免，
这是 §4.4 对豁免清单的原始要求。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InvariantViolation
from app.db import Base
from app.global_domain.enums import (
    Basis,
    ChannelObsSourceKind,
    IntentAction,
    IntentStatus,
    LifecycleStatus,
    RouterLogStatus,
)
from app.global_domain.models import (
    CEvidence,
    CEvidenceRevision,
    DomainProfile,
    M1BaselineCurrent,
    M1BaselineVersion,
    M1ChannelObs,
    M1Variable,
    M2Preference,
    RouterLog,
    UpdateIntentRow,
)
from app.global_domain.targets import (
    TargetNamespace,
    classify_target,
    variable_id_from_target,
)

#: `session.info` 上的开关键：本次事务是否要在写入后跑自检。
#
#: 方向是**反的**、也是刻意的：默认**开**（`session.info` 里没有这个键就自检），
#: 只有明确设置才关闭。这样任何「忘了配」的新入口都落在安全一侧 ——
#: 脚本、测试、将来的后台 worker 直接 `Router(session)` 就自动获得自检，
#: 而不需要先知道有这么一个开关存在。
CHECK_INVARIANTS_FLAG = "mouxue.check_invariants"


# --------------------------------------------------------------------------- #
# 每张全局表的可追溯机制（穷举）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Provenance:
    """一张表的可追溯机制。`kind` 取下面几个固定值之一。"""

    #: - `router_log_id`：自带该列，I1 逐行校验日志存在且 applied；
    #: - `via_pointer`：自身无列，可追溯性经指针指向的行**传递**（I1 + I3 合起来成立）；
    #: - `source_ref`：归因口径不是日志而是 `source_ref`（由 I6 校验）；
    #: - `catalog`：目录数据，随版本发布、运行时无人可写（用户裁定豁免）；
    #: - `ledger`：治理账本本身 —— 它就是留痕机制，不可能也不需要给自己留痕；
    #: - `infrastructure`：与领域无关的基础设施表；
    #: - `revision`（P4）：**沙箱域**里由一个 append-only 修订表覆盖每次改动的表；
    #: - `append_only`（P4）：修订表本身 —— 只追加，改不了就删不掉；
    #: - `sandbox_record`（P4）：沙箱域的原始记录，**刻意没有**历史机制（见下）。
    kind: str
    reason: str


PROVENANCE: dict[str, Provenance] = {
    "m1_baseline_version": Provenance(
        "router_log_id", "权威认知状态：每一条基线读数都必须能指认是哪次写入产生的"
    ),
    "m2_preference": Provenance(
        "router_log_id", "权威认知状态：用户声明即事实，因此更需要知道它是什么时候被谁写进来的"
    ),
    "c_evidence": Provenance(
        "router_log_id", "权威认知状态：C 条目会参与后续的上下文采用"
    ),
    "hypothesis": Provenance(
        "router_log_id",
        "权威认知状态：假说**不参与装配**，但它会进上下文（标为猜想），"
        "因此『他什么时候说想试什么』必须可追溯 —— 而追溯的落点仍是日志",
    ),
    "c_evidence_revision": Provenance(
        "router_log_id", "append-only 修订史：当前无人写（编辑/归档待裁定，缺口 A14），但机制先立着"
    ),
    "m1_baseline_current": Provenance(
        "via_pointer",
        "指针表，自身不存内容：它的每次变化都伴随一条新版本行，"
        "而那一行有日志。I3 保证指针键与版本一致，I1 保证版本有日志，两者合起来即传递可追溯",
    ),
    "m1_channel_obs": Provenance(
        "source_ref",
        "证据原料而非结论：它的归因要求不同 —— 需要的不是「哪次写入」，"
        "而是「来自哪个独立来源」（§7.3 的 ≥2 条门槛按 source_ref 去重）。"
        "一条 RouterLog 说不出「这是第几个 Session」，因此由 I6 校验 source_ref",
    ),
    "m1_variable": Provenance("catalog", "目录数据：随版本发布的静态定义，运行时无人可写（用户裁定，P1）"),
    "m1_variable_channel": Provenance("catalog", "目录数据：设计态覆盖，由 seeds 写"),
    "m2_strategy": Provenance("catalog", "B 策略库：目录数据，写入方式 = 迁移脚本 / 版本发布"),
    "benchmark_run": Provenance(
        "source_ref",
        "测量运行记录（用户裁定 G-03 = 甲）：它**不是**认知结论，而是结论的**依据** —— "
        "M1 读数的 `source_ref` 指向它，所以它的可追溯口径与 `m1_channel_obs` 同族："
        "需要的不是「哪次写入」，而是「这次分数是从哪一批题目与哪次作答算出来的」。"
        "它本身只追加、不改写：改一条 run 等于毁掉一批分数的证据，"
        "而那个动作不会让任何不变式变红 —— 所以由这张表的存在理由（而不是某条约束）"
        "来守住它。逐行校验在 `tests/test_benchmark_persistence.py`",
    ),
    "benchmark_response": Provenance(
        "source_ref",
        "作答与单题分：同样只追加。它是 `benchmark_run` 的下游，"
        "归因口径跟随 run（`source_ref`）—— 分开列两行是因为两张表的**写入时机**不同："
        "run 在开始时创建、结束时补分；response 在每题作答后立刻落，"
        "所以中途中断的运行也能留下「答到哪一题」这个事实",
    ),
    "domain_topic": Provenance(
        "catalog",
        "主题表（目录数据）：随版本发布的静态清单，运行时无人可写。"
        "源在 `app/seeds/domain_topics.json`，本表由 `seeds.loader` 物化 —— "
        "它存在的理由是写入层需要它来校验勾选里的 id 是否真的存在，"
        "而 ARCH-05 不允许 `app.global_domain` 去 import 那份 JSON 的读取器"
        "（`app.benchmark.topics`）",
    ),
    "domain_profile": Provenance(
        "router_log_id",
        "领域勾选档案（用户裁定 岔路 A = 乙）：口径与 `m2_preference` **完全相同** —— "
        "两者都是用户对自己状态的直接声明，而"
        "「用户声明即事实，因此更需要知道它是什么时候被谁写进来的」。"
        "它不过 `PRECHECK_TABLE`（它是测量的**参照**，不是关于特质的**断言**），"
        "但**照记 RouterLog** —— 记账与裁定是两件事，"
        "而「不裁定」绝不能被读成「不留痕」",
    ),
    "update_proposal": Provenance("ledger", "提案账本：由 Router 创建，其痕迹体现在引用它的日志上"),    "update_intent": Provenance("ledger", "意图账本：同上，I5 校验「已应用的意图 ⇄ 已应用的日志」"),
    "router_log": Provenance("ledger", "审计日志本身 —— 让日志给自己留痕是无穷回归"),
    "id_sequence": Provenance("infrastructure", "顺序号分配表，与领域无关（见 app/db.py）"),
    "llm_profile": Provenance(
        "infrastructure",
        "**模型调用配置**（哪家公司 / base_url / 模型 / 思考开关）——"
        "与认知状态无关：它不参与上下文采用、不被 Router 裁定、不进任何会话的"
        "决策依据，改它不会让任何一条基线或偏好变化。"
        "**密钥本体不在这张表里**（落在 var/secrets/，不进库、不进响应、不进日志），"
        "所以这张表即使被整体导出也不含任何可被冒用的东西。"
        "边界有测试盯着（tests/test_llm_config_boundary.py）：写完配置后 "
        "router_log 行数与认知四表行数都不变、I1–I7 仍通过",
    ),
    # ------------------------------------------------------------------ #
    # Session 域（P4）：沙箱内自由读写，但**每张表都要回答「改完还看得出吗」**
    # ------------------------------------------------------------------ #
    "session_input": Provenance(
        "revision",
        "任务定义区：每次改动写一条 input_revision，因此「我当初怎么说的 / 后来改成了什么」"
        "可逐字段回看 —— 而回看这件事正是防漂移的依据（M7 §3）",
    ),
    "input_revision": Provenance(
        "append_only", "定义区修订史本身：只追加，旧值因此不可能被抹掉"
    ),
    "clarification": Provenance(
        "sandbox_record",
        "★ 澄清问题（任务不明确时反问，P13）：它是**过程的痕迹**，不是关于用户的断言。"
        "三个理由说明为什么它不需要审计："
        "① 它只影响本会话的计划能不能改（闸门），不进任何全局表；"
        "② 它的答案**同时写进定义区**并留下 `input_revision` —— "
        "也就是说「我据此认定了什么」那一笔痕记在定义区的修订史上，"
        "而这里留的是「我问过、他什么时候答的」；"
        "③ 一行有生命周期（open → answered | dismissed）且**不删除**："
        "搁置也要写理由（库层 CHECK），所以「这条计划为什么是猜的」永远答得出。"
        "边界测试：`tests/session/test_clarify.py` 钉住「答完定义区真的有内容」",
    ),
    "session_motivation": Provenance(
        "revision",
        "动机状态：「为什么学」会变，而变的过程是判断「该不该继续」的关键信息，"
        "所以每次改动留一条 motivation_revision（带 basis：你说的还是推断的）",
    ),
    "motivation_revision": Provenance(
        "append_only", "动机修订史本身：只追加"
    ),
    "task_plan": Provenance(
        "revision",
        "计划对象：结构改动产生新的 plan_revision + 新版本行，"
        "因此旧计划与其当时的进度原地冻结、永久可读",
    ),
    "plan_revision": Provenance(
        "append_only",
        "计划修订史本身：只追加。`reason` 在库层是 NOT NULL —— "
        "「无理由不改计划」（M7 §9 第 3 条）落在约束上，而不是只写在提示词里",
    ),
    "plan_step": Provenance(
        "sandbox_record",
        "沙箱内的计划行：结构按 version_no 冻结已构成历史，进度（pending→done）"
        "刻意就地更新 —— 给它再记一份日志会把「标记一步做完」变成一次审计事件，"
        "而那不改变任何权威状态",
    ),
    "plan_checkpoint": Provenance(
        "sandbox_record",
        "检查点结果：与 plan_step 同口径。它只影响本会话内的建议，不进全局",
    ),
    "session": Provenance(
        "sandbox_record",
        "会话头部与状态机：状态转移不写审计日志（沙箱域没有 router_log），"
        "但每次转移都留下 activated_at / completed_at / abandoned_at 时间戳，"
        "因此「它什么时候完成的」仍可回答",
    ),
    "folder": Provenance(
        "sandbox_record", "领域文件夹：用户自己建的组织结构，改错就改回，不需要审计"
    ),
    "chat_message": Provenance(
        "sandbox_record",
        "对话原文：**原始记录不该被修订** —— 聊天记录一旦可改，"
        "「我当时到底说了什么」就失去意义。要改口就在新消息里改口",
    ),
    "annotation": Provenance(
        "sandbox_record",
        "★ 句子标注（输出可标注某句并看推断理由，P15）：它是**用户对助手的追问**，"
        "不是关于用户的断言，也不改变任何权威状态。"
        "三个理由说明为什么它不需要审计："
        "① 它指向一条消息里逐字存在的一段（`writes.annotate_message` 核对，"
        "对不上直接 422），所以它不可能凭空捏造一句谁都没说过的话；"
        "② 它**只读**：`basis.explain` 不写库、不调模型，"
        "理由是现算的（工具轨迹 + 条目 id + 重放的节标题），"
        "所以「凭什么这么说」的回答里没有一句是新编的；"
        "③ 它可以被用户删掉（解释权在用户手里），而删除不影响任何别的东西。"
        "边界测试：`tests/session/test_annotations.py` 钉住三条拒绝",
    ),
    "reflection": Provenance(
        "sandbox_record",
        "★ 复盘之后的感想（P15）：它是**用户自己写的字**，不是系统对他的断言 —— "
        "所以它不需要审计，但它的形状有理由："
        "① 按四要素（发生了什么 / 预期差异 / 原因 / 下次改变）分项存，"
        "因为 `V-PAR` 的 `measured` 通道声明的正是「复盘产物的四要素完整度」——"
        "拼成一坨存，那个「完整度」就只能靠再解析一段自然语言来回答；"
        "② 它**只落在会话里**：要变成全局认知仍然只有一条路（沉淀 → Router），"
        "所以它不进 `router_log` 也不需要令牌；"
        "③ 两道门槛写在代码里：状态必须在 `review`/`completed`/`abandoned`，"
        "且必须**已经复盘过**（有一条 `session_summary`）—— "
        "否则「感想」与「总结」会变成同一件事的两个入口。"
        "边界测试：`tests/session/test_reflection.py` 钉住两道门槛与空感想",
    ),
    "note": Provenance(
        "sandbox_record",
        "会话笔记：同上。它是草稿纸，不是声明 —— 声明走的是沉淀 → 路由那条路",
    ),
    "chat_attachment": Provenance(
        "sandbox_record",
        "★ 会话资料（用户上传的文本文件，P6+.5）：**用户自己给的材料**，"
        "不是认知结论。三个理由说明它为什么不该被治理语义覆盖："
        "① 它只在**这个会话**里被讨论用，不进任何其他会话的上下文；"
        "② 它对全局的唯一影响路径是模型据此提 `submit_global_update`，"
        "那一步走的是审核队列 —— 也就是说**真正需要审计的那一刻被审计的是提案，"
        "而不是这份文件**；"
        "③ 它必须可删（传错了要能撤），而 append-only 的修订机制会让"
        "「撤回一次误传」变成一次不可撤回的历史事件。"
        "边界测试：`tests/api/test_attachments_api.py` 逐表比对上传前后的行数",
    ),
    "stage_item": Provenance(
        "sandbox_record",
        "暂存条目：`status` 单向（active → deposited/discarded/archived）且永不删除，"
        "「当时想试什么、后来沉淀了没有」由 status + settled_at 回答",
    ),
    "milestone": Provenance(
        "sandbox_record", "会话级里程碑：达成时间由 done_at 记录，不改任何权威状态"
    ),
    "session_summary": Provenance(
        "sandbox_record",
        "完成总结：它本身是沙箱内的文本；它的**后果**（沉淀出的全局写入）"
        "由 Router 的日志负责留痕 —— 两个东西各留各的痕",
    ),
    "state_snapshot": Provenance(
        "sandbox_record",
        "动态状态（精力/压力/环境）：M7 §4.1 明确「可覆盖、不写 revision」，"
        "因为它描述的是**当时的状态**而不是一条会参与计划的声明；"
        "而它永不进 M1（M1 §5.5），所以也没有权威状态需要留痕",
    ),
    "sidebar_message": Provenance(
        "sandbox_record",
        "★ 侧边栏常驻模型的对话（P6+.6）：与原话同理 —— "
        "**原始记录不该被修订**。它与 `chat_message` 分表的理由见 `SidebarMessage` 的"
        "类文档（归属约束不同），但留痕姿态完全一致：追加、不改写。"
        "它没有任何进入全局的路径 —— 侧边栏要写全局也得走 `submit_global_update`，"
        "而那一步的痕迹记在 `router_log` 上",
    ),
    "context_compact": Provenance(
        "append_only",
        "★ 对话压缩记录（P6+.6）：把早期原话换成一段摘要。**它是本系统里"
        "唯一一种「有损的可追溯」**，所以它同时要满足两件相反的事："
        "① 压掉的原话**一行都没删**（仍在 `chat_message` 里，"
        "`covers_msg_ids` 指得出是哪几条）；"
        "② 每一次压缩**各留一行**，包括被后来的压缩折叠掉的那些 —— "
        "只留一个「当前摘要」字段会让每次覆盖都永久埋掉一段对话，"
        "而「它当时忘了什么」这个问题就再也答不上来。"
        "`append_only` 这个 kind 在这里的含义正是：**只增不改，"
        "且旧行不因为被折叠而失去意义**（`folds_compact_ids` 记的是折叠关系）。"
        "★ 另有一条边界由结构保证：摘要是**派生文本**，"
        "它拿不出 `chat_message.msg_id`，因此任何「用户说过…」的全局写入"
        "都不可能以它为来源 —— 见 `agent/context.py` 与"
        "`tests/agent/test_context_memory.py` 里那条断言",
    ),
}

#: I1 逐行校验的表：`(模型, 主键属性名①, 表名)`。
#:
#: ① 用途只是把违规行写成人能读懂的位置（`m1_baseline_version#M1-SE-001`），
#:   让报错直接可定位，而不是「有 3 行不对，你自己找」。
I1_ROW_LEVEL: tuple[tuple[Any, str], ...] = (
    (M1BaselineVersion, "version_id"),
    (M2Preference, "pref_id"),
    (CEvidence, "evidence_id"),
    (CEvidenceRevision, "rev_id"),
    # 领域勾选清单：**用户声明即事实**，与 `m2_preference` 同族 ——
    # 所以它照受 I1 的逐行校验（见 PROVENANCE 里的理由）。
    (DomainProfile, "profile_id"),
)

#: 不做逐行日志校验的表（从 `PROVENANCE` **派生**，不手写第二份清单）。
#:
#: 注意它包含 `m1_baseline_current`：那张表没有 `router_log_id` 列，
#: 但它属于「已声明机制」而非「无人看守」—— 其可追溯性由 I1（版本有日志）
#: 与 I3（指针键与版本一致）合起来传递地成立。
I1_EXEMPT_TABLES: frozenset[str] = frozenset(
    name for name, prov in PROVENANCE.items() if prov.kind != "router_log_id"
)


def unclassified_tables() -> set[str]:
    """既没被 I1 逐行覆盖、也没在 `PROVENANCE` 里声明机制的表。**必须为空。**"""
    covered = {model.__tablename__ for model, _ in I1_ROW_LEVEL}
    declared = set(PROVENANCE)
    return set(Base.metadata.tables) - covered - declared


# --------------------------------------------------------------------------- #
# 不变式
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Invariant:
    invariant_id: str
    title: str
    #: 返回人类可读的违规描述；空列表 = 通过。
    check: Callable[[Session], list[str]]


@dataclass(frozen=True)
class InvariantFailure:
    invariant_id: str
    title: str
    problems: tuple[str, ...]


def _check_i1(session: Session) -> list[str]:
    """I1：权威状态没有无痕变化。

    先查覆盖完整性，再逐行查日志 —— 顺序很重要：**一张新表没被分类时，
    「逐行检查全过」是假象**（根本没检查它），先报覆盖缺口才不会被误导。
    """
    problems: list[str] = []
    for table in sorted(unclassified_tables()):
        problems.append(
            f"{table} 既未被 I1 逐行覆盖，也没在 PROVENANCE 里声明可追溯机制"
            f"（§4.4 要求豁免必须显式列举）"
        )
    if problems:
        return problems

    for model, pk_attr in I1_ROW_LEVEL:
        table = model.__tablename__
        pk_col = model.__table__.c[pk_attr]
        stmt = (
            select(pk_col, model.router_log_id, RouterLog.status)
            .select_from(model)
            .outerjoin(RouterLog, RouterLog.log_id == model.router_log_id)
        )
        for row_pk, log_id, log_status in session.execute(stmt):
            where = f"{table}#{row_pk}"
            if log_id is None:
                problems.append(f"{where} 没有 router_log_id（无痕写入）")
            elif log_status is None:
                problems.append(f"{where} 指向不存在的 RouterLog {log_id}")
            elif log_status is not RouterLogStatus.APPLIED:
                problems.append(
                    f"{where} 指向 status={log_status.value} 的日志 {log_id}"
                    f"（只有 applied 的日志才能作为写入凭据）"
                )
    return problems


def _check_i2(session: Session) -> list[str]:
    """I2：已应用的日志必须指向一个**真实存在**的全局地址。

    两个方向都要查：① target 为空 —— 那就是「有东西变了但不知道是什么」；
    ② target 的命名空间没被注册，或注册了但地址背后没有实体（如 `M1.V-NOPE`）。
    后者是 A9 归一化出错时唯一会露出来的地方。
    """
    problems: list[str] = []
    known_variables = set(session.scalars(select(M1Variable.variable_id)))

    applied = session.scalars(
        select(RouterLog).where(RouterLog.status == RouterLogStatus.APPLIED)
    )
    for log in applied:
        if log.target is None:
            problems.append(f"日志 {log.log_id} 已应用，但没有 target（改了什么无从得知）")
            continue
        namespace = classify_target(log.target)
        if namespace is TargetNamespace.UNKNOWN:
            problems.append(f"日志 {log.log_id} 的 target {log.target!r} 不属于任何已注册命名空间")
        elif namespace is TargetNamespace.M1:
            variable_id = variable_id_from_target(log.target)
            if variable_id not in known_variables:
                problems.append(
                    f"日志 {log.log_id} 的 target {log.target!r} 指向不存在的变量 {variable_id}"
                )
    return problems


def _check_i3(session: Session) -> list[str]:
    """I3：当前版本指针必须自洽。

    主键保证「每个 (变量, 通道) 至多一行」，外键保证「版本存在」。
    **但没有任何约束保证指针键与该版本的 (变量, 通道) 一致** ——
    一次手写的 UPDATE 就能让 `(V-SE, measured)` 指向 `V-DC/measured` 的版本，
    而库层不会吭声。这正是 I3 要独立存在的理由。
    """
    problems: list[str] = []
    stmt = (
        select(M1BaselineCurrent, M1BaselineVersion)
        .select_from(M1BaselineCurrent)
        .outerjoin(
            M1BaselineVersion,
            M1BaselineVersion.version_id == M1BaselineCurrent.version_id,
        )
    )
    for pointer, version in session.execute(stmt):
        where = f"{pointer.variable_id}/{pointer.channel.value}"
        if version is None:
            problems.append(f"指针 {where} 指向不存在的版本 {pointer.version_id}")
            continue
        if version.variable_id != pointer.variable_id or version.channel is not pointer.channel:
            problems.append(
                f"指针 {where} 指向 {version.variable_id}/{version.channel.value}"
                f" 的版本 {version.version_id} —— 指针键与版本不一致"
            )
    return problems


def _check_i4(session: Session) -> list[str]:
    """I4：红线 3 的**事后**版本。

    判定表在写入当下拦 `inferred` 写参数；I4 反过来问「库里有没有哪条已应用的
    参数写入是越权的」。前者可能被新的绕过路径漏过，后者只看结果。
    `user_confirmed` 必须是 True —— `None` 也算违规：分不清「确认过」与「没记录」，
    就等于没有确认机制。
    """
    problems: list[str] = []
    applied = session.scalars(
        select(RouterLog).where(
            RouterLog.status == RouterLogStatus.APPLIED,
            RouterLog.action == IntentAction.UPDATE_PARAMETER,
        )
    )
    allowed = {Basis.MEASURED, Basis.EXPERIENCED}
    for log in applied:
        if log.basis not in allowed:
            problems.append(
                f"日志 {log.log_id} 是已应用的 update_parameter，"
                f"但 basis={log.basis.value if log.basis else None}（红线 3：只允许 measured/experienced）"
            )
        if log.user_confirmed is not True:
            problems.append(
                f"日志 {log.log_id} 是已应用的 update_parameter，"
                f"但 user_confirmed={log.user_confirmed!r}（红线 3：必须已确认）"
            )
    return problems


def _check_i5(session: Session) -> list[str]:
    """I5：已应用的意图与已应用的日志**一一对应**，且同一个意图不会被写两次。

    ⚠️ M0 §4.4 把 I5 写成「RouterLog 数量 >= 全局 revision 条目总数」。
    **那个式子无法检查**：「全局 revision 条目」在数据模型里没有定义，
    而且方向也不对 —— 一次提交里 N 条 intent 产生 N 条日志与 N 行，
    反过来一条日志也不一定只对应一行（回滚日志的 action 为空）。
    P5 把它落实成它真正想说的那句：**无双写**。
    """
    problems: list[str] = []

    applied_intents = set(
        session.scalars(
            select(UpdateIntentRow.intent_id).where(
                UpdateIntentRow.status == IntentStatus.APPLIED
            )
        )
    )
    applied_logs = list(
        session.scalars(
            select(RouterLog).where(
                RouterLog.status == RouterLogStatus.APPLIED,
                RouterLog.intent_ref.is_not(None),
            )
        )
    )
    logged_intents = {log.intent_ref for log in applied_logs}

    for intent_id in sorted(applied_intents - logged_intents):
        problems.append(f"意图 {intent_id} 已应用，却没有对应的 applied 日志")
    for intent_id in sorted(logged_intents - applied_intents):
        problems.append(f"日志声称意图 {intent_id} 已应用，但该意图不存在或不是 applied 状态")

    duplicates = session.execute(
        select(RouterLog.intent_ref, func.count())
        .where(
            RouterLog.status == RouterLogStatus.APPLIED,
            RouterLog.intent_ref.is_not(None),
        )
        .group_by(RouterLog.intent_ref)
        .having(func.count() > 1)
    )
    for intent_id, count in duplicates:
        problems.append(f"意图 {intent_id} 有 {count} 条 applied 日志（一次写入被记了多次）")

    return problems


def _check_i6(session: Session) -> list[str]:
    """I6（P5 新增）：观测必须可归因。

    为什么这条值得单列：`count_independent_session_evidence` 的口径是
    `COUNT(DISTINCT source_ref) WHERE source_ref IS NOT NULL`。一条
    `source_kind=session` 但 `source_ref` 为空的观测，**看起来是证据、数出来是 0**。
    于是用户会看到「记了三次还说不满两条」这种无法解释的拒绝。

    这类「静默归零」正是自检该抓的东西：不是崩溃、不是报错，
    而是一个数在那里慢慢变成没意义的值。
    """
    problems: list[str] = []
    attributable = (ChannelObsSourceKind.SESSION, ChannelObsSourceKind.BENCHMARK)
    stmt = select(
        M1ChannelObs.obs_id, M1ChannelObs.variable_id, M1ChannelObs.channel, M1ChannelObs.source_ref
    ).where(
        M1ChannelObs.source_kind.in_(attributable),
        (M1ChannelObs.source_ref.is_(None)) | (func.trim(M1ChannelObs.source_ref) == ""),
    )
    for obs_id, variable_id, channel, source_ref in session.execute(stmt):
        problems.append(
            f"观测 {obs_id}（{variable_id}/{channel.value}）声明为证据，"
            f"却没有可用的 source_ref（{source_ref!r}）——"
            f"它会计入 §7.3 的分母，但永远数不进分子"
        )
    return problems


def _check_i7(session: Session) -> list[str]:
    """I7（P6 新增，缺口 A20）：偏好的「谁取代谁」必须自洽。

    两条子句，都在防同一件事 —— **一对互相矛盾的约束同时生效，而系统不知道**：

    (a) 同一个非空 `target_field` 下至多一条 active。槽位是装配位置，
        两条 active 抢同一个槽位时，"放在哪"就没有答案了；
    (b) 一条 active 偏好的 `supersedes_pref_id` 指向的那条**不能还是 active**。
        否则「我推翻了它」与「它仍然算数」同时成立。

    为什么值得单列成不变式：A18 之后**两条 active 声明都是硬的**，
    于是「我讨厌网课」和「我能接受精品网课」会一起进入计划生成，
    而它们互相矛盾。原先这件事只靠 `m2_policy` 记得归档 ——
    而"某个策略记得做某件事"是**希望**，不是保证。
    有 I7 之后，任何绕过策略写进来的矛盾都会在**写入的同一个事务里**被抓住。
    """
    problems: list[str] = []

    rows = list(session.scalars(select(M2Preference)))
    active_by_id = {
        row.pref_id: row for row in rows if row.status is LifecycleStatus.ACTIVE
    }

    # (a) 槽位唯一
    by_slot: dict[str, list[str]] = {}
    for row in active_by_id.values():
        if row.target_field:
            by_slot.setdefault(row.target_field, []).append(row.pref_id)
    for target_field, pref_ids in sorted(by_slot.items()):
        if len(pref_ids) > 1:
            problems.append(
                f"装配槽位 {target_field} 上有 {len(pref_ids)} 条 active 偏好"
                f"（{'、'.join(sorted(pref_ids))}）—— "
                f"「放在哪」没有唯一答案，而旧的那条本该被归档"
            )

    # (b) 被推翻的那条不能再生效
    for row in sorted(active_by_id.values(), key=lambda r: r.pref_id):
        target_id = row.supersedes_pref_id
        if target_id is None:
            continue
        if target_id == row.pref_id:
            problems.append(f"偏好 {row.pref_id} 声称推翻自己")
            continue
        victim = active_by_id.get(target_id)
        if victim is not None:
            problems.append(
                f"偏好 {row.pref_id} 声称推翻 {target_id}，"
                f"但 {target_id} 仍然是 active —— "
                f"「我推翻了它」与「它还算数」不能同时成立"
            )

    return problems


INVARIANTS: tuple[Invariant, ...] = (
    Invariant("I1", "权威状态没有无痕变化（且每张表都声明了可追溯机制）", _check_i1),
    Invariant("I2", "已应用的日志指向真实存在的全局地址", _check_i2),
    Invariant("I3", "当前版本指针与它指向的版本自洽", _check_i3),
    Invariant("I4", "已应用的参数写入满足红线 3（measured/experienced + 已确认）", _check_i4),
    Invariant("I5", "已应用的意图与日志一一对应，且无双写", _check_i5),
    Invariant("I6", "声明为证据的观测必须可归因（source_ref 非空）", _check_i6),
    Invariant("I7", "偏好「谁取代谁」自洽（槽位唯一 · 被推翻者不再 active）", _check_i7),
)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def check_global_invariants(session: Session) -> list[InvariantFailure]:
    """跑完全部不变式，返回失败项（空列表 = 全过）。**只读，不修改任何东西。**"""
    failures: list[InvariantFailure] = []
    for invariant in INVARIANTS:
        problems = invariant.check(session)
        if problems:
            failures.append(
                InvariantFailure(
                    invariant_id=invariant.invariant_id,
                    title=invariant.title,
                    problems=tuple(problems),
                )
            )
    return failures


def format_failures(failures: list[InvariantFailure]) -> str:
    lines: list[str] = []
    for failure in failures:
        lines.append(f"[{failure.invariant_id}] {failure.title}（{len(failure.problems)} 处）")
        lines.extend(f"    - {problem}" for problem in failure.problems)
    return "\n".join(lines)


def assert_global_invariants(session: Session) -> None:
    """全过则静默返回；有任何失败抛 `InvariantViolation`。

    **一次报全部**而不是遇到第一个就抛：不变式之间是独立的，
    一处违规往往伴随另一处（无痕写入同时也会让 I5 的计数对不上），
    只报第一条会让人以为修完那一条就好了。
    """
    failures = check_global_invariants(session)
    if not failures:
        return
    raise InvariantViolation(
        f"全局不变式自检失败（{len(failures)} 项）：\n{format_failures(failures)}",
        detail={
            "failures": [
                {
                    "invariant_id": failure.invariant_id,
                    "title": failure.title,
                    "problems": list(failure.problems),
                }
                for failure in failures
            ]
        },
    )


def set_invariants_enabled(session: Session, enabled: bool) -> None:
    """由请求边界（`app.api.deps`）设置，供 `Router` 读取。

    配置住在**请求边界**，判定住在**写入边界** —— 两边都不需要知道对方的实现，
    而 Router 不依赖 `Settings`（它不该知道配置从哪来）。
    """
    session.info[CHECK_INVARIANTS_FLAG] = enabled


def invariants_enabled(session: Session) -> bool:
    """没设置过就是**开**。默认关闭会让「忘了配」变成一处静默的保障缺口。"""
    return bool(session.info.get(CHECK_INVARIANTS_FLAG, True))


def iter_invariants() -> Iterator[Invariant]:
    """给测试用：逐条枚举，以便「每条不变式都必须有一个故意破坏它的用例」可被强制。"""
    yield from INVARIANTS
