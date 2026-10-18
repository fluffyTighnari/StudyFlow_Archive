import type { SessionRead } from "../api/types";

const STATE_LABEL: Record<string, string> = {
  draft: "草稿",
  active: "进行中",
  review: "复盘",
  completed: "已完成",
  abandoned: "已放弃",
};

const TARGET_LABEL: Record<string, string> = {
  active: "开始",
  review: "去复盘",
  completed: "完成",
  abandoned: "放弃",
};

/**
 * 顶栏：会话状态 + 状态机允许的转移。
 *
 * **按钮只从 `allowed_transitions` 生成**，界面不自己判断"应该能去哪"。
 * 这是状态机唯一可信的出口：若界面自己列按钮，后端拒绝时就变成
 * "点了没反应"，而用户会以为界面坏了 —— 而不是"这一步不允许"。
 */
export function TopBar({
  session,
  busy,
  error,
  onTransition,
}: {
  session: SessionRead;
  busy: boolean;
  error: string | null;
  onTransition: (target: string) => void;
}) {
  return (
    <header className="app-topbar">
      <div className="app-topbar__title">
        <span className="app-topbar__name">{session.title}</span>
        <span className="panel__hint">
          {STATE_LABEL[session.state] ?? session.state} · 会话 v{session.version}
        </span>
      </div>
      <div className="app-topbar__actions">
        {session.allowed_transitions.map((target) => (
          <button
            key={target}
            type="button"
            className={
              target === "abandoned"
                ? "button button--danger tk-focusable"
                : target === "completed"
                  ? "button button--quiet tk-focusable"
                  : "button tk-focusable"
            }
            disabled={busy}
            onClick={() => onTransition(target)}
          >
            {TARGET_LABEL[target] ?? target}
          </button>
        ))}
        {error !== null && <span className="error-note">{error}</span>}
      </div>
    </header>
  );
}
