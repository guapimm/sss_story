# learn 模式（知识小说）— `infra/llm/` + `infra/prompts/` 耦合审计

审计范围：`src/storyforge/infra/llm/` 全部 9 个文件 + `src/storyforge/infra/prompts/`（`library.py` + 3 个 `.md`）。
全部逐行读过；行号采用 `read` 工具口径。

> **★ 2026-09-24 阅读提示（负责人要求）**：本文多处写"**顶到契约红线**"、
> "**物理约束 797/800，只剩 3 行**"。那是**事实**（规则 10 是硬上限），
> 但**不要**读成"到上限就该拆文件"。
>
> 拆分判据是**耦合**，不是行数 —— 详见 `07-工作方式与目标导向.md` §2.1.1。
> 本文真正的价值是**耦合点清单**：哪段代码带 story 假设、扩展点在哪。
>
> 具体到行动：learn 要加自己的规划 schema 时，正确理由是
> **"它是 learn 的职责，与 story 规划 schema 不同"**（→ 新文件），
> 而不是"because 778/800"。判据不同，结论可能相同 —— 但**理由必须是对的那个**。

---

## 0. 三个先看的事实

### 事实 1：★ 任务书给的行数**全部偏低**，`client.py` 与 `schemas.py` 已顶到契约红线

任务书声明的行数（`client.py 592` / `schemas.py 641` / `capability.py 284` / `coercion.py 131` / `transport.py 188` / `prompt_builder.py 211` / `enum_text.py 14` / `extraction_schemas.py 240`）与实际**无一相符**。实测（`LF` 计数 + 契约测试自己的度量 `text.count("\n")+1`，两种口径互证）：

| 文件 | 实际内容行 | 契约度量行 | 距 800 上限 |
|---|---:|---:|---:|
| `client.py` | 796 | **797** | **3 行** |
| `schemas.py` | 777 | **778** | **22 行** |
| `extraction_schemas.py` | 336 | 337 | 463 |
| `capability.py` | 393 | 394 | 406 |
| `prompt_builder.py` | 294 | 295 | 505 |
| `transport.py` | 261 | 262 | 538 |
| `coercion.py` | 183 | 184 | 616 |
| `enum_text.py` | 29 | 30 | 770 |
| `__init__.py` | 68 | 69 | 731 |
| `prompts/library.py` | 425 | 426 | 374 |

上限来自 `tests/contract/test_architecture_invariants.py:324` `MAX_FILE_LINES = 800`（同处 `MAX_FUNC_LINES = 200`）。

**对 learn 的直接后果（最重要的操作性结论）：**
> `client.py` 只剩 3 行余量、`schemas.py` 只剩 22 行 —— 这两个文件**在物理上无法容纳任何 learn 新增代码**。learn 的模式专属内容**必须**放进新文件，不能"顺手加在现有文件里"。
>
> 另：`client.py:355-539` 的 `complete()` 单函数 **185 行**（上限 200），给它加分支的空间同样接近耗尽。

### 事实 2：★ `extract_scene.md` 是**孤儿文件**，且与线上 prompt **已经漂移**

全仓库 `_load(...)` 调用点只有 3 处（`grep` 全量确认）：

```
src/storyforge/infra/prompts/library.py:369  layers.stable.add("system", _load("writer_system"))
src/storyforge/graph/nodes/reviewer.py:151   system = _load(STYLE_CRITIC_SYSTEM_FILE)   # = "style_critic"
src/storyforge/graph/nodes/reviewer.py:218   system = _load("writer_system")
```

`extract_scene.md` **零调用点**。线上真正生效的是硬编码字符串常量 `graph/nodes/extractors.py:56-85` 的 `SCENE_EXTRACTOR_SYSTEM`。两者**内容已不一致**（逐行 diff 实证）：

| | `extract_scene.md`（30 行 / 870 字符） | `SCENE_EXTRACTOR_SYSTEM`（30 行 / 846 字符） |
|---|---|---|
| 第 2 条 | 「动作属于事件，不属于关系…`relations` 只放**社会关系**（师徒/敌对/血亲/盟友等）」 | 「动作属于事件，不属于关系…（**本节点不抽关系**）」 |
| 第 3 条 | 「区分事实与说法」 | 顺序相反 |
| `deaths` 条 | 「漏记会导致该角色复活的**世界级 Bug**」 | 「**世界级 Bug。**」 |

这正是 `library.py` 模块 docstring（L5-12）自己声明的病：「旧项目 prompt 散落在 6 个模块的 docstring 与内联字符串里」—— **同一个病在本仓库已经复发一次**。而 `doc/01-总体架构.md:88` 仍把 `extract_scene.md` 画成活跃文件。
→ learn 的 prompt 治理**必须**先解决"一个 prompt 只有一个来源"，否则会复制这个 bug。

### 事实 3：★ 契约里承诺的 `modes/` 扩展层**仍是空的**（本节复核，非转述）

- `src/storyforge/modes/__init__.py` —— **0 行，空文件**。
- `doc/01-总体架构.md:369-398` 定义了 8 个 Protocol（含 `PromptPack` 的 `writer_system_extension` / `extractor_instructions` / `critic_instructions`）—— **一个都没实现**。
- `domain/enums.py:27-31` `BookMode` 已定义；`infra/storage/models.py:83` DB 列 `books.mode` 已落地。
- `tests/contract/test_architecture_invariants.py:133-154` 规则 3 只扫 `domain` 与 `graph/nodes` —— **`infra/` 完全不在扫描范围内**。

**对本次审计的含义**：`infra/llm/` 属于**共享基础设施**，learn 要复用它。由于 `modes/` 是空的，且 `infra/` 不受规则 3 约束，learn 的扩展点只能走 **(b) 给这些函数加参数/Spec 对象** —— 不能走"加 if 判模式"（那会把规则 3 的意图破坏在它的盲区里，是最糟的组合）。

---

## 1. 逐文件审计

### 1.1 `infra/llm/__init__.py` — 68 行

**职责**：`infra/llm` 的公开门面 —— 只做 re-export + `__all__`，从 `capability` / `client` / `prompt_builder` 三个子模块聚合导出。

**归属判断：`core`（learn 零改动复用）**

**判断依据**：文件内**没有任何逻辑**，全是 `from ... import ...`（L7-37）与 `__all__`（L39-68）。三个被导入的模块本身已逐一判定（见下）。

**★ 硬编码的 story 假设清单：无。**

**存在的问题（与 learn 相关的两个缺陷）：**

```
src/storyforge/infra/llm/__init__.py:39-68 — `__all__` 缺 `NodeRequirement` — `capability.py:386` 在它自己的 `__all__` 里导出了 `NodeRequirement`，但门面 L7-19 的导入列表里没有它。learn 若要通过门面构造节点能力需求，拿不到这个类。
src/storyforge/infra/llm/__init__.py:20-30 — 门面只导出 `client` / `capability` / `prompt_builder` — `transport.py` 与 `coercion.py` 不在门面内（`coercion` 的 5 个函数被 `schemas.py` 与 `extraction_schemas.py` 直接深路径导入）。learn 侧同样需要深路径导入，**没有统一入口**。
```

**扩展点评估：`(a) 原样复用`** —— learn 不碰。若要让 learn 通过门面拿 `NodeRequirement`，加一行 re-export 即可（1 行，不触发规模上限）。

---

### 1.2 `infra/llm/enum_text.py` — 29 行

**职责**：把 `StrEnum` 渲染成 prompt 可用的取值串 `a | b | c` —— 让 schema 的 `Field(description=...)` 由枚举生成，杜绝"枚举 / schema / prompt 三处漂移"。

**归属判断：`core` —— 本组最干净的文件，learn 逐字复用**

**判断依据**：整个文件只有一个 3 行函数：

```python
def enum_values(enum_cls: EnumMeta) -> str:
    return " | ".join(str(e.value) for e in enum_cls)
```

（`enum_text.py:24-26`）。函数体只依赖 `EnumMeta` 迭代协议，**对枚举内容零假设**。被 `schemas.py:53`（别名 `_enum_values`）与 `extraction_schemas.py:33` 共用，两个调用点分别喂 `BeatType` / `HealthAction` 和 `EntityKind` / `InjuryKind` / `InjurySeverity` / `InjuryStatus` / `RelationKind` / `RelationSentiment`。

**★ 硬编码的 story 假设清单：无。**
唯一含 story 词的是模块 docstring L9-10（「神魂受创（伤势·极重）」）—— 是**历史教训的引用**，不是代码。

**★ 但要注意学习模式真正的耦合点不在这里，而在它的下游：**

```
src/storyforge/infra/llm/enum_text.py:24 — `def enum_values(enum_cls: EnumMeta) -> str:` — 函数本身中性，但它只在 schema 层被调用。learn 要产出"枚举→prompt"同样需要它，这没问题；真正的耦合是**learn 的新枚举值放哪里** —— 见下条。
```

`tests/contract/test_architecture_invariants.py:277-298` 规则 8 禁止在 `domain/enums.py` 之外定义任何 `Enum` 子类。因此 learn 的枚举（如知识点类型、讲解手法）**必须**写进 `domain/enums.py`（592 行，story/learn 共享）—— 这是本审计里**唯一一个"共享文件被两个模式同时增长"的点**，但它不在 `infra/llm/` 内，仅在此备注。

**扩展点评估：`(a) 原样复用`。** learn 零改动。

---

### 1.3 `infra/llm/coercion.py` — 183 行

**职责**：模型输出的**形状归一** —— 把模型自然给出但与 schema 不同形的 JSON 机械转换成规范形状（字段名漂移、字典↔列表、裸值→对象）。

**归属判断：`mixed` —— ★ 这是任务书假设需要修正的一处**

任务书猜测 `coercion.py` 是"genuinely domain-agnostic"。**函数体确实是，但模块级数据表不是。**

**判断依据**：拆开看是两半。

**（甲）4 个函数 —— 完全中性：**

- `first_key(d, *names)`（L61-66）：按候选名取首个非 None 值。
- `apply_field_aliases(data)`（L69-82）：按表补规范名，只补不改。
- `coerce_str_list(v)`（L85-116）：字符串列表的形状归一（顶层 dict → `k——v`；列表内 dict → 单字段取值/多字段拼行）。
- `coerce_records(v, *field_names)`（L144-174）：裸值列表 → 对象列表。
- `_dict_item_to_text(item)`（L119-141）：列表内 dict 项拼行。

这 5 个函数的**算法**不引用任何领域概念，只做 `str`/`dict`/`list` 的机械搬运。

**（乙）`FIELD_ALIASES` —— 11 条全部是 story 词汇：**

