"""把 §6.11（P16 第三块）写进 `_M9`，并更新页脚与全景文档里的规模数字。

## 为什么用脚本改文档

`_M9` 有 1 375 行、全景文档 1 300+ 行。手改容易在别处漏一个数字，
而这份文档的价值恰恰在于"里面的数字是真的"。脚本里的每一处替换都带断言：
替换不上就报错，而不是安静地什么都不做。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow")
M9 = ROOT / "_M9_前端与主题套件_v0.1.md"
OVERVIEW = ROOT / "_交付评审_当前状态全景_v0.1.md"

SECTION = """### 6.11 主页面第三块：**生成中的样子、发送键左边那一组、设置页**（P16 第三块）

#### 6.11.1 用户的原话，与它要求的东西

> 「文件夹展开时应该有文件夹展开的图标显示」
> 「正在回复时 session 左侧应该有动态进行中提示，鼠标聚焦时同样应该有提示，
> 右侧应该有上次更新时间」
> 「上下文和模型应该放在发送键左侧 上下文是一个圆圈状展示，靠近可展示具体
> 使用数量；模型点击应该支持更改」
> 「同时应该展示完整的 Token usage（总量 / Cache hit / Uncachecd input /
> Cached input / Output）」
> 「设置也得开始做，general 直接复刻原本的 dsh 模版」（语言 / 外观 / 字号 /
> 对话显示 / 忙碌时发送 / 轮次完成通知 / 权限通知 / 问题通知）

拆成 7 件：①文件夹展开时用「张开」的图标 ②生成中的会话行有动态点、
悬停有提示 ③会话行右侧有上次更新时间 ④上下文与模型挪到发送键左侧、
上下文是圆圈（靠近可看具体数量）⑤模型可点开更换 ⑥完整的 token 用量
（五个数）⑦设置页（照 DSH 的 General 那一页）。

#### 6.11.2 文件夹开合：换图标，而不是转箭头

原来那个箭头是"旋转 90° 表示展开"。用户要的是"展开时应该有展开的图标" ——
于是收起时是封口的夹子（`FolderIcon`），展开时是张开的夹子（`FolderOpenIcon`）。

两者说的不是同一件事：**箭头说的是"这里能点"，图标说的是"它现在是开的"**。
一个收起的文件夹和一个展开的文件夹，扫一眼就该分得出来，而不是靠记住箭头朝向。
按钮仍然是那个可聚焦的键（键盘用户走的是它），图标只是它的内容。

#### 6.11.3 生成中的提示：状态得先被提到 App

「正在回复时左侧应该有动态提示」这句在结构上要求一件事：**侧栏要知道
右侧正在生成的是哪个会话**。而 `sending` 原本住在 `ConversationView` 里 ——
侧栏看不见它。于是 `streamingSessionId` 上提到 `App`，
`ConversationView` 通过 `onStreamingChange(sessionId | null)` 上报。

这一条是这一批里唯一"必须动结构"的地方，其余都是加在已有位置上的。

三处细节，每一处都有代价被算过：

- **动态点用 `box-shadow` 呼吸，不是 `transform: scale()`**：6px 的点在缩放时
  会糊成一块，而阴影是"往外散一圈"，在两个主题下都读得出来；
- **`prefers-reduced-motion: reduce` 时不动画，但保留颜色与外圈**：
  关掉动画不等于关掉这个信息，否则"减少动态"的用户会失去"正在生成"这件事；
- **上次更新时间平时 `width: 0` + 透明，悬停/聚焦/生成中才展开**：
  用 `display: none` 会让"出现"变成一次布局跳动，整行左右弹一下；
  用宽度过渡，标题被挤窄一点，位置不动。

时间的写法是相对时间（「9 小时前」），不是时间戳：在侧栏那一列里，
用户想知道的是"这条新不新"，不是"它几点几分"。悬停提示（`title`）里同时给出
状态、是否正在生成、上次更新、所属文件夹 —— 一行说清四件事。

#### 6.11.4 上下文环：为什么是圆，以及为什么环里不写数字

用户点名要圆圈。它在这个位置上比一条进度线更好的**唯一**理由是：
**直径不随数字变化**。左下角那一排控件不会因为"上下文从 3% 涨到 41%"
而左右跳动 —— 而跳动一次就会把发送键推走一次。

环里只写百分比（11px），完整数字在悬停提示与点击展开里：

```
上下文：已用 8 132 tok / 上限 131 072 tok（6%）
```

