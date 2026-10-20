"""「这句话真的在原文里吗」—— 引文核对的**唯一实现**。

## 为什么它住在 `app.core`

因为有两个互不相识的调用者：

1. `app/agent/handoff.py` 的「用户真的说过这句话吗」（`R3 + stated` 的第二道门）；
2. `app/benchmark` 的要点判官 —— 它声明的每条命中都要附上用户答案里的原话，
   而那段原话必须真的在答案里。

**两处各写一遍是不行的**：它们必须给出**完全一样**的判断，
否则会出现"路由层认了某句引用、判分层不认"这种没人能解释的分歧。
而 `app.core` 是最底层（ARCH-04：它不得 import 任何上层域），
两边都能 import 它，谁也不用认识谁。

## 这条核对器的边界（很重要）

它比的是**「原文里有没有这段话」**，不是**「模型复述得像不像」**。
所以比对前把空白全部去掉（模型转述时在标点/换行上有出入是常态，
而空白不承载含义），但**任何一个字的差别仍然对不上**。

宁可让它在措辞差异上失败（降级为"未命中"/"需确认"），
也不能放松到"意思像就算" —— 后者正是本项目一贯要防的那件事：
系统用一个理由解释自己的行为，而那个理由无法核对。
"""

from __future__ import annotations

#: 引文至少要有这么多字。
#:
#: 少于它，几乎任何一句原文都能「包含」这段引用，核对就没有意义了 ——
#: 一个两个字的关键词（「记忆」「效率」）在任何答案里都找得到，
#: 于是它会把每条命中都放行。**下限不是严谨，是这条校验能不能成立的前提。**
MIN_QUOTE_CHARS = 4


def normalize_for_quote(text: str) -> str:
    """比对引文时**去掉所有空白**。理由见模块文档。"""
    return "".join(text.split())


def contains_quote(haystack: str, needle: str, *, min_chars: int = MIN_QUOTE_CHARS) -> bool:
    """`needle` 是不是 `haystack` 里的一段连续原文。

    ★ 判据是 `in`（连续子串），不是"关键词都出现过"。
    后者会让「用户说了 A 和 B」与「用户说了 A…B 连起来的意思」分不开，
    而引文核对存在的全部意义就是把这两件事分开。
    """
    normalized = normalize_for_quote(needle)
    if len(normalized) < min_chars:
        return False
    return normalized in normalize_for_quote(haystack)


__all__ = ["MIN_QUOTE_CHARS", "contains_quote", "normalize_for_quote"]
