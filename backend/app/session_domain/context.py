"""讨论上下文包（P4 方案 A1，用户裁定）—— **采用序由确定性代码决定**。

## 这个模块在回答什么

用户点开计划里的某一步说「我想改这个」时，模型需要知道哪些事。
本模块就是那个答案，而且是**确定性**的答案：同样的数据必然产出同样的包，
与模型是谁、prompt 怎么写无关。

## 为什么不能留给 Agent 在运行时拼

M0 §7.4 把「上下文采用序」写在 Agent 层。实施时发现那样做会有一个后果：
**决定讨论质量的那一半（哪些认知数据与这一步相关）会落在一段无法测试的代码里**。
而本项目对 LLM 的一贯立场是「代码裁定、LLM 只提议」——
如果"看什么"由模型每次自己挑，那"用户偏好高于 stage"这类顺序就只能靠 prompt 的说服力。

因此采用序下沉到这里（与 A9 把 target 命名空间从路由层下沉到
`global_domain/targets.py` 属同一类修正），并在 M0 §7.4 回写。

## 两条不可混淆的次序

| | 谁定 | 内容 |
|---|---|---|
| **写入裁定序** | Router（§7.4） | 用户明确偏好 > measured > experienced > inferred。**只在同槽位、同批次内**适用 |
| **上下文采用序**（本模块） | 确定性代码 | 会话资料 > 用户偏好 > 检查点 > stage > 基线/证据 > 策略库。**只决定呈现顺序与取舍，不裁定任何写入** |

**会话资料是 P6+.5 加进来的一节**（用户裁定「让 chat 支持文件」）：
用户上传的 txt/md/docx 在这里只以**清单**出现（文件名 / 字数 / 预览），
正文由 `read_attachment` 按需取。它排在偏好之前，是被一个具体失败形态逼出来的：
清单排在后面，模型可能整节没注意到，于是**从来不去取正文**，
而现象是"我明明传了它却说没看到"。

**检查点是 P6 加进来的一节**（用户裁定）：原先的包里没有检查点，于是用户
点开"每天过 50 个新词"说"我记不住"时，模型看不到这一步的通过标准，
也不知道失败预案里本来就写着"回到上一批词重刷三天"。它排在 stage 之前，
因为**验收标准是这一步的硬要求，而 stage 里是还没定的想法**。

本模块**不裁定任何写入**：它返回的包里没有 `UpdateIntent`，
也没有"该写什么"的结论。计划怎么改，仍然要用户点头（`propose_plan_change`）。

## 一条硬规矩：用户声明永不截断

硬约束（`basis=stated`）**全部**进包，不设上限、不排序取舍。
理由是 HC1：用户说过的话是约束，不是候选。任何"太多了，挑几条"的逻辑
都等于系统替用户决定他哪句话不重要 —— 而那正是 M2 红线 2 禁止的事。
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from pydantic import BaseModel, Field

from app.core.errors import NotFound
from app.attachments import preview
from app.global_domain import reads as global_reads
from app.session_domain import reads
from app.session_domain.enums import CheckpointResult, PlanStepStatus, StageItemStatus
from app.session_domain.models import PlanStep, TaskPlan

#: 参与计划装配的 M1 变量（M2 §13 第 13 行）：
#: `V-SMP/V-ACT/V-OSP → SP1（呈现/活动形态）`；`V-DC/V-IR/V-PAR → 检查点/时间锁强度`。
#: 其余变量（例如自我效能）**不进计划装配**，因此也不进讨论上下文 ——
#: 把不参与决策的数据塞给模型，只会给它更多编造理由的素材。
PLANNING_VARIABLES: tuple[str, ...] = (
    "V-SMP",
    "V-ACT",
    "V-OSP",
    "V-DC",
    "V-IR",
    "V-PAR",
)

#: 某一节里最多举几条「被排除的东西」。排除清单的意义是**让人一眼看出漏了什么**，
#: 而不是把全部被排除项抄一遍（那会淹没包本身）。总条数由 `dropped_count` 给出。
MAX_EXCLUSION_EXAMPLES = 10

#: C 证据的条数上限（与 M8 `retrieve_cognitive` 的边界同口径）。
MAX_EVIDENCE = 12

#: 资料清单的条数上限（P6+.5）。
#:
#: 每条清单只有几十个字（文件名 + 字数 + 预览 150 字），所以 20 条约 4000 字 ——
#: 在 16384 的窗口里是可接受的常数开销。**超出的部分不是消失**：
#: 模型可以调 `read_attachment`（不带参数）列出全部，所以清单被截断时
#: 会明确写出"还有 N 份没列在这里"。
MAX_ATTACHMENTS = 20


# --------------------------------------------------------------------------- #
# 包的结构
# --------------------------------------------------------------------------- #


class StepContext(BaseModel):
    step_id: str
    seq: int
    action: str
    output: str | None = None
    method_ref: str | None = None
    status: PlanStepStatus


class PreferenceItem(BaseModel):
    pref_id: str
    statement: str
    priority: int | None = None
    #: `True` = 用户下达的约束，**不可被静默推翻**（HC1）。
    hard: bool


class StageItemRef(BaseModel):
    item_id: str
    type: str
    content: str


class AttachmentRef(BaseModel):
    """会话资料的一条**清单项**（P6+.5）。

    **它不是正文。** 正文可能很长（上限 20 万字），塞进上下文会挤掉
    真正重要的东西；而清单是常数开销。模型要读全文就调 `read_attachment`。

    这个"清单常驻 + 按需取全文"的形状与 `retrieve_session` 完全同构 ——
    不是新机制，是同一个机制用在第二种载体上。
    """

    attachment_id: str
    filename: str
    kind: str
    chars_len: int
    #: 是否被截断过。**必须让模型知道**：不然它会以为"文件就这么多"，
    #: 然后基于一份不完整的材料下结论。
    truncated: bool
    #: 开头一小段，用来判断"这是不是我要找的那份"。
    preview: str
    #: 解析取舍说明（编码、表格拍平、不取了什么）。
    note: str


class CheckpointItem(BaseModel):
    """这一步的**验收标准与失败预案**（用户裁定要进包）。

    没有它的后果很具体：用户点开"每天过 50 个新词"说"我记不住"时，
    模型看不到这步有没有通过标准 —— 于是它没法回答"那怎么算过关"、
    也不知道失败预案里本来就写着"回到上一批词重刷三天"。
    而这两样正是讨论一步时最先该知道的。
    """

    cp_id: str
    at_step: str
    check_method: str
    pass_criteria: str
    fail_action: str | None = None
    result: str
    #: 为什么这条进包。因为"这一步的"与"判过 fail 的"是两种不同的相关，
    #: 混在一起会让界面无法解释，也会让模型分不清哪个是验收标准、哪个是历史。
    reason: str


class BaselineItem(BaseModel):
    """**一条通道一行，永不合成一个分数**（M0 §6.5 证据通道纯度）。"""

    variable_id: str
    channel: str
    score: float | None = None
    confidence: str | None = None
    completeness: str
    version_id: str


class EvidenceItem(BaseModel):
    evidence_id: str
    statement: str
    condition: str
    effect: str
    strength: str


class StrategyItem(BaseModel):
    strategy_id: str
    name: str
    solves: str | None = None
    when_to_use: str | None = None


class StepVersion(BaseModel):
    """这一步在某一版计划里的样子。**身份用 `step_id` 串起来**，
    所以"改措辞而没有换身份"这件事是可验证的。"""

    version_no: int
    action: str
    output: str | None = None
    status: PlanStepStatus


class Situation(BaseModel):
    """处境信息：动机 + 当前精力/压力/环境。**只作参考，不参与裁定。**"""

    motivation_type: str
    motivation_strength: str
    drivers: list[str] = Field(default_factory=list)
    obstacles: list[dict] = Field(default_factory=list)
    energy: str | None = None
    stress: str | None = None
    environment: str | None = None


class SectionReport(BaseModel):
    """一节的内容 + 它**排除了什么**。"""

    name: str
    #: 上下文采用序（小 = 先）。见模块文档的对照表。
    precedence: int
    item_count: int
    dropped_count: int = 0
    dropped_examples: list[str] = Field(default_factory=list)


class DiscussionContext(BaseModel):
    """一次「讨论这一步」所需的全部上下文。"""

    session_id: str
    plan_version: int
    step: StepContext
    constraints: list[PreferenceItem]
    soft_preferences: list[PreferenceItem]
    checkpoints: list[CheckpointItem]
    stage: list[StageItemRef]
    #: 用户上传给这个会话的资料清单（P6+.5）。
    attachments: list[AttachmentRef]
    baseline: list[BaselineItem]
    evidence: list[EvidenceItem]
    strategies: list[StrategyItem]
    step_history: list[StepVersion]
    situation: Situation
    sections: list[SectionReport]
    #: 采用序本身也回给调用方 —— 让"为什么这条排在那条前面"可被界面解释，
    #: 而不是只存在于本模块的 docstring 里。
    precedence_order: list[str]


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #


def _find_step(steps: list[PlanStep], step_id: str, session_id: str) -> PlanStep:
    for step in steps:
        if step.step_id == step_id:
            return step
    raise NotFound(
        f"当前版本的计划里没有步骤 {step_id}",
        detail={"session_id": session_id, "step_id": step_id},
    )


def _step_history(db: DbSession, plan: TaskPlan, step_id: str) -> list[StepVersion]:
    """这一步在**每一版**计划里的样子（升序）。

    它回答的问题是「我当初是怎么写的、改了几次」——
    而这正是讨论一个计划项时最需要、也最容易缺的东西。
    """
    history: list[StepVersion] = []
    for version_no in range(1, plan.current_version + 1):
        for row in reads.plan_steps(db, plan.plan_id, version_no=version_no):
            if row.step_id == step_id:
                history.append(
                    StepVersion(
                        version_no=version_no,
                        action=row.action,
                        output=row.output,
                        status=row.status,
                    )
                )
    return history


def _strategies_for_step(
    db: DbSession, step: PlanStep
) -> tuple[list, list[str]]:
    """这一步相关的策略：`method_ref` 命中则只用它，否则取权重最高的 4 条。

    返回 `(策略行, 未被选中的策略名)` —— 第二个值是排除清单的原料。
    """
    catalog = global_reads.list_strategies(db, limit=None)
    by_id = {row.strategy_id: row for row in catalog}
    if step.method_ref and step.method_ref in by_id:
        return [by_id[step.method_ref]], sorted(
            row.strategy_id for row in catalog if row.strategy_id != step.method_ref
        )
    selected = global_reads.list_strategies(db, limit=4)
    chosen = {row.strategy_id for row in selected}
    return selected, sorted(row.strategy_id for row in catalog if row.strategy_id not in chosen)


def assemble_discussion_context(
    db: DbSession, session_id: str, step_id: str
) -> DiscussionContext:
    """组装一次讨论所需的上下文。**纯读取、无副作用、不调 LLM。**"""
    session_row = reads.get_session(db, session_id)
    plan = reads.require_plan(db, session_id)
    steps = reads.plan_steps(db, plan.plan_id)
    step = _find_step(steps, step_id, session_id)

    sections: list[SectionReport] = []

    # ---- 0. 用户上传的资料清单（P6+.5）---- #
    #
    # **刻意排在偏好之前（precedence=0）。** 采用序的数字表示"呈现顺序"，
    # 而这里有一个具体的失败形态在驱动它：清单排在后面，模型就可能
    # 整节没注意到 —— 于是**从来不去调 `read_attachment`**，
    # 而现象是"我明明传了它却说没看到"。
    # "清单里有、它却没去取"是这个方案唯一的新失败形态，
    # 所以这一节的位置本身就是一条缓解措施。
    #
    # 它与偏好的性质不同：偏好是**长期约束**（始终在场），
    # 资料是**这一次对话直接指向的对象**。先看眼前这件事，再看长期约束。
    attachment_rows = reads.list_attachments(db, session_id)
    kept_attachments = attachment_rows[-MAX_ATTACHMENTS:]
    dropped_attachments = attachment_rows[: len(attachment_rows) - len(kept_attachments)]
    sections.append(
        SectionReport(
            name="会话资料（用户上传的清单）",
            precedence=0,
            item_count=len(kept_attachments),
            dropped_count=len(dropped_attachments),
            dropped_examples=[
                f"{row.attachment_id}（{row.filename}，超出 {MAX_ATTACHMENTS} 条上限；"
                "调 read_attachment 不带参数仍能看到它）"
                for row in dropped_attachments[:MAX_EXCLUSION_EXAMPLES]
            ],
        )
    )

    # ---- 1. 用户偏好（硬约束全进，永不截断）---- #
    hard = global_reads.list_active_constraints(db)
    soft = global_reads.list_active_soft_preferences(db)
    sections.append(
        SectionReport(
            name="用户偏好（硬约束）",
            precedence=1,
            item_count=len(hard),
            dropped_count=0,
            dropped_examples=[],
        )
    )
    # 软偏好**全部**进包，不按槽位过滤。理由是不做假：`target_field` 是
    # **计划级**的装配槽位（`SP1.presentation`），而一个 `plan_step` 上并没有
    # 槽位字段 —— 于是"这条偏好是否关于这一步"在数据上无法确定。
    # 与其用关键词猜一个"相关"，不如全给并标明它是软偏好（可推翻但需留痕）。
    sections.append(
        SectionReport(
            name="用户偏好（软偏好）",
            precedence=1,
            item_count=len(soft),
            dropped_count=0,
            dropped_examples=[],
        )
    )

    # ---- 2. 检查点：这一步的验收标准 + 任何判过 fail 的 ---- #
    #
    # 两类相关性，理由分开标出来（`CheckpointItem.reason`）：
    # 前者回答"怎么算过关"，后者回答"上次为什么没过"。
    # 后者不限于本步 —— 一次失败的检查点是**改计划的证据**（`_recorded_evidence`
    # 就是靠它），而讨论任何一步时都可能用得上它。
    all_checkpoints = reads.plan_checkpoints(db, plan.plan_id)
    relevant: list[tuple[object, str]] = []
    for cp in all_checkpoints:
        if cp.at_step == step.step_id:
            relevant.append((cp, "本步骤的验收标准"))
        elif cp.result is CheckpointResult.FAIL:
            relevant.append((cp, "判为未通过（改计划的证据）"))
    others = [cp for cp in all_checkpoints if all(cp.cp_id != row.cp_id for row, _ in relevant)]
    sections.append(
        SectionReport(
            name="检查点（本步骤的验收标准）",
            precedence=2,
            item_count=len(relevant),
            dropped_count=len(others),
            dropped_examples=[
                f"{cp.cp_id}（挂在 {cp.at_step}，本次没讨论那一步）"
                for cp in others[:MAX_EXCLUSION_EXAMPLES]
            ],
        )
    )

    # ---- 3. stage：只有"还未定归宿"的条目才相关 ---- #
    active_stage = reads.list_stage_items(
        db, session_id, statuses=frozenset({StageItemStatus.ACTIVE})
    )
    all_stage = reads.list_stage_items(db, session_id)
    settled = [item for item in all_stage if item.status is not StageItemStatus.ACTIVE]
    sections.append(
        SectionReport(
            name="stage（本次想试的）",
            precedence=3,
            item_count=len(active_stage),
            dropped_count=len(settled),
            dropped_examples=[
                f"{item.item_id}（{item.status.value}）"
                for item in settled[:MAX_EXCLUSION_EXAMPLES]
            ],
        )
    )

    # ---- 4. 基线与证据 ---- #
    baseline_map = global_reads.current_baseline_map(db)
    baseline: list[BaselineItem] = []
    irrelevant_variables = sorted(set(baseline_map) - set(PLANNING_VARIABLES))
    for variable_id in PLANNING_VARIABLES:
        for channel, row in sorted(
            baseline_map.get(variable_id, {}).items(), key=lambda kv: kv[0].value
        ):
            baseline.append(
                BaselineItem(
                    variable_id=variable_id,
                    channel=channel.value,
                    score=row.score,
                    confidence=None if row.confidence is None else row.confidence.value,
                    completeness=row.completeness.value,
                    version_id=row.version_id,
                )
            )
    sections.append(
        SectionReport(
            name="M1 基线（按通道）",
            precedence=4,
            item_count=len(baseline),
            dropped_count=len(irrelevant_variables),
            dropped_examples=[
                f"{variable_id}（不参与计划装配，M2 §13）"
                for variable_id in irrelevant_variables
            ],
        )
    )

    strategies, not_chosen = _strategies_for_step(db, step)
    evidence_all = global_reads.list_active_evidence(db)
    keywords = [row.name for row in strategies]
    if session_row.title:
        keywords.append(session_row.title)

    def _matches(item) -> bool:
        haystack = f"{item.condition}\n{item.effect}\n{item.statement}"
        return any(keyword and keyword in haystack for keyword in keywords)

    matched = [item for item in evidence_all if _matches(item)]
    unmatched = [item for item in evidence_all if not _matches(item)]
    kept = matched[:MAX_EVIDENCE]
    over_cap = matched[MAX_EVIDENCE:]
    sections.append(
        SectionReport(
            name="C 个性化证据",
            precedence=4,
            item_count=len(kept),
            dropped_count=len(unmatched) + len(over_cap),
            dropped_examples=[
                f"{item.evidence_id}（条件与本步骤的策略/任务类型不匹配）"
                for item in unmatched[:MAX_EXCLUSION_EXAMPLES]
            ]
            + [
                f"{item.evidence_id}（超出 {MAX_EVIDENCE} 条上限）"
                for item in over_cap[:MAX_EXCLUSION_EXAMPLES]
            ],
        )
    )

    sections.append(
        SectionReport(
            name="M2 策略库",
            precedence=5,
            item_count=len(strategies),
            dropped_count=len(not_chosen),
            dropped_examples=[
                f"{sid}（本步骤未选用）" for sid in not_chosen[:MAX_EXCLUSION_EXAMPLES]
            ],
        )
    )

    motivation = reads.get_motivation(db, session_id)
    snapshot = reads.get_snapshot(db, session_id)

    return DiscussionContext(
        session_id=session_id,
        plan_version=plan.current_version,
        step=StepContext(
            step_id=step.step_id,
            seq=step.seq,
            action=step.action,
            output=step.output,
            method_ref=step.method_ref,
            status=step.status,
        ),
        constraints=[
            PreferenceItem(
                pref_id=item.pref_id,
                statement=item.statement,
                priority=item.priority,
                hard=True,
            )
            for item in hard
        ],
        soft_preferences=[
            PreferenceItem(
                pref_id=item.pref_id,
                statement=item.statement,
                priority=item.priority,
                hard=False,
            )
            for item in soft
        ],
        checkpoints=[
            CheckpointItem(
                cp_id=cp.cp_id,
                at_step=cp.at_step,
                check_method=cp.check_method,
                pass_criteria=cp.pass_criteria,
                fail_action=cp.fail_action,
                result=cp.result.value,
                reason=why,
            )
            for cp, why in relevant
        ],
        stage=[
            StageItemRef(item_id=item.item_id, type=item.type.value, content=item.content)
            for item in active_stage
        ],
        attachments=[
            AttachmentRef(
                attachment_id=row.attachment_id,
                filename=row.filename,
                kind=row.kind,
                chars_len=row.chars_len,
                truncated=row.truncated,
                preview=preview(row.text),
                note=row.note,
            )
            for row in kept_attachments
        ],
        baseline=baseline,
        evidence=[
            EvidenceItem(
                evidence_id=item.evidence_id,
                statement=item.statement,
                condition=item.condition,
                effect=item.effect,
                strength=item.evidence_strength.value,
            )
            for item in kept
        ],
        strategies=[
            StrategyItem(
                strategy_id=row.strategy_id,
                name=row.name,
                solves=row.solves,
                when_to_use=row.when_to_use,
            )
            for row in strategies
        ],
        step_history=_step_history(db, plan, step.step_id),
        situation=Situation(
            motivation_type=motivation.type.value,
            motivation_strength=motivation.strength.value,
            drivers=list(motivation.drivers or []),
            obstacles=list(motivation.obstacles or []),
            energy=None if snapshot is None or snapshot.energy is None else snapshot.energy.value,
            stress=None if snapshot is None or snapshot.stress is None else snapshot.stress.value,
            environment=None if snapshot is None else snapshot.environment,
        ),
        sections=sections,
        precedence_order=[
            "0. 会话资料（用户上传的文件清单；正文按需用 read_attachment 取）",
            "1. 用户偏好（硬约束优先于软偏好；硬约束永不截断）",
            "2. 检查点（本步骤的验收标准；另外任何判过 fail 的也进 —— 那是改计划的证据）",
            "3. stage（本次想试、还没定归宿的）",
            "4. M1 基线（按通道，不合成）与 C 证据（条件能对上才进）",
            "5. M2 策略库（这一步相关的）",
        ],
    )


__all__ = [
    "DiscussionContext",
    "PLANNING_VARIABLES",
    "assemble_discussion_context",
]
