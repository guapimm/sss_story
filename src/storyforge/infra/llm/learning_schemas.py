"""learn 模式的结构化输出契约（阶段 2-3 收尾：LLM 生成学习地图与平替修辞）。

## 为什么单独成模块（不塞进 `schemas.py` / `extraction_schemas.py`）

  · `schemas.py` 当时已近 800 行（规则 10 上限，**该上限已于 2026-09-26 取消**）
  · `extraction_schemas.py` 是**story 抽取**的契约（正文 → 结构化事实）
  · 本模块是 **learn 规划**的契约（主题 + 研究 → 学习地图 / 平替修辞）

三者职责不同。**拆分依据是职责，不是凑行数** —— 本模块与 story 规划
本就是两套契约，learn 线还要独立演进；800 行上限只是当年的触发点。

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

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class KnowledgePointOut(BaseModel):
    """一个知识点（模型输出侧）。

    ⚠️ 与 `domain/learning.py:KnowledgePoint` 的区别：那个是**领域模型**
    （带 `point_id` 与来源溯源，由代码赋值）；本类是**模型输出契约**
    （模型只给名字与说明，**不给 id** —— id 由代码生成，避免模型编造重复 id）。

    ## ★ `evidence_index`：让溯源**逐条可核验**（2026-09-23 新增）

    ### 修的是什么

    原实现的溯源是**假溯源**（`commands/learning.py:469` 注释自承
    「不逐条对应某个 URL」）：把研究资料的**前 3 条 URL 全挂给每条知识点**。
    实测 11 条知识点的 `source_urls` **完全相同**。

    后果：`validate_map` 只查"URL 非空" → **全绿通过**，
    但无法回答「**这条**知识点依据的是**哪一段**」。
    而 `evidence` 字段（注释写明是"**反幻觉的依据**"）
    被 `evidence=shortfall or ""` 占用成了别的东西 —— **语义错位**。

    ### 为什么用 index 而不是让模型直接抄 URL

      · **结构化事实 vs 文本启发式**：项目立的规矩是
        「能用结构性事实回答的问题，不要用文本启发式回答」。
        让模型回一个**编号**，代码据此取 URL 与原文 —— 可追溯、
        可校验（编号越界即报错）。让模型抄 URL 则会引入拼写漂移。
      · **防 URL 幻觉**：模型只能引用**存在**的编号，不能编造网址。

    ### 取值约定

      · 从 **1** 开始（与 prompt 里给资料的编号一致；
        `0` 不合法 —— 那通常是"从 0 开始数"的 off-by-one）
      · 越界 → 校验失败（不静默丢弃，见 `_check_index_in_range`）
    """

    name: str = Field(description="知识点名（简洁，≤20 字）")
    summary: str = Field(default="", description="一句话说明（≤40 字）")
    difficulty: int = Field(default=1, description="难度 1~5")
    #: 依赖的前置知识点名（可空；用于排序）
    dependencies: list[str] = Field(default_factory=list)
    #: ★ 该知识点依据的研究资料**编号**（prompt 里给出的 `[N]`，从 1 开始）。
    #:
    #: 空 tuple = 模型未标注依据 → 代码侧会显式暴露（不静默补默认值：
    #: "没标注"与"依据第 1 条"是两回事，替它猜等于制造假溯源）。
    evidence_index: list[int] = Field(
        default_factory=list,
        description="依据的研究资料编号（从 1 开始，可多条）",
    )

    @field_validator("evidence_index", mode="before")
    @classmethod
    def _normalize_index(cls, v: Any) -> Any:
        """归一：接受单个 int、数字字符串、`[1, "2"]` 等写法。

        与项目既有原则一致 —— **对输入宽容，对输出严格**：
        模型把编号写成 `"1"` 或裸 `1` 都是自然的，形状漂移不该让整轮失败。
        但**越界**是实质错误，交由 `_check_index_in_range` 显式报错。
        """
        if v is None or v == "":
            return []
        if isinstance(v, (int, str)) or not isinstance(v, (list, tuple)):
            v = [v]
        out: list[int] = []
        for x in v:
            try:
                out.append(int(str(x).strip()))
            except (TypeError, ValueError):
                continue      # 非数字项丢弃（形状瑕疵），不抛错
        return out

    @model_validator(mode="after")
    def _check_index_in_range(self) -> KnowledgePointOut:
        """编号必须 ≥1；**上界**由调用方按资料条数校验（schema 不知道条数）。

        ⚠️ 这里只拦"明显非法"（0 / 负数）—— 它们一定是 off-by-one。
        上界的校验放在 `commands/learning.py`，因为只有那里知道
        `len(snippets)`。
        """
        bad = [i for i in self.evidence_index if i < 1]
        if bad:
            raise ValueError(
                f"知识点 {self.name!r} 的 evidence_index 含非法编号 {bad} —— "
                f"编号从 **1** 开始（0 通常是 off-by-one）"
            )
        return self



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


#: 核验三态（方案 B1，`doc/learn-验收标准-草稿.md` §4.1）。
#:
#: ★ 三态而非二态的原因：**教学需要"把资料讲透"**，
#: 合理推演是必要的（由「点茶用茶筅」推出「茶筅是核心器具」）。
#: 一刀切要求"逐字有据"会让地图退化成资料摘抄。
#: 故 `inferred` 必须与 `unsupported` 分开 —— 前者计入通过（但标记），
#: 后者计入失败（疑似幻觉）。
VERDICTS: tuple[str, ...] = ("supported", "inferred", "unsupported")


class PointVerdictOut(BaseModel):
    """单条知识点的核验判定。"""

    point: str = Field(description="知识点名（照抄待核验列表里的名字）")
    verdict: str = Field(
        description="三态之一：supported（资料有直接依据）/ "
        "inferred（可由资料合理推得）/ unsupported（资料里找不到依据）"
    )
    reason: str = Field(default="", description="判定理由（≤50 字，须指向资料）")
    evidence_index: list[int] = Field(
        default_factory=list,
        description="支撑该判定的资料编号（从 1 开始；unsupported 留空）",
    )

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, v: Any) -> Any:
        """归一：大小写、空格、以及模型可能给的中文/近义写法。

        ★ 但**不把未知值硬塞成 supported** —— 那会把"判不了"伪装成"通过"，
        正是本项目最忌讳的"给假绿"。未知值原样保留，由节点层显式暴露。
        """
        if v is None:
            return ""
        s = str(v).strip().lower()
        aliases = {
            "支持": "supported", "有据": "supported", "有依据": "supported",
            "ok": "supported", "yes": "supported", "true": "supported",
            "推断": "inferred", "推论": "inferred", "推得": "inferred",
            "implicit": "inferred", "implied": "inferred",
            "不支持": "unsupported", "无据": "unsupported", "无依据": "unsupported",
            "no": "unsupported", "false": "unsupported",
            "hallucination": "unsupported", "编造": "unsupported",
        }
        return aliases.get(s, s)

    @field_validator("evidence_index", mode="before")
    @classmethod
    def _normalize_evidence_index(cls, v: Any) -> Any:
        """与 `KnowledgePointOut._normalize_index` 同口径（输入宽容）。"""
        if v is None or v == "":
            return []
        if isinstance(v, (int, str)) or not isinstance(v, (list, tuple)):
            v = [v]
        out: list[int] = []
        for x in v:
            try:
                out.append(int(str(x).strip()))
            except (TypeError, ValueError):
                continue
        return out


class VerificationOut(BaseModel):
    """知识点核验结果（方案 B1）。

    ## 为什么要独立于生成（防"自己批自己"）

    核验侧必须是**可区分的第二个判断者**。当前约束（2026-09-23）：

      · `DEEPSEEK_API_KEY` 未配置 → 跨厂商（B3）**做不了**
      · 故落 **B1：同厂不同档位**（`mimo-v2.6-pro` 核验 + 独立 prompt）

    ★ **B1 不是强隔离**（同代模型可能共享训练数据与偏见）。
    故判据里保留**人工抽检**作为最终防线，不能只信自动核验。

    ## 与 `FactVerifier` Protocol 的关系

    `modes/protocols.py` 的 `FactVerifier` 是**契约**（`verify(claims) -> results`），
    本类是其 learn 侧实现的输出形状。Protocol 的通用化留待需要时再做。
    """

    verdicts: list[PointVerdictOut] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, data: object) -> object:
        """★ 兼容裸列表与单条对象（与 `GapCheckOut` 同原则：归一，不拒绝）。"""
        if isinstance(data, list):
            return {"verdicts": data}
        if not isinstance(data, dict):
            return data
        d = dict(data)
        v = d.get("verdicts")
        if isinstance(v, dict):
            d["verdicts"] = [v]
        elif v is None:
            for alt in ("判定", "结果", "results", "checks", "verdict"):
                if alt in d:
                    got = d.pop(alt)
                    d["verdicts"] = got if isinstance(got, list) else [got]
                    break
        return d

    # -- 统计 ---------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """三态计数（**未知判定单独计**，不混入 supported）。"""
        out = dict.fromkeys(VERDICTS, 0)
        for v in self.verdicts:
            if v.verdict in out:
                out[v.verdict] += 1
            else:
                out["unknown"] = out.get("unknown", 0) + 1
        return out

    def accuracy(self) -> float:
        """准确率 = (supported + inferred) / 总数（草稿 §4.3 口径）。

        空结果返回 0.0 —— 不返回 1.0：**没有判定不等于全部通过**
        （假绿是本项目反复吃过亏的形态）。
        """
        total = len(self.verdicts)
        if total == 0:
            return 0.0
        c = self.counts()
        return (c["supported"] + c["inferred"]) / total

    def consecutive_unsupported(self) -> int:
        """最长**连续** `unsupported` 段（草稿 §4.3 硬门禁：不得连续 3 条）。

        ★ 连续与总数是两回事：集中幻觉比分散更危险
        （连续 3 条说明模型在某个方向上整段编造，而非个别失误）。
        """
        best = cur = 0
        for v in self.verdicts:
            cur = cur + 1 if v.verdict == "unsupported" else 0
            best = max(best, cur)
        return best


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
        "difficulty": 1, "dependencies": ["前置知识点名"],
        "evidence_index": [1, 3]}
     ]}
  ]
}
```

## ★ 每个知识点必须标注 `evidence_index`（依据哪几条资料）

研究资料在下方以 **`[1] [2] [3] …` 编号**给出。

  · `evidence_index` 填**编号数组**，如 `[1]`、`[2, 5]`
  · **必须填**。这是"这条知识点从哪来"的唯一凭据 ——
    没有依据的知识点会被判为**可能的幻觉**并被拒
  · **只填真实存在的编号**。不能引用不存在的编号，也不能编造网址
  · 一条知识点可以依据多条资料；不同知识点应依据**不同**资料
    （若所有知识点都填同一组编号，说明没有真正逐条溯源）

> 为什么要你标编号而不是让代码猜：你的标注是**可追溯的事实**，
> 而"事后用相似度匹配"是猜测 —— 猜测无法作为反幻觉的依据。

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


#: 知识点核验（方案 B1）。**独立于 `LEARNING_MAP_SYSTEM`** ——
#: 这是"防自己批自己"的第一层（第二层是换模型档位 `mimo-v2.6-pro`）。
#:
#: ★ 核验侧**看不到生成过程**：只给「研究资料 + 生成的知识点」，
#: 不给"生成时用的 prompt"，避免它顺着生成者的思路走。
VERIFY_SYSTEM = """你是事实核查员。判断下面每条知识点**是否被给出的研究资料支撑**。

