"""learn 模式的结构化输出契约（阶段 2-3 收尾：LLM 生成学习地图与平替修辞）。

## 为什么单独成模块（不塞进 `schemas.py` / `extraction_schemas.py`）

  · `schemas.py` 已 **778/800 行**（只剩 22 行）—— 物理上放不下
  · `extraction_schemas.py` 是**story 抽取**的契约（正文 → 结构化事实）
  · 本模块是 **learn 规划**的契约（主题 + 研究 → 学习地图 / 平替修辞）

三者职责不同。**拆分依据是职责，不是凑行数** ——
契约规则 10 是**为旧项目 `pipeline.py` 8,607 行立的**，
撞限时的正确反应是拆，不是放宽。

## 权威来源（旧项目实测）

  · 学习地图结构：`agents/researcher.py:217-245`（`_gen_map`）
    与 `:380-384`（`_MapFiller` 的骨架）
  · 分档规模：`researcher.py:28-33` 的 `RESEARCH_PROFILE`
    （`points_per_module` / `modules` 按 fast/standard/grand/ultra 分档）
  · 缺口自检：`researcher.py:344-373`（`_gap_fill` 的 `{"gaps": [...]}`）
  · 平替修辞：`templates/scenes.py` 的 `mapping_rules` 格式（`术语 → 说法`）

## 形状漂移的教训（本模块刻意做的事）

本项目已因"prompt 只写散文、不给 JSON 骨架"栽过两次
（`test_prompt_skeletons.py` 的 docstring + 缺陷 11）。
因此**每个 prompt 常量都必须带 ```json 骨架**，
并由 `tests/contract/test_prompt_skeletons.py` 的 `STRUCTURED_NODES` 强制。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class KnowledgePointOut(BaseModel):
    """一个知识点（模型输出侧）。

    ⚠️ 与 `domain/learning.py:KnowledgePoint` 的区别：那个是**领域模型**
    （带 `point_id` 与来源溯源，由代码赋值）；本类是**模型输出契约**
    （模型只给名字与说明，**不给 id** —— id 由代码生成，避免模型编造重复 id）。
    """

    name: str = Field(description="知识点名（简洁，≤20 字）")
    summary: str = Field(default="", description="一句话说明（≤40 字）")
    difficulty: int = Field(default=1, description="难度 1~5")
    #: 依赖的前置知识点名（可空；用于排序）
    dependencies: list[str] = Field(default_factory=list)


class LearningModuleOut(BaseModel):
    """一个学习模块（若干知识点的分组）。

    旧项目 `_MapFiller` 的硬性限制（逐条照抄，因为它们是**实测出来的**
    防膨胀约束）：`description ≤40 字`；每模块 `knowledge_points ≤6 个`，
    `summary ≤30 字`。
    """

    name: str = Field(description="模块名（≤20 字）")
    description: str = Field(default="", description="模块说明（**≤40 字**）")
    difficulty: int = Field(default=2, description="模块难度 1~5")
    knowledge_points: list[KnowledgePointOut] = Field(
        default_factory=list, description="本模块的知识点（**≤6 个**）"
    )


class LearningMapOut(BaseModel):
    """学习地图（模型输出侧）。

    `topic_summary` 要体现"整合同一学习体系后的关系"——
    旧项目 `_gen_map` 的多子主题分支明确要求这一点。
    """

    topic_summary: str = Field(
        default="", description="主题总述：整份地图覆盖了什么、各部分如何关联"
    )
    modules: list[LearningModuleOut] = Field(
        default_factory=list, description="学习模块（按学习顺序排列）"
    )

    @model_validator(mode="after")
    def _check_not_empty(self) -> LearningMapOut:
        if not self.modules:
            raise ValueError("学习地图至少要有一个模块（空地图没有内容可教）")
        total = sum(len(m.knowledge_points) for m in self.modules)
        if total == 0:
            raise ValueError("所有模块都没有知识点（地图是空壳）")
        return self

    @property
    def point_count(self) -> int:
        return sum(len(m.knowledge_points) for m in self.modules)


class GapCheckOut(BaseModel):
    """缺口自检的输出（旧项目 `researcher.py:357-358`）。

    模型只给"盲区方向"（最多 3 个），补全由另一次调用做 ——
    与旧项目 `_gap_fill` 的两段式一致（先查缺口、再补模块）。
    """

    gaps: list[str] = Field(default_factory=list, description="尚未覆盖的盲区方向")

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: object) -> object:
        """★ 兼容 `{"gaps": "单个字符串"}` 与裸列表（实测见过的形状）。

        与抽取侧同一原则：**归一，不拒绝**（形状漂移不该让整轮失败）。

        ⚠️ 初版有两个真 bug，被 `test_learning_nodes.py` 当场抓到：
          1. 构造了 `d = dict(data)` 却 `return data`（**改的是副本**，
             归一完全不生效 —— 中文键那条测试直接失败）
          2. 没处理 `gaps` 是非列表非字符串（如 `123`）→ pydantic 报
             `Input should be a valid list`
        """
        if isinstance(data, list):
            return {"gaps": [str(x) for x in data]}
        if not isinstance(data, dict):
            return data
        d = dict(data)
        g = d.get("gaps")
        if isinstance(g, str):
            d["gaps"] = [g] if g.strip() else []
        elif g is not None and not isinstance(g, list):
            # 数字/布尔等 → 包成单元素列表（不丢信息，也不让校验炸）
            d["gaps"] = [str(g)]
        elif g is None:
            # 模型可能用了别的键名（中文键实测出现过）
            for alt in ("盲区", "missing", "gap", "方向", "gaps_direction"):
                if alt in d:
                    v = d.pop(alt)
                    if isinstance(v, list):
                        d["gaps"] = [str(x) for x in v]
                    elif v is None:
                        d["gaps"] = []
                    else:
                        d["gaps"] = [str(v)]
                    break
        return d


class TermMappingOut(BaseModel):
    """一条平替修辞（模型输出侧）。

    ★ 与旧项目 `mapping_rules` 的字符串格式（`术语 → 说法`）相比，
    本类多出 `rationale` —— 用户在 L-2 确认时**需要理由才能判断**
    （"为什么把 Token 叫指令刻度"），否则只能盲选。
    """

    term: str = Field(description="原术语（正文中禁止直出，如「Token」「递归」）")
    replacement: str = Field(description="世界内说法（正文里用这个）")
    rationale: str = Field(default="", description="为什么这样映射（≤30 字，供用户判断）")


class TermMappingBatchOut(BaseModel):
    """一批平替修辞（模型输出侧）。"""

    mappings: list[TermMappingOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# prompt 骨架（★ 必须带 JSON 骨架 —— 由 test_prompt_skeletons.py 强制）
# --------------------------------------------------------------------------- #

#: 学习地图生成。对照旧项目 `_gen_map`（`researcher.py:217-245`）。
#:
#: 分档规模来自旧项目 `RESEARCH_PROFILE`（`researcher.py:28-33`）：
#: `points_per_module` = 4/6/8/10，`modules` = 2~3/3~5/6~10/10~14。
LEARNING_MAP_SYSTEM = """你是学习路线设计师。根据研究资料，为一部**知识小说**设计学习地图。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架（字段名照抄，不要自造同义名）

