/**
 * 浏览器运行时：把主题包挂到页面上，并记住用户的选择。
 *
 * **这一层故意没有框架**：它只认 `document` 与 `localStorage`。
 * 换主题的唯一动作是往 `<head>` 里放一个 `<style>` 元素 ——
 * 不动 DOM 结构、不重建组件树、不触发任何 React 状态。
 * 这就是"主题可以随便换、组件一行不用改"在实现上的全部含义。
 *
 * ## 为什么是一个 `<style>` 元素而不是逐条 setProperty
 *
 * 逐条 `document.documentElement.style.setProperty("--bg-app", ...)` 也能用，
 * 但有两个坏处：
 *
 * 1. 自定义片段（`pack.css`）**没有地方可去** —— `setProperty` 只能设变量，
 *    设不了"图案、滤镜、动画"。而 B 方案的存在理由正是它们。
 * 2. 主题切换会留下一堆内联样式在 `<html>` 上，而内联样式的优先级高于任何
 *    样式表 —— 于是"回到默认主题"会变成一件需要逐条清理的事，
 *    而清理漏掉一条就会表现成"换了主题但有一处没变"。
 *
 * 一个 `<style>` 元素没有这些问题：换主题 = 换掉它的文本内容。
 * 它永远是完整的、也永远只有一份。
 */

import { compileTheme } from "./compile.mjs";

/** @typedef {import("./compile.mjs").ThemePack} ThemePack */

/** 记住用户选择的键。带前缀，免得和宿主应用的其它键撞上。 */
export const THEME_STORAGE_KEY = "mouxue.theme";

/** 注入的 `<style>` 元素的 id（也用来查出"现在装的是哪个主题"）。 */
export const THEME_STYLE_ID = "tk-theme";

/**
 * 选择初始主题。
 *
 * 三条口径，顺序不能调：
 * 1. 存过的选择（用户上次挑的）；
 * 2. 系统偏好（`prefers-color-scheme: dark` → 取第一个 `appearance: "dark"` 的主题）；
 * 3. 基础主题。
 *
 * 第 1 条优先于第 2 条是刻意的：**用户挑过深色之后，不该因为系统变亮而变回去。**
 * 那会让人以为"我设的主题丢了"。
 *
 * @param {{packs: ThemePack[], baseId: string, storedId?: string|null, prefersDark?: boolean}} options
 * @returns {ThemePack}
 */
export function chooseTheme({ packs, baseId, storedId = null, prefersDark = false }) {
  const byId = new Map(packs.map((pack) => [pack.id, pack]));
  if (storedId && byId.has(storedId)) return byId.get(storedId);
  if (prefersDark) {
    const dark = packs.find((pack) => pack.appearance === "dark");
    if (dark) return dark;
  }
  return byId.get(baseId) ?? packs[0];
}

/** 读出存过的主题 id（读不到就返回 null —— 不猜、不抛）。
 *
 * @param {Storage} [storage]
 * @returns {string|null}
 */
export function readStoredThemeId(storage = globalThis.localStorage) {
  try {
    return storage?.getItem(THEME_STORAGE_KEY) ?? null;
  } catch {
    // 隐私模式下 localStorage 的读取本身就会抛。这时"没有存过"是正确答案。
    return null;
  }
}

/** 记住用户的选择（写不进去也不算失败 —— 只是下次要重新挑一遍）。
 *
 * @param {string} id
 * @param {Storage} [storage]
 * @returns {boolean} 是否写成功
 */
export function storeThemeId(id, storage = globalThis.localStorage) {
  try {
    storage?.setItem(THEME_STORAGE_KEY, id);
    return true;
  } catch {
    return false;
  }
}

/**
 * 把主题装到页面上。
 *
 * @param {ThemePack} pack 要装的主题包
 * @param {{base?: ThemePack|null, doc?: Document}} [options]
 * @returns {string} 这次实际装上的主题 id ——
 *   返回值是必要的：调用方（主题切换器）需要知道"我要求的"与"实际生效的"
 *   是不是同一个，否则用户点了一个坏掉的主题包时，界面会显示成"换成功了"。
 */
export function applyTheme(pack, { base = null, doc = globalThis.document } = {}) {
  if (!doc) throw new Error("applyTheme 需要 document（这个函数只在浏览器里跑）");
  let element = doc.getElementById(THEME_STYLE_ID);
  if (!element) {
    element = doc.createElement("style");
    element.id = THEME_STYLE_ID;
    // 放在 head 末尾。**不能**放进 body：片段里可能有 `body { ... }` 这类规则，
    // 而 `<style>` 在 body 里会让"规则作用在哪个文档"变得不像它看起来那样。
    doc.head.appendChild(element);
  }
  element.textContent = compileTheme({ base, pack });
  // 标记也写上：`currentThemeId` 读的是它。**不能用内存变量代替** ——
  // 页面刷新后内存变量是空的，而 DOM 里的标记还在。
  element.dataset.themeId = pack?.id ?? "";
  return pack?.id ?? "";
}

/**
 * 现在装的是哪个主题（读的是 DOM 上的标记，不是内存变量 —— 页面刷新后仍然对）。
 *
 * 返回 `null` 表示"还没装过"。这个区别有意义：没装过说明该走
 * `chooseTheme` 的完整挑选流程，而不是"装的是一个叫 null 的主题"。
 */
export function currentThemeId(doc = globalThis.document) {
  const element = doc?.getElementById(THEME_STYLE_ID);
  return element?.dataset?.themeId || null;
}
