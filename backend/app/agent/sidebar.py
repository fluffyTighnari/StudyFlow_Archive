"""侧边栏常驻通用模型（P6+.6）。

## 它是一个什么东西

**不是会话**。它没有任务、没有计划、没有步骤、没有终态，也不需要复盘。
它是那块一直在右边的面板：用户随手把感受、方法、想法说给它，
它负责理解、追问、以及**在用户同意之后**把值得留下的东西整理进全局认知。

## 与会话模型的差别，逐条落在代码里

| 维度 | 会话 | 侧边栏 |
|---|---|---|
| 上下文包 | `assemble_discussion_context`（含计划/stage/步骤） | `assemble_sidebar_context`（只有全局那一半） |
| 系统提示词 | `SYSTEM_PROMPT` | `SIDEBAR_SYSTEM_PROMPT`（见下） |
| 工具集 | 全部 15 个 | `SIDEBAR_TOOL_SCOPES` = 4 个（3 全局读 + 1 全局写） |
| 锚点 | 某一 `step_id` | 无（空串） |
| 状态机 | 有（`active` 才能聊） | **没有** —— 常驻 |
| 消息表 | `chat_message` | `sidebar_message` |

## ★ 为什么用**子类**而不是复制一份循环

`AgentLoop._walk` 是唯一的循环体，会话与侧边栏共用。它会话特有的决定
只有五个（`_system_prompt` / `_open_context` / `_tool_schemas` /
`_make_dispatcher` / `resolve_anchor`），而这五个已经是基类上的可覆写方法。

复制一份循环体会在"某条路上修了、另一条忘了"的时候安静地分叉 ——
这正是 `run` / `run_stream` 那条注释在防的同一件事。
**五个钩子这个数字本身是一条证据**：会话概念在循环里的落点确实只有这么多。

## ★ 侧边栏**能**做什么、**不能**做什么

能（4 个工具）：
- `retrieve_folder` / `retrieve_cognitive` / `retrieve_strategy` —— 读全局；
- `submit_global_update` —— **提议**写全局（走提案 → 用户确认，不经沙箱直写）。

不能：任何会话写入。它们连**工具清单里都不出现**
（`SIDEBAR_TOOL_SCOPES` 不含 `SESSION_READ` / `SESSION_WRITE`）。
这不是"禁用"而是"不存在" —— 见 `ToolScope` 的说明。

## 关于第 28 条要求「模型兼顾前面通用模型的全部功能」

这一条**不能**字面满足：通用模型的功能里有一半是"改计划"，
而侧边栏没有计划可改。能兼顾的是**通用的那一半** ——
读全局、理解用户、提议写全局认知。剩下那一半**在物理上不存在**，
而不是被限制了。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent.dispatch import Dispatcher
from app.agent.external import ExternalToolbox
from app.agent.loop import AgentLoop
from app.agent.tools import SIDEBAR_TOOL_SCOPES, ToolSchema, tool_schemas
from app.session_domain.enums import SIDEBAR_SCOPE_ID
from app.session_domain.sidebar_context import (
    SidebarContext,
    assemble_sidebar_context,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DbSession

    from app.agent.llm import LLMClient

#: 侧边栏的系统提示词。
#:
#: 与会话那份的**根本差别只有一句**：这里没有"当前这一步"。
#: 而它必须写出来 —— 一个不知道自己没有计划的模型会开始猜
#: （"你接下来应该…"），并且猜得像是它看过计划一样。
#:
#: 六条行为契约中适用于侧边栏的四条逐条落进来（改计划与 checkpoint 两条
#: 不适用，因为工具根本不存在）；提示词与工具集**必须同时成立**：
#: 只写提示词是"一句请求"，只给工具是"一次不懂为什么的拒绝"。
SIDEBAR_SYSTEM_PROMPT = """\
你是一个**常驻的学习助手**。用户在学习过程中随时把感受、困惑、方法上的想法说给你。
你不在任何一个具体任务里 —— 你**没有当前计划、没有当前步骤**。

## 你能做什么、不能做什么

1. **能读全局，不能直写全局。** 用户的学习偏好、认知基线、证据、学习策略你都能读。
   要写它们只有一个入口：`submit_global_update`，而它会产出提案等用户确认。
2. **你没有任何会话写入工具。** 这不是限制，是**你确实没有可改的东西** ——
   没有计划、没有步骤。所以不要承诺"我帮你改一下计划"：
   要说"你可以在那条会话里跟我（或那个会话里的我）说，我来改"。
3. **每个动作都要有据。** 不主动改任何东西；提议写入必须带理由。
   **用户的感受是他的数据，不是你的结论** —— 他说"我觉得我适合早上学"，
   那是一条 `stated`；你不能把它写成"实测发现他早上效率高"。

## 你要做好的三件事

1. **接住感受。** 用户说"今天好累""这个方法真别扭""我终于想通了"时，
   先说人话（共情、追问细节），不要立刻把它们变成一条条待办。
