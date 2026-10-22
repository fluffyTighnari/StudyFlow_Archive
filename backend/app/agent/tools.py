"""14 个工具的**声明**与实现边界（M8 §4）。

## 为什么工具是数据，不是十四个函数

一个直觉的做法是写十个 `def retrieve_session(...)`，再在别处把它们的签名
反射成 JSON Schema。这里**故意不那样做**，因为模型能看到的东西只有
`description` 和字段说明 —— 而 M8 里最要紧的那几条规矩**全都是关于边界的**：

- `update_task_definition`「仅侧页/用户显式要求；Agent 主动 → rejected」；
- `retrieve_session`「必须带 scope+filter，禁止全量」；
- `submit_global_update` 的 `statement`「必须把理由一起写进来」。

这些句子如果只写在设计文档里，模型读不到；如果只写在代码的 `if` 里，
模型同样读不到（它只会拿到一次 `rejected`，然后不知道为什么）。
所以它们被写在**这里**，作为工具说明的一部分，同时在实现里真的执行。
「说明」与「执行」对同一条边界各说一遍，是刻意的冗余：
说而不做是空话，做而不说是让模型瞎猜。

## 三类作用域

| 作用域 | 含义 | 谁来执行 |
| --- | --- | --- |
| `read` | 只读，可以任意次数调用 | 本模块的 handler |
| `session_write` | 写**会话沙箱**，不经过 Router | 本模块的 handler |
| `global` | 写全局权威基线 | **`handoff` → Router**（ARCH-12） |

`global` 类的工具在这里 `handler=None`，这不是缺口，是本模块**故意不持有的能力**：
只要它没有 handler，"绕过 handoff 直接执行全局写入"在结构上就不成立 ——
而不是靠一句注释提醒后来的人。`dispatch` 会断言
「`handler is None` ⟺ `scope is global`」，见 `tests/agent/test_tools.py`。

## 版本号不由模型填

每个写工具都在 handler 内部**现读当前版本**再写。让模型填版本号等于把并发控制
伪装成模型能力：它既不知道版本号，也无法在两次调用之间保持一致。
模型负责「想改什么」，版本一致性是代码的事（P4 定的三条版本域仍然照旧生效）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.orm import Session as DbSession

from app.agent.llm import ToolSchema
from app import attachments as attachment_layer
from app.core.errors import InvalidField, NotFound
from app.core.quotes import contains_quote
from app.global_domain import reads as global_reads
from app.global_domain.targets import target_for_variable
from app.router.contracts import CEvidenceValue, M1ParameterValue, M2PreferenceValue
from app.session_domain import reads as session_reads
from app.session_domain import writes as session_writes
from app.session_domain.context import MAX_ATTACHMENTS
from app.session_domain.enums import (
    ChatRole,
    CheckpointResult,
    MotivationType,
    PlanStepStatus,
    StageItemType,
)
from app.session_domain.writes import CheckpointDraft, StepDraft


class ToolScope(StrEnum):
    """一个工具需要什么才能跑。

    ★ `READ` 与 `SESSION_READ` 的区分是 P6+.6 加的，而它不是为了整齐 ——
    侧边栏常驻模型**没有当前会话**，所以它需要一条**可推导的**规则来说明
    "哪些工具它用不了"。规则就是：需要会话的工具只能出现在会话里。

    用"手写一个侧边栏工具名单"来代替这条规则会有一个安静的坏处：
    将来新增一个读会话的工具时，有人会忘了往名单里加或减 ——
    而"忘了减"意味着侧边栏把 `retrieve_session` 暴露给模型，
    模型不带 `session_id` 调它，于是它去查一个叫 `sidebar` 的会话。

    `compaction` 的教训在这里同样成立：**能从数据推的规则不要变成记忆。**
    """

    #: 全局读（不碰任何会话）。
    READ = "read"
    #: **需要当前会话的读**：`retrieve_session` / `read_attachment`。
    #: 它们都接受显式的 `session_id`，但"留空 = 当前会话"这个默认值
    #: 在侧边栏里没有意义 —— 那里没有"当前会话"。
    SESSION_READ = "session_read"
    SESSION_WRITE = "session_write"
    #: 全局写（走 `submit_global_update` → 提案，不经沙箱）。
    GLOBAL = "global"


#: 侧边栏常驻模型能用的作用域。
#:
#: 它等于"不需要当前会话的那些"。**这个集合是从 `ToolScope` 推导的，
#: 不是列出来的名单** —— 见 `ToolScope` 的说明。
SIDEBAR_TOOL_SCOPES: frozenset[ToolScope] = frozenset(
    {ToolScope.READ, ToolScope.GLOBAL}
)


class ToolStatus(StrEnum):
    """M8 §4 的统一输出状态。

    `empty` 与 `error` 必须分开：`empty` 说的是「那里确实没有东西」，
    `error` 说的是「这次没查到」。混在一起，模型会把一次失败读成
    「用户的基线是空的」，然后基于一个错误的事实继续规划。
    """

    SUCCESS = "success"
    EMPTY = "empty"
    REJECTED = "rejected"
    ERROR = "error"


#: 用户在**这一次操作**里明确做过的事。工具据此判断"这件事用户要求了吗"。
#:
#: 这是 A21 原则的推广：一个动作可以直接生效，当且仅当用户在这次手势里
#: 已经给出或确认了那个值。**关键在于由运行期记录，而不是由模型自称** ——
#: 工具参数里若有个 `user_requested: true`，那是模型的自述，
#: 而模型恰恰是最不该被信任的那个环节（本项目已经栽过五次：
#: 「凡是系统用一个理由解释自己的行为的地方，都要检查那个理由是不是真的」）。
GESTURE_DEFINITION = "definition:"  # 前缀，后接字段名
GESTURE_STEP_DONE = "step_done:"  # 前缀，后接 step_id
GESTURE_PLAN_CHANGE = "plan_change"  # 用户要求改计划（点某个计划项提看法）
GESTURE_CHECKPOINT = "checkpoint:"  # 前缀，后接 cp_id
GESTURE_STAGE = "stage"


@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (ToolStatus.SUCCESS, ToolStatus.EMPTY)

    def as_dict(self) -> dict[str, Any]:
        """回灌给模型的形状。**`status` 必须在最外层** —— 见 M8 §4。"""
        payload: dict[str, Any] = {
            "status": self.status.value,
            "message": self.message,
        }
        if self.data:
            payload["data"] = self.data
        return payload


@dataclass
class ToolContext:
    """一次工具调用能看到的全部东西。

    没有 `token`：会话沙箱里的写入不需要能力令牌（那是 Router 的机制，
    而沙箱根本不到 Router）。全局写入的令牌由 `handoff` 在内部签发 ——
    它绝不可以从这里传进来。
    """

    db: DbSession
    session_id: str
    #: 会话所属文件夹。由 dispatch 一次性查好 —— 工具层不该各自去查一遍
    #: （那会让"这次调用作用于哪个文件夹"变成分散在十个 handler 里的隐式事实）。
    folder_id: str | None = None
    #: 当次操作里用户明确做过的事，见上面的 `GESTURE_*`。
    user_gestures: frozenset[str] = frozenset()
    #: ★ 这次调用发生在**侧边栏**里 —— 没有当前会话（P6+.6）。
    #:
    #: 为什么用一个显式布尔，而不是"`session_id` 等于侧边栏哨兵值"：
    #: 后者会让每一处 `ctx.session_id` 都悄悄指向一个不存在的会话，
    #: 于是 `get_session(db, "sidebar")` 报出「会话不存在：sidebar」——
    #: **那条错误看起来像数据问题，而真相是"这里根本不该有会话"。**
    #: 有了这个字段，需要会话的工具可以在**动手之前**说清真正的原因。
    in_sidebar: bool = False

    def has(self, gesture: str) -> bool:
        return gesture in self.user_gestures

    def require_session(self, *, tool: str) -> str:
        """取当前会话 id；侧边栏里**拒绝**并说清原因。

        每条需要会话的工具都从这里取 `session_id`，而不是直接读字段 ——
        这样"侧边栏不能做会话操作"这条规则只有一处实现，
        而新增工具的人会自然用上它（字段名叫 `require_`）。
        """
        if self.in_sidebar:
            raise InvalidField(
                f"`{tool}` 需要一次具体的会话，而侧边栏不绑定任何会话。"
                "如果要用它，请显式给出 `session_id` 参数。",
                detail={"in_sidebar": True, "tool": tool},
            )
        return self.session_id


Handler = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    scope: ToolScope
    summary: str
    parameters: dict[str, Any]
    handler: Handler | None = None

    def schema(self) -> ToolSchema:
        return ToolSchema(name=self.name, description=self.summary, parameters=self.parameters)


# --------------------------------------------------------------------------- #
# schema 小工具
# --------------------------------------------------------------------------- #


def _string(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _object(
    properties: dict[str, Any], *, required: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {**schema, **extra}


def _array(items: dict[str, Any], description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description, **extra}


#: 一次计划修订里的步骤。`output` 必填 —— M2-A3：没有产出的步骤无法验收，
#: 而"这一步做完了没有"最终要落到一个可指认的东西上。
_STEP_SCHEMA = _object(
    {
        "step_id": _string(
            "已有步骤的 id（如 ST-003）。**改动/保留既有步骤时必须原样带上** —— "
            "带上了，这一步的完成状态才会被继承；漏掉了，等于把它当成新步骤，"
            "用户会看到「做过的第 3 步又变回未开始」（M7 §5）。"
        ),
        "action": _string("这一步做什么。一句话，动词开头。"),
        "output": _string("这一步的产出物（M2-A3，必填）。没有产出的步骤无法验收。"),
        "method_ref": _string("用了哪条策略（M2 B 库里的策略 id），可空。"),
    },
    required=["action", "output"],
)

_CHECKPOINT_SCHEMA = _object(
    {
        "cp_id": _string("已有检查点的 id（如 CP-002）。新检查点留空。"),
        "at_step": _string("挂在哪个步骤之后（步骤 id）。"),
        "check_method": _string("怎么检查（可操作的动作，不是「感觉自己会了」）。"),
        "pass_criteria": _string("什么算通过。"),
        "fail_action": _string(
            "没通过时做什么。**写它才有意义** —— 检查点失败后界面按它给出建议，"
            "空着的话失败就只是一个坏消息（M2-A4）。"
        ),
    },
    required=["at_step", "check_method", "pass_criteria"],
)

def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """把 `$ref` 就地展开，消掉 `$defs`。

    为什么不直接发 pydantic 生成的原始 schema：它把三个模型放进 `$defs`，
    字段里只留 `{"$ref": "#/$defs/M2PreferenceValue"}`。
    **本地模型看的是被转换后的语法树**，而 `$ref` 在多数本地工具解析器里
    是没有实现或半实现的 —— 最可能的结果是模型看到一个空对象 schema，
    于是把 `value` 猜成一个字符串。

    这类失败不报错、不抛异常，只表现为「模型总是填不对 value」，
    而人会先去怀疑模型笨。所以这里花十几行让它自包含。
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            # 这三个模型之间没有环；真出现了环，宁可把 ref 原样留着让人看见，
            # 也不要无声地截断成一个空 schema。
            return node
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1])
                if isinstance(target, dict):
                    merged = resolve(target, depth + 1)
                    extra = {k: v for k, v in node.items() if k != "$ref"}
                    return {**merged, **extra} if extra else merged
                return node
            return {key: resolve(value, depth) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item, depth) for item in node]
        return node

    return resolve(schema)


