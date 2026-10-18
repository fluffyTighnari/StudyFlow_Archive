/**
 * 从磁盘读取 schema —— **这个模块只给命令行与测试用，浏览器不要 import 它。**
 *
 * 纯事实（层名、词表读取、id 形态）在 `tokens.mjs` 里，那一份是浏览器安全的。
 * 这里只剩"把 JSON 读进来"这一件事，以及给它加一句能查的报错。
 *
 * **为什么一切仍然从 schema 里读，而不是在代码里再写一份**
 *
 * 这个套件要防的第一个错误是「主题包里写了一个不存在的令牌名」。
 * 如果词表在 schema 与校验器里各有一份，那么两处不一致时会发生什么？
 * 校验器会放行一个 schema 不认识的令牌，或者反过来拒绝一个合法的 ——
 * 两种都只在界面上表现为「某个地方颜色没变」，而没有报错。
 * 所以词表只有一份（`schema/theme.schema.json`），这里是它的读取器。
 *
 * 另一个更隐蔽的原因：**「文档说的令牌」与「校验器认的令牌」也必须只有一份。**
 * `THEME_SPEC.md` 里的表格由测试比对（见 test/schema.test.mjs），
 * 比对的基准就是这里读出来的词表。
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// 纯事实从 `tokens.mjs` 转出来 —— 让「node 侧」的调用方（校验器、测试、命令行）
// 可以只 import 这一个模块就拿到全部，同时保证浏览器侧只 import `tokens.mjs`。
export {
  CSS_FRAGMENT_LIMIT,
  LAYERS,
  THEME_ID_PATTERN,
  allTokenNames,
  requiredSemanticTokens,
  tokenVocabulary,
} from "./tokens.mjs";

/** 套件根目录（`frontend/theme-kit/`）。 */
export const KIT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * 读取并解析 schema。
 *
 * 返回值是**原始 JSON**，不是某个校验库的对象 —— 这个套件零依赖，
 * 校验逻辑自己写（几十行，且能用中文把每条拒绝理由写清楚）。
 */
export function loadSchema(kitRoot = KIT_ROOT) {
  const path = join(kitRoot, "schema", "theme.schema.json");
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (cause) {
    throw new Error(
      `读不到主题契约 ${path}：${cause.message}\n` +
        `（契约文件缺失时**必须**报错，不能退化成「没有词表 = 什么都合法」——` +
        `那会让所有主题包在同一刻集体通过校验。）`,
    );
  }
}
