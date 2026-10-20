"""给无头渲染造一点"只有数据在时才出现"的状态：一份资料 + 一个归档文件夹。

## 为什么要造

`资料卡片`、`已归档分组` 这两块只在有数据时渲染。DOM 里没有它们，
**可能是没实现，也可能是没数据** —— 这两件事必须分开证明，否则
"✅ 页面正常"可以是一个空页面的正常。

这段代码**只走公开 HTTP 接口**（和界面用的是同一批端点），不碰库、不 import app：
它能通过，就说明界面上那两块的来源在真实接口上是存在的。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8187"


def post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method="POST",
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def patch(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method="PATCH",
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    request = urllib.request.Request(f"{BASE}{path}", headers={"accept": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def upload(session_id: str, filename: str, text: str) -> dict:
    request = urllib.request.Request(
        f"{BASE}/api/sessions/{session_id}/attachments?filename={urllib.parse.quote(filename)}",
        data=text.encode("utf-8"),
        method="POST",
        headers={"content-type": "text/markdown", "accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


import urllib.parse  # noqa: E402  （放在下面只是为了让上面的函数看起来先读接口）


def main() -> int:
    sessions = get("/api/sessions?include_archived=true")["items"]
    # 挑一个 draft/active 的会话：上传资料受 `ADD_ATTACHMENT` 守卫，
    # 终态会话会被 409 拒掉（那是设计，不是 bug）。
    writable = [s for s in sessions if s["state"] in {"draft", "active"}]
    if not writable:
        print("❌ 库里没有 draft/active 的会话，先 seedui。")
        return 1
    session_id = writable[0]["session_id"]
    print(f"   用会话 {session_id}（{writable[0]['state']}）")

    # 0. 保证这个会话处于 active：界面默认选第一个文件夹的第一个会话，
    #    而 e2echeck 可能已经把它推到终态了 —— 那就换一个能写的。
    if writable[0]["state"] == "draft":
        post(f"/api/sessions/{session_id}/transitions", {"target": "active", "expected_version": writable[0]["version"]})
        print("   已把它推进 active")

    # 1. 一份资料（够长、够短都行；这里给一段中文，用来验证字数与预览）。
    body = "# 备注\n\n这是为了验证「资料卡片」而上传的一份资料。" + "内容。" * 40
    try:
        attachment = upload(session_id, "验证笔记.md", body)
        print(f"✅ 资料已上传：{attachment['filename']}（{attachment['chars_len']} 字）")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 上传资料失败：{exc}")
        return 1

    # 2. 一个归档文件夹（侧栏底部的「已归档」区靠它出现）。
    folder = post("/api/folders", {"name": "归档验证用"})
    patch(f"/api/folders/{folder['folder_id']}", {"archived": True})
    print(f"✅ 已归档文件夹：{folder['name']}")

    # 3. 一句确认：归档之后它不该再出现在"未归档"里。
    live = [f for f in get("/api/folders?include_archived=true")["items"] if f["archived_at"] is None]
    archived = [f for f in get("/api/folders?include_archived=true")["items"] if f["archived_at"]]
    print(f"   未归档 {len(live)} 个 / 已归档 {len(archived)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
