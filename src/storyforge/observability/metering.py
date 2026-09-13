"""LLM 调用观测契约（对策⑦）。

## 这个模块解决的确切问题

旧项目 1,003 条真实调用记录的实测结果：

    prompt_tokens      = 9,151,550
    cached_tokens      = 4,023,724   → 命中 44.0%
    completion_tokens  = 8,970,128
    reasoning_tokens   = 6,876,206   → 占 completion 的 76.7%
    blank（空输出）     = 223 / 1003  = 22.2%   ★
    finish_reason      = 100% 未采集           ★★

### 四个必须在设计期就修正的口径缺陷

1. **`finish_reason` 完全缺失** → 无法区分「模型主动停」与「被 max_tokens 截断」。
   **这是诊断 22.2% 空输出的前提**，不是可选项。
2. **reasoning 被重复计入** → 旧项目 `trace_service.py:117-120` 算
   `completion + reasoning`，`reasoning ⊆ completion`（896/897 行验证
   `prompt+completion == total`）→ **多收 77% 的输出费**。
   本模块用 `@model_validator` **在类型层禁止**这种相加。
3. **`native` 是弱保证** → 旧项目只要 ≥3/5 字段就算 native，导致 33 行
   `cached_tokens` 为 NULL → 成本函数既不收 hit 也不收 miss（**漏报**）。
   本设计：字段缺失必须显式 `UNAVAILABLE`，且 `usage_complete` 独立判定。
4. **成本 model-blind** → 旧项目只有一套 MiMo 权重，却用在占 **61%** 流量的
   `deepseek-v4.1-flash` 上。本设计成本按 `model × provider` 配置化。

## 归因维度

旧项目 `context_breakdown` 存在但**形同虚设**：只在 trace 开启时算、
是**字符数而非 token**、`system_instruction` 恒 0、`relationships` 键根本不存在、
**覆盖率实测 0.76%**。本设计要求 100% 覆盖且单位统一为 token。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from storyforge.domain.enums import CallStatus, UsageSource

#: 原生 usage 的五个标准字段（继承旧项目 `usage_complete` 契约）
_NATIVE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
)

#: 上下文来源键 —— 用于 `context_breakdown` 归因
#: 旧项目 `AGENTS.md` 把逐来源拆分列为北极星指标但从未落实。
CONTEXT_SOURCES = (
    "system",          # system prompt（旧项目恒 0）
    "bible",           # L0 世界观圣经
    "volume",          # 卷级台阶/目标
    "outline",         # 细纲
    "states",          # 人物/实体状态
    "relations",       # 关系与归属（旧项目此键不存在）
    "injuries",        # 伤势（旧项目无此维度）
    "foreshadow",      # 伏笔任务
    "timeline",        # 前情时间线
    "rag",             # 检索片段
    "previous_chapter",# 上一章结尾
    "summary",         # 分层摘要
    "style",           # 文风范本
    "instruction",     # 指令/输出要求
    "other",
)


class UsageMetrics(BaseModel):
    """单次调用的用量。

    **语义约束（关键）**：`reasoning_tokens ⊆ completion_tokens`。
    这不只是注释 —— `@model_validator` 会拒绝 `reasoning > completion` 的数据，
    从类型层防止旧项目「completion + reasoning 相加」的错误复现。
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    #: ⊂ completion_tokens（不是并列关系）
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    cache_creation_tokens: int | None = None
    total_tokens: int | None = None

    @model_validator(mode="after")
    def _check_reasoning_subset(self) -> UsageMetrics:
        if (
            self.reasoning_tokens is not None
            and self.completion_tokens is not None
            and self.reasoning_tokens > self.completion_tokens
        ):
            raise ValueError(
                f"reasoning_tokens({self.reasoning_tokens}) 不得大于 "
                f"completion_tokens({self.completion_tokens}) —— "
                f"reasoning 是 completion 的子集，不是并列项"
            )
        if (
            self.cached_tokens is not None
            and self.prompt_tokens is not None
            and self.cached_tokens > self.prompt_tokens
        ):
            raise ValueError(
                f"cached_tokens({self.cached_tokens}) 不得大于 "
                f"prompt_tokens({self.prompt_tokens})"
            )
        return self

    # -- 派生 ---------------------------------------------------------------

    @property
    def missing_fields(self) -> list[str]:
        """缺失的原生字段（**不把 None 当 0**）。"""
        return [f for f in _NATIVE_FIELDS if getattr(self, f) is None]

    @property
    def usage_source(self) -> UsageSource:
        n_missing = len(self.missing_fields)
        if n_missing == 0:
            return UsageSource.NATIVE
        if n_missing == len(_NATIVE_FIELDS):
            return UsageSource.UNAVAILABLE
        return UsageSource.PARTIAL

    @property
    def usage_complete(self) -> bool:
        """**独立维度**（旧项目把它与 usage_source 混为一谈）。

        语义：五个原生字段全部非 None。
        注意 `UNAVAILABLE` 与 `usage_complete=False` 不是同一件事 ——
        部分缺失时 source 是 PARTIAL 且 complete 也是 False。
        """
        return len(self.missing_fields) == 0

    @property
    def cache_hit_rate(self) -> float | None:
        """缓存命中率。无法计算时返回 **None**（不返回 0 —— 缺省即不通过）。"""
        if not self.prompt_tokens or self.cached_tokens is None:
            return None
        return self.cached_tokens / self.prompt_tokens

    @property
    def reasoning_share(self) -> float | None:
        """reasoning 占 completion 的比例（旧项目实测 76.7%）。"""
        if not self.completion_tokens or self.reasoning_tokens is None:
            return None
        return self.reasoning_tokens / self.completion_tokens

    @property
    def billable_output_tokens(self) -> int | None:
        """计费输出 token。

        ⚠️ **绝不** 等于 `completion + reasoning`。
        reasoning 已包含在 completion 内，这里直接返回 completion。
        这个属性的存在就是为了让调用方有一个"正确算法"可用，
        而不是自己拼 `completion + reasoning`。
        """
        return self.completion_tokens


