# 主题套件说明（THEME_SPEC）

> **这份文档是给"写主题包的人或模型"看的唯一说明书。** 它和
> `schema/theme.schema.json` 是同一份契约的两种形态：schema 管机器校验，
> 本文管人怎么想。两者不一致时以 schema 为准 —— 而且
> `test/schema.test.mjs` 会把不一致变成一次失败（本文的令牌表与 schema
> 的词表逐一对齐，少一个、多一个都会红）。

---

## 0. 一句话

**主题包 = 一组令牌的值 + 一段可选的自定义 CSS 片段。**
组件只读令牌（`var(--…)`），所以换主题 = 换一组值，**组件代码一行都不用改**。

```
theme-kit/
├── schema/theme.schema.json   # 契约：令牌词表 + 必填清单 + 主题包结构
├── themes/base-warm.json      # 默认主题（暖橙黄）
├── themes/midnight-ink.json   # 示例：深色 + css 片段（图案）
├── src/base.css               # 套件自己的基础样式（零字面量）
├── src/compile.mjs            # 就地把主题包编译成 CSS（纯函数）
├── src/theme.mjs              # 浏览器运行时：挂到页面 + 记住选择
├── src/validate.mjs           # 校验器（命令行）
└── tools/check-literals.mjs   # 零字面量检查器
```

---

## 1. 三层令牌：为什么这样分

分成三层不是为了好看，而是因为**"改颜料"和"改用途"是两件事**：

| 层 | 回答的问题 | 谁改它 |
|---|---|---|
| **primitive** | 「这个橙是什么橙」 | 想换配色的人 |
| **semantic** | 「页面底色该用哪个橙」 | 想换风格的人 |
| **component** | 「用户气泡要不要跟卡片不一样」 | 想给某个组件开小灶的人 |

关键性质：**令牌的值可以引用另一个令牌**（`--accent: var(--p-orange-500)`）。
于是"只改颜料"的主题不必把语义层抄一遍 —— 改 `--p-orange-*` 四个值，
整条语义链跟着变。这就是 `midnight-ink` 能只用 4 个 primitive 覆盖
就换掉整个色系的原因。

**css 变量没有层。** 编译后三层都写在同一个 `:root` 上。分层是**作者的**
心智模型，也是文档与校验的划分方式，不是运行时概念 —— 说清楚这一点，
是为了避免有人以为"层"能提供优先级。

---

## 2. 令牌清单

> 下面三张表就是全部令牌。**写主题包时不要发明名字** ——
> 校验器会拒绝未知令牌，并给出最接近的几个作为提示（`TK005`）。

<!-- tokens:begin -->

### 第一层：primitive（物理颜料，57 个）

