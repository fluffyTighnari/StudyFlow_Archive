"""生成自评单例：**卷面**与**计分键**（v2）。

从 `app.benchmark.self_assessment` 这一份定义出两份文件。这里**不再抄一份条目表** ——
旧版本把 44 条抄进了本文件，于是改动量表后卷面与程序各说各话，
而那种腐化不会报错（见模块 docstring 的说明）。现在条目、反向键、facet、
两档归属都只有一处定义。

三个刻意的设计：

1. **卷面不标反向句**。标了就等于告诉人"这句要反着选"，反向句当场失效 ——
   而它的全部作用就是拦住"一路选最右"的作答习惯。
2. **顺序交错，不按变量分组**。按变量分组会形成主题启动（刚答完四句"求助"，
   第五句还带着那个自我形象），交错 + 固定顺序 = 可复现，也不再按主题抱团。
3. **四档权重顶到两端**（0 / 1/3 / 2/3 / 1）。不顶到两端，"从来没有"的人拿不到 0 分、
   天花板也不是 10，而这个损失**不会报错**。

用法（在 `backend/` 下）：

    .venv/Scripts/python.exe tools/gen_selfreport.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.benchmark import self_assessment as scale  # noqa: E402
from app.seeds.m1_variables import VARIABLES as _SEED_VARIABLES  # noqa: E402

ROOT = BACKEND.parent
DOCS = ROOT / "design" / "docs"
SHEET = DOCS / "_自评单例_v0.2.md"
KEY = DOCS / "_自评计分键_v0.2.md"

#: 变量 → (中文名, 说明)。说明写进计分键，不写进卷面。
VARIABLES: dict[str, tuple[str, str]] = {
    item["variable_id"]: (item["display_name"], item["definition_what"])
    for item in _SEED_VARIABLES
}

#: 变量 → 该变量的条目（按卷面号）。
BY_VARIABLE: dict[str, list[scale.Statement]] = {}
for _statement in scale.STATEMENTS:
    BY_VARIABLE.setdefault(_statement.variable_id, []).append(_statement)


def _short_count(variable_id: str) -> int:
    return sum(1 for s in BY_VARIABLE[variable_id] if s.short)


def _reverse_count(variable_id: str) -> int:
    return sum(1 for s in BY_VARIABLE[variable_id] if s.reverse)


def render_sheet() -> str:
    total = len(scale.STATEMENTS)
    short_total = len(scale.statements_for("short"))
    lines: list[str] = [
        f"# 自评单例 {scale.SCALE_VERSION}（卷面 · 全面版 {total} 条）",
        "",
        f"> **这是一次性的全局单例**：全面版 {total} 条、简短版 {short_total} 条，"
        "做完就存下来，以后只在「你主动想重做」或「核对出现矛盾」时再做，"
        "不用每次跑卷子都做。",
        "> 全卷只读这一遍，**不要回头看前面的答案**；想太久反而会挑一个「我应该是那样」的答案。",
        "",
        "## 怎么填",
        "",
        "每题四个选项，选**最近三个月里实际最常发生**的那一档：",
        "",
        "| 选项 | 含义 |",
        "|---|---|",
    ]
    for value, label, _weight in scale.OPTIONS:
        lines.append(f"| **{value}** | {label} |")
    lines += [
        "",
        "三条提醒：",
        "",
        "1. **没有正确答案**，也没有「好的答案」。某些题目听起来像缺点，照实选。",
        "2. 如果两档都像，选**更常发生**的那一档，不要折中。",
        "3. 中途可以停（草稿存在本机），但**别一次只做几条** —— 断续做会让前后标准漂移。",
        "",
        f"全面版预计 20–30 分钟；简短版约 {round(short_total * 0.2)}–{round(short_total * 0.35)} 分钟。",
        "做完只需回一份清单，例如 `1C 2B 3D …`。",
        "",
        "> 简短版是全面版的**子集**，题号与全面版完全一致 —— 答过全面版的人，"
        "简短版里看到的是同一批句子，不是另一套。",
        "",
        "---",
        "",
    ]
    for statement in scale.STATEMENTS:
        lines.append(f"**{statement.no}.** {statement.text}")
        lines.append("")
    lines += [
        "---",
        "",
        f"## 附加两题（不进上面 {total} 条，单独作答）",
        "",
        "**X1（单选）** 你最能维持**高质量**专注的时长区间是？",
        "",
        "　".join(
            f"`{value} {label}`" for value, label in scale.ATTENTION_SPAN_OPTIONS
        ),
        "",
        "> 为什么单列：专注时长的本体是**区间**，不是四条句子的平均值。"
        "选最后一档的人，用 10 分钟任务永远测不到上限（那会记为删失，不是满分）。",
        "",
        "**X2（排序，可留空）** 下面四种学习方式，按你**实际用得最多**排序（写 1–4）：",
        "",
        "　".join(f"`( ) {label}`" for label in scale.ACTIVITY_RANK_OPTIONS),
        "",
        "> 允许留空：如果你觉得四种差不多，就留空 —— 那本身是有信息量的答案，"
        "比你随便排一个顺序更真实。",
        "",
        f"（本卷共 {total} 条 + 附加 2 题；条目顺序固定，第 {total} 条为末条。）",
        "",
    ]
    return "\n".join(lines)


def render_key() -> str:
    total = len(scale.STATEMENTS)
    short_total = len(scale.statements_for("short"))
    lines: list[str] = [
        f"# 自评单例 · 计分键 {scale.SCALE_VERSION}",
        "",
        "> **看卷面时不要读这一份**。它记着哪些是反向句 —— 被作答者看到，反向句当场失效。",
        "> 本文件由 `backend/tools/gen_selfreport.py` 从 "
        "`app/benchmark/self_assessment.py` 生成；**改量表改那一处，不要改这里**。",
        "",
        "## 1. 条目 → 变量映射（按卷面顺序）",
        "",
        "| 卷面号 | 变量 | 变量名 | 维度（facet） | 反向 | 简短版 |",
        "|---|---|---|---|---|---|",
    ]
    for statement in scale.STATEMENTS:
        name = VARIABLES.get(statement.variable_id, (statement.variable_id, ""))[0]
        lines.append(
            f"| {statement.no} | {statement.variable_id} | {name} | "
            f"{statement.facet or '—'} | {'**R**' if statement.reverse else 'F'} | "
            f"{'✓' if statement.short else ''} |"
        )
    lines += [
        "",
        "## 2. 权重与计分",
        "",
        "四档权重**顶到两端**（这是硬要求，不是风格）：",
        "",
        "| 选项 | 从不 | 偶尔 | 经常 | 几乎总是 |",
        "|---|---|---|---|---|",
        "| 原始 w | 0 | 1/3 | 2/3 | 1 |",
        "",
        "计分链：",
        "",
        "```",
        "单条得分 x_i = 10 × w_i            （反向句 w_i = 1 − w_raw）",
        "变量分   = 10 × mean(x_i)          （i 取该变量的全部条目；偏好/四维除外，见 §3）",
        "```",
        "",
        "**不顶到两端会怎样**：如果最低档给 0.1、最高档给 0.9，那么"
        "「从来没有」的人拿到 1 分而不是 0 分，天花板是 9 分。"
        "这个偏差不会报错、不会显示，只会安静地把所有人的分数压向中间。",
        "",
        "## 3. 四类变量的算法**不一样**（这里最容易被抹平）",
        "",
        "| 变量 | 算法 | 为什么不能取总均值 |",
        "|---|---|---|",
        "| V-SE / V-HSR / V-DC / V-IR / V-PAR / V-ENV / V-ATT | 全部条目均值 × 10；facet 分**只做诊断** | 单一量表的多个条目；facet 不参与变量分 |",
        "| **V-CP** | 案例端、抽象端各自均值；**两个数都留**，变量分取更高的一端 | 偏好是两端各有一个高度，不是一条轴上的位置 |",
        "| **V-OSP** | 全局端、线性端各自均值；**两个数都留**，变量分取更高的一端 | 同上；且按 M1 红线 3 不作稳定基线，只作参考 |",
        "| **V-SMP / V-ACT** | 四维**各自**得分，取最高两维的均值 | 四维不是同一量表的四个条目；取均值等于把一张四维图压成一个点 |",
        "",
        "## 4. 两个「不要强行下结论」的情形",
        "",
        "| 情形 | 判定 | 不要做什么 |",
        "|---|---|---|",
        "| V-SMP / V-ACT 四维都 ≥8 | 记 **「无明显偏好」** | 不要为了让结果好看而排出一个顺序 |",
        "| V-CP / V-OSP 两端都 ≥8 | 记 **「两端都舒适」** | 不要挑一个当成偏好；这时默认呈现方式应回落到「给两版」 |",
        "",
        "## 5. 有效性检查（不做这一步，上面全部算白算）",
        "",
        "阈值**随题量走**，不写死数字：全面版与简短版各自按自己的条数计算。",
        "",
        "| 检查 | 触发条件 | 处理 |",
        "|---|---|---|",
        f"| **直线作答** | ≥ {total} × 90% 条选同一档 | 标记 `straight_lining`，置信度降为 `provisional`，**不当作极端值** |",
        "| **反向不一致** | 某变量「正向均值」与「反向反转后均值」相差 >3 分 | 标记 `acquiescence:<变量>`，该变量 `confidence=low` |",
        f"| **过快** | 全程 < {total} × 0.1 分钟（全面版） | 标记 `too_fast`，置信度降级 |",
        f"| **过慢** | 全程 > {total} × 0.9 分钟（全面版） | 不降级，但记下（可能在中途查资料/校准标准） |",
        "",
        "> 反向句的判法：一个变量若正向均值 8、反向句（已取 1−w）均值 3，"
        "差的不是他的人格，是他答题时的一贯偏移。**这时不能取平均** —— 平均会把偏移洗掉，"
        "得到一个两边都不像的数。",
        "",
        "## 6. 反向句覆盖（诚实说明）",
        "",
        "| 变量 | 条目 | 反向 | 简短版 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for variable_id, items in BY_VARIABLE.items():
        name = VARIABLES.get(variable_id, (variable_id, ""))[0]
        reverse = _reverse_count(variable_id)
        facet_count = len(scale.FACETS[variable_id])
        if variable_id in ("V-SMP", "V-ACT"):
            note = f"{facet_count} 个维度各有正反指标；不用「反向句」这一说法"
        else:
            note = f"{facet_count} 个 facet：{reverse} 条反向"
        lines.append(
            f"| {variable_id} | {len(items)} | {reverse} | {_short_count(variable_id)} | {name}；{note} |"
        )
    lines += [
        "",
        f"> **V-SMP / V-ACT 早期版本 0 条反向句**，v2 起每个维度都补了正反指标："
        "四维各有一条句子的反向形式（如「光看演示不动手，我总觉得没学会」）。"
        "四维仍然取最高两维，反向句只防美化，不重复计入同一维度。",
        "",
        "## 7. 两档",
        "",
        f"| 档位 | 条数 | 用途 | 置信度 |",
        "|---|---|---|---|",
        f"| `full` | {total} | 稳定基线 | 按有效性标记决定 |",
        f"| `short` | {short_total} | 快速方向；覆盖全部 11 个变量 | 固定 `hypothesis` |",
        "",
        "两档题号**完全一致**：`short` 是 `full` 的子集，所以共用一份计分器，"
        "历史作答（只存卷面号）也能对上。简短版每个 facet 至少 1 条、偏好变量每端 2 条，"
        "足以给方向，不足以做稳定基线。",
        "",
        "## 8. 更新规则（它是「全局单例」的原因）",
        "",
        "- 第一次基线：做一次，写进 `stated` 通道，11 个变量各得一个读数；",
        "- 之后：**不随卷子重做**。只在两种情况下重做 ——",
        "  1. 你主动想重做（比如觉得某几条已经不符合现状）；",
        "  2. 矛盾核对把某个变量的 `stated` 判为冲突（那时只重做该变量，不重做全卷）。",
        "- 每次重做都留版本：`stated` 读数带 `observed_at`，旧值不删 —— "
        "把两次的差值当成信息，而不是覆盖掉。",
        "",
    ]
    return "\n".join(lines)


def verify() -> None:
    problems: list[str] = []
    statements = scale.STATEMENTS
    total = len(statements)
    short = scale.statements_for("short")

    numbers = [s.no for s in statements]
    if numbers != list(range(1, total + 1)):
        problems.append("卷面号不是从 1 开始的连续整数")
    if len(set(numbers)) != total:
        problems.append("卷面号有重复")

    short_nos = {s.no for s in short}
    full_nos = set(numbers)
    if not short_nos < full_nos:
        problems.append("简短版不是全面版的真子集")
    if len(short_nos) != len(short):
        problems.append("简短版有重复条目")

    if set(BY_VARIABLE) != set(scale.FACETS):
        problems.append(
            f"变量集合与 FACETS 不一致：{sorted(set(BY_VARIABLE) ^ set(scale.FACETS))}"
        )
    for variable_id, items in BY_VARIABLE.items():
        facets = {s.facet for s in items}
        expected = set(scale.FACETS[variable_id])
        if facets != expected:
            problems.append(
                f"{variable_id} 的 facet 与 FACETS 不一致："
                f"缺 {sorted(expected - facets)}、多 {sorted(facets - expected)}"
            )
        for facet in expected:
            if not any(s.facet == facet for s in items):
                problems.append(f"{variable_id} 的 facet {facet} 没有条目")
        if variable_id not in ("V-SMP", "V-ACT") and not any(
            s.reverse for s in items
        ):
            problems.append(f"{variable_id} 没有反向句")
        if not any(s.short for s in items):
            problems.append(f"{variable_id} 在简短版里一条都没有")

    for statement in statements:
        if statement.variable_id not in scale.FACETS:
            problems.append(f"第 {statement.no} 条指向未知变量 {statement.variable_id}")
        if statement.text.strip() == "":
            problems.append(f"第 {statement.no} 条没有题面")

    if scale.OPTION_WEIGHTS.get("A") != 0.0 or scale.OPTION_WEIGHTS.get("D") != 1.0:
        problems.append("A / D 档权重没有顶到端点")

    for tier in scale.TIERS:
        try:
            got = scale.statements_for(tier)
        except scale.SelfAssessmentError as exc:  # pragma: no cover
            problems.append(f"{tier} 档取不出来：{exc}")
            continue
        if not got:
            problems.append(f"{tier} 档是空的")
    try:
        scale.statements_for("tiny")
    except scale.SelfAssessmentError:
        pass
    else:
        problems.append("不认识的档位没有被拒绝")

    if problems:
        raise SystemExit("自评单例有问题：\n  " + "\n  ".join(problems))


def main() -> None:
    verify()
    DOCS.mkdir(parents=True, exist_ok=True)
    SHEET.write_text(render_sheet(), encoding="utf-8")
    KEY.write_text(render_key(), encoding="utf-8")
    total = len(scale.STATEMENTS)
    short_total = len(scale.statements_for("short"))
    print(f"卷面 {SHEET.name}  {SHEET.stat().st_size / 1024:.1f}KB  {total} 条")
    print(f"计分键 {KEY.name}  {KEY.stat().st_size / 1024:.1f}KB  {short_total} 条（简短版）")
    print("条目分布：" + "  ".join(
        f"{variable_id} {len(items)}({_short_count(variable_id)})"
        for variable_id, items in BY_VARIABLE.items()
    ))
    print(
        f"反向句 {sum(1 for s in scale.STATEMENTS if s.reverse)} 条；档位权重 "
        f"{[weight for _value, _label, weight in scale.OPTIONS]}"
    )


if __name__ == "__main__":
    main()
