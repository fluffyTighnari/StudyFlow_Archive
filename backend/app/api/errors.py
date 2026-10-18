"""领域异常 → HTTP 响应的唯一映射点（M0 §9.1）。

放在 API 层而不是各路由里，是为了让「同一种失败在任何端点上都是同一种响应」
成为结构性事实：域层只抛 `DomainError` 子类，HTTP 状态码只在 `errors.py` 出现一次。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import DomainError


async def _domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(status_code=exc.http_status, content=exc.to_payload())


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
