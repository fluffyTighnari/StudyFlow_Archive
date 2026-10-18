/**
 * 主题包校验器。**零依赖，自己写。**
 *
 * ## 为什么不用一个 JSON Schema 库
 *
 * 两个原因，第二个更重要：
 *
 * 1. 这个套件要能被直接拷进任何项目（包括没有 npm 的环境），所以不能有依赖；
 * 2. 库的报错是**英文的、面向 schema 作者的**（`must NOT have additional
 *    properties`），而这里需要的是**面向主题作者的**："你不认识
 *    `--bg-background` 这个名字；你想找的可能是 `--bg-app`。"
 *    一个写主题包的人（或者一个按需求生成主题包的模型）看不懂前者，
 *    于是它的反应会是「随便换个写法试试」，而不是「我的令牌名写错了」。
 *
 * 所以每条拒绝都有一个**稳定编号**（TK001…），测试按编号断言 ——
 * 编号变了测试就红，而不是文案悄悄改了却没人发现。
 *
 * ## 关于自定义 css 片段：能被机器保证的只有一部分
 *
 * 校验器拒绝的是**构造**（`@import` / `@layer` / `!important` / `</` /
 * `javascript:` / 括号不平衡 / 超长），不判断片段写得**好不好看、对不对**。
 * 这一点写在 `THEME_SPEC.md` 里，也在校验输出里如实说明 ——
 * 假装它被管住了，比不管它更坏。
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import {
  CSS_FRAGMENT_LIMIT,
  KIT_ROOT,
  LAYERS,
  THEME_ID_PATTERN,
  allTokenNames,
  loadSchema,
  requiredSemanticTokens,
  tokenVocabulary,
} from "./schema.mjs";
import { contrastRatio, mergeTokens, parseColor, resolveColor } from "./compile.mjs";

/**
 * **颜色类**令牌的判定（`TK012` 的第二半）。
 *
 * 由来同样是真实运行：模型把 `--border-subtle` 写成了
 * `"2px solid #e6dcc9"`（一条 border 简写），而它是**一个颜色**。
 * 这种值不会让别处报错，但 `border: var(--border-width) solid var(--border-subtle)`
 * 会在计算时变成 `border: 1px solid 2px solid #e6dcc9` —— 整条声明失效，
 * **边框直接消失**，而界面上看不出任何"错误"。
 *
 * 只覆盖"名字就说明它是一个颜色"的那些令牌；`--shadow-*` 这类多值令牌
 * 刻意排除在外（阴影里出现 `rgba()` 是正常的）。
 */
function isColorToken(name) {
  if (/-width$|-offset$|-radius|-duration|-leading|-text-|-space-|-font-/.test(name)) {
    return false;
  }
  return /^--(?:p-(?:white|warm|ink|orange|green|red|blue)|bg|fg|accent|border|state|focus|checkpoint|trace|stage|shell|badge|pack|chat-bubble|chat-note|chat-thinking|plan-step)/.test(
    name,
  );
}

/**
 * 边框简写的成分：长度单位，或者线型关键字。
 * 出现在一个**颜色**令牌里，就说明作者把整条简写塞进来了。
 */
const SHORTHAND_IN_COLOR =
  /-?\d*\.?\d+(?:px|rem|em|pt)\b|\b(?:solid|dashed|dotted|double|groove|ridge|inset|outset)\b/i;

/**
 * 一个令牌属于哪一类（颜色 / 长度 / 行高 / 时长）。认不出来就是 null。
 *
 * 有了它，才能查"类型串了"这一类错误 —— 而它们是**双向**的：
 * 真实运行里出现过两次，方向正好相反：
 *
 * - `--fg-primary: var(--p-text-base)`（颜色令牌指向字号）；
 * - `--text-body: var(--fg-primary)`（字号令牌指向颜色）。
 *
 * 两次都通过了当时所有的检查，因为"值是一个合法的 var()"这件事本身没错。
 */
function kindOfToken(name) {
  if (isColorToken(name)) return "颜色";
  const rule = KIND_RULES.find((candidate) => candidate.matches(name));
  return rule ? rule.kind : null;
}

/**
 * 颜色令牌里"**确定不是颜色**"的字面量信号。返回一个说法，或者 null。
 *
 * 为什么不去解析值本身：解析器不认识 `oklch(…)`、`light-dark(…)`、
 * `rebeccapurple` 这些合法颜色，用它当判据会把它们全判成错 ——
 * 而假违规的代价是这个检查最后没人跑。
 */
