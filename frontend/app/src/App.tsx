import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { describeError } from "./api/client";
import {
  queryKeys,
  useCreateSession,
  useFolders,
  useOverview,
  useSessions,
  useTransition,
  useUpdateInput,
} from "./api/queries";
import type { DefinitionField } from "./api/types";
import { ChatPanel } from "./components/ChatPanel";
import { ContextPanel } from "./components/ContextPanel";
import { DefinitionPanel } from "./components/DefinitionPanel";
import { PlanPanel } from "./components/PlanPanel";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";

/**
 * 装配。这一层只回答两个问题：**哪一份数据放在哪一栏**，
 * 以及**一个动作之后要重读什么**。业务规则一条都不在这儿。
 *
 * 所有数据来自**一个**查询（`/state`），所以界面上永远不会出现
 * "计划是新的、对话是旧的"这种自相矛盾的画面。
 */
export function App({ initialThemeId }: { initialThemeId: string }) {
  // 初始值由入口在 render 之前算好（主题已经装上了），这里只负责"换"。
  const [themeId, setThemeId] = useState(initialThemeId);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [focusStepId, setFocusStepId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const client = useQueryClient();

  const folders = useFolders();
  const sessions = useSessions(folderId);
  const overview = useOverview(sessionId);

  const updateInput = useUpdateInput(sessionId ?? "");
  const transition = useTransition();
  const createSession = useCreateSession();

  // 省掉"进来先点两下"：默认选第一个文件夹、第一个会话。
  useEffect(() => {
    if (folderId === null && folders.data !== undefined && folders.data.length > 0) {
      setFolderId(folders.data[0]?.folder_id ?? null);
    }
  }, [folders.data, folderId]);

  useEffect(() => {
    if (sessionId === null && sessions.data !== undefined && sessions.data.length > 0) {
      setSessionId(sessions.data[0]?.session_id ?? null);
    }
  }, [sessions.data, sessionId]);

  // 换会话时清掉"针对某一步"的状态 —— 那个 step_id 属于上一个会话。
  useEffect(() => {
    setFocusStepId(null);
    setActionError(null);
  }, [sessionId]);

  const state = overview.data;
  const plan = state?.plan ?? null;

  const focusStep = useMemo(() => {
    if (focusStepId === null || plan === null) return null;
    return plan.steps.find((step) => step.step_id === focusStepId) ?? null;
  }, [focusStepId, plan]);

  const handleSaveField = useCallback(
    (field: DefinitionField, value: string, reason: string | null) => {
      if (state === undefined) return;
      setActionError(null);
      updateInput.mutate(
        {
          expected_version: state.input.version,
          reason,
          [field]: value === "" ? null : value,
        },
        { onError: (cause) => setActionError(describeError(cause)) },
      );
    },
    [state, updateInput],
  );

  const handleTransition = useCallback(
    (target: string) => {
      if (state === undefined) return;
      setActionError(null);
      transition.mutate(
        { sessionId: state.session.session_id, target, expectedVersion: state.session.version },
        { onError: (cause) => setActionError(describeError(cause)) },
      );
    },
    [state, transition],
  );

  // 一次讨论结束后：计划、定义、状态、对话都可能变了。
  // **整份快照重读** —— 不做"哪个可能变了"的猜测，猜错的表现是"改完计划界面没变"。
  const handleDiscussionFinished = useCallback(() => {
    if (sessionId === null) return;
    void client.invalidateQueries({ queryKey: queryKeys.overview(sessionId) });
    void client.invalidateQueries({ queryKey: queryKeys.sessions(folderId ?? "") });
  }, [client, sessionId, folderId]);

  const loadingError = folders.error ?? sessions.error ?? overview.error;
  const canChat = state !== undefined && state.session.state === "active";

  return (
    <div className="app-frame tk-shell">
      <Sidebar
        folders={folders.data ?? []}
        sessions={sessions.data ?? []}
        folderId={folderId}
        sessionId={sessionId}
        themeId={themeId}
        onPickFolder={setFolderId}
        onPickSession={setSessionId}
        onCreateSession={(title) => {
          if (folderId === null) return;
          createSession.mutate(
            { folderId, title },
            {
              onSuccess: (created) => setSessionId(created.session_id),
              onError: (cause) => setActionError(describeError(cause)),
            },
          );
        }}
        creating={createSession.isPending}
        createError={createSession.error === null ? null : describeError(createSession.error)}
      />

      <main className="app-main">
        {loadingError !== null && (
          <p className="error-note">数据读取失败：{describeError(loadingError)}</p>
        )}

        {state === undefined ? (
          <div className="placeholder-screen">
            <span className="placeholder-screen__title">文件夹是长期任务，会话是子任务</span>
            <span>左边选一个会话；没有就在文件夹里新建一个。</span>
          </div>
        ) : (
          <>
            <TopBar
              session={state.session}
              busy={transition.isPending}
              error={actionError}
              onTransition={handleTransition}
            />
            <div className="app-columns">
              <div className="app-column">
                <DefinitionPanel
                  input={state.input}
                  saving={updateInput.isPending}
                  error={updateInput.error === null ? null : describeError(updateInput.error)}
                  onSave={handleSaveField}
                />
                <MotivationPanel motivation={state.motivation} stage={state.stage} />
              </div>
              <div className="app-column">
                {plan !== null ? (
                  <PlanPanel
                    plan={plan}
                    focusStepId={focusStepId}
                    onFocusStep={(stepId) =>
                      setFocusStepId((current) => (current === stepId ? null : stepId))
                    }
                  />
                ) : (
                  <section className="panel tk-surface">
                    <header className="panel__head">
                      <span className="panel__title">计划</span>
                    </header>
                    <p className="empty-note">
                      还没有计划。计划是从讨论里长出来的 —— 在右边说清楚你打算怎么学，
                      它会给出第一版。
                    </p>
                  </section>
                )}
                <ContextPanel sessionId={state.session.session_id} step={focusStep} />
              </div>
              <div className="app-column">
                <ChatPanel
                  sessionId={state.session.session_id}
                  messages={state.chat}
                  focusStep={focusStep}
                  disabled={!canChat}
                  onFinished={handleDiscussionFinished}
                  onClearFocus={() => setFocusStepId(null)}
                />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

const MOTIVATION_LABEL: Record<string, string> = {
  intrinsic: "自己想学",
  extrinsic: "外部要求",
  mixed: "两者都有",
};

const STAGE_TYPE_LABEL: Record<string, string> = {
  temp_pref: "临时偏好",
  idea: "想法",
  plan_override: "计划覆盖",
};

/** 动机与暂存。暂存条目的状态**照实显示**（保留行 + 标状态，不删除）。 */
function MotivationPanel({
  motivation,
  stage,
}: {
  motivation: { type: string; strength: string; drivers: string[] | null; obstacles: string[] | null };
  stage: { item_id: string; type: string; content: string; status: string }[];
}) {
  return (
    <section className="panel tk-surface" aria-label="动机与暂存">
      <header className="panel__head">
        <span className="panel__title">动机</span>
        <span className="panel__hint">{MOTIVATION_LABEL[motivation.type] ?? motivation.type}</span>
      </header>
      <div className="definition-row">
        <span className="definition-row__label">强度</span>
        <span className="definition-row__value">{motivation.strength}</span>
      </div>
      {motivation.drivers !== null && motivation.drivers.length > 0 && (
        <div className="definition-row">
          <span className="definition-row__label">推着你的</span>
          <span className="definition-row__value">{motivation.drivers.join("、")}</span>
        </div>
      )}
      {motivation.obstacles !== null && motivation.obstacles.length > 0 && (
        <div className="definition-row">
          <span className="definition-row__label">挡着你的</span>
          <span className="definition-row__value">{motivation.obstacles.join("、")}</span>
        </div>
      )}

      <header className="panel__head">
        <span className="panel__title">本次暂存</span>
        <span className="panel__hint">{stage.length} 条</span>
      </header>
      {stage.length === 0 ? (
        <p className="empty-note">还没有暂存条目。讨论里临时提到的东西会先落在这里。</p>
      ) : (
        stage.map((item) => (
          <div className="definition-row" key={item.item_id}>
            <span className="definition-row__label">
              {STAGE_TYPE_LABEL[item.type] ?? item.type}
              <span className={item.status === "active" ? "badge badge--stated" : "badge"}>
                {item.status}
              </span>
            </span>
            <span className="definition-row__value">{item.content}</span>
          </div>
        ))
      )}
    </section>
  );
}
