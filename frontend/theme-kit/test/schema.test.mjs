/**
 * 契约一致性的测试。**这一组测试盯的是"两份东西悄悄不一致"。**
 *
 * 主题套件里有三处描写同一件事：
 *
 * 1. `schema/theme.schema.json` 的词表（机器认的）；
 * 2. `THEME_SPEC.md` 的令牌表（人读的）；
 * 3. `themes/base-warm.json` 的值（实际生效的）。
 *
 * 三者不一致时，界面**不会报错**：一个只在文档里存在的令牌，写进主题包
 * 会被校验器拒绝（还算好）；而一个只在 schema 里存在、基础主题从没给值的
 * 令牌，会让 `var()` 解析成空 —— 表现出来是"某处样式没生效"，
 * 而查起来要翻三个文件。
 */

import { strict as assert } from "node:assert";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { compileTheme, resolveValue, mergeTokens } from "../src/compile.mjs";
import {
  KIT_ROOT,
  LAYERS,
  THEME_ID_PATTERN,
  allTokenNames,
  loadSchema,
  requiredSemanticTokens,
  tokenVocabulary,
} from "../src/schema.mjs";

const schema = loadSchema();
const vocabulary = tokenVocabulary(schema);
const base = JSON.parse(
  readFileSync(join(KIT_ROOT, "themes", "base-warm.json"), "utf8"),
);

/** 基础主题声明的全部令牌名。 */
function baseTokenNames() {
  const names = new Set();
  for (const layer of LAYERS) {
    for (const name of Object.keys(base.tokens[layer] ?? {})) names.add(name);
  }
  return names;
}

// --------------------------------------------------------------------------- //
// schema 自己
// --------------------------------------------------------------------------- //

test("必填清单是语义层词表的子集", () => {
  for (const name of requiredSemanticTokens(schema)) {
    assert.ok(vocabulary.semantic.has(name), `必填的 ${name} 不在语义层词表里`);
  }
});

test("三层词表互不重叠（一个名字只能属于一层）", () => {
  const seen = new Map();
  for (const layer of LAYERS) {
    for (const name of vocabulary[layer]) {
      assert.ok(!seen.has(name), `${name} 同时出现在 ${seen.get(name)} 与 ${layer}`);
      seen.set(name, layer);
    }
  }
});

test("schema 里 id 的 pattern 与代码里的常量一致", () => {
  assert.equal(schema.properties.id.pattern, THEME_ID_PATTERN.source);
});

test("schema 里 tokens 的三层就是代码里的三层", () => {
  assert.deepEqual(Object.keys(schema.properties.tokens.properties), LAYERS);
});

// --------------------------------------------------------------------------- //
// 基础主题 ↔ schema
// --------------------------------------------------------------------------- //

test("词表里的每一个令牌，基础主题都给了值（没有悬空的令牌）", () => {
  const missing = [...allTokenNames(schema)].filter((name) => !baseTokenNames().has(name));
  assert.deepEqual(
    missing,
    [],
    "这些令牌在 contract 里存在，但基础主题没给值 —— 用到它们的地方会解析成空",
  );
});

test("基础主题里的每个令牌都在词表里", () => {
  const unknown = [...baseTokenNames()].filter((name) => !allTokenNames(schema).has(name));
  assert.deepEqual(unknown, []);
});

test("基础主题里的每个 var() 都能解到字面量", () => {
  const merged = mergeTokens(base, base);
  const bad = [];
  for (const [name, value] of merged) {
    if (typeof value === "string" && value.includes("var(") && resolveValue(merged, value) === null) {
      bad.push(name);
    }
  }
  assert.deepEqual(bad, [], "这几条引用解不动（悬空引用或成环）");
});

test("基础主题编译出来的 CSS 里没有空值", () => {
  const css = compileTheme({ base, pack: base });
  assert.doesNotMatch(css, /:\s*;/, "有一条声明的值是空的");
});

// --------------------------------------------------------------------------- //
// 文档 ↔ schema（文档不能漂）
// --------------------------------------------------------------------------- //

/** 抽出 `THEME_SPEC.md` 里 tokens 标记之间的那一节。 */
function tokenSection() {
  const text = readFileSync(join(KIT_ROOT, "THEME_SPEC.md"), "utf8");
  const begin = text.indexOf("<!-- tokens:begin -->");
  const end = text.indexOf("<!-- tokens:end -->");
  assert.ok(begin >= 0 && end > begin, "THEME_SPEC.md 里的 tokens 标记被删掉了");
  return text.slice(begin, end);
}

