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
import json
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
from storyforge.observability.metering import (
    ContextBreakdown,
    LLMCallRecord,
    UsageMetrics,
    hash_prompt_prefix,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


#: JSON 字符串内部的**裸控制字符**（未转义的换行/制表/回车）。
#:
#: ## 为什么必须修（实测）
#:
#: 真实运行（小米 MiMo）反复出现：模型在 JSON 字符串里直接换行 ——
#:
#:     {"worldview": "世界观围绕一座档案馆展开。
#:     地下三层是绝对禁区。..."}
#:
#: 这在**中文长文本**上极其常见：模型把"段落"当成排版的一部分，
#: 于是照着写换行。而 JSON 规范禁止字符串内出现裸换行
#: （`Invalid JSON: expected ':'` 就是它引发的报错）。
#:
#: 后果与旧项目 `entity_classify` 空输出 100% 同源：内容完全正确，
#: 只因一个可机械修复的格式瑕疵，整条链路失败三次后放弃。
#:
#: 修法是**在字符串字面量内部**把裸控制字符转义 —— 注意只在字符串内部动，
#: 结构性的换行（键值之间）必须保留。所以这里用一个状态机逐字符扫描，
#: 而不是全局 `replace("\n", "\\n")`（那会把 JSON 结构也毁掉）。
_BARE_CONTROL_IN_STRING = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _summarize_validation_error(exc: Exception | None) -> str:
    """把 Pydantic 校验错误压成**模型能照着改的**简短清单。

    ## 为什么要压

    Pydantic 的完整报错含 URL、type 名、input 回显，一次能有两千字。
    把它原样塞进修复 prompt 有两个坏处：
      · 稀释注意力（真正要改的只有"缺哪个字段"）
      · 白烧 token（每次修复都重发一遍）

    实测需要的信息其实只有三条：**哪个字段**、**期望什么**、**实际给了什么**。
    这三条压出来通常不到 200 字。
    """
    if exc is None:
        return "（无错误详情）"
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)[:400]
    try:
        items = errors()
    except Exception:  # noqa: BLE001
        return str(exc)[:400]

    lines: list[str] = []
    for e in items[:12]:                       # 最多列 12 条，再多也没人看
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = str(e.get("msg", ""))[:120]
        etype = e.get("type", "")
        if etype == "missing":
            lines.append(f"- 缺必填字段：`{loc}`")
        elif etype == "extra_forbidden":
            lines.append(f"- 多出未定义字段：`{loc}`（请删除）")
        else:
            lines.append(f"- `{loc}`：{msg}")
    if len(items) > 12:
        lines.append(f"- …另有 {len(items) - 12} 处同类问题")
    return "\n".join(lines) if lines else str(exc)[:400]


