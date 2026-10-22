"""会话生命周期 —— 状态机强制、三个版本域、修订留痕、stage 归宿。

这些测试要证明的不是"函数能跑"，而是**几条当时被刻意设计出来的规矩确实成立**：

1. `draft` 真的建不出计划（否则"激活"只是界面上一个按钮）；
2. 三个版本域互不干扰，但同一域内必撞（否则乐观锁是装饰）；
3. 值没变就不写修订（否则修订表被噪声淹没，"我什么时候改过"再也答不出来）；
4. 会话结束时 stage 条目转 `archived` 而**不是被删除**（用户裁定）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import (
    InvalidField,
    MissingPrerequisite,
    NotFound,
    StateNotWritable,
    VersionConflict,
)
from app.global_domain.enums import Basis
from app.session_domain import reads, writes
from app.session_domain.enums import (
    ChangedBy,
    CheckpointResult,
    InputConfidence,
    PlanStepStatus,
    SessionState,
    StageItemStatus,
    StageItemType,
)


def _live_session(db: Session, *, goal: str = "背完考研核心 1500 词"):
    """建一个已激活、已写好目标的会话。返回 (folder, session_row)。"""
    folder = writes.create_folder(db, name="英语学习")
    row = writes.create_session(
        db,
        folder_id=folder.folder_id,
        title="背单词",
        input_goal=goal,
        intent_raw="我想把考研单词过一遍",
        intent_confidence=InputConfidence.STATED,
    )
    db.flush()
    writes.transition(
        db, row.session_id, target="active", expected_version=row.version
    )
    db.flush()
    return folder, row


# --------------------------------------------------------------------------- #
# 创建：四个头一次建齐
# --------------------------------------------------------------------------- #


def test_create_session_builds_all_four_heads(session: Session) -> None:
    """侧页在任何状态下都要显示这四块，因此它们必须一起存在。"""
    folder = writes.create_folder(session, name="英语学习")
    row = writes.create_session(session, folder_id=folder.folder_id, title="背单词")
    session.flush()

    assert row.state is SessionState.DRAFT
    assert row.version == 1
    assert reads.get_input(session, row.session_id).version == 1
    assert reads.get_motivation(session, row.session_id).strength.value == "medium"
    assert reads.get_snapshot(session, row.session_id) is not None
    assert reads.get_plan(session, row.session_id) is None


def test_create_session_rejects_a_missing_folder(session: Session) -> None:
    with pytest.raises(NotFound):
        writes.create_session(session, folder_id="F-9999", title="背单词")


def test_folder_gets_a_default_retrieval_dimension(session: Session) -> None:
    """默认值不能是空列表：空列表意味着"所有证据都相关"，那是最坏的默认。"""
    folder = writes.create_folder(session, name="英语学习")
    assert folder.c_retrieval_dims == ["task_type"]


# --------------------------------------------------------------------------- #
# 状态机：真的拒绝
# --------------------------------------------------------------------------- #


def test_draft_can_create_a_plan(session: Session) -> None:
    """★ 政策变更：`CREATE_PLAN` 不再要求先激活。

    旧断言是"draft 期连目标都还没确认，生成的计划没有可依据的定义区" ——
    而代价是「要有计划先能激活，要能激活先有目标，要有目标先聊」这一圈：
    新建的会话在**起草期**最想做的事恰恰是"先帮我排一版"。

    现在它写出来的是一份带版本号的计划（`goal_derived` 空着也自洽），
    缺目标这件事由**计划理由**记着（见 `test_clarify.py` 的猜测说明），
    而不是靠拒绝。
    """
    folder = writes.create_folder(session, name="英语学习")
    row = writes.create_session(session, folder_id=folder.folder_id, title="背单词")
    session.flush()

    plan = writes.create_plan(
        session,
        row.session_id,
        steps=[writes.StepDraft(action="通读 unit 1")],
        expected_session_version=row.version,
    )
    assert plan.version == 1
    assert reads.require_plan(session, row.session_id) is plan
    assert reads.plan_steps(session, plan.plan_id)
    # 建计划**不推进状态**：起草期的计划仍然是一份草稿，不会把会话偷偷激活。
    assert row.state is SessionState.DRAFT


def test_activation_no_longer_requires_a_goal(session: Session) -> None:
    """★ 政策变更：`draft → active` 不再要求先写目标（原来会抛 `MissingPrerequisite`）。

    撤掉它的理由就是新判据：目标空着执行起来，定义区那一栏**就是空的**
    （界面上看得见）、计划 `goal_derived` 为空（可核对）——
    没有任何一行在说谎，所以这里没有"必须拦"的数据理由。
    它挡住的却是"新建会话必须先想出一句目标才能开始"，
    而那句话用户本来就会在聊的过程中说出来。
    """
    folder = writes.create_folder(session, name="英语学习")
    row = writes.create_session(session, folder_id=folder.folder_id, title="背单词")
    session.flush()

    activated = writes.transition(session, row.session_id, target="active", expected_version=1)
    assert activated.state is SessionState.ACTIVE
    # 空着的目标仍然是**空着**：不拦不等于替它编一个值。
    assert reads.get_input(session, row.session_id).goal is None


def test_a_blank_goal_is_still_not_a_goal(session: Session) -> None:
    """`goal="   "` 不算填过 —— 这条判断没有变，变的只是**它不再挡住激活**。

    旧断言把它写成"激活要抛 MissingPrerequisite"。现在激活放行，
    而"空白不算内容"这件事仍然要成立：`clarify.is_empty` 仍判它为空，
    于是排计划时它照样算"缺 `goal`"、照样要带上"这是按助手判断排的"。
    否则一个空格就会让计划看起来像是**照着用户说的**排的。
    """
    from app.session_domain import clarify

    folder = writes.create_folder(session, name="英语学习")
    row = writes.create_session(
        session, folder_id=folder.folder_id, title="背单词", input_goal="   "
    )
    session.flush()
    writes.transition(session, row.session_id, target="active", expected_version=1)
    assert clarify.is_empty(reads.get_input(session, row.session_id).goal)


def test_transition_records_timestamps(session: Session) -> None:
    _folder, row = _live_session(session)
    assert row.activated_at is not None

    writes.transition(session, row.session_id, target="review", expected_version=row.version)
    writes.transition(
        session, row.session_id, target="completed", expected_version=row.version
    )
    assert row.completed_at is not None
    assert row.state is SessionState.COMPLETED


def test_completed_session_refuses_content_writes_but_takes_notes_and_chat(
    session: Session,
) -> None:
    """★ 归档之后关的是**内容写入**，说话与笔记开着（政策变更）。

    旧断言里还有一条"`completed` 之后不能再聊"。它被删掉的理由与笔记一样：
    消息是 append-only 的，不涨版本、也不被任何判定读取 ——
    拦它挡掉的只是"回来再问一句"，而那不会让任何数据自相矛盾。
    真正该关的仍然关着：改定义区会让"当时为什么这么总结"对不上，
    所以它照旧抛 `StateNotWritable`。
    """
    _folder, row = _live_session(session)
    writes.transition(session, row.session_id, target="review", expected_version=row.version)
    writes.transition(
        session, row.session_id, target="completed", expected_version=row.version
    )

    with pytest.raises(StateNotWritable):
        writes.update_input(
            session,
            row.session_id,
            changes={"goal": "换个目标"},
            expected_version=reads.get_input(session, row.session_id).version,
        )

    note = writes.add_note(session, row.session_id, content="三个月后再来回看这条")
    assert note.note_id
    message = writes.add_chat_message(session, row.session_id, content="还想再聊两句")
    assert message.msg_id


# --------------------------------------------------------------------------- #
# 三个版本域
# --------------------------------------------------------------------------- #


def test_input_write_does_not_touch_the_session_version(session: Session) -> None:
    """定义区与"会话工作区"是两把锁：改目标不该把改 stage 的人顶掉。"""
    _folder, row = _live_session(session)
    before = row.version

    writes.update_input(
        session,
        row.session_id,
        changes={"goal": "背完 2000 词"},
        expected_version=reads.get_input(session, row.session_id).version,
    )
    session.flush()
    assert row.version == before
    assert reads.get_input(session, row.session_id).version == 2


def test_stage_write_does_bump_the_session_version(session: Session) -> None:
    """stage 与状态机共用一个版本号（M0 §6.2 要求 stage 带版本，但它没有头表）。"""
    _folder, row = _live_session(session)
    before = row.version

    writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="我想试试出声背",
        expected_version=before,
    )
    session.flush()
    assert row.version == before + 1


def test_stale_version_is_rejected_with_the_current_one(session: Session) -> None:
    """冲突时把当前版本一起返回 —— 客户端据此可以重读重试，而不是丢掉用户的输入。"""
    _folder, row = _live_session(session)
    stale = row.version
    writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.IDEA,
        content="先过一遍再精读",
        expected_version=stale,
    )

    with pytest.raises(VersionConflict) as excinfo:
        writes.add_stage_item(
            session,
            row.session_id,
            item_type=StageItemType.IDEA,
            content="另一个窗口写的东西",
            expected_version=stale,
        )
    assert excinfo.value.http_status == 409
    assert excinfo.value.detail["current_version"] == stale + 1
    assert excinfo.value.detail["expected_version"] == stale


def test_motivation_borrows_the_session_version(session: Session) -> None:
    """动机没有自己的 version 列，因此借 `session.version` —— 借了就必须 +1。"""
    _folder, row = _live_session(session)
    before = row.version

    writes.update_motivation(
        session,
        row.session_id,
        change="强度从中提高到高",
        expected_version=before,
        strength=writes.Level.HIGH,
    )
    session.flush()
    assert row.version == before + 1

    with pytest.raises(VersionConflict):
        writes.update_motivation(
            session,
            row.session_id,
            change="用已经过期的版本再写一次",
            expected_version=before,
        )


# --------------------------------------------------------------------------- #
# 修订留痕
# --------------------------------------------------------------------------- #


def test_only_changed_fields_produce_revisions(session: Session) -> None:
    """点一次保存就产生 7 条"goal 从 X 改成 X"，修订表立刻被噪声淹没。"""
    _folder, row = _live_session(session)
    input_version = reads.get_input(session, row.session_id).version

    writes.update_input(
        session,
        row.session_id,
        changes={"goal": "背完考研核心 1500 词", "why": None},
        expected_version=input_version,
    )
    session.flush()
    # goal 一个字没变；why 本来就是 None。两者都不该产生修订。
    assert reads.list_input_revisions(session, row.session_id) == []


def test_input_revision_keeps_the_old_value(session: Session) -> None:
    _folder, row = _live_session(session)
    writes.update_input(
        session,
        row.session_id,
        changes={"goal": "背完 2000 词"},
        expected_version=reads.get_input(session, row.session_id).version,
        changed_by=ChangedBy.USER,
        reason="目标上调",
    )
    session.flush()

    revisions = reads.list_input_revisions(session, row.session_id)
    assert len(revisions) == 1
    assert revisions[0].field == "goal"
    assert revisions[0].old_value == "背完考研核心 1500 词"
    assert revisions[0].new_value == "背完 2000 词"
    assert revisions[0].reason == "目标上调"


def test_unknown_input_field_is_rejected(session: Session) -> None:
    """白名单而不是黑名单：否则"新加一列"会自动变成"可以经 API 随便写"。"""
    _folder, row = _live_session(session)
    with pytest.raises(InvalidField) as excinfo:
        writes.update_input(
            session,
            row.session_id,
            changes={"version": 99},
            expected_version=reads.get_input(session, row.session_id).version,
        )
    assert excinfo.value.detail["unknown"] == ["version"]


def test_motivation_change_requires_saying_what_changed(session: Session) -> None:
    _folder, row = _live_session(session)
    with pytest.raises(MissingPrerequisite):
        writes.update_motivation(
            session, row.session_id, change="   ", expected_version=row.version
        )


# --------------------------------------------------------------------------- #
# 计划：版本化与进度
# --------------------------------------------------------------------------- #


def _plan(db: Session, row, steps=None):
    return writes.create_plan(
        db,
        row.session_id,
        steps=steps
        or [
            writes.StepDraft(action="通读 unit 1", output="一份生词表"),
            writes.StepDraft(action="精读 unit 1", output="10 个例句"),
        ],
        checkpoints=[
            writes.CheckpointDraft(
                at_step="ST-002",
                check_method="闭卷默写",
                pass_criteria="正确率 >= 80%",
                fail_action="回到 ST-001 重做",
            )
        ],
        expected_session_version=row.version,
    )


def test_plan_creation_makes_version_one_and_records_a_revision(session: Session) -> None:
    _folder, row = _live_session(session)
    plan = _plan(session, row)
    session.flush()

    assert plan.current_version == 1
    assert plan.version == 1
    assert len(reads.plan_steps(session, plan.plan_id)) == 2
    assert len(reads.plan_checkpoints(session, plan.plan_id)) == 1

    revisions = reads.list_plan_revisions(session, plan.plan_id)
    assert [(r.from_version, r.to_version) for r in revisions] == [(0, 1)]
    assert revisions[0].reason == "创建计划"


def test_create_plan_twice_is_refused(session: Session) -> None:
    """第二次创建会覆盖旧版本与它的进度 —— 要改就修订。"""
    _folder, row = _live_session(session)
    _plan(session, row)
    session.flush()
    with pytest.raises(MissingPrerequisite) as excinfo:
        writes.create_plan(
            session,
            row.session_id,
            steps=[writes.StepDraft(action="另起一套计划")],
            expected_session_version=row.version,
        )
    assert "revise" in excinfo.value.message


def test_revise_plan_needs_a_reason(session: Session) -> None:
    """M7 §9 第 3 条「无理由不改计划」—— 在库层是 NOT NULL，这里先给中文提示。"""
    _folder, row = _live_session(session)
    plan = _plan(session, row)
    session.flush()

    with pytest.raises(MissingPrerequisite):
        writes.revise_plan(
            session,
            row.session_id,
            steps=[writes.StepDraft(action="合并成一步")],
            change="简化",
            reason="  ",
            expected_version=plan.version,
        )


def test_revise_keeps_old_version_rows_and_carries_progress(session: Session) -> None:
    """旧版本的步骤原地冻结（"当时为什么那样规划"看得见），进度不被改措辞清零。"""
    _folder, row = _live_session(session)
    plan = _plan(session, row)
    session.flush()

    steps = reads.plan_steps(session, plan.plan_id)
    writes.mark_step(
        session,
        row.session_id,
        step_id=steps[0].step_id,
        status=PlanStepStatus.DONE,
        expected_version=plan.version,
    )
    session.flush()

    writes.revise_plan(
        session,
        row.session_id,
        steps=[
            writes.StepDraft(action="通读 unit 1（改为出声读）", step_id=steps[0].step_id),
            writes.StepDraft(action="精读 unit 1", step_id=steps[1].step_id),
        ],
        change="第 1 步改成出声读",
        reason="发现不出声记不住",
        expected_version=plan.version,
    )
    session.flush()

    assert plan.current_version == 2
    # 旧版本仍然是两行，且第 1 步仍是 DONE（冻结）
    old = reads.plan_steps(session, plan.plan_id, version_no=1)
    assert len(old) == 2
    assert old[0].status is PlanStepStatus.DONE
    assert old[0].action == "通读 unit 1"
    # 新版本沿用同一个 step_id，且进度被继承
    new = reads.plan_steps(session, plan.plan_id)
    assert [s.step_id for s in new] == [s.step_id for s in steps]
    assert new[0].status is PlanStepStatus.DONE
    assert new[0].action == "通读 unit 1（改为出声读）"


def test_marking_a_step_does_not_create_a_plan_revision(session: Session) -> None:
    """否则"标记一步做完"会伪造一次计划修订，`plan_revision` 立刻失去意义。"""
    _folder, row = _live_session(session)
    plan = _plan(session, row)
    session.flush()
    steps = reads.plan_steps(session, plan.plan_id)
    before = len(reads.list_plan_revisions(session, plan.plan_id))

    writes.mark_step(
        session,
        row.session_id,
        step_id=steps[0].step_id,
        status=PlanStepStatus.DOING,
        expected_version=plan.version,
    )
    session.flush()

    assert len(reads.list_plan_revisions(session, plan.plan_id)) == before
    assert plan.current_version == 1


def test_checkpoint_must_point_at_a_step_in_the_same_version(session: Session) -> None:
    _folder, row = _live_session(session)
    with pytest.raises(InvalidField) as excinfo:
        writes.create_plan(
            session,
            row.session_id,
            steps=[writes.StepDraft(action="只有一步")],
            checkpoints=[
                writes.CheckpointDraft(
                    at_step="ST-999",
                    check_method="默写",
                    pass_criteria="全对",
                )
            ],
            expected_session_version=row.version,
        )
    assert excinfo.value.detail["at_step"] == "ST-999"


def test_recording_a_failed_checkpoint_does_not_change_the_plan(session: Session) -> None:
    """检查点失败是"计划该被讨论"的信号，不是"系统替用户改计划"的许可。"""
    _folder, row = _live_session(session)
    plan = _plan(session, row)
    session.flush()
    cp = reads.plan_checkpoints(session, plan.plan_id)[0]
    steps_before = [(s.step_id, s.action) for s in reads.plan_steps(session, plan.plan_id)]

    writes.record_checkpoint(
        session,
        row.session_id,
        cp_id=cp.cp_id,
        result=CheckpointResult.FAIL,
        expected_version=plan.version,
        result_note="正确率 60%",
    )
    session.flush()

    assert plan.current_version == 1
    assert [(s.step_id, s.action) for s in reads.plan_steps(session, plan.plan_id)] == (
        steps_before
    )
    updated = reads.plan_checkpoints(session, plan.plan_id)[0]
    assert updated.result is CheckpointResult.FAIL
    assert updated.result_at is not None


# --------------------------------------------------------------------------- #
# stage 的归宿：保留行，永不删除
# --------------------------------------------------------------------------- #


def test_settling_a_stage_item_is_one_way(session: Session) -> None:
    _folder, row = _live_session(session)
    item = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="我想试试出声背",
        expected_version=row.version,
    )
    session.flush()

    writes.settle_stage_item(
        session,
        row.session_id,
        item_id=item.item_id,
        status=StageItemStatus.DEPOSITED,
        expected_version=row.version,
        result_ref="UP-0001",
    )
    session.flush()
    assert item.status is StageItemStatus.DEPOSITED
    assert item.result_ref == "UP-0001"
    assert item.settled_at is not None

    with pytest.raises(MissingPrerequisite):
        writes.settle_stage_item(
            session,
            row.session_id,
            item_id=item.item_id,
            status=StageItemStatus.DISCARDED,
            expected_version=row.version,
        )


def test_stage_item_cannot_be_settled_back_to_active(session: Session) -> None:
    _folder, row = _live_session(session)
    item = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.IDEA,
        content="先过一遍再精读",
        expected_version=row.version,
    )
    session.flush()
    with pytest.raises(InvalidField):
        writes.settle_stage_item(
            session,
            row.session_id,
            item_id=item.item_id,
            status=StageItemStatus.ACTIVE,
            expected_version=row.version,
        )


def test_completing_archives_undecided_stage_items_instead_of_deleting_them(
    session: Session,
) -> None:
    """用户裁定：会话结束时未决条目转 `archived`，**行留着**。

    你永远不会知道自己当时跳过了什么，而"我当时想试试…"本身是信息。
    """
    _folder, row = _live_session(session)
    kept = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.TEMP_PREF,
        content="已经沉淀过的",
        expected_version=row.version,
    )
    session.flush()
    writes.settle_stage_item(
        session,
        row.session_id,
        item_id=kept.item_id,
        status=StageItemStatus.DEPOSITED,
        expected_version=row.version,
    )
    pending = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.IDEA,
        content="这次没来得及试",
        expected_version=row.version,
    )
    session.flush()

    writes.transition(session, row.session_id, target="review", expected_version=row.version)
    writes.transition(
        session, row.session_id, target="completed", expected_version=row.version
    )
    session.flush()

    items = {item.item_id: item for item in reads.list_stage_items(session, row.session_id)}
    assert set(items) == {kept.item_id, pending.item_id}, "条目被删除了"
    assert items[kept.item_id].status is StageItemStatus.DEPOSITED
    assert items[pending.item_id].status is StageItemStatus.ARCHIVED


def test_abandoning_also_archives_pending_items(session: Session) -> None:
    _folder, row = _live_session(session)
    item = writes.add_stage_item(
        session,
        row.session_id,
        item_type=StageItemType.IDEA,
        content="想试但放弃了",
        expected_version=row.version,
    )
    session.flush()
    writes.transition(
        session, row.session_id, target="abandoned", expected_version=row.version
    )
    session.flush()
    assert item.status is StageItemStatus.ARCHIVED


# --------------------------------------------------------------------------- #
# 不用修订的东西：聊天 / 笔记 / 里程碑（追加记录）
# --------------------------------------------------------------------------- #


def test_milestone_completion_does_not_sediment_anything(session: Session) -> None:
    """`checkpoint_milestone` 只提示（M8 §4.3）—— 完成不自动沉淀。"""
    _folder, row = _live_session(session)
    milestone = writes.add_milestone(session, row.session_id, title="背完 unit 1")
    session.flush()
    writes.complete_milestone(
        session, row.session_id, milestone_id=milestone.milestone_id, results="80%"
    )
    session.flush()
    assert milestone.status.value == "done"
    assert milestone.done_at is not None
    # 全局域没有被这个动作碰到
    assert reads.get_milestone(session, milestone.milestone_id).session_id == row.session_id


def test_completing_a_milestone_twice_is_refused(session: Session) -> None:
    _folder, row = _live_session(session)
    milestone = writes.add_milestone(session, row.session_id, title="背完 unit 1")
    session.flush()
    writes.complete_milestone(session, row.session_id, milestone_id=milestone.milestone_id)
    with pytest.raises(MissingPrerequisite):
        writes.complete_milestone(
            session, row.session_id, milestone_id=milestone.milestone_id
        )


def test_summary_records_findings_as_raw_material(session: Session) -> None:
    _folder, row = _live_session(session)
    summary = writes.write_summary(
        session,
        row.session_id,
        content="这次找到了自己的节奏",
        findings=[{"statement": "我背单词必须出声", "basis": "experienced"}],
    )
    session.flush()
    assert summary.findings == [{"statement": "我背单词必须出声", "basis": "experienced"}]


def test_motivation_revision_records_its_basis(session: Session) -> None:
    """动机改动凭什么：用户说的还是推断的 —— 这个区别决定它有多硬。"""
    _folder, row = _live_session(session)
    writes.update_motivation(
        session,
        row.session_id,
        change="从外驱转内驱",
        expected_version=row.version,
        type=writes.MotivationType.INTRINSIC,
        basis=Basis.STATED,
    )
    session.flush()
    revisions = reads.list_motivation_revisions(session, row.session_id)
    assert len(revisions) == 1
    assert revisions[0].basis is Basis.STATED


def test_snapshot_is_overwrite_only_and_never_versioned_separately(
    session: Session,
) -> None:
    """M7 §4.1：可覆盖、不写 revision。第二次写覆盖第一次，不留历史行。"""
    _folder, row = _live_session(session)
    writes.update_snapshot(
        session, row.session_id, expected_version=row.version, energy=writes.Level.LOW
    )
    writes.update_snapshot(
        session,
        row.session_id,
        expected_version=row.version,
        energy=writes.Level.HIGH,
        environment="咖啡厅",
    )
    session.flush()
    snapshot = reads.get_snapshot(session, row.session_id)
    assert snapshot is not None
    assert snapshot.energy is writes.Level.HIGH
    assert snapshot.environment == "咖啡厅"
