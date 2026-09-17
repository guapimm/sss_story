"""节点：正文抽取（P4-9，对策⑥）—— **三个节点，每个只抽一类**。

## 为什么必须拆开（这是旧项目最贵的一条教训）

旧项目 `post_processor.py` 的**单条** system prompt 超过 **10,000 字符**，
一次要求抽取 11 类信息（cards / summary / state_updates / deaths /
relationship_updates / events / plot_progress / claims / schema_delta /
hang / entities）。实测结果：

    entity_classify   空输出 100%（32/32）
    verifier          空输出  62.3%
    reasoning 占 completion 99.4%

> **一次抽太多的直接后果不是"抽得差"，而是"什么都不抽"。**

本模块拆成三个节点，各带**自己那一类的 schema**：

    scene_extractor     事件 / 状态 / 损伤 / 死亡 / 实体提及
    entity_resolver     提及名 → 既有实体 ID 或新建规范名
    relation_extractor  **只抽社会关系**（不抽动作）

## 动作 vs 关系（旧项目 86.7% 关系是非人际关系的根因）

旧项目把「查看/记录/离开/拾取/值守」这类**动作**写进了关系表，
实测 966 条关系里 **86.7% 不是人物-人物**。
本模块的三条硬约束：
  · `relation_extractor` 的 prompt **明确排除动作**
  · `RelationUpdateOut` 的两端必须是**实体名**（不是动词短语）
  · 落地时由 `domain/entities.py` 的 `EntityRelation.validate` 兜底

## 抽取类节点禁止思考

由 `config/providers.toml` 的 `thinking = "forbidden"` 声明驱动
（能力声明式路由，代码里没有模型名）。
抽取是**模式匹配**任务，长思考只会烧预算并把输出挤没 ——
旧项目 reasoning 占 completion **99.4%** 就是这件事。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import (
    EntityResolutionOut,
    RelationExtractionOut,
    SceneExtractionOut,
)
from storyforge.infra.prompts.library import build_extractor_layers

logger = logging.getLogger(__name__)


SCENE_EXTRACTOR_SYSTEM = """你是小说记录员。根据本章正文与当前记忆账本，抽取结构化记忆增量。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架（字段名照抄，不要自造同义名）

```json
{
  "summary": "本章梗概（80 字内）",
  "events": [
    {"story_time": "故事内时间", "location": "地点名",
     "participants": ["参与者"], "description": "发生了什么",
     "consequence": "后果"}
  ],
  "state_updates": [
    {"entity": "角色名", "field": "位置", "value": "新值",
     "evidence": "正文依据"}
  ],
  "injury_updates": [
    {"entity": "受伤者", "name": "损伤名", "kind": "伤势",
     "severity": "中", "status": "好转", "recov": "恢复方向",
     "evidence": "正文依据"}
  ],
  "deaths": ["本章死亡的角色名"],
  "entities": [
    {"name": "实体名", "kind": "人物", "aliases": [],
     "intro_method": "narrator_intro", "context": "首次出现的句子"}
  ],
  "unsupported": ["无引入依据的名词"]
}
```

## ★ `state_updates` 的两种写法（**只有这两种合法**）

**写法 A（推荐）**：每条一个对象，`entity` 是**角色名**。

```json
"state_updates": [
  {"entity": "林默", "field": "位置", "value": "楼梯间"},
  {"entity": "林默", "field": "情绪", "value": "恐慌→恐惧"},
  {"entity": "林默", "field": "装备", "value": "+《住户须知》|来源:口袋"}
]
```

**写法 B（紧凑）**：外层键是**角色名**，值是"字段：值"。

```json
"state_updates": {"林默": "情绪从疲惫变为惊恐"}
```

⛔ **绝对不要**用字段名当外层键，例如把位置/情绪/装备直接列成外层键：

```text
"state_updates": { "location": …, "emotion": …, "goal": … }   ← ✗ 错
```

这样写会让系统把 `location` 当成**角色名**，于是这些状态
**没有一条挂在真正的角色身上**（实测已发生过：那一章主角是"林默"，
但 4 条状态全挂在了不存在的实体上）。
**每条状态必须指明是"谁"的状态。**

## 抽取原则

