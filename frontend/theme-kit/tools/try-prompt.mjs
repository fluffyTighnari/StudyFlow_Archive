/**
 * 拿真模型试一次「文档 + 需求 → 主题包」。**这是 PROMPT.md 的验收工具。**
 *
 * ```
 * node tools/try-prompt.mjs --requirement "我要一个像旧宣纸的暖白主题，圆角小一点" \
 *                           --id rice-paper --model qwen3.5:9b
 * ```
 *
 * ## 为什么要真跑
 *
 * 「写个 prompt，模型就能生成符合需求的插件」这句话有两种验证方式：
 * 一种是读一遍 prompt 觉得写得挺清楚；另一种是**真的让模型生成一次，
 * 然后拿校验器去验**。前者会在第一次真用的时候碎掉 —— 而碎掉的方式很安静：
 * 主题包装上了，只是有一块地方没变。
 *
 * 所以这个脚本把三件事串起来：拼 prompt → 真调模型 → **跑校验器**。
 * 校验器是唯一的裁判：模型说"我跑过了"不算。
 *
 * 生成的文件会留在 `themes/<id>.json`（方便你看它到底写了什么），
 * 但它**不会被自动采纳** —— 加进 THEME_PACKS 是人的动作。
 */

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { KIT_ROOT } from "../src/schema.mjs";
import { validateAll } from "../src/validate.mjs";

function parseArgs(argv) {
  const args = {
    requirement: "",
    id: "",
    model: "qwen3.5:9b",
    baseUrl: "http://127.0.0.1:11434",
    timeout: 600,
    rounds: 3,
    think: true,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--requirement") args.requirement = argv[++i];
    else if (key === "--id") args.id = argv[++i];
    else if (key === "--model") args.model = argv[++i];
    else if (key === "--base-url") args.baseUrl = argv[++i];
    else if (key === "--timeout") args.timeout = Number(argv[++i]);
    else if (key === "--rounds") args.rounds = Number(argv[++i]);
    else if (key === "--no-think") args.think = false;
    else throw new Error(`不认识的参数：${key}`);
  }
  return args;
}

/** 拼出真正发给模型的提示词：PROMPT.md 的两处占位被填上，末尾接上说明书全文。 */
export function buildPrompt(requirement, id) {
  const template = readFileSync(join(KIT_ROOT, "PROMPT.md"), "utf8");
  const spec = readFileSync(join(KIT_ROOT, "THEME_SPEC.md"), "utf8");
  return template
    .replace("（把 `THEME_SPEC.md` 的全文粘在这里。）", spec)
    .replace("【需求】", requirement)
    .replaceAll("【id】", id);
}

/**
 * 从模型的回复里抠出第一个完整的 JSON 对象。
 *
 * 需求是"只输出 JSON"，但真实模型常常会先来一句"好的，这是主题包："，
 * 或者用 ``` 包起来。**这里刻意宽松** —— 提示词已经要求过了，
 * 而这一层的职责是"把它写的东西拿到手"，不是"考它格式"。
 * 真正严格的是校验器。
 */
export function extractJson(text) {
  const fenced = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  const body = fenced ? fenced[1] : text;
  const start = body.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < body.length; i += 1) {
    const ch = body[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return body.slice(start, i + 1);
    }
  }
  return null;
}

/**
 * 问一次模型（流式，边收边显示进度）。
 *
 * **用流式不是为了好看**：非流式时 Ollama 要等整个生成结束才发响应头，
 * 而 undici 默认的 headersTimeout 是 300 秒 —— 一个 6 千 token 的 prompt
 * 在慢机器上很容易超时，报出来是 `UND_ERR_HEADERS_TIMEOUT`，
 * 看起来像"服务连不上"，其实是"它还在算"。
 */