两个数字都有出处：已用是**最近一次回复实测**的 `prompt_tokens`
（后端落库的那一列，不是估算），上限是 `/api/llm/config` 里的 `num_ctx`。
都没有时就显示「—」和「没测到」，不显示 0% —— 0% 是另一句话。

同一个读数原来在输入卡下面还有一行（「上下文 8.1k / 131k」+「生成 1.2k」）。
环与用量面板接手之后，那一行删掉了：**同一个数字出现在两处，
两处迟早会不一致**，而用户不知道信哪个。

#### 6.11.5 模型：从 `<select>` 换成自绘菜单

`<select>` 在这里有两个改不动的问题：

1. 它的样子由系统决定 —— 这一页的圆角、字号、留白对它无效；
2. **它没法显示"哪一个是当前的"是事实**。它只能靠 `value` 匹配选项，而
   "配置文件里直接给了 provider + model、却没进档案列表"是很常见的情况
   （本次预览库就是这样）。这时下拉框的第一项会变成"当前"，用户会以为
   自己在选一个不存在的档案。

自绘菜单把两件事分开：第一组「当前生效」是一行**事实**（不可点），
第二组「换成」是**动作**。只有一份档案时，按钮直接 `disabled` 并在菜单里
说明"要有多份可选，得在设置里加模型档案（`/api/llm/profiles` 还没有写入端点）" ——
摆一个能点但只有一项的菜单，是一次白费等一次点击。

#### 6.11.6 完整的 token 用量：`null` 与 `0` 是两句话

面板上五格：**总量 / 缓存命中 / 未命中输入 / 命中输入 / 输出**。

数据来自 `chat_message` 的实测列，本轮为此给表加了两列
（`cache_hit_tokens` / `cache_miss_tokens`，都可空）：

| 读数 | 来源 | 没有时显示 |
|---|---|---|
| 总量 | `Σ prompt_tokens + Σ completion_tokens` | 没测到 |
| 缓存命中 | `Σ cache_hit_tokens ÷ Σ prompt_tokens` | 没测到 |
| 命中输入 | `Σ cache_hit_tokens` | 没测到 |
| 未命中输入 | `Σ cache_miss_tokens`，没有就用 `prompt − hit` 反推 | 没测到 |
| 输出 | `Σ completion_tokens` | 没测到 |

**为什么 `null` 不能当 0**：DeepSeek 报 `prompt_cache_hit_tokens`，本地 Ollama
与测试替身不报。把不报当成 0%，界面上就变成"缓存一次都没命中" ——
那是对**提供方能力**的错误陈述，而且用户没有任何办法从界面上看出来。
`null` 一路传到界面上是「没测到」，这是这个产品里已经用过一次的规矩
（`consumed_tokens` 缺失时也是这么显示的）。

**没有做的一件事**：不给这两个数加"校验"（比如"命中不能大于 prompt"）。
那会把提供方的一个 bug 变成我们悄悄改过的数字 —— 而错的是它的报告。
数字对不上时，看数据的人应该看得见对不上（e2e 里只查这一条一致性）。

#### 6.11.7 设置页：七行，三种状态

照 DSH 的「设置 → 通用」做：整页覆盖的对话框，左栏「通用」，右栏分区。

**每一行都带一个状态**（`ready` / `partial` / `absent`），这是这一页最重要的
设计决定：

| 行 | 状态 | 落在哪里 |
|---|---|---|
| 界面语言 | `absent` | 文案目前只有中文；加英文是一份完整的翻译表，摆一个切了没反应的下拉框更糟 |
| 主题（浅色 / 深色 / 跟随系统） | ✅ | `body[data-ds-dark-theme]` —— 深色那一层令牌早就备好了，缺的只是开关 |
| 字号（12–16，只影响对话内容） | ✅ | `<html data-reading-size>` → `--dsh-content-font-size` |
| 对话显示（详细 / 紧凑） | ✅ | 已完成轮次的思考与工具轨迹用 `<details open>` 折起来（不是隐藏） |
| 忙碌时的发送行为（排队 / 打断） | ✅ | `ConversationView` 里的 `queuedRef` 与 `abort()` |
| 轮次完成通知（总是 / 仅失焦 / 从不） | `partial` | 浏览器通知；**没权限时如实说"被挡住了"** |
| 问题提醒（开 / 关） | ✅ | `GET /{id}/clarifications?status=open` 里有未答的问题时提醒一次 |
| 权限提醒 | `absent` | 本产品的工具调用不需要逐个批准（作用域在代码里定死），没有可提醒的东西 |

