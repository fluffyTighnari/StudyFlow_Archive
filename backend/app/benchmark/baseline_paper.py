"""基线测卷 v0.1 —— **卷面内容与客观题核对**。

## 为什么它不在 `items/` 题库里

题库（`app.benchmark.items`）面向的是**抽样施测**：一次运行只管一个变量、
一个方法，题目按难度直方图抽。这份基线卷是**固定卷**：A/B1–B5/C1–C2/D
按顺序走完，一次覆盖 11 个变量的多个格子，卷面本身带着分段、计时与
"这一段测什么"的说明。两者装进同一种 JSON，会让题库的抽样规则
被迫理解"章节"这种它不需要的概念。

## 三条纪律

1. **答案不出服务端**。`public_paper()` 剥掉 `answer` 与 `answers`；
   卷面上只出现题干、选项、材料。B1/B2 的选择题如果泄题，测到的
   不是提取与推理。
2. **只核对能核对的东西**。客观题（B1、B2-2..6、B3 材料内 5 项）由代码核对；
   三句话概括、求助经过、决策理由、组织提纲、复盘四要素**存原文、不自动打分**
   —— 按卷面 §5 的口径，它们要人/判官事后判，编一个分数比空着更糟。
3. **没采到 ≠ 0**。B3 的"第一次外部动作"没记就是 `None`；B5 跳过是
   一个**有效答案**（"可选任务"），不是 0 秒。两者在结果里分开说。
"""

from __future__ import annotations

import copy
import re
from typing import Any

PAPER_ID = "BASE-LANG-CS-01"
PAPER_VERSION = "v0.1"

#: 客观题答案。**只在服务端**。
B1_ANSWERS: dict[int, str] = {
    1: "B",
    2: "A",
    3: "B",
    4: "B",
    5: "B",
    6: "B",
    7: "A",
    8: "B",
    9: "C",
    10: "B",
    11: "A",
    12: "A",
    13: "C",
    14: "B",
    15: "C",
}

B2_ANSWERS: dict[int, str] = {2: "B", 3: "C", 4: "B", 5: "B", 6: "C"}

#: B3 材料内能查到的格子（1–4、7）。第 5、6 格材料里没有 ——
#: 它们**不计对错**：这道题测的是"发现缺数据之后多久做外部动作"。
B3_EXPECTED: dict[int, tuple[str, ...]] = {
    1: ("-k", "k 后缀", "k后缀"),
    2: ("ne", "后置词 ne"),
    3: ("ko", "后置词 ko"),
    4: ("澳大利亚昆士兰", "昆士兰", "澳大利亚"),
    7: ("5", "五个", "5 个", "五种"),
}

B3_MISSING = (5, 6)

#: C2 的 12 条事实里，与"声调语言"无关的候选（卷面说明写的是 3 条）。
#: ★ 这份卷子只**记录**作答：源文档说"有 3 条无关"，但逐条核对只能确定
#: ⑨（汉语量词）与 ⑩（韩语敬语）两条。少的那一条不在代码里编一个出来 ——
#: 编出来的评分键会让下一次真的判分时对不上用户的答案。
C2_OBVIOUSLY_UNRELATED = (9, 10)


def _mcq(no: int, stem: str, options: list[str]) -> dict[str, Any]:
    return {
        "no": no,
        "stem": stem,
        "options": [
            {"key": "ABCD"[index], "text": text} for index, text in enumerate(options)
        ],
    }