2. **讨论方法，但不改任何东西。** 他问"有没有更好的学法"时，
   拿 `retrieve_strategy` 与 `retrieve_cognitive` 里的东西和他讨论，
   给出取舍理由。**讨论本身不产生任何写入。**
3. **提议整理进认知基准。** 当你判断某句话值得长期留下
   （一条偏好、一个观察、一条证据），用 `submit_global_update` 提出来，
   并**说清你在提议什么、为什么**，让他决定。

## 不要做的事

- **不要把每句话都存成笔记或待办。** 大多数时候你只是聊天：接住、追问、给建议。
  只有当你判断某句话值得长期留下时，才用 `submit_global_update` 提出提案。
  用户说一句感受，你回一句"已记入笔记"—— 那不是帮助，是把对话变成了归档。
- 不要强行把话题拉回某个具体会话或计划；你没有计划，也不需要装有一个。
- 不要在没被要求时反复推销"要不要整理进认知"。提一次就够了，他拒绝了就不再提。

## 需要他补充信息时：用 `mou-form`

不要只写一串问句。当他必须给一个值或勾一个选项才能继续时，输出一个
`mou-form` 代码块（合法 JSON，不要注释）：

```mou-form
{"title":"我想先确认两件事","submit":"按这个继续","fields":[
  {"key":"scene","type":"string","label":"这个情况通常发生在什么时候","placeholder":"例如 晚上坐下来之后"},
  {"key":"tried","type":"bool","label":"你已经试过把手机放远一点","value":false}
]}
```

- `type` 只能是 `string`（输入框）或 `bool`（勾选框）；一次最多 4 个字段。
- 他点提交后，界面会把回答拼成一条完整消息发给你，你再继续。
- `mou-form` 是**正文里的代码块**，不是可调用的工具；不要发起同名工具调用。

## 关于提议写全局认知

- 记得他的原话就附 `basis_quote`；不记得就**不要硬编引语**，改用
  `basis=experienced` 提成待确认提案。提案本来就是让他决定的地方。
- 一次提一条最清楚；不要一次塞很多条让他逐条裁决。

## 回答的排版记号（界面会解析，不是 markdown）

- 关键判断用 **两个星号** 包住；专有名词用 `反引号` 包住（只换字体，不加标记）。
- 需要用户回答的一个短问题，可以单独一行以 `? ` 开头；多个字段请用 `mou-form`。
- 不要输出 HTML，不要用表格。

## 语气

