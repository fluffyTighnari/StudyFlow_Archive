"""§5.3 判定表 —— 一个**确定性函数**，而不是一段政策散文。

设计要点：判定表以数据形式逐行列在这里，`evaluate` 只是「先查全局否决、再查表」。
**没有 if-elif 链**，因为 if-elif 链的覆盖情况无法被穷举验证，而表可以
（`tests/router/test_precheck_table.py` 会逐行断言）。

三类结论被刻意分成三个互不覆盖的字段（见 `contracts.PrecheckResult`）：

- `verdict`   —— 判定表的结论；
- `blockers`  —— 与判定正交的硬前置条件（M0 §7.3 的「≥2 条独立 Session 证据」）；
- `advisories`—— §5.4 的重做建议，**永不阻断**。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.global_domain.enums import (
    INTENT_TRIGGERS,
    Basis,
    IntentAction,
    Precheck,
    TriggerType,
)
from app.global_domain.reads import count_independent_session_evidence
from app.router.contracts import BenchmarkAdvisory, PrecheckResult, UpdateIntent
from app.router.policies.benchmark_advisory import evaluate_benchmark_advisory
from app.router.registry import (
    ResolvedTarget,
    TargetClass,
    action_allowed,
    resolve_target,
    value_schema_for,
)

# --------------------------------------------------------------------------- #
# 全局否决（先于判定表）
# --------------------------------------------------------------------------- #

REASON_UNREGISTERED_TARGET = "target 未在 UpdatePolicyRegistry 注册（M0 §5.3 / §7.3）"
REASON_ACTION_NOT_ALLOWED = "该 target 不允许此 action（M0 §7.3）"
REASON_INFERRED_PARAMETER = (
    "红线 3：`inferred` 永不写参数 —— 推断不是证据（M0 §5.3 / §13.3）"
)
REASON_UNREGISTERED_COMBINATION = (
    "该 (trigger, action, target 类, basis) 组合未在 §5.3 判定表中注册"
)


# --------------------------------------------------------------------------- #
# 判定表本体
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PrecheckRule:
    """判定表的一行，与 M0 §5.3 的表格行一一对应。"""

    row_id: str
    triggers: frozenset[TriggerType]
    actions: frozenset[IntentAction]
    target_classes: frozenset[TargetClass]
    bases: frozenset[Basis]
    verdict: Precheck
    reason: str

    def matches(
        self,
        trigger: TriggerType,
        action: IntentAction,
        target_class: TargetClass,
        basis: Basis,
    ) -> bool:
        return (
            trigger in self.triggers
            and action in self.actions
            and target_class in self.target_classes
            and basis in self.bases
        )


# --------------------------------------------------------------------------- #
# ★ 「偏好类」这个合并类在 P7 被拆掉了
#
# 拆之前是 `_PREF_TARGETS = {M2_PREFERENCE, C_EVIDENCE}`，一行管两件性质不同的
# 东西。后果不是抽象的不整齐，而是**具体地放行了两格语义不通的组合**：
#
#   `C.evidence` + `stated`    → 自动生效。一条"他只是说了"的「证据」
#   `C.evidence` + `inferred`  → 需确认。一条"我推断出来的"「证据」
#
# 而这两样会被后续装配当成**已验证的方法**（M2 §2.2 SP2）。也就是说：
# 一句从未发生过的做法可以自动进库，然后在规划时被当成经验使用。
#
# 拆成两个集合之后，语义变成一张 2×2 的表（详见 M0 §A22）：
#
#   ┌────────────┬────────────────┬──────────────────────┐
#   │            │ stated         │ experienced          │
#   ├────────────┼────────────────┼──────────────────────┤
#   │ 偏好(无条件)│ M2.preferences │ M2.preferences       │
#   │ 做法(带条件)│ **C.hypothesis**│ **C.evidence**       │
#   └────────────┴────────────────┴──────────────────────┘
#
# 一格都不重叠，而"档位"与"种类"**都由 basis + target 决定** ——
# 模型只需要填 basis，不需要它再判断一次"这是方法还是偏好"。
# --------------------------------------------------------------------------- #

#: `stated` 能落到哪儿：偏好（无条件倾向）与假说（想试的做法）。
#: ★ `C_EVIDENCE` **不在这里** —— 那是本轮关掉的第一格。
_STATED_TARGETS = frozenset({TargetClass.M2_PREFERENCE, TargetClass.HYPOTHESIS})

#: `experienced` 能落到哪儿：偏好（观察到的倾向）与证据（已验证的做法）。
#: ★ `HYPOTHESIS` **不在这里** —— 试过了就不再是假说。
_EXPERIENCED_TARGETS = frozenset({TargetClass.M2_PREFERENCE, TargetClass.C_EVIDENCE})

#: `inferred` 只落到偏好。推断既不构成证据（红线 3 的同一条理由），
#: 也不构成假说 —— 假说的定义是**他自己说想试**，而"我推断你想试"不是他说的。
_INFERRED_TARGETS = frozenset({TargetClass.M2_PREFERENCE})

_M1_TARGETS = frozenset({TargetClass.M1_PREFERENCE, TargetClass.M1_TRAIT})
_R4_ANY = frozenset({TriggerType.R4A, TriggerType.R4B, TriggerType.R4C})

#: 能**退场 / 恢复 / 改写**的 target 类 —— 只有 C 家族（证据与假说）。
#:
#: ## 为什么偏好不在这里
#:
#: 偏好早就有自己的退场机制：`supersedes_pref_id`（A20 裁定，P6 实现）——
#: 写一条新的、并指着旧的说"它被取代了"。用户裁定 A20 时选的正是"替换"而不是
#: "编辑"，因为**偏好的一句话常常整体被另一句话替代**（"我讨厌网课" →
#: "精品网课可以，如果讲得好"），改写会留下一个说不清哪句算数的中间态。
#:
#: 所以这里不是漏了偏好，是偏好走另一条更早定下的路。
#:
#: ## 为什么 M1 不在这里
#:
#: M1 是**测量结果**，不是他的说法。测量结果不能删 —— 只能重测（R2）
#: 或用 `rollback()` 回到上一版（R5）。"用户亲手改掉一个测量读数"会让
#: 分数不再有出处，而那是整份基线唯一的价值。
_ARCHIVABLE_TARGETS = frozenset({TargetClass.C_EVIDENCE, TargetClass.HYPOTHESIS})


# --------------------------------------------------------------------------- #
# ★ 「直接生效」的唯一判据（用户于 P5.2 裁定，方案 A）
# --------------------------------------------------------------------------- #

#: 一条 intent 可以直接生效 ⟺ **用户在那个手势里已经自己给出或确认了这个值**。
#:
#: 这是 R4 判定表里那一列「是否直接生效」的**唯一**依据，取代原先那句
#: 「以 M7 §7.2 为准」—— 那是一句没有理由的理由，而它掩盖了 M7 §7.2 第 170 行
#: 的自相矛盾（前半句「沉淀必确认」，后半句立刻给 R4c 开了一个没有理由的例外）。
#:
#: **另一个候选依据是「证据强度」（§5 的 R4 表：R4a 强 / R4b 中 / R4c 弱），
#: 用户在 P5.2 明确否决了它** —— 它方向相反（最弱的 R4c 反而最宽松），
#: 而且那张表自己声明「证据强度只影响 LLM 的 confidence 标注，不改变路由动作」。
#:
#: 注意这个判据有一个**边界**：确认动作只覆盖**当时摆在用户面前的东西**。
#: - R2 的按钮说的是「确认写入基线」，所以它覆盖 `measured`，**不覆盖**
#:   顺手记的一条 `experienced` 体感（`R2 + experienced` 至今未注册，见缺口表）；
#: - R4c 的列表是 **stage 里的本次发现**（即 `experienced`），所以他勾选的确认
#:   **不覆盖** `inferred` —— LLM 的推断不在 stage 里，用户没看见它。
#:   于是 `R4c + inferred` 仍是「需确认」（与 M0 §11 A11 一致）。
#:
#: `tests/router/test_precheck_table.py` 会把这张判定表**逐行**与这个集合对齐：
#: 新增一行而没在这里表态，测试就会红。
USER_CONFIRMED_GESTURES: frozenset[tuple[TriggerType, IntentAction, Basis]] = frozenset(
    {
        # 用户在 UI 偏好栏里自己写下这句话。
        (TriggerType.R1, IntentAction.ADD_PREFERENCE, Basis.STATED),
        # ★ P7 新增：用户在界面里写下「我试过这个，有效」（`/api/evidence`）。
        #
        # 这一格在 P7 之前**不存在**，而 `/api/evidence` 一直走得通 ——
        # 靠的是它把 `experienced` 谎报成 `stated`。拆格之后那条谎报走不通了
        # （`C.evidence` + `stated` 现在是拒绝），于是这条真实存在的手势
        # 必须有自己的名字。**这不是放松判据，是让判据第一次说出真相。**
        (TriggerType.R1, IntentAction.ADD_PREFERENCE, Basis.EXPERIENCED),
        # 用户在 Benchmark 末端点「确认写入基线」——那次测绘的结果是他自己确认的。
        (TriggerType.R2, IntentAction.UPDATE_PARAMETER, Basis.MEASURED),
        # 用户在对话框里明确说出自己的偏好（不是「我观察到…」）。
        #
        # ★ P7：这一格现在只对「偏好」和「假说」生效，**不再对证据生效**。
        # 他说"我想试试 X"是一条假说（`C.hypothesis`）；要说它是证据，
        # 他得**试过**，而"试过"不可能靠一句话得到（`C.evidence` + `stated`
        # 现在是拒绝，见 `_STATED_TARGETS`）。
        (TriggerType.R3, IntentAction.ADD_PREFERENCE, Basis.STATED),
        # 用户从 stage 勾选条目后点「沉淀本次发现」——勾选就是确认动作。
        # 两种 action 都覆盖：同一个手势确认的是**这些条目**，不是某个 action。
        # （R2 也是这样：按钮确认的是那次测绘，与写的是偏好还是参数无关。）
        (TriggerType.R4C, IntentAction.ADD_PREFERENCE, Basis.EXPERIENCED),
        (TriggerType.R4C, IntentAction.UPDATE_PARAMETER, Basis.EXPERIENCED),
        # ★ P7.1：用户在条目上点「删除 / 恢复 / 编辑」。
        #
        # 这三格与 `R1 + add_preference` **逐字同理**：都是他亲手在界面上做的，
        # 没有第二个人需要再确认一次。判据本身一个字没改 ——
        # 改的是判定表原先**盖不住**这三格（未注册组合 = 直接拒绝），
        # 于是"用户能不能删自己写的假说"这个问题的答案，
        # 一度不是"能"，而是"协议层没有表达它的词"。
        #
        # 这也解释了为什么当时我把它记成"缺口 A14，等裁定"是错的：
        # 判定表要挡的是**模型替用户断言**；用户删自己的东西，
        # 它从来就不该挡 —— 缺的不是裁定，是那个词。
        (TriggerType.R1, IntentAction.ARCHIVE, Basis.STATED),
        (TriggerType.R1, IntentAction.RESTORE, Basis.STATED),
        # ★ 改写是两格，不是一格 —— 因为**它携带内容**，于是服从 2×2 可信度格：
        #   改证据要 `experienced`，改假说要 `stated`。（`archive` / `restore`
        #   不携带内容，所以对两者都只有一格。这个区别是写测试时撞出来的：
        #   把它们混成"生命周期都是 stated"，结果**证据永远改不了**。）
        (TriggerType.R1, IntentAction.REVISE, Basis.STATED),
        (TriggerType.R1, IntentAction.REVISE, Basis.EXPERIENCED),
    }
)


def is_user_confirmed_gesture(
    trigger: TriggerType, action: IntentAction, basis: Basis
) -> bool:
    """这个组合是否「用户在那个手势里已经自己给出或确认了这个值」。"""
    return (trigger, action, basis) in USER_CONFIRMED_GESTURES


PRECHECK_TABLE: tuple[PrecheckRule, ...] = (
    PrecheckRule(
        "R1.add_preference",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _STATED_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R1 用户 UI 直写偏好：用户声明即事实（路由层 §5）",
    ),
    # ↓ P7 新增的第 14 行。**它不是"多了一个功能"，而是原来那一行盖不住的一格。**
    #
    #   `R1 + experienced` 在 P7 之前是一个**未注册组合**（直接 must_reject），
    #   而 `/api/evidence` 恰恰走的就是这条路 —— 它当时靠写 `stated` 绕过去。
    #   把那句 `stated` 拿掉之后（`stated` 如今意味着"他还没试过"），
    #   这条路必须先有一个名字。
    #
    #   结论 auto_apply 的依据与 `R1 + stated` **逐字相同**：R1 是用户在界面上
    #   亲手写下的，没有第二个人需要再确认一次。变的只是他写的是"我试过"而不是
    #   "我想试" —— 而**那正是他点的是哪个表单**这件事，系统不需要再猜。
    PrecheckRule(
        "R1.add_preference.experienced",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _EXPERIENCED_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R1 用户 UI 直写已验证的经验（`/api/evidence`）：他亲手写下「我试过这个，有效」，"
        "与 `R1 + stated` 同理 —— 亲手写的东西不需要再弹一次确认。"
        "**`basis` 由他点的是哪个表单决定，不由模型判断**（P7）",
    ),
    PrecheckRule(
        "R2.update_parameter",
        frozenset({TriggerType.R2}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.MEASURED}),
        Precheck.AUTO_APPLY,
        "R2：Benchmark 流程末端的「确认写入基线」按钮即确认动作，记 user_confirmed=true"
        "（M0 §11 A8）",
    ),
    PrecheckRule(
        "R3.add_preference",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _STATED_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R3 调节 LLM UI（用户已确信）：加偏好直接生效（路由层 §5）",
    ),
    PrecheckRule(
        "R3.update_parameter",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.MEASURED, Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 更新参数需用户确认（路由层 §5）",
    ),
    PrecheckRule(
        "R3.add_preference.experienced",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _EXPERIENCED_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 的「加偏好直接生效」以**用户自己声明**为前提；experienced 只是观察，"
        "不构成「用户已确信」，故需确认（M0 §11 A12）",
    ),
    PrecheckRule(
        "R4a.add_preference",
        frozenset({TriggerType.R4A}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _EXPERIENCED_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4a Session 完成沉淀：**系统在会话结束时自动产出，用户还没看过** → 需确认"
        "（判据见 `USER_CONFIRMED_GESTURES`）",
    ),
    PrecheckRule(
        "R4a.update_parameter",
        frozenset({TriggerType.R4A}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4a 同上：自动产出、用户未过目 → 需确认",
    ),
    PrecheckRule(
        "R4b.add_preference",
        frozenset({TriggerType.R4B}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _EXPERIENCED_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4b 里程碑沉淀：同 R4a（自动产出、用户未过目）",
    ),
    PrecheckRule(
        "R4b.update_parameter",
        frozenset({TriggerType.R4B}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R4b 里程碑沉淀：同 R4a（自动产出、用户未过目）",
    ),
    PrecheckRule(
        "R4c.add_preference",
        frozenset({TriggerType.R4C}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _EXPERIENCED_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R4c 手动沉淀：**勾选就是确认动作**（用户裁定，P5.2 方案 A），"
        "与 R2 的「确认写入基线」按钮同理 —— 见 `USER_CONFIRMED_GESTURES`",
    ),
    PrecheckRule(
        "R4c.update_parameter",
        frozenset({TriggerType.R4C}),
        frozenset({IntentAction.UPDATE_PARAMETER}),
        _M1_TARGETS,
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R4c 手动沉淀：同一个手势确认的是**这些条目**，与 action 无关 —— "
        "原先这里写「更新参数仍需用户确认（M0 §11 A6）」是错的，"
        "因为它等于说「同一个点击对偏好是确认、对参数不是」，而给不出理由。"
        "注意 trait 变量仍被 §7.3 的「≥2 条独立 Session 证据」硬前置条件拦住"
        "（那是 blocker，与判定正交，因此 `AUTO_APPLY + 有 blocker` 依然是待确认）",
    ),
    # ↓ 补缺口 A11：§5.3 未覆盖 `inferred` + add_preference，而路由层 §4.2 第 2 条
    #   要求「inferred 的建议默认降级为 no_change 候选（除非用户显式确认）」。
    #   故判定为 needs_confirmation 而**不是** must_reject —— 红线 3 只禁止
    #   inferred 写**参数**；直接丢掉 LLM 的推断型偏好建议会让它连被看见的机会都没有。
    PrecheckRule(
        "R3.add_preference.inferred",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ADD_PREFERENCE}),
        _INFERRED_TARGETS,
        frozenset({Basis.INFERRED}),
        Precheck.NEEDS_CONFIRMATION,
        "inferred 偏好降级为候选，需用户显式确认（路由层 §4.2 第 2 条；M0 §11 A11）",
    ),
    PrecheckRule(
        "R4.add_preference.inferred",
        _R4_ANY,
        frozenset({IntentAction.ADD_PREFERENCE}),
        _INFERRED_TARGETS,
        frozenset({Basis.INFERRED}),
        Precheck.NEEDS_CONFIRMATION,
        "inferred 偏好降级为候选，需用户显式确认（路由层 §4.2 第 2 条；M0 §11 A11）。"
        "**对 R4c 也成立**，理由是确认动作只覆盖当时摆在他面前的东西："
        "R4c 的列表是 stage 里的本次发现（即 experienced），"
        "而 LLM 的推断不在 stage 里 —— 用户没看见它，所以那个点击没有确认它",
    ),
    # ----------------------------------------------------------------- #
    # ↓ P7.1：C 家族的生命周期（归档 / 恢复 / 改写）
    #
    #   **R1 自动生效、R3 需确认** —— 与新增那条路完全同构，理由也同一句：
    #   R1 是他亲手点的，R3 是模型在对话里提议的。判据没有为这三个动作
    #   改动一个字，这正是「同一句判据管所有手势」该有的样子。
    #
    #   注意 R3 那三行也只收 `stated`：模型不能声称"我观察到他想删掉它"
    #   —— 删除是他的意思表示，只能他说（`handoff` 会核对原话引用）。
    # ----------------------------------------------------------------- #
    PrecheckRule(
        "R1.archive",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.ARCHIVE}),
        _ARCHIVABLE_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R1 用户在条目上点删除：他亲手做的，直接生效。"
        "**行还在**（状态改 archived）—— 物理删除会让历史日志指向空气，"
        "而 I2 的全部意义就是「改了什么都能追回来」。"
        "注意 `basis` 仍是 `stated` 且**只能是它**：归档不携带内容，"
        "所以它没有「可信度」可言 —— 那是他的一个动作，不是一句断言",
    ),
    PrecheckRule(
        "R1.restore",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.RESTORE}),
        _ARCHIVABLE_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R1 用户撤销删除：同 `R1.archive`，判据逐字相同 —— "
        "「解释权在他手里」也包含「我改主意了」。"
        "没有这一格，一次误点就是一条认知永久消失于视野",
    ),
    # ↓ `revise` 为什么要**按 target 类拆成两行**，而 `archive` 不用。
    #
    #   因为改写**携带内容**（三个槽整体替换），于是它落在 2×2 格上：
    #   改一条证据，改完的仍然必须是"已验证的做法"（`experienced`）；
    #   改一条假说，改完的仍然必须是"他说的想试"（`stated`）。
    #
    #   这一条是写测试时撞出来的，而且撞出来的方式很说明问题：
    #   我最初把 `revise` 也写成只收 `stated`，于是**证据永远改不了**
    #   —— 因为写证据需要 `experienced`，而被拒的信息是
    #   "R1 + revise + c_evidence + experienced 未注册"。
    #   那不是"用户不能编辑证据"，那是一句谁也读不懂的内部错误。
    #
    #   教训：**凡是携带内容的动作，都必须服从可信度格；只有不携带内容的
    #   动作（归档 / 恢复）才可以按"他做了个动作"来定 basis。**
    PrecheckRule(
        "R1.revise.evidence",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.REVISE}),
        frozenset({TargetClass.C_EVIDENCE}),
        frozenset({Basis.EXPERIENCED}),
        Precheck.AUTO_APPLY,
        "R1 用户直接编辑一条证据：三个槽整体替换，"
        "改动前后的完整快照都进 RouterLog（`old_value` / `new_value`）—— "
        "**他能改，但改不掉「他改过」这件事**。"
        "`basis` 必须是 `experienced`：改完的仍然是「已验证的做法」",
    ),
    PrecheckRule(
        "R1.revise.hypothesis",
        frozenset({TriggerType.R1}),
        frozenset({IntentAction.REVISE}),
        frozenset({TargetClass.HYPOTHESIS}),
        frozenset({Basis.STATED}),
        Precheck.AUTO_APPLY,
        "R1 用户直接编辑一条假说：同 `R1.revise.evidence`，"
        "但 `basis` 必须是 `stated` —— "
        "**改写不是「我试过了」的意思表示**，那一步要写成 `C.evidence`",
    ),
    PrecheckRule(
        "R3.archive",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.ARCHIVE}),
        _ARCHIVABLE_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 模型在对话里提议「这条以后别用了」：**提议不是删除**，"
        "等他裁决。与 `R3 + add_preference + experienced` 同一档 —— "
        "判据仍是「他当场自己说了吗」，而删除比新增更需要他点头",
    ),
    PrecheckRule(
        "R3.restore",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.RESTORE}),
        _ARCHIVABLE_TARGETS,
        frozenset({Basis.STATED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 模型提议恢复一条已归档的条目：同 `R3.archive`，需他裁决",
    ),
    PrecheckRule(
        "R3.revise.evidence",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.REVISE}),
        frozenset({TargetClass.C_EVIDENCE}),
        frozenset({Basis.EXPERIENCED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 模型提议改写一条证据：需确认。"
        "**改写比新增更容易被当成无害的措辞调整** —— 而它改的是下次装配要用的东西，"
        "所以这一格一个字都不放松",
    ),
    PrecheckRule(
        "R3.revise.hypothesis",
        frozenset({TriggerType.R3}),
        frozenset({IntentAction.REVISE}),
        frozenset({TargetClass.HYPOTHESIS}),
        frozenset({Basis.STATED}),
        Precheck.NEEDS_CONFIRMATION,
        "R3 模型提议改写一条假说：需确认，理由同 `R3.revise.evidence`",
    ),
)


def lookup_rule(
    trigger: TriggerType,
    action: IntentAction,
    target_class: TargetClass,
    basis: Basis,
) -> PrecheckRule | None:
    """返回第一条匹配的行。表中无重叠匹配，故「第一条」与「唯一一条」等价。"""
    for rule in PRECHECK_TABLE:
        if rule.matches(trigger, action, target_class, basis):
            return rule
    return None


def overlapping_rules() -> list[tuple[str, str]]:
    """检测判定表内的重叠 —— 供测试断言表本身无歧义。"""
    overlaps: list[tuple[str, str]] = []
    for index, left in enumerate(PRECHECK_TABLE):
        for right in PRECHECK_TABLE[index + 1 :]:
            if (
                left.triggers & right.triggers
                and left.actions & right.actions
                and left.target_classes & right.target_classes
                and left.bases & right.bases
            ):
                overlaps.append((left.row_id, right.row_id))
    return overlaps


# --------------------------------------------------------------------------- #
# 评估
# --------------------------------------------------------------------------- #


#: 做法表上"这个 basis 不该进这张表"的一句话解释。
#:
#: ## 为什么这值得单独写一段文字
#:
#: 一条未注册组合的默认拒绝理由是机械的："(trigger, action, target 类, basis)
#: 未在判定表中注册"。它对**读代码的人**够用，对**读这句话的模型**不够 ——
#: 模型会以为"这个触发器不支持这种写法"，于是换一个 trigger 再试一次，
#: 而真正的问题是它把「想做」和「做过」搞混了。
#:
#: 这一轮的全部工作就是让那个区别明确，所以拒绝的理由也必须说出那个区别：
#: 不是"这条路不通"，而是"你要写的是另一种东西，它在另一张表"。
_PRACTICE_BASIS_HINTS: dict[tuple[TargetClass, Basis], str] = {
    (TargetClass.C_EVIDENCE, Basis.STATED): (
        "｜他**只是说了**想试，还没试过 —— 那是**假说**，"
        "请写 target=C.hypothesis；证据要求他试过（experienced/measured）"
    ),
    (TargetClass.C_EVIDENCE, Basis.INFERRED): (
        "｜**推断不构成证据**。证据要能回答「我什么时候、在什么条件下试过」，"
        "而推断只能回答「模型觉得你可能」。软偏好才是推断的出口"
    ),
    (TargetClass.HYPOTHESIS, Basis.EXPERIENCED): (
        "｜他**试过了**，那就不再是假说 —— 请写 target=C.evidence。"
        "假说表只装还没验证的想法，否则装配时「只取已验证」就会失效"
    ),
    (TargetClass.HYPOTHESIS, Basis.INFERRED): (
        "｜假说的定义是**他自己说想试**；推断不构成他说的。"
        "想提议一个新做法，先问他一句，他答应了再写"
    ),
}


def _practice_hint(target_class: TargetClass, basis: Basis) -> str:
    """给「做法表 + 不该进这张表的 basis」补一句指向正确出口的解释。"""
    return _PRACTICE_BASIS_HINTS.get((target_class, basis), "")


def validate_value(resolved: ResolvedTarget, intent: UpdateIntent):
    """按 Registry 声明的结构校验参数。返回 `(值对象, 错误信息)`。

    ★ P7.1 起**按 action 取结构**：`archive` / `restore` / `revise` 的参数形状
    与「新增」不同（归档只要一句 reason，改写要多一个 reason）。若仍按
    target 类取那一份，归档会因为填不出 `statement` 而被判"结构不合法" ——
    那会把一个**语义正确**的请求报成语法错误，而模型只能从中学到"别删东西"。
    """
    schema = value_schema_for(resolved.target_class, intent.action)
    if schema is None:
        return None, None
    if intent.action is IntentAction.NO_CHANGE:
        return None, None
    try:
        # `value=None` 按 `{}` 处理，让缺字段的报错落在**字段名**上
        # （"statement: 必填"），而不是一句"不是一个 dict"。
        # 后者会让模型不知道少了什么，而它只能整份重写 —— 重写经常顺手改坏
        # 本来没错的那几条。
        return schema.model_validate({} if intent.value is None else intent.value), None
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(根)"
        return None, f"value 结构不合法：{location} —— {first['msg']}"


def evaluate(
    session: Session, trigger: TriggerType, intent: UpdateIntent
) -> PrecheckResult:
    """对单条 intent 做确定性判定。**不写任何东西。**"""
    if trigger not in INTENT_TRIGGERS:
        # R5（回滚）不产生 intent，因此没有「判定」这回事。
        # 让它走到这里只可能是编程错误 —— 静默套用某条 R1 规则会更糟。
        raise ValueError(
            f"{trigger} 不产出 UpdateIntent，不走 §5.3 判定表；"
            "回滚请用 Router.rollback()（M0 §7.1）"
        )

    resolved = resolve_target(intent.target)

    # ① 未注册 target —— 最优先，因为后续所有判断都依赖它。
    if resolved is None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=REASON_UNREGISTERED_TARGET,
        )

    # ② (target, action) 组合是否被允许。这一条同时覆盖了 §5.3 的
    #    「update_parameter 非 M1.*」与「未注册 target」两行。
    if not action_allowed(resolved, intent.action):
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=f"{REASON_ACTION_NOT_ALLOWED}：target={intent.target}，action={intent.action}",
        )

    # ③ 红线：inferred 永不写参数。
    if intent.action is IntentAction.UPDATE_PARAMETER and intent.basis is Basis.INFERRED:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=REASON_INFERRED_PARAMETER,
        )

    # ④ 参数结构校验：挡在提案落库之前，而不是等 commit 才炸出一半。
    value, value_error = validate_value(resolved, intent)
    if value_error is not None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=value_error,
        )

    # ⑤ no_change 仅写日志（target 必须已注册）。
    if intent.action is IntentAction.NO_CHANGE:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.AUTO_APPLY,
            reason="no_change：仅写日志，不产生任何全局变更（M0 §5.3）",
        )

    # ⑥ 判定表。
    rule = lookup_rule(trigger, intent.action, resolved.target_class, intent.basis)
    if rule is None:
        return PrecheckResult(
            intent_id=intent.intent_id,
            verdict=Precheck.MUST_REJECT,
            reason=(
                f"{REASON_UNREGISTERED_COMBINATION}："
                f"trigger={trigger}, action={intent.action}, "
                f"target 类={resolved.target_class}, basis={intent.basis}"
                f"{_practice_hint(resolved.target_class, intent.basis)}"
            ),
        )

    return PrecheckResult(
        intent_id=intent.intent_id,
        verdict=rule.verdict,
        reason=rule.reason,
        advisories=_advisories(session, trigger, resolved),
        blockers=_blockers(session, trigger, resolved, intent, value),
    )


def _advisories(
    session: Session, trigger: TriggerType, resolved: ResolvedTarget
) -> tuple[BenchmarkAdvisory, ...]:
    """§5.4 重做建议。**只对 R2 生效** —— 只有「重做基准测试」才有重做代价。

    R1 直写、R3/R4 增量沉淀都不是重测，不该弹「本季度已重做 N 次」。
    """
    if trigger is not TriggerType.R2 or resolved.variable_id is None:
        return ()
    return (evaluate_benchmark_advisory(session, resolved.variable_id),)


def _blockers(
    session: Session,
    trigger: TriggerType,
    resolved: ResolvedTarget,
    intent: UpdateIntent,
    value,
) -> tuple[str, ...]:
    """与判定正交的硬前置条件。

    目前只有一条：M1 **trait** 变量以 `experienced` 为依据写入时，
    M0 §7.3 要求「≥2 条独立 Session 证据**或**完整重测」。完整重测即 R2/measured，
    所以这里只在 experienced 路径上检查。
    """
    if (
        resolved.target_class is not TargetClass.M1_TRAIT
        or intent.action is not IntentAction.UPDATE_PARAMETER
        or intent.basis is not Basis.EXPERIENCED
        or value is None
        or resolved.variable_id is None
    ):
        return ()

    count = count_independent_session_evidence(
        session, resolved.variable_id, value.channel
    )
    if count >= 2:
        return ()
    return (
        f"「{resolved.variable_id}」是 trait 变量，以 experienced 为依据写入需要"
        f"≥2 条独立 Session 证据，或走完整重测（R2/measured）。"
        f"当前 {value.channel} 通道只有 {count} 条独立 Session 证据（M0 §7.3）。",
    )
