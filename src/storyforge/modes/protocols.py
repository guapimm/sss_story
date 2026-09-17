"""模式能力层 —— **8 个职责单一的 Protocol**（决策 D-08）。

## 为什么不是继承树（旧项目实测）

旧项目 `flowmode/` 把模式差异外置的思路是**对的**，但形态有三个问题：

| 问题 | 实测证据 |
|---|---|
| **上帝接口** | `FlowMode` 基类 **37 个方法**，`_flow(book).` 调用点 **48 处** |
| **方向反了** | 现在是 **story 侧给 learn 钩子写空实现**（`story_flow.py:171-201` 有 8 个 `@staticmethod` 空实现） |
| **门面外仍有直读** | 门面之外 **30+ 处**直接判 `book_kind`（`main.py:1304` 路由层绕过门面、`drift.py` 4 处 `is_story` 形参…） |

**新设计**：8 个 Protocol 组合，各自独立可替换。story 只需其中 3 个
（`PromptPack` + `GenrePlugin` + `Estimator`），learn 专属的 5 个
在 story 侧**不实现也不占位**（由 `modes/story.py` 的 `StoryMode`
只暴露自己那 3 个）。

## ★ 正向着色 vs 反向空实现（D-08 的核心）

    旧：story 侧给 learn 的钩子写**空实现** → 调用返回空结果，**静默出错**
    新：learn 侧对未实现的 Provider **显式 raise NotImplementedError**

差别的意义：空实现会让"learn 没做"表现成"learn 做了但结果为空"，
调用方无法区分；显式抛错让问题在**第一次调用**就暴露。

## 分层约束

本模块属于 **L2**（domain 之上、graph 之下），**不得** import：
  · `infra.llm`（模式层不直接调模型；组装 prompt 是 `infra/prompts` 的事）
  · `graph.*`（模式层不依赖图；图反向依赖模式层）

由契约测试强制（见 `tests/contract/test_architecture_invariants.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# 1. LearningMap —— 学习地图（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KnowledgePoint:
    """一个知识点 —— learn 模式的教学原子。

    ★ 与旧项目 `KnowledgeLedger` 的区别：旧项目把"知识点"和"教学账本"
    混在一张表里（`point_name` / `status` / `taught_ch` / `review_count` /
    `next_review_ch`），导致"知识点是什么"与"教到哪了"两个关注点耦合。
    本设计拆开：`KnowledgePoint` 是**内容**，`TeachingRecord` 是**进度**。
    """

    #: 稳定标识（跨章引用用；不由模型生成）
    point_id: str
    #: 知识点名（正文里可以出现，但「出戏原名」受 style_critic 约束）
    name: str
    #: 一句话说明
    summary: str = ""
    #: 所属主题/章节分组
    topic: str = ""
    #: 难度（供排序与复习调度）
    difficulty: int = 1


@dataclass(frozen=True)
class LearningMap:
    """一张学习地图 —— 研究产出的知识点集合。"""

    topic: str
    points: tuple[KnowledgePoint, ...] = ()

    def __len__(self) -> int:
        return len(self.points)


@runtime_checkable
class LearningMapProvider(Protocol):
    """学习地图构建（learn 专属）。"""

    def build_map(self, topic: str) -> LearningMap:
        """按主题构建学习地图。"""
        ...


# --------------------------------------------------------------------------- #
# 2. TeachingLedger —— 教学账本（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass
class TeachingRecord:
    """一个知识点的教学进度。

    `next_review_chapter` 由艾宾浩斯间隔算出（见 `modes/learn.py` 的
    间隔表）—— 旧项目 `review_count` / `next_review_ch` 有字段但
    **没有任何调度实现**。
    """

    point_id: str
    taught_chapter: int
    review_count: int = 0
    next_review_chapter: int | None = None

    @property
    def is_taught(self) -> bool:
        return self.taught_chapter > 0


@runtime_checkable
class TeachingLedgerProvider(Protocol):
    """教学账本（learn 专属）—— 记录教过什么、何时复习。"""

    def record_taught(
        self, chapter_no: int, points: list[KnowledgePoint]
    ) -> None: ...

    def untaught(self) -> list[KnowledgePoint]:
        """还没教过的知识点（供下一步规划）。"""
        ...


# --------------------------------------------------------------------------- #
# 3. KnowledgeCardProvider —— 知识卡（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KnowledgeCard:
    """章末知识卡 —— 一章结束时提炼出的可复习条目。

    旧项目存 `Chapter.cards`（Text，默认 `"[]"`），由
    `ChapterPostProcessor` 生成；story 侧的对应物是"记忆记录员"。
    """

    chapter_no: int
    point_id: str
    title: str
    body: str = ""


@runtime_checkable
class KnowledgeCardProvider(Protocol):
    """知识卡抽取与渲染（learn 专属）。"""

    def extract_cards(
        self, prose: str, points: list[KnowledgePoint]
    ) -> list[KnowledgeCard]: ...

    def render_section(self, cards: list[KnowledgeCard]) -> str:
        """把知识卡渲染成注入审校 prompt 的小节。"""
        ...


# --------------------------------------------------------------------------- #
# 4. ResearchProvider —— 联网研究（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResearchSnippet:
    """一条检索结果。"""

    title: str
    url: str = ""
    text: str = ""


@dataclass(frozen=True)
class ResearchResult:
    """一次研究的结果集合。"""

    topic: str
    snippets: tuple[ResearchSnippet, ...] = ()
    #: 已完成的阶段（供断点续跑）—— 旧项目 `{queries/snippets/map/verdicts}`
    stage: str = ""


@runtime_checkable
class ResearchProvider(Protocol):
    """联网研究（learn 专属）。

    旧项目用 Tavily（`api.tavily.com/search`），带 `research_cache.json`
    缓存与分阶段断点续跑。**story 侧不实现** —— 省一次 LLM 调用与全部网络依赖
    （实测 `estimator.py:30-31`：learn 每章 research=1 次，story=0 次）。
    """

    def search(self, query: str, max_results: int = 5) -> list[ResearchSnippet]: ...

    def research(self, topic: str) -> ResearchResult: ...


# --------------------------------------------------------------------------- #
# 5. FactVerifier —— 事实核验（learn 专属）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    """一条待核验的陈述。"""

    text: str
    #: 出处（若来自研究结果）
    source: str = ""


@dataclass(frozen=True)
class VerificationResult:
    """核验结论。"""

    claim: Claim
    #: True=有支撑 / False=与来源矛盾 / None=无法判定（**不得当 False**）
    supported: bool | None
    reason: str = ""


@runtime_checkable
class FactVerifier(Protocol):
    """事实核验（learn 专属）—— 教学内容的正确性闸门。"""

    def verify(self, claims: list[Claim]) -> list[VerificationResult]: ...


# --------------------------------------------------------------------------- #
# 6. Estimator —— 成本预估（story 与 learn 都要）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Scope:
    """预估范围。"""

    #: 知识点总数（learn 用；story 恒 0）
    points_count: int = 0
    #: 每模块的预估章数之和
    est_chapters: int = 0


@dataclass(frozen=True)
class Estimate:
    """预估结果（**不含 speed** —— 速度依赖历史数据，属实现细节）。"""

    chapter_count: int
    total_tokens: int
    est_seconds: int
    #: 逐环节拆分（旧项目 `breakdown`，逐角色 calls/tokens）
    breakdown: tuple[dict[str, Any], ...] = ()
    #: 知识点数（story 恒 0）
    points_count: int = 0
    #: 多卷总览（仅 ultra 档；其余为空）
    volumes: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Estimator(Protocol):
    """成本与时长预估。

    ★ 签名用 `Scope`/`Scale` 而非旧项目的
    `estimate(selected, scale, quality, overflow, story: bool)` ——
    `story: bool` 是**模式分支的典型形态**（契约规则 3 会拦下）。
    改枚举/结构化参数后，模式差异由实现类承担，共享代码零分支。

    旧项目实测差异（`estimator.py:30-31`）：
      · story：无知识点（不计 `points_count`）、skip 联网研究、不产知识卡
      · learn：每章 research 1 次、产知识卡
    """

    def estimate(self, scope: Scope, scale: str, quality: str) -> Estimate: ...


# --------------------------------------------------------------------------- #
# 7. GenrePlugin —— 流派插件（story 与 learn 都要）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GenreContext:
    """流派插件拿到的上下文（写作/审校时）。"""

    topic: str = ""
    genre: str = ""
    #: 当前流派 id（如 `xianxia` / `knowledge_seeker`）
    flow: str = ""
    chapter_no: int = 0
    #: 其它实现相关字段（保持 Protocol 简单）
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GenrePlugin(Protocol):
    """题材/流派插件 —— 该流派的独立约束集合。

    ★ 移植自旧项目 `app/genres/base.py:9` 的 `GenrePlugin` ABC，
    其 docstring 自述职责是「该流派的**独立 Agent 栈**定义」。
    旧项目已有 4 个 builtin（default / rebirth / rules / system）
    与 `GenreManager.get(scene, flow, plugin_mode)`。

    ⚠️ 旧项目 `plugin_mode` 就是**模式分支的参数化形态**，本设计
    改为「由 `modes/` 层提供对应实现」，共享代码不判模式。
    """

    def writer_blocks(self, ctx: GenreContext) -> list[str]:
        """写作时该流派的额外约束块。"""
        ...

    def extract_hints(self) -> str:
        """给抽取器的流派专属提示。旧项目已实现（模式感知）。"""
        ...


# --------------------------------------------------------------------------- #
# 8. PromptPack —— 提示词整包（story 与 learn 都要）
# --------------------------------------------------------------------------- #


@runtime_checkable
class PromptPack(Protocol):
    """提示词整包 —— 旧项目 `FlowMode` 的 **37 个方法**收敛到这里。

    ★ 这是 learn 的 prompt 能否落地的**单点前提**：
    当前 `infra/prompts/library.py:55` 的 `PROMPTS_DIR` 是**单一扁平目录、
    无模式维度**，learn 的 `writer_system.md` 无处安放（会与 story 撞名）。

    三个方法的语义（与 `01-总体架构.md:394-397` 一致）：
      · `writer_system_extension()` —— 追加到 writer 的 system prompt
      · `extractor_instructions()` —— 抽取节点的额外指令
      · `critic_instructions()` —— 审校节点的额外指令
    """

    #: 模板子目录名（`""` = 现有扁平目录，保持 3 个既有调用点零改动）
    dir_name: str

    def writer_system_extension(self) -> str: ...

    def extractor_instructions(self) -> str: ...

    def critic_instructions(self) -> str: ...


# --------------------------------------------------------------------------- #
# Mode —— 模式对象（Protocol 的组合）
# --------------------------------------------------------------------------- #


@runtime_checkable
class Mode(Protocol):
    """一个模式 = 若干 Protocol 的组合（`01-总体架构.md:400`）。

    ★ 刻意**不**列出全部 8 个属性：这样"模式提供哪些能力"就是实现类
    自己的事，共享代码只能通过 `getattr` 探测 —— 而不是像旧项目那样
    假设"任何模式都有 37 个方法"。

    调用方应使用 `require_capability()` 而不是直接 `getattr` 后调用。
    """

    #: 模式标识（`story` / `learn`），与 `BookMode` 枚举值一致
    name: str

    #: 提示词包（所有模式都实现）
    prompts: PromptPack

    #: 流派插件（所有模式都实现）
    genre: GenrePlugin

    #: 成本预估（所有模式都实现）
    estimator: Estimator


#: learn 专属的 5 个能力名 —— 供 `require_capability` 与契约测试共用。
LEARN_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "learning_map",
        "ledger",
        "cards",
        "research",
        "verifier",
    }
)

#: story 与 learn 都提供的能力名。
SHARED_CAPABILITIES: frozenset[str] = frozenset(
    {"prompts", "genre", "estimator"}
)


class CapabilityNotAvailable(NotImplementedError):
    """请求了该模式未提供的能力。

    继承 `NotImplementedError` 是刻意的：调用方若只捕获后者也能工作，
    但本类型额外带上**"哪个能力、哪个模式、怎么办"**三个信息 ——
    旧项目的空实现只返回 `""`，调用方无法区分"没做"与"做了但为空"。
    """


def require_capability(mode: Mode, capability: str) -> Any:
    """取模式能力，缺失时抛**带修复指引**的错（D-08 正向着色）。

    用法：

        ledger = require_capability(mode, "ledger")
        ledger.record_taught(3, points)
        # story 模式下 → CapabilityNotAvailable: 能力 'ledger' 未被模式
        #   'story' 提供（learn 专属）。若需启用 learn，请… 
    """
    obj = getattr(mode, capability, None)
    if obj is None:
        kind = "learn 专属" if capability in LEARN_ONLY_CAPABILITIES else "必需"
        raise CapabilityNotAvailable(
            f"能力 {capability!r} 未被模式 {getattr(mode, 'name', '?')!r} 提供"
            f"（{kind}）。"
            + (
                "若需启用 learn 模式，请实现该 Protocol；"
                "一期只做 story（决策 D-08）。"
                if capability in LEARN_ONLY_CAPABILITIES
                else "这是一个必需能力，说明该模式的实现不完整。"
            )
        )
    return obj


__all__ = [
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
    # 8 个 Protocol
    "LearningMapProvider",
    "TeachingLedgerProvider",
    "KnowledgeCardProvider",
    "ResearchProvider",
    "FactVerifier",
    "Estimator",
    "GenrePlugin",
    "PromptPack",
    # 组合与工具
    "Mode",
    "LEARN_ONLY_CAPABILITIES",
    "SHARED_CAPABILITIES",
    "CapabilityNotAvailable",
    "require_capability",
]