像一个认识他很久的助教：直接、不客套、不灌鸡汤。
**不确定就说不确定**，不要为了显得懂而编。
"""


def render_sidebar_context(ctx: SidebarContext) -> str:
    """把侧边栏的上文包渲染成模型可读的文本。

    与会话侧的 `_render_context` 同一个姿态：**文本而不是原始 JSON**，
    因为这里要的是「他是一个什么样的人、他到过哪里」，
    而不是让模型把注意力花在括号上。

    ★ 最后一段（「你没有计划」）不是装饰：它是这一整份上下文里
    **唯一说明"缺了什么"的地方**。一份只列出"有什么"的上下文，
    会让模型把它当成完整的世界图景，于是开始推断"他接下来该做第几步"。
    """
    lines: list[str] = ["# 常驻助手（不绑定任何会话）", ""]

    lines.append("## 用户偏好（硬约束 —— 他明确说过，不可推翻）")
    if ctx.constraints:
        lines += [f"- [{item.pref_id}] {item.statement}" for item in ctx.constraints]
    else:
        lines.append("- （还没有）")
    lines.append("")

    lines.append("## 用户偏好（软偏好 —— 可以推翻，但要说清为什么）")
    if ctx.soft_preferences:
        lines += [f"- [{item.pref_id}] {item.statement}" for item in ctx.soft_preferences]
    else:
        lines.append("- （还没有）")
    lines.append("")

    lines.append("## 领域勾选（他自认为熟悉 / 感兴趣的专题）")
    if ctx.domain_checklist is None:
        # ★ 「还没勾」是一条**可执行的事实** —— 它可以邀请他去勾。
        # 渲染成"（空）"会让模型以为"他不熟悉任何领域"，
        # 而那是另一个结论，且是错的。
        lines.append("- **他还没有勾过**（可以在基准测试里勾，或提醒他去勾）")
    else:
        lines.append(
            f"- 熟悉（第 {ctx.domain_checklist.version_no} 版）："
            + ("、".join(ctx.domain_checklist.familiar) or "（一个都没勾）")
        )
        lines.append(
            "- 更感兴趣：" + ("、".join(ctx.domain_checklist.interested) or "（一个都没勾）")
        )
    lines.append("")

    lines.append("## M1 认知基线（按通道；stated/measured/experienced 是不同的证据强度）")
    if ctx.baseline:
        for item in ctx.baseline:
            lines.append(
                f"- {item.variable_id}｜{item.channel}｜{item.score}"
                f"（置信度 {item.confidence or '未知'}，完整度 {item.completeness}）"
            )
    else:
        lines.append("- （还是空的 —— 他没做过基准测试，也没有累积出观测）")
    lines.append("")

    lines.append("## 个性化证据 —— 他**试过**，验证有效")
    if ctx.evidence:
        for item in ctx.evidence:
            lines.append(
                f"- [{item.evidence_id}] 当 {item.condition} 时，{item.effect}"
                f"（{item.statement}，强度 {item.strength}）"
            )
    else:
        lines.append("- （还没有）")
    lines.append("")

    # ★ 与上面那一节结构同形，所以差别必须写在读的人看得见的地方（P7）。
    lines.append("## 假说 —— 他**说过想试、但还没试过**（**未验证，不是依据**）")
    if ctx.hypotheses:
        for item in ctx.hypotheses:
            lines.append(
                f"- [{item.hypothesis_id}]（未验证）当 {item.condition} 时，"
                f"{item.effect}（{item.statement}）"
            )
        lines.append(
            "  用法：可以提议「你之前说想试试这个，要不要试一下」，"
            "但不要把它当成已证明有效的做法来安排。"
        )
    else:
        lines.append("- （没有）")
    lines.append("")

    lines.append("## 策略库")
    if ctx.strategies:
        for row in ctx.strategies:
            lines.append(
                f"- [{row.strategy_id}] **{row.name}** —— 解决：{row.solves}；"
                f"什么时候用：{row.when_to_use}"
            )
    else:
        lines.append("- （还没有）")
    lines.append("")

    lines.append("## 他最近到过哪里（只是索引；要看内容请用 retrieve_session 显式给 session_id）")
    if ctx.recent_sessions:
        for row in ctx.recent_sessions:
            head = f"：{row.summary_head}" if row.summary_head else ""
            lines.append(
                f"- [{row.session_id}]（{row.state}）{row.title}{head}"
            )
    else:
        lines.append("- （还没有任何会话）")
    lines.append("")

    lines.append(
        "## ★ 你不在任何任务里\n"
        "上面**没有当前计划、没有当前步骤、没有 stage** —— 那是因为你不需要它们，"
        "不是因为你没看到。所以：\n"
        "- 不要问「你现在在哪一步」，也不要推断「你接下来应该做第几步」；\n"
        "- 他若要改计划，让他去对应会话里说（你改不了，也不该假装能改）；\n"
        "- 想做长期整理，就走 `submit_global_update`，让他确认。"
    )
    return "\n".join(lines)


class SidebarLoop(AgentLoop):
    """侧边栏的一次对话。`AgentLoop` 的五个钩子全部换掉。

    **唯一改动的就是那五个方法** —— 循环体、工具分发、零进展检测、
    停止理由、`thinking` 累积、用量统计**逐字复用**。
    """

    def __init__(
        self,
        db: DbSession,
        client: LLMClient,
        *,
        max_steps: int = 8,
        external: ExternalToolbox | None = None,
    ) -> None:
        # `scope_id` 用那个单例常量：侧边栏只有一个，
        # 所以"它在不在"这个问题不需要查库就能回答。
        super().__init__(
            db, SIDEBAR_SCOPE_ID, client, max_steps=max_steps, external=external
        )

    # ---- 五个钩子 ---- #

    def _system_prompt(self) -> str:
        return SIDEBAR_SYSTEM_PROMPT

    def resolve_anchor(self, step_id: str | None) -> str:
        """侧边栏**没有锚点**。

        ★ 与会话侧那个"第一个没做完的步骤"的规则不同，这里不能退化成
        "返回某一个计划项"：整个系统里"讨论锚定在哪"是**上下文采用序**
        与工具授权都要读的东西，而侧边栏两者都没有。
        返回空串是一个诚实的取值 —— 它表示"不锚定任何东西"，
        而 `None` 在会话侧的含义是"没指定，你自己挑一个"。
        合并这两者会让侧边栏悄悄锚到某个会话的某一步上。
        """
        return ""

    def _open_context(self, step_id: str | None) -> tuple[str, int, str]:
        ctx = assemble_sidebar_context(self.db)
        return "", 0, render_sidebar_context(ctx)

    def _tool_schemas(self) -> list[ToolSchema]:
        # 外挂工具按**同一个作用域集合**过滤：READ 与 GLOBAL 里有 READ，
        # 所以联网搜索与 MCP 只读工具在侧边栏里**可用** —— 它们是只读的，
        # 而侧边栏缺的只是"当前会话"，不是"世界"。
        return [*tool_schemas(SIDEBAR_TOOL_SCOPES), *self._external_schemas(SIDEBAR_TOOL_SCOPES)]

    def _make_dispatcher(self) -> Dispatcher:
        return Dispatcher(
            self.db,
            self.session_id,
            scopes=SIDEBAR_TOOL_SCOPES,
            in_sidebar=True,
            external=self.external,
        )
