# learn 模式全仓耦合审计（story → learn 复用边界）

> **性质**：为「learn（知识小说）作为旗舰模式、与 story 低耦合」提供代码级事实依据。
> **方法**：4 个并行子审计（domain/graph/infra-storage/scorecard）+ 主审计逐条复核关键结论。
> **行数口径**：本仓库**行尾混用 LF/CRLF**，PowerShell `Get-Content | Measure-Object -Line`
> 在含 CRLF 的文件上**系统性少计**。下表一律采用 **read tool / grep 口径**（= 字节级 LF 计数），
> 与 `tests/contract` 自用的 `text.count("\n")+1` 一致。
> **复核声明**：本文所有 ★ 级结论均由主审计**亲自读原文确认**，非转述。
>
> **★ 2026-09-24 阅读提示（负责人要求）**：本文多处写"**接近/达到 800 行上限**"、
> "**物理上不能加代码**"。那是**事实陈述**（契约规则 10 确实是硬上限），
> 但**不要**据此得出"到上限就该拆文件"。
>
> 拆分判据是**耦合**，不是行数 —— 详见 `07-工作方式与目标导向.md` §2.1.1。
> 本文真正的价值在于**逐条的耦合点清单**（哪段代码带了 story 假设、
> 扩展点在哪），而不是行数。
>
> 具体到"该不该拆"：先看那几段代码**是不是同一职责**。
> 例如 `schemas.py`（778 行）里全是规划 schema，那是**内聚**的；
> 若 learn 要加自己的 schema，正确做法是**新文件**（职责不同），
> 而不是"因为 778/800 所以必须拆"。

---

## 0. 三条改变决策的结论（先读这个）

**① `modes/` 层是 0 字节 —— D-08 承诺的扩展点一个都不存在。**
`src/storyforge/modes/__init__.py` = **0 字节**，无 `__pycache__`（从未被 import）。
`doc/00-决策记录.md:92` 与 `doc/01-总体架构.md:369-398` 规定的 **8 个 Protocol**
（LearningMap / TeachingLedger / KnowledgeCardProvider / ResearchProvider / FactVerifier /
Estimator / GenrePlugin / PromptPack）**零实现**。全仓对 `storyforge.modes` 的唯一引用是
`tests/contract/test_architecture_invariants.py:144`（一处白名单豁免）。
→ **learn 今天能拿到的扩展点数量 = 0。** 且 owner 说的「story 复用 learn 代码」在当前代码状态下
**物理上无法成立** —— 没有 learn 代码可复用，方向只能是「learn 复用 story 的 core」。

**② 模式分支守卫有洞，learn 会静默违规而 CI 全绿（已实测确认）。**
`tests/contract/test_architecture_invariants.py:130`：
```python
MODE_BRANCH_TOKENS = {"book_kind", "is_story", "_is_story", "book_kind_"}
```
- **不含 `"mode"`** → `if book.mode == "learn":` 的 attr 名 `mode` 不被匹配
- **不含 `"learn"` / `"story"` 字面量** → 字符串也不被匹配
- `:140` 只扫 `("domain", "graph/nodes")` → **`graph/graphs/`、`infra/`、`cli.py` 完全在射程外**
- `:147-148` 只比对 `ast.Attribute.attr` 与字符串字面量，**不检测 `ast.Compare`**
  —— 而设计稿 `doc/01-总体架构.md:442` 用的正是 `ast.Compare`，**实现与设计稿不一致**

⇒ 写 `if book.mode == "learn":`，**测试通过**。规则 3 比它声称的窄。
**建议：动 learn 之前先把 `"mode"` + `"learn"`/`"story"` 加进 TOKEN 集合 —— 本次审计性价比最高的一项。**

**③ 伤势机器在生产路径上从未执行过（story 侧既有 P0 缺陷，对 learn 是净利好）。**
- `graph/graphs/books.py:303` 调 `build_detail_batch(...)` **未传 `health_schedule`**
- → `graph/nodes/detail_planner.py:602` 参数默认 `None`
- → `detail_planner.py:672` `if health_schedule is not None:` **恒假**
- → `health_beats_injected` **永远 0**，而 `books.py:361` 还在把它写进 state（只写不读）
- `HealthScheduler.schedule()` 调用点**全部在 `tests/`**，生产代码 0 个调用点

⇒ **learn 不必为伤势写适配器**（它没跑）。但 learn 若复用 `detail_planner.py`，
会继承它的**编译期**依赖（该文件 `:57-60` 顶层 import `HealthScheduler`）。

---

## 1. 模块清单

