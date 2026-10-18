"""`/api/folders` —— 长期领域（「英语学习」）。

一个 folder 下可以同时有多个 `active` 会话（**用户裁定**，P4）：
领域内多主题并行是常态（背单词与学翻译可以同时进行），
因此这里**没有**"同时只能激活一个"的唯一约束。

folder 的存在意义不只是分组：`c_retrieval_dims` 决定 C 证据的检索维度 ——
同领域的历史证据才相关，跨领域套用是「别人有效所以我有效」的典型错误。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.session_domain import reads, writes
from app.session_domain.schemas import FolderCreate, FolderRead

router = APIRouter(prefix="/api/folders", tags=["folders"])


@router.get("", response_model=list[FolderRead])
def list_folders(session: Session = Depends(get_session)) -> list[FolderRead]:
    return [FolderRead.model_validate(row) for row in reads.list_folders(session)]


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
    return FolderRead.model_validate(row)
