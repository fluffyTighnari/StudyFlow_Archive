"""渲染上下文时，没有锚点就说没有锚点 —— 不要 `None.step_id`。

`_render_context` 原来直接展开 `ctx.step.step_id`（必填）。现在 `step` 可空，
于是这一段要分支：

- 有计划有锚点：照旧写「当前这一步」；
- 没有计划：写「这个会话还没有计划」，并明确告诉模型：
  可以先聊，用户想的话也可以当场排一版（`create_task_plan`）。

同时把上面那段"用户没点任何计划项 → `propose_plan_change` 用不了"改成新政策：
现在**引得出他这句话就行**，不必先去点界面。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "app" / "agent" / "loop.py"
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    assert count == 1, f"{label}：锚点出现 {count} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


swap(
    """        lines += [
            "## 用户此刻的动作：**他没有点任何计划项**，只是直接说话",
            "",
            "因此 `propose_plan_change` 现在**用不了**。"
            "想改计划结构只有两条路：有落库的证据（检查点判 fail / stage 里悬着想法）"
            "走 `update_task_plan`，否则把建议讲给他听、告诉他点开哪一项就能改。",
            "",
        ]""",
    """        lines += [
            "## 用户此刻的动作：**他没有点任何计划项**，只是直接说话",
            "",
            "这**不影响改计划**：他说了要改哪一步，把**他那句原话**放进 `user_quote`"
            "（必须在他说过的这一句里逐字出现）就能改。有落库的证据"
            "（检查点判 fail / stage 里悬着想法）也一样能改。"
            "两条都没有、他也确实没说过 ⇒ 那就别改，把建议讲给他听。",
            "",
        ]""",
    "提示：没点计划项也能改，只要引得出原话",
)

swap(
    """    lines += [
        f"## 当前这一步（{ctx.step.step_id}）",
        f"- 做什么：{ctx.step.action}",
        f"- 产出：{ctx.step.output or '（没写）'}",
        f"- 状态：{ctx.step.status.value}",
    ]""",
    """    if ctx.step is None:
        # 没有计划：不能假装有一步。说清楚现状，并把"可以当场排一版"摆在手边。
        lines += [
            "## 这个会话还没有计划",
            "",
            "所以这次讨论**没有锚定到任何一步**。可以照常聊：先把他的目标、现状、"
            "卡点问清楚；他觉得合适时，你可以用 `create_task_plan` 当场排一版"
            "（起草期就能排，不需要先"激活"）。",
        ]
    else:
        lines += [
            f"## 当前这一步（{ctx.step.step_id}）",
            f"- 做什么：{ctx.step.action}",
            f"- 产出：{ctx.step.output or '（没写）'}",
            f"- 状态：{ctx.step.status.value}",
        ]""",
    "渲染：没有计划时如实说明",
)

PATH.write_text(text, encoding="utf-8")
print("✅ loop.py 渲染改完")
