"""I1–I6 自检的正确性 —— 用「故意破坏它」来证明它真的在检查。

**这组反向用例是 P5 的核心，不是补充。**

一个永远返回「通过」的自检比没有自检更坏：它会让「四重保障④」变成一句装饰，
而所有人都会因为它是绿的而放心。P3 已经吃过一次同样的教训 ——
三条边界测试曾在空集合上全部通过（`_IncludedRouter` 不摊平），
看起来全绿、实际什么都没检查。

因此这里的结构是：
① 注册表 `BREAKING_CASES` 必须**恰好覆盖** `INVARIANTS` 的每一个 id（有测试断言），
   所以「新加一条不变式却忘了写破坏用例」会直接红；
② 每个破坏用例都断言**只有目标不变式失败**（不是「至少包含」）——
   否则一条检查过宽、到处误报的不变式也能骗过测试。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InvariantViolation
from app.core.ids import next_intent_id
from app.db import DbSequenceStore
from app.global_domain import invariants as inv
from app.global_domain.enums import (
    Basis,
    ChannelObsSourceKind,
    IntentAction,
    IntentStatus,
    LifecycleStatus,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.invariants import (
    PROVENANCE,
    assert_global_invariants,
    check_global_invariants,
)
from app.router import SubmitRequest, UpdateIntent
from app.router import service as service_module
from app.global_domain.models import (
    M1BaselineCurrent,
    M1BaselineVersion,
    M2Preference,
    RouterLog,
    UpdateIntentRow,
)


@dataclass(frozen=True)
class Ctx:
    """交给破坏手法的夹具集合。

    统一成一个参数，是为了让注册表里的每一项都有**同一个签名** ——
    否则加一条破坏用例时得先数清楚它要几个夹具，而签名不匹配会以
    `TypeError` 的形式在测试里出现，看起来像夹具坏了而不是用例写错了。
    """

    write_baseline: Callable[..., Any]
    write_observation: Callable[..., None]
    write_preference: Callable[..., Any]


# --------------------------------------------------------------------------- #
# 破坏手法
# --------------------------------------------------------------------------- #


def _one(session: Session, stmt) -> object:
    return session.scalars(stmt).one()


def _break_i1(session: Session, ctx: Ctx) -> None:
    """把一条基线版本的来路擦掉 —— 就是「幽灵写入」的样子。"""
    ctx.write_baseline(score=3.0)
    row = _one(session, select(M1BaselineVersion))
    row.router_log_id = None
    session.flush()


def _break_i1_orphan_log(session: Session, ctx: Ctx) -> None:
    """日志指向一条根本不存在的日志 —— 看起来有来路，其实没有。"""
    ctx.write_baseline(score=3.0)
    row = _one(session, select(M1BaselineVersion))
    row.router_log_id = "RL-9999"
    session.flush()


def _break_i1_rejected_log(session: Session, ctx: Ctx) -> None:
    """拿一条**被拒绝**的日志当写入凭据 —— 状态机上的偷梁换柱。

    同时把意图退回 `pending`：否则 I5 也会（正确地）报「意图 applied 却没有日志」，
    那是对同一个事实的第二处描述，会让本用例分不清「I1 判据过宽」
    与「两条不变式本来就相关」。
    """
    ctx.write_baseline(score=3.0)
    row = _one(session, select(M1BaselineVersion))
    log = session.get(RouterLog, row.router_log_id)
    assert log is not None
    log.status = RouterLogStatus.REJECTED
    intent = session.get(UpdateIntentRow, log.intent_ref)
    assert intent is not None
    intent.status = IntentStatus.PENDING
    session.flush()


def _break_i1_coverage(session: Session, ctx: Ctx) -> None:
    """把一张表的机制声明删掉 —— 豁免清单不允许「默认成立」。"""
    del PROVENANCE["update_proposal"]
    session.flush()


def _break_i2_unknown_variable(session: Session, ctx: Ctx) -> None:
    """一条已应用的日志指向不存在的变量 —— A9 归一化出错时的唯一征兆。"""
    session.add(
        RouterLog(
            log_id="RL-9001",
            source="user_ui",
            status=RouterLogStatus.APPLIED,
            trigger=TriggerType.R1,
            action=IntentAction.ADD_PREFERENCE,
            target="M1.V-NOPE",
            basis=Basis.STATED,
            user_confirmed=True,
            reasoning="合成：指向不存在的变量",
        )
    )
    session.flush()


def _break_i2_no_target(session: Session, ctx: Ctx) -> None:
    """有东西被应用了，但不知道是什么。"""
    session.add(
        RouterLog(
            log_id="RL-9002",
            source="user_ui",
            status=RouterLogStatus.APPLIED,
            trigger=TriggerType.R1,
            action=None,
            target=None,
            basis=None,
            user_confirmed=True,
            reasoning="合成：已应用但没有 target",
        )
    )
    session.flush()


def _break_i3_channel_mismatch(session: Session, ctx: Ctx) -> None:
    """把指针指到**另一个变量**的版本上。

    主键与外键都拦不住这一手：`(V-SE, measured)` 与 `(V-DC, measured)` 都是合法键，
    版本也都真实存在 —— 只有「键必须与版本一致」这条**没有**任何库层约束。
    一次手写的 UPDATE 就能让「自我效能感」的当前值悄悄变成「延迟满足」的读数，
    而且此后所有读到它的人都不会发现。
    """
    ctx.write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    ctx.write_baseline(variable_id="V-DC", channel="measured", score=9.0)
    other = _one(
        session,
        select(M1BaselineVersion).where(M1BaselineVersion.variable_id == "V-DC"),
    )
    pointer = session.get(M1BaselineCurrent, ("V-SE", "measured"))
    assert pointer is not None
    pointer.version_id = other.version_id
    session.flush()


def _break_i3_dangling_pointer(session: Session, ctx: Ctx) -> None:
    """指针指向一个不存在的版本。

    **这一半其实轮不到 I3 出手**：外键会先拦住（见
    `test_foreign_key_already_blocks_the_dangling_pointer`）。
    保留在这里是为了说明 I3 真正独有的那一半是什么 —— 键与版本**都合法**、
    只是配错了对。
    """
    ctx.write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    pointer = _one(session, select(M1BaselineCurrent))
    pointer.version_id = "M1-SE-9999"
    session.flush()


def _break_i4_inferred(session: Session, ctx: Ctx) -> None:
    """一条 basis=inferred 的参数写入被标成了 applied —— 红线 3 被越过。"""
    session.add(
        RouterLog(
            log_id="RL-9003",
            source="idea_llm",
            status=RouterLogStatus.APPLIED,
            trigger=TriggerType.R3,
            action=IntentAction.UPDATE_PARAMETER,
            target="M1.V-SE",
            basis=Basis.INFERRED,
            user_confirmed=True,
            reasoning="合成：inferred 写参数",
        )
    )
    session.flush()


def _break_i4_unconfirmed(session: Session, ctx: Ctx) -> None:
    """确认标志缺失 —— `None` 与「确认过」必须被区分开。"""
    session.add(
        RouterLog(
            log_id="RL-9004",
            source="baseline",
            status=RouterLogStatus.APPLIED,
            trigger=TriggerType.R2,
            action=IntentAction.UPDATE_PARAMETER,
            target="M1.V-SE",
            basis=Basis.MEASURED,
            user_confirmed=None,
            reasoning="合成：没有确认记录",
        )
    )
    session.flush()


def _break_i5_double_write(session: Session, ctx: Ctx) -> None:
    """同一条意图被写两次 —— 「无双写」的反面。"""
    ctx.write_baseline(score=3.0)
    intent_id = _one(session, select(UpdateIntentRow.intent_id))
    session.add(
        RouterLog(
            log_id="RL-9005",
            source="baseline",
            status=RouterLogStatus.APPLIED,
            trigger=TriggerType.R2,
            action=IntentAction.UPDATE_PARAMETER,
            target="M1.V-SE",
            basis=Basis.MEASURED,
            user_confirmed=True,
            intent_ref=intent_id,
            reasoning="合成：同一意图的第二条 applied 日志",
        )
    )
    session.flush()


def _break_i5_intent_without_log(session: Session, ctx: Ctx) -> None:
    """意图被标成 applied，却没有日志 —— 与上面相反的那一侧。"""
    ctx.write_baseline(score=3.0)
    intent = _one(session, select(UpdateIntentRow))
    session.add(
        UpdateIntentRow(
            intent_id="UI-9001",
            proposal_id=intent.proposal_id,
            action=IntentAction.ADD_PREFERENCE,
            target="M2.preferences",
            value={},
            basis=Basis.STATED,
            reasoning="合成：只有意图、没有日志",
            confidence="high",
            status=IntentStatus.APPLIED,
        )
    )
    session.flush()


def _break_i6_unattributable(session: Session, ctx: Ctx) -> None:
    """一条声明为 Session 证据、却无法归因的观测。"""
    ctx.write_observation(source_ref=None)
    session.flush()


def _break_i6_blank(session: Session, ctx: Ctx) -> None:
    """空白字符串与 NULL 一样致命：`trim()` 之后什么都没有。"""
    ctx.write_observation(source_ref="   ")
    session.flush()


def _break_i7_two_active_in_one_slot(session: Session, ctx: Ctx) -> None:
    """把被覆盖的旧偏好**改回 active** —— 于是两条 active 抢同一个装配槽位。

    注意这里只能直接改 `status`，不能直接 `session.add()` 一条新偏好：
    后者会让 I1 也报警（那一行没有日志），而本用例必须做到
    「只有 I7 失败」（见 `test_invariant_fails_loudly_when_broken` 的断言）。
    """
    ctx.write_preference(statement="呈现优先用例子", target_field="SP1.presentation")
    ctx.write_preference(statement="呈现优先用定义", target_field="SP1.presentation")
    archived = session.scalars(
        select(M2Preference).where(M2Preference.statement == "呈现优先用例子")
    ).one()
    assert archived.status is LifecycleStatus.ARCHIVED, "前置：它本该被同槽位覆盖"
    archived.status = LifecycleStatus.ACTIVE
    session.flush()


def _pref_id_of(session: Session, statement: str) -> str:
    return session.scalars(
        select(M2Preference.pref_id).where(M2Preference.statement == statement)
    ).one()


def _break_i7_active_superseded_by_active(session: Session, ctx: Ctx) -> None:
    """被显式推翻的那条又活了过来 —— 「我推翻了它」与「它还算数」同时成立。

    两条都没有 `target_field`，所以槽位那条子句不会响：
    本用例专门验 A20 引入的第二条子句。
    """
    ctx.write_preference(statement="我讨厌网课，因为浪费时间")
    pref_id = _pref_id_of(session, "我讨厌网课，因为浪费时间")
    ctx.write_preference(
        statement="我能接受精品网课，如果讲得好", supersedes_pref_id=pref_id
    )
    victim = session.get(M2Preference, pref_id)
    assert victim is not None and victim.status is LifecycleStatus.ARCHIVED
    victim.status = LifecycleStatus.ACTIVE
    session.flush()


#: `不变式 id → [(说明, 破坏手法), ...]`。
#:
#: **键集合必须与 `INVARIANTS` 完全一致**，由 `test_breaking_cases_cover_every_invariant` 强制。
BREAKING_CASES: dict[str, list[tuple[str, Callable[[Session, Ctx], None]]]] = {
    "I1": [
        ("擦掉 router_log_id", _break_i1),
        ("指向不存在的日志", _break_i1_orphan_log),
        ("用被拒绝的日志当凭据", _break_i1_rejected_log),
        ("删掉一张表的机制声明", _break_i1_coverage),
    ],
    "I2": [
        ("指向不存在的变量", _break_i2_unknown_variable),
        ("已应用但没有 target", _break_i2_no_target),
    ],
    "I3": [("指针指向另一个变量的版本", _break_i3_channel_mismatch)],
    "I4": [
        ("inferred 的参数写入被标为 applied", _break_i4_inferred),
        ("缺少确认记录", _break_i4_unconfirmed),
    ],
    "I5": [
        ("同一意图两条 applied 日志", _break_i5_double_write),
        ("意图 applied 但没有日志", _break_i5_intent_without_log),
    ],
    "I6": [
        ("Session 观测没有 source_ref", _break_i6_unattributable),
        ("source_ref 是空白", _break_i6_blank),
    ],
    "I7": [
        ("同槽位两条同时 active", _break_i7_two_active_in_one_slot),
        ("被推翻的那条又活了", _break_i7_active_superseded_by_active),
    ],
}


# --------------------------------------------------------------------------- #
# 正向：健康状态必须全部通过
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _restore_provenance() -> Iterator[None]:
    """`PROVENANCE` 是模块级字典，破坏用例会真的改它。

    没有这层保护，`I1` 的「删掉一张表的机制声明」会把污染带到之后**所有**测试上，
    表现为一串看起来毫不相关的失败 —— 而根因是一个测试改了全局常量。
    """
    snapshot = dict(PROVENANCE)
    try:
        yield
    finally:
        PROVENANCE.clear()
        PROVENANCE.update(snapshot)


def test_healthy_seeded_state_passes_every_invariant(seeded: Session) -> None:
    assert check_global_invariants(seeded) == []


def test_provenance_covers_every_table_in_the_metadata(seeded: Session) -> None:
    """每张表都必须声明可追溯机制（§4.4：豁免必须显式列举，不允许默认）。"""
    assert inv.unclassified_tables() == set()
    assert set(PROVENANCE) == set(inv.Base.metadata.tables)


def test_every_invariant_declares_an_id_title_and_check() -> None:
    ids = [i.invariant_id for i in inv.iter_invariants()]
    assert ids == ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]
    assert all(i.title.strip() for i in inv.iter_invariants())
    assert all(callable(i.check) for i in inv.iter_invariants())


# --------------------------------------------------------------------------- #
# 反向：每条不变式都必须能被抓出来
# --------------------------------------------------------------------------- #


def test_breaking_cases_cover_every_invariant() -> None:
    """注册表的键必须**恰好**等于不变式集合。

    少了 → 有一条不变式永远没被验证过；多了 → 有个 id 拼错了。
    两种都让「每条不变式都有破坏用例」这句话失去意义。
    """
    declared = {i.invariant_id for i in inv.iter_invariants()}
    assert set(BREAKING_CASES) == declared


_CASES = [
    pytest.param(invariant_id, description, corrupt, id=f"{invariant_id}-{index}")
    for invariant_id, cases in sorted(BREAKING_CASES.items())
    for index, (description, corrupt) in enumerate(cases, start=1)
]


@pytest.mark.parametrize(("invariant_id", "description", "corrupt"), _CASES)
def test_invariant_fails_loudly_when_broken(
    seeded: Session,
    write_baseline,
    write_observation,
    write_preference,
    invariant_id: str,
    description: str,
    corrupt: Callable[[Session, Ctx], None],
) -> None:
    assert check_global_invariants(seeded) == [], "前置状态本身就不干净"

    corrupt(
        seeded,
        Ctx(
            write_baseline=write_baseline,
            write_observation=write_observation,
            write_preference=write_preference,
        ),
    )

    failures = check_global_invariants(seeded)
    assert {f.invariant_id for f in failures} == {invariant_id}, (
        f"{description}：期望只有 {invariant_id} 报警，实际 "
        f"{sorted(f.invariant_id for f in failures)} —— "
        "多报说明某条不变式的判据过宽（会误伤正常状态）"
    )
    assert failures[0].problems, "报了违规却没有任何可读描述"

    with pytest.raises(InvariantViolation) as excinfo:
        assert_global_invariants(seeded)
    assert invariant_id in str(excinfo.value)


def test_assert_reports_every_failure_not_just_the_first(seeded: Session, write_baseline) -> None:
    """一次报全部 —— 只报第一条会让人以为修完那一条就好了。"""
    write_baseline(score=3.0)
    row = _one(seeded, select(M1BaselineVersion))
    row.router_log_id = None
    _break_i2_no_target(seeded, write_baseline)

    with pytest.raises(InvariantViolation) as excinfo:
        assert_global_invariants(seeded)
    assert set(excinfo.value.detail["failures"][i]["invariant_id"] for i in range(2)) == {
        "I1",
        "I2",
    }
    assert "I1" in str(excinfo.value) and "I2" in str(excinfo.value)


def test_checks_are_read_only(seeded: Session, write_baseline) -> None:
    """自检在写入之后、响应之前跑，绝不能自己产生副作用。"""
    write_baseline(score=3.0)
    before = seeded.execute(
        select(RouterLog.log_id, RouterLog.status, RouterLog.target).order_by(RouterLog.log_id)
    ).all()

    for _ in range(3):
        check_global_invariants(seeded)

    after = seeded.execute(
        select(RouterLog.log_id, RouterLog.status, RouterLog.target).order_by(RouterLog.log_id)
    ).all()
    assert before == after
    assert seeded.info.get(inv.CHECK_INVARIANTS_FLAG) is None, "自检不该改开关"


def test_invariants_are_on_by_default_and_can_be_switched_off(
    seeded: Session, router, next_id, write_baseline
) -> None:
    """开关默认**开**：任何「忘了配」的新入口都必须落在安全一侧。

    反过来说，`set_invariants_enabled(False)` 必须真的让 Router 跳过自检 ——
    否则这个开关只是一个装饰，而「临时关掉排查」会变成一句骗人的安慰。
    """
    assert inv.invariants_enabled(seeded), "默认必须是开"

    # 默认开：写入时会跑自检，而当前状态是干净的，所以写得进去。
    write_baseline(score=3.0)

    # 关掉之后，即使状态已经坏了，写入也不再触发自检 —— 证明开关真的起作用。
    inv.set_invariants_enabled(seeded, False)
    assert not inv.invariants_enabled(seeded)
    row = _one(seeded, select(M1BaselineVersion))
    row.router_log_id = None
    seeded.flush()
    assert check_global_invariants(seeded), "状态确实已经坏了"

    # 而 Router 在这一状态下仍能写入（因为自检被关）。
    result = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            source_ref="RUN-B",
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="update_parameter",
                    target="M1.V-DC",
                    value=_m1_value("measured", 5.0),
                    basis="measured",
                    reasoning="验收：关掉自检后仍可写入",
                    confidence="high",
                )
            ],
        )
    )
    assert result.results[0].status is IntentStatus.APPLIED

    # 打开之后，下一次写入立刻把问题暴露出来。
    inv.set_invariants_enabled(seeded, True)
    dc_version = _one(
        seeded,
        select(M1BaselineVersion).where(M1BaselineVersion.variable_id == "V-DC"),
    )
    with pytest.raises(InvariantViolation):
        router.rollback(
            "V-DC", "measured", dc_version.version_id, reason="打开自检后立刻暴露"
        )


def test_write_that_applies_nothing_does_not_run_the_check(
    seeded: Session, router, next_id, monkeypatch
) -> None:
    """被拒的批什么都没写，不该付自检的代价 —— 也不该因状态已坏而误报。"""
    calls: list[str] = []
    monkeypatch.setattr(
        service_module, "assert_global_invariants", lambda s: calls.append("checked")
    )

    result = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R3,
            intents=[
                UpdateIntent(
                    intent_id=next_id(),
                    action="update_parameter",
                    target="M1.V-SE",
                    value=_m1_value("measured", 3.0),
                    basis="inferred",
                    reasoning="红线 3",
                    confidence="high",
                )
            ],
        )
    )
    assert result.results[0].status is IntentStatus.REJECTED
    assert calls == []


def test_a_real_write_does_run_the_check(seeded: Session, router, monkeypatch) -> None:
    """对照组：真的写了就必须自检。没有这一半，上面那条可以通过「从不自检」达成。"""
    calls: list[str] = []
    monkeypatch.setattr(
        service_module, "assert_global_invariants", lambda s: calls.append("checked")
    )

    router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            source_ref="RUN-A",
            intents=[
                UpdateIntent(
                    intent_id=next_intent_id(DbSequenceStore(seeded)),
                    action="update_parameter",
                    target="M1.V-SE",
                    value=_m1_value("measured", 3.0),
                    basis="measured",
                    reasoning="对照组",
                    confidence="high",
                )
            ],
        )
    )
    assert calls == ["checked"]


def _m1_value(channel: str, score: float) -> dict[str, Any]:
    return {
        "channel": channel,
        "score": score,
        "completeness": "complete",
        "confidence": "calibrated",
        "boundary": None,
    }


def test_observation_kinds_that_need_no_attribution_are_not_flagged(
    seeded: Session, write_observation
) -> None:
    """`user_ui` / `external` 观测不参与 §7.3 的计数，因此不要求 `source_ref`。

    若 I6 把这两类也管上，正常数据会被误报 —— 而误报比漏报更快地
    教会人们忽略自检结果。
    """
    write_observation(kind=ChannelObsSourceKind.USER_UI, source_ref=None)
    write_observation(kind=ChannelObsSourceKind.EXTERNAL, source_ref=None)
    assert check_global_invariants(seeded) == []


def test_foreign_key_already_blocks_the_dangling_pointer(
    seeded: Session, write_baseline
) -> None:
    """I3 与库层约束的分工，写清楚免得日后误以为 I3 是多余的。

    「指针指向不存在的版本」**外键就能挡**（`PRAGMA foreign_keys=ON`，见 `app/db.py`）；
    I3 唯一不可替代的是「指针键与版本不一致」—— 那一半两个约束都不管，
    因为两边单独看都完全合法。
    """
    from sqlalchemy.exc import IntegrityError

    write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    pointer = _one(seeded, select(M1BaselineCurrent))
    pointer.version_id = "M1-SE-9999"
    with pytest.raises(IntegrityError):
        seeded.flush()
    seeded.rollback()
