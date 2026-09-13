"""确定性质量打分卡（零 LLM）。

## 为什么需要它

用户要求：「建议每章，但是按节拍、伏笔、风险不太可控，**如果可以量化质量就选 A**」。

旧项目的做法是**抽样 LLM 审校**（`standard` 档只审头 5 尾 7 章，100 章的书
**88 章完全不审**），而实测结果是被点名文风问题的章中 **30.8% 仍判 passed**。

本模块让**每一章都有确定性分数**：可复现、毫秒级、零成本。
发现问题的责任在打分卡，判断与改写的责任在 LLM。

## 分层

    literal.py     字面层：跨章重叠 / 章内冗余 / 对话占比 / 套话 / 比喻 / 句长 / 章末类型
    structural.py  结构层：细纲落实率 / 钩子落地 / 状态一致性 / 无依据名词
    aggregate.py   汇总层：加权总分 + 触发判定 + 无条件送审

## ⚠️ 明确排除

**不为「无推进重复（注水）」建确定性指标** —— 旧项目四次独立验证全部失败
（段级 Dice 最高 0.519、句级重复 0/47、内容单元法在已知坏样本上报 0.0%、
低阈值探针命中 0）。那是**语义判断**，只能靠 LLM 审校 + 人工盲读。
"""

from storyforge.scorecard.aggregate import (
    DimensionScore,
    Scorecard,
    Thresholds,
    TriggerReason,
    build_dimensions,
    collect_triggers,
    evaluate,
)
from storyforge.scorecard.literal import (
    CLICHES,
    LiteralMetrics,
    analyze,
    classify_ending,
    cross_chapter_overlap,
    dialogue_ratio,
    find_cliches,
    intra_redundancy,
    recurring_ngrams,
    triple_parallelism_count,
)
from storyforge.scorecard.structural import (
    StructuralMetrics,
    analyze_structural,
    check_state_consistency,
    find_unsupported_entities,
    measure_hook_landed,
    measure_outline_adherence,
)

__all__ = [
    # literal
    "CLICHES",
    "LiteralMetrics",
    "analyze",
    "classify_ending",
    "cross_chapter_overlap",
    "dialogue_ratio",
    "find_cliches",
    "intra_redundancy",
    "recurring_ngrams",
    "triple_parallelism_count",
    # structural
    "StructuralMetrics",
    "analyze_structural",
    "check_state_consistency",
    "find_unsupported_entities",
    "measure_hook_landed",
    "measure_outline_adherence",
    # aggregate
    "DimensionScore",
    "Scorecard",
    "Thresholds",
    "TriggerReason",
    "build_dimensions",
    "collect_triggers",
    "evaluate",
]
