# learn 模式需求盘点 · 基于项目自身文档

> **方法**：只读 `doc/` 与 `src/`、`tests/`。
> **来源文件**：`doc/00-决策记录.md`、`doc/01-总体架构.md`、`doc/research/06-frontend-api-testinfra.md`（§4 / §9.4 / 附录 A）、`doc/06-风险与开放问题.md`、`doc/05-验收标准.md`、`doc/03-分期实施计划.md`、`doc/04-数据模型规格.md`、`doc/research/00-evidence-baseline.md`、`README.md`。
> **口径**：所有"旧项目实现位置"均转引文档给出的 `file:line`（旧项目根 = `E:\AI_project\story_llm`，文档内路径前缀多为 `app/` = `backend/app/`）。未在文档中出现的能力一律不列（不发明）。

---

## A. learn 相对 story 多出的能力（逐条）

来源：`doc/research/06-frontend-api-testinfra.md` §4（标题：**「四、learn 模式功能清单（预留接口依据）」**，`:301-303` — "下面是 learn 相对 story **多出来的全部能力**，逐条给出当前实现位置与建议预留的接口面"）。

| # | 能力名 | 一句话作用 | 旧项目实现位置（文档给出的 file:line） | 文档建议预留的接口面 |
|---|---|---|---|---|
| A1 | **知识账本 Teaching Ledger** | 记录每个知识点「未讲授/已讲授/已复用」并按艾宾浩斯排下次复习章 | 表 `models.py:195` `KnowledgeLedger`（字段 `book_id/point_name/status/taught_ch/review_count/next_review_ch`）；复习调度 `REVIEW_INTERVALS="3,7,15,30"`，查询点 `memory.py:3091-3096`；使用点 30+ 处：`memory.py:1352/2986/3054/3091/3113/3132`、`main.py:500/892/3513`、`continue_story.py:433/438/782/1585/1588`（§4.1 `:305-309`）<br>story 侧空实现：`flowmode/story_flow.py:79-80` `chapter_points()` 恒 `[]`；learn 版 `base.py:51-53` 返回 `plan.get("points", [])` | **`TeachingLedger` 抽象** —— `record_taught(book, chapter, points)` / `due_reviews(book, ch)` / `untaught(book)` / `stats(book)`；story 实现为 NoOp（§4.1 `:312`）<br>§9.4 `:961` 签名：`record_taught / due_reviews / untaught / stats` → story `NullLedger` |
| A2 | **知识点与模块（学习地图）** | 一本书的知识域被切成 Module，每模块含 points 列表，供人勾选 | 表 `models.py` `Module`：`book_id/rank/name/description/difficulty/points_json[{name,summary,difficulty,dependencies}]/est_chapters/selected/kind(core\|extend)`；`Book.modules_json`（学习地图 Top10）、`Book.selected_points_json`（`{模块名:[点名]}`）、`Book.gap_fill_max`（缺口补写）、`Book.volume_domain_max`（续卷新知识域上限）、`Book.profile_options / profile_selected`（宽泛主题的学习画像）<br>选择端点：`main.py:844` `select`（89 行，含超容量/偏薄/拓展域 **3 重软拦截**）<br>story 侧无 Module 数据；`MapView.vue:835` 用 `story_brief.arcs` **合成负 id 的伪模块**来避免"无地图"假象（§4.2 `:314-320`） | **`LearningMap` 抽象** —— `build(book) -> [Module]` / `select(book, ids, points)` / `estimate(...)`；story 实现为 **`StoryArcMap`（剧情线当模块）**（§4.2 `:322`）<br>§9.4 `:960`：`build(book) -> list[Node]`, `select(book, ids, points)`, `estimate(...)` → story `StoryArcMap` |
| A3 | **知识卡（章末知识卡）** | 每章末产出一张知识卡，并注入审校 prompt | 存储 `Chapter.cards`（`models.py:113`，Text，默认 `"[]"`）；生成 `ChapterPostProcessor`（`agents/post_processor.py:214`），learn prompt = 「知识卡+记忆提炼」，story prompt = 「记忆记录员」<br>端点：`POST /books/{id}/cards/backfill`（补全书）、`POST /chapters/{id}/cards/regenerate`（单章重提炼，**story 400**）<br>门面：`flowmode/base.py:207-210` `editor_cards_section(cards)` learn 注入「【章末知识卡】」；`story_flow.py:21-22` 换「【纯小说审校】」<br>story 侧：`story_flow.py:132-133` `backfill_gate` 返回「纯小说没有知识卡」；`pipeline.py:6053` 用它拦截（§4.3 `:326-330`） | **`KnowledgeCardProvider`** —— `extract(chapter_text, points) -> [Card]` / `render_prompt_section(cards)`（§4.3 `:332`）<br>§9.4 `:962`：`extract(text, points) -> list[Card]`, `render_section(cards)` → story `NullCards` |
| A4 | **知识点事实核验（factual verification）** | 对勾选的知识点做事实正确性核验，带缓存 | 缓存 `research_store.py` 的 `VerifyCache`（`verify_cache.json`，实测 **190.4 KB**），键 `sha1(point\|summary)[:20]`，值 `{verdict, note(≤60), updated_at}`；消费在 pipeline 研究阶段 + 续接 `research_extra_topics`<br>校验器 `agents/verifier.py`（164 行）—— **learn/story 共用 agent**，仅切换 system 文本（`verifier_entity_system`）<br>story 侧：`story_flow.py:176-177` `verify_selected` 恒 `None`（§4.4 `:336-339`） | **`FactVerifier`** —— `verify(points) -> [Verdict]` / `cached(points) -> (hits, misses)`（§4.4 `:341`）<br>§9.4 `:964`：`verify(points)`, `cached(points)` → story `NullVerifier` |
| A5 | **联网研究（Tavily）** | 创作前/续接时联网补充主题资料，带缓存与断点续跑 | `app/search/provider.py`（47 行）：`SearchProvider` / `TavilyProvider`（`api.tavily.com/search`）/ `MockSearchProvider`；`get_search_provider()` 按 `settings.TAVILY_API_KEY` 是否为空选择<br>缓存 `research_store.py` 的 `ResearchStore`（`research_cache.json`，实测 **5,576.8 KB**），含 `put_stage/get_partial/clear_checkpoint` 的**分阶段断点续跑**（`{queries/snippets/map/verdicts}`）<br>`estimator.py:30-31`：learn 每章 research 调用 = **1 次**，story = **0 次**（`breakdown[0]["calls"]`，由 `test_flow_isolation.py:112-114` 断言）<br>端点 `POST /books/{id}/continue/research`（**story 400**，`main.py:2815`）<br>`.env` 键：`TAVILY_API_KEY`、`SEARCH_MAX_RESULTS`、`RESEARCH_CACHE_TTL_DAYS`（§4.5 `:345-350`） | **`ResearchProvider`** —— `search(query, max) -> [Snippet]` / `research(topic) -> Result` / `checkpoint(topic)`；story 实现为 NoOp（**省一次 LLM 调用与全部网络依赖**）（§4.5 `:352`）<br>§9.4 `:963`：`search(q, n)`, `research(topic)`, `checkpoint(topic)` → story `NullResearch` |
| A6 | **预估器（estimator）** | 按已勾选模块与档位预估每章 token 与调用次数 | `app/estimator.py`（181 行）：`OUT_TOKENS`（research 3500 / architect 4500 / worldbuilder 3000 / write 3200 / cards 700 / memory 600 / review 900）、`CALLS_PER_CHAPTER = 2.5`；`estimate(selected_modules, scale, quality, overflow, story: bool = False)`<br>**story 差异**：无知识点（不计 `points_count`）、skip 联网研究、不产知识卡<br>`_avg_speed(model)` 走历史 `Metric` 表（`completion_tokens>100`，近 20 条），缺失时降级到档位默认（fast 45 / balanced 22 / premium 35 tok/s），并用 `safe_call` + `log_problem` 保证失败不静默<br>门面：`flowmode/base.py:69-71` learn 版 `estimate(..., story=False)`；`story_flow.py:89-91` story 版 `story=True`（§4.6 `:356-360`） | **保持 `estimate(selected, scale, quality, overflow, mode)` 签名，`mode` 从 bool 改成枚举**（§4.6 `:362`）<br>§9.4 `:965`：`estimate(selection, scale, quality, overflow)` → story `StoryEstimator` |
| A7 | **learn 专属调度钩子（8 个，story 全部空实现）** | 把 learn 独有的调度点从共享 pipeline 里外置 | 来源 `flowmode/story_flow.py:171-201`。文档原话："**这 8 个就是"learn 预留接口"的最小集**"（§4.7 `:364-366`）<br>对应 `pipeline.py` 调用点：`1823`（fast_guard）、`1850`（verify）、`4417/5919`（expand_bg）、`5621`（warn_untaught）、`5653`（deepen）、`5715`（load_untaught）、`6446`（due_reviews）、`8334`（warn_consecutive）（§4.7 `:379`）<br>另见 §3.2 `:197`："这 8 个方法**就是 learn 模式的"接口预留清单"雏形** —— 它们精确划出了 learn 独有的调度钩子" | §9.4 `:969`：**"这 8 个 learn 专属调度钩子在 story 侧应整个不存在（而不是返回 None），由 `NullXxx` 提供 no-op，pipeline 直接调不判空"** |
| A8 | **learn 专属端点（story 直接拒绝或语义为空）** | 一组 learn 才有的 HTTP 端点 | §4.8 `:381-391` 表格：`POST /books/{id}/profile` + `/profile/reset`（学习画像选择；story 无画像，phase 不停在 `profile_ready`）／`POST /books/{id}/select`（勾选知识模块+知识点；story 勾剧情线走伪模块）／`GET /templates`（`templates/scenes.py` **只有 1 个 xianxia 模板**）／`GET /flows?scene=`（`flows.py` 373 行）／`POST /books/{id}/continue/research`（**story 400**）／`POST /chapters/{id}/cards/regenerate`（**story 400**）／`GET /books/{id}/library?type=set`（复用历史知识集/大纲；story 语义为空） | §9.4 `:954` + §5.6 `:528`：**契约测试表驱动化** —— 建 `mode × endpoint → expected(200\|400\|404)` 矩阵，把 §3.3-E 的双向拒绝变成测试（**补齐 `cards/backfill` 的缺口**） |
| A9 | **流派画像与流派的插件化分隔**（learn/story 之外的第二条隔离轴） | 按 mode 取题材插件 | `app/genre_profiles.py`（132 行）：18 个题材默认画像 + 12 个 flow 覆盖，字段 `combat_level / has_realm / win_core / one_shot_ok / pacing / dialogue_floor`<br>`app/genres/`（插件包，5 文件 + 4 个 builtin，约 390 行）：`GenrePlugin` ABC + `GenreManager.get(scene, flow, plugin_mode)`<br>`GenreManager` **按 `plugin_mode`（= learn/story）取插件**（`flowmode/base.py:213-220` `post_processor_hints`）<br>`app/genre_state.py`（159 行）：`SECTION_PATTERNS` 三类题材特定小节（规则怪谈 `rules` / 重生流 `rebirth` / 系统流 `system`），从圣经正则抽结构化状态，独立 `genre_states` 表（§4.9 `:397-400`） | §4.9 `:402`：**"`GenrePlugin.extract_hints()` 已是模式感知的，重构时把 `plugin_mode` 参数换成 `Mode` 枚举即可，不用改结构"**<br>§9.4 `:967`：`extract_hints()`, boss 三态 → story「已实现」 |

