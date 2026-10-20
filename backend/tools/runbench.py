"""`runbench` —— 离线跑一整卷（出卷 → 作答 → 落库 → 计分 → 分数报告）。

## 它为什么必须存在

基准块的每一段机制都有端点（抽题 / 逐题落库 / 计分 / 等值化），但**没有一条
能把它们串成一次真实的考试**。缺了这条串，只有三种人会用到基准：

1. 写测试的人（他直接 POST 端点）；
2. 前端（还没写完）；
3. 你 —— 而你要做的是**花 30 或 60 分钟做一卷**，这件事必须有一个
   不依赖界面的入口，否则"机制做完了"这句话没有验证面。

它也是**唯一能把「分数」这件事完整走一遍的地方**：抽题 → 逐题落 →
判官 → 聚合 → 等值化 → 报告。

## 两个动作，一条线

```powershell
# ① 出卷（抽题 + 开一次运行 + 导出可作答的卷子）
python -m tools.runbench --base-url http://127.0.0.1:8125 `
  --form quick --variable V-SE --channel behavioral_task --method M-SE-03 `
  --items-dir tests/fixtures/bank --out var/paper.json

# ② 交卷（读回填好答案的卷子 → 逐题落库 → 计分 → 报告）
python -m tools.runbench --base-url http://127.0.0.1:8125 --paper var/paper.json
```

## 五条口径（每一条都对应一个会骗人的失败态）

- **替用户作答是不允许的。** 卷子里每一道题的 `answer` 一开始都是空串。
  工具不会给任何一题填默认答案 —— 分数只有在**人真的答了**之后才存在。
- **没答的题不上报**，因此它在库里是 `NULL`（没测到），**不是 0**。
  报成 0 会让"他跳过了这道"与"他答错了这道"变成同一个数。
- **`0.0` 与「没测到」在报告里必须看得出区别。** 报告逐题打印
  `分数` 或 `没测到（原因）`，两者不共用"0"这个符号。
- **判分规则不住在这里。** 计分全部由服务端算（`POST /runs/{id}/score`）；
  这个工具**从不自己算一个分** —— 两份实现不会一直一致，
  而它们不一致的表现是同一个人的两个分数都不报错。
- **卷子文件带着 `run_id`。** 交卷时用的是**出卷那一次**运行，
  不会新开一次 —— 否则"题是抽这批、分记在那批"这种事会静悄悄地发生。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

#: 报告里"没测到"的写法。**它不是 0，也不是空** —— 三个字必须出现。
NOT_MEASURED = "没测到"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _request(
    base: str,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    query: dict | None = None,
    timeout: int = 600,
) -> tuple[int, Any]:
    """发一次请求。**HTTP 错误不当异常抛** —— 它的消息本身就是要显示的东西。"""
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code, payload = response.status, response.read()
    except urllib.error.HTTPError as exc:
        code, payload = exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, {"message": f"连不上服务（{exc.reason}）—— 先起服务再跑"}
    try:
        parsed = json.loads(payload) if payload else None
    except json.JSONDecodeError:
        parsed = payload.decode("utf-8", "replace")[:300]
    return code, parsed


def _refusal(body: Any) -> str:
    """把一次失败响应里**最该给人看的那句**取出来。"""
    if isinstance(body, dict):
        return str(body.get("message") or body.get("detail") or body)[:300]
    return str(body)[:300]


# --------------------------------------------------------------------------- #
# 题库：出卷要把题目正文读出来
# --------------------------------------------------------------------------- #


def load_bank(items_dir: Path) -> dict[str, Any]:
    """读题目目录 → `{item_id: BenchItem}`。**用的是运行时那个加载器**。

    ★ 不自己写一份解析：两份判断的下场是「校验器放行的题，出卷工具读不懂」
    （P11 的题型分支就是这么来的）。这里只做一件事 —— 把它变成按 id 取的字典。

    ★ 坏文件不跳过：`load_bank` 的文档已经写了为什么（跳过会悄悄少测一格），
    这里只把它的异常翻译成一句能照做的提示。
    """
    from app.benchmark.items import ItemSpecError  # noqa: PLC0415 - 只有出卷时要
    from app.benchmark.items import load_bank as load_items  # noqa: PLC0415

    try:
        items = load_items(items_dir)
    except ItemSpecError as exc:
        raise ValueError(str(exc)) from exc
    return {item.item_id: item for item in items}


def build_paper(plan: dict, bank: dict[str, Any], *, run_id: str) -> dict:
    """把一次抽题结果变成**可作答的卷子**。

    ★ 每一道题的 `answer` 都是空串 —— 这是这个工具的立身之本：
    它准备一份卷子，而不是替人答一份。缺题目正文的题**照样留在卷子里**
    并标注 `missing_item`：静默丢掉一道会让卷子看起来比真实的小，
    而分数会建立在"他答了全部题"这个假象上。
    """
    questions: list[dict] = []
    for position, entry in enumerate(plan.get("items") or [], start=1):
        item_id = str(entry.get("item_id") or "")
        item = bank.get(item_id)
        if item is None:
            questions.append(
                {
                    "position": position,
                    "item_id": item_id,
                    "role": entry.get("role"),
                    "difficulty": entry.get("difficulty"),
                    "topics": list(entry.get("topics") or []),
                    "why": entry.get("why"),
                    "missing_item": (
                        f"题库目录里没有 {item_id} —— 卷子里留着这一格，"
                        "因为静默丢掉会让卷子看起来比真实的小"
                    ),
                    "prompt": "",
                    "material": "",
                    "prompts": [],
                    "answers": {},
                }
            )
            continue
        prompts = _prompts_of(item)
        questions.append(
            {
                "position": position,
                "item_id": item_id,
                "role": entry.get("role"),
                "difficulty": entry.get("difficulty"),
                "topics": list(entry.get("topics") or []),
                "why": entry.get("why"),
                "missing_item": None,
                "prompt": str(getattr(item, "raw", {}).get("prompt") or ""),
                "kind": (item.kinds or ("",))[0],
                "time_min": getattr(item, "time_min", None),
                "material": str(getattr(item, "material_text", "") or ""),
                "prompts": prompts,
                # ★ **一个问一格**，不是一个题一格（P15 跑真链路时抓出来的）：
                #   一道题常常有"要点题 + 对错题"两个问，而它们的答案是两种东西
                #   （一段话 vs 一个选项）。合成一格之后，交卷时同一段话被
                #   当成两个问的答案上报 —— 对错题于是必然判错，
                #   而报告上看起来只是"他答错了"，没有人会怀疑格式。
                "answers": {str(p["question_no"]): "" for p in prompts},
            }
        )
    return {
        "run_id": run_id,
        "form": plan.get("form"),
        "variable_id": plan.get("variable_id"),
        "channel": plan.get("channel"),
        "method_id": plan.get("method_id"),
        "seed": plan.get("seed"),
        "summary": plan.get("summary"),
        "notes": list(plan.get("notes") or []),
        "topics": list(plan.get("topics") or []),
        "how_to_answer": (
            "每一道题的 `answers` 里是**逐个问**的空格子（键是问题编号），请自己填。"
            "填完保存，再跑："
            "`python -m tools.runbench --base-url <url> --paper <这份文件>`。"
            "★ 不填的问**不会**被上报 —— 它在库里是「没测到」，不是 0 分。"
        ),
        "questions": questions,
    }


def _prompts_of(item: Any) -> list[dict]:
    """题目里"要回答什么"。规格按题型分支（P11），所以这里也按题型取。

    ★ `question_no` 用的是**规格里的编号**（要点题 0、对错题 1..n），
    不是列表下标 —— 上报时要与题库对得上，否则分数会记到别的问上。
    """
    prompts: list[dict] = []
    key_points = getattr(item, "key_points_question", None)
    if isinstance(key_points, dict) and str(key_points.get("prompt") or "").strip():
        prompts.append(
            {
                "question_no": 0,
                "kind": "key_points",
                "text": str(key_points["prompt"]),
            }
        )
    for entry in getattr(item, "exact_questions", []) or []:
        if not isinstance(entry, dict):
            continue
        prompts.append(
            {
                "question_no": int(entry.get("no") or 0),
                "kind": "exact",
                "text": str(entry.get("question") or ""),
                "options": list(entry.get("options") or []),
            }
        )
    self_report = getattr(item, "self_report_question", None)
    if self_report is not None:
        prompts.append(
            {
                "question_no": 0,
                "kind": "self_report",
                "text": str(getattr(self_report, "prompt", "") or ""),
                "options": [
                    str(getattr(statement, "text", statement))
                    for statement in (getattr(self_report, "statements", None) or [])
                ],
            }
        )
    return prompts


def render_paper(paper: dict) -> str:
    """卷子的人类可读版（打印出来能直接在纸上答）。"""
    lines = [
        f"运行 {paper['run_id']} · {paper.get('form')} · {paper.get('variable_id')}",
        f"{paper.get('summary') or ''}",
        f"共 {len(paper['questions'])} 题",
        "",
    ]
    for question in paper["questions"]:
        lines.append(
            f"[{question['position']}] {question['item_id']} "
            f"（{question.get('role')} / {question.get('difficulty')}）"
        )
        if question.get("missing_item"):
            lines.append(f"    ⚠️ {question['missing_item']}")
        if question.get("prompt"):
            lines.append(f"    要求：{question['prompt']}")
        if question.get("material"):
            lines.append(f"    材料：{question['material']}")
        for prompt in question.get("prompts") or []:
            number = str(prompt["question_no"])
            lines.append(f"    问 {number}：{prompt['text']}")
            for option in prompt.get("options") or []:
                lines.append(f"        - {option}")
            lines.append(f"    答{number}：（填进卷子 JSON 的 answers[\"{number}\"]）")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 交卷
# --------------------------------------------------------------------------- #


def collect_answers(paper: dict) -> tuple[list[dict], list[str]]:
    """把填好的卷子变成一串请求体。**跳过的问要单独报出来。**

    返回 `(要上报的, 没答的)`。★ 没答的**不上报**：上报一条
    `raw_answer=None` 的作答，在库里与"他答了但没测到"长得一样，
    而"他没做这道题"是一件必须分得清的事。

    ★ 没答的以**问**为单位报（`A-1 问1`），不是以题为单位 ——
    一道题里答了要点题、空着对错题，与整道题没做是两件事。
    """
    payloads: list[dict] = []
    unanswered: list[str] = []
    for question in paper["questions"]:
        prompts = question.get("prompts") or []
        answers = question.get("answers") or {}
        if not prompts:
            unanswered.append(question["item_id"])
            continue
        for prompt in prompts:
            number = str(prompt.get("question_no") or 0)
            answer = str(answers.get(number) or "")
            if not answer.strip():
                unanswered.append(f"{question['item_id']} 问{number}")
                continue
            payloads.append(
                {
                    "item_id": question["item_id"],
                    "question_no": int(prompt.get("question_no") or 0),
                    "kind": prompt.get("kind") or "key_points",
                    "judged_by": "code",
                    "raw_answer": answer,
                }
            )
    return payloads, unanswered


def render_report(score: dict) -> str:
    """分数报告。★ `0.0` 与「没测到」在这里是两种写法。"""
    lines = [f"运行 {score.get('run_id')}", ""]

    def number(value: Any, what: str) -> str:
        if value is None:
            return f"{what}：{NOT_MEASURED}"
        return f"{what}：{value}"

    lines.append(number(score.get("method_score"), "方法分（原始）"))
    if score.get("equated_score") is not None:
        equating = score.get("equating") or {}
        why = equating.get("reason") or equating.get("description") or ""
        lines.append(f"等值化后：{score['equated_score']}" + (f"（{why}）" if why else ""))
        # ★ 只在两句话**不一样**时才把第二句也打出来 —— 同一句话打两遍
        #   会让人以为有两处独立结论（第一版就是这样，跑真链路时看到的）。
        if equating.get("description") and equating["description"] != why:
            lines.append(f"  ⚠️ 校正底下那句话：{equating['description']}")
    lines.append(f"置信：{score.get('confidence')} —— {score.get('confidence_reason')}")
    lines.append("")
    lines.append("逐题：")
    for item in score.get("items") or []:
        if item.get("item_score") is None:
            shown = f"{NOT_MEASURED}（{item.get('void_reason') or '没有原因，这是缺陷'}）"
        else:
            shown = str(item["item_score"])
        counted = "" if item.get("counted") else "（不进读数）"
        method = item.get("scoring_method") or "没有计分规则"
        lines.append(
            f"  {item['item_id']} 问{item['question_no']} {item['kind']}："
            f"{shown}{counted} · {method}"
        )
    if score.get("skipped"):
        lines.append("")
        lines.append("没参与计分的题：")
        for row in score["skipped"]:
            lines.append(f"  · {row}")
    if score.get("notes"):
        lines.append("")
        lines.append("这一层还没做的部分（如实列出）：")
        for note in score["notes"]:
            lines.append(f"  · {note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 两个动作
# --------------------------------------------------------------------------- #


def do_make_paper(args: argparse.Namespace) -> int:
    items_dir = Path(args.items_dir)
    if not items_dir.is_dir():
        print(f"❌ 题库目录不存在：{items_dir.resolve()}")
        return 2
    query = {
        "form": args.form,
        "variable_id": args.variable,
        "channel": args.channel,
        "method_id": args.method,
        "seed": args.seed,
    }
    code, plan = _request(args.base_url, "GET", "/api/benchmark/plan", query=query)
    if code != 200:
        print(f"❌ 抽题预览失败（{code}）：{_refusal(plan)}")
        return 1

    code, run = _request(
        args.base_url,
        "POST",
        "/api/benchmark/runs",
        {
            "form": args.form,
            "variable_id": args.variable,
            "channel": args.channel,
            "method_id": args.method,
            "seed": args.seed,
        },
    )
    if code != 201:
        print(f"❌ 开一次运行失败（{code}）：{_refusal(run)}")
        return 1
    run_id = str(run.get("run_id") or "")
    # ★ 抽题预览与真正落库的那一次必须是**同一批题**。
    #   不一致意味着预览骗人，而那正是它存在的理由（让人在按下 60 分钟之前先看）。
    if list(run.get("item_ids") or []) != [q["item_id"] for q in plan.get("items") or []]:
        print("❌ 预览的题与真正落库的那一批不一致 —— 这条链不能用来出卷")
        print(f"    预览：{[q['item_id'] for q in plan.get('items') or []]}")
        print(f"    落库：{list(run.get('item_ids') or [])}")
        return 1

    try:
        bank = load_bank(items_dir)
    except ValueError as exc:
        print(f"❌ 题库读不动：{exc}")
        print("   （不跳过坏文件：少一格会让卷子看起来比真实的小，而分数照常算出来）")
        return 2

    paper = build_paper(plan, bank, run_id=run_id)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_paper(paper))
    print()
    print(f"✅ 卷子已写出：{out}（运行 {run_id}，{len(paper['questions'])} 题）")
    print(f"   填好每道题的 `answer` 后：python -m tools.runbench "
          f"--base-url {args.base_url} --paper {out}")
    return 0


def do_submit(args: argparse.Namespace) -> int:
    paper_path = Path(args.paper)
    if not paper_path.is_file():
        print(f"❌ 卷子文件不存在：{paper_path.resolve()}")
        return 2
    paper = json.loads(paper_path.read_text(encoding="utf-8"))
    run_id = str(paper.get("run_id") or "")
    if not run_id:
        print("❌ 这份卷子里没有 `run_id` —— 用 `--out` 出的卷子才交得回来")
        return 2

    payloads, unanswered = collect_answers(paper)
    if not payloads:
        print("❌ 整卷都没有答案 —— 这个工具不替人答题，所以这里什么都不做")
        return 2

    accepted = 0
    for payload in payloads:
        code, body = _request(
            args.base_url, "POST", f"/api/benchmark/runs/{run_id}/responses", payload
        )
        if code != 201:
            print(
                f"❌ {payload['item_id']} 问{payload['question_no']} 落库失败"
                f"（{code}）：{_refusal(body)}"
            )
            return 1
        accepted += 1
    print(f"已落库 {accepted} 条作答；没答的 {len(unanswered)} 题**没上报**"
          f"（它们在库里是「没测到」，不是 0 分）")
    if unanswered:
        print(f"    没答的题：{'、'.join(unanswered)}")

    code, score = _request(args.base_url, "POST", f"/api/benchmark/runs/{run_id}/score")
    if code != 200:
        print(f"❌ 计分失败（{code}）：{_refusal(score)}")
        return 1
    print()
    print(render_report(score))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runbench", description="离线跑一整卷：出卷 / 交卷 + 计分报告"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8125")
    parser.add_argument("--form", choices=("quick", "full"), default="quick")
    parser.add_argument("--variable", default="V-SE")
    parser.add_argument("--channel", default="behavioral_task")
    parser.add_argument("--method", default="M-SE-03")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--items-dir",
        default="tests/fixtures/bank",
        help="题目 JSON 目录（出卷时读题目正文用）",
    )
    parser.add_argument(
        "--out", default="var/paper.json", help="卷子写到哪（出卷动作）"
    )
    parser.add_argument(
        "--paper", default=None, help="交卷：读回填好答案的那份卷子"
    )
    args = parser.parse_args(argv)

    if args.paper:
        return do_submit(args)
    return do_make_paper(args)


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.exit(main())
