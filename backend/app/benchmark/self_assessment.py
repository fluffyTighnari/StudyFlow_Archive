"""自评单例（127 条 / 简短版 90 条）—— **量表的唯一一份定义**。

## 它为什么与「基准测试」分开

基准测试是**受控施测**的进阶专项测量：给材料、计时、判分，
产出的是 `measured` / `experienced` 通道。自评量表不给人任何材料，
它问的是"最近三个月里你实际最常发生的那一档"，产出的是 **`stated` 通道**。

两者可以写同一个变量，但**不是同一把尺子**：把自评的 7 分和任务的 7 分
平均，等于把"他觉得自己怎样"和"他做起来怎样"混成一个数。所以：

- 自评只写 `channel=stated`；
- 基准运行只写它自己设计的通道；
- 界面上它们各自一行，不合并。

## 为什么计分键在服务端

计分键里写着哪些句子是**反向句**。作答者一旦看到，反向句当场失效 ——
于是"作答偏移"会被抹平成"真实倾向"。所以：

- `GET` 只给题目文本与选项标签，**永远不带 `reverse`**；
- 反向、加权、四维取最高两端，都在这里算完。

## 权重为什么顶到两端

`A=0, B=1/3, C=2/3, D=1`。若最低档给 0.1、最高档给 0.9，
那么"从来没有"的人拿到 1 分而不是 0 分、天花板是 9 分 ——
这个偏差不报错、不显示，只会安静地把所有人的分数压向中间。

## v2 为什么是 127 条，而不是把句子重复几遍凑数

题量本身不产生信息。v2 的扩容方式是**给每个变量定义 facet**，再让每个 facet
至少有三条互不重复的行为指标。重复句只会增加相关误差，并稀释
`straight_lining` / `acquiescence` 这两个效度标记。

| 类型 | 变量 | facet 数 | 条数 |
|---|---|---|---|
| 单量表 | V-SE / HSR / DC / IR / PAR / ATT | 4 | 12 / 12 / 11 / 12 / 12 / 11 |
| 单量表 | V-ENV | 3 | 9 |
| 偏好（两端） | V-CP / V-OSP | 2 | 8 / 8 |
| 偏好（四维） | V-SMP / V-ACT | 4 | 16 / 16 |

**v2 修正了 v1 的一处反向键错误**（28/30/38/40 号）：
这四句分别是"想先拿到一般规律""没有全局框架心里没底""不如直接给定义"
"给框架图觉得空"——它们**正向**指向自己所属的一端，标成反向会把该端的分数
取反。v1 因此会把"强烈偏好案例"的人判成"两端都舒适"。v2 把四句改回正向，
并按"每端 2 正 + 1 自己端正向 + 1 反向"补齐。

## 两档

- `full` = 127 条，写全 11 个变量；
- `short` = 90 条，是 full 的**子集**（每个 facet 取 2 条，偏好变量每端取 3 条），
  题号与 full 完全一致 —— 所以两档可以共用一份计分器，历史作答也能对上。

简短版覆盖全部 11 个变量，但每条 facet 只有 2 条：足够给方向，不足以做稳定基线，
置信度因此固定为 `hypothesis`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

#: 四档权重。**顺序即卷面顺序**，改动它等于换一把尺子。
OPTIONS: tuple[tuple[str, str, float], ...] = (
    ("A", "从不 / 几乎没有", 0.0),
    ("B", "偶尔", 1 / 3),
    ("C", "经常", 2 / 3),
    ("D", "几乎总是", 1.0),
)

OPTION_WEIGHTS: dict[str, float] = {value: weight for value, _label, weight in OPTIONS}

#: 卷面版本。v1 = 44 条（1–44 号，已冻结）；v2 = 127 条。
SCALE_VERSION = "v2"

#: 两档。`short` 是 `full` 的子集，题号一致。
TIERS: tuple[str, ...] = ("short", "full")


@dataclass(frozen=True)
class Statement:
    no: int
    text: str
    #: 这条句子计入哪个变量。
    variable_id: str
    #: `True` = 反向句（反向作答表示"更少"，所以取 `1 - w`）。
    reverse: bool = False
    #: 这条句子测的是该变量的哪一维/哪一端。v2 起 11 个变量都有 facet。
    facet: str | None = None
    #: 是否进入简短版（90 条）。
    short: bool = False


#: 127 条，**顺序固定**（卷面号就是下标 + 1）。
#:
#: 1–44 是 v1 原题，**逐字冻结**：改一句就要重算历史作答，而旧运行只存卷面号。
#: 45–127 是 v2 新增，按"每轮每个变量各出一条"的顺序交错排列，降低回答惯性。
STATEMENTS: tuple[Statement, ...] = (
    # ── v1 · 1–44（冻结）───────────────────────────────────────────
    Statement(1, "读一份信息密集的材料时，我先找这段到底在讲什么，再回头抠细节。", "V-SE", facet="定位", short=True),
    Statement(2, "卡住超过十几分钟，我会去问人或查资料，而不是继续自己磨。", "V-HSR", facet="阈值", short=True),
    Statement(3, "做选择前，我会把候选的几个选项列出来对比。", "V-DC", facet="展开", short=True),
    Statement(4, "决定要开始一件事之后，我通常几分钟内就动手了。", "V-IR", facet="延迟", short=True),
    Statement(5, "一件事做完，我会顺手记下哪里和预期不一样。", "V-PAR", facet="留痕", short=True),
    Statement(6, "旁边有人说话时，我会停下来听他们在说什么。", "V-ENV", facet="听觉", short=True),
    Statement(7, "我能连续专注 25 分钟以上不做别的事。", "V-ATT", facet="单段", short=True),
    Statement(8, "学新东西时，我更愿意先看一个具体案例，再看定义和原理。", "V-CP", facet="案例端", short=True),
    Statement(9, "有图表或流程图的材料，我读起来比纯文字快。", "V-SMP", facet="图", short=True),
    Statement(10, "开始学陌生主题时，我先画一张全局图，或先列出它包含哪些部分。", "V-OSP", facet="全局端", short=True),
    Statement(11, "自己读材料、做笔记，是我学得最有效率的方式。", "V-ACT", facet="读", short=True),
    Statement(12, "开口问之前，我会先把「我卡在哪一步」想清楚。", "V-HSR", facet="表述", short=True),
    Statement(13, "比较之前，我会先想清楚按什么标准判断好坏。", "V-DC", facet="定准", short=True),
    Statement(14, "我会先把最小的第一步做掉（比如先打开文件写一行），再谈计划。", "V-IR", facet="拆分", short=True),
    Statement(15, "我会说出下次具体改哪一步，而不只是「下次注意点」。", "V-PAR", facet="转化", short=True),
    Statement(16, "手机弹出通知，我会立刻去看。", "V-ENV", facet="数字", short=True),
    Statement(17, "被打断之后，我能很快回到原来的位置。", "V-ATT", facet="恢复", short=True),
    Statement(18, "只有抽象定义和公式时，我常常读不下去。", "V-CP", facet="案例端"),
    Statement(19, "有人讲一遍，比我自己读一遍记得牢。", "V-SMP", facet="听", short=True),
    Statement(20, "我会先要一份分几步走的清单，照着顺序做。", "V-OSP", facet="线性端", short=True),
    Statement(21, "看视频或听人讲一遍，是我学得最有效率的方式。", "V-ACT", facet="听看", short=True),
    Statement(22, "材料里出现数字和条件句（仅当…、除…之外）时，我会把它们单独记下来。", "V-SE", facet="抽取", short=True),
    Statement(23, "定下来之后，我还会留意有没有更合适的选项出现。", "V-DC", facet="复核", short=True),
    Statement(24, "任务摆在面前时，我很少先去处理别的杂事。", "V-IR", facet="替代", short=True),
    Statement(25, "隔一段时间，我会回头看我之前的复盘，看有没有真改。", "V-PAR", facet="回看", short=True),
    Statement(26, "换个地方（书房到咖啡厅），我的效率会明显不一样。", "V-ENV", facet="场所", short=True),
    Statement(27, "一件事做到 40 分钟以上，我的错误会明显变多。", "V-ATT", reverse=True, facet="疲劳", short=True),
    # ↓ 28 号：v1 误标为反向，v2 修正为正向（这句是"抽象端"的正向指标）。
    Statement(28, "案例讲得再多，我还是想先拿到那条一般规律。", "V-CP", facet="抽象端", short=True),
    Statement(29, "我会边读边写或画，靠手写下来才记得住。", "V-SMP", facet="读写", short=True),
    Statement(30, "没有全局框架就往下学，我会觉得心里没底。", "V-OSP", facet="全局端"),
    Statement(31, "把学到的东西讲给别人听或跟人讨论，我才算真懂。", "V-ACT", facet="讲与讨论", short=True),
    Statement(32, "读完一份长材料，我能不看原文说出三四个关键事实。", "V-SE", facet="复述", short=True),
    Statement(33, "身边有能问的人时，我确实会去问。", "V-HSR", facet="渠道", short=True),
    Statement(34, "任务安排好之后，我常常先去做点别的，过一阵才开始。", "V-IR", reverse=True, facet="延迟", short=True),
    Statement(35, "做完就过去了，我很少回头想过程里发生了什么。", "V-PAR", reverse=True, facet="留痕", short=True),
    Statement(36, "周围再怎么吵，我基本都能继续做手上的事。", "V-ENV", reverse=True, facet="听觉", short=True),
    Statement(37, "我做事时经常几分钟就切一次（看手机、换窗口）。", "V-ATT", reverse=True, facet="切换"),
    # ↓ 38 号：v1 误标为反向，v2 修正为正向（这句是"抽象端"的正向指标）。
    Statement(38, "别人用举例子的方式解释时，我常常觉得不如直接给定义。", "V-CP", facet="抽象端"),
    Statement(39, "亲手做一遍（拼、摆、按步骤操作）比看讲解有用。", "V-SMP", facet="动觉", short=True),
    # ↓ 40 号：v1 误标为反向，v2 修正为正向（这句是"线性端"的正向指标）。
    Statement(40, "一上来给框架图，我常常觉得空，不如直接告诉我第一步做什么。", "V-OSP", facet="线性端"),
    Statement(41, "亲手做一个东西出来，我才会。", "V-ACT", facet="动手", short=True),
    Statement(42, "细节一多，我读完就只剩一个模糊印象，具体说了什么想不起来。", "V-SE", reverse=True, facet="复述", short=True),
    Statement(43, "遇到不会的东西，我倾向于自己死磕到会，尽量不麻烦别人。", "V-HSR", reverse=True, facet="阻力"),
    Statement(44, "我常常凭第一感觉就定了，事后才想还有没有别的办法。", "V-DC", reverse=True, facet="复核", short=True),

    # ── v2 新增 · 45–127（每轮每个变量各一条）──────────────────────
    Statement(45, "读长文时，我会先扫一遍标题和小标题，再决定从哪读起。", "V-SE", facet="定位"),
    Statement(46, "同一处卡了两次还没进展，我就会换一种办法或找人。", "V-HSR", facet="阈值"),
    Statement(47, "遇到「怎么办」的问题，我会先列出至少三种做法再挑。", "V-DC", facet="展开"),
    Statement(48, "我说「马上做」之后，通常真的在十分钟内开始。", "V-IR", facet="延迟"),
    Statement(49, "做完一件事，我会花两分钟写一句「哪里不顺」。", "V-PAR", facet="留痕"),
    Statement(50, "有人在旁边打电话，我很难不受影响。", "V-ENV", facet="听觉"),
    Statement(51, "我能坐在一件事上，中间不碰手机超过半小时。", "V-ATT", facet="单段"),
    Statement(52, "新概念我会先找一个具体例子，再回头看定义。", "V-CP", facet="案例端", short=True),
    Statement(53, "看示意图时，我能很快看出各部分怎么连在一起。", "V-SMP", facet="图", short=True),
    Statement(54, "学新主题前，我会先列一份「它包含哪些部分」的清单。", "V-OSP", facet="全局端", short=True),
    Statement(55, "遇到重要内容，我会先自己读原文，再看别人的解读。", "V-ACT", facet="读", short=True),

    Statement(56, "一段话读完，我常常说不出它和上一段是什么关系。", "V-SE", reverse=True, facet="定位", short=True),
    Statement(57, "一道题能磨一下午，即使完全没有进展，我也不太会换路。", "V-HSR", reverse=True, facet="阈值", short=True),
    Statement(58, "我常常只想到一个方案就开始做了。", "V-DC", reverse=True, facet="展开", short=True),
    Statement(59, "开始前我会把任务改写成「先做五分钟的那一小步」。", "V-IR", facet="拆分"),
    Statement(60, "出问题时，我会先看是哪一步的设计有问题，而不是怪状态。", "V-PAR", facet="归因", short=True),
    Statement(61, "学习时手机放在手边，我也能忍住不去看。", "V-ENV", reverse=True, facet="数字", short=True),
    Statement(62, "我的专注通常撑不到十分钟就断了。", "V-ATT", reverse=True, facet="单段", short=True),
    Statement(63, "只有框架和定义时，我反而更能抓住重点。", "V-CP", reverse=True, facet="案例端", short=True),
    Statement(64, "拿到一份带图示的说明，我通常先看图再看文字。", "V-SMP", facet="图"),
    Statement(65, "一上来先看整体结构，我会觉得空，想直接知道第一步做什么。", "V-OSP", reverse=True, facet="全局端", short=True),
    Statement(66, "我习惯把材料读两遍：第一遍通读，第二遍抠细节。", "V-ACT", facet="读"),

    Statement(67, "读到「仅当」「前提是」这类条件，我会停下来确认它约束的是哪一段。", "V-SE", facet="抽取"),
    Statement(68, "求助时我会直接说「我试过 A 和 B，卡在 C」，而不是「这题不会」。", "V-HSR", facet="表述"),
    Statement(69, "选之前我会先问自己：这件事最不能牺牲的是什么。", "V-DC", facet="定准"),
    Statement(70, "一想到要开始，我常先去找资料、理桌面，把准备当成开始。", "V-IR", reverse=True, facet="拆分", short=True),
    Statement(71, "事情没做好，我通常归因于「今天不在状态」。", "V-PAR", reverse=True, facet="归因", short=True),
    Statement(72, "打开电脑后，我会不自觉地切到别的窗口。", "V-ENV", facet="数字"),
    Statement(73, "一件事没告一段落，我就跳去做别的了。", "V-ATT", reverse=True, facet="切换", short=True),
    Statement(74, "我更喜欢先拿到一条能套用的规律，再去看例子。", "V-CP", facet="抽象端", short=True),
    Statement(75, "让我把内容画成一张图，我会比写成段落更清楚。", "V-SMP", facet="图", short=True),
    Statement(76, "我更喜欢一条明确的步骤清单，按顺序往下做。", "V-OSP", facet="线性端", short=True),
    Statement(77, "与其听人讲，我更愿意自己把材料读一遍。", "V-ACT", facet="读", short=True),

    Statement(78, "读完材料，我常把某个数字记成另一个——是总数还是某一项，事后说不准。", "V-SE", reverse=True, facet="抽取", short=True),
    Statement(79, "我常常把问题原样丢给别人，让对方帮我找出卡在哪。", "V-HSR", reverse=True, facet="表述", short=True),
    Statement(80, "我常被最新出现的那个理由带走，事后发现标准一直没定。", "V-DC", reverse=True, facet="定准", short=True),
    Statement(81, "打开电脑后，我会先刷一会儿再进入正事。", "V-IR", reverse=True, facet="替代", short=True),
    Statement(82, "我会区分「这次运气不好」和「这个做法本身有问题」。", "V-PAR", facet="归因"),
    Statement(83, "换个座位或换间屋子，我的专注度会明显变化。", "V-ENV", facet="场所"),
    Statement(84, "我会在任务之间来回跳，很少一次只做一件。", "V-ATT", reverse=True, facet="切换", short=True),
    Statement(85, "没有具体例子，抽象原理我很难记住。", "V-CP", reverse=True, facet="抽象端", short=True),
    Statement(86, "听录音或播客时，我能跟上讲述的节奏。", "V-SMP", facet="听", short=True),
    Statement(87, "没有整体框架就开始学，我会觉得心里没底。", "V-OSP", reverse=True, facet="线性端", short=True),
    Statement(88, "有讲解视频时，我会先看视频再自己练。", "V-ACT", facet="听看", short=True),

    Statement(89, "合上材料，我能按顺序说出它分几块、每块讲了什么。", "V-SE", facet="复述"),
    Statement(90, "遇到名词不懂，我会先查文档或搜索，而不是先问人。", "V-HSR", facet="渠道"),
    Statement(91, "重要决定做完，我会隔一晚再看一眼。", "V-DC", facet="复核"),
    Statement(92, "我会用「先整理一下」「先做点简单的」来推迟真正难的那一步。", "V-IR", reverse=True, facet="替代"),
    Statement(93, "复盘完我会把它变成下一步的一个具体动作。", "V-PAR", facet="转化"),
    Statement(94, "桌面乱不乱，我都能照样进入状态。", "V-ENV", reverse=True, facet="场所", short=True),
    Statement(95, "被打断后，我需要很久才能想起刚才做到哪。", "V-ATT", reverse=True, facet="恢复", short=True),
    Statement(96, "同一段内容，听别人讲一遍比我自己读一遍更省力。", "V-SMP", facet="听"),
    Statement(97, "我听讲座或课程时，注意力比我自己读书时更集中。", "V-ACT", facet="听看"),

    Statement(98, "需要核对某条信息时，我大致知道它在材料的哪一段。", "V-SE", facet="溯源", short=True),
    Statement(99, "卡住时我会先找一份完整的例子照着走，再考虑自己推。", "V-HSR", facet="渠道", short=True),
    Statement(100, "能撤回的决定，我做得快；改不回来的，我会多花时间。", "V-DC", facet="可逆", short=True),
    Statement(101, "没有闹钟或别人催，我很难按时开始。", "V-IR", reverse=True, facet="外部触发", short=True),
    Statement(102, "我的复盘常常停在「下次要注意」，没有具体到改哪一步。", "V-PAR", reverse=True, facet="转化", short=True),
    Statement(103, "被打断时我会先记下「下一个动作」，回来就能接上。", "V-ATT", facet="恢复"),
    Statement(104, "听的时候我不太需要重复回放就能记住大意。", "V-SMP", facet="听", short=True),
    Statement(105, "我更愿意跟着视频一步步做，而不是自己啃文档。", "V-ACT", facet="听看", short=True),

    Statement(106, "被问到「这个说法出自哪里」，我常常答不上来，只能整篇重找。", "V-SE", reverse=True, facet="溯源", short=True),
    Statement(107, "我怕问的问题太基础，会让别人觉得我不行。", "V-HSR", reverse=True, facet="阻力", short=True),
    Statement(108, "不管这件事能不能反悔，我花的时间都差不多。", "V-DC", reverse=True, facet="可逆", short=True),
    Statement(109, "我知道自己什么时候最容易开始，会把难事放在那个时段。", "V-IR", facet="外部触发", short=True),
    Statement(110, "我会在下次做同类事之前，先翻一眼上次的复盘。", "V-PAR", facet="回看"),
    Statement(111, "连续学一小时后，我还能保持和开头差不多的质量。", "V-ATT", facet="疲劳", short=True),
    Statement(112, "读的时候我会在页边写批注或划重点。", "V-SMP", facet="读写", short=True),
    Statement(113, "我会主动找人讨论刚学的内容。", "V-ACT", facet="讲与讨论", short=True),

    Statement(114, "我读材料时会顺手标出关键段落，之后回看很快。", "V-SE", facet="溯源"),
    Statement(115, "需要帮助时，我会直接说，不太纠结对方怎么看我。", "V-HSR", facet="阻力", short=True),
    Statement(116, "只要坐到固定的位置、做完固定的开场动作，我就能进入状态。", "V-IR", facet="外部触发"),
    Statement(117, "复盘记完就再也没打开过。", "V-PAR", reverse=True, facet="回看", short=True),
    Statement(118, "把要点抄一遍，比只看一遍记得牢。", "V-SMP", facet="读写"),
    Statement(119, "给别人讲一遍，能帮我发现自己没想清楚的地方。", "V-ACT", facet="讲与讨论"),

    Statement(120, "我习惯用文字整理思路，而不是只在脑子里过。", "V-SMP", facet="读写", short=True),
    Statement(121, "和别人一起学，比一个人学更能推动我。", "V-ACT", facet="讲与讨论", short=True),
    Statement(122, "需要按步骤操作的东西，我上手做两遍就熟了。", "V-SMP", facet="动觉", short=True),
    Statement(123, "我会先做一个最小版本出来，再回头补理论。", "V-ACT", facet="动手", short=True),
    Statement(124, "光看演示不动手，我总觉得没学会。", "V-SMP", facet="动觉"),
    Statement(125, "我通过实际做出一个东西来检验自己会不会。", "V-ACT", facet="动手"),
    Statement(126, "边做边学比先看完再动手更适合我。", "V-SMP", facet="动觉", short=True),
    Statement(127, "比起先学完再做，我更习惯边做边查。", "V-ACT", facet="动手", short=True),
)

#: 附加题：专注时长的**区间**（不是四条句子的平均值）。
ATTENTION_SPAN_OPTIONS: tuple[tuple[str, str], ...] = (
    ("A", "15 分钟以内"),
    ("B", "15–25 分钟"),
    ("C", "25–45 分钟"),
    ("D", "45–60 分钟"),
    ("E", "60 分钟以上"),
)

#: 附加题：四种学习方式按**实际用得最多**排序（可留空）。
ACTIVITY_RANK_OPTIONS: tuple[str, ...] = (
    "自己读材料、做笔记",
    "看视频、听人讲",
    "讲给别人听、讨论",
    "动手做出来",
)

#: 变量 → 维度名（顺序即结果里展示的顺序）。v2 起 11 个变量都有 facet。
FACETS: dict[str, tuple[str, ...]] = {
    "V-SE": ("定位", "抽取", "复述", "溯源"),
    "V-HSR": ("阈值", "表述", "渠道", "阻力"),
    "V-DC": ("展开", "定准", "复核", "可逆"),
    "V-IR": ("延迟", "拆分", "替代", "外部触发"),
    "V-PAR": ("留痕", "归因", "转化", "回看"),
    "V-ENV": ("听觉", "数字", "场所"),
    "V-ATT": ("单段", "切换", "恢复", "疲劳"),
    "V-CP": ("案例端", "抽象端"),
    "V-SMP": ("图", "听", "读写", "动觉"),
    "V-OSP": ("全局端", "线性端"),
    "V-ACT": ("读", "听看", "讲与讨论", "动手"),
}

#: 只有这两个变量的两端是**同一根轴的两头**，取"更偏的那端"作为变量分。
_PREFERENCE_TWO_END = ("V-CP", "V-OSP")
#: 这四个变量是四维，取最高两维的均值。
_FOUR_DIM = ("V-SMP", "V-ACT")


class SelfAssessmentError(ValueError):
    """作答不合法（缺题、重复、选项认不出、tier 不认识）。**不猜、不补默认值。**"""


@dataclass(frozen=True)
class VariableScore:
    variable_id: str
    score: float
    #: 逐维度/逐端的分数。**v2 起 11 个变量都有它** —— 单量表变量的 facet
    #: 只做展示与诊断，不改变变量分（变量分仍是全部条目的均值）。
    facets: dict[str, float] = field(default_factory=dict)
    #: 展示用的一句话（进 `boundary`，让"为什么是这个数"可核对）。
    boundary: str = ""


@dataclass(frozen=True)
class AssessmentResult:
    scores: tuple[VariableScore, ...]
    #: 有效性标记：`straight_lining` / `acquiescence:<变量>` / `too_fast` / `too_slow`。
    flags: tuple[str, ...]
    #: 附注（如"四维都 ≥8 → 无明显偏好"）。
    notes: tuple[str, ...]
    #: 分钟数；调用方没给开始时刻时为 `None`（**不编一个数**）。
    elapsed_minutes: float | None = None


def statements_for(tier: str = "full") -> tuple[Statement, ...]:
    """某一档的卷面。`short` 是 `full` 的子集，题号一致。"""
    if tier == "full":
        return STATEMENTS
    if tier == "short":
        return tuple(s for s in STATEMENTS if s.short)
    raise SelfAssessmentError(f"不认识的档位 {tier!r}（可选：{'、'.join(TIERS)}）")


def _points(statement: Statement, option: str) -> float:
    weight = OPTION_WEIGHTS[option]
    return 1.0 - weight if statement.reverse else weight


def _parse(answers: dict[int, str], statements: tuple[Statement, ...]) -> None:
    allowed = {s.no for s in statements}
    for no, value in answers.items():
        if no not in allowed:
            raise SelfAssessmentError(f"第 {no} 条不在这一档的卷面上")
        if value not in OPTION_WEIGHTS:
            options = "、".join(OPTION_WEIGHTS)
            raise SelfAssessmentError(f"第 {no} 条的选项 {value!r} 不合法（可选：{options}）")
    missing = [s.no for s in statements if s.no not in answers]
    if missing:
        raise SelfAssessmentError(
            f"第 {'、'.join(str(no) for no in missing)} 条没答 —— 缺题时不结算"
        )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _facet_means(items: list[Statement], answers: dict[int, str]) -> dict[str, float]:
    per_facet: dict[str, list[float]] = {}
    for statement in items:
        facet = statement.facet or "—"
        per_facet.setdefault(facet, []).append(_points(statement, answers[statement.no]))
    return {name: round(10.0 * _mean(values), 2) for name, values in per_facet.items()}


def score(
    answers: dict[int, str],
    *,
    elapsed_minutes: float | None = None,
    tier: str = "full",
) -> AssessmentResult:
    """作答 → 11 个变量的 `stated` 读数。**规则只有这一处。**

    三类变量算法不同：

    - 单量表变量：该变量**全部条目**的均值 × 10；facet 分数只做诊断；
    - 偏好变量（V-CP / V-OSP）：两端各自均值，两个数都留；
    - 四维变量（V-SMP / V-ACT）：四维各自均值，取**最高两维**；
      四维都 ≥8 → 记「无明显偏好」，不排顺序。
    """
    statements = statements_for(tier)
    _parse(answers, statements)

    by_variable: dict[str, list[Statement]] = {}
    for statement in statements:
        by_variable.setdefault(statement.variable_id, []).append(statement)

    scores: list[VariableScore] = []
    notes: list[str] = []

    for variable_id, items in by_variable.items():
        facet_scores = _facet_means(items, answers)

        if variable_id in _PREFERENCE_TWO_END:
            ordered = [facet_scores.get(name, 0.0) for name in FACETS[variable_id]]
            both_high = all(value >= 8 for value in ordered)
            if both_high:
                notes.append(f"{variable_id}：两端都 ≥8 → 两端都舒适，不挑一端")
            # M1 只存一个数：取**更偏的那一端**；两端并列时取第一个（与卷面顺序一致）。
            primary = max(ordered)
            descriptor = " / ".join(
                f"{name} {facet_scores.get(name, 0.0)}" for name in FACETS[variable_id]
            )
            scores.append(
                VariableScore(
                    variable_id=variable_id,
                    score=primary,
                    facets=facet_scores,
                    boundary=("两端都舒适；" if both_high else "") + descriptor,
                )
            )
            continue

        if variable_id in _FOUR_DIM:
            ranked = sorted(facet_scores.values(), reverse=True)
            top_two = ranked[:2]
            if all(value >= 8 for value in facet_scores.values()):
                notes.append(f"{variable_id}：四维都 ≥8 → 无明显偏好")
            scores.append(
                VariableScore(
                    variable_id=variable_id,
                    score=round(_mean(top_two), 2),
                    facets=facet_scores,
                    boundary=" / ".join(
                        f"{name} {facet_scores.get(name, 0.0)}"
                        for name in FACETS[variable_id]
                    ),
                )
            )
            continue

        # 单一量表：**全部条目**均值 × 10；facet 只做诊断，不参与变量分。
        value = round(10.0 * _mean(_points(s, answers[s.no]) for s in items), 2)
        scores.append(
            VariableScore(
                variable_id=variable_id,
                score=value,
                facets=facet_scores,
                boundary=" / ".join(
                    f"{name} {facet_scores.get(name, 0.0)}"
                    for name in FACETS[variable_id]
                ),
            )
        )

    flags: list[str] = []
    total = len(statements)

    # 直线作答：同一档占到全卷的 90% 以上。阈值随题量走，不写死。
    counts: dict[str, int] = {}
    for no in (s.no for s in statements):
        value = answers[no]
        counts[value] = counts.get(value, 0) + 1
    if counts and max(counts.values()) >= 0.9 * total:
        flags.append("straight_lining")

    # 反向不一致：同一变量里正向句与反向句（已取 1-w）的均值相差 > 3 分。
    for variable_id, items in by_variable.items():
        forward = [_points(s, answers[s.no]) for s in items if not s.reverse]
        reversed_items = [_points(s, answers[s.no]) for s in items if s.reverse]
        if forward and reversed_items:
            if abs(10 * _mean(forward) - 10 * _mean(reversed_items)) > 3:
                flags.append(f"acquiescence:{variable_id}")

    # 时长阈值也随题量走：按"每题 ~6 秒"设下限、"每题 ~54 秒"设上限。
    if elapsed_minutes is not None:
        minutes = max(0.0, float(elapsed_minutes))
        if minutes < 0.1 * total:
            flags.append("too_fast")
        elif minutes > 0.9 * total:
            flags.append("too_slow")

    return AssessmentResult(
        scores=tuple(scores),
        flags=tuple(flags),
        notes=tuple(notes),
        elapsed_minutes=elapsed_minutes,
    )


def public_scale(tier: str = "full") -> dict[str, Any]:
    """给界面的卷面：**只有题目与选项标签，没有任何计分信息。**"""
    statements = statements_for(tier)
    return {
        "tier": tier,
        "scale_version": SCALE_VERSION,
        "available_tiers": list(TIERS),
        "options": [
            {"value": value, "label": label} for value, label, _weight in OPTIONS
        ],
        "statements": [{"no": s.no, "text": s.text} for s in statements],
        "extra": {
            "attention_span": {
                "prompt": "你最能维持高质量专注的时长区间是？",
                "options": [
                    {"value": value, "label": label}
                    for value, label in ATTENTION_SPAN_OPTIONS
                ],
            },
            "activity_rank": {
                "prompt": "下面四种学习方式，按你实际用得最多排序（可留空）。",
                "options": list(ACTIVITY_RANK_OPTIONS),
            },
        },
        "total": len(statements),
        "full_total": len(STATEMENTS),
    }


def attention_span_label(value: str | None) -> str:
    """附加题的选项 → 一句人话。认不出就原样返回（不猜）。"""
    if not value:
        return ""
    for option, label in ATTENTION_SPAN_OPTIONS:
        if option == value:
            return label
    return value


__all__ = [
    "ACTIVITY_RANK_OPTIONS",
    "ASSESSMENT",
    "AssessmentResult",
    "ATTENTION_SPAN_OPTIONS",
    "FACETS",
    "OPTIONS",
    "SCALE_VERSION",
    "STATEMENTS",
    "SelfAssessmentError",
    "TIERS",
    "VariableScore",
    "attention_span_label",
    "public_scale",
    "score",
    "statements_for",
]

#: 让 `from ... import ASSESSMENT` 也能拿到（只读快照，便于测试比对卷面长度）。
ASSESSMENT = public_scale()
