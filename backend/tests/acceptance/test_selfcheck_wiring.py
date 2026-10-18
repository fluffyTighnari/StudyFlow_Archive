"""自检的**接线**测试：它真的跑了吗？真的能拦住写入吗？

`test_invariants.py` 证明「判定是对的」，`test_acceptance_criteria.py` 证明
「8 条判据成立」。这一组证明第三件、也是最容易落空的事：**它真的被调用了。**

四重保障的第④重可以因为一个 `if` 写反而永远不执行 —— 而所有测试仍然全绿。
这是 P3 那三条「空集合上恒真」的边界测试教会我们的事：
**能通过的检查必须被证明它真的跑过。**

## 为什么自检不在 `app.api.deps` 里

P5 起初把它写在 `get_session` 的 `yield` 之后（「写入完成、尚未提交的那一瞬间」），
结果 `test_the_check_runs_before_the_response_is_sent` 直接失败：

    RuntimeError: Caught handled exception, but response already started.

这个 FastAPI/Starlette 版本在响应**已经发出之后**才关闭依赖的退出栈，
所以那里的异常既不能变成 500、也不能回滚 —— 客户端收到的是 201。
自检因此落在 `Router._verify_after_write`（唯一写入汇聚点），
下面第一个用例就是那次失败的回归测试。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import InvariantViolation


def _preference_body(intent_id: str = "UI-0001") -> dict:
    return {
        "trigger_type": "R1",
        "intents": [
            {
                "intent_id": intent_id,
                "action": "add_preference",
                "target": "M2.preferences",
                "value": {"statement": "先看例子再看定义", "priority": 5},
                "basis": "stated",
                "reasoning": "接线测试",
                "confidence": "high",
            }
        ],
    }


def test_a_normal_write_passes_the_check_and_commits(client) -> None:
    """对照组：干净状态下自检放行、数据确实落库。

    没有这一半，「自检拦住了坏写入」可以通过「自检拦住了所有写入」来达成。
    """
    response = client.post("/api/proposals", json=_preference_body())
    assert response.status_code == 201, response.text
    assert response.json()["results"][0]["status"] == "applied"

    listed = client.get("/api/preferences").json()
    assert [p["statement"] for p in listed] == ["先看例子再看定义"]


def test_the_check_runs_before_the_response_is_sent(app, client, monkeypatch) -> None:
    """自检失败 ⇒ 500 + `invariant_violation`，**而且写入被回滚**。

    这个用例是为了钉住 P5 期那个真实缺陷：只要自检的位置回到 HTTP 层的
    `yield` 之后，它就会以
    `RuntimeError: Caught handled exception, but response already started.`
    收场（或者更糟：静默返回 201）。放在 Router 里则是普通的一次异常，
    由 `ExceptionMiddleware` 正常处理。
    """
    calls: list[str] = []

    def _explode(session: Session) -> None:
        calls.append("checked")
        raise InvariantViolation("合成：自检在本次请求中失败了")

    monkeypatch.setattr("app.router.service.assert_global_invariants", _explode)

    response = client.post("/api/proposals", json=_preference_body())
    assert calls == ["checked"], "写过全局状态的请求没有触发自检"
    assert response.status_code == 500, response.text
    payload = response.json()
    assert payload["code"] == "invariant_violation"
    assert "合成" in payload["message"]

    # 关键的一行：那一批写入**没有**被提交。
    assert client.get("/api/preferences").json() == []


def test_the_check_is_skipped_when_nothing_was_written(app, client, monkeypatch) -> None:
    """纯读请求不该付自检的代价 —— 否则一张表变大之后每个 GET 都会变慢。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "app.router.service.assert_global_invariants",
        lambda session: calls.append("checked"),
    )

    assert client.get("/api/m1/variables").status_code == 200
    assert client.get("/api/proposals").status_code == 200
    assert calls == []


