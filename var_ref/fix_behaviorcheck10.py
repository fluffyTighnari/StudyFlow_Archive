"""两条最后的小修：动机版本重试、澄清的断言改看 message。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "tools" / "behaviorcheck.py"
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, label: str) -> None:
    global text
    assert text.count(old) == 1, f"{label}：锚点出现 {text.count(old)} 次"
    text = text.replace(old, new, 1)
    print(f"✅ {label}")


swap(
    '''def current_state(ctx: dict) -> str:''',
    '''def call_with_version_retry(
    ctx: dict, method: str, path: str, body: dict, *, version_key: str = "expected_version"
) -> tuple[Reply, dict]:
    """版本撞了就按后端说的 `current_version` 重发一次。

    这不是"绕过守卫"：守卫拦下的是**用过期的版本号写的请求**，而 409 已经把
    当前版本告诉你了 —— 和界面里"读到冲突 → 重新读一遍 → 再发一次"是同一件事。
    动机的版本号在 `/state` 里不一定带，靠这一步兜住。
    """
    reply = ctx["client"].call(method, path, body)
    if reply.status != 409:
        return reply, body
    detail = reply.data.get("detail") if isinstance(reply.data, dict) else None
    if not isinstance(detail, dict) or "current_version" not in detail:
        return reply, body
    retry = dict(body)
    retry[version_key] = int(detail["current_version"])
    return ctx["client"].call(method, path, retry), retry


def current_state(ctx: dict) -> str:''',
    "加上「按后端说的版本重发一次」",
)

# 动机用例改成走这个包装
start = text.index('@case("动机", "改动机"')
end = text.index('@case(', start + 10)
block = text[start:end]
assert "reply = ctx[\"client\"].call(" in block, block[:200]
new_block = block.replace(
    'reply = ctx["client"].call(\n        "PATCH",\n        f"/api/sessions/{ctx_ids(ctx, \'session_id\')}/motivation",',
    'reply, _body = call_with_version_retry(\n        ctx,\n        "PATCH",\n        f"/api/sessions/{ctx_ids(ctx, \'session_id\')}/motivation",',
    1,
)
assert new_block != block, "动机用例的形状和我以为的不一样"
text = text[:start] + new_block + text[end:]
print("✅ 改动机：走重试包装")

# 澄清断言改看 message
swap(
    '''    detail = reply.detail
    ctx.note_error("问已填好的槽位", detail)
    return reply.status == 422 and "已经有内容" in detail, f"{reply.status} {detail[:110]}", ""''',
    '''    message = str((reply.data or {}).get("message") or reply.detail)
    ctx.note_error("问已填好的槽位", message)
    return reply.status == 422 and "已经有内容" in message, f"{reply.status} {message[:110]}", ""''',
    "问已填好的槽位：读 message",
)

PATH.write_text(text, encoding="utf-8")
compile(PATH.read_text(encoding="utf-8"), str(PATH), "exec")
print("\n✅ 语法 OK")
