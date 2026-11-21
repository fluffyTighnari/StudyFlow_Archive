"""铺一份界面能直接看的演示数据 —— **全部经由真实 HTTP 端点**。

为什么不用手写 SQL：手写 SQL 能造出"界面上看起来对、但违反不变量"的数据
（比如计划版本号与 `current_version` 对不上、检查点指向不存在的步骤）。
这类数据最坏的地方是**它会让人以为是界面的 bug**。

用法（在 `backend/` 下）：

    .\\.venv\\Scripts\\python.exe -m tools.seedui --db var/ui.db --reset
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

FOLDER_GOAL = "把英语捡回来：能读懂技术文档，能在会上开口"


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="var/ui.db")
    parser.add_argument("--reset", action="store_true", help="先删掉库文件")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="只铺一份最小计划（一个文件夹 / 一个会话 / 一个步骤）。"
        "给容器入口用：fake 替身需要一份计划才能起来，"
        "但界面里不该因此出现两条用户没建过的历史会话。",
    )
    args = parser.parse_args()

    db_path = pathlib.Path(args.db)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            if candidate.exists():
                candidate.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    settings = Settings(database_url=f"sqlite+pysqlite:///{args.db}", seed_on_startup=True)
    app = create_app(settings)

    with TestClient(app) as client:
        folder = client.post(
            "/api/folders",
            json={
                "name": "示例领域" if args.minimal else "英语",
                "goal": "" if args.minimal else FOLDER_GOAL,
            },
        ).json()
        folder_id = folder["folder_id"]

        sessions = [
            {
                "title": "背完考研核心 1500 词",
                "input": {
                    "input_goal": "30 天内背完考研核心 1500 词，每天 50 个",
                    "intent_raw": "我想在一个月内把考研核心词过一遍，每天大概 50 个",
                    "intent_confidence": "stated",
                },
                "why": "工作需要：读英文文档比读翻译快，而且不会漏细节",
                "starting_point": "四级过了，但两年没用，词汇掉得厉害",
                "success_criteria": "能不看字幕听懂技术播客的前 15 分钟",
                "constraints": "每天只有通勤的 40 分钟",
                "task_type": "记忆型",
                "goal_derived": "先把第一单元吃透，再谈数量",
                "steps": [
                    {"action": "通读 unit 1 词表", "output": "一份标了生词的词表"},
                    {"action": "精读 unit 1 例句", "output": "10 个自己造的例句"},
                    {"action": "unit 1 自测", "output": "正确率记录"},
                ],
                "checkpoints": [
                    {
                        "at_step": None,  # 稍后填成第一步的 id
                        "check_method": "遮住中文，看英文能说出意思",
                        "pass_criteria": "50 个词里至少 42 个说得出",
                        "fail_action": "重做这一组，并把错词单独抄一遍",
                    }
                ],
            },
            {
                "title": "技术文档精读：React 文档第一章",
                "input": {
                    "input_goal": "读懂 React 文档 Getting Started 的全部内容",
                    "intent_raw": "前端要补 React，想先把官方文档第一章读完",
                    "intent_confidence": "inferred",
                },
                "why": "前端要补 React，官方文档是最准的一手材料",
                "starting_point": "会写一点 JSX，但没系统读过文档",
                "success_criteria": "能独立照文档做出一个小页面",
                "constraints": "每周挤两个晚上",
                "task_type": "理解型",
                "goal_derived": "先读完第一章，再动手抄",
                "steps": [
                    {"action": "通读 Getting Started", "output": "每节一句话摘要"},
                    {"action": "照着抄一个计数器", "output": "能跑起来的最小例子"},
                ],
                "checkpoints": [],
            },
        ]

        if args.minimal:
            # 只留第一条，并且**换成一个中性的标题**：
            # fake 替身只需要"一份带步骤的计划"，不需要它是某个人的学习史。
            # 用"背完考研核心 1500 词"会让第一次打开容器的人以为
            # 这些是系统替他建的（或者更糟：是他自己建过又忘了的）。
            one = dict(sessions[0])
            one.update(
                {
                    "title": "示例会话",
                    "input": {
                        "input_goal": "演示用：把一件事拆成几步",
                        "intent_raw": "只是想看看计划区长什么样",
                        "intent_confidence": "stated",
                    },
                    "why": "演示数据：让模型替身有一份计划可改",
                    "starting_point": "",
                    "success_criteria": "",
                    "constraints": "",
                    "task_type": "记忆型",
                    "goal_derived": "演示用：把第一步做掉",
                    "steps": [{"action": "第一步示例", "output": "一个看得见的结果"}],
                    "checkpoints": [],
                }
            )
            sessions = [one]

        for spec in sessions:
            created = client.post(
                "/api/sessions",
                json={"folder_id": folder_id, "title": spec["title"], **spec["input"]},
            )
            if created.status_code != 201:
                print("建会话失败：", created.status_code, created.text)
                return 1
            session_id = created.json()["session_id"]

            state = client.get(f"/api/sessions/{session_id}/state").json()
            moved = client.post(
                f"/api/sessions/{session_id}/transitions",
                json={"target": "active", "expected_version": state["versions"]["session"]},
            )
            if moved.status_code not in (200, 201):
                print("状态转移失败：", moved.status_code, moved.text)
                return 1

            # 定义区的其余字段：走 PATCH，带理由（这是一次真实编辑）
            state_body = client.get(f"/api/sessions/{session_id}/state").json()
            input_row = state_body["input"]
            patched = client.patch(
                f"/api/sessions/{session_id}/input",
                json={
                    "expected_version": input_row["version"],
                    "why": spec["why"],
                    "starting_point": spec["starting_point"],
                    "success_criteria": spec["success_criteria"],
                    "constraints": spec["constraints"],
                    "task_type": spec["task_type"],
                    "reason": "第一次把任务说清楚",
                },
            )
            if patched.status_code != 200:
                print("改定义失败：", patched.status_code, patched.text)
                return 1

            state = client.get(f"/api/sessions/{session_id}/state").json()
            plan_body = {
                "expected_session_version": state["versions"]["session"],
                "goal_derived": spec["goal_derived"],
                "steps": spec["steps"],
                "checkpoints": [],
            }
            plan = client.post(f"/api/sessions/{session_id}/plan", json=plan_body)
            if plan.status_code != 201:
                print("建计划失败：", plan.status_code, plan.text)
                return 1
            plan_data = plan.json()
            first_step = plan_data["steps"][0]["step_id"]

            if spec["checkpoints"]:
                checkpoints = [
                    {**cp, "at_step": first_step} for cp in spec["checkpoints"]
                ]
                # 改计划是 **PUT /plan**（`/plan/revisions` 只有 GET，用来列历史）。
                revised = client.put(
                    f"/api/sessions/{session_id}/plan",
                    json={
                        "expected_version": plan_data["version"],
                        "change": "补一个检查点",
                        "reason": "没有检查点就无法知道背没背下来",
                        "steps": [
                            {"step_id": step["step_id"], "action": step["action"],
                             "output": step["output"]}
                            for step in plan_data["steps"]
                        ],
                        "checkpoints": checkpoints,
                    },
                )
                if revised.status_code not in (200, 201):
                    print("补检查点失败：", revised.status_code, revised.text)
                    return 1

            print(f"✓ {spec['title']}  session={session_id}")

        folders = client.get("/api/folders").json()
        print(f"\n共 {len(folders)} 个文件夹；库：{args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
