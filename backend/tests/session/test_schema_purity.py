"""沙箱与权威域之间的**结构性隔离** —— 表结构层面。

这一组测试的共同主题是：**沙箱里做的事，不该在权威域留下任何痕迹。**
它不是靠"我记得没写"来保证的，而是靠数量对比：跑完一整个会话生命周期，
`router_log` / `m1_baseline_version` / `m2_preference` 的行数必须**一个都没变**。

唯一的例外是沉淀 —— 而那正是本文件最后一节要验的事。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

# **显式 import 定义表的模块，让"一共有几张表"与测试收集顺序无关。**
#
# 这是被一次真实的摇摆逼出来的：只跑 `tests/session` 时 `Base.metadata` 里是 30 张
# （`llm_profile` 没被 import），而跑全量时是 31 —— 因为别的测试文件碰巧
# import 了 `app.llm_config`。于是同一条断言在两种跑法下答案不同，
# 而"表数量"恰恰是这组测试最想钉住的东西。`main.py` 的 `create_app` 也是这样
# 显式 import 的（表在 `lifespan` 之前必须全部就位），这里照做。
import app.llm_config  # noqa: F401  (注册 llm_profile)
import app.session_domain.models  # noqa: F401  (注册 17 张沙箱表)
from app.core.ids import next_intent_id
from app.db import Base, DbSequenceStore
from app.global_domain.enums import TriggerType
from app.global_domain.invariants import PROVENANCE, unclassified_tables
from app.global_domain.models import (
    M1BaselineVersion,
    M1Variable,
    M2Preference,
    RouterLog,
)
from app.router import SubmitRequest, UpdateIntent
from app.session_domain import reads, writes
from app.session_domain.enums import PlanStepStatus, StageItemType
from app.session_domain.models import StateSnapshot

from tests.session.test_lifecycle import _live_session

#: P4 新增的表（M0 §6.2）。写成显式清单，而不是 `>=` 之类的宽松断言 ——
#: 表的增减都是设计决定，应该逼出一次讨论，而不是自动通过。
SESSION_TABLES = frozenset(
    {
        "folder",
        "session",
        "session_input",
        "input_revision",
        "session_motivation",
        "motivation_revision",
        "task_plan",
        "plan_revision",
        "plan_step",
        "plan_checkpoint",
        "chat_message",
        "chat_attachment",
        "note",
        "stage_item",
        "milestone",
        "session_summary",
        "state_snapshot",
        #: 澄清问题（任务不明确时反问，P13）。它属于会话域：
        #: 一行问题挂在某个会话上，答案直接写进那个会话的定义区。
        "clarification",
        #: 句子标注（输出可标注某句并看推断理由，P15）。它指向本会话的一条
        #: 助手消息里逐字存在的一段 —— 跨出这个会话，那个理由就不成立了。
        "annotation",
        #: 复盘之后的感想（P15）。四要素分项存 —— V-PAR 评的就是它的完整度。
        "reflection",
    }
)

#: 三个版本域（见 `writes` 模块文档）。只有这三张表带 `version` 列。
VERSIONED_TABLES = frozenset({"session", "session_input", "task_plan"})


def _count(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


# --------------------------------------------------------------------------- #
# 每张表都要声明"改完还看得出吗"
# --------------------------------------------------------------------------- #


def test_every_table_is_registered_and_classified(session: Session) -> None:
    """`unclassified_tables()` 必须为空。一加表就变红，是刻意的。"""
    assert unclassified_tables() == set()
    assert SESSION_TABLES <= set(Base.metadata.tables)
    # 13 张全局表 + 17 张沙箱表（P6+.5 加了 `chat_attachment`）
    # + 1 张基础设施表（`llm_profile`，P6.x 的模型调用配置）
    # + 2 张基准运行表（`benchmark_run` / `benchmark_response`，
    #   Benchmark 设计期裁定 G-03 = 甲：分数必须能追回答案）
    # + 2 张领域勾选表（`domain_topic` 目录 / `domain_profile` 档案，
    #   裁定 岔路 A = 乙：勾选进库并过路由的记账）
    # + 2 张对话记忆表（`context_compact` 压缩记录 / `sidebar_message`
    #   侧边栏常驻模型的对话，P6+.6）
    # + 1 张假说表（`hypothesis`，P7 语义拆分：**想试但还没试**的具体做法）。
    #   它不参与装配，但会进上下文并标为猜想，所以它是全局认知，
    #   必须过 Router、必须有日志 —— 也就必须在 `PROVENANCE` 里表态。
    # + 1 张标注表（`annotation`，P15：用户对助手某句话的追问）。
    #   它**不是**关于用户的断言（`PROVENANCE` 里说明为什么不需要审计），
    #   但它必须挂在一个会话上 —— 所以归沙箱（17 → 18 张）。
    # + 1 张感想表（`reflection`，P15：复盘之后按指引写的四要素产物）。
    #   它**是**用户自己写的字，所以同样不需要审计；但它必须挂在会话上，
    #   而且形状有理由（分项存，V-PAR 评的就是那份完整度）→ 沙箱（18 → 19 张）。
    assert len(Base.metadata.tables) == 41


def test_every_declared_provenance_explains_itself() -> None:
    """没有理由的豁免，就是"没人看守"的另一种说法。"""
    for name, provenance in PROVENANCE.items():
        assert provenance.reason.strip(), f"{name} 没有说明可追溯机制"
        assert provenance.kind.strip(), f"{name} 没有声明 kind"


def test_sandbox_tables_declare_a_sandbox_mechanism() -> None:
    """沙箱的 16 张表必须落在三个沙箱口径之一，不能借用全局域的口径。

    `router_log_id` 尤其不行：沙箱表的每一次写入都不会产生审计日志，
    声称"本表自带 router_log_id"会让 I1 立刻失败 —— 而更重要的是，
    那等于声称沙箱写入是受治理的全局写入，与 §6.2 的定义直接矛盾。
    """
    allowed = {"revision", "append_only", "sandbox_record"}
    for name in sorted(SESSION_TABLES):
        assert PROVENANCE[name].kind in allowed, (
            f"{name} 用了 {PROVENANCE[name].kind!r}，"
            f"沙箱表只能是 {sorted(allowed)} 之一"
        )


def test_revision_tables_have_a_matching_append_only_companion() -> None:
    """声明 `revision` 的表，必须真的存在那张 append-only 修订表。"""
    pairs = {
        "session_input": "input_revision",
        "session_motivation": "motivation_revision",
        "task_plan": "plan_revision",
    }
    kinds = {name: PROVENANCE[name].kind for name in PROVENANCE}
    for subject, journal in pairs.items():
        assert kinds[subject] == "revision"
        assert kinds[journal] == "append_only"
        assert journal in Base.metadata.tables


def test_only_three_tables_carry_a_version_column(session: Session) -> None:
    """三个版本域是设计决定，不是实现细节 —— 多一个就会多一把锁、多一处 409。"""
    carrying = {
        name
        for name, table in Base.metadata.tables.items()
        if "version" in table.columns
    }
    assert carrying == VERSIONED_TABLES


def test_stage_items_share_the_session_version(session: Session) -> None:
    """stage 没有自己的 `version`（M0 §6.2 要求 stage 带版本，但它没有头表）。

    这条测试把那个取舍写成可检查的事实：`stage_item` 无 `version` 列，
    而它的乐观锁由 `session.version` 承担（见
    `test_lifecycle.test_stage_write_does_bump_the_session_version`）。
    """
    assert "version" not in Base.metadata.tables["stage_item"].columns
    assert "version" in Base.metadata.tables["session"].columns


# --------------------------------------------------------------------------- #
# 沙箱写入不碰全局
# --------------------------------------------------------------------------- #


def _global_footprint(db: Session) -> tuple[int, int, int, int]:
    return (
        _count(db, RouterLog),
        _count(db, M1BaselineVersion),
        _count(db, M2Preference),
        _count(db, M1Variable),
    )


def test_a_whole_session_lifecycle_leaves_no_trace_in_the_authoritative_domain(
    session: Session,
) -> None:
    """一整个会话走完，全局域**一个字节都没变**，也没有一条审计日志。

    注意这里刻意包含"定义区写了目标、激活、建计划、改计划、标步骤、记检查点、
    加 stage、写聊天、写笔记、达成里程碑、写总结、归档"—— 也就是沙箱里能做的全部事情。
    """
    before = _global_footprint(session)

    _folder, row = _live_session(session)
    plan = writes.create_plan(
        session,
        row.session_id,
        steps=[
            writes.StepDraft(action="通读 unit 1", output="生词表"),
            writes.StepDraft(action="精读 unit 1", output="10 个例句"),
        ],
        checkpoints=[
            writes.CheckpointDraft(
                at_step="ST-002", check_method="默写", pass_criteria=">=80%"
            )
        ],
        expected_session_version=row.version,
    )
    session.flush()
    step_id = reads.plan_steps(session, plan.plan_id)[0].step_id
    writes.mark_step(
        session,
        row.session_id,
        step_id=step_id,
        status=writes.PlanStepStatus.DONE,
        expected_version=plan.version,
    )
    writes.record_checkpoint(
        session,
        row.session_id,
        cp_id=reads.plan_checkpoints(session, plan.plan_id)[0].cp_id,
        result=writes.CheckpointResult.PASS,
        expected_version=plan.version,
    )
    writes.revise_plan(
        session,
        row.session_id,
        steps=[writes.StepDraft(action="合并成一步")],
        change="简化",
        reason="发现两步可以合并",
        expected_version=plan.version,
    )
    writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="想试出声背",
        expected_version=row.version,
    )
    writes.add_chat_message(session, row.session_id, content="今天状态不错")
    writes.add_note(session, row.session_id, content="下周继续")
    milestone = writes.add_milestone(session, row.session_id, title="背完 unit 1")
    session.flush()
    writes.complete_milestone(session, row.session_id, milestone_id=milestone.milestone_id)
    writes.write_summary(session, row.session_id, content="节奏找到了")
    session.flush()
    plan = reads.require_plan(session, row.session_id)
    for step in reads.plan_steps(session, plan.plan_id):
        current = reads.require_plan(session, row.session_id)
        writes.mark_step(
            session,
            row.session_id,
            step_id=step.step_id,
            status=PlanStepStatus.DONE,
            expected_version=current.version,
        )
    session.flush()
    writes.transition(
        session, row.session_id, target="review", expected_version=row.version
    )
    writes.transition(
        session, row.session_id, target="completed", expected_version=row.version
    )
    session.flush()

    assert _global_footprint(session) == before


def test_state_snapshot_never_reaches_m1(session: Session) -> None:
    """M1 §5.5 红线：动态状态**永不进 M1**。

    两条一起验：写快照不产生任何全局痕迹；快照的字段名不与任何 M1 变量同义重名
    （后者防的是"把 energy 当成一个 M1 变量"这种偷渡）。
    """
    before = _global_footprint(session)
    _folder, row = _live_session(session)

    writes.update_snapshot(
        session,
        row.session_id,
        expected_version=row.version,
        energy=writes.Level.LOW,
        stress=writes.Level.HIGH,
        environment="咖啡厅",
        note="今天有点累",
    )
    session.flush()

    assert _global_footprint(session) == before

    snapshot_columns = set(StateSnapshot.__table__.columns.keys()) - {
        "session_id",
        "updated_at",
    }
    variable_ids = set(
        session.scalars(select(M1Variable.variable_id))
    )
    assert snapshot_columns & variable_ids == set()
    # 反向也查一遍：没有哪个 M1 变量的 id 长得像快照字段
    assert not [v for v in variable_ids if v[2:].lower() in snapshot_columns]


def test_session_writes_do_not_create_router_logs_even_with_invariants_on(
    session: Session,
) -> None:
    """`router_log` 是**唯一**审计源，而沙箱写入不产生审计日志 —— 这是定义，不是缺陷。

    沙箱的留痕机制是三个 `*_revision` 表（见 `PROVENANCE`），
    把它和全局审计混成一张表，会让"哪些行代表权威状态变化"再也说不清。
    """
    _folder, row = _live_session(session)
    writes.add_note(session, row.session_id, content="一条笔记")
    writes.add_chat_message(session, row.session_id, content="一句聊天")
    session.flush()
    assert _count(session, RouterLog) == 0


# --------------------------------------------------------------------------- #
# 唯一的那条路：沉淀
# --------------------------------------------------------------------------- #


def test_sedimenting_into_the_authoritative_domain_does_go_through_the_router(
    session: Session, router
) -> None:
    """沉淀是沙箱 → 全局的**唯一**接口，而它必须过 Router（因此必然有日志）。"""
    _folder, row = _live_session(session)
    item = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="我背单词必须出声",
        expected_version=row.version,
    )
    session.flush()

    from app.session_domain.sedimentation import build_candidates

    plan = build_candidates(
        session, row.session_id, trigger=TriggerType.R4C, stage_item_ids=[item.item_id]
    )
    assert len(plan.included) == 1

    result = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R4C,
            intents=[
                UpdateIntent(
                    intent_id=next_intent_id(DbSequenceStore(session)),
                    action=plan.included[0].action,  # type: ignore[arg-type]
                    target=plan.included[0].target,
                    value=plan.included[0].value,
                    basis=plan.included[0].proposed_basis,
                    reasoning="测试沉淀",
                    confidence="medium",
                )
            ],
            source_ref=row.session_id,
            session_ref=row.session_id,
        )
    )
    session.flush()

    assert result.status.value == "applied"
    assert _count(session, RouterLog) == 1
    assert [
        p.statement for p in session.scalars(select(M2Preference))
    ] == ["我背单词必须出声"]
