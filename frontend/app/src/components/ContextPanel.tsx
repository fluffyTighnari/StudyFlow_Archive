import { describeError } from "../api/client";
import { useContextPack } from "../api/queries";
import type { StepRead } from "../api/types";

/**
 * 讨论上下文包。
 *
 * 为什么把"模型看到了什么"摆到界面上：这个系统的核心主张是
 * **同样的输入得到同样的建议**，而"输入"就是这个包。它不展示的话，
 * 用户没有任何办法回答"它凭什么这么说" —— 只能选择相信或不相信。
 *
 * 两个细节必须是**可核查的**（M9 §6 的义务）：
 *
 * 1. `precedence_order`（谁压谁）原样列出，不重新排序、不美化；
 * 2. 包里有什么、某一块是空的，都照实显示 —— 空的区块显示"（无）"，
 *    而不是悄悄省略那一行。省略会让人以为"它没考虑硬约束"，
 *    而真相是"这次没有硬约束"。
 */

const SECTION_LABEL: Record<string, string> = {
  session_goal: "会话目标",
  hard_constraints: "硬约束",
  soft_preferences: "软偏好",
  stage: "本次暂存",
  checkpoints: "检查点",
  baseline: "全局基线",
  evidence: "证据",
  strategies: "参考策略",
  step_history: "这一步的历史",
  situation: "当前状态",
};

export function ContextPanel({ sessionId, step }: { sessionId: string; step: StepRead | null }) {
  const query = useContextPack(sessionId, step?.step_id ?? null);

  if (step === null) {
    return (
      <section className="panel tk-surface" aria-label="上下文包">
        <header className="panel__head">
          <span className="panel__title">它会看到什么</span>
        </header>
        <p className="empty-note">点计划里的任意一步，这里会显示为它准备的那一份上下文。</p>
      </section>
    );
  }

  return (
    <section className="panel tk-surface" aria-label="上下文包">
      <header className="panel__head">
        <span className="panel__title">它会看到什么</span>
        <span className="panel__hint">计划第 {query.data?.plan_version ?? "?"} 版</span>
      </header>

      {query.isPending && <p className="empty-note">读取中…</p>}
      {query.isError && (
        <p className="error-note">读不到上下文包：{describeError(query.error)}</p>
      )}

      {query.data !== undefined && (
        <>
          <div className="definition-row">
            <span className="definition-row__label">优先级（谁压谁）</span>
            <span className="definition-row__value">
              {(query.data.precedence_order ?? []).join(" › ") || "（未声明）"}
            </span>
          </div>

          {Object.entries(SECTION_LABEL).map(([key, label]) => {
            const value = query.data[key as keyof typeof query.data];
            const count = Array.isArray(value) ? value.length : null;
            return (
              <details key={key}>
                <summary className="definition-row__label">
                  {label}
                  {count === null ? "" : `（${count} 条）`}
                </summary>
                {count === 0 || value === null || value === undefined ? (
                  <p className="empty-note">（无）</p>
                ) : (
                  <pre className="thinking">{JSON.stringify(value, null, 1)}</pre>
                )}
              </details>
            );
          })}
        </>
      )}
    </section>
  );
}