`absent` 那几行不摆假开关，只写"为什么没有"。这条规矩在 §6.9 的 `+` 菜单上
已经用过一次 —— **一个点了没反应、或者开了永远等不到效果的开关，比没有更坏**。

两个实现细节：

- **字号为什么走属性 + 五个令牌**：`check-literals` 只认 `var()`。把
  `14px` 塞进 `style.setProperty()` 只是把那个数字换了个文件放。所以令牌五档
  （`--dsh-reading-size-12…16`），界面只打 `data-reading-size`，档位到令牌的
  映射写在 CSS 里；
- **`#settings` 能直接打开设置**：既是一个说得通的深链（刷新后设置还开着），
  也让**无头浏览器真的能看见这个面板** —— `--dump-dom` 点不了按钮，
  而在这一批里，设置页是唯一"不打开就看不见"的交付物。

#### 6.11.8 排队与打断：默认是排队，Cmd/Ctrl+Enter 是另一档

与 DSH 一致：默认 Enter = 排队，修饰键 = 那个设置里没选的行为。

- **排队只留最后一条**：留一队的话，用户中途改主意想说别的，前一句还会在后面
  被发出来 —— 而那时它已经不成立了；
- **存的是文本 + 当时锚定的那一步**：等轮次结束再发时，用户可能已经点了别的
  计划项；若那时才读 `focusStep`，这条消息就会挂到另一步上，而他从没改过目标；
- **离开会话就清掉排队**：它是"发给这个会话"的，跟到别的会话去发是错的；
- 排队期间页面上有一句「已排队，等这一轮说完就发：「…」」—— 取消它的方式是把
  草稿删掉再说一遍，所以**必须看得见它已经排上了**，否则用户以为被吞了。

#### 6.11.9 这一轮拦下的两个缺陷

**① 缓存读数接到了错的对象上（12 条测试当场红）**。
`add_sidebar_message` 写的是 `SidebarMessage`，而我给它加了 `cache_hit_tokens`
参数并往构造器里传 —— 但模型那一侧只给 `ChatMessage` 加了列。
SQLAlchemy 的报错很直接（`TypeError: 'cache_hit_tokens' is an invalid keyword
argument for SidebarMessage`），问题出在我把"两个长得很像的消息表"当成了
一张。修法不是给侧边栏也加列（侧边栏的用量面板不在这条需求里），
而是**把那个改动退回去**：少一处 schema 变更，少一个将来要维护的东西。

**② 覆盖层的底色在两个主题下反了**。
第一版把 `--dsh-scrim` 写成了
`color-mix(in srgb, var(--dsw-alias-label-primary-inverted) 45%, transparent)`。
`label-primary-inverted` 在浅色主题里是**白**（00），在深色主题里是**深灰**（800）——
于是"压暗下面那层"在浅色主题下变成了"提亮"。改成静态的
`--dsw-static-neutral-bluish-1000`（`#0f1115`）：覆盖层在两个主题下都该是暗的。

#### 6.11.10 验证（这一轮跑过的每一条）

| 检查 | 结果 |
|---|---|
| `pytest -q` | **1 429 条全绿**（+4：`tests/session/test_chat_cache_readings.py`） |
| `python -m tools.archcheck` | 架构依赖检查通过：无违规 |
| `npm run literals` | 零字面量检查通过（新增 7 处字面量都改成了令牌：`--dsh-ring-rotate` / `--dsh-ring-cap` / `--dsh-numeric` / `--dsh-pulse-animation` / `@keyframes dsh-pulse` 移进主题层） |
| `tsc -b` + `vite build` | 通过（CSS 72.22 kB / JS 323.34 kB） |
| `tools/e2echeck`（新库 + 真服务） | **33 步：可用 32 / 缺口 1 / 失败 0**（新增 `step_usage_readings`） |
| `var_ref/check_v3.py`（无头 Edge 对 DOM） | 13 项必检 + 3 项条件 + 7 项样式标记 + 14 项设置面板，全绿 |

`check_v3.py` 值得单独说一句：它除了核对"环在不在、模型键在不在"，
还核对了**展开着的文件夹用的是张开的那个图标**（SVG 的 path 在 DOM 里出现），
以及**上次更新时间是一句相对时间**。这两条正是"看着像做了、其实没接上"
最常发生的地方。

#### 6.11.11 交付物

