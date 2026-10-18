"""LLM 调用配置：**哪一家、哪个模型、用哪把钥匙**。

## 这张表为什么不是"认知状态"

`llm_profile` 里没有一样东西影响"我是谁、我擅长什么"：它不参与上下文采用、
不被 Router 裁定、不进任何会话的决策依据。改它不会让任何一条基线或偏好发生变化。
所以它在 `PROVENANCE` 里声明为 `infrastructure`（与 `id_sequence` 同口径），
**而不是**当作一条可以绕开 Router 的全局认知写入。

有一条测试盯着这件事（`tests/test_llm_config_boundary.py`）：
写完配置以后，`router_log` 行数不变、认知四表行数不变、I1–I7 仍然通过。

## 密钥为什么不进数据库（用户选了"配置存库、界面里填"）

用户裁定的是"配置可写、能存库、界面里填"，**不是"密钥必须躺在数据库里"**。
所以这里把两件事分开：

- **配置**（公司 / base_url / 模型 / 思考开关 / 上下文窗口）→ 数据库，可以随便备份导出；
- **密钥** → `var/secrets/<profile_id>.key` 一个文件，权限收紧、不进 git
  （`.gitignore` 已挡）、不进任何 API 响应、不进 `tool_trace`、不进日志。

理由很实在：数据库是最容易被整体复制的东西（拷 .db 文件、备份、`git add` 时漏挡），
而密钥是唯一"泄漏即损失"的字段。分开之后，"我把库发给别人看一眼"不再是事故。

## 界面体验没有变差

"界面里填"照旧：`POST /api/llm/profiles` 带 `api_key` 字段，服务端写进那个文件，
之后**只回 `has_key: true` 与掩码**（`sk-…abcd`）。读取只在造客户端的那一刻发生。
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
from dataclasses import dataclass

from sqlalchemy import Boolean, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.agent.providers import Provider, get_provider
from app.core.ids import format_seq
from app.db import Base, DbSequenceStore

#: 密钥计数器 scope 与可读前缀：`LP-0001`。
SCOPE_LLM_PROFILE = "llm_profile"


class LlmProfile(Base):
    """一份可切换的模型调用配置。**不含密钥。**"""

    __tablename__ = "llm_profile"

    profile_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: 用户自己起的名字（"DeepSeek 主力" / "本地 27B"）。
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `openai` 或 `ollama` —— 决定用哪个客户端、以及怎么拉模型列表。
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str] = mapped_column(String(400), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 思考开关，三态：空串 = 不发这个字段（等价于 `None`）。
    #:
    #: 用字符串而不是布尔列，是因为要区分"没设"与"设成 false"——
    #: 而这两个在 SQL 里用 `NULL` 与 `0` 也能表达，但那需要每个读的地方
    #: 都记得 `is None` 而不是 `if not`。一个 `''`/`'true'`/`'false'` 的三态
    #: 在库里肉眼可读，也让导出的 JSON 不带歧义。
    think: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    #: 上下文窗口（Ollama 用；OpenAI 兼容服务不需要，留 0 = 不设）。
    num_ctx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 是否是当前生效的那一份。**全表至多一行为真**，由 `activate()` 维护。
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 一句备注（比如"便宜的那个，用来跑长对话"）。
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    @property
    def has_key(self) -> bool:
        return secret_path(self.profile_id).is_file()

    @property
    def think_flag(self) -> bool | None:
        if self.think == "true":
            return True
        if self.think == "false":
            return False
        return None


# --------------------------------------------------------------------------- #
# 密钥文件仓
# --------------------------------------------------------------------------- #


def secrets_dir() -> pathlib.Path:
    """`backend/var/secrets/`。可用 `MOUXUE_SECRETS_DIR` 覆盖（测试用临时目录）。"""
    override = os.environ.get("MOUXUE_SECRETS_DIR")
    if override:
        path = pathlib.Path(override)
    else:
        path = pathlib.Path(__file__).resolve().parent.parent / "var" / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def secret_path(profile_id: str) -> pathlib.Path:
    # 只允许 `LP-0001` 这类 id 参与拼路径：`../` 之类的 id 会写到目录外，
    # 而那种写法只可能来自被篡改的输入。
    if not profile_id.replace("-", "").isalnum():
        raise ValueError(f"不合法的 profile_id：{profile_id!r}")
    return secrets_dir() / f"{profile_id}.key"


def write_secret(profile_id: str, api_key: str) -> None:
    path = secret_path(profile_id)
    path.write_text(api_key, encoding="utf-8")
    _restrict(path)


def _restrict(path: pathlib.Path) -> None:
    """尽力收紧权限。

    **尽力**是诚实的说法：Windows 上 `chmod` 基本不起作用（POSIX 位被忽略），
    真正管用的是目录 ACL —— 而改 ACL 需要一个我们不想引入的依赖。
    所以这里做两件确实有用的事：去掉"其他人可写"的位（POSIX 上有效），
    并把文件放在一个已被 `.gitignore` 挡住的目录里（两个平台都有效）。
    """
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - 平台差异
        pass


def read_secret(profile_id: str) -> str:
    path = secret_path(profile_id)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def delete_secret(profile_id: str) -> None:
    secret_path(profile_id).unlink(missing_ok=True)


def mask_key(api_key: str) -> str:
    """`sk-d96672a3…` → `sk-d9…cdef`。**只用于回显，不用于任何判断。**

    短到没法掩码时返回 `已设置` —— 硬掩码一个 6 字符的串会泄漏它的大部分。
    """
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "已设置"
    return f"{api_key[:4]}…{api_key[-4:]}"


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProfileInput:
    """创建/更新一份配置的入参。`api_key=None` = 不改动已存的密钥。"""

    label: str
    provider_id: str
    model: str
    base_url: str | None = None
    think: bool | None = None
    num_ctx: int | None = None
    note: str = ""
    api_key: str | None = None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def resolved_base_url(provider: Provider, base_url: str | None) -> str:
    """地址取用户填的，没填就用这家的默认值。空地址**拒绝**。

    允许空地址的后果：请求发到 `"/chat/completions"` 这种相对路径上，
    报错是"URL 里没有 host" —— 一句话都不提"你没填地址"。
    """
    url = (base_url or provider.base_url).strip().rstrip("/")
    if not url:
        raise ValueError(
            f"{provider.label} 没有默认地址，必须自己填 base_url"
            "（任何 OpenAI 兼容服务，含 `/v1`）。"
        )
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"base_url 必须以 http:// 或 https:// 开头，收到 {url!r}")
    return url


def _think_column(think: bool | None) -> str:
    if think is None:
        return ""
    return "true" if think else "false"


def list_profiles(session: Session) -> list[LlmProfile]:
    return list(
        session.scalars(select(LlmProfile).order_by(LlmProfile.profile_id)).all()
    )


def get_profile(session: Session, profile_id: str) -> LlmProfile | None:
    return session.get(LlmProfile, profile_id)


def require_profile(session: Session, profile_id: str) -> LlmProfile:
    profile = get_profile(session, profile_id)
    if profile is None:
        raise LookupError(f"没有这份配置：{profile_id}")
    return profile


def active_profile(session: Session) -> LlmProfile | None:
    return session.scalars(
        select(LlmProfile).where(LlmProfile.is_active.is_(True)).limit(1)
    ).first()


def create_profile(session: Session, data: ProfileInput) -> LlmProfile:
    provider = get_provider(data.provider_id)
    profile_id = format_seq("LP", DbSequenceStore(session).next(SCOPE_LLM_PROFILE), 4)
    now = _now()
    profile = LlmProfile(
        profile_id=profile_id,
        label=data.label.strip() or provider.label,
        provider_id=provider.provider_id,
        kind=provider.kind,
        base_url=resolved_base_url(provider, data.base_url),
        model=data.model.strip() or provider.default_model,
        think=_think_column(data.think),
        num_ctx=data.num_ctx or 0,
        note=data.note,
        is_active=False,
        created_at=now,
        updated_at=now,
    )
    if not profile.model:
        raise ValueError("没给模型名，而这家也没有默认值 —— 请填一个模型名。")
    session.add(profile)
    session.flush()
    if data.api_key:
        write_secret(profile_id, data.api_key)
    # 第一份配置自动生效：否则用户填完了却发现"没在用"，而界面上没有任何提示。
    if active_profile(session) is None:
        profile.is_active = True
    session.flush()
    return profile


def update_profile(
    session: Session, profile_id: str, data: ProfileInput
) -> LlmProfile:
    profile = require_profile(session, profile_id)
    provider = get_provider(data.provider_id)
    profile.label = data.label.strip() or profile.label
    profile.provider_id = provider.provider_id
    profile.kind = provider.kind
    profile.base_url = resolved_base_url(provider, data.base_url)
    profile.model = data.model.strip() or provider.default_model
    profile.think = _think_column(data.think)
    profile.num_ctx = data.num_ctx or 0
    profile.note = data.note
    profile.updated_at = _now()
    # `api_key` 三态：None = 不动、"" = 清掉、其它 = 换一把。
    if data.api_key is not None:
        if data.api_key:
            write_secret(profile_id, data.api_key)
        else:
            delete_secret(profile_id)
    session.flush()
    return profile


def delete_profile(session: Session, profile_id: str) -> None:
    profile = require_profile(session, profile_id)
    was_active = profile.is_active
    session.delete(profile)
    session.flush()
    delete_secret(profile_id)
    if was_active:
        # 删掉生效中的那份之后**必须**有另一份接上，否则下一句话会打到一个空配置上。
        replacement = session.scalars(
            select(LlmProfile).order_by(LlmProfile.profile_id).limit(1)
        ).first()
        if replacement is not None:
            replacement.is_active = True
            session.flush()


def activate(session: Session, profile_id: str) -> LlmProfile:
    profile = require_profile(session, profile_id)
    for other in list_profiles(session):
        other.is_active = other.profile_id == profile.profile_id
    session.flush()
    return profile
