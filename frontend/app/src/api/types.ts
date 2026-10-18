/**
 * 后端 DTO 的前端镜像。
 *
 * **这些类型是从真实契约抄来的，不是猜的**：`/openapi.json` 里那 113 个 schema
 * 是唯一出处（枚举值照着 `app/session_domain/enums.py` 抄）。
 * 抄错的表现很安静 —— 字段名对不上时 JSON 里就是 `undefined`，
 * 界面显示空白，而后端一切正常。
 *
 * 只镜像界面用得到的那部分；没用到的字段不写（写多了就成了第二份契约，
 * 而它没有校验，迟早会和真契约漂开）。
 */

// ---------------------------------------------------------------------------
// 会话
// ---------------------------------------------------------------------------

/** `SessionState`：draft / active / review / completed / abandoned */
export type SessionState = "draft" | "active" | "review" | "completed" | "abandoned";

export interface SessionRead {
  session_id: string;
  folder_id: string;
  title: string;
  state: SessionState;
  version: number;
  created_at: string;
  activated_at: string | null;
  completed_at: string | null;
  abandoned_at: string | null;
  /** 状态机当前允许的转移目标（界面的按钮只从这里取，不自己发明）。 */
  allowed_transitions: string[];
}

export interface FolderRead {
  folder_id: string;
  name: string;
  goal: string | null;
  c_retrieval_dims: string[] | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// 任务定义区（input）
// ---------------------------------------------------------------------------

/** `InputConfidence`：`stated` 是用户说的，`inferred` 是系统推断的，`missing` 是问了没说。 */
export type InputConfidence = "stated" | "inferred" | "missing";

export interface InputRead {
  session_id: string;
  goal: string | null;
  why: string | null;
  starting_point: string | null;
  success_criteria: string | null;
  constraints: string | null;
  task_type: string | null;
  priority: number | null;
  intent_raw: string | null;
  intent_confidence: InputConfidence;
  version: number;
  updated_at: string;
}

/** 定义区里可编辑的字段（`reason` 是改动的理由，进审计）。 */
export type DefinitionField =
  | "goal"
  | "why"
  | "starting_point"
  | "success_criteria"
  | "constraints"
  | "task_type";

export interface InputUpdate {
  expected_version: number;
  reason?: string | null;
  goal?: string | null;
  why?: string | null;
  starting_point?: string | null;
  success_criteria?: string | null;
  constraints?: string | null;
  task_type?: string | null;
  priority?: number | null;
  intent_confidence?: InputConfidence | null;
}

// ---------------------------------------------------------------------------
// 计划区
// ---------------------------------------------------------------------------

/** `PlanStepStatus`：pending / doing / done / skipped（**没有 blocked**，见 M9 §6 待裁定） */
export type PlanStepStatus = "pending" | "doing" | "done" | "skipped";

export interface StepRead {
  step_id: string;
  seq: number;
  action: string;
  method_ref: string | null;
  output: string | null;
  status: PlanStepStatus;
}

/** `CheckpointResult`：pending / pass / fail */
export type CheckpointResult = "pending" | "pass" | "fail";

export interface CheckpointRead {
  cp_id: string;
  at_step: string;
  check_method: string;
  pass_criteria: string;
  fail_action: string | null;
  result: CheckpointResult;
  result_at: string | null;
  result_note: string | null;
}

export interface PlanRead {
  plan_id: string;
  session_id: string;
  /** 当前行版本（写入用）。 */
  version: number;
  /** 第几版（人看的序号，单调递增）。 */
  version_no: number;
  /** 指向的当前版本 —— 与 `version_no` 不同：它可能落后于最新一版。 */
  current_version: number;
  goal_derived: string | null;
  steps: StepRead[];
  checkpoints: CheckpointRead[];
}

/** 三个版本域各自独立计数（`versions.plan` 在建计划之前是 `null`）。 */
export interface VersionsRead {
  session: number;
  input: number;
  plan: number | null;
}

export interface MotivationRead {
  session_id: string;
  type: "intrinsic" | "extrinsic" | "mixed";
  strength: "high" | "medium" | "low";
  drivers: string[] | null;
  obstacles: string[] | null;
  updated_at: string;
}

export interface SummaryRead {
  summary_id: string;
  content: string;
  findings: string[] | null;
  created_at: string;
}

/**
 * `GET /api/sessions/{id}/state` —— **界面真正需要的那一个端点**。
 *
 * 它是"一次取全"的快照：会话、定义、计划、暂存、对话、版本号都在里面。
 * 分别去取 `/input`、`/chat` 是不行的 —— 那两个路径上只有 PATCH 和 POST，
 * 没有 GET（照猜写就是 405）。而且分开取会拿到互相不一致的几份快照
 * （比如计划里已经多了第 4 步，对话里还没有对应的那条消息）。
 */
export interface SessionStateRead {
  session: SessionRead;
  input: InputRead;
  motivation: MotivationRead;
  snapshot: Record<string, unknown> | null;
  plan: PlanRead | null;
  stage: StageItemRead[];
  chat: ChatMessageRead[];
  notes: unknown[];
  milestones: MilestoneRead[];
  summary: SummaryRead | null;
  versions: VersionsRead;
}

export interface MilestoneRead {
  milestone_id: string;
  title: string;
  status: "pending" | "done";
  results: string | null;
  done_at: string | null;
  created_at: string;
}

export type StageItemType = "temp_pref" | "idea" | "plan_override";
export type StageItemStatus = "active" | "deposited" | "discarded" | "archived";

export interface StageItemRead {
  item_id: string;
  type: StageItemType;
  content: string;
  status: StageItemStatus;
  result_ref: string | null;
  settled_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// 讨论
// ---------------------------------------------------------------------------

export type ChatRole = "user" | "assistant";

/** 工具调用轨迹的一条（assistant 消息上带着）。 */
export interface ToolTraceEntry {
  tool: string;
  status: string;
  detail: string | null;
}

export interface ChatMessageRead {
  msg_id: string;
  role: ChatRole;
  content: string;
  tags: string[] | null;
  tool_trace: ToolTraceEntry[] | null;
  created_at: string;
}

export interface DiscussResponse {
  message: ChatMessageRead;
  stop_reason: string;
  steps_used: number;
}

// ---------------------------------------------------------------------------
// 讨论上下文包（P4 的判据：优先级与排除清单同等可核查）
// ---------------------------------------------------------------------------

export interface DiscussionContext {
  session_id: string;
  plan_version: number;
  step: StepRead;
  constraints: unknown[];
  soft_preferences: unknown[];
  checkpoints: unknown[];
  stage: unknown[];
  baseline: unknown[];
  evidence: unknown[];
  strategies: unknown[];
  step_history: unknown[];
  situation: Record<string, unknown>;
  sections: unknown[];
  precedence_order: string[];
}

// ---------------------------------------------------------------------------
// SSE 事件（P6+.1 契约：7 种 type，`seq` 从 1 严格递增）
// ---------------------------------------------------------------------------

export type AgentEventType =
  | "stage"
  | "thinking"
  | "token"
  | "tool_call"
  | "tool_result"
  | "done"
  | "error";

export interface AgentEvent {
  type: AgentEventType;
  seq: number;
  step: number;
  data: Record<string, unknown>;
}