| 路径 | 行数 | 归属 | 判断依据 |
|---|---:|---|---|
| `__init__.py` | 18 | core | 仅分层声明文档 |
| **L5 接口层** | | | |
| `cli.py` | 712 | **mixed** | `doctor`/`init-db`/`arch-check`/`report` 中性；`plan`/`write` 硬编码 story 四级链；`baseline`/`score` 纯 story |
| `api/__init__.py` | 0 B | **empty stub** | 0 字节；`api/` 共 2 文件均 0 B；无 `__pycache__` |
| `api/routes/__init__.py` | 0 B | **empty stub** | 0 字节 |
| **L4 编排层** | | | |
| `orchestrator.py` | 414 | **mixed** | 机制 core（队列由枚举**全量派生**）；阶段词表 story-only |
| **L3 图与节点** | | | |
| `graph/state.py` | 263 | **mixed** | `BookState` 是 5 个 stage state 的唯一基底；含 `injuries_block`/`is_volume_end` |
| `graph/graphs/executor.py` | 98 | **core** | 纯机械 state 合并 + stage 映射表 + ConfirmPoint 转换，零领域语义 |
| `graph/graphs/chapter.py` | 146 | **core** | 6 个纯路由 + 图接线 + `needs_c7_confirm` 纯函数 |
| `graph/graphs/chapter_nodes.py` | 450 | **mixed** | `check_readiness`/`make_commit_node` core；上下文块名与抽取链 story |
| `graph/graphs/books.py` | 403 | **mixed** | 图组装 + `open_checkpointer`/`thread_config`/`GraphDeps` core；`build_all` 注册表 story 键名 |
| `graph/nodes/bible_architect.py` | 334 | **mixed** | 结构校验 core；prompt + `era` 硬闸门 story |
| `graph/nodes/chapter_writer.py` | 441 | **mixed** | 拒答检测/长度闭环 core；`assemble_context` 一层 story |
| `graph/nodes/reviewer.py` | 328 | **mixed** | `apply_revision_if_better` core；`CATEGORY_RULES` 与 prompt 文件 story |
| `graph/nodes/extractors.py` | 376 | **mixed** | `resolve_entities` 术语归一 core；`_ACTION_WORDS`/prompt story |
| `graph/nodes/continuity.py` | 79 | **mixed** | 纯去重/比对，输入输出为领域对象；零 LLM |
| `graph/nodes/volume_planner.py` | 433 | **story-only** | 存在理由即「防困在一处 + 对手阶梯 + 力量上限」 |
| `graph/nodes/skeleton_planner.py` | 536 | **story-only** | 算法层 core 但语义层（三类线下限、`new_locations` 硬前置）story |
| `graph/nodes/detail_planner.py` | 733 | **story-only** | 伤势机器 + 11 节拍词表 + 场景范式全在文件内 |
| **L2 领域层** | | | |
| `domain/enums.py` | 592 | **mixed** | 中性枚举（`BookStatus`/`CallStatus`/`UsageSource`/`PipelineStage`）+ story 枚举（`BeatType`/`InjuryKind`/`RelationKind`…） |
| `domain/entities.py` | 350 | **mixed** | `Entity`/生命周期/别名 core；`RelationKind` 张力维度 story |
| `domain/outline.py` | 433 | **story-only** | `VolumePlan`/`SceneBeat`/`ChapterPlan`/`Milestone` 全是网文结构 |
| `domain/health.py` | 572 | **story-only** | 伤势模型 + 阈值表 + 恢复排程，全套武侠/仙侠语义 |
| `domain/validators/structure_gate.py` | 458 | **story-only** | 卷级扩张度闸门（新地图/新势力/对手多样性/力量上限） |
| `domain/schedulers/health_scheduler.py` | 315 | **story-only** | 伤势节拍排程 |
| `domain/**/__init__.py` ×4 | 0 B | **empty stub** | 无包级导出面 |
| **L2 打分卡** | | | |
| `scorecard/__init__.py` | 83 | **core** | 纯 re-export 门面 |
| `scorecard/literal.py` | 632 | **mixed** | 度量引擎 core；8 张词表 story |
| `scorecard/structural.py` | 460 | **mixed** | 文本比对原语 core；专名/姓氏表 story |
| `scorecard/aggregate.py` | 503 | **story-only** | 维度名、权重、触发条件全硬编码在函数体内 |
| **L1 基础设施** | | | |
| `infra/llm/client.py` | 796 | **core** | ⚠️ **797/800，物理上无法容纳任何新增** |
| `infra/llm/schemas.py` | 777 | **story-only** | ⚠️ **778/800**；`BibleOut`/`VolumePlanOut`/`SceneBeatOut` 全是 story |
| `infra/llm/extraction_schemas.py` | 336 | **mixed** | `EntityResolutionOut` 中性；`InjuryUpdateOut`/`RelationUpdateOut` story |
| `infra/llm/capability.py` | 393 | **core** | 模型/provider/TOML 路由，零领域概念 |
| `infra/llm/transport.py` | 261 | **core** | provider usage 字段映射 + 内容块归一，零领域概念 |
| `infra/llm/prompt_builder.py` | 294 | **core** | 三层组装 + 稳定性校验；仅 `:51/61` 易变正则表需参数化 |
| `infra/llm/coercion.py` | 183 | **mixed** | 5 个归一函数中性；`FIELD_ALIASES` 11/11 条 story |
| `infra/llm/enum_text.py` | 29 | **core** | 单函数 `enum_values(EnumMeta)`，对枚举内容零假设 |
| `infra/prompts/library.py` | 425 | **story-only** | `beat_block`/`health_block`/`build_chapter_layers` 全 story |
| `infra/prompts/writer_system.md` | 27 | **story-only** | 「资深网文小说作家」 |
| `infra/prompts/style_critic.md` | 28 | **story-only** | 「资深网文主编」；AI 味/比喻/对话占比 |
| `infra/prompts/extract_scene.md` | 30 | **story-only / 死文件** | **无任何 `_load` 调用点**（见 §5.7） |
| `infra/storage/models.py` | 728 | **mixed** | `Book`/`Run`/`WorkUnit`/`LLMCall`/`Entity`/`Event` core；`Volume`/`Injury`/`Foreshadowing` story |
| `infra/storage/artifacts.py` | 253 | **core**（机制） | `_path()` 是唯一拼接点；9 个 key 构造器全 `books/{id}/...`，**无 mode** |
| `infra/storage/db.py` | 137 | **core** | 引擎/PRAGMA/WAL/自省，全文件零 story |
| `infra/storage/repositories/__init__.py` | 0 B | **empty stub** | 0 字节；13 张表**零生产写入路径** |
| `infra/retrieval/__init__.py` | 0 B | **empty stub** | 0 字节；learn **比 story 更依赖检索** |
| **观测** | | | |
| `observability/metering.py` | 423 | **mixed** | 除 `CONTEXT_SOURCES` 外全中性；**该常量是 learn 运行期硬门禁** |
| `observability/report.py` | 345 | **core** | 定价由 `providers.toml` 驱动，零 fiction 指标 |
| `observability/__init__.py` | 44 | **core** | 符号汇总，全中性名 |
| **模式层** | | | |
| `modes/__init__.py` | **0 B** | **empty stub** | **D-08 承诺的 8 Protocol 全部缺失** |

---

## 2. 可直接复用的核心（learn 也用得上 —— **不要碰**）

### 2.1 完整文件级复用，零改动

| 模块 | 行数 | 为什么 learn 直接用 |
|---|---:|---|
| `infra/storage/db.py` | 137 | 引擎/`PRAGMA foreign_keys=ON`/WAL/session/自省。逐符号核验无一处提 book/mode/story |
| `infra/storage/artifacts.py` | 253 | **机制层完全 mode-free**：`_path()`:90-92 唯一拼接点；原子写、读失败抛 `ArtifactNotFound`、append 语义、`content_hash`。task 书提到的「story/learn 路径 bug」**在本仓库不存在**（见 §5.8） |
| `observability/report.py` | 345 | `pricing_table()` 从 `config/providers.toml` 构造；`blank_rate`/`cache_hit_rate`/`reasoning_share`/`cost_breakdown` 全中性 |
| `observability/__init__.py` | 44 | 计量/成本/定价中性符号 |
| `infra/llm/transport.py` | 261 | provider usage 字段映射 + `ChatOpenAI` 参数构造，零领域概念 |
| `infra/llm/capability.py` | 393 | 模型能力声明式路由，零领域概念（learn 只需加 `[nodes.*]` 配置块） |
| `infra/llm/client.py` | 796 | `node: str` 是自由字符串，路由在外部 registry；重试/计量/幂等/结构化修复全解耦。⚠️ **797/800 行，物理上不能加代码** |
| `infra/llm/enum_text.py` | 29 | `enum_values(EnumMeta)` —— 整个仓库最干净的模块 |
| `graph/graphs/executor.py` | 98 | state 机械合并 + stage 映射 + ConfirmPoint 翻译，无一处领域语义 |
| `graph/graphs/chapter.py` | 146 | 6 个纯路由 + 图接线 + `needs_c7_confirm` 纯函数 |
| `scorecard/__init__.py` | 83 | 纯 re-export |

