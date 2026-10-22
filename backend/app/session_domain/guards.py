"""会话状态机与写保护 —— **强制拒绝**（P4，用户裁定）。

## 为什么状态机值得存在

一个不拒绝任何东西的状态机等于没有状态机。本项目的立足点是「不能绕过」，
所以 `completed` 必须意味着**有些事真的做不了**，否则「完成」只是界面上一个标签。

但「拒绝」必须是有理由的拒绝，否则它只是摩擦。因此这里把**每个拒绝的理由**
写在数据表里（`WRITE_GUARDS` / `TRANSITIONS`），并且理由要能指回文档或裁定：

| 状态 | 含义 | 能写什么 |
|---|---|---|
| `draft` | 起草中，计划还没生成 | 定义区、动机、快照 |
| `active` | 执行中 | 全部 |
| `review` | 整理中（执行完了，还没归档） | 总结、沉淀、笔记 |
| `completed` | 已归档 | **只有笔记**（用户裁定给的唯一例外口子） |
| `abandoned` | 放弃 | 笔记、沉淀（M7 §8：部分条目可沉淀） |

## 两个错误不要合并

`StateNotWritable`（状态不对）与 `VersionConflict`（版本不对）都是 409，
但 `code` 不同：前者告诉你「换个状态再来」，后者告诉你「别人刚改过，刷新」。
把它们合并成一个「409 了」的模糊提示，是让用户去猜系统的状态。

## 为什么是元组 + 查询函数，而不是 dict

`WRITE_GUARDS` 要被测试**逐行穷举**（每个操作 × 每个状态 = 是否可写）。
dict 也能穷举，但一行写错一个键名不会报错；`WriteGuard` 是 dataclass，
`operation` 是枚举，写错就是 `ImportError` 或 `AttributeError` —— 立刻可见。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import StateNotWritable
from app.session_domain.enums import (
    SEDIMENTABLE_STATES,
    SessionOperation,
    SessionState,
)

_ALL_STATES: frozenset[SessionState] = frozenset(SessionState)


@dataclass(frozen=True)
class WriteGuard:
    """「在哪些状态下允许做这个操作」。"""

    operation: SessionOperation
    states: frozenset[SessionState]
    reason: str

    def allows(self, state: SessionState) -> bool:
        return state in self.states


#: **允许写**的状态集合（不是「拒绝」—— 准入表漏一行的后果是「哪里都写不了」，
#: 立刻在测试里炸；漏一行的拒绝表后果是「哪里都能写」，永远不炸）。
#:
#: ★ 判据只有一条：**不拦就会写出自相矛盾、或无法核对的数据**，才值得拒绝。
#: 其余都是流程偏好，它们的代价由用户付：新建会话聊不起来、整理期改不了计划、
#: 完成要点两次 —— 这些都不是数据问题，是"系统替用户决定了他不该做什么"。
#: 所以 `completed` / `abandoned` 之后仍然关着（归档之后再改定义区与动机，
#: 「当时为什么这么总结」就再也核对不了），而 `review` 一律放开。
WRITE_GUARDS: tuple[WriteGuard, ...] = (
    WriteGuard(
        SessionOperation.UPDATE_INPUT,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "定义区是「这次要做什么」的约定：起草、执行、整理期都可以改。"
        "**只有归档之后不行** —— 那时再改，总结与已沉淀的结论就对不上原话了，"
        "而「当时为什么这么总结」正是回看时唯一能核对的东西",
    ),
    WriteGuard(
        SessionOperation.UPDATE_MOTIVATION,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "动机驱动计划与推进节奏；计划已经定稿、会话已经归档之后再改动机，"
        "不会影响任何已经发生的事，只会改写历史叙述",
    ),
    WriteGuard(
        SessionOperation.UPDATE_SNAPSHOT,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "精力/压力/环境是**当下**的状态（M7 §4.1）。归档之后它描述的不再是任何时刻，"
        "写它等于往一张历史表里填新数据",
    ),
    WriteGuard(
        SessionOperation.CREATE_PLAN,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "计划不挑状态：起草期最想做的事往往就是「先帮我排一版」。"
        "它写出来的是一份带版本号的计划，goal_derived 空着也自洽 —— "
        "拦它只会让新会话在界面上无从下手（要聊先有计划，要有计划先能聊）",
    ),
    WriteGuard(
        SessionOperation.REVISE_PLAN,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "计划在执行期与整理期都能改（整理期发现要改是正常诉求，"
        "改出来的是一份新版本，自洽）。**无理由不改计划**（M7 §9 第 3 条）："
        "`plan_revision.reason` 在库层是 NOT NULL",
    ),
    WriteGuard(
        SessionOperation.MARK_STEP,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "步骤进度在执行期与整理期都有意义（整理期补记「昨天做完了」是常见的事）；"
        "归档之后它描述的是当时的事实，不该再被改写",
    ),
    WriteGuard(
        SessionOperation.RECORD_CHECKPOINT,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "检查点记录的是「这个关键步骤过没过」：执行期随时记，整理期补记也允许；"
        "归档之后它就是当时的判定，不能再改",
    ),
    WriteGuard(
        SessionOperation.ADD_STAGE_ITEM,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "stage 是「本次会话想试但还没决定要不要沉淀」的暂存区（M7 §7.1）："
        "执行期与整理期都能往里放（整理期正是最容易想起「还有个想法没试」的时候）；"
        "归档之后它只剩归宿（见 SETTLE_STAGE_ITEM）",
    ),
    WriteGuard(
        SessionOperation.SETTLE_STAGE_ITEM,
        SEDIMENTABLE_STATES,
        "标记归宿要跟着「沉淀」走：沉淀允许的状态就是这里允许的状态。"
        "`abandoned` 也在内，因为 M7 §8 明确放弃的会话「部分条目可沉淀」",
    ),
    WriteGuard(
        SessionOperation.ADD_CHAT,
        _ALL_STATES,
        "**说话不设门**。消息是 append-only 的：它不涨任何版本、不参与任何判定、"
        "也不会让哪两条记录互相矛盾。原来只在 active 开口子，代价是「新建一个会话"
        "之后连一句 hello 都说不出去」—— 那是一条没有数据理由的门，"
        "它挡掉的正是最正常的动作。想说什么就说什么，话题跑偏由用户自己负责",
    ),
    WriteGuard(
        SessionOperation.ADD_NOTE,
        _ALL_STATES,
        "★ **终态的唯一例外口子**（用户裁定）。写完一个会话之后想补一句笔记，"
        "是真实需求而不是绕过治理：笔记是草稿纸，它不改任何权威状态，"
        "也不会被任何自动流程读取",
    ),
    WriteGuard(
        SessionOperation.ADD_ATTACHMENT,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "资料是**讨论的输入**：起草（还没计划）时最想先把它传上来，"
        "执行中则用它改计划。**刻意不跟着 `ADD_NOTE` 一起开到终态** —— "
        "笔记是草稿纸（无生命周期，随手补一句），而资料是一份会被归档进"
        "这条会话记录的材料；归档之后还要改它，那是「编辑历史」而不是「补笔记」",
    ),
    WriteGuard(
        SessionOperation.ATTACHMENT_REMOVE,
        frozenset({SessionState.DRAFT, SessionState.ACTIVE, SessionState.REVIEW}),
        "删除与上传同域：它是**整理材料**（传错了、传重了），不是补一句话。"
        "理由同上 —— 归档记录不该被改写",
    ),
    WriteGuard(
        SessionOperation.ADD_MILESTONE,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "里程碑是执行期的路标",
    ),
    WriteGuard(
        SessionOperation.COMPLETE_MILESTONE,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "同上：达成只发生在执行中",
    ),
    WriteGuard(
        SessionOperation.WRITE_SUMMARY,
        frozenset({SessionState.ACTIVE, SessionState.REVIEW}),
        "总结在**整理期**写（这是 review 存在的理由）；执行中允许写阶段性总结。"
        "`completed` 不允许再写，是因为总结已经定稿、沉淀提案已经产出",
    ),
    WriteGuard(
        SessionOperation.WRITE_REFLECTION,
        frozenset(
            {SessionState.REVIEW, SessionState.COMPLETED, SessionState.ABANDONED}
        ),
        "感想是**复盘之后**的动作（P15），所以执行中（`active`）不开放 —— "
        "那时想记就写笔记（`POST /notes`，它在所有状态都可写）。"
        "把这条边界写进状态机而不是留给界面，是因为界面上的按钮拦不住 API："
        "「什么时候能写感想」必须有一个可穷举的答案。"
        "`completed` 与 `abandoned` 都开放：回看可以在归档之后很久才发生，"
        "而放弃一个会话同样值得写下「为什么」。"
        "另有一条**事实**门槛不在这张表里 —— 没有复盘总结时不允许写，"
        "见 `reflection._blocked_reason`：那张表管状态，这条管「有没有可对照的东西」",
    ),
    WriteGuard(
        SessionOperation.TRANSITION,
        _ALL_STATES,
        "状态转移本身不看状态 —— 它看 `TRANSITIONS` 这张边表。"
        "放进本表是为了让「哪些操作参与状态校验」是穷举的，"
        "而不是靠人记得「转移不在此列」",
    ),
)


@dataclass(frozen=True)
class Transition:
    """一条状态转移边。`label` 是界面上按钮的文字，也是拒绝时的解释材料。"""

    source: SessionState
    target: SessionState
    label: str
    reason: str


#: 状态转移边表（M7 §8）。**列出的边之外一概不允许** ——
#: 尤其是 `draft → completed`：跳过执行与整理直接归档，等于没发生任何学习。
TRANSITIONS: tuple[Transition, ...] = (
    Transition(
        SessionState.DRAFT,
        SessionState.ACTIVE,
        "激活",
        "draft ──激活──▶ active（M7 §8）。激活要求定义区至少有目标",
    ),
    Transition(
        SessionState.ACTIVE,
        SessionState.REVIEW,
        "结束执行",
        "active ──▶ review：执行完了，进入整理期（写总结、挑要沉淀的东西）",
    ),
    Transition(
        SessionState.ACTIVE,
        SessionState.COMPLETED,
        "直接归档完成",
        "active ──▶ completed：给了总结就能直接归档。"
        "原来缺这条边，`/complete` 在 active 下必然 409 —— 而且是**总结写完现实之后**"
        "才在转移那一步失败，用户看到的是「点完成报了个状态错」",
    ),
    Transition(
        SessionState.REVIEW,
        SessionState.ACTIVE,
        "回到执行",
        "review ──▶ active：整理时发现还差一步，回去继续（M7 §8 允许回退）",
    ),
    Transition(
        SessionState.REVIEW,
        SessionState.COMPLETED,
        "归档完成",
        "review ──▶ completed：总结写完、沉淀提案已产出，会话归档",
    ),
    Transition(
        SessionState.COMPLETED,
        SessionState.REVIEW,
        "重新打开",
        "completed ──▶ review：回到整理期看看（先读一遍再动手）",
    ),
    Transition(
        SessionState.COMPLETED,
        SessionState.ACTIVE,
        "回到执行",
        "completed ──▶ active：重开一个归档的会话继续干。"
        "原来只允许两跳，那是纯摩擦 —— 状态与时间戳在任何一跳上都是一致的",
    ),
    Transition(
        SessionState.DRAFT,
        SessionState.ABANDONED,
        "放弃",
        "draft / active / review 都可以放弃；abandoned 是终态",
    ),
    Transition(
        SessionState.ACTIVE,
        SessionState.ABANDONED,
        "放弃",
        "同上",
    ),
    Transition(
        SessionState.REVIEW,
        SessionState.ABANDONED,
        "放弃",
        "同上",
    ),
)


def guard_for(operation: SessionOperation) -> WriteGuard:
    """取一个操作的准入表。找不到就抛 —— 新加的操作**必须**先表态。"""
    for guard in WRITE_GUARDS:
        if guard.operation is operation:
            return guard
    raise LookupError(f"操作 {operation!r} 没有准入规则：先想清楚它在哪些状态下可写")


def is_writable(state: SessionState, operation: SessionOperation) -> bool:
    return guard_for(operation).allows(state)


def assert_writable(state: SessionState, operation: SessionOperation) -> None:
    """不满足就抛 `StateNotWritable`（409 + `code=state_not_writable`）。"""
    guard = guard_for(operation)
    if guard.allows(state):
        return
    allowed = " / ".join(sorted(s.value for s in guard.states))
    raise StateNotWritable(
        f"会话处于 {state.value} 状态，不能执行 {operation.value}。"
        f"允许的状态：{allowed}。原因：{guard.reason}",
        detail={
            "state": state.value,
            "operation": operation.value,
            "allowed_states": sorted(s.value for s in guard.states),
        },
    )


def transition_for(source: SessionState, target: SessionState) -> Transition | None:
    for edge in TRANSITIONS:
        if edge.source is source and edge.target is target:
            return edge
    return None


def can_transition(source: SessionState, target: SessionState) -> bool:
    return transition_for(source, target) is not None


def assert_transition(source: SessionState, target: SessionState) -> Transition:
    """不允许的边抛 `StateNotWritable`，并把**所有**可走的边一起返回。

    为什么把可选边塞进 `detail`：前端的按钮就是这张表渲染的，
    拒绝时顺手告诉它"现在能去哪"，界面就不用再猜一次。
    """
    edge = transition_for(source, target)
    if edge is not None:
        return edge
    allowed = sorted(e.target.value for e in TRANSITIONS if e.source is source)
    raise StateNotWritable(
        f"{source.value} → {target.value} 不是允许的转移。"
        f"从 {source.value} 可以转到：{' / '.join(allowed) if allowed else '（无处可去，终态）'}",
        detail={
            "from": source.value,
            "to": target.value,
            "allowed_targets": allowed,
        },
    )


def allowed_targets(source: SessionState) -> list[str]:
    """某个状态可以转到哪些状态（界面按钮来源）。"""
    return sorted(e.target.value for e in TRANSITIONS if e.source is source)


def overlapping_operations() -> list[SessionOperation]:
    """`WRITE_GUARDS` 里重复出现的操作。**必须为空**（同 `precheck.overlapping_rules`）。"""
    seen: dict[SessionOperation, int] = {}
    for guard in WRITE_GUARDS:
        seen[guard.operation] = seen.get(guard.operation, 0) + 1
    return [op for op, count in seen.items() if count > 1]


def unguarded_operations() -> list[SessionOperation]:
    """`SessionOperation` 里**没有**准入规则的那些。**必须为空。**

    这是本模块的完整性自检：新增一个操作却忘了表态，它就会落到这里被测试抓住 ——
    否则那个操作会在**所有**状态下都「不能写」，或者被某处硬编码放行。
    """
    declared = {guard.operation for guard in WRITE_GUARDS}
    return [op for op in SessionOperation if op not in declared]
