"""研究编排 —— **按知识点数量目标决定轮次**（用户决策）。

## 为什么要编排层（而不是让调用方自己循环）

用户要求「按篇幅需要**动态调整**，一次不够**后续动态补充**」。
这句话有两个含义，都必须有落点：

  1. **停**的判据 —— 什么时候算够？
  2. **续**的判据 —— 什么时候该再搜一轮？

若把循环留给调用方，每个调用点都会自己拍一个轮次数（旧项目形态），
口径必然漂移。本模块把两者收敛成一份可解释的规则。

## 判据（不是"搜了几轮"，而是"知识点够不够"）

    ① 按篇幅档位算**目标知识点数**（`ResearchSettings.points_per_scale`）
    ② 每轮换一个**检索角度**，把结果累积进知识点池
    ③ 达到目标 → 停；不够 → 继续，直到 `max_rounds`

依据：旧项目 `estimator.py` 用 `points_count` 推章数
（`chapter_count = max(mini, min(cap, points_count * 4))`）——
约 **4 章讲 1 个知识点**。反推目标点数可让"篇幅 ↔ 知识点"两端一致。

## 检索角度（为什么不是把同一个 query 搜 6 遍）

同一 query 重复搜只会拿到同样的结果（且浪费配额）。
本模块按**角度模板**逐轮推进，每轮 query 不同：

    第 1 轮：<主题> 核心概念 基础原理
    第 2 轮：<主题> 发展历史 关键人物
    第 3 轮：<主题> 常见误区 争议
    第 4 轮：<主题> 实际应用 案例
    第 5 轮：<主题> 进阶 前沿
    第 6 轮：<主题> 与相邻领域的关系

角度用尽仍不够 → 停下来**如实报告缺口**（而不是硬凑知识点）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from storyforge.config import ResearchSettings, get_settings
from storyforge.infra.retrieval.provider import ResearchError
from storyforge.infra.retrieval.store import ResearchCache
from storyforge.modes.protocols import ResearchResult, ResearchSnippet

logger = logging.getLogger(__name__)

#: 检索角度模板 —— `{topic}` 会被替换。
#:
#: ★ 顺序经过设计：先"是什么"再"怎么来的"，然后"容易错在哪"，
#: 最后才是应用与前沿。教学内容的自然顺序。
ANGLES: tuple[str, ...] = (
    "{topic} 核心概念 基础原理",
    "{topic} 发展历史 关键人物",
    "{topic} 常见误区 争议",
    "{topic} 实际应用 案例",
    "{topic} 进阶 前沿进展",
    "{topic} 与相邻领域的关系",
)


@dataclass
class ResearchRound:
    """一轮研究的记录（供**可视化**展示"搜了什么、拿到多少"）。"""

    round_no: int
    query: str
    snippets: tuple[ResearchSnippet, ...] = ()
    #: 本轮之后累计的知识点估算数
    cumulative_points: int = 0
    from_cache: bool = False
    error: str = ""


@dataclass
class ResearchPlan:
    """一次完整研究的结果 + 过程（过程用于可视化与断点续跑）。"""

    topic: str
    scale: str
    target_points: int
    rounds: list[ResearchRound] = field(default_factory=list)
    snippets: tuple[ResearchSnippet, ...] = ()
    #: ★ 达到目标？未达到时如实说明缺口，不假装成功
    reached_target: bool = False
    stopped_reason: str = ""

    @property
    def total_snippets(self) -> int:
        return len(self.snippets)

    @property
    def estimated_points(self) -> int:
        """估算的知识点数（见 `estimate_points`）。"""
        return estimate_points(self.snippets)

    @property
    def rounds_used(self) -> int:
        return len(self.rounds)

    def summary_lines(self) -> list[str]:
        """给 CLI 的**逐轮可视化**（用户要求"避免黑盒"）。"""
        lines = [
            f"主题：{self.topic}",
            f"档位：{self.scale}    目标知识点：{self.target_points}",
            f"轮次：{self.rounds_used}    检索条数：{self.total_snippets}",
            "",
        ]
        for r in self.rounds:
            mark = "缓存" if r.from_cache else f"{len(r.snippets)} 条"
            if r.error:
                mark = f"★ {r.error}"
            lines.append(
                f"  第{r.round_no}轮  {mark:>8}  累计知识点≈{r.cumulative_points}"
                f"   query: {r.query}"
            )
        lines.append("")
        verdict = (
            f"✓ 达到目标（≈{self.estimated_points} ≥ {self.target_points}）"
            if self.reached_target
            else f"⚠ 未达目标（≈{self.estimated_points} < {self.target_points}）"
                 f" —— {self.stopped_reason}"
        )
        lines.append(verdict)
        return lines


#: 每多少个**有效片段**估算为 1 个知识点。
#:
#: ★ 刻意保守（2 条才算 1 个）：一条检索结果往往只覆盖一个概念的一部分，
#: 需要多条才能拼出一个可讲的知识点。低估会让轮次偏多（多花点配额），
#: **高估会让知识点不够就开写**（正文没料可讲）——后者代价大得多。
#:
#: ⚠️ 用**整除**而不是 `条数 × 0.5`：后者会把 1 条截断成 0 个
#: （`int(1 * 0.5) == 0`）——那比"保守"更糟，是错的。
SNIPPETS_PER_POINT = 2

#: 低于此长度的 snippet 不计入（多为导航页/摘要页，信息量不足）
MIN_SNIPPET_CHARS = 80


def estimate_points(snippets: tuple[ResearchSnippet, ...] | list[ResearchSnippet]) -> int:
    """从检索结果估算知识点数。

    ⚠️ 这是**估算**，不是精确计数 —— 精确计数需要 LLM 抽取
    （那属于 2-3 学习地图的职责）。本函数只服务于"该不该再搜一轮"，
    因此宁可保守（低估）。

    ## ★ 用「每 2 条算 1 个」而不是「条数 × 0.5 取整」

    初版写 `int(useful * 0.5)`，被自己的测试当场抓到：
    **单个有效片段算出 0 个知识点**（`int(1 * 0.5) == 0`）——
    这比"保守"更糟，是**错的**：只要有内容就至少有 1 个知识点苗子。

    改为向上取整的等价写法：每满 2 条记 1 个，奇数条补 1 个。
    这样 `1 条 → 1 个`、`2 条 → 1 个`、`3 条 → 2 个`，单调且不归零。
    """
    useful = sum(1 for s in snippets if len(s.text.strip()) >= MIN_SNIPPET_CHARS)
    if useful <= 0:
        return 0
    return (useful + SNIPPETS_PER_POINT - 1) // SNIPPETS_PER_POINT


def target_points_for(scale: str, settings: ResearchSettings | None = None) -> int:
    """按篇幅档位取目标知识点数。

    未知档位 → 用 `standard` 的值并**留下可查的痕迹**（不静默取 0：
    取 0 会让研究直接"达标"，正文无料可讲）。
    """
    s = settings or get_settings().research
    if scale in s.points_per_scale:
        return int(s.points_per_scale[scale])
    fallback = int(s.points_per_scale.get("standard", 25))
    logger.warning("未知篇幅档位 %r → 目标知识点回落为 standard=%d", scale, fallback)
    return fallback


def research_topic(
    topic: str,
    *,
    scale: str = "standard",
    provider,
    cache: ResearchCache | None = None,
    settings: ResearchSettings | None = None,
    resume: bool = True,
) -> ResearchPlan:
    """按知识点目标做**多轮**研究。

    参数：
      · `provider` —— `TavilyResearchProvider` 或 `MockResearchProvider`
      · `cache`    —— 传 None 则不缓存（测试用）；默认用 `ResearchCache()`
      · `resume`   —— 命中缓存时是否直接复用（断点续跑）

    失败语义：单轮失败**不中断整体**（记在 `ResearchRound.error` 里继续
    下一轮）—— 一轮网络抖动不该让整个研究失败。全部轮次都失败时
    `reached_target=False` 且 `stopped_reason` 说明原因。
    """
    s = settings or get_settings().research
    cache = cache if cache is not None else ResearchCache()
    target = target_points_for(scale, s)

    plan = ResearchPlan(topic=topic, scale=scale, target_points=target)

    # ── 断点续跑：已有未过期缓存 → 直接复用 ──
    if resume:
        hit = cache.get(topic)
        if hit is not None:
            raw = hit.get("result") or {}
            cached = _result_from_cache(topic, raw)
            if cached.snippets:
                plan.snippets = cached.snippets
                plan.rounds.append(
                    ResearchRound(
                        round_no=0,
                        query="（缓存命中，未重新联网）",
                        snippets=cached.snippets,
                        cumulative_points=estimate_points(cached.snippets),
                        from_cache=True,
                    )
                )
                cache.bump_hit(topic)
                if plan.estimated_points >= target:
                    plan.reached_target = True
                    plan.stopped_reason = "缓存命中且已达标"
                    return plan
                logger.info(
                    "缓存命中但未达标（≈%d < %d），继续补搜",
                    plan.estimated_points, target,
                )

    # ── 多轮检索 ──
    accumulated: list[ResearchSnippet] = list(plan.snippets)
    seen_urls: set[str] = {sn.url for sn in accumulated if sn.url}
    errors: list[str] = []

    for i, template in enumerate(ANGLES[: s.max_rounds], start=1):
        query = template.format(topic=topic)
        rnd = ResearchRound(round_no=i, query=query)
        try:
            got = provider.search(query, s.max_results_per_query)
        except ResearchError as exc:
            rnd.error = str(exc)[:120]
            errors.append(rnd.error)
            rnd.cumulative_points = estimate_points(accumulated)
            plan.rounds.append(rnd)
            continue

        # 去重：同一 URL 不重复计入（同一篇文章在多轮里反复出现是常态）
        fresh = [sn for sn in got if not sn.url or sn.url not in seen_urls]
        seen_urls.update(sn.url for sn in fresh if sn.url)
        rnd.snippets = tuple(fresh)
        accumulated.extend(fresh)
        rnd.cumulative_points = estimate_points(accumulated)
        plan.rounds.append(rnd)

        if rnd.cumulative_points >= target:
            plan.stopped_reason = f"第 {i} 轮达标"
            break
    else:
        plan.stopped_reason = (
            f"角度用尽（{min(s.max_rounds, len(ANGLES))} 轮）仍未达标"
        )

    plan.snippets = tuple(accumulated)
    plan.reached_target = plan.estimated_points >= target
    if not plan.reached_target and not plan.stopped_reason:
        plan.stopped_reason = "轮次用尽"
    if errors and not plan.reached_target:
        plan.stopped_reason += f"（其中 {len(errors)} 轮检索失败）"

    # ── 写缓存（无论是否达标都写：不达标的结果下次可从断点续）──
    if accumulated:
        cache.put(
            topic,
            {
                "snippets": [
                    {"title": sn.title, "url": sn.url, "text": sn.text}
                    for sn in accumulated
                ],
                "scale": scale,
                "reached_target": plan.reached_target,
            },
            stage="snippets",
        )
    return plan


def _result_from_cache(topic: str, raw: dict) -> ResearchResult:
    """把缓存里的原生 dict 还原成领域对象（坏数据 → 空结果，不炸）。"""
    out: list[ResearchSnippet] = []
    for r in raw.get("snippets") or []:
        if not isinstance(r, dict):
            continue
        out.append(
            ResearchSnippet(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                text=str(r.get("text", "")),
            )
        )
    return ResearchResult(topic=topic, snippets=tuple(out), stage="cache")


__all__ = [
    "ANGLES",
    "SNIPPETS_PER_POINT",
    "MIN_SNIPPET_CHARS",
    "ResearchRound",
    "ResearchPlan",
    "estimate_points",
    "target_points_for",
    "research_topic",
]
