"""`/api/hypotheses` —— 他**想试但还没试**的做法（P7）。

## 这一组测试要证明的三件事

1. **它是真的能用的**：POST 之后 GET 读得回来，且 `basis` 是 `stated`
   （不是靠调用方声明，而是端点自己定的）。
2. **它与 `/api/evidence` 是两个动作，不是同一个动作加参数**：
   同一个"当 X 时做 Y"的句子，进哪个端点决定它被记成假说还是经验。
3. **结构判据真的会拦**：「我喜欢在安静的环境下学习」这类没有场景的句子
   进不来（`condition` 是必填且不许空白），于是"方法 vs 偏好"的区分
   不是提示词里的请求，而是会被拒绝的输入。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.helpers import items

HYPOTHESES = "/api/hypotheses"
EVIDENCE = "/api/evidence"

ANKI = {
    "statement": "背单词想试试 Anki 的间隔重复",
    "condition": "背单词时",
    "effect": "用 Anki 的间隔重复",
}


def test_creating_a_hypothesis_works_and_says_it_is_only_stated(
    client: TestClient,
) -> None:
    """POST → 201，落库，且 `basis=stated`。

    `basis` 由**端点**定（客户端没法传），所以它出现在响应里不是回显，
    而是对"这条东西的可信度是什么"的一次正式声明。
    """
    response = client.post(HYPOTHESES, json=ANKI)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["trigger_type"] == "R1"
    assert payload["results"][0]["status"] == "applied"

    rows = items(client.get(HYPOTHESES).json())
    assert len(rows) == 1
    row = rows[0]
    assert row["condition"] == "背单词时"
    assert row["effect"] == "用 Anki 的间隔重复"
    assert row["basis"] == "stated"
    assert row["status"] == "active"
    # 可追溯：它和偏好、证据一样留了日志（I1/I2 的要求）
    assert row["router_log_id"]


def test_the_ui_write_leaves_a_proposal_and_a_log(client: TestClient) -> None:
    """UI 直写没有走捷径 —— 与 LLM 提议完全同构（两条记录都在）。"""
    client.post(HYPOTHESES, json=ANKI)
    logs = items(client.get("/api/router-logs").json())
    assert [log["status"] for log in logs] == ["applied"]
    proposals = items(client.get("/api/proposals").json())
    assert len(proposals) == 1


def test_the_same_sentence_is_a_hypothesis_or_an_experience_depending_on_where(
    client: TestClient,
) -> None:
    """★ 这一条是整个 P7 的缩影。

    同一句话「当背单词时，用 Anki」：
    - 从 `/api/hypotheses` 进来 → **假说**（未验证）
    - 从 `/api/evidence` 进来   → **经验**（已验证）

    差别不在措辞里，也不在模型的判断里 —— 差别在**他点了哪个表单**。
    这正是"不让模型猜"的具体形状：系统不需要理解句子，只需要知道他选了哪个动作。
    """
    client.post(HYPOTHESES, json=ANKI)
    client.post(
        EVIDENCE,
        json={**ANKI, "evidence_strength": "medium"},
    )

    hypotheses = items(client.get(HYPOTHESES).json())
    evidence = items(client.get(EVIDENCE).json())
    assert len(hypotheses) == 1 and len(evidence) == 1
    assert hypotheses[0]["basis"] == "stated"
    assert evidence[0]["basis"] == "experienced"
    # 两张表的 id 前缀不同，日志里并排出现时分得开
    assert hypotheses[0]["hypothesis_id"].startswith("H-")
    assert evidence[0]["evidence_id"].startswith("E-")


def test_a_preference_shaped_sentence_cannot_enter(client: TestClient) -> None:
    """「我喜欢在安静的环境下学习」没有「什么场景」可填 → 422。

    ★ 这是"不是方法"的**结构判据**：偏好表没有 `condition` 槽，
    做法表**必须有**。于是同一句话在结构上只能进偏好表 ——
    模型不需要判断它是不是方法，它填不满这张表。
    """
    response = client.post(
        HYPOTHESES,
        json={
            "statement": "我发现自己很喜欢在安静的环境下学习",
            "condition": "   ",
            "effect": "在安静的地方学",
        },
    )
    assert response.status_code == 422
    assert items(client.get(HYPOTHESES).json()) == []
    # 畸形请求不落日志（策略拒绝才落），与偏好端点同一条分界
    assert items(client.get("/api/router-logs").json()) == []


def test_what_to_do_cannot_be_empty(client: TestClient) -> None:
    """「我想试试」但说不出试什么 —— 那不是假说，那是一句还没想清楚的感觉。

    它该留在聊天记录里，不该进认知库：一条没有 `effect` 的记录救不了任何一次规划，
    却会让「他想试的东西」这个列表变成噪音。
    """
    response = client.post(
        HYPOTHESES,
        json={"statement": "我想试试那个", "condition": "背单词时", "effect": "  "},
    )
    assert response.status_code == 422


def test_the_client_cannot_declare_a_basis(client: TestClient) -> None:
    """★ 多给一个 `basis` 字段 → 422（`extra="forbid"`）。

    这一条防的是"以 experienced 写进假说表"：那个组合会让
    "装配只看已验证"退化成一条要记得的过滤。**字段填不出来，
    比字段填得错要好。**
    """
    response = client.post(
        HYPOTHESES,
        json={**ANKI, "basis": "experienced"},
    )
    assert response.status_code == 422
    assert items(client.get(HYPOTHESES).json()) == []


def test_the_whole_lifecycle_is_reachable(client: TestClient) -> None:
    """★ P7.1 反转了这条测试：删除/编辑/恢复**应该有**。

    ## 为什么原来的写法是错的

    我原先在这里断言"归档端点故意不存在"，理由是「`IntentAction` 只有三个值，
    补它要新增一个 action，那是改判定表的语义，不该顺手做」。

    那个理由把**两件事**混了：判定表要挡的是「模型替用户断言」，
    而"用户删自己写下的假说"从来不该被挡。缺的不是裁定，是 `IntentAction`
    里那个词 —— 而"缺一个词"是**我该补的活**，不是"等他决定"的事。

    用户的原话点破了它：「解释权永远在用户手里」。

    所以这条测试现在钉的是相反的事实：五条路径都在，而且**同一条链走通**。
    """
    hypothesis_id = client.post(HYPOTHESES, json=ANKI).json()  # 创建
    hypothesis_id = items(client.get(HYPOTHESES).json())[0]["hypothesis_id"]

    # ① 编辑：三槽整体替换，id 不变
    revised = client.put(
        f"{HYPOTHESES}/{hypothesis_id}",
        json={
            "statement": "背单词想试试 Anki 的间隔重复，每天不超过 20 张",
            "condition": "每天背单词时",
            "effect": "用 Anki 间隔重复，上限 20 张",
            "reason": "想加个量上的限制",
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["results"][0]["status"] == "applied"
    row = items(client.get(HYPOTHESES).json())[0]
    assert row["hypothesis_id"] == hypothesis_id, "改写不该换 id"
    assert row["effect"] == "用 Anki 间隔重复，上限 20 张"

    # ② 删除：行还在，只是不在默认列表里
    deleted = client.delete(f"{HYPOTHESES}/{hypothesis_id}", params={"reason": "试过了，没用"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["results"][0]["status"] == "applied"
    assert items(client.get(HYPOTHESES).json()) == [], "归档后不该出现在默认列表"
    archived = items(client.get(HYPOTHESES, params={"include_archived": True}).json())
    assert len(archived) == 1 and archived[0]["status"] == "archived", (
        "★ 行必须还在 —— 物理删除会让 router_log 里那条 applied 指向空气"
    )

    # ③ 恢复
    restored = client.post(f"{HYPOTHESES}/{hypothesis_id}/restore")
    assert restored.status_code == 200
    assert items(client.get(HYPOTHESES).json())[0]["status"] == "active"

    # ④ 每一次动作都留了日志，且**前后值都在**（edit worth of trail）
    logs = items(client.get("/api/router-logs").json())
    actions = [log["action"] for log in logs]
    assert actions == ["restore", "archive", "revise", "add_preference"]
    revise_log = logs[2]
    assert revise_log["old_value"]["effect"] == "用 Anki 的间隔重复"
    assert revise_log["new_value"]["effect"] == "用 Anki 间隔重复，上限 20 张"
    assert revise_log["target"] == f"C.hypothesis.{hypothesis_id}"


def test_deleting_something_that_does_not_exist_is_a_404_not_a_log(
    client: TestClient,
) -> None:
    """路径参数里的假 id → 404，不落日志。

    与 `/api/preferences` 的 `supersedes_pref_id` 相反，而且是有意的：
    那里 `P-9999` 是**用户填的字段**，他说出一个不存在的 id 是一种"他以为还在"
    的真实状态，值得留痕；这里是**路径参数**，404 已经说清了它不存在，
    再落一条日志只会让审计表堆满 404 的回声。
    """
    assert client.delete(f"{HYPOTHESES}/H-9999").status_code == 404
    assert client.put(
        f"{HYPOTHESES}/H-9999",
        json={"statement": "x", "condition": "y", "effect": "z"},
    ).status_code == 404
    assert client.post(f"{HYPOTHESES}/H-9999/restore").status_code == 404
    assert items(client.get("/api/router-logs").json()) == []


def test_revise_still_refuses_to_turn_a_hypothesis_into_evidence(
    client: TestClient,
) -> None:
    """改写不能改可信度 —— 试过了就该写 `C.evidence`，那是另一张表。

    多给 `basis` 字段 → 422（`extra="forbid"`）。这一条防的是
    "先随便写一条假说，再把它改成证据"这条捷径：它会绕过
    `insert_evidence` 的 basis 校验与 `C.hypothesis + experienced` 的拒绝。
    """
    hypothesis_id = client.post(HYPOTHESES, json=ANKI).json()
    hypothesis_id = items(client.get(HYPOTHESES).json())[0]["hypothesis_id"]
    response = client.put(
        f"{HYPOTHESES}/{hypothesis_id}",
        json={**ANKI, "basis": "experienced"},
    )
    assert response.status_code == 422
    assert items(client.get(HYPOTHESES).json())[0]["basis"] == "stated"


def test_a_hypothesis_can_come_from_nowhere(client: TestClient) -> None:
    """UI 手写的假说没有来源会话 —— 那不是缺失，是真的没有。

    填一个"当时恰好开着的会话"会让「他在哪次聊的时候说的」变成一个假事实。
    """
    client.post(HYPOTHESES, json=ANKI)
    assert items(client.get(HYPOTHESES).json())[0]["source_session_id"] is None