function colorLiteralProblem(value) {
  const rest = value.replace(/var\([^)]*\)/g, " ");
  if (SHORTHAND_IN_COLOR.test(rest)) return "值里有边框简写的成分";
  return null;
}

/** 令牌值里不允许出现的字符：它们能让「一个值」变成「几条声明」。 */
const VALUE_FORBIDDEN = /[;{}]/;

/**
 * 值的**类型**检查（`TK012`）。
 *
 * 这条规则的由来是一次真实运行：模型给 `--text-body` 写了 `'1.0em'`
 * ——引号 + em，而它本该是一个长度。这种值**不会**被别处拦住：
 * 它不是颜色、引号里也没有 `;`，schema 只要求它是字符串。而它生效的结果是
 * `font-size: var(--text-body)` 在计算时变成无效值，浏览器把整条声明丢掉 ——
 * 于是"正文大一号"这个需求**静默地没实现**，界面上看不出报错。
 *
 * 只给"值域确实很窄"的几族加类型检查。阴影、字体栈这类值的形态太多，
 * 硬套一个正则只会产生假违规 —— 而假违规的代价是这个检查最后没人跑。
 */
const KIND_RULES = [
  {
    // --p-radius-* / --radius-* / --space-* / --p-space-* / --p-text-* / --text-*
    // / --border-width / --focus-ring-width / --focus-ring-offset
    matches: (name) =>
      /(?:^|--)(?:p-)?(?:radius|space|text)-/.test(name) ||
      /(?:border|focus-ring)-(?:width|offset)$/.test(name),
    kind: "长度",
    pattern: /^(?:var\(\s*--[\w-]+\s*(?:,[^)]*)?\)|-?\d*\.?\d+(?:px|rem|em|%))$/,
    hint: "写成 `14px` 这样的长度，或者引用一个长度令牌（`var(--p-text-base)`）；不要加引号",
  },
  {
    matches: (name) => /leading/.test(name),
    kind: "无单位数字（行高）",
    pattern: /^(?:var\(\s*--[\w-]+\s*(?:,[^)]*)?\)|-?\d*\.?\d+)$/,
    hint: "行高写成 `1.6` 这样的无单位数字（带单位会让它变成固定高度）",
  },
  {
    matches: (name) => /duration/.test(name),
    kind: "时间",
    pattern: /^(?:var\(\s*--[\w-]+\s*(?:,[^)]*)?\)|\d*\.?\d+(?:ms|s))$/,
    hint: "写成 `200ms` 或 `0.2s`",
  },
];

