"""**会话沙箱 → 全局基线**的唯一一道门（ARCH-12）。

这是 `app/agent` 里**唯一允许 import `app.router`** 的模块，
而这条规则由 `tools/archcheck.py` 的 ARCH-12 机械保证 —— 不是靠自觉。
理由和 ARCH-03 同一类：一条安全边界如果只写在注释里，
那么它在下一次"顺手先把 import 加进来"的提交里就没了。

## 这个文件回答一个问题：会话里发生的事，什么时候算数？

沙箱与调停的分工（M7 §10）：

- 会话里的写入**不经过 Router**。stage、笔记、计划修订、动机变化全是沙箱内的事，
  它们可以随时发生、随时作废，不需要任何人批准。
- 但**沙箱里的东西不会自己变成结论**。要让一件事进入全局基线 ——
  「我是一个偏好先看例子的人」「我讨厌网课」—— 只能由这个文件翻译成
  `UpdateIntent` 交给 Router 裁定。

## 三件不由模型决定的事

1. **`intent_id` 由代码分配**。让模型编 id 会导致重复（它没有全局计数器），
   而重复的 intent 会让审计日志里出现"同一个意图被应用了两次"这种
   永远解释不清的记录。所以工具 schema 里**没有** `intent_id`。
2. **`source_ref` 就是当前会话**。谁在说话不能由说话的人声明 ——
   否则模型可以自称来自一次基准测试（R2），而 R2 + measured 在判定表里是
   **自动生效**的，等于绕过用户确认。同理 `trigger_type` 被限制在
   R3/R4a/R4b：R1（UI 直写）、R2（基准测试）与 R4c（用户勾选沉淀）
   都**不是** Agent 能声称的 —— 它们全都是"用户已经做过的某个动作"，
   而模型说"他做过了"不构成他做过。
3. **能力令牌在这里签发**。它不经过工具参数、不经过 `ToolContext`，
   因此模型看不到也拿不到它。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.agent.tools import ToolContext, ToolResult, ToolStatus
from app.core.ids import next_intent_id
from app.core.quotes import MIN_QUOTE_CHARS, normalize_for_quote
from app.db import DbSequenceStore
from app.router import Router, SubmitRequest, UpdateIntent
from app.router.registry import TargetClass, resolve_target
from app.session_domain import reads as session_reads
from app.session_domain.enums import ChatRole

#: Agent 允许使用的触发规则。**这是一个白名单，不是提示。**
#:
#: - `R3` 会话中发现的偏好（add_preference 自动生效）
#: - `R4a` 认知变量更新
#: - `R4b` 里程碑沉淀
#:
#: 被排除的三条：
#: - `R1` 是**用户界面直写**。Agent 声称 R1 等于自称是用户，会让
#:   「用户自己敲的字」与「模型替他说的话」在日志里无法区分；
#: - `R2` 是**基准测试结果**，它在判定表里配 `measured` 是 auto_apply。
#:   一个能声称 R2 的模型可以直接改认知变量而不惊动任何人 ——
#:   这是本文件守着的最重要的一条。
#: - `R4c` 是**用户从 stage 勾选后点沉淀**（缺口 G-01，用户裁定「甲案」）。
#:   判定表给它的「直接生效」理由是**那个勾选动作本身**
#:   （`(R4C, ADD_PREFERENCE, EXPERIENCED)` 与 `(R4C, UPDATE_PARAMETER, EXPERIENCED)`
#:   都在 `USER_CONFIRMED_GESTURES` 里，后者直接写 M1 认知变量）。
#:   而**会话里根本没有这个手势** —— 模型声称 R4c 时，没有任何东西核实过勾选发生过。
#:   所以它与 R2 是同一件事：**它的自动生效建立在一次 UI 动作上，而模型可以凭空声称**。
#:
#:   > 排除 R2 的那句话，逐字适用于 R4c。当时只排到了 R2。
#:
#:   穷举证明（`tools/triggerscan`，48 格）：R4c 被移除前，
#:   `R4c + add_preference/update_parameter/add_evidence + experienced`
#:   三格在**没有任何用户手势**的情况下直接生效。
#:
#:   `R4c` 在**用户侧**依旧合法：`POST /api/sessions/{id}/sediment` 带勾选
#:   仍然走 R4c，并且那条通路上的勾选是**真的发生过**的（`stage_item_ids` 由界面给出）。
#:   换句话说，被拿掉的不是 R4c 这条规则，而是**模型声称它的资格**。
#:
#: **残余（同一类，性质不同，未证伪）**：`R3 + stated` 仍会自动生效。
#: 它的理由是"用户在**对话里**说了这句话" —— 一句关于对话内容的声明，
#: 原则上可核对（`chat_message` 里就有），也是 A18「`stated` 一律按硬约束消费」
#: 的设计前提。它不建立在 UI 手势上，因此不在 G-01 里。
ALLOWED_TRIGGERS = ("R3", "R4a", "R4b")


#: `R3 + stated` 必须带的那段引用，**最少多少个字**才算一次引用。
#:
#: 这不是"防作弊"（模型想编总能编），而是**防退化**：不设下限的话，
#: 一句 `我` 会匹配到任何一段用户文本，于是校验形同虚设、而它会显得"通了"。
#: 4 个字是一个折中：真实的口语声明（"我记不住"）够长，
#: 而"我"、"好的"这种碰巧命中的碎片不够长。
#:
#: ★ 实现搬到了 `app.core.quotes`（P9）—— 因为**要点判官也要用同一套口径**
#: （它声明的每条命中都要附用户答案里的原话，而那句话要核对得上）。
#: 两处各写一遍的话，会出现"路由层认了某句引用、判分层不认"这种
#: 没人能解释的分歧。这里的名字保留下来，因为它在 5 处调用点里出现过。
MIN_BASIS_QUOTE_CHARS = MIN_QUOTE_CHARS
_normalize_for_quote = normalize_for_quote


def verify_stated_quote(db: DbSession, session_id: str, quote: str) -> tuple[bool, str]:
    """`R3 + stated` 的第二道门：**这句话真的是用户说的吗？**

    ## 为什么这道门必须在这里，而不能在路由层

    路由层（`app.router`）**不知道对话内容** —— ARCH-09 禁止它 import
    `session_domain`，而对话记录住在沙箱域。所以"用户到底说过没有"这件事
    只有这一层能查（它有 `ctx.db` 与 `ctx.session_id`，两边都够得着）。
    **这不是为了方便，是依赖方向逼出来的落点**；也正因为它在这一层，
    这个校验才有可能被绕过 —— 所以下面还断言了它拦的是**每条 intent**。

    ## 查什么、不查什么

    只查 `role == user` 的消息。**刻意不查上传的资料** —— 那正是
    缺口 G-01b 的全部内容：把文件里读到的话说成"用户说的"，
    效力却等同于用户亲口下达的约束（A18）。资料是用户的**材料**，
    不是用户的**话**；两者在这个系统里的效力本来就不同。

    ## 返回什么

    `(是否通过, 给模型看的理由)`。理由要能直接塞进工具结果里 ——
    模型需要知道自己为什么被降级，否则它会以为系统坏了。
    """
    needle = _normalize_for_quote(quote)
    if len(needle) < MIN_BASIS_QUOTE_CHARS:
        return False, (
            f"你给的引用只有 {len(needle)} 个字，太短了 —— "
            f"至少要 {MIN_BASIS_QUOTE_CHARS} 个字的连续原话，"
            "否则几乎任何一句用户消息都能「包含」它，这道校验就没有意义。"
        )
    messages = session_reads.list_chat_messages(db, session_id)
    for message in messages:
        if message.role is not ChatRole.USER:
            continue
        if needle in _normalize_for_quote(message.content or ""):
            return True, ""
    return False, (
        f"你在 `basis_quote` 里写的「{quote[:60]}」**没有出现在用户说过的话里**"
        "（我只查用户自己发的话，上传的文件不算）。"
    )


def _is_hypothesis_target(item: dict[str, Any], ctx: ToolContext) -> bool:
    """这条 intent 是不是要写进假说表。

    ★ 用 `resolve_target` 判，而不是比字符串 `== "C.hypothesis"`。
    `C.hypothesis.XXX` 这类带后缀的地址同样指向假说表（`target_for_*` 的
    命名空间允许子地址），字符串相等会漏掉它们，而漏掉的结果是
    "该拒绝的没拒绝"，正好是最不该出错的方向。

    `resolve_target` 返回 `None` 表示未注册 —— 那是判定表的事，
    交给 Router 报错，这里只回答"是不是假说"。
    """
    if item.get("action") == "no_change":
        return False
    target = item.get("target")
    if not isinstance(target, str):
        return False
    resolved = resolve_target(target)
    return resolved is not None and resolved.target_class is TargetClass.HYPOTHESIS


def submit_global_update(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """`submit_global_update` 的真实执行体 —— 工具层故意没有这个 handler。"""
    trigger = args["trigger_type"]
    if trigger not in ALLOWED_TRIGGERS:
        return ToolResult(
            ToolStatus.REJECTED,
            f"`trigger_type={trigger}` 不是你能声称的。只能填 "
            f"{'、'.join(ALLOWED_TRIGGERS)}："
            "R1 是用户界面直写（那是**用户自己**说的话）、"
            "R2 是基准测试结果（它在判定表里自动生效，你声称 R2 就等于绕过用户确认）、"
            "R4c 是用户从 stage 勾选后点沉淀（**那个勾选得真的发生过**，"
            "而它由界面产生，不由你声明）。"
            "如果用户在这次对话里确实说过某件事，用 R3。",
            {"allowed": list(ALLOWED_TRIGGERS)},
        )

    raw_intents = args["intents"]
    if not raw_intents:
        return ToolResult(ToolStatus.REJECTED, "至少要带一条 intent。")

    intents: list[UpdateIntent] = []
    downgrades: list[str] = []
    for index, item in enumerate(raw_intents, start=1):
        item = dict(item)
        # `basis_quote` **只活在工具层**：它不进 `UpdateIntent`（那是全局契约，
        # 路由层也验证不了它），而是这里用完就摘掉。与 `intent_id` 同一个待遇。
        quote = item.pop("basis_quote", None)
        if (
            trigger == "R3"
            and item.get("basis") == "stated"
            and item.get("action") != "no_change"
        ):
            ok, why = (
                verify_stated_quote(ctx.db, ctx.session_id, quote)
                if isinstance(quote, str)
                else (
                    False,
                    "`R3 + basis=stated` 声称的是「用户自己说的」，但你没给 "
                    "`basis_quote`（用户在对话里的原话）。",
                )
            )
            if not ok:
                # ★ P7：假说这条路**不能降级**，只能拒绝。
                #
                # 降级的用途是"把一条可能对的观察从『已是事实』挪到『等人确认』"。
                # 而假说表按定义只装他**自己说**的话 —— `C.hypothesis` + `experienced`
                # 是判定表专门拒绝的一格。于是"降级"在这里会产出一条注定被拒的
                # intent，模型收到的错误信息会是「假说不接受 experienced」，
                # 而真正的问题其实是「你没有他的原话」。
                # 让错误信息指向真正的原因，比让它指向机制细节有用得多。
                if _is_hypothesis_target(item, ctx):
                    return ToolResult(
                        ToolStatus.REJECTED,
                        f"第 {index} 条想写成假说（`C.hypothesis`），但引用验证没过"
                        f"（{why}）。假说记录的是**他自己说的**「我想试试 X」，"
                        "所以必须带上他在这次对话里的原话。如果你只是猜他会想试 —— "
                        "别写，先问他一句「要不要试试……」，他答应了再写。",
                    )
                # ★ 降级，而不是拒绝。理由：这条认知**可能真的对**，
                # 只是模型没资格替用户声称它 —— 那正是"进待确认队列"这个出口的用途。
                # 拒绝会让模型丢掉一条可能正确的观察；降级只把它从
                # "已经是事实"挪到"等人确认"。
                item["basis"] = "experienced"
                item["reasoning"] = (
                    f"{item.get('reasoning', '')}"
                    f"［系统降级 stated→experienced：{why}］"
                ).strip()
                downgrades.append(f"第 {index} 条：{why}")
        try:
            intents.append(
                UpdateIntent(
                    # id 由代码分配，见模块开头第 1 条。
                    intent_id=next_intent_id(DbSequenceStore(ctx.db)),
                    **item,
                )
            )
        except Exception as exc:  # pydantic 的校验错误信息对模型是可读的
            return ToolResult(
                ToolStatus.REJECTED,
                f"第 {index} 条 intent 不符合契约：{exc}",
            )

    result = Router(ctx.db).submit(
        SubmitRequest(
            trigger_type=trigger,
            intents=intents,
            # 出处就是当前会话 —— 不接受模型填写。
            source_ref=ctx.session_id,
            session_ref=ctx.session_id,
        )
    )
    return _to_tool_result(ctx, trigger, result, downgrades)


def _to_tool_result(
    ctx: ToolContext,
    trigger: str,
    result: Any,
    downgrades: list[str] | None = None,
) -> ToolResult:
    """把路由层的裁定翻译成工具结果。

    **被拒绝也要如实转达**，而不是包装成成功：模型需要知道自己的提议
    被哪条规则挡了，否则它会一遍遍重试同一条写入。

    ★ **但"等用户确认"不是"被拒"** —— 这两件事在这里被混成了一件。
    一次提交里有三种结局，必须分成三种说：

    | 结局 | 报给模型的 | 它该做什么 |
    |---|---|---|
    | 全部生效 | `success` + "已生效" | 可以据此往下说 |
    | 有生效、也有等确认 / 被拒 | `success` + 逐条说明 | 告诉用户"这几条要你去确认" |
    | 全部等确认 | `success` + "**还没生效**，等用户确认" | **必须告诉用户去看待确认队列** |
    | 全部被拒 | `rejected` + 理由 | 看理由，别原样重试 |

    第三行是这次修的主句。它以前落在第四行，于是模型听到"这次提交没有任何一条生效"
    ——而实际上用户界面上多了一张等待裁决的提案卡，模型却不知道要告诉他。

    `downgrades` 是**第二道门**（`R3 + stated` 的引用核对）拦下来的条目。
    它必须出现在给模型的话里：模型需要知道"我以为用户说过、系统认为他没说"，
    否则它会照着自己那个错误前提继续推理下去。
    """
    applied = [r for r in result.results if r.status.value == "applied"]
    pending = [r for r in result.results if r.status.value == "pending"]
    rejected = [
        r for r in result.results if r.status.value not in {"applied", "pending"}
    ]
    downgrade_note = (
        ""
        if not downgrades
        else "★ 有 " + str(len(downgrades)) + " 条被**降级**了"
        "（`stated` → `experienced`，因此需要用户确认）："
        + "；".join(downgrades)
        + "。降级的含义是「你的观察可能对，但你无权替用户声称他说过」——"
        "**这不是系统故障**，也别把它当成用户拒绝。"
    )
    applied = [r for r in result.results if r.status.value == "applied"]
    pending = [r for r in result.results if r.status.value == "pending"]
    rejected = [
        r for r in result.results if r.status.value not in {"applied", "pending"}
    ]

    data = {
        "proposal_id": result.proposal_id,
        "proposal_status": result.status.value,
        "trigger_type": trigger,
        "results": [
            {
                "intent_id": r.intent_id,
                "status": r.status.value,
                "verdict": r.verdict.value if hasattr(r.verdict, "value") else str(r.verdict),
                "reason": r.reason,
                "router_log_id": r.router_log_id,
                "advisories": [a.model_dump() if hasattr(a, "model_dump") else a for a in r.advisories],
                "blockers": list(r.blockers),
            }
            for r in result.results
        ],
        # `pending` 的含义要说清楚：提案已落库，等用户确认 —— 它**还不是结论**。
        "pending_confirmation": bool(pending),
        "applied_count": len(applied),
        "pending_count": len(pending),
        "rejected_count": len(rejected),
    }

    if applied and not pending and not rejected:
        return ToolResult(
            ToolStatus.SUCCESS,
            f"已提交 {len(applied)} 条更新，**已生效**"
            f"（提案 {result.proposal_id}，状态 {result.status.value}）。"
            + downgrade_note,
            data,
        )

    if applied:
        parts = [f"{len(applied)} 条**已生效**"]
        if pending:
            parts.append(
                f"{len(pending)} 条**进了待确认队列**（在界面上等用户裁决 —— "
                "**请把这件事告诉他**）"
            )
        if rejected:
            parts.append(f"{len(rejected)} 条被拒（理由在 results 里，**不要原样重试**）")
        return ToolResult(
            ToolStatus.SUCCESS,
            "提交结果：" + "、".join(parts) + f"（提案 {result.proposal_id}）。"
            + downgrade_note,
            data,
        )

    if pending:
        # ★ 这一格以前说成"没有任何一条生效"，见本函数开头。
        return ToolResult(
            ToolStatus.SUCCESS,
            f"{len(pending)} 条已提交，**但还没有生效** —— 提案 {result.proposal_id} "
            f"（状态 {result.status.value}）正等用户在界面上确认。"
            "**请把这件事告诉用户**，让他去看待确认队列；不要当作已经写进去了。"
            + downgrade_note,
            data,
        )

    return ToolResult(
        ToolStatus.REJECTED,
        "这次提交没有任何一条生效，**也没有进待确认队列**。理由在 results 里："
        + "；".join(r.reason for r in rejected)
        + downgrade_note,
        data,
    )
