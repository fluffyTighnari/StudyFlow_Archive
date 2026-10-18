/**
 * 把主题包编译成一段 CSS。**纯函数，不碰文件系统，浏览器里也能跑。**
 *
 * ## 编译这件事在这里只有一件工作
 *
 * 令牌之间可以互相引用（`--accent: var(--p-orange-500)`），所以"合并"不是
 * 把两个对象拼起来就完了 —— 但也不需要真的去解引用：CSS 变量本身就是在
 * 运行时解析的。这里做的是：
 *
 * 1. 按层合并「基础包 → 主题包」，**主题包的每个属性赢**；
 * 2. 按固定顺序（primitive → semantic → component）写成一个 `:root` 块。
 *
 * 为什么要有第 1 步（而不是干脆写两个 `:root` 块、让层叠去决定谁赢）：
 * 因为对比度检查需要**看到最终的令牌集合**。靠层叠的话，检查器得自己
 * 模拟一遍浏览器；那是一个必然会和浏览器不一致的模拟。
 *
 * ## 为什么基础的 14 个必填令牌仍然要写进每个包
 *
 * 不是因为合并需要，而是因为「一个不写 extends 的包」等于声明自己是
 * **一个完整的主题**。这条规矩的价值在于：它让「我只想改个主色」
 * 必须显式写成 `extends` —— 而"完整主题"与"小改一下"是两件不同的事，
 * 混在一起的代价是作者以为自己在改一处，实际把别处的可读性一起改掉了。
 */

// 从 `tokens.mjs` 取，**不能**从 `schema.mjs` 取：那个模块顶层 import 了
// `node:fs`，而 compile.mjs 在浏览器里是要跑的（见 tokens.mjs 开头的说明）。
import { LAYERS } from "./tokens.mjs";

/**
 * 一个主题包的形状。
 *
 * 这份 `@typedef` 不是装饰：宿主应用把它当类型用（`frontend/app` 打开 `allowJs`
 * 直接从这里取类型）。手写一份 `.d.ts` 也能编过，但那是第二份契约 ——
 * 套件改了签名，宿主不会报错，只会在运行时以 `undefined is not a function` 出现。
 * 写在这里，类型就永远跟着实现走。
 *
 * @typedef {object} ThemePack
 * @property {string} [["$schema"]] 指向契约文件（可选）
 * @property {string} id 短名，如 `base-warm`
 * @property {string} name 人看的名字
 * @property {string} [appearance] `light` / `dark`
 * @property {string} [description]
 * @property {string} [extends] 继承哪个主题包的 id
 * @property {{primitive?: Record<string, string>, semantic?: Record<string, string>, component?: Record<string, string>}} tokens 三层令牌
 * @property {string} [css] 受限的 CSS 片段
 */

/**
 * 合并基础包与主题包，得到最终令牌表。
 *
 * @param {ThemePack|null} base 基础包（可以是 null —— 用于只校验不改的场合）
 * @param {ThemePack} pack 主题包
 * @returns {Map<string, string>} 令牌名 → 值（插入顺序即书写顺序）
 */
export function mergeTokens(base, pack) {
  const merged = new Map();
  for (const layer of LAYERS) {
    for (const [name, value] of Object.entries(base?.tokens?.[layer] ?? {})) {
      merged.set(name, value);
    }
    for (const [name, value] of Object.entries(pack?.tokens?.[layer] ?? {})) {
      merged.set(name, value);
    }
  }
  return merged;
}

/** 把令牌表写成 `:root` 块的正文（每行一个令牌）。 */
export function declarations(tokens) {
  const lines = [];
  for (const [name, value] of tokens) {
    lines.push(`  ${name}: ${value};`);
  }
  return lines.join("\n");
}

/**
 * 编译成一个完整的样式表文本。
 *
 * 两个 `@layer` 是**套件提供的**，主题包不要自己写 `@layer`：
 * 层序是全局级联的一部分，让每个主题包各自声明层序，等于让"后加载的那个包"
 * 决定整个应用的层叠规则。校验器会拒绝片段里的 `@layer`，理由就在这里。
 *
 * `@layer` 的顺序声明也一起发出，是为了让套件**单独**就是完整的：
 * 层序由第一次出现决定，重复声明同样的顺序不会再改变它 ——
 * 所以这段写在注入的样式里是幂等的，而少了它，注入的样式在
 * 「应用自己的 CSS 还没加载」的那一瞬间会以未分层的身份生效（优先级不同）。
 *
 * @param {{base?: ThemePack|null, pack: ThemePack}} options
 * @returns {string}
 */
