"""要点题的**判官**：LLM 提议命中，**代码核对引文**。

## 为什么判分要用 LLM，而这里还要写这么多字

`key_points` 题没有唯一答案（「用三句话概括这篇材料」），所以判法必须选一种。
用户已裁定：**用 LLM 判，并记录 `scoring_method`**。

但"记一个判法名字"是不够的。LLM 判分恰好是本项目一贯要防的那种
「系统用一个理由解释自己的行为」—— 它给一个数，然后（如果被问）编一个理由。
所以判官**不许只给一个数**，它必须交出两样东西：

1. **逐条声明命中了评分指南的第几条**（只能从给定的条目里选，不许自己新增要点）；
2. **每条命中附上用户答案里的原话**。

然后由**代码**核对第 2 项：那段原话必须真的在用户答案里（`app.core.quotes`）。
**核对不过的命中，降级为未命中** —— 与 G-01b 同一条口径：降级不是拒绝。

于是判分链条变成：`LLM 提议命中 → 代码核对引文 → 分数 = 10 × 核实过的命中数 / 要点总数`。
那个 0-10 的分因此不再是一个不可检查的断言，而是一个**能追到原话**的结论。

## ★ 判官坏了 ≠ 0 分

模型没返回合法 JSON、或者返回的结构不对时，**不能当作"一条都没命中"** ——
那会让一次判官故障静默地把用户的分数打成 0。

所以这里返回的是 `JudgeVerdict(ok=False, ...)`，调用方据此把该题标成
**「没测到」（`item_score=None`）**。这条与 §5.2 里
"没卡住不是 0 分，是这题没测到"是同一条口径。

## 为什么"怎么够到模型"不在这里

因为 `app.benchmark` 是 L3''、`app.agent` 是 L4 —— **低层不能 import 高层**。
所以判官只收一个 `ask(prompt) -> str` 的回调，由调用方（API 层）把真实
LLM 客户端接上去。这样这一层既不认识 `LLMClient`、也不认识 HTTP，
但它认识**判分这件事本身**。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.core.quotes import contains_quote, normalize_for_quote

#: 判官必须交出的结构。用 JSON 而不是自由文本：
#: 自由文本要靠正则去猜"它到底命中了没有"，而猜错的方向恰好是**放行**。
SYSTEM_PROMPT = """你是一个要点题判官。你的任务不是评分，而是**逐条判断哪些要点被答到了**。

铁律：
1. 你只能从给定的评分指南条目里选，**不许新增要点、不许改写要点**。
2. 每一条你判定「答到了」的要点，必须附上**用户答案里的一段连续原话** ——
   原话必须逐字出现在用户答案中（标点与空白可以不同，**字不能不同**）。
3. 拿不准的那一条就**不要**判定为答到。你的判断会被程序逐条核对引文，
   核对不过的命中会被降级为未命中 —— 编一段引用只会浪费一次判定。

