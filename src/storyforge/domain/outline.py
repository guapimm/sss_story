"""大纲领域模型（决策 D-07、对策②，痛点 5）。

## 旧项目的实测缺陷

**书 23（400 章 / ultra）**：`chapters` 全部落在**第 1 卷**（`{1: 400}`），
卷 2~5 的 `blueprint="missing"`、`main_line=""`。

**成因链**（已定位到代码与配置）：
  1. `scales.json` 的 `story.ultra.rule` 明文规定 **「本次只细化第 1 卷」**，
     且 `per_vol: 300~500`
  2. `architect._normalize_blueprint_volumes()` 为缺失的后续卷**补占位卷并标 missing**
  3. 模型把 `target_chapters` 全部分配给第 1 卷（因为只有它需要细化）
  4. 第 1 卷的 `vol_goal` 是「在长明楼读透三十七条」→ **400 章全在一栋楼里**

**地理熵实测**（从章 `plot`+`title` 抽取地点 token，比较前半本 vs 后半本）：

    book 24（447 章）：后半本新增地点 **0**
    book 23（400 章）：                   **1**（且非地名）
    book 18（350 章）：                   **5**（全是已登场势力的变体）

书 23 的高频"地点"token 是 `周德海(11) 裴照隔门(9) 电梯在四楼(5) 长明楼(5) 陈砚下楼(5)
他上楼(4) 电梯门(4) ...` —— **400 章的场景主体是「门/电梯/楼层/走廊」**，
唯一地标「长明楼」只出现 **5 次**。

**更根本的是多层代码显式禁止换图**（8 处）：
  · `architect.py:397`「禁止每章自起自收（新场景开场+圆满收束）」
  · `architect.py:401`「地点延续或自然过渡，**禁止无交代的场景硬切**」
  · `architect.py:372`「ultra 档同卷相邻章必须场面连续；**卷末可为下一卷换场**」
  · `writer.py:258`「**禁止无过渡换地图**」
  · `writer_rules.py:374`「**禁止新增细纲之外的…新地图**」
  · `editor.py:243`「无过渡换地图仍须**改写**」
  · `editor.py:106`「新地图（剧情级加戏）→ 记**细纲偏离**」

算术结果：ultra = 3~6 卷 → **全书只 2~5 次换图许可**；
书 23 是 400 章 / 5 卷 = **每 80 章才有一次**。

## 本模块的对策

1. **卷级升级台阶是必填字段**（`new_locations` / `new_factions` / `antagonist` /
   `escalation_from_prev`）—— 模型无法通过"省略"来逃避扩张设计。
2. **`is_transition` 显式建模换场章** —— 解除旧项目"换图一律判缺陷"的禁令，
   改为"无过渡的换图才是缺陷"。
3. **`BeatType` 含 `HEAL` / `RECOVERY_PREP`** —— 恢复成为合法节拍类型。
4. **`WorldStateSnapshot` 结构化** `entry_state`/`exit_state`
   —— 旧项目是自由文本，实测填充率**仅 13%** 且无法机械校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from storyforge.domain.enums import BeatType

# --------------------------------------------------------------------------- #
# 世界状态快照（结构化 entry/exit state）
# --------------------------------------------------------------------------- #


@dataclass
class WorldStateSnapshot:
    """章首/章末的世界状态 —— **结构化**，不是自由文本。

    ## 为什么必须结构化

    旧项目的 `entry_state`/`exit_state` 是自由文本字符串
    （`architect.py:108-113`），四要素是「地点·时间·在场人物·谁知道什么」。
    实测：

      · `entry_state` 填充率 **仅 13%**（段1 100% → 段2 12% → 段3起**归零**）
      · 即使填了，也只存在于一段散文里，**不参与任何机械校验与状态计算**

    结构化后可以：
      · 机械校验 `exit[N] == entry[N+1]`（旧项目只能做字符串比对）
      · 检测"伤势角色是否被合理处理"（旧项目无法检测）
      · 计算关系/装备/位置的连续性

    ★ 章号一律全书递增号（决策 D-15）。
    """

    #: 地点实体 ID（不是字符串 —— 旧项目地点是 40+ 个碎片变体）
    location_entity_id: int | None = None
    #: 故事内时间（文本，如"第三日夜"）
    story_time: str = ""
    #: 在场实体 ID 列表
    present_entity_ids: list[int] = field(default_factory=list)
    #: 谁知道什么（fog-of-war）—— 旧项目**完全没有此能力**
    #: 形如 {entity_id: ["知道了X", "不知道Y"]}
    known_facts: dict[int, list[str]] = field(default_factory=dict)
    #: 未收束事件（章末钩子的结构化形式）
    open_thread: str = ""

    def is_empty(self) -> bool:
        return (
            self.location_entity_id is None
            and not self.story_time
            and not self.present_entity_ids
            and not self.known_facts
            and not self.open_thread
        )

    def continuity_problems(self, nxt: WorldStateSnapshot) -> list[str]:
        """本章 exit 与下一章 entry 的连续性检查。

        旧项目 `outline_lint.py:121-125` 只能做 `exit_state != entry_state`
        的**字符串比对**，无法给出"哪里不一致"。
        """
        problems: list[str] = []
        if (
            self.location_entity_id is not None
            and nxt.location_entity_id is not None
            and self.location_entity_id != nxt.location_entity_id
        ):
            problems.append(
                f"地点跳变：本章结束于实体 {self.location_entity_id}，"
                f"下一章开始于实体 {nxt.location_entity_id}"
            )
        if self.open_thread and not nxt.story_time:
            problems.append("本章留下未收束事件，但下一章没有交代时间")
        missing = set(self.present_entity_ids) - set(nxt.present_entity_ids)
        if missing and nxt.present_entity_ids:
            # 在场人物减少是允许的（人物离场），但必须有交代 —— 由细纲校验
            pass
        return problems


# --------------------------------------------------------------------------- #
# 场景拍点
# --------------------------------------------------------------------------- #


@dataclass
class SceneBeat:
    """细纲的一个场景拍点。

    继承旧项目**已验证有效**的设计（`flowmode/base.py:148-199`）：
    细纲是「场景/人物：目标→阻碍→结果」的分场景拍点表，双向硬约束
    （防遗漏 + 防加戏）。新增 `why_now` 要求动机由上一拍结果逼出。
    """

    #: 本章第几个场景（0-based）—— 伤势节拍的 `scene_seq` 指向它
    seq: int
    location_entity_id: int | None = None
    participant_entity_ids: list[int] = field(default_factory=list)
    #: 谁想做什么
    goal: str = ""
    #: 阻碍
    obstacle: str = ""
    #: 结果
    outcome: str = ""
    #: 为什么是现在 —— 必须由上一行的结果或上一章的出口状态逼出
    why_now: str = ""

    def is_complete(self) -> bool:
        return bool(self.goal and self.obstacle and self.outcome and self.why_now)


# --------------------------------------------------------------------------- #
# 章节计划
# --------------------------------------------------------------------------- #


@dataclass
class ChapterPlan:
    """一章的写作计划（细纲）。"""

    #: ★ 全书递增章号（决策 D-15）—— 卷内序号派生不存储
    global_no: int
    volume_no: int
    segment_no: int = 0
    title: str = ""
    #: 一句话梗概
    plot: str = ""
    beat_type: BeatType = BeatType.NORMAL
    #: ★ 是否换场章。
    #: 旧项目把「新地图」一律记为「细纲偏离」（`editor.py:106`）并判为缺陷
    #: （`writer_rules.py:374` 禁止写细纲外的新地图）。
    #: 本设计显式建模：**规划内的换场正常，规划外的才是加戏**。
    is_transition: bool = False
    #: 分场景拍点（≥4）
    scenes: list[SceneBeat] = field(default_factory=list)
    #: 章首/章末世界状态（结构化）
    entry_state: WorldStateSnapshot = field(default_factory=WorldStateSnapshot)
    exit_state: WorldStateSnapshot = field(default_factory=WorldStateSnapshot)
    #: 本章要埋下的伏笔 key
    plant_foreshadowings: list[str] = field(default_factory=list)
    #: 本章要回收的伏笔 key
    resolve_foreshadowings: list[str] = field(default_factory=list)
    #: 章末钩子
    end_hook: str = ""
    #: ★ 伤势节拍（由 `HealthScheduler` 注入，见 domain/health.py）
    health_beats: list[Any] = field(default_factory=list)

    # -- 校验 ---------------------------------------------------------------

    def lint(self, *, min_scenes: int = 4, min_plot_chars: int = 1) -> list[str]:
        """确定性 lint（零 LLM）—— 继承旧项目 `outline_lint.py` 的"缺省即不通过"。

        旧项目实测的落地率问题：
          · `plot_detail` p50 **788**（下限 900，**未达标**）
          · `entry_state` 填充率 **13%**
          · `milestone_ids` **0%**
        """
        issues: list[str] = []
        if not self.title.strip():
            issues.append(f"第{self.global_no}章缺标题")
        if not self.plot.strip():
            issues.append(f"第{self.global_no}章缺 plot")
        elif len(self.plot) < min_plot_chars:
            issues.append(f"第{self.global_no}章 plot 过短")
        if len(self.scenes) < min_scenes:
            issues.append(
                f"第{self.global_no}章场景数 {len(self.scenes)} < {min_scenes}"
            )
        for s in self.scenes:
            if not s.is_complete():
                issues.append(
                    f"第{self.global_no}章场景{s.seq}不完整"
                    f"（需 goal/obstacle/outcome/why_now 四项齐全）"
                )
        if self.entry_state.is_empty():
            issues.append(f"第{self.global_no}章缺 entry_state")
        if self.exit_state.is_empty():
            issues.append(f"第{self.global_no}章缺 exit_state")
        if not self.end_hook.strip():
            issues.append(f"第{self.global_no}章缺 end_hook")
        # 伤势节拍必须指向真实场景
        for b in self.health_beats:
            seq = getattr(b, "scene_seq", None)
            if seq is not None and not (0 <= seq < len(self.scenes)):
                issues.append(
                    f"第{self.global_no}章伤势节拍指向场景 {seq}，"
                    f"但本章只有 {len(self.scenes)} 个场景"
                )
        return issues


# --------------------------------------------------------------------------- #
# 卷级升级台阶（对策②核心）
# --------------------------------------------------------------------------- #


@dataclass
class Milestone:
    """卷内里程碑。

    旧项目书 23 的 400 章只有 **4 个里程碑**，且 3 → 205 之间有
    **202 章的里程碑真空**（`ultra_structure_config` 要求 `min_milestones: 9`，
    但该配置 `enabled: false, shadow: true`）。
    """

    chapter_no: int
    key: str
    description: str = ""


#: `escalation_from_prev` 的模板句黑名单。
#: 旧项目书 24 实测出现「推向「首次记忆剥夺」的下一个阶段」这类**模板化填充**
#: （卷 2 的 `vol_end_hook`），无实质内容。
_TEMPLATE_PATTERNS: tuple[str, ...] = (
    "推向「",
    "的下一个阶段",
    "持续推进",
    "开启新篇章",
    "故事继续",
    "进一步发展",
)


@dataclass
class VolumePlan:
    """一卷的**升级台阶**。

    这是本项目与旧项目最本质的结构差异：旧项目把卷信息塞在
    `books.outline` 的 JSON 列里，后续卷只有占位目标（`blueprint="missing"`）；
    本设计把升级台阶做成**必填字段**，模型无法省略。
    """

    volume_no: int
    title: str
    # ── 升级台阶（必填语义，由 structure_gate 校验实质）──
    #: 本卷新开地图（≥2，且不与既往重复）
    new_locations: list[str] = field(default_factory=list)
    #: 本卷新登场势力（≥2）
    new_factions: list[str] = field(default_factory=list)
    #: 本卷主要对手（必须与既往卷不同）
    antagonist: str = ""
    #: 本卷力量/能力上限
    power_ceiling: str = ""
    #: 本卷核心矛盾
    core_conflict: str = ""
    #: 相对上卷「升级在哪」—— 必须具体，禁止模板句
    escalation_from_prev: str = ""
    # ── 收尾 ──
    #: 旧地图如何收尾（有交代地离开，而非弃置）
    old_map_closure: str = ""
    volume_end_hook: str = ""
    # ── 规模 ──
    chapter_start: int = 1
    chapter_end: int = 1
    milestones: list[Milestone] = field(default_factory=list)
    #: 卷目标（人类可读）
    goal: str = ""

    @property
    def chapter_count(self) -> int:
        return max(0, self.chapter_end - self.chapter_start + 1)

    def is_template_escalation(self) -> bool:
        """`escalation_from_prev` 是否是模板句（无实质内容）。"""
        text = self.escalation_from_prev.strip()
        if len(text) < 8:
            return True
        return any(p in text for p in _TEMPLATE_PATTERNS)


@dataclass
class SeriesPlan:
    """全书规划：全部卷的升级台阶。

    ★ 关键：**必须一次定稿全部卷**（对策②「先规划全部卷的升级台阶，再细化当前卷」）。
    旧项目「只细化第 1 卷」导致 400 章挤第 1 卷。
    """

    book_id: int
    total_volumes: int
    target_chapters: int
    volumes: list[VolumePlan] = field(default_factory=list)

    def chapter_sum(self) -> int:
        return sum(v.chapter_count for v in self.volumes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "total_volumes": self.total_volumes,
            "target_chapters": self.target_chapters,
            "volumes": [
                {
                    "volume_no": v.volume_no,
                    "title": v.title,
                    "new_locations": list(v.new_locations),
                    "new_factions": list(v.new_factions),
                    "antagonist": v.antagonist,
                    "power_ceiling": v.power_ceiling,
                    "core_conflict": v.core_conflict,
                    "escalation_from_prev": v.escalation_from_prev,
                    "old_map_closure": v.old_map_closure,
                    "volume_end_hook": v.volume_end_hook,
                    "chapter_start": v.chapter_start,
                    "chapter_end": v.chapter_end,
                    "goal": v.goal,
                    "milestones": [
                        {"chapter_no": m.chapter_no, "key": m.key,
                         "description": m.description}
                        for m in v.milestones
                    ],
                }
                for v in self.volumes
            ],
        }


__all__ = [
    "WorldStateSnapshot",
    "SceneBeat",
    "ChapterPlan",
    "Milestone",
    "VolumePlan",
    "SeriesPlan",
]