### A 表附注（文档明说的 mode 显式化要求）

- §9.1 `:860`：**创建走 `POST /books {mode: "story"|"learn", ...}`，`mode` 是一等字段且不可变**；所有 mode 相关行为由 mode 决定的策略对象承担。
- `doc/04-数据模型规格.md:69`：`books.mode TEXT NOT NULL, -- 'story' | 'learn'（一期只 story）`。

---

## B. D-08 设计的 8 个 Protocol

**来源 1** —— `doc/00-决策记录.md:92`（D-08「影响」栏，逐字引用）：

> 新设计改为 **8 个 Protocol 组合**（`LearningMap` / `TeachingLedger` / `KnowledgeCardProvider` / `ResearchProvider` / `FactVerifier` / `Estimator` / `GenrePlugin` / `PromptPack`）：
> • 一期只实现 story 所需的 Protocol
> • learn 注册为 `NotImplementedMode`，调用即 `raise NotImplementedError`（**比反向空实现安全** —— 空实现会静默返回错误结果）
> • 用 **AST 检查器**把「共享代码不得含模式分支」变成机器可验证约束

**来源 2** —— `doc/01-总体架构.md:369-398`（§4.2 代码块，逐字引用）：

```python
# modes/protocols.py —— 每个 Protocol 职责单一，可独立实现/替换
class LearningMapProvider(Protocol):
    def build_map(self, topic: str) -> LearningMap: ...

class TeachingLedgerProvider(Protocol):
    def record_taught(self, chapter_no: int, points: list[Point]) -> None: ...
    def untaught(self) -> list[Point]: ...

class KnowledgeCardProvider(Protocol):
    def extract_cards(self, prose: str, points: list[Point]) -> list[Card]: ...

class ResearchProvider(Protocol):
    def research(self, topic: str) -> ResearchResult: ...     # 旧项目用 Tavily

class FactVerifier(Protocol):
    def verify(self, claims: list[Claim]) -> list[VerificationResult]: ...

class Estimator(Protocol):
    def estimate(self, scope: Scope, scale: Scale) -> Estimate: ...

class GenrePlugin(Protocol):
    def writer_blocks(self, ctx: Context) -> list[str]: ...
    def extract_hints(self) -> str: ...

class PromptPack(Protocol):
    def writer_system_extension(self) -> str: ...
    def extractor_instructions(self) -> str: ...
    def critic_instructions(self) -> str: ...
```

