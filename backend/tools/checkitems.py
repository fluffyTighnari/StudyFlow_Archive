"""题库校验器 —— 「这批题目能不能被真的判分？」

纯数据检查，无运行时依赖。可作为 CLI 独立运行：

    cd backend && python -m tools.checkitems --items ../items --materials ../materials

它回答**两个会静默出错**的问题：

1. ★ **每条埋点引文是否真的在材料原文里** —— 否则 `key_points` 的评分指南
   建立在一句不存在的话上。判分时没人会发现：要点清单还是那份清单，
   照它评出来的分却对不上原文。
2. ★ **每个选择题的答案是否真的能在选项列表里找到** —— 否则**正确答案会被判错**，
   而它表现为"这个人这题答错了"，不是"这批数据坏了"。

第三个是提示性的：**多空填空题不能按 `exact`（对=10 / 错=0）给分** ——
两空全对与对一空拿一样的分，那会让分数失去分辨力。

设计取向与 `tools/archcheck.py` 同族：**只报告，不改数据**；
每个 `❌` 都给出"要改哪里"而不是只给"这里错了"。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 规格与文本原语取自 `app.benchmark.items` —— **单一真相**。
# 校验器与抽题/计分必须对「一道题长什么样」「引文算不算命中」给出同一个答案；
# 各存一份的下场是：校验器放行的题，判分器读不懂。
from app.benchmark.items import (
    ANSWER_SEP as _ANSWER_SEP,
    BLANK as _BLANK,
    ELLIPSIS as _ELLIPSIS,
    PAREN as _PAREN,
    RETIRED_FIELDS,
    SPEC_BLOCKING,
    SPEC_SAMPLING,
    divergence as _divergence,
    fold as _fold,
    follows_boundary as _follows_boundary,
    no_paren as _no_paren,
    norm as _norm,
    strip_tail as _strip_tail,
)
from app.benchmark.topics import TopicError, label_of, require_topics, validate_catalog

# --------------------------------------------------------------------------- #
# 发现项
# --------------------------------------------------------------------------- #

OK, WARN, BAD = "ok", "warn", "bad"

_MARK = {OK: "✅", WARN: "⚠️ ", BAD: "❌"}


@dataclass
class Finding:
    level: str
    message: str
    #: 分类标签。**汇总必须区分「数据错了」与「字段缺了」** ——
    #: 这两者的修法完全不同（前者要改内容，后者是机械补齐），
    #: 混成一句"引文或答案对不上"会把机械问题说成内容问题。
    tag: str = ""


@dataclass
class ItemReport:
    path: Path
    item_id: str = "?"
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, message: str, tag: str = "") -> None:
        self.findings.append(Finding(level, message, tag))

    @property
    def worst(self) -> str:
        if any(f.level is BAD for f in self.findings):
            return BAD
        if any(f.level is WARN for f in self.findings):
            return WARN
        return OK

    def bad_tags(self) -> set[str]:
        return {f.tag for f in self.findings if f.level is BAD and f.tag}


# --------------------------------------------------------------------------- #
# 单项检查
# --------------------------------------------------------------------------- #


def check_material_quotes(item: dict, material: str, report: ItemReport) -> None:
    """★ 逐段核对 `anchors` 里的每一条 `evidence_sentences`。

    这一条是本工具存在的主要理由：**评分指南是照着这些引文写的**，
    而引文如果不在了（材料改过、或引文是凭记忆写的），
    要点清单就会指向原文里没有的东西。

    **四层判定，区分「省略」与「改字」**：

    | 层 | 判据 | 结论 |
    |---|---|---|
    | 1 | 去空白后逐字命中 | ✅ 逐字 |
    | 2 | 再抹平引号/破折号写法、去掉片段末尾句末标点后命中 | ⚠️ 仅标点差异 |
    | 3 | 再去掉括号注释后命中 | ⚠️ 略去括号注释 |
    | 4 | 都不命中 | ❌ 报出分歧点 |

    **为什么第 2、3 层是 ⚠️ 而不是 ❌**：省略与标点写法**不是改字**。
    把这类正确的引文报成错误，会让这个工具失去可信度 ——
    而它一旦不可信，第 4 层的真问题也没人看了。
    """
    anchors = item.get("anchors")
    if not isinstance(anchors, dict):
        report.add(BAD, "缺少 `anchors`（埋点）—— 没有埋点就无法核对评分指南", "quote")
        return

    hay = _norm(material)
    hay_fold = _fold(material)
    hay_fold_noparen = _no_paren(hay_fold)

    groups: list[tuple[str, str]] = []
    thread = anchors.get("main_thread")
    if isinstance(thread, dict):
        for sent in thread.get("evidence_sentences") or []:
            groups.append(("主线", sent))
    for i, fact in enumerate(anchors.get("key_facts") or [], start=1):
        if isinstance(fact, dict):
            for sent in fact.get("evidence_sentences") or []:
                groups.append((f"关键事实{i}", sent))
    for i, cond in enumerate(anchors.get("limit_conditions") or [], start=1):
        if isinstance(cond, dict):
            for sent in cond.get("evidence_sentences") or []:
                groups.append((f"限定条件{i}", sent))

    if not groups:
        report.add(BAD, "`anchors` 里一条 `evidence_sentences` 都没有 —— 无法核对", "quote")
        return

    exact = 0
    by_punct: list[str] = []
    by_paren: list[str] = []
    truncated: list[str] = []
    missing: list[str] = []

    for label, sent in groups:
        spans = [s for s in _ELLIPSIS.split(sent) if _norm(s)]
        if not spans:
            missing.append(f"{label}：引文为空")
            continue
        for span in spans:
            if _norm(span) in hay:
                exact += 1
                continue
            sf = _strip_tail(span)
            pos = hay_fold.find(sf)
            if pos >= 0:
                if _follows_boundary(hay_fold, pos, len(sf)):
                    by_punct.append(f"{label}：「{span[:34]}…」")
                else:
                    truncated.append(
                        f"{label}：「{span[-14:]}」之后原文是"
                        f"「{hay_fold[pos + len(sf) : pos + len(sf) + 12]}」"
                        f" —— 引文在词中间截断"
                    )
                continue
            sfp = _no_paren(sf)
            if sfp and sfp in hay_fold_noparen:
                # 括号注释在**原文**那一侧：定位到原文窗口再把它们捞出来
                _, _, hit = _divergence(sf, hay_fold)
                wp = hay_fold.find(sf[:hit]) if hit else -1
                window = hay_fold[wp : wp + len(sf) + 48] if wp >= 0 else ""
                elided = "、".join(_PAREN.findall(window))
                by_paren.append(
                    f"{label}：原文此处有括号注释"
                    f"「{elided or '（未能定位原文括号）'}」，引文略去了"
                )
                continue
            frag_tail, mat_tail, hit = _divergence(sf, hay_fold)
            missing.append(
                f"{label}：命中前 {hit} 字后分歧 —— "
                f"引文「{frag_tail}」/ 原文「{mat_tail}」"
            )

    total = exact + len(by_punct) + len(by_paren) + len(truncated) + len(missing)

    if missing:
        report.add(
            BAD,
            f"埋点引文核对：{total} 段里 {len(missing)} 段**与原文有实质分歧**"
            f"（不是标点、也不是省略）—— 评分指南可能建立在不存在的句子上：",
        )
        for m in missing:
            report.add(BAD, f"    · {m}", "quote")
    if truncated:
        report.add(
            BAD,
            f"埋点引文核对：{len(truncated)} 段**在词中间截断**（补全到词/分句边界即可）：",
        )
        for m in truncated:
            report.add(BAD, f"    · {m}", "quote")
    if by_paren:
        report.add(
            WARN,
            f"埋点引文核对：{len(by_paren)} 段**略去了原文的括号注释**"
            f"（合法 —— 但要点清单里最好带上原文那一处）：",
        )
        for m in by_paren:
            report.add(WARN, f"    · {m}")
    if by_punct:
        report.add(
            WARN,
            f"埋点引文核对：{len(by_punct)} 段**仅标点/引号写法不同**"
            f"（建议统一，免得下次核对又要人来判断）：",
        )
        for m in by_punct:
            report.add(WARN, f"    · {m}")
    if not missing and not truncated and not by_paren and not by_punct:
        report.add(
            OK,
            f"埋点引文核对：{total} 段**全部逐字命中原文**（省略号拼接已逐段核对）",
        )
    elif not missing and not truncated:
        report.add(
            OK,
            f"埋点引文核对：{exact}/{total} 段逐字命中，其余为省略/标点差异，**无实质分歧**",
        )


def check_exact_questions(item: dict, report: ItemReport) -> None:
    """★ 答案是否真的能在选项里找到；以及多空填空的给分口径。"""
    questions = item.get("exact_questions")
    if not isinstance(questions, list) or not questions:
        report.add(BAD, "没有 `exact_questions` —— 提取题之外没有可客观判分的题", "answer")
        return

    in_options: set[str] = set()
    bad_answer: list[str] = []
    multi_blank: list[str] = []

    for q in questions:
        if not isinstance(q, dict):
            continue
        no = q.get("no", "?")
        qtype = q.get("type")
        answer = q.get("answer")
        blanks = len(_BLANK.findall(str(q.get("question") or "")))
        parts = [p for p in _ANSWER_SEP.split(str(answer or "")) if p.strip()]

        if not answer:
            bad_answer.append(f"第 {no} 题没有 `answer`")
            continue

        if qtype == "choice":
            options = q.get("options") or []
            hit = next((o for o in options if _norm(str(o)) == _norm(str(answer))), None)
            if hit is not None:
                in_options.add(f"第 {no} 题")
            else:
                close = difflib.get_close_matches(
                    _norm_loose(str(answer)),
                    [_norm_loose(str(o)) for o in options],
                    n=1,
                    cutoff=0.0,
                )
                ratio = 0.0
                if close:
                    ratio = difflib.SequenceMatcher(
                        None, _norm_loose(str(answer)), close[0]
                    ).ratio()
                bad_answer.append(
                    f"第 {no} 题的答案**不在 options 里**"
                    f"（最接近的选项相似度 {ratio:.0%}）—— 这会让正确答案被判错"
                )
        elif qtype == "fill":
            if blanks > 1:
                if len(parts) != blanks:
                    multi_blank.append(
                        f"第 {no} 题有 {blanks} 个空，但 `answer` 拆出 {len(parts)} 段"
                        f" —— 无法按空判分"
                    )
                else:
                    multi_blank.append(
                        f"第 {no} 题有 {blanks} 个空（answer 已按 `；` 分段）"
                        f" —— **不能按 exact（对=10/错=0）给分**，需按空比例计分"
                    )
            elif len(parts) > 1:
                multi_blank.append(
                    f"第 {no} 题只 1 个空但 answer 拆出 {len(parts)} 段 —— 口径不一致"
                )

    choices = [q for q in questions if isinstance(q, dict) and q.get("type") == "choice"]
    if bad_answer:
        for b in bad_answer:
            report.add(BAD, b, "answer")
    else:
        report.add(OK, f"选择题答案核对：{len(choices)} 道题的答案**都能在 options 里命中**")

    for m in multi_blank:
        report.add(WARN, m)


def check_key_points(item: dict, report: ItemReport) -> None:
    kp = item.get("key_points_question")
    if not isinstance(kp, dict):
        report.add(BAD, "没有 `key_points_question`", "content")
        return
    guide = kp.get("scoring_guide")
    if not isinstance(guide, list) or len(guide) < 3:
        report.add(
            BAD,
            "`key_points_question.scoring_guide` 不足 3 条 —— "
            "要点清单必须能判定「命中了没有」，否则判分是评分者的自由裁量",
        )
        return
    # 指南应当覆盖「主线 / 关键事实 / 限定条件」三面
    joined = "".join(str(g) for g in guide)
    missing = [k for k in ("主线", "关键事实", "限定条件") if k not in joined]
    if missing:
        report.add(WARN, f"`scoring_guide` 未覆盖：{'、'.join(missing)}")
    else:
        report.add(OK, f"要点清单：{len(guide)} 条，覆盖主线/关键事实/限定条件三面")


def check_spec_fields(item: dict, report: ItemReport) -> None:
    # ★ 已删除字段：**报成错误而不是忽略**。
    # 一道题带着旧的 `domain` 而没人发现，它的抽样资格就永远是错的 ——
    # 而它看起来"只是多了一个字段"，没有任何症状。
    for gone, why in RETIRED_FIELDS.items():
        if gone in item:
            report.add(
                BAD,
                f"还在用**已删除**的字段 `{gone}`（{item[gone]!r}）—— {why}",
                "spec",
            )

    missing_blocking = [
        f"{k}（{v}）" for k, v in SPEC_BLOCKING.items() if not item.get(k)
    ]
    missing_sampling = [
        f"{k}（{v}）" for k, v in SPEC_SAMPLING.items() if item.get(k) is None
    ]
    if missing_blocking:
        report.add(BAD, "**阻断链路**的规格字段缺失：", "spec")
        for m in missing_blocking:
            report.add(BAD, f"    · {m}", "spec")
    if missing_sampling:
        report.add(WARN, "**会让抽样静默失准**的字段缺失：", "spec")
        for m in missing_sampling:
            report.add(WARN, f"    · {m}", "spec")

    # ★ 「后续所有题目都按这份清单，不要给随机题目」的落点。
    # 拼错一个 topic_id = 这道题抽不出来（或抽错），而它不会报错 ——
    # 所以这里必须查，且必须查**主题表**而不是题目自己说了什么。
    raw_topics = item.get("topics")
    if isinstance(raw_topics, list) and raw_topics:
        try:
            require_topics(raw_topics, where=report.path.name)
        except TopicError as exc:
            report.add(BAD, str(exc), "spec")
        else:
            names = "、".join(
                f"{t}（{label_of(t)}）" for t in raw_topics
            )
            report.add(OK, f"主题：{names}", "spec")
    elif raw_topics is not None:
        report.add(
            BAD,
            f"`topics` 必须是非空列表（现在：{raw_topics!r}）—— "
            f"它是「按清单抽题」的唯一依据",
            "spec",
        )

    if not missing_blocking and not missing_sampling:
        report.add(OK, "规格字段齐全", "spec")


# --------------------------------------------------------------------------- #
# 单份题目
# --------------------------------------------------------------------------- #


def check_one(path: Path, materials_dir: Path) -> ItemReport:
    report = ItemReport(path=path)
    try:
        item = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - 报告要包含任何解析失败
        report.add(BAD, f"JSON 解析失败：{exc}")
        return report

    if not isinstance(item, dict):
        report.add(BAD, "顶层不是对象")
        return report

    report.item_id = str(item.get("item_id") or item.get("id") or "?")

    # 材料
    ref = item.get("material_ref")
    material_path = (
        (materials_dir.parent / ref) if ref else (materials_dir / f"{report.item_id}.md")
    )
    if not material_path.is_file():
        report.add(BAD, f"材料文件不存在：{material_path}")
        material = ""
    else:
        material = material_path.read_text(encoding="utf-8-sig")
        report.add(OK, f"材料：{material_path.name}（{len(_norm(material))} 字，去空白后）")

    if material:
        check_material_quotes(item, material, report)
    check_key_points(item, report)
    check_exact_questions(item, report)
    check_spec_fields(item, report)
    return report


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="checkitems", description="题库校验器（只报告，不改数据）"
    )
    parser.add_argument(
        "--items", default="items", help="题目 JSON 所在目录（默认 items/）"
    )
    parser.add_argument(
        "--materials", default=None, help="材料目录（默认 <items>/../materials）"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="只打印 ❌ 与汇总"
    )
    args = parser.parse_args(argv)

    items_dir = Path(args.items)
    if not items_dir.is_dir():
        print(f"❌ 题目目录不存在：{items_dir.resolve()}")
        return 2
    materials_dir = (
        Path(args.materials) if args.materials else items_dir.parent / "materials"
    )

    paths = sorted(p for p in items_dir.glob("*.json"))
    if not paths:
        print(f"❌ {items_dir.resolve()} 下没有 *.json")
        return 2

    # ★ 先查**主题表自己**有没有坏。
    # 题目引用得对，前提是那张表本身没坏 —— 若表里有个重复 id，
    # 那么"每道题的 topics 都存在"这句话就没有意义了。
    catalog_problems = validate_catalog()
    if catalog_problems:
        print("❌ 主题表本身有问题（`app/seeds/domain_topics.json`）：")
        for p in catalog_problems:
            print(f"    · {p}")
        print()
        return 2

    reports = [check_one(p, materials_dir) for p in paths]

    # 重复 id
    seen: dict[str, int] = {}
    for r in reports:
        seen[r.item_id] = seen.get(r.item_id, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}

    for r in reports:
        print(f"\n── {r.item_id}  {_MARK[r.worst]}  ({r.path.name}) " + "─" * 20)
        for f in r.findings:
            if args.quiet and f.level is not BAD:
                continue
            print(f"  {_MARK[f.level]} {f.message}")

    print("\n" + "=" * 68)
    print("汇总")
    print("=" * 68)

    bad_items = [r.item_id for r in reports if r.worst is BAD]
    warn_items = [r.item_id for r in reports if r.worst is WARN]
    print(f"题目总数：{len(reports)}")
    print(f"  ✅ 通过：{len(reports) - len(bad_items) - len(warn_items)}")
    print(f"  ⚠️  有提示：{len(warn_items)}  {('、'.join(warn_items)) if warn_items else ''}")
    print(f"  ❌ 有阻断项：{len(bad_items)}  {('、'.join(bad_items)) if bad_items else ''}")
    if dupes:
        print(f"  ❌ 重复 item_id：{dupes}")

    # ★ 区分「数据错了」与「字段缺了」—— 修法不同，不能并成一句话
    data_bad = [r.item_id for r in reports if r.bad_tags() & {"quote", "answer"}]
    spec_bad = [r.item_id for r in reports if r.bad_tags() & {"spec"}]
    if data_bad:
        print(
            f"\n❌ **引文或答案对不上**：{'、'.join(data_bad)}\n"
            f"   → 先修这些。分数是照着引文与答案算的，**不修就是错的**。"
        )
    if spec_bad:
        print(
            f"\n⚠️  **仅缺规格字段**：{'、'.join(spec_bad)}\n"
            f"   → 这些是**机械补齐**（从 `id`/批次推断即可），内容本身没问题。"
        )

    # 难度分布 vs 设计要求（易 1 : 中 2 : 难 1）
    diffs: dict[str, int] = {}
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig")).get("difficulty")
        except Exception:  # noqa: BLE001
            continue
        if d:
            diffs[str(d)] = diffs.get(str(d), 0) + 1
    if diffs:
        print(f"\n难度分布：{diffs}")
        missing_d = [k for k in ("易", "中", "难") if k not in diffs]
        if missing_d:
            print(
                f"  ⚠️  缺少 {'、'.join(missing_d)} —— 抽样规则要求单次抽取的难度构成"
                f"为「易 1 : 中 2 : 难 1」，题池里没有的档位凑不出来。"
            )

    print()
    if data_bad:
        print("❌ 有题目的**引文或答案对不上** —— 先修这些，否则分数是错的。")
        return 1
    if spec_bad:
        print("✅ 全部题目的引文与答案都能核对上；差异只在规格字段（可机械补齐）。")
        return 1
    print("✅ 全部题目的引文与答案都能核对上，规格字段齐全。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
