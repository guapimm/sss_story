"""模型能力声明与路由（决策 D-09：能力声明式，不绑定厂商）。

## 为什么需要这一层

旧项目实测：**同一套 prompt** 下，
  · `mimo-v2.5`      空输出率 **0.0%**
  · `deepseek-v4.1`  空输出率 **25.9%**
  · `qwen3.8-flash`  空输出率 **91.4%**

说明「思考预算 / 输出预算」的分配策略**不具跨模型鲁棒性**，必须由
**模型能力标志**驱动，而不是在代码里假设。因此：

  · 代码中**禁止** `if model == "xxx"`（由 tests/contract 强制）
  · 节点声明**能力需求**（`NodeRequirement`），路由器按能力匹配模型
  · 新模型接入 = 改 `config/providers.toml`，零代码改动

## 另一个关键点：抽取类节点必须禁止思考

旧项目 `entity_classify` 空输出 **100%**（32/32）、`verifier` **62.3%**、
`reasoning/completion` 高达 **99.4%** —— 抽取是模式匹配任务，长思考只会
烧掉输出预算。因此这类节点显式声明 `thinking = "forbidden"`。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from storyforge.config import DEFAULT_CONFIG_DIR, REPO_ROOT

ThinkingMode = Literal["allowed", "forbidden", "required"]
ReasoningControl = Literal["none", "effort", "budget"]
CacheMode = Literal["none", "automatic_prefix", "explicit_control"]


class ProviderConfigError(RuntimeError):
    """配置错误必须显式抛出 —— 不静默降级（旧项目 R-… / 配置静默覆盖的教训）。"""


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModelCapability:
    """一个模型的能力声明。字段决定**调用策略**，不只是元信息。"""

    id: str
    provider: str
    tier: str
    context_window: int
    max_output_tokens: int
    supports_structured_output: bool
    supports_reasoning: bool
    reasoning_control: ReasoningControl
    cache_mode: CacheMode
    concurrency: int = 1
    default_reasoning_effort: str | None = None

    @property
    def can_cache(self) -> bool:
        return self.cache_mode != "none"


@dataclass(frozen=True)
class ProviderSpec:
    """提供方连接信息。API Key 只存**环境变量名**，绝不明文落盘。"""

    name: str
    base_url: str
    api_key_env: str

    def resolve_api_key(self) -> str | None:
        """从环境变量取 key。缺失返回 None（由调用方决定是否报错）。"""
        return os.environ.get(self.api_key_env) or None


@dataclass(frozen=True)
class NodeRequirement:
    """节点对模型的能力需求 —— 路由依据。"""

    node: str
    tier: str
    supports_structured_output: bool = False
    needs_long_output: bool = False
    thinking: ThinkingMode = "allowed"
    #: 额外约束，用于将来扩展（不参与当前匹配逻辑）
    extra: dict[str, Any] = field(default_factory=dict)


class NoCapableModelError(RuntimeError):
    """没有满足能力需求的模型 —— 必须显式失败，不静默降级到不合适的模型。"""


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #


class ModelRegistry:
    """模型能力注册表。从 providers.toml 加载，支持运行时替换（测试用）。"""

    def __init__(
        self,
        providers: dict[str, ProviderSpec],
        models: list[ModelCapability],
        node_requirements: dict[str, NodeRequirement],
        default_provider: str | None = None,
    ) -> None:
        if not models:
            raise ProviderConfigError("providers.toml 未声明任何模型")
        self._providers = dict(providers)
        self._models = list(models)
        self._node_requirements = dict(node_requirements)
        self._default_provider = default_provider

    # -- 查询 --------------------------------------------------------------

    @property
    def providers(self) -> dict[str, ProviderSpec]:
        return dict(self._providers)

    @property
    def models(self) -> list[ModelCapability]:
        return list(self._models)

    def get_model(self, model_id: str) -> ModelCapability:
        for m in self._models:
            if m.id == model_id:
                return m
        raise ProviderConfigError(f"未声明的模型：{model_id}")

    def get_provider(self, name: str) -> ProviderSpec:
        if name not in self._providers:
            raise ProviderConfigError(f"未声明的提供方：{name}")
        return self._providers[name]

    def requirement_for(self, node: str) -> NodeRequirement:
        if node not in self._node_requirements:
            raise ProviderConfigError(
                f"节点 {node!r} 未在 providers.toml 的 [nodes.*] 中声明能力需求。"
                f"禁止在代码中直接指定模型（决策 D-09）。"
            )
        return self._node_requirements[node]

    # -- 路由 --------------------------------------------------------------

    def _matches(self, model: ModelCapability, req: NodeRequirement) -> bool:
        if req.supports_structured_output and not model.supports_structured_output:
            return False
        # thinking="forbidden" 要求模型**支持关闭思考**；
        # supports_reasoning=False 的模型天然满足（它本来就不思考）。
        if req.thinking == "forbidden" and model.supports_reasoning:
            # 支持思考但能通过 reasoning_control 关掉也算合格
            if model.reasoning_control == "none":
                return False
        if req.thinking == "required" and not model.supports_reasoning:
            return False
        return True

    def route(self, node: str) -> tuple[ModelCapability, ProviderSpec]:
        """按节点的能力需求选模型。

        匹配顺序：同 tier 优先 → 若同 tier 无候选，放宽 tier 但**能力必须全满足**。
        找不到候选时**抛错**，不静默降级（避免把抽取任务发给长思考模型）。

        注意：放宽 tier 只放宽「档位偏好」，**绝不放宽能力约束**。
        `thinking="required"` 的节点永远不会拿到不支持思考的模型 —— 这是硬约束。
        """
        req = self.requirement_for(node)
        candidates = [m for m in self._models if m.tier == req.tier and self._matches(m, req)]
        if not candidates:
            # 放宽 tier（档位是偏好），但能力约束仍必须满足
            candidates = [m for m in self._models if self._matches(m, req)]
        if not candidates:
            raise NoCapableModelError(
                f"节点 {node!r} 需要 tier={req.tier} "
                f"structured={req.supports_structured_output} thinking={req.thinking}，"
                f"但 providers.toml 中没有满足条件的模型。"
                f"（不会降级到不满足能力约束的模型）"
            )
        model = candidates[0]
        return model, self.get_provider(model.provider)

    def thinking_policy_for(self, node: str, model: ModelCapability) -> dict[str, Any]:
        """把「节点需求 + 模型能力」翻译成客户端可直接用的调用参数。

        返回值示例：
          {"enable_thinking": False}                        ← 抽取类，强制不思考
          {"reasoning_effort": "low"}                       ← 支持 effort 档
          {"reasoning_max_tokens": 4096}                    ← 支持显式预算
          {}                                               ← 模型不支持思考，无需参数
        """
        req = self.requirement_for(node)
        if not model.supports_reasoning:
            return {}
        if req.thinking == "forbidden":
            return {"enable_thinking": False}
        if model.reasoning_control == "effort":
            return {"reasoning_effort": model.default_reasoning_effort or "low"}
        if model.reasoning_control == "budget":
            return {"enable_thinking": True}
        return {}


# --------------------------------------------------------------------------- #
# 从 TOML 构造
# --------------------------------------------------------------------------- #


def _parse_capability(raw: dict[str, Any]) -> ModelCapability:
    missing = [
        k
        for k in ("id", "provider", "tier", "context_window", "max_output_tokens")
        if k not in raw
    ]
    if missing:
        raise ProviderConfigError(f"模型声明缺字段 {missing}：{raw.get('id', raw)}")
    return ModelCapability(
        id=raw["id"],
        provider=raw["provider"],
        tier=raw.get("tier", "balanced"),
        context_window=int(raw["context_window"]),
        max_output_tokens=int(raw["max_output_tokens"]),
        supports_structured_output=bool(raw.get("supports_structured_output", False)),
        supports_reasoning=bool(raw.get("supports_reasoning", False)),
        reasoning_control=raw.get("reasoning_control", "none"),
        cache_mode=raw.get("cache_mode", "none"),
        concurrency=int(raw.get("concurrency", 1)),
        default_reasoning_effort=raw.get("default_reasoning_effort"),
    )


def load_registry(path: Path | None = None) -> ModelRegistry:
    """从 providers.toml 加载。文件缺失或格式错误一律抛错。"""
    p = path or (DEFAULT_CONFIG_DIR / "providers.toml")
    if not p.exists():
        raise ProviderConfigError(f"找不到 providers 配置：{p}")
    with p.open("rb") as f:
        raw = tomllib.load(f)

    providers: dict[str, ProviderSpec] = {}
    for name, spec in (raw.get("providers") or {}).items():
        providers[name] = ProviderSpec(
            name=name,
            base_url=spec.get("base_url", ""),
            api_key_env=spec.get("api_key_env", ""),
        )

    models = [_parse_capability(m) for m in (raw.get("models") or [])]

    # 模型引用的 provider 必须存在
    for m in models:
        if m.provider not in providers:
            raise ProviderConfigError(
                f"模型 {m.id!r} 引用了未声明的提供方 {m.provider!r}"
            )

    reqs: dict[str, NodeRequirement] = {}
    for node, spec in (raw.get("nodes") or {}).items():
        requires = spec.get("requires") or {}
        reqs[node] = NodeRequirement(
            node=node,
            tier=spec.get("tier", "balanced"),
            supports_structured_output=bool(requires.get("supports_structured_output", False)),
            needs_long_output=bool(requires.get("needs_long_output", False)),
            thinking=spec.get("thinking", "allowed"),
        )

    return ModelRegistry(
        providers=providers,
        models=models,
        node_requirements=reqs,
        default_provider=raw.get("default_provider"),
    )


# 运行时注入（与 config 同风格：不在 import 期冻结）
_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = load_registry()
    return _registry


def configure_registry(registry: ModelRegistry | None) -> None:
    global _registry
    _registry = registry


__all__ = [
    "ThinkingMode",
    "ReasoningControl",
    "CacheMode",
    "ModelCapability",
    "ProviderSpec",
    "NodeRequirement",
    "ModelRegistry",
    "ProviderConfigError",
    "NoCapableModelError",
    "load_registry",
    "get_registry",
    "configure_registry",
]