### 2.2 函数级复用（learn 逐字可用）

**通用确定性文本度量原语**（`scorecard/literal.py`、`scorecard/structural.py`）：
`norm_whitespace`:215 ｜ `grams`:224 ｜ `intra_redundancy`:236 ｜ `cross_chapter_overlap`:248 ｜
`sentence_length_cv`:461 ｜ `top_repeated_ngrams`:480 ｜ `recurring_ngrams`:581 ｜
`triple_parallelism_count`:283 ｜ `_bigrams`:150 ｜ `_dice`:154 ｜
`_char_ngrams`:110 ｜ `_containment`:131 ｜ `_action_grams`:220 ｜ `_action_containment`:245 ｜
`measure_outline_adherence`:146 ｜ `measure_hook_landed`:194 ｜ `DimensionScore`:140（`earned` 逻辑通用）
> **关键判断**：**度量引擎是 core，指标选择是 story**。二者在代码上已分离，
> 但选择表全是模块级常量、**无注册机制**。

**中文实体名容错匹配工具**（`graph/nodes/skeleton_planner.py`）：
`_lcs_len`、`_name_and_gloss`、`_normalize_for_match`（:142-264）
→ 知识小说的术语匹配（梯度下降/GD/最速下降法）直接可用。

**实体归一**（`graph/nodes/extractors.py:212` `resolve_entities` + `ENTITY_RESOLVER_SYSTEM`:88）
→ 正是知识小说需要的**术语归一**，learn 可直接复用。

**`infra/llm/extraction_schemas.py:307-318` `EntityResolutionOut`**
—— `mentions`/`links`/`new_canonical` 三字段**完全领域中性**。

**`infra/prompts/library.py:388-400` `build_extractor_layers(*, instructions, prose, known="")`**
—— 签名 100% 中性，且**刻意不含圣经**（实测理由：单条 system 超 10,000 字符 → 空输出 100%）。

**`graph/nodes/reviewer.py:259` `apply_revision_if_better`** —— 只比较两个 `Scorecard`，零改动。
**`graph/nodes/chapter_writer.py:56-73/211/224/233/337-388`** —— 拒答检测（6 条中英正则）+ 续写 + `looks_like_restart`，全部体裁无关。
**`graph/graphs/chapter_nodes.py:62-98` `check_readiness`、:401-432 `make_commit_node`**。
**`infra/llm/schemas.py:669-735` `ReviewResultOut`** —— 「issues 非空 ⇒ `revised_content` 非空」+ `has_usable_revision` 是**通用防重写空转闭环**。
**`orchestrator.py` 机制** —— `:220-222` 队列与 `:338-341` worker 均 `for s in PipelineStage` **全量派生**（`:216-219` 明示理由：手写白名单会漏，旧项目 `ACTIVE_PHASES` 漏 `architecting` 致书永久卡死）。learn 只要能提供阶段词表，**协调器一行不改**。

### 2.3 白纸地（无 story 耦合需拆解）

`api/`（2 文件均 0 B）、`infra/retrieval/`（0 B）、`infra/storage/repositories/`（0 B）、`modes/`（0 B）。
其中 **`repositories/` 是 story 与 learn 的共同前置** —— 现状是「13 张表零生产写入路径，只有文件产物」。

---

## 3. story 专属、必须隔离

| 模块 | 行数 | 隔离理由 |
|---|---:|---|
| `graph/nodes/volume_planner.py` | 433 | 文件存在理由 = 「防止困在一处 + 对手阶梯 + 力量上限」。`antagonist`/`power_ceiling`/`escalation_from_prev`/`old_map_closure`/`volume_end_hook` **无一个中性** |
| `graph/nodes/skeleton_planner.py` | 536 | `validate_skeleton` 的 `checks` 元组 `:299-303` 硬编码 core/relationship/opposition 三类线下限；7 个模块级常量 `:109-115` 零注入点；`_arc_core_key` 正则写死中文线名 `:276-278` |
| `graph/nodes/detail_planner.py` | 733 | 伤势机器（`attach_health_beats`:252、`lint_chapter`:313、`format_health_beats`:576、prompt 伤势段:535-543）+ 11 节拍词表 `:76-77` + 场景范式 `:84` + `end_hook` `:93`。**已近 800 行上限** |
| `domain/outline.py` | 433 | `VolumePlan`/`SeriesPlan`/`ChapterPlan`/`SceneBeat`/`Milestone` 全是网文结构 |
| `domain/health.py` | 572 | 伤势模型 + 阈值表 + 恢复排程，全套武侠语义 |
| `domain/schedulers/health_scheduler.py` | 315 | 伤势节拍排程（**且生产未接线**） |
| `domain/validators/structure_gate.py` | 458 | 卷级扩张度闸门 |
| `scorecard/aggregate.py` | 503 | 维度/权重/触发全硬编码在 `:246-367` 与 `:376-437` |
| `infra/llm/schemas.py` | 777 | `BibleOut`/`VolumePlanOut`/`SceneBeatOut`/`ChapterPlanOut` 全 story；**778/800 无法扩展** |
| `infra/prompts/library.py` | 425 | `beat_block`/`health_block`/`build_chapter_layers` 全 story |
| `infra/prompts/*.md` ×3 | 27/28/30 | 写作/审校/抽取 prompt 全 story |

**★ `BeatType.HEAL` / `RECOVERY_PREP` 的判定：story-only，确凿。**
`domain/enums.py:348-351`（`HEAL = "heal"`、`RECOVERY_PREP = "recovery_prep"`）—— 注释自述
「★ 关键新增：`HEAL` / `RECOVERY_PREP`」，存在理由是修复「旧项目 `VALID_BEATS` 无 heal →
恢复不是合法节拍类型 → 伤势恢复率 0%」。整条链是**伤势系统的一部分**：
`BeatType` ↔ `HealthAction` ↔ `InjuryKind` ↔ `HealthScheduler` ↔ `ChapterPlan.health_beats`。
learn 无「伤势」概念 → **不适用，必须隔离**（但因其生产未接线，隔离成本≈0）。

