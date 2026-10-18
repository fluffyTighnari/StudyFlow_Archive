/**
 * 零字面量检查器：`node tools/check-literals.mjs <文件或目录>...`
 *
 * ## 这条规矩到底在管什么
 *
 * 「主题可以自定义配色/字体/圆角/背景」这句话要成立，前提是**值只有一个出口**：
 * 组件写 `var(--radius-card)`，而不是写 `12px`。只要有一处写了 `12px`，
 * 换主题时那一处就不会变 —— 而它不会报错，只会在某个主题下表现成
 * "有一块地方没跟着变"。这种缺陷没法靠测试功能发现（功能是好的），
 * 所以只能靠**扫源码**。
 *
 * ## 判据（一句话）
 *
 * 把每个声明里的 `var(...)` **全部挖掉**，剩下的东西必须只是结构性关键字
 * （`none` / `auto` / `solid` / `100%` / `0` / `1fr` / 数字…）。
 * 剩下的还有长度、颜色、字体名 → 违规。
 *
 * 为什么是"挖掉 var 之后看剩下什么"，而不是"逐条正则找 px/#"：
 * 因为前者能自然处理 `border: var(--border-width) solid var(--border-subtle)`
 * 这类混合值，也不会把 `rgba(0,0,0,0.4)` 里的 `0` 误判成字面量长度。
 *
 * ## 只管两件事，其余不管
 *
 * 1. `.css` 文件里的声明；
 * 2. `.tsx` / `.ts` 里出现的颜色字面量与长度字面量（内联样式、模板字符串里都可能）。
 *
 * **不管**的是：DOM 结构、组件逻辑、类名 —— 那些本来就不该被主题影响，
 * 而这个检查器只关心"值从哪来"。
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";

/** 结构性关键字：它们不是"被主题决定的样式值"，而是布局的语法。 */
const STRUCTURAL = new Set([
  "0",
  "100%",
  "50%",
  "auto",
  "none",
  "inherit",
  "initial",
  "unset",
  "revert",
  "revert-layer",
  "transparent",
  "currentcolor",
  "solid",
  "dashed",
  "dotted",
  "hidden",
  "visible",
  "absolute",
  "relative",
  "sticky",
  "fixed",
  "static",
  "flex",
  "grid",
  "block",
  "inline",
  "inline-block",
  "inline-flex",
  "column",
  "row",
  "column-reverse",
  "row-reverse",
  "wrap",
  "nowrap",
  "pre",
  "pre-wrap",
  "break-word",
  "break-all",
  "ellipsis",
  "center",
  "flex-start",
  "flex-end",
  "space-between",
  "space-around",
  "space-evenly",
  "stretch",
  "baseline",
  "start",
  "end",
  "left",
  "right",
  "top",
  "bottom",
  "middle",
  "border-box",
  "content-box",
  "pointer",
  "default",
  "not-allowed",
  "grab",
  "text",
  "ease",
  "linear",
  "ease-in",
  "ease-out",
  "ease-in-out",
  "infinite",
  "forwards",
  "backwards",
  "both",
  "normal",
  "bold",
  "italic",
  "uppercase",
  "lowercase",
  "capitalize",
  "line-through",
  "underline",
  "overline",
  "vertical",
  "horizontal",
  "isolate",
  "antialiased",
  "optimizelegibility",
  "nowrap",
  "pointer-events",
  "clone",
  "revert",
  "scroll",
  "smooth",
  "contain",
  "cover",
  "contain",

  // 布局语法：这些是"怎么写"，不是"多大"。
  // 一个 `grid-template-columns: var(--layout-sidebar) 1fr` 里唯一的设计决定
  // 就是那个令牌；`1fr` 与 `minmax()` 是语法。全视口高度（`100dvh`）同理 ——
  // 它不是设计决定，而是"占满一屏"这个结构性事实。
  "calc",
  "minmax",
  "fit-content",
  "max-content",
  "min-content",
  "100%",
  "100dvh",
  "100vh",
  "*",
  "/",
  "+",
  "-",
  ">",
  "<",

  // `transition` / `animation` 的简写里会写出「过渡哪个属性」。
  // 那些词是属性名，不是样式值 —— 把它们当字面量会逼出一堆假违规，
  // 而假违规的代价是**这个检查器最后没人跑**。
  "all",
  "background",
  "background-color",
  "background-position",
  "border-color",
  "box-shadow",
  "color",
  "fill",
  "filter",
  "gap",
  "height",
  "opacity",
  "outline-color",
  "padding",
  "margin",
  "stroke",
  "transform",
  "width",
]);

