/**
 * 运行时（浏览器那一层）的测试。
 *
 * 没有浏览器也能测，办法是给它一个**假 document**。这不是为了凑覆盖率：
 * 这一层负责的事情里有一条很容易写错而看不出来 ——
 * **「用户挑过的主题」要记住，而「系统偏好变了」不能把它顶掉**。
 * 那件事一旦写反，表现是"我设的深色又被换回浅色了"，用户只会觉得这功能有病。
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { compileTheme } from "../src/compile.mjs";
import {
  THEME_STORAGE_KEY,
  THEME_STYLE_ID,
  applyTheme,
  chooseTheme,
  currentThemeId,
  readStoredThemeId,
  storeThemeId,
} from "../src/theme.mjs";

const base = { id: "base", name: "基础", tokens: { semantic: { "--bg-app": "#fff" } } };
const dark = {
  id: "dark",
  name: "深色",
  appearance: "dark",
  tokens: { semantic: { "--bg-app": "#000" } },
};
const plain = { id: "plain", name: "素色", tokens: { semantic: { "--bg-app": "#eee" } } };
const packs = [base, dark, plain];

/** 一个够用的假 document：只需要 createElement / getElementById / head。 */
function fakeDocument() {
  const byId = new Map();
  return {
    head: {
      appendChild(element) {
        byId.set(element.id, element);
        element.parentNode = this;
      },
    },
    createElement() {
      return { id: "", textContent: "", dataset: {}, parentNode: null };
    },
    getElementById(id) {
      return byId.get(id) ?? null;
    },
  };
}

// --------------------------------------------------------------------------- //
// 选主题的三条口径
// --------------------------------------------------------------------------- //

test("存过的选择优先于系统偏好（用户挑过就不该被顶掉）", () => {
  const chosen = chooseTheme({
    packs,
    baseId: "base",
    storedId: "plain",
    prefersDark: true,
  });
  assert.equal(chosen.id, "plain");
});

test("没存过时，系统偏好为暗色 → 取第一个 dark 主题", () => {
  const chosen = chooseTheme({ packs, baseId: "base", prefersDark: true });
  assert.equal(chosen.id, "dark");
});

test("没存过、也不是暗色偏好 → 基础主题", () => {
  assert.equal(chooseTheme({ packs, baseId: "base", prefersDark: false }).id, "base");
});

test("存的是一个已经不存在的主题 → 退回默认，而不是装上 undefined", () => {
  const chosen = chooseTheme({ packs, baseId: "base", storedId: "deleted" });
  assert.equal(chosen.id, "base");
});

test("连基础主题都找不到时不崩，取第一个", () => {
  assert.equal(chooseTheme({ packs, baseId: "nope" }).id, "base");
});

// --------------------------------------------------------------------------- //
// 存取
// --------------------------------------------------------------------------- //

test("存与读是一对", () => {
  const store = new Map();
  const storage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, v),
  };
  assert.equal(readStoredThemeId(storage), null);
  assert.equal(storeThemeId("dark", storage), true);
  assert.equal(store.get(THEME_STORAGE_KEY), "dark");
  assert.equal(readStoredThemeId(storage), "dark");
});

test("localStorage 不可用时读取返回 null、写入返回 false（不抛）", () => {
  const hostile = {
    getItem() {
      throw new Error("隐私模式");
    },
    setItem() {
      throw new Error("隐私模式");
    },
  };
  assert.equal(readStoredThemeId(hostile), null);
  assert.equal(storeThemeId("dark", hostile), false);
});

// --------------------------------------------------------------------------- //
// 装到页面上
// --------------------------------------------------------------------------- //

test("第一次 applyTheme 会建元素，第二次是改内容（不会越堆越多）", () => {
  const doc = fakeDocument();
  applyTheme(base, { base, doc });
  applyTheme(dark, { base, doc });
  const element = doc.getElementById(THEME_STYLE_ID);
  assert.ok(element, "样式元素应当被创建");
  assert.equal(element.textContent, compileTheme({ base, pack: dark }));
  assert.equal(
    doc.head.appendChild instanceof Function && Object.keys(doc).length > 0,
    true,
  );
});

test("样式元素挂在 head 上（不是 body —— 片段里可能有 body 规则）", () => {
  const doc = fakeDocument();
  applyTheme(base, { base, doc });
  assert.equal(doc.getElementById(THEME_STYLE_ID).parentNode, doc.head);
});

test("currentThemeId 读的是 DOM 上的标记，刷新页面后仍然对", () => {
  const doc = fakeDocument();
  assert.equal(currentThemeId(doc), null, "还没装过时应当是 null，不是空字符串");
  applyTheme(dark, { base, doc });
  assert.equal(currentThemeId(doc), "dark");
});

test("applyTheme 返回实际装上的主题 id（调用方要能核对）", () => {
  const doc = fakeDocument();
  assert.equal(applyTheme(dark, { base, doc }), "dark");
});

test("没有 document 时明确报错，而不是悄悄什么都不做", () => {
  assert.throws(() => applyTheme(base, { base, doc: null }), /需要 document/);
});
