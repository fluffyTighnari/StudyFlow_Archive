"""统一异常体系 —— 领域异常到 HTTP 状态的唯一映射点（M0 §9.1）。

所有异常都携带稳定 `code`，前端与 RouterLog 依赖 code 而非 message 判定分支。
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """领域异常基类。"""

    code: str = "domain_error"
    http_status: int = 400

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class CapabilityError(DomainError):
    """令牌缺失 / 伪造 / 已撤销 —— 全局写入被结构性拒绝（M0 §4.2②）。"""

    code = "capability_denied"
    http_status = 403


class ValidationRejected(DomainError):
    """UpdateIntent 校验失败：target 未注册、action 非法、值域越界、缺 trigger_type。"""

    code = "intent_rejected"
    http_status = 422


class VersionConflict(DomainError):
    """乐观锁版本冲突：侧页与 Agent 同时写（M0 §6.2）。"""

    code = "version_conflict"
    http_status = 409


class StateNotWritable(DomainError):
    """会话状态下不允许这次写（P4，用户裁定「状态机要真的拒绝」）。

    **HTTP 状态也是 409，但 `code` 与 `VersionConflict` 不同。**
    两者都是「当前资源状态不允许这次操作」，共享状态码是对的；
    而调用方靠 `code` 分支（见本模块文档第一句），因此不会被混淆。
    """

    code = "state_not_writable"
    http_status = 409


class MissingPrerequisite(DomainError):
    """前置条件不满足，且**不是**状态问题 —— 例如「目标为空就激活会话」。

    与 `StateNotWritable` 分开，是因为两者的补救动作不同：
    前者要你补内容，后者要你换个状态再做。
    """

    code = "missing_prerequisite"
    http_status = 422


class InvalidField(DomainError):
    """请求里出现了这个对象不接受的字段 / 值。

    刻意**不用** `ValidationRejected`（它的 code 是 `intent_rejected`，
    语义是"意图被策略拒绝"）：一个 422 里混着两种完全不同的失败，
    前端就只能靠 message 文本猜，而 message 是会改的。
    """

    code = "invalid_field"
    http_status = 422


class NotFound(DomainError):
    code = "not_found"
    http_status = 404


class AttachmentRejected(DomainError):
    """上传的文件不能被接受（P6+.5）：类型不对、读不出编码、伪装的文件、太大。

    **单独一类，不复用 `InvalidField`/`ValidationRejected`。** 理由和
    `InvalidField` 不服 `ValidationRejected` 是同一条：前端对它的处置完全不同 ——
    文件被拒时要显示"换个文件"的引导（`message` 里已经写了下一步做什么），
    而不是把字段名标红。混进 422 的通用分支，用户只会看到一句"请求无效"。

    422 而不是 400：请求本身是合法的（一个 POST、一段字节），
    是**内容**没通过校验。
    """

    code = "attachment_rejected"
    http_status = 422


# 注：这里曾有一个 `QuotaExceeded`(409)。M0 §5.4 已由用户裁定废除 R2 重做配额，
# 因此该类被**删除**而不是保留备用 —— 一个名叫「配额超限」的异常留在代码里，
# 早晚会有人拿它去实现一个「拦一下」的分支，而「建议永不阻断」的承诺
# 就会在某次不起眼的改动里悄悄失效。删除是唯一可靠的做法。


class InvariantViolation(DomainError):
    """不变式自检失败（M0 §4.4）—— 意味着存在幽灵写入或数据被绕过路由手工改动。"""

    code = "invariant_violation"
    http_status = 500
