"""`/api/router-logs` —— 统一审计日志（M0 §6.1「唯一审计源」）。

只读。**没有写接口，也不会有** —— 日志由 Router 在每次写入事件中生成；
一个能手工追加日志的端点会让整个审计体系失去意义。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.global_domain import reads
from app.global_domain.schemas import RouterLogRead

router = APIRouter(prefix="/api/router-logs", tags=["audit"])


@router.get("", response_model=list[RouterLogRead])
def list_router_logs(
    target: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[RouterLogRead]:
    """按时间倒序返回审计日志（可选按 target 过滤）。

    `target` 用 `M1.V-SE` 这类规范名 —— 与 `RouterLog.target` 同源，
    不在这里解析 `V-SE`：那会变成 A9 命名空间映射的第二处实现。
    """
    return [
        RouterLogRead.model_validate(row)
        for row in reads.list_router_logs(session, target=target, limit=limit)
    ]
