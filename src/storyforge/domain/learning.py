"""学习地图与平替修辞 —— learn 模式的核心数据结构（阶段 2-3）。

## 两个概念的区别（旧项目把它们混在一起，是主要混乱源）

    **知识点**（KnowledgePoint）—— 要教什么（内容）
    **平替修辞**（TermMapping）  —— 怎么把它说成世界内的话（表达）

旧项目 `KnowledgeLedger` 把这两者与"教学进度"塞进同一张表
（`point_name` / `status` / `taught_ch` / `review_count` / `next_review_ch`），
于是「教什么」「怎么说」「教到哪」三个关注点互相纠缠。
本设计拆成三个独立结构：

    KnowledgePoint  内容      ← 本模块
    TermMapping     表达      ← 本模块
    TeachingRecord  进度      ← `modes/protocols.py`

## 「平替修辞」的权威定义

= 旧项目的 `mapping_rules` / 「世界内说法」。旧项目把它写在
`templates/scenes.py` 的 `xianxia` 模板里（**10 条**），形如：

    基础概念 → 灵根/入门心法
    函数     → 自创功法
    报错/异常 → 心魔反噬
    库/模块   → 先辈秘典

并在**四处**注入 prompt（实测）：
  · `worldbuilder.py:130`  —— 「术语映射参考（须全篇统一）」
  · `writer.py:439` → `flowmode/base.py:120 term_mapping_section()`
    —— 「【术语映射规则——正文中的知识点必须按此转换为世界内说法】」
  · `editor.py:142` + `style_metrics.py:179`
    —— 审校层硬拦「出戏代码腔/符号知识点…必须改写成世界内说法」

## 为什么必须**用户逐条确认**（用户决策）

平替修辞决定了整本书的"味道"：`Token` 叫「指令刻度」还是叫「灵纹」
是完全不同的作品。且它一旦进入世界观就**全篇统一、后续难改**，
所以要在写第一章之前让用户逐条过一遍、能改能退。

★ 本模块只定义**数据结构与校验**，不做 LLM 调用（那是 `graph/nodes` 的事）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KnowledgePoint:
    """一个知识点 —— learn 的教学原子。

    ★ 与阶段 1 `modes/protocols.py` 的 `KnowledgePoint` 的关系：
    那个是 **Protocol 层**的轻量定义（`point_id/name/summary/topic/difficulty`），
    本类是 **domain 层**的完整模型（多出 `source_urls` / `evidence` 等
    研究溯源字段）。二者字段兼容，`from_protocol()` 负责转换。

    为什么不在协议层就放全字段：协议层要**稳定**（它是 story/learn 的
    共同契约），而研究溯源字段是 learn 实现细节，会随研究流程演化。
    """

    #: 稳定标识（跨章引用；**不由模型生成** —— 模型给的名字会漂移）
    point_id: str
    #: 知识点名（正文里可以出现，但「出戏原名」受审校约束）
    name: str
    summary: str = ""
    topic: str = ""
    difficulty: int = 1
    #: ★ 溯源：来自哪些检索结果（**没有溯源的"知识点"可能是模型幻觉**）
    source_urls: tuple[str, ...] = ()
    #: 支撑该知识点的原文片段（供人工核对，也是反幻觉的依据）
    evidence: str = ""

    @property
    def has_source(self) -> bool:
        """是否有可核对的来源。

        ★ Mock 研究的片段 URL 为空 → 这里为 False →
        `validate_map` 会把它标出来，避免把占位文本当真知识。
        """
        return bool([u for u in self.source_urls if u.strip()])

    def to_dict(self) -> dict:
        return {
            "point_id": self.point_id,
            "name": self.name,
            "summary": self.summary,
            "topic": self.topic,
            "difficulty": self.difficulty,
            "source_urls": list(self.source_urls),
            "evidence": self.evidence,
        }


@dataclass
class TermMapping:
    """一条平替修辞 —— **原术语 → 世界内说法**。

    逐条独立可编辑/可回退（用户决策：确认粒度=逐条），
    因此每条带 `accepted` 状态与 `revision` 次数。
    """

    #: 原术语（如「Token」「递归」）—— 正文里**禁止直出**
    term: str
    #: 世界内说法（如「指令刻度」「心镜回照」）—— 正文里用这个
    replacement: str
    #: 这条的说明/理由（给用户判断用）
    rationale: str = ""
    #: 用户是否已确认（逐条确认的核心字段）
    accepted: bool = False
    #: 被用户要求重做的次数（供观测"哪些条目难产"）
    revision: int = 0

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "replacement": self.replacement,
            "rationale": self.rationale,
            "accepted": self.accepted,
            "revision": self.revision,
        }


@dataclass
class LearningMap:
    """一张学习地图 —— 研究产出的知识点集合 + 平替修辞表。

    ★ 两级确认（用户决策：L-1 / L-2）：
      · **L-1** 确认知识点集合（`points`）—— 要教什么
      · **L-2** 确认平替修辞表（`mappings`）—— 怎么说

    分开确认的理由：先定"教什么"再定"怎么说"是自然顺序；
    混在一次确认里，用户改了一个知识点会连带要重看整张映射表。
    """

    topic: str
    scale: str = "standard"
    points: list[KnowledgePoint] = field(default_factory=list)
    mappings: list[TermMapping] = field(default_factory=list)
    #: 研究未达标时的缺口说明（如实传递，不掩盖）
    research_shortfall: str = ""

    # -- 查询 ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.points)

    @property
    def accepted_mappings(self) -> list[TermMapping]:
        return [m for m in self.mappings if m.accepted]

    @property
    def accepted_points(self) -> list[KnowledgePoint]:
        return self.points        # 知识点级确认用整体（见 `points_confirmed`）

    def unconfirmed_mappings(self) -> list[TermMapping]:
        return [m for m in self.mappings if not m.accepted]

    def find_mapping(self, term: str) -> TermMapping | None:
        t = (term or "").strip()
        return next((m for m in self.mappings if m.term == t), None)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "scale": self.scale,
            "points": [p.to_dict() for p in self.points],
            "mappings": [m.to_dict() for m in self.mappings],
            "research_shortfall": self.research_shortfall,
        }


# --------------------------------------------------------------------------- #
# 校验（零 LLM，纯函数 —— 便于单测与在确认点前拦下问题）
# --------------------------------------------------------------------------- #


def validate_map(m: LearningMap) -> list[str]:
    """校验学习地图的内部一致性。返回问题列表（空 = 通过）。

    这些检查都对应**实测过的失败形态**：

      1. 空知识点集 —— 会生成"没有教学内容的教小说"
      2. `point_id` 重复 —— 跨章引用会指错（旧项目 `since_ch` 口径漂移的同类）
      3. 术语重复 —— 同一术语两条映射会让"用哪个说法"不确定
      4. 映射两侧为空 —— 空壳条目进了 prompt 等于没约束
      5. 说法与原术语**相同** —— 等于没做平替（正文仍会直出原术语）
      6. 知识点无来源 —— 可能是幻觉（Mock 研究的必然结果，需显式暴露）
      7. 来源**全体相同** —— 是统一赋值而非逐条溯源（见下方长注释）
    """
    issues: list[str] = []

    if not m.points:
        issues.append("知识点集合为空 —— 没有内容可教")

    seen_ids: set[str] = set()
    for p in m.points:
        if not p.point_id.strip():
            issues.append(f"知识点 {p.name!r} 缺 point_id")
        elif p.point_id in seen_ids:
            issues.append(f"point_id 重复：{p.point_id!r}（跨章引用会指错）")
        seen_ids.add(p.point_id)

        if not p.name.strip():
            issues.append(f"知识点 {p.point_id!r} 缺名称")

    no_source = [p.name for p in m.points if not p.has_source]
    if no_source:
        issues.append(
            f"{len(no_source)} 个知识点没有可核对来源（可能是幻觉）："
            f"{no_source[:5]}"
        )

    # ★ 7. 溯源**不得全体相同**（2026-09-23 新增）
    #
    # ## 为什么只查「非空」不够
    #
    # 实测（书 991 / 11 个知识点）：每条都挂了**同一组 3 个 URL** ——
    # 原实现的溯源是"把资料前 3 条全挂给每条知识点"。
    # 于是 `has_source` 全为 True、上面那条检查**全绿**，
    # 而实际上无法回答「**这条**知识点依据的是**哪一段**」。
    #
    # 这是「**有形式、无实质**」的典型：校验器只查"有没有"、不查"对不对"，
    # 就会给出假绿。旧项目 `entry_state` 填充率 13% 而无人发现，是同一类。
    #
    # 判据取"**全部知识点来源集完全一致**"而非"两两不同"：
    #   · 两条知识点恰好依据同一份资料是**正常**的（尤其资料少时）
    #   · 但**全体**完全一致 ⇒ 一定是代码统一赋值，不是逐条溯源
    # 这样既不误报，又能抓住真正的退化。
    sourced = [p for p in m.points if p.has_source]
    if len(sourced) >= 3:
        distinct = {tuple(sorted(p.source_urls)) for p in sourced}
        if len(distinct) == 1:
            issues.append(
                f"{len(sourced)} 个知识点的来源**完全相同** —— "
                f"这不是逐条溯源，是统一赋值（无法核对某条具体依据哪一段）"
            )

    seen_terms: set[str] = set()
    for mp in m.mappings:
        if not mp.term.strip():
            issues.append("存在空的「原术语」条目")
            continue
        if not mp.replacement.strip():
            issues.append(f"术语 {mp.term!r} 的世界内说法为空（等于没做平替）")
        if mp.term in seen_terms:
            issues.append(f"术语重复：{mp.term!r}（同一术语两条映射会歧义）")
        seen_terms.add(mp.term)
        if mp.term.strip() == mp.replacement.strip():
            issues.append(
                f"术语 {mp.term!r} 的「世界内说法」与原术语相同 —— 等于没平替"
            )

    return issues


def parse_mapping_rule(rule: str) -> TermMapping | None:
    """解析旧项目 `mapping_rules` 的单条字符串 → `TermMapping`。

    旧项目格式（`templates/scenes.py`）：

        基础概念 → 灵根/入门心法

    分隔符是 ` → `（U+2192）。解析失败返回 `None`（**不抛错**：
    这是导入旧数据的路径，一条坏数据不该让整批失败；但调用方应记录）。

    ⚠️ 同时对 `->` 与 `→` 都支持：手工编辑的数据两种都出现过。
    """
    text = (rule or "").strip()
    if not text:
        return None
    for sep in ("→", "->", "—>", "=>"):
        if sep in text:
            left, _, right = text.partition(sep)
            left, right = left.strip(), right.strip()
            if left and right:
                return TermMapping(
                    term=left, replacement=right,
                    rationale="（导入自旧项目 mapping_rules）",
                )
    return None


__all__ = [
    "KnowledgePoint",
    "TermMapping",
    "LearningMap",
    "validate_map",
    "parse_mapping_rule",
]