| # | Protocol 名（D-08 命名） | 02 §4.2 代码块中的类名 | 文档所述职责 |
|---|---|---|---|
| 1 | `LearningMap` | `LearningMapProvider` | 学习地图：`build_map(topic) -> LearningMap`。§9.4 `:960` 补 `build(book) -> list[Node]` / `select(book, ids, points)` / `estimate(...)`，story = `StoryArcMap` |
| 2 | `TeachingLedger` | `TeachingLedgerProvider` | 知识账本：`record_taught(chapter_no, points)` / `untaught() -> list[Point]`。§9.4 `:961` 补 `due_reviews` / `stats`，story = `NullLedger`，learn = `KnowledgeLedger` + 艾宾浩斯 |
| 3 | `KnowledgeCardProvider` | 同名 | 知识卡：`extract_cards(prose, points) -> list[Card]`。§9.4 `:962` 补 `render_section(cards)`，story = `NullCards` |
| 4 | `ResearchProvider` | 同名 | 联网研究：`research(topic) -> ResearchResult`（**注释明写"旧项目用 Tavily"**）。§9.4 `:963` 补 `search(q, n)` / `checkpoint(topic)`，story = `NullResearch` |
| 5 | `FactVerifier` | 同名 | 事实核验：`verify(claims) -> list[VerificationResult]`。§9.4 `:964` 补 `cached(points)`，story = `NullVerifier` |
| 6 | `Estimator` | 同名 | 预估：`estimate(scope, scale) -> Estimate`。§9.4 `:965`：story 公式**无 points/research/cards**；learn 公式**含 points_count**，story = `StoryEstimator` |
| 7 | `GenrePlugin` | 同名 | 流派插件：`writer_blocks(ctx) -> list[str]` / `extract_hints() -> str`。§9.4 `:967`：`extract_hints()`, boss 三态；story/learn **均已实现** |
| 8 | `PromptPack` | 同名 | 提示词整包：`writer_system_extension()` / `extractor_instructions()` / `critic_instructions()`。§9.4 `:966`：**"今天 `FlowMode` 的 37 个方法"收敛到这里**，story = `StoryPrompts`，learn = `LearnPrompts` |