B2_MATERIAL = """### 分裂作格：同一种语言里为什么会同时存在两套对齐方式

研究语言时有一个常见误解：一种语言属于某个"类型"，就从头到尾都按这个类型走。作格语言是最常被误解的一类。

先看两种基本对齐。在**主格-宾格**系统里，不及物动词的唯一论元（S）和及物动词的施事（A）用同一种标记，受事（O）另行标记。汉语普通话、英语都属此类。在**作格-绝对格**系统里，同形的一对换成了 S 与 O —— 它们都用"绝对格"，而 A 单独用"作格"。巴斯克语（Basque）是欧洲少数保留作格系统的语言，其及物句里施事带 `-k` 后缀，受事与不及物主语则不带标记。

如果一种语言只有一套对齐，事情很简单。麻烦在于**很多语言同时使用两套，并且按条件切换**，这被称为**分裂作格（split ergativity）**。印地语（Hindi）是常被引用的例子：在完成体（perfective）里，及物动词的施事要带后置词 `ne`，受事不带标记，形成作格-绝对格；而在未完成体（imperfective）里，同一个人说同一件事，施事改用普通主格形式，受事带 `ko`，回到了主格-宾格。也就是说，**体（aspect）**这一语法范畴决定了语言当场走哪一套对齐。

分裂的条件不止"体"一种。第二类常见条件是**名词短语的生命度与人称**。南岛语系与部分印欧语里存在一种"层级分裂"：第一、二人称代词（说话人与听话人）倾向使用主格-宾格，而第三人称、尤其无生名词倾向使用作格-绝对格。这条倾向被概括为"人称-生命度层级"，但它**不是普遍规律**——有语言完全按体分裂而对人称不敏感，也有语言在同一个体里按人称分裂。第三类条件是**小句类型**：主句与从句可以各用一套对齐，这在澳大利亚与高加索的一些语言里都有报道。

比形态分裂更难处理的是**句法分裂**。前面说的作格都是"标记层面的"：格后缀长在谁身上。而有的语言在标记上可能看起来是主格-宾格，但在**句法操作**（如并列小句的成分删略、关系化、反身代词约束）上遵循 S=O 的作格模式。迪尔巴尔语（Dyirbal，澳大利亚昆士兰）是经典案例：两个小句并列时，被删略的成分按"绝对格"的身份来认同，而不按施事身份；一个及物句与一个不及物句并列时，能被删掉的是受事，而不是施事。

这里有一个容易被忽略的限定：**形态作格与句法作格不必一致**。一种语言可以形态上是明确的作格语言，句法上却按主格-宾格运作；也可以反过来。凡是"某语言是作格语言"这类一句话结论，都需要追问：**是在哪个层面作格**。

最后需要说清一个方法论上的界限。分裂作格的判据依赖对**格标记**分布的观察，而许多语言（包括汉语）根本不用格后缀表达这些关系，而是靠语序与介词。因此"汉语是不是作格语言"这类问题，答案取决于你用什么判据；不同判据会给出不同答案，而这不是研究者的分歧，是判据本身在度量不同的东西。语言学里这类"先定判据、再谈分类"的要求，与测量学里"先定仪器、再谈读数"是同一条规矩。"""

B5_CODE = """def report_a(rows):
    out = []
    for r in rows:
        if r.get("state") != "done":
            continue
        if r.get("score") is None:
            continue
        out.append({"id": r["id"], "score": r["score"]})
    return out

def report_b(rows):
    out = []
    for r in rows:
        if r.get("state") != "done":
            continue
        if r.get("score") is None:
            continue
        out.append({"id": r["id"], "score": r["score"] * 2})
    return out"""

C2_FACTS = [
    "声调语言里，音高的高低升降能区分词义，而不只是表达语气。",
    "汉语普通话有四个声调加一个轻声。",
    "约 60%–70% 的语言是声调语言，分布集中在东南亚、撒哈拉以南非洲与中美洲。",
    "日语的“高低音”（pitch accent）只标记词与词之间的对比，能产性远低于典型声调。",
    "声调常常从音段特征的丢失中产生：辅音的清浊对立消失后，音高差异被“接管”为声调。",
    "越南语的声调与韵尾类型有系统性对应。",
    "非洲的很多声调语言用声调承担语法功能（如时态、人称），而不仅是词汇区分。",
    "英语的重音是“重读”，不区分词义，因此不算声调。",
    "汉语的量词系统要求数词与名词之间必须有量词。",
    "韩语的敬语系统按说话对象改变动词形态。",
    "声调的产生（tonogenesis）可以在一两代人的时间内完成，有语言在几十年内新增了调类。",
    "声调语言与非声调语言的边界并非二值：存在只有两个调、且只在少数词上对立的语言。",
]


def paper() -> dict[str, Any]:
    """完整卷面（含答案）。只有服务端评分与校验使用。"""
    return copy.deepcopy(PAPER)


