"""把 `_M9` §6.11 交付物表里的行数改成**实测值**（写文档时还没量）。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\_M9_前端与主题套件_v0.1.md"
)
text = PATH.read_text(encoding="utf-8")

FIXES = [
    ("| `frontend/app/src/settings.ts` | 190 |", "| `frontend/app/src/settings.ts` | 171 |"),
    (
        "| `frontend/app/src/components/SettingsDialog.tsx` | 330 |",
        "| `frontend/app/src/components/SettingsDialog.tsx` | 290 |",
    ),
    (
        "| `frontend/app/src/components/ConversationView.tsx` | 592 |",
        "| `frontend/app/src/components/ConversationView.tsx` | 590 |",
    ),
    (
        "| `frontend/app/src/components/MessageList.tsx` | 245 |",
        "| `frontend/app/src/components/MessageList.tsx` | 251 |",
    ),
    ("| `frontend/app/src/App.tsx` | 528 |", "| `frontend/app/src/App.tsx` | 537 |"),
    (
        "| `frontend/app/src/styles/app.css` | 2 100 |",
        "| `frontend/app/src/styles/app.css` | 2 263 |",
    ),
    (
        "| `backend/app/session_domain/writes.py` | 1 898 |",
        "| `backend/app/session_domain/writes.py` | 1 907 |",
    ),
    (
        "| `backend/tests/session/test_chat_cache_readings.py` | 110 |",
        "| `backend/tests/session/test_chat_cache_readings.py` | 115 |",
    ),
    (
        "| `frontend/app/src/api/queries.ts` / `types.ts` | — |",
        "| `frontend/app/src/api/queries.ts` / `types.ts` | 352 / 455 |",
    ),
    (
        "| `backend/tools/e2echeck.py` | — | 新增 `step_usage_readings`（第 16 步，共 33 步） |",
        "| `backend/tools/e2echeck.py` | 2 732 | 新增 `step_usage_readings`（第 16 步，共 33 步） |",
    ),
]

for old, new in FIXES:
    assert text.count(old) == 1, f"锚点出现 {text.count(old)} 次：{old}"
    text = text.replace(old, new, 1)

PATH.write_text(text, encoding="utf-8")
print(f"已校正 {len(FIXES)} 处行数")
