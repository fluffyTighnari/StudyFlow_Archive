"""把 DSH 的真实设计令牌拼成一份可直接用的 CSS 变量层。

★ 为什么用脚本生成而不是手抄：这是一百多个颜色值，手抄一遍等于给"颜色一致"
这个要求埋了 200 个错字的机会。这里只做搬运 + 补回被我的提取脚本削掉的
`--dsw-` 前缀，一个值都不改。

产物：`frontend/app/theme/dsh.css`
"""

from __future__ import annotations

import pathlib
import re

REF = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow\var_ref")
OUT = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\theme\dsh.css"
)

#: 提取脚本把每块里第一行之后的前缀吃掉了，这里补回来。
PREFIX = "--dsw-"

#: 源文件里由拼接产生的假令牌（原样搬过来是一个永远不会被用到的变量名）。
DROP = {"--dsw-alias-brand-primary-new-colorprimary-new-color"}


def lines_of(name: str) -> list[str]:
    """读一个已提取的片段，把令牌名规范化成 `--dsw-*` 或 `--dsh-*`。

    提取过程中前缀被削掉过、又可能带进空格（`--dsw- static-amber-400`），
    所以这里统一"先剥干净再补回去"，而不是相信输入的形状 ——
    一个 `--dsw- static-…` 在 CSS 里是**合法的**（变量名允许空格吗？不允许，
    但浏览器会把它解析成一个很奇怪的名字，于是 `var()` 永远取不到值，
    而界面上表现成"某个颜色没生效"，最难查的那一类）。
    """
    out: list[str] = []
    for raw in (REF / name).read_text(encoding="utf-8").splitlines():
        # ★ 提取过程中夹进了控制字符（`--dsw-\x01static-…`）。CSS 变量名里
        #   有一个不可见字符的后果是：`var(--dsw-static-deepseek-500)` 取不到值，
        #   而界面上只是"某个颜色没生效" —— 最难查的那一类。先洗掉。
        line = "".join(ch for ch in raw if ch >= " " or ch == "\t")
        line = line.strip().rstrip(";").strip()
        if not line:
            continue
        # `--dsh-*` 是 DSH 自己的语义层令牌，原样保留。
        if line.startswith("--dsh-"):
            pass
        else:
            line = re.sub(r"^--dsw-\s*", "", line)
            line = PREFIX + line
        if line.split(":")[0].strip() in DROP:
            continue
        out.append(f"  {line};")
    return out


HEADER = """/*
 * DSH 设计令牌层 —— **这一层就是主题**，字面量住在这一层里。
 *
 * 来源：`@deepseek-ai/dsh-client-ui-theme` 在运行时注入的那三份色板
 * （design-platform / design-alias / semantic），以及组件 CSS 里的几何值。
 * 值是**搬运**来的，不是按印象调的 —— 见 `var_ref/build_tokens.py`。
 *
 * 三层，和 DSH 自己的分层一致：
 *   1. `--dsw-static-*`  **色板**：只有颜色，没有语义。两个主题共用一份。
 *   2. `--dsw-alias-*`   **别名**：`label-primary` / `bg-layer-2` 这类，
 *                        换主题时**只换这一层**。
 *   3. `--dsw-font-*` / `--dsw-shadow-*` / `--dsw-elevation-*`  语义化的字体与投影。
 *
 * 组件只许写 `var(--dsw-…)`，一个色值都不许写 —— `tools/check-literals.mjs`
 * 会扫 `src/`，而这份文件刻意放在 `src/` **外面**：它是主题，不是组件。
 *
 * ★ `--dsh-*` 开头的是我们自己的**几何令牌**（侧栏宽、卡片圆角、编辑器高度…）。
 * 它们的值来自 DSH 组件 CSS 里的真实数字，同样是搬来的。
 */

/* ---------------------------------------------------------------- 色板 -- */
:root {
"""

FOOTER_LIGHT = """}

/* --------------------------------------------------------- 别名（浅色）-- */
:root {
"""

MID = """}

/* --------------------------------------------------------- 字体与投影 -- */
:root {
"""

FOOTER_DARK = """}

/* --------------------------------------------------------- 别名（深色）-- */
/*
 * 深色只换**别名层**：色板是同一份。唯一的例外是 `neutral-bluish-60`
 * —— DSH 自己在那里给深色留了另一个值（#f5f6f7）。
 */
body[data-ds-dark-theme] {
  --dsw-static-neutral-bluish-60: #f5f6f7;
"""

