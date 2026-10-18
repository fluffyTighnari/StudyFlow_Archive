"""Session 域的 HTTP 演练 —— **两条端到端通路**（P4 的验收）。

方案 A1 承诺的两条路，在这里各走一遍：

1. **会话完成 → 沉淀提案 → 待确认队列**（`POST /complete`）；
2. **点计划项 → 讨论上下文包**（`GET /plan/steps/{id}/context`）。

以及三件必须能被客户端分辨的失败：版本冲突（409 `version_conflict`）、
状态不允许（409 `state_not_writable`）、形状不对（422）。它们的**状态码可以相同**，
但 `code` 必须不同 —— 否则界面只能靠 message 猜，而 message 是会改的。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _folder(client: TestClient, name: str = "英语学习") -> str:
    response = client.post("/api/folders", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["folder_id"]


def _session(client: TestClient, folder_id: str, title: str = "背单词") -> str:
    response = client.post(
        "/api/sessions",
        json={"folder_id": folder_id, "title": title, "input_goal": "背完考研核心 1500 词"},
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _state(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/api/sessions/{session_id}/state")
    assert response.status_code == 200, response.text
    return response.json()


def _activate(client: TestClient, session_id: str) -> dict:
    state = _state(client, session_id)
    response = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": state["versions"]["session"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _plan(client: TestClient, session_id: str) -> dict:
    state = _state(client, session_id)
    response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_session_version": state["versions"]["session"],
            "goal_derived": "先把 unit 1 吃透",
            "steps": [
                {"action": "通读 unit 1", "output": "一份生词表"},
                {"action": "精读 unit 1", "output": "10 个例句"},
            ],
            "checkpoints": [
                {
                    "at_step": "ST-002",
                    "check_method": "闭卷默写",
                    "pass_criteria": "正确率 >= 80%",
                    "fail_action": "回到 ST-001 重做",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 侧页：一次取全量
# --------------------------------------------------------------------------- #


def test_creating_a_folder_and_session_gives_a_complete_side_panel(
    client: TestClient,
) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)

    payload = _state(client, session_id)

    assert payload["session"]["state"] == "draft"
    assert payload["session"]["allowed_transitions"] == ["abandoned", "active"]
    assert payload["input"]["goal"] == "背完考研核心 1500 词"
    assert payload["motivation"]["type"] == "mixed"
    assert payload["snapshot"] is not None
    # draft 期还没有计划 —— 界面据此显示"生成计划"而不是一个空列表
    assert payload["plan"] is None
    assert payload["versions"] == {"session": 1, "input": 1, "plan": None}
    for key in ("stage", "chat", "notes", "milestones"):
        assert payload[key] == []


def test_state_404s_for_an_unknown_session(client: TestClient) -> None:
    response = client.get("/api/sessions/S-9999/state")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# 状态机：真的拒绝，并把"能去哪"一起返回
# --------------------------------------------------------------------------- #


def test_activation_without_a_goal_is_a_422_with_a_field(client: TestClient) -> None:
    folder_id = _folder(client)
    created = client.post(
        "/api/sessions", json={"folder_id": folder_id, "title": "没有目标"}
    )
    session_id = created.json()["session_id"]

    response = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": 1},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "missing_prerequisite"
    assert response.json()["detail"]["field"] == "goal"


def test_an_illegal_transition_reports_where_it_can_go_instead(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)

    response = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "completed", "expected_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "state_not_writable"
    assert response.json()["detail"]["allowed_targets"] == ["abandoned", "active"]


def test_chatting_in_a_completed_session_is_refused(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "completed", "expected_version": version},
    )

    refused = client.post(f"/api/sessions/{session_id}/chat", json={"content": "再聊两句"})
    assert refused.status_code == 409
    assert refused.json()["code"] == "state_not_writable"
    assert refused.json()["detail"]["allowed_states"] == ["active"]

    # 笔记是终态的唯一例外口子
    note = client.post(f"/api/sessions/{session_id}/notes", json={"content": "三个月后回看"})
    assert note.status_code == 201


# --------------------------------------------------------------------------- #
# 乐观锁：两种 409 要分得开
# --------------------------------------------------------------------------- #


def test_a_stale_version_is_a_409_version_conflict(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    stale = _state(client, session_id)["versions"]["session"]

    first = client.post(
        f"/api/sessions/{session_id}/stage",
        json={"expected_version": stale, "type": "temp_pref", "content": "想试出声背"},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/api/sessions/{session_id}/stage",
        json={"expected_version": stale, "type": "idea", "content": "另一个窗口写的"},
    )
    assert second.status_code == 409
    payload = second.json()
    assert payload["code"] == "version_conflict"
    # 当前版本一并回来 —— 客户端据此可以重读重试，而不是丢掉用户刚输入的内容
    assert payload["detail"]["current_version"] == stale + 1


def test_two_version_domains_do_not_collide(client: TestClient) -> None:
    """改定义区不该把正在改 stage 的人顶掉（反之亦然）。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    state = _state(client, session_id)

    edited = client.patch(
        f"/api/sessions/{session_id}/input",
        json={"expected_version": state["versions"]["input"], "goal": "背完 2000 词"},
    )
    assert edited.status_code == 200, edited.text

    after = _state(client, session_id)
    assert after["versions"]["input"] == state["versions"]["input"] + 1
    assert after["versions"]["session"] == state["versions"]["session"]

    stage = client.post(
        f"/api/sessions/{session_id}/stage",
        json={
            "expected_version": state["versions"]["session"],
            "type": "idea",
            "content": "先过一遍再精读",
        },
    )
    assert stage.status_code == 201, stage.text


