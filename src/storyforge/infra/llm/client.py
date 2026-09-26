"""LLM 客户端 —— 调用、重试、结构化输出、计量、**幂等**。

## 本模块解决的旧项目问题

### 1. 空输出率 22.2%（`entity_classify` **100%**）

旧项目实测 1,003 次调用：`ok 772 / blank 223 / failed 8`，
浪费 token **2,602,167 = 全部 token 的 14.36%**。
根因之一是「思考吃光输出预算」：

    max_tokens = max(4096, reasoning * 4)   # 思考与输出共用上限
    → 可用输出预算 = 4x - x = 3x

且「微产出判废」（思考烧 3000+ 字只吐 <20 字 → 判废重试）的埋点
**在生产从未触发** —— 说明埋点打偏了，真正烧钱的是更宽的 `blank` 路径。

本模块的对策：
  · **强制采集 `finish_reason`**（旧项目 100% 缺失 → 22.2% 空输出无法归因）
  · 抽取类节点**显式禁止思考**（由能力声明驱动，见 `capability.py`）
  · 输出预算下限保障（正文不被思考挤没）

### 2. 节点被静默重放（LangGraph `#7417`）

官方 issue（53 条评论仍 open）：**>180s 的调用在 LangGraph Cloud 被静默重放**
→ 2~3× 冗余成本。而旧项目实测 `editor` 平均 **117s**、`architect` **72s**，
章节生成极易超阈值。

本模块的对策：**幂等键** `(book_id, stage, seq, node, attempt)`，
命中已有成功记录时**直接复用**而不重新调用。

### 3. 成本 model-blind

旧项目只有一套 MiMo 权重，却用在占生产 61% 流量的 deepseek 上。
本模块按 `ModelCapability` 记录实际模型，成本由 `observability/report.py`
按 `config/providers.toml` 的单价计算。

### 4. 429 与预算

旧项目实测 `retry_reason: "Request timed out." 46 次`。
本模块实现指数退避 + 429 等待**不计入** deadline（等待非计算）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from storyforge.domain.enums import CallStatus, UsageSource
from storyforge.infra.llm.capability import (
    ModelCapability,
    ModelRegistry,
    ProviderSpec,
    get_registry,
)
from storyforge.infra.llm.probe_budget import ProbeBudget, ProbeBudgetError
from storyforge.infra.llm.structured_output import (
    _summarize_validation_error,
    extract_json as _extract_structured_json,
)
from storyforge.observability.metering import (
    ContextBreakdown,
    LLMCallRecord,
    UsageMetrics,
    hash_prompt_prefix,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _describe_error(exc: Exception | None) -> str:
    """把异常渲染成**保证非空**的一行描述（归因用）。

    ## 为什么需要它（本轮踩到两次的同一类缺陷）

    报错写成 `f"...：{exc}"` 时，若 `str(exc)` 为空，冒号后面就什么都没有：

        节点 detail_planner 调用失败（3 次尝试）：          ← 无法归因

    实测两个来源：

      · **空输出分支**未写 `last_err` → `last_err is None` → 打印 "None"（已修）
      · **超时分支** `last_err = exc`，而 `asyncio.wait_for` 的
        `TimeoutError` **不带消息**（实测 `str(exc) == ''`、`args == ()`）

    两者表现一模一样，成因却不同 —— 与其逐个分支去堵，
    不如在出口统一保证：**永远给出可归因的文本**。
    """
    if exc is None:
        return "（无错误详情：last_err 未设置，是调用方的缺陷）"
    text = str(exc).strip()
    if text:
        return text
    # 有异常对象但没有消息 —— 至少把类型与 args 带出来
    args = getattr(exc, "args", ())
    return f"{type(exc).__name__}" + (f"（args={args!r}）" if args else "（无消息）")


class LLMError(RuntimeError):
    """LLM 调用失败。"""


class StructuredOutputError(LLMError):
    """结构化输出校验失败（重试耗尽）。"""


class LLMClientProtocol(Protocol):
    """客户端接口 —— 便于测试注入哑实现。"""

    async def complete(
        self,
        *,
        node: str,
        system: str,
        user: str,
        **kwargs: Any,
    ) -> str: ...

    async def complete_structured(
        self,
        *,
        node: str,
        system: str,
        user: str,
        schema: type[T],
        **kwargs: Any,
    ) -> T: ...


# --------------------------------------------------------------------------- #
# 调用上下文
# --------------------------------------------------------------------------- #


@dataclass
class CallContext:
    """一次调用的元信息（用于计量与幂等）。"""

    node: str
    book_id: int | None = None
    global_no: int | None = None
    run_id: int | None = None
    attempt_no: int = 1
    #: 幂等键 —— 命中已有结果则复用（防 LangGraph `#7417` 静默重放）
    idempotency_key: str | None = None
    #: 本卷/本段（供缓存分析与调试）
    volume_no: int | None = None


@dataclass
class CallResult:
    """一次调用的结果与记录。"""

    text: str
    record: LLMCallRecord
    model: str
    provider: str
    #: 是否复用了已有结果（幂等命中）
    from_cache: bool = False


# --------------------------------------------------------------------------- #
# 幂等存储
# --------------------------------------------------------------------------- #


class IdempotencyStore(Protocol):
    """幂等键 → 已有结果的存储。"""

    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...


@dataclass
class MemoryIdempotencyStore:
    """进程内幂等存储（测试与单次运行用）。

    真实实现应落 DB（`llm_calls` 表已有 `call_id`），
    使**跨进程重启**也能复用（LangGraph 重放可能发生在重启后）。
    """

    _data: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        self._data[key] = value


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


@dataclass
class ClientConfig:
    max_attempts: int = 3
    timeout_s: float = 300.0
    #: 429 退避基数（秒），指数增长
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    #: 结构化输出最多修复重试次数
    structured_retries: int = 2
    #: 输出预算下限（token）。保证正文不被思考挤没 ——
    #: 实测 MiMo 上 `detail_planner` 曾把 98.75% 的 completion 花在 reasoning，
    #: `finish_reason=length` 且正文为空（旧项目同类失败见模块注释）。
    min_output_tokens: int = 4096
    #: 输出预算上限（token）。防止误配成模型的完整上下文长度而白烧额度。
    max_output_tokens_cap: int = 32768
    #: 可选 S0 预算闸门；全部为 None 时保持旧行为。
    max_provider_requests: int | None = None
    max_prompt_chars: int | None = None
    max_configured_credits: float | None = None


class LLMClient:
    """LLM 客户端。

    ## 传输层

    `transport` 是一个可注入的协程函数，签名为：

        async def transport(model, provider, system, user, **params) -> tuple[str, dict]

    返回 `(text, usage_dict)`。真实实现用 `langchain-openai` 的 `ChatOpenAI`；
    测试注入哑实现。这样**客户端逻辑（重试/计量/幂等）可独立测试**，
    不依赖网络。

    ## 幂等

    若 `context.idempotency_key` 非空且存储中已有结果，**直接复用**，
    不发起调用。这是对 LangGraph 静默重放（`#7417`）的防护。
    """

    def __init__(
        self,
        transport: Any,
        *,
        registry: ModelRegistry | None = None,
        config: ClientConfig | None = None,
        idempotency: IdempotencyStore | None = None,
        collector: list[LLMCallRecord] | None = None,
    ) -> None:
        self.transport = transport
        self.registry = registry or get_registry()
        self.config = config or ClientConfig()
        self.idempotency = idempotency or MemoryIdempotencyStore()
        self.records: list[LLMCallRecord] = collector if collector is not None else []
        self._probe_budget = ProbeBudget(
            max_provider_requests=self.config.max_provider_requests,
            max_prompt_chars=self.config.max_prompt_chars,
            max_configured_credits=self.config.max_configured_credits,
        )

    @property
    def _budget_enabled(self) -> bool:
        return self._probe_budget.enabled

    @property
    def provider_attempts(self) -> int:
        return self._probe_budget.provider_attempts

    @property
    def configured_credits(self) -> float:
        return self._probe_budget.configured_credits

    @property
    def unknown_usage(self) -> bool:
        return self._probe_budget.unknown_usage

    @property
    def budget_stopped(self) -> bool:
        return self._probe_budget.budget_stopped

    @property
    def budget_stop_reason(self) -> str | None:
        return self._probe_budget.budget_stop_reason

    # -- 路由 ---------------------------------------------------------------

    def _resolve(self, node: str) -> tuple[ModelCapability, ProviderSpec, dict[str, Any]]:
        model, provider = self.registry.route(node)
        policy = self.registry.thinking_policy_for(node, model)
        return model, provider, policy

    # -- 主调用 -------------------------------------------------------------

    async def complete(
        self,
        *,
        node: str,
        system: str,
        user: str,
        context: CallContext | None = None,
        context_breakdown: ContextBreakdown | None = None,
        prompt_prefix: str | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
        cache_result: bool = True,
    ) -> CallResult:
        """单次补全（带重试、计量、幂等）。

        `json_mode=True` 时要求 provider 走**原生 JSON 输出**
        （`response_format={"type":"json_object"}`）。仅当模型声明
        `supports_json_mode` 时才会真正下发 —— 见 `_resolve`。

        ## `cache_result=False`：由调用方**校验通过后**自行写缓存

        ★★ 修的是一个真实缺陷（S0 侦察发现并复现）：`complete_structured`
        的修复重试**曾完全失效** —— 本函数**在校验之前**就写缓存，而重试
        **重用同一个 key** ⇒ 第 2 次命中第 1 次那份**非法 JSON**，一次格式
        瑕疵 = 硬失败（实测：带 key 时 transport 只被调 **1** 次）。
        影响面是**所有**传 key 的结构化节点（抽取 3 + 审校 2 + 章节 2）。
        取证见 `scripts/probe_structured_retry_cache.py`。
        """
        ctx = context or CallContext(node=node)
        ctx.node = node
        model, provider, policy = self._resolve(node)

        # ── 幂等：命中已有结果则复用（防静默重放导致重复计费）──
        if ctx.idempotency_key:
            cached = self.idempotency.get(ctx.idempotency_key)
            if cached is not None:
                logger.info("idempotent hit: %s", ctx.idempotency_key)
                return CallResult(
                    text=cached,
                    record=self._record(
                        ctx, model, provider, CallStatus.OK, UsageMetrics(),
                        started=time.monotonic(), extra={"note": "idempotent_hit"},
                    ),
                    model=model.id,
                    provider=provider.name,
                    from_cache=True,
                )

        prompt_chars = len(system) + len(user)
        try:
            pricing = self._probe_budget.prepare(model, prompt_chars)
        except ProbeBudgetError as exc:
            raise LLMError(str(exc)) from None

        params: dict[str, Any] = dict(policy)

        # ★ 输出预算：显式下发 `max_tokens`，否则会被 provider 默认值坑死。
        #
        # 实测缺陷（书 16 细纲阶段）：`max_output_tokens` 从 `providers.toml`
        # 读了进来（MiMo 声明 131072）却**从未下发给 API** —— 只有调用方显式
        # 传才设置，而所有节点都没传 → 生效的是 provider 默认值（约 8000）：
        #
        #     prompt 4126 字符 → completion=8000, reasoning=7900（98.75%）
        #     finish_reason=length → 正文一个字都没写出 → 三次尝试全废
        #
        # 与旧项目 `max_tokens = max(4096, reasoning*4)`（思考与输出共用上限）
        # 是同一类失败：**思考把输出挤没**。
        #
        # 修法：默认用模型声明的上限（调用方仍可覆盖），两端都夹 ——
        # 下限保证正文有空间，上限防止把完整上下文长度当输出预算。
        budget = max_output_tokens
        if budget is None:
            budget = min(
                max(model.max_output_tokens, self.config.min_output_tokens),
                self.config.max_output_tokens_cap,
            )
        if budget > 0:
            params["max_tokens"] = budget
        # ★ 原生 JSON 模式：只在模型**声明支持**时下发。
        #
        # 实测（小米 MiMo）：不加这个参数时模型会把 JSON 包在 ```json 里，
        # 且中文长文本里常出现缺逗号 / 字符串内裸换行 —— 靠事后修复是打地鼠。
        # 加上 `response_format={"type":"json_object"}` 后**严格解析直接通过**。
        #
        # 但这是**能力**，不是所有 provider 都支持（不支持的会直接 400），
        # 因此由 `providers.toml` 的 `supports_json_mode` 声明，不硬编码厂商判断。
        if json_mode and model.supports_json_mode:
            params["json_mode"] = True

        last_err: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                self._probe_budget.reserve_attempt(
                    max_output_tokens, self.config.max_output_tokens_cap
                )
            except ProbeBudgetError as exc:
                raise LLMError(str(exc)) from None
            ctx.attempt_no = attempt
            started = time.monotonic()
            try:
                text, usage_raw = await asyncio.wait_for(
                    self.transport(
                        model=model,
                        provider=provider,
                        system=system,
                        user=user,
                        **params,
                    ),
                    timeout=self.config.timeout_s,
                )
            except TimeoutError:
                # ★ 超时也要写**带信息**的 last_err（见 `_timeout_error`）。
                last_err = self._timeout_error(
                    model_id=model.id, attempt=attempt,
                    prompt_chars=len(system) + len(user),
                )
                self._record_failure(
                    ctx, model, provider, CallStatus.TIMEOUT, started,
                    error_type="TimeoutError",
                    extra={"timeout_s": self.config.timeout_s},
                    prompt_chars=len(system) + len(user),
                )
                if self._budget_enabled:
                    raise LLMError(self._probe_budget.stop(
                        f"budget stopped after provider timeout: {last_err}"
                    )) from None
                await self._sleep_backoff(attempt)
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                self._record_failure(
                    ctx, model, provider, CallStatus.FAILED, started,
                    error_type=type(exc).__name__,
                    retry_reason="rate_limit" if self._is_rate_limit(exc) else None,
                    prompt_chars=len(system) + len(user),
                )
                if self._budget_enabled:
                    raise LLMError(self._probe_budget.stop(
                        f"budget stopped after provider error ({type(exc).__name__})"
                    )) from None
                await self._sleep_backoff(attempt)
                continue

            result, attempt_err = self._record_response(
                ctx, model, provider, text, usage_raw, started, pricing,
                system, user, context_breakdown, prompt_prefix, cache_result,
            )
            if result is not None:
                return result
            last_err = attempt_err

        # ★ 兜底：绝不让报错信息在冒号后面空着。
        #
        # 这一类缺陷本轮踩到**两次**（空输出未写 last_err；TimeoutError 的
        # str() 为空），两次都表现为"节点 X 调用失败（3 次尝试）："后面什么都没有，
        # 完全无法归因。与其逐个分支去堵，不如在出口统一保证非空。
        raise LLMError(
            f"节点 {node} 调用失败（{self.config.max_attempts} 次尝试）："
            f"{_describe_error(last_err)}"
        )

    def _record_response(
        self, ctx: CallContext, model: ModelCapability, provider: ProviderSpec,
        text: str, usage_raw: dict[str, Any], started: float, pricing: Any,
        system: str, user: str,
        context_breakdown: ContextBreakdown | None, prompt_prefix: str | None,
        cache_result: bool,
    ) -> tuple[CallResult | None, Exception | None]:
        prompt_chars = len(system) + len(user)
        try:
            usage = self._parse_usage(usage_raw)
        except Exception as exc:  # noqa: BLE001
            if not self._budget_enabled:
                raise
            reason = self._probe_budget.unknown(
                f"invalid usage payload ({type(exc).__name__})"
            )
            self._record_failure(
                ctx, model, provider, CallStatus.FAILED, started,
                error_type="InvalidUsage", prompt_chars=prompt_chars,
            )
            raise LLMError(reason) from None
        finish_reason = (usage_raw or {}).get("finish_reason")

        if self._budget_enabled:
            usage_stop = self._probe_budget.observe_usage(usage, pricing)
            if self.unknown_usage:
                self._record_failure(
                    ctx, model, provider, CallStatus.FAILED, started,
                    error_type="UnknownUsage", prompt_chars=prompt_chars,
                )
                raise LLMError(usage_stop or "budget stopped: unknown usage")

        if not (text or "").strip():
            blank_error = self._blank_error(
                finish_reason=finish_reason, model_id=model.id,
                prompt_chars=prompt_chars, reasoning_tokens=usage.reasoning_tokens,
            )
            self.records.append(self._record(
                ctx, model, provider, CallStatus.BLANK, usage, started=started,
                error_type=None if finish_reason else "BlankWithoutFinishReason",
                extra={
                    "finish_reason": finish_reason or "unknown",
                    "context_breakdown": context_breakdown,
                    "prompt_prefix": prompt_prefix,
                    "prompt_total_chars": prompt_chars,
                },
            ))
            logger.warning(
                "blank output: node=%s model=%s attempt=%d finish_reason=%s "
                "reasoning=%s prompt_chars=%d",
                ctx.node, model.id, ctx.attempt_no, finish_reason,
                usage.reasoning_tokens, prompt_chars,
            )
            if self.budget_stopped:
                raise LLMError(f"budget stopped: {self.budget_stop_reason}")
            return None, blank_error

        self.records.append(self._record(
            ctx, model, provider, CallStatus.OK, usage, started=started,
            extra={
                "finish_reason": finish_reason or "stop",
                "context_breakdown": context_breakdown,
                "prompt_prefix": prompt_prefix,
                "prompt_total_chars": prompt_chars,
                "response_chars": len(text or ""),
            },
        ))
        if self.budget_stopped:
            raise LLMError(f"budget stopped: {self.budget_stop_reason}")
        if ctx.idempotency_key and cache_result:
            self.idempotency.put(ctx.idempotency_key, text)
        return CallResult(text, self.records[-1], model.id, provider.name), None

    async def complete_structured(
        self,
        *,
        node: str,
        system: str,
        user: str,
        schema: type[T],
        context: CallContext | None = None,
        context_breakdown: ContextBreakdown | None = None,
        prompt_prefix: str | None = None,
    ) -> tuple[T, CallResult]:
        """结构化输出（JSON → Pydantic 校验，失败则带错误重试）。

        旧项目 `entity_classify` 空输出 **100%**、`verifier` **62.3%** ——
        结构化输出是这类节点的主要失败面。本实现三层防护：

          1. **原生 JSON 模式**（`json_mode=True`）—— 从根上避免
             "被代码块包裹 / 缺逗号 / 字符串内裸换行"这一整类语法失败。
             实测小米 MiMo 加此参数后严格解析直接通过。
          2. **机械修复** `_repair_json` —— 兜底处理无歧义的格式瑕疵。
          3. **把具体错误回灌**给模型重试（而不是原样重发），
             并压成"缺哪个字段"的短清单（`_summarize_validation_error`）。
        """
        ctx = context or CallContext(node=node)
        last_err: Exception | None = None
        raw = ""

        for attempt in range(1, self.config.structured_retries + 2):
            instr = user
            if attempt > 1 and last_err is not None:
                instr = (
                    f"{user}\n\n"
                    f"【上次输出未通过校验，请修正】\n"
                    f"{_summarize_validation_error(last_err)}\n"
                    f"只输出符合要求的 JSON，不要任何解释或代码块标记。"
                )
            # ★ `cache_result=False`：让本函数的修复重试**真能再次请求模型**
            #   （否则第 2 次会命中第 1 次那份非法文本）。详见 `complete()`。
            result = await self.complete(
                node=node, system=system, user=instr, context=ctx,
                context_breakdown=context_breakdown, prompt_prefix=prompt_prefix,
                json_mode=True, cache_result=False,
            )
            raw = result.text
            try:
                parsed = schema.model_validate_json(self._extract_json(raw))
            except (ValidationError, ValueError) as exc:
                last_err = exc
                logger.warning(
                    "structured parse failed: node=%s attempt=%d err=%s",
                    node, attempt, str(exc)[:200],
                )
                continue
            # ★ 校验通过后才写缓存 —— 缓存里**只有**可用结果，
            #   于是"重放成功调用"仍省一次计费，而"重放非法 JSON"被消除。
            if ctx.idempotency_key and not result.from_cache:
                self.idempotency.put(ctx.idempotency_key, raw)
            return parsed, result

        raise StructuredOutputError(
            f"节点 {node} 结构化输出校验失败（{self.config.structured_retries + 1} 次尝试）："
            f"{_summarize_validation_error(last_err)}\n"
            f"原始输出前 300 字：{raw[:300]}"
        )

    # -- 辅助 ---------------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> str:
        """兼容入口；机械 JSON 处理在独立模块内完成。"""
        return _extract_structured_json(text)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).lower()
        return "429" in s or "rate" in s or "too many" in s

    def _blank_error(
        self, *, finish_reason: str | None, model_id: str,
        prompt_chars: int, reasoning_tokens: int | None,
    ) -> LLMError:
        """构造**带诊断信息**的空输出错误。

        ## 为什么必须记进 `last_err`

        否则 `max_attempts` 用尽后 `last_err` 仍是 `None`，报错变成：

            节点 detail_planner 调用失败（3 次尝试）：        ← 冒号后为空

        完全无法归因。实测踩到：多书测试里细纲阶段失败，
        日志里只有这一句没有原因的话。

        `finish_reason` 是这里最关键的一条 —— 它区分
        「模型主动停」（`stop`）与「被输出上限截断」（`length`）。
        """
        return LLMError(
            f"空输出（finish_reason={finish_reason or 'unknown'}，"
            f"prompt {prompt_chars} 字符，"
            f"model={model_id} reasoning={reasoning_tokens}）"
        )

    def _record_failure(
        self,
        ctx: CallContext,
        model: ModelCapability,
        provider: ProviderSpec,
        status: CallStatus,
        started: float,
        *,
        error_type: str | None,
        prompt_chars: int,
        retry_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """记录一次**失败**的调用（超时 / 异常）。

        统一入口的原因：失败分支原先各写各的，有的带 `prompt_total_chars`
        有的不带 → 记录里出现 `prompt=None`，无法回答"是 prompt 太大被拒"。
        **失败记录的诊断价值不比成功记录低**（本轮多次归因都靠它）。
        """
        self.records.append(
            self._record(
                ctx, model, provider, status, UsageMetrics(),
                started=started, error_type=error_type, retry_reason=retry_reason,
                extra={
                    "finish_reason": None,
                    "prompt_total_chars": prompt_chars,
                    **(extra or {}),
                },
            )
        )

    def _timeout_error(
        self, *, model_id: str, attempt: int, prompt_chars: int
    ) -> LLMError:
        """构造**带上下文**的超时错误。

        `asyncio.wait_for` 抛的 `TimeoutError` **不带任何消息**
        （实测 `str(exc) == ''`）→ 直接赋给 `last_err` 会让报错变成
        「节点 X 调用失败（3 次尝试）：」后面空着，无法归因。
        """
        return LLMError(
            f"请求超时（上限 {self.config.timeout_s}s，"
            f"attempt {attempt}/{self.config.max_attempts}，"
            f"model={model_id}，prompt {prompt_chars} 字符）"
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        """指数退避。

        ⚠️ 429 的等待**不计入 deadline** —— 等待非计算（旧项目同款判断：
        「等待不计入挂机预算」，免费档拥堵时靠它熬过去）。
        """
        delay = min(self.config.backoff_base_s * (2 ** (attempt - 1)),
                    self.config.backoff_max_s)
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse_usage(self, raw: dict[str, Any] | None) -> UsageMetrics:
        """从 provider 返回的 usage 构造 `UsageMetrics`。

        ⚠️ 缺失字段保持 `None`（→ `usage_source = unavailable/partial`），
        **绝不冒充 0**。旧项目 `native` 只要 ≥3/5 字段就算，
        导致 33 行 `cached_tokens` 为 NULL → 成本**漏报**。
        """
        d = raw or {}
        return UsageMetrics(
            prompt_tokens=d.get("prompt_tokens"),
            completion_tokens=d.get("completion_tokens"),
            reasoning_tokens=d.get("reasoning_tokens"),
            cached_tokens=d.get("cached_tokens"),
            cache_creation_tokens=d.get("cache_creation_tokens"),
            total_tokens=d.get("total_tokens"),
        )

    def _record(
        self,
        ctx: CallContext,
        model: ModelCapability,
        provider: ProviderSpec,
        status: CallStatus,
        usage: UsageMetrics,
        *,
        started: float,
        error_type: str | None = None,
        retry_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMCallRecord:
        """构造调用记录。**所有字段都填，包括旧项目缺失的诊断字段。**"""
        e = extra or {}
        prefix = e.get("prompt_prefix")
        elapsed_ms = (time.monotonic() - started) * 1000 if started else None
        # BLANK 状态必须有诊断信息（`LLMCallRecord` 会校验）
        finish_reason = e.get("finish_reason")
        if status == CallStatus.BLANK and not (finish_reason or error_type):
            finish_reason = "unknown"

        return LLMCallRecord(
            call_id=uuid.uuid4().hex[:24],
            run_id=ctx.run_id,
            book_id=ctx.book_id,
            global_no=ctx.global_no,
            node_name=ctx.node,
            attempt_no=ctx.attempt_no,
            model=model.id,
            provider=provider.name,
            usage=usage,
            finish_reason=finish_reason,
            context_breakdown=e.get("context_breakdown"),
            prompt_prefix_hash=hash_prompt_prefix(prefix) if prefix else None,
            prompt_total_chars=e.get("prompt_total_chars"),
            elapsed_ms=elapsed_ms,
            status=status,
            retry_reason=retry_reason,
            error_type=error_type,
        )

    # -- 统计 ---------------------------------------------------------------

    def blank_rate(self) -> float:
        """本次运行的空输出率。旧项目实测 **22.2%**。"""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.is_blank) / len(self.records)

    def truncated_count(self) -> int:
        return sum(1 for r in self.records if r.truncated)


__all__ = [
    "LLMClient",
    "LLMClientProtocol",
    "LLMError",
    "StructuredOutputError",
    "CallContext",
    "CallResult",
    "ClientConfig",
    "IdempotencyStore",
    "MemoryIdempotencyStore",
]
