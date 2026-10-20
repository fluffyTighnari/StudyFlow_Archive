"""侧栏的三处改动：文件夹开合图标、生成中的动态提示、右侧上次更新时间、左下角设置入口。

## 为什么要用脚本改

`Sidebar.tsx` 有八百多行，而这些改动散在五六个地方（导入、props、文件夹行、
会话行、底部）。
整文件重写会把没打算动的部分也带进 diff；这里只做锚点替换，替换不上就报错
（`assert` —— 悄悄跳过会让"改了但没生效"变成一次误报）。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\src\components\Sidebar.tsx"
)
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, *, label: str, count: int = 1) -> str:
    global text
    found = text.count(old)
    assert found == count, f"{label}：锚点出现 {found} 次（期望 {count}）"
    text = text.replace(old, new, count)
    print(f"✅ {label}")
    return text


# ---------------------------------------------------------------- 图标导入 ==
text = swap(
    """import {
  ArchiveIcon,
  CaretIcon,
  ChatIcon,
  ChevronRightIcon,
  CloseIcon,
  FolderIcon,
  MoreIcon,
  PanelLeftIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SlidersIcon,
  TrashIcon,
} from "./icons";""",
    """import {
  ArchiveIcon,
  CaretIcon,
  ChatIcon,
  ChevronRightIcon,
  CloseIcon,
  FolderIcon,
  FolderOpenIcon,
  GearIcon,
  MoreIcon,
  PanelLeftIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SlidersIcon,
  TrashIcon,
} from "./icons";""",
    label="图标导入",
)

# ------------------------------------------------------------------- props ==
text = swap(
    """  onCreateBlank,
  onCollapse,
  folderActions,
  sessionActions,
}: {""",
    """  onCreateBlank,
  onCollapse,
  onOpenSettings,
  /** 此刻正在生成回复的那个会话（`null` = 没有）。它在左边显示一个动态点。 */
  streamingSessionId,
  folderActions,
  sessionActions,
}: {""",
    label="Sidebar props",
)

text = swap(
    """  /** 新建 未命名文件夹 + 未命名会话，并直接聚焦新对话。 */
  onCreateBlank: () => void;
  onCollapse: () => void;""",
    """  /** 新建 未命名文件夹 + 未命名会话，并直接聚焦新对话。 */
  onCreateBlank: () => void;
  onCollapse: () => void;
  onOpenSettings: () => void;
  streamingSessionId: string | null;""",
    label="Sidebar props 类型",
)

# ------------------------------------------------------- 文件夹开合图标 ==
text = swap(
    """          <button
            type="button"
            className="dsh-folderChevronButton dsh-focusable"
            aria-expanded={open}
            aria-label={open ? `收起「${folder.name}」` : `展开「${folder.name}」`}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFolder(folder.folder_id);
            }}
          >
            <span className="dsh-folderChevron" data-open={open || undefined}>
              <ChevronRightIcon />
            </span>
          </button>""",
    """          {/*
            开合用**图标本身**表示（封口夹子 → 张开的夹子），不再只是箭头旋转：
            箭头说的是"这里能点"，图标说的是"它现在是开的"。
            按钮仍然是那个可聚焦的键 —— 键盘用户走的是它，不是图标。
          */}
          <button
            type="button"
            className="dsh-folderToggle dsh-focusable"
            aria-expanded={open}
            aria-label={open ? `收起「${folder.name}」` : `展开「${folder.name}」`}
            title={open ? "收起" : "展开"}
            onClick={(event) => {
              event.stopPropagation();
              onToggleFolder(folder.folder_id);
            }}
          >
            {open ? <FolderOpenIcon /> : <FolderIcon />}
          </button>""",
    label="文件夹开合图标",
)

# --------------------------------------------------------- 会话行：时间/动态 ==
text = swap(
    """  const sessionRow = (session: SessionRead, showFolder: boolean) => {
    const editing = editFor?.kind === "session" && editFor.id === session.session_id;
    const open = menuFor?.kind === "session" && menuFor.id === session.session_id;
    return (
      <div className="dsh-rowWrap" key={session.session_id}>
        {editing ? (
          renderRename({ kind: "session", id: session.session_id })
        ) : (
          <button
            type="button"
            className="dsh-sessionRow dsh-focusable"
            aria-current={session.session_id === activeSessionId}
            onClick={() => onSelectSession(session)}
            title={
              showFolder
                ? `${session.title}（${folderNameOf(folders, session.folder_id)}）`
                : session.title
            }
          >
            <span
              className="dsh-stateDot"
              data-state={session.state}
              title={STATE_LABEL[session.state] ?? session.state}
              aria-label={STATE_LABEL[session.state] ?? session.state}
            />
            <span className="dsh-sessionTitle">{session.title}</span>
            {showFolder && (
              <span className="dsh-sessionFolder">
                {folderNameOf(folders, session.folder_id)}
              </span>
            )}
          </button>
        )}""",
    """  const sessionRow = (session: SessionRead, showFolder: boolean) => {
    const editing = editFor?.kind === "session" && editFor.id === session.session_id;
    const open = menuFor?.kind === "session" && menuFor.id === session.session_id;
    const streaming = streamingSessionId === session.session_id;
    const stateLabel = STATE_LABEL[session.state] ?? session.state;
    /* 悬停时的那句提示：状态 + 是否正在生成 + 上次更新。一行说清三件事。 */
    const rowHint =
      `${session.title} · ${stateLabel}` +
      (streaming ? " · 正在生成回复…" : "") +
      ` · 上次更新 ${sinceLabel(session.updated_at)}` +
      (showFolder
        ? ` · 文件夹：${folderNameOf(folders, session.folder_id)}`
        : "");
    return (
      <div className="dsh-rowWrap" key={session.session_id}>
        {editing ? (
          renderRename({ kind: "session", id: session.session_id })
        ) : (
          <button
            type="button"
            className="dsh-sessionRow dsh-focusable"
            aria-current={session.session_id === activeSessionId}
            data-streaming={streaming || undefined}
            onClick={() => onSelectSession(session)}
            title={rowHint}
          >
            {/*
              左边那个点：正在生成时**动起来**（CSS 动画 + `aria-live` 之外的可读文本）。
              不是生成中的时候，它就是原来那个状态点。
            */}
            <span
              className="dsh-stateDot"
              data-state={session.state}
              data-pulse={streaming || undefined}
              aria-label={streaming ? `${stateLabel} · 正在生成回复` : stateLabel}
            />
            <span className="dsh-sessionTitle">{session.title}</span>
            {showFolder && (
              <span className="dsh-sessionFolder">
                {folderNameOf(folders, session.folder_id)}
              </span>
            )}
            {/*
              上次更新时间：平时藏着，悬停/聚焦/正在生成时出现。
              一直显示会让这一列变成一串日期，而用户扫的是标题。
            */}
            <span className="dsh-sessionTime" data-streaming={streaming || undefined}>
              {streaming ? "生成中…" : sinceLabel(session.updated_at)}
            </span>
          </button>
        )}""",
    label="会话行（动态提示 + 上次更新时间）",
)

# ------------------------------------------------------- 左下角：设置入口 ==
text = swap(
    """      {/*
        左下角：DSH 这里是设置。**这个产品不做设置**，但这一层留着 ——
        它撑着底部的留白，而"底部什么都没有"本身就是一个视觉决定。
      */}
      <div className="dsh-footArea" />""",
    """      {/*
        左下角：设置。DSH 那一页是整页覆盖的对话框，这里照做
        （`SettingsDialog`）。它是这一层唯一的入口 —— 放别的东西进来，
        "设置在哪"就又变成一次寻找。
      */}
      <div className="dsh-footArea">
        <button
          type="button"
          className="dsh-footButton dsh-focusable"
          onClick={onOpenSettings}
          title="设置"
          aria-label="设置"
        >
          <GearIcon />
          <span>设置</span>
        </button>
      </div>""",
    label="左下角设置入口",
)

PATH.write_text(text, encoding="utf-8")
print(f"\n写完（{len(text.splitlines())} 行）")
