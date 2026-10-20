"""领域勾选清单（用户裁定 岔路 A = 乙：进库、过 Router 记账）。

用户的原话：

> 在第一次测试的开始，我们需要按照先选择自己比较熟悉的领域。
> 第二次，选择自己在不熟悉的领域中比较偏好的领域，这是一个独立的区域，
> 我们在后续的测试中可以跳过这个阶段，直接进入测试，也可以随时调整这一点。

## 这组测试守三件事

1. **两次勾选的语义不对称**：`familiar` 进陌生度计算，`interested` 不进。
   它们**不许重叠** —— 重叠之后"同一个专题既熟悉又感兴趣"要按哪条规则算，
   就变成了一个取决于代码里判断顺序的问题，而那种东西无法向用户解释。
2. **id 必须真的存在**：拼错的 topic_id 会被安静地存下来，
   然后那条勾选永远匹配不上任何题目 —— 症状只是"有时候抽不到题"。
3. **只追加**：改一次清单是一行新的，旧的留着。「我当时勾了哪些」可回看。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.global_domain.enums import ProposalSource, RouterLogStatus, TriggerType
from app.global_domain.models import DomainProfile, DomainTopic, RouterLog
from app.global_domain.reads import (
    current_domain_profile,
    domain_profile_history,
    list_domain_topics,
)
from app.router import Router
from app.seeds.loader import load_all


def _write(router: Router, familiar=(), interested=(), reason=None) -> str:
    return router.set_domain_profile(
        familiar=list(familiar), interested=list(interested), reason=reason
    )


# --------------------------------------------------------------------------- #
# 目录表被播种
# --------------------------------------------------------------------------- #


def test_the_catalogue_is_seeded_with_the_whole_taxonomy(
    router: Router, session: Session
) -> None:
    """127 个专题、18 个大类 —— 与 `app/seeds/domain_topics.json` 一致。

    ★ 这条断言是**刻意的脆弱锚**：目录表是写入层校验 topic_id 的唯一依据，
    它少了内容，"勾一个存在的专题却被拒"就会出现，而原因看起来像校验器坏了。
    """
    topics = list_domain_topics(session)
    assert len(topics) == 127
    assert len({t.group_id for t in topics}) == 18
    # 每个专题都带边界说明 —— 没有它，用户勾选时会变成猜
    assert all(t.covers.strip() for t in topics)


def test_the_catalogue_display_order_is_data_driven(
    router: Router, session: Session
) -> None:
    """按 `seq` 排，不按 id 的字母序。

    字母序会让「文学」（`LIT`）排在「生物」（`BIO`）后面 —— 纯粹因为
    `L` > `B`。清单是给人看的，它的顺序不该由英文缩写的字典序决定。
    """
    topics = list_domain_topics(session)
    assert [t.seq for t in topics] == sorted(t.seq for t in topics)
    first_group = topics[0].group_id
    # 同一个大类必须连续出现，否则界面上分组会串
    seen: list[str] = []
    for t in topics:
        if not seen or seen[-1] != t.group_id:
            seen.append(t.group_id)
    assert len(seen) == len(set(seen)), f"大类不连续：{seen}"
    assert first_group == "LANG"


# --------------------------------------------------------------------------- #
# 写入：只追加、过 Router 记账
# --------------------------------------------------------------------------- #


def test_the_first_write_is_version_one_and_creates_a_router_log(
    router: Router, session: Session
) -> None:
    """★ 不过判定表，**但照记日志**（口径同 `m2_preference`）。

    记账与裁定是两件事。如果"不裁定"被读成"不留痕"，
    那么这份清单就会成为全系统唯一一处**改了看不出**的关于用户的数据 ——
    而每次抽题都要读它。
    """
    profile_id = _write(
        router, familiar=["BIO-MARINE", "CS-AI"], interested=["LIT-POETRY"]
    )
    row = session.get(DomainProfile, profile_id)
    assert row is not None
    assert row.version_no == 1
    assert row.familiar == ["BIO-MARINE", "CS-AI"]
    assert row.interested == ["LIT-POETRY"]

    log = session.scalars(
        select(RouterLog).where(RouterLog.trigger == TriggerType.R6)
    ).one()
    assert log.log_id == row.router_log_id
    assert log.status is RouterLogStatus.APPLIED
    assert log.source is ProposalSource.USER_UI
    assert log.new_value["profile_id"] == profile_id
    assert log.user_confirmed is True  # 用户自己勾的


def test_a_second_write_appends_without_touching_the_first(
    router: Router, session: Session
) -> None:
    """★ 只追加：改一次清单是一行新的，旧的那版**逐字节不变**。

    「我对某专题熟不熟是怎么变的」本身是事实。原地改会让"当时他熟不熟"
    永久丢失 —— 而半年后重看一次运行记录时，没人能说清那次抽题是照哪一版抽的。
    """
    first = _write(router, familiar=["BIO-MARINE"], interested=["LIT-POETRY"])
    second = _write(
        router, familiar=["BIO-MARINE", "CS-AI"], interested=["HIST-MODERN"],
        reason="最近在做生信",
    )
    assert first != second

    history = domain_profile_history(session)
    assert [p.version_no for p in history] == [2, 1]  # 最新在前
    old = next(p for p in history if p.profile_id == first)
    assert old.familiar == ["BIO-MARINE"], "旧版被改写了"
    assert old.reason is None
    assert current_domain_profile(session).profile_id == second
    assert current_domain_profile(session).reason == "最近在做生信"


def test_an_empty_checklist_is_representable_and_distinct_from_never_asked(
    router: Router, session: Session
) -> None:
    """★ 「还没勾过」与「勾了但全空」是两件事。

    前者是"这个问题还没问过他"，后者是"我熟悉所有领域（清单上一格没漏）"。
    抽题规则对这两者的处理不一样，所以 `current_domain_profile()` 返回 `None`
    与返回一个空档案必须可区分。
    """
    assert current_domain_profile(session) is None

    profile_id = _write(router, familiar=[], interested=["LIT-POETRY"])
    profile = current_domain_profile(session)
    assert profile is not None
    assert profile.profile_id == profile_id
    assert profile.familiar == []
    # 全空是"还没勾"，空 familiar 是"我熟悉所有领域"
    assert profile.interested == ["LIT-POETRY"]


# --------------------------------------------------------------------------- #
# 校验：三条规则
# --------------------------------------------------------------------------- #


def test_an_unknown_topic_id_is_refused(router: Router, session: Session) -> None:
    """★ 拼错的 id 会被安静地存下来，然后那条勾选永远匹配不上任何题目。

    它的症状只是"有时候抽不到题" —— 一个不会指向真正原因的现象。
    所以在**唯一写入口**上硬拒。
    """
    with pytest.raises(ValueError, match="不在主题表里"):
        _write(router, familiar=["BIO-MARIN"])  # 少一个 E
    with pytest.raises(ValueError, match="不在主题表里"):
        _write(router, interested=["nope-x"])
    assert session.query(DomainProfile).count() == 0


def test_interest_may_not_overlap_with_familiarity(
    router: Router, session: Session
) -> None:
    """★ 兴趣只在**陌生区**里问。

    重叠会逼出一个"哪条规则生效"的问题：同一个专题既进陌生度计算、
    又是兴趣标签，而这两个用途的优先级在代码里只能靠判断顺序决定。
    那种东西无法向用户解释，所以它在数据层就不该存在。
    """
    with pytest.raises(ValueError, match="同时被勾了"):
        _write(router, familiar=["BIO-MARINE"], interested=["BIO-MARINE"])
    assert session.query(DomainProfile).count() == 0


def test_duplicate_ids_are_refused(router: Router, session: Session) -> None:
    """重复项会让"勾了几个"这个计数虚高，而用户在界面上只看到一格。"""
    with pytest.raises(ValueError, match="重复"):
        _write(router, familiar=["BIO-MARINE", "BIO-MARINE"])


def test_an_unseeded_catalogue_refuses_to_write(session: Session) -> None:
    """★ 目录表空着的时候**必须拒绝写入**，而不是"没法校验就放行"。

    放行会让一个坏掉的部署看起来一切正常：勾选照存，然后永远匹配不上题目。
    宁可拒绝一次、报出"主题表没有被播种"，也不要静默地存下一份孤儿数据。
    """
    from app.router import Router as R

    router = R(session)  # 注意：**不**调 load_all
    assert session.query(DomainTopic).count() == 0
    with pytest.raises(ValueError, match="没有被播种"):
        router.set_domain_profile(familiar=["BIO-MARINE"], interested=[])


# --------------------------------------------------------------------------- #
# I1：每一版都有日志
# --------------------------------------------------------------------------- #


def test_every_version_has_its_own_applied_log(
    router: Router, session: Session
) -> None:
    """★ I1 的逐行校验：本表的每一行都必须指向一条 `applied` 的 RouterLog。

    这条不是"实现细节"，它是**本表敢被写进全局域的资格**。
    `domain_profile` 在 `PROVENANCE` 里声明的是 `router_log_id`，而
    那个声明只有在 I1 真的逐行查它时才有意义。
    """
    for i in range(3):
        _write(router, familiar=["BIO-MARINE"], interested=[f"CS-AI"] if i else [])

    rows = session.scalars(select(DomainProfile)).all()
    assert len(rows) == 3
    for row in rows:
        log = session.get(RouterLog, row.router_log_id)
        assert log is not None, f"{row.profile_id} 没有对应的日志"
        assert log.status is RouterLogStatus.APPLIED

    # 反例保护：3 版 → 恰好 3 条 R6 日志（不是 1 条、也不是 9 条）
    assert (
        session.scalar(
            select(func.count())
            .select_from(RouterLog)
            .where(RouterLog.trigger == TriggerType.R6)
        )
        == 3
    )


def test_the_checklist_write_does_not_touch_any_baseline(
    router: Router, session: Session
) -> None:
    """勾选清单**不是**认知读数 —— 它不该写出任何 M1 基线。

    这条防止一类很容易犯的错：把"我熟悉生物学"顺手当成一个 `stated` 分数
    写进 M1。那会让"熟悉度"变成一句关于**能力**的断言，
    而它本来只是测量的参照。
    """
    from app.global_domain.models import M1BaselineVersion

    _write(router, familiar=["BIO-MARINE"], interested=["LIT-POETRY"])
    assert session.query(M1BaselineVersion).count() == 0