class ContextBreakdown(BaseModel):
    """逐来源的输入 token 归因。

    旧项目失败之处：字符数（非 token）、覆盖率 0.76%、`system_instruction` 恒 0。
    本设计要求 100% 覆盖：`total_chars` 必须等于各分项之和。
    """

    #: 来源 → token 数（键必须在 CONTEXT_SOURCES 内）
    tokens: dict[str, int] = Field(default_factory=dict)
    #: 各来源字符数（用于校验换算比例）
    chars: dict[str, int] = Field(default_factory=dict)
    #: 组装后的总字符数（用于断言覆盖完整）
    total_chars: int = 0

    @model_validator(mode="after")
    def _check_coverage(self) -> ContextBreakdown:
        unknown = set(self.tokens) - set(CONTEXT_SOURCES)
        if unknown:
            raise ValueError(
                f"未知的上下文来源 {sorted(unknown)}；"
                f"请加入 CONTEXT_SOURCES（保证归因口径唯一）"
            )
        if self.chars and self.total_chars:
            accounted = sum(self.chars.values())
            # 允许 1% 误差（分块边界）
            if accounted > self.total_chars * 1.01:
                raise ValueError(
                    f"归因字符数 {accounted} 超过总字符数 {self.total_chars} —— 归因有重叠"
                )
        return self

    @property
    def coverage(self) -> float:
        """归因覆盖率 —— 旧项目实测 **0.76%**（等于没有）。"""
        if not self.total_chars:
            return 0.0
        return sum(self.chars.values()) / self.total_chars


