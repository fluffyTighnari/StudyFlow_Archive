"""FastAPI 依赖注入：数据库 Session 与 Router 实例。

**事务边界在这里**：`session_scope` 正常提交、异常回滚。
上层的路由处理函数与 `Router` 都不调用 `commit()` ——
这正是 P2 里那条「Router 不决定事务边界」的断言在真实链路里的落点。

**不变式自检不在这里。** P5 一度把它写在下面 `yield` 之后的收尾代码里
（「写入完成、尚未提交的那一瞬间」），但**那一瞬间在 HTTP 上并不存在**：
这个 FastAPI/Starlette 版本在响应**已经发出之后**才关闭依赖的退出栈，
于是自检抛的异常只能变成
`RuntimeError: Caught handled exception, but response already started.`
—— 检查跑了，客户端却收到 201。

所以自检落在 `Router._verify_after_write`（唯一写入汇聚点）。这里只负责
**把配置传下去**：`session.info` 上的开关决定 Router 要不要自检。
配置住在请求边界、判定住在写入边界，两边互不知道对方的实现。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.ids import next_intent_id
from app.db import DbSequenceStore, session_scope
from app.global_domain.invariants import set_invariants_enabled
from app.router import Router


def get_session(request: Request) -> Iterator[Session]:
    """每请求一个 Session，一个事务。

    退出时的 `session_scope` 会先 commit 再 close。`expire_on_commit=False`
    （见 `db.make_session_factory`）保证已加载的属性在 close 之后仍可读 ——
    但**路由处理函数仍然显式构造 DTO**，不依赖这一点：
    响应序列化时机由框架决定，把「对象还活着」当成前提是不可靠的。
    """
    with session_scope(request.app.state.session_factory) as session:
        set_invariants_enabled(session, request.app.state.check_invariants)
        yield session


def get_router(session: Session = Depends(get_session)) -> Router:
    """绑定当前请求 Session 的 Router。

    令牌在 `Router.__init__` 内部签发，**不外泄**：处理函数拿到的只是一个
    能调 `submit` / `resolve` / `rollback` 的对象，拿不到写函数本身。
    """
    return Router(session)


def get_settings(request: Request) -> Settings:
    """当前应用的配置（题库目录、LLM、不变式开关…）。

    ★ **为什么走 `app.state` 而不是每次 `Settings.from_env()`**：
    再读一次环境变量会得到一份**可能不同**的配置 —— 而"题库目录是哪个"
    这种值在进程生命周期里改掉的话，会出现"这次抽题读的是 A 目录、
    上次记的历史来自 B 目录"，而抽题器无从察觉。
    配置在 `create_app` 时定一次，之后只读。
    """
    return request.app.state.settings


def new_ui_intent_id(session: Session) -> str:
    """为 UI 发起的写入分配一个全局唯一的 `intent_id`。

    为什么必须在这里分配：`intent_id` 是 `update_intent` 的主键，而 Router
    **拒绝**重复与已占用（422）。UI 每次点击都是独立请求，无法持有跨请求计数器；
    若让 Router 静默改名，调用方手上的 id 就会与库里的对不上，
    而用户界面正靠这个 id 回传裁决。

    这正是 M0 §14 记的那条分工：**契约要求 id，工具层负责保证唯一。**
    """
    return next_intent_id(DbSequenceStore(session))