PAPER: dict[str, Any] = {
    "paper_id": PAPER_ID,
    "version": PAPER_VERSION,
    "title": "基线测卷 · 第一次",
    "subtitle": "LANG 陌生 / CS 熟悉 · 全面卷",
    "duration_min": 60,
    "domain": {"familiar": "CS", "interested": "LANG"},
    "intro": [
        "从上往下，一段一段来。不要提前看后面的内容 —— 尤其 B2 的题：提前看到题，B2 测的就不是提取了。",
        "每段开始前看一眼计时条，到点就停，不要补。做不完也是数据。",
        "答案逐段存档；中途关掉页面，回来接着答。",
    ],
    "sections": [
        {
            "id": "A",
            "kind": "reference",
            "kicker": "参照 · 2min",
            "title": "领域确认与背景",
            "minutes": 2,
            "intro": "这一段不产分数。它确定的是这次测量的参照：你熟悉什么、陌生什么。",
            "domain_question": {
                "prompt": "按 18 个大类，确认本次的两个领域：",
                "options": [
                    {"key": "CS", "label": "计算机科学 CS", "note": "擅长（熟悉度 ≥7，近期活跃）"},
                    {"key": "LANG", "label": "语言学与语言 LANG", "note": "不擅长（≤3）但感兴趣"},
                ],
            },
            "items": [
                {
                    "key": "lang_recent",
                    "no": "A2",
                    "prompt": "近三个月，LANG 这一块你接触过什么？",
                    "hint": "读过什么、看没看过语言学科普、学过外语语法 —— 一句就够。",
                    "multiline": False,
                },
                {
                    "key": "cs_recent",
                    "no": "A3",
                    "prompt": "近三个月，CS 上你主要在做什么？",
                    "hint": "工作 / 项目 / 刷题 / 写代码，一句就够。",
                    "multiline": False,
                },
                {
                    "key": "goal",
                    "no": "A4",
                    "prompt": "这次基线你希望它回答什么？",
                    "hint": "一个问题就行，别写“全面了解自己”。复盘会拿它和结果对一遍。",
                    "multiline": True,
                },
            ],
        },
        {
            "id": "B1",
            "kind": "mcq",
            "kicker": "专注 · 10min · 不打断",
            "title": "逻辑与类型推理",
            "minutes": 10,
            "domain": "CS（熟悉）· 中偏上 · 单选",
            "intro": "从计时开始连续做 10 分钟。中间不要切窗口、不要查资料、不要看手机。到点就停，做不完正常。",
            "rules": [
                "这一段测不到专注上限 —— 上限要在 25 分钟以上才测得出来；它只落一条观测。",
                "如果 10 分钟内完全没有专注下降，这条观测记为删失（没测到），不是“专注很好”。",
            ],
            "distraction_prompt": "这段里有没有明显分心？",
            "items": [
                _mcq(1, "下面哪个类型签名表示“接受一个 int，返回一个 int → int 的函数”？", [
                    "int -> int",
                    "int -> (int -> int)",
                    "(int -> int) -> int",
                    "int -> int -> int",
                ]),
                _mcq(2, "一个函数在尾递归位置调用自己，编译器可以把它优化成循环，关键原因是：", [
                    "尾调用之后没有需要用到的栈上局部状态",
                    "尾递归的次数一定很少",
                    "尾递归一定比循环快",
                    "尾递归不需要返回值",
                ]),
                _mcq(3, "哈希表最坏情况查找退化为 O(n)，原因是：", [
                    "哈希函数一定不均匀",
                    "所有键落进同一个桶，链表变成线性表",
                    "哈希表不支持 O(1) 插入",
                    "扩容时必然重排所有元素",
                ]),
                _mcq(4, "下面这段伪代码的时间复杂度是：\n\nfor i in 1..n:\n    j = 1\n    while j < n:\n        j = j * 2", [
                    "O(n)",
                    "O(n log n)",
                    "O(n²)",
                    "O(log n)",
                ]),
                _mcq(5, "「f(x) 返回一个新对象，不修改 x」这个性质叫什么？", [
                    "引用透明",
                    "不可变性（immutability）",
                    "惰性求值",
                    "型变（variance）",
                ]),
                _mcq(6, "关于 null 与“空集合”：哪个说法是对的？", [
                    "两者可以互换，都是“没有值”",
                    "null 表示“未知/缺失”，空集合表示“确实是空的”，混用会让“没数据”与“没查”分不开",
                    "空集合只在前端出现",
                    "null 更快，应优先用 null",
                ]),
                _mcq(7, "SQL 里 WHERE x <> 5 不会返回 x IS NULL 的行，原因是：", [
                    "NULL 表示未知，任何与 NULL 的比较结果都是 UNKNOWN，不满足 WHERE",
                    "数据库把 NULL 当成 0",
                    "<> 只对字符串生效",
                    "NULL 行在写入时就被跳过了",
                ]),
                _mcq(8, "两个进程用“先锁 A 再锁 B”和“先锁 B 再锁 A”的顺序各拿一把锁，最可能发生：", [
                    "数据不一致",
                    "死锁",
                    "内存泄漏",
                    "脏读",
                ]),
                _mcq(9, "下面哪个不是幂等操作？", [
                    "PUT /user/1 {name: \"a\"}",
                    "GET /user/1",
                    "追加一条记录到日志表",
                    "删除 id=1 的记录",
                ]),
                _mcq(10, "类型系统里 List<String> 是不是 List<Object> 的子类型？", [
                    "是，天然是",
                    "不是；若不安全的协变成立，往里塞 Integer 就会在取出来时炸",
                    "取决于语言是否支持泛型",
                    "只在 Java 里不是",
                ]),
                _mcq(11, "一段代码 a + b * c 被解析成 a + (b * c) 而不是 (a + b) * c，这由什么决定？", [
                    "运算符优先级",
                    "类型检查器",
                    "垃圾回收",
                    "虚拟机字节码",
                ]),
                _mcq(12, "「缓存穿透」通常指：", [
                    "查询的键根本不存在，每次都绕过缓存打到数据库",
                    "缓存里的数据过期",
                    "缓存与数据库不一致",
                    "缓存容量不够导致的淘汰",
                ]),
                _mcq(13, "一个 O(n log n) 排序在 n = 10⁶ 时约需 2×10⁷ 次操作，而一个 O(n²) 算法约需 10¹² 次。这个对比说明：", [
                    "O(n²) 在小数据上永远更慢",
                    "常数因子比复杂度更重要",
                    "复杂度差距在大规模下会压过常数因子，规模越大越不可互换",
                    "O(n log n) 一定更快",
                ]),
                _mcq(14, "下面哪个说法正确地描述了“读已提交（read committed）”隔离级别？", [
                    "能避免不可重复读，但可能幻读",
                    "可能读到其他事务已提交的新数据，同一事务内两次读可能不同",
                    "完全不发生并发问题",
                    "只允许一个事务同时运行",
                ]),
                _mcq(15, "你写了一个 retry 包装器，对任何失败都重试 3 次。对下面哪种操作重试是危险的？", [
                    "查询操作",
                    "幂等的写操作",
                    "扣款这类非幂等的写操作",
                    "读缓存",
                ]),
            ],
        },
        {
            "id": "B2",
            "kind": "extract",
            "kicker": "提取 · 6min · LANG",
            "title": "读完再说",
            "minutes": 6,
            "intro": "先读完全文，再答。不要往回翻着抄 —— 那是查阅，不是提取。6 分钟到就停。",
            "material": B2_MATERIAL,
            "material_warning": "开始作答后材料会收起。答不出来是数据，抄回来不是。",
            "key_points": {
                "prompt": "用三句话：① 这段材料的主线是什么 ② 写出三个关键事实或数据 ③ 写出一个容易被忽略的限定条件",
                "hint": "写成三行即可，不必成文。",
            },
            "items": [
                _mcq(2, "印地语在完成体里，及物动词的施事要带什么？", [
                    "ko",
                    "ne",
                    "-k",
                    "不带标记",
                ]),
                _mcq(3, "巴斯克语及物句里，带 -k 后缀的是：", [
                    "受事",
                    "不及物主语",
                    "施事",
                    "只用于从句",
                ]),
                _mcq(4, "迪尔巴尔语的“句法作格”体现在：", [
                    "格后缀的形态上",
                    "并列小句删略时按绝对格（S=O）认同成分",
                    "只有第一人称作格",
                    "完成体才作格",
                ]),
                _mcq(5, "材料对“人称-生命度层级”的态度是：", [
                    "这是所有作格语言的普遍规律",
                    "只是倾向，并非普遍规律，有语言按体分裂而对人称不敏感",
                    "只适用于南岛语系",
                    "材料没有提到",
                ]),
                _mcq(6, "关于“汉语是不是作格语言”，材料给出的立场是：", [
                    "汉语是作格语言",
                    "汉语不是作格语言",
                    "汉语不用格后缀表达这些关系，答案取决于用什么判据",
                    "汉语属于分裂作格，完成体作格",
                ]),
            ],
        },
        {
            "id": "B3",
            "kind": "table",
            "kicker": "卡点 · 6min · LANG",
            "title": "数据表",
            "minutes": 6,
            "intro": "把下表补齐，并算出最后一行。第 1–4 项与第 7 项材料里都有；第 5、6 项材料里没有。",
            "material_ref": "B2",
            "rows": [
                {"no": 1, "label": "巴斯克语及物句施事后缀"},
                {"no": 2, "label": "印地语完成体施事后置词"},
                {"no": 3, "label": "印地语未完成体受事后置词"},
                {"no": 4, "label": "迪尔巴尔语所在地区"},
                {"no": 5, "label": "迪尔巴尔语使用人口", "missing": True},
                {"no": 6, "label": "“分裂作格”在 Glottolog 里的分类编号", "missing": True},
                {"no": 7, "label": "材料里出现的语言总数"},
            ],
            "exits": [
                {"key": "material", "label": "材料 §“迪尔巴尔语”段"},
                {"key": "search", "label": "网上检索（Glottolog / Ethnologue）"},
                {"key": "ask", "label": "直接问我"},
            ],
            "action_prompt": "从你发现第 5 项缺数据，到你第一次做外部动作，大概过了多久？",
            "action_hint": "点下面任意一个出口，系统记下秒数；也可以只填“多久”与“做了什么”。",
            "approach_prompt": "求助前你做了什么？",
            "resolve_prompt": "最后解决了吗？",
            "resolve_options": ["解决了", "部分解决", "没解决", "还没走到这一步"],
        },
        {
            "id": "B4",
            "kind": "decision",
            "kicker": "决策 · 5min · LANG",
            "title": "四条路选一条",
            "minutes": 5,
            "scenario": "两周后你要给一个完全不懂语言学的人做一次 15 分钟讲解，主题是“分裂作格”。下面四条路都可行，你只能选一条，选完不能再换资料形式（时间只够走一条）。",
            "options": [
                {"key": "A", "label": "综述", "text": "先读一篇 6000 字的综述（材料已给），再自己浓缩成讲稿"},
                {"key": "B", "label": "视频", "text": "先看三个 10 分钟左右的科普视频，挑其中一个的思路重讲一遍"},
                {"key": "C", "label": "结构", "text": "先画出这个主题的全局结构（什么是对齐、有哪些分裂条件、卡在哪），边查边补细节"},
                {"key": "D", "label": "大纲", "text": "直接做幻灯片大纲，缺什么补什么"},
            ],
            "fields": [
                {"key": "choice", "no": 1, "prompt": "你选哪条（A/B/C/D）"},
                {"key": "compared", "no": 2, "prompt": "你比较了哪几条（可多选）", "multi": True},
                {"key": "criterion", "no": 3, "prompt": "你按什么标准判断的（一句）"},
                {"key": "changed", "no": 4, "prompt": "中途有没有改主意（有/没有，改了从哪到哪）"},
            ],
        },
        {
            "id": "B5",
            "kind": "optional",
            "kicker": "启动 · 3min · 可选",
            "title": "抽出一个函数",
            "minutes": 3,
            "intro": "这一项明确是可选的：现在做可以，跳过也完全可以，跳过不影响任何分数。",
            "task": "下面这段代码有两处几乎重复的取数逻辑，把它抽成一个函数，写出改完的版本（语言随意）。",
            "code": B5_CODE,
            "fields": [
                {"key": "approach", "prompt": "如果做了：你第一次动手做的是什么？", "hint": "写第一个字符 / 打开编辑器 / 复制代码。"},
            ],
            "decision_options": [
                {"key": "do", "label": "现在做"},
                {"key": "skip", "label": "跳过"},
            ],
            "decision_hint": "从读完这段到第一次动手的秒数会被记录；“决定做 / 跳过”本身也是一个答案。",
        },
        {
            "id": "C1",
            "kind": "pair",
            "kicker": "呈现偏好 · 3min",
            "title": "递归：抽象版 vs 案例版",
            "minutes": 3,
            "intro": "两版讲的是同一件事、篇幅相近。读完回答三个问题。",
            "versions": [
                {
                    "key": "A",
                    "label": "A 版 · 抽象",
                    "text": "递归是函数在定义中调用自身、并通过基线条件终止的求解方式。一个合法的递归定义包含三部分：基线条件（base case，不再递归的最小情形）、递推关系（把规模 n 的问题化归为规模更小的同类问题）、以及单调性保证（每次递归调用都使问题规模严格减小，否则不终止）。其执行依赖调用栈：每一次未返回的调用都在栈上保留一帧局部状态，因此空间开销与递归深度成正比，深度过大会导致栈溢出。凡可递归求解的问题，都可用显式栈改写为迭代。",
                },
                {
                    "key": "B",
                    "label": "B 版 · 案例",
                    "text": "小林要统计一个文件夹里所有 .md 文件的行数。目录里有子目录，子目录里还有子目录。他写了个函数：先看当前目录，遇到文件就数行数加进总数，遇到子目录就把这个子目录交给同一个函数再走一遍。走到底——一个既没有文件也没有子目录的空目录——就返回 0，不再往下。他后来发现，如果某个子目录被链接指回上一层，这个函数会一直往下跑直到崩掉，因为“越走越小”这个前提不成立了。",
                },
            ],
            "questions": [
                {"key": "comfort", "no": 1, "prompt": "舒适度：读哪一版更舒服？", "options": [("A", "A 版"), ("B", "B 版"), ("same", "差不多")]},
                {"key": "understanding", "no": 2, "prompt": "理解难度：哪一版更容易懂？", "options": [("A", "A 版"), ("B", "B 版"), ("same", "差不多")]},
                {"key": "start", "no": 3, "prompt": "更愿从哪种开始：要学一个新概念，你想先拿到哪一种？", "options": [("A", "A 版"), ("B", "B 版")]},
            ],
        },
        {
            "id": "C2",
            "kind": "organize",
            "kicker": "组织 · 3min · LANG",
            "title": "12 条事实，你来组织",
            "minutes": 3,
            "intro": "下面 12 条事实顺序是乱的，其中有 3 条跟“声调语言”这个主题无关。把它们组织成一份 5 分钟能讲完的讲解提纲（条目即可，不用成文）。",
            "facts": [
                {"no": index + 1, "text": text} for index, text in enumerate(C2_FACTS)
            ],
            "unrelated_prompt": "你判断哪几条无关？（多选，卷面说共有 3 条）",
            "outline_prompt": "讲解提纲",
            "outline_hint": "条目即可。",
            "first_move_prompt": "你先做了什么？",
            "first_moves": ["先删无关项", "先分类", "先排顺序", "直接写"],
        },
        {
            "id": "D1",
            "kind": "recall",
            "kicker": "回溯 · 4min",
            "title": "跨场景：能专注多久",
            "minutes": 4,
            "intro": "三种场景各回忆 2 次。想不起具体的就勾“想不起”，不要估一个数 —— 编出来的数与真记录混在一起，以后就分不开了。",
            "scenarios": [
                {"key": "quiet", "label": "安静独处"},
                {"key": "company", "label": "有人在场", "note": "家人 / 同事 / 咖啡厅"},
                {"key": "reachable", "label": "通知可达", "note": "手机在手边 / 消息弹窗"},
            ],
            "slots": 2,
            "fields": [
                {"key": "minutes", "label": "专注（分钟）"},
                {"key": "interruptions", "label": "被打断（次）"},
            ],
            "unknown_label": "想不起",
        },
        {
            "id": "D2",
            "kind": "reflection",
            "kicker": "复盘 · 2min",
            "title": "就用刚才这一段做复盘",
            "minutes": 2,
            "intro": "评分看的是四要素齐不齐、具不具体，不是“反思得好不好听”。“下次注意点”这一类拿不到分 —— 它在计划里也变不成任何一步。",
            "prompts": [
                {"key": "what", "no": 1, "prompt": "发生了什么（具体到哪一段、你怎么做的）"},
                {"key": "expected", "no": 2, "prompt": "哪一处和你预期不一样"},
                {"key": "why", "no": 3, "prompt": "为什么会这样"},
                {"key": "next", "no": 4, "prompt": "下次具体改哪一步"},
            ],
        },
    ],
    #: 本次不做的段落 —— 如实列出，而不是在界面上留三张空卡片。
    "not_measured": [
        {
            "title": "C3 四版呈现",
            "reason": "图 / 音频 / 动觉素材已经生成，但这一版卷面没有排入；接入后补。",
        },
        {
            "title": "E 真实学习闭环",
            "reason": "缺“你想学的主题”：主题定了才能出 8–10 题与次日平行卷。",
        },
    ],
}