**组合方式**（`doc/01-总体架构.md:400-421`，逐字引用）：

> **模式 = 这些 Protocol 的组合**
> ```python
> # modes/story.py —— 一期唯一实现
> class StoryMode:
>     """story 只需 PromptPack + GenrePlugin + Estimator；
>     learn 专属的 5 个 Provider 不实现。"""
>     def __init__(self, ...):
>         self.prompts: PromptPack = StoryPromptPack()
>         self.genre: GenrePlugin = GenrePluginImpl()
>         self.estimator: Estimator = StoryEstimator()
>
> # modes/learn.py —— 一期显式占位
> class LearnMode:
>     """一期不做。调用即抛错，不静默返回错误结果。"""
>     @property
>     def learning_map(self) -> LearningMapProvider:
>         raise NotImplementedError(
>             "learn 模式尚未实现（决策 D-08：一期只做 story）。"
>             "若需启用，请实现 LearningMapProvider 等 5 个 Protocol。"
>         )
> ```

> **⚠️ 文档内部不一致（如实标注）**：`01-总体架构.md:121` 与 `:370` 的文件名是 `modes/protocol.py` / `modes/protocols.py`（两处写法不同）；`:122` 写 `story.py`；`:123` 写 `learn.py`。而 §9.4 `:932-947` 给的是 `@dataclass(frozen=True) class ModeCapabilities` + `MODES = {"story": ..., "learn": ...}` 注册表形式。**同一件事在文档里有两种表述（Protocol 集合 vs ModeCapabilities dataclass），未统一。**

---

## C. 文档明确指出的「旧项目模式层设计缺陷」

`doc/01-总体架构.md:359-365`（§4.1「为什么不用继承树」）**明说旧项目 `flowmode/` 的思路（把模式差异外置）是"正确的"，但形态有三个问题**：

| # | 缺陷 | 影响 | 引用证据 | 文档行号 |
|---|---|---|---|---|
| C1 | **上帝接口** | 37 方法基类 + 48 处调用点，任何新模式都必须实现全部方法 | "`FlowMode` 基类 **37 个方法**，`_flow(book).` 调用点 **48 处**（pipeline 46 + bible_rebuild 3）" | `01-总体架构.md:363` |
| C2 | **方向反了** | **story 侧给 learn 钩子写空实现**（应由 learn 侧为未用能力占位） | "现在是 **story 侧给 learn 钩子写空实现**（`story_flow.py:171-201` 有 8 个 `@staticmethod` 空实现）。应该是 learn 侧为 story 未用到的能力占位" | `01-总体架构.md:364`；同文 `00-决策记录.md:92` |
| C3 | **门面外仍有直读** | 门面建成但**被绕过**，规约 15 靠人盯 | "门面之外仍有 **30+ 处**直接判 `book_kind`：`main.py:1304` 直取 `_is_story`（路由层绕过门面）、`main.py` 7 处 `kind=="story"`、`drift.py` 4 处 `is_story` 形参、`architect.py:2895/2916` 裸 `_flow.kind`、`continue_story.py` 6 处 `book.book_kind`" | `01-总体架构.md:365` |
| C4 | **空实现静默返回错误结果** | 比抛错更危险：错误结果不报错 | "空实现静默返回错误结果"→ 新设计"抛错并附「如何实现」的提示" | `01-总体架构.md:426-429` |

