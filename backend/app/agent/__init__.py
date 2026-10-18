"""Agent 运行时（P6 / P6+.1）。

这个包的职责只有一件事：**把「用户说的一句话」变成一次或多次治理动作**，
并且让每一件发生过的事都能被回看。

它**不拥有**任何权威状态：

- 读，走 `global_domain.reads` 与 `session_domain.reads`；
- 写全局，**只经过** `handoff` → `Router`（ARCH-12 钉住了这条边界）；
- 写会话，走 `session_domain`（沙箱内直写，不经过 Router）。

模块分工：

| 文件 | 管什么 | 不该做的事 |
| --- | --- | --- |
| `llm.py` | 与模型的契约（`chat` 与 `stream` 两条路，含确定性替身） | 不认识工具，不认识数据库 |
| `events.py` | 讨论过程的**事件契约**（SSE 帧的形状与语义） | 不产出事件、不做判定 |
| `tools.py` | 14 个工具的**声明**（作用域、说明、参数 schema） | 不执行任何工具 |
| `dispatch.py` | 执行工具调用、强制作用域、记录轨迹 | 不调 Router（交给 handoff） |
| `handoff.py` | 沙箱 → `UpdateIntent` → Router 的**唯一**映射 | 不拼 prompt |
| `loop.py` | ReAct 循环、步数预算、`stop_reason` | 不解释工具结果 |

`llm.py` 的两个方法（`chat` / `stream`）与 `loop.py` 的一份循环体是配对的：
**两条路共用一个 `_walk`**，差别只压在 `AgentLoop._ask` 里。
这不是为了省代码 —— 循环体写两份，就会在"一条路上修了、另一条忘了"的时候
安静地分叉，而分叉的表现是**界面看到的与库里存的不一样**。
`tests/agent/test_stream.py` 用"两条路结果逐字段相等"钉住它。
"""
