"""FastAPI 应用装配（M0 §9.1 的 `main.py` 入口）。

**启动时不做任何治理相关的决定**：只建表、装种子、挂路由。
「谁可以写全局」这件事在 `Router` 与 `writes.py` 里已经定死，
应用装配层多一条捷径就会把 P2 的全部保证作废。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api import errors as api_errors
from app.api import (
    attachments,
    discuss,
    evidence,
    llm,
    folders,
    m1,
    preferences,
    proposals,
    router_logs,
    sedimentation,
    sessions,
)
from app.agent.llm import LLMClient
from app.agent.llm_factory import make_client_from_profile, make_client_from_settings
from app.config import Settings
from app.db import Base, make_engine, make_session_factory, session_scope
from app.llm_config import active_profile, read_secret
from app.seeds.loader import load_all


def make_llm_client(settings: Settings) -> LLMClient:
    """按配置构造 LLM 客户端。**未知 provider 直接抛错。**

    这是刻意的"启动即失败"。如果这里退化成"不认识就用默认的那个"，
    那么一个拼错的 `MOUXUE_LLM_PROVIDER` 会表现成
    「模型答得意外地好」（fake 的脚本恰好合适）或者「模型一直很慢」
    （其实打到了远端），而这两种表现都不会让你想到去看环境变量。

    实现搬去了 `app/agent/llm_factory.py`：那里还要处理"某家公司"这一类
    （`MOUXUE_LLM_PROVIDER=deepseek`）的默认地址与默认模型，
    而那件事与"应用怎么组装"无关。这里保留这个名字，
    因为 `tools/livesse.py` 等地方按它理解启动流程。
    """
    return make_client_from_settings(settings)


# 表必须被 import 过才会注册进 `Base.metadata`（否则 `create_all`
# 建不出它们，而 I1 的「每张表都要表态」也无从检查 —— 一张没注册的表
# 不会出现在 `unclassified_tables()` 里，于是"检查通过"是假象）。
# 放在这里而不是 `app/db.py`：`db` 是基础设施，不该反向 import 领域。
import app.session_domain.models  # noqa: E402,F401  (导入即注册)
import app.llm_config  # noqa: E402,F401  (导入即注册)


class HealthRead(BaseModel):
    """存活探针的响应。

    之所以是一个显式模型而不是 `dict[str, str]`：`dict[str, str]` 会让
    OpenAPI 里这条路径的响应 schema 退化成「任意对象」，
    而「探针只回答活着，不泄漏任何全局状态」这件事就失去了可断言的形式。
    """

    status: str = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        # 目前用 create_all()；Alembic 迁移见 M0 §14 的延期说明。
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)

        if settings.seed_on_startup:
            # 种子写的是**目录表**（m1_variable / m1_variable_channel / m2_strategy），
            # 它们在不变式 I1 的豁免清单里（M0 §4.4）—— 即「无人可写」的静态数据，
            # 不需要也不应该经 Router。
            with session_scope(factory) as session:
                load_all(session)

        app.state.engine = engine
        app.state.session_factory = factory
        # `deps.get_session` 读它决定要不要在提交前跑不变式自检。
        # 放在 app.state 而不是让 deps 去 import config：依赖注入层不该知道配置从哪来。
        app.state.check_invariants = settings.check_invariants
        # 讨论端点（`/api/sessions/{id}/discuss`）用的客户端。
        # **测试通过替换这个属性注入确定性替身**，因此测试从不依赖本地模型在跑。
        app.state.llm_client = make_llm_client(settings)
        # 库里有一份**生效中**的配置时，它覆盖上面那个 ——
        # 那是用户在界面里填的，比 `.env` 更接近"我此刻想用哪个模型"。
        # 启动日志里会说明最终用的是哪一份（没有这一行的话，
        # "我配了但好像没生效"只能靠猜）。
        with session_scope(factory) as session:
            profile = active_profile(session)
            if profile is not None:
                app.state.llm_client = make_client_from_profile(
                    profile,
                    api_key=read_secret(profile.profile_id),
                    timeout=settings.llm_timeout,
                )
                app.state.active_llm_profile = profile.profile_id
        app.state.settings = settings
        # 一次讨论的步数预算。**配置说了它管这件事，就必须真的由它管** ——
        # 一个没人读的配置项比没有这个配置项更坏：它让"调过了"变成一句空话。
        app.state.agent_max_steps = settings.agent_max_steps
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="觅学之契 · 我的学习范式",
        description="个人学习流工作台 —— 治理内核切片（P3）",
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    api_errors.install(app)

    for module in (
        m1,
        preferences,
        evidence,
        proposals,
        router_logs,
        folders,
        sessions,
        attachments,
        sedimentation,
        discuss,
        llm,
    ):
        app.include_router(module.router)

    @app.get("/api/health", response_model=HealthRead, tags=["meta"])
    def health() -> HealthRead:
        """存活探针。刻意不返回任何全局状态摘要 —— 那会成为一个绕过 reads 的读路径。"""
        return HealthRead()

    return app


#: `uvicorn app.main:app --reload` 的入口。
#: 注意：这里**不触发建表与种子** —— 那些在 lifespan 里，只在服务器真正启动时发生。
app = create_app()