async function askModel(messages, args) {
  const response = await fetch(`${args.baseUrl}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      model: args.model,
      messages,
      stream: true,
      think: args.think,
      options: { num_ctx: 32768 },
    }),
    signal: AbortSignal.timeout(args.timeout * 1000),
  });
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}：${await response.text()}`);
  }

  let content = "";
  let thinking = "";
  const meta = {};
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done: finished } = await reader.read();
    if (finished) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
      if (!line) continue;
      let frame;
      try {
        frame = JSON.parse(line);
      } catch {
        continue;
      }
      if (frame.error) throw new Error(`模型服务报错：${frame.error}`);
      if (frame.message?.content) content += frame.message.content;
      if (frame.message?.thinking) thinking += frame.message.thinking;
      if (frame.done) {
        for (const key of ["done_reason", "eval_count", "prompt_eval_count"]) {
          if (key in frame) meta[key] = frame[key];
        }
      }
      process.stderr.write(`\r  ${content.length} 字符…`);
    }
  }
  process.stderr.write(
    `\r  正文 ${content.length} 字符，思考 ${thinking.length} 字符；` +
      `done_reason=${meta.done_reason ?? "?"} eval_count=${meta.eval_count ?? "?"}\n`,
  );
  return content;
}

/** 校验一个主题包文件，返回 {errors, warnings}（只针对这一个 id）。 */
function validateOne(id) {
  return (
    validateAll(KIT_ROOT).find((r) => r.filenameId === id) ?? {
      errors: [{ code: "TK000", message: "校验器没找到这个文件" }],
      warnings: [],
    }
  );
}

/** 把校验器的原话拼成一条"返工"消息。**原话照抄**，不替它总结。 */
function repairMessage(previousJson, errors) {
  const lines = errors.map((e) => `- [${e.code}] ${e.message}`).join("\n");
  return (
    "校验器拒绝了你的主题包。以下是它的原话（**逐条照着改**）：\n\n" +
    lines +
    "\n\n请只输出修正后的完整 JSON，不要解释。特别注意：" +
    "令牌名要逐字来自文档第 2 节的表格；每个令牌要放在它所属的那一层。\n\n" +
    // 这一条是真实运行逼出来的：模型把放错层的令牌**删掉**来让校验通过，
    // 于是"正文大一号""圆角小一点"两条需求一起消失了 —— 校验通过，
    // 需求没了。删掉一个令牌永远比移动它容易，所以必须明说。
    "**放错层的令牌要把它移到正确的层，不要删掉它。**" +
    "那些令牌是你用来满足用户需求的：删掉它们，校验会通过，但需求没有实现 —— " +
    "而用户看到的是一份「通过了」的报告。\n\n" +
    "你上一次给的是：\n" +
    previousJson
  );
}

/**
 * 两层令牌的差集：上一轮有、这一轮没有的令牌。
 *
 * **两边都必须能解析才比。** 上一版没做这个检查：某轮输出不是合法 JSON 时，
 * 差集会把上一轮的全部令牌都算成"被删掉了"，于是打印出
 * "⚠ 这一轮比上一轮少了 5 个令牌，它们可能正是需求的落点" ——
 * 而真相是那一轮连 JSON 都没写对。一句听起来合理但方向完全错的解释，
 * 比不解释更坏。
 */
function droppedTokens(before, after) {
  const collect = (json) => {
    const set = new Set();
    try {
      const tokens = JSON.parse(json).tokens;
      if (tokens === null || typeof tokens !== "object") return null;
      for (const layer of Object.values(tokens)) {
        if (layer === null || typeof layer !== "object" || Array.isArray(layer)) continue;
        // **只认 `--` 开头的键。** 上一版把 `Object.keys` 全部收下，
        // 而某个模型把 `tokens.semantic` 写成了数组 —— 于是"少了哪些令牌"
        // 打出来是「0、1、2、…、221」。一句读起来像结论、其实只是数组下标的话，
        // 比不打印更坏。
        for (const name of Object.keys(layer)) {
          if (name.startsWith("--")) set.add(name);
        }
      }
    } catch {
      return null;
    }
    return set;
  };
  const previous = collect(before);
  const current = collect(after);
  if (previous === null || current === null) return null;
  return [...previous].filter((name) => !current.has(name));
}

