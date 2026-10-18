/**
 * 套件自带的两个主题包。应用可以直接用，也可以把它们当成写自己主题包的例子。
 *
 * `import ... with { type: "json" }` 是**必须的**：Node 24 对 JSON 模块
 * 强制要求 import 属性（少了会直接 ERR_IMPORT_ATTRIBUTE_MISSING）。
 * 如果某个打包器版本不认这个语法，就在应用里自己 `import` 那两个 JSON
 * —— 套件的核心（compile / theme）不认识任何具体主题包，这是刻意的：
 * 一个"必须知道有哪些主题"的编译器，没法被别人拿去做自己的主题。
 */

import baseWarm from "../themes/base-warm.json" with { type: "json" };
import midnightInk from "../themes/midnight-ink.json" with { type: "json" };

/** 基础主题的 id。其它主题默认在它之上做覆盖。 */
export const BASE_THEME_ID = "base-warm";

/** 全部主题包，顺序即界面上切换器的顺序（默认主题在最前）。 */
export const THEME_PACKS = [baseWarm, midnightInk];

/** 基础主题本身（写主题包时的"经典参照"）。 */
export const baseTheme = baseWarm;
