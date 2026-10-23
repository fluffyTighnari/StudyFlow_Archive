"""`/api/llm` —— 模型调用配置（**在界面里填，即时生效**）。

## 这一组端点为什么不属于"认知状态"

它管的是"用哪家的模型说话"，不是"用户是怎样的人"。所以它：

- **不经过 Router**，也不产生 `router_log`（它没有认知含义可追溯）；
- 不碰 M1/M2/C 四张认知表（有测试逐表比对行数，见
  `tests/test_llm_config_boundary.py`）；
- 在 `PROVENANCE` 里声明为 `infrastructure`，理由写在表旁边 ——
  **不是"反正没人写"式的默认豁免**。

## 密钥的处理（这是这一组端点最需要守的一件事）

- 请求体里**可以**带 `api_key`（界面里要能填）；
- 响应里**永远不带**。回的是 `has_key` 与掩码 `sk-d9…cdef`；
- 密钥落在 `var/secrets/<profile_id>.key`，不进数据库、不进日志、不进 `tool_trace`。
  有测试拿真密钥跑一遍全部端点，断言那句话不出现在任何响应里。

## 为什么 `POST /profiles/{id}/models` 是 POST 不是 GET

它会**用这把密钥去请求外部服务**。做成 GET 的话，任何一次"预取"或爬虫
都会顺手把你的密钥用掉一次额度；POST 表达的是"这是我主动发起的一次动作"。
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.llm import ChatMessage, LLMError, ModelLister
from app.agent.llm_factory import effective_context, make_client_from_profile
from app.agent.providers import PROVIDERS
from app.api.envelope import ItemsResponse, ok
from app.api.deps import get_session
from app.config import Settings
from app.llm_config import (
    LlmProfile,
    ProfileInput,
    activate,
    active_profile,
    create_profile,
    delete_profile,
    list_profiles,
    mask_key,
    read_secret,
    require_profile,
    update_profile,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])

#: 掩码示例用：`"sk-1234…cdef"`。**没有任何地方回真值。**
_SAMPLE_KEY = "sk-0000000000000000000000000000"


class ProviderRead(BaseModel):
    provider_id: str
    label: str
    kind: str
    base_url: str
    needs_key: bool
    supports_model_list: bool
    default_model: str
    key_hint: str
    known_models: list[str]


class ProfileRead(BaseModel):
    profile_id: str
    label: str
    provider_id: str
    kind: str
    base_url: str
    model: str
    #: 档案里勾选过的模型。**一定包含 `model`**（旧档案也会回一项）。
    models: list[str]
    #: 三态之一：`"auto"` / `"true"` / `"false"`。
    think: str
    num_ctx: int
    #: 实际使用的上下文预算（留空/旧默认时取模型窗口）。
    effective_num_ctx: int
    note: str
    is_active: bool
    #: 密钥**在不在**（不是它是什么）。
    has_key: bool
    #: 掩码，给界面显示"当前用的是哪把"。短到没法掩码时是 `已设置`。
    key_masked: str
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, profile: LlmProfile) -> ProfileRead:
        key = read_secret(profile.profile_id)
        return cls(
            profile_id=profile.profile_id,
            label=profile.label,
            provider_id=profile.provider_id,
            kind=profile.kind,
            base_url=profile.base_url,
            model=profile.model,
            models=profile.model_list,
            think=profile.think or "auto",
            num_ctx=profile.num_ctx,
            effective_num_ctx=effective_context(profile),
            note=profile.note,
            is_active=profile.is_active,
            has_key=bool(key),
            key_masked=mask_key(key),
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


class ProfileWrite(BaseModel):
    label: str = ""
    provider_id: str
    model: str = ""
    #: 勾选的模型清单。`null` = 不改（新档案则只用 `model`）；
    #: `[]` = 清空（仍会保留 `model`，档案不能没有可用的模型）。
    models: list[str] | None = None
    base_url: str | None = None
    #: `true`/`false`/`null`（null = 不发这个字段，见 `OllamaClient` 的说明）。
    think: bool | None = None
    num_ctx: int | None = None
    note: str = ""
    #: 三态：不给 = 不改动已存的；`""` = 清掉；有值 = 换一把。
    api_key: str | None = None


class ActivateWrite(BaseModel):
    """启用一份档案时**同时**指定用哪一个模型。

    `model` 可空：老客户端只发 `{}` 时行为不变（沿用档案当前 `model`）。
    给了就必须是这份档案里勾选过的 —— 见 `activate()` 的说明。
    """

    model: str | None = None


class ModelsRead(BaseModel):
    profile_id: str
    models: list[str]
    #: 拉不到时（服务不支持 / 没密钥 / 网络不通）的**人话解释**，此时 `models` 为空。
    #: 为什么用 200 而不是 4xx：这不是"客户端请求错了"，
    #: 而是"这家服务问不出清单" —— 界面要能照常显示"手动填模型名"。
    note: str = ""


class ProbeRead(BaseModel):
    ok: bool
    #: 一句话结论（成功时说清"这家有多少个模型、当前这个在不在"）。
    detail: str


class DeletedRead(BaseModel):
    """删除的结果。

    **为什么不回 204**：有一条断言是对的 ——"每个端点都要声明它返回什么"，
    而 204 什么都没声明。返回"删掉了谁、现在生效的是哪一份"比一个空响应更有用：
    界面据此就能重画"当前模型"那一栏，不必再补一次 GET。
    """

    profile_id: str
    #: 删除之后接上的那一份（删光了就是 `None`）。
    active_profile_id: str | None = None


@router.get("/providers", response_model=ItemsResponse[ProviderRead])
def list_providers() -> dict[str, list[ProviderRead]]:
    """内置公司目录。**界面据此渲染下拉框**，因此表里没有的服务也能用 `custom`。"""
    return ok([
        ProviderRead(
            provider_id=p.provider_id,
            label=p.label,
            kind=p.kind,
            base_url=p.base_url,
            needs_key=p.needs_key,
            supports_model_list=p.supports_model_list,
            default_model=p.default_model,
            key_hint=p.key_hint,
            known_models=list(p.known_models),
        )
        for p in PROVIDERS
    ])


@router.get("/config", response_model=dict[str, Any])
def current_config(session: Session = Depends(get_session)) -> dict[str, Any]:
    """此刻**实际**在用的是什么。

    这是"我配了但好像没生效"的唯一答案来源：它同时给出生效中的 profile
    （如果有）与 `.env` 那条路的兜底值，并说清哪一个是当前生效的。
    """
    settings = Settings.from_env()
    profile = active_profile(session)
    return {
        "source": "profile" if profile is not None else "settings",
        "profile": ProfileRead.of(profile).model_dump() if profile is not None else None,
        "settings": {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            # **只回"有没有"，不回值。**
            "has_api_key": bool(settings.llm_api_key),
            "llm_think": (
                "auto" if settings.llm_think is None else str(settings.llm_think).lower()
            ),
            "llm_num_ctx": settings.llm_num_ctx,
        },
        "env_file_read": _env_file_read(),
    }


def _env_file_read() -> bool:
    """`.env` 到底有没有被读到 —— 布尔值，不是路径。

    "文件在、却一个键都没读到"（编码/写法不对）是最让人困惑的一种失败，
    所以这个字段的意义是：`false` 时界面能提示"你的 `.env` 没被读到"。
    """
    from app.config import _ENV_FILE_KEYS_LOADED  # noqa: PLC0415

    return bool(_ENV_FILE_KEYS_LOADED)


@router.get("/profiles", response_model=ItemsResponse[ProfileRead])
def list_all(session: Session = Depends(get_session)) -> dict[str, list[ProfileRead]]:
    return ok([ProfileRead.of(p) for p in list_profiles(session)])


@router.post("/profiles", response_model=ProfileRead, status_code=201)
def create(
    body: ProfileWrite, session: Session = Depends(get_session)
) -> ProfileRead:
    with _readable_config_error():
        profile = create_profile(session, _to_input(body))
    return ProfileRead.of(profile)


@router.patch("/profiles/{profile_id}", response_model=ProfileRead)
def update(
    profile_id: str, body: ProfileWrite, session: Session = Depends(get_session)
) -> ProfileRead:
    with _readable_config_error():
        profile = update_profile(session, profile_id, _to_input(body))
    return ProfileRead.of(profile)


@router.delete("/profiles/{profile_id}", response_model=DeletedRead)
def remove(profile_id: str, session: Session = Depends(get_session)) -> DeletedRead:
    try:
        delete_profile(session, profile_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 删除后接上的那一份：`delete_profile` 已经把"删掉生效中的那份"这件事
    # 处理成"另一份自动接上"，所以这里读回来的就是结果。
    return DeletedRead(
        profile_id=profile_id,
        active_profile_id=getattr(active_profile(session), "profile_id", None),
    )


@router.post("/profiles/{profile_id}/activate", response_model=ProfileRead)
def activate_one(
    profile_id: str,
    body: ActivateWrite | None = None,
    session: Session = Depends(get_session),
) -> ProfileRead:
    try:
        profile = activate(session, profile_id, model=body.model if body else None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProfileRead.of(profile)


@router.post("/profiles/{profile_id}/models", response_model=ModelsRead)
def list_models(
    profile_id: str, session: Session = Depends(get_session)
) -> ModelsRead:
    """用这份配置去问它有哪些模型。**拉不到也算成功**（见 `ModelsRead.note`）。"""
    try:
        profile = require_profile(session, profile_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    client = make_client_from_profile(
        profile, api_key=read_secret(profile.profile_id), timeout=30.0
    )
    if not isinstance(client, ModelLister):  # pragma: no cover - 两个真实客户端都实现了
        return ModelsRead(
            profile_id=profile_id,
            models=[],
            note="这个客户端不支持列模型，请手动填模型名。",
        )
    try:
        return ModelsRead(profile_id=profile_id, models=client.list_models())
    except LLMError as exc:
        return ModelsRead(profile_id=profile_id, models=[], note=str(exc))


@router.post("/profiles/{profile_id}/probe", response_model=ProbeRead)
def probe(profile_id: str, session: Session = Depends(get_session)) -> ProbeRead:
    """真的去问一次 —— **清单不等于"能不能用"**。

    ## 这条端点是实测改出来的

    原写法：拉 `/models`，当前模型名不在清单里就回一句
    "**不在**（模型名可能写错了）"。

    第一次真跑商用 API（`tools/livedeepseek.py`）就把这个结论推翻了：
    DeepSeek 的 `/v1/models` 只列 `deepseek-flash` 与 `deepseek-v4-pro`，
    而 `deepseek-chat` **不在清单里却完全能用**（那次讨论用它跑通了整个闭环）。
    于是那句"可能写错了"是在**指控用户拼错**，而真相是"清单里只有新名字，
    旧名字作为别名继续服务"。

    这正是本项目反复出现的那类缺陷：*系统用一个理由解释自己的行为，
    而那个理由不是真的。* 所以现在"不在清单里"时**去发一句话**，
    按真实结果说话。

    代价是这一下可能花掉一次调用的额度 —— 可以接受：这个按钮存在的意义
    就是"存下来之前先试试"，而它给出的答案必须是真的。
    （本地 27B 首次调用要加载进显存，会慢；超时会有话说。）
    """
    try:
        profile = require_profile(session, profile_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    client = make_client_from_profile(
        profile, api_key=read_secret(profile.profile_id), timeout=30.0
    )
    try:
        models = client.list_models()
    except LLMError as exc:
        return ProbeRead(ok=False, detail=str(exc))

    if profile.model in models:
        return ProbeRead(ok=True, detail="可用")

    # 不在清单里 —— **别下结论，去问一次**。
    try:
        client.chat(
            [ChatMessage.system("只回一个字。"), ChatMessage.user("好")], tools=()
        )
    except LLMError as exc:
        return ProbeRead(ok=False, detail=f"不可用：{str(exc)[:120]}")

    return ProbeRead(ok=True, detail="可用")


@contextmanager
def _readable_config_error() -> Iterator[None]:
    """把配置层的 `ValueError` 与 `LookupError` 翻成**能读的** HTTP 错误。

    不翻的话它们是 500：界面上看到"服务器内部错误"，而真正的原因
    （"不认识的服务商：'deepsek'"、"base_url 必须以 http 开头"）
    一个字都没到用户眼前 —— 那正是最需要它的地方。

    `LookupError` → 404（这份配置不存在）；其余 → 422（你给的字段有问题）。
    """
    try:
        yield
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _to_input(body: ProfileWrite) -> ProfileInput:
    return ProfileInput(
        label=body.label,
        provider_id=body.provider_id,
        model=body.model,
        models=body.models,
        base_url=body.base_url,
        think=body.think,
        num_ctx=body.num_ctx,
        note=body.note,
        api_key=body.api_key,
    )
