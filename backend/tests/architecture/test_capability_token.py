"""能力令牌测试 —— 四重保障之②（M0 §4.2）。

核心命题：**令牌是不可伪造的**。任何未经 Router 签发的对象都必须被拒绝，
包括用 `object.__new__` 绕过构造器的伪造尝试。
"""

from __future__ import annotations

import pytest

from app.core.capability import (
    RouterToken,
    assert_capability,
    is_valid,
    revoke_token,
)
from app.core.errors import CapabilityError


def test_direct_construction_is_blocked() -> None:
    with pytest.raises(TypeError, match="不可直接构造"):
        RouterToken()


def test_construction_with_any_arguments_is_blocked() -> None:
    with pytest.raises(TypeError):
        RouterToken("pretend-token")
    with pytest.raises(TypeError):
        RouterToken(nonce="deadbeef")


def test_issued_token_is_valid(token: RouterToken) -> None:
    assert is_valid(token)
    assert_capability(token)  # 不抛异常即通过


def test_object_new_forgery_is_rejected() -> None:
    """绕过构造器、手写 _nonce —— 仍无法把自己登记进签发注册表。"""
    forged = object.__new__(RouterToken)
    forged._nonce = "deadbeef" * 4

    assert not is_valid(forged)
    with pytest.raises(CapabilityError):
        assert_capability(forged)


def test_subclass_forgery_is_rejected() -> None:
    class EvilToken(RouterToken):
        __slots__ = ()

    forged = object.__new__(EvilToken)
    forged._nonce = "whatever"

    assert not is_valid(forged)
    with pytest.raises(CapabilityError):
        assert_capability(forged)


@pytest.mark.parametrize("bad", [None, "token", 123, object(), object])
def test_non_token_objects_are_a_type_error(bad: object) -> None:
    """**压根不是令牌** → `TypeError`，而不是 `CapabilityError`（M0 §12 判据 6）。

    两者必须分开：`None` 说明调用点忘了传令牌（编程错误 → 500），
    而 `CapabilityError` 说明有人拿了个「像令牌的东西」来试（越权 → 403）。
    混成一种，一个「忘了传参」的 bug 会被读成「有人试图越权」，
    而排查方向会完全跑偏。
    """
    assert not is_valid(bad)
    with pytest.raises(TypeError):
        assert_capability(bad)


def test_token_shaped_but_unissued_is_a_capability_error() -> None:
    """反过来：`isinstance` 通过、但 nonce 没登记过 → `CapabilityError`（403）。"""
    from app.core.errors import CapabilityError

    forged = object.__new__(RouterToken)
    forged._nonce = "0" * 32
    with pytest.raises(CapabilityError):
        assert_capability(forged)


def test_revoked_token_is_rejected(token: RouterToken) -> None:
    assert is_valid(token)
    revoke_token(token)
    assert not is_valid(token)
    with pytest.raises(CapabilityError):
        assert_capability(token)


def test_repr_does_not_leak_the_full_nonce(token: RouterToken) -> None:
    text = repr(token)
    assert "RouterToken" in text
    assert len(text) < 40


def test_issued_tokens_are_distinct() -> None:
    from app.router.capability import new_token

    a, b = new_token(), new_token()
    assert a is not b
    assert is_valid(a) and is_valid(b)
