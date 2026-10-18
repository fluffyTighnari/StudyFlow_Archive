import type { CheckpointRead, PlanRead, PlanStepStatus } from "../api/types";

/**
 * 计划区。
 *
 * 这一块承载的义务最多（M9 §6）：
 *
 * - **点任意一项就能跟 LLM 讨论它** → `onFocusStep`，选中项有明确的视觉状态；
 * - **版本可见**：`version_no` 是"第几版"，`current_version` 是"现在指向哪一版"
 *   —— 两者可能不同，界面上不能只显示一个数字（那会让人以为"改了就是最新版"）；
 * - **检查点必须可见**：它是"怎么知道学会了"的唯一凭据，藏在折叠里等于没有；
 * - 状态用文字 + 形状表达（已完成有删除线），不只靠颜色。
 */

const STATUS_LABEL: Record<PlanStepStatus, string> = {
  pending: "未开始",
  doing: "进行中",
  done: "已完成",
  skipped: "已跳过",
};

const STATUS_CLASS: Record<PlanStepStatus, string> = {
  pending: "badge",
  doing: "badge badge--stated",
  done: "badge badge--stated",
  skipped: "badge badge--missing",
};

export function PlanPanel({
  plan,
  focusStepId,
  onFocusStep,
}: {
  plan: PlanRead;
  focusStepId: string | null;
  onFocusStep: (stepId: string) => void;
}) {
  const checkpointsByStep = new Map<string, CheckpointRead[]>();
  for (const checkpoint of plan.checkpoints) {
    const list = checkpointsByStep.get(checkpoint.at_step) ?? [];
    list.push(checkpoint);
    checkpointsByStep.set(checkpoint.at_step, list);
  }

  return (
    <section className="panel tk-surface" aria-label="计划区">
      <header className="panel__head">
        <span className="panel__title">计划</span>
        <span className="panel__hint">
          第 {plan.version_no} 版
          {plan.current_version !== plan.version_no && `（当前指向第 ${plan.current_version} 版）`}
        </span>
      </header>

      {plan.goal_derived !== null && (
        <p className="panel__hint">按定义推出的目标：{plan.goal_derived}</p>
      )}

      {plan.steps.length === 0 ? (
        <p className="empty-note">
          这个会话还没有计划。计划由讨论产出 —— 在右边说清楚你想怎么学，它会给出一版。
        </p>
      ) : (
        <ol className="step-list">
          {plan.steps.map((step) => {
            const checkpoints = checkpointsByStep.get(step.step_id) ?? [];
            return (
              <li key={step.step_id}>
                <button
                  type="button"
                  className={`step step--${step.status} tk-focusable`}
                  aria-current={step.step_id === focusStepId}
                  onClick={() => onFocusStep(step.step_id)}
                >
                  <span className="step__head">
                    <span className="step__seq">{step.seq}</span>
                    <span className="step__action">{step.action}</span>
                    <span className={STATUS_CLASS[step.status]}>{STATUS_LABEL[step.status]}</span>
                  </span>
                  {step.method_ref !== null && (
                    <span className="step__method">方法：{step.method_ref}</span>
                  )}
                  {step.output !== null && <span className="step__output">{step.output}</span>}
                </button>

                {checkpoints.map((checkpoint) => (
                  <div className="checkpoint" key={checkpoint.cp_id}>
                    <div className="checkpoint__head">
                      <span className="panel__title">检查点</span>
                      <span
                        className={
                          checkpoint.result === "pass"
                            ? "badge badge--stated"
                            : checkpoint.result === "fail"
                              ? "badge badge--missing"
                              : "badge"
                        }
                      >
                        {checkpoint.result === "pass"
                          ? "通过"
                          : checkpoint.result === "fail"
                            ? "未通过"
                            : "待判定"}
                      </span>
                    </div>
                    <span className="checkpoint__criteria">
                      怎么查：{checkpoint.check_method}
                    </span>
                    <span className="checkpoint__criteria">
                      算过：{checkpoint.pass_criteria}
                    </span>
                  </div>
                ))}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