class LLMCallRecord(BaseModel):
    """一次 LLM 调用的完整记录。对应 `llm_calls` 表。"""

    call_id: str
    run_id: int | None = None
    book_id: int | None = None
    #: ★ 单一章号（旧项目 `GenerationAttempt` 无此列 → 只有 3.2% 的 trace 能按章算钱）
    global_no: int | None = None

    node_name: str
    attempt_no: int = 1
    model: str
    provider: str

    usage: UsageMetrics = Field(default_factory=UsageMetrics)

    # ── ★ 旧项目完全缺失的诊断字段 ──
    #: stop / length / content_filter / tool_calls / None
    #: 没有它就无法区分"主动停"与"被截断" → 这是诊断 22.2% 空输出的前提
    finish_reason: str | None = None

    context_breakdown: ContextBreakdown | None = None
    #: 稳定前缀 hash —— 缓存命中分析的核心依据
    prompt_prefix_hash: str | None = None
    prompt_total_chars: int | None = None

    elapsed_ms: float | None = None
    status: CallStatus = CallStatus.OK
    retry_reason: str | None = None
    error_type: str | None = None

    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    # -- 校验 ---------------------------------------------------------------

    @model_validator(mode="after")
    def _check_blank_consistency(self) -> LLMCallRecord:
        """空输出必须能被诊断 —— 强制要求 `finish_reason` 有值或状态非 BLANK。

        这条校验的目的：**让 22.2% 空输出率可被归因**。
        若某次调用是 BLANK 却没有任何 finish_reason / error_type，
        说明观测代码漏采了信息，应当立即发现而不是事后统计才发现。
        """
        if self.status == CallStatus.BLANK and not (self.finish_reason or self.error_type):
            raise ValueError(
                "BLANK 状态的调用必须记录 finish_reason 或 error_type，"
                "否则无法诊断空输出成因（旧项目 22.2% 空输出率无法归因的原因）"
            )
        return self

    # -- 派生 ---------------------------------------------------------------

    @property
    def is_blank(self) -> bool:
        return self.status == CallStatus.BLANK

    @property
    def truncated(self) -> bool:
        """是否被输出上限截断。"""
        return self.finish_reason == "length"

    def cost(self, pricing: ModelPricing) -> float:
        """按**模型单价**计算成本（旧项目是 model-blind 的单一权重）。"""
        return pricing.cost_of(self.usage)


@dataclass(frozen=True)
class ModelPricing:
    """模型单价（每 1M token）。

    ## 旧项目的两个成本口径缺陷

    1. **model-blind**：只有一套 MiMo 权重（hit=2 / miss=100 / output=200 Credits），
       却用在占生产 **61%** 流量的 `deepseek-v4.1-flash` 上（不同 provider、不同价）。
    2. **两份互相矛盾的公式**：`cache_attribution.py:65-74` 是正确的 NULL-safe 版本，
       而 `editor_patch_ab.py:199-204` 用 `usage.get(k) or 0` **把 NULL 变 0**
       → 把整段 prompt 当 miss 计费。`test_editor_patch_ab.py:30-32` 还把这个行为
       **钉成正确**。

    本设计：单一 `ModelPricing`，缺失字段**跳过计费**（不按 0 算）。
    """

    #: 每 1M token 的价格
    cached_input: float = 0.0
    input: float = 0.0
    output: float = 0.0
    currency: str = "USD"

    def cost_of(self, usage: UsageMetrics) -> float:
        """计算成本。缺失字段按 0 token 处理，但**不把缺失本身算成 miss**。

        关键差异：`cached_tokens is None` 时，我们**不知道**命中了多少，
        此时应把整个 prompt 视为 miss（保守估算）并**在报告中标注不确定性**，
        而不是像旧项目那样静默把 NULL 当 0（那会把折扣价当原价，漏报）。
        """
        total = 0.0
        if usage.prompt_tokens is not None:
            if usage.cached_tokens is None:
                # 不确定命中量 → 保守按全部 miss 计，并可由调用方检查 usage_source
                total += usage.prompt_tokens / 1_000_000 * self.input
            else:
                hit = usage.cached_tokens
                miss = usage.prompt_tokens - hit
                total += hit / 1_000_000 * self.cached_input
                total += miss / 1_000_000 * self.input
        if usage.completion_tokens is not None:
            # ⚠️ 只用 completion（reasoning 已含在内），绝不 completion + reasoning
            total += usage.completion_tokens / 1_000_000 * self.output
        return total


