/**
 * 浏览器边界：**套件的浏览器入口不许（直接或间接）碰到 `node:` 模块。**
 *
 * 这条测试来自一次真实的失败。`index.mjs` 的注释早就写着
 * 「只导出不碰文件系统的东西」，而实际上依赖链是
 *
 *     index.mjs → theme.mjs → compile.mjs → schema.mjs → node:fs
 *
 * 开发服务器不报错（`node:fs` 在浏览器里被换成一个空模块，
 * 只要不调用 `loadSchema()` 就没人碰它）—— 于是这个错误一路藏到
 * **第一次生产构建**，报出来的是
 * `"join" is not exported by "__vite-browser-external"`，
 * 一句和"套件能不能在浏览器跑"看起来毫无关系的话。
 *
 * 所以这里不再靠注释：顺着 import 图走一遍，谁碰了 `node:` 就红。
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const KIT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** 浏览器会 import 的入口（应用侧 + 包导出面）。 */
const BROWSER_ENTRIES = [
  "src/index.mjs",
  "src/theme.mjs",
  "src/compile.mjs",
  "src/tokens.mjs",
  "src/packs.mjs",
];

/** 只认静态与动态 `import`；`export ... from` 也走同一条边。 */
const IMPORT_RE = /(?:from|import)\s*\(?\s*"([^"]+)"/g;

function importsOf(file) {
  const source = readFileSync(file, "utf8");
  const found = [];
  for (const match of source.matchAll(IMPORT_RE)) {
    found.push(match[1]);
  }
  return found;
}

/** 从入口出发收集整张图（相对路径的边才继续走）。 */
function graph(entries) {
  const seen = new Map(); // 绝对路径 → 它的依赖
  const queue = entries.map((entry) => join(KIT_ROOT, entry));
  while (queue.length > 0) {
    const file = queue.pop();
    if (seen.has(file)) continue;
    const deps = importsOf(file);
    seen.set(file, deps);
    for (const dep of deps) {
      if (dep.startsWith(".")) {
        queue.push(join(dirname(file), dep));
      }
    }
  }
  return seen;
}

test("浏览器入口的依赖图里没有 node: 模块", () => {
  const files = graph(BROWSER_ENTRIES);
  assert.ok(files.size >= BROWSER_ENTRIES.length, "依赖图没走起来（一个文件都没读到）");

  const offenders = [];
  for (const [file, deps] of files) {
    for (const dep of deps) {
      if (dep.startsWith("node:")) {
        offenders.push(`${relative(KIT_ROOT, file)} → ${dep}`);
      }
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `浏览器入口能走到 node: 模块：\n  ${offenders.join("\n  ")}\n` +
      `打包时会报一句与"套件能不能在浏览器跑"看不出关系的话。\n` +
      `把纯事实放进 tokens.mjs，把读磁盘留在 schema.mjs。`,
  );
});

test("浏览器入口不会走到校验器", () => {
  // 校验器要读磁盘、要读目录，它是**工具**，不是运行时。
  // 应用侧 import 到它，等于把"校验"这件事带进用户的浏览器。
  const files = graph(BROWSER_ENTRIES);
  const names = [...files.keys()].map((file) => relative(KIT_ROOT, file).replace(/\\/g, "/"));
  assert.ok(!names.includes("src/validate.mjs"), `依赖图里出现了校验器：${names.join(", ")}`);
  assert.ok(!names.includes("src/schema.mjs"), `依赖图里出现了 schema 读取器：${names.join(", ")}`);
});

test("这条检查本身能发现违规（否则它只是个装饰）", () => {
  // 用一份**故意违规**的图来验证上面那段判断逻辑真的会红。
  const fake = new Map([
    [join(KIT_ROOT, "src", "index.mjs"), ["./compile.mjs"]],
    [join(KIT_ROOT, "src", "compile.mjs"), ["node:fs"]],
  ]);
  const offenders = [];
  for (const [file, deps] of fake) {
    for (const dep of deps) if (dep.startsWith("node:")) offenders.push(`${file} → ${dep}`);
  }
  assert.equal(offenders.length, 1, "违规检测逻辑本身失效了");
});

test("套件里仍然有人负责读 schema（别把边界修成功能缺失）", () => {
  // 边界修过头的样子：谁都不 import schema.mjs 了，于是"读不到契约必须报错"
  // 这条规矩连同它的实现一起消失。所以这里正面钉住它还在。
  const validator = readFileSync(join(KIT_ROOT, "src", "validate.mjs"), "utf8");
  assert.match(validator, /from "\.\/schema\.mjs"/, "校验器不再从 schema.mjs 读契约了");
});
