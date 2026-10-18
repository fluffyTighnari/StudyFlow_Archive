"""讨论过程的**事件契约** —— 后端 SSE 与前端之间的唯一新增约定。

## 为什么要有这个模块，而不是在端点里拼字符串

因为"界面看到的"与"库里存的"必须是两件**可核对**的事，而不是同一件事。

循环跑完之后会把回复与工具轨迹写进 `chat_message`；而流式路径上，
用户在回答写完之前就已经看到了它的前半段。于是就有了两个来源：
**增量拼起来的字符串**与**收官帧里的 `content`**。
它们正常时相等，异常时（丢帧、断开、供应商重放）不等 ——
而不等的时候若有人按"界面看到的东西"做判定，系统就会按一个
从未被验证过的值行事。

所以这里的规矩是：

- `token` / `thinking` 是**显示用**的。前端只能拿它们渲染，
  **任何判定都不许用**（包括"模型说完了没有"）。
- `done` 才是**判定用**的收官事件：它携带的就是那次讨论最终的
  `reply` / `stop_reason` / `tool_trace` —— 也就是写进库里的那份。

这条规矩在代码里的形态是：`AgentLoop.run_stream` 的返回值与
`AgentLoop.run` 的返回值是**同一个对象类型**（`DiscussionResult`），
两条路共用一个循环体。前端拿到的 `done` 只是它的序列化。

## 事件类型

| 类型 | 何时 | 前端的义务 |
|---|---|---|
| `stage` | 进入一个新阶段（读上下文 / 等模型 / 执行工具） | 显示"它在做什么"。这是**唯一**能让长静默期不显得像卡死的信号 |
| `thinking` | 模型吐出一段思考增量 | 与正文**视觉上必须可区分**，且不并入回答。它可能永远不出现（非思考型模型） |
| `token` | 模型吐出一段正文增量 | 追加到当前气泡 |
| `tool_call` | 循环要执行一次工具调用 | 显示工具名与参数（参数是模型给的，可以折叠） |
| `tool_result` | 该调用有了结果 | 显示 `status`（`success`/`empty`/`rejected`/`error`）。**`rejected` 必须与成功明显不同** —— 那是"它想做但被拦住了"，是本项目最该被看见的一类事 |
| `done` | 讨论结束 | 用它的 `reply` 覆盖增量拼接的结果（**后者只是显示，前者才是结论**） |
| `error` | 流本身出错（连不上模型、连接断在半路） | 显示错误，并且**不要说"模型说了什么"** —— 那时没有任何结论 |

## `seq` 不是装饰

每条事件带一个从 1 开始、**在一条流内严格递增**的序号。
前端据此可以判断"有没有丢帧"——而丢帧是必须被发现的：
一次断线重连后若缺了中段，拼接出来的回答会读起来通顺但语义残缺，
没有任何东西会提示用户。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    STAGE = "stage"
    THINKING = "thinking"
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


class StageName(StrEnum):
    """`stage` 事件的取值域。

    写成枚举而不是让各处随便传字符串：前端要按它决定显示哪一句话，
    而**拼错的阶段名会让界面安静地不显示任何东西**（switch 落到 default），
    看起来就像"后端没发"。
    """

    #: 已读完上下文包，准备开始。`data.anchor` = 讨论锚定的 step_id。
    READ_CONTEXT = "read_context"
    #: 第 N 轮，正在等模型。`data.round` = 轮次。本地大模型这一等可能几十秒，
    #: 而**这是唯一会提前到达的信号** —— 没有它，界面在第一次出字之前
    #: 只能一直"正在连接"。
    WAIT_MODEL = "wait_model"
    #: 正在执行 K 个工具调用。`data.count` / `data.tools`。
    RUN_TOOLS = "run_tools"


@dataclass(frozen=True)
class AgentEvent:
    """一条事件。`seq` 由产出方（循环）分配，见模块文档。"""

    type: EventType
    seq: int
    #: 这是第几步（循环的轮次，从 1 开始）。`done` 时等于总的步数。
    step: int
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """SSE 帧。

        `ensure_ascii=False`：中文在这里会以原样 UTF-8 发出，帧更短也更可读。
        列名与 JSON 之间只隔一个空格是 SSE 规定的可选项 —— 加上它，
        `curl` 看原始流时人能读。

        **不加 `retry:`**：那条指令是给 `EventSource` 自动重连用的，
        而我们用的是 `fetch` + ReadableStream（原生 `EventSource` 只支持 GET，
        而讨论要带 body）。写一条不会生效的指令，比不写更让人困惑。
        """
        payload = json.dumps(
            {"seq": self.seq, "step": self.step, **self.data},
            ensure_ascii=False,
        )
        return f"event: {self.type.value}\ndata: {payload}\n\n"
