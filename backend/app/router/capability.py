"""Router 的令牌签发封装。

本模块是**全工程唯一** import `issue_token` 的地方（架构规则 ARCH-03 强制）。
其他任何模块想要令牌，只能经由 Router 的公开接口间接获得，而 Router 的公开接口
本身会执行 precheck 与策略判定。
"""

from __future__ import annotations

from app.core.capability import RouterToken, issue_token


def new_token() -> RouterToken:
    """签发一枚令牌。仅供 `app.router` 内部调用。"""
    return issue_token()


__all__ = ["new_token", "RouterToken"]
