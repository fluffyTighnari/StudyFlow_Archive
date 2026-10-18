"""M1 认知基准的 11 个变量定义（种子）—— 对齐 M1 v0.4 §1/§2/§4。

红线（M1 §5.1）：**测量目标必须链接至少 1 个 P1 方法**，否则变量不落库。
该约束由 `tests/test_seeds.py` 对全部 11 个变量断言。

测量材料由用户寻找/自制；本文件只固化框架、方法优先级与干预钩子。
"""

from __future__ import annotations

from typing import Any

from app.global_domain.enums import ConstructType, EvidenceChannel


def _m(
    method_id: str,
    kind: str,
    name: str,
    priority: str,
    used_for: str = "both",
    *,
    description: str = "",
    maps_to_score: str = "",
    time_min: int = 0,
    material: str = "",
) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "kind": kind,
        "name": name,
        "description": description,
        "cost": {"time_min": time_min, "material": material},
        "priority": priority,
        "maps_to_score": maps_to_score,
        "basis": {
            "self_report": "stated",
            "retrospective": "experienced",
            "session_observation": "experienced",
            "behavioral_task": "measured",
            "external_evidence": "measured",
        }[kind],
        "used_for": used_for,
    }


# --------------------------------------------------------------------------- #
# trait（7）
# --------------------------------------------------------------------------- #

