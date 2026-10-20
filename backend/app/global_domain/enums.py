"""全局域契约枚举 —— 数据库列、Pydantic 契约、路由判定共用**同一份**定义。

单一来源的意义：precheck 判定表（M0 §5.3）里的取值不允许在别处手写字符串常量，
否则策略表与实际存储会静默漂移。
"""

from __future__ import annotations

from enum import StrEnum


# --------------------------------------------------------------------------- #
# M1 认知基准
# --------------------------------------------------------------------------- #


class ConstructType(StrEnum):
    """M1 §2：11 个变量分三类，决定证据通道与更新规则。"""

    TRAIT = "trait"
    PREFERENCE = "preference"
    STRATEGY_OPTION = "strategy_option"


class EvidenceChannel(StrEnum):
    """M1 §3.2：三种证据类型各自独立通道，互不换算、不取均值。"""

    MEASURED = "measured"
    EXPERIENCED = "experienced"
    STATED = "stated"


class BaselineConfidence(StrEnum):
    """Benchmark §6 输出契约的置信标注。"""

    HYPOTHESIS = "hypothesis"
    PROVISIONAL = "provisional"
    CALIBRATED = "calibrated"


class Completeness(StrEnum):
    """测量执行到哪一级 —— **逐 `(变量, 通道)` 判定**（M0 §6.6）。

    - `complete`：该通道该测的方法已执行完
    - `partial`：该通道该测但尚未测完（含尚无任何版本）
    - `not_applicable`：该通道**按设计就不设测量方法**（如 V-CP 的 measured 通道）

    三者互斥且完备——「不适用」与「没做完」是两件事，混为一谈会让 UI 永远显示
    「未完成」，从而逼用户去做一个设计上就不该做的测量。
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


class MethodKind(StrEnum):
    """M1 §1：五类测量方法。"""

    SELF_REPORT = "self_report"
    RETROSPECTIVE = "retrospective"
    BEHAVIORAL_TASK = "behavioral_task"
    SESSION_OBSERVATION = "session_observation"
    EXTERNAL_EVIDENCE = "external_evidence"


class MethodPriority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class MethodUse(StrEnum):
    INITIAL = "initial"
    INCREMENTAL = "incremental"
    BOTH = "both"


# --------------------------------------------------------------------------- #
# 通用：证据基础与强度
# --------------------------------------------------------------------------- #


class Basis(StrEnum):
    """UpdateIntent.basis（路由层 §4）。inferred 永不写参数。"""

    STATED = "stated"
    MEASURED = "measured"
    EXPERIENCED = "experienced"
    INFERRED = "inferred"


#: **偏好的硬度**：哪些 `basis` 算「用户下达的约束」而不是「LLM 推断出的倾向」。
#:
#: 缺口 A18（用户裁定）：`stated` 是用户自己敲的字（R1 直写，或 R3 里他确认过的
#: 「确信好用的方法」），因此**不可被 LLM 静默推翻** —— M2 红线 2 规定
#: 「HC1–HC4 优先于任何软偏好」。`experienced` / `inferred` 是 LLM 观察或推断的，
#: 属**软偏好 / 候选**（A11/A12 让它们以 needs_confirmation 进入库）。
#:
#: 放在 `enums.py` 而不是消费侧，是因为**这个集合是取值域的一部分**：
#: 它必须只有一份定义，否则「哪些声明不可被推翻」会散落成多份，
#: 而其中任何一份写错都不会报错，只会让 LLM 悄悄推翻用户的话。
#: 与 `INTENT_TRIGGERS`（本文件）同为「派生自枚举的判定集合」。
HARD_CONSTRAINT_BASIS: frozenset[Basis] = frozenset({Basis.STATED})


class LLMConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CEvidenceStrength(StrEnum):
    """M2 §4.2：C 个性化证据的强度。"""

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


class StrategyStrength(StrEnum):
    """M2 §3.1：B 策略库的 evidence_strength。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    LOW_MEDIUM = "low-medium"


