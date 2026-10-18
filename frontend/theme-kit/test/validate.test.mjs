/**
 * 校验器的测试。
 *
 * 这些测试的写法有一条讲究：**断言的是错误编号，不是错误文案。**
 * 文案是给人读的，会改；编号是给测试钉的，改了测试就红。
 * 如果哪天有人把 TK005 的判定删掉，这条测试会失败 —— 而不是
 * "输出看起来还是对的"。
 */

import { strict as assert } from "node:assert";
import { mkdtempSync, writeFileSync, mkdirSync, cpSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { loadSchema } from "../src/schema.mjs";
import {
  checkFragment,
  stripCommentsAndStrings,
  suggest,
  validateAll,
  validatePack,
} from "../src/validate.mjs";
import { KIT_ROOT } from "../src/schema.mjs";

const schema = loadSchema();
const knownIds = new Set(["base-warm", "midnight-ink"]);

/**
 * 一个合法的最小主题包（用 extends，所以不必回答 14 个必填）。
 *
 * 末尾那次删除是有讲究的：`{ extends: undefined }` 展开后键**仍然存在**，
 * 而 JSON 里不可能有这种写法 —— 校验器按"键存在就必须是字符串"处理是对的，
 * 所以这里让 `undefined` 表示"这个字段不在 JSON 里"。
 */
function makePack(overrides = {}) {
  const pack = {
    id: "probe",
    name: "探针",
    extends: "base-warm",
    tokens: { primitive: { "--p-orange-500": "#123456" } },
    ...overrides,
  };
  for (const [key, value] of Object.entries(pack)) {
    if (value === undefined) delete pack[key];
  }
  return pack;
}

function codes(result) {
  return result.errors.map((e) => e.code);
}

function validate(pack, opts = {}) {
  return validatePack(pack, { schema, knownIds, ...opts });
}

// --------------------------------------------------------------------------- //
// 通过的情形
// --------------------------------------------------------------------------- //

test("两个自带主题包都通过校验，且没有提醒", () => {
  const results = validateAll(KIT_ROOT);
  assert.equal(results.length, 2);
  for (const result of results) {
    assert.deepEqual(result.errors, [], `${result.file} 不该有拒绝项`);
    assert.deepEqual(result.warnings, [], `${result.file} 不该有提醒`);
  }
});

test("一个只改颜料的主题包合法", () => {
  const result = validate(makePack());
  assert.deepEqual(result.errors, []);
});

// --------------------------------------------------------------------------- //
// 令牌名与值
// --------------------------------------------------------------------------- //

test("未知令牌名被拒绝，并给出最接近的名字", () => {
  const result = validate(
    makePack({ tokens: { semantic: { "--bg-background": "#fff" } } }),
  );
  assert.deepEqual(codes(result), ["TK005"]);
  assert.match(result.errors[0].message, /--bg-app/);
});

test("令牌名写错一个字母时，建议里能命中正确的那一个", () => {
  const result = validate(
    makePack({ tokens: { component: { "--chat-bubble-user-b": "#fff" } } }),
  );
  assert.deepEqual(codes(result), ["TK005"]);
  assert.match(result.errors[0].message, /--chat-bubble-user-bg/);
});

test("值里出现分号被拒绝 —— 一个值不该能变成几条声明", () => {
  const result = validate(
    makePack({
      tokens: { primitive: { "--p-orange-500": "red; --p-red-500: blue" } },
    }),
  );
  assert.deepEqual(codes(result), ["TK006"]);
  assert.match(result.errors[0].message, /不该能变成几条声明/);
});

test("空值与非字符串值被拒绝", () => {
  assert.deepEqual(
    codes(validate(makePack({ tokens: { primitive: { "--p-orange-500": "" } } }))),
    ["TK006"],
  );
  assert.deepEqual(
    codes(validate(makePack({ tokens: { primitive: { "--p-orange-500": 42 } } }))),
    ["TK006"],
  );
});

test("var() 引用不存在的令牌被拒绝", () => {
  const result = validate(
    makePack({ tokens: { semantic: { "--accent": "var(--p-orange-900)" } } }),
  );
  assert.deepEqual(codes(result), ["TK008"]);
});

test("var() 引用真实存在的令牌被接受（包括跨层引用）", () => {
  const result = validate(
    makePack({ tokens: { semantic: { "--accent": "var(--p-orange-400)" } } }),
  );
  assert.deepEqual(result.errors, []);
});

// --------------------------------------------------------------------------- //
// 必填
// --------------------------------------------------------------------------- //

test("不写 extends 又缺必填令牌 → 逐个报出来", () => {
  const result = validate(
    makePack({
      extends: undefined,
      tokens: { semantic: { "--bg-app": "#fff", "--fg-primary": "#000" } },
    }),
  );
  // 14 个必填里写了 2 个，缺 12 个
  assert.equal(result.errors.filter((e) => e.code === "TK007").length, 12);
  assert.ok(
    result.errors.some((e) => e.message.includes("--focus-ring")),
    "焦点环是最容易被忘掉的那个，它必须在清单里",
  );
});

test("不写 extends 但把 14 个都写上 → 通过", () => {
  const semantic = Object.fromEntries(
    schema.$defs.requiredSemanticTokens.const.map((name) => [name, "#123456"]),
  );
  const result = validate(makePack({ extends: undefined, tokens: { semantic } }));
  assert.deepEqual(result.errors, []);
});

test("写了 extends 就不再要求那 14 个", () => {
  const result = validate(makePack());
  assert.deepEqual(result.errors, []);
});

// --------------------------------------------------------------------------- //
// 结构
// --------------------------------------------------------------------------- //

test("未知字段被拒绝（静默忽略会让作者以为写上了）", () => {
  const result = validate(makePack({ tint: "#fff" }));
  assert.deepEqual(codes(result), ["TK002"]);
  assert.match(result.errors[0].message, /tint/);
});

test("未知的令牌层被拒绝", () => {
  const result = validate(makePack({ tokens: { theme: {} } }));
  assert.deepEqual(codes(result), ["TK002"]);
});

test("id 形态不合法被拒绝", () => {
  assert.deepEqual(codes(validate(makePack({ id: "Bad_ID" }))), ["TK002"]);
});

test("appearance 只能是 light / dark", () => {
  assert.deepEqual(codes(validate(makePack({ appearance: "dim" }))), ["TK002"]);
  assert.deepEqual(validate(makePack({ appearance: "dark" })).errors, []);
});

test("id 与文件名不一致被拒绝", () => {
  const result = validate(makePack(), { filenameId: "other" });
  assert.deepEqual(codes(result), ["TK003"]);
});

test("extends 指向不存在的主题包被拒绝，并列出可选项", () => {
  const result = validate(makePack({ extends: "nope" }));
  assert.deepEqual(codes(result), ["TK004"]);
  assert.match(result.errors[0].message, /base-warm/);
});

test("extends 指向自己也被拒绝", () => {
  const result = validate(makePack({ extends: "probe" }));
  assert.deepEqual(codes(result), ["TK004"]);
});

test("不是合法 JSON 时报 TK001（而不是静默跳过这个文件）", () => {
  const dir = mkdtempSync(join(tmpdir(), "tk-"));
  mkdirSync(join(dir, "themes"));
  cpSync(join(KIT_ROOT, "schema"), join(dir, "schema"), { recursive: true });
  writeFileSync(join(dir, "themes", "broken.json"), "{ oops", "utf8");
  const results = validateAll(dir);
  assert.deepEqual(results.map((r) => r.errors.map((e) => e.code)), [["TK001"]]);
});

// --------------------------------------------------------------------------- //
// 自定义片段
// --------------------------------------------------------------------------- //

test("片段里的被禁构造逐个被拒绝", () => {
  const cases = [
    "@import url(x.css);",
    "@layer other { .a { color: red } }",
    ".a { color: #fff !important }",
    ".a::after { content: \"</style>\" }",
    ".a { background: url(javascript:alert(1)) }",
    ".a { width: expression(alert(1)) }",
  ];
  for (const css of cases) {
    const result = checkFragment(css);
    assert.ok(
      result.errors.some((e) => e.code === "TK009"),
      `这段片段应当被拒绝：${css}`,
    );
  }
});

test("注释里的 @import 不算违规（注释不是代码）", () => {
  assert.deepEqual(checkFragment("/* 这里解释为什么不能用 @import */").errors, []);
});

test("括号不平衡被拒绝 —— 它会把后面的规则一起吃掉", () => {
  const result = checkFragment(".a { color: red;");
  assert.deepEqual(result.errors.map((e) => e.code), ["TK010"]);
});

test("片段超长被拒绝", () => {
  const result = checkFragment(".a { color: red }".repeat(2000));
  assert.deepEqual(result.errors.map((e) => e.code), ["TK011"]);
});

test("position:fixed 与远程 url 只提醒、不拒绝", () => {
  const result = checkFragment(".a { position: fixed; background: url(https://x/y.png) }");
  assert.deepEqual(result.errors, []);
  assert.equal(result.warnings.filter((w) => w.code === "TK103").length, 2);
});

test("清注释与字符串不会把代码一起吃掉了", () => {
  const stripped = stripCommentsAndStrings('.a { color: red } /* c */ "s"');
  assert.match(stripped, /color: red/);
  assert.doesNotMatch(stripped, /c \*\//);
});

// --------------------------------------------------------------------------- //
// 对比度：只提醒
// --------------------------------------------------------------------------- //

test("对比度过低会提醒，但仍然算通过", () => {
  const result = validate(
    makePack({
      extends: undefined,
      tokens: {
        semantic: Object.fromEntries(
          schema.$defs.requiredSemanticTokens.const.map((name) => [name, "#ffffff"]),
        ),
      },
    }),
  );
  assert.deepEqual(result.errors, []);
  assert.ok(
    result.warnings.some((w) => w.code === "TK101"),
    "白字压白底必须被提醒，否则这个检查等于没有",
  );
});

test("对比度算不出来时说「无法判定」，不假装没问题", () => {
  const result = validate(
    makePack({
      extends: undefined,
      tokens: {
        semantic: Object.fromEntries(
          schema.$defs.requiredSemanticTokens.const.map((name) => [
            name,
            "var(--p-font-sans)",
          ]),
        ),
      },
    }),
  );
  assert.deepEqual(result.errors, []);
  assert.ok(result.warnings.some((w) => w.code === "TK102"));
  assert.match(
    result.warnings.find((w) => w.code === "TK102").message,
    /这不是「没问题」/,
  );
});

test("令牌放错层时，错误信息直接说出它属于哪一层", () => {
  // 真实模型第一次生成时就犯了这个错：把 --text-body 写进了 primitive。
  const result = validate(
    makePack({ tokens: { primitive: { "--text-body": "17px" } } }),
  );
  assert.deepEqual(codes(result), ["TK005"]);
  assert.match(result.errors[0].message, /属于第「semantic」层/);
});

test("`extends: null` 等于没写 extends（连着三轮被它卡住的真实事故）", () => {
  // JSON 没有"省略"这个写法，机器生成的包会用 null 表示"我没有这个字段"。
  // 把它当类型错误，会让模型在同一个字段上反复返工 —— 而那一轮的内容
  // 其实只差这一处。
  const result = validate(makePack({ extends: null }));
  assert.ok(
    !result.errors.some((e) => e.code === "TK002"),
    "不该再报「extends 必须是字符串」—— null 就是没写",
  );
  // 没有 extends 就必须自己回答 14 个必填，而这个测试包一个语义令牌都没写
  assert.equal(result.errors.filter((e) => e.code === "TK007").length, 14);
});

test("`css: null`、`description: null` 也当作没写", () => {
  const result = validate(makePack({ css: null, description: null }));
  assert.deepEqual(result.errors, []);
  assert.equal(checkFragment("").errors.length, 0);
});

test("`tokens.css` 的报错直接说片段该写在哪", () => {
  const result = validate(
    makePack({ tokens: { semantic: { "--bg-app": "#fff" }, css: ".a{}" } }),
  );
  assert.deepEqual(codes(result), ["TK002"]);
  assert.match(result.errors[0].message, /写在\*\*顶层\*\*的 `css` 字段里/);
});

// --------------------------------------------------------------------------- //
// 值的类型（TK012）—— 这一条是真实运行逼出来的
// --------------------------------------------------------------------------- //

test("长度令牌写了带引号的 em 字符串 → 被拒绝（模型真写出来的那个值）", () => {
  const result = validate(
    makePack({ tokens: { semantic: { "--text-body": "'1.0em'" } } }),
  );
  assert.deepEqual(codes(result), ["TK012"]);
  assert.match(result.errors[0].message, /长度/);
  assert.match(result.errors[0].message, /不要加引号/);
});

test("长度令牌写了长度 → 通过；写别的 → 被拒绝", () => {
  const ok = [
    ["--text-body", "17px"],
    ["--text-body", "1rem"],
    ["--space-gutter", "var(--p-space-3)"],
    ["--radius-card", "var(--p-radius-lg)"],
    ["--border-width", "1px"],
  ];
  for (const [name, value] of ok) {
    const layer = name.startsWith("--p-") ? "primitive" : "semantic";
    assert.deepEqual(
      validate(makePack({ tokens: { [layer]: { [name]: value } } })).errors,
      [],
      `${name}: ${value} 应当被接受`,
    );
  }
  for (const [name, value] of [
    ["--text-body", "big"],
    ["--space-gutter", "#123456"],
    ["--text-body", "17"],
  ]) {
    const result = validate(makePack({ tokens: { semantic: { [name]: value } } }));
    assert.ok(
      result.errors.some((e) => e.code === "TK012"),
      `${name}: ${value} 应当被拒绝`,
    );
  }
});

test("行高要求无单位数字，时长要求时间单位", () => {
  assert.deepEqual(
    validate(makePack({ tokens: { primitive: { "--p-leading-normal": "1.6" } } })).errors,
    [],
  );
  assert.ok(
    validate(makePack({ tokens: { primitive: { "--p-leading-normal": "1.6em" } } })).errors.some(
      (e) => e.code === "TK012",
    ),
  );
  assert.deepEqual(
    validate(makePack({ tokens: { primitive: { "--p-duration-base": "200ms" } } })).errors,
    [],
  );
  assert.ok(
    validate(makePack({ tokens: { primitive: { "--p-duration-base": "0.2" } } })).errors.some(
      (e) => e.code === "TK012",
    ),
  );
});

test("颜色令牌指向一个字号令牌 → 被拒绝（模型真写出来的那一行）", () => {
  // 真实运行：--fg-primary: "var(--p-text-base)" —— 正文字色指向了字号。
  // 它不是简写、也不是长度字面量，所以当时所有检查都放行了。
  const result = validate(
    makePack({ tokens: { semantic: { "--fg-primary": "var(--p-text-base)" } } }),
  );
  assert.deepEqual(codes(result), ["TK012"]);
  assert.match(result.errors[0].message, /`--p-text-base`（长度令牌）/);
});

test("字号令牌指向一个颜色令牌 → 同样被拒绝（反方向的同一个错）", () => {
  // 另一次真实运行：--text-body: "var(--fg-primary)"。
  const result = validate(
    makePack({ tokens: { semantic: { "--text-body": "var(--fg-primary)" } } }),
  );
  assert.deepEqual(codes(result), ["TK012"]);
  assert.match(result.errors[0].message, /`--fg-primary`（颜色令牌）/);
});

test("同类型的引用不被误报（长度↔长度、颜色↔颜色、时长↔时长）", () => {
  const cases = [
    ["semantic", "--fg-primary", "var(--p-ink-900)"],
    ["semantic", "--border-width", "var(--p-space-1)"],
    ["primitive", "--p-duration-base", "var(--p-duration-slow)"],
    ["primitive", "--p-leading-normal", "var(--p-leading-relaxed)"],
    ["semantic", "--shadow-card", "var(--p-shadow-md)"],
  ];
  for (const [layer, name, value] of cases) {
    assert.deepEqual(
      validate(makePack({ tokens: { [layer]: { [name]: value } } })).errors,
      [],
      `${name}: ${value} 不该被报`,
    );
  }
});

test("颜色令牌指向另一个颜色令牌 → 通过", () => {
  assert.deepEqual(
    validate(
      makePack({
        tokens: { semantic: { "--fg-primary": "var(--p-ink-900)" } },
      }),
    ).errors,
    [],
  );
});

test("不认识的合法颜色写法不被误报（oklch / 具名色）", () => {
  for (const value of ["oklch(0.7 0.1 60)", "light-dark(#fff, #000)", "rebeccapurple"]) {
    assert.deepEqual(
      validate(makePack({ tokens: { semantic: { "--bg-app": value } } })).errors,
      [],
      `${value} 是合法颜色，不该被拒`,
    );
  }
});

test("字体栈与阴影不做类型检查（它们的形态太多，硬套正则会造假违规）", () => {
  assert.deepEqual(
    validate(
      makePack({
        tokens: {
          primitive: { "--p-font-sans": '"Segoe UI", system-ui, sans-serif' },
          semantic: { "--shadow-card": "0 1px 2px rgba(0, 0, 0, 0.1)" },
        },
      }),
    ).errors,
    [],
  );
});

test("颜色令牌里塞边框简写 → 被拒绝（模型真写出来的那个值）", () => {
  // 真实运行：模型把 --border-subtle 写成 "2px solid #e6dcc9"。
  // 它不报错，但 border: var(--border-width) solid var(--border-subtle)
  // 会整条失效 —— 边框直接消失。
  const result = validate(
    makePack({ tokens: { semantic: { "--border-subtle": "2px solid #e6dcc9" } } }),
  );
  assert.deepEqual(codes(result), ["TK012"]);
  assert.match(result.errors[0].message, /应当是一个颜色/);
});

test("颜色令牌的正常写法都通过", () => {
  for (const value of ["#e6dcc9", "rgba(0, 0, 0, 0.1)", "var(--p-warm-300)"]) {
    assert.deepEqual(
      validate(makePack({ tokens: { semantic: { "--border-subtle": value } } })).errors,
      [],
      `${value} 应当被接受`,
    );
  }
});

test("禁用构造的报错显示真正的写法，不是内部编号名", () => {
  const result = checkFragment("@layer theme-extra { .a { color: var(--fg-primary) } }");
  assert.deepEqual(result.errors.map((e) => e.code), ["TK009"]);
  assert.match(result.errors[0].message, /`@layer`/);
  assert.doesNotMatch(
    result.errors[0].message,
    /at-layer/,
    "内部编号名对作者毫无意义 —— 他去搜 `at-layer` 什么都搜不到",
  );
});

test("用逗号分隔声明 → 被拒绝（模型返工那一轮真写出来的那段）", () => {
  // 真实运行：content:'';position:absolute,inset:0,pointer-events:none,z-index:-1;
  // 括号配平、没有禁用构造、长度也没问题 —— 当时全部放行，而整段是无效的。
  const css =
    ".tk-surface::before{content:'';position:absolute,inset:0,pointer-events:none,z-index:-1,border-radius:inherit}";
  const result = checkFragment(css);
  assert.deepEqual(result.errors.map((e) => e.code), ["TK013"]);
  assert.match(result.errors[0].message, /分隔声明要用/);
});

test("值里的逗号不算违规（字体栈、多段阴影、多段过渡）", () => {
  for (const css of [
    ".a { font-family: var(--font-ui), sans-serif }",
    ".a { box-shadow: 0 1px 2px rgba(0,0,0,.1), 0 2px 4px rgba(0,0,0,.1) }",
    ".a { transition: color 1s, background 2s }",
    ".a { background: image-set(url(a.png) 1x, url(b.png) 2x) }",
    ".a, .b { color: var(--fg-primary) }",
  ]) {
    assert.deepEqual(checkFragment(css).errors, [], `不该报：${css}`);
  }
});

test("嵌套选择器列表里的逗号不算违规", () => {
  const css = ".a { color: var(--fg-primary); .b, .c { color: var(--fg-secondary) } }";
  assert.deepEqual(checkFragment(css).errors, []);
});

// --------------------------------------------------------------------------- //
// 建议与自检
// --------------------------------------------------------------------------- //

test("编辑距离建议不会对完全无关的名字乱猜", () => {
  assert.equal(suggest("--completely-different-name", new Set(["--bg-app"])), "");
});

test("把「危险」记成 --danger-bg 时，能指到 --state-danger-soft", () => {
  // 真实运行里模型写的名字。编辑距离与前缀两路都抓不到它。
  const vocabulary = new Set([
    "--state-danger",
    "--state-danger-soft",
    "--bg-inset",
    "--fg-primary",
  ]);
  const hint = suggest("--danger-bg", vocabulary);
  assert.match(hint, /--state-danger/);
});

test("校验器读不到契约时报错，而不是「没有词表 = 什么都合法」", () => {
  const dir = mkdtempSync(join(tmpdir(), "tk-noschema-"));
  assert.throws(() => loadSchema(dir), /读不到主题契约/);
  assert.throws(() => validateAll(dir), /读不到主题契约/);
});