1. **只报有变化的内容**。未变化的条目**绝不重复输出**
   （同值不覆盖、不推进计时）。
2. **区分事实与说法**。角色说的话不一定是世界真相。
3. **动作属于事件，不属于关系**。查看、记录、离开、拾取、值守这类动作
   写在 `events` 里（本节点不抽关系）。

## 各字段说明

- `summary`：本章梗概（80 字内）。
- `events`：2~5 条关键事件，每条含故事内时间、地点、参与者、描述、后果。
- `state_updates`：人物**可变状态**的变化（位置/境界/目标/情绪/装备等）。
  只记变化项。装备用 `+物品名`/`-物品名` 表示获得/失去，
  新获得物品须带来源：`+物品名|来源:方式·来源人/地点`。
  不变属性（外貌/出身/天赋）严禁出现在这里。
- `injury_updates`：损伤变化。**必须**给出 severity 与 status。
  好转/痊愈/解除**必须**能在 `events` 里找到对应的治疗事件，
  否则不要报恢复类状态。
- `deaths`：本章死亡的角色名。**漏记会导致该角色复活的世界级 Bug。**
- `entities`：本章出现的实体（人物/势力/地点/物品/功法/规则/术语）。
  `kind` 必须用规定的取值。**「势力」与「组织」都是合法值**。
  `intro_method` 标注引入方式；若是凭空出现或无依据回忆，
  标 `appeared_out_of_nowhere` / `baseless_recall` / `baseless_knowledge`。
- `unsupported`：无引入依据的名词清单（可为空）。
  **普通名词（石头/院落/老者/灯光）不要列入**。"""


ENTITY_RESOLVER_SYSTEM = """你是实体归一器。把本章出现的**实体提及**对齐到既有实体，或给出新的规范名。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架（字段名照抄）

```json
{
  "mentions": ["本章出现的实体名"],
  "links": {"提及名": 123},
  "new_canonical": {"提及名": "规范名"}
}
```

## 任务

给定「既有实体清单」与「本章提及列表」：

- `links`：提及名 → **既有实体 ID**（能确定对上的才给，宁可漏不可错）。
- `new_canonical`：提及名 → **规范名**。
  · 能归一到既有正名/别名的 → 给出**那个正名**；
  · 确实是新实体的 → 给出**统一后的规范写法**
    （如「老周」「周德海」应统一为一个规范名，不要并存）。
- `mentions`：本章出现的实体名（原样列出，供核对）。

## 硬性要求

1. **同一实体不得并存两个规范名**。若「陈砚」「小陈」「砚哥」都指同一人，
   必须归一到同一个规范名。
2. **别名不要丢**：曾用不同写法的，规范名取**最正式**的那一个。
3. **不要臆造 ID**：`links` 里只能出现上方清单里真实存在的 ID。
   拿不准就放进 `new_canonical`，不要猜 ID。"""


RELATION_EXTRACTOR_SYSTEM = """你是社会关系抽取器。**只抽人物/势力之间的社会关系**。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架（字段名照抄）

```json
{
  "relations": [
    {"from_entity": "主体名", "to_entity": "客体名", "kind": "盟友",
     "intensity": 60, "sentiment": "positive", "is_mutual": true,
     "surface_facing": "世人所知的关系", "true_facing": "真实立场",
     "evidence": "正文依据"}
  ]
}
```

## 绝对禁止

**不要把动作当成关系。** 「查看」「记录」「离开」「拾取」「值守」
「翻找」「听见」这类动作**不是关系**，不要出现在输出里。

旧项目实测：因为把动作写进关系表，966 条关系里 **86.7% 不是人物-人物** —— 
关系网因此完全失去分析价值。

## 抽取要求

每条关系给出：

- `from_entity` / `to_entity`：两端都必须是**实体名**（人名或势力名），
  不能是动词短语、不能是物体、不能是地点。
- `kind`：关系类型，**必须用规定的取值**。
- `intensity`：0~100。
- `sentiment`：情感倾向。
- `surface_facing`：**世人所知**的关系表现（如「师徒」）。
- `true_facing`：**真实立场**。与 surface 不同时即暗线张力。
  ★ 这是「人物关系一维化」的解药：只写「盟友」会让关系网扁平，
  写上「表面盟友、暗中提防」才有张力。