| 令牌 | 是什么 |
|---|---|
| `--p-white` | 纯白（卡片底色、反白文字） |
| `--p-warm-50` | 最浅的暖底（页面底色） |
| `--p-warm-100` | 暖底第二级（侧栏、次级面） |
| `--p-warm-200` | 暖底第三级（内凹面、分隔） |
| `--p-warm-300` | 暖底第四级（描边、进度槽） |
| `--p-ink-100` | 墨色第 8 浅（灰底块、代码块） |
| `--p-ink-200` | 墨色第 7 浅（弱描边） |
| `--p-ink-300` | 墨色第 6 浅（强一点的描边） |
| `--p-ink-400` | 墨色第 5 浅（更弱的文字） |
| `--p-ink-500` | 墨色第 4 浅（弱化文字） |
| `--p-ink-700` | 墨色第 3 深（次级文字） |
| `--p-ink-800` | 墨色第 2 深（标题） |
| `--p-ink-900` | 墨色第 1 深（正文主体） |
| `--p-orange-400` | 主色第 4 档（浅） |
| `--p-orange-500` | 主色（默认强调） |
| `--p-orange-600` | 主色第 2 档（hover、描边） |
| `--p-orange-700` | 主色第 3 档（链接文字，压在浅底上要够黑） |
| `--p-green-500` | 成功色（浅） |
| `--p-green-600` | 成功色（文字用） |
| `--p-red-500` | 危险色（浅） |
| `--p-red-600` | 危险色（文字用） |
| `--p-blue-500` | 信息色（浅） |
| `--p-blue-600` | 信息色（文字用） |
| `--p-radius-xs` | 圆角最小档（4px 级） |
| `--p-radius-sm` | 圆角小档（8px 级） |
| `--p-radius-md` | 圆角中档（12px 级） |
| `--p-radius-lg` | 圆角大档（16px 级） |
| `--p-radius-xl` | 圆角特大档（24px 级） |
| `--p-radius-pill` | 全圆角（胶囊） |
| `--p-space-1` | 间距第 1 档 |
| `--p-space-2` | 间距第 2 档 |
| `--p-space-3` | 间距第 3 档 |
| `--p-space-4` | 间距第 4 档 |
| `--p-space-5` | 间距第 5 档 |
| `--p-space-6` | 间距第 6 档 |
| `--p-space-7` | 间距第 7 档 |
| `--p-space-8` | 间距第 8 档（大块留白靠它） |
| `--p-text-xs` | 字号最小档（标注） |
| `--p-text-sm` | 字号小档（辅助） |
| `--p-text-base` | 正文字号 |
| `--p-text-lg` | 字号大档（小标题） |
| `--p-text-xl` | 字号特大档（卡片标题） |
| `--p-text-2xl` | 字号 2 档标题 |
| `--p-text-3xl` | 字号 3 档标题 |
| `--p-leading-tight` | 行高紧凑 |
| `--p-leading-normal` | 行高正常 |
| `--p-leading-relaxed` | 行高宽松 |
| `--p-font-sans` | 界面字体栈 |
| `--p-font-mono` | 等宽字体栈 |
| `--p-shadow-sm` | 阴影最轻 |
| `--p-shadow-md` | 阴影中档（卡片） |
| `--p-shadow-lg` | 阴影大档（浮层） |
| `--p-duration-fast` | 过渡时长最短 |
| `--p-duration-base` | 过渡时长默认 |
| `--p-duration-slow` | 过渡时长最长 |
| `--p-ease-out` | 缓动：出场 |
| `--p-ease-standard` | 缓动：标准 |

### 第二层：semantic（用途，50 个）

**必填的 14 个**用 ⭐ 标出：不写 `extends` 的主题包必须回答它们。