async function main(argv) {
  const args = parseArgs(argv);
  if (!args.requirement || !args.id) {
    process.stderr.write(
      '用法：node tools/try-prompt.mjs --requirement "…" --id my-theme [--model qwen3.5:9b] [--rounds 3]\n',
    );
    return 2;
  }

  const prompt = buildPrompt(args.requirement, args.id);
  process.stdout.write(
    `模型：${args.model}｜需求：${args.requirement}\n` +
      `提示词 ${prompt.length} 字符｜最多 ${args.rounds} 轮（每一轮都由校验器判）\n\n`,
  );

  const messages = [{ role: "user", content: prompt }];
  const path = join(KIT_ROOT, "themes", `${args.id}.json`);
  let lastJson = null;

  for (let round = 1; round <= args.rounds; round += 1) {
    process.stdout.write(`第 ${round} 轮：\n`);
    let content;
    try {
      content = await askModel(messages, args);
    } catch (cause) {
      // **不替读者判断原因。** 上一版这里写的是"这台机器上没有 Ollama 时…"，
      // 而那次失败时 Ollama 明明是活着的（刚跑完一次生成）—— 一句没被验证过的
      // 解释，和模型编造"我跑过校验器"是同一种东西。这里只报事实（错误码），
      // 把"可能是哪一种"列成清单，让读者自己对着 code 判断。
      const code = cause?.cause?.code ?? cause?.name ?? "unknown";
      const detail = cause?.cause?.message ?? cause?.message ?? String(cause);
      process.stderr.write(
        `调不动模型：${code}（${detail}）\n` +
          `常见原因（**这条消息不替你判断是哪一个**，以上面那个 code 为准）：\n` +
          `  · ECONNREFUSED —— 服务没起，或端口不对\n` +
          `  · TimeoutError —— 超过 --timeout（现在是 ${args.timeout}s）\n` +
          `  · ECONNRESET —— 连接被中途断开（模型正在换入/换出时可能发生，重试即可）\n` +
          `如果最终没能验证 PROMPT.md，请如实记为「没有验证」，而不是「看起来没问题」。\n`,
      );
      return 2;
    }

    const json = extractJson(content);
    if (!json) {
      process.stdout.write("  模型没有给出任何 JSON，这一轮结束。\n");
      process.stdout.write(content.slice(0, 600) + "\n");
      return 1;
    }
    writeFileSync(path, `${json}\n`, "utf8");

    // **报出这一轮删掉了什么。** 「通过校验」和「满足需求」是两件事，
    // 而删掉一个令牌是让校验通过的最省事办法 —— 真实运行里模型就是这么干的，
    // 于是两条需求无声消失。工具不能替人判断删得对不对，但必须把它说出来。
    if (lastJson !== null) {
      const dropped = droppedTokens(lastJson, json);
      if (dropped === null) {
        process.stdout.write(
          "  （上一轮或这一轮的 JSON 不合法，轮间对比不成立，跳过「少了哪些令牌」这一步）\n",
        );
      } else if (dropped.length > 0) {
        process.stdout.write(
          `  ⚠ 这一轮比上一轮少了 ${dropped.length} 个令牌：${dropped.join("、")}\n` +
            `     它们可能正是需求的落点 —— 请自己看一眼需求还在不在。\n`,
        );
      }
    }
    lastJson = json;

    const result = validateOne(args.id);
    if (result.errors.length === 0) {
      process.stdout.write(`  ✅ 通过校验（${json.length} 字符，已写入 themes/${args.id}.json）\n`);
      for (const item of result.warnings) {
        process.stdout.write(`     ⚠ [${item.code}] ${item.message}\n`);
      }
      process.stdout.write(`\n共 ${round} 轮。\n`);
      if (round > 1) {
        process.stdout.write(
          "注意：这个包是**返工之后**拿到的 —— 说明一次通过不了，" +
            "返工循环（而不是提示词里那句「请自己检查」）才是它通过的原因。\n",
        );
      }
      return 0;
    }

    process.stdout.write(`  ❌ 被拒绝（${result.errors.length} 条）：\n`);
    for (const item of result.errors) {
      process.stdout.write(`     [${item.code}] ${item.message}\n`);
    }
    messages.push({ role: "assistant", content });
    messages.push({ role: "user", content: repairMessage(json, result.errors) });
  }

  process.stdout.write(
    `\n${args.rounds} 轮都没通过。最后一次的内容留在 themes/${args.id}.json（不会被采纳）。\n` +
      `**这算「没有验证通过」，不算「大概能用」。**\n`,
  );
  return 1;
}

process.exitCode = await main(process.argv.slice(2));