class CEvidenceSource(StrEnum):
    """M2 §4.2：C 条目来源。"""

    USER_DIRECT = "user_direct"
    SESSION_DERIVED = "session_derived"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ChannelObsSourceKind(StrEnum):
    """`m1_channel_obs.source_kind` 的取值域。

    原先这里是自由文本，于是「该通道有几条**独立 Session 证据**」这个判断
    （M0 §7.3 的 ≥2 硬前置条件）就依赖一个字符串约定 —— 而字符串约定无法被
    测试保护：写入方写成 `"Session"` / `"session_obs"` 都不会报错，
    只会让证据条数静默为 0，进而让一条本该通过的写入被永久拦下。
    改成枚举后，写入方根本拼不错。
    """

    SESSION = "session"
    BENCHMARK = "benchmark"
    USER_UI = "user_ui"
    EXTERNAL = "external"


# --------------------------------------------------------------------------- #
# 路由层：提案 / 意图 / 日志
# --------------------------------------------------------------------------- #


class TriggerType(StrEnum):
    """路由层 §5：四类**更新方式**（R4 细分三种触发时机），外加一个非 intent 驱动的 R5。

    R1–R4 都是「一次更新的成因」，它们都会产出 `UpdateIntent` 并走 §5.3 判定表。
    R5 不是：回滚不产生新取值，它引用一个**已存在**的版本。

    **为什么不把回滚塞进 R1**：R1 的语义是「用户 UI 直写**偏好**」（M2/C），
    而回滚写的是 M1 的版本链。混用之后，「这个季度用户直接写了几次偏好」
    与「他回滚了几次基线」会变成同一个计数，而这两件事的审计含义完全不同。
    宁可多一个取值，也不让一个计数承载两种事实。
    """

    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4A = "R4a"
    R4B = "R4b"
    R4C = "R4c"
    #: 回滚：用户把某 `(变量, 通道)` 的当前版本指回一个旧版本（生成新版本，不删历史）。
    R5 = "R5"
    #: 声明式写入：用户对自己状态的**直接声明**，而它不产生 `UpdateIntent`。
    #: 目前只有一处 —— 领域勾选清单（「我熟悉哪些专题」）。
    R6 = "R6"


#: 不产出 `UpdateIntent`、因此**不查判定表**的触发类型。
#:
#: 它们的共同点是：携带的**不是**「一个新的取值」，而是别的东西 ——
#: R5 携带「一个已存在版本的引用」，R6 携带「一份测量参照」。
#: 没有 `(action, target, basis)` 三元组，判定表就无从查起。
NON_INTENT_TRIGGERS: frozenset[TriggerType] = frozenset(
    {TriggerType.R5, TriggerType.R6}
)

#: 会产出 `UpdateIntent` 的触发类型 —— 也就是**必须**能在 §5.3 判定表里查到结论的那些。
#: 派生自枚举而非手写清单：新增一个 intent 驱动的触发时会自动要求补表（有测试断言）。
INTENT_TRIGGERS: frozenset[TriggerType] = frozenset(TriggerType) - NON_INTENT_TRIGGERS


class ProposalSource(StrEnum):
    """路由层 §7 RouterLog.source 的取值域。"""

    USER_UI = "user_ui"
    BASELINE = "baseline"
    IDEA_LLM = "idea_llm"
    SESSION_SUMMARY = "session_summary"


class ProposalStatus(StrEnum):
    """M0 §5.1 状态机。partially_applied 必须存在：一次提多条、用户只同意部分。"""

    PROPOSED = "proposed"
    PENDING_CONFIRMATION = "pending_confirmation"
    APPLIED = "applied"
    PARTIALLY_APPLIED = "partially_applied"
    REJECTED = "rejected"
    EXPIRED = "expired"