**逐条 `file:line` 证据（文档给出）**：

- `flowmode/base.py:7-234` — `FlowMode` 37 方法基类，**默认实现 = learn 语义**（`research/06 §3.2 :188`；附录 A `:1033`）。
- `flowmode/story_flow.py:171-201` — **8 个 `@staticmethod` 是 learn 专属业务的 story 空实现**：`fast_guard_allowed / verify_selected / maybe_expand_knowledge_bg / warn_untaught_points / dynamic_deepen_knowledge / load_untaught_points / pick_due_reviews / warn_consecutive_no_points`（`research/06 §3.2 :189/:197`；附录 A `:1034`）。
- `flowmode/story_flow.py:201` — `StoryFlow(FlowMode)`，覆写差异点 + **14 个 `@staticmethod`** 把 learn 专属入口置空（`research/06 §3.2 :189`；注意 §3.2 表格写 14 个、§3.2 正文 `:197` 与 §4.7 写 8 个 —— **两处数字不一致，文档未解释**）。
- `flowmode/learn_flow.py:11-49` — `LearnFlow(FlowMode)`，**8 个 learn 钩子惰性派发**回 `pipeline._xxx`（`research/06 §3.2 :190`；附录 A `:1035`）。
- `flowmode/base.py` 234 行 / `story_flow.py` 201 行 / `learn_flow.py` 49 行 / `__init__.py` 25 行（`research/06 §3.2 :188-191`；`research/00 :54` 给出旧口径 base 192 / story_flow 156 / learn_flow 39 / __init__ 17 —— **两份文档行数不一致**）。
- `app/main.py:1304,1351` — **"最脏"**：路由层直接 import 私有 `_is_story`，`revise-local` 端点**绕过 `_flow(book).context()` 门面**自己算 `story_mode`（`research/06 §3.3-A :207-211`；附录 A `:1037`）。
- `app/agents/architect.py:2895,2916` — 裸 `_flow.kind` 业务判断（`research/06 §3.3-B :228-229`；附录 A `:1039`）。
- `app/drift.py:54,66,162,166` — drift 检查内部的 `is_story` 分支（`research/06 §3.3-C :241-244`；附录 A `:1038`）。
- `app/continue_story.py:431,871,915,926,932,1582` — 6 处 `book_kind` 直读（附录 A `:1040`）。
- `app/main.py:535,552,572,593,2796,2814,3375` — 7 处路由层 `kind == "story"`（附录 A `:1041`）。
- `app/pipeline.py:216-217` — `_is_story(book)` 定义处；`app/pipeline.py:223-225` — `_flow(book)` 门面入口（附录 A `:1031-1032`）。

**聚合结论**（`doc/research/00-evidence-baseline.md:1586`，逐字引用）：

> `flowmode/` 门面思路正确，但 37 方法上帝接口 + **方向反了**（story 侧给 learn 钩子写空实现）；门面外仍有 30+ 处直读 `book_kind` | 改为 **8 个 Protocol 组合**；learn 用 `NotImplementedError` 占位；用 AST 检查器把规约变成机器可验证

**新设计的三条对比改进**（`01-总体架构.md:423-429` 表格）：

| 旧项目 | 新设计 |
|---|---|
| 37 方法上帝接口 | 8 个 Protocol，各自独立 |
| 反向空实现（story 给 learn 写空） | **正向着色**：learn 显式 `raise NotImplementedError` |
| 空实现静默返回错误结果 | 抛错并附「如何实现」的提示 |
| 靠人工遵守「禁止模式分支」规约 | **AST 检查器**机器验证：`domain/` 与 `graph/nodes/` 不得出现 `book_kind` / `is_story` 判断 |

---

## D. 当前 story_commandcode 里 learn 相关代码的实际状态

> **总结论：`modes/` 层完全为空（0 行实现），8 个 Protocol 一个都不存在，learn 的全部 A 表能力零实现。文档要求的「预留接口」在代码里没有落地。**

### D1. `src/storyforge/modes/` —— **只有 1 个 0 字节 `__init__.py`**

