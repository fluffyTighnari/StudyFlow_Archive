"""侧栏整理：改名 / 归档 / 手动排序 / 删除（文件夹与会话各一套）。

这些端点看起来"只是界面上的小功能"，但它们踩在本项目最容易被搞混的一条分界上：

| | 内容写 | 侧栏整理 |
|---|---|---|
| 例子 | 改定义区、改计划、状态转移 | 改名、归档、挪一位、删除 |
| 动 `version` | 是 | **否** |
| 写修订表 | 是 | **否** |
| 改变学习含义 | 是 | **否**（除了删除） |

因此这里有一批"**不应该发生**"的断言（`version` 不变、`state` 不变），
与"应该发生"的断言同等重要 —— 后者失效表现为功能不работа，前者失效表现为
用户在侧栏改个名字，正在进行的对话就撞 409。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _folder(client: TestClient, name: str) -> str:
    response = client.post("/api/folders", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["folder_id"]


def _session(client: TestClient, folder_id: str, title: str) -> str:
    response = client.post(
        "/api/sessions", json={"folder_id": folder_id, "title": title}
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _names(client: TestClient, path: str) -> list[str]:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return [item["name"] if "name" in item else item["title"] for item in response.json()["items"]]


def _activate(client: TestClient, session_id: str) -> None:
    """把会话推到 `active` —— **对话只允许发生在执行中**（`WRITE_GUARDS`）。

    所以凡是要造"最后一条聊天"的用例都得先走这一步：
    定义区写下目标，再走状态机的边 `draft → active`。
    """
    state = client.get(f"/api/sessions/{session_id}/state").json()
    version = state["session"]["version"]
    prepared = client.patch(
        f"/api/sessions/{session_id}/input",
        json={"expected_version": state["input"]["version"], "goal": "背完考研单词"},
    )
    assert prepared.status_code == 200, prepared.text
    activated = client.post(
        f"/api/sessions/{session_id}/transitions",
        json={"target": "active", "expected_version": version},
    )
    assert activated.status_code == 200, activated.text


def _say(client: TestClient, session_id: str, text: str) -> None:
    """说一句（需要先 `_activate`）。"""
    response = client.post(f"/api/sessions/{session_id}/chat", json={"content": text})
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------- #
# 文件夹
# --------------------------------------------------------------------------- #


def test_a_folder_can_be_renamed(client: TestClient) -> None:
    fid = _folder(client, "英语")
    response = client.patch(f"/api/folders/{fid}", json={"name": "英语 · 考研"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "英语 · 考研"
    assert fid in [item for item in client.get("/api/folders").json()["items"][0].values()]


def test_renaming_rejects_an_empty_name(client: TestClient) -> None:
    """空名字会让侧栏出现一行**看不见**的条目 —— 那比报错糟得多。"""
    fid = _folder(client, "英语")
    response = client.patch(f"/api/folders/{fid}", json={"name": ""})
    assert response.status_code == 422, response.text


def test_a_folder_can_be_archived_and_restored(client: TestClient) -> None:
    fid = _folder(client, "英语")

    assert client.patch(f"/api/folders/{fid}", json={"archived": True}).status_code == 200
    assert _names(client, "/api/folders") == []  # 侧栏看不到
    archived = client.get("/api/folders", params={"include_archived": True}).json()["items"]
    assert [item["folder_id"] for item in archived] == [fid]
    assert archived[0]["archived_at"] is not None

    assert client.patch(f"/api/folders/{fid}", json={"archived": False}).status_code == 200
    assert _names(client, "/api/folders") == ["英语"]
    restored = client.get("/api/folders", params={"include_archived": True}).json()["items"]
    assert restored[0]["archived_at"] is None


def test_archiving_a_folder_does_not_touch_its_sessions_state(client: TestClient) -> None:
    """★ 归档**不是状态机的一个状态**。

    如果哪天有人在归档里顺手把会话改成 `abandoned`，这条测试会红 ——
    因为那等于把"整理一下侧栏"变成了"我放弃了这个学习任务"。
    """
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    before = client.get(f"/api/sessions/{sid}/state").json()["session"]

    client.patch(f"/api/folders/{fid}", json={"archived": True})

    after = client.get(f"/api/sessions/{sid}/state").json()["session"]
    assert after["state"] == before["state"]
    assert after["version"] == before["version"]


def test_folders_can_be_moved_up_and_down(client: TestClient) -> None:
    first = _folder(client, "甲")
    second = _folder(client, "乙")
    third = _folder(client, "丙")
    assert _names(client, "/api/folders") == ["甲", "乙", "丙"]

    assert (
        client.patch(f"/api/folders/{third}", json={"move": "up"}).status_code == 200
    )
    assert _names(client, "/api/folders") == ["甲", "丙", "乙"]

    assert (
        client.patch(f"/api/folders/{first}", json={"move": "down"}).status_code == 200
    )
    assert _names(client, "/api/folders") == ["丙", "甲", "乙"]


def test_moving_at_the_edge_is_a_no_op_not_an_error(client: TestClient) -> None:
    """到头了不是错误。界面那头按钮本来就该置灰，服务端不该多一种失败。"""
    first = _folder(client, "甲")
    _folder(client, "乙")

    assert (
        client.patch(f"/api/folders/{first}", json={"move": "up"}).status_code == 200
    )
    assert _names(client, "/api/folders") == ["甲", "乙"]


def test_moving_a_folder_keeps_the_order_gapless(client: TestClient) -> None:
    """每次挪动之后 `sort_order` 都是 0..n-1，不留洞。"""
    first = _folder(client, "甲")
    middle = _folder(client, "乙")
    last = _folder(client, "丙")

    client.patch(f"/api/folders/{last}", json={"move": "up"})
    orders = {
        item["folder_id"]: item["sort_order"]
        for item in client.get("/api/folders").json()["items"]
    }
    assert sorted(orders.values()) == [0, 1, 2]
    assert orders[first] == 0
    assert orders[last] == 1
    assert orders[middle] == 2


def test_moving_renumbers_instead_of_swapping_equal_values(session) -> None:
    """历史库里的 `sort_order` 可能**全是 0**（加这一列之前建的）。

    那时"交换相邻两行"等于什么都没做，而界面表现为「点了上移，没反应」。
    所以排序的实现是**按顺序重编号**，而不是交换数值 —— 这条测试盯的是那个决定。
    """
    from app.session_domain import reads, writes
    from app.session_domain.models import Folder

    rows = []
    for index, name in enumerate(("甲", "乙", "丙")):
        row = Folder(
            folder_id=f"F-{index:04d}", name=name, sort_order=0, created_at=_now()
        )
        session.add(row)
        rows.append(row)
    session.flush()

    writes.move_folder(session, "F-0002", direction="up")

    ordered = [item.folder_id for item in reads.list_folders(session)]
    assert ordered == ["F-0000", "F-0002", "F-0001"]
    assert [item.sort_order for item in reads.list_folders(session)] == [0, 1, 2]


def test_deleting_a_folder_takes_its_sessions_with_it(client: TestClient) -> None:
    """`session.folder_id` 是 RESTRICT，所以"只删文件夹"在数据上不存在。"""
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    _activate(client, sid)
    _say(client, sid, "我想先把单词过一遍")

    response = client.delete(f"/api/folders/{fid}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted_sessions": 1, "deleted_folders": 1}
    assert _names(client, "/api/folders") == []
    assert client.get(f"/api/sessions/{sid}/state").status_code == 404


def test_deleting_a_missing_folder_is_404_not_silence(client: TestClient) -> None:
    assert client.delete("/api/folders/F-9999").status_code == 404


def test_a_patch_that_changes_nothing_is_rejected(client: TestClient) -> None:
    """空 `PATCH` 多半说明客户端把字段名拼错了 —— 那不该静默成功。"""
    fid = _folder(client, "英语")
    response = client.patch(f"/api/folders/{fid}", json={})
    assert response.status_code == 422, response.text


def test_an_unknown_field_is_rejected_not_ignored(client: TestClient) -> None:
    """`gol` 打成 `goal` 那类错必须当场红，而不是"看起来存上了"。"""
    fid = _folder(client, "英语")
    response = client.patch(f"/api/folders/{fid}", json={"nm": "新名字"})
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


def test_a_session_can_be_renamed_without_bumping_its_version(
    client: TestClient,
) -> None:
    """★ 改名**不动 `version`**。

    否则用户在侧栏把自己正在聊的会话改个名字，下一次内容写入就撞 409 ——
    一个纯粹由界面整理动作引起的冲突。
    """
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    before = client.get(f"/api/sessions/{sid}/state").json()["session"]["version"]

    response = client.patch(f"/api/sessions/{sid}", json={"title": "背单词 · 第二轮"})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "背单词 · 第二轮"
    assert response.json()["version"] == before


def test_a_session_patch_cannot_change_its_state(client: TestClient) -> None:
    """状态只能走 `/transitions`（带理由、带审计、受 `WRITE_GUARDS` 约束）。

    如果 `PATCH` 也能改 `state`，那张边表（`guards.TRANSITIONS`）就白建了。
    """
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    response = client.patch(f"/api/sessions/{sid}", json={"state": "completed"})
    assert response.status_code == 422, response.text
    current = client.get(f"/api/sessions/{sid}/state").json()["session"]
    assert current["state"] == "draft"


def test_a_session_can_be_archived_and_hidden_from_the_sidebar(
    client: TestClient,
) -> None:
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    other = _session(client, fid, "翻译")

    client.patch(f"/api/sessions/{sid}", json={"archived": True})

    assert _names(client, "/api/sessions") == ["翻译"]
    archived = client.get("/api/sessions", params={"include_archived": True}).json()[
        "items"
    ]
    assert {item["session_id"] for item in archived} == {sid, other}
    assert len(archived) == 2

    client.patch(f"/api/sessions/{sid}", json={"archived": False})
    # 用集合比：中文按码位排序没有意义，这里要的是"两条都在"。
    assert set(_names(client, "/api/sessions")) == {"背单词", "翻译"}


def test_sessions_are_moved_within_their_own_folder_only(client: TestClient) -> None:
    """跨文件夹比顺序是没有意义的 —— 挪动只在同级之间发生。"""
    english = _folder(client, "英语")
    maths = _folder(client, "数学")
    a = _session(client, english, "背单词")
    b = _session(client, english, "写作文")
    _session(client, maths, "刷题")

    client.patch(f"/api/sessions/{b}", json={"move": "up"})
    assert _names(client, f"/api/sessions?folder_id={english}") == ["写作文", "背单词"]

    # 头一条再上移：无变化、不报错
    assert client.patch(f"/api/sessions/{b}", json={"move": "up"}).status_code == 200
    assert _names(client, f"/api/sessions?folder_id={english}") == ["写作文", "背单词"]
    assert a  # 用一下，别让 linter 说它没用


def test_deleting_a_session_reports_how_much_went_with_it(client: TestClient) -> None:
    """界面要先把数量说给用户听 —— 所以删除端点必须数回去。"""
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    _activate(client, sid)
    for text in ("第一句", "第二句"):
        _say(client, sid, text)

    response = client.delete(f"/api/sessions/{sid}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted_sessions": 1, "deleted_chat_messages": 2}
    assert client.get(f"/api/sessions/{sid}/state").status_code == 404


def test_deleting_a_session_cascades_its_children(client: TestClient) -> None:
    """子表靠 `ON DELETE CASCADE`（连接上开了 `PRAGMA foreign_keys=ON`）。

    手写十几次 `delete(...)` 才是那个会漏掉一张表的写法；
    但"漏了"不会报错，只会在数据库里留一堆指不到会话的行 ——
    所以这里直接查数据库之外看不到的东西：删完之后新会话的 id 不复用、
    而且列表里再也读不到那条会话的任何附件。
    """
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    uploaded = client.post(
        f"/api/sessions/{sid}/attachments",
        params={"filename": "生词.txt"},
        content="apple\nbanana\n".encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}/attachments").status_code == 404


# --------------------------------------------------------------------------- #
# 排序口径：Manual 与 Last updated 都来自同一份数据
# --------------------------------------------------------------------------- #


def test_updated_at_follows_the_last_chat_not_the_creation(
    client: TestClient,
) -> None:
    """`Order by: Last updated` 要的是"最近说过话"，不是"最近建的"。

    这里刻意让**先建的**会话后说话，然后断言它的 `updated_at` 更晚。
    """
    fid = _folder(client, "英语")
    older = _session(client, fid, "先建的")
    newer = _session(client, fid, "后建的")

    _activate(client, older)
    _say(client, older, "还在聊")

    rows = {
        item["session_id"]: item
        for item in client.get("/api/sessions", params={"folder_id": fid}).json()["items"]
    }
    assert rows[older]["updated_at"] > rows[newer]["updated_at"]
    assert rows[newer]["updated_at"] == rows[newer]["created_at"]


def test_folder_updated_at_and_counts_come_from_its_live_sessions(
    client: TestClient,
) -> None:
    fid = _folder(client, "英语")
    sid = _session(client, fid, "背单词")
    archived = _session(client, fid, "旧的")
    client.patch(f"/api/sessions/{archived}", json={"archived": True})

    folder = client.get("/api/folders").json()["items"][0]
    # 归档的那条不算：归档本来就是"别再占着侧栏了"
    assert folder["session_count"] == 1
    assert folder["updated_at"] >= client.get("/api/sessions").json()["items"][0][
        "updated_at"
    ]

    _activate(client, sid)
    _say(client, sid, "说一句")
    after = client.get("/api/folders").json()["items"][0]
    assert after["updated_at"] > folder["updated_at"]


@pytest.mark.parametrize("direction", ["sideways", "", "UP"])
def test_an_unknown_move_direction_is_rejected(
    client: TestClient, direction: str
) -> None:
    fid = _folder(client, "英语")
    response = client.patch(f"/api/folders/{fid}", json={"move": direction})
    assert response.status_code == 422, response.text
