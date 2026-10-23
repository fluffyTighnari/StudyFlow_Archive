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

#: 假说的条数上限（P7）。**刻意远小于 `MAX_EVIDENCE` 的 12。**
#:
#: 两个理由，第二个是主要的：
#: 1. 假说会无限积累 —— 每句"我想试试 X"都是一条，而删不掉（归档端点待裁定，
#:    缺口 A14）。证据相反，只有真的做过的事才会留下痕迹。
#: 2. **假说的作用是"提醒"，不是"依据"。** 一屏 12 条想试的东西，
#:    模型会开始把它们当成素材来安排 —— 而 3 条、且明确标着 `verified=false`，
#:    更像"他最近提过这几件想做的事"，而不是一份可用的方法清单。
#:
#: 被截断的假说**不会消失**：`retrieve_cognitive(scope="hypothesis")` 能取全，
#: 而"我想试试之前的猜想"那句话本来就是靠这个工具满足的。
MAX_HYPOTHESES = 3


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
    """一条**已验证**的具体做法：他试过，有效。"""

    evidence_id: str
    statement: str
    condition: str
    effect: str
    strength: str


class HypothesisItem(BaseModel):
    """一条**未验证**的具体做法：他说过想试，还没试（P7）。

    ## 为什么 `verified` 是恒假的字段而不是省掉

    因为这一节与 `evidence` 那一节在结构上太像了（同样是 condition + effect），
    而**像的东西最容易被当成一样的东西用**。一个显式的
    `verified: bool = False` 让"这条不是证据"这件事出现在数据里，
    而不只出现在这一节的标题里 —— 标题会被长上下文淹没，字段不会。

    这就是用户要的"更详细的明确语义"：不是多说几句提醒，
    而是让类型自己说明它是什么。
    """

    hypothesis_id: str
    statement: str
    condition: str
    effect: str
    #: **恒为 False。** 存在的意义见上面。写成一个有默认值的字段而不是常量，
    #: 是为了让它出现在序列化结果里（模型看得见），而不是只在提示词里。
    verified: bool = False
    #: 他是在哪次会话里说的。可空（UI 手写的没有来源会话）。
    source_session_id: str | None = None


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
    #: 真正能进计划取舍的字段。
    foundation: str | None = None
    value: str | None = None
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
    #: 这次讨论锚定的那一步。**可空** —— 会话还没有计划时就是空的
    #: （"先聊两句再说"，或者"让它当场排一版"）。空着不塞假步骤：
    #: 塞一个空步骤，模型会把它当成真的第 0 步。
    step: StepContext | None
    #: 当前版本计划的**全部步骤**（含编号）。模型据此在回答里用 `[ST-001]`
    #: 指某一步，界面把它变成可点击的引用；没有这一节，它只能看见锚点那一步，
    #: 于是"指向计划里某一项"就只能靠猜编号。
    plan_steps: list[StepContext] = Field(default_factory=list)
    constraints: list[PreferenceItem]
    soft_preferences: list[PreferenceItem]
    checkpoints: list[CheckpointItem]
    stage: list[StageItemRef]
    #: 用户上传给这个会话的资料清单（P6+.5）。
    attachments: list[AttachmentRef]
    baseline: list[BaselineItem]
    evidence: list[EvidenceItem]
    #: ★ 他**想试但还没试**的做法（P7）。**不进装配** —— 它出现在这里
    #: 是为了让模型能主动提「你之前说想试试这个」，而不是为了当依据。
    #: 每条都带 `verified=False`，于是"它不是证据"这件事在数据里就成立。
    hypotheses: list[HypothesisItem]
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
    db: DbSession, step: PlanStep | None
) -> tuple[list, list[str]]:
    """这一步相关的策略：`method_ref` 命中则只用它，否则取权重最高的 4 条。

    返回 `(策略行, 未被选中的策略名)` —— 第二个值是排除清单的原料。

    `step=None`（会话还没有计划）与"这一步没指定方法"走同一条分支：
    没有 `method_ref` 就按权重取前 4 条。**不编一条 `method_ref` 出来。**
    """
    catalog = global_reads.list_strategies(db, limit=None)
    by_id = {row.strategy_id: row for row in catalog}
    method_ref = step.method_ref if step is not None else None
    if method_ref and method_ref in by_id:
        return [by_id[method_ref]], sorted(
            row.strategy_id for row in catalog if row.strategy_id != method_ref
        )
    selected = global_reads.list_strategies(db, limit=4)
    chosen = {row.strategy_id for row in selected}
    return selected, sorted(row.strategy_id for row in catalog if row.strategy_id not in chosen)