只输出一个 JSON 对象，不要任何别的文字：
{"hits": [{"no": 1, "quote": "用户答案里的原话"}]}
没答到任何要点时输出 {"hits": []}。"""


#: 提示词里的标记。`parse_prompt` 靠它认出"这是一次判官调用" ——
#: 确定性替身要用这个判断，而不是"猜这段文字像不像判分请求"。
JUDGE_PROMPT_MARKER = "## 评分指南"
ANSWER_MARKER = "## 用户答案"
JUDGEMENT_MARKER = "## 你的判断"


def build_prompt(guide: Sequence[str], answer: str) -> str:
    """给人看也给人核的提示词。编号从 1 开始。"""
    numbered = "\n".join(f"{index}. {point}" for index, point in enumerate(guide, start=1))
    return (
        f"{JUDGE_PROMPT_MARKER}（共 {len(guide)} 条）\n{numbered}\n\n"
        f"{ANSWER_MARKER}\n{answer}\n\n"
        f"{JUDGEMENT_MARKER}\n只输出 JSON：{{\"hits\": [{{\"no\": 1, \"quote\": \"…\"}}]}}"
    )


def parse_prompt(prompt: str) -> tuple[list[str], str] | None:
    """`build_prompt` 的逆：从提示词里取回 `(评分指南, 用户答案)`。

    不是产品逻辑需要它，而是**替身与测试需要**：一个确定性的判官替身
    必须真的读懂"指南是哪几条、他答了什么"，否则它只能回放一句写死的 JSON，
    而那样"引文是从答案里取的"这件事从来没被验证过。

    有了它，替身给出的引用**来自提示词里的答案原文** ——
    于是端到端那一步能证明两件事：判官确实收到了答案，
    以及**代码确实核对了引用**（因为替身会故意夹一条编的引用进去）。

    认不出就返回 `None`（不是抛异常）：调用方拿它当"这次不是判官调用"用。
    """
    if JUDGE_PROMPT_MARKER not in prompt or ANSWER_MARKER not in prompt:
        return None
    head, _, rest = prompt.partition(ANSWER_MARKER)
    answer, _, _tail = rest.partition(JUDGEMENT_MARKER)
    lines = head.splitlines()
    guide: list[str] = []
    for line in lines:
        stripped = line.strip()
        dot = stripped.find(". ")
        if dot > 0 and stripped[:dot].isdigit():
            guide.append(stripped[dot + 2 :])
    if not guide:
        return None
    return guide, answer.strip()


#: 从回复里抠出第一个 JSON 对象。
#:
#: ★ 为什么允许"回复里带别的字"：真实模型经常先写一句「好的，我来判断」，
#: 而**为这件事让整次判分失败是不划算的**。抠 JSON 与"容忍任意格式"不同 ——
#: 抠不到就是判官坏了（走 `ok=False`），不是"当作没命中"。
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class JudgeHit:
    """判官声明的一条命中，以及**核对之后的结论**。"""

    no: int
    quote: str
    verified: bool
    #: 没通过核对时，代码给出的原因（要能直接塞进 `judge_note` 给人看）。
    why: str = ""


@dataclass(frozen=True)
class JudgeVerdict:
    """一次要点判定的结果。`ok=False` 表示**判官坏了，不是答得差**。"""

    ok: bool
    hits: tuple[JudgeHit, ...] = ()
    #: 模型原话（存档用）。**必须留** —— 否则"它当时看到了什么"无法复查。
    raw: str = ""
    model: str | None = None
    error: str = ""
    #: 判官自己多报的条目号（不在指南范围内）—— 单独留着，
    #: 因为它是"模型在编要点"的直接证据，而不只是一次普通失败。
    out_of_range: tuple[int, ...] = field(default=())

    @property
    def verified_hits(self) -> tuple[JudgeHit, ...]:
        return tuple(hit for hit in self.hits if hit.verified)

    @property
    def downgraded(self) -> tuple[JudgeHit, ...]:
        return tuple(hit for hit in self.hits if not hit.verified)


def parse_verdict(raw: str, *, guide_size: int, answer: str) -> JudgeVerdict:
    """把模型回复解析成**核对过**的判定。纯函数，不碰模型。

    这里的每一步都刻意**不宽容**：
    - 抠不出 JSON → `ok=False`（判官坏了）；
    - `hits` 不是列表 → `ok=False`；
    - 条目号越界 → 记进 `out_of_range`，**这条不计入命中**（模型在编要点）；
    - 引文核对不过 → `verified=False`（降级，不是拒绝）。
    """
    match = _JSON_OBJECT.search(raw or "")
    if match is None:
        return JudgeVerdict(ok=False, raw=raw, error="判官没有给出 JSON 对象")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return JudgeVerdict(ok=False, raw=raw, error=f"判官的 JSON 解析失败：{exc}")
    if not isinstance(payload, dict):
        return JudgeVerdict(ok=False, raw=raw, error="判官给的 JSON 不是一个对象")
    raw_hits = payload.get("hits")
    if not isinstance(raw_hits, list):
        return JudgeVerdict(
            ok=False, raw=raw, error=f"判官的 hits 不是列表：{type(raw_hits).__name__}"
        )

    hits: list[JudgeHit] = []
    out_of_range: list[int] = []
    seen: set[int] = set()
    for entry in raw_hits:
        if not isinstance(entry, dict):
            continue
        no = entry.get("no")
        quote = entry.get("quote")
        if not isinstance(no, int) or not isinstance(quote, str):
            continue
        if not 1 <= no <= guide_size:
            out_of_range.append(no)
            continue
        if no in seen:
            # 同一条要点报两次：**只认第一次**。重复计数会把分母算错 ——
            # 分母是"要点总数"（题目的属性），不是"模型报了几条"。
            continue
        seen.add(no)
        verified = contains_quote(answer, quote)
        hits.append(
            JudgeHit(
                no=no,
                quote=quote,
                verified=verified,
                why=""
                if verified
                else f"引文在用户答案里找不到（或少于 4 字）：「{normalize_for_quote(quote)[:40]}」",
            )
        )
    return JudgeVerdict(ok=True, hits=tuple(hits), raw=raw, out_of_range=tuple(out_of_range))


def judge_key_points(
    guide: Sequence[str],
    answer: str,
    *,
    ask: Callable[[str], tuple[str, str | None]],
) -> JudgeVerdict:
    """问一次判官，然后**核对它说的每一句话**。

    `ask(prompt) -> (模型原话, 模型名)`。把"怎么够到模型"留给调用方，
    见模块文档最后一节。
    """
    try:
        raw, model = ask(build_prompt(guide, answer))
    except Exception as exc:  # noqa: BLE001 —— 模型侧的任何故障都只是"判官坏了"
        return JudgeVerdict(ok=False, error=f"调用判官失败：{exc}")
    verdict = parse_verdict(raw, guide_size=len(guide), answer=answer)
    return JudgeVerdict(
        ok=verdict.ok,
        hits=verdict.hits,
        raw=verdict.raw,
        model=model,
        error=verdict.error,
        out_of_range=verdict.out_of_range,
    )


def verdict_note(verdict: JudgeVerdict) -> str:
    """给 `judge_note` 的一句话 —— 人看的那一句。"""
    if not verdict.ok:
        return f"判官没有给出可用的判定：{verdict.error}"
    parts = [f"命中 {len(verdict.verified_hits)}/{len(verdict.hits) or 0} 条经核对认可"]
    if verdict.downgraded:
        parts.append(
            "降级为未命中："
            + "；".join(f"第 {hit.no} 条（{hit.why}）" for hit in verdict.downgraded)
        )
    if verdict.out_of_range:
        parts.append(
            "★ 判官报了指南里没有的条目："
            + "、".join(str(no) for no in verdict.out_of_range)
            + "（在编要点）"
        )
    return "；".join(parts)


__all__ = [
    "ANSWER_MARKER",
    "JUDGE_PROMPT_MARKER",
    "JUDGEMENT_MARKER",
    "SYSTEM_PROMPT",
    "JudgeHit",
    "JudgeVerdict",
    "build_prompt",
    "judge_key_points",
    "parse_prompt",
    "parse_verdict",
    "verdict_note",
]