| 令牌 | 是什么 |
|---|---|
| `--bg-app` ⭐ | 页面底色 |
| `--bg-surface` ⭐ | 卡片/面板底色 |
| `--bg-surface-2` | 次级面板底色（比 surface 深一档） |
| `--bg-inset` ⭐ | 内凹区域（代码块、输入框槽） |
| `--bg-overlay` | 遮罩层（对话框背后） |
| `--fg-primary` ⭐ | 正文主体文字 |
| `--fg-secondary` ⭐ | 次级文字（说明、副标题） |
| `--fg-muted` | 弱化文字（时间戳、计数） |
| `--fg-on-accent` ⭐ | 压在 `--accent` 上的文字 |
| `--fg-link` | 链接文字 |
| `--accent` ⭐ | 强调色（主按钮、当前项） |
| `--accent-hover` ⭐ | 强调色的 hover 态 |
| `--accent-soft` | 强调色的淡底（选中行、标签底） |
| `--accent-border` | 强调色的描边 |
| `--border-subtle` ⭐ | 弱描边（卡片轮廓） |
| `--border-strong` ⭐ | 强描边（聚焦、分隔） |
| `--border-accent` | 强调描边 |
| `--state-ok` ⭐ | 成功/通过 |
| `--state-ok-soft` | 成功底 |
| `--state-warn` ⭐ | 警告 |
| `--state-warn-soft` | 警告底 |
| `--state-danger` ⭐ | 危险/拒绝 |
| `--state-danger-soft` | 危险底 |
| `--state-info` | 信息 |
| `--state-info-soft` | 信息底 |
| `--radius-control` | 控件圆角（按钮、输入框） |
| `--radius-card` | 卡片圆角 |
| `--radius-panel` | 大面板圆角 |
| `--radius-chip` | 标签/胶囊圆角 |
| `--border-width` | 描边宽度 |
| `--focus-ring-width` | 聚焦环宽度 |
| `--focus-ring-offset` | 聚焦环外扩距离 |
| `--space-gutter` | 容器内边距（页面呼吸感） |
| `--space-section` | 区块之间的距离 |
| `--space-inline` | 行内元素之间的距离 |
| `--space-stack` | 纵向堆叠之间的距离 |
| `--font-ui` | 界面字体 |
| `--font-mono` | 等宽字体 |
| `--text-body` | 正文字号 |
| `--text-caption` | 标注字号 |
| `--text-title` | 标题字号 |
| `--text-display` | 大标题字号 |
| `--leading-body` | 正文行高 |
| `--shadow-card` | 卡片阴影 |
| `--shadow-pop` | 浮层阴影 |
| `--shadow-inset` | 内凹高光 |
| `--duration-fast` | 快速反馈（hover） |
| `--duration-base` | 常规过渡 |
| `--ease-out` | 出场缓动 |
| `--focus-ring` ⭐ | 聚焦环颜色（键盘可用性靠它） |
| `--layout-sidebar` | 侧栏宽度 |
| `--layout-content-max` | 主内容的最大宽度（**大块留白**靠它，不是靠写死 px） |
| `--layout-chat` | 讨论面板宽度 |
| `--opacity-disabled` | 不可用控件的不透明度（禁用态要"看得见但点不动"） |

### 第三层：component（组件槽位，28 个）

| 令牌 | 是什么 |
|---|---|
| `--chat-bubble-user-bg` | 用户气泡底 |
| `--chat-bubble-user-fg` | 用户气泡文字 |
| `--chat-bubble-assistant-bg` | 助手气泡底 |
| `--chat-bubble-assistant-fg` | 助手气泡文字 |
| `--chat-note-fg` | 系统提示文字（"这次讨论没有留下结论"） |
| `--chat-thinking-bg` | 思考过程的底（与回答必须能分开看） |
| `--plan-step-bg` | 计划项底 |
| `--plan-step-current-bg` | 正在讨论的那一项底 |
| `--plan-step-current-border` | 正在讨论的那一项描边 |
| `--plan-step-done-fg` | 已完成项文字 |
| `--plan-step-pending-fg` | 未开始项文字 |
| `--checkpoint-bg` | 检查点底 |
| `--checkpoint-border` | 检查点描边 |
| `--stage-chip-bg` | stage 标签底 |
| `--stage-chip-fg` | stage 标签文字 |
| `--trace-bg` | 工具轨迹底 |
| `--trace-border` | 工具轨迹描边 |
| `--trace-ok-fg` | 工具成功文字 |
| `--trace-rejected-fg` | 工具被拒文字 |
| `--pack-precedence-fg` | 上下文包里"优先级"标记文字 |
| `--pack-dropped-fg` | 上下文包里"被排除"文字 |
| `--shell-sidebar-bg` | 侧栏底 |
| `--shell-topbar-bg` | 顶栏底 |
| `--shell-divider` | 分隔线颜色 |
| `--badge-inferred-bg` | **推断**类徽章底（必须一眼可辨） |
| `--badge-inferred-fg` | **推断**类徽章文字 |
| `--badge-stated-bg` | **用户声明**类徽章底 |
| `--badge-stated-fg` | **用户声明**类徽章文字 |

<!-- tokens:end -->

> **为什么 badge 那两个必须存在**：这个系统的核心主张是"分清哪些是你说的、
> 哪些是系统推断的"。推断类的东西**必须在视觉上就能被认出来** ——
> 靠用户去读每一个徽章上的小字，等于没做这件事。所以它们不是装饰性令牌。

---

