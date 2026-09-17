"""模式能力层（决策 D-08）—— story / learn 的**唯一差异承载处**。

## 本层解决的确切问题

旧项目 `flowmode/` 的思路（把模式差异外置）是正确的，但形态有三个问题：

| 问题 | 旧项目实测 |
|---|---|
| **上帝接口** | `FlowMode` 基类 **37 个方法**，`_flow(book).` 调用点 **48 处** |
| **方向反了** | story 侧给 learn 写 **8 个 `@staticmethod` 空实现**（`story_flow.py:171-201`）|
| **门面外仍有直读** | 门面外 **30+ 处**直接判 `book_kind`（路由层绕过门面、`is_story` 形参…）|

本层的对策：
  1. **8 个职责单一的 Protocol**（`protocols.py`），不是 37 方法的上帝接口
  2. **正向着色**：learn 侧为自己的能力占位并 `raise`（`learn.py`），
     而不是 story 侧写空实现
  3. **唯一分派点**：只有 `registry.mode_for()` 判模式，
     其余共享代码一律走 Protocol（契约规则 3 用 AST 强制）

## 分层

    L2 domain/scorecard   ← 不得 import 本层
    L2.5 **modes/**        ← 本层：模式能力（不得 import infra.llm / graph）
    L3 graph
    L5 cli

`modes/` 是契约规则 3 的 `_MODE_SCAN_SKIP` 唯一成员 —— **模式分支只许在此**。

## 一期范围

story 完整实现 3 个能力；learn 可构造但调用即抛
`CapabilityNotAvailable`（带实现指引）。
"""

from storyforge.modes.genres import (
    GenreCard,
    GenreConfigError,
    load_all_genres,
    load_genre,
)
from storyforge.modes.learn import LearnMode, LearnPromptPack, make_learn_mode
from storyforge.modes.protocols import (
    LEARN_ONLY_CAPABILITIES,
    SHARED_CAPABILITIES,
    CapabilityNotAvailable,
    Claim,
    Estimate,
    Estimator,
    FactVerifier,
    GenreContext,
    GenrePlugin,
    KnowledgeCard,
    KnowledgeCardProvider,
    KnowledgePoint,
    LearningMap,
    LearningMapProvider,
    Mode,
    PromptPack,
    ResearchProvider,
    ResearchResult,
    ResearchSnippet,
    Scope,
    TeachingLedgerProvider,
    TeachingRecord,
    VerificationResult,
    require_capability,
)
from storyforge.modes.registry import DEFAULT_GENRE, StoryMode, mode_for
from storyforge.modes.scales import ScaleConfigError, ScaleSpec, scale_spec
from storyforge.modes.story import (
    StoryEstimator,
    StoryGenrePlugin,
    StoryPromptPack,
)

__all__ = [
    # Protocols（8）
    "LearningMapProvider",
    "TeachingLedgerProvider",
    "KnowledgeCardProvider",
    "ResearchProvider",
    "FactVerifier",
    "Estimator",
    "GenrePlugin",
    "PromptPack",
    # 数据类型
    "KnowledgePoint",
    "LearningMap",
    "TeachingRecord",
    "KnowledgeCard",
    "ResearchSnippet",
    "ResearchResult",
    "Claim",
    "VerificationResult",
    "Scope",
    "Estimate",
    "GenreContext",
    # 组合与工具
    "Mode",
    "LEARN_ONLY_CAPABILITIES",
    "SHARED_CAPABILITIES",
    "CapabilityNotAvailable",
    "require_capability",
    # 实现
    "StoryMode",
    "StoryPromptPack",
    "StoryGenrePlugin",
    "StoryEstimator",
    "LearnMode",
    "LearnPromptPack",
    "make_learn_mode",
    "mode_for",
    "DEFAULT_GENRE",
    # 数据加载
    "GenreCard",
    "GenreConfigError",
    "load_genre",
    "load_all_genres",
    "ScaleSpec",
    "ScaleConfigError",
    "scale_spec",
]