```
E:\AI_project\story_commandcode\src\storyforge\modes\__init__.py   0 字节
（目录内文件总数 = 1，无 .pyc，从未被 import 过）
```

- 文档要求（`01-总体架构.md:120-123`）：`modes/protocol.py`（ModeProtocol）、`modes/story.py`（一期实现）、`modes/learn.py`（占位：接口 + NotImplemented）—— **三个文件全部不存在**。
- 文档要求（`01-总体架构.md:371-397`）的 8 个 Protocol —— **全仓 grep `LearningMap|TeachingLedger|KnowledgeCard|ResearchProvider|FactVerifier|Estimator|GenrePlugin|PromptPack|ModeProtocol|NotImplementedMode` 在 `*.py` 中零命中**（唯一命中在 `doc/research/_tools/` 的旧项目测量脚本里，非本项目代码）。
- 文档要求（`01-总体架构.md:412-420`）的 `class LearnMode` + `raise NotImplementedError` —— **全仓 `NotImplemented` 零命中**。
- 文档要求（`03-分期实施计划.md:484`）"P1 即定 `ModeProtocol`；用「加一个 mock learn 实现」测试可扩展性" —— **未执行**。
- **README 与代码不符**：`README.md:93` 写 `modes/            story（一期）+ learn（接口占位）` —— 实际 `modes/` 是空的，`story.py` 也不存在。

### D2. 没有任何代码 import `storyforge.modes`

唯一提到 `modes` 的地方是**契约测试自己**：

- `tests/contract/test_architecture_invariants.py:144`：`if "storyforge.modes" in names or "modes" in names: continue  # 模式层本身允许`

这条豁免分支**永远不会生效**，因为没有任何文件 import `modes`。

### D3. `domain/enums.py` 的 `BookMode` 定义了，但从未被使用

```python
# src/storyforge/domain/enums.py:27-31
class BookMode(StrEnum):
    """书籍模式。一期只实现 STORY（决策 D-08），LEARN 保留接口不实现。"""

    STORY = "story"
    LEARN = "learn"
```

- 导出在 `__all__`（`enums.py:560-561`）。
- **`BookMode.LEARN` 在全仓（src + tests + scripts）零命中**。
- 唯一消费者：`src/storyforge/infra/storage/models.py:41`（import）+ `:83`（仅取默认值）：
  ```python
  mode: Mapped[str] = mapped_column(String(16), nullable=False, default=BookMode.STORY.value)
  ```
- **`Book.mode` 列从未被读取，也从未被显式写入** —— 全仓 `\.mode\b` 属性访问零命中（`model_validator(mode=...)` / `json_mode` / `cache_mode` / `journal_mode` 已排除）。
- 更根本的：**全仓没有任何代码创建 `Book` 行**（`Book(` / `INSERT INTO books` 零命中）。`books` 表当前是**死表**。因此 `Book.mode` 默认值也从未真正生效过。

### D4. `settings.mode` 定义了，但从未被读取

```python
# src/storyforge/config.py:274-275
#: 一期只支持 story（决策 D-08）；learn 保留接口但不实现
mode: Literal["story", "learn"] = "story"
```

```toml
# config/settings.toml:8
mode = "story"                 # 一期只做 story（D-08）；learn 预留接口
```

- **`settings.mode` / `get_settings().mode` 属性访问在 src、tests、scripts 中零命中。**
- 这是一个**只写不读的配置项** —— 与 `doc/05-验收标准.md:225` 反模式 #17「**每个写出的信号必须有读取点**」/「旧项目 `must_review` 25 处写入 0 处读取」**同型**，而 `tests/contract/test_architecture_invariants.py:517-595` 的规则 17 **只扫模块级 `must_/needs_/should_/require_` 前缀的绑定，不扫 pydantic 字段**，因此这个死配置**逃过了检查器**。
- 同时 `settings.mode` 与 `Book.mode` 构成**两处模式真相**，而 §9.1 `:860` 要求"`mode` 是一等字段且不可变"（应只落 `books.mode`）。

### D5. AST 检查器规则 3 —— **已建、已通过，但对"完全缺席"完全无感**

`tests/contract/test_architecture_invariants.py:133-154` 实现了 D-08 要求的机器约束：

```python
MODE_BRANCH_TOKENS = {"book_kind", "is_story", "_is_story", "book_kind_"}
def test_rule03_no_mode_branching_in_domain_and_nodes(src_root): ...
```

**实测运行结果**（`uv run pytest tests/contract/test_architecture_invariants.py -q`）：

```
....................                                                     [100%]
```

**20 passed** —— 规则 3 通过。

