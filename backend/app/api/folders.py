"""`/api/folders` —— 长期领域（「英语学习」）。

一个 folder 下可以同时有多个 `active` 会话（**用户裁定**，P4）：
领域内多主题并行是常态（背单词与学翻译可以同时进行），
因此这里**没有**"同时只能激活一个"的唯一约束。

folder 的存在意义不只是分组：`c_retrieval_dims` 决定 C 证据的检索维度 ——
同领域的历史证据才相关，跨领域套用是「别人有效所以我有效」的典型错误。

## 这一组端点里，`PATCH` / `DELETE` 是**侧栏整理**，不是内容写

改名、归档、挪一位、删除 —— 四件事都不改变任何学习含义：
`state` 不动、修订表不写、`version` 不涨。唯一真的会丢掉东西的是 `DELETE`，
所以它把"删掉了多少"如实数回去，让界面能先把数量说给用户听。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_session
from app.core.errors import InvalidField
from app.session_domain import reads, writes
from app.session_domain.models import Folder as FolderModel
from app.session_domain.schemas import FolderCreate, FolderPatch, FolderRead

router = APIRouter(prefix="/api/folders", tags=["folders"])


def _read(row: FolderModel, *, stat: reads.FolderStat | None) -> FolderRead:
    """文件夹的读模型。计数与"最近动静"来自派生统计，不是表上的列。"""
    updated_at: datetime = (
        stat.updated_at if stat is not None else row.created_at
    )
    return FolderRead(
        folder_id=row.folder_id,
        name=row.name,
        goal=row.goal,
        c_retrieval_dims=row.c_retrieval_dims,
        created_at=row.created_at,
        sort_order=row.sort_order,
        archived_at=row.archived_at,
        updated_at=updated_at,
        session_count=stat.session_count if stat is not None else 0,
        active_session_count=stat.active_session_count if stat is not None else 0,
    )


@router.get("", response_model=ItemsResponse[FolderRead])
def list_folders(
    include_archived: bool = Query(
        default=False,
        description=(
            "是否把归档的文件夹也列出来。"
            "侧栏默认看不到它们（界面上另有一个「已归档」区）。"
        ),
    ),
    session: Session = Depends(get_session),
) -> dict[str, list[FolderRead]]:
    stats = reads.folder_stats(session)
    return ok([
        _read(row, stat=stats.get(row.folder_id))
        for row in reads.list_folders(session, include_archived=include_archived)
    ])


@router.post("", response_model=FolderRead, status_code=201)
def create_folder(
    body: FolderCreate, session: Session = Depends(get_session)
) -> FolderRead:
    row = writes.create_folder(
        session,
        name=body.name,
        goal=body.goal,
        c_retrieval_dims=body.c_retrieval_dims,
    )
    return _read(row, stat=None)


@router.patch("/{folder_id}", response_model=FolderRead)
def patch_folder(
    folder_id: str, body: FolderPatch, session: Session = Depends(get_session)
) -> FolderRead:
    """改名 / 归档 / 挪一位。三件事可以同时发生，也可以一件都没有。

    `archived=false` 是"取消归档"，不传 `archived` 是"别动它" ——
    这两件事在合并语义里必须分开（写模型用 `exclude_unset`）。
    一个什么都没改的 `PATCH` 直接 422：它多半说明客户端拼错了字段名。
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise InvalidField(
            "这个请求什么都没改（name / goal / archived / move 至少要给一个）。",
            detail={"field": None},
        )
    row = reads.get_folder(session, folder_id)
    if "name" in updates:
        row = writes.rename_folder(session, folder_id, name=updates["name"] or "")
    if "goal" in updates:
        row = writes.set_folder_goal(session, folder_id, goal=updates["goal"])
    if "archived" in updates:
        row = writes.archive_folder(
            session, folder_id, archived=bool(updates["archived"])
        )
    if updates.get("move"):
        row = writes.move_folder(session, folder_id, direction=updates["move"])
    stats = reads.folder_stats(session)
    return _read(row, stat=stats.get(row.folder_id))


@router.delete("/{folder_id}", response_model=dict[str, int])
def delete_folder(
    folder_id: str, session: Session = Depends(get_session)
) -> dict[str, int]:
    """删除文件夹**连同里面的会话**。

    返回 `{"deleted_sessions": n, "deleted_folders": 1}`：界面要先用这个数字
    跟用户确认 —— 在删掉 3 个会话、12 条对话之前，"确定删除吗"这句话是不够的。
    """
    return writes.delete_folder(session, folder_id)
