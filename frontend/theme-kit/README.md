# 主题套件（theme-kit）

**换皮肤不改组件** —— 这个套件把界面上每一个"值"收进三层令牌，
再由一个可校验的主题包提供。组件只写 `var(--…)`，一行都不认识具体颜色。

零依赖、不绑框架：它只认 `document` 与 `localStorage`。

## 文件

| 路径 | 是什么 |
|---|---|
| `THEME_SPEC.md` | **说明书**：三层令牌、全部令牌清单、主题包格式、自定义片段的边界、错误编号 |
| `PROMPT.md` | **元提示词**：把「说明文档 + 个性化需求」变成主题包 |
| `schema/theme.schema.json` | 契约：令牌词表 + 必填清单 + 主题包结构（**唯一出处**） |
| `themes/base-warm.json` | 默认主题：大块留白、圆角、暖橙黄 |
| `themes/midnight-ink.json` | 示例：深色 + css 片段（格线质感） |
| `src/base.css` | 套件基础样式 + 公开样式钩子（`.tk-*`） |
| `src/compile.mjs` | 主题包 → CSS（纯函数，浏览器可用） |
| `src/theme.mjs` | 运行时：挂到页面、记住选择、按系统偏好挑默认 |
| `src/validate.mjs` | 校验器（命令行，退出码 0/1/2） |
| `src/packs.mjs` | 自带的两个主题包 |
| `tools/check-literals.mjs` | 零字面量检查器 |
| `tools/try-prompt.mjs` | **拿真模型验 `PROMPT.md`**：生成 → 跑校验器（模型说自己跑过不算） |
| `test/` | `node --test`，零依赖 |

## 三件检查（都可以单独跑）

```bash
node src/validate.mjs --all          # 主题包是否合法
node tools/check-literals.mjs src    # 套件自己的 CSS 里有没有写死的值
node --test                          # 契约、编译器、文档一致性
```

## 验 `PROMPT.md` 有没有用

```bash
node tools/try-prompt.mjs \
  --requirement "我想要一个像旧宣纸的暖白主题，圆角小一点，正文大一号" \
  --id rice-paper --model qwen3.5:9b
```

它拼出提示词、真调模型、**再由校验器判**（模型说"我跑过了"不算 ——
第一次真跑时它就是这么编的，而那个主题包有四个问题）。
生成的文件留在 `themes/<id>.json`，但**不会自动采纳**：加进 `THEME_PACKS` 是人的动作。

## 两条被测试钉住的规矩

1. **值只有一个出口**：套件与组件的 CSS 里不许出现颜色/长度字面量，
   且每个 `var(--x)` 都必须在契约里存在。前者由 `check-literals` 扫源码，
   后者由 `test/validate.test.mjs` 对着 schema 校验。
2. **文档不能漂**：`THEME_SPEC.md` 的令牌表与 schema 的词表逐一对齐，
   少一个、多一个都会让测试红；文档里提到的每个文件路径也必须真实存在。

## 自己写一个主题包

读 `THEME_SPEC.md` 第 3 节，或者把 `PROMPT.md` 交给模型。
最小形态（只改两个值，整个界面主色跟着变）：

```json
{
  "$schema": "../schema/theme.schema.json",
  "id": "sunset",
  "name": "落日橘",
  "appearance": "light",
  "extends": "base-warm",
  "tokens": { "primitive": { "--p-orange-500": "#e2553d" } }
}
```

写完后校验：

```bash
node src/validate.mjs themes/sunset.json
```