def _repair_json(s: str) -> str:
    """修复模型 JSON 输出的**常见机械瑕疵**。

    只做**不改变语义**的修复（转义字符串内的裸控制字符）。
    不做"补引号""猜键名"这类有歧义的修复 —— 那些应当报错，
    让上游用具体的错误信息去重试。

    扫描规则（单遍状态机）：
      · 遇到未转义的 `"` → 翻转「是否在字符串内」
      · 在字符串内遇到裸控制字符 → 替换成转义形式
      · 字符串外的字符一律不动
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in s:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in _BARE_CONTROL_IN_STRING:
            out.append(_BARE_CONTROL_IN_STRING[ch])
            continue
        out.append(ch)
    return "".join(out)


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
    ) -> CallResult:
        """单次补全（带重试、计量、幂等）。

        `json_mode=True` 时要求 provider 走**原生 JSON 输出**
        （`response_format={"type":"json_object"}`）。仅当模型声明
        `supports_json_mode` 时才会真正下发 —— 见 `_resolve`。
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

        params: dict[str, Any] = dict(policy)

        # ★ 输出预算：显式下发 `max_tokens`，否则会被 provider 默认值坑死。
        #
        # ## 实测发现的真实缺陷（多书测试书 16 细纲阶段）
        #
        # `ModelCapability.max_output_tokens` 从 `providers.toml` 读了进来
        # （MiMo 声明 131072），但**从未下发给 API** —— 只有调用方显式传
        # `max_output_tokens` 才设置，而所有节点都没传。
        # 于是实际生效的是 provider 默认值（MiMo 上实测约 8000）：
        #
        #     prompt 4126 字符 → completion=8000, reasoning=7900（98.75%）
        #     finish_reason=length → 正文一个字都没写出来 → 三次尝试全废
        #
        # 这与旧项目 `max_tokens = max(4096, reasoning*4)`（思考与输出共用上限
        # → 可用输出预算只剩 3x）是**同一类失败**：思考把输出挤没。
        #
        # ## 修法：默认用模型声明的上限，而不是 provider 默认值
        #
        # 调用方仍可显式覆盖（章节写作会传具体值）。这里只保证
        # 「不传」时**不会**退化成一个小到被思考吃光的默认值。
        #
        # 两端都夹：下限保证正文有空间，上限防止把模型的完整上下文长度
        # 当成输出预算（那会让每次调用都预留巨额额度）。
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
            except TimeoutError as exc:
                last_err = exc
                self.records.append(
                    self._record(
                        ctx, model, provider, CallStatus.TIMEOUT, UsageMetrics(),
                        started=started, error_type="TimeoutError",
                        extra={"finish_reason": None},
                    )
                )
                await self._sleep_backoff(attempt)
                continue
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                status = (
                    CallStatus.FAILED
                    if not self._is_rate_limit(exc)
                    else CallStatus.FAILED
                )
                self.records.append(
                    self._record(
                        ctx, model, provider, status, UsageMetrics(),
                        started=started, error_type=type(exc).__name__,
                        retry_reason="rate_limit" if self._is_rate_limit(exc) else None,
                    )
                )
                await self._sleep_backoff(attempt)
                continue

            usage = self._parse_usage(usage_raw)
            finish_reason = (usage_raw or {}).get("finish_reason")

            # ★ 空输出：必须记录，且必须带诊断信息
            if not (text or "").strip():
                # ★ 必须把空输出也记进 `last_err` ——
                # 否则 max_attempts 用尽后 `last_err` 仍是 None，
                # 报错变成 "节点 detail_planner 调用失败（3 次尝试）："（**空的**），
                # 完全无法归因。实测踩到：多书测试里细纲阶段失败，
                # 日志里只有这一句没有原因的话。
                last_err = LLMError(
                    f"空输出（finish_reason={finish_reason or 'unknown'}，"
                    f"prompt {len(system) + len(user)} 字符，"
                    f"model={model.id} reasoning={usage.reasoning_tokens}）"
                )
                self.records.append(
                    self._record(
                        ctx, model, provider, CallStatus.BLANK, usage,
                        started=started,
                        error_type=None if finish_reason else "BlankWithoutFinishReason",
                        extra={
                            "finish_reason": finish_reason or "unknown",
                            "context_breakdown": context_breakdown,
                            "prompt_prefix": prompt_prefix,
                            "prompt_total_chars": len(system) + len(user),
                        },
                    )
                )
                logger.warning(
                    "blank output: node=%s model=%s attempt=%d finish_reason=%s "
                    "reasoning=%s prompt_chars=%d",
                    node, model.id, attempt, finish_reason,
                    usage.reasoning_tokens, len(system) + len(user),
                )
                await self._sleep_backoff(attempt)
                continue

            # 成功
            self.records.append(
                self._record(
                    ctx, model, provider, CallStatus.OK, usage, started=started,
                    extra={
                        "finish_reason": finish_reason or "stop",
                        "context_breakdown": context_breakdown,
                        "prompt_prefix": prompt_prefix,
                        "prompt_total_chars": len(system) + len(user),
                        # ★ 缺陷1/A-16诊断：成功也记输出字符数 ——
                        # 空输出/截断归因需要"输出多长"，不能只记 prompt 多长
                        "response_chars": len(text or ""),
                    },
                )
            )
            if ctx.idempotency_key:
                self.idempotency.put(ctx.idempotency_key, text)
            return CallResult(
                text=text, record=self.records[-1],
                model=model.id, provider=provider.name,
            )

        raise LLMError(
            f"节点 {node} 调用失败（{self.config.max_attempts} 次尝试）：{last_err}"
        )

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
            result = await self.complete(
                node=node, system=system, user=instr, context=ctx,
                context_breakdown=context_breakdown, prompt_prefix=prompt_prefix,
                json_mode=True,
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
            return parsed, result

        raise StructuredOutputError(
            f"节点 {node} 结构化输出校验失败（{self.config.structured_retries + 1} 次尝试）："
            f"{_summarize_validation_error(last_err)}\n"
            f"原始输出前 300 字：{raw[:300]}"
        )

    # -- 辅助 ---------------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> str:
        """从模型输出中提取 JSON（剥离代码块、前后缀，并**修复常见语法瑕疵**）。"""
        s = (text or "").strip()
        if s.startswith("```"):
            nl = s.find("\n")
            if nl != -1:
                s = s[nl + 1 :]
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3]
        s = s.strip()
        # 找到第一个 { 与最后一个 }
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            s = s[i : j + 1]
        else:
            i, j = s.find("["), s.rfind("]")
            if i != -1 and j != -1 and j > i:
                s = s[i : j + 1]
        # 先试原样，能解析就不动它（**不冒险改写正确输入**）
        try:
            json.loads(s)
            return s
        except (ValueError, TypeError):
            pass
        return _repair_json(s)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        s = str(exc).lower()
        return "429" in s or "rate" in s or "too many" in s

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
