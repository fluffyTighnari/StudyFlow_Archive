/**
 * 套件的浏览器入口。**只导出不碰文件系统的东西** ——
 * 校验器与 schema 读取器要读磁盘，所以它们在 `src/validate.mjs` /
 * `src/schema.mjs` 里，只有命令行与测试会 import 它们。
 *
 * 这条分界不是洁癖：主题包的值最终要在浏览器里生效，如果入口顺带
 * import 了 `node:fs`，打包工具会当场报错（或者更糟：悄悄塞一个空实现，
 * 然后校验在你以为跑过了的时候其实没跑）。
 */

export {
  compileTheme,
  contrastRatio,
  declarations,
  mergeTokens,
  parseColor,
  resolveColor,
  resolveValue,
} from "./compile.mjs";

export {
  THEME_STORAGE_KEY,
  THEME_STYLE_ID,
  applyTheme,
  chooseTheme,
  currentThemeId,
  readStoredThemeId,
  storeThemeId,
} from "./theme.mjs";