**★ 伤势系统的判定：story-specific，且是「死了的 story-specific」。**
`domain/health.py`(572) + `health_scheduler.py`(315) + `enums.py` 的 `Injury*`/`HealthAction` +
`models.py:432-486` `Injury` 表 + `library.py:239-257/313-320` + `detail_planner.py` 伤势段。
`_BASE_THRESHOLDS`(`health.py:73-107`) 的 kind 值 `伤势/后遗症/心魔/代价/诅咒`
（`enums.py:212-219`）是武侠/仙侠专有。**但生产未接线**（结论③）。

---

## 4. mixed 模块及需要抽出的扩展点

### 4.1 `domain/enums.py`(592) —— 枚举单一来源，rule08 强制
`tests/contract/test_architecture_invariants.py:277` **禁止在 `domain/enums.py` 之外定义任何 `Enum` 子类**。
→ **learn 的新触发词表、新节拍类型、新实体类型必须进这个文件**（learn 无法自带枚举）。
- 中性可复用：`BookMode`/`BookStatus`/`ACTIVE_STATUSES`/`TERMINAL_STATUSES`/`UsageSource`/`CallStatus`/`RunStatus`/`WorkUnitStatus`/`PipelineStage`/`ConfirmPoint`/`GateErrorCode`/`LocationLevel`
- 需 learn 平行：`BeatType`:331、`TriggerReason`:526、`InjuryKind`/`InjurySeverity`/`InjuryStatus`/`HealthAction`、`RelationKind`:271、`EndingPattern`:508
- `EntityKind`:77 需**扩充**：现含 `TECHNIQUE="功法"`（`:90`）、别名表 `宗门`/`门派`/`武学`/`术法`（`:103-121`）；learn 需「概念/定理/公式/人物/学科」等。`normalize_entity_kind`:129 **未知即抛错不降级**（刻意设计）→ learn 不加表就**直接抛错**

### 4.2 `graph/state.py`(263) —— 全书唯一耦合咽喉
`BookState`:53 是 Bible/VolumePlan/Skeleton/Detail/Chapter **5 个 stage state 的唯一基底** —— 改一处命中全部。
- 扩展点：`:53` 拆出 `HierarchyState`（卷/章字段移出公共基底）
- 需下沉：`:153 health_beats_injected`、`:202 is_volume_end`、`:208 injuries_block`、`:216 known_entities`（**读但全书无写入点**）

### 4.3 `infra/prompts/library.py`(425) —— 4 个扩展点
- `:55 PROMPTS_DIR` = `REPO_ROOT/"src"/"storyforge"/"infra"/"prompts"` —— **单一扁平目录，无模式维度**。learn 的 `writer_system.md` 无处安放（会撞名）。**learn 的 P1 单点前提。**
- `:58 _load(name)` → 加 `pack` 维度（现有 3 调用点零改动）。可直接套用 `doc/01-总体架构.md:394-397` 已批准的 `PromptPack` Protocol
- `:290-327 beat_block()` —— 8 键 dict，**`:327 table.get(beat_type, "")` 兜底空串 → learn 传自己的节拍类型会静默无指令**（不报错）
- `:350-385 build_chapter_layers()` —— `:378-384` 七个易变块**固定顺序**（memory/anchor/foreshadow/health/beat/detail/length）。learn 没有 foreshadow/health/beat → 三块恒空，而 learn 真正需要的（知识点/例题/公式）**没有位置**

### 4.4 `scorecard/aggregate.py`(503) —— 无插入点，必须重构或平行实现
- `build_dimensions(lit, st, th)`:235-237 签名只接受两个**具体 dataclass**；维度名+权重硬编码在 `:246-367`
  （10 字面维权重 1.5/1.5/1.0/1.0/1.0/0.8/0.7/1.2/0.5/0.8；4 结构维 2.0/1.2/1.5/0.6）
- `collect_triggers(...)`:376-439 —— 18 个 `TriggerReason` **硬编码逐条 if**，非表驱动
- `evaluate`:447-457 签名**无** `dimensions`/`metrics`/`dimension_builder` 参数
- **权重完全没配置化**（`ScorecardSettings` 只含阈值，无权重）—— 最大缺口
- 扩展点：加 `dimension_spec: Sequence[DimensionSpec] | None = None`；词表参数化成 `StyleTables`

### 4.5 `infra/llm/coercion.py`(183) —— 一个参数化点
- 5 个函数（`:61-174`）完全中性
- **`FIELD_ALIASES`:46-58 的 11 条别名 11/11 全是 story 规划字段**（`volume_no`/`chapter_count`/`target_chapters`/`total_volumes`/`logline`/`new_locations`/`new_factions`/`escalation_from_prev`/`old_map_closure`/`power_ceiling`/`core_conflict`），零注入点
- ⚠️ 该文件 docstring `:3-13` 自称「与具体的 `BibleOut`/`SeriesPlanOut` 定义无关」，**与事实直接矛盾**
- 扩展点：`apply_field_aliases(data, aliases=None)`

### 4.6 `infra/llm/prompt_builder.py`(294) —— 静默失效风险
`:51`/`:61` 两张易变正则表（`_VOLATILE_PATTERNS`/`_SEMI_FORBIDDEN_PATTERNS`）需参数化。
**不做的后果是静默的**：learn 用「第N讲」为单元时 `validate_stability` 既不报错也不告警
→ 缓存逐章 miss 而无人知道 —— 正是该文件 `:107-109` 自述要消灭的失败形态。

### 4.7 `cli.py`(712) / `orchestrator.py`(414) / `models.py`(728)
- `cli.py`：`plan`/`write` 加 mode 分派（`:399-406`、`:519-525` 硬编码 story 图）；`baseline`/`score` learn 独立实现
- `orchestrator.py`：机制零改动；阶段词表经 `modes/` 注入
- `models.py`：`Book`/`Run`/`WorkUnit`/`LLMCall`/`Entity`/`Event`/`EntityState` 复用；`Volume` 升级台阶、`Chapter.beat_type` 需 mode 化

---

## 5. ★ 硬编码的 story 假设清单

### 5.1 会**直接崩溃**的硬闸门（learn 开跑即死）