## 判定标准（三态，只能选一个）

- `supported`：资料里有**直接依据**（能找到对应句子）
- `inferred` ：资料未直说，但可由资料**合理推得**（教学需要讲透，合理推演允许）
- `unsupported`：资料里**找不到任何依据**（疑似编造）

⚠️ `inferred` **不是** `unsupported` —— 不要把"资料没逐字写"当成编造。
⚠️ 但 `unsupported` 也不要放过：**没有依据的知识点会被写进小说**，
读者会把编造当知识学。

## 输出骨架（字段名照抄）

```json
{"verdicts": [
  {"point": "知识点名（照抄下面的名字）", "verdict": "supported",
   "reason": "资料 [2] 明确写了……", "evidence_index": [2]}
]}
```

## 硬性要求

1. **每条知识点都要给判定**，一条不漏（数量必须与待核验列表一致）。
2. `verdict` 只能是 `supported` / `inferred` / `unsupported` 三者之一。
3. `point` **照抄**待核验列表里的名字，不要改写。
4. `evidence_index` 填资料编号（从 1 开始）；判 `unsupported` 时留空数组。
5. `reason` ≤50 字，判 `unsupported` 时说明"资料里没有提到什么"。
6. **判定要严格**：宁可标 `inferred`，也不要为了让人满意而标 `supported`。
   看不出依据就用 `unsupported` —— 你的职责是**查出问题**，不是让人通过。"""


__all__ = [
    "KnowledgePointOut",
    "LearningModuleOut",
    "LearningMapOut",
    "GapCheckOut",
    "PointVerdictOut",
    "TermMappingOut",
    "TermMappingBatchOut",
    "VERDICTS",
    "VerificationOut",
    "LEARNING_MAP_SYSTEM",
    "VERIFY_SYSTEM",
    "GAP_CHECK_SYSTEM",
    "MAPPING_SYSTEM",
]