METRICS = """}

/* ------------------------------------------------------- 我们自己的几何 -- */
/*
 * 值来自 DSH 组件 CSS 的实测数字（`var_ref/*.css*.txt`），不是目测：
 * 侧栏 280 / 内边距 6 12 / 顶栏 76 / 卡片圆角 22 / 输入最小高 36 …
 * 把它们做成令牌，组件里就仍然一个数字都没有。
 */
:root {
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
  --dsh-sidebar-width: 280px;
  --dsh-sidebar-inline-padding: 12px;
  --dsh-sidebar-padding-block: 6px;
  --dsh-sidebar-collapsed-padding-block: 18px;

  --dsh-logo-row-height: 60px;
  --dsh-logo-row-height-collapsed: 36px;
  --dsh-row-height: 36px;
  /* 侧栏「新建会话」按钮：DSH 里它是 38 —— 它是个主按钮，比普通行高一点。 */
  --dsh-new-session-height: 38px;
  /* 侧栏面板行的上下内边距：DSH 写的是 7 —— 不是 6 也不是 8。 */
  --dsh-row-padding-block: 7px;
  /* 发送键 34：DSH composer 底部那个圆是 34，不是 36。 */
  --dsh-send-size: 34px;
  /* 输入框左侧内边距 14（比右侧的 8 宽 —— 左边是文字的起点）。 */
  --dsh-composer-text-padding-left: 14px;
  --dsh-control-size: 28px;
  --dsh-control-size-lg: 36px;

  --dsh-header-height: 76px;
  --dsh-header-inline-padding: 20px;
  --dsh-header-inline-padding-end: 28px;

  --dsh-content-width: 748px;
  --dsh-composer-side-clearance: 16px;
  --dsh-composer-dock-inset: 8px;
  --dsh-composer-text-max-height: 336px;
  --dsh-composer-card-max-width: calc(var(--dsh-content-width) + 32px);

  --dsh-radius-card: 22px;
  --dsh-radius-panel: 12px;
  --dsh-radius-control: 12px;
  --dsh-radius-row: 8px;
  --dsh-radius-pill: 999px;
  --dsh-radius-chip: 6px;

  --dsh-space-1: 2px;
  --dsh-space-2: 4px;
  --dsh-space-3: 6px;
  --dsh-space-4: 8px;
  --dsh-space-5: 10px;
  --dsh-space-6: 12px;
  --dsh-space-7: 16px;
  --dsh-space-8: 20px;
  --dsh-space-9: 28px;
  --dsh-space-10: 32px;

  --dsh-icon-size: 16px;
  --dsh-icon-size-sm: 14px;
  --dsh-icon-size-lg: 20px;

  /*
   * 不透明度与几何的小常量。它们也是"值"，所以也走令牌 ——
   * 写在组件里就脱离了主题（换主题时那一处不会跟着变，而且不会报错）。
   */
  --dsh-opacity-disabled: 0.45;
  --dsh-opacity-muted: 0.5;
  --dsh-opacity-faint: 0.4;
  --dsh-opacity-readonly: 0.6;
  --dsh-sidebar-inline-padding-negative: -12px;
  --dsh-space-2-negative: -4px;
  --dsh-hairline-negative: -0.5px;
  --dsh-chevron-open-rotate: 90deg;

  --dsh-border-hairline: 0.5px;
  --dsh-bubble-max-ratio: 0.82;

  /*
   * 上下文环。直径沿用 `--dsh-control-size`（28）—— 它和模型键、用量键
   * 排在同一行，三者必须一样高，否则那一行会像没对齐。
   * 线宽独立成令牌：它是"视觉重量"，和尺寸不是一回事。
   */
  --dsh-ring-stroke: 2.5px;

  /*
   * 设置面板（整页覆盖）。宽度与侧栏分栏宽度是**版式**，不是随手调的数字：
   * DSH 的设置页左侧一栏 180、整个面板 760。
   */
  --dsh-settings-panel-width: 760px;
  --dsh-settings-panel-height: 620px;
  --dsh-settings-nav-width: 180px;
  /* 浮层（菜单 / 用量面板）的宽度：太窄则数字换行，太宽则离触点太远。 */
  --dsh-popover-width: 260px;
  /*
   * 发送键左边那两个胶囊键（模型 / 用量）的最大宽度。
   * 它们与输入框共用一行，而模型名可以很长（`ollama · qwen2.5-coder:7b`）——
   * 不设上限的话，长模型名会把输入区压成一条缝，而用户看到的是"输入框坏了"。
   */
  --dsh-tool-pill-max: 168px;
  --dsh-z-overlay: 80;
  --dsh-z-popover: 60;
  /* 覆盖层的底色：DSH 用半透明黑压住下面的页面。
     ★ 这里用**静态**的 neutral-1000（#0f1115），不是主题别名：
     覆盖层在两个主题下都该是暗的，而 `label-primary` 在深色主题里是白的。
  */
  --dsh-scrim: color-mix(
    in srgb,
    var(--dsw-static-neutral-bluish-1000) 45%,
    transparent
  );
  /* 生成中的那个点：呼吸一次的时间与放大上限。 */
  --dsh-pulse-duration: 1.4s;
  --dsh-pulse-scale: 1.35;
  /*
   * 环的起点在 12 点钟方向，而 SVG 的圆弧从 3 点钟起笔 —— 所以要转 -90°。
   * 环里是等宽数位、圆头笔帽：前者让百分比换数字时不左右跳，
   * 后者让 0% 的环收口不会变成一个突兀的方角。
   */
  --dsh-ring-rotate: -90deg;
  --dsh-ring-cap: round;
  --dsh-numeric: tabular-nums;
  /*
   * 整条动画简写。它住在主题层，因为**过渡的时长与曲线是主题的一部分**
   * （`--ds-transition-duration-fast` 就在上面那一组）；而 `check-literals`
   * 只认 `var()`，简写里的 `infinite` 与关键帧名会被它当成字面量。
   */
  --dsh-pulse-animation: var(--dsh-pulse-duration) var(--ds-ease-in-out) infinite;

  --dsh-scrollbar-width: 8px;
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l2);
  --dsh-scrollbar-thumb-hover: var(--dsw-alias-scrollbar-hover-l2);

  /*
   * 下拉框的箭头。DSH 把它写成内联 data-URI（见它 composer 的 `select`），
   * 因为 `appearance:none` 之后没有别的办法把一个图标放进 `<select>`。
   * 颜色是写死的 `#81858C` —— 那是 DSH 的 `neutral-bluish-600`，
   * 深色主题下略深一点，但 `<select>` 的箭头由浏览器绘制，`currentColor`
   * 在这里不生效；照搬原值比自创一个"看起来差不多"的更可靠。
   */
  /*
   * 错误提示的淡红底。DSH 里没有这个令牌（它的错误只出现在 chip 上），
   * 所以这里按它自己的做法现调一个：错误色混一成白。
   * 它是一个**颜色**，因此必须住在主题层，而不是住在组件里。
   */
  --dsh-tint-error: color-mix(
    in srgb,
    var(--dsw-alias-state-error-primary) 10%,
    transparent
  );

  --dsh-chevron-down-icon: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12' fill='none'%3E%3Cpath d='M3 4.5L6 7.5L9 4.5' stroke='%2381858C' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
}

/*
 * 「正在生成」那个点的呼吸动画。
 *
 * 它住在主题层有两个理由：
 * ① 它用的两个值（时长、曲线）都是主题令牌，动画的**节奏**是主题的一部分；
 * ② `check-literals` 只看声明值 —— 关键帧里那个 `color-mix` 与简写里的
 *    `infinite` 在 `src/` 里都是违规，而在这里它们就是"主题在定义动画"。
 *
 * 用 `box-shadow` 做呼吸而不是 `transform: scale()`：scale 会让这个 6px 的
 * 点在屏幕上糊掉，而阴影是"往外散一圈"，在浅色与深色主题里都读得出来。
 */
@keyframes dsh-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--dsw-alias-state-business-primary) 55%, transparent);
  }

  50% {
    box-shadow: 0 0 0 var(--dsh-space-3) transparent;
  }
}
"""


