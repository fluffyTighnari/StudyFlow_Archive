"""全局写原语的行为测试（M0 §6.5：证据通道纯度与 append-only 版本化）。

这组测试是 P5「治理不变量」的雏形：它们断言的不是「函数能跑」，
而是**结构上不允许发生的事**确实无法发生。
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import CapabilityError
from app.global_domain.enums import (
    Basis,
    BaselineConfidence,
    CEvidenceSource,
    CEvidenceStrength,
    ChannelObsSourceKind,
    Completeness,
    ConstructType,
    EvidenceChannel,
    LifecycleStatus,
)
from app.global_domain.models import (
    M1BaselineCurrent,
    M1BaselineVersion,
    M1Variable,
)
from app.global_domain.reads import (
    current_baseline_map,
    current_baseline_version,
    list_active_evidence,
    list_active_preferences,
)
from app.global_domain.writes import (
    append_channel_obs,
    insert_baseline_version,
    insert_evidence,
    insert_preference,
    rollback_baseline_version,
)


@pytest.fixture()
def variables(session) -> list[str]:
    ids = ["V-SE", "V-CP"]
    for order, vid in enumerate(ids):
        session.add(
            M1Variable(
                variable_id=vid,
                name=vid.lower().replace("-", "_"),
                display_name=vid,
                construct_type=ConstructType.TRAIT,
                definition_what="要测什么",
                definition_what_not="不要测什么",
                scale_low_anchor="低",
                scale_high_anchor="高",
                measurement_def={"goal": "stub", "methods": []},
                sort_order=order,
            )
        )
    session.flush()
    return ids


# --------------------------------------------------------------------------- #
# 守卫
# --------------------------------------------------------------------------- #


def test_write_without_token_is_rejected(session, variables) -> None:
    """没传令牌 = 编程错误 → `TypeError`；伪造/撤销的令牌才是 403（M0 §12 判据 6）。"""
    with pytest.raises(TypeError):
        insert_evidence(
            None,
            session,
            statement="s",
            condition="c",
            effect="e",
            evidence_strength=CEvidenceStrength.MEDIUM,
            source=CEvidenceSource.USER_DIRECT,
        )


def test_write_with_forged_token_is_rejected(session, variables) -> None:
    from app.core.capability import RouterToken

    forged = object.__new__(RouterToken)
    forged._nonce = "f" * 32
    with pytest.raises(CapabilityError):
        insert_baseline_version(
            forged,
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            basis=Basis.MEASURED,
            completeness=Completeness.COMPLETE,
            score=6.0,
        )


# --------------------------------------------------------------------------- #
# 版本化：append-only + 当前版本唯一
# --------------------------------------------------------------------------- #


def test_insert_baseline_then_replace(session, variables, token) -> None:
    first = insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=6.0,
        confidence=BaselineConfidence.PROVISIONAL,
    )
    assert first.version_id == "M1-SE-001"
    assert first.replaces_version_id is None

    second = insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=7.5,
    )
    assert second.version_id == "M1-SE-002"
    assert second.replaces_version_id == "M1-SE-001"

    # 旧行内容未被改写（append-only），只是不再是当前版本。
    session.refresh(first)
    assert first.score == 6.0

    current = current_baseline_version(session, "V-SE", EvidenceChannel.MEASURED)
    assert current is not None and current.version_id == "M1-SE-002"


def test_channels_are_structurally_independent(session, variables, token) -> None:
    """S3：不存在「一个变量一个分数」这一实体，因此三通道均值无从算起。"""
    for channel, score in (
        (EvidenceChannel.MEASURED, 8.0),
        (EvidenceChannel.EXPERIENCED, 3.0),
        (EvidenceChannel.STATED, 5.0),
    ):
        insert_baseline_version(
            token,
            session,
            variable_id="V-SE",
            channel=channel,
            basis=Basis(channel.value),
            completeness=Completeness.PARTIAL,
            score=score,
        )

    mapping = current_baseline_map(session)
    channels = mapping["V-SE"]
    assert set(channels) == {
        EvidenceChannel.MEASURED,
        EvidenceChannel.EXPERIENCED,
        EvidenceChannel.STATED,
    }
    assert channels[EvidenceChannel.MEASURED].score == 8.0
    assert channels[EvidenceChannel.EXPERIENCED].score == 3.0
    assert channels[EvidenceChannel.STATED].score == 5.0
    # 同一变量的版本号按变量计数，三通道各占一个号，互不覆盖。
    version_ids = sorted(c.version_id for c in channels.values())
    assert version_ids == ["M1-SE-001", "M1-SE-002", "M1-SE-003"]


def test_versions_are_append_only_and_coexist(session, variables, token) -> None:
    """同一通道允许存在多条历史版本 —— 只有「当前指针」是唯一的。"""
    for score in (3.0, 5.0, 7.0):
        insert_baseline_version(
            token,
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            basis=Basis.MEASURED,
            completeness=Completeness.COMPLETE,
            score=score,
        )
    assert session.query(M1BaselineVersion).count() == 3
    assert session.query(M1BaselineCurrent).count() == 1


def test_current_pointer_uniqueness_is_structural(session, variables, token) -> None:
    """每个 (变量, 通道) 至多一个当前指针 —— 由主键保证，不靠应用层自觉。

    这正是 S3「证据通道纯度」的落点：不存在「一个变量一个分数」的实体。
    """
    insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=5.0,
    )
    session.add(
        M1BaselineCurrent(
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            version_id="M1-SE-001",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_score_range_is_enforced(session, variables, token) -> None:
    with pytest.raises(IntegrityError):
        insert_baseline_version(
            token,
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.STATED,
            basis=Basis.STATED,
            completeness=Completeness.PARTIAL,
            score=11.0,
        )


def test_score_may_be_null_when_evidence_insufficient(session, variables, token) -> None:
    """Benchmark §6：证据不足不强行给分，score 允许为 NULL。"""
    row = insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.PARTIAL,
        score=None,
    )
    assert row.score is None


# --------------------------------------------------------------------------- #
# 回滚
# --------------------------------------------------------------------------- #


def test_rollback_inserts_a_new_version_rather_than_deleting(session, variables, token) -> None:
    v1 = insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=6.0,
    )
    insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=9.0,
    )

    restored = rollback_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        to_version_id=v1.version_id,
    )
    assert restored.version_id == "M1-SE-003"
    assert restored.score == 6.0
    assert restored.source_ref == "rollback:M1-SE-001"

    # 全部三行历史都在，没有任何删除。
    history = session.query(M1BaselineVersion).all()
    assert len(history) == 3


def test_rollback_rejects_mismatched_variable(session, variables, token) -> None:
    v1 = insert_baseline_version(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.MEASURED,
        basis=Basis.MEASURED,
        completeness=Completeness.COMPLETE,
        score=6.0,
    )
    with pytest.raises(ValueError):
        rollback_baseline_version(
            token,
            session,
            variable_id="V-CP",
            channel=EvidenceChannel.MEASURED,
            to_version_id=v1.version_id,
        )


# --------------------------------------------------------------------------- #
# 通道观测 / 偏好 / C 证据
# --------------------------------------------------------------------------- #


def test_append_channel_obs_does_not_create_a_baseline_version(
    session, variables, token
) -> None:
    obs = append_channel_obs(
        token,
        session,
        variable_id="V-SE",
        channel=EvidenceChannel.EXPERIENCED,
        source_kind=ChannelObsSourceKind.SESSION,
        value=7.0,
        source_ref="S-001",
    )
    assert obs.obs_id == "OB-0001"
    assert current_baseline_version(session, "V-SE", EvidenceChannel.EXPERIENCED) is None


def test_insert_preference_records_priority_and_basis(session, token) -> None:
    pref = insert_preference(
        token,
        session,
        statement="喜欢自己推导后再对照答案",
        priority=8,
        target_field="SP1.presentation",
    )
    assert pref.pref_id == "P-0001"
    assert pref.priority == 8
    assert pref.status == LifecycleStatus.ACTIVE
    assert [p.pref_id for p in list_active_preferences(session)] == ["P-0001"]


def test_evidence_requires_a_condition(session, token) -> None:
    """M2 §4.4 第 3 条：纯偏好（无 condition）不允许注册为 C 条目。"""
    with pytest.raises(ValueError, match="适用条件"):
        insert_evidence(
            token,
            session,
            statement="先推导再看答案更有效",
            condition="   ",
            effect="更快发现理解漏洞",
            evidence_strength=CEvidenceStrength.STRONG,
            source=CEvidenceSource.SESSION_DERIVED,
        )


def test_evidence_with_condition_is_stored_active(session, token) -> None:
    row = insert_evidence(
        token,
        session,
        statement="熟悉领域先独立推导再对照答案，能更快发现理解漏洞",
        condition="熟悉领域 + 有标准答案",
        effect="更快定位理解缺口",
        evidence_strength=CEvidenceStrength.STRONG,
        source=CEvidenceSource.SESSION_DERIVED,
    )
    assert row.evidence_id == "E-0001"
    assert row.status == LifecycleStatus.ACTIVE
    assert [e.evidence_id for e in list_active_evidence(session)] == ["E-0001"]


def test_evidence_keyword_search_matches_condition(session, token) -> None:
    insert_evidence(
        token,
        session,
        statement="推导法有效",
        condition="熟悉领域",
        effect="更快",
        evidence_strength=CEvidenceStrength.MEDIUM,
        source=CEvidenceSource.SESSION_DERIVED,
    )
    insert_evidence(
        token,
        session,
        statement="案例法有效",
        condition="陌生抽象概念",
        effect="更易入门",
        evidence_strength=CEvidenceStrength.MEDIUM,
        source=CEvidenceSource.SESSION_DERIVED,
    )
    hits = list_active_evidence(session, keyword="熟悉")
    assert [e.evidence_id for e in hits] == ["E-0001"]


# --------------------------------------------------------------------------- #
# 外键
# --------------------------------------------------------------------------- #


def test_foreign_key_is_enforced(session, token) -> None:
    """SQLite 默认不强制外键；db.make_engine 已打开 PRAGMA。"""
    with pytest.raises(IntegrityError):
        insert_baseline_version(
            token,
            session,
            variable_id="V-DOES-NOT-EXIST",
            channel=EvidenceChannel.MEASURED,
            basis=Basis.MEASURED,
            completeness=Completeness.PARTIAL,
            score=5.0,
        )
