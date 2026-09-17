"""检索层 —— 联网研究（learn 专属，阶段 2-2）。

## 这一层解决什么

learn 的目标流程要求「联网搜索知识点（**按篇幅需要动态调整**，
一次不够后续动态补充，需**可视化**）」。

旧项目的实现（`app/search/provider.py` 47 行 + `research_store.py` 185 行）
证明这条路可行，但有两处必须改：

| 旧项目 | 本设计 |
|---|---|
| `SearchProvider` 基类 + `if key else Mock` 的**运行期分支** | 显式注入（`TavilyResearchProvider` / `MockResearchProvider`），无环境分支 |
| 缓存是**全局单文件 JSON**（`research_cache.json` 实测 5,576.8 KB） | 存 `data/` 下，带 TTL（旧项目 `RESEARCH_CACHE_TTL_DAYS` 有键但无实效） |

## 触发策略（用户决策：按知识点数量目标决定轮次）

判据不是"搜了几轮"，而是**知识点够不够**：

    ① 按篇幅档位算目标知识点数（`ResearchSettings.points_per_scale`）
    ② 每轮研究产出若干知识点，累计到目标就停
    ③ 不够则换角度继续搜，直到 max_rounds 或目标达成

依据：旧项目 `estimator.py` 用 `points_count` 推章数
（`chapter_count = max(mini, min(cap, points_count * 4))`）——
即约 **4 章讲 1 个知识点**。反推目标点数可让"篇幅 ↔ 知识点"两端一致。

## 分层

本层属 **L1 infra** —— 可被 `graph`/`orchestrator` 调用，
**不得** import `graph`（单向依赖）。
"""

from storyforge.infra.retrieval.provider import (
    MockResearchProvider,
    ResearchError,
    TavilyResearchProvider,
    make_research_provider,
)
from storyforge.infra.retrieval.research import (
    ANGLES,
    ResearchPlan,
    ResearchRound,
    estimate_points,
    research_topic,
    target_points_for,
)
from storyforge.infra.retrieval.store import ResearchCache

__all__ = [
    "MockResearchProvider",
    "TavilyResearchProvider",
    "ResearchError",
    "make_research_provider",
    "ResearchCache",
    "ANGLES",
    "ResearchRound",
    "ResearchPlan",
    "estimate_points",
    "target_points_for",
    "research_topic",
]
