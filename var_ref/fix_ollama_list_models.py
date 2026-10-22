"""`OllamaClient.list_models` 被缩进到了一个模块级函数里 —— 它从来没被绑到类上。

现场：`POST /api/llm/profiles/{id}/probe` 对一份 Ollama 档案直接 500：
`AttributeError: 'OllamaClient' object has no attribute 'list_models'`。

原因不是"忘了写"，而是**写在错误的缩进里**：方法体在
`context_truncation_note()` 的 `return` 之后，仍留着 4 空格缩进 ——
语法合法、永不执行、也永远不会成为类的方法。一段看起来实现了的代码，实际是死的。

（`OpenAICompatClient` 的 `list_models` 位置是对的，所以只有本地模型这条路出事。）

修法：把这一块从函数体里剪出来，接到 `OllamaClient` 类体末尾
（`_parse_call` 之后）—— 它读 `self.base_url` / `self.timeout`，
所以它必须真的在这个类里。
"""

from __future__ import annotations

import ast
import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "backend" / "app" / "agent" / "llm.py"
lines = PATH.read_text(encoding="utf-8").splitlines()

# ── 1. 剪出那块死代码 ────────────────────────────────────────────────
marker = "    # ---- 模型清单 ------------------------------------------------------ #"
#: 这个注释出现两次（`OpenAICompatClient` 里也有一条），要搬的是**第一条**。
assert lines.count(marker) == 2, f"注释行出现 {lines.count(marker)} 次"
start = lines.index(marker)
end = start
while not lines[end].startswith("        return sorted(names)"):
    end += 1

block_start = start + 1
while lines[block_start].strip() == "":
    block_start += 1
block = lines[block_start : end + 1]
assert block[0].strip().startswith("def list_models("), block[0]

del lines[start : end + 1]
# 把删完之后可能留下来的连续空行收成两行
while lines[start : start + 2] == ["", ""]:
    del lines[start]

# ── 2. 接到 OllamaClient 类体末尾 ────────────────────────────────────
anchor = "    def _parse_call("
hits = [i for i, line in enumerate(lines) if line.startswith(anchor)]
assert len(hits) == 1, hits
insert_at = hits[0] + 1
# `_parse_call` 是一行写完的方法；如果它是多行，就找它后面第一个空行
if not lines[insert_at].startswith("    def "):
    while lines[insert_at].strip() != "":
        insert_at += 1

new_block = [
    "",
    "    # ---- 模型清单 ------------------------------------------------------ #",
    "",
    "    #: ★ 这一块原先写在 `context_truncation_note()` 的 `return` 之后 ——",
    "    #: 缩进合法、永不执行，也从来没成为这个类的方法。于是 Ollama 档案的",
    "    #: 「探活 / 拉清单」直接 500（`AttributeError: ... has no attribute`）。",
    "    #: 它读 `self.base_url` 与 `self.timeout`，所以位置本身就是它的依赖。",
    *block,
    "",
]
lines[insert_at:insert_at] = new_block

PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

tree = ast.parse(PATH.read_text(encoding="utf-8"))
for cls in tree.body:
    if isinstance(cls, ast.ClassDef) and cls.name in ("OllamaClient", "OpenAICompatClient"):
        methods = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
        print(f"{cls.name}: {methods}")
assert "list_models" in [
    n.name
    for cls in tree.body
    if isinstance(cls, ast.ClassDef) and cls.name == "OllamaClient"
    for n in cls.body
    if isinstance(n, ast.FunctionDef)
], "还是没有绑上"
print("✅ list_models 现在真的是 OllamaClient 的方法")
