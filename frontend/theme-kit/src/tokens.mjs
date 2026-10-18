/**
 * 纯事实层：层名、词表读取、长度上限、id 形态。
 *
 * **这个模块不 import 任何 `node:` 模块，所以浏览器里也能用。**
 * 它与 `schema.mjs` 的分界在这里：
 *
 * - `tokens.mjs`：从**一个已经拿到的 schema 对象**里读事实（纯函数）；
 * - `schema.mjs`：**去磁盘上把 schema 读出来**（`node:fs`），只有命令行与测试会 import。
 *
 * 为什么一定要分开（这不是洁癖）：`compile.mjs` 需要 `LAYERS` 这个常量，
 * 而它早先从 `schema.mjs` 里拿 —— 于是浏览器侧的依赖链变成
 * `index.mjs → theme.mjs → compile.mjs → schema.mjs → node:fs`，
 * **整个套件在打包时当场失败**（Rollup：`"join" is not exported by
 * "__vite-browser-external"`）。开发服务器不会报错（它只在函数被调用时才失败），
 * 所以这个错误会一直藏到第一次生产构建。
 *
 * 教训与项目里那条判据同源：`index.mjs` 的注释早就写着"只导出不碰文件系统的东西"，
 * 而**没有东西在强制它** —— 一句真话听起来像保证，但它不是机制。
 * 现在有了：`test/browser-boundary.test.mjs` 会顺着 import 图走一遍。
 */

/** 三层令牌。顺序就是编译后的书写顺序，也是文档里的顺序。 */
export const LAYERS = Object.freeze(["primitive", "semantic", "component"]);

/** 自定义 css 片段的长度上限（字符）。一个主题包不该长成一整个应用。 */
export const CSS_FRAGMENT_LIMIT = 20000;

/** 主题包 id 的合法形态（与 schema 里的 pattern 一致；两处不一致时测试会红）。 */
export const THEME_ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

/** 从 schema 里取出三层各自的令牌词表：`{ primitive: Set, semantic: Set, component: Set }`。 */
export function tokenVocabulary(schema) {
  const out = {};
  for (const layer of LAYERS) {
    const def = schema.$defs?.[`${layer}Layer`];
    const names = def?.propertyNames?.enum;
    if (!Array.isArray(names) || names.length === 0) {
      throw new Error(
        `schema 里第「${layer}」层的词表读不出来（$defs.${layer}Layer.propertyNames.enum）。` +
          `一个空的词表会让这一层什么都通过，所以这里直接报错。`,
      );
    }
    out[layer] = new Set(names);
  }
  return out;
}

/** 全部令牌名（三层合并），用于检查 `var(--x)` 引用是否指向真实存在的令牌。 */
export function allTokenNames(schema) {
  const names = new Set();
  for (const set of Object.values(tokenVocabulary(schema))) {
    for (const name of set) names.add(name);
  }
  return names;
}

/**
 * 必填语义令牌（14 个）。**出处是 schema，不是这里。**
 *
 * 语义：一个主题包若不写 `extends`，就必须自己回答这 14 个问题。
 */
export function requiredSemanticTokens(schema) {
  const list = schema.$defs?.requiredSemanticTokens?.const;
  if (!Array.isArray(list) || list.length === 0) {
    throw new Error(
      "schema 里读不到必填令牌清单（$defs.requiredSemanticTokens.const）—— " +
        "读不到就等于「没有必填项」，那样任何空壳都能通过校验。",
    );
  }
  return list;
}