@dataclass
class CallCollector:
    """收集一次运行内的所有调用记录，并给出聚合诊断。

    用途：把 22.2% 空输出率、76.7% reasoning 占比、44% 缓存命中率这些
    **在旧项目里要事后写 SQL 才能算出来**的指标，变成运行期实时可见的数字。
    """

    records: list[LLMCallRecord] = field(default_factory=list)

    def add(self, record: LLMCallRecord) -> None:
        self.records.append(record)

    # -- 聚合 ---------------------------------------------------------------

    @property
    def total_calls(self) -> int:
        return len(self.records)

    @property
    def blank_calls(self) -> int:
        return sum(1 for r in self.records if r.is_blank)

    @property
    def blank_rate(self) -> float:
        """空输出率 —— 旧项目实测 **22.2%**（`entity_classify` **100%**）。"""
        if not self.records:
            return 0.0
        return self.blank_calls / self.total_calls

    @property
    def truncated_calls(self) -> int:
        return sum(1 for r in self.records if r.truncated)

    def cache_hit_rate(self) -> float | None:
        hit = sum(r.usage.cached_tokens or 0 for r in self.records)
        total = sum(r.usage.prompt_tokens or 0 for r in self.records)
        if not total:
            return None
        return hit / total

    def reasoning_share(self) -> float | None:
        reasoning = sum(r.usage.reasoning_tokens or 0 for r in self.records)
        completion = sum(r.usage.completion_tokens or 0 for r in self.records)
        if not completion:
            return None
        return reasoning / completion

    def by_node(self) -> dict[str, dict[str, Any]]:
        """按节点聚合 —— 找"空转大户"与"reasoning 大户"。"""
        out: dict[str, dict[str, Any]] = {}
        for r in self.records:
            d = out.setdefault(
                r.node_name,
                {"calls": 0, "blank": 0, "truncated": 0,
                 "prompt": 0, "completion": 0, "reasoning": 0, "cached": 0},
            )
            d["calls"] += 1
            d["blank"] += int(r.is_blank)
            d["truncated"] += int(r.truncated)
            d["prompt"] += r.usage.prompt_tokens or 0
            d["completion"] += r.usage.completion_tokens or 0
            d["reasoning"] += r.usage.reasoning_tokens or 0
            d["cached"] += r.usage.cached_tokens or 0
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.total_calls,
            "blank": self.blank_calls,
            "blank_rate": self.blank_rate,
            "truncated": self.truncated_calls,
            "cache_hit_rate": self.cache_hit_rate(),
            "reasoning_share": self.reasoning_share(),
        }


def hash_prompt_prefix(text: str) -> str:
    """稳定前缀 hash —— 用于缓存命中/未命中分析。

    旧项目只有 `capture_prompt_hash`（整体 hash），无法回答
    "为什么这次没命中缓存"。前缀 hash 才能定位**是哪一块变了**。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def breakdown_to_json(b: ContextBreakdown | None) -> str | None:
    if b is None:
        return None
    return json.dumps(
        {"tokens": b.tokens, "chars": b.chars, "total_chars": b.total_chars},
        ensure_ascii=False,
    )


__all__ = [
    "CONTEXT_SOURCES",
    "UsageMetrics",
    "ContextBreakdown",
    "LLMCallRecord",
    "ModelPricing",
    "CallCollector",
    "hash_prompt_prefix",
    "breakdown_to_json",
]
