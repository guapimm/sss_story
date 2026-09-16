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

from typing import Any

from pydantic import BaseModel, Field, model_validator

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
    """单个实体的状态变化。

    ## ★ 实测的形状漂移（`storyforge write` 在此处崩过）

    模型给出的真实形状：

        {"person": "李明", "attribute": "情绪", "from": "疲惫", "to": "恐惧但表面镇定"}

    这是**非常自然**的写法 —— "从什么变成什么"本来就是状态变化的核心，
    而且它把信息给全了。但 schema 要 `{entity, field, value}` →
    三次重试全败 → `StructuredOutputError` **中断整个 write**。

    修法与前几处一致：**归一，不拒绝**（`_normalize_shape`）。
      · `person` / `entity` / `name` → `entity`
      · `attribute` / `field` / `property` → `field`
      · `to` / `value` / `new` / `new_value` → `value`
      · 若给了 `from` 而没给 `to`，用 `from → to` 形式保留变化方向
        （只留 `from` 会丢掉"变成了什么"，那是这条记录的全部意义）
    """

    entity: str = Field(description="实体名")
    #: ⚠️ `field` 有默认值 `"状态"` —— 模型把变化写成
    #: `{"李明": "情绪从疲惫变为恐惧"}` 时**没有单独给字段名**，
    #: 而那条记录的信息（谁变成了什么样）是完整的。
    #: 代码**不替模型猜**是"情绪"还是"目标"，用最泛化的 `"状态"`；
    #: 拒绝它则会让整章抽取失败（信息明明拿到了）。
    field: str = Field(
        default="状态",
        description="字段名（位置/境界/目标/情绪/装备/性格/立场 等）",
    )
    value: str = Field(description="新值（字符串或 JSON）")
    evidence: str = Field(
        default="",
        description="正文中的依据（本章哪个事件导致了该变化）",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: Any) -> Any:
        """把模型**天然想给的形状**归一到 `{entity, field, value}`。"""
        if not isinstance(data, dict):
            return data
        d = dict(data)

        def pick(*names: str) -> Any:
            for n in names:
                if d.get(n) not in (None, "", []):
                    return d[n]
            return None

        entity = pick("entity", "person", "name", "who", "实体", "人物")
        field = pick("field", "attribute", "property", "aspect", "字段", "属性")
        to_v = pick("value", "to", "new", "new_value", "after", "新值")
        from_v = pick("from", "old", "old_value", "before", "原值")

        if entity is not None and "entity" not in d:
            d["entity"] = str(entity)
        if field is not None and "field" not in d:
            d["field"] = str(field)
        if "value" not in d:
            if to_v is not None and from_v is not None:
                # 保留变化方向：只留 to 会丢掉"原来是什么"，
                # 只留 from 更是丢掉了"变成了什么"（这条记录的全部意义）
                d["value"] = f"{from_v} → {to_v}"
            elif to_v is not None:
                d["value"] = str(to_v)
        return d