/**
 * 单位：出现即说明这里藏了一个具体尺寸。
 *
 * 前面那一组字符是"合法的前置字符"：**必须带上引号** ——
 * 内联样式里最常见的形式恰恰是 `'12px'`，而漏掉引号会让这一整类
 * （React 的 `style={{ padding: '12px' }}`）悄悄通过。
 * 一个报不出真实违规的检查器，比没有检查器更坏。
 */
const LENGTH_UNIT =
  /(?:^|[\s(,:='"])(-?\d*\.?\d+(?:px|rem|em|pt|pc|in|cm|mm|ex|ch|vh|vw|vmin|vmax|q))\b/gi;

/** 颜色：十六进制、函数式、以及几个最常见的具名色。 */
const COLOR_LITERAL =
  /#(?:[0-9a-f]{3,8})\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix|color)\s*\(|\b(?:white|black|red|blue|green|yellow|orange|purple|pink|gray|grey|silver|navy|teal|olive|maroon|lime|aqua|fuchsia)\b/gi;

/**
 * 给 `.test()` 用的非全局版本。
 *
 * **带 `g` 的正则调用 `.test()` 是有状态的**：它会记住 `lastIndex`，
 * 于是同一个值连续测两次会得到相反的结果。这类 bug 的表现是
 * "检查结果时对时错"，比漏报更难查 —— 所以两个用途各用一份正则。
 */
const COLOR_JUST_TEST = new RegExp(COLOR_LITERAL.source, "i");

export function stripVarCalls(value) {
  let out = "";
  let index = 0;
  while (index < value.length) {
    if (value.startsWith("var(", index)) {
      let depth = 0;
      let cursor = index + 3;
      for (; cursor < value.length; cursor += 1) {
        if (value[cursor] === "(") depth += 1;
        else if (value[cursor] === ")") {
          depth -= 1;
          if (depth === 0) break;
        }
      }
      index = cursor + 1;
      continue;
    }
    out += value[index];
    index += 1;
  }
  return out;
}

/** 检查一个 CSS 声明值。返回违规说明，或 null。 */
export function checkDeclarationValue(property, value, { allowLineHeightLiteral = false } = {}) {
  const trimmed = value.trim();
  if (trimmed === "") return null;

  // 这两条属性的值必须是令牌：它们是"主题"最核心的两件事之一（字号与行高），
  // 而它们的值又长得像普通数字，很容易被写死却没人发现。
  if (
    !allowLineHeightLiteral &&
    /^(font-size|line-height)$/i.test(property) &&
    !trimmed.includes("var(")
  ) {
    return `${property} 必须走令牌（现在写的是 ${trimmed}）—— 字号与行高是主题最核心的两件事之一`;
  }

  const rest = stripVarCalls(trimmed);
  const words = rest
    .split(/[\s,()]+/)
    .map((w) => w.trim().toLowerCase())
    .filter(Boolean);

  const leftovers = [];
  for (const word of words) {
    if (STRUCTURAL.has(word)) continue;
    if (/^\d+(\.\d+)?fr$/.test(word)) continue; // grid 轨道
    if (/^\d+$/.test(word)) continue; // 结构性数字：z-index、层级、倍数
    if (/^[\d.]+s|[\d.]+ms$/.test(word) && !/\d(px|rem|em)/.test(word)) {
      // 时间字面量：只在动画语法里当结构性参数用（duration 必须走令牌）
      if (!/^(animation|transition)/i.test(property)) continue;
    }
    leftovers.push(word);
  }

  // **一条声明里的问题要全部报出来**，不能报一个就返回：
  // 只报长度的话，作者改掉长度之后会以为已经干净了，而颜色还在。
  const problems = [];
  const lengths = [...rest.matchAll(LENGTH_UNIT)].map((m) => m[1]);
  if (lengths.length > 0) {
    problems.push(
      `值里有长度字面量：${[...new Set(lengths)].join(" / ")}` +
        `（长度应当来自 var(--space-*) / var(--radius-*) / var(--border-width)）`,
    );
  }
  if (COLOR_JUST_TEST.test(rest)) {
    problems.push(`值里有颜色字面量：${rest.trim()}（颜色应当来自 var(--…)）`);
  }
  if (leftovers.length > 0) {
    problems.push(`值里有非结构性内容：${leftovers.join(" ")}（只剩 ${rest.trim()}）`);
  }
  return problems.length > 0 ? problems.join("；") : null;
}

/** 扫一个 CSS 文件，返回 `[{ line, column, message }]`。 */
export function checkCssFile(path) {
  const text = readFileSync(path, "utf8");
  const violations = [];
  // 先去掉注释，免得注释里举例的 `#fff` 被当成违规（注释不是样式值）。
  const withoutComments = text.replace(/\/\*[\s\S]*?\*\//g, (block) =>
    block.replace(/[^\n]/g, " "),
  );

  const declaration = /([-\w]+)\s*:\s*([^;{}]+)[;}]/g;
  let match;
  while ((match = declaration.exec(withoutComments)) !== null) {
    const [, property, value] = match;
    const problem = checkDeclarationValue(property, value);
    if (problem) {
      const before = withoutComments.slice(0, match.index);
      violations.push({
        line: before.split("\n").length,
        message: `${property}: ${problem}`,
      });
    }
  }
  return violations;
}

/**
 * 扫一个 `.tsx` / `.ts` 文件：只找"值"层面的字面量，不管逻辑。
 *
 * **每一行的每一处都要报**，不能一行只报一个：一行里同时有颜色和长度是
 * 很常见的（`style={{ color: '#fff', padding: '12px' }}`），
 * 只报第一个会让第二次检查"看起来通过了"。
 */
export function checkTsxFile(path) {
  const text = readFileSync(path, "utf8");
  const violations = [];
  const lines = text.split("\n");
  lines.forEach((line, index) => {
    if (line.trimStart().startsWith("*") || line.trimStart().startsWith("//")) return;
    for (const match of line.matchAll(COLOR_LITERAL)) {
      violations.push({
        line: index + 1,
        message: `代码里出现颜色字面量 \`${match[0]}\`（颜色应当来自 var(--…)）`,
      });
    }
    for (const match of line.matchAll(LENGTH_UNIT)) {
      violations.push({
        line: index + 1,
        message: `代码里出现长度字面量 \`${match[1]}\`（长度应当来自 var(--…)）`,
      });
    }
  });
  return violations;
}

function* walk(target) {
  const info = statSync(target);
  if (info.isFile()) {
    yield target;
    return;
  }
  for (const entry of readdirSync(target).sort()) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    yield* walk(join(target, entry));
  }
}

export function checkPaths(paths) {
  const results = [];
  for (const path of paths) {
    for (const file of walk(path)) {
      const ext = extname(file);
      let violations = [];
      if (ext === ".css") violations = checkCssFile(file);
      else if (ext === ".tsx" || ext === ".ts") violations = checkTsxFile(file);
      else continue;
      if (violations.length > 0) results.push({ file, violations });
    }
  }
  return results;
}

function main(argv) {
  if (argv.length === 0) {
    process.stderr.write("用法：node tools/check-literals.mjs <文件或目录>...\n");
    return 2;
  }
  let results;
  try {
    results = checkPaths(argv);
  } catch (cause) {
    process.stderr.write(`[检查器没能跑起来] ${cause.message}\n`);
    return 2;
  }
  const root = process.cwd();
  let count = 0;
  for (const { file, violations } of results) {
    for (const v of violations) {
      count += 1;
      process.stdout.write(`${relative(root, file)}:${v.line}  ${v.message}\n`);
    }
  }
  if (count === 0) {
    process.stdout.write("零字面量检查通过：所有样式值都来自令牌。\n");
    return 0;
  }
  process.stdout.write(
    `\n共 ${count} 处字面量。它们的危险不在于「写死了一个值」，而在于` +
      `「换主题时它不会变，而且不会报错」。\n`,
  );
  return 1;
}

if (process.argv[1] && process.argv[1].endsWith("check-literals.mjs")) {
  process.exitCode = main(process.argv.slice(2));
}
