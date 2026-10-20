"""补两处文档：`_M9` §6.11.10 记下"冷启动那一下 DOM 停在空态"这件事，
以及 §6.11.11 交付物表里 `var_ref/` 那一行的说明。"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\_M9_前端与主题套件_v0.1.md"
)
text = PATH.read_text(encoding="utf-8")

OLD = """`check_v3.py` 值得单独说一句：它除了核对"环在不在、模型键在不在"，
还核对了**展开着的文件夹用的是张开的那个图标**（SVG 的 path 在 DOM 里出现），
以及**上次更新时间是一句相对时间**。这两条正是"看着像做了、其实没接上"
最常发生的地方。"""

NEW = """`check_v3.py` 值得单独说一句：它除了核对"环在不在、模型键在不在"，
还核对了**展开着的文件夹用的是张开的那个图标**（SVG 的 path 在 DOM 里出现），
以及**上次更新时间是一句相对时间**。这两条正是"看着像做了、其实没接上"
最常发生的地方。

它还带一个**冷启动重试**，这一点值得记下来：每次无头渲染都开一个全新的
浏览器 profile（无缓存、无 cookie），于是它会把所有接口冷读一遍。服务刚重启时
那一下，DOM 会停在"会话列表已经画出来了、但还没选中会话"的中间态 ——
输入卡因此不在 DOM 里，六项检查一起报缺。第一次碰到它的结论是
"这几处没做"，而真相是**测量方式在冷启动时不可靠**。

处理方式不是调大等待（`--virtual-time-budget` 是**虚拟**时间，它跟着
网络等待一起走，调大反而更容易在真实网络落地前就 dump），而是：
**等"输入卡出现"这个标志再收工，最多三次；如果第二次才成功，就打印一行 ⚠️**。
既不把失败遮掉，也不把一次冷启动当成产品缺陷。
（顺带把 `check_sidebar.py` 里"输入框在"那一项也接上同一份重试 ——
它在同一次冷启动里也红过一次。）"""

assert text.count(OLD) == 1, "锚点出现 %d 次" % text.count(OLD)
text = text.replace(OLD, NEW, 1)

OLD2 = "| `var_ref/check_v3.py` / `patch_*.py` / `build_tokens.py` | — | DOM 对账、六处补丁脚本、令牌生成器 |"
NEW2 = (
    "| `var_ref/check_v3.py` / `patch_*.py` / `build_tokens.py` | — | "
    "DOM 对账（含冷启动重试；`check_sidebar.py` 也复用它）、六处补丁脚本、"
    "令牌生成器（新增五档阅读字号、环的旋转与笔帽、等宽数位、呼吸动画与其关键帧） |"
)
assert text.count(OLD2) == 1, "交付物行锚点出现 %d 次" % text.count(OLD2)
text = text.replace(OLD2, NEW2, 1)

PATH.write_text(text, encoding="utf-8")
print("✅ _M9：补上冷启动重试那条记录")