/** 自定义片段里被禁的构造，以及每一禁的理由（理由要能对作者讲清楚）。 */
const FORBIDDEN_IN_FRAGMENT = [
  {
    code: "at-import",
    display: "`@import`",
    pattern: /@import\b/i,
    reason: "外部样式表会让「这个主题长什么样」取决于网络与远端，且加载顺序不可控",
  },
  {
    // 真实运行里模型正是写了这个：它想"把自己的规则放进主题层"，
    // 于是自己包了一层 `@layer theme-extra { … }` —— 而套件已经替它包好了。
    code: "at-layer",
    display: "`@layer`",
    pattern: /@layer\b/i,
    reason:
      "层序由套件提供（theme → theme-extra → components），片段会被自动包进 theme-extra；" +
      "主题包各自声明层序，等于让最后加载的那个包决定整个应用的层叠规则",
  },
  {
    code: "at-charset",
    display: "`@charset`",
    pattern: /@charset\b/i,
    reason: "@charset 只允许出现在样式表最开头，放进片段里不会生效，只会让人以为它生效了",
  },
  {
    code: "important",
    display: "`!important`",
    pattern: /!\s*important\b/i,
    reason:
      "!important 是唯一能盖过套件自身可读性兜底的构造；主题包应当是「换一套值」，不是「赢过所有人」",
  },
  {
    code: "javascript-url",
    display: "`javascript:`",
    pattern: /javascript:/i,
    reason: "旧式脚本注入载体",
  },
  {
    code: "expression",
    display: "`expression(`",
    pattern: /expression\s*\(/i,
    reason: "旧式脚本注入载体（IE 时期）",
  },
  {
    code: "moz-binding",
    display: "`-moz-binding:`",
    pattern: /-moz-binding\s*:/i,
    reason: "旧式脚本注入载体",
  },
];

/** 只提醒、不拒绝的构造。 */
const NOTICE_IN_FRAGMENT = [
  {
    code: "position-fixed",
    display: "`position: fixed`",
    pattern: /position\s*:\s*fixed\b/i,
    reason: "固定定位会盖住界面上的其它东西，而且它跟着视口而不是跟着应用",
  },
  {
    code: "high-z-index",
    display: "过大的 `z-index`",
    pattern: /z-index\s*:\s*(\d{3,})/i,
    reason: "过大的 z-index 会让这个主题压住套件自己的浮层（对话框、菜单）",
  },
  {
    code: "remote-url",
    display: "远程 `url(...)`",
    pattern: /url\(\s*['"]?(?:https?:)?\/\//i,
    reason: "远程资源意味着离线时主题是坏的，而且会把访问记录发给第三方",
  },
];

const CONTRAST_PAIRS = [
  { fg: "--fg-primary", bg: "--bg-app", min: 4.5, what: "正文/页面底色" },
  { fg: "--fg-primary", bg: "--bg-surface", min: 4.5, what: "正文/卡片底色" },
  { fg: "--fg-secondary", bg: "--bg-surface", min: 4.5, what: "次级文字/卡片底色" },
  { fg: "--fg-muted", bg: "--bg-surface", min: 3.0, what: "弱化文字/卡片底色" },
  { fg: "--fg-on-accent", bg: "--accent", min: 4.5, what: "主色上的文字" },
];

/**
 * 声明块里出现 `,属性:` 的检查（`TK013`）。
 *
 * 由来：真实运行里模型（在**返工那一轮**）把片段写成了
 * `content:'';position:absolute,inset:0,pointer-events:none,z-index:-1;`
 * —— 用**逗号**当声明分隔符。这不是"写得不好看"：整段会变成一个无效声明，
 * 浏览器全部丢掉，**纹理根本不会出现**，而校验器当时是放行的
 * （括号配平、没有禁用构造、长度也没问题）。
 *
 * 判据刻意收窄到"逗号后面紧跟一个**已知的 CSS 属性名**加冒号"：
 * 值里的逗号是合法的（`font-family: A, B` / `transition: a 1s, b 2s` /
 * `background: url(a), url(b)`），只有"属性名"这个形态才说明它想开始一条新声明。
 */
const DECLARATION_LIKE = /^[ \t]*([a-z-]{2,})[ \t]*:/i;

const KNOWN_PROPERTIES = new Set([
  "position", "inset", "top", "right", "bottom", "left", "z-index", "content",
  "display", "visibility", "opacity", "pointer-events", "overflow", "mix-blend-mode",
  "background", "background-color", "background-image", "background-size",
  "background-position", "background-repeat", "backdrop-filter", "filter",
  "border", "border-radius", "border-color", "border-width", "border-style",
  "box-shadow", "outline", "outline-offset", "color", "padding", "padding-top",
  "padding-right", "padding-bottom", "padding-left", "margin", "margin-top",
  "margin-right", "margin-bottom", "margin-left", "width", "min-width",
  "max-width", "height", "min-height", "max-height", "font-size", "font-weight",
  "font-family", "line-height", "letter-spacing", "text-align", "text-decoration",
  "text-transform", "transition", "animation", "transform", "gap", "row-gap",
  "column-gap", "flex", "flex-direction", "align-items", "justify-content",
  "grid-template-columns", "grid-template-rows", "grid-template-areas", "order",
]);

/** 找出"用逗号分隔声明"的位置，返回那些属性名。 */
export function commaSeparatedProperties(scan) {
  const hits = [];
  let depth = 0;
  for (let i = 0; i < scan.length; i += 1) {
    const ch = scan[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") depth -= 1;
    else if (ch === "," && depth > 0) {
      const match = DECLARATION_LIKE.exec(scan.slice(i + 1));
      if (match && KNOWN_PROPERTIES.has(match[1].toLowerCase())) hits.push(match[1]);
    }
  }
  return hits;
}

// --------------------------------------------------------------------------- //
// 工具
// --------------------------------------------------------------------------- //

/** 去掉注释与字符串字面量，用于扫描构造（免得注释里提到 @import 就被拒）。 */
export function stripCommentsAndStrings(css) {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\]|\\.)*'/g, "''");
}

function error(code, message) {
  return { code, message };
}

function warning(code, message) {
  return { code, message };
}

// --------------------------------------------------------------------------- //
// 单个主题包
// --------------------------------------------------------------------------- //

/**
 * 校验一个主题包。
 *
 * @param {object} raw 解析后的 JSON（未校验）
 * @param {object} opts
 * @param {object} opts.schema 契约
 * @param {string} [opts.filenameId] 文件名推出的 id（用于比对）
 * @param {Set<string>} [opts.knownIds] 已知主题包 id（用于校验 extends）
 * @param {object|null} [opts.base] 基础包（用于对比度判定）
 * @returns {{errors: Array, warnings: Array, pack: object|null}}
 */
export function validatePack(raw, opts) {
  const { schema, filenameId, knownIds = new Set(), base = null } = opts;
  const errors = [];
  const warnings = [];

  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    errors.push(error("TK002", "主题包必须是一个 JSON 对象"));
    return { errors, warnings, pack: null };
  }

  // **`null` 等于"没有这个字段"。**
  //
  // JSON 里没有"省略"，所以"这个可选字段我没有值"最自然的写法就是 `null` ——
  // 机器生成的包尤其如此。把它当成类型错误会让人（和模型）在同一个字段上
  // 反复返工：真实运行里连跑三轮，每轮都栽在 `"extends": null` 上，
  // 而那一轮的内容其实只差这一处。
  //
  // 只对**顶层可选字段**做这个归一化；令牌值里的 `null` 仍然是非法的
  //（那是一个值，不是"没有值"）。
  for (const key of ["appearance", "description", "extends", "css", "$schema"]) {
    if (key in raw && raw[key] === null) delete raw[key];
  }

  // -- 结构 ---------------------------------------------------------------- //
  const allowed = new Set([
    "$schema",
    "id",
    "name",
    "appearance",
    "description",
    "extends",
    "tokens",
    "css",
  ]);
  for (const key of Object.keys(raw)) {
    if (!allowed.has(key)) {
      errors.push(
        error(
          "TK002",
          `不认识的字段 \`${key}\`。主题包只有这些字段：${[...allowed].join(" / ")}` +
            `（未知字段会被静默忽略，那样你以为写上了的东西其实没生效）`,
        ),
      );
    }
  }
  for (const required of ["id", "name", "tokens"]) {
    if (!(required in raw)) {
      errors.push(error("TK002", `缺少必填字段 \`${required}\``));
    }
  }
  if (typeof raw.id === "string" && !THEME_ID_PATTERN.test(raw.id)) {
    errors.push(
      error(
        "TK002",
        `id \`${raw.id}\` 不合法：只能是小写字母、数字与连字符，且以字母或数字开头`,
      ),
    );
  }
  if (filenameId !== undefined && raw.id !== filenameId) {
    errors.push(
      error(
        "TK003",
        `id \`${raw.id}\` 与文件名 \`themes/${filenameId}.json\` 不一致 —— ` +
          `按 id 加载主题时会找不到它`,
      ),
    );
  }
  if ("name" in raw && (typeof raw.name !== "string" || raw.name.trim() === "")) {
    errors.push(error("TK002", "`name` 必须是非空字符串（界面上要给用户看）"));
  }
  if ("description" in raw && typeof raw.description !== "string") {
    errors.push(error("TK002", "`description` 必须是字符串"));
  }
  if ("appearance" in raw && raw.appearance !== "light" && raw.appearance !== "dark") {
    errors.push(
      error(
        "TK002",
        `\`appearance\` 只能是 \`light\` 或 \`dark\`（现在写的是 ${JSON.stringify(raw.appearance)}）` +
          `—— 这个字段会被用来在系统偏好为暗色时挑默认主题，写错会让亮色主题被当成暗色的`,
      ),
    );
  }

  // -- extends ------------------------------------------------------------- //
  const vocabulary = tokenVocabulary(schema);
  const tokens = raw.tokens;
  if (tokens !== null && typeof tokens === "object" && !Array.isArray(tokens)) {
    for (const key of Object.keys(tokens)) {
      if (!LAYERS.includes(key)) {
        // `tokens.css` 是最常犯的那一个：片段要写在**顶层**的 `css` 字段里。
        const hint =
          key === "css"
            ? " —— `css` 片段写在**顶层**的 `css` 字段里，不在 `tokens` 下面"
            : `；只有 ${LAYERS.join(" / ")}`;
        errors.push(
          error("TK002", `\`tokens\` 下不认识的层 \`${key}\`${hint}`),
        );
      }
    }
  } else {
    errors.push(error("TK002", "`tokens` 必须是一个对象（三层令牌）"));
  }

  if ("extends" in raw) {
    if (typeof raw.extends !== "string") {
      errors.push(error("TK002", "`extends` 必须是字符串（另一个主题包的 id）"));
    } else if (raw.extends === raw.id) {
      errors.push(error("TK004", `\`extends\` 指向它自己（\`${raw.id}\`）`));
    } else if (!knownIds.has(raw.extends)) {
      errors.push(
        error(
          "TK004",
          `\`extends\` 指向一个不存在的主题包 \`${raw.extends}\`` +
            (knownIds.size ? `；现有的是：${[...knownIds].sort().join(" / ")}` : ""),
        ),
      );
    }
  }

  // -- 令牌名与值 ----------------------------------------------------------- //
  const declared = new Set();
  if (tokens && typeof tokens === "object" && !Array.isArray(tokens)) {
    for (const layer of LAYERS) {
      const group = tokens[layer];
      if (group === undefined) continue;
      if (group === null || typeof group !== "object" || Array.isArray(group)) {
        errors.push(error("TK002", `\`tokens.${layer}\` 必须是一个对象`));
        continue;
      }
      for (const [name, value] of Object.entries(group)) {
        if (!vocabulary[layer].has(name)) {
          // **跨层提示**：真实模型第一次生成时就犯了这个错 ——
          // 它把 `--text-body` 写进了 primitive，而它属于 semantic。
          // 只说"这一层没有这个名字"会让它继续猜；说清"它在哪一层"就一次到位。
          const otherLayer = LAYERS.find(
            (other) => other !== layer && vocabulary[other].has(name),
          );
          const hint = otherLayer
            ? `它属于第「${otherLayer}」层 —— 每一层各有哪些令牌，见 THEME_SPEC.md 第 2 节`
            : suggest(name, vocabulary[layer]);
          errors.push(
            error(
              "TK005",
              `第「${layer}」层里没有 \`${name}\` 这个令牌` + (hint ? `；${hint}` : ""),
            ),
          );
          continue;
        }
        declared.add(name);
        if (typeof value !== "string" || value.trim() === "") {
          errors.push(error("TK006", `\`${name}\` 的值必须是非空字符串`));
          continue;
        }
        if (value.length > 200) {
          errors.push(
            error("TK006", `\`${name}\` 的值太长了（${value.length} 字符，上限 200）`),
          );
        }
        if (VALUE_FORBIDDEN.test(value)) {
          errors.push(
            error(
              "TK006",
              `\`${name}\` 的值里有 \`;\` \`{\` 或 \`}\` —— 一个值不该能变成几条声明` +
                `（值：${JSON.stringify(value)}）`,
            ),
          );
        }
        const kindRule = KIND_RULES.find((rule) => rule.matches(name));
        if (kindRule && !kindRule.pattern.test(value.trim())) {
          errors.push(
            error(
              "TK012",
              `\`${name}\` 应当是一个${kindRule.kind}，但写的是 ${JSON.stringify(value)}。` +
                `${kindRule.hint}。**这种值不会让别处报错**：它会一路写进 CSS，` +
                `然后在计算时变成无效值被浏览器丢掉 —— 需求就这样静默地没实现`,
            ),
          );
        }
        if (isColorToken(name)) {
          // 颜色令牌的字面量问题由下面那一趟统一查（避免报两遍）。
        }      }
    }
  }

  // -- 必填 ---------------------------------------------------------------- //
  if (!("extends" in raw)) {
    for (const name of requiredSemanticTokens(schema)) {
      if (!declared.has(name)) {
        errors.push(
          error(
            "TK007",
            `缺少必填令牌 \`${name}\`。不写 \`extends\` 就等于声明自己是一个完整主题，` +
              `这 14 个是「文字还看不看得清」所依赖的那一组；只想改几处就写 \`extends\``,
          ),
        );
      }
    }
  }

  // -- var() 引用 ----------------------------------------------------------- //
  const everything = allTokenNames(schema);
  for (const layer of LAYERS) {
    for (const [name, value] of Object.entries(tokens?.[layer] ?? {})) {
      if (typeof value !== "string") continue;
      for (const ref of value.matchAll(/var\(\s*(--[\w-]+)/g)) {
        if (!everything.has(ref[1])) {
          const hint = suggest(ref[1], everything);
          errors.push(
            error(
              "TK008",
              `\`${name}\` 引用了不存在的令牌 \`${ref[1]}\`` + (hint ? `；${hint}` : ""),
            ),
          );
        }
      }
    }
  }

  // -- 自定义片段 ----------------------------------------------------------- //
  if ("css" in raw) {
    if (typeof raw.css !== "string") {
      errors.push(error("TK009", "`css` 必须是字符串"));
    } else {
      errors.push(...checkFragment(raw.css).errors);
      warnings.push(...checkFragment(raw.css).warnings);
    }
  }

  // -- 颜色令牌里的字面量不该是边框简写（`TK012`） --------------------------- //
  for (const layer of LAYERS) {
    for (const [name, value] of Object.entries(tokens?.[layer] ?? {})) {
      if (typeof value !== "string" || !isColorToken(name)) continue;
      const problem = colorLiteralProblem(value);
      if (problem === null) continue;
      errors.push(
        error(
          "TK012",
          `\`${name}\` 应当是一个颜色，但${problem}：${JSON.stringify(value)}。` +
            `颜色令牌只放颜色（\`#e6dcc9\` 或 \`var(--p-warm-300)\`）。` +
            `**这种值不会让别处报错**：它会一路写进 CSS，然后在计算时变成无效值` +
            `被浏览器丢掉 —— 需求就这样静默地没实现`,
        ),
      );
    }
  }

  // -- 引用的令牌类型要一致（`TK012`） --------------------------------------- //
  // 查**双向**：颜色 ↔ 长度 / 行高 / 时长 之间互相指错，都是静默失效。
  for (const layer of LAYERS) {
    for (const [name, value] of Object.entries(tokens?.[layer] ?? {})) {
      if (typeof value !== "string") continue;
      const own = kindOfToken(name);
      if (own === null) continue;
      for (const ref of value.matchAll(/var\(\s*(--[\w-]+)/g)) {
        const other = kindOfToken(ref[1]);
        if (other === null || other === own) continue;
        errors.push(
          error(
            "TK012",
            `\`${name}\` 是一个${own}令牌，但它引用的是 \`${ref[1]}\`（${other}令牌）。` +
              `引用一个类型不对的令牌**不会让别处报错**：值会一路写进 CSS，` +
              `然后在计算时变成无效值被浏览器丢掉 —— 而界面上只是"没生效"。` +
              (own === "颜色"
                ? `颜色要引用颜色（如 \`var(--p-ink-900)\`）。`
                : `${own}要引用${own}（见文档第 2 节的表格）。`) +
              `真实运行里，这个位置被写成过 \`var(--p-text-base)\`（字号）和 ` +
              `\`var(--fg-primary)\`（颜色），两次都静默失效`,
          ),
        );
      }
    }
  }

  // -- 对比度（只提醒） ------------------------------------------------------ //
  if (errors.length === 0) {
    const merged = mergeTokens(base, raw);
    for (const pair of CONTRAST_PAIRS) {
      if (!merged.has(pair.fg) || !merged.has(pair.bg)) continue;
      const fg = resolveColor(merged, merged.get(pair.fg));
      const bg = resolveColor(merged, merged.get(pair.bg));
      if (fg === null || bg === null) {
        warnings.push(
          warning(
            "TK102",
            `对比度无法判定（${pair.what}）：${pair.fg} 或 ${pair.bg} 不是纯色值。` +
              `**这不是「没问题」** —— 只是这个检查器算不了`,
          ),
        );
        continue;
      }
      const ratio = contrastRatio(fg, bg);
      if (ratio !== null && ratio < pair.min) {
        warnings.push(
          warning(
            "TK101",
            `对比度偏低（${pair.what}）：${ratio.toFixed(2)}:1，建议 ≥ ${pair.min}:1`,
          ),
        );
      }
    }
  }

  return { errors, warnings, pack: raw };
}

/** 片段检查（结构与构造）。 */
export function checkFragment(css) {
  const errors = [];
  const warnings = [];

  if (css.length > CSS_FRAGMENT_LIMIT) {
    errors.push(
      error(
        "TK011",
        `自定义片段太长（${css.length} 字符，上限 ${CSS_FRAGMENT_LIMIT}）—— ` +
          `主题包不该长成一个应用`,
      ),
    );
  }

  // 这一条必须在**原文**上查：`content: "</style>"` 同样能逃出 <style> 元素。
  if (css.includes("</")) {
    errors.push(
      error("TK009", "片段里出现 `</`：它能从 <style> 元素里逃出去（例如 content: \"</style>\"）"),
    );
  }

  const scan = stripCommentsAndStrings(css);
  for (const rule of FORBIDDEN_IN_FRAGMENT) {
    if (rule.pattern.test(scan)) {
      // 显示**真正的构造**（`@layer`），不是内部的编号名（`at-layer`）。
      // 这一处是被真实运行打出来的：模型写了 `@layer theme-extra { … }`，
      // 而报错说"片段里有 `at-layer`" —— 作者拿着这句话去搜，什么都搜不到。
      errors.push(
        error("TK009", `片段里有 ${rule.display ?? `\`${rule.code}\``}：${rule.reason}`),
      );
    }
  }
  for (const rule of NOTICE_IN_FRAGMENT) {
    if (rule.pattern.test(scan)) {
      warnings.push(
        warning(
          "TK103",
          `片段里有 ${rule.display ?? `\`${rule.code}\``}：${rule.reason}`,
        ),
      );
    }
  }

  const open = (scan.match(/\{/g) ?? []).length;
  const close = (scan.match(/\}/g) ?? []).length;
  if (open !== close) {
    errors.push(
      error(
        "TK010",
        `片段的括号不平衡（\`{\` ${open} 个，\`}\` ${close} 个）—— ` +
          `不平衡的片段会把后面所有规则一起吃掉`,
      ),
    );
  }

  const commaHits = commaSeparatedProperties(scan);
  if (commaHits.length > 0) {
    errors.push(
      error(
        "TK013",
        `片段里用逗号分隔了声明（\`… ,${commaHits[0]}: …\`）。` +
          `分隔声明要用 \`;\` —— 用逗号会让整段变成**一个无效声明**，` +
          `浏览器把它全部丢掉，图案根本不会出现（而括号是配平的，别的检查看不出来）`,
      ),
    );
  }

  return { errors, warnings };
}

/**
 * 给一个写错的令牌名找找近亲。
 *
 * 两个信号，因为它们各抓一类错误：
 *
 * 1. **编辑距离**（差一两个字母）：`--chat-bubble-user-b` → `--chat-bubble-user-bg`；
 * 2. **同族前缀**：`--bg-background` → `--bg-app / --bg-surface / …`。
 *    第二类没有它不行 —— 而且它恰恰是**模型最容易犯的错**
 *    （它会把"背景"直译成 background，而这里的名字是 `--bg-*`）。
 *    编辑距离对这类名字完全无能为力（差太远），于是提示会退化成"什么都没有"，
 *    而"没有提示"会让写的人以为这个名字是对的。
 */
/**
 * 给一个写错的令牌名找找近亲。
 *
 * 三路信号，因为写错的方式有三类：
 *
 * 1. **编辑距离**（差一两个字母）：`--chat-bubble-user-b` → `--chat-bubble-user-bg`；
 * 2. **同族前缀**：`--bg-background` → `--bg-app / --bg-surface / …`。
 *    没有它不行 —— 它恰恰是**模型最容易犯的错**（把"背景"直译成 background，
 *    而这里的名字是 `--bg-*`），而编辑距离对这类名字完全无能为力；
 * 3. **整段包含**：`--danger-bg` → `--state-danger-soft`。
 *    真实运行里模型就是这么写的：它把"危险"那套名字记成了 `--danger-*`，
 *    而实际叫 `--state-danger-*`。前两路都抓不到（差太远、前缀也不同），
 *    于是提示退化成"什么都没有" —— 而"没有提示"会让写的人以为这个名字是对的。
 *
 * 三路都不命中就返回空串（**不乱猜**：猜错比不说更坏）。
 */
export function suggest(name, vocabulary, limit = 2) {
  const target = name.toLowerCase();
  const bare = target.replace(/^--/, "");
  const segments = bare.split("-").filter((part) => part.length >= 4);
  const scored = [];
  for (const candidate of vocabulary) {
    const lower = candidate.toLowerCase();
    const distance = editDistance(target, lower);
    if (distance <= limit) {
      scored.push([distance, 0, candidate]);
      continue;
    }
    const shared = commonPrefixLength(
      bare,
      lower.replace(/^--/, ""),
    );
    if (shared >= 3) {
      scored.push([100, 100 - shared, candidate]);
      continue;
    }
    // 整段包含：错名里最长的那一段出现在候选名里
    let longest = 0;
    for (const part of segments) {
      if (lower.includes(part)) longest = Math.max(longest, part.length);
    }
    if (longest >= 4) scored.push([200, 100 - longest, candidate]);
  }
  if (scored.length === 0) return "";
  scored.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2].localeCompare(b[2]));
  return `你想找的可能是 ${scored.slice(0, 3).map(([, , n]) => `\`${n}\``).join(" / ")}`;
}

function commonPrefixLength(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  return i;
}

function editDistance(a, b) {
  const rows = a.length + 1;
  const cols = b.length + 1;
  let prev = Array.from({ length: cols }, (_, j) => j);
  for (let i = 1; i < rows; i += 1) {
    const curr = [i, ...Array(cols - 1).fill(0)];
    for (let j = 1; j < cols; j += 1) {
      curr[j] = Math.min(
        prev[j] + 1,
        curr[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    prev = curr;
  }
  return prev[cols - 1];
}

// --------------------------------------------------------------------------- //
// 整个 themes/ 目录
// --------------------------------------------------------------------------- //

/** 读出 `themes/*.json` 的原始内容（不校验）。 */
export function readPacks(kitRoot = KIT_ROOT) {
  const dir = join(kitRoot, "themes");
  const files = readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  return files.map((file) => {
    const path = join(dir, file);
    const filenameId = file.replace(/\.json$/, "");
    let raw = null;
    let parseError = null;
    try {
      raw = JSON.parse(readFileSync(path, "utf8"));
    } catch (cause) {
      parseError = error("TK001", `${file} 不是合法 JSON：${cause.message}`);
    }
    return { file, path, filenameId, raw, parseError };
  });
}