```
infra/llm/schemas.py:203-208 — `if not (self.era or "").strip(): raise ValueError("字段 era 为空 …请确保 era 填上时代与地域背景")` — ★★ 学习型内容的「知识框架」没有"时代与地域"；era 空则**整本书圣经生成失败**。learn 一号阻塞点
infra/llm/schemas.py:346-402 VolumePlanOut — `new_locations`(:369「本卷**新开地图**」)/`new_factions`(:372「新登场**势力**」)/`antagonist`(:375「主要**对手**」)/`power_ceiling`(:376「**力量上限**」)/`escalation_from_prev`(:378「相对上卷**升级**在哪」)/`old_map_closure`(:385「**旧地图**如何收尾」)/`volume_end_hook`(:389「卷末**钩子**」)；:391 建议 150~250 章；:397-402 `_not_empty` 硬校验「长篇需要**横向扩张**」
graph/nodes/skeleton_planner.py:463 — `if not volume.new_locations or not volume.new_factions: return 失败` — ★ 硬前置：缺这两项直接拒绝生成骨架 → **learn 开箱即崩**
observability/metering.py:182-189 — `unknown = set(self.tokens) - set(CONTEXT_SOURCES)` → `raise ValueError(f"未知的上下文来源 …请加入 CONTEXT_SOURCES")` — ★ CONTEXT_SOURCES(:58-74) 是**封闭元组**；learn 上报 knowledge_cards/research/teaching_ledger 即**运行期崩**
infra/llm/capability.py:190-196 — `if node not in self._node_requirements: raise ProviderConfigError("节点 {node!r} 未在 providers.toml 的 [nodes.*] 中声明")` — learn 每个节点名必须加 providers.toml 块（防呆正确，但必须先做）
```

### 5.2 节拍类型 / 关系种类 / 实体种类（wuxia-or-story flavored）

```
domain/enums.py:331-353 BeatType — `NORMAL/OPENING/TURNING/CLIMAX/BOSS/EMOTION/RECALL/SETTLE/**HEAL**/**RECOVERY_PREP**/TRANSITION` — 叙事节拍；HEAL/RECOVERY_PREP 注释自述「★ 关键新增」（:348/:350），是伤势系统的一部分
domain/enums.py:212-219 InjuryKind — `WOUND="伤势" / SEQUELA="后遗症" / INNER_DEMON="**心魔**" / COST="代价" / CURSE="诅咒"` — 心魔/诅咒是武侠/仙侠专有
domain/enums.py:454-466 HealthAction — `INFLICTED/SEEK_TREATMENT/ACQUIRE_MEDICINE/CONDITION_WORSENS/TREATMENT_ATTEMPT/RECOVERING/HEALED/MADE_PERMANENT` — 疗伤动作序列
domain/enums.py:271-307 RelationKind — `MASTER_APPRENTICE="师徒"/SWORN="**结拜**"/BLOOD="血亲"/COUPLE="**道侣**"/SAME_SECT="**同门**"/MEMBER="归属"/LOYAL="效忠"/DEFECTED="叛变"/WANTED_BY="通缉"/ALLIANCE/VASSAL="附属"/FEUD="**世仇**"/DEBT/OATH="立誓"` — 道侣/同门/世仇是武侠专有
domain/enums.py:90 — `TECHNIQUE = "功法"` — 功法是仙侠专有；:119-121 别名 `"功法"/"武学"/"术法"` → TECHNIQUE
domain/enums.py:103-105 — `"宗门": EntityKind.FACTION, "门派": EntityKind.FACTION, "国家": EntityKind.FACTION` — 宗门/门派
domain/enums.py:536-538 TriggerReason — `GOLDEN_THREE="golden_three"`(黄金三章)/`VOLUME_END`/`CLIMAX_BOSS` — 黄金三章是网文留存策略
domain/enums.py:508-523 EndingPattern — `MID_ACTION/DIALOGUE/OMEN/SUMMARY/TIME_SKIP/OTHER` — 叙事章末分类
domain/enums.py:417-432 ConfirmPoint — `C1_BIBLE/C2_SKELETON_FIRST_BATCH/C3_FIRST_DETAIL/C4_VOLUME_LADDER/C5_GOLDEN_THREE/...` — 全 story 语义
domain/enums.py:407-414 PipelineStage — `BIBLE/VOLUME_PLAN/SKELETON/DETAIL/CHAPTER` — story 五阶段
```

### 5.3 规划节点内嵌的升级流结构