_TRAITS: list[dict[str, Any]] = [
    {
        "variable_id": "V-SE",
        "name": "signal_extraction",
        "display_name": "信号提取",
        "definition_what": "在复杂/密集信息背景中提取核心信号的能力倾向。",
        "definition_what_not": "不是阅读速度，也不是领域知识量；不测「看过多少」，只测「抓到什么」。",
        "scale_low_anchor": "易被细节淹没，抓不住主线与关键条件",
        "scale_high_anchor": "快速识别主线、关键事实与易忽略的限定条件",
        "measurement": {
            "goal": "在复杂/密集信息背景中提取核心信号的能力倾向。",
            "methods": [
                _m("M-SE-01", "self_report", "行为指标自评", "P1",
                   description="3-5 条行为描述，条目均值 → 0-10", maps_to_score="条目均值→0-10", time_min=3),
                _m("M-SE-02", "retrospective", "历史回溯", "P1",
                   description="4-6 个过往事件；偏差≥3 以回溯为准", maps_to_score="事件提取表现", time_min=4),
                _m("M-SE-03", "behavioral_task", "嵌入图形任务（EFT 变体）", "P2",
                   description="材料用户找；正确率/用时换算", maps_to_score="正确率/用时换算", time_min=8, material="用户寻找"),
                _m("M-SE-04", "behavioral_task", "文本提取对照", "P2",
                   description="全局/细节提取表现差", maps_to_score="表现差", time_min=6, material="用户寻找"),
                _m("M-SE-05", "session_observation", "Session 观测", "P1", "incremental",
                   description="review.extraction_efficiency 累积", maps_to_score="累积观测", time_min=0),
                _m("M-SE-06", "external_evidence", "外部证据", "P3",
                   description="信息密集成果/他人反馈", time_min=0),
            ],
            "evidence_channels": {
                "measured": "行为任务正确率/用时",
                "experienced": "跨 Session 提取效率观测累积",
                "stated": "行为指标自评",
            },
            "update_rule": "任务复测 或 跨 Session 提取效率观测累积；自评分 ≠ 任务分，不取均值",
            "update_frequency": "low",
        },
        "intervention_hooks": {"low": "材料预处理/重点提取步骤", "high": "可减少辅助结构"},
    },
    {
        "variable_id": "V-HSR",
        "name": "help_seeking_resistance",
        "display_name": "求助阻力",
        "definition_what": "遇困难时独立解决 vs 主动求助的模式。",
        "definition_what_not": "不是「爱不爱问」，而是遇到阻塞时的实际行为路径。",
        "scale_low_anchor": "长时间卡住也不求助，宁可停滞",
        "scale_high_anchor": "先自行攻坚，卡住后及时定向求助",
        "measurement": {
            "goal": "遇困难时独立解决 vs 主动求助的模式。",
            "methods": [
                _m("M-HSR-01", "self_report", "行为指标自评", "P1", time_min=2),
                _m("M-HSR-02", "retrospective", "困难事件回溯（近 10 个）", "P1",
                   description="求助延迟/求助前做了什么/求助对象/是否解决", time_min=4),
                _m("M-HSR-03", "behavioral_task", "求助观察任务", "P2", material="用户寻找", time_min=6),
                _m("M-HSR-04", "external_evidence", "他人反馈", "P3", time_min=0),
            ],
            "evidence_channels": {
                "measured": "求助观察任务中的实际求助时点",
                "experienced": "≥2 条独立 Session 行为记录",
                "stated": "自评",
            },
            "update_rule": "≥2 条独立 Session 行为记录累积；不把「一次体验感受」当行为证据",
            "update_frequency": "medium",
        },
        "intervention_hooks": {"low": "独立尝试时间（先自行攻坚再求助）", "high": "强制反馈点（定时外部校验）"},
    },
    {
        "variable_id": "V-DC",
        "name": "decision_caution",
        "display_name": "决策审慎",
        "definition_what": "决策前评估选项的倾向。",
        "definition_what_not": "不测决策正确率；V-DI（冲动）为同一维度另一端，不单独设变量。",
        "scale_low_anchor": "快速拍板，较少比较选项",
        "scale_high_anchor": "习惯先列举选项与后果再决定",
        "measurement": {
            "goal": "决策前评估选项的倾向。",
            "methods": [
                _m("M-DC-01", "self_report", "行为指标自评", "P1", time_min=2),
                _m("M-DC-02", "retrospective", "历史回溯（决策延迟/选项数）", "P1", time_min=4),
                _m("M-DC-03", "behavioral_task", "决策任务", "P2",
                   description="决策时长/修改次数", material="用户寻找", time_min=6),
                _m("M-DC-04", "session_observation", "Session 决策观测", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "决策任务时长/修改次数",
                "experienced": "Session 内决策过程观测",
                "stated": "自评",
            },
            "update_rule": "≥2 条独立 Session 行为记录累积；不把一次体验感受当行为证据",
            "update_frequency": "medium",
        },
        "intervention_hooks": {"low": "行动前检查清单（防冲动决策）", "high": "决策时限 + 试错配额"},
    },
    {
        "variable_id": "V-IR",
        "name": "initiation_resistance",
        "display_name": "启动阻力",
        "definition_what": "开始任务的心理阻力。",
        "definition_what_not": "不测拖延的道德评价；只测「决定 → 启动」的耗时与准备行为。",
        "scale_low_anchor": "决定后长时间无法开始，需要大量准备动作",
        "scale_high_anchor": "决定后能立即进入最小可执行动作",
        "measurement": {
            "goal": "开始任务的心理阻力。",
            "methods": [
                _m("M-IR-01", "self_report", "行为指标自评", "P1", time_min=2),
                _m("M-IR-02", "retrospective", "启动回溯（最近 3 次任务）", "P1",
                   description="决定→启动耗时、准备、延迟原因；无启动≠阻力", time_min=3),
                _m("M-IR-03", "behavioral_task", "启动观察（内嵌可选任务）", "P2", material="用户寻找", time_min=5),
                _m("M-IR-04", "session_observation", "Session 启动延迟观测", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "启动观察任务中的实际启动延迟",
                "experienced": "Session 内启动延迟记录",
                "stated": "自评",
            },
            "update_rule": "≥2 条独立 Session 行为记录累积；不把一次体验感受当行为证据",
            "update_frequency": "medium",
        },
        "intervention_hooks": {"low": "无需特别干预", "high": "开始时间锁 + 最小启动步（5 分钟规则）"},
    },
    {
        "variable_id": "V-PAR",
        "name": "post_action_reflection",
        "display_name": "行动后反思",
        "definition_what": "行动后复盘提取教训的习惯。",
        "definition_what_not": "不测「想得多不多」；只测复盘是否产出可执行的下一步改变。",
        "scale_low_anchor": "行动结束即翻篇，不提取教训",
        "scale_high_anchor": "习惯记录预期差异并给出下次改变",
        "measurement": {
            "goal": "行动后复盘提取教训的习惯。",
            "methods": [
                _m("M-PAR-01", "self_report", "行为指标自评", "P1", time_min=2),
                _m("M-PAR-02", "retrospective", "历史回溯（复盘频率/产出质量）", "P1", time_min=3),
                _m("M-PAR-03", "external_evidence", "复盘产出质量评估", "P3", time_min=0),
            ],
            "evidence_channels": {
                "measured": "复盘产物的四要素完整度",
                "experienced": "Session 复盘字段累积",
                "stated": "自评",
            },
            "update_rule": "≥2 条独立 Session 行为记录累积；不把一次体验感受当行为证据",
            "update_frequency": "medium",
        },
        "intervention_hooks": {"low": "强制内置复盘检查点", "high": "复盘设为可选"},
    },
    {
        "variable_id": "V-ENV",
        "name": "environment_sensitivity",
        "display_name": "环境敏感度",
        "definition_what": "注意力受环境干扰的程度。",
        "definition_what_not": "单次分心 ≠ 高敏感；无相关经历记为未知，靠 Session 记录补。",
        "scale_low_anchor": "在有干扰的环境下仍能维持产出",
        "scale_high_anchor": "轻微声响/通知即中断，且恢复慢",
        "measurement": {
            "goal": "注意力受环境干扰的程度。",
            "methods": [
                _m("M-ENV-01", "retrospective", "跨场景回溯（安静/有人活动/易受通知）", "P1",
                   description="专注中断、恢复时间、完成情况", time_min=2),
                _m("M-ENV-02", "self_report", "跨场景自评", "P1", time_min=2),
                _m("M-ENV-03", "session_observation", "跨 Session 统计", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "跨场景实测任务——M1 §2 的 P2 列为「—」，当前无方法覆盖",
                "experienced": "跨 Session 中断统计",
                "stated": "跨场景自评",
            },
            "update_rule": "V-ENV 跨 Session 统计；单次分心 ≠ 敏感",
            "update_frequency": "medium",
        },
        "intervention_hooks": {"low": "环境约束可放宽", "high": "默认低干扰环境/降噪措施"},
    },
    {
        "variable_id": "V-ATT",
        "name": "typical_attention_span",
        "display_name": "典型专注时长",
        "definition_what": "可维持高质量专注的时长区间。",
        "definition_what_not": "25min 测绘内不测上限；输出为暂定区间或未知。",
        "scale_low_anchor": "不足 15 分钟即明显掉线",
        "scale_high_anchor": "可维持 45 分钟以上高质量专注",
        "measurement": {
            "goal": "可维持高质量专注的时长区间。",
            "methods": [
                _m("M-ATT-01", "self_report", "自评区间", "P1", time_min=2),
                _m("M-ATT-02", "retrospective", "历史专注统计", "P1",
                   description="何时下降、中断后能否恢复；有记录算中位数", time_min=3),
                _m("M-ATT-03", "behavioral_task", "专注时长任务", "P2", material="用户寻找", time_min=10),
                _m("M-ATT-04", "session_observation", "Session 滚动统计", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "专注时长任务实测",
                "experienced": "Session 滚动中位数",
                "stated": "自评区间",
            },
            "update_rule": "V-ATT 中位数滚动；单次专注 ≠ 稳定时长",
            "update_frequency": "medium",
        },
        "intervention_hooks": {
            "low": "用时长的任务按下界切分 timebox",
            "high": "—",
        },
    },
]


# --------------------------------------------------------------------------- #
# preference（2）与 strategy_option（2）
# --------------------------------------------------------------------------- #

_PREFERENCES: list[dict[str, Any]] = [
    {
        "variable_id": "V-CP",
        "name": "context_material_preference",
        "display_name": "情境材料偏好",
        "definition_what": "情境案例 vs 抽象材料的舒适偏好。",
        "definition_what_not": "偏好 ≠ 绩效；该变量只用于呈现层优化，不承诺学习效果增益。",
        "scale_low_anchor": "偏好抽象定义与形式化表述",
        "scale_high_anchor": "偏好具体案例与真实场景切入",
        "measurement": {
            "goal": "情境案例 vs 抽象材料的舒适偏好。",
            "methods": [
                _m("M-CP-01", "self_report", "双端自评（抽象/案例舒适度）", "P1", time_min=1),
                _m("M-CP-02", "behavioral_task", "成对双版本选择", "P2",
                   description="同概念、信息量相近", material="用户寻找", time_min=3),
                _m("M-CP-03", "session_observation", "Session 体验分累积", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "成对双版本选择中的实际选择倾向（同概念、信息量相近）",
                "experienced": "Session 体验分",
                "stated": "双端舒适度自评",
            },
            "update_rule": "R3/R4 沉淀低频更新；偏好 ≠ 绩效，不与能力证据混算",
            "update_frequency": "low",
        },
        "intervention_hooks": {
            "low": "按偏好调呈现/活动配比（不承诺绩效、不豁免 mandatory）",
            "high": "同左",
        },
    },
    {
        "variable_id": "V-SMP",
        "name": "sensory_modality_preference",
        "display_name": "感官媒介偏好",
        "definition_what": "图表/听觉/读写/动觉四类媒介的舒适度。",
        "definition_what_not": "不安排四任务实测（P3）；只用于呈现层优化。",
        "scale_low_anchor": "该媒介下明显不适或效率低",
        "scale_high_anchor": "该媒介下最舒适、最愿主动使用",
        "measurement": {
            "goal": "图表/听觉/读写/动觉舒适度。",
            "methods": [
                _m("M-SMP-01", "self_report", "四维舒适度自评", "P1", time_min=1),
                _m("M-SMP-02", "behavioral_task", "呈现选择任务", "P2", material="用户寻找", time_min=3),
                _m("M-SMP-03", "session_observation", "Session 媒介体验分", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "呈现选择任务中的实际选择",
                "experienced": "Session 媒介体验分",
                "stated": "四维舒适度自评",
            },
            "update_rule": "R3/R4 沉淀低频更新；偏好 ≠ 绩效",
            "update_frequency": "low",
        },
        "intervention_hooks": {
            "low": "按偏好调呈现/活动配比（不承诺绩效、不豁免 mandatory）",
            "high": "同左",
        },
    },
]

_STRATEGY_OPTIONS: list[dict[str, Any]] = [
    {
        "variable_id": "V-OSP",
        "name": "organizing_strategy_preference",
        "display_name": "组织策略偏好",
        "definition_what": "全局建模 vs 线性分步切入（绩效驱动）。",
        "definition_what_not": "偏好与绩效必须分开记录；不作为稳定基线（M1 §5.3）。",
        "scale_low_anchor": "偏好从第一步开始线性推进",
        "scale_high_anchor": "偏好先建立整体框架再填充细节",
        "measurement": {
            "goal": "全局建模 vs 线性分步切入（绩效驱动）。",
            "methods": [
                _m("M-OSP-01", "self_report", "自评", "P1", time_min=1),
                _m("M-OSP-02", "retrospective", "过往绩效反推", "P1", time_min=2),
                _m("M-OSP-03", "behavioral_task", "成对双版本 + 组织任务", "P2",
                   description="偏好与绩效分开记录", material="用户寻找", time_min=5),
                _m("M-OSP-04", "session_observation", "Session 绩效观测", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "组织任务绩效",
                "experienced": "Session 绩效观测",
                "stated": "自评",
            },
            "update_rule": "R3/R4 沉淀低频更新；strategy_option 类不作为稳定基线",
            "update_frequency": "low",
        },
        "intervention_hooks": {
            "low": "按偏好调呈现/活动配比（不承诺绩效、不豁免 mandatory）",
            "high": "同左",
        },
    },
    {
        "variable_id": "V-ACT",
        "name": "activity_strategy_library",
        "display_name": "活动策略库",
        "definition_what": "四活动形态（案例/理论/习题/实操）的偏好强度。",
        "definition_what_not": "四维不强制总和为 10；偏好 ≠ 能力。",
        "scale_low_anchor": "该活动形态下主动选择度低",
        "scale_high_anchor": "无外部要求时也主动采用该活动形态",
        "measurement": {
            "goal": "四活动形态偏好强度。",
            "methods": [
                _m("M-ACT-01", "self_report", "四维评分（主动选择度/舒适度/无要求时是否使用）", "P1", time_min=1),
                _m("M-ACT-02", "session_observation", "Session 活动体验分", "P3", "incremental", time_min=0),
            ],
            "evidence_channels": {
                "measured": "实测任务——M1 §2 的 P2 列为「—」，当前无方法覆盖",
                "experienced": "Session 活动体验分",
                "stated": "四维评分",
            },
            "update_rule": "R3/R4 沉淀低频更新；strategy_option 类不作为稳定基线",
            "update_frequency": "low",
        },
        "intervention_hooks": {
            "low": "按偏好调呈现/活动配比（不承诺绩效、不豁免 mandatory）",
            "high": "同左",
        },
    },
]


def _build() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    order = 0
    for group, construct_type, frequency_default in (
        (_TRAITS, "trait", "medium"),
        (_PREFERENCES, "preference", "low"),
        (_STRATEGY_OPTIONS, "strategy_option", "low"),
    ):
        for spec in group:
            measurement = spec["measurement"]
            measurement.setdefault("update_frequency", frequency_default)
            out.append(
                {
                    "variable_id": spec["variable_id"],
                    "name": spec["name"],
                    "display_name": spec["display_name"],
                    "construct_type": ConstructType(construct_type),
                    "definition_what": spec["definition_what"],
                    "definition_what_not": spec["definition_what_not"],
                    "scale_low_anchor": spec["scale_low_anchor"],
                    "scale_high_anchor": spec["scale_high_anchor"],
                    "measurement_def": measurement,
                    "intervention_hooks": spec["intervention_hooks"],
                    "pairing": None,
                    "sort_order": order,
                }
            )
            order += 1
    return out


#: 11 个变量的种子定义。
VARIABLES: list[dict[str, Any]] = _build()


def _build_channels() -> list[dict[str, Any]]:
    """从 `VARIABLES[].measurement_def` **派生**逐通道设计覆盖（M0 §6.6）。

    刻意派生而非手抄：手抄 33 行（11 变量 × 3 通道）与 `methods[].basis` 之间
    一定会漂移，而漂移的表现形式是「UI 显示某通道已完成，实际没有任何方法支撑」——
    一个不会报错的错误。
    """
    out: list[dict[str, Any]] = []
    for variable in VARIABLES:
        measurement = variable["measurement_def"]
        methods = measurement["methods"]
        for channel_name, note in measurement["evidence_channels"].items():
            method_ids = [
                m["method_id"] for m in methods if m["basis"] == channel_name
            ]
            out.append(
                {
                    "variable_id": variable["variable_id"],
                    "channel": EvidenceChannel(channel_name),
                    "designed": bool(method_ids),
                    "method_ids": method_ids,
                    "design_note": note,
                }
            )
    return out


#: 测量设计的逐通道落库：11 变量 × 3 通道 = 33 行。
CHANNELS: list[dict[str, Any]] = _build_channels()

#: M1 中「偏好类」变量（只影响呈现/活动形态）。
PREFERENCE_VARIABLE_IDS: frozenset[str] = frozenset(
    s["variable_id"] for s in _PREFERENCES
) | frozenset(s["variable_id"] for s in _STRATEGY_OPTIONS)