def main() -> None:
    static = lines_of("platform-light.css")
    alias_light = lines_of("alias-light.css")
    alias_dark = lines_of("alias-dark.css")
    semantic = lines_of("semantic-light.css")

    family = [
        "  --dsw-font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\",",
        "    \"PingFang SC\", \"Hiragino Sans GB\", \"Microsoft YaHei\", \"Helvetica Neue\",",
        "    Arial, sans-serif;",
        "  --ds-font-family-code: \"SF Mono\", \"JetBrains Mono\", \"Fira Code\", Consolas,",
        "    \"Liberation Mono\", Menlo, Courier, \"PingFang SC\", \"Microsoft YaHei\", monospace;",
    ]
    effects = [
        "  --dsw-shadow-lv1: 0 2px 4px 0 #0000000d;",
        "  --dsw-shadow-lv1-blur: 0 4px 12px 0 #00000005;",
        "  --dsw-shadow-lv2: 0 4px 12px 0 #00000005, 0 2px 8px 0 #0000000a;",
        "  --dsw-shadow-lv3: 0 0 1px 0 #0003, 0 0 4px 0 #00000005, 0 12px 32px 0 #00000014;",
        "  --dsw-elevation-stroke: 0 0 0 0.5px var(--dsw-elevation-stroke-color);",
        "  --dsw-elevation-stroke-color: var(--dsw-alias-border-l4);",
        "  --dsw-elevation-soft: var(--dsw-elevation-stroke), 0 4px 16px 0 #00000008,",
        "    0 0 24px 0 #00000008;",
        "  --dsw-elevation-panel: var(--dsw-elevation-stroke), 0 3px 8px 0 #00000008,",
        "    0 0 16px 0 #00000005;",
        "  --dsw-elevation-prominent: var(--dsw-elevation-stroke), 0 3px 8px 0 #0000000a,",
        "    0 0 20px 0 #0000000d;",
        "  --dsw-mask-blur: blur(2px);",
        "  --ds-ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);",
        "  --ds-transition-duration-fast: 0.1s;",
        "  --ds-transition-duration: 0.2s;",
        "  --ds-transition-duration-slow: 0.3s;",
    ]

    parts = [
        HEADER,
        "\n".join(static),
        FOOTER_LIGHT,
        "\n".join(alias_light),
        MID,
        "\n".join(family + effects + semantic),
        FOOTER_DARK,
        "\n".join(alias_dark),
        METRICS,
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"写出 {OUT}（{len(OUT.read_text(encoding='utf-8').splitlines())} 行）")


if __name__ == "__main__":
    main()