```
graph/nodes/detail_planner.py:76-77 — `- \`beat_type\`：节拍类型。取值 \`normal | opening | turning | climax | boss | emotion | recall | settle | heal | recovery_prep | transition\`` — 11 个节拍类型**字面量重复枚举**在 prompt 文本里（与 enums.py 双份来源）
graph/nodes/detail_planner.py:84 — `- \`scenes\`：**4~8 个**分场景拍点。每个拍点必须四项齐全：\`goal\`…+\`obstacle\`…+\`outcome\`…+\`why_now\`` — 「目标→阻碍→结果」是戏剧场景范式
graph/nodes/detail_planner.py:93 — `- \`end_hook\`：章末钩子 —— 本章现场内**悬而未决的事件本身**` — 章末钩子是网文留存机制
graph/nodes/detail_planner.py:538-542 — `"【伤势节拍·已由调度器排定，必须落到场景里】"` / `"（寻医=求诊场景；求药=取得药物；恶化=伤情发作；疗伤尝试=施治过程；好转=见效；痊愈=明确治愈且有代价）"` — 伤势语义写进 prompt
graph/nodes/skeleton_planner.py:299-303 — `checks` 元组硬编码 core/relationship/opposition 三类线下限 — 无注入点
graph/nodes/skeleton_planner.py:109-115 — 7 个模块级常量（角色数/势力数下限等）— 零注入点
graph/nodes/skeleton_planner.py:276-278 — `_arc_core_key` 正则写死中文线名
graph/nodes/volume_planner.py:279 — `f"【题材】{genre}\n"` / `:278 f"【主题】{topic}\n"` — 直接拼进 prompt，无 mode 分派
```

### 5.4 打分卡：散文专属质量维度

```
scorecard/literal.py:65 — `_DIALOGUE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,})[”\"」』]")` — 引号对白密度；**教学说明文对话占比≈0 → 100% 误杀**
scorecard/literal.py:68-87 — `CLICHES: tuple[str,...] = (` **52 条**，含 `"命运的齿轮"/"破茧成蝶"/"嘴角勾起"/"眼中闪过一丝精光"`；且 **`"综上所述"/"总而言之"` 是教学标准连接词却在此表内**（:86）
scorecard/literal.py:90-95 — `SUMMARY_ENDINGS`（27 条）— 把**总结式收尾列为可疑**，而教学文体必然总结
scorecard/literal.py:99-108 — `TIME_HEAD`(33)/`TIME_TAIL`(12) — 叙事时间跳跃语义，说明文无此概念
scorecard/literal.py:111-117 — `OPEN_TEASER_ENDINGS`（36 条）— 「预兆/无主暗示」是小说钩子概念
scorecard/literal.py:120-129 — `ACTION_TAIL`（**87 个动作动词**）— 小说动作拍收束
scorecard/literal.py:132-135 — `BRIDGE_BREAK_WORDS` — 「章首承接上一章出口状态」是小说连续性要求
scorecard/literal.py:144-147 与 209-212 — `STRONG_EMOTION_WORDS`（**重复定义两次**）— 「情绪直陈」是小说文风问题
scorecard/literal.py:197-206 — `_SIMILE_PATTERNS`（8 条正则）— 比喻在小说是 AI 味，**在科普是教学手段**
scorecard/literal.py:367-431 — `classify_ending(...)` — 六类章末模式全是叙事分类
scorecard/literal.py:492-498 — `starts_with_time_skip(...)` — 判「章首承接」，小说连续性
scorecard/structural.py:44-63 — `_COMMON_NOUN_SUFFIXES`(30)/`_COMMON_NOUNS`(45)/`_BODY_PARTS`(17) — 中式奇幻场景名词（石头/院落/油灯/左肩/太阳穴）
scorecard/structural.py:295-313 — `_PROPER_NOUN_SUFFIXES`(39，含 宗门/城/谷/殿/阁/塔/山/域/岛)/`_SURNAMES`(**219 个中文姓**)
scorecard/structural.py:32 — `_OUTCOME_MARKERS = ("→","->","结果","最后","终于","于是","因而","所以")` — 小说细纲格式（**且是死代码**）
scorecard/aggregate.py:102 — `hard_dialogue_floor: float = 0.05` — 对「对话下限」设**硬闸门**
scorecard/aggregate.py:112 — `hard_min_chars: int = 2500` — 小说章长假设
scorecard/aggregate.py:137 — `always_review_chapters: tuple[int, ...] = (1, 2, 3)` — 黄金三章
scorecard/aggregate.py:403 — `if beat_type in ("climax", "boss"):` — 裸字符串判小说节拍
scorecard/aggregate.py:424-427 — `BRIDGE_BREAK` / `ENDING_OMEN` 触发 — 小说章首承接/章末钩子
config.py:113-133 — `ScorecardSettings` 全 `hard_*`/`target_*` 阈值均按**网文 prose 分布**标定（注释：旧项目书 23 全 47 章实测分位数）
```

> **实测判定（子审计运行时复现）**：把真实中文说明文喂 `evaluate(global_no=5)` →
> `score 0.41 / passed=False / triggers ['dialogue_low','too_short','low_score']`。
> `dialogue_ratio=0.0 < hard_dialogue_floor=0.05` 是**硬触发**，而教学章「不要对话」是正确写法。
> **不是调参问题，是维度集合本身错配。**

### 5.5 prompt 文本：小说框架

```
infra/prompts/writer_system.md:1 — `你是资深网文小说作家。根据本章细纲与记忆上下文写作。` — 角色定义即小说作家
infra/prompts/writer_system.md:6 — `- 伤势：重伤不得生龙活虎，断肢/永久伤残不得凭空痊愈；` — 伤势写进**稳定前缀**（全书不变）
infra/prompts/writer_system.md:8 — `- 力量层次：按世界观设定行事，可旁支/暂时跌落但须有事件支撑；` — 力量层次
infra/prompts/writer_system.md:27 — `6. **只输出小说正文本身**。` — 输出物定义
infra/prompts/style_critic.md:1 — `你是资深网文主编。审校本章正文，找出**具体、可执行**的问题。` — 网文主编
infra/prompts/style_critic.md:7-8 — `1. **AI 味**：套话密集（仿佛/瞬间/不禁/嘴角勾起…）、比喻过多、` — ★ 用户点名的 AI 味/比喻维度
infra/prompts/style_critic.md:10 — `是否有可删的冗余描写、**对话占比是否过低**。` — ★ 对话占比维度
infra/prompts/style_critic.md:11 — `3. **一致性**：是否违反人物状态表（伤势/装备/境界/位置）、` — 境界/伤势
infra/prompts/extract_scene.md:1 — `你是小说记录员。` — 且该文件是**死文件**（见 §5.7）
infra/prompts/library.py:1-35 docstring + :290-327 beat_block — 见 §4.3
graph/nodes/bible_architect.py:45 — `BIBLE_ARCHITECT_SYSTEM = """你是长篇小说的世界观架构师。…` — 世界观架构师
graph/nodes/bible_architect.py:55-58 — `- \`power_system\`：力量/能力体系的**层级与边界**—— 有哪些层次、如何晋升、代价是什么。这是防止后期战力崩坏的关键。` — 力量体系/战力崩坏
graph/nodes/reviewer.py:68 — `"- **一致性**：严格对照上方人物状态表（伤势/装备/境界/位置）改写冲突处；"` — 境界/伤势
```

### 5.6 存储路径 / CLI / 观测

```
infra/storage/models.py:136-149 — `new_locations`(:137)/`new_factions`(:139)/`antagonist`(:141)/`power_ceiling`(:142)/`escalation_from_prev`(:145)/`old_map_closure`(:148)/`volume_end_hook`(:149) — Volume 表整表是升级流结构
infra/storage/models.py:186 — `beat_type: Mapped[str] = mapped_column(String(24), nullable=False, default=BeatType.NORMAL.value)` — 叙事节拍
infra/storage/models.py:188 — `is_transition: Mapped[bool] = …  #: 是否换场章` — 换场
infra/storage/models.py:219 — `一等实体 —— 人 / 势力 / 地点 / 物品 / 功法 / 规则 / 术语。` — 势力/功法
infra/storage/models.py:298 — `#: 当前控制势力（可变 —— 随剧情易主）`
infra/storage/models.py:345-353 — `# ── 张力维度 ──` / `intensity`(:346) / `sentiment`(:347) / `surface_facing`(:351 世人所知) / `true_facing`(:353 真实立场) — 暗线张力
infra/storage/models.py:432-486 — `class Injury(Base)` 整表 — 伤势恢复排程
infra/storage/models.py:541-572 — `class Foreshadowing(Base)` 整表 — 伏笔回收排程
infra/storage/models.py:617 — `#: bible / volume_plan / skeleton / detail / chapter` — WorkUnit.stage 合法值注释写死 story 五阶段
infra/storage/models.py:696 — `#: JSON {bible: N, outline: N, ...}` — 归因示例键
infra/storage/models.py:83 — `mode: Mapped[str] = mapped_column(String(16), nullable=False, default=BookMode.STORY.value)` — ★ 唯一 mode 列；**零读取点**；无索引；裸 String 无 CHECK（复现了本文件 :5 自己批判的「无约束 TEXT 列」缺陷）
config.py:274-275 — `#: 一期只支持 story（决策 D-08）；learn 保留接口但不实现` / `mode: Literal["story", "learn"] = "story"` — ★ 定义存在但**零读取点**
cli.py:617 — `p = argparse.ArgumentParser(prog="storyforge", description="长篇小说生成系统")` — 程序自述
cli.py:647 — `pn.add_argument("--genre", default="suspense", help="题材（默认 suspense）")` — 默认题材「悬疑」
cli.py:92 — `for node in ("scene_extractor", "entity_resolver", "relation_extractor"):` — 抽取节点元组写死 story 三个
cli.py:473 — `print(f"[4/4] 生成本卷细纲（第 1~{end} 章，含伤势节拍注入）…")` — 伤势节拍
cli.py:527 — `bible_ref = f"books/{args.book_id}/bible.md"` — 手工拼路径，**违反 artifacts.py:28「业务代码不得自己拼路径」**
cli.py:536 — `store.read_text(f"books/{args.book_id}/plans/{no:05d}.json")` — 同上
cli.py:310-317 / :329 — 硬编码旧项目书 23 baseline 分位数与章末分布 — story-only
observability/metering.py:58-74 — `CONTEXT_SOURCES` 硬编码 `"bible"(:60) / "volume"(:61) / "outline"(:62) / "relations"(:64) / "injuries"(:65) / "foreshadow"(:66) / "previous_chapter"(:69) / "style"(:71)`
```

### 5.7 ★ 治理问题：prompt 单一来源已复发（learn 写 prompt 前必须先修）

`_load()` 全仓**只有 3 个调用点**（已亲自 grep 确认）：
- `infra/prompts/library.py:369` → `writer_system`
- `graph/nodes/reviewer.py:151` → `style_critic`（经 `STYLE_CRITIC_SYSTEM_FILE`:49）
- `graph/nodes/reviewer.py:218` → `writer_system`

**`extract_scene.md` 无任何调用点 = 死文件。** 线上生效的是
`graph/nodes/extractors.py:56` 的内联常量 `SCENE_EXTRACTOR_SYSTEM`（:196 使用）。
两者已漂移（md 30 行/870 字符 vs 常量 30 行/846 字符）。
→ `library.py` 模块 docstring `:5-12` 声称要消灭的「prompt 散落在内联字符串里」这个病，
**在本仓库已经复发一次**。learn 若直接开写，会得到第 3 处来源。

### 5.8 ★ 纠正：`artifacts.py` 的「story/learn 路径 bug」不存在

`infra/storage/artifacts.py:13` 出现的 `story/books/`、`learn/books/` 位于
**「旧项目缺陷 → 本设计对策」表格的左侧（旧项目缺陷）列** —— 该行恰恰是**声称已修复**。
全仓 grep `story/books|learn/books` 共 12 处命中：11 处在 `doc/`+`tests/` 引述旧项目，
第 12 处即 `artifacts.py:13` 自身。**没有任何一处出现在实际生成的路径里。**
单一路径生成函数 `ArtifactStore._path()`:90-92；9 个 key 构造器（:95/:99/:103/:107/:124/:141/:145/:149/:153）
全部输出 `books/{book_id}/...`。**不要为它排工。**

---

## 6. `modes/` 目录的实际状态

**结论：目录存在，内容为单个 0 字节 `__init__.py`。无 `__pycache__`（从未被 import）。**

```
src/storyforge/modes/
  └── __init__.py    0 bytes    (唯一文件)
      TOTAL ITEMS: 1   TOTAL BYTES: 0
