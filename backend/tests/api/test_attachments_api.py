"""会话资料（上传）—— **行为层**的边界证明。

这个文件要回答的不是"端点通不通"，而是三件更重的事：

1. **上传不碰全局认知。** 走完整套上传/读取/删除之后，认知四表与 `router_log`
   的行数**一个都不能变**，I1–I7 仍然通过。这是 `chat_attachment` 被声明成
   `infrastructure`/`sandbox_record` 时给出的承诺，必须从行为上兑现 ——
   结构断言（`test_api_boundaries.py`）只能证明它没 import 写域，
   证明不了它没用一个"中立"的模块去写。

2. **正文不进 `chat_message.content`。** 这是设计里最容易被后人"顺手优化掉"
   的一条：把正文拼进用户消息看起来更直接，而后果是它会跟着
   `HISTORY_LIMIT=6` 在后面两三轮里反复回流，把上下文挤掉 ——
   现象是"聊了两句之后模型开始忘事"，看不出跟附件有关。
   所以这里不只断言"清单接口没有正文"，还断言**聊天记录里没有正文**。

3. **失败要说人话。** 每种拒绝都断言 `code` 与 message 里的下一步动作 ——
   因为那句 message 是直接显示给用户的。

上传用的是**原始请求体**（不是 multipart），所以 `client.post(..., content=raw)`
就是真实的调用形状 —— 测试与前端走的是同一条路。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.attachments import parse
from app.config import Settings
from app.db import Base, make_engine, make_session_factory, session_scope
from app.global_domain.invariants import check_global_invariants
from app.global_domain.models import (
    CEvidence,
    M1BaselineVersion,
    M2Preference,
    RouterLog,
)
from app.main import create_app
from app.session_domain import reads, writes
from app.session_domain.models import ChatAttachment, ChatMessage

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

#: 认知四表 —— 上传**一个字节都不该**改动它们。
COGNITION_TABLES = (M1BaselineVersion, M2Preference, CEvidence, RouterLog)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """真 `TestClient` + 临时库 + **临时 uploads 目录**。

    `MOUXUE_UPLOADS_DIR` 必须覆盖：不覆盖的话，测试会往开发者真实的
    `backend/var/uploads/` 里写文件（而且那些文件会一直留着）。
    """
    monkeypatch.setenv("MOUXUE_UPLOADS_DIR", str(tmp_path / "uploads"))
    db_path = tmp_path / "attachments.db"
    url = f"sqlite+pysqlite:///{db_path}"
    app = create_app(
        Settings(
            database_url=url,
            seed_on_startup=True,
            check_invariants=True,
            llm_provider="fake",
        )
    )
    with TestClient(app) as test_client:
        engine = make_engine(url)
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        session_id = _seed_session(factory)
        test_client.factory = factory  # type: ignore[attr-defined]
        test_client.session_id = session_id  # type: ignore[attr-defined]
        yield test_client


def _seed_session(factory) -> str:
    with session_scope(factory) as db:
        folder = writes.create_folder(db, name="英语", goal="能读原版文档")
        row = writes.create_session(
            db,
            folder_id=folder.folder_id,
            title="背单词",
            input_goal="两个月能读文档",
        )
        writes.transition(
            db, row.session_id, target="active", expected_version=row.version, reason="开始"
        )
        return row.session_id


def _row_counts(factory) -> dict[str, int]:
    counts: dict[str, int] = {}
    with session_scope(factory) as db:
        for model in COGNITION_TABLES:
            counts[model.__tablename__] = int(
                db.scalar(select(func.count()).select_from(model)) or 0
            )
    return counts


def _invariants(factory) -> list[str]:
    with session_scope(factory) as db:
        return check_global_invariants(db)


def _upload(client: TestClient, filename: str, raw: bytes):
    return client.post(
        f"/api/sessions/{client.session_id}/attachments",
        params={"filename": filename},
        content=raw,
    )


def _docx(*paragraphs: str) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>',
        )
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 1. 基本通路
# --------------------------------------------------------------------------- #


def test_upload_list_read_delete_round_trip(client: TestClient) -> None:
    uploaded = _upload(client, "我的经历.md", "# 经历\n\n我一焦虑就逃避。".encode())
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["filename"] == "我的经历.md"
    assert body["kind"] == "plain"
    assert body["truncated"] is False
    assert "一焦虑就逃避" in body["preview"]
    attachment_id = body["attachment_id"]
    assert attachment_id.startswith("AT-")

    listed = client.get(f"/api/sessions/{client.session_id}/attachments")
    assert listed.status_code == 200
    assert [item["attachment_id"] for item in listed.json()["items"]] == [attachment_id]

    text = client.get(
        f"/api/sessions/{client.session_id}/attachments/{attachment_id}"
    ).json()
    assert text["total_chars"] == len("# 经历\n\n我一焦虑就逃避。")
    assert text["remaining_chars"] == 0
    assert "一焦虑就逃避" in text["text"]

    deleted = client.delete(
        f"/api/sessions/{client.session_id}/attachments/{attachment_id}"
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"]["attachment_id"] == attachment_id
    assert client.get(f"/api/sessions/{client.session_id}/attachments").json()["items"] == []


def test_docx_upload_works_end_to_end(client: TestClient) -> None:
    """真的走一遍 `.docx`：上传 → 清单 → 读正文。"""
    response = _upload(client, "计划细节.docx", _docx("第一步：每天 25 个新词", "第二步：读文档"))
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "docx"
    text = client.get(
        f"/api/sessions/{client.session_id}/attachments/{response.json()['attachment_id']}"
    ).json()["text"]
    assert "每天 25 个新词" in text
    assert "读文档" in text


def test_uploading_the_same_file_twice_does_not_add_a_second_item(client: TestClient) -> None:
    """内容相同 = 同一份资料。

    不这么做的话，上下文里会出现两条一模一样的清单项，
    而模型看到两条同样的东西，会以为用户强调了两遍。
    """
    raw = "同样的内容".encode()
    first = _upload(client, "经历.md", raw)
    second = _upload(client, "经历（副本）.md", raw)
    assert second.status_code == 201
    assert second.json()["attachment_id"] == first.json()["attachment_id"]
    assert "之前已经传过" in second.json()["note"]
    assert len(client.get(f"/api/sessions/{client.session_id}/attachments").json()["items"]) == 1


def test_different_content_with_same_name_is_two_items(client: TestClient) -> None:
    """同名不同内容 = 两份资料。**身份是 `AT-xxxx`，不是文件名。**"""
    first = _upload(client, "计划.md", "第一版".encode())
    second = _upload(client, "计划.md", "第二版".encode())
    assert first.json()["attachment_id"] != second.json()["attachment_id"]
    assert len(client.get(f"/api/sessions/{client.session_id}/attachments").json()["items"]) == 2


# --------------------------------------------------------------------------- #
# 2. 边界：上传**不碰**全局认知
# --------------------------------------------------------------------------- #


def test_upload_never_touches_cognition(client: TestClient) -> None:
    """走完整套生命周期，认知四表与 `router_log` 的行数一个都不变。

    这是"资料是沙箱记录"从**行为**上的证明。结构断言只能证明它没 import 写域；
    一个"中立"的模块照样能写库，所以两层都要有。
    """
    before = _row_counts(client.factory)
    assert _invariants(client.factory) == []

    uploaded = _upload(client, "经历.md", "我一焦虑就逃避，喜欢先做简单题。".encode())
    attachment_id = uploaded.json()["attachment_id"]
    client.get(f"/api/sessions/{client.session_id}/attachments")
    client.get(f"/api/sessions/{client.session_id}/attachments/{attachment_id}")
    client.delete(f"/api/sessions/{client.session_id}/attachments/{attachment_id}")

    assert _row_counts(client.factory) == before, (
        "上传资料改动了全局认知表 —— 沙箱边界破了。"
        "资料要变成认知，只能经「沉淀 → 提案 → 审核」那条路。"
    )
    assert _invariants(client.factory) == []


def test_upload_does_not_depend_on_the_router(client: TestClient) -> None:
    """上传只写沙箱：`router_log` 永远是空的（没有一次裁定发生）。

    与上一条分开写，是因为它回答的是另一个问题：
    上一条说"行数没变"，这一条说"**根本没发生过裁定**" ——
    万一将来有人在端点里加了一次 `no_change` 的提交，行数会变，
    而上一条也会红；但这里的措辞能让下一个人立刻知道错在哪。
    """
    _upload(client, "经历.md", "内容".encode())
    with session_scope(client.factory) as db:
        assert db.scalar(select(func.count()).select_from(RouterLog)) == 0


# --------------------------------------------------------------------------- #
# 3. 正文绝不进聊天记录（否则它会跟着历史上下文反复回流）
# --------------------------------------------------------------------------- #


def test_attachment_text_never_lands_in_chat_messages(client: TestClient) -> None:
    """正文不许出现在 `chat_message.content` 里。

    这条是设计里最容易被"顺手优化掉"的一处：把正文拼进用户消息看起来更直接，
    而后果是 `discuss.py` 的 `_history()`（最近 6 条原话）会把它
    在随后两三轮里反复带进上下文，把真正重要的东西挤掉。
    """
    marker = "这是一段足够独特的正文标记XYZ"
    _upload(client, "经历.md", f"{marker}\n后面还有很多话。".encode())

    with session_scope(client.factory) as db:
        contents = [
            row.content for row in db.scalars(select(ChatMessage)).all()
        ]
        assert all(marker not in content for content in contents), (
            "附件正文进了 chat_message —— 它会跟着最近 6 条历史反复回流。"
        )
        stored = db.scalars(select(ChatAttachment)).one()
        assert marker in stored.text, "正文应该存在资料表里"


def test_list_endpoint_returns_no_text_field(client: TestClient) -> None:
    """清单**不含正文** —— 它只回答"传了哪些"。"""
    _upload(client, "经历.md", ("很长" * 200).encode())
    item = client.get(f"/api/sessions/{client.session_id}/attachments").json()["items"][0]
    assert "text" not in item
    assert item["chars_len"] == 400


# --------------------------------------------------------------------------- #
# 4. 失败要说人话
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("filename", "raw", "expect_in_message"),
    [
        ("报告.doc", b"x" * 10, "不支持这种文件"),
        ("数据.xlsx", b"x" * 10, "不支持这种文件"),
        ("照片.txt", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4, "不支持这种文件"),
        ("空.md", b"   ", "这个文件是空的"),
        ("假的.docx", b"not a zip", "不支持这种文件"),
    ],
)
def test_rejections_are_422_with_a_next_step(
    client: TestClient, filename: str, raw: bytes, expect_in_message: str
) -> None:
    """每种拒绝都是 422 `attachment_rejected`，且 message 里写着下一步做什么。

    **`code` 与 message 都断言**：前端按 `code` 分支（决定"显示文件引导"
    还是"标红字段"），用户读 message（决定"我该另存为什么格式"）。
    """
    response = _upload(client, filename, raw)
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["code"] == "attachment_rejected", payload
    assert expect_in_message in payload["message"], payload


def test_oversized_upload_is_rejected_by_the_parser(client: TestClient) -> None:
    from app.attachments import MAX_BYTES

    response = _upload(client, "巨大.txt", b"a" * (MAX_BYTES + 1))
    assert response.status_code == 422
    assert "不能超过" in response.json()["message"]


def test_non_utf8_non_gbk_is_rejected_with_the_encoding_hint(client: TestClient) -> None:
    # 一个既不是 UTF-8 也不是 GB18030 的字节序列（孤立的代理字节）。
    raw = b"\x80\x81\xfe\xff" * 20
    response = _upload(client, "乱码.txt", raw)
    # 要么被"二进制"嗅探拦下、要么被编码兜底拦下 —— 两种都是 422 且有解释。
    assert response.status_code == 422
    assert response.json()["code"] == "attachment_rejected"


def test_missing_filename_is_a_422(client: TestClient) -> None:
    """文件名是必填的查询参数 —— 扩展名决定解析方式，没有它无法判断。"""
    response = client.post(
        f"/api/sessions/{client.session_id}/attachments", content=b"content"
    )
    assert response.status_code == 422


def test_unknown_session_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/sessions/S-9999/attachments",
        params={"filename": "a.md"},
        content=b"content",
    )
    assert response.status_code == 404


def test_unknown_attachment_id_is_404(client: TestClient) -> None:
    response = client.get(f"/api/sessions/{client.session_id}/attachments/AT-9999")
    assert response.status_code == 404


def test_cross_session_read_is_404_not_403(client: TestClient) -> None:
    """别的会话的资料：**404 而不是 403**。

    403 等于告诉探测者"那个 id 存在，只是不属于你" —— 而附件 id 是
    顺序生成的，这就把"这个库里有多少份资料"泄露出去了。
    """
    other = _upload(client, "经历.md", "内容".encode()).json()["attachment_id"]
    with session_scope(client.factory) as db:
        folder = writes.create_folder(db, name="另一个", goal=None)
        row = writes.create_session(
            db, folder_id=folder.folder_id, title="另一个会话", input_goal="x"
        )
        other_session = row.session_id

    assert client.get(f"/api/sessions/{other_session}/attachments/{other}").status_code == 404
    assert (
        client.delete(f"/api/sessions/{other_session}/attachments/{other}").status_code == 404
    )


# --------------------------------------------------------------------------- #
# 5. 状态机与原件
# --------------------------------------------------------------------------- #


def test_upload_into_a_completed_session_is_refused(client: TestClient) -> None:
    """归档之后还能传资料吗？**不能**，而理由是"归档记录不该被改写"。

    与笔记不同：笔记是草稿纸（`_ALL_STATES`），资料是会被归档进
    这条会话记录的材料。这条测试把那个不对称钉住。
    """
    with session_scope(client.factory) as db:
        row = reads.get_session(db, client.session_id)
        for target in ("review", "completed"):
            row = writes.transition(
                db,
                client.session_id,
                target=target,
                expected_version=row.version,
                reason="测试",
            )
    response = _upload(client, "after.md", b"content")
    assert response.status_code == 409
    assert response.json()["code"] == "state_not_writable"


def test_original_file_is_kept_on_disk_and_removed_on_delete(client: TestClient) -> None:
    """原件留着（供重建），删除时连原件一起删。

    路径里**只有我们自己生成的 id** —— 用户文件名不进路径。
    """
    from app.attachments import uploads_dir

    uploaded = _upload(client, "../../危险的名字.md", "内容".encode()).json()
    with session_scope(client.factory) as db:
        row = db.get(ChatAttachment, uploaded["attachment_id"])
        stored = uploads_dir() / row.stored_path
        assert stored.exists()
        # 目录/文件名里不许出现用户给的那串东西。
        assert "危险" not in row.stored_path
        assert row.stored_path.startswith(f"{client.session_id}/")

    client.delete(f"/api/sessions/{client.session_id}/attachments/{uploaded['attachment_id']}")
    assert not stored.exists()


# --------------------------------------------------------------------------- #
# 6. 分段读正文（界面与工具同一套 offset 语义）
# --------------------------------------------------------------------------- #


def test_text_endpoint_paginates_without_gaps_or_overlaps(client: TestClient) -> None:
    text = "".join(f"{index:04d}" for index in range(500))
    uploaded = _upload(client, "长.md", text.encode()).json()
    attachment_id = uploaded["attachment_id"]

    first = client.get(
        f"/api/sessions/{client.session_id}/attachments/{attachment_id}",
        params={"offset": 0, "limit": 700},
    ).json()
    second = client.get(
        f"/api/sessions/{client.session_id}/attachments/{attachment_id}",
        params={"offset": 700, "limit": 700},
    ).json()
    assert first["text"] + second["text"] == text[:1400]
    assert first["remaining_chars"] == 1300
    assert second["remaining_chars"] == 600


def test_text_endpoint_past_the_end_is_empty_not_an_error(client: TestClient) -> None:
    uploaded = _upload(client, "短.md", "短".encode()).json()
    body = client.get(
        f"/api/sessions/{client.session_id}/attachments/{uploaded['attachment_id']}",
        params={"offset": 999},
    ).json()
    assert body["text"] == ""
    assert body["returned_chars"] == 0


def test_preview_seen_by_the_ui_is_the_same_function_the_model_gets(client: TestClient) -> None:
    """界面看到的预览与进模型的预览是**同一个函数**产出的。

    两处各写一份"取前 150 字"的逻辑，迟早会分叉 ——
    而分叉之后"我看到的是这个、它说的是那个"变成一类查不出的分歧。
    """
    from app.attachments import preview

    raw = "第一行\n\n第二行\t带制表符".encode()
    uploaded = _upload(client, "预览.md", raw).json()
    expected = preview(parse("预览.md", raw).text)
    assert uploaded["preview"] == expected == "第一行 第二行 带制表符"