| 路径 | 行数 | 改动 |
|---|---|---|
| `backend/app/session_domain/models.py` | — | `chat_message` 加 `cache_hit_tokens` / `cache_miss_tokens`（可空） |
| `backend/app/session_domain/writes.py` | 1 898 | `add_chat_message` 接受并落库两个缓存读数（进 `llm_fields` 拒绝集合） |
| `backend/app/session_domain/schemas.py` | — | `ChatMessageRead` 暴露两个读数 |
| `backend/app/agent/llm.py` | — | 两条路径（`chat` / `stream`）的 `meta` 都带 `cache_hit_tokens` / `cache_miss_tokens` |
| `backend/app/api/discuss.py` | — | 两处留档（非流式 / 流式）写入缓存读数 |
| `backend/tests/session/test_chat_cache_readings.py` | 110 | **新增 4 条** |
| `backend/tools/e2echeck.py` | — | 新增 `step_usage_readings`（第 16 步，共 33 步） |
| `frontend/app/src/settings.ts` | 190 | 设置的读写 / 落到 DOM / 通知（三种模式 + 权限如实报告） |
| `frontend/app/src/components/SettingsDialog.tsx` | 330 | 设置面板（七行 × 三种状态） |
| `frontend/app/src/components/Composer.tsx` | 614 | 发送键左边那一组：上下文环 / 模型菜单 / 用量面板；删掉卡片下方旧统计行 |
| `frontend/app/src/components/ConversationView.tsx` | 592 | 用量聚合、排队 / 打断、生成中上报、问题提醒 |
| `frontend/app/src/components/Sidebar.tsx` | 880 | 展开图标、动态点、悬停提示、上次更新时间、左下角设置入口 |
| `frontend/app/src/components/MessageList.tsx` | 245 | `compact`（过程内容折叠） |
| `frontend/app/src/App.tsx` | 528 | 装配设置、生成中状态、待回答的问题、`#settings` 深链 |
| `frontend/app/src/api/queries.ts` / `types.ts` | — | `useOpenClarification` / `ClarificationRead` / 两个缓存字段 |
| `frontend/app/src/styles/app.css` | 2 100 | 环 / 浮层 / 设置 / 字号五档 / 动态点 / 时间格（零字面量） |
| `frontend/app/theme/dsh.css`（生成物） | 679 | 新增 `--dsh-reading-size-*` / `--dsh-ring-*` / `--dsh-settings-*` / `--dsh-scrim` / `--dsh-pulse-animation` / `@keyframes dsh-pulse` |
| `var_ref/check_v3.py` / `patch_*.py` / `build_tokens.py` | — | DOM 对账、六处补丁脚本、令牌生成器 |

**未做（写清代价，不写"以后会做"）**：英文界面（要一份完整翻译表）；
设置里的「模型 / 工具 / 数据」三栏（左栏里如实写着"还没有做"）；
权限通知（这个产品没有这类弹窗）；`/api/llm/profiles` 的写入端点
（所以"换模型"只能在已有档案之间换，不能在界面上新增一份）。

---
"""


def main() -> None:
    m9 = M9.read_text(encoding="utf-8")
    marker = "---\n\n*文档版本：v0.1"
    assert m9.count(marker) == 1, f"页脚锚点出现 {m9.count(marker)} 次"
    assert "### 6.11" not in m9, "§6.11 已经写过了"
    m9 = m9.replace(marker, SECTION + "\n*文档版本：v0.1", 1)

    # 页脚：把第三块补进"回写状态"那一句
    old_foot = """§6.10 为 P16 第二块（**侧栏能真的整理**：搜索 / 分组 × 排序 / 手动挪位 / 每行菜单 /
资料卡片；「归档」被刻意做成时间戳而不是第 6 个状态；并记下两个真缺陷 ——
自动选中在两个查询之间的竞态、旧 dev 库缺列导致 500）。*"""
    new_foot = """§6.10 为 P16 第二块（**侧栏能真的整理**：搜索 / 分组 × 排序 / 手动挪位 / 每行菜单 /
