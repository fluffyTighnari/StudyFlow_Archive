"""测试侧的小工具：**把列表端点的信封拆开**。

## 为什么测试里要有一个 `items()`

接口层的形状由 `{"items": [...]}` 统一（见 `app/api/envelope.py`）。
但 90% 的测试关心的是**列表里的内容**，不是信封本身 ——
每个断言都写 `.json()["items"]` 会让真正的断言淹没在样板里。

所以：**形状本身只在一处被断言**（`tests/api/test_api_boundaries.py` 的
OpenAPI 检查 + `tools/e2echeck` 的 `step_response_shape_consistency`），
其余地方用这个函数拆开。

★ 它是**严格**的：拿到裸列表会直接失败，而不是"两种都接受"。
半迁移状态必须响亮地报错，否则"统一形状"这件事永远做不完。
"""

from __future__ import annotations

from typing import Any


def items(payload: Any) -> list[Any]:
    """`{"items": [...]}` → `[...]`。

    传入裸列表（旧的裸数组形状）→ **断言失败**，并说清为什么。
    """
    assert isinstance(payload, dict), (
        f"列表端点应该返回 {{'items': [...]}}，实际是 {type(payload).__name__}："
        f"{str(payload)[:200]}"
    )
    assert isinstance(payload.get("items"), list), (
        f"列表端点的数组必须在 items 下，实际字段 {sorted(payload)}"
    )
    return payload["items"]


__all__ = ["items"]