```

**文档承诺 vs 实际（逐条核对）**

| 来源 | 承诺 | 实际 |
|---|---|---|
| `doc/01-总体架构.md:120-123` | `modes/protocol.py`（ModeProtocol）、`modes/story.py`（一期实现）、`modes/learn.py`（占位 NotImplemented） | **三个文件全部 ABSENT** |
| `doc/01-总体架构.md:369-398` | 8 个 Protocol：`LearningMapProvider` / `TeachingLedgerProvider` / `KnowledgeCardProvider` / `ResearchProvider` / `FactVerifier` / `Estimator` / `GenrePlugin` / `PromptPack` | **零实现**（全仓 grep 无任何命中） |
| `doc/00-决策记录.md:92` | 一期只实现 story 所需 Protocol；learn 注册 `NotImplementedMode`，调用即 `raise NotImplementedError` | **未落地** |
| `doc/00-决策记录.md:179-180`（O-02） | learn 专属实体（KnowledgePoint / KnowledgeCard / TeachingLedger）**仅定义 Pydantic 模型与 Protocol，不建表** | **模型与 Protocol 均不存在** |
| `doc/01-总体架构.md:429` | AST 检查器强制「共享代码不得含模式分支」 | 检查器存在（rule03）但**有洞**（见 §0②） |
| `doc/01-总体架构.md:557` | 扩展点：「learn 模式 → `modes/learn.py` 实现 Protocol」 | **无此文件** |

**唯一的引用**：`tests/contract/test_architecture_invariants.py:144`
```python
if "storyforge.modes" in names or "modes" in names:
    continue  # 模式层本身允许
