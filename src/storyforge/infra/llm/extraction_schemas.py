"""抽取类输出的 Pydantic schema —— **每节点只抽一类**。

## 为什么从 `schemas.py` 拆出来

`schemas.py` 撞了规则 10 的 **800 行**上限（拆前 811 行）；
**该上限已于 2026-09-26 取消**（见 `doc/07` §2.1.1）。
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

import json
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

    #: ★ 有默认值 `""` —— 存在**合法的"没有实体名"**记录：
    #:
    #:   ① 形状 G（「字段名: 值」对，实测书 19 第 1 章）：
    #:      `{"location": "+楼梯间"}` —— 外层键是**字段名**，不是实体名。
    #:      强行编一个实体名（如 "location"）会造成**静默语义反转**
    #:      （记录挂在不存在的实体上）；留空并标明"未知"才是诚实的。
    #:   ② 形状 F 拆不出实体名的整句：`{"value": "楼道里的气温忽然降了"}`。
    #:
    #: 下游若需要实体名，应显式处理 `entity == ""`（表示"未指明"），
    #: 而不是假设它总有值 —— 与 `field` 的默认值同一原则：
    #: **代码不替模型猜**，但也不因此丢掉拿到的信息。
    entity: str = Field(
        default="",
        description="实体名（未指明时留空字符串，不要编造）",
    )
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

    ## ★★ 形状 G：「字段名: 值」对（阶段 1 收尾实测，**静默语义反转**）

    实测原始输出（书 19 第 1 章）：

        {"location": "+楼梯间", "equipment": "+《住户须知》",
         "emotion": "恐慌→恐惧", "goal": "遵守规则以求生存"}

    这里外层键**不是实体名，而是字段名** —— 模型把"本章主角的状态
    按字段列出"了，这对它来说是自然的写法（prompt 里 `state_updates`
    的说明正是"位置/境界/目标/情绪/装备等"）。

    按"外层键=实体名"处理会产出 `entity='location'` ——
    **不报错、看起来正常、实际全错**（本章主角是"林默"，出现 18 次，
    这些记录没有一条挂在他身上）。这比崩溃危险：崩溃会被发现，这个不会。

    判据见 `_FIELD_KEY_ALIASES`：外层键命中已知字段名 → 按字段处理。
    """
    if not isinstance(v, dict):
        return v
    if not v:
        return []
    out: list[dict[str, Any]] = []
    for k, val in v.items():
        key = str(k).strip()
        if isinstance(val, dict):
            # 内层键是字段名 —— 但要先判外层键是不是字段名（形状 G 的变体）
            if _is_field_key(key):
                for inner_k, inner_v in val.items():
                    out.append({
                        "field": key if not _is_field_key(str(inner_k)) else str(inner_k).strip(),
                        "value": _stringify(inner_v),
                    })
            else:
                for inner_k, inner_v in val.items():
                    out.append({
                        "entity": key,
                        "field": str(inner_k).strip(),
                        "value": _stringify(inner_v),
                    })
        elif isinstance(val, list):
            for x in val:
                out.append(_entry_for(key, x))
        else:
            out.append(_entry_for(key, val))
    return out


#: 已知的**字段名**（`state_updates` 里"哪个属性变了"的常见取值）。
#:
#: ★ 用途：区分「外层键是实体名」与「外层键是字段名」（形状 G）。
#: 来源：`SCENE_EXTRACTOR_SYSTEM` 对 `state_updates` 的说明
#: （"位置/境界/目标/情绪/装备等"）+ 实测观测到的英文写法。
#:
#: ⚠️ 只收**英文**键：中文键（"位置"/"情绪"）与实体名难以区分，
#: 且实测的形状 G 全是英文 —— 加中文会带来误判风险（真有人叫"位置"）。
_FIELD_KEY_ALIASES: frozenset[str] = frozenset({
    # 状态类
    "location", "position", "place", "equipment", "items", "item",
    "emotion", "mood", "feeling", "goal", "objective", "status", "state",
    "realm", "level", "rank", "cultivation", "power", "ability", "skill",
    "condition", "health", "appearance", "identity", "role", "title",
    "inventory", "money", "wealth", "faction", "affiliation", "relationship",
    "knowledge", "memory", "plan", "intention", "attitude", "personality",
})

#: 中文**字段名**（`SCENE_EXTRACTOR_SYSTEM` 原文列举的那些）。
#:
#: ⚠️ 与英文表**分开**：中文只在"内层键"位置才认字段名
#: （外层是中文时无法与实体名区分，故不做形状 G 判定）。
_CN_FIELD_KEYS: frozenset[str] = frozenset({
    "位置", "地点", "境界", "目标", "情绪", "装备", "物品", "状态",
    "能力", "身份", "外貌", "心态", "认知", "记忆", "计划",
})


