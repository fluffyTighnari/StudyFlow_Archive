import { useState } from "react";

import type { FolderRead, SessionRead, SessionState } from "../api/types";
import { ThemeSwitcher } from "./ThemeSwitcher";

const STATE_LABEL: Record<SessionState, string> = {
  draft: "草稿",
  active: "进行中",
  review: "复盘",
  completed: "已完成",
  abandoned: "已放弃",
};

export function Sidebar({
  folders,
  sessions,
  folderId,
  sessionId,
  themeId,
  onPickFolder,
  onPickSession,
  onCreateSession,
  creating,
  createError,
}: {
  folders: FolderRead[];
  sessions: SessionRead[];
  folderId: string | null;
  sessionId: string | null;
  themeId: string;
  onPickFolder: (id: string) => void;
  onPickSession: (id: string) => void;
  onCreateSession: (title: string) => void;
  creating: boolean;
  createError: string | null;
}) {
  const [draftTitle, setDraftTitle] = useState("");

  return (
    <aside className="app-sidebar" aria-label="文件夹与会话">
      <div className="app-sidebar__brand">
        <span className="app-sidebar__brand-title">觅学之契</span>
        <span className="app-sidebar__brand-sub">我的学习范式 · 它会一直重新认识你</span>
      </div>

      <div className="app-sidebar__group">
        <span className="app-sidebar__group-label">
          文件夹＝长期任务（{folders.length}）
        </span>
        {folders.length === 0 && <p className="empty-note">还没有文件夹。</p>}
        {folders.map((folder) => (
          <button
            key={folder.folder_id}
            type="button"
            className="app-nav-item tk-focusable"
            aria-current={folder.folder_id === folderId}
            onClick={() => onPickFolder(folder.folder_id)}
          >
            <span>{folder.name}</span>
          </button>
        ))}
      </div>

      {folderId !== null && (
        <div className="app-sidebar__group">
          <span className="app-sidebar__group-label">会话＝子任务（{sessions.length}）</span>
          {sessions.length === 0 && <p className="empty-note">这个文件夹里还没有会话。</p>}
          {sessions.map((session) => (
            <button
              key={session.session_id}
              type="button"
              className="app-nav-item tk-focusable"
              aria-current={session.session_id === sessionId}
              onClick={() => onPickSession(session.session_id)}
            >
              <span>{session.title}</span>
              <span className="badge badge--stated">{STATE_LABEL[session.state]}</span>
            </button>
          ))}

          <form
            onSubmit={(event) => {
              event.preventDefault();
              const title = draftTitle.trim();
              if (title === "") return;
              onCreateSession(title);
              setDraftTitle("");
            }}
          >
            <div className="composer__row">
              <input
                className="field-input tk-focusable"
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
                placeholder="新会话标题（例如：背 50 个单词）"
                aria-label="新会话标题"
              />
              <button
                type="submit"
                className="button tk-focusable"
                disabled={creating || draftTitle.trim() === ""}
              >
                {creating ? "创建中" : "新建"}
              </button>
            </div>
          </form>
          {createError !== null && <p className="error-note">{createError}</p>}
        </div>
      )}

      <ThemeSwitcher current={themeId} />
    </aside>
  );
}
