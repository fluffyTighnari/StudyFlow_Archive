"""全局状态的**地址空间**（A9 命名空间归一化的唯一落点）。

## 为什么这个模块在全局域，而不在路由层

M0 §11 A9 裁定：存储主键用连字符（`V-SE`），路由 `target` 用点号（`M1.V-SE`），
两者之间的转换**只允许有一处实现**。

P5 之前这处实现在 `app/router/registry.py` 里。P5 把它下沉到这里，原因是
**不变式自检需要它，而自检不能依赖路由层**：

- M0 §4.4 把自检放在 `app/core/`，但 ARCH-04 禁止 `app.core` 依赖 `app.global_domain`
  —— 自检要读全局表，所以它只能落在**全局域**；
- 而 ARCH-05 又禁止 `app.global_domain` 反向依赖 `app.router`
  —— 所以自检不能去 import `registry.py`。

于是 `M1.` 这个前缀不能只长在路由层。这不是把常量挪个位置就完事：
**「全局状态怎么被寻址」本来就是全局域的事实，而「哪些 action 允许打在哪个 target 类上」
才是路由层的策略**。原先把两者放在同一个文件里，只是因为在 P2 只有路由层需要它。

`registry.py` 现在从这里 import 并转出（`from app.global_domain.targets import ...`），
因此 `app.router.registry.target_for_variable` 这类既有引用继续可用，
A9 的「只有一处实现」依旧成立 —— 只是那一处换了地址。
"""

from __future__ import annotations

from enum import StrEnum

#: `M1.<variable_id>` 的前缀。**注意结尾的点**：`M1` 单独出现不是合法 target，
#: 而 `M1.` 前缀检查天然拒绝了 `"M1"` 与 `"M1x"` 这两种写错。
M1_TARGET_PREFIX = "M1."
#: M2 偏好的单一 target 根（偏好是一个集合，不是逐个字段的 target）。
M2_TARGET_ROOT = "M2.preferences"
#: C 证据的单一 target 根。
C_TARGET_ROOT = "C.evidence"

#: 三个全局命名空间的根，按长度降序（匹配时先长后短，避免前缀互相吃掉）。
TARGET_ROOTS: tuple[str, ...] = (M2_TARGET_ROOT, C_TARGET_ROOT, M1_TARGET_PREFIX)


class TargetNamespace(StrEnum):
    """一个 target 字符串属于哪个命名空间。"""

    M1 = "m1"
    M2 = "m2"
    C = "c"
    #: 不属于任何已注册命名空间 —— 调用方必须拒绝。
    UNKNOWN = "unknown"


def target_for_variable(variable_id: str) -> str:
    """存储主键 `V-SE` → 路由 target `M1.V-SE`。"""
    return f"{M1_TARGET_PREFIX}{variable_id}"


def variable_id_from_target(target: str) -> str:
    """路由 target `M1.V-SE` → 存储主键 `V-SE`。"""
    if not target.startswith(M1_TARGET_PREFIX):
        raise ValueError(f"不是 M1 target：{target!r}")
    return target[len(M1_TARGET_PREFIX) :]


def classify_target(target: str) -> TargetNamespace:
    """判定 target 属于哪个命名空间。**只做语法判定，不查任何表。**

    这是自检 I2 需要的那个问题：「这条日志的 target 是不是一个我们认识的地址」。
    至于「这个地址背后有没有东西」（例如 `V-NOPE` 这个变量根本不存在），
    是 I2 的另一半，需要查 `m1_variable`。
    """
    if target == M2_TARGET_ROOT or target.startswith(M2_TARGET_ROOT + "."):
        return TargetNamespace.M2
    if target == C_TARGET_ROOT or target.startswith(C_TARGET_ROOT + "."):
        return TargetNamespace.C
    if target.startswith(M1_TARGET_PREFIX) and len(target) > len(M1_TARGET_PREFIX):
        return TargetNamespace.M1
    return TargetNamespace.UNKNOWN