```
—— 这是**白名单豁免**，不是实现。

**旁证：`config/genres/` 同样是空的**（0 个文件）。
`doc/01-总体架构.md:48-52` 声明 `config/genres/{xianxia,suspense,…}.toml`「每题材一文件」，
且 `:554` 列为扩展点「新题材 → `config/genres/*.toml` 数据驱动，加题材不改代码」。
实际：**目录存在但 0 文件**；`config/scales.toml`、`config/thresholds.toml` 也**均不存在**。
`genre` 在代码里是**自由字符串**（`graph/state.py:84`、`cli.py:647` 默认 `"suspense"`、
`models.py:84 String(40)`、直接拼进 prompt）→ **文档承诺的题材扩展点未落地**。
该机制本身是**中性的**（learn 也需要按学科/领域驱动），值得与 learn 一并新建。

---

## 7. 附带发现的缺陷（与 mode 无关，但 learn 会撞上）

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `infra/llm/client.py:662` | `provider: ProviderCapability,` —— **`ProviderCapability` 全仓不存在**（仅此 1 处，无 import；同文件 `:736` 用的是正确的 `ProviderSpec`） | 潜伏 `NameError`，仅因 `:43 from __future__ import annotations` 未爆。**1 行修复** |
| 2 | `graph/graphs/executor.py:62-68` | `_stage_key` 只映射 BIBLE/VOLUME_PLAN/SKELETON/DETAIL，**缺 `PipelineStage.CHAPTER`** | 而 `orchestrator.py:220-222` 为**全部 5 个**枚举值建队列 → 派发 CHAPTER 必抛 `KeyError` |
| 3 | `scorecard/literal.py:612` | `__all__` 含 `"AI_SCENE_ENDINGS"` 但**该名字无定义**（全仓仅此 1 处命中） | `from storyforge.scorecard.literal import *` 抛 `AttributeError`；`scorecard/__init__.py` 用显式 import 侥幸未爆 |
| 4 | `scorecard/literal.py:203` 与 `:205` | 两条 `犹如` 正则**完全相同** | `simile_count` 对「犹如」**重复计数 2 倍** → 抬高 `SIMILE_HEAVY` 误报率 |
| 5 | `scorecard/literal.py:144-147` 与 `:209-212` | `STRONG_EMOTION_WORDS` **重复定义** | 后者覆盖前者 |
| 6 | `scorecard/aggregate.py:465-469` | `analyze_structural(prose, None)` 返回**空白 `StructuralMetrics`（非 None）** | 结构层 4 维 `passed=True/None` **白送 2.1 分**，权重分母 10.0→15.3，门槛实质失效。learn 不传 `ChapterPlan` 时**必须显式传 `st=None`** |
| 7 | `infra/llm/schemas.py:743-752` | `HealthBeatOut` **src 与 tests 双双零引用**（注释自述「不调 LLM」） | 死代码 |
| 8 | `scorecard/structural.py:32` | `_OUTCOME_MARKERS` 死代码 | — |
| 9 | `scorecard/structural.py:256` | `check_state_consistency(prose, plan, ...)` 的 `plan` 参数**函数体内从未使用** | 签名误导 |
| 10 | `config.py:146` | `ngram: int = 4` **从未被传给 `evaluate`** | `analyze` 永远用默认 `n=4`，配置项无效 |
| 11 | `graph/state.py:216` / `chapter_nodes.py:369` | `known_entities` 被读，但**全书无写入点** | 死字段 |
| 12 | `domain/outline.py:248-250` | `plant_foreshadowings`/`resolve_foreshadowings` 渲染+序列化但**无写入方**，prompt 也不要求模型输出 | 死字段 → learn 不必当兼容契约 |

### 规模红线（`tests/contract/test_architecture_invariants.py:324-325`：文件 ≤800 行、函数 ≤200 行）

| 文件 | 行数 | 契约度量 | 距 800 上限 |
|---|---:|---:|---:|
| `infra/llm/client.py` | 796 | 797 | **3 行** |
| `infra/llm/schemas.py` | 777 | 778 | **22 行** |
| `graph/nodes/detail_planner.py` | 733 | 734 | 66 |
| `infra/storage/models.py` | 728 | 729 | 71 |
| `cli.py` | 712 | 713 | 87 |

`client.py:355-539` 的 `complete()` 单函数 **185 行**（上限 200）。
→ **`client.py` 与 `schemas.py` 物理上无法容纳任何 learn 新增代码。**
这把「复用还是并行」从偏好问题变成**硬约束问题**。

---

## 8. 最终归属汇总表

| 归属 | 模块 | learn 的动作 |
|---|---|---|
| **core（零改动）** | `db.py`、`artifacts.py`、`report.py`、`observability/__init__.py`、`transport.py`、`capability.py`(代码)、`client.py`、`enum_text.py`、`executor.py`、`chapter.py`、`scorecard/__init__.py` | **(a) 原样复用，不要碰** |
| **core（函数级）** | `literal.py` 度量原语、`structural.py` 比对原语、`EntityResolutionOut`、`build_extractor_layers`、`apply_revision_if_better`、`resolve_entities`、`check_readiness`、拒答/长度闭环 | **(a) 原样复用** |
| **mixed** | `enums.py`、`entities.py`、`state.py`、`library.py`、`aggregate.py`、`coercion.py`、`prompt_builder.py`、`models.py`、`cli.py`、`orchestrator.py`、`extraction_schemas.py`、`bible_architect.py`、`chapter_writer.py`、`reviewer.py`、`extractors.py`、`continuity.py`、`chapter_nodes.py`、`books.py`、`metering.py` | **(b) 抽扩展点**（§4 逐项） |
| **story-only** | `volume_planner.py`、`skeleton_planner.py`、`detail_planner.py`、`outline.py`、`health.py`、`health_scheduler.py`、`structure_gate.py`、`schemas.py`、`prompts/*.md` | **(c) 隔离 / 平行实现** |
| **empty stub** | `modes/`、`api/`、`api/routes/`、`infra/retrieval/`、`infra/storage/repositories/` | **(c) 白纸新建**（`modes/` 是 learn 的指定扩展点） |

---

## 9. 建议落地顺序

1. **修守卫**（成本最低、收益最高）：`test_architecture_invariants.py:130` 加 `"mode"` + `"learn"`/`"story"` 字面量 + `ast.Compare` 检测；`:140` 扩到 `graph/graphs/`、`infra/`、`cli.py`
2. **`CONTEXT_SOURCES` 扩键**（`metering.py:58-74`）：否则 learn 首次上报归因即崩
3. **建 `modes/` 层**（D-08 的 8 个 Protocol）：`orchestrator.py` 的枚举派生机制天然适配，协调器一行不改
4. **修 `client.py:662` 的 `ProviderCapability`→`ProviderSpec`**（1 行）+ `executor.py:63-68` 补 `CHAPTER` 映射
5. **修 prompt 单一来源**：`extract_scene.md` 死文件 + `PROMPTS_DIR`/`_load` 加 pack 维度
6. **抽 `graph/textmatch.py`**：把 `skeleton_planner` 的中文实体匹配工具下沉
7. **拆 `state.py` 的 `HierarchyState`**：解除 5 个 stage 的唯一基底耦合
8. **`aggregate.py` 加 `dimension_spec` 参数 + 权重配置化**：这是 learn 打分卡的前提
9. **`detail_planner`/`volume_planner` 平行实现**（不要试图参数化）
10. **建 `config/genres/` 加载器**（中性，story/learn 共需）
