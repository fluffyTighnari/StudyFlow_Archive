"""测量仪器审计器 —— 「每个方法凭什么变成一个数？」

它执行 `M1 §5 红线 7` / `Benchmark v0.3 §5.0`：

> **`measured` 通道的读数不得由「存在正确答案」的题产出。**

## 为什么需要它

这条红线如果只写在文档里，它一定会烂 —— 而 `Benchmark v0.3` §6.1 自己
预言过这件事（"题库没有校验工具就一定会烂"）。更具体地说：
M1 的变量模板里本来就有 `maps_to_score` 字段，但实测 **11 个变量共 40 个方法里
只有 5 个填了它**，而 §5.3 的聚合链**预设每个方法都产出一个单题分**。
空值不会报错，它只会让 §5.3 静默跳过那个方法 ——
症状是"这个变量的分数偏低"，不是"少算了一个方法"。

## 三种诊断必须分开

| 诊断 | 含义 | 级别 |
|---|---|---|
| `freetext` | 声明了，但不在词表里 → 无法分类是读数还是标尺 | ❌ |
| `red_line` | 已声明的 `measured` 仪器**全是标尺** → 这格测不出倾向 | ❌ |
| `undeclared` | 计分机制还不存在 → §5.3 会静默跳过它 | ⚠️ |

**这个三分法本身是踩过一次坑才有的。** 第一版把后两者混成一句，于是
11 个变量全被报成"测不出倾向" —— 而实际上只有 V-SE 是**已经知道**测不出，
其余 10 个是**还没写**。把「还不知道」说成「已经坏了」，
与把「省略」说成「改字」是同一个错误的两个面：**都会让这个工具失去可信度，
而它一旦不可信，真的那一条也没人看了。**

只报告，不改数据；每个 ❌ 都指出要动哪里。与 `tools/archcheck.py`、
`tools/checkitems.py` 同一族。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from app.seeds.m1_variables import _PREFERENCES, _STRATEGY_OPTIONS, _TRAITS

OK, WARN, BAD = "ok", "warn", "bad"
_MARK = {OK: "✅", WARN: "⚠️ ", BAD: "❌"}

#: `maps_to_score` 的**封闭词表**。值 → (含义, **是否产出读数**)。
#:
#: 第二项的口径是「**能不能产出一个关于这个人的读数**」，不是「能不能产出
#: `measured` 读数」—— `self_report` 确实产出读数，只是在 `stated` 通道上。
#: 第一版把两者混起来，于是把"自评条目"也报成了没有仪器。
#:
#: 只有两个值不产出读数：`ruler`（正误题，分数随能力移动）与 `none`（无定义）。
VOCAB: dict[str, tuple[str, bool]] = {
    "ruler": ("正误题：对/错可判定 —— **只做标尺，不产出读数**", False),
    "behavior": ("行为量 → 换算表（时限/延迟/次数/时长）", True),
    "coding": ("编码题：开放式回答落进固定类别", True),
    "choice": ("成对/多项偏好选择（无正误）", True),
    "rubric": ("评分锚 + 完整度判定", True),
    "self_report": ("条目均值（`stated` 通道）", True),
    "incremental": ("Session 观测累积（`experienced` 通道）", True),
    "none": ("外部证据 / 无定义", False),
}

#: `basis == measured` 的方法**允许声明**的词表值。
#:
#: `ruler` 合法 —— 但那等于自己承认"我只是个标尺"。
#: `none` 也合法 —— `external_evidence` 类（他人反馈/成果）本来就产出一个分数，
#: 它是**旁证**，不是仪器。
#:
#: ★ 这里曾经漏掉 `none`，于是 `MEASURED_LEGAL` 被当成了
#: 「能产出读数」的补集来用 —— 而这两件事不同：
#: 前者是"允许声明什么"，后者是"声明什么才有读数"。
#: 是 `test_no_measured_method_claims_a_non_measured_mechanism` 把它抓出来的。
MEASURED_LEGAL = {"behavior", "coding", "choice", "rubric", "ruler", "none"}

#: **跨通道**的机制：`measured` 方法声明这些，等于说"这一格的实测读数
#: 由自评条目 / Session 累积产出" —— 通道就串了。这才是真正该禁的。
OTHER_CHANNEL = {"self_report", "incremental"}

#: 真正能产出 `measured` 读数的机制。红线 7 检查的是这个集合**是不是空的**。
MEASURED_READING = {"behavior", "coding", "choice", "rubric"}


@dataclass
class Finding:
    level: str
    message: str
    #: `freetext` | `undeclared` | `red_line` | `ruler`
    tag: str = ""


@dataclass
class VarReport:
    variable_id: str
    display_name: str = ""
    measured_note: str = ""
    methods: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def worst(self) -> str:
        if any(f.level is BAD for f in self.findings):
            return BAD
        return WARN if self.findings else OK

    def has(self, tag: str) -> bool:
        return any(f.tag == tag for f in self.findings)

    @property
    def undeclared(self) -> list[dict]:
        return [m for m in self.methods if not m["maps_to_score"]]


def audit_variable(var: dict) -> VarReport:
    """审计一个变量。见模块文档的三种诊断。"""
    methods = var["measurement"]["methods"]
    report = VarReport(
        variable_id=var["variable_id"],
        display_name=var.get("display_name", ""),
        measured_note=var["measurement"]["evidence_channels"].get("measured") or "",
        methods=methods,
    )

    # ---- ① 自由文本：声明了但无法分类 ----
    for m in methods:
        val = m["maps_to_score"]
        if val and val not in VOCAB:
            report.findings.append(Finding(
                BAD,
                f"{m['method_id']} 的 maps_to_score={val!r} 不在词表里"
                f"（应为 {'/'.join(VOCAB)} 之一）—— 自由文本让"
                f"「这是读数还是标尺」无法被检查，而两者会被同等对待",
                "freetext",
            ))

    # ---- ② 未声明 ----
    if report.undeclared:
        report.findings.append(Finding(
            WARN,
            f"{len(report.undeclared)}/{len(methods)} 个方法**尚未声明计分机制**："
            f"{'、'.join(m['method_id'] for m in report.undeclared)}\n"
            f"        → 多数是 `behavioral_task` / `retrospective`：它们怎么变成一个数"
            f"是一个**还没做出的测量设计决定**，不是可以推导的默认值",
            "undeclared",
        ))

    # ---- ③ 红线 7：只对**已声明且可分类**的 measured 仪器下判 ----
    measured = [m for m in methods if m["basis"] == "measured"]
    declared = [
        m for m in measured
        if m["maps_to_score"] in MEASURED_LEGAL
    ]
    if not declared:
        if measured:
            report.findings.append(Finding(
                WARN,
                f"`measured` 侧 {len(measured)} 个方法**都还没声明计分机制** ——"
                f"红线 7 无法判定（还不知道它们是读数还是标尺）",
                "undeclared",
            ))
        return report

    readers = [m for m in declared if m["maps_to_score"] in MEASURED_READING]
    rulers = [m for m in declared if m["maps_to_score"] == "ruler"]
    pending = [m for m in measured if not m["maps_to_score"]]

    if readers:
        if rulers:
            report.findings.append(Finding(
                WARN,
                f"{len(rulers)} 个方法标为 `ruler`"
                f"（{'、'.join(m['method_id'] for m in rulers)}）"
                f" —— 它们进等值化的标尺池，**不进通道分**",
                "ruler",
            ))
        return report

    # 读数为空。★ 判别据：**「全是标尺」与「没有读数」不是同一件事**。
    # 第一版只看 `not readers`，于是把 V-HSR / V-PAR（一个标尺都没有）
    # 也判成了红线 7 —— 而它们根本没有正误题在冒充读数。
    if rulers:
        report.findings.append(Finding(
            BAD,
            f"★ **触犯红线 7：该变量的 `measured` 仪器全是标尺**"
            f"（{'、'.join(m['method_id'] for m in rulers)}）\n"
            f"        → 这一格**现在测不出倾向**，只能测出做这些题的能力。"
            f"要么把仪器改成编码题/行为量，要么这一格先只拿 stated/experienced",
            "red_line",
        ))
    else:
        # 没有任何东西产出 `measured` 读数，且也不是"标尺冒充读数"。
        # 两种子情况必须分开说：**待设计** vs **压根没有仪器**。
        tail = (
            f"（{'、'.join(m['method_id'] for m in pending)} 的计分机制待声明）"
            if pending else
            " —— `measured` 侧只有旁证（`none`），**没有任何仪器**"
        )
        report.findings.append(Finding(
            WARN,
            f"`measured` 通道声明了「{report.measured_note}」，"
            f"但当前没有任何方法产出它{tail}",
            "no_instrument",
        ))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="测量仪器审计：每个方法凭什么变成一个数")
    ap.add_argument("--variable", action="append", default=None,
                    help="只审计指定变量（可重复）；默认全部 11 个")
    ap.add_argument("--quiet", action="store_true", help="只打印有发现项的变量")
    args = ap.parse_args(argv)

    variables = _TRAITS + _PREFERENCES + _STRATEGY_OPTIONS
    if args.variable:
        wanted = set(args.variable)
        variables = [v for v in variables if v["variable_id"] in wanted]
        missing = wanted - {v["variable_id"] for v in variables}
        if missing:
            print(f"没有这些变量：{'、'.join(sorted(missing))}")
            return 2

    print("=" * 72)
    print("测量仪器审计（M1 §5 红线 7 / Benchmark §5.0）")
    print("=" * 72)

    reports: list[VarReport] = []
    total_methods = 0
    for var in variables:
        r = audit_variable(var)
        reports.append(r)
        total_methods += len(r.methods)
        if args.quiet and not r.findings:
            continue
        print(f"\n{_MARK[r.worst]} {r.variable_id} {r.display_name}")
        print(f"    measured 通道：{r.measured_note}")
        for m in r.methods:
            val = m["maps_to_score"] or "（未声明）"
            can = VOCAB.get(m["maps_to_score"], ("", False))[1]
            flag = "读数" if can else ("标尺" if m["maps_to_score"] == "ruler" else "—")
            print(f"      {m['method_id']:<9} {m['kind']:<19} basis={m['basis']:<10} "
                  f"{val:<12} [{flag}]")
        for f in r.findings:
            print(f"  {_MARK[f.level]} {f.message}")

    # ------------------------------------------------------------------ #
    # 汇总
    # ------------------------------------------------------------------ #
    total_empty = sum(len(r.undeclared) for r in reports)
    ruler_pool = sorted({
        m["method_id"] for r in reports for m in r.methods
        if m["maps_to_score"] == "ruler"
    })
    freetext = sorted(r.variable_id for r in reports if r.has("freetext"))
    red_line = sorted(r.variable_id for r in reports if r.has("red_line"))

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"变量：{len(reports)}   方法：{total_methods}")
    print(f"**尚未声明计分机制**的方法：{total_empty}/{total_methods}"
          f"（多为 `behavioral_task`/`retrospective`，是待做的测量设计决定）")
    if ruler_pool:
        print(f"标尺池（只做等值化，不进通道分）：{'、'.join(ruler_pool)}")

    if freetext:
        print(f"\n❌ **计分机制是自由文本、无法分类**：{'、'.join(freetext)}")
        print("   → 不在词表里的值会让「这是读数还是标尺」无法被检查。")
    if red_line:
        print(f"\n❌ **触犯红线 7（`measured` 仪器全是标尺）**：{'、'.join(red_line)}")
        print("   → 这些格子的 `measured` 读数要么改仪器（编码题/行为量），")
        print("     要么先只拿 stated/experienced。")
        print("   → **不要把空着的计分机制填一个值让它变绿** —— 那是发明而不是测量。")
    if freetext or red_line:
        return 1
    print("\n✅ 所有已声明的 `measured` 仪器都能产出一个倾向读数（没有正误题冒充读数）。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