- **但这是"真空通过"**：因为 `modes/` 层根本不存在，`domain/` 与 `graph/nodes/` 里也**没有任何模式分支**（没有分支可判）。规则 3 防的是"门面建成但门面外仍有直读"，而当前**连门面都没有**。
- 规则 3 的检查集是 `{"book_kind", "is_story", "_is_story", "book_kind_"}`，**不含 `mode` / `BookMode`**。因此 D3/D4 那两处 `mode` 字段的引入与将来可能出现的 `if book.mode == "learn"` 分支，**当前不会被这条规则拦住**。
- 检查器与 §4.3 示例（`01-总体架构.md:438`）一致，但 §4.3 示例里的 `forbidden = {..., 'kind == "story"'}` 这一项**未实现**。

### D6. A 表 9 项能力在代码中的实际存在度

| # | 能力 | 文档要求的存在形态 | 代码实际 |
|---|---|---|---|
| A1 | 知识账本 | `TeachingLedger` Protocol + NullLedger | ❌ 零命中。`models.py` 的 16 张表里无 `knowledge_ledger`（表清单：books/volumes/chapters/entities/locations/entity_relations/entity_affiliations/entity_states/injuries/events/event_participants/foreshadowings/runs/work_units/llm_calls） |
| A2 | 学习地图 / Module | `LearningMap` Protocol + StoryArcMap + KnowledgePoint/KnowledgeCard Pydantic 模型（O-02 要求） | ❌ 零命中。`config/genres/` **是空目录（0 个文件）**，与 `01-总体架构.md:48-51` 承诺的 `genres/xianxia.toml`、`suspense.toml` 不符 |
| A3 | 知识卡 | `KnowledgeCardProvider` + NullCards | ❌ 零命中。`Chapter` 表无 `cards` 列 |
| A4 | 事实核验 | `FactVerifier` + NullVerifier | ❌ 零命中 |
| A5 | 联网研究 | `ResearchProvider` + NullResearch | ❌ 零命中。`pyproject.toml` 依赖里无 Tavily |
| A6 | 预估器 | `Estimator` + StoryEstimator | ❌ 零命中（`estimator` 在 src 内零命中）。`config/scales.toml`、`config/thresholds.toml` **均不存在**（阈值实为硬编码进 `config/settings.toml`） |
| A7 | 8 个调度钩子 | story 侧应"整个不存在"，由 NullXxx 提供 no-op | ❌ 零命中（既无钩子也无 Null） |
| A8 | learn 专属端点 | `mode × endpoint` 表驱动契约测试 | ❌ 无。`src/storyforge/api/` 下只有空的 `__init__.py` 与空的 `routes/__init__.py` —— **HTTP 层未实现** |
| A9 | GenrePlugin | 按 `Mode` 枚举取插件 | ❌ 零命中。无 `GenrePlugin`/`GenreManager`/`genre_profiles`。`genre` 仅作**普通字符串参数**透传进 bible/volume prompt（`bible_architect.py:247,256`；`volume_planner.py:244,279`） |

### D7. 与文档声称"已完成"的落差

| 文档声称 | 文档行号 | 代码实际 |
|---|---|---|
| `modes/` 含 `protocol.py`/`story.py`/`learn.py` | `01-总体架构.md:120-123` | 仅 0 字节 `__init__.py` |
| `modes/            story（一期）+ learn（接口占位）` | `README.md:93` | 同上，**README 与代码不符** |
| "P0-9 反模式检查器 20 条" ✅ 完成 | `03-分期实施计划.md:43`、`:715`、`:722` | ✅ 属实（20 passed），但规则 3 因无代码而真空通过 |
| `04-数据模型规格.md:69`：`books.mode` 是必填列 | — | ✅ 列存在，但**无写入方、无读取方** |
| §9.4 `:951`："第一期（story）**必须做**的事：定义上面 8 个 Protocol/ABC，只写 story 实现" | `research/06 :950-954` | ❌ 0/8 |
| §9.7 `:1014`：**P0 优先级**："定死 `ModeCapabilities` 8 个 Protocol + story 实现，learn 全 `NotImplementedError`" | `research/06 :1014` | ❌ 未做；P0 已在 `03-分期实施计划.md:493` 标记为 ✅ **完成** |
| `03-分期实施计划.md:484`（风险缓解）："P1 即定 `ModeProtocol`" | — | ❌ P1 亦已标记 ✅ 完成（`:494`） |

### D8. 唯一真正生效的隔离机制

`tests/contract/test_architecture_invariants.py` 规则 1/2/2b（`domain/` 不得 import langgraph/langchain/SQLAlchemy）+ 规则 3（模式分支）**结构性地守住了 D-08 的意图**——但守的是"没有模式分支"，而非"接口已预留"。**"没有分支"与"接口就绪"是两件事，文档 D-08 要求的是后者。**

---

## E. 文档里 learn 相关的尚未决断问题

### E1. 编号开放问题（`doc/06-风险与开放问题.md` §3）