/**
 * 表格**第一列**里的令牌 = 清单。
 *
 * 为什么只取第一列：说明文字里会**引用**别的令牌（"压在 `--accent` 上的文字"），
 * 那不是清单项。把引用也算成清单，会让"清单是否完整"这个问题失去意义 ——
 * 而引用的真实性由下面那条 `mentioned` 检查单独兜住。
 */
function listedTokens() {
  const lines = tokenSection().split("\n");
  const names = [];
  for (const line of lines) {
    const match = /^\|\s*`(--[\w-]+)`/.exec(line);
    if (match) names.push(match[1]);
  }
  return names;
}

/** 这一节里出现过的**所有**令牌名（含说明文字里的引用）—— 它们也都必须是真实令牌。 */
function mentionedTokens() {
  return [...tokenSection().matchAll(/`(--[\w-]+)`/g)].map((m) => m[1]);
}

test("文档的令牌表与契约逐一对齐（少一个、多一个都会红）", () => {
  const listed = listedTokens();
  const inSpec = new Set(listed);
  const inSchema = allTokenNames(schema);

  const missing = [...inSchema].filter((name) => !inSpec.has(name)).sort();
  const extra = [...inSpec].filter((name) => !inSchema.has(name)).sort();
  assert.deepEqual(missing, [], "这些令牌在契约里，但文档没写");
  assert.deepEqual(extra, [], "这些令牌写在文档里，但契约里没有");

  // 重复出现会让"哪一份是清单"变得含糊，也让上面两条比较失去意义。
  const duplicates = listed.filter((name, i) => listed.indexOf(name) !== i);
  assert.deepEqual([...new Set(duplicates)], [], "这些令牌在文档的表里出现了不止一次");
});

test("文档里被引用到的令牌也都是真实令牌（引用不能凭空出现）", () => {
  const unknown = [...new Set(mentionedTokens())].filter(
    (name) => !allTokenNames(schema).has(name),
  );
  assert.deepEqual(unknown, [], "文档引用了契约里不存在的令牌");
});

test("文档里声明的必填数量与实际一致", () => {
  const text = readFileSync(join(KIT_ROOT, "THEME_SPEC.md"), "utf8");
  const count = requiredSemanticTokens(schema).length;
  assert.match(
    text,
    new RegExp(`必填的 ${count} 个`),
    `文档里写的必填数量与契约里的 ${count} 个不一致`,
  );
});

test("文档与说明里提到的每个文件都真实存在", () => {
  const missing = [];
  for (const doc of ["THEME_SPEC.md", "README.md", "PROMPT.md"]) {
    const text = readFileSync(join(KIT_ROOT, doc), "utf8");
    for (const match of text.matchAll(/`((?:src|schema|themes|tools|test)\/[\w./-]+)`/g)) {
      const path = match[1];
      if (path.includes("*") || path.endsWith("/")) continue;
      if (!existsSync(join(KIT_ROOT, path))) missing.push(`${doc} → ${path}`);
    }
  }
  assert.deepEqual(missing, [], "文档指向了不存在的文件（链接会烂掉，而且是悄悄烂）");
});

test("THEME_SPEC 里列的错误编号都能在校验器里找到", () => {
  const spec = readFileSync(join(KIT_ROOT, "THEME_SPEC.md"), "utf8");
  const validator = readFileSync(join(KIT_ROOT, "src", "validate.mjs"), "utf8");
  const codes = new Set([...spec.matchAll(/`(TK\d{3})`/g)].map((m) => m[1]));
  assert.ok(codes.size >= 8, "错误编号表太小了，可能被删了一部分");
  for (const code of codes) {
    assert.ok(
      validator.includes(`"${code}"`),
      `${code} 写在文档里，但校验器里根本没有这个编号 —— 文档在承诺一个不存在的行为`,
    );
  }
});

test("themes/ 目录里的每个文件都被文档或测试提到过（没有孤儿）", () => {
  const files = readdirSync(join(KIT_ROOT, "themes")).filter((f) => f.endsWith(".json"));
  assert.deepEqual(files.sort(), ["base-warm.json", "midnight-ink.json"]);
});
