"""可观测层 —— LLM 调用计量、成本归因、质量与 Token 报告。

设计要点见 `metering.py` 的模块 docstring：本层的存在是为了让
「空输出率 22.2%」「reasoning 占 76.7%」「缓存命中 44%」这类指标
**在运行期实时可见**，而不是像旧项目那样需要事后写 SQL 才能算出来。
"""

from storyforge.observability.metering import (
    CONTEXT_SOURCES,
    CallCollector,
    ContextBreakdown,
    LLMCallRecord,
    ModelPricing,
    UsageMetrics,
    breakdown_to_json,
    hash_prompt_prefix,
)
from storyforge.observability.report import (
    pricing_table,
    FALLBACK_PRICING,
    TokenReport,
    build_report,
    pricing_for,
    record_to_row,
    row_to_record,
)

__all__ = [
    "CONTEXT_SOURCES",
    "CallCollector",
    "ContextBreakdown",
    "LLMCallRecord",
    "ModelPricing",
    "UsageMetrics",
    "breakdown_to_json",
    "hash_prompt_prefix",
    "pricing_table",
    "FALLBACK_PRICING",
    "TokenReport",
    "build_report",
    "pricing_for",
    "record_to_row",
    "row_to_record",
]
