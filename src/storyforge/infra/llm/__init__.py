"""LLM 基础设施层 —— 能力声明、客户端、Prompt 组装、计量。

依赖方向：本层可依赖 `observability/`（计量契约），
但 `domain/` 与 `scorecard/` **不得**依赖本层（由 tests/contract 强制）。
"""

from storyforge.infra.llm.capability import (
    CacheMode,
    ModelCapability,
    ModelRegistry,
    NoCapableModelError,
    ProviderConfigError,
    ProviderSpec,
    ReasoningControl,
    ThinkingMode,
    configure_registry,
    get_registry,
    load_registry,
)
from storyforge.infra.llm.client import (
    CallContext,
    CallResult,
    ClientConfig,
    IdempotencyStore,
    LLMClient,
    LLMClientProtocol,
    LLMError,
    MemoryIdempotencyStore,
    StructuredOutputError,
)
from storyforge.infra.llm.prompt_builder import (
    Chunk,
    ChunkList,
    PromptLayerError,
    PromptLayers,
    diff_layers,
)

__all__ = [
    # capability
    "CacheMode",
    "ModelCapability",
    "ModelRegistry",
    "NoCapableModelError",
    "ProviderConfigError",
    "ProviderSpec",
    "ReasoningControl",
    "ThinkingMode",
    "configure_registry",
    "get_registry",
    "load_registry",
    # client
    "CallContext",
    "CallResult",
    "ClientConfig",
    "IdempotencyStore",
    "LLMClient",
    "LLMClientProtocol",
    "LLMError",
    "MemoryIdempotencyStore",
    "StructuredOutputError",
    # prompt
    "Chunk",
    "ChunkList",
    "PromptLayerError",
    "PromptLayers",
    "diff_layers",
]
