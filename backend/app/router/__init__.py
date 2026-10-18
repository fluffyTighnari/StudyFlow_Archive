"""治理层 Router —— **全局策略的唯一写入入口**（更新路由层 v0.4 / M0 §7）。

对外只暴露 `Router` 与契约类型。以下模块**对外不可见**：

- `capability.py`  令牌签发封装（ARCH-03：唯一可 import `issue_token` 的模块）
- `policies/`      更新策略实现（ARCH-02：只对 Router 内部可见）
- `precheck.py`    §5.3 判定表
- `conflict.py`    写入裁定序（只报告不裁定）

`Router.precheck` 是唯一允许外部窥视策略表内容的入口，且它返回的 `PrecheckResult`
里**没有**任何可写句柄 —— 看得见，改不着。
"""

from app.router.contracts import (
    BenchmarkAdvisory,
    ConflictReport,
    Decision,
    IntentResult,
    PrecheckResult,
    RollbackRequest,
    RollbackResult,
    SubmitRequest,
    SubmitResult,
    UpdateIntent,
)
from app.router.service import Router

__all__ = [
    "Router",
    "UpdateIntent",
    "SubmitRequest",
    "SubmitResult",
    "Decision",
    "IntentResult",
    "PrecheckResult",
    "ConflictReport",
    "BenchmarkAdvisory",
    "RollbackRequest",
    "RollbackResult",
]
