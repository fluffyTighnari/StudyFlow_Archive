/**
 * 零字面量检查器自己的测试。
 *
 * 这个检查器的价值全部押在一件事上：**它必须真的能报出违规。**
 * 一个永远输出"通过"的检查器，比没有检查器更坏 —— 它会让"我们检查过了"
 * 变成一句假话，而这个项目已经吃过九次"系统用一个假的理由解释自己"的苦。
 *
 * 所以这里用**故意的反例文件**把每条判据都试一遍。
 */

import { strict as assert } from "node:assert";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { KIT_ROOT } from "../src/schema.mjs";
import {
  checkCssFile,
  checkDeclarationValue,
  checkPaths,
  checkTsxFile,
} from "../tools/check-literals.mjs";

function withTempFile(name, content) {
  const dir = mkdtempSync(join(tmpdir(), "tk-lit-"));
  const path = join(dir, name);
  writeFileSync(path, content, "utf8");
  return path;
}

// --------------------------------------------------------------------------- //
// 真实的套件必须干净
// --------------------------------------------------------------------------- //

test("套件自己的样式里没有字面量", () => {
  const results = checkPaths([join(KIT_ROOT, "src")]);
  const lines = results.flatMap((r) =>
    r.violations.map((v) => `${r.file}:${v.line} ${v.message}`),
  );
  assert.deepEqual(
    lines,
    [],
    "套件自己写了字面量 —— 那样它就没资格要求组件别写",
  );
});

// --------------------------------------------------------------------------- //
// 反例：每条判据都必须真的能报出来
// --------------------------------------------------------------------------- //

test("颜色字面量被报出来", () => {
  const path = withTempFile("a.css", ".x { color: #fff; background: rgb(0, 0, 0); }");
  assert.equal(checkCssFile(path).length, 2);
});

test("长度字面量被报出来", () => {
  const path = withTempFile("b.css", ".x { border-radius: 12px; padding: 0.5rem; }");
  assert.equal(checkCssFile(path).length, 2);
});

test("字号与行高即使写成数字也被报出来", () => {
  const path = withTempFile("c.css", ".x { font-size: 15px; line-height: 1.5; }");
  assert.equal(checkCssFile(path).length, 2);
  assert.match(checkCssFile(path)[1].message, /必须走令牌/);
});

test("内联样式里的颜色与长度也会被报出来", () => {
  const path = withTempFile(
    "d.tsx",
    "export const A = () => <div style={{ color: '#fff', padding: '12px' }} />;\n",
  );
  const violations = checkTsxFile(path);
  assert.equal(violations.length, 2);
});

test("注释里举例的颜色不算违规", () => {
  const path = withTempFile("e.css", "/* 例如 #fff 或 12px 都不行 */\n.x { color: var(--fg-primary); }");
  assert.deepEqual(checkCssFile(path), []);
});

// --------------------------------------------------------------------------- //
// 不能误报：正常的写法必须放行
// --------------------------------------------------------------------------- //

test("混合了关键字与令牌的值是正常的", () => {
  assert.equal(
    checkDeclarationValue("border", " var(--border-width) solid var(--border-subtle)"),
    null,
  );
  assert.equal(
    checkDeclarationValue("transition", " background var(--duration-base) var(--ease-out)"),
    null,
  );
  assert.equal(checkDeclarationValue("margin", " 0"), null);
  assert.equal(checkDeclarationValue("width", " 100%"), null);
  assert.equal(checkDeclarationValue("z-index", " 1"), null);
  assert.equal(checkDeclarationValue("grid-template-columns", " 1fr 2fr"), null);
  assert.equal(checkDeclarationValue("color", " currentColor"), null);
  assert.equal(checkDeclarationValue("position", " absolute"), null);
});

test("var() 里面的字面量不算违规（那是令牌自己的值，不是组件的值）", () => {
  assert.equal(checkDeclarationValue("color", " var(--fg-primary, #fff)"), null);
});

test("真正的违规判得出来（正对照）", () => {
  assert.match(checkDeclarationValue("color", " #123456"), /颜色字面量/);
  assert.match(checkDeclarationValue("padding", " 8px"), /长度字面量/);
  assert.match(checkDeclarationValue("box-shadow", " 0 1px 2px rgba(0,0,0,.2)"), /颜色字面量/);
});

test("扫目录时只认 .css / .ts / .tsx，别的一律不看", () => {
  const dir = mkdtempSync(join(tmpdir(), "tk-lit-dir-"));
  mkdirSync(join(dir, "sub"));
  writeFileSync(join(dir, "a.css"), ".x { color: #fff; }", "utf8");
  writeFileSync(join(dir, "b.md"), "例如 #fff 和 12px", "utf8");
  writeFileSync(join(dir, "sub", "c.tsx"), "const x = '12px';\n", "utf8");
  const results = checkPaths([dir]);
  assert.deepEqual(
    results.map((r) => r.file.split(/[\\/]/).pop()).sort(),
    ["a.css", "c.tsx"],
  );
});
