"""把 DSH 插件包里内联的 CSS 模块字符串抠出来，供复刻时读几何数据。

★ 只做提取，不改动：这些 CSS 属于 DSH 自己的插件包，我们只是读它来对齐尺寸。
"""

from __future__ import annotations

import pathlib
import re
import sys

REF = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref")


def extract(name: str) -> int:
    text = (REF / name).read_text(encoding="utf-8", errors="replace")
    # 插件包把 CSS 写成 `const css$N = "..."` 或 `css$N = "..."`
    pattern = re.compile(r'css\$?\d*\s*=\s*"((?:[^"\\]|\\.)*)"', re.S)
    hits = pattern.findall(text)
    written = 0
    for i, raw in enumerate(hits):
        if "--dsw-" in raw or "display:" in raw:
            css = raw.encode("utf-8").decode("unicode_escape")
            pretty = css.replace("}", "}\n").replace("{", "{\n  ").replace(";", ";\n  ")
            out = REF / f"{pathlib.Path(name).stem}.css{i}.txt"
            out.write_text(pretty, encoding="utf-8")
            written += 1
    print(f"{name}: 抠出 {written} 段")
    return written


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        extract(arg)
