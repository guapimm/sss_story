"""Token 计量与成本归因的持久化（对策⑦）。

## 本模块补的三件事

### 1. 上下文归因 100% 覆盖（旧项目 **0.76%**）

旧项目 `pipeline.py:8374` 的 `context_breakdown` 存在但形同虚设：
  · 只在 trace 开启时算
  · 是**字符数而非 token**
  · `system_instruction` 恒 0
  · `relationships` 键**根本不存在**（`memory.build_context` 从不返回它）
  · 覆盖率实测 **0.76%**

`phase1_trace_report.md:36` 原文：`Classified chars: 533; attempt input chars: 69,699;
coverage proxy: 0.0076`。

### 2. 空输出可诊断（旧项目 `finish_reason` **100% 未采集**）

实测：1,003 次调用中 `blank 223` = **22.2%**，浪费 token **2,602,167 = 全部 token 的 14.36%**。
没有 `finish_reason` 就无法区分「模型主动停」与「被 max_tokens 截断」——
**这是诊断的前提**。

### 3. 成本按模型单价（旧项目 model-blind）

旧项目只有一套 MiMo 权重（hit=2/miss=100/output=200），却用在占生产 **61%** 流量的
`deepseek-v4.1-flash` 上。且存在**两份互相矛盾的公式**：
`cache_attribution.py:65-74`（NULL-safe）vs `editor_patch_ab.py:199-204`
（用 `usage.get(k) or 0` 把 NULL 变 0 → 把整段 prompt 当 miss 计费）。

本模块：**单一** `ModelPricing`，从配置读，缺失字段跳过计费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from storyforge.infra.storage.models import LLMCall
from storyforge.observability.metering import (
    CONTEXT_SOURCES,
    CallCollector,
    ContextBreakdown,
    LLMCallRecord,
    ModelPricing,
    UsageMetrics,
)

#: 未知模型的兜底单价（宁可高估，不可漏算）。
#:
#: ⚠️ 这里**刻意不含任何具体模型名** —— 单价从 `config/providers.toml` 读。
#: 旧项目的缺陷正是 model-blind：只有一套 MiMo 权重（hit=2/miss=100/output=200），
#: 却用在占生产 **61%** 流量的 deepseek 上（不同 provider、不同价）。
#: 「代码里不得出现模型名」由 `tests/contract` 规则 4 强制。
FALLBACK_PRICING = ModelPricing(cached_input=0.01, input=0.30, output=1.20)


def pricing_table() -> dict[str, ModelPricing]:
    """从能力声明（`config/providers.toml`）构造单价表。

    这样新增模型只需改配置，**零代码改动**（决策 D-09）。
    """
    try:
        from storyforge.infra.llm.capability import get_registry

        reg = get_registry()
    except Exception:  # noqa: BLE001
        return {}
    table: dict[str, ModelPricing] = {}
    for m in reg.models:
        if m.has_pricing:
            table[m.id] = ModelPricing(
                cached_input=m.pricing_cached_input,
                input=m.pricing_input,
                output=m.pricing_output,
            )
    return table


def pricing_for(model: str, table: dict[str, ModelPricing] | None = None) -> ModelPricing:
    """按模型取单价。未知模型用兜底值（**不静默按 0**）。

    解析顺序：
      1. 显式传入的 `table`
      2. `config/providers.toml` 声明的 `pricing`
      3. 兜底单价（并会在报告中体现为估算）
    """
    t = table if table is not None else pricing_table()
    return t.get(model, FALLBACK_PRICING)


# --------------------------------------------------------------------------- #
# 记录 → 存储行
# --------------------------------------------------------------------------- #


def record_to_row(record: LLMCallRecord) -> dict[str, Any]:
    """把领域记录转成 `llm_calls` 表的一行。

    ⚠️ `reasoning_tokens` 与 `completion_tokens` 是**包含关系**，
    这里逐字段照抄而**不做任何相加** —— 旧项目 `trace_service.py:117-120`
    算 `completion + reasoning` 导致多收 77% 的输出费。
    """
    u = record.usage
    bd = record.context_breakdown
    return {
        "call_id": record.call_id,
        "run_id": record.run_id,
        "book_id": record.book_id,
        "global_no": record.global_no,
        "node_name": record.node_name,
        "attempt_no": record.attempt_no,
        "model": record.model,
        "provider": record.provider,
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "reasoning_tokens": u.reasoning_tokens,
        "cached_tokens": u.cached_tokens,
        "cache_creation_tokens": u.cache_creation_tokens,
        "total_tokens": u.total_tokens,
        "usage_source": u.usage_source.value,
        "usage_complete": u.usage_complete,
        "finish_reason": record.finish_reason,
        "context_breakdown": (
            __import__("json").dumps(
                {"tokens": bd.tokens, "chars": bd.chars, "total_chars": bd.total_chars},
                ensure_ascii=False,
            )
            if bd
            else None
        ),
        "prompt_prefix_hash": record.prompt_prefix_hash,
        "prompt_total_chars": record.prompt_total_chars,
        "elapsed_ms": record.elapsed_ms,
        "status": record.status.value,
        "retry_reason": record.retry_reason,
        "error_type": record.error_type,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }


def row_to_record(row: LLMCall) -> LLMCallRecord:
    """从存储行还原领域记录。"""
    import json

    from storyforge.domain.enums import CallStatus, UsageSource

    bd = None
    if row.context_breakdown:
        try:
            d = json.loads(row.context_breakdown)
            bd = ContextBreakdown(
                tokens=d.get("tokens", {}),
                chars=d.get("chars", {}),
                total_chars=d.get("total_chars", 0),
            )
        except Exception:  # noqa: BLE001
            bd = None

    return LLMCallRecord(
        call_id=row.call_id,
        run_id=row.run_id,
        book_id=row.book_id,
        global_no=row.global_no,
        node_name=row.node_name,
        attempt_no=row.attempt_no,
        model=row.model,
        provider=row.provider,
        usage=UsageMetrics(
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            reasoning_tokens=row.reasoning_tokens,
            cached_tokens=row.cached_tokens,
            cache_creation_tokens=row.cache_creation_tokens,
            total_tokens=row.total_tokens,
        ),
        finish_reason=row.finish_reason,
        context_breakdown=bd,
        prompt_prefix_hash=row.prompt_prefix_hash,
        prompt_total_chars=row.prompt_total_chars,
        elapsed_ms=row.elapsed_ms,
        status=CallStatus(row.status),
        retry_reason=row.retry_reason,
        error_type=row.error_type,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


# --------------------------------------------------------------------------- #
# 运行级报告
# --------------------------------------------------------------------------- #


@dataclass
class TokenReport:
    """一次运行的 Token 与质量报告。"""

    calls: int = 0
    blank_calls: int = 0
    truncated_calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    #: 归因覆盖率（旧项目 0.76%）
    attribution_coverage: float = 0.0
    #: 按节点聚合
    by_node: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    #: 按模型聚合
    by_model: dict[str, dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_node is None:
            self.by_node = {}
        if self.by_model is None:
            self.by_model = {}

    @property
    def blank_rate(self) -> float:
        """空输出率。旧项目实测 **22.2%**（`entity_classify` **100%**）。"""
        return self.blank_calls / self.calls if self.calls else 0.0

    @property
    def cache_hit_rate(self) -> float | None:
        if not self.prompt_tokens:
            return None
        return self.cached_tokens / self.prompt_tokens

    @property
    def reasoning_share(self) -> float | None:
        """reasoning 占 completion 比例。旧项目实测 **76.7%**。"""
        if not self.completion_tokens:
            return None
        return self.reasoning_tokens / self.completion_tokens

    def cost(self, table: dict[str, ModelPricing] | None = None) -> float:
        """总成本（按模型单价）。"""
        total = 0.0
        for model, agg in self.by_model.items():
            p = pricing_for(model, table)
            total += p.cost_of(
                UsageMetrics(
                    prompt_tokens=agg.get("prompt", 0),
                    cached_tokens=agg.get("cached", 0),
                    completion_tokens=agg.get("completion", 0),
                )
            )
        return total

    def cost_breakdown(self, table: dict[str, ModelPricing] | None = None) -> dict[str, float]:
        """成本拆分：hit / miss / output 三档。

        ★ 这张表直接回答「优化哪个口子最值」：

            旧项目实测（MiMo 权重 hit=2/miss=100/output=200）：
                hit    0.35%
                miss  22.15%
                output 77.50%   ← 主战场
        且 `output` 中 **76.7% 是 reasoning** = 总成本的 **59.4%**。
        """
        t = table if table is not None else pricing_table()
        hit = miss = out = 0.0
        for model, agg in self.by_model.items():
            p = pricing_for(model, t)
            cached = agg.get("cached", 0) or 0
            prompt = agg.get("prompt", 0) or 0
            completion = agg.get("completion", 0) or 0
            hit += cached / 1e6 * p.cached_input
            miss += max(0, prompt - cached) / 1e6 * p.input
            # ⚠️ 只用 completion（reasoning 已含在内），绝不 completion + reasoning
            out += completion / 1e6 * p.output
        return {"hit": hit, "miss": miss, "output": out}

    def summary(self) -> str:
        lines = [
            f"调用 {self.calls} 次，空输出 {self.blank_calls}（{self.blank_rate:.1%}）"
            f"，截断 {self.truncated_calls}",
            f"prompt={self.prompt_tokens:,} cached={self.cached_tokens:,} "
            f"（命中 {self.cache_hit_rate:.1%}）"
            if self.cache_hit_rate is not None
            else f"prompt={self.prompt_tokens:,} cached=不可用",
            f"completion={self.completion_tokens:,} reasoning={self.reasoning_tokens:,} "
            f"（占比 {self.reasoning_share:.1%}）"
            if self.reasoning_share is not None
            else f"completion={self.completion_tokens:,}",
            f"归因覆盖率 {self.attribution_coverage:.1%}",
        ]
        return "\n".join(lines)


def build_report(records: list[LLMCallRecord]) -> TokenReport:
    """从调用记录构建报告。"""
    collector = CallCollector()
    for r in records:
        collector.add(r)

    rep = TokenReport(
        calls=collector.total_calls,
        blank_calls=collector.blank_calls,
        truncated_calls=collector.truncated_calls,
        by_node=collector.by_node(),
    )
    for r in records:
        u = r.usage
        rep.prompt_tokens += u.prompt_tokens or 0
        rep.cached_tokens += u.cached_tokens or 0
        rep.completion_tokens += u.completion_tokens or 0
        rep.reasoning_tokens += u.reasoning_tokens or 0

        m = rep.by_model.setdefault(
            r.model,
            {"calls": 0, "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0},
        )
        m["calls"] += 1
        m["prompt"] += u.prompt_tokens or 0
        m["cached"] += u.cached_tokens or 0
        m["completion"] += u.completion_tokens or 0
        m["reasoning"] += u.reasoning_tokens or 0

    # 归因覆盖率：有 context_breakdown 且覆盖 >= 90% 的调用占比
    covered = 0
    for r in records:
        if r.context_breakdown and r.context_breakdown.coverage >= 0.9:
            covered += 1
    rep.attribution_coverage = covered / len(records) if records else 0.0
    return rep


__all__ = [
    "CONTEXT_SOURCES",
    "CallCollector",
    "ContextBreakdown",
    "LLMCallRecord",
    "ModelPricing",
    "UsageMetrics",
    "pricing_table",
    "FALLBACK_PRICING",
    "TokenReport",
    "pricing_for",
    "record_to_row",
    "row_to_record",
    "build_report",
]
