"""能力令牌 —— 四重保障之②（M0 §4.2）。

为什么需要它：文档说「绕过路由层 = 非法」，但「非法」必须由**结构**保证，而不是靠自觉。
本模块让全局写函数在类型层面就需要一个拿不到的东西。

防护层次：
1. `RouterToken.__init__` 抛 `TypeError` —— 常规构造被堵死。
2. 只有 `issue_token()` 能签发，且把 nonce 登记进模块私有的 `_ISSUED_NONCES`。
3. `assert_capability()` 校验 nonce 是否在注册表中 —— 即使有人用
   `object.__new__(RouterToken)` 绕过构造器并手写 `_nonce`，也无法把自己登记进去。
4. 架构规则 ARCH-03（`tools/archcheck.py`）限制 `issue_token` 只能被
   `app.router.*` import，进一步收窄签发入口。

威胁模型说明：单进程 Python 无法对抗「直接 import 模块私有名」的蓄意攻击。
本设计的目标是让**无心之失与 LLM 生成的代码**无法绕过，并让蓄意绕过成为一个
显眼、可被 ARCH-03 捕获的动作 —— 这正是 M0 §4.1 所要求的强度。
"""

from __future__ import annotations

import secrets

from app.core.errors import CapabilityError

# 模块私有：架构上禁止被外部 import（ARCH-03 只放行 issue_token，不含本名）。
_ISSUED_NONCES: set[str] = set()


class RouterToken:
    """不可伪造的全局写入能力令牌。

    只能在 `app.router` 内经 `issue_token()` 获得。
    """

    __slots__ = ("_nonce",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "RouterToken 不可直接构造；只能由 app.router 经 issue_token() 签发（M0 §4.2②）"
        )

    def __repr__(self) -> str:  # 不泄漏 nonce 全文
        nonce = getattr(self, "_nonce", "")
        shown = nonce[:8] if nonce else "unissued"
        return f"<RouterToken {shown}…>"


def issue_token() -> RouterToken:
    """签发一枚有效令牌。

    ★ 架构规则 ARCH-03 规定：只有 `app.router.*` 可以 import 本函数。
    """
    token = object.__new__(RouterToken)
    token._nonce = secrets.token_hex(16)
    _ISSUED_NONCES.add(token._nonce)
    return token


def revoke_token(token: object) -> None:
    """撤销令牌（会话结束 / 测试清理）。撤销后 `assert_capability` 会拒绝。"""
    _ISSUED_NONCES.discard(getattr(token, "_nonce", ""))


def is_valid(token: object) -> bool:
    """令牌是否处于有效状态。"""
    return type(token) is RouterToken and getattr(token, "_nonce", None) in _ISSUED_NONCES


def assert_capability(token: object) -> None:
    """全局写函数的守卫。所有 `global_domain.writes` 的公开函数必须先调用它。

    **两层拒绝，语义不同**（M0 §12 判据 6）：

    - **压根不是令牌**（`None` / 字符串 / 数字）→ `TypeError`。
      这是编程错误，不是权限问题：调用方忘了传令牌，而不是用户越权。
      让它变成 500 是对的 —— 它不该由任何用户输入触发；
      若混进 403，一个「忘记传令牌」的 bug 会被读成「有人试图越权」。
    - **长得像令牌、但没被签发过**（`object.__new__` 伪造、子类伪造、已撤销）
      → `CapabilityError`（HTTP 403）。这是真正的越权尝试，要有明确且可映射的拒绝。

    两层都不可省：只做第二层，`None` 会以「无效令牌」的面目出现，
    掩盖了「调用点忘了传参」这一类最常见的错误。
    """
    if not isinstance(token, RouterToken):
        raise TypeError(
            f"全局写函数需要 RouterToken，收到 {type(token).__name__}；"
            "RouterToken 不可直接构造（M0 §4.2②）"
        )
    if not is_valid(token):
        raise CapabilityError(
            "全局写入被拒绝：缺少有效 RouterToken（M0 §4.2②）",
            detail={"token_type": type(token).__name__},
        )


def reset_registry_for_tests() -> None:
    """仅供测试：清空已签发注册表。"""
    _ISSUED_NONCES.clear()
