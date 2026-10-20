"""验证"删掉一份资料"这条真实链路（界面上的小叉就是调它）。

用脚本而不是一行命令：PowerShell 里嵌 Python 的转义引号反复出问题
（`f\"...\"` 被当成参数边界），而在这种地方"换一种写法绕过去"只会再踩一次。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8187"
SESSION = sys.argv[2] if len(sys.argv) > 2 else "S-0002"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path) as response:
        return json.loads(response.read().decode("utf-8"))


def delete(path: str) -> dict:
    request = urllib.request.Request(BASE + path, method="DELETE")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    items = get(f"/api/sessions/{SESSION}/attachments")["items"]
    print("删前：", [item["filename"] for item in items] or "（没有资料）")
    if not items:
        print("· 这个会话没有资料可删，跳过。")
        return 0
    for item in items:
        removed = delete(f"/api/sessions/{SESSION}/attachments/{item['attachment_id']}")
        deleted = removed["deleted"]
        print(
            f"删掉：{deleted['filename']}（{deleted['chars_len']} 字，"
            f"truncated={deleted['truncated']}）"
        )
    after = get(f"/api/sessions/{SESSION}/attachments")["items"]
    print("删后：", [item["filename"] for item in after] or "（没有资料）")
    return 0 if not after else 1


if __name__ == "__main__":
    raise SystemExit(main())
