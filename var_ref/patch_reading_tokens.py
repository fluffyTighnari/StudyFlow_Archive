"""在 `build_tokens.py` 的几何令牌块里加上"阅读字号"五档，并重新生成 `dsh.css`。

## 为什么要回到生成器而不是手改 `dsh.css`

`dsh.css` 是**生成物**（文件头就写着）。手改它，下一次有人跑生成器就全被抹掉 ——
而那种丢失不会报错，只会在某一天"字号设置突然不生效"。

## 为什么是五档而不是一个数字

组件里不许出现数字（`check-literals`）。把 `14px` 塞进 TS 的
`style.setProperty("--dsh-content-font-size", "14px")` 只是把那个数字换了个
文件放，检查器一样看得见。所以：令牌五档，界面只负责往 `<html>` 上打
`data-reading-size="14"`，档位到令牌的映射写在 CSS 里。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref\build_tokens.py")
text = PATH.read_text(encoding="utf-8")

ANCHOR = ":root {\n  --dsh-content-font-size: 14px;\n"
ADDITION = """:root {
  --dsh-content-font-size: 14px;
  /*
   * 阅读字号（「设置 → 外观 → 字号，只影响对话内容」）。
   *
   * 五档：12 / 13 / 14 / 15 / 16。每一档是一个令牌 —— 组件里不许出现数字，
   * 而界面只负责往 <html> 上打 `data-reading-size="14"`，
   * 档位到令牌的映射写在 `app.css` 里。
   */
  --dsh-reading-size-12: 12px;
  --dsh-reading-size-13: 13px;
  --dsh-reading-size-14: 14px;
  --dsh-reading-size-15: 15px;
  --dsh-reading-size-16: 16px;
"""

if "--dsh-reading-size-12" in text:
    print("· 令牌已经在生成器里，跳过")
else:
    if ANCHOR not in text:
        raise SystemExit("锚点没找到 —— 生成器的结构变了，先看一眼再改。")
    PATH.write_text(text.replace(ANCHOR, ADDITION, 1), encoding="utf-8")
    print("✅ build_tokens.py：加了五档阅读字号令牌")