## 3. 主题包格式

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 小写字母/数字/连字符；**必须与文件名一致**（`themes/<id>.json`），否则 `TK003` |
| `name` | ✅ | 界面上给用户看的名字 |
| `appearance` | | `light` / `dark`。系统偏好为暗色且用户没挑过时，套件取第一个 `dark` 主题 |
| `description` | | 一句话说明这个主题是什么味道 |
| `extends` | | 继承另一个主题包的缺省值。**写了它，14 个必填令牌就可以不写** |
| `tokens` | ✅ | 三层令牌的值。可以有 `primitive` / `semantic` / `component` 三组 |
| `css` | | 自定义片段（见第 4 节） |

### 最小合法主题包（只改主色）

```json
{
  "$schema": "../schema/theme.schema.json",
  "id": "sunset",
  "name": "落日橘",
  "appearance": "light",
  "extends": "base-warm",
  "tokens": {
    "primitive": {
      "--p-orange-500": "#e2553d",
      "--p-orange-600": "#c43f2a"
    }
  }
}
```

**它只写了两个值，但整个界面的强调色都跟着变了** —— 因为语义层里
`--accent: var(--p-orange-500)` 这条引用是基础主题提供的，而引用会在
运行时解析到新值。

### 完整主题包必须回答的 14 个问题

不写 `extends` 时必填（缺一个就是 `TK007`）：
`--bg-app` `--bg-surface` `--bg-inset` `--fg-primary` `--fg-secondary`
`--accent` `--accent-hover` `--fg-on-accent` `--border-subtle` `--border-strong`
`--state-ok` `--state-warn` `--state-danger` `--focus-ring`

---

## 2.4 意图 → 令牌：想改什么，就改哪里

> **这一节是给"按需求生成主题包"用的，也是最容易用错的一节。**
> 需求里的说法是"圆角小一点"「正文大一号」这类**程度词**，而令牌是按
> **用途**分的 —— 中间这一步对应关系，靠猜很容易改错地方：
> 改了令牌、校验也通过了，但界面上几乎看不出变化。
>
> 真实运行里就发生过：模型把"圆角小一点"落到了 `--p-radius-xs` 上，
> 而那个令牌只用于很小的标签 —— **需求静默地没实现**。

| 你想要的效果 | 改这些令牌 |
|---|---|
| 整体换色系 | primitive 的 `--p-orange-400…700`（主色）、`--p-warm-*`（底色）、`--p-ink-*`（文字） |
| 页面底色 | `--bg-app`（或 `--p-warm-50`） |
| 卡片 / 面板底色 | `--bg-surface` / `--bg-surface-2` / `--bg-inset` |
| 正文文字颜色 | `--fg-primary`（或 `--p-ink-900`） |
| 次级 / 弱化文字 | `--fg-secondary` / `--fg-muted` |
| 主色（按钮、当前项、链接强调） | `--accent`（或 `--p-orange-500`）+ `--accent-hover` |
| 压在主色上的文字 | `--fg-on-accent`（⚠ 换了 `--accent` 就要回头看它） |
| **圆角整体变小 / 变大** | `--radius-card`（卡片）、`--radius-control`（按钮与输入框）、`--radius-panel`、`--radius-chip`；**或者**改 primitive 的 `--p-radius-sm/md/lg`（语义层是 `var()` 引用，会跟着变） |
| **正文更大 / 更小** | `--text-body`（或 `--p-text-base`）；标题另用 `--text-title` / `--text-display` |
| 行距更松 / 更紧 | `--leading-body`（或 `--p-leading-relaxed` / `--p-leading-tight`） |
| 更宽松 / 更紧凑的留白 | `--space-gutter`（页面呼吸感）、`--space-section`、`--space-stack`、`--space-inline` |
| 描边更明显 / 更轻 | `--border-subtle`、`--border-strong`、`--border-width` |
| 阴影更轻 / 更重 | `--shadow-card`、`--shadow-pop`、`--shadow-inset` |
| 字体 | `--font-ui`、`--font-mono`（或 primitive 的 `--p-font-sans` / `--p-font-mono`） |
| 键盘聚焦环 | `--focus-ring`、`--focus-ring-width`、`--focus-ring-offset` |
| 侧栏宽度 / 内容最大宽 / 讨论面板宽 | `--layout-sidebar`、`--layout-content-max`、`--layout-chat` |
| 动态反馈快慢 | `--duration-fast`、`--duration-base`、`--ease-out` |
| **图案、滤镜、多层背景、动画** | 只能写 `css` 片段（挂在第 5 节的公开钩子上） |