export function compileTheme({ base = null, pack }) {
  const tokens = mergeTokens(base, pack);
  const extra = typeof pack?.css === "string" ? pack.css.trim() : "";
  const parts = [
    `/* 由主题套件编译：${pack?.id ?? "(未命名)"}（基础：${base?.id ?? "无"}）` +
      `*/\n@layer theme, theme-extra, components;\n\n@layer theme {\n:root {\n${declarations(tokens)}\n}\n}`,
  ];
  if (extra) {
    parts.push(`@layer theme-extra {\n${extra}\n}`);
  }
  return `${parts.join("\n\n")}\n`;
}

// --------------------------------------------------------------------------- //
// 对比度：只用来**提醒**，不用来拒绝
// --------------------------------------------------------------------------- //

/** 解析 `#rgb` / `#rrggbb` / `rgb()` / `rgba()`。其他形式返回 null（不猜）。 */
export function parseColor(input) {
  if (typeof input !== "string") return null;
  const text = input.trim();

  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const digits = hex[1];
    const full =
      digits.length === 3
        ? digits
            .split("")
            .map((d) => d + d)
            .join("")
        : digits;
    return [
      parseInt(full.slice(0, 2), 16),
      parseInt(full.slice(2, 4), 16),
      parseInt(full.slice(4, 6), 16),
    ];
  }

  const fn = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(text);
  if (fn) {
    return [Number(fn[1]), Number(fn[2]), Number(fn[3])].map((n) =>
      Math.max(0, Math.min(255, Math.round(n))),
    );
  }

  return null;
}

/** WCAG 相对亮度。 */
function luminance([r, g, b]) {
  const channel = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 对比度（1～21）。任一侧解析不出来时返回 null。 */
export function contrastRatio(foreground, background) {
  const fg = parseColor(foreground);
  const bg = parseColor(background);
  if (!fg || !bg) return null;
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * 把值里的 `var(--x)` 一路替换成它最终的样子。解不动就返回 null。
 *
 * **不猜**：引用了不存在的令牌、括号不配平、成环 —— 这几种情况都如实返回
 * null 并由调用方说明"这一项没法判定"，而不是拿一个默认值去算一个假结果。
 *
 * **这个函数不关心值的类型。** 它只做替换。判断"这是不是一个颜色"是
 * `resolveColor` 的事 —— 两件事混在一起时，`8px` 这种完全正常的圆角值
 * 会被当成"解不动"，而那会让调用方以为主题包写错了。
 */
export function resolveValue(tokens, value, depth = 0) {
  if (typeof value !== "string") return null;
  if (depth > 10) return null; // 成环
  if (!value.includes("var(")) return value;

  let out = "";
  let index = 0;
  while (index < value.length) {
    const start = value.indexOf("var(", index);
    if (start < 0) {
      out += value.slice(index);
      break;
    }
    out += value.slice(index, start);

    let level = 0;
    let cursor = start + 3;
    let end = -1;
    for (; cursor < value.length; cursor += 1) {
      if (value[cursor] === "(") level += 1;
      else if (value[cursor] === ")") {
        level -= 1;
        if (level === 0) {
          end = cursor;
          break;
        }
      }
    }
    if (end < 0) return null; // 括号不配平

    const inner = value.slice(start + 4, end);
    const comma = splitTopLevelComma(inner);
    const name = comma[0].trim();
    const fallback = comma.length > 1 ? comma.slice(1).join(",").trim() : null;

    let replacement = tokens.has(name)
      ? resolveValue(tokens, tokens.get(name), depth + 1)
      : null;
    if (replacement === null && fallback !== null) {
      replacement = resolveValue(tokens, fallback, depth + 1);
    }
    if (replacement === null) return null;
    out += replacement;
    index = end + 1;
  }
  return out.includes("var(") ? resolveValue(tokens, out, depth + 1) : out;
}

/** 在**顶层**（括号深度为 0）的第一个逗号处切开 —— `var(--x, rgb(0, 0, 0))` 里的逗号不算。 */
function splitTopLevelComma(text) {
  let level = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "(") level += 1;
    else if (ch === ")") level -= 1;
    else if (ch === "," && level === 0) return [text.slice(0, i), text.slice(i + 1)];
  }
  return [text];
}

/**
 * 解成一个**颜色**字面量。解不动、或者解出来不是颜色 → null。
 *
 * 对比度检查用它，而不是用 `resolveValue`：对比度只能在两个颜色之间算，
 * 拿"解出来了但不是颜色"去算，会得到一个没有任何意义的数字。
 */
export function resolveColor(tokens, value) {
  const resolved = resolveValue(tokens, value);
  if (resolved === null) return null;
  return parseColor(resolved) ? resolved : null;
}