```
src/storyforge/infra/llm/coercion.py:46-58 — `FIELD_ALIASES: dict[str, tuple[str, ...]] = {` — ★ 模块级常量，**零注入点**。11 个 key 全部是小说规划层字段：`volume_no` / `chapter_count` / `target_chapters` / `total_volumes` / `logline` / `new_locations` / `new_factions` / `escalation_from_prev` / `old_map_closure` / `power_ceiling` / `core_conflict`（L47-57）。learn 的知识点/课纲字段名（`module_no` / `point_count` / `objectives`…）一个都不在里面，模型一旦漂移就无人接住。
src/storyforge/infra/llm/coercion.py:47 — `"volume_no": ("volume_number", "volume_index", "vol_no", "卷号", "卷序号"),` — 卷号是 story 的层级单位。
src/storyforge/infra/llm/coercion.py:48 — `"chapter_count": ("chapters", "chapter_total", "num_chapters", "章数"),` — 章数。
src/storyforge/infra/llm/coercion.py:49 — `"target_chapters": ("target_total_chapters", "total_chapters", "目标章数"),` — 「全书目标章数」是长篇小说的规模概念。
src/storyforge/infra/llm/coercion.py:50 — `"total_volumes": ("volume_count", "num_volumes", "总卷数"),` — 卷数。
src/storyforge/infra/llm/coercion.py:51 — `"logline": ("summary", "one_liner", "一句话梗概", "简介"),` — ★ logline 是好莱坞编剧术语（一句话故事梗概）。
src/storyforge/infra/llm/coercion.py:52 — `"new_locations": ("locations", "新地图", "新地点"),` — 「新地图」是网文扩张术语。
src/storyforge/infra/llm/coercion.py:53 — `"new_factions": ("factions", "新势力"),` — 「势力」。
src/storyforge/infra/llm/coercion.py:54 — `"escalation_from_prev": ("escalation", "升级点", "上卷升级"),` — ★ 「升级」是升级流小说专属。
src/storyforge/infra/llm/coercion.py:55 — `"old_map_closure": ("map_closure", "旧地图收尾"),` — 「旧地图收尾」。
src/storyforge/infra/llm/coercion.py:56 — `"power_ceiling": ("ceiling", "力量上限"),` — ★ 「力量上限」是战力体系专属。
src/storyforge/infra/llm/coercion.py:57 — `"core_conflict": ("conflict", "核心矛盾"),` — 核心矛盾（叙事）。
```

**（丙）两处较隐蔽的 story 假设：**

```
src/storyforge/infra/llm/coercion.py:136 — `key = first_key(item, "term", "name", "key", "id", "标题", "名称", "规则")` — ★ `"规则"` 被登记为主名候选。这是为 `BibleOut.rules` 的 `{"规则": "…", "例外": "…"}` 实测形状开的特例（见 L93 docstring）——「规则/例外」是世界观设定术语。
src/storyforge/infra/llm/coercion.py:93 — `` `[{"规则": "x", "例外": "y"}]` → `["规则：x；例外：y"]` `` — docstring 里的形状样例是 story 圣经字段。
src/storyforge/infra/llm/coercion.py:99 — `（实测 terms 的天然写法：{"值夜人": "含义", "朱批": "含义"}）` — 「值夜人」「朱批」是具体书的词汇（注释，非代码）。
```

**扩展点评估：`(b) 需要加扩展点` —— 一处，且必须做**

- **`apply_field_aliases`（L69）需要接受别名表参数。** 最小改法：签名改为 `apply_field_aliases(data: Any, aliases: Mapping[str, tuple[str, ...]] | None = None)`，`None` 时回落到 `FIELD_ALIASES`（保持向后兼容，两个现有调用点 `schemas.py:418` / `schemas.py:452` 零改动）。learn 侧传自己的表。
- **`FIELD_ALIASES`（L46）建议拆成 `STORY_FIELD_ALIASES` 并移出 `coercion.py`**（或保留为默认值）。它是**纯数据**，放在通用归一层里是分层错误 —— 与 `coercion.py` docstring L3-13 自称的定位（「与具体的 `BibleOut` / `SeriesPlanOut` 定义无关」）直接矛盾：**11 条别名全部来自这两个 story schema 的字段名**。
- **`_dict_item_to_text`（L119）的候选名（L136-138）也需要能注入**，否则 learn 的 `{"概念": "…", "解释": "…"}` 走不进 `k——v` 分支，会掉到 L141 的 `键：值` 兜底（信息不丢，但形状与 learn 期望不一致）。
- ★ 注意 `coercion.py:3-13` 的 docstring 记录了拆分理由：**`schemas.py` 曾因这层从 640 涨到 802 行撞上限**。现在 `schemas.py` 是 778 行 —— **同一个上限压力已经回来了**。learn 若往 `schemas.py` 加东西必炸（见 1.8）。

---

### 1.4 `infra/llm/transport.py` — 261 行

**职责**：真实 LLM 传输层 —— 把 `LLMClient.transport` 接到 langchain-openai 的 `ChatOpenAI`，并把各家的 usage / 缓存 / reasoning 字段归一成 `(text, usage_dict)`。

**归属判断：`core` —— ★★ 100% 领域无关，learn 绝对不要碰。**

**判断依据**：逐段核对，**没有任何一处引用领域概念**：

1. `MissingAPIKeyError`（L58-63）：key 缺失显式报错。
2. `_coerce_int`（L66-73）：「拿不到就 None，绝不冒充 0」。
3. `extract_usage`（L76-137）：生产商 usage 字段映射 —— `input_tokens` / `output_tokens` / `total_tokens` / `input_token_details.cache_read` / `cache_creation` / `output_token_details.reasoning`（L86-102），以及非标兜底 `prompt_cache_hit_tokens` / `prompt_tokens_details.cached_tokens` / `cache_read_input_tokens` / `completion_tokens_details.reasoning_tokens` / `finish_reason`（L104-135）。**全是 provider 协议字段。**
4. `extract_text`（L140-161）：`AIMessage.content` 从 `str` 或内容块列表归一成纯文本（Anthropic 风格 block）。
5. `LangChainTransport.__init__` / `_build`（L178-216）：按 `(provider, model, params 指纹)` 缓存 `ChatOpenAI` 实例；`enable_thinking` / `reasoning_effort` / `max_tokens` / `response_format={"type":"json_object"}`（L203-212）。
6. `__call__`（L218-243）：组装 system/user 两条消息、`ainvoke`、空输出时带 `finish_reason` 记 warning（L236-242）。
7. `make_transport`（L246-252）。

**★ 硬编码的 story 假设清单：无。**

全部 story 痕迹都在 **docstring / 注释**里，且是**历史证据引用**而非行为：
```
src/storyforge/infra/llm/transport.py:42-43 — `⚠️ 旧项目实测 reasoning / completion 达 99.4%（抽取节点）` — 注释引用旧项目数据。
src/storyforge/infra/llm/transport.py:44 — `· 抽取节点由能力声明**禁止思考**（thinking = "forbidden"）` — 注释描述能力机制，非硬编码。
```
L229-232 的消息构造 `[{"role":"system",...},{"role":"user",...}]` 是**通用 LLM 协议形状**，learn 同样适用。

**扩展点评估：`(a) 原样复用` —— 零改动，不要碰。**

唯一可提的观察：`__call__` 的 `**params`（L225）是自由透传，learn 若需要新 provider 参数（如 `top_p`），只需在 `_build`（L195-212）的 `kwargs` 白名单里加一行 —— 但这是**provider 能力差异，不是模式差异**，与 learn 无关。

---

### 1.5 `infra/llm/capability.py` — 393 行

**职责**：模型能力声明与**能力驱动路由** —— 从 `providers.toml` 加载 provider/model/节点能力需求，按能力匹配选模型，并把"节点需求 + 模型能力"翻译成调用参数。

**归属判断：`core` —— 代码层 100% 领域无关，learn 只需改配置**

**判断依据**：

- `ThinkingMode` / `ReasoningControl` / `CacheMode`（L47-49）：三个字面量类型。
- `ModelCapability`（L61-99）：模型元信息 —— `context_window` / `max_output_tokens` / `supports_structured_output` / `supports_reasoning` / `reasoning_control` / `cache_mode` / `concurrency` / `supports_json_mode` / 价格四件套 / `pricing_currency`。**全是模型属性。**
- `ProviderSpec`（L102-112）：`name` / `base_url` / `api_key_env` + `resolve_api_key()`。
- `NodeRequirement`（L115-129）：`node` / `tier` / `supports_structured_output` / `needs_long_output` / `thinking` / `reasoning_effort` / `extra`。**★ 全中性**，且 L129 已有 `extra: dict[str, Any]` 预留扩展位（注释明说「不参与当前匹配逻辑」）。
- `ModelRegistry`（L141-282）：`requirement_for`（L190）/ `_matches`（L200-216）/ `route`（L218-240）/ `thinking_policy_for`（L242-282）。匹配逻辑全部围绕能力标志。
- `_parse_capability`（L290-316）/ `load_registry`（L319-361）：TOML 解析。
- `get_registry` / `configure_registry`（L368-377）：运行时注入。

**★ 硬编码的 story 假设清单：仅 1 处，且是注释。**

```
src/storyforge/infra/llm/capability.py:46 — `#:   \`required\`  —— 必须想（世界观构建这类需要推理的）。` — 用「世界观构建」举例说明 `ThinkingMode.REQUIRED` 的用途。**仅是 docstring 举例**，不参与任何逻辑；learn 的「知识框架构建」同样需要 `required`。可改为中性措辞，但无功能影响。
```

**★ 真正的耦合在配置，不在代码（这是本文件对 learn 唯一的实操要求）：**

```
src/storyforge/infra/llm/capability.py:190-196 — `def requirement_for(self, node: str) -> NodeRequirement:` 内 `raise ProviderConfigError(f"节点 {node!r} 未在 providers.toml 的 [nodes.*] 中声明能力需求。")` — ★ **未声明即抛错，不静默降级**（这是正确的设计）。实测 `config/providers.toml` 当前只声明了 **10 个节点**：`chapter_writer` / `bible_architect` / `volume_planner` / `skeleton_planner` / `detail_planner` / `scene_extractor` / `entity_resolver` / `relation_extractor` / `style_critic` / `targeted_reviser`。
```

→ **learn 的每一个节点名（如 `knowledge_mapper` / `concept_extractor`…）都必须在 `config/providers.toml` 里加一个 `[nodes.<name>]` 块，否则第一次调用就抛 `ProviderConfigError`。** 这是配置工作量，不是代码改动 —— 但也意味着 learn 的节点命名是**全局配置命名空间**的一部分。

**扩展点评估：`(a) 原样复用` —— 代码零改动；只需新增 `[nodes.*]` 配置块。**

`NodeRequirement.extra`（L129）已经是现成的、无需改代码的扩展位。

---

### 1.6 `infra/llm/client.py` — 796 行（契约度量 797）

**职责**：LLM 客户端 —— 调用、重试退避、结构化输出（JSON 修复 + 校验错误回灌重试）、计量、幂等。

**归属判断：`core` —— ★★ 领域无关，而且**物理上禁止**改动**

**判断依据**：全文按功能段核对，无一处领域语义：

| 段 | 行 | 内容 |
|---|---|---|
| `_BARE_CONTROL_IN_STRING` | L93 | JSON 字符串内裸控制字符映射 |
| `_summarize_validation_error` | L96-132 | Pydantic 错误压成"哪个字段/期望什么/实际给了什么" |
| `_describe_error` | L135-160 | 保证归因文本非空 |
| `_repair_json` | L163-195 | 单遍状态机修字符串内裸换行 |
| `LLMError` / `StructuredOutputError` | L198-203 | 异常类型 |
| `LLMClientProtocol` | L206-226 | `complete` / `complete_structured` 协议 |
| `CallContext` / `CallResult` | L234-259 | 调用元信息与结果 |
| `IdempotencyStore` / `MemoryIdempotencyStore` | L266-287 | 幂等存储 |
| `ClientConfig` | L295-309 | max_attempts / timeout / backoff / structured_retries / 输出预算上下限 |
| `LLMClient.complete` | L355-539 | 重试循环 + 空输出诊断 + 计量 + 幂等 |
| `LLMClient.complete_structured` | L541-598 | 三层防护（json_mode → `_repair_json` → 错误回灌重试） |
| `_extract_json` | L602-627 | 剥代码块、取首 `{` 末 `}` |
| `_is_rate_limit` | L629-632 | 429 判定 |
| `_blank_error` / `_record_failure` / `_timeout_error` | L634-702 | 诊断错误构造 |
| `_sleep_backoff` | L704-713 | 指数退避 |
| `_parse_usage` / `_record` | L715-772 | usage 与记录构造 |
| `blank_rate` / `truncated_count` | L776-783 | 统计 |

**★ 硬编码的 story 假设清单：3 处，其中 2 处是注释、1 处需要留意。**

```
src/storyforge/infra/llm/client.py:246 — `volume_no: int | None = None`（`CallContext` 字段，注释 L245 为「本卷/本段（供缓存分析与调试）」） — ★ 这是**唯一进入数据结构的 story 命名**。它是可选字段，learn 传 `None` 即可运行；但它会被 `_record`（L757-758 附近）写入 `LLMCallRecord`，进而落进 `observability/metering.py:207-216` 的固定列（`book_id` / `global_no` / `node_name`）。learn 的调用记录会永久带一个恒 `None` 的 `volume_no` 列。
src/storyforge/infra/llm/client.py:26 — `章节生成极易超阈值。` — docstring，解释幂等键的动机（引 LangGraph #7417 静默重放）。
src/storyforge/infra/llm/client.py:80 — `{"worldview": "世界观围绕一座档案馆展开。` — docstring，举例说明 JSON 字符串内裸换行。
src/storyforge/infra/llm/client.py:412 — `# 调用方仍可显式覆盖（章节写作会传具体值）。这里只保证` — 注释举例。
```

**★ 另有一处**潜伏 bug**（与本审计范围外但必须报告）：**

```
src/storyforge/infra/llm/client.py:662 — `provider: ProviderCapability,` — ★ `ProviderCapability` 这个类型**在整个仓库不存在**（全量 grep 仅此 1 处命中，无任何 import）。同文件 L736 的 `_record` 用的是正确的 `ProviderSpec`。
```

为什么现在没炸：`client.py:43` 有 `from __future__ import annotations`，注解被惰性化为字符串，永不求值。**但这是 learn 会踩的雷**：任何工具（`typing.get_type_hints` / Pydantic / 未来的运行时类型检查）一求值 `_record_failure` 的注解就会 `NameError`。修法：`ProviderCapability` → `ProviderSpec`（1 行）。

**扩展点评估：`(a) 原样复用` —— ★★ 零改动，且必须零改动。**

三重理由：
1. **物理约束**：797/800 行，只剩 3 行。任何 learn 分支都会立即违反契约规则 10。
2. **函数级约束**：`complete()` 单函数 185 行（上限 200），余量同样见底。
3. **设计上本就无需改**：`node: str`（L357/L545）是**自由字符串**，路由由 `capability.registry` 决定；`CallContext` 的所有 story 字段都是 optional。learn 只要传自己的 `node` 名 + `CallContext(node=..., book_id=..., run_id=...)`，**其余全部原样复用**。

learn 唯一需要接受的不完美：`CallContext.volume_no` 恒 `None`（无害），以及下面这条**范围外但必须一并处理**的计量耦合。

**★ 范围外但被 client.py 直接依赖的耦合（报告给父代理）**：

```
src/storyforge/observability/metering.py:58-74 — `CONTEXT_SOURCES = ("system", "bible", "volume", "outline", "states", "relations", "injuries", "foreshadow", "timeline", "rag", "previous_chapter", "summary", "style", "instruction", "other")` — ★ 15 个上下文来源键里 `bible` / `volume` / `injuries` / `foreshadow` / `previous_chapter` 是 story 专属。learn 的 prompt 分层（知识点／例题／公式）**没有对应键**，`PromptLayers.breakdown()`（`prompt_builder.py:206-212`）会把 learn 的块归到未登记 source 上，`ContextBreakdown` 归因失真。
src/storyforge/observability/metering.py:207-216 — `class LLMCallRecord`` 的 `book_id` / `global_no` / `node_name` 固定列 — learn 的"知识点序号"无处安放。
```

**这不是 `infra/llm/` 的问题，但 `client.py:_record`（L732-772）是唯一往这些列写数据的地方**，learn 上线时必然撞上。

---

### 1.7 `infra/llm/prompt_builder.py` — 294 行

**职责**：Prompt 三层组装（`stable` / `semi` / `volatile`）+ 稳定性校验 + 缓存前缀 hash + 分层 diff —— 缓存命中的核心机制。

**归属判断：`core`（骨架） + 1 处需参数化的 story 假设**

**判断依据**：

- `Chunk`（L73-82）/ `ChunkList`（L232-255）：`(source, text)` 有序容器，**纯结构**。
- `PromptLayers`（L85-229）：`stable` / `semi` / `volatile` 三桶 + `validate_stability` / `stable_text` / `prefix_hash` / `cacheable_prefix` / `assemble` / `breakdown` / `layer_stats`。
- `diff_layers`（L258-284）：逐块比对两次 prompt 的分叉点。
- `PromptLayerError`（L69-70）。

上述全部**模式无关** —— "稳定前缀 + 准稳定层 + 易变尾部"对知识小说**逐字成立**（知识点框架全书不变、模块目标按模块变、上一节回顾按节变）。

**★ 硬编码的 story 假设清单：两个模块级正则表，共 5 条 story 假设。**

```
src/storyforge/infra/llm/prompt_builder.py:54 — `("章号", re.compile(r"第\s*\d+\s*章")),` — ★ 「第N章」被写死为易变特征。learn 若以「第N讲 / 第N课 / 第N节」为单元，此规则**静默失效** → 易变内容混进 stable → 缓存逐章 miss 而无人报警（正是本文件 L107-109 自述要消灭的「命中率卡在 33~44% 且没人知道为什么」）。
src/storyforge/infra/llm/prompt_builder.py:55 — `("卷号", re.compile(r"第\s*\d+\s*卷")),` — ★ 「第N卷」同上；learn 的模块编号格式未知。
src/storyforge/infra/llm/prompt_builder.py:56 — `("字数参数", re.compile(r"字数\s*[:：]?\s*\d+|约\s*\d+\s*字|\d+\s*[~～-]\s*\d+\s*字")),` — 「字」单位；知识小说若用「词/条/个知识点」计数则漏检。
src/storyforge/infra/llm/prompt_builder.py:63 — `("章号", re.compile(r"第\s*\d+\s*章")),`（`_SEMI_FORBIDDEN_PATTERNS` 内） — 同 L54，semi 层禁止。
src/storyforge/infra/llm/prompt_builder.py:64 — `("字数参数", re.compile(r"字数\s*[:：]?\s*\d+|约\s*\d+\s*字|\d+\s*[~～-]\s*\d+\s*字")),` — 同 L56。
src/storyforge/infra/llm/prompt_builder.py:91 — `layers.stable.add("system", "你是资深网文作家...")` — `PromptLayers` docstring 的用法示例，是「网文作家」。**注释，非代码。**
```

**★ 关键机制细节（决定了 learn 的风险等级）：**

```
src/storyforge/infra/llm/prompt_builder.py:104 — `def validate_stability(self, *, strict: bool = True) -> list[str]:` — ★ 默认 `strict=True` 时**抛错**（L145-148）。learn 若把「第N讲」放进 stable，由于 L54-55 的正则只认「章/卷」，**不会抛错，也不会报警** —— 是一个真实的静默失效面。
src/storyforge/infra/llm/prompt_builder.py:184-196 — `def assemble(...)` 内部 L196 `self.validate_stability(strict=True)` — 唯一的强制校验入口，全靠上面两张表。
src/storyforge/infra/llm/prompt_builder.py:51 与 :61 — 两张表都是 `tuple[tuple[str, re.Pattern[str]], ...]` 的**模块级私有常量**（`_VOLATILE_PATTERNS` / `_SEMI_FORBIDDEN_PATTERNS`），**零参数、零注入点**。
src/storyforge/infra/llm/prompt_builder.py:45 — `from storyforge.infra.llm.capability import CacheMode, ModelCapability` — `CacheMode` 只被 `__all__`（L293）重导出，**文件内零使用**（死导入）；`ModelCapability` 在 L172/L184 的类型注解里使用。
```

**扩展点评估：`(b) 需要加扩展点` —— 一处，且是 learn 的**必修项****

- **把两张表参数化到 `validate_stability` / `assemble` 上。** 最小改法：
  ```python
  def validate_stability(self, *, strict: bool = True,
                         patterns: tuple[tuple[str, re.Pattern[str]], ...] | None = None,
                         semi_patterns: ... | None = None) -> list[str]:
  ```
  `None` 时回落到现有常量（现有调用点 `assemble` L196 与 `tests/unit/test_prompt_layers.py` 零改动）。learn 传自己的单元编号/计量单位正则。
- **建议做法更彻底**：定义 `StabilityPatterns` dataclass（`volatile` / `semi_forbidden` 两组），由调用方（未来的 `modes/` 层 PromptPack，见 `doc/01-总体架构.md:394-397`）提供。这样两张表就从"隐含的 story 假设"变成"显式的模式参数"。
- **不要**在 `prompt_builder.py` 里加 `if mode == "learn"` —— 见事实 3：`infra/` 不受规则 3 扫描，加 if 会把规则 3 的意图破坏在它的盲区里。
- 顺手可清：L45 的 `CacheMode` 死导入（若 `__all__` L293 保留，需保留导入；但应注意它与 `__init__.py:31-37` 的门面导出**重复**）。

---

### 1.8 `infra/llm/schemas.py` — 777 行（契约度量 778）

**职责**：LLM 结构化输出的 Pydantic schema（规划侧）—— 圣经 / 卷级台阶 / 全书规划 / 骨架 / 章节细纲 / 审校 / 伤势节拍；并 re-export 抽取侧 schema。

**归属判断：`story-only`（规划侧几乎全部）—— 本组 story 假设最密集的文件**

**判断依据**：这是 `infra/llm/` 里唯一**通篇由叙事领域对象构成**的文件。除了 L628-637 的 re-export 与 L669-735 的审校结果外，每个类的字段名与 `description` 都是小说规划概念。

**★ 硬编码的 story 假设清单（按类分组）：**

**（甲）`BibleOut`（L61-226）—— 世界观圣经，整块 story**

```
src/storyforge/infra/llm/schemas.py:61 — `class BibleOut(BaseModel):` + L62 `"""世界观圣经（对策：L0 稳定前缀，全书不变）。"""` — 「圣经」这个概念本身就是"小说设定集"。learn 需要的是「知识框架」，字段集完全不同。
src/storyforge/infra/llm/schemas.py:65 — `worldview: str = Field(description="世界观设定：世界结构、力量体系、时代背景")` — ★ 「力量体系」是升级流小说专属。
src/storyforge/infra/llm/schemas.py:67 — `power_system: str = Field(default="", description="力量/能力体系说明")` — ★★ 战力体系字段，learn 无对应物。
src/storyforge/infra/llm/schemas.py:68 — `era: str = Field(default="", description="时代与地域背景")` — 「时代与地域」是叙事空间。
src/storyforge/infra/llm/schemas.py:69 — `tone: str = Field(default="", description="整体基调与文风")` — 文风是小说概念（知识小说也有，但优先级不同）。
src/storyforge/infra/llm/schemas.py:191-209 — `def _reject_missing_core_fields(self) -> BibleOut:` 内 L203-208 `if not (self.era or "").strip(): raise ValueError("字段 era 为空 ... 请确保 era 填上时代与地域背景。")` — ★★ **硬闸门**：`era` 为空则**整本书的圣经生成失败**。learn 的知识框架没有"时代与地域"，若不改这里，learn 一开跑就死。这是本文件对 learn 的**第一号阻塞点**。
src/storyforge/infra/llm/schemas.py:222-226 — `_BIBLE_RELOCATABLE: dict[str, str] = {"era": "str", "tone": "str", "terms": "list"}` — ★ 模块级常量，零注入点。登记的是 story 圣经的顶层字段。
```

**（乙）`MilestoneOut`（L294-343）—— 中文网文里程碑写法**

```
src/storyforge/infra/llm/schemas.py:304-307 — `chapter_no: int = Field(description="里程碑在**本卷内**的章序号（从 1 开始，不超过本卷 chapter_count）。")` — ★ 卷/章双层结构。
src/storyforge/infra/llm/schemas.py:337 — `m = _re.search(r"第\s*(\d+)\s*章", data)` — ★★ **硬编码「第N章」正则**，从模型的自然语言里抠章号。
src/storyforge/infra/llm/schemas.py:341 — `desc = _re.sub(r"^第\s*\d+\s*章\s*[:：、.。]?\s*", "", data).strip()` — ★★ 剥离「第N章：」前缀。
src/storyforge/infra/llm/schemas.py:322 — `「第N章：描述」。这不是模型犯错 —— 那是中文小说里表达里程碑最自然的` — 注释自述：这个正则来自"中文小说"的写法。
```

**（丙）`VolumePlanOut`（L346-431）—— 升级台阶，整块 story**

```
src/storyforge/infra/llm/schemas.py:346 — `class VolumePlanOut(BaseModel):` + L347 `"""一卷的**升级台阶**。"""` — ★ 「升级台阶」是网文结构概念。
src/storyforge/infra/llm/schemas.py:369-371 — `new_locations: list[str] = Field(description="本卷新开地图（≥2 个，且不得与既往卷重复）")` — ★★ 「新开地图」。
src/storyforge/infra/llm/schemas.py:372-374 — `new_factions: list[str] = Field(description="本卷新登场势力（≥2 个，且不得与既往卷重复）")` — ★ 「势力」。
src/storyforge/infra/llm/schemas.py:375 — `antagonist: str = Field(description="本卷主要对手（必须与既往卷不同）")` — ★★ 「主要对手」是叙事对抗概念。
src/storyforge/infra/llm/schemas.py:376 — `power_ceiling: str = Field(description="本卷力量/能力上限")` — ★★ 力量上限。
src/storyforge/infra/llm/schemas.py:377 — `core_conflict: str = Field(description="本卷核心矛盾")` — 叙事矛盾。
src/storyforge/infra/llm/schemas.py:378-383 — `escalation_from_prev: str = Field(min_length=8, description="相对上卷「升级在哪」...")` — ★ 升级语义。
src/storyforge/infra/llm/schemas.py:385-388 — `old_map_closure: str = Field(min_length=2, description="旧地图如何收尾（有交代地离开，而非弃置）")` — ★ 「旧地图」。
src/storyforge/infra/llm/schemas.py:389 — `volume_end_hook: str = Field(default="", description="卷末钩子")` — ★ 悬念钩子。
src/storyforge/infra/llm/schemas.py:391 — `chapter_count: int = Field(description="本卷章数（建议 150~250）")` — ★ 网文规模量级（150~250 章/卷）。
src/storyforge/infra/llm/schemas.py:392-395 — `milestones: list[MilestoneOut] = Field(default_factory=list, description="卷内里程碑（间隔不超过 25 章）")` — 章间隔约束。
src/storyforge/infra/llm/schemas.py:397-402 — `def _not_empty(cls, v: list[str]) -> list[str]:` 内 `raise ValueError("必须至少给出 1 项（长篇需要横向扩张，见卷级台阶要求）")` — ★ **硬校验**：「横向扩张」是长篇叙事要求。
```

**（丁）`SeriesPlanOut` / `ArcOut` / `SkeletonOut`（L434-517）**

```
src/storyforge/infra/llm/schemas.py:441 — `logline: str = Field(description="一句话故事梗概")` — ★ 故事梗概。
src/storyforge/infra/llm/schemas.py:444 — `total_volumes: int = Field(description="总卷数")` / L445 `target_chapters: int = Field(description="全书目标章数")` — 卷/章规模。
src/storyforge/infra/llm/schemas.py:473 — `kind: str = Field(description="core（主线）| relationship（关系线）| opposition（对立线）| sub（支线）")` — ★★ **弧线类型硬编码为裸字符串**，不走 `enum_values`。**这违反了本文件自己的"枚举单一来源"信条**（L12-15），且规则 8（`test_architecture_invariants.py:277`）只禁 `Enum` 类定义、**管不到裸字符串列表** → 无测试守卫。learn 想用「概念线/例题线」必须重写这一行，且没有任何注入点。
src/storyforge/infra/llm/schemas.py:483 — `arcs: list[ArcOut] = Field(description="故事线（≥6 条，其中核心线 ≥2、关系线 ≥2、对立线 ≥2）")` — ★ 数量下限写进 description，是 story 结构要求。
src/storyforge/infra/llm/schemas.py:484-487 — `segments: list[str] = Field(default_factory=list, description="本卷分段目标（每段一句，**必须两两不同**）")` — 「卷分段」。
src/storyforge/infra/llm/schemas.py:488 — `characters: list[str] = Field(description="本卷主要角色名（≥3）")` — 角色。
src/storyforge/infra/llm/schemas.py:489 — `factions: list[str] = Field(description="本卷登场势力名（≥4）")` — ★ 势力。
src/storyforge/infra/llm/schemas.py:513-517 — `places: list[str] = Field(default_factory=list, description="本卷用到的主要地点名。**必须包含卷级台阶指定的新地图**...")` — ★ 地点/地图。
```

**（戊）`SceneBeatOut` / `WorldStateOut` / `ChapterPlanOut` / `DetailBatchOut`（L525-614）—— 章节细纲，整块 story**

```
src/storyforge/infra/llm/schemas.py:525 — `class SceneBeatOut(BaseModel):` + L526-531 docstring `「场景/人物：目标→阻碍→结果」的分场景拍点表，双向硬约束` — ★★ 「目标→阻碍→结果」是**戏剧场景结构**。知识小说的一章可能是「定义→定理→证明→例题」。
src/storyforge/infra/llm/schemas.py:533-539 — `goal` / `obstacle` / `outcome` / `why_now` 四字段 + L536-539 `why_now: str = Field(description="为什么是现在 —— 必须由上一行的结果或上一章出口状态逼出，禁止无动机的地点跳转")` — ★ 叙事动机链。
src/storyforge/infra/llm/schemas.py:540 — `location: str = Field(default="", description="场景地点名（若有）")` / L541 `participants: list[str] = Field(default_factory=list, description="在场人物名")` — 场景地点/人物。
src/storyforge/infra/llm/schemas.py:544 — `class WorldStateOut(BaseModel):` + L545 `"""章首/章末世界状态 —— **结构化**（旧项目是自由文本，填充率仅 13%）。"""` — ★ 「世界状态」。
src/storyforge/infra/llm/schemas.py:548 — `story_time: str = Field(default="", description="故事内时间（如「第三日夜」）")` — ★★ 故事内时间，举例「第三日夜」。
src/storyforge/infra/llm/schemas.py:549 — `present: list[str] = Field(default_factory=list, description="在场人物名")` — 人物。
src/storyforge/infra/llm/schemas.py:550-553 — `known: list[str] = Field(default_factory=list, description="本章结束时，**谁知道什么**（用于防止角色知道不该知道的事）")` — ★ 「谁知道什么」是叙事信息差管理。
src/storyforge/infra/llm/schemas.py:554-558 — `open_thread: str = Field(default="", description="章末未收束的事件本身...")` — ★ 未收束事件。
src/storyforge/infra/llm/schemas.py:565 — `plot: str = Field(description="一句话剧情梗概")` — ★★ 剧情。
src/storyforge/infra/llm/schemas.py:566-569 — `beat_type: str = Field(default=BeatType.NORMAL.value, description=f"节拍类型：{_enum_values(BeatType)}")` — ★★ 喂 `BeatType`（11 个值，含 `climax` / `boss` / `heal` / `recovery_prep`）。枚举本身见 `domain/enums.py:331-353`。
src/storyforge/infra/llm/schemas.py:570-578 — `is_transition: bool = Field(default=False, description="本章是否**换场**（地点或时间发生了推移）...")` — ★ 换场/换地图。
src/storyforge/infra/llm/schemas.py:579 — `scenes: list[SceneBeatOut] = Field(description="分场景拍点（4~8 个）")` — 场景数下限。
src/storyforge/infra/llm/schemas.py:580-581 — `entry_state: WorldStateOut` / `exit_state: WorldStateOut` — 章首/章末状态。
src/storyforge/infra/llm/schemas.py:582 — `end_hook: str = Field(description="章末钩子：本章现场内**悬而未决的事件本身**")` — ★★ cliffhanger。
```

**（己）`ReviewIssueOut` / `ReviewResultOut`（L645-735）—— 审校，类别词表 story**

```
src/storyforge/infra/llm/schemas.py:659-663 — `category: str = Field(default="other", description="问题类别：ai_style | consistency | pacing | logic | foreshadow | ooc | outline_deviation | other")` — ★★ **8 个审校类别硬编码为裸字符串**，不走枚举。其中 `foreshadow`（伏笔）、`ooc`（人物崩坏）、`outline_deviation`（细纲偏离）是 story 专属；`ai_style` / `pacing` 是**散文小说文风判据**（见 1.12 `style_critic.md`）。learn 需要完全不同的类别集（如 `factual_error` / `prerequisite_gap` / `example_quality`），而这里**没有任何注入点**。与 L473 `ArcOut.kind` 是同一反模式。
src/storyforge/infra/llm/schemas.py:673-685 docstring — `旧项目实测：Editor 反复要求修改但**从不产出可用的 revised_content**` — 历史证据引用。
src/storyforge/infra/llm/schemas.py:688-692 — `revised_content: str | None` — 语义中性（"完整修订稿"），learn 可复用。
src/storyforge/infra/llm/schemas.py:693 — `score: float = Field(ge=0, le=10, description="质量评分（0~10）")` — 中性。
src/storyforge/infra/llm/schemas.py:723-730 — `def _revised_required_when_issues(self) -> ReviewResultOut:` — **中性的强闭环**（issues 非空 ⇒ revised_content 非空），learn 逐字复用。
src/storyforge/infra/llm/schemas.py:732-735 — `has_usable_revision` property — 中性。
```

**（庚）`HealthBeatOut`（L743-752）—— ★ 伤势，且是**死代码****

```
src/storyforge/infra/llm/schemas.py:739 — `# 伤势节拍（由调度器产出，不调 LLM；此处仅为契约完整性）` — 注释自述「不调 LLM」。
src/storyforge/infra/llm/schemas.py:746 — `injury_name: str = Field(description="损伤名")` — ★★ 伤势。
src/storyforge/infra/llm/schemas.py:747 — `entity: str = Field(description="受伤者")` — ★ 受伤者。
src/storyforge/infra/llm/schemas.py:748 — `action: str = Field(description=f"动作：{_enum_values(HealthAction)}")` — ★★ 喂 `HealthAction`（8 个值：`inflicted` / `seek_treatment` / `acquire_medicine` / `condition_worsens` / `treatment_attempt` / `recovering` / `healed` / `made_permanent`，见 `domain/enums.py:454-466`）。
src/storyforge/infra/llm/schemas.py:750 — `scene_seq: int = Field(default=0, description="指向本章第几个场景（0-based）")` — 场景序号。
```

**★ 实测确认 `HealthBeatOut` 是死代码**：全仓库 `src/` 除定义处外 **0 个引用**，`tests/` 也 **0 个引用**（`grep HealthBeatOut` 仅命中 L743 与 L775）。真正的伤势领域对象是 `domain/health.py` 的 `HealthBeat`（`dataclass`，L305）与 `_ACTION_LABEL` 映射（L456-465）。**learn 可以完全无视这个类**，删掉还能腾 10 行。

**（辛）死导入与 re-export —— 直接影响 learn 的清理前提**

```
src/storyforge/infra/llm/schemas.py:34-44 — `from storyforge.domain.enums import (BeatType, EntityKind, HealthAction, InjuryKind, InjurySeverity, InjuryStatus, LifecycleState, RelationKind, RelationSentiment,)` — ★ 实测只有 `BeatType`（L35，用于 L567-568）与 `HealthAction`（L37，用于 L748）被真正使用；`EntityKind`(L36) / `InjuryKind`(L38) / `InjurySeverity`(L39) / `InjuryStatus`(L40) / `RelationKind`(L42) / `RelationSentiment`(L43) **六个全部是死导入**（仅出现在导入行）。`LifecycleState`(L41) 只用于 L776 的 `__all__` 重导出。
src/storyforge/infra/llm/schemas.py:628-637 — `from storyforge.infra.llm.extraction_schemas import (EntityMentionOut, ..., StateUpdateOut)` — re-export 8 个抽取 schema（注释 L617-627 说明"定义搬家对调用方零影响"）。
src/storyforge/infra/llm/schemas.py:776 — `"LifecycleState",`（`__all__` 内） — 把这个 story 领域枚举从 LLM schema 模块再导出，是无意义的耦合面。
```

**★ `schemas.py` 对 learn 的硬约束（重申事实 1）：778 行 / 800 上限 → 只剩 22 行。**

**扩展点评估：`(c) 需要并行实现 —— 且是本组最贵的**

learn 需要的是一整套**独立的规划 schema 模块**（如 `infra/llm/learn_schemas.py`），理由三条：
1. **物理上放不下**：22 行余量。
2. **语义上不该复用**：`VolumePlanOut` 的 8 个字段里有 7 个（`new_locations`/`new_factions`/`antagonist`/`power_ceiling`/`escalation_from_prev`/`old_map_closure`/`volume_end_hook`）在 learn 里没有对应物，强行复用得靠"全部可选"来绕过，等于把 schema 变成无意义的空壳 —— 而 `schemas.py:584-608` 的注释已经明确否定了这种"把可选字段堆在一起"的做法。
3. **可复用的只有 3 个模式，且都是"做法"不是"代码"**：
   - `_normalize_shape` / `model_validator(mode="before")` 的**归一化手法**（learn 照写）；
   - `ReviewResultOut`（L669-735）的 **issues→revised_content 强闭环**（learn 可原样复制这个类，它几乎不含 story 词汇，只有 `category` 词表 L659-663 要换）；
   - `DetailBatchOut`（L611-614）这种**批次容器**的形状。

**必须改的、且改在 `schemas.py` 内部的一处**（若父代理选择让 learn 复用 `ReviewResultOut`）：
- `ReviewIssueOut.category`（L659-663）的 8 类别 → 需参数化或拆成两个类。这是 learn 复用审校闭环的**唯一阻塞点**。

---

### 1.9 `infra/llm/extraction_schemas.py` — 336 行

**职责**：抽取类输出的 schema —— 「正文 → 结构化事实」这一步的输出形状（实体提及 / 状态变化 / 损伤 / 事件 / 关系 / 场景抽取 / 实体归一）。

**归属判断：`mixed` —— 5 个类 story、2 个类 core**

**判断依据**：拆开逐类看。

**（甲）`EntityMentionOut`（L36-51）—— mixed**

```
src/storyforge/infra/llm/extraction_schemas.py:40-43 — `kind: str = Field(description=f"实体类型：{enum_values(EntityKind)}（**势力**与**组织**都是合法值）")` — ★ 喂 `EntityKind`（`人物/势力/地点/物品/功法/规则/术语/组织/事件/其他`，见 `domain/enums.py:77-95`）。其中 `TECHNIQUE="功法"`、`FACTION="势力"` 是武侠词，且 `_ENTITY_KIND_ALIASES`（`enums.py:100-126`）里还有 `宗门/家族/门派/国家` 映射到 `FACTION`。**但 `术语`/`规则`/`组织` 对知识小说是有意义的** —— 所以是 mixed 而非纯 story。
src/storyforge/infra/llm/extraction_schemas.py:45-50 — `intro_method: str = Field(default="unknown", description="引入方式：self_intro | mentioned_by_other | narrator_intro | appeared_before | baseless_recall | baseless_knowledge | appeared_out_of_nowhere | unknown")` — ★★ **8 个引入方式硬编码为裸字符串**，不走枚举。其中 `baseless_recall`（无依据回忆）、`narrator_intro`（叙述者介绍）、`appeared_out_of_nowhere`（凭空出现）是**叙事引入方式**。知识小说的「术语首次出现是否有定义」是另一回事。与 `ArcOut.kind`(schemas.py:473) / `ReviewIssueOut.category`(schemas.py:659) 同一反模式。
```

**（乙）`StateUpdateOut`（L54-121）—— mixed**

```
src/storyforge/infra/llm/extraction_schemas.py:81-84 — `field: str = Field(default="状态", description="字段名（位置/境界/目标/情绪/装备/性格/立场 等）")` — ★★ `description` 里直接列出「**境界**」—— 修仙/升级流专属术语；「情绪/性格/立场」是人物属性。**但字段本身 `{entity, field, value}` 的形状对 learn 完全成立**（知识点状态：已讲/未讲/难度）。
src/storyforge/infra/llm/extraction_schemas.py:105 — `entity = pick("entity", "person", "name", "who", "实体", "人物")` — ★ 候选名含 `person` / `人物`，预设了"状态变化的载体是人"。
src/storyforge/infra/llm/extraction_schemas.py:108 — `from_v = pick("from", "old", "old_value", "before", "原值")` — 中性。
src/storyforge/infra/llm/extraction_schemas.py:91-121 — `_normalize_shape` 整个归一化器 **算法中性**（把 `{person, attribute, from, to}` 归一到 `{entity, field, value}`），可原样搬到 learn。
```

**（丙）`normalize_record_list`（L124-175）—— core，但**放错位置****

```
src/storyforge/infra/llm/extraction_schemas.py:124 — `def normalize_record_list(v: Any) -> Any:` — ★ 纯形状归一工具（dict 套 dict / dict 值列表 → 记录列表），docstring L127-133 举的四个实测形状（`state_updates`）是 story，但**函数体只做机械搬运**（L155-175）。它本质属于 `coercion.py`（那里已有 `coerce_str_list` / `coerce_records` 三个同类函数），却因为 `schemas.py` 撞了 800 行上限被留在了这里。
```

**（丁）`InjuryUpdateOut`（L178-191）—— ★★ 纯 story**

```
src/storyforge/infra/llm/extraction_schemas.py:178 — `class InjuryUpdateOut(BaseModel):` + L179 `"""损伤变化。"""` — ★★ 整类就是伤势。
src/storyforge/infra/llm/extraction_schemas.py:185 — `entity: str = Field(description="受伤者名字")` — ★ 受伤者。
src/storyforge/infra/llm/extraction_schemas.py:186 — `name: str = Field(description="损伤名（如「左肩剑创」）")` — ★★ 举例「左肩**剑创**」—— 武侠刀剑伤。
src/storyforge/infra/llm/extraction_schemas.py:187-189 — `kind` / `severity` / `status` 分别喂 `InjuryKind`（`伤势/后遗症/心魔/代价/诅咒`，`enums.py:212-219`）、`InjurySeverity`（`轻微/轻/中/重/极重`）、`InjuryStatus`（`新发/恶化/维持/好转/缓解/痊愈/解除/永久·可缓解/绑定`） — ★★ `INNER_DEMON="心魔"` / `CURSE="诅咒"` 是玄幻专属。
src/storyforge/infra/llm/extraction_schemas.py:190 — `recov: str = Field(default="", description="恢复方向（新发/恶化时给）")` — ★ 恢复链。
src/storyforge/infra/llm/extraction_schemas.py:181-182 — docstring `（旧项目规则「同值不覆盖、不推进计时，防慢性损伤无限续期」）` — 伤势机制。
```

**（戊）`EventOut`（L194-239）—— mixed（最接近 core 的抽取类）**

```
src/storyforge/infra/llm/extraction_schemas.py:208 — `story_time: str = Field(default="", description="故事内时间")` — ★ 故事内时间。
src/storyforge/infra/llm/extraction_schemas.py:209 — `location: str = Field(default="", description="地点名")` / L210 `participants: list[str] = Field(default_factory=list, description="参与者名字")` — 地点/参与者。
src/storyforge/infra/llm/extraction_schemas.py:211 — `description: str = Field(description="事件描述")` — ★ 中性。
src/storyforge/infra/llm/extraction_schemas.py:212 — `consequence: str = Field(default="", description="后果/影响")` — ★ 中性。
src/storyforge/infra/llm/extraction_schemas.py:197-206 docstring — `★ 动作（查看/记录/离开/拾取/值守）属于**事件**，不属于关系` — 历史证据引用。
src/storyforge/infra/llm/extraction_schemas.py:226-232 — `_normalize_shape` 的别名对（`time`→`story_time`、`场景`→`location`、`人物`→`participants`） — 别名表 story 化，但算法中性。
```

→ **`EventOut` 对 learn 基本可用**（一节课就是"发生了一件事"），只需 `story_time` 改名或容忍。这是 learn 最可能复用的抽取 schema。

**（己）`RelationUpdateOut`（L242-256）—— ★★ 纯 story**

```
src/storyforge/infra/llm/extraction_schemas.py:242 — `class RelationUpdateOut(BaseModel):` + L243 `"""**社会关系**变化（只抽关系，不抽动作）。"""` — ★★ 社会关系网是叙事专属。
src/storyforge/infra/llm/extraction_schemas.py:245-246 — `from_entity: str = Field(description="关系主体名")` / `to_entity: str = Field(description="关系客体名")` — 关系二元组。
src/storyforge/infra/llm/extraction_schemas.py:247 — `kind: str = Field(description=f"关系类型：{enum_values(RelationKind)}")` — ★★ 喂 `RelationKind`（`师徒/结拜/血亲/道侣/盟友/对手/敌对/仇敌/上下级/同门/归属/效忠/叛变/通缉/联盟/附属/世仇/债务/立誓/其他关系`，`enums.py:271-307`）。**`师徒`/`道侣`/`同门` 是武侠词**；整个"关系会演化"的建模前提对知识小说不成立。
src/storyforge/infra/llm/extraction_schemas.py:248 — `intensity: int = Field(default=50, ge=0, le=100, description="强度 0~100")` — 关系强度。
src/storyforge/infra/llm/extraction_schemas.py:249-252 — `sentiment: str = Field(default=RelationSentiment.NEUTRAL.value, description=f"情感倾向：{enum_values(RelationSentiment)}")` — ★ 情感倾向。
src/storyforge/infra/llm/extraction_schemas.py:253 — `surface_facing: str = Field(default="", description="世人所知的关系表现")` — ★★ 「**世人所知**」是叙事信息差概念。
src/storyforge/infra/llm/extraction_schemas.py:254 — `true_facing: str = Field(default="", description="真实立场（与表面不同即有暗线张力）")` — ★★ 「**暗线张力**」是编剧术语。
```

**（庚）`SceneExtractionOut`（L259-304）—— story（伤势是一等公民）**

```
src/storyforge/infra/llm/extraction_schemas.py:260 — `"""\`scene_extractor\` 的输出：事件 + 状态 + 损伤。"""` — ★ 损伤。
src/storyforge/infra/llm/extraction_schemas.py:277 — `summary: str = Field(description="本章梗概（80 字内）")` — ★ 本章梗概。
src/storyforge/infra/llm/extraction_schemas.py:278 — `events: list[EventOut] = Field(default_factory=list, description="2~5 条关键事件")` — 可复用。
src/storyforge/infra/llm/extraction_schemas.py:279 — `state_updates: list[StateUpdateOut] = Field(default_factory=list)` — mixed。
src/storyforge/infra/llm/extraction_schemas.py:280 — `injury_updates: list[InjuryUpdateOut] = Field(default_factory=list)` — ★★ 伤势。
src/storyforge/infra/llm/extraction_schemas.py:281 — `deaths: list[str] = Field(default_factory=list, description="本章死亡的角色名")` — ★★ 死亡台账。
src/storyforge/infra/llm/extraction_schemas.py:282-284 — `entities: list[EntityMentionOut]` — mixed。
src/storyforge/infra/llm/extraction_schemas.py:285-288 — `unsupported: list[str] = Field(default_factory=list, description="无引入依据的名词（凭空出现/无依据回忆/无依据知晓）")` — ★ 叙事引入缺陷。
src/storyforge/infra/llm/extraction_schemas.py:297-298 — `for f in ("state_updates", "injury_updates", "events", "entities", "deaths", "unsupported"):` — ★★ **容器归一化的字段清单硬编码**：新增 learn 字段必须改这一行（且它与 Pydantic 字段定义是**两处真相**，L290-304 无机制保证两者同步）。
```

**（辛）`EntityResolutionOut`（L307-318）+ `RelationExtractionOut`（L321-324）**

```
src/storyforge/infra/llm/extraction_schemas.py:307-318 — `class EntityResolutionOut(BaseModel):` 的 `mentions: list[str]` / `links: dict[str, int]` / `new_canonical: dict[str, str]` — ★★ **完全领域中性的三个字段**：「本章出现的实体名 / 提及名→既有实体 ID / 提及名→规范名」。这正是知识小说需要的**术语归一**（「梯度下降」「最速下降法」「GD」应统一为一个规范名）。**learn 零改动复用。**
src/storyforge/infra/llm/extraction_schemas.py:321-324 — `class RelationExtractionOut(BaseModel):` 只有 `relations: list[RelationUpdateOut]` — story。
```

**扩展点评估：`(a) + (b) + (c) 三者都有，按类分**

1. **`EntityResolutionOut`（L307）→ `(a)` learn 零改动复用。** 这是本模块对 learn 最有价值的产出。
2. **`normalize_record_list`（L124）→ `(b)` 应迁到 `coercion.py`。** 它是通用工具，留在抽取 schema 里会让 learn 为了用一个归一化函数而 import 整个 story 抽取模块（连带 `InjuryKind` / `RelationKind` 等 6 个 story 枚举，L25-32）。
3. **`StateUpdateOut`（L54）/ `EventOut`（L194）→ `(b)` 复用主体，参数化 description 词表。** 具体：把 L83 的「位置/境界/目标/情绪/装备/性格/立场」与 L105 的候选名列表提出来。
4. **`InjuryUpdateOut`（L178）/ `RelationUpdateOut`（L242）/ `SceneExtractionOut`（L259）/ `RelationExtractionOut`（L321）→ `(c)` learn 需要自己的平行实现。** 其中 `SceneExtractionOut`（L259）可拆：`summary` + `events` + `entities` + `unsupported` 是骨架（可复用），`injury_updates` + `deaths` 是 story 专属（learn 版删掉即可）。
5. **★ 三张裸字符串枚举表必须处理**（与 `schemas.py` 同病）：`EntityMentionOut.intro_method`（L45-50，8 值）、`StateUpdateOut.field` 的 description（L83）、`_normalize_containers` 的字段清单（L297-298）。

---

### 1.10 `infra/prompts/library.py` — 425 行

**职责**：Prompt 库的**代码侧组装** —— 从 Markdown 加载模板、定义 `ChapterContext`、按缓存三层组装章节写作 prompt 与抽取 prompt。

**归属判断：`story-only`（约 85%）+ `core`（约 15%）—— 本组 learn 改造的主战场**

**判断依据**：32 个顶层符号里，真正中性的是 `_load`、`PROMPTS_DIR`、`build_extractor_layers` 三个。

**★ 硬编码的 story 假设清单（按符号）：**

```
src/storyforge/infra/prompts/library.py:55 — `PROMPTS_DIR = REPO_ROOT / "src" / "storyforge" / "infra" / "prompts"` — ★★ **单一扁平目录，无模式维度**。learn 的 `writer_system.md` 无处安放（会与 story 的撞名）。这是本文件对 learn 的**第一号阻塞点**。
src/storyforge/infra/prompts/library.py:58-65 — `def _load(name: str) -> str:` 内 L60 `p = PROMPTS_DIR / f"{name}.md"` — ★★ 模板名是**自由字符串**，调用方硬写 `"writer_system"`（L369）、`"style_critic"`（`reviewer.py:49`/`:151`）。无模式前缀、无注册表。
src/storyforge/infra/prompts/library.py:79 — `BIBLE_HEADER = "【世界观圣经】"` — ★ 世界观圣经。
src/storyforge/infra/prompts/library.py:82-89 — `def bible_block(bible_text: str) -> str:` — ★ 圣经块。
src/storyforge/infra/prompts/library.py:97-102 — `def volume_block(volume_no: int, title: str, goal: str, escalation: str = "") -> str:` 内 L99 `parts = [f"【本卷】第{volume_no}卷《{title}》", f"卷目标：{goal}"]` + L101 `parts.append(f"本卷升级：{escalation}")` — ★★ 「本卷/卷目标/本卷升级」是升级台阶的渲染。
src/storyforge/infra/prompts/library.py:105-107 — `def segment_block(segment_title: str, segment_goal: str) -> str:` 返回 `f"【本段】{segment_title}\n段落目标：{segment_goal}"` — ★ 「段落」是 story 的中间层级。
src/storyforge/infra/prompts/library.py:115-153 — `class ChapterContext:` — ★★ **硬编码的 story 字段集**（详见下面逐字段）：
src/storyforge/infra/prompts/library.py:120 — `volume_no: int` — ★ 卷号。
src/storyforge/infra/prompts/library.py:123-124 — `plot_detail: str = ""` — ★ 细纲场景拍点。
src/storyforge/infra/prompts/library.py:125 — `end_hook: str = ""` — ★ 章末钩子。
src/storyforge/infra/prompts/library.py:127-129 — `prev_exit_state: str = ""` / `next_entry_state: str = ""` / `next_title: str = ""` — ★ 前后锚点（章间衔接）。
src/storyforge/infra/prompts/library.py:133 — `injuries: str = ""` — ★★ 损伤上下文块。
src/storyforge/infra/prompts/library.py:134-135 — `foreshadow_plant: list[str] = None` / `foreshadow_resolve: list[str] = None` — ★★ 伏笔埋/收台账。
src/storyforge/infra/prompts/library.py:139-140 — `#: 伤势节拍指令（由 HealthScheduler 注入）` / `health_beats: str = ""` — ★★ 伤势节拍。
src/storyforge/infra/prompts/library.py:142 — `beat_type: str = BeatType.NORMAL.value` — ★★ 默认值直接绑 `BeatType`。
src/storyforge/infra/prompts/library.py:144 — `is_transition: bool = False` — ★ 换场标志。
src/storyforge/infra/prompts/library.py:156-201 — `def detail_block(ctx: ChapterContext) -> str:` 内 L172 `parts.append(f"【本章细纲——场景骨架硬约束】\n{ctx.plot_detail}\n")` / L174 `"细纲执行规则（最高优先级，逐行核对）："` — ★★ 细纲最高优先级。
src/storyforge/infra/prompts/library.py:190-191 — `"- 禁止新增细纲之外的重要人物、重大转折、新功法/新设定——" "它们属于剧情级加戏。"` — ★★ 「新**功法**」「剧情级加戏」。
src/storyforge/infra/prompts/library.py:192 — `"（**新地图**不在此列：若本章是换场章或细纲已规划，正常写）"` — ★ 新地图。
src/storyforge/infra/prompts/library.py:198 — `f"禁止自创无主暗示（如莫名的灵光/阴影/波动/人影悄然出现又消失），"` — ★ 叙事钩子判据。
src/storyforge/infra/prompts/library.py:204-236 — `def anchor_block(ctx: ChapterContext) -> str:` 内 L231 `"【前后锚点·章间衔接硬约束】相邻章必须因果相接，禁止地点/状态跳变：\n"` — ★★ 章间衔接。
src/storyforge/infra/prompts/library.py:235 — `"不得从「拒绝见某人」直接跳到「与某人同席交谈」。"` — ★ 具体叙事场景举例。
src/storyforge/infra/prompts/library.py:239-257 — `def health_block(ctx: ChapterContext) -> str:` 内 L253 `"【伤势推进·本章已排入细纲】\n"` / L256 `"不要额外加疗伤桥段"` — ★★ **整个函数就是伤势**。learn 零可用性。
src/storyforge/infra/prompts/library.py:260-266 — `def foreshadow_block(ctx: ChapterContext) -> str:` 内 L263 `"【伏笔任务·本章需埋下】\n- "` / L265 `"【伏笔任务·本章需回收】\n- "` — ★★ 伏笔埋/收。
src/storyforge/infra/prompts/library.py:269-287 — `def memory_block(ctx: ChapterContext) -> str:` 内 L273 `f"【人物状态表·写作时须遵守】\n{ctx.states}"` / L275 `f"【关系网络·出场实体的既有关系，不得违背】\n{ctx.relations}"` / L277 `f"【损伤状态·行为须一致】\n{ctx.injuries}"` / L279 `f"【前情时间线】\n{ctx.timeline}"` / L282 `"【上一章结尾原文——本章开篇必须无缝承接此段的场景、语气与情绪】\n"` — ★★ 五块里三块（人物状态/关系网络/损伤状态）是 story；「上一章结尾原文」预设章与章连续叙事。
src/storyforge/infra/prompts/library.py:290-327 — `def beat_block(beat_type: str) -> str:` — ★★★ **本组最硬的单点硬编码**。一个 8 键的 dict，键是 `BeatType` 的 `.value`，值是**成品 prompt 文本**：
src/storyforge/infra/prompts/library.py:293-295 — `BeatType.CLIMAX.value: ("【💥 本章为爆点章】信息密度拉满：多条伏线在此交汇引爆，...")` — ★★ 爆点章 / 伏线。
src/storyforge/infra/prompts/library.py:297-299 — `BeatType.BOSS.value: ("【⚔️ 本章为 Boss 战/大对决】核心冲突的高潮呈现：局势至少一次颠覆，胜利须付出可见代价。")` — ★★ Boss 战。
src/storyforge/infra/prompts/library.py:301-304 — `BeatType.EMOTION.value: ("【💧 本章为情绪点——事件驱动】...")` — ★ 情绪点。
src/storyforge/infra/prompts/library.py:306-308 — `BeatType.RECALL.value: ("【🕰️ 本章含回忆杀】...")` — ★★ 回忆杀（中文网文圈术语）。
src/storyforge/infra/prompts/library.py:310-311 — `BeatType.TURNING.value: ("【🔄 本章为转折章】...")` — ★ 转折章。
src/storyforge/infra/prompts/library.py:313-315 — `BeatType.HEAL.value: ("【🩹 本章为恢复章】按细纲的伤势节拍写痊愈/解除过程：必须有可见的治疗动作与代价，禁止一句话带过。")` — ★★★ **恢复章 = 伤势痊愈**。
src/storyforge/infra/prompts/library.py:317-319 — `BeatType.RECOVERY_PREP.value: ("【🩹 本章含恢复铺垫】寻医/求药/疗伤尝试——为后续痊愈做铺垫，不必在本章痊愈。")` — ★★★ **寻医/求药/疗伤**。
src/storyforge/infra/prompts/library.py:321-324 — `BeatType.TRANSITION.value: ("【🧭 本章为换场章】可换地图/换时间。...")` — ★ 换地图。
src/storyforge/infra/prompts/library.py:327 — `return table.get(beat_type, "")` — ★★ **兜底是空串**：learn 传自己的节拍类型回来永远是空 → **静默无指令**（不报错）。这是一个静默失效面。
src/storyforge/infra/prompts/library.py:330-342 — `def length_block(min_chars: int, soft_max: int) -> str:` 内 L338 `f"【输出要求】直接输出本章完整正文本身：..."` — ★ 「本章」措辞（算法中性，措辞 story）。
src/storyforge/infra/prompts/library.py:350-385 — `def build_chapter_layers(...) -> PromptLayers:` — ★★★ **learn 必须替换的总装配函数**：
src/storyforge/infra/prompts/library.py:369 — `layers.stable.add("system", _load("writer_system"))` — ★★ 硬编码模板名（无模式维度）。
src/storyforge/infra/prompts/library.py:370 — `layers.stable.add("bible", bible_block(bible_text))` — ★ 圣经进稳定层。
src/storyforge/infra/prompts/library.py:372-374 — `layers.semi.add("volume", volume_block_text)` / `if segment_block_text: layers.semi.add("segment", segment_block_text)` — ★ 卷/段语义。
src/storyforge/infra/prompts/library.py:378-384 — `layers.volatile.add("memory", memory_block(ctx))` / `"anchor"` / `"foreshadow"` / `"health"` / `"beat"` / `"detail"` / `"length"` — ★★★ **7 个易变块的固定顺序与来源名硬编码**。learn 没有 `foreshadow` / `health` / `beat` → 这三块恒空，而 learn 真正需要的（知识点清单/例题/公式）没有位置。
src/storyforge/infra/prompts/library.py:68-69 — `def _enum_list(enum_cls) -> str: return " | ".join(e.value for e in enum_cls)` — ★ **死代码**：全文件零调用（`grep _enum_list` 仅命中 L68 定义处）。它与 `enum_text.py:24` 的 `enum_values` **功能完全重复** —— 正是本模块 docstring 声称要消灭的"同一逻辑两处实现"。
src/storyforge/infra/prompts/library.py:403-425 — `__all__` 内 L419-424 导出 `EntityKind` / `InjuryKind` / `InjurySeverity` / `InjuryStatus` / `RelationKind` / `RelationSentiment` — ★ 这 6 个枚举在 L43-51 被导入，**但全文零使用**（仅出现在导入行与 `__all__`）—— 6 个死导入 + 6 个无意义的公开导出面。
```

**★ 本文件里对 learn 真正 core 的部分：**

```
src/storyforge/infra/prompts/library.py:388-400 — `def build_extractor_layers(*, instructions: str, prose: str, known: str = "") -> PromptLayers:` — ★★★ **签名 100% 模式中性**：任意指令 + 正文 + 已知账本 → 三层 prompt，且**刻意不含圣经**（docstring L391-393 的实测理由：旧项目单条 system 超 10,000 字符 → `entity_classify` 空输出 100%）。**learn 的抽取节点零改动复用这个函数**（配 `EntityResolutionOut`，见 1.9）。
src/storyforge/infra/prompts/library.py:58-65 — `def _load(name: str) -> str:` — 机制中性（缺失抛错，不静默用空串），**只有 `PROMPTS_DIR`（L55）的单目录假设需要扩展**。
```

**扩展点评估：`(b) 需要加扩展点 —— 四处，且是本组改造量最大的文件`**

1. **`PROMPTS_DIR`（L55）+ `_load`（L58）→ 需要模式维度。** 最小改法：`_load(name, *, pack: str = "")` → `PROMPTS_DIR / pack / f"{name}.md"`，`pack=""` 时保持现状（现有 3 个调用点零改动）。更好：定义 `PromptPack` Protocol（`doc/01-总体架构.md:394-397` 已经设计好了 `writer_system_extension` / `extractor_instructions` / `critic_instructions` 三个方法 —— **直接用这个已批准的设计**）。
2. **`beat_block`（L290）→ 必须从函数内 dict 提成可注入的 `Mapping[str, str]`。** 这是本文件最硬的耦合。注意 `tests/unit/test_prompt_layers.py:258-281` 用 8 个 keyword 断言把它钉死了（`climax→爆点章`、`boss→Boss`、`heal→恢复章`…）—— 参数化时要保持 story 默认值不变，否则该测试会红。另建议把 L327 的 `table.get(beat_type, "")` 兜底改为**记录一条 warning**（现在的静默空串会掩盖 learn 的配置漏配）。
3. **`ChapterContext`（L115）+ `build_chapter_layers`（L350）→ learn 需要自己的上下文类型与装配函数。** 建议：抽一个 `LayerSpec` / `PromptPack` 参数（含 `system_template_name` + `semi_builders` + `volatile_builders` 的有序列表），story 传现有 7 块，learn 传自己的（知识点/例题/公式/上一节回顾/字数）。**`build_chapter_layers` 的 3 层装配骨架（L367-385）本身是 core，应保留为共享实现。**
4. **`health_block`（L239）+ `foreshadow_block`（L260）→ 随 story 走。** 这两个函数对 learn 零价值，建议迁到 story 侧（`modes/story/` 或 `infra/prompts/story/`），不要留在共享的 `library.py` 里让 learn import 一个永远不用的函数。
5. **顺手可清（腾出容量）**：L68-69 `_enum_list`（死代码，与 `enum_text.enum_values` 重复）、L43-51 + L419-424 的 6 个死导入/死导出。**`library.py` 是 426 行，离 800 尚远，但清理能减少 learn 的认知负担。**

---

### 1.11 `infra/prompts/writer_system.md` — 27 行

**职责**：章节写作的 system prompt（稳定前缀第一块）—— 「你是资深网文小说作家」+ 6 条硬性要求。

**归属判断：`story-only`（100%）**

**判断依据**：这是**纯粹的文体契约文件**，通篇是小说写作规则。

**★ 硬编码的 story 假设清单：**

```
src/storyforge/infra/prompts/writer_system.md:1 — `你是资深网文小说作家。根据本章细纲与记忆上下文写作。` — ★★ role framing + 「本章细纲」（细纲是 story 规划层产物）。
src/storyforge/infra/prompts/writer_system.md:6 — `- 伤势：重伤不得生龙活虎，断肢/永久伤残不得凭空痊愈；` — ★★★ 伤势一致性。
src/storyforge/infra/prompts/writer_system.md:7 — `- 随身物：不得使用账本未持有且本章无获得过程的物品；` — ★ 物品台账。
src/storyforge/infra/prompts/writer_system.md:8 — `- 力量层次：按世界观设定行事，可旁支/暂时跌落但须有事件支撑；` — ★★ 力量层次（战力体系）。
src/storyforge/infra/prompts/writer_system.md:9 — `- 明暗关系：暗中关系不得让不知情角色在言行中知晓；` — ★★ 明暗关系（信息差）。
src/storyforge/infra/prompts/writer_system.md:10 — `- 辨识度：每个角色的口癖、癖好须独特一致，不得串味；` — ★ 角色辨识度。
src/storyforge/infra/prompts/writer_system.md:13-15 — `2. **名词引入**（违反即重大 Bug）：新的**人物名**必须通过自我介绍、他人提及或叙述者明确介绍引入...地名/物品名/势力名须有到达、获得、展示或叙述者介绍的过程。` — ★★ 人物名/地名/势力名引入规则。
src/storyforge/infra/prompts/writer_system.md:17-19 — `3. **情绪呈现**：强情绪词（悲伤/怒不可遏等）每章不超过 2 次，其余用差异化行为呈现（攥拳、沉默、动作迟缓、声音发紧、手抖、出神）。禁止连续两处用同一动作表现同一情绪。情绪强度须与关系亲疏匹配。` — ★★ 情绪呈现手法（小说文风）。
src/storyforge/infra/prompts/writer_system.md:21-23 — `4. **因果逻辑**：每个事件须能回答「谁、出于什么目的、用了什么手段」；冲突要有利益或情感动机；禁止无解释的巧合救场、无铺垫的突然转折；反派行动须有可理解的利益驱动。` — ★★ 反派/冲突动机。
src/storyforge/infra/prompts/writer_system.md:25 — `5. **世界观**：严格遵守下方世界观圣经的设定与术语，不得与已有情节矛盾。` — ★★ 世界观圣经 + 情节。
src/storyforge/infra/prompts/writer_system.md:27 — `6. **只输出小说正文本身**。不要标题前缀、不要 JSON、不要代码块包裹、不要任何解释。` — ★★ 「小说正文」；但后半句的输出格式约束（无 JSON/无代码块/无解释）**对 learn 同样必要**。
```

**扩展点评估：`(c) learn 需要独立文件` —— 且这是**最低成本**的一项**

- **文件名（`writer_system`）与加载路径（`library.py:369` 硬编码）是唯一的耦合机制** —— 只要按 1.10 的扩展点 1 加了 pack 维度，learn 放一份 `learn/writer_system.md` 即可，**零代码改动**。
- 内容上 learn 需要重写的：L1（role）、L5-11（伤势/力量层次/明暗关系）、L13-15（人物名/势力名）、L21-23（反派）、L25（世界观圣经）、L27 前半（小说正文）。
- **可从 story 版继承的**：L27 后半的输出格式约束、L21 的"因果逻辑"通用部分。

---

### 1.12 `infra/prompts/style_critic.md` — 28 行

**职责**：风格审校节点的 system prompt —— 6 项审查重点（AI 味/节奏/一致性/细纲遵守/因果/章末）+ 修订稿强制约束。

**归属判断：`story-only`（100%）**

**判断依据**：通篇是散文小说的文风与结构判据。

**★ 硬编码的 story 假设清单 —— 本节直接回应任务书点名的「散文专属评分维度」：**

```
src/storyforge/infra/prompts/style_critic.md:1 — `你是资深网文主编。审校本章正文，找出**具体、可执行**的问题。` — ★★ role framing（网文主编）。
src/storyforge/infra/prompts/style_critic.md:7-8 — `1. **AI 味**：套话密集（仿佛/瞬间/不禁/嘴角勾起…）、比喻过多、情绪直接命名而非用行为呈现、句长过于均匀、段落结构模板化。` — ★★★ **任务书点名的三个维度集中在这一条**：「套话密集」（= AI 味）、「**比喻过多**」（metaphor）、「情绪直接命名」、「句长过于均匀」。全部是**散文小说文风判据**。
src/storyforge/infra/prompts/style_critic.md:9-10 — `2. **节奏**：是否注水（同一拍反复复述、画面在动但信息没前进）、是否有可删的冗余描写、**对话占比是否过低**。` — ★★★ **任务书点名的「对话占比」**。知识小说的对话占比通常接近 0，这条对 learn 是**有害的**（会逼模型加对话）。
src/storyforge/infra/prompts/style_critic.md:11-12 — `3. **一致性**：是否违反人物状态表（**伤势**/装备/**境界**/位置）、是否让角色知道不该知道的事、是否违反世界观设定。` — ★★★ 伤势 / 境界 / 世界观三件套。
src/storyforge/infra/prompts/style_critic.md:13-14 — `4. **细纲遵守**：是否落实了细纲的场景与关键转折；章末是否落在规划的钩子上。` — ★★ 细纲/钩子。
src/storyforge/infra/prompts/style_critic.md:15 — `⚠️ **注意**：本章若是换场章，换地图/换时间是**正常**的，不是偏离。` — ★ 换场章/换地图。
src/storyforge/infra/prompts/style_critic.md:16 — `5. **因果**：事件是否有动机、转折是否有铺垫。` — ★ 叙事因果（learn 也有"逻辑链"，但含义不同）。
src/storyforge/infra/prompts/style_critic.md:17-18 — `6. **章末**：是否停在未收束的拍上。禁止「莫名的波动」「暗处的身影」这类无主暗示作结（除非它正是规划的钩子）。` — ★★ 章末钩子/无主暗示。
src/storyforge/infra/prompts/style_critic.md:22-26 — `**若你指出了任何问题（issues 非空），必须同时给出 \`revised_content\` —— 完整的修正后正文全文。**` + `不得只给片段、不得只给修改说明、不得在 notes 里说"已改写"而 revised_content 为空。系统会检查这一点...` — ★ **这部分是 core**：「指出问题必须给完整修订稿，代码会检查」是**通用闭环**，learn 逐字需要。
src/storyforge/infra/prompts/style_critic.md:28 — `` `score` 给 0~10 分。`notes` 是补充说明，**不作为判据**。 `` — 中性。
```

**★ 配套的代码侧 story 假设（在 `reviewer.py`，范围外但必须一并报告）**：`CATEGORY_RULES`（`graph/nodes/reviewer.py:55-84`）把 8 个 issue 类别映射到具体改写指令，其中 `ai_style`（比喻配额/情绪命名）、`pacing`（**对话占比**）、`consistency`（**伤势/装备/境界**）、`foreshadow`（伏笔）、`ooc`（人物崩坏）全是小说判据。而 `scorecard/literal.py` + `aggregate.py` 把「**比喻**」（`simile_count`，`literal.py:451`）、「**套话**」（`find_cliches`，`literal.py:434`）、「**对话占比**」（`dialogue_ratio`，`literal.py:270`）做成了**数值化阈值**（`aggregate.py:101-119`：`hard_dialogue_floor=0.05` / `hard_simile_count=6` / `hard_cliche_hits=8` / `target_dialogue_ratio=0.25`）。**这套打分卡对 learn 直接不可用**（尽管已知 `reviewer.py` 不在本审计范围内，但 `style_critic.md` 是它的 prompt 来源，两者必须同批处理）。

**扩展点评估：`(c) learn 需要独立文件`**

- 与 `writer_system.md` 同理：**文件名 `style_critic` 由 `reviewer.py:49` 的 `STYLE_CRITIC_SYSTEM_FILE` 常量硬编码，经 `_load` 加载** —— 加 pack 维度后 learn 放自己的 `critic.md` 即可。
- 可继承的：L20-26 的修订稿强制约束、L28 的评分/notes 约定。
- **必须重写的：L5-18 全部 6 项审查重点**（尤其删掉「对话占比」与「比喻过多」）。

---

### 1.13 `infra/prompts/extract_scene.md` — 30 行

**职责**：**名义上**是场景抽取节点的 system prompt。

**归属判断：`story-only`，且 ★ **实际是死文件（零调用点，且已与线上 prompt 漂移）****

**判断依据**：见事实 2 —— 全仓库 `_load(...)` 仅 3 处调用，无一指向 `extract_scene`。线上生效的是 `graph/nodes/extractors.py:56-85` 的内联常量 `SCENE_EXTRACTOR_SYSTEM`。

**★ 硬编码的 story 假设清单（内容层面）：**

```
src/storyforge/infra/prompts/extract_scene.md:1 — `你是小说记录员。根据本章正文与当前记忆账本，抽取结构化记忆增量。` — ★★ role framing。
src/storyforge/infra/prompts/extract_scene.md:12 — `3. **区分事实与说法**。角色说的话不一定是世界真相。` — ★ 「角色」概念。
src/storyforge/infra/prompts/extract_scene.md:15 — `- \`summary\`：本章梗概（80 字内）。` — ★ 本章梗概。
src/storyforge/infra/prompts/extract_scene.md:16 — `- \`events\`：2~5 条关键事件，每条含故事内时间、地点、参与者、描述、后果。` — ★ 故事内时间。
src/storyforge/infra/prompts/extract_scene.md:17-20 — `- \`state_updates\`：人物**可变状态**的变化（位置/**境界**/目标/情绪/装备等）。...装备用 \`+物品名\`/\`-物品名\` 表示获得/失去...不变属性（外貌/出身/天赋）严禁出现在这里。` — ★★ 「境界」（修仙）；装备获得/失去。
src/storyforge/infra/prompts/extract_scene.md:21-23 — `- \`injury_updates\`：损伤变化。**必须**给出 severity 与 status。好转/痊愈/解除**必须**能在 \`events\` 里找到对应的治疗事件，否则不要报恢复类状态。` — ★★★ **伤势抽取 + 恢复必须有事件支撑**（整条伤势链的抽取侧）。
src/storyforge/infra/prompts/extract_scene.md:24 — `- \`deaths\`：本章死亡的角色名。漏记会导致该角色复活的世界级 Bug。` — ★★ 死亡台账。
src/storyforge/infra/prompts/extract_scene.md:25-27 — `- \`entities\`：本章出现的实体（人物/势力/地点/物品/**功法**/规则/术语）。\`kind\` 必须用规定的取值。**「势力」与「组织」都是合法值**。` — ★★ 人物/势力/功法。
src/storyforge/infra/prompts/extract_scene.md:28 — `\`intro_method\` 标注引入方式；若是凭空出现或无依据回忆，标 \`appeared_out_of_nowhere\` / \`baseless_recall\` / \`baseless_knowledge\`。` — ★ 叙事引入方式。
src/storyforge/infra/prompts/extract_scene.md:29-30 — `- \`unsupported\`：无引入依据的名词清单（可为空）。**普通名词（石头/院落/老者/灯光）不要列入**。` — ★ 叙事名词引入缺陷。
```

**扩展点评估：`(c) learn 需要独立文件 —— 但先修治理问题`**

- **learn 真正需要的是「知识点抽取」prompt**（这一节讲了哪些概念/公式/事实）。它会是一个**新文件**，与 `extract_scene.md` 无关。
- **★ 但在写 learn 的抽取 prompt 之前，必须先决定 prompt 的单一来源机制**：当前 `extract_scene.md`（孤儿）与 `SCENE_EXTRACTOR_SYSTEM`（线上）**已经漂移**。如果 learn 照抄"内联常量"的做法，就会得到 **3 处来源**。建议：把 `SCENE_EXTRACTOR_SYSTEM` 迁回 `.md`（让 `_load` 生效），并把 `library.py:58-65` 的 `_load` 升级为带 pack 的版本 —— 一次改动同时修好 story 的既有缺陷与 learn 的扩展点。

---

## 2. 汇总：任务书四个"是否纯基础设施"的直接回答

任务书要求"如果是，就大声说"。**结论是 3 是、1 有保留：**

### ✅ `transport.py` —— **是。100% 领域无关。★ 绝对不要碰。**

261 行里没有任何一处引用领域概念。全部内容为：provider usage 字段映射（`extract_usage` L76-137）、消息内容块归一（`extract_text` L140-161）、`ChatOpenAI` 实例缓存与参数构造（L178-216）、空输出 `finish_reason` 告警（L236-242）。**learn 零改动复用。**

### ✅ `capability.py` —— **代码层是。★ 不要碰代码，只需加配置。**

393 行全是模型/provider/TOML 逻辑。唯一的 story 痕迹是 L46 的一句注释举例。**learn 的唯一动作：在 `config/providers.toml` 里为每个 learn 节点新增 `[nodes.<name>]` 块** —— 否则 `requirement_for`（L190-196）会按设计**显式抛错**（这是正确的防呆，不是缺陷）。注意 `NodeRequirement.extra`（L129）已是现成的扩展位。

### ✅ `client.py` —— **是。★ 不要碰，而且物理上不能碰。**

796 行（契约度量 797，上限 800 → **只剩 3 行**）全部是重试/退避/JSON 修复/结构化重试/计量/幂等。`node: str` 是自由字符串，路由在外部的 `registry`；`CallContext` 的 story 字段全部 optional。**learn 传自己的 `node` 名 + `CallContext(node=..., book_id=..., run_id=...)` 即可完整复用。**

两个必须知道的事项：
1. **`client.py:662` 有一个潜伏的 `NameError`** —— `ProviderCapability` 全仓库不存在（应为 `ProviderSpec`）。因 L43 的 `from __future__ import annotations` 而暂未爆发。**1 行修复，建议提前做。**
2. **范围外但被 client 直连**：`observability/metering.py:58-74` 的 `CONTEXT_SOURCES`（含 `bible`/`volume`/`injuries`/`foreshadow`/`previous_chapter`）与 `LLMCallRecord` 的固定列（`:207-216`）是 learn 的上下文归因与调用记录的**真实阻塞点** —— 不在 `infra/llm/` 内，但 learn 上线必然撞上，需另开审计。

### ⚠️ `coercion.py` —— **有保留。函数是，但模块级数据表不是。**

- **函数层（L61-174，5 个函数）：完全领域无关**，learn 零改动复用。
- **`FIELD_ALIASES`（L46-58，11 条）：11/11 全部是 story 规划层字段名**（`volume_no` / `chapter_count` / `target_chapters` / `total_volumes` / `logline` / `new_locations` / `new_factions` / `escalation_from_prev` / `old_map_closure` / `power_ceiling` / `core_conflict`），且是**零注入点的模块级私有常量**。learn 的字段名漂移无人接住。
- 另有 L136 的 `"规则"` 主名候选、L93 的「规则/例外」样例 —— 同为 story 特例。
- **`coercion.py:3-13` 的 docstring 自称「与具体的 `BibleOut` / `SeriesPlanOut` 定义无关」，与事实矛盾** —— 这张表就是从这两个 story schema 的字段名反向抄出来的。
- **需要扩展点**：`apply_field_aliases(data, aliases=None)`（`None` 回落默认，两个现有调用点零改动）。
- ★ 附带警告：`coercion.py:3-13` 记录了 `schemas.py` 曾因这层从 640 涨到 802 行撞上限 —— **现在 `schemas.py` 是 778 行，同一个压力已经回来了。**

---

## 3. 归属汇总表

| 文件 | 行数 | 归属 | learn 动作 |
|---|---:|---|---|
| `llm/__init__.py` | 68 | **core** | (a) 零改动（可选补 `NodeRequirement` re-export） |
| `llm/enum_text.py` | 29 | **core** | (a) 零改动（learn 枚举须入 `domain/enums.py`） |
| `llm/transport.py` | 261 | **core** | (a) 零改动 —— 见 §2 |
| `llm/capability.py` | 393 | **core** | (a) 代码零改动；`providers.toml` 加 `[nodes.*]` 块 |
| `llm/client.py` | 796 | **core** | (a) 零改动，且**物理禁止改动**（797/800） |
| `llm/prompt_builder.py` | 294 | **core** | **(b)** `validate_stability` 的正则表需参数化 |
| `llm/coercion.py` | 183 | **mixed** | **(b)** `apply_field_aliases` 需接受别名表；`FIELD_ALIASES` 拆出 |
| `llm/extraction_schemas.py` | 336 | **mixed** | `(a)` `EntityResolutionOut` 直接复用 / **(b)** `StateUpdateOut`+`EventOut` 参数化 / **(c)** 伤势·关系类重写 |
| `llm/schemas.py` | 777 | **story-only** | **(c)** learn 需独立规划 schema 模块（778/800，只剩 22 行） |
| `prompts/library.py` | 425 | **story-only** | **(b)** 四处：`PROMPTS_DIR`/`_load` 加 pack、`beat_block` 提表、`ChapterContext`+`build_chapter_layers` 参数化、`health_block`/`foreshadow_block` 迁出。`build_extractor_layers` 是 core。 |
| `prompts/writer_system.md` | 27 | **story-only** | **(c)** learn 写自己的（文件名即唯一耦合点） |
| `prompts/style_critic.md` | 28 | **story-only** | **(c)** learn 写自己的（保留修订稿强制约束部分） |
| `prompts/extract_scene.md` | 30 | **story-only / 死文件** | **(c)** 先修"单一来源"治理，再写 learn 的抽取 prompt |

---

## 4. 给父代理的优先级建议（按阻塞强度）

1. **P0 — 三个"不要碰"的红线**：`transport.py` / `capability.py` / `client.py` 代码零改动。`client.py` 是 797/800 行，任何 learn 分支都会立刻让契约规则 10 变红。
2. **P0 — `schemas.py` 必须平行实现**：778/800 行、22 行余量。learn 的规划 schema 必须进新文件（如 `infra/llm/learn_schemas.py`）。
3. **P0 — `client.py:662` 的 `ProviderCapability` → `ProviderSpec`**：1 行，潜伏 `NameError`，先修掉。
4. **P1 — prompt 的单一来源（事实 2）**：`extract_scene.md` 零调用点且已与线上 `SCENE_EXTRACTOR_SYSTEM`（`extractors.py:56-85`）漂移。**在写 learn 的 prompt 之前先修这个**，否则 learn 会复制同一个病。顺带：`library.py:68-69` 的 `_enum_list` 与 `enum_text.enum_values` 是同一逻辑的两份实现（死代码），`library.py:43-51/L419-424` 有 6 个死导入/死导出。
5. **P1 — `PROMPTS_DIR`（`library.py:55`）+ `_load`（`library.py:58`）加 pack 维度**：这是 learn 的 prompt 能否落地的**单点前提**，改动量极小（`_load(name, *, pack="")`），且现有 3 个调用点零改动。可直接套用 `doc/01-总体架构.md:394-397` 已批准的 `PromptPack` Protocol。
6. **P1 — `prompt_builder.py:51/61` 两张易变正则表参数化**：不做的后果是**静默**的 —— learn 若用「第N讲」为单元，`validate_stability`（L104，`strict=True` 才抛错）**不会报错也不会告警**，缓存逐章 miss 而无人知道。这正是该文件 L107-109 自述要消灭的失败形态。
7. **P2 — `coercion.py` 的 `FIELD_ALIASES` 拆表**：11 条 story 字段名 + `apply_field_aliases` 加参数。改动小、收益明确（learn 的字段漂移有地方登记）。
8. **P2 — 两处"裸字符串枚举"反模式**：`schemas.py:473`（`ArcOut.kind` 的 4 个弧线类型）与 `schemas.py:659-663`（`ReviewIssueOut.category` 的 8 个审校类别），以及 `extraction_schemas.py:45-50`（`intro_method` 的 8 个值）。它们**违反了本层的"枚举单一来源"信条**，且规则 8（`test_architecture_invariants.py:277`）只禁 `Enum` 类定义、**管不到裸字符串列表，无测试守卫**。learn 若要换这些词表，没有注入点。
9. **P3 — `extraction_schemas.py:124` 的 `normalize_record_list` 迁到 `coercion.py`**：它是通用形状归一工具，却因 `schemas.py` 撞上限被留在了 story 抽取模块里。learn 为了用它得连带 import 6 个 story 枚举（`extraction_schemas.py:25-32`）。
10. **P3 — 死代码清理**：`schemas.py:743-752` 的 `HealthBeatOut`（src 与 tests **双双零引用**，注释自述"不调 LLM"）；`schemas.py:34-44` 的 6 个死导入；`prompt_builder.py:45` 的 `CacheMode` 死导入。清理能腾出恰好紧缺的行数余量。

**一句话结论**：`infra/llm/` 的**下半层（transport / capability / client）是真正干净的基础设施，一行都不用改**；**上半层（schemas / extraction_schemas / prompts）几乎全是 story**，其中 `schemas.py`（778/800）与 `prompts/library.py` 是 learn 必须平行实现的两个文件；`prompt_builder.py` 与 `coercion.py` 只需各加一个参数化注入点。