def normalize_record_list(v: Any) -> Any:
    """把**字典形状**的"记录集合"摊成记录列表。

    ## 实测（同一节点、四轮四种形状）

        "state_updates": [{"entity": "李明", "attribute": "情绪", …}]  ← list
        "state_updates": {"李明": "情绪从疲惫变为恐惧和镇定"}           ← dict
        "state_updates": {"李明": {"情绪状态": "从疲惫转为极度惊恐"}}    ← dict 套 dict
        "state_updates": {"李明": ["情绪：惊恐", "目标：隐瞒发现"]}      ← dict 值列表

    这四种**语义等价**（都是"每个实体发生了什么变化"），
    后三种更紧凑，模型有充分理由这么写。`list[...]` 直接拒 →
    三次重试全败 → **中断整个 write**。

    ## 摊平规则（键是标签、值是内容）

    外层键是人名/实体名，**内层键才是"哪个字段"**：

        {"李明": {"情绪状态": "从疲惫转为极度惊恐"}}
          → [{"entity": "李明", "field": "情绪状态",
              "value": "从疲惫转为极度惊恐"}]

        {"李明": "情绪从疲惫变为恐惧"}
          → [{"entity": "李明", "value": "情绪从疲惫变为恐惧"}]
            （内层没给字段名 → 不猜，交给 `field` 的默认值）

    ## ⚠️ 关键：`field` 必须由**代码**给出默认值

    模型没说"这是哪个字段"时**不要猜**。但也不能因此拒绝 ——
    所以 `StateUpdateOut.field` 有默认值 `"状态"`（最泛化的说法）。
    """
    if not isinstance(v, dict):
        return v
    if not v:
        return []
    out: list[dict[str, Any]] = []
    for k, val in v.items():
        key = str(k).strip()
        if isinstance(val, dict):
            for inner_k, inner_v in val.items():
                out.append({
                    "entity": key,
                    "field": str(inner_k).strip(),
                    "value": (f"{inner_v}" if not isinstance(inner_v, list)
                              else "；".join(str(x) for x in inner_v)),
                })
        elif isinstance(val, list):
            for x in val:
                out.append({"entity": key, "value": str(x)})
        else:
            out.append({"entity": key, "value": str(val).strip()})
    return out


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

    ## ★ 实测形状漂移

    模型把 `story_time` 写成 **`time`**（更短更自然）。
    这些字段都有默认值，所以**不会报错**，但会**静默丢数据**
    （时间信息落进 `story_time` 之外的地方 = 丢了）。
    → 一并归一，理由与 `StateUpdateOut` 相同。
    """

    story_time: str = Field(default="", description="故事内时间")
    location: str = Field(default="", description="地点名")
    participants: list[str] = Field(default_factory=list, description="参与者名字")
    description: str = Field(description="事件描述")
    consequence: str = Field(default="", description="后果/影响")

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: Any) -> Any:
        """`time` → `story_time`、中文 `后果` → `consequence` 等同义改写。

        ⚠️ 这几个字段都有默认值 —— 不归一**不会报错**，
        只会让数据**静默落空**（时间/后果丢失）。
        静默丢数据比报错更糟：报表看不出来，下游拿到的是空值。
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for canonical, aliases in (
            ("story_time", ("time", "故事时间", "时间")),
            ("description", ("event", "desc", "描述", "事件")),
            ("consequence", ("result", "impact", "后果", "影响")),
            ("location", ("place", "scene", "地点", "场景")),
            ("participants", ("people", "actors", "involved", "参与者", "人物")),
        ):
            if canonical in d and d[canonical] not in (None, "", []):
                continue
            for a in aliases:
                if d.get(a) not in (None, "", []):
                    d[canonical] = d[a]
                    break
        return d


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
    """`scene_extractor` 的输出：事件 + 状态 + 损伤。

    ## ★ 实测：同一节点、不同轮次的形状都不同

    两轮真实输出（同一本书、同一提示词）：

        "state_updates": [{"entity": "李明", "attribute": "情绪", …}]   ← list
        "state_updates": {"李明": "情绪从疲惫变为恐惧和镇定"}            ← dict！

    第二种（人名 → 变化描述）语义完全等价、更紧凑，模型有理由这么写。
    `list[...]` 直接拒它 → 三次重试全败 → **中断整个 write**。

    修法：`_normalize_containers` 把**字典形状**摊成列表
    （键是标签、值是内容 —— 与 `coerce_str_list` 对顶层字典的处理同一原则），
    再交给各元素自己的 `_normalize_shape`。
    """

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

    @model_validator(mode="before")
    @classmethod
    def _normalize_containers(cls, data: Any) -> Any:
        """把字典形状的记录集合摊成列表（实测两轮形状不同）。"""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for f in ("state_updates", "injury_updates", "events", "entities",
                  "deaths", "unsupported"):
            if isinstance(d.get(f), dict):
                d[f] = normalize_record_list(d[f])
            elif isinstance(d.get(f), str):
                # 单个字符串也见过（`unsupported` / `deaths` 只有一条时）
                d[f] = [d[f]] if d[f].strip() else []
        return d


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