def _is_field_key(key: str) -> bool:
    """该键是不是"字段名"（而非实体名）。"""
    return key.strip().lower() in _FIELD_KEY_ALIASES


def _stringify(v: Any) -> str:
    """把值转成字符串（列表用「；」连接，与既有口径一致）。"""
    if isinstance(v, list):
        return "；".join(str(x) for x in v)
    if isinstance(v, dict):
        return "；".join(f"{k}：{x}" for k, x in v.items())
    return str(v).strip()


def _entry_for(key: str, val: Any) -> dict[str, Any]:
    """构造**一条**记录 —— 自动判断 `key` 是实体名还是字段名（形状 G）。

        {"location": "+楼梯间"}   → {"field": "location", "value": "+楼梯间"}
        {"林默": "情绪变为惊恐"}   → {"entity": "林默", "value": "情绪变为惊恐"}
    """
    text = _stringify(val)
    if _is_field_key(key):
        return {"field": key.strip().lower(), "value": text}
    return {"entity": key, "value": text}


#: 记录元素的**分隔符**（"实体名<sep>值"）—— 按优先级尝试。
#:
#: ⚠️ 只用**有明确标记性**的分隔符：`：` / `:` / `——` / `|`。
#: **不用**单破折号 `-` 或空格：它们会把「林默-失忆」这类正常内容拆错，
#: 也会把整句话（"林默情绪从疲惫变为极度惊恐"）错误切开。
_ENTRY_SEPARATORS: tuple[str, ...] = ("：", ":", "——", "|")


def normalize_string_entries(v: Any) -> Any:
    """把 `list[str]` 型的记录集合归成 dict 列表（**形状 F**）。

    ## 为什么需要（阶段 1 收尾实测，第 5 种形状）

    真实 `write` 崩在这里：

        StructuredOutputError: 节点 scene_extractor 结构化输出校验失败
        - `state_updates.0`：Input should be an object

    根因：`normalize_record_list` 的入口判据是 `isinstance(v, dict)` ——
    **外层是 list 时直接透传**，元素若是 `str` 就到不了归一逻辑。

    ## 归一规则（对输入宽容，信息不得丢）

        "林默：失忆"                        → {"entity":"林默","value":"失忆"}
        "林默：情绪从疲惫变为惊恐"            → {"entity":"林默","value":"情绪从…"}
        '{"entity":"林默","value":"失忆"}'   → 解 JSON 后原样返回
        "林默情绪从疲惫变为极度惊恐"           → {"value":"<整句>"}   ← 拆不出就整条留
        {"entity":"林默",...}（元素已是 dict）→ 原样保留

    ★ 关键取舍：**拆不出实体名时整条留作 `value`**，不丢信息。
    宁可把整句放进 value，也不能因为拆不出结构就扔掉它。
    """
    if not isinstance(v, list):
        return v
    out: list[Any] = []
    for item in v:
        # ① 已是 dict → 原样保留
        if isinstance(item, dict):
            out.append(item)
            continue
        # ② **不是字符串**也不是 dict → 原样保留，交给 Pydantic 处理。
        #    ★ 这一支是实测踩出来的：调用方可能传**已构造的 Pydantic 对象**
        #    （测试里 `SceneExtractionOut(events=[EventOut(...)])` 就是），
        #    早先版本对它做 `str(item)` → 变成 `"description='...'"` 这种
        #    **repr 串**，既不是 JSON 也无法拆条 → `Field required`。
        #    Pydantic 本身能接受同类模型实例，**归一逻辑不该多管**。
        if not isinstance(item, str):
            out.append(item)
            continue
        text = item.strip()
        if not text:
            continue                      # 空串不产出"幽灵状态变化"
        # ③ 元素本身是 JSON 串 → 解出来（模型有时把对象转义成字符串）
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                out.append(parsed)
                continue
        # ④ 「实体名<sep>值」拆分
        out.append(_split_entry(text))
    return out


def _split_entry(text: str) -> dict[str, Any]:
    """把一行文字拆成 `{entity, value}`；拆不出实体名时只给 `value`。

    三种尝试，按可靠性从高到低：

      ① 显式分隔符（`：` / `:` / `——` / `|`）
      ② 前缀「实体名 + **已知字段名**」（如 `林默情绪从疲惫变为惊恐`）
      ③ 都不成 → 整条留作 `value`（**信息不得丢**）

    拆出的"实体名"还会过一次 `_is_field_key` —— 因为模型可能写成
    `"location: 楼梯间"`（英文冒号 + 字段名）。
    """
    # ① 显式分隔符
    for sep in _ENTRY_SEPARATORS:
        idx = text.find(sep)
        if idx <= 0:
            continue
        head = text[:idx].strip()
        tail = text[idx + len(sep):].strip()
        if not head or not tail:
            continue
        # 头部过长 ⇒ 不是实体名，是整句话（防"林默走进了楼梯间：他感到…"被拆错）
        if len(head) > 12:
            continue
        if _is_field_key(head):
            return {"field": head.lower(), "value": tail}
        return {"entity": head, "value": tail}

    # ② 前缀「实体名 + 已知字段名」（无分隔符的自然句，实测形状 F-自然句）
    hit = _split_name_field_prefix(text)
    if hit is not None:
        return hit

    # ③ 拆不出 → 整条留作 value（**信息不得丢**）
    return {"value": text}