- `is_mutual`：是否双向。**不对等的关系同样是有效信息**
  （一方视为挚友、另一方视为工具，正是好戏）。
- `evidence`：正文依据。

## 硬性要求

1. **只报本章有关系变化或首次确立的**，不要把既有关系原样重复一遍。
2. **允许非对等**：`is_mutual=false` 是正常且常见的，不要强行对称。
3. 两端至少有一端是**人物**（势力-势力关系也可以，但不要出现
   「人-物体」这种不是社会关系的条目）。"""


@dataclass
class ExtractionResult:
    """一次抽取的产出（三个节点各自返回自己的那一份）。"""

    scene: SceneExtractionOut | None = None
    entities: EntityResolutionOut | None = None
    relations: RelationExtractionOut | None = None
    llm_calls: int = 0
    ok: bool = False
    error: str | None = None
    problems: list[str] = field(default_factory=list)


def render_known_entities(entities: list[tuple[int, str, list[str]]]) -> str:
    """渲染既有实体清单（供归一器对齐）。

    参数：`[(id, 规范名, 别名列表), ...]`
    """
    if not entities:
        return "（本章是全书第一章，尚无既有实体）"
    lines = ["ID | 规范名 | 别名"]
    for eid, name, aliases in entities:
        lines.append(f"{eid} | {name} | {'、'.join(aliases) if aliases else '—'}")
    return "\n".join(lines)


def _layers(system: str, prose: str, known: str):
    """组装抽取 prompt 并校验。

    ★ 抽取类节点的 prompt **不含世界观圣经** —— 抽取只需本章正文与既有账本，
    塞入圣经只会增加成本且稀释注意力（旧项目 `post_processor` 的
    system prompt 超 10,000 字符 → `entity_classify` 空输出 100%）。
    """
    layers = build_extractor_layers(instructions=system, prose=prose, known=known)
    return layers


async def extract_scene(
    client: LLMClient,
    *,
    book_id: int,
    global_no: int,
    prose: str,
    known: str = "",
    idempotency_key: str | None = None,
) -> tuple[SceneExtractionOut | None, int]:
    """抽事件 / 状态 / 损伤 / 死亡 / 实体提及。返回 `(结果, 实际调用次数)`。"""
    layers = _layers(SCENE_EXTRACTOR_SYSTEM, prose, known)
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="scene_extractor",
        system=messages["system"],
        user=messages["user"],
        schema=SceneExtractionOut,
        context=CallContext(
            node="scene_extractor", book_id=book_id, global_no=global_no,
            idempotency_key=idempotency_key,
        ),
        prompt_prefix=layers.cacheable_prefix(),
    )
    return out, (0 if call.from_cache else 1)


async def resolve_entities(
    client: LLMClient,
    *,
    book_id: int,
    global_no: int,
    mentions: list[str],
    known_entities: list[tuple[int, str, list[str]]],
    idempotency_key: str | None = None,
) -> tuple[EntityResolutionOut | None, int]:
    """把提及名对齐到既有实体或给出规范名。

    输入用 `mentions` 而非整章正文 —— 归一不需要重读正文，
    这也让本节点的输入体量与章节长度**解耦**（省钱，且上下文不随章增长）。
    """
    layers = _layers(
        ENTITY_RESOLVER_SYSTEM,
        "本章提及的实体：\n" + "\n".join(f"- {m}" for m in mentions),
        render_known_entities(known_entities),
    )
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="entity_resolver",
        system=messages["system"],
        user=messages["user"],
        schema=EntityResolutionOut,
        context=CallContext(
            node="entity_resolver", book_id=book_id, global_no=global_no,
            idempotency_key=idempotency_key,
        ),
        prompt_prefix=layers.cacheable_prefix(),
    )
    return out, (0 if call.from_cache else 1)


async def extract_relations(
    client: LLMClient,
    *,
    book_id: int,
    global_no: int,
    prose: str,
    known: str = "",
    idempotency_key: str | None = None,
) -> tuple[RelationExtractionOut | None, int]:
    """只抽社会关系（不抽动作）。"""
    layers = _layers(RELATION_EXTRACTOR_SYSTEM, prose, known)
    messages = layers.assemble()
    out, call = await client.complete_structured(
        node="relation_extractor",
        system=messages["system"],
        user=messages["user"],
        schema=RelationExtractionOut,
        context=CallContext(
            node="relation_extractor", book_id=book_id, global_no=global_no,
            idempotency_key=idempotency_key,
        ),
        prompt_prefix=layers.cacheable_prefix(),
    )
    return out, (0 if call.from_cache else 1)


#: 关系两端**不得**是动作词（旧项目 86.7% 非人际关系的直接特征）。
_ACTION_WORDS: frozenset[str] = frozenset(
    {
        "查看", "记录", "离开", "拾取", "值守", "翻找", "听见", "看到",
        "走进", "走出", "打开", "关上", "坐下", "站起", "拿起", "放下",
    }
)


def validate_relations(out: RelationExtractionOut) -> list[str]:
    """零 LLM 校验：剔除"动作冒充关系"。

    这是旧项目 **86.7% 非人物-人物关系**的直接防线。
    返回问题列表（空 = 通过）。
    """
    problems: list[str] = []
    for r in out.relations:
        for role, name in (("主体", r.from_entity), ("客体", r.to_entity)):
            n = (name or "").strip()
            if not n:
                problems.append(f"关系{role}为空（两端都必须是实体名）")
                continue
            if n in _ACTION_WORDS:
                problems.append(
                    f"关系{role}「{n}」是**动作**不是实体 —— "
                    f"旧项目把动作写进关系表导致 86.7% 关系不是人物-人物"
                )
            if len(n) > 20:
                problems.append(
                    f"关系{role}「{n[:20]}…」过长，疑似动词短语而非实体名"
                )
    return problems


def validate_scene(
    out: SceneExtractionOut,
    *,
    planned_beats: list[Any] | None = None,
    global_no: int = 0,
    scene_count: int = 0,
    injuries: list[Any] | None = None,
) -> list[str]:
    """零 LLM 校验：抽取结果的**内部一致性**。

    ## 恢复类申报必须由**结构化节拍**支撑（不是关键词匹配）

    旧项目用自由文本关键词判恢复（`memory.py:2114-2121`：events 里要同时
    出现角色名 + 25 个治疗语义词之一），副作用是把「记录一致性」变成
    「叙事义务」：prompt 只让写 2~5 条关键事件，过渡章可能没有 events
    → **恢复申报被拒 → 自锁**（这是恢复率 0% 的成因之一）。

    本实现改用 `domain/health.validate_recovery_claim` 的**结构化证据**：
    必须存在章号匹配、`scene_seq` 真实存在的 `HealthBeat`。
    确定性（可查表）、无自锁（不依赖自由文本）。
    """
    from storyforge.domain.health import validate_recovery_claim  # noqa: PLC0415

    problems: list[str] = []
    if not (out.summary or "").strip():
        problems.append("缺 summary")

    # 报"死亡"却没有任何事件支撑 → 世界级 Bug 的来源
    if out.deaths and not out.events:
        problems.append(
            f"报了 {len(out.deaths)} 个死亡但没有任何事件支撑 —— "
            f"死亡必须有事件依据（漏记会导致角色复活，多记会导致角色消失）"
        )

    if not injuries:
        return problems

    by_name = {getattr(i, "name", ""): i for i in injuries}
    beats = list(planned_beats or [])
    for iu in out.injury_updates:
        injury = by_name.get(iu.name)
        if injury is None:
            continue
        try:
            from storyforge.domain.enums import InjuryStatus  # noqa: PLC0415

            status = InjuryStatus(iu.status)
        except ValueError:
            problems.append(f"损伤「{iu.name}」的状态取值非法：{iu.status!r}")
            continue
        check = validate_recovery_claim(
            injury, target_status=status, chapter_no=global_no,
            beats=beats, scene_count=scene_count,
        )
        if not check.ok:
            problems.append(check.reason)
    return problems


__all__ = [
    "SCENE_EXTRACTOR_SYSTEM",
    "ENTITY_RESOLVER_SYSTEM",
    "RELATION_EXTRACTOR_SYSTEM",
    "ExtractionResult",
    "render_known_entities",
    "extract_scene",
    "resolve_entities",
    "extract_relations",
    "validate_relations",
    "validate_scene",
]