class IntentAction(StrEnum):
    """一条 intent 想对全局认知做什么。

    ## P7.1：补齐 C 家族的生命周期（用户裁定）

    用户原话：「假说应该支持删除啊，我们前面不是说好了偏好/方法这种可以手动编辑吗，
    因为**解释权永远在用户手里**」。

    于是这里从 3 个值变成 6 个值。**这不是"加了三个功能"** —— 它们是同一句话的
    三个必然推论：认知库里的东西是用户的东西，那他必须能说"这个不要了"
    （`ARCHIVE`）、"我刚才删错了"（`RESTORE`）、"我说得不对，改成这个"（`REVISE`）。

    ## 为什么不能复用 `add_preference`

    `add_preference` 是**插入**：`max_rows=1`，每次产出一行新记录。
    用它表达"改这条"会让 id 漂移 —— 而 `router_log.target` 记的是地址，
    历史日志会指向一条语义已经变了的新行（I2 要求日志指向**真实存在**的全局地址，
    它管不了"那条地址现在装的是另一件事"）。

    分开的收益很具体：**插入和改写是两种意图，模型填错动作比填错 id 更容易被看出来。**
    """

    ADD_PREFERENCE = "add_preference"
    UPDATE_PARAMETER = "update_parameter"
    NO_CHANGE = "no_change"
    #: 退场。**行还在，状态改 `archived`** —— 不是物理删除。
    #:
    #: 物理删除会让 `router_log` 里那条 `applied` 指向空气，而 I2 的全部意义
    #: 就是"改了什么都能追回来"。所以「删除」在协议层的含义是"退场"，
    #: 用户看到的是一次删除，系统留下的是一条可以撤销的状态变更。
    ARCHIVE = "archive"
    #: 撤销退场（`archived → active`）。
    #:
    #: 没有它，`ARCHIVE` 就是单向的 —— 一次误点会让一条认知永久消失于视野，
    #: 而"解释权在用户手里"显然也包括"我改主意了"。
    RESTORE = "restore"
    #: 改写已有条目（三个槽整体替换）。
    #:
    #: **整体替换而不是逐字段补丁**：补丁需要一套"哪些字段可以不动"的规则，
    #: 而 `condition` 是必填的结构判据 —— 允许"不动它"就等于允许一条没有场景的
    #: 假说继续存在。整体替换让"提交的表单就是它的全部"。
    REVISE = "revise"


class Precheck(StrEnum):
    """M0 §5.3 的三种判定结果。"""

    AUTO_APPLY = "auto_apply"
    NEEDS_CONFIRMATION = "needs_confirmation"
    MUST_REJECT = "must_reject"


class IntentStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class RouterLogStatus(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"
    LOGGED_ONLY = "logged_only"


class AdvisoryLevel(StrEnum):
    """M0 §5.4 重做建议的级别。

    **故意没有「阻断」这一级。** 重做基准测试永不被拒绝——基线本来是拿来校准的。
    建议只负责让用户看见代价，不替用户做决定（原则 P3：用户是最高权威）。
    """

    NONE = "none"
    SUGGEST = "suggest"
    WARN = "warn"


# --------------------------------------------------------------------------- #
# 作用域常量
# --------------------------------------------------------------------------- #

#: M1 中「偏好类」变量：只影响呈现/活动形态，不承诺绩效（M1 §4 偏好类红线）。
M1_PREFERENCE_VARIABLES: frozenset[str] = frozenset({"V-SMP", "V-ACT", "V-OSP", "V-CP"})

#: M1 中「trait」变量：能力/行为/环境类，需要 ≥2 条独立 Session 证据或完整重测。
M1_TRAIT_VARIABLES: frozenset[str] = frozenset(
    {"V-SE", "V-HSR", "V-DC", "V-IR", "V-PAR", "V-ENV", "V-ATT"}
)

ALL_M1_VARIABLES: frozenset[str] = M1_PREFERENCE_VARIABLES | M1_TRAIT_VARIABLES


# --------------------------------------------------------------------------- #
# Benchmark 基准测验（Benchmark v0.3）
# --------------------------------------------------------------------------- #


class BenchmarkForm(StrEnum):
    """两种卷别（Benchmark v0.3 §3）。

    它们**不是**难易两档，而是两种不同的仪器：
    快速卷用一个复用任务承担 4 个 trait 的观测（省时，代价是互相污染，
    置信封顶 `provisional`）；全面卷用独立任务（干净）外加一个真实学习闭环。
    """

    QUICK = "quick"
    FULL = "full"


class BenchmarkRunStatus(StrEnum):
    """一次基准运行的生命周期（用户要求：可中断保存退出、可继续、可重开）。

    ★ **为什么需要显式状态，而不靠 `finished_at IS NULL`**：
    「还在做」与「我存了盘要走」在 `finished_at` 上是**同一个值** —— 都是 NULL。
    但它们对用户的含义相反，而且决定了下次进入时那个问题的答案：
    「继续上次，还是重新开始？」

    只有 NULL 时，系统无法回答这个问题，于是只剩两条坏路：
    一律当作"继续"（用户明明想重开，却被塞回半截的旧运行），
    或一律当作"过期"（用户存了盘第二天回来，发现自己白做了一半）。
    一个字段分不出两件事时，产品就只能在两种错误里选一种。

    `ABANDONED` 单独存在同理：用户点"重新开始"之后，旧运行不是"没做完"，
    而是**被明确作废的**。混进 `INTERRUPTED` 会让「他中途放弃过几次」
    这个事实永远读不出来。
    """

    #: 正在进行。
    IN_PROGRESS = "in_progress"
    #: 用户主动"保存并退出" —— 可继续。
    INTERRUPTED = "interrupted"
    #: 正常走完并算出了分数。
    FINISHED = "finished"
    #: 用户选择重新开始，这一次被明确作废。**不可继续。**
    ABANDONED = "abandoned"


class ResponseQuestionKind(StrEnum):
    """一道题的计分规则（Benchmark v0.3 §5.2）。

    ★ `EXACT_BLANK` 与 `EXACT` **必须分开记**：
    多空填空题按「对=10/错=0」给分，会让"两空全对"与"对一空"拿一样的分
    —— 分数失去分辨力，而这个损失不会报错。
    分开记之后，回看一条分数时能知道**是哪种规则算出来的**。
    """

    #: 客观题：对 = 10，错 = 0。
    EXACT = "exact"
    #: 多空填空：`10 × 命中空数 / 总空数`。
    EXACT_BLANK = "exact_blank"
    #: 要点题：`10 × 命中要点数 / 要点总数`。
    KEY_POINTS = "key_points"
    #: 行为观测：由**明示换算表**把原始量（秒/次数）映射到 0-10。
    OBSERVATION = "observation"
    #: 自评量表（P11）：`10 × 各句点数的均值`，选项权重由**题目**声明。
    #:
    #: ★ 它产出读数（进 `stated` 通道）。红线 7 针对的是**正误题** ——
    #: 正误题的分数随能力移动，而自评问的是"你平时怎样"，不随能力移动。
    #: 把它也排除掉，`stated` 通道会永远拿不到任何读数，
    #: 而那看起来只是"还没测"。
    SELF_REPORT = "self_report"


class JudgedBy(StrEnum):
    """单题分是谁给的。

    这是一个**审计字段**，不是一个偏好。它存在的理由：
    分数会被当成"关于这个人的断言"消费，因此必须能回答
    「这句话是谁说的」。`LLM` 尤其需要 —— 它带 `judge_model` 与
    `judge_note`（判官的原话 + 它声明的命中要点），
    而命中声明要经过**引文核对**（Benchmark v0.3 §5.2 的 `key_points`）。
    """

    #: 由代码按答案/换算表算出。可复算。
    CODE = "code"
    #: 由 LLM 按 `scoring_guide` 判。**必须附 judge_model 与命中声明。**
    LLM = "llm"
    #: 由用户自己标记。
    HUMAN = "human"