def assemble_discussion_context(
    db: DbSession, session_id: str, step_id: str | None
) -> DiscussionContext:
    """组装一次讨论所需的上下文。**纯读取、无副作用、不调 LLM。**

    ★ 没有计划、或没有给锚点时**不抛错**：`step=None`、`plan_version=0`。
    这就是"新建的会话也能开口"那条路的落点（原先是 `require_plan` → 404）。
    """
    session_row = reads.get_session(db, session_id)
    plan = reads.get_plan(db, session_id)
    steps = reads.plan_steps(db, plan.plan_id) if plan is not None else []
    step = _find_step(steps, step_id, session_id) if step_id else None

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
    all_checkpoints = reads.plan_checkpoints(db, plan.plan_id) if plan is not None else []
    relevant: list[tuple[object, str]] = []
    for cp in all_checkpoints:
        if step is not None and cp.at_step == step.step_id:
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
    #
    # ★ P7 把 `keywords` / `_matches` 提到了这里（原先定义在证据那一节）。
    # 因为现在有**两处**要问"这一条和当前步骤对不对得上"——证据与假说。
    # 两份实现迟早会对不上，而它们对不上的表现是"同一条经验在证据里进了、
    # 在假说里没进"，那种不对称没人能解释。
    strategies, not_chosen = _strategies_for_step(db, step)
    keywords = [row.name for row in strategies]
    if session_row.title:
        keywords.append(session_row.title)

    def _matches(item) -> bool:
        haystack = f"{item.condition}\n{item.effect}\n{item.statement}"
        return any(keyword and keyword in haystack for keyword in keywords)

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

    # ---- 3b. 假说：他说过想试、但还没试过的做法（P7）---- #
    #
    # **刻意紧挨着 stage（同为 precedence=3）** —— 两者是同一句话的两种形态：
    # 「想试但还没定」在这个会话里是 stage 条目，在全局认知里是假说。
    # 把它们放在一起，模型看到的就是一处对照：
    #   stage：本次想试的      → 可以现在就安排
    #   假说：以前说过想试的    → 只能提议，不能当依据
    # 而"假说"这一节存在的原因，正是用户那句「我想试试之前的猜想」——
    # 假说如果只躺在 `/api/hypotheses` 里，模型永远想不起它。
    hypotheses_all = global_reads.list_hypotheses(db)
    matched_hypotheses = [item for item in hypotheses_all if _matches(item)]
    not_matched_hypotheses = [item for item in hypotheses_all if not _matches(item)]
    kept_hypotheses = matched_hypotheses[:MAX_HYPOTHESES]
    over_cap_hypotheses = matched_hypotheses[MAX_HYPOTHESES:]
    sections.append(
        SectionReport(
            name="假说（想试但还没验证的做法）",
            precedence=3,
            item_count=len(kept_hypotheses),
            dropped_count=len(not_matched_hypotheses) + len(over_cap_hypotheses),
            dropped_examples=[
                f"{item.hypothesis_id}（与本步骤的策略/任务类型不匹配）"
                for item in not_matched_hypotheses[:MAX_EXCLUSION_EXAMPLES]
            ]
            + [
                f"{item.hypothesis_id}（超出 {MAX_HYPOTHESES} 条上限）"
                for item in over_cap_hypotheses[:MAX_EXCLUSION_EXAMPLES]
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

    evidence_all = global_reads.list_active_evidence(db)
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

    sections.append(
        SectionReport(
            name="计划全貌（步骤编号）",
            precedence=6,
            item_count=len(steps),
            dropped_count=0,
        )
    )

    return DiscussionContext(
        session_id=session_id,
        plan_version=plan.current_version if plan is not None else 0,
        step=(
            StepContext(
                step_id=step.step_id,
                seq=step.seq,
                action=step.action,
                output=step.output,
                method_ref=step.method_ref,
                status=step.status,
            )
            if step is not None
            else None
        ),
        plan_steps=[
            StepContext(
                step_id=row.step_id,
                seq=row.seq,
                action=row.action,
                output=row.output,
                method_ref=row.method_ref,
                status=row.status,
            )
            for row in steps
        ],
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
        hypotheses=[
            HypothesisItem(
                hypothesis_id=item.hypothesis_id,
                statement=item.statement,
                condition=item.condition,
                effect=item.effect,
                verified=False,
                source_session_id=item.source_session_id,
            )
            for item in kept_hypotheses
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
        step_history=(
            _step_history(db, plan, step.step_id) if plan is not None and step is not None else []
        ),
        situation=Situation(
            motivation_type=motivation.type.value,
            motivation_strength=motivation.strength.value,
            foundation=motivation.foundation,
            value=motivation.value,
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
            "3. stage（本次想试、还没定归宿的）与假说（他以前说过想试、还没验证的做法）",
            "4. M1 基线（按通道，不合成）与 C 证据（条件能对上才进）",
            "5. M2 策略库（这一步相关的）",
        ],
    )


__all__ = [
    "DiscussionContext",
    "PLANNING_VARIABLES",
    "assemble_discussion_context",
]
