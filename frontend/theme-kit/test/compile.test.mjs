/**
 * 编译器的测试。
 *
 * 这里最要紧的一条是**确定性**：同一份输入必须产出逐字节相同的 CSS。
 * 主题包的合并用的是 `Map`，而 `Map` 的顺序取决于插入顺序 —— 只要哪天有人
 * 改成 `Object.entries` 拼来拼去、或者加一层排序，输出就会在不同运行间
 * 悄悄变化，表现为"每次刷新页面样式都闪一下"。这种问题在浏览器里很难查，
 * 但在这里一条断言就够了。
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  compileTheme,
  contrastRatio,
  mergeTokens,
  parseColor,
  resolveValue,
} from "../src/compile.mjs";
import { KIT_ROOT } from "../src/schema.mjs";

const base = JSON.parse(
  readFileSync(join(KIT_ROOT, "themes", "base-warm.json"), "utf8"),
);
const dark = JSON.parse(
  readFileSync(join(KIT_ROOT, "themes", "midnight-ink.json"), "utf8"),
);

test("主题包的每个属性都赢过基础包", () => {
  const merged = mergeTokens(base, {
    id: "x",
    tokens: { semantic: { "--bg-app": "#123456" } },
  });
  assert.equal(merged.get("--bg-app"), "#123456");
});

test("主题包没提的令牌从基础包继承", () => {
  const merged = mergeTokens(base, { id: "x", tokens: { semantic: {} } });
  assert.equal(merged.get("--radius-card"), base.tokens.semantic["--radius-card"]);
});

test("深色主题只改颜料，语义链跟着变（extends 的用法）", () => {
  const merged = mergeTokens(base, dark);
  // 深色包把 --p-orange-500 换成了另一个值
  assert.equal(merged.get("--p-orange-500"), dark.tokens.primitive["--p-orange-500"]);
  // 而 --accent 仍然是基础包那条 var() 引用 —— 它会在运行时解析到新颜料
  assert.equal(merged.get("--accent"), "var(--p-orange-500)");
});

test("编译结果里有层序声明、令牌块，以及片段所在的 theme-extra 层", () => {
  const css = compileTheme({ base, pack: dark });
  assert.match(css, /@layer theme, theme-extra, components;/);
  assert.match(css, /@layer theme \{\n:root \{/);
  assert.match(css, /@layer theme-extra \{/);
  assert.match(css, /\.tk-surface::before/);
});

test("没有片段的主题包不产出 theme-extra 层", () => {
  const css = compileTheme({ base, pack: base });
  assert.doesNotMatch(css, /@layer theme-extra/);
});

test("同一份输入编译两次，结果逐字节相同", () => {
  const once = compileTheme({ base, pack: dark });
  const twice = compileTheme({ base, pack: dark });
  assert.equal(once, twice);
});

test("令牌的书写顺序是 primitive → semantic → component", () => {
  const css = compileTheme({ base, pack: base });
  const index = (name) => css.indexOf(`${name}:`);
  assert.ok(index("--p-white") < index("--bg-app"), "primitive 应当在 semantic 之前");
  assert.ok(
    index("--bg-app") < index("--chat-bubble-user-bg"),
    "semantic 应当在 component 之前",
  );
});

test("令牌的值原样保留（编译不做单位换算 —— 换算是浏览器的事）", () => {
  const css = compileTheme({ base, pack: base });
  assert.match(css, /--space-gutter: var\(--p-space-5\);/);
});

// --------------------------------------------------------------------------- //
// 颜色与对比度
// --------------------------------------------------------------------------- //

test("解析三种颜色写法；不认识的形式返回 null 而不是猜", () => {
  assert.deepEqual(parseColor("#f59e0b"), [245, 158, 11]);
  assert.deepEqual(parseColor("#fff"), [255, 255, 255]);
  assert.deepEqual(parseColor("rgb(1, 2, 3)"), [1, 2, 3]);
  assert.deepEqual(parseColor("rgba(1, 2, 3, 0.5)"), [1, 2, 3]);
  assert.equal(parseColor("var(--x)"), null);
  assert.equal(parseColor("red"), null);
});

test("黑白对比度是 21:1（这个数字错了后面全是错的）", () => {
  assert.equal(Math.round(contrastRatio("#000000", "#ffffff")), 21);
  assert.equal(Math.round(contrastRatio("#ffffff", "#ffffff")), 1);
});

test("对比度在任一侧说不清时返回 null", () => {
  assert.equal(contrastRatio("var(--x)", "#fff"), null);
});

test("var() 链能一路解到字面量", () => {
  const tokens = new Map([
    ["--accent", "var(--p-orange-500)"],
    ["--p-orange-500", "#e8890c"],
  ]);
  assert.equal(resolveValue(tokens, "var(--accent)"), "#e8890c");
});

test("循环引用不会把编译器转死，而是如实返回「解不动」", () => {
  const tokens = new Map([
    ["--a", "var(--b)"],
    ["--b", "var(--a)"],
  ]);
  assert.equal(resolveValue(tokens, "var(--a)"), null);
});

test("值的 fallback 会被用上", () => {
  const tokens = new Map();
  assert.equal(resolveValue(tokens, "var(--nope, #123456)"), "#123456");
});
