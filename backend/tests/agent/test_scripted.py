"""`ScriptedPlanChange` —— 演示替身自己的规矩。

它存在的理由是"把模型行不行与管子通不通分开"（见模块 docstring）。
所以这个文件盯的就是那两件事：

1. **它真的能驱动闭环**：产出的调用必须过 `propose_plan_change` 的入口条件
   （`target` 是真实存在的 step_id、`suggested_change` 带 `steps`），
   而且要能让计划产生新版本 —— 否则"演示能跑"是一句空话；
2. **它经得起被点很多次**：第二轮、第五轮的行为必须和第一轮一样。
   它起初是**一次性**的（剧本取完就只剩一句话），于是第二次点击时
   工具调用消失了 —— 而那看起来像"闭环又断了"，会让人去查错地方。
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.agent.llm import ChatMessage
from app.agent.scripted import ScriptedPlanChange

STEPS = [
    {"step_id": "ST-001", "action": "通读 unit 1 词表", "output": "生词表"},
    {"step_id": "ST-002", "action": "精读 unit 1 例句", "output": "10 个例句"},
]

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _client() -> ScriptedPlanChange:
    return ScriptedPlanChange(
        target="ST-001",
        steps=STEPS,
        change="第 1 步分两天",
        user_view="太长了，分两天",
    )


def _tool_calls(reply) -> list[str]:
    return [item.name for item in reply.tool_calls]


def test_first_call_asks_to_change_the_plan() -> None:
    reply = _client().chat([ChatMessage.user("太长了")])
    assert _tool_calls(reply) == ["propose_plan_change"], (
        "替身没有调 propose_plan_change —— 那它证明不了闭环能通"
    )


def test_the_call_carries_a_real_target_and_a_full_step_list() -> None:
    """入口条件要真的满足：`target` 是传进来的那个 id，`steps` 是整份。"""
    reply = _client().chat([ChatMessage.user("太长了")])
    args = reply.tool_calls[0].arguments

    assert args["target"] == "ST-001", "target 不是调用方给的真实 step_id"
    assert len(args["suggested_change"]["steps"]) == len(STEPS), (
        "suggested_change.steps 不是整份 —— 合并规则会按「省略 = 保留」处理，"
        "少给的会被保留，于是演示看起来'只改了一部分'"
    )


def test_every_round_starts_from_a_fresh_script() -> None:
    """**第二轮起也要能改计划** —— 这是它被改成可重复的原因。

    一轮讨论恰好问两次（先工具调用、再收尾）。所以第 1、2 次调用对应第一轮，
    第 3、4 次对应第二轮。
    """
    client = _client()
    rounds = []
    for _ in range(3):
        first = client.chat([ChatMessage.user("太长了")])
        second = client.chat([ChatMessage.user("太长了")])
        rounds.append((_tool_calls(first), second.content))

    assert [item[0] for item in rounds] == [["propose_plan_change"]] * 3, (
        f"某一轮没有工具调用：{rounds}"
    )
    assert all("改好了" in content for _calls, content in rounds), (
        f"某一轮没有收尾回复：{rounds}"
    )


def test_the_new_action_does_not_stack_prefixes() -> None:
    """幂等：文案由**原文**生成，不是在当前文案上再拼一层。

    拼前缀的表现是「分两天：分两天：分两天：…」—— 看起来像界面重复渲染。
    """
    client = _client()
    # 第一轮的两条都要走完，第二条（收尾）**没有工具调用** ——
    # 直接连着取两次会 IndexError，而那是我第一版测试自己犯的错。
    first = client.chat([ChatMessage.user("x")]).tool_calls[0].arguments
    client.chat([ChatMessage.user("x")])
    second = client.chat([ChatMessage.user("x")]).tool_calls[0].arguments

    action_first = first["suggested_change"]["steps"][0]["action"]
    action_second = second["suggested_change"]["steps"][0]["action"]
    assert action_first == action_second, f"两次给的文案不一样：{action_first} / {action_second}"
    assert action_first.count("分两天") == 1, f"前缀叠起来了：{action_first}"


def test_other_steps_are_passed_through_unchanged() -> None:
    """只改第 1 步，其余原样带着 —— 不然会顺手把别人的计划页改掉。"""
    reply = _client().chat([ChatMessage.user("x")])
    steps = reply.tool_calls[0].arguments["suggested_change"]["steps"]

    assert steps[1]["step_id"] == "ST-002"
    assert steps[1]["action"] == "精读 unit 1 例句"


def test_nothing_under_app_imports_the_demo_double() -> None:
    """**演示替身不许出现在运行时路径里。**

    它住在 `app/agent/` 是因为它要满足 Agent 的协议（和 `FakeLLM` 同理），
    但它是给人演示用的：一旦哪个运行时模块 import 了它，
    "用真模型还是用替身"就变成了代码里的既成事实，而不是启动参数 ——
    那时线上跑的是替身也不会有人发现，因为**它会答得像模像样**。

    所以这条测试盯的不是"它在不在"，而是"谁在用"。
    """
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == APP_DIR / "agent" / "scripted.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "agent.scripted"
            ):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno}")

    assert offenders == [], (
        "运行时路径里出现了演示替身（它只该被 tools/ 引用）：" + ", ".join(offenders)
    )


def test_the_demo_entry_is_the_only_user_of_the_double() -> None:
    """反面：**必须有人真的在用它** —— 否则上一条测试会因为没人用而永远通过。

    这是本项目反复吃过的亏：一条"某处不该有 X"的检查，
    在 X 被删掉之后依然全绿，于是它保护的东西早就不存在了。
    """
    tools_dir = APP_DIR.parent / "tools"
    users = [
        path.name
        for path in sorted(tools_dir.rglob("*.py"))
        if "agent.scripted" in path.read_text(encoding="utf-8")
    ]

    assert users == ["serveui.py"], (
        f"应该恰好是 tools/serveui.py 在用演示替身，实际：{users}"
    )


# --------------------------------------------------------------------------- #
# 它兼任判官（P9）
# --------------------------------------------------------------------------- #


def _judge_prompt(guide: list[str], answer: str) -> str:
    from app.benchmark.judge import build_prompt

    return build_prompt(guide, answer)


GUIDE = ["要点一：主线是 A", "要点二：事实是 B", "要点三：限定是 C"]
ANSWER = "要点一：主线是 A；要点二：事实是 B；限定我忘了写。"


def test_a_judge_call_does_not_eat_the_discussion_script() -> None:
    """★ 这是它必须**分开处理**判官调用的全部理由。

    讨论剧本是按**次数**发的（一段两回复）。一次判官调用若吃掉一格，
    下一次点计划项时替身就"失忆"了 —— 而那看起来像闭环断了，
    排查方向完全错（会去查前端、查路由，而真正的原因在这里）。
    """
    client = _client()
    client.chat([ChatMessage(role="user", content=_judge_prompt(GUIDE, ANSWER))])
    # 判官调用之后，第一轮讨论的两条剧本**一条都没少**
    first = client.chat([ChatMessage(role="user", content="帮我改计划")])
    second = client.chat([ChatMessage(role="user", content="工具结果回灌")])
    assert _tool_calls(first) == ["propose_plan_change"]
    assert _tool_calls(second) == []


def test_the_judge_reply_quotes_the_answer_from_the_prompt() -> None:
    """它引用的必须是**提示词里那段答案的原文** —— 否则"引文核对"没被真的走过。"""
    from app.benchmark.judge import parse_verdict

    client = _client()
    reply = client.chat([ChatMessage(role="user", content=_judge_prompt(GUIDE, ANSWER))])
    verdict = parse_verdict(reply.content or "", guide_size=len(GUIDE), answer=ANSWER)
    assert verdict.ok
    assert verdict.verified_hits, "替身至少要给出一条**真能核对过**的命中"


def test_the_judge_reply_always_carries_one_fabricated_hit() -> None:
    """★ 它是唯一能证明"引文核对真的在跑"的办法。

    一个只回真实引用的替身，在"代码根本没核对引文"的实现上同样会满分 ——
    于是那条最重要的防线从没被验过，而下场是编造的引用被当成命中。
    """
    from app.benchmark.judge import parse_verdict
    from app.agent.scripted import FABRICATED_QUOTE

    client = _client()
    reply = client.chat([ChatMessage(role="user", content=_judge_prompt(GUIDE, ANSWER))])
    verdict = parse_verdict(reply.content or "", guide_size=len(GUIDE), answer=ANSWER)
    assert any(hit.quote == FABRICATED_QUOTE for hit in verdict.downgraded)
    assert FABRICATED_QUOTE not in ANSWER


def test_a_non_judge_prompt_is_left_to_the_script() -> None:
    """认不出判官调用时必须**原样交回剧本**，不能自己编一个回复。"""
    client = _client()
    reply = client.chat([ChatMessage(role="user", content="帮我看看这个计划")])
    assert _tool_calls(reply) == ["propose_plan_change"]


def test_the_double_declares_a_model_name() -> None:
    """判分要记 `judge_model` —— 没有它，"两次判出不同分"无法追。"""
    assert _client().model
