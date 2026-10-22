"""把上一版补丁里写错缩进/锚点的那两条，按真实代码形状改一遍。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")

# 1) 阶段项结算：用现读的会话版本
old_stage = '        {"expected_version": ctx.get("stage_version", 1), "status": "deposited"},'
new_stage = '        {"expected_version": fresh_session_version(ctx), "status": "deposited"},'
assert text.count(old_stage) == 1, text.count(old_stage)
text = text.replace(old_stage, new_stage)
print("✅ 阶段项结算：现读会话版本")

# 2) 动机：期望版本从 1 起（0 会被 422 挡住）
start = text.index('        f"/api/sessions/{ctx_ids(ctx, \'session_id\')}/motivation",')
segment = text[start : start + 400]
if '"expected_version": 0' in segment:
    text = (
        text[:start]
        + segment.replace('"expected_version": 0', '"expected_version": 1', 1)
        + text[start + 400 :]
    )
    print("✅ 动机：版本从 1 起")
else:
    print("… 动机那段里没有 expected_version: 0，跳过")

# 3) 里程碑：也现读会话版本
old_milestone = '''        "POST",
        f"/api/sessions/{ctx_ids(ctx, 'session_id')}/milestones",'''
if old_milestone in text:
    print("… 里程碑那条不需要版本号，保持原样")

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("✅ 语法 OK")