def test_input_change_writes_a_revision_with_the_old_value(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    state = _state(client, session_id)

    client.patch(
        f"/api/sessions/{session_id}/input",
        json={"expected_version": state["versions"]["input"], "goal": "背完 2000 词"},
    )
    revisions = client.get(f"/api/sessions/{session_id}/input/revisions")
    assert revisions.status_code == 200
    rows = revisions.json()
    assert len(rows) == 1
    assert rows[0]["old_value"] == "背完考研核心 1500 词"
    assert rows[0]["new_value"] == "背完 2000 词"


def test_unknown_input_field_is_a_422(client: TestClient) -> None:
    """多给的字段必须 422，**不能静默忽略**。

    静默忽略的后果是"看起来成功"：把 `goal` 打成 `gol`，界面返回 200、
    侧页那一栏还是空的，而失败信息从头到尾没有出现过。
    """
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    response = client.patch(
        f"/api/sessions/{session_id}/input",
        json={"expected_version": 1, "gol": "拼错的目标"},
    )
    assert response.status_code == 422
    # 并且什么都没写进去
    assert _state(client, session_id)["input"]["goal"] == "背完考研核心 1500 词"


# --------------------------------------------------------------------------- #
# 通路 ②：点计划项 → 讨论上下文包
# --------------------------------------------------------------------------- #


def test_the_discussion_context_pack_is_reachable_over_http(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    plan = _plan(client, session_id)
    step_id = plan["steps"][0]["step_id"]

    response = client.get(f"/api/sessions/{session_id}/plan/steps/{step_id}/context")
    assert response.status_code == 200, response.text
    pack = response.json()

    assert pack["step"]["step_id"] == step_id
    assert pack["step"]["action"] == "通读 unit 1"
    assert [item["version_no"] for item in pack["step_history"]] == [1]
    # 采用序必须随包返回：让"为什么这条排在那条前面"可被界面解释
    # 6 个档位（0 资料 / 1 偏好 / 2 检查点 / 3 stage / 4 基线与证据 / 5 策略）
    assert len(pack["precedence_order"]) == 6
    assert pack["precedence_order"][0].startswith("0. 会话资料")
    names = [section["name"] for section in pack["sections"]]
    # 资料清单排在最前（P6+.5，用户裁定「让 chat 支持文件」）：
    # 它排在偏好之前，是被"清单排在后面模型就不去取正文"这个失败形态逼出来的。
    assert names[0] == "会话资料（用户上传的清单）"
    assert names[1] == "用户偏好（硬约束）"
    # 检查点这一节是 P6 加进来的（用户裁定），排在 stage 之前
    assert "检查点（本步骤的验收标准）" in names
    assert names.index("检查点（本步骤的验收标准）") < names.index("stage（本次想试的）")
    # 每一节都要能说出"排除了几条"
    assert all("dropped_count" in section for section in pack["sections"])


def test_the_context_pack_keeps_a_plan_override_but_sediment_refuses_it(
    client: TestClient,
) -> None:
    """同一条 stage 条目，在两条路上的命运**故意不同** —— 这正是两条路的区别。

    - **讨论计划**时它高度相关：它就是一条针对计划的建议 → 进上下文包。
    - **沉淀**时它必须被排除：它是"这次任务该怎么做"，不是"我是个什么样的人"
      → 排进排除清单并说明理由。

    把这两件事混起来（要么都收、要么都丢）是这类系统最典型的腐烂方式：
    计划调整被渲染成"我的认知变了"。
    """
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    plan = _plan(client, session_id)
    step_id = plan["steps"][0]["step_id"]
    state = _state(client, session_id)
    item_id = client.post(
        f"/api/sessions/{session_id}/stage",
        json={
            "expected_version": state["versions"]["session"],
            "type": "plan_override",
            "content": "把第 2 步拆成两步",
        },
    ).json()["item_id"]

    pack = client.get(
        f"/api/sessions/{session_id}/plan/steps/{step_id}/context"
    ).json()
    assert [item["item_id"] for item in pack["stage"]] == [item_id]

    sedimented = client.post(
        f"/api/sessions/{session_id}/sediment",
        json={"trigger": "R4c", "stage_item_ids": [item_id]},
    ).json()
    assert sedimented["plan"]["included"] == []
    assert "计划修订" in sedimented["plan"]["excluded"][0]["reason"]
    # 被排除的条目**留在 active**：它还在等一个真正的计划修订，而不是"已经处理完了"
    assert _state(client, session_id)["stage"][0]["status"] == "active"


def test_the_context_endpoint_404s_for_a_missing_step(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    _plan(client, session_id)
    response = client.get(f"/api/sessions/{session_id}/plan/steps/ST-999/context")
    assert response.status_code == 404
    assert response.json()["detail"]["step_id"] == "ST-999"


# --------------------------------------------------------------------------- #
# 计划：无理由不改计划
# --------------------------------------------------------------------------- #


def test_revising_a_plan_without_a_reason_is_a_422(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    plan = _plan(client, session_id)

    response = client.put(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_version": plan["version"],
            "change": "改成一步",
            "steps": [{"action": "合并成一步"}],
        },
    )
    assert response.status_code == 422


def test_old_plan_versions_stay_readable(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    plan = _plan(client, session_id)
    steps = [{"action": "通读 unit 1", "step_id": "ST-001"}]

    revised = client.put(
        f"/api/sessions/{session_id}/plan",
        json={
            "expected_version": plan["version"],
            "change": "只留一步",
            "reason": "发现两步可以合并",
            "steps": steps,
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["current_version"] == 2

    old = client.get(f"/api/sessions/{session_id}/plan", params={"version_no": 1})
    assert old.status_code == 200
    assert len(old.json()["steps"]) == 2
    assert old.json()["version_no"] == 1

    revisions = client.get(f"/api/sessions/{session_id}/plan/revisions").json()
    assert [(r["from_version"], r["to_version"]) for r in revisions] == [(0, 1), (1, 2)]


# --------------------------------------------------------------------------- #
# 通路 ①：会话完成 → 沉淀提案 → 待确认队列
# --------------------------------------------------------------------------- #


def test_r4c_sediment_applies_because_the_tick_is_the_confirmation(
    client: TestClient,
) -> None:
    """A21：(R4C, ADD_PREFERENCE, EXPERIENCED) 是"用户已确认"的手势之一。

    用户在 stage 里亲手写下并勾选了它 —— 再弹一次"你确定吗"是把治理成本
    转嫁给最不该承担它的人。
    """
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    state = _state(client, session_id)

    created = client.post(
        f"/api/sessions/{session_id}/stage",
        json={
            "expected_version": state["versions"]["session"],
            "type": "temp_pref",
            "content": "我背单词必须出声",
        },
    )
    item_id = created.json()["item_id"]

    # 先预览：**不写任何东西**
    preview = client.post(
        f"/api/sessions/{session_id}/sediment",
        json={"trigger": "R4c", "stage_item_ids": [item_id], "dry_run": True},
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["submit"] is None
    assert client.get("/api/preferences").json() == []

    # 再执行
    done = client.post(
        f"/api/sessions/{session_id}/sediment",
        json={"trigger": "R4c", "stage_item_ids": [item_id]},
    )
    assert done.status_code == 201, done.text
    payload = done.json()
    assert payload["submit"]["status"] == "applied"
    assert [r["status"] for r in payload["submit"]["results"]] == ["applied"]

    preferences = client.get("/api/preferences").json()
    assert [p["statement"] for p in preferences] == ["我背单词必须出声"]

    # 条目被打上 deposited 并指回提案
    items = _state(client, session_id)["stage"]
    assert items[0]["status"] == "deposited"
    assert items[0]["result_ref"] == payload["submit"]["proposal_id"]


def test_a_plan_override_in_stage_is_excluded_with_a_reason(client: TestClient) -> None:
    """勾了 5 条只沉淀 3 条时，界面必须回答"另外 2 条为什么没有"。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    state = _state(client, session_id)

    created = client.post(
        f"/api/sessions/{session_id}/stage",
        json={
            "expected_version": state["versions"]["session"],
            "type": "plan_override",
            "content": "把第 2 步拆成两步",
        },
    )
    item_id = created.json()["item_id"]

    response = client.post(
        f"/api/sessions/{session_id}/sediment",
        json={"trigger": "R4c", "stage_item_ids": [item_id]},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["submit"] is None or payload["submit"]["results"] == []
    assert payload["plan"]["included"] == []
    assert len(payload["plan"]["excluded"]) == 1
    assert "计划修订" in payload["plan"]["excluded"][0]["reason"]
    # 没沉淀的条目**留在 active** —— 否则界面显示"已沉淀"而全局里什么都没有
    assert _state(client, session_id)["stage"][0]["status"] == "active"


def test_completing_a_session_writes_a_summary_and_queues_an_r4a_proposal(
    client: TestClient,
) -> None:
    """R4a 是**自动产出、用户还没看过**的 → 必须进待确认队列，而不是直接生效。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )

    response = client.post(
        f"/api/sessions/{session_id}/complete",
        json={
            "expected_version": _state(client, session_id)["versions"]["session"],
            "content": "这次找到了自己的节奏",
            "findings": [{"statement": "我更适合短时段多次，因为长时段后段效率掉一半"}],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["session"]["session"]["state"] == "completed"
    assert payload["session"]["session"]["completed_at"] is not None
    assert payload["session"]["summary"]["content"] == "这次找到了自己的节奏"
    # 缺省 basis=inferred（总结是推出来的）→ R4a → 待确认
    assert payload["plan"]["included"][0]["proposed_basis"] == "inferred"
    assert payload["submit"]["status"] == "pending_confirmation"
    assert payload["submit"]["results"][0]["status"] == "pending"

    # 全局尚未改变
    assert client.get("/api/preferences").json() == []

    queue = client.get("/api/proposals", params={"status": "pending_confirmation"}).json()
    assert [p["proposal_id"] for p in queue] == [payload["submit"]["proposal_id"]]
    assert queue[0]["trigger_type"] == "R4a"


def test_approving_the_queued_r4a_proposal_actually_writes_it(client: TestClient) -> None:
    """整条路走通：完成会话 → 待确认 → 逐条批准 → 全局真的变了。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )
    completed = client.post(
        f"/api/sessions/{session_id}/complete",
        json={
            "expected_version": _state(client, session_id)["versions"]["session"],
            "content": "总结",
            "findings": [{"statement": "长时段效率会掉，短时段多次更适合我"}],
        },
    ).json()
    proposal_id = completed["submit"]["proposal_id"]

    detail = client.get(f"/api/proposals/{proposal_id}").json()
    intent_id = detail["intents"][0]["intent_id"]

    resolved = client.post(
        f"/api/proposals/{proposal_id}/resolve",
        json={"decisions": [{"intent_id": intent_id, "approved": True}]},
    )
    assert resolved.status_code == 200, resolved.text

    preferences = client.get("/api/preferences").json()
    assert [p["statement"] for p in preferences] == [
        "长时段效率会掉，短时段多次更适合我"
    ]
    # 审计日志里能查到这次写入是从哪个会话来的
    logs = client.get("/api/router-logs", params={"trigger": "R4a"}).json()
    assert len(logs) == 1
    assert logs[0]["proposal_ref"] == proposal_id


def test_completing_without_findings_produces_no_proposal(client: TestClient) -> None:
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )

    response = client.post(
        f"/api/sessions/{session_id}/complete",
        json={
            "expected_version": _state(client, session_id)["versions"]["session"],
            "content": "这次没什么收获",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan"]["included"] == []
    assert payload["submit"] is None
    assert payload["session"]["session"]["state"] == "completed"
    assert client.get("/api/preferences").json() == []


def test_finishing_a_session_archives_undecided_stage_items(client: TestClient) -> None:
    """用户裁定：未决条目转 archived，**行留着**（"我当时想试试…"本身是信息）。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    state = _state(client, session_id)
    client.post(
        f"/api/sessions/{session_id}/stage",
        json={
            "expected_version": state["versions"]["session"],
            "type": "idea",
            "content": "这次没来得及试",
        },
    )
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )
    client.post(
        f"/api/sessions/{session_id}/complete",
        json={
            "expected_version": _state(client, session_id)["versions"]["session"],
            "content": "收尾",
            "sediment": False,
        },
    )

    items = _state(client, session_id)["stage"]
    assert len(items) == 1, "条目被删除了"
    assert items[0]["status"] == "archived"
    assert items[0]["settled_at"] is not None


def test_complete_rejects_a_stale_version_before_writing_anything(
    client: TestClient,
) -> None:
    """**先验版本，再动任何数据** —— 否则会留下"总结写了但归档失败"的半成品。"""
    folder_id = _folder(client)
    session_id = _session(client, folder_id)
    _activate(client, session_id)
    version = _state(client, session_id)["versions"]["session"]
    client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "review", "expected_version": version},
    )

    response = client.post(
        f"/api/sessions/{session_id}/complete",
        json={"expected_version": 1, "content": "总结", "findings": []},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"

    state = _state(client, session_id)
    assert state["summary"] is None
    assert state["session"]["state"] == "review"