#: `submit_global_update` 的 `intents[].value`。
#:
#: **从真实契约生成**，不手写 —— M8 §4.6 说 A19 的 `statement` 规范
#: 「写在 `M2PreferenceValue.statement` 的字段说明里，也就是本节工具 schema 生成的位置」。
#: 手写一份就等于抄一遍，而抄的那一份会在契约改动后悄悄过期
#: （那时模型看到的规范与实际校验的规范不一致，且没有任何测试会发现）。
_VALUE_SCHEMA: dict[str, Any] = _inline_refs(
    TypeAdapter(M1ParameterValue | M2PreferenceValue | CEvidenceValue).json_schema()
)


# --------------------------------------------------------------------------- #
# 1. 检索类（只读 RAG）
# --------------------------------------------------------------------------- #


def _retrieve_folder(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    folder_id = args.get("folder_id") or ctx.folder_id
    if not folder_id:
        return ToolResult(
            ToolStatus.ERROR,
            "不知道要读哪个文件夹：这次调用既没有带 folder_id，"
            "当前会话也不属于任何文件夹。",
        )
    folder = session_reads.get_folder(ctx.db, folder_id)
    recent = session_reads.list_sessions(ctx.db, folder_id=folder_id)[-5:]
    return ToolResult(
        ToolStatus.SUCCESS,
        f"文件夹「{folder.name}」的目标与最近 {len(recent)} 个会话摘要。",
        {
            "folder_id": folder.folder_id,
            "folder_goal": folder.goal,
            # **不返回 chat_log 全文**（M8 §4.1）：这里给的是结论层的东西，
            # 会话内部的原话要经 retrieve_session 且必须带过滤条件。
            "recent_sessions": [
                {
                    "session_id": row.session_id,
                    "title": row.title,
                    "state": row.state.value,
                    "summary": _summary_text(ctx, row.session_id),
                    "is_current": row.session_id == ctx.session_id,
                }
                for row in recent
            ],
        },
    )


def _summary_text(ctx: ToolContext, session_id: str) -> str | None:
    summary = session_reads.latest_summary(ctx.db, session_id)
    return summary.content if summary is not None else None


#: `retrieve_session` 允许的四个取数范围。**没有"全部"** —— 见 `_retrieve_session`。
_SESSION_SCOPES = ("chat_log", "notes", "milestones", "task_plan")


def _retrieve_session(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    # ★ 侧边栏里没有"当前会话"，所以这里必须要求显式的 `session_id`。
    # 没有这一句的话，`ctx.session_id`（侧边栏的哨兵值）会被拿去查一个
    # 不存在的会话，报出「会话不存在：sidebar」—— 那条错误看起来像数据问题，
    # 而真相是"这里根本不该有会话"。
    session_id = args.get("session_id") or ctx.require_session(tool="retrieve_session")
    scope = args["scope"]
    filters = args.get("filter") or {}
    limit = int(args.get("max_items") or 20)

    if scope == "chat_log" and not any(filters.get(key) for key in ("time", "topic", "tags")):
        # **禁止全量**（M8 §4.1）是这条工具唯一一条硬边界，因此这里真的拦。
        # 不给它留"过滤器空着就等于不过滤"的后门：聊天记录是唯一无界的表，
        # 全量读进来会挤掉真正重要的上下文，而且模型会以为自己"看到了全部"。
        return ToolResult(
            ToolStatus.REJECTED,
            "chat_log 必须带至少一个过滤条件（time / topic / tags）—— 禁止全量检索。"
            "换一个更具体的问法，或改查 notes / milestones / task_plan。",
        )

    if scope == "task_plan":
        plan = session_reads.get_plan(ctx.db, session_id)
        if plan is None:
            return ToolResult(ToolStatus.EMPTY, "这个会话还没有计划。", {"items": []})
        items = [
            {
                "kind": "step",
                "step_id": step.step_id,
                "action": step.action,
                "output": step.output,
                "status": step.status.value,
            }
            for step in session_reads.plan_steps(ctx.db, plan.plan_id)
        ] + [
            {
                "kind": "checkpoint",
                "cp_id": cp.cp_id,
                "at_step": cp.at_step,
                "check_method": cp.check_method,
                "pass_criteria": cp.pass_criteria,
                "fail_action": cp.fail_action,
                "result": cp.result.value,
            }
            for cp in session_reads.plan_checkpoints(ctx.db, plan.plan_id)
        ]
        return _items_result(items[:limit], "计划")

    if scope == "notes":
        notes = session_reads.list_notes(ctx.db, session_id)
        items = [
            {"note_id": n.note_id, "content": n.content, "tags": n.tags} for n in notes
        ]
        return _items_result(_filter_by_text(items, filters, "content")[:limit], "笔记")

    if scope == "milestones":
        items = [
            {
                "milestone_id": m.milestone_id,
                "title": m.title,
                "status": m.status.value,
                "results": m.results,
            }
            for m in session_reads.list_milestones(ctx.db, session_id)
        ]
        return _items_result(_filter_by_text(items, filters, "title")[:limit], "里程碑")

    messages = session_reads.list_chat_messages(ctx.db, session_id)
    items = [
        {
            "msg_id": m.msg_id,
            "role": m.role.value,
            "content": m.content,
            "tags": m.tags,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]
    items = _filter_by_text(items, filters, "content")
    if filters.get("tags"):
        wanted = {str(tag) for tag in filters["tags"]}
        items = [item for item in items if wanted & set(item.get("tags") or ())]
    # `time` 当作"最近 N 条"用：P6 不做 FTS5（缺口 B6 的邻居），
    # 按时间倒序取尾部是这里唯一诚实的解释 —— 见模块末尾的已知缺口。
    items = items[-limit:]
    return _items_result(items, "对话记录")


def _filter_by_text(
    items: list[dict[str, Any]], filters: dict[str, Any], key: str
) -> list[dict[str, Any]]:
    topic = filters.get("topic")
    if not topic:
        return items
    needle = str(topic).lower()
    return [item for item in items if needle in str(item.get(key, "")).lower()]


# --------------------------------------------------------------------------- #
# 会话资料（用户上传的文件，P6+.5）
# --------------------------------------------------------------------------- #

#: 一次 `read_attachment` 最多返回多少字符。
#:
#: 6000 是量出来的：它大约等于 16384 窗口里"读一份文件"能承受的份额
#: （prompt 本身已经 7000+ token）。**上限不是"读到哪算哪"**：
#: 返回里永远带着"还有多少字没读"与下一次该用的 `offset`，
#: 所以模型要么接着读，要么明确知道自己没读完 —— 而不是以为读完了。
MAX_ATTACHMENT_SLICE = 6000

#: 默认每次读多少。比上限小，留出"它还想再读一点"的余地。
DEFAULT_ATTACHMENT_SLICE = 4000


def _brief(row: Any) -> dict[str, Any]:
    """一条附件清单项。**不带正文** —— 清单是常数开销，正文按需取。"""
    return {
        "attachment_id": row.attachment_id,
        "filename": row.filename,
        "kind": row.kind,
        "chars_len": row.chars_len,
        "bytes_len": row.bytes_len,
        "truncated": row.truncated,
        "preview": attachment_layer.preview(row.text),
        "note": row.note,
        "created_at": row.created_at.isoformat(),
    }


def _read_attachment(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """读会话资料：不带参数 = 列清单；带 `attachment_id` = 读正文的一段。"""
    # 同 `_retrieve_session`：附件属于某个会话，侧边栏必须显式给 session_id。
    session_id = args.get("session_id") or ctx.require_session(tool="read_attachment")
    attachment_id = (args.get("attachment_id") or "").strip()

    if not attachment_id:
        # 列清单。**这是"资料可能不止清单里那几条"的出口**：
        # 上下文只带最近 MAX_ATTACHMENTS 份，模型想看到全部就得来这里。
        rows = session_reads.list_attachments(ctx.db, session_id)
        if not rows:
            return ToolResult(
                ToolStatus.EMPTY,
                "这个会话还没有上传过资料。如果用户提到「我传给你的那份文件」，"
                "那是他以为传过了 —— 直接说没收到，别假装读过。",
                {"items": []},
            )
        items = [_brief(row) for row in rows]
        return ToolResult(
            ToolStatus.SUCCESS,
            f"这个会话共有 {len(items)} 份资料。"
            "**清单里没有正文** —— 要读哪一份就带 attachment_id 再调一次。",
            {"items": items},
        )

    try:
        row = session_reads.get_attachment(ctx.db, attachment_id)
    except NotFound:
        # 明确列出现有的 id：模型编一个 id 是常见错法（它在上下文里只看到过
        # 名单），而"没找到"这句话本身不给它改正的机会。
        available = [r.attachment_id for r in session_reads.list_attachments(ctx.db, session_id)]
        return ToolResult(
            ToolStatus.REJECTED,
            f"没有这份资料：{attachment_id}。"
            + (f"现有的：{'、'.join(available) or '（无）'}。" if available else "这个会话还没传过资料。")
            + "不带参数调一次可以看到完整清单。",
        )
    if row.session_id != session_id:
        return ToolResult(
            ToolStatus.REJECTED,
            f"{attachment_id} 不属于会话 {session_id} —— 换一个会话的资料我读不到。",
        )

    total = len(row.text)
    offset = max(0, int(args.get("offset") or 0))
    limit = min(MAX_ATTACHMENT_SLICE, max(1, int(args.get("limit") or DEFAULT_ATTACHMENT_SLICE)))

    if offset >= total:
        return ToolResult(
            ToolStatus.EMPTY,
            f"「{row.filename}」全文 {total} 字，offset={offset} 已经到头了 —— "
            "你要么已经读完了，要么 offset 写大了。",
            {"attachment_id": attachment_id, "filename": row.filename, "total_chars": total},
        )

    chunk = row.text[offset : offset + limit]
    end = offset + len(chunk)
    remaining = total - end
    head = (
        f"「{row.filename}」（{row.kind}，全文 {total} 字"
        + ("，**上传时就已截断**" if row.truncated else "")
        + f"）。这次给你第 {offset + 1}–{end} 字。"
    )
    tail = (
        f"**还有 {remaining} 字没读** —— 接着读就用 offset={end}。"
        if remaining
        else "**这份读完了。**"
    )
    # 解析取舍说明只在第一段给：它解释的是"这份文件被怎么解出来的"，
    # 每段都重复一遍只是噪音（而噪音会挤掉正文的位置）。
    note = f"（{row.note}）" if offset == 0 and row.note else ""
    return ToolResult(
        ToolStatus.SUCCESS,
        f"{head}{note}\n{tail}",
        {
            "attachment_id": attachment_id,
            "filename": row.filename,
            "kind": row.kind,
            "total_chars": total,
            "offset": offset,
            "returned_chars": len(chunk),
            "remaining_chars": remaining,
            "truncated_at_upload": row.truncated,
            "text": chunk,
        },
    )


def _items_result(items: list[dict[str, Any]], what: str) -> ToolResult:
    if not items:
        return ToolResult(ToolStatus.EMPTY, f"按这个条件没有找到{what}。", {"items": []})
    return ToolResult(ToolStatus.SUCCESS, f"找到 {len(items)} 条{what}。", {"items": items})


def _retrieve_cognitive(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    scope = args.get("scope") or "all"
    query = (args.get("query") or "").strip().lower()
    task_type = args.get("task_type")
    limit = int(args.get("max_items") or 8)

    items: list[dict[str, Any]] = []
    if scope in ("m1", "all"):
        baselines = global_reads.current_baseline_map(ctx.db)
        for variable in global_reads.list_variables(ctx.db):
            version = baselines.get(variable.variable_id)
            entry = {
                # **字段路径**要带上（M8 §4.1）：模型据此才能提出
                # `target = "M1.V-XX"` 这样的写入，而不是一个它自己发明的名字。
                # 路径用**唯一那处构造器**拼（A9），不在这里手写前缀加变量 id ——
                # 两处拼接迟早会不一致，而两处不一致的命名空间等于没有命名空间。
                # （A9 的钉子测试会连注释一起扫，所以这里连示例都不写出来。）
                "path": target_for_variable(variable.variable_id),
                "name": variable.display_name,
                "content": variable.definition_what,
                "detail": {
                    "score": version.score if version else None,
                    "channel": version.channel.value if version else None,
                    "confidence": version.confidence.value if version else None,
                    "completeness": version.completeness.value if version else None,
                    "intervention_hooks": variable.intervention_hooks,
                },
            }
            items.append(entry)

    if scope in ("m2_preferences", "all"):
        for pref in global_reads.list_active_preferences(ctx.db):
            items.append(
                {
                    "path": f"M2.preferences.{pref.pref_id}",
                    "name": pref.pref_id,
                    "content": pref.statement,
                    "detail": {
                        "target_field": pref.target_field,
                        "priority": pref.priority,
                        "basis": pref.basis.value,
                        # 硬度是**派生**的，且只有一处判定（A18）。
                        # 这里直接给结论，是为了让模型不必自己重算一遍 `basis == "stated"`。
                        "is_hard_constraint": global_reads.is_hard_constraint(pref),
                        "supersedes_pref_id": pref.supersedes_pref_id,
                    },
                }
            )

    if scope in ("c_evidence", "all"):
        for evidence in global_reads.list_active_evidence(ctx.db):
            items.append(
                {
                    "path": f"C.evidence.{evidence.evidence_id}",
                    "name": evidence.evidence_id,
                    "content": evidence.statement,
                    "detail": {
                        "condition": evidence.condition,
                        "effect": evidence.effect,
                        "strength": evidence.evidence_strength.value,
                        # P7：这条是怎么来的。判定表只放 experienced/measured
                        # 进来，所以它恒为"已验证" —— 但把依据给出来，模型
                        # 在解释时才有一句可引的话，而不是自己编一个理由。
                        "basis": evidence.basis.value,
                        "verified": True,
                    },
                }
            )

    if scope in ("hypothesis", "all"):
        for hypothesis in global_reads.list_hypotheses(ctx.db):
            items.append(
                {
                    "path": f"C.hypothesis.{hypothesis.hypothesis_id}",
                    "name": hypothesis.hypothesis_id,
                    "content": hypothesis.statement,
                    "detail": {
                        "condition": hypothesis.condition,
                        "effect": hypothesis.effect,
                        # ★ 两个字段说的其实是同一件事，但**两处都写**：
                        # `basis` 是事实，`verified` 是给模型读的结论。
                        # 只给 `basis` 要模型自己推（它可能推错）；
                        # 只给 `verified` 则丢了原始依据。
                        "basis": hypothesis.basis.value,
                        "verified": False,
                        "verified_note": (
                            "**未验证**：他只是说过想试。不要把它当作已证明有效的做法"
                            "去安排计划；可以主动提议「你之前说想试试这个，要不要这次用」"
                        ),
                        "source_session_id": hypothesis.source_session_id,
                    },
                }
            )

    if query:
        items = [
            item
            for item in items
            if query in str(item["content"]).lower() or query in str(item["name"]).lower()
        ]

    note = ""
    if task_type:
        # **`task_type` 在 P6 只用于排序，不用于过滤** —— 这是一个已知缺口：
        # M8 §4.1 说「scope=m1 按 task_type 过滤」，但 `m1_variable` 表里
        # 没有 task_type 字段（它藏在 `measurement_def` / `intervention_hooks`
        # 的 JSON 里，结构未定型）。按一个猜测的键去过滤，最可能的结果是
        # **一条都不返回**，而模型会把"没查到"读成"这类任务没有相关变量"。
        # 所以宁可只排序并把这个事实说出来。
        items.sort(key=lambda item: task_type not in str(item["detail"]), reverse=True)
        note = f"（task_type={task_type} 只用于排序：M1 变量表里还没有 task_type 字段）"

    return _items_result(items[:limit], "认知条目" if not note else f"认知条目{note}")


def _retrieve_strategy(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    limit = min(int(args.get("max_items") or 4), 4)
    strategies = global_reads.list_strategies(ctx.db, limit=limit)
    if not strategies:
        return ToolResult(ToolStatus.EMPTY, "策略库里没有条目。", {"strategies": []})
    return ToolResult(
        ToolStatus.SUCCESS,
        f"返回 {len(strategies)} 条参考策略。**它们是参考不是约束** —— "
        "采用与否由你决定，但要说明理由。",
        {
            "strategies": [
                {
                    "strategy_id": row.strategy_id,
                    "name": row.name,
                    "solves": row.solves,
                    "when": row.when_to_use,
                    "cost": row.cost,
                    "adjust": row.adjust_signals,
                    "evidence_strength": row.evidence_strength.value,
                }
                for row in strategies
            ],
            "task_type": args.get("task_type"),
            "difficulty": args.get("difficulty"),
        },
    )


# --------------------------------------------------------------------------- #
# 2. 定义与动机
# --------------------------------------------------------------------------- #

#: M8 §4.2.1 的字段白名单。**与 `session_domain.writes.INPUT_FIELDS` 必须一致** ——
#: 这里再列一份不是重复，而是让「哪些字段能被模型改」出现在模型读得到的地方；
#: 一致性由测试钉住（`test_definition_fields_match_the_write_layer`）。
DEFINITION_FIELDS = (
    "goal",
    "why",
    "starting_point",
    "task_type",
    "constraints",
    "success_criteria",
    "priority",
)

#: 可以对着它**提问**的槽位（P13）。它是 `clarify.SLOTS` 的子集对应的字段名，
#: 而两者的关系由测试钉住 —— 问一个答案没地方落的字段，
#: 表现是"用户答了、定义区还是空的、闸门再拦一次"。
ASKABLE_SLOTS = ("goal", "starting_point", "success_criteria")


def _update_task_definition(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    field_name = args["field"]
    if field_name not in DEFINITION_FIELDS:
        return ToolResult(
            ToolStatus.REJECTED,
            f"`{field_name}` 不在可改字段白名单里（M8 §4.2.1）。"
            f"可改的是：{'、'.join(DEFINITION_FIELDS)}。"
            "`intent_raw` / `intent_confidence` / `input_revisions` 是系统字段。",
        )
    quote = _quoted_user_words(ctx, args.get("user_quote"))
    if not ctx.has(f"{GESTURE_DEFINITION}{field_name}") and not quote:
        return ToolResult(
            ToolStatus.REJECTED,
            "这次没有证据表明是用户自己要在定义区改这一项：既没有界面手势，"
            "`user_quote` 也引不出他这句里的原话。把改动讲给他听让他确认；"
            "要你代改，就带上一段**他的原话**（必须能在这一轮那句话里逐字找到）。",
        )
    if field_name == "goal" and not str(args.get("reason") or "").strip():
        return ToolResult(ToolStatus.REJECTED, "改 goal 必须带 reason（M8 §4.2）。")

    row = session_reads.get_session(ctx.db, ctx.session_id)
    updated = session_writes.update_input(
        ctx.db,
        ctx.session_id,
        changes={field_name: args["new_value"]},
        expected_version=session_reads.get_input(ctx.db, ctx.session_id).version,
        changed_by="agent",
        reason=args.get("reason"),
    )
    data: dict[str, Any] = {
        "field": field_name,
        "new_value": args["new_value"],
        "input_version": updated.version,
        "session_version": row.version,
    }
    if field_name == "goal":
        data["scope_change_suspected"] = bool(args.get("scope_change_suspected", False))
    return ToolResult(ToolStatus.SUCCESS, f"已更新任务定义的 `{field_name}`。", data)


def _update_motivation(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    changes = args.get("changes") or {}
    change = str(args.get("reason") or "").strip()
    if not change:
        return ToolResult(
            ToolStatus.REJECTED,
            "动机修订必须写清楚「改了什么」（reason）—— 空的一次修订无法回看。",
        )
    # ★ `expected_version` 比的是**会话**版本：`session_motivation` 表没有
    # `version` 列（`update_motivation` 内部取的是 `session.version`）。
    # 原来这里读 `motivation.version` → AttributeError 冲出 dispatch →
    # 整个请求 500 且事务回滚，连用户刚说的那句话一起没了。
    session_row = session_reads.get_session(ctx.db, ctx.session_id)
    motivation = session_writes.update_motivation(
        ctx.db,
        ctx.session_id,
        change=change,
        expected_version=session_row.version,
        type=MotivationType(changes["type"]) if changes.get("type") else None,
        strength=changes.get("strength"),
        drivers=changes.get("drivers"),
        obstacles=changes.get("obstacles"),
        reason=args.get("reason"),
        basis=args.get("basis", "experienced"),
        changed_by="agent",
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        "已记录动机变化。",
        {
            # 动机自己没有版本号，能报的是它跟着变的那个：会话版本
            "session_version": session_row.version + 1,
            "type": motivation.type.value if motivation.type else None,
            "strength": motivation.strength.value if motivation.strength else None,
            "drivers": motivation.drivers,
        },
    )


# --------------------------------------------------------------------------- #
# 3. 会话写入
# --------------------------------------------------------------------------- #


def _write_chat_note(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    note = session_writes.add_note(
        ctx.db, ctx.session_id, content=args["content"], tags=args.get("tags")
    )
    return ToolResult(ToolStatus.SUCCESS, f"已记下笔记 {note.note_id}。", {"note_id": note.note_id})


# --------------------------------------------------------------------------- #
# 4. 规划写入
# --------------------------------------------------------------------------- #


def _steps_from(args: dict[str, Any], existing: list[Any] = ()) -> list[StepDraft]:
    """把 `steps` 解析成新版本的步骤列表：**它就是那一版计划。**

    ## 方案 B（用户裁定，2026-02 变更）：模型完整掌控任务表

    **原行为（方案 A）**：`steps` 被当成"编辑指令"，没提到的步骤原样保留，
    删除要显式 `drop`。它来自一次真实事故 —— 模型只提交了被讨论的那一步，
    计划从 2 步变成 1 步，用户另一半计划**静默消失**。

    **现行为（方案 B）**：`steps` 是**那一版的整份有序列表**。
    位置由列表顺序决定（所以**可以重排、可以插入**），
    没列出来的就是删掉。

    **为什么改**：A 的代价是模型**无法表达"插在这里"** ——
    而"这一步太大了，拆成两半"是最常见的计划改动。A 之下第二半只能被
    追加到计划末尾（对有依赖关系的计划，那个位置是错的），而且**不报错**：
    版本号正常 +1、界面正常显示，只是计划排错了。
    另一个代价是 A 让"重排"这件事根本做不到（注解曾写着"drop 再新增"，
    但那也插不回原位）。

    **为什么敢改**：A 要防的是"模型把 `steps` 读成要改的那几条"。
    那是**模型能力问题**，而现在的目标是接商用 API（用户裁定）；
    用一条限制把能力锁死，换来的是"排错了也不说"。

    **换了什么保护**：删掉的步骤现在会在工具返回里**逐条报出来**
    （见 `_change_report`），所以删除是**可见的**而不是静默的；
    计划本身是版本化的，旧版本的行原地不动，删掉的步骤回得去。

    ## 保留的两条保护

    - **未知 `step_id` 直接拒绝**：那多半是拼错了。当成新增会让
      "我以为改了"变成"多了一条谁也不认识的步骤"，而模型会告诉用户"改好了"。
    - **未知/重复的 id 都不静默处理**，见下。
    """

    existing_ids = {row.step_id for row in existing}
    seen: set[str] = set()
    drafts: list[StepDraft] = []

    for position, item in enumerate(args.get("steps") or (), start=1):
        step_id = item.get("step_id")
        if step_id:
            if step_id not in existing_ids:
                # 用 `InvalidField` 而不是自定义异常：它已经在 dispatcher 的
                # `OPERATIONAL_ERRORS` 名单里，因此会变成一句可读的 `rejected`。
                raise InvalidField(
                    f"第 {position} 条引用了当前计划里不存在的 step_id：{step_id}。"
                    "它不会被当成新增 —— 那会让你以为改了某一步，"
                    "实际库里多了一条谁也不认识的步骤，而你告诉用户「已经改好了」。"
                    "先确认 id（`retrieve_session` 的 `task_plan` 范围里有）；"
                    "确实要新增就用不带 step_id 的条目。",
                    detail={"unknown_step_id": step_id, "position": position},
                )
            if step_id in seen:
                # 重复 id 会撞同一个行主键（`plan_step_row_id`），
                # 变成一条没人看得懂的 IntegrityError。
                raise InvalidField(
                    f"step_id {step_id} 在这一版里出现了两次。"
                    "每一步只能出现一次 —— 重复的后果是同一条计划行被写两遍，"
                    "而两次的内容大概率不一样。",
                    detail={"duplicated_step_id": step_id},
                )
            seen.add(step_id)

        drafts.append(
            StepDraft(
                action=item["action"],
                output=item.get("output"),
                method_ref=item.get("method_ref"),
                step_id=step_id,
            )
        )

    return drafts


def _change_report(
    drafts: list[StepDraft], existing: list[Any]
) -> str:
    """把这一版相对上一版**删掉了什么**报出来 —— 而且报**完整内容**。

    方案 B 把"省略 = 删除"的权限给了模型，那么删除就必须**可见** ——
    否则又回到"静默丢东西"，只是这次是裁定的后果而不是缺陷。
    这句话会出现在工具返回里，也就是 `tool_trace` 里（界面能看到），
    模型自己也会读到它。

    ## 为什么必须带 `output`，不只是 id 和 action

    一次真实运行（用子代理充当模型，2026-02）里，模型给 `steps` 时只写了
    拆开后的两步，于是另外两步被规则删掉 —— 它在收尾回复里发现了这件事，
    想加回来，但它**取不回原文**（`retrieve_session` 只读当前版本，
    而被删的步骤不在当前版本里）。它的原话是"我不打算凭印象替你重写那两步"，
    然后把问题交回给用户。**这个判断是对的，而它本不必被逼到这一步。**

    带上 `output` 之后，模型当场就能把被删的步骤原样放回去。
    删除仍然允许（那是方案 B 的裁定），但**恢复必须是可能的**。
    """
    before = {row.step_id: row for row in existing}
    after_ids = {draft.step_id for draft in drafts if draft.step_id}
    removed = [step_id for step_id in before if step_id not in after_ids]
    added = [draft for draft in drafts if draft.step_id is None]

    parts: list[str] = []
    if removed:
        described = []
        for step_id in removed:
            row = before[step_id]
            output = getattr(row, "output", None)
            described.append(
                f"{step_id}「{getattr(row, 'action', '')}」"
                + (f"（产出：{output}）" if output else "")
            )
        parts.append("删掉了 " + "、".join(described))
    if added:
        parts.append(
            "新增了 " + "、".join(f"「{draft.action}」" for draft in added)
        )
    if not parts:
        return "步骤数量没变。"
    return (
        "；".join(parts)
        + "。**如果删掉的步骤不该删**：把它们按原样（带原来的 step_id）"
        "重新放进 steps 再调一次即可 —— 上面把它们的内容照抄回来了。"
    )


def _checkpoints_from(
    args: dict[str, Any], existing: list[Any] = ()
) -> list[CheckpointDraft]:
    """检查点：**整个键缺席 = 保留现有的**，给了就是那一版的全部。

    这与 `_steps_from` 的"方案 B"看起来不对称，所以要说清为什么：

    - `steps` 是**模型掌管的表**，它每次都必须给出完整一份（那里没有"沉默"这一格）；
    - `checkpoints` 是另一张表，而实际发生的是"模型改步骤时根本不提检查点"。
      把"没提"读成"删掉"的后果是**通过标准随着一句措辞修改一起消失** ——
      而那恰恰是最不该无声消失的东西（M2-A4 的闭环全靠它）。

    所以这里保留一条明确的语义：**沉默 ≠ 删除**；但**显式给空列表 = 删除**
    （`"checkpoints": []` 就是"这一版不要检查点"，那是说出来的动作）。
    """
    if "checkpoints" not in args:
        return [
            CheckpointDraft(
                at_step=row.at_step,
                check_method=row.check_method,
                pass_criteria=row.pass_criteria,
                fail_action=row.fail_action,
                cp_id=row.cp_id,
            )
            for row in existing
        ]

    existing_ids = {row.cp_id for row in existing}
    seen: set[str] = set()
    drafts: list[CheckpointDraft] = []
    for position, item in enumerate(args.get("checkpoints") or (), start=1):
        cp_id = item.get("cp_id")
        if cp_id:
            if cp_id not in existing_ids:
                raise InvalidField(
                    f"第 {position} 条检查点引用了不存在的 cp_id：{cp_id}。"
                    "先确认 id；要新增检查点就不带 cp_id。",
                    detail={"unknown_cp_id": cp_id, "position": position},
                )
            if cp_id in seen:
                raise InvalidField(
                    f"cp_id {cp_id} 在这一版里出现了两次。",
                    detail={"duplicated_cp_id": cp_id},
                )
            seen.add(cp_id)
        drafts.append(
            CheckpointDraft(
                at_step=item["at_step"],
                check_method=item["check_method"],
                pass_criteria=item["pass_criteria"],
                fail_action=item.get("fail_action"),
                cp_id=cp_id,
            )
        )
    return drafts


def _create_task_plan(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    steps = _steps_from(args)
    if not steps:
        return ToolResult(ToolStatus.REJECTED, "计划至少要有一个步骤。")
    missing = [i for i, step in enumerate(steps, start=1) if not step.output]
    if missing:
        return ToolResult(
            ToolStatus.REJECTED,
            f"第 {missing} 步没有产出（output）—— M2-A3：没有产出的步骤无法验收，"
            "「做完了没有」会变成一个只能凭感觉回答的问题。",
        )
    plan = session_writes.create_plan(
        ctx.db,
        ctx.session_id,
        steps=steps,
        checkpoints=_checkpoints_from(args),
        goal_derived=args.get("goal"),
        reason=args.get("reason") or "创建计划",
        expected_session_version=session_reads.get_session(ctx.db, ctx.session_id).version,
        changed_by="agent",
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"计划已创建（版本 {plan.version}，{len(steps)} 步）。",
        {"plan_id": plan.plan_id, "version": plan.version},
    )


def _recorded_evidence(ctx: ToolContext) -> tuple[bool, str]:
    """有没有**落在库里的**、「计划该被改」的证据？

    两类，都是别人（用户、过去的自己）已经写下的东西，不是模型当下的判断：

    1. 有检查点判为 `fail`（M2-A4 的闭环入口）；
    2. stage 里有还没结案的条目 —— 那是用户自己写下的「我想试试别的」。

    ## 为什么这里**不含**「用户点了某个计划项」

    因为那是 `propose_plan_change` 的地盘，而那个工具会**强制硬约束逐条覆盖**。
    一次真实运行抓出了这条绕道：模型被要求改计划时选了参数更少的
    `update_task_plan`，而它当时唯一要满足的就是那个手势 ——
    于是硬约束覆盖率检查被整个绕过，计划照样改了。

    这是「同一条规矩在两个地方不一样严」的典型形态，而模型会挑松的那条。
    所以两条路各自只认**一种**证据，它们不再重合：

    | 工具 | 认什么 | 额外要求 |
    |---|---|---|
    | `propose_plan_change` | 用户本人这次点了那个计划项 | 硬约束逐条覆盖 |
    | `update_task_plan` | 检查点未通过 / stage 里有未结案的想法 | 无（有据即可） |

    **一个不能用的判据**：「有步骤卡住了」看起来很自然，但
    `PlanStepStatus` 只有 `pending|doing|done|skipped`，**没有 blocked**。
    也就是说「这一步卡住了」目前根本没有承载它的字段。把它写进判断条件
    会让这个分支永远不成立，而代码看上去像是支持的 —— 那比不写更坏。
    （这是一条新记录：M7 §8 的状态机里，步骤的"卡住"状态缺失。）
    """
    plan = session_reads.get_plan(ctx.db, ctx.session_id)
    if plan is not None:
        failed = [
            cp.cp_id
            for cp in session_reads.plan_checkpoints(ctx.db, plan.plan_id)
            if cp.result is CheckpointResult.FAIL
        ]
        if failed:
            return True, f"检查点 {'、'.join(failed)} 判为未通过"
    open_items = [
        item.item_id
        for item in session_reads.list_stage_items(ctx.db, ctx.session_id)
        if item.status.value == "active"
    ]
    if open_items:
        return True, f"stage 里还有未结案的想法（{'、'.join(open_items)}）"
    return False, ""


def _quoted_user_words(ctx: ToolContext, quote: Any) -> str:
    """用户这句话里**真的有**这段原话吗？有就返回它，没有返回空串。

    ★ 门槛是 `app.core.quotes.contains_quote`：去空白后**至少四个字**。
    少于四个字的"引文"在任何一句话里都找得到，核对就不成立了。

    ## 为什么引文算证据，而"点击"不是唯一的证据

    原来改计划 / 改定义区 / 标步骤完成都只认界面手势（"用户点过那一项"）。
    可用户是在聊天框里表达的：他说「第 3 步拆成两天」，系统回他
    「请先点开计划项」。那是一道**没有数据理由的门**：放行不会写出任何
    自相矛盾的行，拦下却挡住了最正常的动作。

    换成的判据是"能不能引出他这句话里的原文" —— 门仍然可核对，
    而且可核对得更彻底：引文由服务端在**用户刚写下的那条消息**里逐字比对，
    模型编不出一句用户没说过的引文。防的是"模型替用户发言"，
    而这恰恰是可验证的那一半。
    """
    text = str(quote or "").strip()
    if not text:
        return ""
    messages = [
        row
        for row in session_reads.list_chat_messages(ctx.db, ctx.session_id)
        if str(getattr(row.role, "value", row.role)) == "user"
    ]
    if not messages:
        return ""
    said = str(messages[-1].content or "")
    # ★ 比对只用 `app.core.quotes.contains_quote` —— 它含 `MIN_QUOTE_CHARS=4`
    # 下限。自己写一份 `in` 的话，一个"改"字就能满足改计划的入口条件，
    # 而这条下限存在的理由正是"一两个字的词在任何句子里都找得到"。
    return text if contains_quote(said, text) else ""


def _user_asked_for_a_change(ctx: ToolContext, args: dict[str, Any]) -> tuple[bool, str]:
    """用户**这次**要求改吗？返回 (有没有, 可引用的原话)。

    两条路都算，且都可核对：
    - 界面手势：用户点了某个计划项（`GESTURE_PLAN_CHANGE`）；
    - 他自己的话：`user_quote` 能在这一轮那句话里逐字找到。
    """
    if ctx.has(GESTURE_PLAN_CHANGE):
        return True, ""
    quote = _quoted_user_words(ctx, args.get("user_quote"))
    return (bool(quote), quote)


def _constraint_gate(ctx: ToolContext, checks: Any) -> ToolResult | None:
    """硬约束逐条覆盖 + 冲突即拒。**两条改计划的路共用这一份。**

    ★ 为什么必须共用：原来只有 `propose_plan_change` 查这一关，
    `update_task_plan` 不查。模型会挑松的那条走（实测抓到过），
    于是"计划与用户亲口说的硬约束冲突"在数据里成立，而系统假装不可能。
    同一条规矩在两个地方不一样严，等于没有这条规矩。
    """
    constraints = global_reads.list_active_constraints(ctx.db)
    if not constraints:
        return None
    if checks is None:
        return ToolResult(
            ToolStatus.REJECTED,
            f"当前有 {len(constraints)} 条硬约束，必须逐条给出判断"
            "（constraint_checks），不能整批略过。",
            {"constraint_ids": [row.pref_id for row in constraints]},
        )
    answered = {item.get("pref_id") for item in checks}
    missing = [row for row in constraints if row.pref_id not in answered]
    if missing:
        return ToolResult(
            ToolStatus.REJECTED,
            "还有硬约束没有被逐条判断："
            + "；".join(f"{row.pref_id}「{row.statement}」" for row in missing)
            + "。**漏掉一条约束就整次拒绝** —— 沉默地略过一条用户亲口说的话，"
            "比明确说「这条我违反了」危险得多。",
            {"constraint_ids": [row.pref_id for row in constraints]},
        )
    conflicts = [item for item in checks if item.get("verdict") == "conflict"]
    if conflicts:
        return ToolResult(
            ToolStatus.REJECTED,
            "这次调整与硬约束冲突，因此没有生效："
            + "；".join(
                f"{item['pref_id']}（{item.get('why', '未说明')}）" for item in conflicts
            )
            + "。这不影响你继续当前步骤 —— 可以把冲突讲给用户，由他决定要不要改口。",
            {"conflicts": conflicts},
        )
    return None


def _ask_about_task(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """任务不明确时**反问**（P13）。

    ★ 这个工具存在的理由：**改定义区需要用户自己要求**
    （`_update_task_definition` 只在他点开那一项、或引得出他那句原话时才动得了），
    所以当 `goal` 是空的时候，模型**没有别的办法**知道要做什么 ——
    它只能猜或者问。这个工具把"问"变成一个有后果的动作：
    问题记下来、界面显示出来，**并且决定之后排出来的计划带不带「这是猜的」**。

    ★ 它**不挡计划**（政策变更：`plan_gate` 不再拒）。原来那句
    "在被回答之前计划改不了"已经不真了 —— 现在缺 `goal` 时计划照排，
    只是理由里会被追加一句"这是按助手判断排的"（`clarify.guess_note`）。
    模型看到的必须是这件事的**现状**：它不必等答案，但它排的东西会被标记。

    ★ 它不检查问题问得好不好。"问得含糊"是模型的问题，
    而"问了两遍 / 问了一个不存在的字段"是系统的问题 —— 后者能判，前者不能。
    """
    slot = str(args.get("slot") or "")
    question = str(args.get("question") or "")
    if not slot or not question.strip():
        # ★ 形状错误在**这里**说清楚（而不是抛出去）：这一层的契约是
        # 「任何一条路径都返回 ToolResult」，模型要看到的是
        # `rejected` + 一句能照着做的话，而不是一次崩溃。
        # 领域的拒绝（槽位已有内容 / 已经问过一次）仍然由 dispatcher 转 ——
        # 那两条要读库，不是形状问题。
        return ToolResult(
            ToolStatus.REJECTED,
            f"要给出 `slot` 与 `question`。可以问的槽位：{'、'.join(ASKABLE_SLOTS)}。",
        )
    # 领域的拒绝（槽位已有内容 / 已经问过一次 / 槽位名不存在）由
    # `Dispatcher.execute` 统一转成 `rejected` 并把 detail 交给模型 ——
    # 这里**不再包一层**：包了就会出现两套措辞，而模型只看得见其中一套。
    item = session_writes.ask_about_task(
        ctx.db,
        ctx.session_id,
        slot=slot,
        question=question,
        options=args.get("options") or None,
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"问题已记下（{item.clarification_id}，关于 `{slot}`），"
        f"会显示在任务定义区。它**不挡住改计划** —— 但在他回答或说「先不管」之前，"
        f"你排出来的计划会带上「这是按助手判断排的」，那句说明落在计划修订理由里"
        f"（`{slot}` 仍然缺着，这一点别瞒着他）。"
        f"不要再自己填一个看起来合理的值：那是伪造一条关于他的事实。",
        {
            "clarification_id": item.clarification_id,
            "slot": item.slot,
            "question": item.question,
            "status": item.status.value,
        },
    )


def _update_task_plan(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """模型自己认为计划该改 —— **必须出示落在库里的证据**。

    ## 这里刻意**不接受**模型给的 `basis`

    `revise_plan` 有一个 `basis` 参数，而在这个工具的参数表里它被拿掉了。
    理由：`basis` 说的是"这条改动的依据有多硬"，而在这条路上**没有任何东西
    能验证模型凭什么说 `stated`** —— 那就是一句模型产的、读起来像事实的声明，
    与 `user_view` 是同一个形态的缺陷，只是换了个字段名。

    所以这里传 `basis=None`（列可空）。将来若真的需要它，
    正确的做法是在需要它的那一刻**从证据推导**，而不是读模型早先选的那个词。

    对比 `propose_plan_change`：那里的 `basis="stated"` 是**被入口条件强制**的
    （用户必须真的点了那一项），所以它有资格被记下来。
    一条 `basis` 只有在别处被强制时才算事实。
    """
    if not str(args.get("reason") or "").strip():
        return ToolResult(ToolStatus.REJECTED, "无理由不改计划（M7 §9 第 3 条）。")
    has_evidence, why = _recorded_evidence(ctx)
    asked, quote = _user_asked_for_a_change(ctx, args)
    if not has_evidence and not asked:
        return ToolResult(
            ToolStatus.REJECTED,
            "这次改计划没有任何可核对的开头：没有判为未通过的检查点、"
            "stage 里没有悬着的想法、也没有用户这一次要求改的依据"
            "（他既没点开计划项，`user_quote` 也引不出他这句里的原话）。"
            "把建议讲给用户听；他只要说一句要改哪里，"
            "把那句原话带进 `user_quote` 就能改。",
        )
    if asked and not why:
        why = f"用户这一句要求改：「{quote}」" if quote else "用户点开了这一项要求改"

    rejected = _constraint_gate(ctx, args.get("constraint_checks"))
    if rejected is not None:
        return rejected

    diff = args["diff"]
    plan = session_reads.require_plan(ctx.db, ctx.session_id)
    # 把**当前版本**传进去：校验 `step_id`、并算出这一版删掉了什么。
    current_steps = session_reads.plan_steps(ctx.db, plan.plan_id)
    current_checkpoints = session_reads.plan_checkpoints(ctx.db, plan.plan_id)
    steps = _steps_from(diff, current_steps)
    report = _change_report(steps, current_steps)
    revised = session_writes.revise_plan(
        ctx.db,
        ctx.session_id,
        steps=steps,
        checkpoints=_checkpoints_from(diff, current_checkpoints),
        change=diff.get("change") or args["reason"],
        reason=args["reason"],
        expected_version=plan.version,
        changed_by="agent",
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"计划已改到版本 {revised.version}（依据：{why}）。{report}",
        {"plan_id": revised.plan_id, "version": revised.version, "evidence": why},
    )


def _propose_plan_change(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """「讨论计划项」的落点 —— P6 要打通的就是这一条。

    ## 入口条件：**用户本人要求改**

    只看 `_user_asked_for_a_change`：用户这次点开了那个计划项。
    **落库的证据（失败检查点 / 未结案的 stage）不算** ——
    那是 `update_task_plan` 的地盘，而这条路要额外交硬约束逐条覆盖，
    两条路各自只认一种证据，见 `_recorded_evidence` 的说明。

    `user_view` **不是**证据。它是模型转述用户的话，而没有任何东西验证过
    这段转述 —— 拿它当"用户要求过"的凭据，等于让模型的一句话变成权限。
    用户真正点开那个计划项这个动作，由界面转成 `GESTURE_PLAN_CHANGE` 带进来
    （见 `app/api/discuss.py`）。

    ## 三查里哪一查由代码做，为什么

    M8 §4.4 说 `propose_plan_change` 要评估三查：① stage 一致性
    ② HC1–HC4 ③ C 证据/历史。其中 ① 和 ③ 是**判断题**，只能由模型做；
    ② 则有一半是**机械题**：当前有几条硬约束是数得出来的。

    所以分工是：模型对每条硬约束给出 `constraint_checks[{pref_id, verdict, why}]`，
    而**代码只强制覆盖率** —— 漏掉任何一条，整次调用 `rejected`。
    为什么不让模型自由作答：让模型自己说"这条改动没有违反任何约束"，
    恰好就是「理由 ≠ 事实」的形态；而"你有没有逐条回答"是机械可验的，
    它拦住的正是最危险的那种失败 —— **沉默地漏掉一条约束**。
    """
    target = args["target"]
    checks = args.get("constraint_checks")

    # **先过"用户要求改"这一关。**
    #
    # 这一行是补上去的，而它的缺席很值得记一笔：`user_view` 是**模型写的一段话**，
    # 它读起来完全像"用户是这么说的"，但没有任何东西验证过它。
    # 于是这个面向用户的工具反而比面向模型的 `update_task_plan` 更松 ——
    # 后者至少要 `reason` 加一条落在库里的证据，前者只要模型自己说一句
    # 「用户觉得太慢」就能重写整个计划。
    #
    # 这正是本项目反复栽的那一类：「系统用一个理由解释自己的行为，
    # 而那个理由没人验证过」。区别在于这次的"理由"是模型产的。
    asked, quote = _user_asked_for_a_change(ctx, args)
    if not asked:
        return ToolResult(
            ToolStatus.REJECTED,
            "没有证据表明用户这次要求改这个计划：他既没有点开那一项，"
            "`user_quote` 也引不出他这句里的原话。**把建议讲给用户听**；"
            "他说一句要改哪里，把那句原话带进 `user_quote` 就能改 —— "
            "不需要他先去界面上点。",
        )

    rejected = _constraint_gate(ctx, checks)
    if rejected is not None:
        return rejected

    diff = args["suggested_change"]
    plan = session_reads.require_plan(ctx.db, ctx.session_id)
    # 把当前版本传进去，为的是两件事：① 校验 `step_id` 存不存在（拼错要拦），
    # ② 算出"这一版删掉了什么"并**报出来**（方案 B 之下删除是允许的，
    #    但必须可见 —— 见 `_change_report`）。
    current_steps = session_reads.plan_steps(ctx.db, plan.plan_id)
    current_checkpoints = session_reads.plan_checkpoints(ctx.db, plan.plan_id)
    steps = _steps_from(diff, current_steps)
    report = _change_report(steps, current_steps)
    revised = session_writes.revise_plan(
        ctx.db,
        ctx.session_id,
        steps=steps,
        checkpoints=_checkpoints_from(diff, current_checkpoints),
        change=diff.get("change") or f"针对 {target} 的调整",
        reason=args["user_view"],
        # `basis=stated`：这次调整的依据是**用户自己在界面上点开的那个计划项**，
        # 而入口条件刚刚强制验证过这一点 —— 所以这个 `stated` 不是模型的一句声明，
        # 它是一个被代码保证的事实。
        #
        # 对比 `_update_task_plan`：那里**不接受**模型给 `basis`（下面有说明），
        # 因为那条路上没有任何东西能验证模型凭什么说 `stated`。
        # 一条 `basis` 只有在别处被强制时才有资格被记下来。
        basis="stated",
        expected_version=plan.version,
        changed_by="agent",
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"调整已应用（计划版本 {revised.version}）。{report}"
        "三查中：硬约束逐条回答且无冲突；"
        "stage 一致性与 C 证据由你的判断负责。"
        f"改动依据：用户本人要求改（{quote or '点开了这个计划项'}）。",
        {
            "plan_id": revised.plan_id,
            "version": revised.version,
            "target": target,
            "evidence": quote or "用户本人要求改",
            "constraint_checks": list(checks or ()),
        },
    )


def _write_stage(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    item = session_writes.add_stage_item(
        ctx.db,
        ctx.session_id,
        item_type=StageItemType(args["type"]),
        content=args["content"],
        expected_version=session_reads.get_session(ctx.db, ctx.session_id).version,
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"已放进 stage（{item.item_id}）。**stage 永不进全局** —— "
        "它只是「想试但还没决定」，要变成结论必须由用户在沉淀那一步明确同意。",
        {"stage_id": item.item_id},
    )


# --------------------------------------------------------------------------- #
# 5. 执行推进
# --------------------------------------------------------------------------- #


def _mark_step_done(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    step_id = args["step_id"]
    quote = _quoted_user_words(ctx, args.get("user_quote"))
    if not ctx.has(f"{GESTURE_STEP_DONE}{step_id}") and not quote:
        return ToolResult(
            ToolStatus.REJECTED,
            "没有证据表明这一步是他自己说做完的：既没有界面手势，"
            "`user_quote` 也引不出他这句里的原话。他说过「第 3 步做完了」就把那句原话带进来；"
            "他没说过，就别替他打勾 —— 打勾之后「做完了没有」就不再是他的判断了。",
        )
    plan = session_reads.require_plan(ctx.db, ctx.session_id)
    step = session_writes.mark_step(
        ctx.db,
        ctx.session_id,
        step_id=step_id,
        status=PlanStepStatus.DONE,
        expected_version=plan.version,
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        f"步骤 {step_id} 已标记完成。",
        {"step_id": step.step_id, "status": step.status.value, "plan_version": plan.version + 1},
    )


def _checkpoint_milestone(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    milestone = session_writes.complete_milestone(
        ctx.db,
        ctx.session_id,
        milestone_id=args["milestone_id"],
        results=args.get("result"),
    )
    return ToolResult(
        ToolStatus.SUCCESS,
        "里程碑已达成。**这次达成不会自动变成全局认知** —— "
        "如果其中有值得留下来的发现，建议用户走一次沉淀（R4b）由他确认。",
        {
            "milestone_id": milestone.milestone_id,
            "status": milestone.status.value,
            "suggested_deposit": True,
            "assisted": bool(args.get("assisted", False)),
        },
    )


# --------------------------------------------------------------------------- #
# 14 个工具
# --------------------------------------------------------------------------- #

_TOOLS: tuple[ToolSpec, ...] = (
    # ---- 1. 检索类（只读） ----
    ToolSpec(
        name="retrieve_folder",
        scope=ToolScope.READ,
        summary=(
            "读这个文件夹（长期目标）的目标与最近 5 个会话的摘要。"
            "用于理解「他在学的这件事整体是什么」。不返回对话原文。"
        ),
        parameters=_object(
            {"folder_id": _string("文件夹 id（如 F-0001）。留空 = 当前会话所属的文件夹。")}
        ),
        handler=_retrieve_folder,
    ),
    ToolSpec(
        name="retrieve_session",
        #: ★ `SESSION_READ`：它读的是**某个会话**，而侧边栏没有当前会话 ——
        #: `session_id` 的默认值「留空 = 当前会话」在侧边栏里没有意义。
        scope=ToolScope.SESSION_READ,
        summary=(
            "在**当前或指定会话**里按范围检索。scope 必填且只能四选一："
            "chat_log（对话原文）/ notes（笔记）/ milestones（里程碑）/ task_plan（计划与检查点）。"
            "**必须带过滤条件，禁止全量拉取** —— 尤其 chat_log，"
            "空过滤器会被拒绝。计划与检查点是结构化数据，通常不需要过滤。"
        ),
        parameters=_object(
            {
                "session_id": _string("会话 id（如 S-0001）。留空 = 当前会话。"),
                "scope": {
                    "type": "string",
                    "enum": list(_SESSION_SCOPES),
                    "description": "取哪一类。没有「全部」这个选项。",
                },
                "filter": _object(
                    {
                        "time": _string("时间范围或「最近」的说法。"),
                        "topic": _string("主题关键词，按内容子串匹配。"),
                        "tags": _array({"type": "string"}, "标签，任一命中即可。"),
                    },
                    description="过滤条件。**chat_log 至少要给一个**，否则会被拒绝。",
                ),
                "max_items": {"type": "integer", "minimum": 1, "maximum": 50,
                              "description": "最多返回多少条。默认 20。"},
            },
            required=["scope", "filter"],
        ),
        handler=_retrieve_session,
    ),
    ToolSpec(
        name="read_attachment",
        #: ★ 同上：附件属于某个会话，侧边栏没有会话。
        scope=ToolScope.SESSION_READ,
        summary=(
            "读**用户上传给这个会话的资料**（txt / md / docx）。"
            "不带参数 = 列出全部资料清单（文件名、字数、开头一段）；"
            "带 `attachment_id` = 读它的正文，一次最多 "
            f"{MAX_ATTACHMENT_SLICE} 字，用 `offset` 接着往下读。"
            "**上下文里那份清单只有最近 "
            f"{MAX_ATTACHMENTS} 份、而且没有正文** —— 所以「用户说他传过文件」时，"
            "**先来这里看，不要回答「我没有看到文件」**。"
            "返回里会写清还有多少字没读；没读完就接着读，别对半份材料下结论。"
        ),
        parameters=_object(
            {
                "attachment_id": _string(
                    "要读哪一份（`AT-0001` 这种）。**留空 = 只列清单，不读正文。**"
                ),
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "从第几个字符开始读（0 起）。上一次返回里给了下一次该用的值。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_ATTACHMENT_SLICE,
                    "description": f"这次最多读多少字。默认 {DEFAULT_ATTACHMENT_SLICE}。",
                },
                "session_id": _string("会话 id。留空 = 当前会话。"),
            },
            required=[],
        ),
        handler=_read_attachment,
    ),
    ToolSpec(
        name="retrieve_cognitive",
        scope=ToolScope.READ,
        summary=(
            "读全局认知基线（这是「我是什么样的人、我擅长什么方法」的权威记录）。"
            "scope 可选 m1（十一个认知变量及其当前取值）/ m2_preferences（用户偏好与约束）"
            "/ c_evidence（**已验证**的具体做法）/ hypothesis（**他想试但还没试**的做法）"
            "/ all。"
            "每条结果都带**字段路径**，写回时请用那个路径。"
            "**c_evidence 与 hypothesis 的区别是可信度**：前者他试过、有效，"
            "可以据以安排；后者他只是说过想试，**只能用来提议，不能当作依据**。"
            "「我想试试之前那个猜想」就是查 hypothesis。"
            "**注意：这里没有置信度标注** —— 别把一条 provisional 的取值当成定论。"
        ),
        parameters=_object(
            {
                "scope": {
                    "type": "string",
                    "enum": ["m1", "m2_preferences", "c_evidence", "hypothesis", "all"],
                    "description": "读哪一部分。默认 all。",
                },
                "query": _string("关键词，按内容与名称子串过滤。可空。"),
                "task_type": _string(
                    "任务类型（如「掌握技能」）。用于把相关的变量排在前面 —— "
                    "P6 还做不到按它过滤，因为 M1 变量表里没有这个字段。"
                ),
                "max_items": {"type": "integer", "minimum": 1, "maximum": 30,
                              "description": "最多返回多少条。默认 8。"},
            }
        ),
        handler=_retrieve_cognitive,
    ),
    ToolSpec(
        name="retrieve_strategy",
        scope=ToolScope.READ,
        summary=(
            "读 3–4 条方法候选（B 策略库）。**它们是软参考，不是约束** —— "
            "采用哪条、要不要全部不用，由你判断，但要说明理由。"
        ),
        parameters=_object(
            {
                "task_type": _string("任务类型。"),
                "difficulty": _string("用户自述的难度或卡点。"),
                "max_items": {"type": "integer", "minimum": 1, "maximum": 4,
                              "description": "最多 4 条（M8 的边界）。"},
            }
        ),
        handler=_retrieve_strategy,
    ),
    # ---- 2. 定义与动机 ----
    ToolSpec(
        name="ask_about_task",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "任务定义区缺了计划离不开的东西时**先问**，不要猜（P13）。"
            "★ 你改 `goal` 需要**用户自己要求**（他点开那一项、或引得出他那句原话），"
            "所以目标不明时你只有两条路：问，或者猜。这个工具是前者，而它是有后果的 ——"
            "问题会被记下、显示在定义区，并决定之后排出来的计划**带不带「这是猜的」**"
            "（没答之前计划照排，但会注明那是按你的判断排的）。"
            "★ 什么时候该用：用户说「帮我复习第三章」而你不知道那是哪份材料；"
            "或者他说「学一下这个」而没有说清要达成什么。"
            "★ 什么时候不该用：槽位已经有内容（会被拒绝），"
            "或者你在问一件与计划无关的事（那就在回复里问，不要占定义区）。"
            "`options` 可给候选答案，让回答有形状。"
        ),
        parameters=_object(
            {
                "slot": {
                    "type": "string",
                    "enum": list(ASKABLE_SLOTS),
                    "description": "定义区里空着的那个字段。",
                },
                "question": _string("要问的那一句。具体、可回答，一句话就够。"),
                "options": _array(
                    _string("候选答案。"),
                    "可选的候选项 —— 让他不用从零开始想怎么答。",
                ),
            },
            required=["slot", "question"],
        ),
        handler=_ask_about_task,
    ),
    ToolSpec(
        name="update_task_definition",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "改任务定义区的一个字段。**只在用户自己要求或确认时才能用** —— "
            "你主动改会被拒绝（M8 §4.2）：改目标这件事必须由目标的主人做。"
            "改 goal 必须带 reason；若这次改动属于「目标对象或领域变了」，"
            "把 scope_change_suspected 置 true，界面会据此提示是否新建会话。"
        ),
        parameters=_object(
            {
                "field": {
                    "type": "string",
                    "enum": list(DEFINITION_FIELDS),
                    "description": "白名单外的字段会被拒绝。",
                },
                "new_value": _string("新值。"),
                "reason": _string("为什么改。改 goal 时必填。"),
                "user_quote": _string(
                    "用户这一句要求改的原话（必须能在他说过的那一句里逐字找到）。"
                    "给了才会被当成「他自己要改」。"
                ),
                "scope_change_suspected": {
                    "type": "boolean",
                    "description": "是否属于大幅变更（目标对象/领域改变）。默认 false。",
                },
            },
            required=["field", "new_value"],
        ),
        handler=_update_task_definition,
    ),
    ToolSpec(
        name="update_motivation",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "更新动机（为什么学、强度、驱动因素、障碍）。你可以主动调用。"
            "**区分「动机陈述」与随口吐槽**：前者进这里，后者最多写成一条 chat_note。"
            "drivers 要引用户的原话，不要替他总结成漂亮话。"
            "**不要写当次状态（精力/压力/环境）—— 那是 state_snapshot，你只读不写。**"
        ),
        parameters=_object(
            {
                "changes": _object(
                    {
                        "type": {
                            "type": "string",
                            "enum": ["intrinsic", "extrinsic", "mixed"],
                            "description": "动机类型。",
                        },
                        "strength": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "强度。",
                        },
                        "drivers": _array({"type": "string"}, "驱动因素，引原话。"),
                        "obstacles": _array(
                            _object(
                                {
                                    "type": _string("障碍类型。"),
                                    "detail": _string("具体是什么。"),
                                }
                            ),
                            "障碍。",
                        ),
                    },
                    description="要改的项。没提到的项保持不变。",
                ),
                "reason": _string("改了什么、依据是哪句话。必填。"),
                "basis": {
                    "type": "string",
                    "enum": ["stated", "experienced", "inferred"],
                    "description": (
                        "`stated` = 用户亲口说的；`experienced` = 你从对话过程观察到的；"
                        "`inferred` = 你推断的。**必填，没有默认值** —— "
                        "这一项决定这条记录的硬度，不该由沉默决定。"
                    ),
                },
            },
            required=["changes", "reason", "basis"],
        ),
        handler=_update_motivation,
    ),
    # ---- 3. 会话写入 ----
    ToolSpec(
        name="write_chat_note",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "把这次对话里**将来可能被引用**的东西记成一条笔记。"
            "防噪声：不要什么都记 —— 判断标准是「以后会不会有人需要翻回来看」。"
            "闲聊、寒暄、临时情绪都不要记。"
        ),
        parameters=_object(
            {
                "content": _string("笔记内容。写要点，不要抄一整段对话。"),
                "tags": _array({"type": "string"}, "标签。"),
            },
            required=["content"],
        ),
        handler=_write_chat_note,
    ),
    # ---- 4. 规划写入 ----
    ToolSpec(
        name="create_task_plan",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "建计划。步骤**必须带产出（output）**（M2-A3）—— 没有产出的步骤无法验收。"
            "检查点要写 fail_action：没有失败预案的检查点，失败时只是一个坏消息。"
        ),
        parameters=_object(
            {
                "goal": _string("这份计划服务于哪个目标（通常就是会话的 goal）。"),
                "steps": _array(_STEP_SCHEMA, "步骤列表，按顺序。"),
                "checkpoints": _array(_CHECKPOINT_SCHEMA, "检查点。可空。"),
                "reason": _string("为什么这样规划。"),
            },
            required=["steps"],
        ),
        handler=_create_task_plan,
    ),
    ToolSpec(
        name="update_task_plan",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "**入口条件：用户没有点任何计划项，但有落在库里的证据**"
            "（检查点判 fail、或 stage 里还悬着想法 —— 两者之一）。"
            "**入口条件不满足就会被拒绝** —— 驳回语会告诉你缺的是哪一条、该换哪个工具。"
            "改计划结构 → 产生**新版本**（旧版本的行原地不动，所以「当时为什么那样规划」看得见）。"
            "**必须带 reason。**"
            "**用户点了某个计划项时不要用这个** —— 那种情况该用 `propose_plan_change`，"
            "它必须逐条回答硬约束。"
            "**`steps` 是这一版的整份步骤表，顺序就是你给的顺序**："
            "要有序就按序写全，要插一步就插在那个位置，"
            "**没列出来的步骤会被删掉**（删了什么会在返回里逐条告诉你）。"
            "想保留某一步就把它带上，并带上它原来的 step_id —— "
            "这样它的完成状态才会被继承。"
            "**新增的步骤不要填 step_id**：填了会被当成引用一个已存在的步骤，而那个 id 不存在时整次调用会被拒绝 —— 不填才是新增。"
            "**改 steps 时别顺手重写 `checkpoints`**：整个键不传 = 保留现有的。"
        ),
        parameters=_object(
            {
                "diff": _object(
                    {
                        "steps": _array(
                            _STEP_SCHEMA,
                            "新版计划的**完整**步骤列表（顺序即计划顺序；"
                            "没列出来的会被删掉）。",
                        ),
                        "checkpoints": _array(
                            _CHECKPOINT_SCHEMA,
                            "新版计划的检查点。**整个键不给 = 保留现有的**；"
                            "给了就是这一版的全部。",
                        ),
                        "change": _string("一句话说明改了什么。"),
                    },
                    required=["steps"],
                    description="计划的新版本内容（整份，不是补丁）。",
                ),
                "reason": _string("为什么改。必填。"),
                "user_quote": _string(
                    "用户这一句要求改的原话（必须逐字能在他那一句里找到）。"
                    "没点开计划项时，这就是入口条件。"
                ),
                "constraint_checks": _array(
                    _object(
                        {
                            "pref_id": _string("硬约束的 pref_id（如 P-0001）。"),
                            "verdict": {
                                "type": "string",
                                "enum": ["ok", "conflict", "unclear"],
                                "description": "ok = 不冲突；conflict = 冲突（会整次拒绝）；unclear = 说不清。",
                            },
                            "why": _string("为什么这么判。"),
                        },
                        required=["pref_id", "verdict"],
                    ),
                    "对当前每一条硬约束的逐条判断。**有硬约束时必须覆盖全部。**",
                ),
            },
            required=["diff", "reason"],
        ),
        handler=_update_task_plan,
    ),
    ToolSpec(
        name="propose_plan_change",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "**入口条件：用户本人点了那个计划项**（上下文里写着「用户此刻的动作："
            "他点开了计划项 ST-00X」时就是点了 —— **不要让他再点一次，也不要问他哪一步**）。"
            "你要做三查：① 与 stage 里那些「想试但没定」的东西一致吗 "
            "② 有没有违反用户亲口下达的硬约束"
            "③ 与 C 证据和历史是否相符。"
            "**用户亲口下达的硬约束必须逐条回答**（constraint_checks）：漏掉任何一条，"
            "整次调用会被拒绝 —— "
            "沉默地略过用户说过的一句话，比明确说「这条我违反了」危险得多。"
            "**改 `steps` 时别顺手重写 `checkpoints`**：整个键不传 = 保留现有的。"
            # 这句话在 `update_task_plan` 里也有，**必须逐字一致**：
            # 两个工具写的是同一份步骤表（同一个 `_steps_from`），
            # 描述上不一致会让模型以为"这两条路的 steps 语义不同" ——
            # 而它选哪条路是看情况的，于是同一个动作在两条路上产生不同结果。
            # 这条一致性有测试钉住（`test_the_steps_semantics_is_stated_identically_...`）。
            "**`steps` 是这一版的整份步骤表，顺序就是你给的顺序**："
            "要有序就按序写全，要插一步就插在那个位置，"
            "**没列出来的步骤会被删掉**（删了什么会在返回里逐条告诉你）；"
            "想保留某一步就带上它原来的 step_id，这样它的完成状态才会被继承。"
            "**新增的步骤不要填 `step_id`**：填了会被当成"
            "「引用一个已存在的步骤」，而那个 id 不存在时整次调用会被拒绝 —— "
            "不填才是新增。"
            "别的证据（检查点未通过、stage 里悬着想法）请走 `update_task_plan`。"
            "被拒绝**不影响执行**，会话照常继续。"
        ),
        parameters=_object(
            {
                "target": _string("用户点的是哪一项（step_id / cp_id / '整个计划'）。"),
                "user_view": _string("用户的原话或看法。**引原话**，不要替他润色。"),
                "user_quote": _string(
                    "用户这一句要求改的原话（必须逐字能在他那一句里找到）。"
                    "没点开计划项时，这就是入口条件。"
                ),
                "constraint_checks": _array(
                    _object(
                        {
                            "pref_id": _string("硬约束的 pref_id（如 P-0001）。"),
                            "verdict": {
                                "type": "string",
                                "enum": ["ok", "conflict", "unclear"],
                                "description": "ok = 不冲突；conflict = 冲突（会整次拒绝）；unclear = 说不清。",
                            },
                            "why": _string("为什么这么判。"),
                        },
                        required=["pref_id", "verdict"],
                    ),
                    "对当前每一条硬约束的逐条判断。**必须覆盖全部，不能少。**",
                ),
                "suggested_change": _object(
                    {
                        "steps": _array(
                            _STEP_SCHEMA,
                            "调整后的**完整**步骤列表（顺序即计划顺序；"
                            "没列出来的会被删掉）。",
                        ),
                        "checkpoints": _array(
                            _CHECKPOINT_SCHEMA,
                            "调整后的检查点。**整个键不给 = 保留现有的**。",
                        ),
                        "change": _string("一句话说明改了什么。"),
                    },
                    required=["steps"],
                    description="调整后的计划内容（整份）。",
                ),
            },
            required=["target", "user_view", "suggested_change"],
        ),
        handler=_propose_plan_change,
    ),
    ToolSpec(
        name="write_stage",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "把「想试试但还没决定」的东西放进 stage。"
            "边界：想试 → stage；值得留的结论 → chat_note；动机变了 → update_motivation。"
            "**stage 永不进全局** —— 要变成全局认知必须由用户在沉淀那一步明确同意。"
        ),
        parameters=_object(
            {
                "type": {
                    "type": "string",
                    "enum": ["temp_pref", "idea", "plan_override"],
                    "description": "temp_pref = 临时偏好；idea = 想法；plan_override = 对计划的临时偏离。",
                },
                "content": _string("内容。写清楚「想试什么、为什么」。"),
            },
            required=["type", "content"],
        ),
        handler=_write_stage,
    ),
    # ---- 5. 执行推进 ----
    ToolSpec(
        name="mark_step_done",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "把一个步骤标为完成。用户说「我做完了」时，把**他那句原话**"
            "放进 `user_quote` 就能替他打勾 —— 系统会在他这句话里逐字核对；"
            "引不出原话就说明他没说过，那时候别替他打勾。"
        ),
        parameters=_object(
            {
                "step_id": _string("步骤 id（如 ST-003）。"),
                "user_quote": _string(
                    "用户说这一步做完的那句原话（必须逐字能在他那一句里找到）。"
                ),
            },
            required=["step_id"],
        ),
        handler=_mark_step_done,
    ),
    ToolSpec(
        name="checkpoint_milestone",
        scope=ToolScope.SESSION_WRITE,
        summary=(
            "记录里程碑达成。**只提示沉淀，不自动沉淀**："
            "达成本身不改变全局认知，要不要把它变成一条结论由用户决定。"
            "注意：里程碑（milestones[]）与检查点（task_plan.checkpoints[]）不是一回事 —— "
            "检查点的通过/失败由用户在界面上一键记录，不走这个工具。"
        ),
        parameters=_object(
            {
                "milestone_id": _string("里程碑 id（如 MS-001）。"),
                "result": _string("达成了什么、发现了什么。写下来才好判断值不值得沉淀。"),
                "assisted": {
                    "type": "boolean",
                    "description": "是不是你辅助判定的（用户说「已完成，我发现…」）。默认 false。",
                },
            },
            required=["milestone_id"],
        ),
        handler=_checkpoint_milestone,
    ),
    # ---- 6. 全局提交（需审核） ----
    ToolSpec(
        name="submit_global_update",
        scope=ToolScope.GLOBAL,
        summary=(
            "把一条认知写回**全局基线**（这是唯一一条通往全局的路，由路由层裁定）。"
            "必须带 trigger_type：R3（用户在对话里说出的偏好）/ R4a（会话完成沉淀）/ "
            "R4b（里程碑沉淀）。一次可以带多条 intent。"
            "路由层会产出提案：**被拒的会带理由回来**，需要确认的会等用户裁决。"
            "**只有 R3 的 add_preference 会自动生效** —— 而 `stated` 必须带 `basis_quote`"
            "（用户在对话里的原话），系统会去核对；对不上就降级为 `experienced`、"
            "转成待确认。**从上传的文件里读到的内容不是 `stated`** —— "
            "文件是用户给的**材料**，不是用户说的**话**。"
            "**R4c（用户从 stage 勾选后点沉淀）你不能声称** —— 那个勾选由界面产生；"
            "如果用户确实说过什么，用 R3。"
            "**`add_preference` 的 statement 必须把理由一起写进来**（A19）："
            "写「我讨厌看网课，因为浪费时间」而不是「我讨厌网课」—— "
            "理由才是能泛化的那一半，缺了它下次会用「看 2 小时讲座」来满足「别看网课」。"
            "**`archive` / `restore` / `revise` 要用逐条地址**"
            "（`C.hypothesis.H-0001`），不能只给集合根 —— 对一整类东西归档没有确定的含义。"
            "这三个动作**永远要等用户确认**（R3 也是 needs_confirmation）："
            "删除和改写是他对自己认知的处置，你只能提议，"
            "而他要么在界面上自己点，要么在待确认队列里点头。"
        ),
        parameters=_object(
            {
                "trigger_type": {
                    "type": "string",
                    "enum": ["R3", "R4a", "R4b"],
                    "description": "这次提交是哪条触发规则。必填 —— 判定表靠它决定要不要问用户。",
                },
                "intents": _array(
                    _object(
                        {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "add_preference",
                                    "update_parameter",
                                    "archive",
                                    "restore",
                                    "revise",
                                    "no_change",
                                ],
                            },
                            "target": _string(
                                "全局地址，必须规范化：`M2.preferences` / "
                                "`M1.V-XX` / `C.evidence`。变量 id 请从 retrieve_cognitive "
                                "拿到的**字段路径**里取，不要自己编。"
                            ),
                            "value": {
                                **_VALUE_SCHEMA,
                                "description": (
                                    "写入内容。形状取决于 target："
                                    "M1.* 用参数值，M2.preferences / C.evidence 用声明值。"
                                ),
                            },
                            "priority": {"type": "integer", "minimum": 0, "maximum": 10},
                            "basis": {
                                "type": "string",
                                "enum": ["stated", "measured", "experienced", "inferred"],
                                "description": (
                                    "这条认知有多硬。`stated` = **用户亲口说的**，"
                                    "它会被当成用户下达的约束（违反即计划不合格）。"
                                    "所以用 `stated` 时**必须**配 `basis_quote`。"
                                    "如果你是从用户上传的文件里读到的，那不是 `stated` —— "
                                    "用 `experienced`（它就只是你的观察，会等用户确认）。"
                                ),
                            },
                            "basis_quote": _string(
                                "**只有 `basis=stated` 需要填**：用户在对话里说过的原话，"
                                "照抄，不要改写。系统会拿它去比对用户自己发过的消息 —— "
                                "对不上就把这条降级成 `experienced`（需要用户确认），"
                                "并在结果里告诉你原因。"
                                "空白字符不计（换行、空格随便），"
                                "但**改一个字就算对不上**。"
                                "上传的文件**不算**用户说过的话：从文件里读到的东西"
                                "本来就是 `experienced`。"
                            ),
                            "reasoning": _string("为什么提出这条写入。必填，进日志。"),
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "你对自己这次判断的把握。它**不等于** basis。",
                            },
                            "conflict_note": _string("与已有内容冲突时说明。"),
                        },
                        required=["action", "target", "basis", "reasoning", "confidence"],
                    ),
                    "要提交的更新，至少一条。",
                ),
            },
            required=["trigger_type", "intents"],
        ),
        # 全局提交没有 handler：见模块开头。它的执行权在 `handoff`（ARCH-12）。
        handler=None,
    ),
)

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _TOOLS}


def tool_schemas(
    scopes: frozenset[ToolScope] | None = None,
) -> list[ToolSchema]:
    """交给模型的工具清单。**顺序固定**，便于比对轨迹。

    `scopes=None` = 全部（会话讨论用的就是它，行为与以前逐字相同）。
    侧边栏传 `SIDEBAR_TOOL_SCOPES` —— 于是"模型看得见哪些工具"
    与"分发器允许哪些工具"用的是**同一个集合**，不会一处改了一处忘。
    """
    return [
        spec.schema() for spec in _TOOLS if scopes is None or spec.scope in scopes
    ]


def iter_tools() -> tuple[ToolSpec, ...]:
    return _TOOLS


def get_tool(name: str) -> ToolSpec | None:
    return TOOLS.get(name)


# --------------------------------------------------------------------------- #
# 已知缺口（P6 范围内刻意留下的）
# --------------------------------------------------------------------------- #
#
# 1. `retrieve_session(scope="chat_log")` 的 `time` 过滤退化成"最近 N 条"：
#    P6 不上 FTS5，按时间倒序取尾部是这里唯一诚实的解释。
#    （缺口 B6 的邻居：`retrieve_session` FTS5 索引。）
# 2. `retrieve_cognitive(task_type=...)` 只排序不过滤 —— M1 变量表里
#    还没有 task_type 字段。这一点在返回消息里明确说了，不让模型误读。
# 3. 适配器只实现了 Ollama 的线上格式；OpenAI 兼容层的 `tool_call_id`
#    语义没有实现（因为没有第二个真实客户端要接）。