```json
{
  "topic_summary": "主题总述：整份地图覆盖了什么、各部分如何关联",
  "modules": [
    {"name": "模块名（≤20 字）",
     "description": "模块说明（≤40 字）",
     "difficulty": 2,
     "knowledge_points": [
       {"name": "知识点名（≤20 字）", "summary": "一句话说明（≤40 字）",
        "difficulty": 1, "dependencies": ["前置知识点名"]}
     ]}
  ]
}
```

## 硬性要求

1. **每个模块最多 6 个知识点**；模块数按篇幅档位给定（见下）。
2. `description` ≤40 字，知识点的 `summary` ≤30 字 —— **不要展开写**。
3. 模块按**学习顺序**排列：先基础后进阶。
4. 知识点之间**不要重复**（跨模块去冗余）。
5. `topic_summary` 要体现"整合同一学习体系后的关系"，不是复述目录。
6. **只依据给定资料**。资料没覆盖的内容不要编造；
   若资料明显不足，宁可少给知识点也不要虚构。

## 为什么限制字数与条数

这是给**小说**用的地图：每个知识点后面要写成正文剧情。
知识点过多会让每章塞不下（正文没料可讲 → 或变成教科书摘抄）；
说明过长会挤占后续 prompt 的预算。"""


#: 缺口自检。对照旧项目 `_gap_fill`（`researcher.py:344-373`）。
GAP_CHECK_SYSTEM = """你是学习路线审查员。对照现有学习地图，找出尚未覆盖的重要知识盲区。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架