#: 中文人名的合理长度（2~4 字：`林默` / `陈砚` / `欧阳修远`）
_NAME_LEN_RANGE: tuple[int, int] = (2, 4)


def _split_name_field_prefix(text: str) -> dict[str, Any] | None:
    """尝试把「实体名 + 已知字段名…」拆开；不成返回 `None`。

    ## 为什么需要这一条

    实测形状 F 里有**没有分隔符**的自然句：

        "林默情绪从疲惫变为极度惊恐"

    不拆的话整句只能塞进 `value` —— 信息虽在，但**挂不到任何角色身上**
    （用户痛点 2「实体/势力追踪混乱」正是这类"记录没有归属"的后果）。

    ## 判据（刻意收窄，避免误拆）

    只有**同时满足**才拆：

      1. 前缀是 2~4 字（人名量级）
      2. **紧跟其后的字段名在 `_CN_FIELD_KEYS` 白名单里**

    第 2 条是关键：它要求一个**已知**字段名紧邻，
    所以 `"楼道里的气温忽然降了下来"`（`气温` 不在白名单）不会被拆。
    这比"按标点/长度猜"可靠得多。

    ⚠️ 已知的良性误判：`"他情绪失控了"` 会拆出 `entity="他"`（代词不是实体名）。
    这比丢掉归属更可接受，且下游按实体表过滤时会自然淘汰。
    """
    for field_key in sorted(_CN_FIELD_KEYS, key=len, reverse=True):
        idx = text.find(field_key)
        # 字段名必须在**开头不远处**（前缀即实体名）
        if not (_NAME_LEN_RANGE[0] <= idx <= _NAME_LEN_RANGE[1]):
            continue
        head = text[:idx].strip()
        tail = text[idx + len(field_key):].strip()
        if not head or not tail:
            continue
        # 前缀不得含标点/空白（那说明它不是紧凑的人名）
        if any(c in head for c in "，。！？；：、 \t\n"):
            continue
        return {"entity": head, "field": field_key, "value": tail}
    return None


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


#: 需要**摊平/拆条**的字段（schema 是 `list[Model]`）。
#:
#: ⚠️ 不要往里加 `deaths`/`unsupported` —— 见 `_PLAIN_STRING_LIST_FIELDS`。
_RECORD_LIST_FIELDS: tuple[str, ...] = (
    "state_updates", "injury_updates", "events", "entities",
)

#: schema 本就是 **`list[str]`** 的字段 —— 只允许 `str` → `[str]` 包装。
#:
#: ⚠️ 实测踩到：把它们放进 `_RECORD_LIST_FIELDS` 会让
#: `deaths: ["中年男人"]` 被拆成 `[{"value": "中年男人"}]`
#: → `Input should be a valid string`。**元素是名字，不是记录。**
_PLAIN_STRING_LIST_FIELDS: tuple[str, ...] = ("deaths", "unsupported")


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
        """把记录集合的**各种形状**归一到列表。

        ★ 三类字段**分开处理**（踩过的坑：混在一起会把 `list[str]` 字段改坏）：

        | 字段 | schema 类型 | 归一动作 |
        |---|---|---|
        | `state_updates` / `injury_updates` / `events` / `entities` | `list[Model]` | dict → 摊平；list[str] → 拆条 |
        | `deaths` / `unsupported` | **`list[str]`** | 只做 `str` → `[str]` 包装，**不拆条** |
        | 其余 | — | 不动 |

        ⚠️ **`deaths`/`unsupported` 绝不能走"拆条"** ——
        它们本来就是字符串列表，元素是**角色名/名词**，不是记录。
        实测踩到：把 `"中年男人"` 拆成 `{"value": "中年男人"}` →
        `list[str]` 校验失败（`Input should be a valid string`）。
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for f in _RECORD_LIST_FIELDS:
            val = d.get(f)
            if isinstance(val, dict):
                d[f] = normalize_record_list(val)
            elif isinstance(val, list):
                # ★ 形状 F：元素可能是字符串（`list[str]`）
                d[f] = normalize_string_entries(val)
            elif isinstance(val, str):
                d[f] = [val] if val.strip() else []
        for f in _PLAIN_STRING_LIST_FIELDS:
            val = d.get(f)
            if isinstance(val, str):
                # 只有一条时模型会给裸字符串
                d[f] = [val] if val.strip() else []
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
