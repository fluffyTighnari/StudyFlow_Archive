"""把「上下文环 / 模型键 / token 用量」搬到发送键左边，并做成可点开的样子。

## 用户要的四件事

1. 上下文与模型放在**发送键左侧**（原来它们在卡片下面一行）；
2. 上下文是**圆圈状**展示，靠近可看到具体数量；
3. 模型**点击可改**（原来是个原生 `<select>`，样式受限、也没法显示"当前"）；
4. 展示**完整的 token 用量**：总量 / 缓存命中率 / 未命中输入 / 命中输入 / 输出。

## 一个必须说清的取舍

第 4 条里"缓存命中"这一格，只有在**提供方报了缓存读数**时才有值
（DeepSeek 报 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`；
本地 Ollama 与测试替身不报）。所以它显示成数值或**「没测到」**，
**绝不显示 0%** —— 0% 的意思是"测过了，一次都没命中"，那是另一件事。
后端这一轮为此给 `chat_message` 加了两个可空列（见 `var_ref/patch_cache_tokens.py`）。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\src\components\Composer.tsx"
)
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, *, label: str, count: int = 1) -> None:
    global text
    found = text.count(old)
    assert found == count, f"{label}：锚点出现 {found} 次（期望 {count}）"
    text = text.replace(old, new, count)
    print(f"✅ {label}")


# ------------------------------------------------------------------- 导入 ==
swap(
    """import {
  ArrowUpIcon,
  CloseIcon,
  LayersIcon,
  ListIcon,
  PaperclipIcon,
  PlusIcon,
  StopIcon,
} from "./icons";""",
    """import {
  ArrowUpIcon,
  CloseIcon,
  LayersIcon,
  ListIcon,
  PaperclipIcon,
  PlusIcon,
  StopIcon,
  TokenIcon,
  ChevronRightIcon,
} from "./icons";""",
    label="图标导入",
)

# --------------------------------------------------------------- 用量类型 ==
swap(
    """/** 每个 token 数都按"千"折叠，否则那一行会被一串数字撑开。 */
function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}""",
    """/** 每个 token 数都按"千"折叠，否则那一行会被一串数字撑开。 */
function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}

/** 带千位分隔的完整数字（点开用量面板时要看的是**准确值**）。 */
function fullNumber(n: number): string {
  return n.toLocaleString("en-US");
}

/**
 * 本会话的 token 用量。四个数各自独立：
 *
 * - `uncachedInput` 未命中缓存的输入；`cachedInput` 命中缓存的输入；
 * - `output` 生成了多少；
 * - `hitRate` **`null` = 没测到**（提供方没报缓存读数），不是 0。
 */
export interface TokenUsage {
  total: number;
  uncachedInput: number;
  cachedInput: number;
  output: number;
  hitRate: number | null;
}""",
    label="用量类型与格式化函数",
)

# ------------------------------------------------------------------ props ==
swap(
    """  contextTokens,
  completionTokens,
}: {""",
    """  contextTokens,
  completionTokens,
  tokenUsage,
}: {""",
    label="props 解构",
)

swap(
    """  /** 本次会话累计生成的 tokens（`null` = 一条都还没测到）。 */
  completionTokens: number | null;
}) {""",
    """  /** 本次会话累计生成的 tokens（`null` = 一条都还没测到）。 */
  completionTokens: number | null;
  /** 完整用量（总量 / 命中 / 未命中 / 输出）。`null` = 这个会话还没有任何读数。 */
  tokenUsage: TokenUsage | null;
}) {""",
    label="props 类型",
)

# ------------------------------------------------------------ 状态与菜单 ==
swap(
    """  const [menuOpen, setMenuOpen] = useState(false);""",
    """  const [menuOpen, setMenuOpen] = useState(false);
  /* 两个小浮层：模型选择、token 用量。各自独立，点外面一起关。 */
  const [modelOpen, setModelOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);""",
    label="浮层状态",
)

# 点外面关闭：把两个新浮层也纳入
swap(
    """    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current !== null && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };""",
    """    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current !== null && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
        setModelOpen(false);
        setUsageOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      setModelOpen(false);
      setUsageOpen(false);
    };""",
    label="点外面 / Esc 关闭浮层",
)

PATH.write_text(text, encoding="utf-8")
print(f"\n第一阶段写完（{len(text.splitlines())} 行）")