```json
{"gaps": ["盲区方向1", "盲区方向2"]}
```

## 硬性要求

1. **最多 3 个**盲区，只列最重要的。
2. 每个盲区是一句话的描述（≤30 字），不是单个词。
3. **只找真盲区**。地图已覆盖的方向不要重复列出；
   若确实覆盖完整，返回空数组 `{"gaps": []}`。
4. 判据是"这个方向对该主题重要，但地图没讲" ——
   不要提"可以更详细"这类程度问题。"""


#: 平替修辞生成。对照旧项目 `templates/scenes.py` 的 `mapping_rules`
#: 与四处注入的措辞（`flowmode/base.py:120` 等）。
#:
#: ★ 用户决策：L-2 是**逐条确认**，所以每条必须给 `rationale`。
MAPPING_SYSTEM = """你是「平替修辞」设计师。把知识点术语转换成**世界内说法**，
让读者在故事里自然理解概念，而正文绝不出现原术语。

**只输出 JSON**，不要任何解释或代码块标记。

## 输出骨架

```json
{"mappings": [
  {"term": "原术语", "replacement": "世界内说法",
   "rationale": "为什么这样映射（≤30 字，供用户判断）"}
]}
```

## 什么是平替修辞（这决定成败）

读者看的是**修仙故事**，不是教材。所以：

    原始术语「函数」  →  世界内说法「自创功法」
    原始术语「递归」  →  世界内说法「心镜回照」
    原始术语「报错」  →  世界内说法「心魔反噬」

正文里写"他运转自创功法"，而不是"他调用函数"。

## 硬性要求

1. **`replacement` 必须与原术语不同**（相同等于没做平替）。
2. 说法要**成体系**：同一世界观下的术语应风格一致
   （都走"功法/器物/境界"路线，不要一半仙侠一半现代）。
3. 说法要**可读**：读者看正文时能理解，不需要回头查表。
4. 优先覆盖**会出现在正文里的**术语（核心概念、操作、错误、结构），
   纯学术名词可以不给。
5. `rationale` 是给用户判断用的，写清"这个说法抓住了原术语的哪个特征"。

## ★ 参考示例（粒度与风格）

```json
{"mappings": [
  {"term": "基础概念", "replacement": "灵根/入门心法", "rationale": "入门根基，人人必学"},
  {"term": "变量", "replacement": "储物符", "rationale": "可存可取、可更换内容"},
  {"term": "循环", "replacement": "轮回诀", "rationale": "重复执行同一过程"}
]}
```

⚠️ 示例只示范**粒度与风格**，不要照抄 —— 说法必须切合本书的知识点。"""


__all__ = [
    "KnowledgePointOut",
    "LearningModuleOut",
    "LearningMapOut",
    "GapCheckOut",
    "TermMappingOut",
    "TermMappingBatchOut",
    "LEARNING_MAP_SYSTEM",
    "GAP_CHECK_SYSTEM",
    "MAPPING_SYSTEM",
]