| # | 问题 | 默认处理 | 决策点 | 行号 | 状态 |
|---|---|---|---|---|---|
| **O-02** | learn 专属字段是否建表 | **保留接口，不建表**（避免同旧项目 Phase 2 五张 0 行死表） | P1 | `:222` | 文档另处标记 ✅ 已确认（`00-决策记录.md:175-180`）—— 但**其前提"仅定义 Pydantic 模型与 Protocol"未实现**（见 D1/D6），因此"保留接口"实际 = **什么都没保留** |
| **O-06** | **学习模型（learn）何时启动** | **一期结束后评估** | **P5 复盘时** | `:226` | ⬜ 未决 —— 这是本任务标题对应的那条 |
| **O-08** | **是否保留「知识小说」的联网研究（Tavily）** | **一期不做，接口预留** | **二期** | `:228` | ⬜ 未决；接口亦未预留（D6-A5） |

### E2. 其他文档中的 learn 未定项 / 未执行项

| 项 | 内容 | 位置 | 性质 |
|---|---|---|---|
| **P6+ 未规划** | 「P6+ \| 后续 \| 卷循环、卷末验收、**learn 模式**、评测 \| P5 \| ⬜ **规划中**」 | `03-分期实施计划.md:20` | learn 被排在 P6+，状态"规划中"，**无交付物清单** |
| **D-04 排除** | 「一期不做 learn 模式（见 D-08）」 | `00-决策记录.md:52` | 已定 |
| **一期不验收项** | 「learn 模式功能 \| D-08（只留接口）」 | `05-验收标准.md:285` | 已定 |
| **明确不做的事** | 「learn 模式实现 \| D-08（只留接口）」 | `01-总体架构.md:541` | 已定 |
| **扩展点（二期）** | 「learn 模式 \| `modes/learn.py` 实现 Protocol \| **二期**」 | `01-总体架构.md:557` | 已定方向，无验收定义 |
| **文档内部未统一：文件名** | `01-总体架构.md:121` 写 `modes/protocol.py`（单数），`:370` 写 `modes/protocols.py`（复数） | 两处 | ⚠️ 不一致 |
| **文档内部未统一：形态** | §4.2 给"Protocol 集合 + `StoryMode`/`LearnMode` 类"；§9.4 `:932-947` 给"`ModeCapabilities` frozen dataclass + `MODES` 字典" | `01-总体架构.md:369-421` vs `research/06:931-948` | ⚠️ 两种设计未收敛 |
| **文档内部未统一：8 个钩子的计数** | §3.2 表格 `:189` 说 `story_flow.py` 有 **14 个** `@staticmethod`；§3.2 `:197` 与 §4.7 `:366` 说 **8 个** | `research/06` | ⚠️ 不一致 |
| **learn 的可插拔性无验收定义** | 缓解措施只说"用「加一个 mock learn 实现」测试可扩展性"，**未定义该 mock 测试的验收断言**，也未列入 `05-验收标准.md` §4 的 20 条反模式检查 | `03-分期实施计划.md:484` | ⬜ 未定 |
| **`cards/backfill` 契约缺口** | `/api/books/{id}/cards/backfill`（`main.py:1053-1055`，仅 3 行）**未做 story 拒绝**，对比 `/chapters/{id}/cards/regenerate`（`main.py:1278`）明确抛 400 —— 文档自评"这是一处**契约不一致**"，要求改为表驱动契约测试 | `research/06 §2.4 :159`、§5.6 `:528`、附录 A `:1044` | ⬜ 未修（新项目 HTTP 层未实现） |
| **`learn` 停更的定性** | "learn 模式今天实际上只能创建"修仙玄幻"场景 —— 这是 **learn 早已停止演进、story 才是主线的硬证据**"；"系统已经事实性地从「知识小说」漂移成「纯小说生成器」，但 `pipeline.py` 里还留着 188 行 learn-only 代码" | `research/06 §4.8 :393`；`research/03 :1126` | 事实登记（支撑 O-06 的"一期结束后评估"） |

### E3. 未见诸文档的（明确说明"文档未提"）

- **learn 模式的验收标准**：`05-验收标准.md` 全文中 learn 只出现 1 次（`:285`，且是"不验收"）。**无任何 learn 的功能/质量/Token 验收项**。
- **`KnowledgePoint` / `KnowledgeCard` / `TeachingLedger` 的 Pydantic 模型字段规格**：O-02（`00-决策记录.md:179`）承诺"仅定义 Pydantic 模型与 Protocol"，但**字段定义在任何文档中均未给出**（`04-数据模型规格.md` 对 learn 只提到 `books.mode` 一个 TINYINT 语义的列，`:69`）。因此 O-02 的实施依据本身也是缺失的。
