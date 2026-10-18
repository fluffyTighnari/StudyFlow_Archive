"""M0 §12 治理内核验收标准 —— 8 条。

| # | 判据 | 本文件 |
|---|---|---|
| 1 | 全局表的**任何**变更都能追溯到 ≥1 条 RouterLog | `test_criterion_1_*` |
| 2 | 未注册 target 的 intent **100%** 被拒 | `test_criterion_2_*` |
| 3 | `basis=inferred` 的 `update_parameter` **100%** 被拒 | `test_criterion_3_*` |
| 4 | 季度重做建议/警告 **100%** 出现在提案上，且**从不阻断** | `test_criterion_4_*` |
| 5 | 架构测试：非 router 模块 import 全局写接口 → 测试失败 | `test_criterion_5_*` |
| 6 | 令牌：伪造或 `None` → `TypeError` | `test_criterion_6_*` |
| 7 | 提案部分应用后，RouterLog 条数 == applied intent 数 | `test_criterion_7_*` |
| 8 | 回滚后可读历史版本，且历史行未被修改 | `test_criterion_8_*` |

**写法约定**：每一条都写「正面成立」+「反面对照」两半。只写前者的话，
一条把一切都拒绝、把一切都算作通过的实现也能全绿 —— P3 已经因为
「空集合上恒真」的断言栽过一次，这里不再重复。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db import Base
from app.global_domain.enums import (
    AdvisoryLevel,
    EvidenceChannel,
    IntentStatus,
    RouterLogStatus,
    TriggerType,
)
from app.global_domain.invariants import (
    PROVENANCE,
    assert_global_invariants,
    check_global_invariants,
)
from app.global_domain.models import (
    M1BaselineCurrent,
    M1BaselineVersion,
    RouterLog,
    UpdateIntentRow,
)
from app.global_domain.targets import target_for_variable
from app.router import Decision, Router, SubmitRequest, UpdateIntent
from tools.archcheck import check, format_violations

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _m1_intent(
    intent_id: str,
    *,
    action: str = "update_parameter",
    target: str = "M1.V-SE",
    basis: str = "measured",
    channel: str = "measured",
    score: float = 3.0,
    confidence: str = "calibrated",
) -> UpdateIntent:
    value: dict[str, object] = {}
    if action == "update_parameter":
        value = {
            "channel": channel,
            "score": score,
            "completeness": "complete",
            "confidence": confidence,
            "boundary": None,
        }
    return UpdateIntent(
        intent_id=intent_id,
        action=action,
        target=target,
        value=value,
        basis=basis,
        reasoning="验收用例",
        confidence="high",
    )


def _preference_intent(
    intent_id: str, *, basis: str = "inferred", statement: str = "先看例子再看定义"
) -> UpdateIntent:
    return UpdateIntent(
        intent_id=intent_id,
        action="add_preference",
        target="M2.preferences",
        value={"statement": statement, "priority": 5},
        basis=basis,
        reasoning="验收用例：偏好",
        confidence="high",
    )


# --------------------------------------------------------------------------- #
# 判据 1：全局表的任何变更都能追溯到 ≥1 条 RouterLog
# --------------------------------------------------------------------------- #


def test_criterion_1_every_global_change_traces_to_a_router_log(
    seeded: Session, router: Router, next_id, write_baseline, write_preference
) -> None:
    """走三类 target（M1 参数 / M2 偏好 / 指针传递）写完后逐表核对。

    追溯不是「日志表里有东西」这种整体判断，而是**逐行**的：
    少一条 `router_log_id` 就是一个永远查不出来源的字段值，
    而它是这个项目全部个性化推断的输入。
    """
    # 三类写入：M1 基线的两个变量（V-SE 与 V-DC 各自一行）、M2 偏好。
    # 走 R2 + measured：判定表里唯一的 auto_apply 参数写入组合。
    write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    write_baseline(variable_id="V-DC", channel="measured", score=4.0)
    write_preference(statement="先看例子再看定义")

    # ① 该有凭据的行，一行都不能缺。
    for model, pk_attr in (
        (M1BaselineVersion, "version_id"),
        (M1BaselineCurrent, None),
    ):
        table = model.__tablename__
        if pk_attr is None:
            # 指针表没有列，靠传递：它指向的版本必须有日志。
            stmt = (
                select(M1BaselineCurrent.version_id, M1BaselineVersion.router_log_id)
                .select_from(M1BaselineCurrent)
                .join(
                    M1BaselineVersion,
                    M1BaselineVersion.version_id == M1BaselineCurrent.version_id,
                )
            )
            rows = list(seeded.execute(stmt))
            assert rows, "前置写入没有产生任何当前版本指针"
            for version_id, log_id in rows:
                assert log_id is not None, f"{table} → {version_id} 的版本没有日志"
            continue
        for (row_pk, log_id) in seeded.execute(
            select(model.__table__.c[pk_attr], model.router_log_id)
        ):
            assert log_id is not None, f"{table}#{row_pk} 没有 router_log_id"

    # ② 三条写入 ⇒ 三条 applied 日志，一条不多一条不少。
    applied = list(
        seeded.scalars(
            select(RouterLog).where(RouterLog.status == RouterLogStatus.APPLIED)
        )
    )
    assert len(applied) == 3, [log.log_id for log in applied]
    assert all(log.intent_ref is not None for log in applied)

    # ③ 每张表都在「已声明的机制」里 —— 不允许存在没人看守的新表。
    assert set(PROVENANCE) == set(Base.metadata.tables)
    assert check_global_invariants(seeded) == []
    assert_global_invariants(seeded)


def test_criterion_1_a_row_without_a_log_is_actually_detected(
    seeded: Session, write_baseline
) -> None:
    """反面对照：追溯链断了必须有人喊。

    没有这一半，判据 1 的正面用例可以在「自检什么都看不见」的实现下通过 ——
    而那正是它要防的事。
    """
    write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    assert_global_invariants(seeded)  # 写完当下是干净的

    row = seeded.scalars(select(M1BaselineVersion)).one()
    row.router_log_id = None
    seeded.flush()

    failures = check_global_invariants(seeded)
    assert [f.invariant_id for f in failures] == ["I1"]


# --------------------------------------------------------------------------- #
# 判据 2：未注册 target 的 intent 100% 被拒
# --------------------------------------------------------------------------- #

#: 一批「看起来像 target、其实不是」的字符串。
#:
#: 覆盖了四类错误：不存在的变量、不存在的命名空间、只对了一半的前缀、
#: 以及大小写/分隔符写错。**A9 归一化唯一性**能挡住的正是最后两类。
UNREGISTERED_TARGETS = (
    "M1.V-NOPE",      # 命名空间对，变量不存在
    "M1.",            # 前缀对，变量为空
    "M1",             # 漏了点号
    "m1.V-SE",        # 大小写错
    "M1.V-SE/ch",     # 混进了通道
    "M9.x",           # 命名空间不存在
    "M3.preferences", # 近似 M2 但不存在
    "C.evidences",    # 复数写错
    "M2",             # 只有根
    "",               # 空串
)


@pytest.mark.parametrize("target", UNREGISTERED_TARGETS)
def test_criterion_2_unregistered_target_never_writes(
    seeded: Session, router: Router, next_id, target: str
) -> None:
    """判据的核心不是「返回了一个 rejected」，而是**什么都没写进去**。

    两个容易搞错的细节：

    1. 一个「报了拒绝、却仍然落了库」的实现会让所有基于响应的断言全绿。
       因此最后两行查库，而不是查返回值。
    2. **被拒的 intent 仍然会留下一条 `status=rejected` 的日志**，这是对的 ——
       「LLM 提过什么、为什么被拒」本身就是审计要回答的问题（§6）。
       所以这里查的是 `status=applied` 的日志为零，而不是「日志表没变」。
       把「拒绝也留痕」误判成「写入发生了」，会让判据 2 变成一条错误的断言。
    """
    before_applied = seeded.scalar(
        select(func.count())
        .select_from(RouterLog)
        .where(RouterLog.status == RouterLogStatus.APPLIED)
    )
    before_versions = seeded.scalar(select(func.count()).select_from(M1BaselineVersion))

    verdict = "raised"
    try:
        result = router.submit(
            SubmitRequest(
                trigger_type=TriggerType.R1,
                intents=[_m1_intent(next_id(), target=target)],
            )
        )
    except (DomainError, ValidationError) as exc:
        # 在契约层就被拒（例如空串不合格）也算「100% 被拒」——
        # 关键是拒绝发生在**写入之前**。pydantic 的 ValidationError 也要接住：
        # `UpdateIntent` 自己会挡掉明显非法的 target。
        verdict = type(exc).__name__
    else:
        verdict = ",".join(r.status.value for r in result.results)
        assert all(r.status is not IntentStatus.APPLIED for r in result.results)

    assert (
        seeded.scalar(
            select(func.count())
            .select_from(RouterLog)
            .where(RouterLog.status == RouterLogStatus.APPLIED)
        )
        == before_applied
    ), f"target={target!r} 被拒（{verdict}）却仍然写了 applied 日志"
    assert (
        seeded.scalar(select(func.count()).select_from(M1BaselineVersion))
        == before_versions
    ), f"target={target!r} 被拒（{verdict}）却仍然写了基线"


def test_criterion_2_the_registered_counterpart_is_accepted(
    seeded: Session, router: Router, next_id
) -> None:
    """反面对照：注册过的 target 必须能写。

    否则「100% 被拒」可以通过「把一切都拒绝」来达成。
    """
    result = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            intents=[_m1_intent(next_id(), target=target_for_variable("V-SE"))],
            source_ref="RUN-A",
        )
    )
    assert result.results[0].status is IntentStatus.APPLIED


# --------------------------------------------------------------------------- #
# 判据 3：basis=inferred 的 update_parameter 100% 被拒
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("trigger", list(TriggerType))
@pytest.mark.parametrize("variable_id", ["V-SE", "V-DC", "V-CP"])
@pytest.mark.parametrize("channel", ["measured", "experienced", "stated"])
def test_criterion_3_inferred_parameter_update_is_always_rejected(
    seeded: Session, router: Router, next_id, trigger: TriggerType, variable_id: str, channel: str
) -> None:
    """红线 3 的**全部**输入空间：任意触发源 × 变量类 × 通道。

    单点测试会让「只在 R3 上拦住了」这种半成品看起来是绿的。
    触发源直接遍历整个 `TriggerType`，因此**将来新增的触发源自动纳入覆盖** ——
    红线 3 若在新触发源上漏掉，这里会红，而不需要有人记得回来加参数。

    R5（回滚）不产出 intent，会走到 `precheck.evaluate` 的显式 `ValueError`；
    那同样是「没写进去」，与红线 3 的结论一致，但不该被算作红线 3 拦下的，
    故单独断言。
    """
    try:
        result = router.submit(
            SubmitRequest(
                trigger_type=trigger,
                intents=[
                    _m1_intent(
                        next_id(),
                        target=target_for_variable(variable_id),
                        basis="inferred",
                        channel=channel,
                    )
                ],
                source_ref="RUN-A",
            )
        )
    except ValueError as exc:
        assert trigger is TriggerType.R5, exc
    else:
        assert result.results[0].status is IntentStatus.REJECTED, result.results[0].reason
        if trigger is TriggerType.R1:
            # R1 下 action 本身就不被允许？不 —— `update_parameter` 在 M1 上是允许的，
            # 因此拒绝理由必须**就是**红线 3，而不是顺带被别的规则挡住。
            assert "红线 3" in result.results[0].reason

    assert seeded.scalar(select(func.count()).select_from(M1BaselineVersion)) == 0


def test_criterion_3_measured_is_accepted_and_confirmation_still_required(
    seeded: Session, router: Router, next_id, write_baseline
) -> None:
    """反面对照：`measured` 能过，但**必须**留下确认记录。

    「只有 inferred 被拒」不等于「红线成立了」：一个把所有确认都跳过的实现
    同样会让上面的用例全绿。
    """
    write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    log = seeded.scalars(
        select(RouterLog).where(RouterLog.action == "update_parameter")
    ).one()
    assert log.user_confirmed is True
    assert log.basis is not None and log.basis.value == "measured"

    # 而「需要确认」的那一类，在没裁决之前不许写：
    pending = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R4A,
            intents=[
                _m1_intent(
                    next_id(),
                    target=target_for_variable("V-DC"),
                    basis="experienced",
                    channel="experienced",
                )
            ],
            source_ref="S-001",
        )
    )
    assert pending.results[0].status is IntentStatus.PENDING, pending.results[0].reason
    versions = seeded.scalar(
        select(func.count())
        .select_from(M1BaselineVersion)
        .where(M1BaselineVersion.variable_id == "V-DC")
    )
    assert versions == 0, "未裁决的提案不该写入"


# --------------------------------------------------------------------------- #
# 判据 4：重做建议 100% 出现，且从不阻断
# --------------------------------------------------------------------------- #


def test_criterion_4_advisory_appears_at_every_run_count_and_never_blocks(
    seeded: Session, router: Router, next_id
) -> None:
    """本季度重做 0/1/2 次，三次提交的**提案上都要有**建议，且三次都写得进去。

    M0 §5.4 把配额废掉之后，「从不阻断」就不能只靠一句说明 ——
    必须有一个用例走到 ≥2 次、看着警告弹出来、然后确认写入照样成功。
    """
    expected_levels = [AdvisoryLevel.NONE, AdvisoryLevel.SUGGEST, AdvisoryLevel.WARN]

    for index, expected in enumerate(expected_levels):
        result = router.submit(
            SubmitRequest(
                trigger_type=TriggerType.R2,
                intents=[_m1_intent(next_id(), target=target_for_variable("V-SE"))],
                source_ref=f"RUN-{index}",  # 每次换 run id = 记一次重做
            )
        )

        item = result.results[0]
        assert item.advisories, f"第 {index + 1} 次重做的提案上没有重做建议"
        advisory = item.advisories[0]
        assert advisory.level is expected
        assert advisory.run_count == index
        assert advisory.message.strip()

        # 「从不阻断」是结构性的：类型上就没有阻断能力。
        assert advisory.blocks() is False
        assert item.blockers == ()

        # 也不参与裁定：警告级别下依然 applied。
        assert item.status is IntentStatus.APPLIED, item.reason

    assert router.benchmark_advisory("V-SE").run_count == 3
    assert_global_invariants(seeded)


def test_criterion_4_other_triggers_carry_no_advisory(
    seeded: Session, router: Router, next_id, write_preference
) -> None:
    """建议只对 R2 有意义 —— 直接写入不是「重做基准测试」。

    若这条松掉，用户每次在界面上改一个偏好都会被告知「你本季度重测了 N 次」，
    信息噪声会让人开始无视所有建议。
    """
    result = write_preference(statement="先看例子")
    assert result.results[0].advisories == ()


def test_criterion_4_the_stored_proposal_read_model_only_lacks_advisory_and_blockers(
    seeded: Session, router: Router, next_id
) -> None:
    """**缺口标记（A16）**：待确认提案读得回**判定**，读不回**建议**与**硬前置条件**。

    先说清哪些是**有**的（很容易误判成缺失）：`update_intent` 上持久化了
    `precheck`（判定）与 `precheck_reason`（理由），`UpdateIntentRead` 也暴露了它们
    —— 所以用户下次打开界面能看到「这条需要你确认」和判定理由。

    缺的是另外两样，而它们恰好是用户决定**要不要点同意**的依据：

    - `advisories`（§5.4「本季度已重做 N 次」）
    - `blockers`（§7.3 硬前置条件，如「需要 ≥2 条独立 Session 证据，当前 1 条」）

    用户已裁定按「读时取回快照」实现（持久化到 `update_intent` 上）——
    理由是「用户当时看到了什么」是审计事实，不该被后来改动的判定表重写。

    **这是有意钉住当前边界的用例**：等它被实现时这条会红，提醒同时更新
    M0 §11 A16 与 `app/api/proposals.py` 的说明。
    """
    from app.api.proposals import _detail, _intents
    from app.global_domain import reads
    from app.global_domain.schemas import ProposalDetailRead

    def _read_model(proposal_id: str) -> ProposalDetailRead:
        return ProposalDetailRead.from_proposal(
            reads.get_proposal(seeded, proposal_id),
            _intents(seeded, proposal_id),
        )

    # ① R2 提案：auto_apply 写入，提交响应带重做建议。
    written = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R2,
            intents=[_m1_intent(next_id(), target=target_for_variable("V-SE"))],
            source_ref="RUN-A",
        )
    )
    assert written.results[0].advisories, "提交响应里应当有建议"
    assert written.results[0].status is IntentStatus.APPLIED

    after_write = _read_model(written.proposal_id)
    # 有的：判定与理由（已持久化在 update_intent 上）。
    assert after_write.intents[0].precheck is not None
    assert after_write.intents[0].precheck_reason
    assert after_write.intents[0].status is IntentStatus.APPLIED
    # 没有的：建议（只活在提交那一刻的响应里）。
    assert not hasattr(after_write, "advisories")
    assert not hasattr(after_write.intents[0], "advisories")

    # ② R4a 提案：待确认，提交响应带硬前置条件。
    pending = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R4A,
            source_ref="S-001",
            intents=[
                _m1_intent(
                    next_id(),
                    target=target_for_variable("V-DC"),
                    basis="experienced",
                    channel="experienced",
                )
            ],
        )
    )
    assert pending.results[0].status is IntentStatus.PENDING
    assert pending.results[0].blockers, "§7.3 的 ≥2 证据门槛应当作为硬前置条件出现"

    awaiting = _read_model(pending.proposal_id)
    assert awaiting.intents[0].status is IntentStatus.PENDING
    assert awaiting.intents[0].precheck is not None
    # 没有的：硬前置条件 —— 用户看不到「还差一条证据」。
    assert not hasattr(awaiting, "blockers")
    assert not hasattr(awaiting.intents[0], "blockers")

    assert _detail(seeded, pending.proposal_id).proposal_id == pending.proposal_id


# --------------------------------------------------------------------------- #
# 判据 5：架构测试能抓到「非 router 模块 import 全局写接口」
# --------------------------------------------------------------------------- #


def _make_app_package(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "app"
    (root / "global_domain").mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "global_domain" / "__init__.py").write_text("", encoding="utf-8")
    (root / "global_domain" / "writes.py").write_text("", encoding="utf-8")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_criterion_5_a_non_router_write_import_fails_the_check(tmp_path: Path) -> None:
    """合成的违规模块必须被抓到 —— 检查器失灵比没有检查器更危险。"""
    root = _make_app_package(
        tmp_path,
        {
            "session_domain/__init__.py": "",
            "session_domain/sneaky.py": (
                "from app.global_domain.writes import insert_preference\n"
            ),
        },
    )
    violations = check(root)
    assert violations, "非 router 模块 import 全局写接口，架构检查却什么都没报"
    assert "ARCH-01" in {v.rule_id for v in violations}, format_violations(violations)


def test_criterion_5_the_real_tree_is_clean_and_actually_scanned() -> None:
    """真实代码树必须干净，且检查器确实扫到了文件（否则「无违规」是空集的假绿）。"""
    from tools.archcheck import scan

    modules = scan(APP_DIR)
    assert len(modules) >= 20
    assert "app.global_domain.writes" in {m.path for m in modules}
    assert check(APP_DIR) == []


# --------------------------------------------------------------------------- #
# 判据 6：令牌伪造或 None → TypeError
# --------------------------------------------------------------------------- #


def test_criterion_6_a_token_cannot_be_forged_or_omitted(
    seeded: Session, write_preference
) -> None:
    """判据 6：「伪造或 None → `TypeError`」拆成三层来验。

    M0 §12 判据 6 的原句是「令牌：伪造或 `None` → `TypeError`」。落到实现上有
    三种不同的失败，而**它们不该共用一种异常**：

    | 情形 | 异常 | 为什么 |
    |---|---|---|
    | `RouterToken(...)` 任何直接构造（含传 `None`） | `TypeError` | 判据 6 的字面内容 |
    | 把非令牌值（`None` / 字符串 / 数字）传给写函数 | `TypeError` | 调用点忘了传令牌 = 编程错误（→500） |
    | `object.__new__` / 子类伪造、或已撤销的令牌 | `CapabilityError` | 真正的越权尝试（→403） |

    第三行若也变成 `TypeError`，一个「忘了传令牌」的 bug 会以 500 的面目出现
    而一个真实的越权尝试会被当成崩溃 —— 两种都会把排查方向带偏。
    因此这里把三层都钉住，而不是只钉判据原文提到的那一种。
    """
    from app.core.capability import is_valid
    from app.core.errors import CapabilityError
    from app.global_domain.writes import insert_preference
    from app.router.capability import RouterToken, new_token

    # ① 构造路径：判据 6 的字面内容。
    for args in ((), ("forged",), (None,), (None, "forged")):
        with pytest.raises(TypeError):
            RouterToken(*args)

    # ② 非令牌值 → TypeError（编程错误）。
    for bogus in (None, "forged", 42, object()):
        with pytest.raises(TypeError):
            insert_preference(bogus, seeded, statement="合成", priority=1)

    # ③ 像令牌但没被签发过 → CapabilityError（越权）。
    for forged in (
        _forged_token(),
        _forged_subclass_token(),
    ):
        assert not is_valid(forged)
        with pytest.raises(CapabilityError):
            insert_preference(forged, seeded, statement="合成", priority=1)

    # ④ 真令牌放行 —— 否则上面「全都抛异常」可以靠把一切都拒掉来达成。
    insert_preference(new_token(), seeded, statement="真令牌", priority=1)
    seeded.flush()


def _forged_token():
    from app.router.capability import RouterToken

    forged = object.__new__(RouterToken)
    forged._nonce = "f" * 32
    return forged


def _forged_subclass_token():
    from app.router.capability import RouterToken

    class Evil(RouterToken):
        __slots__ = ()

    forged = object.__new__(Evil)
    forged._nonce = "e" * 32
    return forged


# --------------------------------------------------------------------------- #
# 判据 7：部分应用后，RouterLog 条数 == applied intent 数
# --------------------------------------------------------------------------- #


def test_criterion_7_partial_application_keeps_logs_and_intents_in_step(
    seeded: Session, router: Router, next_id
) -> None:
    """一条待确认、两条被拒 —— 裁决后三者状态各不相同，而痕迹必须对得上。

    这是「提案可部分应用」的实质内容：一次提交里的不同 intent 可以有不同命运，
    而**每一种命运都必须只留下一个痕迹**（不重复记、也不漏记）。

    三条 intent 的选择刻意错开拒绝原因，这样「部分应用」不只是一种状态组合：
    ① `inferred` 加偏好 → 待确认（M0 §11 A11，不是 must_reject）；
    ② `inferred` 写参数 → 红线 3，必然被拒；
    ③ 未注册 target → 必然被拒。
    """
    proposal = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R3,
            source_ref="S-001",
            intents=[
                _preference_intent(next_id(), basis="inferred"),
                _m1_intent(
                    next_id(),
                    target=target_for_variable("V-IR"),
                    basis="inferred",
                    channel="measured",
                ),
                _m1_intent(next_id(), target="M1.V-NOPE"),
            ],
        )
    )
    assert proposal.status.value == "pending_confirmation"

    statuses = [r.status for r in proposal.results]
    assert statuses.count(IntentStatus.PENDING) == 1
    assert statuses.count(IntentStatus.REJECTED) == 2

    approved = [
        r.intent_id for r in proposal.results if r.status is IntentStatus.PENDING
    ]
    decisions = [
        Decision(
            intent_id=r.intent_id,
            approved=r.intent_id in approved,
            reason="验收：逐条裁决",
        )
        for r in proposal.results
    ]

    resolved = router.resolve(proposal.proposal_id, decisions)
    assert resolved.status.value == "partially_applied", resolved.status

    applied_intents = seeded.scalar(
        select(func.count())
        .select_from(UpdateIntentRow)
        .where(
            UpdateIntentRow.proposal_id == proposal.proposal_id,
            UpdateIntentRow.status == IntentStatus.APPLIED,
        )
    )
    applied_logs = seeded.scalar(
        select(func.count())
        .select_from(RouterLog)
        .where(
            RouterLog.proposal_ref == proposal.proposal_id,
            RouterLog.status == RouterLogStatus.APPLIED,
        )
    )
    assert applied_intents == len(approved) == 1
    assert applied_logs == applied_intents, (
        f"提案 {proposal.proposal_id}：applied 意图 {applied_intents} 条，"
        f"applied 日志 {applied_logs} 条 —— 有一次写入被记了多次或没被记"
    )

    # 被拒的两条必须没有任何 applied 日志。
    rejected_ids = [
        r.intent_id for r in proposal.results if r.status is IntentStatus.REJECTED
    ]
    assert len(rejected_ids) == 2
    rejected_logs = seeded.scalar(
        select(func.count())
        .select_from(RouterLog)
        .where(
            RouterLog.proposal_ref == proposal.proposal_id,
            RouterLog.status == RouterLogStatus.APPLIED,
            RouterLog.intent_ref.in_(rejected_ids),
        )
    )
    assert rejected_logs == 0
    assert_global_invariants(seeded)


def test_criterion_7_nothing_written_leaves_nothing_applied(
    seeded: Session, router: Router, next_id
) -> None:
    """反面对照：整批全被拒时，applied 意图与日志都必须是 0（而不是 1 与 0）。"""
    result = router.submit(
        SubmitRequest(
            trigger_type=TriggerType.R3,
            intents=[
                _m1_intent(next_id(), target="M1.V-NOPE"),
                _m1_intent(
                    next_id(),
                    target=target_for_variable("V-IR"),
                    basis="inferred",
                ),
            ],
        )
    )
    assert all(r.status is IntentStatus.REJECTED for r in result.results)
    assert (
        seeded.scalar(
            select(func.count())
            .select_from(UpdateIntentRow)
            .where(UpdateIntentRow.status == IntentStatus.APPLIED)
        )
        == 0
    )
    assert seeded.scalar(select(func.count()).select_from(M1BaselineVersion)) == 0


# --------------------------------------------------------------------------- #
# 判据 8：回滚后可读历史版本，且历史行未被修改
# --------------------------------------------------------------------------- #


def _snapshot_history(session: Session) -> list[tuple]:
    """把历史表的**每一列**取出来。

    只比 `score` 会漏掉「有人原地改了 `basis` 或 `router_log_id`」这类篡改 ——
    而 append-only 承诺说的正是「整行不变」。
    """
    columns = list(M1BaselineVersion.__table__.columns)
    rows = session.execute(
        select(*columns).order_by(M1BaselineVersion.version_id)
    ).all()
    return [tuple(row) for row in rows]


def test_criterion_8_rollback_keeps_history_readable_and_untouched(
    seeded: Session, router: Router, next_id, write_baseline
) -> None:
    write_baseline(variable_id="V-SE", channel="measured", score=3.0, source_ref="RUN-A")
    write_baseline(variable_id="V-SE", channel="measured", score=1.0, source_ref="RUN-B")

    versions = list(
        seeded.scalars(
            select(M1BaselineVersion)
            .where(
                M1BaselineVersion.variable_id == "V-SE",
                M1BaselineVersion.channel == "measured",
            )
            .order_by(M1BaselineVersion.created_at, M1BaselineVersion.version_id)
        )
    )
    assert [v.score for v in versions] == [3.0, 1.0]
    first, second = versions
    history_before = _snapshot_history(seeded)

    pointer_before = seeded.get(M1BaselineCurrent, ("V-SE", "measured"))
    assert pointer_before is not None and pointer_before.version_id == second.version_id

    rolled = router.rollback(
        "V-SE", EvidenceChannel.MEASURED, first.version_id, reason="验收：回到第一次读数"
    )

    # ① 回滚 = 插入新版本，指针前移，旧行原地不动。
    assert rolled.new_version_id not in {first.version_id, second.version_id}
    assert rolled.replaced_version_id == second.version_id
    assert _snapshot_history(seeded)[:2] == history_before, "历史行被改动了"

    # ② 历史仍可读，且顺序与内容都对。
    from app.global_domain import reads

    replay = reads.baseline_history(seeded, "V-SE", EvidenceChannel.MEASURED)
    assert [v.version_id for v in replay][:2] == [first.version_id, second.version_id]
    assert [v.score for v in replay] == [3.0, 1.0, 3.0]

    # ③ 新版本自己带凭据，且凭据是一条 R5 日志。
    pointer_after = seeded.get(M1BaselineCurrent, ("V-SE", "measured"))
    assert pointer_after is not None and pointer_after.version_id == rolled.new_version_id
    new_version = seeded.get(M1BaselineVersion, rolled.new_version_id)
    assert new_version is not None
    assert new_version.source_ref == f"rollback:{first.version_id}"
    log = seeded.get(RouterLog, rolled.router_log_id)
    assert log is not None
    assert log.trigger is TriggerType.R5
    assert log.user_confirmed is True
    # 回滚不是 IntentAction，也不产生 intent —— 两处引用都为空。
    assert log.action is None and log.intent_ref is None and log.proposal_ref is None
    assert first.version_id in log.reasoning

    assert_global_invariants(seeded)


def test_criterion_8_rollback_requires_a_reason_and_a_real_target(
    seeded: Session, router: Router, write_baseline
) -> None:
    """回滚是用户能直接做的少数操作之一，因此它的输入校验必须自己扛。

    没有 ≥2 证据门槛（目标版本当初已经过过一次），但**理由**必须有 ——
    一次没有理由的回滚在审计里等于一次无痕改写。
    """
    from app.core.errors import NotFound, ValidationRejected

    write_baseline(variable_id="V-SE", channel="measured", score=3.0)
    only = seeded.scalars(select(M1BaselineVersion)).one()

    with pytest.raises(ValidationRejected):
        router.rollback(
            "V-SE", EvidenceChannel.MEASURED, only.version_id, reason="   "
        )
    with pytest.raises(NotFound):
        router.rollback(
            "V-SE", EvidenceChannel.MEASURED, "M1-SE-9999", reason="不存在的版本"
        )
    # 不存在的**变量**不需要单独查一次：没有任何版本会属于它，
    # 因此必然被「版本不属于该 (变量, 通道)」这条挡下 —— 而且后者给出的信息更具体
    # （它告诉你这个版本真正属于谁）。为这个情形再加一次变量查询，
    # 只会多一条永远走不到的分支。
    with pytest.raises(ValidationRejected, match="不能用于回滚"):
        router.rollback(
            "V-NOPE", EvidenceChannel.MEASURED, only.version_id, reason="不存在的变量"
        )
    # 通道写成字符串或 None 也不能以 `AttributeError` 的样子炸掉：
    # 这正是 P5 验收套件抓到的那个缺陷（先是 AttributeError，修好之后是明确的拒绝）。
    with pytest.raises(ValidationRejected):
        router.rollback(
            "V-SE", "no_such_channel", only.version_id, reason="未知通道"  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationRejected):
        router.rollback(
            "V-SE", EvidenceChannel.MEASURED, only.version_id, reason="  "
        )

    # 通道传字符串（合法值）应当被归一，而不是静默失效。
    rolled = router.rollback(
        "V-SE", "measured", only.version_id, reason="字符串通道也应被归一"  # type: ignore[arg-type]
    )
    assert rolled.variable_id == "V-SE"

    # 失败路径不该留下任何痕迹：只剩上面那一次成功的回滚。
    assert seeded.scalar(select(func.count()).select_from(M1BaselineVersion)) == 2
    assert (
        seeded.scalar(
            select(func.count())
            .select_from(RouterLog)
            .where(RouterLog.trigger == TriggerType.R5)
        )
        == 1
    )
