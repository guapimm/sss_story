"""LLM 结构化输出的 Pydantic schema —— **单一来源**。

## 为什么 schema 必须是单一来源

旧项目最严重的枚举冲突：

    memory.py 阈值表          5 档（轻微/轻/中/重/极重）
    post_processor.py prompt  3 档（轻/重/极重）
    → 「轻微」「中」永远不被产生 → 阈值查不到 → 该损伤**永不催促**
    → 实测书 24：最重的「神魂受创（伤势·极重）」恰恰是唯一不催的那条

本模块的原则：
  · **枚举值从 `domain/enums.py` 取**，prompt 文本由枚举**生成**
  · schema 里的 `Field(description=...)` 就是给模型的指令
  · 三者（枚举 / schema / prompt）同源，不可能漂移

## 为什么每个 schema 都要有 description

旧项目 `post_processor.py` 的单条 system prompt **超过 10,000 字符**，
一次要求抽取 11 类信息（cards / summary / state_updates / deaths /
relationship_updates / events / plot_progress / claims / schema_delta /
hang / entities）→ `entity_classify` 空输出 **100%**。

本模块把职责拆开（对策⑥），每个节点只抽一类，schema 自带约束说明。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from storyforge.domain.enums import (
    BeatType,
    EntityKind,
    HealthAction,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    LifecycleState,
    RelationKind,
    RelationSentiment,
)


def _enum_values(enum_cls) -> str:
    """把枚举渲染成 prompt 可用的取值列表（**单一来源的落地方式**）。"""
    return " | ".join(e.value for e in enum_cls)


# --------------------------------------------------------------------------- #
# 圣经
# --------------------------------------------------------------------------- #


class BibleOut(BaseModel):
    """世界观圣经（对策：L0 稳定前缀，全书不变）。"""

    title: str = Field(description="书名")
    worldview: str = Field(description="世界观设定：世界结构、力量体系、时代背景")
    rules: list[str] = Field(description="世界硬规则（不可违反的设定）", default_factory=list)
    power_system: str = Field(default="", description="力量/能力体系说明")
    era: str = Field(default="", description="时代与地域背景")
    tone: str = Field(default="", description="整体基调与文风")
    terms: list[str] = Field(default_factory=list, description="术语与专名对照")


# --------------------------------------------------------------------------- #
# 卷级升级台阶（对策②核心）
# --------------------------------------------------------------------------- #


class MilestoneOut(BaseModel):
    """卷内里程碑。

    ⚠️ `chapter_no` 是**本卷内**的章序号（从 1 开始），不是全书号。

    为什么：模型不知道各卷的全局偏移（那是代码按各卷 chapter_count 累加出来的），
    让它自己累加必然出错 —— 一旦第一卷的章数估偏，后续所有里程碑整体偏移。
    这与决策 D-15「派生不存储」同一原则：**模型给局部，代码算全局**。
    """

    chapter_no: int = Field(
        description="里程碑在**本卷内**的章序号（从 1 开始，不超过本卷 chapter_count）。"
        "给卷内序号即可，全书号由系统自动换算"
    )
    key: str = Field(description="里程碑标识")
    description: str = Field(default="", description="里程碑内容一句话")


class VolumePlanOut(BaseModel):
    """一卷的**升级台阶**。

    ## 为什么这些字段必填

    旧项目把卷信息塞在 `books.outline` 的 JSON 列里，后续卷只有占位目标
    （`blueprint="missing"`），导致 **400 章全挤第 1 卷** ——
    而第 1 卷的 vol_goal 是「在一栋楼里读透三十七条」。

    实测地理熵：书 24/23/18 的**后半本新增地点 0/1/5**；
    书 23 的 400 章场景 token 主体是「门/电梯/楼层/走廊」。

    根因还包括**多层代码显式禁止换图**（8 处）：
      `architect.py:397/401/372`、`writer.py:258`、`writer_rules.py:374`、
      `editor.py:243/106` → 全书只 2~5 次换图许可。

    本 schema 把扩张做成**必填字段**，模型无法通过省略来逃避。
    """

    volume_no: int = Field(description="卷号，从 1 开始")
    title: str = Field(description="卷标题")
    goal: str = Field(description="本卷目标（人类可读）")
    # ── 升级台阶（必填）──
    new_locations: list[str] = Field(
        description="本卷新开地图（≥2 个，且不得与既往卷重复）"
    )
    new_factions: list[str] = Field(
        description="本卷新登场势力（≥2 个，且不得与既往卷重复）"
    )
    antagonist: str = Field(description="本卷主要对手（必须与既往卷不同）")
    power_ceiling: str = Field(description="本卷力量/能力上限")
    core_conflict: str = Field(description="本卷核心矛盾")
    escalation_from_prev: str = Field(
        min_length=8,
        description="相对上卷「升级在哪」——必须写具体内容，"
        "禁止「推向X的下一个阶段」这类模板句"
        "（模板句由 structure_gate 进一步拦截，此处只挡空值与过短）",
    )
    # ── 收尾 ──
    old_map_closure: str = Field(
        min_length=2,
        description="旧地图如何收尾（有交代地离开，而非弃置）",
    )
    volume_end_hook: str = Field(default="", description="卷末钩子")
    # ── 规模 ──
    chapter_count: int = Field(description="本卷章数（建议 150~250）")
    milestones: list[MilestoneOut] = Field(
        default_factory=list,
        description="卷内里程碑（间隔不超过 25 章）",
    )

    @field_validator("new_locations", "new_factions")
    @classmethod
    def _not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("必须至少给出 1 项（长篇需要横向扩张，见卷级台阶要求）")
        return v


class SeriesPlanOut(BaseModel):
    """全书规划 —— **必须一次给出全部卷的升级台阶**。

    ★ 这是与旧项目最本质的差异。旧项目 `scales.json` 的 ultra 档明文规定
    **「本次只细化第 1 卷」**，于是模型把全部章数分配给第 1 卷。
    """

    logline: str = Field(description="一句话故事梗概")
    theme: str = Field(default="", description="主题")
    tone: str = Field(default="", description="基调")
    total_volumes: int = Field(description="总卷数")
    target_chapters: int = Field(description="全书目标章数")
    volumes: list[VolumePlanOut] = Field(description="**全部卷**的升级台阶")

    @field_validator("volumes")
    @classmethod
    def _volumes_match(cls, v: list[VolumePlanOut], info) -> list[VolumePlanOut]:
        tv = info.data.get("total_volumes")
        if tv is not None and len(v) != tv:
            raise ValueError(
                f"声明 {tv} 卷但只规划了 {len(v)} 卷 —— "
                f"旧项目正是这样产生 blueprint=missing 的占位卷"
            )
        return v


# --------------------------------------------------------------------------- #
# 骨架（本卷弧线/段落/角色/势力）
# --------------------------------------------------------------------------- #


class ArcOut(BaseModel):
    key: str = Field(description="弧线标识（简短、唯一）")
    kind: str = Field(description="core（主线）| relationship（关系线）| opposition（对立线）| sub（支线）")
    description: str = Field(description="这条线讲什么")
    chapter_span: list[int] = Field(
        default_factory=list, description="[起始章, 结束章]（全书章号）"
    )


class SkeletonOut(BaseModel):
    """本卷骨架 —— 弧线 / 段落 / 角色 / 势力。"""

    arcs: list[ArcOut] = Field(description="故事线（≥6 条，其中核心线 ≥2、关系线 ≥2、对立线 ≥2）")
    segments: list[str] = Field(
        default_factory=list,
        description="本卷分段目标（每段一句，**必须两两不同**）",
    )
    characters: list[str] = Field(description="本卷主要角色名（≥3）")
    factions: list[str] = Field(description="本卷登场势力名（≥4）")


# --------------------------------------------------------------------------- #
# 章节细纲
# --------------------------------------------------------------------------- #


class SceneBeatOut(BaseModel):
    """一个场景拍点。

    继承旧项目**已验证有效**的格式（`flowmode/base.py`）：
    「场景/人物：目标→阻碍→结果」的分场景拍点表，双向硬约束
    （防遗漏 + 防加戏）。新增 `why_now` 要求动机由上一拍逼出。
    """

    goal: str = Field(description="谁想做什么（必须具体到动作与对象）")
    obstacle: str = Field(description="阻碍是什么")
    outcome: str = Field(description="结果是什么（必须可检验）")
    why_now: str = Field(
        description="为什么是现在 —— 必须由上一行的结果或上一章出口状态逼出，"
        "禁止无动机的地点跳转"
    )
    location: str = Field(default="", description="场景地点名（若有）")
    participants: list[str] = Field(default_factory=list, description="在场人物名")


class WorldStateOut(BaseModel):
    """章首/章末世界状态 —— **结构化**（旧项目是自由文本，填充率仅 13%）。"""

    location: str = Field(default="", description="地点名")
    story_time: str = Field(default="", description="故事内时间（如「第三日夜」）")
    present: list[str] = Field(default_factory=list, description="在场人物名")
    known: list[str] = Field(
        default_factory=list,
        description="本章结束时，**谁知道什么**（用于防止角色知道不该知道的事）",
    )
    open_thread: str = Field(
        default="",
        description="章末未收束的事件本身（谁在做什么、什么没解决）。"
        "禁止写「莫名的波动」这类无主暗示",
    )


class ChapterPlanOut(BaseModel):
    """一章细纲。"""

    title: str = Field(description="章标题（具体、有信息量，禁止与其他章重复）")
    plot: str = Field(description="一句话剧情梗概")
    beat_type: str = Field(
        default=BeatType.NORMAL.value,
        description=f"节拍类型：{_enum_values(BeatType)}",
    )
    is_transition: bool = Field(
        default=False,
        description="本章是否换场章（换地图/换时间）。"
        "**规划内的换场是正常的**，不是缺陷（旧项目把换图一律判为加戏）",
    )
    scenes: list[SceneBeatOut] = Field(description="分场景拍点（4~8 个）")
    entry_state: WorldStateOut = Field(description="本章开场状态")
    exit_state: WorldStateOut = Field(description="本章结尾状态（下一章据此开场）")
    end_hook: str = Field(description="章末钩子：本章现场内**悬而未决的事件本身**")

    @field_validator("scenes")
    @classmethod
    def _min_scenes(cls, v: list[SceneBeatOut]) -> list[SceneBeatOut]:
        if len(v) < 4:
            raise ValueError(f"场景数 {len(v)} < 4（细纲要求 4~8 个分场景拍点）")
        return v


class DetailBatchOut(BaseModel):
    """细纲批次输出。"""

    chapters: list[ChapterPlanOut] = Field(description="本批各章细纲")


# --------------------------------------------------------------------------- #
# 抽取（拆分后：每节点只抽一类）
# --------------------------------------------------------------------------- #


class EntityMentionOut(BaseModel):
    """正文中提到的实体（供 `entity_resolver` 归一）。"""

    name: str = Field(description="正文中出现的名字")
    kind: str = Field(
        description=f"实体类型：{_enum_values(EntityKind)}"
        "（**势力**与**组织**都是合法值）"
    )
    aliases: list[str] = Field(default_factory=list, description="同章出现的别名")
    intro_method: str = Field(
        default="unknown",
        description="引入方式：self_intro | mentioned_by_other | narrator_intro | "
        "appeared_before | baseless_recall | baseless_knowledge | "
        "appeared_out_of_nowhere | unknown",
    )
    context: str = Field(default="", description="首次出现的上下文句子（≤100 字）")


class StateUpdateOut(BaseModel):
    """单个实体的状态变化。"""

    entity: str = Field(description="实体名")
    field: str = Field(description="字段名（位置/境界/目标/情绪/装备/性格/立场 等）")
    value: str = Field(description="新值（字符串或 JSON）")
    evidence: str = Field(
        default="",
        description="正文中的依据（本章哪个事件导致了该变化）",
    )


class InjuryUpdateOut(BaseModel):
    """损伤变化。

    ★ 只报**有变化**的条目 —— 未变化绝不重复输出
    （旧项目规则「同值不覆盖、不推进计时，防慢性损伤无限续期」）。
    """

    entity: str = Field(description="受伤者名字")
    name: str = Field(description="损伤名（如「左肩剑创」）")
    kind: str = Field(description=f"类别：{_enum_values(InjuryKind)}")
    severity: str = Field(description=f"严重度：{_enum_values(InjurySeverity)}")
    status: str = Field(description=f"状态：{_enum_values(InjuryStatus)}")
    recov: str = Field(default="", description="恢复方向（新发/恶化时给）")
    evidence: str = Field(default="", description="正文依据")


class EventOut(BaseModel):
    """关键事件。

    ★ 动作（查看/记录/离开/拾取/值守）属于**事件**，不属于关系 ——
    旧项目把动作写进关系表，导致 966 条关系里 86.7% 是非人际关系。
    """

    story_time: str = Field(default="", description="故事内时间")
    location: str = Field(default="", description="地点名")
    participants: list[str] = Field(default_factory=list, description="参与者名字")
    description: str = Field(description="事件描述")
    consequence: str = Field(default="", description="后果/影响")


class RelationUpdateOut(BaseModel):
    """**社会关系**变化（只抽关系，不抽动作）。"""

    from_entity: str = Field(description="关系主体名")
    to_entity: str = Field(description="关系客体名")
    kind: str = Field(description=f"关系类型：{_enum_values(RelationKind)}")
    intensity: int = Field(default=50, ge=0, le=100, description="强度 0~100")
    sentiment: str = Field(
        default=RelationSentiment.NEUTRAL.value,
        description=f"情感倾向：{_enum_values(RelationSentiment)}",
    )
    surface_facing: str = Field(default="", description="世人所知的关系表现")
    true_facing: str = Field(default="", description="真实立场（与表面不同即有暗线张力）")
    is_mutual: bool = Field(default=True, description="是否双向关系")
    evidence: str = Field(default="", description="正文依据")


class SceneExtractionOut(BaseModel):
    """`scene_extractor` 的输出：事件 + 状态 + 损伤。"""

    summary: str = Field(description="本章梗概（80 字内）")
    events: list[EventOut] = Field(default_factory=list, description="2~5 条关键事件")
    state_updates: list[StateUpdateOut] = Field(default_factory=list)
    injury_updates: list[InjuryUpdateOut] = Field(default_factory=list)
    deaths: list[str] = Field(default_factory=list, description="本章死亡的角色名")
    entities: list[EntityMentionOut] = Field(
        default_factory=list, description="本章出现的实体（供实体归一）"
    )
    unsupported: list[str] = Field(
        default_factory=list,
        description="无引入依据的名词（凭空出现/无依据回忆/无依据知晓）",
    )


class EntityResolutionOut(BaseModel):
    """`entity_resolver` 的输出：提及 → 既有实体 或 新建。"""

    mentions: list[str] = Field(description="本章出现的实体名")
    links: dict[str, int] = Field(
        default_factory=dict,
        description="提及名 → 既有实体 ID（能对上时给出）",
    )
    new_canonical: dict[str, str] = Field(
        default_factory=dict,
        description="提及名 → 规范名（新建实体或归一到既有正名时给出）",
    )


class RelationExtractionOut(BaseModel):
    """`relation_extractor` 的输出：只抽社会关系。"""

    relations: list[RelationUpdateOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 审校
# --------------------------------------------------------------------------- #


class ReviewIssueOut(BaseModel):
    severity: str = Field(description="critical | major | minor")
    category: str = Field(
        description="问题类别：ai_style | consistency | pacing | logic | "
        "foreshadow | ooc | outline_deviation | other"
    )
    location: str = Field(default="", description="问题位置（章内定位，如「末段」）")
    description: str = Field(description="问题说明（具体、可执行）")
    suggestion: str = Field(default="", description="修改建议")


class ReviewResultOut(BaseModel):
    """LLM 审校结果。

    ## ★ 关键约束：`issues` 非空则 `revised_content` 必须非空

    旧项目实测：Editor 反复要求修改但**从不产出可用的 `revised_content`**
    （`no_revised_content`、`too_short:3839<4600`、`no_improve:6->6`）
    → 重写环节空转 → 原稿入库。

    且旧项目模型曾在 `notes` 里声称"已改写"而 `revised_content=null`
    （`AGENTS.md` 记录）。因此：
      · 本 schema 用 **model_validator** 强制两者一致
        （⚠️ 不能用 field_validator —— Pydantic 按声明顺序校验字段，
          `revised_content` 声明在 `issues` 之前，那时 `info.data` 里还没有 issues，
          校验会静默失效。这个坑实测踩过一次。）
      · `actions_taken` **由代码**依据 `revised_content` 生成，不采信模型自述
    """

    issues: list[ReviewIssueOut] = Field(default_factory=list, description="发现的问题")
    revised_content: str | None = Field(
        default=None,
        description="**修正后的完整正文**（若 issues 非空则必须给出全文，"
        "不得只给片段或说明）",
    )
    score: float = Field(ge=0, le=10, description="质量评分（0~10）")
    notes: str = Field(default="", description="补充说明（散文，**不作为判据**）")

    @model_validator(mode="after")
    def _revised_required_when_issues(self) -> ReviewResultOut:
        if self.issues and not (self.revised_content or "").strip():
            raise ValueError(
                "issues 非空时必须给出 revised_content（完整修正稿）。"
                "旧项目实测：Editor 反复指出问题却不给修订稿 → 重写空转 → 原稿入库"
            )
        return self

    @property
    def has_usable_revision(self) -> bool:
        """是否有可用的修订稿（供代码侧判定，不采信模型自述）。"""
        return bool((self.revised_content or "").strip())


# --------------------------------------------------------------------------- #
# 伤势节拍（由调度器产出，不调 LLM；此处仅为契约完整性）
# --------------------------------------------------------------------------- #


class HealthBeatOut(BaseModel):
    """伤势节拍（`HealthScheduler` 产出）。"""

    injury_name: str = Field(description="损伤名")
    entity: str = Field(description="受伤者")
    action: str = Field(description=f"动作：{_enum_values(HealthAction)}")
    chapter_no: int = Field(description="全书章号")
    scene_seq: int = Field(default=0, description="指向本章第几个场景（0-based）")
    note: str = Field(default="")


__all__ = [
    "BibleOut",
    "MilestoneOut",
    "VolumePlanOut",
    "SeriesPlanOut",
    "ArcOut",
    "SkeletonOut",
    "SceneBeatOut",
    "WorldStateOut",
    "ChapterPlanOut",
    "DetailBatchOut",
    "EntityMentionOut",
    "StateUpdateOut",
    "InjuryUpdateOut",
    "EventOut",
    "RelationUpdateOut",
    "SceneExtractionOut",
    "EntityResolutionOut",
    "RelationExtractionOut",
    "ReviewIssueOut",
    "ReviewResultOut",
    "HealthBeatOut",
    "LifecycleState",
]
