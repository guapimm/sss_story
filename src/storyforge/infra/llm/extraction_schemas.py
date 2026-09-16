"""抽取类输出的 Pydantic schema —— **每节点只抽一类**。

## 为什么从 `schemas.py` 拆出来

`schemas.py` 撞了契约规则 10 的 **800 行**上限（拆前 811 行）。
拆分依据是**职责**，不是凑行数：本模块全是「正文 → 结构化事实」这一步的
输出形状（抽取阶段），与规划阶段（圣经/台阶/骨架/细纲）本就不是一回事。

## 旧项目的对照（本模块存在的理由）

旧项目 `post_processor.py` 的单条 system prompt **超过 10,000 字符**，
一次要求抽取 11 类信息（cards / summary / state_updates / deaths /
relationship_updates / events / plot_progress / claims / schema_delta /
hang / entities）→ `entity_classify` 空输出 **100%**。

本模块把职责拆开：每个节点只抽一类，schema 自带约束说明。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from storyforge.domain.enums import (
    EntityKind,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
    RelationKind,
    RelationSentiment,
)
from storyforge.infra.llm.enum_text import enum_values


class EntityMentionOut(BaseModel):
    """正文中提到的实体（供 `entity_resolver` 归一）。"""

    name: str = Field(description="正文中出现的名字")
    kind: str = Field(
        description=f"实体类型：{enum_values(EntityKind)}"
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
    kind: str = Field(description=f"类别：{enum_values(InjuryKind)}")
    severity: str = Field(description=f"严重度：{enum_values(InjurySeverity)}")
    status: str = Field(description=f"状态：{enum_values(InjuryStatus)}")
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
    kind: str = Field(description=f"关系类型：{enum_values(RelationKind)}")
    intensity: int = Field(default=50, ge=0, le=100, description="强度 0~100")
    sentiment: str = Field(
        default=RelationSentiment.NEUTRAL.value,
        description=f"情感倾向：{enum_values(RelationSentiment)}",
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


__all__ = [
    "EntityMentionOut",
    "EntityResolutionOut",
    "EventOut",
    "InjuryUpdateOut",
    "RelationExtractionOut",
    "RelationUpdateOut",
    "SceneExtractionOut",
    "StateUpdateOut",
]