# --------------------------------------------------------------------------- #
# 公开视图
# --------------------------------------------------------------------------- #


def _strip(item: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        item.pop(key, None)


def public_paper() -> dict[str, Any]:
    """下发卷面：**不含答案、不含评分键**。"""
    out = copy.deepcopy(PAPER)
    for section in out["sections"]:
        for item in section.get("items", []):
            _strip(item, ("answer",))
        _strip(section, ("answers",))
    return out


# --------------------------------------------------------------------------- #
# 核对
# --------------------------------------------------------------------------- #


def _norm(text: Any) -> str:
    return re.sub(r"[\s`'\"“”‘’。，、；：！？,.;:!?()（）-]+", "", str(text or "")).lower()


def _matches_any(answer: Any, expected: tuple[str, ...]) -> bool:
    flat = _norm(answer)
    return bool(flat) and any(_norm(candidate) and _norm(candidate) in flat for candidate in expected)


def _section(answers: dict[str, Any], section_id: str) -> dict[str, Any]:
    value = answers.get(section_id)
    return value if isinstance(value, dict) else {}


def _mcq_result(items: list[dict[str, Any]], answers: dict[str, Any], keyed: dict[int, str]) -> tuple[int, int, int]:
    answered = 0
    correct = 0
    for item in items:
        value = str(answers.get(str(item["no"])) or "").strip().upper()
        if value:
            answered += 1
            if keyed.get(int(item["no"])) == value:
                correct += 1
    return correct, answered, len(items)


def evaluate(answers: dict[str, Any]) -> dict[str, Any]:
    """从原始作答算出**能算的那部分**。返回可直接展示的核对结果。

    主观部分（三句话概括、求助经过、决策理由、提纲、复盘）只统计
    "填没填"，不判好坏 —— 卷面 §5 的口径要判官或人按 rubric 判。
    """
    objective: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    flags: list[str] = []
    notes: list[str] = []

    # ── B1 ────────────────────────────────────────────────────────────
    b1 = _section(answers, "B1")
    b1_answers = b1.get("answers") if isinstance(b1.get("answers"), dict) else {}
    b1_items = next(s["items"] for s in PAPER["sections"] if s["id"] == "B1")
    b1_correct, b1_answered, b1_total = _mcq_result(b1_items, b1_answers, B1_ANSWERS)
    objective.append({
        "section": "B1",
        "label": "逻辑与类型推理",
        "correct": b1_correct,
        "answered": b1_answered,
        "total": b1_total,
        "detail": f"到计时结束时答了 {b1_answered} 题",
    })
    stop_at = b1.get("stop_at")
    if isinstance(stop_at, (int, float)) and stop_at > 0:
        observations.append({"label": "B1 到点停在", "value": f"第 {int(stop_at)} 题"})
    if b1_answered < b1_total:
        flags.append("b1_censored")
        notes.append(
            f"B1 只答了 {b1_answered} / {b1_total} 题：这是一条删失观测（没测到上限），"
            "不是“专注很好”。"
        )
    distractions = b1.get("distractions")
    if isinstance(distractions, (int, float)):
        observations.append({"label": "B1 明显分心", "value": f"{int(distractions)} 次"})
        if distractions > 0:
            notes.append("B1 期间出现过明显分心；触发点与次数已随作答存档。")

    # ── B2 ────────────────────────────────────────────────────────────
    b2 = _section(answers, "B2")
    b2_answers = b2.get("answers") if isinstance(b2.get("answers"), dict) else {}
    b2_items = next(s["items"] for s in PAPER["sections"] if s["id"] == "B2")
    b2_correct, b2_answered, b2_total = _mcq_result(b2_items, b2_answers, B2_ANSWERS)
    summary = str(b2.get("summary") or "").strip()
    objective.append({
        "section": "B2",
        "label": "提取检验（标尺）",
        "correct": b2_correct,
        "answered": b2_answered,
        "total": b2_total,
        "detail": "客观题只做标尺，不进 V-SE 读数",
    })
    if not summary:
        flags.append("b2_summary_empty")
        notes.append("B2 的三句话概括为空 —— 那正是这一区的主要读数，不能补。")
    else:
        observations.append({"label": "三句话概括", "value": f"{len(summary)} 字"})

    # ── B3 ────────────────────────────────────────────────────────────
    b3 = _section(answers, "B3")
    b3_values = b3.get("values") if isinstance(b3.get("values"), dict) else {}
    matched = 0
    for no, expected in B3_EXPECTED.items():
        if _matches_any(b3_values.get(str(no)), expected):
            matched += 1
    filled_missing = sum(
        1 for no in B3_MISSING if str(b3_values.get(str(no)) or "").strip()
    )
    objective.append({
        "section": "B3",
        "label": "材料内数据",
        "correct": matched,
        "answered": len([v for v in b3_values.values() if str(v or "").strip()]),
        "total": len(B3_EXPECTED),
        "detail": "第 5、6 项材料里没有，不判对错",
    })
    first_action = b3.get("first_action_ms")
    if isinstance(first_action, (int, float)) and first_action >= 0:
        observations.append({
            "label": "B3 发现缺数据 → 第一次外部动作",
            "value": f"{round(first_action / 1000, 1)} 秒",
        })
    else:
        flags.append("b3_no_external_action")
        observations.append({"label": "B3 第一次外部动作", "value": "未采到"})
        notes.append(
            "B3 的“第一次外部动作”没有记录 —— 记为未采到，不是 0 秒。"
            "缺了它，V-HSR 这次不产出观测。"
        )
    if filled_missing:
        observations.append({"label": "缺数据两格", "value": f"填了 {filled_missing} 格"})

    # ── B4 ────────────────────────────────────────────────────────────
    b4 = _section(answers, "B4")
    compared = b4.get("compared") if isinstance(b4.get("compared"), list) else []
    observations.append({
        "label": "B4 比较过的选项",
        "value": f"{len(compared)} 条",
    })
    if b4.get("choice"):
        observations.append({"label": "B4 选择", "value": str(b4["choice"])})
    if not compared:
        notes.append("B4 没记“比较了哪几条”—— 单次切换不算审慎，缺这一项就无法区分“直接定”与“比较后定”。")

    # ── B5 ────────────────────────────────────────────────────────────
    b5 = _section(answers, "B5")
    decision = b5.get("decision")
    if decision == "do":
        observations.append({"label": "B5", "value": "做了（可选任务）"})
        first = b5.get("first_action_ms")
        if isinstance(first, (int, float)) and first >= 0:
            observations.append({
                "label": "B5 读完 → 第一次动手",
                "value": f"{round(first / 1000, 1)} 秒",
            })
        else:
            flags.append("b5_no_first_action")
            notes.append("B5 选择“做”，但没有记到第一次动手的时刻 —— 记为未采到，不是 0 秒。")
    elif decision == "skip":
        observations.append({"label": "B5", "value": "跳过（可选任务）"})
    else:
        flags.append("b5_undecided")
        notes.append("B5 没有记录“做 / 跳过”。可选任务也要留下这个决定本身。")

    # ── C1 ────────────────────────────────────────────────────────────
    c1 = _section(answers, "C1")
    picks = {key: c1.get(key) for key in ("comfort", "understanding", "start") if c1.get(key)}
    if picks:
        observations.append({
            "label": "C1 呈现偏好",
            "value": " · ".join(f"{key}={value}" for key, value in picks.items()),
        })
    else:
        flags.append("c1_empty")
        notes.append("C1 三问没有作答。")

    # ── C2 ────────────────────────────────────────────────────────────
    c2 = _section(answers, "C2")
    picks = c2.get("unrelated") if isinstance(c2.get("unrelated"), list) else []
    observations.append({"label": "C2 判为无关", "value": f"{len(picks)} 条"})
    if not str(c2.get("outline") or "").strip():
        flags.append("c2_outline_empty")
        notes.append("C2 提纲为空 —— 这一题测的是“你怎么组织”，组织结果必须留下。")

    # ── D1 / D2 ───────────────────────────────────────────────────────
    d1 = _section(answers, "D1")
    rows = d1.get("events") if isinstance(d1.get("events"), list) else []
    known = [row for row in rows if isinstance(row, dict) and not row.get("unknown")]
    observations.append({
        "label": "D1 场景记录",
        "value": f"{len(known)} / {len(rows)} 次有具体数值",
    })
    d2 = _section(answers, "D2")
    filled = sum(1 for key in ("what", "expected", "why", "next") if str(d2.get(key) or "").strip())
    observations.append({"label": "D2 复盘四要素", "value": f"{filled} / 4 填写"})
    if filled < 4:
        flags.append("d2_incomplete")
        notes.append("D2 复盘四要素没有写齐。缺的要素按 0 计，不替用户补。")

    # ── 整卷完成度 ─────────────────────────────────────────────────────
    required = {"A", "B1", "B2", "B3", "B4", "B5", "C1", "C2", "D1", "D2"}
    done = {sid for sid in required if _section(answers, sid)}
    if done != required:
        flags.append("partial")
        notes.append("还有未作答的段落：" + "、".join(sorted(required - done)) + "。")

    notes.append(
        "主观部分（三句话概括、求助经过、决策理由、提纲、复盘）已存原文，"
        "未自动判分 —— 这些按卷面 §5 的口径另行判读。"
    )
    return {
        "objective": objective,
        "observations": observations,
        "flags": flags,
        "notes": notes,
    }