**两个最容易改错的地方**（都是真实运行里出现过的）：

- **`--p-radius-xs` 不是"圆角"那个开关。** 它只用于很小的标签与徽章。
  想让整个界面"硬朗一点"，要改 `--radius-card` / `--radius-control`，
  或者改 `--p-radius-sm` / `--p-radius-md` 这些**被语义层引用**的档位。
- **改 `--accent` 之后一定要检查 `--fg-on-accent`。** 它们是一对：
  原来的"白字压橙底"或"墨字压橙底"是按旧主色算的，换了主色可能就看不见了。
  校验器会用 `TK101` 提醒你，但**它只提醒、不拒绝**（见第 7 节）。

---

## 4. 自定义片段的边界

`css` 片段是为了令牌表不出来的东西：**图案、滤镜、多层背景、动画**。
它会被套件包进 `@layer theme-extra { … }`，**你不要自己写 `@layer`**。

### 校验器会拒绝的构造（这些都是错误）

| 编号 | 拒绝的构造 | 为什么 |
|---|---|---|
| `TK009` | `@import` | 主题长相取决于网络与远端，加载顺序不可控 |
| `TK009` | `@layer` | 层序是全局级联的一部分；让每个包各自声明层序，等于让最后加载的那个决定整个应用的层叠 |
| `TK009` | `@charset` | 只允许出现在样式表开头，放进片段不会生效 —— 只会让人以为它生效了 |
| `TK009` | `!important` | 唯一能盖过套件自身可读性兜底的构造。主题是"换一套值"，不是"赢过所有人" |
| `TK009` | `</` | 能从 `<style>` 元素里逃出去（`content: "</style>"` 也算） |
| `TK009` | `javascript:` / `expression(` / `-moz-binding` | 旧式脚本注入载体 |
| `TK010` | 括号不平衡 | 不平衡的片段会把后面所有规则一起吃掉 |
| `TK011` | 超过 20000 字符 | 主题包不该长成一个应用 |
| `TK013` | **用逗号分隔声明**（`a:1,b:2`） | 分隔声明必须用 `;`。逗号会让整段变成**一个**无效声明，浏览器全部丢掉 —— 图案根本不会出现，而括号是配平的，别的检查看不出来 |

### 校验器只会提醒的构造

| 编号 | 构造 | 为什么提醒 |
|---|---|---|
| `TK103` | `position: fixed` | 会盖住界面其它东西，且跟着视口而不是跟着应用 |
| `TK103` | 过大的 `z-index` | 会压住套件自己的浮层（对话框、菜单） |
| `TK103` | `url(http…)` | 离线时主题是坏的，且会把访问记录发给第三方 |
| `TK101` | 对比度偏低 | 仍然能跑，只是有人会看不清 |

### 说清楚：这一段**只有一部分是机器保证的**

校验器管的是**构造**（上表那些）与**括号平衡**，它**不判断**你的选择器
好不好、写得对不对。一个语法完全合法但把按钮藏起来的片段会照样通过。

所以：
- 片段请**只挂在第 5 节列出的公开钩子上**；
- 拿不准就别写片段 —— 令牌能表达的，一律用令牌。

这条边界写在这里，是为了不让人以为"校验通过 = 片段没问题"。
**假装它被管住了，比不管它更坏。**

---

## 5. 公开的样式钩子

主题包（尤其是 css 片段）**只能依赖这些类名**。组件内部没有稳定类名，
这是刻意的：有稳定类名就会有人去依赖它，然后组件一重构主题就碎。