def test_the_check_can_be_switched_off_for_troubleshooting(monkeypatch) -> None:
    """开关是给排查用的：确认「是不是自检本身在报错」时能临时关掉。

    关掉必须真的生效 —— 否则这个开关只是装饰，而「临时关一下」会变成
    一句骗人的安慰，下一次它还会以别的方式炸出来。
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    calls: list[str] = []
    monkeypatch.setattr(
        "app.router.service.assert_global_invariants",
        lambda session: calls.append("checked"),
    )

    off_app = create_app(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            seed_on_startup=True,
            check_invariants=False,
        )
    )
    with TestClient(off_app) as off_client:
        assert off_client.post("/api/proposals", json=_preference_body()).status_code == 201
    assert calls == []

    # 默认值是开 —— 关掉必须是一次显式动作。
    assert Settings().check_invariants is True


def test_a_rejected_proposal_does_not_trigger_the_check(app, client, monkeypatch) -> None:
    """被拒的 intent 什么都没写 ⇒ 不触发自检。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "app.router.service.assert_global_invariants",
        lambda session: calls.append("checked"),
    )

    response = client.post(
        "/api/proposals",
        json={
            "trigger_type": "R3",
            "intents": [
                {
                    "intent_id": "UI-0002",
                    "action": "update_parameter",
                    "target": "M1.V-SE",
                    "value": {
                        "channel": "measured",
                        "score": 3.0,
                        "completeness": "complete",
                        "confidence": "calibrated",
                        "boundary": None,
                    },
                    "basis": "inferred",
                    "reasoning": "红线 3",
                    "confidence": "high",
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["results"][0]["status"] == "rejected"
    assert calls == []


def test_rollback_is_checked_too(client, monkeypatch) -> None:
    """回滚是独立于判定表的第二条写入路径，自检不能只在 `submit` 上接。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "app.router.service.assert_global_invariants",
        lambda session: calls.append("checked"),
    )

    written = client.post(
        "/api/proposals",
        json={
            "trigger_type": "R2",
            "source_ref": "RUN-A",
            "intents": [
                {
                    "intent_id": "UI-0010",
                    "action": "update_parameter",
                    "target": "M1.V-SE",
                    "value": {
                        "channel": "measured",
                        "score": 3.0,
                        "completeness": "complete",
                        "confidence": "calibrated",
                        "boundary": None,
                    },
                    "basis": "measured",
                    "reasoning": "铺一条基线",
                    "confidence": "high",
                }
            ],
        },
    )
    assert written.status_code == 201
    version_id = client.get("/api/m1/V-SE/history?channel=measured").json()[0][
        "version_id"
    ]
    calls.clear()

    rolled = client.post(
        "/api/m1/V-SE/rollback",
        json={"channel": "measured", "to_version_id": version_id, "reason": "接线测试"},
    )
    assert rolled.status_code == 200, rolled.text
    assert calls == ["checked"]


# --------------------------------------------------------------------------- #
# 命令行自检
# --------------------------------------------------------------------------- #


def test_cli_reports_success_on_a_healthy_database(tmp_path, monkeypatch, capsys) -> None:
    """CLI 与程序内自检必须走同一套判定 —— 否则「本地跑过没问题」会变成一句空话。"""
    from app.db import Base, make_engine, make_session_factory, session_scope
    from app.seeds.loader import load_all
    from tools import invariantcheck

    database = tmp_path / "healthy.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with session_scope(make_session_factory(engine)) as session:
        load_all(session)
    engine.dispose()

    monkeypatch.setenv("MOUXUE_DATABASE_URL", url)
    assert invariantcheck.main([]) == 0
    out = capsys.readouterr().out
    assert "全局不变式自检通过" in out
    # 成功信息必须**枚举全部不变式**，而不是写死一句话。
    # 原先它写的是「I1–I6 全部成立」，P6 加了 I7 之后这句话就变成假的 ——
    # 而且它恰恰在最需要可信的那一行上撒谎（"全部成立"）。
    from app.global_domain.invariants import iter_invariants

    for invariant in iter_invariants():
        assert invariant.invariant_id in out, (
            f"成功信息里没有提到 {invariant.invariant_id} —— "
            "写死的清单会在加了一条不变式之后继续说「全部成立」"
        )


def test_cli_exit_codes_separate_violations_from_breakage(
    tmp_path, monkeypatch, capsys
) -> None:
    """`1` = 发现违规，`2` = 检查器没能跑起来（例如库文件不存在）。

    两者共用退出码 1 的话，一个连不上库的错误会被读成「治理内核有问题」。
    """
    from app.db import Base, make_engine
    from tools import invariantcheck

    # ① 空库（表存在、无数据）→ 干净。
    empty = tmp_path / "empty.db"
    url = f"sqlite+pysqlite:///{empty.as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("MOUXUE_DATABASE_URL", url)
    assert invariantcheck.main([]) == 0

    # ② 库文件不存在 → 检查器跑不起来 → 2（而不是「发现违规」）。
    monkeypatch.setenv(
        "MOUXUE_DATABASE_URL",
        f"sqlite+pysqlite:///{(tmp_path / 'nowhere.db').as_posix()}",
    )
    assert invariantcheck.main([]) == 2
    assert "InvariantCheckError" in capsys.readouterr().err


def test_cli_reports_violations_with_a_readable_report(
    tmp_path, monkeypatch, capsys
) -> None:
    """违规时退出码必须是 1，且报告要指出**哪条不变式**、**哪一行**。"""
    from sqlalchemy import select

    from app.db import Base, make_engine, make_session_factory, session_scope
    from app.global_domain.enums import Basis, EvidenceChannel
    from app.global_domain.models import M1BaselineVersion
    from app.global_domain.writes import insert_baseline_version
    from app.router.capability import new_token
    from app.seeds.loader import load_all
    from tools import invariantcheck

    database = tmp_path / "broken.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)

    with session_scope(make_session_factory(engine)) as session:
        load_all(session)
        # 绕过 Router 直接写一行 —— 这正是自检存在的理由。
        insert_baseline_version(
            new_token(),
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            score=3.0,
            completeness="complete",
            basis=Basis.MEASURED,
            source_ref="RUN-A",
            router_log_id=None,
        )
        assert session.scalars(select(M1BaselineVersion)).one().router_log_id is None
    engine.dispose()

    monkeypatch.setenv("MOUXUE_DATABASE_URL", url)
    assert invariantcheck.main([]) == 1

    out = capsys.readouterr().out
    assert "[I1]" in out
    assert "无痕写入" in out
    assert os.path.basename(database.as_posix()) in out


def test_cli_uses_the_same_verdict_as_the_in_process_check(
    tmp_path, monkeypatch, capsys
) -> None:
    """「本地跑过了」只有在两边判据一致时才是证据。

    这条是防「CLI 有自己一套简化判断」：它把 CLI 的结论与
    `check_global_invariants` 的结论在**同一个坏库**上对比。
    """
    from sqlalchemy import select

    from app.db import Base, make_engine, make_session_factory, session_scope
    from app.global_domain.enums import Basis, EvidenceChannel
    from app.global_domain.invariants import check_global_invariants
    from app.global_domain.models import M1BaselineVersion
    from app.global_domain.writes import insert_baseline_version
    from app.router.capability import new_token
    from app.seeds.loader import load_all
    from tools import invariantcheck

    database = tmp_path / "same_verdict.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        load_all(session)
        insert_baseline_version(
            new_token(),
            session,
            variable_id="V-SE",
            channel=EvidenceChannel.MEASURED,
            score=3.0,
            completeness="complete",
            basis=Basis.MEASURED,
            source_ref="RUN-A",
            router_log_id=None,
        )
    with session_scope(factory) as session:
        assert session.scalars(select(M1BaselineVersion)).one().router_log_id is None
        in_process = sorted(f.invariant_id for f in check_global_invariants(session))
    engine.dispose()

    monkeypatch.setenv("MOUXUE_DATABASE_URL", url)
    assert invariantcheck.main([]) == 1
    out = capsys.readouterr().out
    for invariant_id in in_process:
        assert f"[{invariant_id}]" in out
    assert in_process == ["I1"]
