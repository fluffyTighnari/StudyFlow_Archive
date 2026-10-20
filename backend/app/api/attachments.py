"""会话资料（用户上传的文本文件）—— 沙箱域的第四组端点（P6+.5）。

## 为什么上传用**原始请求体**，而不是 `multipart/form-data`

标准做法是 multipart，而 FastAPI 的 `UploadFile` 需要 `python-multipart` ——
那个包**没有装**（依赖只有 fastapi/uvicorn/pydantic/sqlalchemy/alembic）。

两个选择摆在面前：加一个依赖，或者让请求体直接就是文件字节。
选后者，理由是三条：

1. **单文件上传不需要 multipart 的任何能力**。multipart 解决的是
   "一个请求里带多个字段 + 多个文件"，而这里是"一次一份文件"；
2. **它让前端更简单，不是更复杂**。multipart 要构造 `FormData`；
   原始体是 `fetch(url, {method: "POST", body: file})` —— 浏览器自己
   把 `File` 当字节流发出去，`Content-Type` 也自动带上；
3. **没有 base64 的 33% 膨胀**（JSON 里塞 base64 是第三种做法，直接排除）。

代价写在明处：**一次只能传一份文件**，且文件名走查询参数
（`?filename=经历.md`，非 ASCII 由客户端 `encodeURIComponent`，
服务端由 FastAPI 解码）。哪天真要"一次传多份 + 附带字段"，
再换成 multipart 是**追加**而不是重写 —— 路径不变，只是多接一种
`Content-Type`。

## 四条端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/sessions/{session_id}/attachments?filename=x.md` | 上传（体 = 文件字节） |
| GET | `/api/sessions/{session_id}/attachments` | 清单（**不含正文**） |
| GET | `/api/sessions/{session_id}/attachments/{attachment_id}` | 读正文（可分段） |
| DELETE | `/api/sessions/{session_id}/attachments/{attachment_id}` | 删掉（含原件） |

## 它**不**碰 Router

资料是沙箱里的东西：上传本身绝不写全局认知。经历要变成基线/偏好/证据，
只能由模型在讨论里提 `submit_global_update`（走审核队列），
或者用户在沉淀那一步自己选。

这是 `tests/api/test_api_boundaries.py` 里
`test_session_write_routes_never_touch_the_router` 要求的（本模块
不许在签名里出现 `Router`），也是 `tests/api/test_attachments_api.py`
从行为上再证一遍的事（上传前后逐表比对行数）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse
from app.api.deps import get_session
from app.session_domain import reads, writes

router = APIRouter(prefix="/api/sessions", tags=["attachments"])


# --------------------------------------------------------------------------- #
# DTO
# --------------------------------------------------------------------------- #


class _Read(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachmentRead(_Read):
    """一份资料的元数据。

    **没有 `text` 字段** —— 清单接口不该顺带把 20 万字正文吐给界面
    （界面要显示的是"传了哪些"，正文是点开某一份才需要的）。
    正文走 `AttachmentTextRead`。
    """

    attachment_id: str
    session_id: str
    filename: str
    ext: str
    kind: str
    bytes_len: int
    chars_len: int
    truncated: bool
    #: 解析取舍说明（编码、表格拍平、不取了什么）。
    note: str
    #: 开头一段。**与进模型上下文的那一段是同一个函数产出的**
    #: （`attachments.preview`）—— 界面看到的和模型看到的不该是两个东西。
    preview: str
    created_at: str


class AttachmentListRead(ItemsResponse[AttachmentRead]):
    """`/api/sessions/{id}/attachments` 的响应：统一信封 + 一个兄弟字段。

    ## 它就是这个信封的**出处**

    本轮统一形状时，`{"items": [...]}` 这个约定的原型正是这里 ——
    因为"这个会话有哪些资料"这个列表**天然需要一个兄弟字段**：是哪个会话的。
    别的 17 个端点当初都是裸数组，只有这一个是信封 ——
    而那个"不一致"现在变成了标准，不是因为它新，是因为它**对**。

    ## 为什么用继承而不是重写一遍 items 字段

    `class X(ItemsResponse[AttachmentRead])` 意味着**信封只有一份定义**。
    重写一遍字段会让"信封"变成两个可以各自漂移的东西 ——
    而这正是本轮要消灭的那种分叉。
    """

    session_id: str


class AttachmentTextRead(_Read):
    """正文的一段。"""

    attachment_id: str
    filename: str
    total_chars: int
    offset: int
    returned_chars: int
    #: 还有多少字没给。界面据此显示"还能往下翻"。
    remaining_chars: int
    text: str


class DeletedAttachmentRead(_Read):
    """删除结果。

    **返回被删掉的那一条**，而不是空体：用户点了删除之后，
    界面上要能从响应里确认"删的是这份"（尤其是重名文件）。
    这也是本项目"写端点必须声明响应模型"那条规矩的自然结果。
    """

    deleted: AttachmentRead


# --------------------------------------------------------------------------- #
# 转换
# --------------------------------------------------------------------------- #


def _to_read(row) -> AttachmentRead:
    from app.attachments import preview

    return AttachmentRead(
        attachment_id=row.attachment_id,
        session_id=row.session_id,
        filename=row.filename,
        ext=row.ext,
        kind=row.kind,
        bytes_len=row.bytes_len,
        chars_len=row.chars_len,
        truncated=row.truncated,
        note=row.note,
        preview=preview(row.text),
        created_at=row.created_at.isoformat(),
    )


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


@router.post(
    "/{session_id}/attachments",
    response_model=AttachmentRead,
    status_code=201,
)
async def upload_attachment(
    session_id: str,
    request: Request,
    session: Session = Depends(get_session),
    filename: str = Query(
        ...,
        min_length=1,
        max_length=300,
        description=(
            "原始文件名（含扩展名）。**走查询参数而不是请求头**："
            "非 ASCII 文件名在 HTTP 头里必须自己编码，而查询参数由客户端与服务端"
            "各自处理一次就够了。扩展名决定解析方式（`.txt`/`.md`/`.docx`）。"
        ),
    ),
) -> AttachmentRead:
    """上传一份资料。

    **请求体就是文件字节**（`Content-Type` 随便，我们不看它 —— 看的是
    扩展名 + 内容嗅探两样）。见模块文档里为什么不用 multipart。

    解析失败一律 422 `attachment_rejected`，`message` 里写着下一步做什么。
    """
    raw = await request.body()
    result = writes.attach_file(session, session_id, filename=filename, raw=raw)
    payload = _to_read(result.attachment)
    # 重复上传时**返回 201 与同一条记录**，而不是 409：
    # 从用户的角度，"这份资料现在在会话里"这个事实已经成立了，
    # 报错只会让界面把它画成一个失败。
    # 但重复这件事必须说出来，否则界面会以为多了一条 —— 见响应头。
    if result.duplicate:
        payload.note = f"（这份文件之前已经传过：{result.attachment.attachment_id}，没有重复入库）{payload.note}"
    return payload


@router.get("/{session_id}/attachments", response_model=AttachmentListRead)
def list_attachments(
    session_id: str, session: Session = Depends(get_session)
) -> AttachmentListRead:
    """这个会话收到的资料清单（**不含正文**，按上传时间升序）。"""
    reads.get_session(session, session_id)
    return AttachmentListRead(
        session_id=session_id,
        items=[_to_read(row) for row in reads.list_attachments(session, session_id)],
    )


@router.get(
    "/{session_id}/attachments/{attachment_id}",
    response_model=AttachmentTextRead,
)
def read_attachment_text(
    session_id: str,
    attachment_id: str,
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0, description="从第几个字符开始（0 起）。"),
    limit: int = Query(
        20_000,
        ge=1,
        le=200_000,
        description=(
            "这次最多给多少字。默认 20000 —— 比给模型的那 6000 大，"
            "因为界面可以整段渲染，而模型要省窗口。"
        ),
    ),
) -> AttachmentTextRead:
    """读一份资料的正文（可分段）。

    与工具 `read_attachment` **读的是同一行、同一套 offset 语义** ——
    界面与模型看到的正文必须是同一份，否则"我看到的是这个、它说的是那个"
    会变成一类查不出的分歧。
    """
    row = reads.require_attachment(session, session_id, attachment_id)
    total = len(row.text)
    start = min(offset, total)
    text = row.text[start : start + limit]
    return AttachmentTextRead(
        attachment_id=row.attachment_id,
        filename=row.filename,
        total_chars=total,
        offset=start,
        returned_chars=len(text),
        remaining_chars=total - start - len(text),
        text=text,
    )


@router.delete(
    "/{session_id}/attachments/{attachment_id}",
    response_model=DeletedAttachmentRead,
)
def delete_attachment(
    session_id: str, attachment_id: str, session: Session = Depends(get_session)
) -> DeletedAttachmentRead:
    """删掉一份资料（连同磁盘上的原件）。见 `writes.delete_attachment`。"""
    row = writes.delete_attachment(session, session_id, attachment_id)
    return DeletedAttachmentRead(deleted=_to_read(row))