| 钩子 | 挂在哪 |
|---|---|
| `.tk-shell` | 整个应用的最外层容器 |
| `.tk-surface` | 大块内容面板（主题片段常在这里加图案） |
| `.tk-topbar` | 顶栏 |
| `.tk-sidebar` | 侧栏 |
| `.tk-divider` | 分隔线 |
| `.tk-stack` / `.tk-row` | 纵向堆叠 / 横向一行 |
| `.tk-muted` | 弱化文字 |
| `.tk-focusable` | 需要聚焦环的可交互元素 |

### `.tk-surface` 额外提供的两条保证

1. 它是**定位上下文**（`position: relative`），所以 `::before` 上的 `inset` 是相对面板算的；
2. 它**自成层叠上下文**（`isolation: isolate`），且直接子元素被抬到内容层之上
   —— 所以 `z-index: -1` 的图案会落在**内容之下、面板背景之上**，不会跑到面板背后去。

于是最常见的图案写法是安全的：

```css
.tk-surface::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: -1;
  border-radius: inherit;   /* 这一条别省 */
  background-image: repeating-linear-gradient(45deg, transparent 0, #e6dec2 2px, transparent 3px);
}
```

`border-radius: inherit` 那一条**不要省**：少了它，图案的直角会从面板的圆角处露出来。
这不在校验器的职责范围内（见第 4 节最后一段）—— 写在这里是因为它太容易漏。

---

## 6. 怎么跑检查

```bash
cd frontend/theme-kit
npm run validate     # 校验全部主题包（退出码 0/1/2）
npm run literals     # 零字面量检查
npm test             # 套件自己的测试（node --test，零依赖）
npm run check        # 三件一起
```

退出码的口径与后端的架构检查一致：
**`0` = 通过；`1` = 有拒绝项；`2` = 检查器本身没能跑起来。**
`2` 必须与 `1` 分开 —— 否则"契约文件读不到"会被读成"主题包有问题"。

单独看一个包：

```bash
node src/validate.mjs themes/midnight-ink.json
node src/validate.mjs --json          # 机器可读
```

---

## 7. 错误编号速查

| 编号 | 含义 |
|---|---|
| `TK001` | 不是合法 JSON |
| `TK002` | 结构问题（未知字段/缺字段/id 形态/appearance 取值/未知层） |
| `TK003` | id 与文件名不一致 |
| `TK004` | `extends` 指向不存在的主题包（或指向自己） |
| `TK005` | 未知令牌名（会给出最接近的几个） |
| `TK006` | 令牌值非法（非字符串/空/超长/含 `;` `{` `}`） |
| `TK007` | 缺必填令牌（无 `extends` 时） |
| `TK008` | `var()` 引用了不存在的令牌 |
| `TK009` | 片段里有被禁构造 |
| `TK010` | 片段括号不平衡 |
| `TK011` | 片段超长 |
| `TK012` | 值的类型不对（长度类令牌没写长度、行高带了单位、时长没写单位、颜色令牌里塞了边框简写） |
| `TK013` | 片段里用逗号分隔声明（要用 `;`） |
| `TK101` | 对比度偏低（提醒） |
| `TK102` | 对比度无法判定（提醒 —— **这不是"没问题"**） |
| `TK103` | 片段里有值得注意的构造（提醒） |

---

## 8. 应用侧怎么接（三行）

```js
import "@mouxue/theme-kit/base.css";          // 钩子与基础样式
import { applyTheme, chooseTheme } from "@mouxue/theme-kit";
import { THEME_PACKS, BASE_THEME_ID } from "@mouxue/theme-kit/packs";

applyTheme(chooseTheme({ packs: THEME_PACKS, baseId: BASE_THEME_ID }), {
  base: THEME_PACKS[0],
});
```

换主题就是再调一次 `applyTheme(otherPack, { base: baseTheme })` ——
**没有别的动作**：不动 DOM 结构、不重建组件树、不触发任何组件状态。
