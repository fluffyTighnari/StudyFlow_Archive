"""M2 §3.2 B 策略候选库（16 条，种子）—— **参考而非约束**。

M2 §3.3 使用规则：
1. B 是参考不是约束：可选/组合/改造/不采用，均须在 `method_choice` 说明理由。
2. `evidence_strength=high` 默认优先（SP3），可被任务情况推翻并留痕。
3. 新增策略须注册 `strategy_id` 并补全四字段。

`default_weight` 由 `evidence_strength` 派生，仅为默认排序用，不是硬规则。
"""

from __future__ import annotations

from typing import Any

from app.global_domain.enums import StrategyStrength

#: evidence_strength → 默认权重（仅供排序）。
_WEIGHT: dict[StrategyStrength, int] = {
    StrategyStrength.HIGH: 8,
    StrategyStrength.MEDIUM: 5,
    StrategyStrength.LOW_MEDIUM: 4,
    StrategyStrength.LOW: 2,
}


def _s(
    strategy_id: str,
    name: str,
    solves: str,
    when_to_use: str,
    cost: str,
    adjust_signals: str,
    evidence_strength: str,
) -> dict[str, Any]:
    strength = StrategyStrength(evidence_strength)
    return {
        "strategy_id": strategy_id,
        "name": name,
        "solves": solves,
        "when_to_use": when_to_use,
        "cost": cost,
        "adjust_signals": adjust_signals,
        "evidence_strength": strength,
        "default_weight": _WEIGHT[strength],
        "catalog_version": "v0.3",
    }


STRATEGIES: list[dict[str, Any]] = [
    _s("retrieval_practice", "主动回忆",
       "记忆留存弱、看懂但用不出", "记忆/理解类巩固阶段", "中（挫败感高）",
       "连续受挫且无提示仍无法提取 → 降难度回填输入", "high"),
    _s("example_guided", "示例引导",
       "抽象概念无法具象化", "首次接触抽象概念", "低",
       "只会照抄无法变形 → 追加变式练习", "medium"),
    _s("stepwise_practice", "逐步练习",
       "技能步骤多、易淹没", "技能实操、流程类", "低-中",
       "步骤熟练但整体卡顿 → 整合性练习", "medium"),
    _s("project_practice", "项目实践",
       "学了不会用、无真实场景", "综合应用、动机低迷期", "高",
       "陷入细节无法收束 → 拆小里程碑", "medium"),
    _s("comparative_analysis", "对比分析",
       "概念混淆、边界不清", "相似概念辨析", "中",
       "对比表做完仍无法迁移 → 换输出形式", "medium"),
    _s("reflection_review", "反思复盘",
       "行动无改进、重复踩坑", "每次 Session 后", "低",
       "复盘流于形式 → 模板引导式复盘", "high"),
    _s("spaced_repetition", "间隔重复",
       "长时记忆衰减", "事实记忆类跨周/月", "中（需排程）",
       "复习挤压新学时间 → 调整间隔/缩减清单", "high"),
    _s("interleaving", "交错练习",
       "题型固化、迁移差", "问题求解、多技能", "中",
       "错误率持续过高 → 暂时回分块", "high"),
    _s("self_explanation", "自解释",
       "理解浮于表面", "理论/代码阅读", "中",
       "解释中说不清 → 定位缺口补输入", "high"),
    _s("elaboration", "精细加工",
       "知识孤立、难关联", "新旧知识连接", "中",
       "关联牵强 → 回基础概念", "medium"),
    _s("dual_coding", "双重编码",
       "抽象信息难保持", "结构性内容", "中",
       "画图占用过多 → 降图密度", "medium"),
    _s("global_modeling", "全局建模",
       "只见树木不见森林", "系统/架构首次概览", "低-中",
       "框架与实际脱节 → 边学边修订", "medium"),
    _s("sequential_walkthrough", "线性分步",
       "流程/推导需顺序", "链式逻辑、语法、算法", "低",
       "顺序迷失全局 → 先建骨架", "medium"),
    _s("case_based", "案例驱动",
       "抽象概念无落点", "理论×真实场景", "低-中",
       "案例替代概念本身 → 补抽象归纳", "medium"),
    _s("hands_on_trial", "动手试错",
       "纸上谈兵、无体感", "技能/工具类", "中",
       "试错无反思 → 强制加复盘", "medium"),
    _s("peer_discussion", "讨论输出",
       "缺外部视角、表达不清", "理解校验、观点形成", "中",
       "讨论偏离主题 → 预设提纲", "low-medium"),
]
