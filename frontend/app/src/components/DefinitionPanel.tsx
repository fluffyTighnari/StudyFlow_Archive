import { useEffect, useState } from "react";

import type { DefinitionField, InputRead } from "../api/types";

/**
 * 任务定义区。
 *
 * 三条规则，每一条都在界面上看得见：
 *
 * 1. **每个字段都标「你说的 / 系统推断的 / 你还没说」**。
 *    这是这个系统最核心的主张 —— 一旦两者混在一起显示，
 *    用户就再也分不清"这是我说的"还是"系统替我编的"。
 *    `inferred` 用虚线边框 + 专门的徽章，**不只靠颜色**（色盲也能分辨）。
 * 2. **改动要带理由**（`reason` 进审计）。理由为空也能改，但界面会提示
 *    "这次改动不会有理由记录" —— 而不是假装记了。
 * 3. **`expected_version` 必须回传**：并发改同一个字段时后端会拒绝，
 *    界面把拒绝原话显示出来，而不是静默重试（静默重试会吃掉别人的修改）。
 */

const FIELD_LABEL: Record<DefinitionField, string> = {
  goal: "目标",
  why: "为什么",
  starting_point: "起点",
  success_criteria: "怎么算完成",
  constraints: "约束",
  task_type: "任务类型",
};

const FIELD_ORDER: DefinitionField[] = [
  "goal",
  "why",
  "starting_point",
  "success_criteria",
  "constraints",
  "task_type",
];

const CONFIDENCE_LABEL = {
  stated: "你说的",
  inferred: "系统推断",
  missing: "你还没说",
} as const;

export function DefinitionPanel({
  input,
  saving,
  error,
  onSave,
}: {
  input: InputRead;
  saving: boolean;
  error: string | null;
  onSave: (field: DefinitionField, value: string, reason: string | null) => void;
}) {
  const [editing, setEditing] = useState<DefinitionField | null>(null);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");

  useEffect(() => {
    // 换会话时收起编辑框 —— 不然会把 A 会话的半句话写进 B 会话。
    setEditing(null);
    setDraft("");
    setReason("");
  }, [input.session_id]);

  const confidenceClass =
    input.intent_confidence === "stated"
      ? "badge badge--stated"
      : input.intent_confidence === "inferred"
        ? "badge badge--inferred"
        : "badge badge--missing";

  return (
    <section className="panel tk-surface" aria-label="任务定义区">
      <header className="panel__head">
        <span className="panel__title">任务定义</span>
        <span className="panel__hint">v{input.version}</span>
      </header>

      <div className="definition-row">
        <span className="definition-row__label">
          整体来源
          <span className={confidenceClass}>{CONFIDENCE_LABEL[input.intent_confidence]}</span>
        </span>
        {input.intent_raw !== null && (
          <span className="definition-row__value definition-row__value--empty">
            原始说法：{input.intent_raw}
          </span>
        )}
      </div>

      {FIELD_ORDER.map((field) => {
        const value = input[field];
        const isEditing = editing === field;
        return (
          <div className="definition-row" key={field}>
            <span className="definition-row__label">
              {FIELD_LABEL[field]}
              {value === null && <span className="badge badge--missing">未填</span>}
            </span>

            {isEditing ? (
              <div className="definition-row__editor">
                <textarea
                  className="field-input tk-focusable"
                  rows={3}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  aria-label={FIELD_LABEL[field]}
                />
                <input
                  className="field-input tk-focusable"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="改这一项的理由（会进审计；不填也能改）"
                  aria-label="改动理由"
                />
                <div className="chip-row">
                  <button
                    type="button"
                    className="button tk-focusable"
                    disabled={saving}
                    onClick={() => {
                      onSave(field, draft, reason.trim() === "" ? null : reason.trim());
                      setEditing(null);
                    }}
                  >
                    {saving ? "保存中" : "保存"}
                  </button>
                  <button
                    type="button"
                    className="button button--quiet tk-focusable"
                    onClick={() => setEditing(null)}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <>
                <span
                  className={
                    value === null
                      ? "definition-row__value definition-row__value--empty"
                      : "definition-row__value"
                  }
                >
                  {value ?? "（还没填）"}
                </span>
                <div className="chip-row">
                  <button
                    type="button"
                    className="button button--quiet tk-focusable"
                    onClick={() => {
                      setEditing(field);
                      setDraft(value ?? "");
                      setReason("");
                    }}
                  >
                    改
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}

      {error !== null && <p className="error-note">{error}</p>}
    </section>
  );
}