/** 校验全部主题包（先收集 id，再逐个校验，好让 extends 能被解析）。 */
export function validateAll(kitRoot = KIT_ROOT) {
  const schema = loadSchema(kitRoot);
  const raws = readPacks(kitRoot);
  const knownIds = new Set(
    raws.map((r) => r.filenameId).filter((id) => THEME_ID_PATTERN.test(id)),
  );

  const byId = new Map();
  for (const entry of raws) {
    if (entry.raw && typeof entry.raw === "object" && typeof entry.raw.id === "string") {
      byId.set(entry.raw.id, entry.raw);
    }
  }

  return raws.map((entry) => {
    if (entry.parseError) {
      return { ...entry, errors: [entry.parseError], warnings: [], pack: null };
    }
    const base =
      typeof entry.raw?.extends === "string" ? (byId.get(entry.raw.extends) ?? null) : null;
    const result = validatePack(entry.raw, {
      schema,
      filenameId: entry.filenameId,
      knownIds,
      base,
    });
    return { ...entry, ...result };
  });
}

// --------------------------------------------------------------------------- //
// CLI
// --------------------------------------------------------------------------- //

function formatResult(result) {
  const lines = [];
  const label = result.raw?.name ? `${result.raw.name}（${result.filenameId}）` : result.file;
  if (result.errors.length === 0) {
    lines.push(`✅ ${label}`);
  } else {
    lines.push(`❌ ${label}`);
  }
  for (const item of result.errors) lines.push(`   [${item.code}] ${item.message}`);
  for (const item of result.warnings) lines.push(`   ⚠ [${item.code}] ${item.message}`);
  return lines.join("\n");
}