资料卡片；「归档」被刻意做成时间戳而不是第 6 个状态；并记下两个真缺陷 ——
自动选中在两个查询之间的竞态、旧 dev 库缺列导致 500），
§6.11 为 P16 第三块（**生成中的样子 / 发送键左边那一组 / 设置页**：文件夹换开合图标、
侧栏动态点与上次更新时间、上下文圆环与五个 token 读数（`null` 一律「没测到」）、
模型自绘菜单、照 DSH 复刻的「设置 → 通用」（七行 × `ready`/`partial`/`absent`，
不摆假开关）；并记下两个真缺陷 —— 缓存读数接到了侧边栏那张表上、覆盖层底色在
两个主题下反了）。*"""
    assert m9.count(old_foot) == 1, "页脚段落没对上"
    m9 = m9.replace(old_foot, new_foot, 1)
    M9.write_text(m9, encoding="utf-8")
    print(f"✅ _M9：写入 §6.11（现 {len(m9.splitlines())} 行）")

    # ------------------------------------------------------------ 全景文档 ==
    overview = OVERVIEW.read_text(encoding="utf-8")

    def one(old: str, new: str, *, label: str) -> None:
        nonlocal overview
        assert overview.count(old) == 1, f"{label}：出现 {overview.count(old)} 次"
        overview = overview.replace(old, new, 1)
        print(f"✅ 全景：{label}")

    one(
        "测试 **1425 条**（后端）+ **103 条**（主题套件）",
        "测试 **1429 条**（后端）+ **103 条**（主题套件）",
        label="顶部规模（测试条数）",
    )
    one(
        "**1425 条测试全绿**",
        "**1429 条测试全绿**",
        label="规模段落（测试条数）",
    )
    one(
        "`src/` 4 246 行 TS/TSX + 1 744 行零字面量 CSS，类型检查与生产构建均通过；主页面已按 DSH 一比一复刻，见 `_M9` §6.9–§6.10）",
        "`src/` 4 990 行 TS/TSX + 2 100 行零字面量 CSS，类型检查与生产构建均通过；主页面已按 DSH 一比一复刻，见 `_M9` §6.9–§6.11）",
        label="前端规模",
    )
    one(
        "测试 1402 → **1425 条**；路径 74 → **76**；`e2echeck` 32 步（**可用 31 / 缺口 1 / 失败 0**）。**未做**：全局侧界面、深色切换开关、markdown 渲染、拖放上传",
        "测试 1402 → **1425 条**；路径 74 → **76**；`e2echeck` 32 步（**可用 31 / 缺口 1 / 失败 0**）。**未做**：全局侧界面、深色切换开关、markdown 渲染、拖放上传",
        label="P16 行（保持原文，第三块另起一行）",
    )

    # P16 那一段后面补上第三块的成果（写在同一个单元格里，保持"一行一块"的结构）
    p16_old = "**未做**：全局侧界面、深色切换开关、markdown 渲染、拖放上传 || **P6+.6**"
    p16_new = (
        "**第三块**（`_M9` §6.11，用户同样逐条点名）：文件夹开合改用**张开的夹子**图标；"
        "回复中时侧栏那一行左侧有**动态呼吸点**、悬停提示写清状态/是否生成中/上次更新/所属文件夹、"
        "右侧显示**上次更新时间**（相对时间，平时 `width:0` 不占位）；**上下文圆环 + 模型键 + 用量键"
        "整体挪到发送键左侧**（环只写百分比，完整数字在悬停与展开里；模型从 `<select>` 换成自绘菜单，"
        "因为 `<select>` 显示不出「当前」是事实）；**五个 token 读数**（总量/缓存命中/未命中输入/命中输入/输出）"
        "——为此给 `chat_message` 加了两列，`null` 一律显示「没测到」而不是 0%；**照 DSH 复刻的「设置 → 通用」**"
        "（七行 × `ready`/`partial`/`absent`，`absent` 的行不摆假开关：英文界面、权限通知、"
        "设置的「模型/工具/数据」三栏）。★ 两处「必须动结构」的地方：`streamingSessionId` 上提到 `App`"
        "（侧栏原本看不见右侧的 `sending`）；`#settings` 深链（否则无头浏览器看不见这个面板）。"
        "测试 1425 → **1429 条**；`e2echeck` 32 → **33 步**（新增 `step_usage_readings`，全局 **32 / 1 / 0**）。"
        "**未做**：英文界面（要一份完整翻译表）、`/api/llm/profiles` 的写入端点（只能换已有档案，"
        "不能在界面上新增）、全局侧界面、markdown 渲染、拖放上传 || **P6+.6**"
    )
    assert overview.count(p16_old) == 1, f"P16 行锚点出现 {overview.count(p16_old)} 次"
    overview = overview.replace(p16_old, p16_new, 1)
    print("✅ 全景：P16 行补上第三块")

    OVERVIEW.write_text(overview, encoding="utf-8")
    print("完成")


if __name__ == "__main__":
    main()