/** CLI 入口。返回退出码：0 = 全部通过；1 = 有拒绝项；2 = 检查器本身没跑起来。 */
export function main(argv = process.argv.slice(2)) {
  const kitRoot = KIT_ROOT;
  let asJson = false;
  const paths = [];
  for (const arg of argv) {
    if (arg === "--json") asJson = true;
    else if (arg === "--all" || arg === ".") continue;
    else paths.push(arg);
  }

  let results;
  try {
    results = validateAll(kitRoot);
  } catch (cause) {
    process.stderr.write(`[校验器没能跑起来] ${cause.message}\n`);
    return 2;
  }

  if (paths.length > 0) {
    const wanted = new Set(
      paths.map((p) => p.replace(/\\/g, "/").split("/").pop().replace(/\.json$/, "")),
    );
    results = results.filter((r) => wanted.has(r.filenameId));
  }

  if (asJson) {
    process.stdout.write(
      `${JSON.stringify(
        results.map((r) => ({
          id: r.filenameId,
          errors: r.errors,
          warnings: r.warnings,
        })),
        null,
        2,
      )}\n`,
    );
  } else {
    for (const result of results) process.stdout.write(`${formatResult(result)}\n`);
    const failed = results.filter((r) => r.errors.length > 0).length;
    const warned = results.filter((r) => r.errors.length === 0 && r.warnings.length > 0).length;
    process.stdout.write(
      `\n共 ${results.length} 个主题包：${results.length - failed} 个通过` +
        `${warned ? `（其中 ${warned} 个有提醒）` : ""}${failed ? `，${failed} 个被拒绝` : ""}\n`,
    );
    if (warned) {
      process.stdout.write(
        "提醒不是错误：对比度偏低仍然能跑，只是有人会看不清 —— 这正是它需要被说出来的原因。\n",
      );
    }
  }

  return results.some((r) => r.errors.length > 0) ? 1 : 0;
}

if (process.argv[1] && process.argv[1].endsWith("validate.mjs")) {
  process.exitCode = main();
}
