# 04 · Agents 与 Prompts 深度审计（供 LangChain + LangGraph 重写）

> 审计对象：`E:\AI_project\story_llm\backend`
> 只读审计（含 `learnstory.db` 只读打开）。所有字符数由脚本实测，见文末「测量方法」，非估算。

**五条头条结论**

1. **prompt 不是"太多"，而是"没有分层"**：约 116 块 / 8.9 万字符的可识别 prompt 文本中，真正每章生效的 writer 规则约 5.3k 字符；其中 **10.9% 的 8-gram 在 writer 与 editor 两处逐字重复书写**，且两处的**对话占比阈值互相矛盾 3~7 倍**（writer 按流派 5%~12%，editor 仍写死 35%）。
2. **`must_review` 是一个只写不读的死信号**（§4.4）：全仓 25 个 `must_review` 命中中**零个读取点**。它以"强制审校"的名义存在，实际只等于"把一段文本塞进 `style_notes`"。这是文风控制链失效的**单点根因**。
3. **检测器不缺，执行通道缺**（§4.6）：Editor 的 LLM 语义审校在生产 DB 里抓到 `比喻` 43 次、`描写` 466 次、`AI味` 55 次；**88.1% 的被评章被点名文风问题，其中 32.4% 仍判 passed**（§4.2）。
4. **人物关系单一有数据级根因**（§4.3）：966 条关系边里 **29.6% 是库存登记**（人物-物品），而 254 条人物-人物边用了 **191 种自由文本标签**——`post_processor` 自带的 9 词受控词表只覆盖 **12.2%**。任何"关系多样性"都无法度量。
5. **题材差异化是薄覆盖层**（§6.3）：非 boss 章（约 85%）题材影响为 **0 字符**；18 个题材里 13 个的 boss 文本完全相同；唯一改控制流的旋钮只有 `dialogue_floor`。

---

## 一、Agent 清单

### 1.1 注册表（`app/agents/registry.py:65-79,124-138`）

`AGENT_IDS` 登记 13 个角色，`build_default_registry()` 登记 12 个（`satellite_expander` 被注释掉，`registry.py:126`）。

**⚠️ registry 与实际 tier 不一致**：注册表把 `editor` 标为 `"balanced"`（`registry.py:131`）、`post_processor` 标为 `"premium"`（`:132`），但类里 `EditorAgent.tier = "fast"`（`editor.py:12`）、`ChapterPostProcessor.tier = "fast"`（`post_processor.py:16`）。因为 `BaseAgent.ask` 用的是 `settings.tier_for_role(self.role)`（`base.py:45`），**注册表 tier 字段是纯展示数据，不参与路由**。

### 1.2 全部 Agent 实测表

system prompt 字符数为**实测渲染值**（story 模式 / lean 档，除非注明）；token 为区间估计（下界 0.60 tok/汉字，上界 1.00 tok/汉字，ASCII 按 3.5 字符/token）。

| Agent | role / tier | 用途 | 输入 | 输出 schema | system 字符 | ~token | prompt 定义位置 |
|---|---|---|---|---|---|---|---|
| `GateAgent` | gate / fast | 输入守门：是否可做知识小说、是否宽泛、生成学习画像 | topic | `{is_learning_topic, reason, suggestions, broad, audience_profiles[]}` | **565** | 312–452 | 类属性 `system`，`gate.py:9-23`（内联） |
| `QueryPlannerAgent` | researcher / fast | 主题→搜索查询 | topic + 画像 + 子主题 | `{queries[]}` | **95** | 57–95 | `researcher.py:58-61`（内联） |
| `ResearchAgent` | researcher / fast | 搜索结果→学习地图（知识点池） | topic + scene 模板 + 检索片段 | `{topic_summary, modules[], audience_profiles[], verified_facts[], overall_confidence, issues[]}` | **727** | 378–613 | `researcher.py:158-177`（内联） |
| `SatelliteExpanderAgent` | researcher / fast | 卫星方向深化（2 个 prompt） | 已覆盖知识域 | `PROPOSE`: `{direction, rationale}`；`GEN`: `{modules[]}` | PROPOSE **180** / GEN **378** | 108/175 | `researcher.py:91-106`（内联 ×2） |
| `VerifyAgent` | verifier / fast | 知识点逐点核验 | points 批 | `{verdicts:[{point,verdict,note}]}` | **160** | 104 | `researcher.py:119-125`（内联） |
| `_GapChecker` / `_MapFiller` | researcher / fast | **函数内动态定义的匿名 Agent** | 学习地图 / 盲区 | `{gaps[]}` / `{modules[]}` | 180 / 300 | 118/180 | `researcher.py:354-358`、`:377-384`（**类体内联，运行时构造**） |
| `ArchitectAgent` | architect / balanced | 章节骨架 + plot + 细纲（**最复杂**，三种生成模式） | 模块 + 档位规则 + 七锚 + 伏笔配额 | `{mode,title,total_volumes,arc,chapter_count,chapters[],volumes[],foreshadowings[],plot_nodes[],conflict_curve[],character_arcs[]}` | **3,398**（learn）/ **1,513**（story） | 2,178 / 1,123 | `architect.py:351-414`（`SYSTEM_TEXT`）、`:892`（`STORY_SYSTEM_TEXT`） |
| `WorldBuilderAgent` | worldbuilder / balanced | 世界观圣经 + 人物状态表 | 知识点/故事简报 + scene 模板 | 圣经纯文本；`CHARACTERS`: `{characters[]}` | BIBLE **775**、BIBLE_STORY **781**、`+REALM_SYSTEM_EXTRA` **169**、CHARACTERS **605** | 657/692/149/434 | `worldbuilder.py:13-30`、`:57-71`、`:74-79`、`:32-52`（内联 ×4） |
| `BibleReviewerAgent` | bible_reviewer / balanced | 圣经一致性审校（7 维度，MIN_SCORE=7） | bible 前 8000 字 | `{score, issues[], revised_bible, summary}` | **1,478** | 1,120 | 模块常量 `REVIEW_SYSTEM`，`bible_reviewer.py:28-94` |
| `StoryDirectorAgent` | architect / balanced | 纯小说一句话意图→七锚骨架 | brief + 档位 + 题材卡 + 流派卡 | `{title,logline,...,golden_opening,milestones[],acts[],character_arcs[],ladder[],factions[],golden_finger,pace_curve,volumes[],arcs[]}` | **2,497** | 1,303–2,207 | 模块常量 `SYSTEM_TEXT`，`story_director.py:12-52` |
| `StoryArcPlannerAgent` | story_arc_planner / balanced | 第 2 卷起的卷导演 | story_brief + 上卷梗概 + 未回收伏笔 | `{vol_title,vol_goal,arcs[],emotional_arc,relationship_turn,cost_moment,ending_hook,fs_plan,notes}` | **916** | 611 | `story_arc_planner.py:17-43` |
| `WriterAgent` | writer / balanced | 章正文（两段式：正文 + 续写闭环） | 记忆上下文 L0–L5 + 细纲 + 文风范本 | 正文纯文本（**非 JSON**） | **4,785**（story）/ **5,197**（learn） | 2,640–4,262 | `_WRITE_BASE`（`writer.py:153-196`）+ `writer_rules.anti_ai_block`（**动态**） |
| `EditorAgent` | editor / fast | 审校 + 评分 + 整章改写 / 定点替换 | 草稿 + 圣经节选 + 状态表 + 时间线 + 细纲 | `{passed,score,notes,issues[{type,position,description,suggestion}],revised_content,local_fixes[]}` | **5,859**（story）/ **6,213**（learn，+`LEARN_SYSTEM_EXTRA` 354） | 4,587–5,213 | 类属性 `system`，`editor.py:13-134`（**单块 5.2k 字符**） |
| `ChapterPostProcessor` | memory / fast | 一次调用同时产知识卡 + 梗概 + 11 类记忆增量 | 正文 + 账本快照 | `PostProcessOut`（12+6 字段，`schemas.py:420-472`） | **5,960**（learn）/ **4,203**（story） | 3,806 / 2,718 | `post_processor.py:18-125`、`:131-212`（**单块 5.7k 字符**） |
| `VerifierAgent` | verifier / standard | 跨章校验三路：专名/漂移/逻辑 | 最近 N 章正文（截断 800–1500 字/章） | `{type,checked,issues[{chapter,name,kind,desc,severity}]}` | `SYSTEM_ENTITIES` **202** / `SYSTEM_DRIFT` **200** / `SYSTEM_LOGIC` **417** | 187/184/393 | `verifier.py:29-59`（内联 ×3 + `:23-26` 基座 60） |
| `VolumeAuditor` | auditor / fast | 卷末体检 + 卷约束生成 | 账本快照 | `{issues[],world_consistency[],memo_for_next_volume,...,review_score}` | **833** | 503 | `audit.py:9-34`（内联） |
| `EraCheckerAgent` | era_checker / fast | 时代一致性自检（默认关） | 正文前 3000 字 + 时代背景 | `{issues:[{原文,原因,建议替换}],checked}` | **102** | 96 | `era_checker.py:19-23`（内联） |

**汇总**：16 个 Agent 类 + 2 个运行时动态类 = **18 个 LLM 角色**，其中 **11 个** 的 prompt 是**内联在类/函数体内的裸字符串**，只有 3 个（`architect`/`story_director`/`story_arc_planner`）用了模块级命名常量，**0 个**走配置文件。

### 1.3 结构性发现

1. **一个 role 被多个语义完全不同的调用复用**：`role = "architect"` 同时用于 `ArchitectAgent`（章节大纲）和 `StoryDirectorAgent`（`story_director.py:189`）。`role = "researcher"` 同时用于查询规划、学习地图、缺口检查、模块补全、卫星深化、知识点核验 **6 个不同任务**（`researcher.py:56,88,117,156` + `:354,377`）。这让 `tier_for_role` 路由、`agent_stats` 统计、`runctx.agent_role` 追踪全部失真。
2. **匿名 Agent 运行时铸造**：`researcher.py:354-358`（`_GapChecker`）与 `:377-384`（`_MapFiller`）在函数体内 `class ... (BaseAgent)` 定义 prompt 并实例化。每次调用都新建类对象，prompt 不可枚举、不可版本化，且在 `_gap_fill` 的 `except Exception: pass`（`:406-407`）里整个静默。
3. **Architect 承担了 6 个不同的 LLM 任务**（实测 `ask_json` 调用点）：骨架生成（`:1428`）、plot 批补写（`:1793`）、全局预规划（`:2169`）、分段骨架（`:2362`）、分段重试（`:2372`）、卷规划（`:2517`）、单发档细化（`:2985`）、瘦身重试（`:2999`）、极简降级（`:3002`）。同一个 `SYSTEM_TEXT` 服务全部 9 个调用点，每个调用点靠**在 user prompt 尾部追加一段临时指令**来改写任务（例如 `:1430-1435` 的"本次只输出骨架…**严禁输出 plot 字段**"）——这是典型的"一个 prompt 干所有事，用尾部补丁纠偏"。
4. **`schemas.py` 只覆盖 2 个 Agent**：`ChapterPlanOut`（architect）、`PostProcessOut`（post_processor）。Editor、Verifier、Auditor、StoryDirector、BibleReviewer、WorldBuilder 全部裸 `dict` 返回，靠 `result.setdefault(...)` 手工兜底（如 `editor.py:346-356`、`audit.py:53-61`）。

---

## 二、Prompt 清单与臃肿度

### 2.1 总量实测

对 `backend/**/*.py` 全量扫描三引号字符串字面量（`doc/research/_tools/prompt_scan.py`）：

| 口径 | 块数 | 总字符 |
|---|---|---|
| 全部字面量（含测试/benchmark/空串） | 2,165 | — |
| `>=300` 字符（含测试） | 272 | **179,952** |
| `app/` 内 `>=200` 字符 | 292 | **152,057** |
| `app/` 内 `>=300` 字符 | 166 | **121,948** |
| `app/` 内 `>=500` 字符 | 77 | **87,720** |
| `app/` 内 `>=1000` 字符 | 19 | **49,311** |
| `app/` 内 `>=2000` 字符 | 12 | **40,201** |
| `app/` 内含指令关键词（你是/禁止/必须/只输出/规则/硬性）且 `>=200` | **116** | **89,446** |

**结论：可识别的"prompt 类文本"约 116 块 / 8.9 万字符。** 这是重写时需要重新组织的全部规则存量。

### 2.2 按文件聚合（`app/`，`>=200` 字符块）

| 文件 | 字符 | 块数 |
|---|---|---|
| `app/agents/architect.py` | **17,372** | 32 |
| `app/pipeline.py` | **16,904** | 44 |
| `app/agents/post_processor.py` | **10,391** | 3 |
| `app/writer_rules.py` | **7,923** | 7 |
| `app/main.py` | 7,908 | 17 |
| `app/evaluation_store.py` | 7,187 | 15 |
| `app/detail_review.py` | 7,042 | 10 |
| `app/agents/editor.py` | **6,213** | 2 |
| `app/prose_style.py` | 5,776 | 7 |
| `app/continue_story.py` | 5,036 | 14 |
| `app/agents/writer.py` | 3,655 | 6 |
| `app/memory.py` | 2,504 | 6 |
| `app/review_triage.py` | 2,845 | 2 |
| `app/agents/story_director.py` | 2,996 | 3 |

**提示词碎片化最严重的两个文件是 `architect.py`（17.4k / 32 块）和 `pipeline.py`（16.9k / 44 块）。** `pipeline.py` 是 8,606 行的编排文件，里面有 **44 段 prompt 文本**——这是"prompt 散落在业务逻辑里"的最直接证据。

### 2.3 Top-25 最大单块 prompt

| 字符 | ~tok | 位置 | 名称 |
|---|---|---|---|
| 5,960 | 3,806 | `agents/post_processor.py:18` | `system`（learn 记忆记录员 + 知识讲师） |
| 5,859 | 4,587 | `agents/editor.py:13` | `system`（主编审校 20 条） |
| 4,203 | 2,718 | `agents/post_processor.py:131` | `STORY_SYSTEM` |
| 3,398 | 2,178 | `agents/architect.py:351` | `SYSTEM_TEXT`（learn 架构师 15 条） |
| 3,374 | 2,002 | `prose_style.py:2` | 模块 docstring（**不是 prompt，但含大量实测口径**） |
| 3,040 | 2,162 | `writer_rules.py:2` | 模块 docstring（书 23 五条根因） |
| 2,578 | 1,522 | `review_triage.py:2` | 模块 docstring（三分诊口径） |
| 2,497 | 1,303 | `agents/story_director.py:12` | `SYSTEM_TEXT`（七锚） |
| 2,472 | 1,404 | （动态）| `writer_rules.anti_ai_block` **lean 档实测渲染值** |
| 2,400 | 1,556 | `detail_review.py:2` | 模块 docstring |
| 2,315 | 1,940 | `agents/writer.py:153` | `_WRITE_BASE` 原始字面量 |
| 2,081 | 1,892 | `writer_rules.py:355` | `_LEAN_ANTI_AI_TEMPLATE` 原始（展开后 2,472） |
| 1,833 | 1,640 | `writer_rules.py:325` | `LEGACY_ANTI_AI_RULES`（legacy 档、benchmark 基线） |
| 1,545 | 1,338 | `writer_rules.py:296` | `_LEGACY_ANTI_AI_TEMPLATE` |
| 1,513 | 1,123 | `agents/architect.py:892` | `STORY_SYSTEM_TEXT` |
| 1,478 | 1,120 | `agents/bible_reviewer.py:28` | `REVIEW_SYSTEM` |
| 975 | 435 | `agents/architect.py:1474` | plot 批补写指令（`_fill_one` 内联） |
| 916 | 611 | `agents/story_arc_planner.py:17` | `SYSTEM_TEXT` |
| 909 | 509 | `chapter_limits.py:2` | docstring |
| 887 | 509 | `architect.py:1648`（区间） | 批补写字段要求（内联，随档位变化） |
| 876 | 511 | `agents/architect.py` | 分段预规划指令 |
| 860 | 495 | `outline_lint.py:2` | docstring |
| 833 | 503 | `agents/audit.py:9` | `VolumeAuditor.system` |
| 798 | 581 | `detail_review.py:804` | `_PROMPT_HEAD`（**死代码**，见 §5.4） |
| 781 | 692 | `agents/worldbuilder.py:57` | `BIBLE_SYSTEM_STORY` |

**⚠️ 反模式：6 个模块把 2.4k–3.4k 字符的"设计文档"写在模块 docstring 里**（`prose_style.py`、`writer_rules.py`、`review_triage.py`、`detail_review.py`、`chapter_limits.py`、`outline_lint.py`，合计约 **13.5k 字符**）。这些不是 prompt，但它们与 prompt 文本同文件同层级，导致任何"扫描 prompt"的尝试都会混入噪音，且规则变更的真实出处极难定位。

### 2.4 重复 / 冲突 / 死代码

#### (a) 同一批规则在 writer 与 editor 两处重复书写 —— 实测重叠 10.9%

对 writer 侧规则文本（`_WRITE_BASE` + `anti_ai_block` + `story_hard_rules` + `output_requirement`，去空白 5,300 字）与 `EditorAgent.system`（去空白 5,191 字）做 **8-gram 集合交集**：

```
writer-rule text chars : 5300
editor system chars    : 5191
8-gram overlap         : 538 of 4944  (writer 的 10.9%，editor 的 10.8%)
```

命中片段（抽样）——全都是**逐字复制**的整条规则：

```
.人物一致性七查      /丹药/道具）必      /亲身经历/他人
.真假话与世界事      /功法/丹药/道        /动作），禁止名
/心如刀绞/悲痛       /新设定/新地图       /无人注意的暗处
/物品损毁/势力       /远处的阴影/一       [毁·可修]/[
"但老陈从未提过      "想起"某个从未       "那是…那是…那
```

具体重复清单：

| 规则 | writer 侧位置 | editor 侧位置 | 字符 |
|---|---|---|---|
| 人物一致性七查 | `writer.py:158-165` | `editor.py:15-22` | ~450 ×2 |
| 名词引入依据八查 | `writer.py:166-174` + `:486` | `editor.py:37-45` | ~700 ×3 |
| 事件冲击管理五条 | `writer.py:175-180` | `editor.py:86-91` | ~330 ×2 |
| 情绪呈现四条 | `writer.py:181-185` | `editor.py:92-97` | ~260 ×2 |
| AI 味检查七项 | `writer_rules.py:373`(词表) | `editor.py:27-34` | ~280 ×2 |
| 真假话/世界事实 | `writer.py:191` | `editor.py:46` | ~90 ×2 |
| 章末收束规范 | `writer.py:188` + `writer_rules.py:374` | `editor.py:80-85` | ~200 ×3 |
| 对话占比 35% | `writer_rules.py:221-225`(legacy) | `editor.py:31` `④对话占比低于35%（与写作端阈值一致）` | ~50 ×2 |

**同一套规则写三遍**（`writer.py` system 一次、`writer.py:486` user 再一次、`editor.py` 再一次）。这 8 个规则族合计约占 **2,000+ 字符**，在两个 Agent 的 prompt 里各占约 1/3。

#### (b) 实质性矛盾：writer 已删的配额，editor 仍在执行

`backend/writer_rules_config.json` = `{"rule_profile": "lean"}`（**当前生效档**）。

- lean 档**删除全部 7 个可数配额**（`writer_rules.py:70-74` docstring 明说"只要配额还在 prompt 里，任何数字都是错的"）：对话≥35%、每段≤5行、同一形容词≤2次、感叹号≤10、省略号≤5、破折号≤5、环境≤2处。
- **但 `editor.py` 从不 import `writer_rules`**（全仓 0 命中）。`editor.py:27-34` 的反 AI 检查仍是 legacy 原教旨：
  - `④ 对话占比低于35%（与写作端阈值一致）` —— **写作端已改成按 `genre_profiles.dialogue_floor(scene,flow)` 的 12%（或悬疑类的 5%）**，且 lean 档明文写"不是配额"（`writer_rules.py:274-278`）。
  - `⑦ 单段超过5行或单章内同一形容词出现超过2次` —— 写作端已删除该规则并明文覆盖（`writer_rules.py:375` "按次数、按行数、按比例的配额式要求，一律以本篇为准"）。
- 后果：writer 按新规则写，editor 按旧规则判 `ai_style` 必改，触发一次整章重写；重写稿又会被 writer 的规则判为注水。**两套规则互相打架，每章最多浪费一次完整重写调用（输入 ~18k + 输出 ~5k tokens）。**

#### (c) 死 prompt（定义但无生产调用点）

| Prompt | 位置 | 字符 | 状态 |
|---|---|---|---|
| `detail_review._PROMPT_HEAD` + `build_batch_prompt` 全套 | `detail_review.py:804-899`（含 1,273 行确定性检查器） | ~7,042 | **死代码**。全仓 `review_detail_batch` 只被 `test_detail_review.py`（32 处）引用；`app/` 内仅 `pipeline.py:2582` 的一句 docstring 提到配置键名。`outline_config.json` 的 `detail_review_enabled` 键**不是默认键**（默认键在 `outline_config.py:59`，但文件里没有该键）→ `get()` 回落到默认 `False`。 |
| `WriterAgent.WRITE_SYSTEM` | `writer.py:208` | **4,148**（2,315 + 1,833 legacy） | 注释自认"生产 prompt 不走这个常量"（`writer.py:203-207`），仅 benchmark/回归用。**benchmark 基线测的是 legacy，生产跑 lean → 基线失真。** |
| `WriterAgent.ANTI_AI_RULES` | `writer.py:26` | 1,833 | 同上，兼容别名。 |
| `writer.py:147,150` boss fallback 字面量 | `writer.py:147,150` | 48 ×2 | 与 `genres/builtin/default.py:30-32` 三重复。 |
| `EditorAgent.LEARN_SYSTEM_EXTRA` | `editor.py:137-146` | 354 | story 路径不注入（`story_flow.py:18-19` 返回 `None`）→ 纯小说书永不使用。 |
| `WriterAgent.LEARN_SYSTEM_EXTRA` | `writer.py:211-217` | 412 | 同上（`story_flow.py:14-15` 返回 `""`）。 |
| `architect._BLUEPRINT_BLOCK` | `architect.py:2070-2078` | 728 | 仅 ultra 档注入。 |
| `long_memory_prompt.variant_block` | `long_memory_prompt.py:44-58` | ≤1,200 | `long_memory_prompt_config.json` = `{"enabled":false,"mode":"off"}` → 永不注入。 |
| `researcher.py` 的 4 个研究 Agent | `researcher.py:58-177` | 1,390 | learn 专属；story 路径不使用。 |
| `VolumeAuditor` / `GateAgent` / `QueryPlanner` / `ResearchAgent` / `VerifyAgent` / `SatelliteExpander` | 各自 | 2,320 | **纯 learn 专属**：story（纯小说）路径完全不调用 → 纯小说重写可整体丢弃。 |

#### (d) 运行时拼接导致"源尺寸 ≠ 实际尺寸"

`anti_ai_block` 是唯一有运行时展开的：源模板 `_LEAN_ANTI_AI_TEMPLATE` **2,081 字符**，经两次 `.replace()`（`@@CLICHES@@` → 254 字符词表、`@@DIALOGUE@@` → 按 `dialogue_floor` + 在场人数动态渲染的 160 字符），实测渲染 **2,472 字符（+19%）**。

同理 `_LEGACY_ANTI_AI_TEMPLATE` 1,545 → `LEGACY_ANTI_AI_RULES` 1,833（+19%）。

其他所有 prompt 都是**静态拼接**，源尺寸 ≈ 实际尺寸。唯一例外是 Architect 的 9 个调用点各自往 `user_prompt` 尾部追加不同指令块，实测尾部补丁块 61–1,513 字符不等。

---

## 三、Writer prompt 实构与预算分解

### 3.1 数据流

```
pipeline._write_one_inner (pipeline.py:8363)
  └─ _prepare_chapter (pipeline.py:~6400)
       ├─ memory.build_context(session, book, chapter_data)   ← 造 context dict (memory.py:1143)
       ├─ GenreManager.get(...).writer_blocks(...)           ← context["genre_blocks"] (pipeline.py:6460)
       ├─ style_corpus.query_style(...)                      ← context["style_snippets"] (pipeline.py:6501)
       ├─ flow.story_craft(context, book)                    ← context["story_craft"] (pipeline.py:6534)
       └─ flow.flow_card(book)                               ← context["flow_card"] (pipeline.py:6536)
  └─ writer.WriterAgent.write(context, chapter, ...)          (writer.py:274)
       ├─ _system = _WRITE_BASE.format(...)                   ← SYSTEM 1 (writer.py:290)
       ├─ system_profile_transform(_system, ...)              ← lean 替换规则 9 (writer.py:292)
       ├─ _system += anti_ai_block(context, chapter)           ← SYSTEM 2 (writer.py:294)
       ├─ _system += flow.system_style(LEARN_SYSTEM_EXTRA)     ← SYSTEM 3 (writer.py:296)
       ├─ user = _build_prompt(context, chapter, tpl)          ← USER 主体 (writer.py:409)
       ├─ user += output_requirement(chapter)                  ← USER 尾部 (writer.py:318)
       └─ ask(user, system=_system, ...)                       (writer.py:330)
```

### 3.2 `_build_prompt` 的精确注入顺序（`writer.py:409-566`）

按代码行序，每一块的实际注入条件与内容：

| # | 行 | 块名 | 条件 | 内容 |
|---|---|---|---|---|
| 1 | 411 | `【L0 世界观圣经】` | 恒有 | `context['bible']`（圣经 + `genres/genre_state` 追加，`memory.py:1666`） |
| 2 | 412 | `worldview_reminder()` | 恒有 | story `46` 字符 / learn `118` 字符 |
| 3 | 413-417 | `【防雷同提醒】` | 恒有 | **硬编码 170 字符** |
| 4 | 419-426 | `flow_card` | `book.flow` 非空 | `story_flows.format_story_flow()`，实测 277–328 字符 |
| 5 | 428-438 | `【文风范本…】` + samples | `style_snippets` 非空 | **wrapper 294 字符** + 最多 2 段 × ≤400 字符（`style_corpus.py:786`） |
| 6 | 439 | `term_mapping_section()` | learn 且 `mapping_rules` 非空 | learn 10 条 ×~13 字符；**story 恒为空串**（`story_flow.py:143-144`） |
| 7 | 440-462 | `【本卷开篇允许换场景】` 或 `【现场必须承接】/【上一章已收束】` | `volume_open` / `hang` | 实测 86 字符（承接态） |
| 8 | 463-471 | `【L2 前情时间线】` + `【⚠️ 禁止重演已发生事件】` | `timeline` 非空 | 时间线 ≤6,000 + **硬编码硬规则 ~700 字符** |
| 9 | 472-477 | `【L5 上一章结尾原文】` | `prev_prose` 且非 `volume_open` | `read_prev_chapter_tails(tail_chars)` |
| 10 | 478-479 | `【L3 人物状态表】` | `states` 非空 | `_fmt_char_line` 分级渲染 |
| 11 | 481-482 | `【图谱关系快照】` | `graph_context` 非空 | **默认恒空**（`audit_config.json: graph_inject_enabled=false`） |
| 12 | 484-486 | 已注册名词表 + `【名词引入硬性约束】` | `entity_registry` 非空 | 表 + **硬编码 ~330 字符约束** |
| 13 | 487-493 | `【⚠️ 断点补写任务】` | `bridge_prev/next` | bridge 模式专用 |
| 14 | 494-495 | `【L3 关键事件记忆】` | `events` 非空 | 最近 10 条事件 |
| 15 | 496-497 | `【L4 相关前文片段】` | `rag_snippets` 非空 | RAG 检索结果 |
| 16 | 498-499 | `【骨架指引】` | `skeleton_guide` 非空 | 全书结构位置 + 人物弧光 |
| 17 | 500-505 | `【伏笔任务-本章需埋下/需回收】` | `plant_fs` / `resolve_fs` 非空 | `json.dumps` |
| 18 | 506-513 | `【复习候选】` | learn 且 `due_reviews` 非空 | 艾宾浩斯复习；story 恒空 |
| 19 | 514 | `_seasoning()` | 恒有 | 开场方式 + 叙事重心 + 基调（`writer.py:222-272`） |
| 20 | 515-523 | beat 指令 | `beat == boss` / ∈ `BEAT_INSTRUCTIONS` | boss 走 `genre_blocks`（74–184 字符）或 fallback 48；climax/emotion/recall/turning 各 ~90–120 字符 |
| 21 | 525-550 | `【🏁 全书终章·…】` | `is_finale` | ultra 版本 ~282 字符 / 非 ultra ~330 字符 |
| 22 | 551 | `chapter_info_block()` | 恒有 | **story**: `story_craft` + 本章信息 + `story_hard_rules`(607) + `_detail_block`(2,118)；**learn**: 概念引导 + `_detail_block` |
| 23 | 552-565 | `length_target_note()` | 恒有 | 71 字符 |
| — | 312-318 | `output_requirement()` | 在 `write()` 里追加 | 208 字符（lean）/ 143（legacy） |

### 3.3 章节 prompt 预算分解表

**基准场景**：story 模式 / lean 档 / standard 档 / 第 2 卷第 12 章 / 非终章 / boss 以下普通章 / 有细纲。

SYSTEM 合计 **4,785** 字符，USER 合计 **25,330** 字符，**总计 30,115 字符 ≈ 10.7k–13.3k tokens**。

| # | 块名 | 来源 | 字符 | ~token | 稳定性 |
|---|---|---|---|---|---|
| — | **SYSTEM 合计** | | **4,785** | 2,640–4,262 | |
| S1 | `_WRITE_BASE`（14 条硬规则） | `writer.py:153-196` | **2,313** | 1,236–1,970 | **STABLE**（按档位格式化） |
| S2 | `anti_ai_block`（lean） | `writer_rules.py:355-390` | **2,472** | 1,404–2,292 | **STABLE**（仅按在场人数/流派微调） |
| S3 | learn 专属追加 | `writer.py:211-217` | 0（story）/ 412（learn） | 0 / 332 | STABLE |
| — | **USER 合计** | | **25,330** | 8,026–9,028 | |
| U1 | `【L0 世界观圣经】` | `memory.py:1679` | 6,000 | 1,714 | **STABLE-per-book**（卷内不变） |
| U2 | `worldview_reminder` | `flowmode/story_flow.py:140-141` | 46 | 26–43 | STABLE |
| U3 | `【防雷同提醒】` | `writer.py:413-417` 硬编码 | 170 | 48–170 | STABLE |
| U4 | 文风范本 wrapper + 2 samples | `writer.py:431-437` + `style_corpus` | 1,244 | 355–1,010 | **VOLATILE** |
| U5 | `【现场必须承接】` | `writer.py:445-462` | 86 | 24–86 | VOLATILE |
| U6 | `【L2 前情时间线】` + 防重演硬规则 | `memory.py:1680` + `writer.py:466-471` | **3,239** | 925–2,539 | VOLATILE |
| U7 | `【L5 上一章结尾原文】` | `memory.py:1684` | 859 | 245–859 | VOLATILE |
| U8 | `【L3 人物状态表】` | `memory.py:1681` | 2,221 | 634–2,221 | VOLATILE |
| U9 | 已注册名词表 + 引入约束 | `memory.py:1689` + `writer.py:486` | 1,693 | 483–1,363 | VOLATILE |
| U10 | `【L3 关键事件记忆】` | `memory.py:1682` | 1,414 | 404–1,414 | VOLATILE |
| U11 | `【L4 相关前文片段】` | `memory.py:1683` | 4,014 | 1,146–4,014 | VOLATILE |
| U12 | `【骨架指引】` | `memory.py:1687` | 736 | 210–736 | VOLATILE（按卷推进缓慢变化） |
| U13 | `【伏笔任务-埋/收】` | `writer.py:502-505` | 51 | 14–51 | VOLATILE |
| U14 | `_seasoning` 文风指令 | `writer.py:267-272` | 132 | 76–124 | VOLATILE（确定性派生自章节标识） |
| U15 | `chapter_info_block` = `story_craft` + 本章信息 + `story_hard_rules`(607) + `_detail_block`(**2,118**) | `story_flow.py:155-168` + `flowmode/base.py:148-199` | **3,187** | 1,267–1,722 | VOLATILE |
| U16 | `output_requirement` | `writer_rules.py:464-477` | 208 | 118–208 | STABLE |
| U17 | `length_target_note` | `writer_rules.py:510-517` | 71 | 36–71 | VOLATILE（随档位） |
| — | **总计** | | **30,115** | **10,666–13,290** | |

**稳定性切分：stable ≈ 10,998 字符（36.5%）；volatile ≈ 19,117 字符（63.5%）。**

### 3.4 对 Prompt Cache 设计的直接含义

1. **可缓存的稳定前缀只有 ~11k 字符（约 36%）**，其中真正跨请求完全不变的只有 S1+S2+U2+U3+U16 = 5,209 字符（约 17%）。
2. **U1（世界观圣经 6,000 字符）是最大的单块**，卷内完全不变 → 天然的第二层缓存键（book_id + volume）。
3. **当前拼装顺序把稳定块与易变块交错**：`S1(stable) → S2(stable) → U1(stable) → U2(stable) → U3(stable) → U4(volatile) → U5 → U6 → …`。U4 之后的 19k 全部无法命中前缀缓存。
   **重写建议**：把 U1–U3 提到 user 最前面作为 **block A（per-book 前缀）**，把 U4–U17 作为 **block B（per-chapter 尾部）**；S1–S3 保持 system。这样缓存前缀可达 **S(4.8k) + A(6.2k) = 11k 字符 ≈ 40%**。
4. **U4（文风范本）为什么必须放在易变区**：它由 `style_corpus.query_style()` 按本章 plot 向量检索（`pipeline.py:6501`），每章不同，且 `query_style` 有 `exclude` 参数做相邻章去重（`pipeline.py:6502`）。
5. **U11（RAG 前文片段）与 U6（时间线）合计 7.3k 字符是第二大块**，且都随章节线性增长，必须在易变区。

### 3.5 learn 模式对照

| 项 | story | learn |
|---|---|---|
| system 合计 | 4,785 | **5,197**（+412 `LEARN_SYSTEM_EXTRA`） |
| `chapter_info_block` | 3,187 | **2,257**（无 `story_hard_rules`，改注入"领域概念引导"） |
| AI 审校 system | 5,859 | **6,213**（+354 knowledge extra） |
| 记忆提炼 system | 4,203 | **5,960**（+1,757 知识卡指令） |

---

## 四、文风控制链为何失效（用户抱怨 #3 / #4）

### 4.1 全链清点：12 个机制，只有 3 个真正改变产物

| # | 机制 | 位置 | 检测什么 | 阈值 | **强制还是建议** |
|---|---|---|---|---|---|
| M1 | `style_metrics.analyze` + `needs_rewrite` | `style_metrics.py:106-164` | 套话密度、对话占比、句长变异系数、章末总结句、开放式收尾、代码泄漏 | `cliche_per_kilo>8` 或 `cliche_hits>=10`；`dialogue_ratio<floor`(0.12/0.05)；`sentence_len_cv<0.35`；命中 `SUMMARY_ENDINGS`/`AI_SCENE_ENDINGS`/`OPEN_TEASER_ENDINGS`；`code_leaks` 非空 | **强制**：`needs_rewrite` → `must_review=True` + `style_notes` 注入 Editor（`pipeline.py:7245-7248`）→ Editor 被要求改写 |
| M2 | `style_metrics.bridge_notes` | `style_metrics.py:183-213` | 章首时间硬切词 + 与上章尾 2 字滑窗零交集 | 硬编码词表 10 个 | **强制**：`must_review=True`（`pipeline.py:7259`） |
| M3 | `novelty.guard_against_duplicate` | `novelty.py:594` | 与历史章句级相似（IDF 加权二字组） | `SENT_MATCH=0.40`、`SENT_STRONG=0.62`、`MIN_COMMON=3`、`IDF_FLOOR=1.0`、`LEN_RATIO=0.30`；near 窗口 4 章（`guard_config.json`） | **强制但可降级**：先试 `_local_rewrite_duplicates`；近邻章局部改写失败 → **放行**（`pipeline.py:7328-7329`）；非近邻失败才 `must_review=True` |
| M4 | `prose_style.analyze`（**最强指标**） | `prose_style.py:869-931` | `cross_chapter_overlap`、`intra_redundancy`、`triple_parallelism`、`ending_pattern`、`top_repeated_ngrams` | 无常驻阈值（诊断型） | **❌ 完全未接线。** 全仓唯一调用者是 `test_prose_style.py:31` |
| M5 | `prose_style.compress_duplicates` | `prose_style.py:510` | 段间 2-gram Dice ≥0.72 且"无新增内容单元" | `duplicate_threshold=0.72`、`duplicate_min_chars=40` | **⚠️ shadow 中**：`prose_style_config.json` `shadow:true` → `pipeline.py:7091` 直接 return，永不改正文。且仅当本章三分诊出 `pacing`/`ai_style` 才调用（`pipeline.py:7016`） |
| M6 | `review_triage.classify_issues` | `review_triage.py:352` | issues 三分诊：hard/style/ignored | `HARD_TYPES={logic,consistency,foreshadow,ooc}`、`STYLE_TYPES={ai_style,pacing}` | **强制但只作用于 hard**：`hard_only_unmet=true`（`review_escalation_config.json`）→ 文风问题"记录 + 报告，但不单独拦章"（`pipeline.py:8197` 注释原文） |
| M7 | `review_triage.derive_issues_from_notes` | `review_triage.py:540` | notes 有可执行结论但 issues 为空 | `NOTES_ACTION_MARKERS`/`NOTES_DEFECT_MARKERS`，上限 8 | **建议**：派生 issues 供分诊，不直接触发改写 |
| M8 | `detail_review`（细纲确定性检查 11 项 + LLM 审校） | `detail_review.py:785,1163` | 动机链、章内自洽、状态一致、章间衔接、场景地点跳变 | `MIN_CONCRETE_DETAIL_CHARS=12` 等 | **❌ 死代码**，全仓无生产调用点 |
| M9 | `outline_lint.lint_outline` | `outline_lint.py:147` | 细纲长度、场景行数、`exit_state[N]` vs `entry_state[N+1]` 连续性、标题近重复 | `detail_lint_enabled`（默认 **False**） | **⚠️ shadow**：`pipeline.py:2600-2603` `blocking=False` → 只发通知不阻断 |
| M10 | 流派 `local_checks` | `genres/builtin/{rules,rebirth,system}.py` | 题材专属规则 | — | **❌ 只 log**：`pipeline.py:7911-7925` 被 `_log_step` 打印，不阻断、不触发重写 |
| M11 | `prose_style.ending_pattern` | `prose_style.py:720` | 章末类型分类（mid_action/omen/summary/time_skip/dialogue/other） | 词表 `_TIME_HEAD`/`_SUMMARY`/`_OMEN`/`_ACTION_TAIL` | **❌ 未接线**（同 M4） |
| M12 | `era_checker` 时代一致性 | `era_checker.py:59` | 现代事物出戏 | `era_check_enabled`（默认 **False**） | **⚠️ 默认关**；开启时**强制**（自动改写原句，`pipeline.py:7876-7886`） |

**净结论：12 个机制里，2 个强制（M1/M2）、1 个条件强制（M3）、1 个默认关但强制（M12）、4 个 shadow/仅报告（M5/M6/M9/M10）、3 个完全未接线（M4/M8/M11）。**

### 4.2 生产数据库实测（最强证据）

对 `backend/learnstory.db`（**只读打开**，`file:...?mode=ro&immutable=1`）中 `chapters.review_json` 的 1,441 条记录统计：

```
chapters with review_json        : 1441
scored chapters (有 Editor 评分)  : 210
  └ 被点名 ai_style / pacing     : 185  (88.1%)
      └ 其中仍拿 score>=7 判 passed: 60   (32.4%)
issue 类型分布 : ai_style 228 > consistency 219 > pacing 204 > other 114
                 > logic 100 > foreshadow 77 > ooc 7   (共 949 条)
```

**读法**：
1. **`ai_style` 是全库第一大问题类型（228 条）**，超过 `consistency`（219）与 `logic`（100）——文风不是"锦上添花"，它是这套系统实测最严重的质量缺陷。
2. **88.1% 的被评章被点名文风问题**，其中 **32.4% 仍被判 passed（score≥7）**。这是 §5.3 "文风仅报告"的直接后果：Editor 自己发现的问题，被自己的 `passed` 判据放过去了。
3. `ai_style + pacing = 432 条`，占全部 issue 的 **45.5%**——将近一半的审校产出是文风类，**而这一类恰好是唯一不拦章的**。审校成本花在了不会产生任何修正的地方。

### 4.3 人物关系的数据库实测

对 `relationships` 表 966 条边的统计：

```
端点类型分布 : 人物-物品 286 (29.6%) > 人物-人物 254 > 人物-地名 141
               > 物品-地名 103 > 物品-物品 32 > 人物-规则 29 ...
人物-人物边数 : 254
distinct relation_type : 191          ← 254 条边用了 191 种不同的关系名
post_processor 给的 9 词受控词表覆盖率 : 31/254 = 12.2%
实际高频值 : 敌对 10 / 血亲 9 / 上下级 6 / 问询者与被问者 6 / 师徒 4
             / 试探与被试探 3 / 监视 3 / 对手 2 / 救援者与被救者 2 ...
```

**读法（这是"人物关系单一"的根因，比任何代码推断都硬）**：

1. **受控词表事实上完全没生效**：`post_processor.py:55` 定义的 9 个「人物-人物」关系词（师徒/敌对/盟友/同门/结拜/血亲/道侣/仇敌/上下级）只覆盖 **12.2%**；其余 **87.8% 是模型每次即兴生成的自由文本标签**（"问询者与被问者"、"试探与被试探"、"控制与拉拢"、"观察与试探"…）。
2. **191 种标签 / 254 条边 ≈ 每种标签平均只用 1.33 次** —— 这意味着**没有任何东西可以聚合、比较、去重或统计"关系多样性"**。一个系统无法回答"这本书有几条敌对关系"（答案是 10），因为同一件事可能被写成"敌对"/"对手"/"仇视"/"对立"。
3. **29.6% 的"关系"根本不是关系**：`人物-物品 286 条` 是库存登记（谁持有什么），它挤掉了关系表 1/3 的容量。这与 `post_processor.py:60` 自己写的"只记特殊绑定/传承/因果，不逐件记常规持有（持有走 state_updates.装备）"直接冲突——**规则写了，模型没守，且没有校验。**
4. 全库唯一一处要求"关系不得全程静态"的文本是 `story_director.py:18`（**仅出现 1 次**），且它约束的是"角色弧光"字段，不是关系表。

### 4.4 ⚠️ `must_review` 是一个**只写不读**的死信号

这是本次审计发现的**最严重的单点缺陷**。`style_metrics.needs_rewrite` 命中后写的是 `must_review = True`（`pipeline.py:7246,7259,7273,7303,7331`），但全仓检索 `must_review` 的全部 25 个命中：

```
pipeline.py:3127,4196,4543,4549,4571,5923,5948,6116,6167,6222,6246,6282,6301,6304,6305
             7123,7246,7259,7273,7303,7331,8363,8403,8419
```

**全部是"写入"或"作为形参透传"，没有任何一处是读取。** 唯一的准读取点是：

```python
# pipeline.py:6301 —— 只写进 trace metadata，不参与任何判断
metadata={"bridge": bool(bridge), "must_review": bool(must_review)},
```

`_commit_chapter(session, book, ch, chapter_data, context, content, must_review, due_reviews)`（`pipeline.py:7122-7123`）把 `must_review` 收进形参后，**从头到尾没有再引用过它一次**。

**真正的审校闸门是另一条链**：

```python
# pipeline.py:7354-7358
_force_flags = bool(style_notes) or _is_turning          # ← 注意：读的是 style_notes，不是 must_review
_do_review, _review_reason = _should_review(
    book.id, is_head_or_tail=(_is_head or _is_tail), force_flags=_force_flags, ...)
```

**读法**：
1. `must_review = True` 是**纯粹的仪式性赋值**。它带来的唯一实际效果，是同一段代码顺带写入的 `style_notes` 文本——而 `style_notes` 只影响 `_force_flags`（强制走审校）与 `_must_fix`（是否采纳模型**自愿**给出的修订稿）。
2. 因此 `style_metrics` 的全部阻断语义，实际强度是：**"强制调用 Editor 一次" + "在 Editor prompt 里加一段警告" + "允许采纳 Editor 自愿给出的修订稿"**。
3. 如果 Editor 返回 `revised_content: null`（`editor.py:319` 明文允许："无必改项时必须输出 null"），**正文一字不改入库**。
4. 这条链解释了 §4.2 的 DB 事实：185 个被点名文风的章里，60 个仍判 passed。

**重写建议**：`must_review` 要么删掉（承认它是 `style_notes` 的冗余别名），要么变成真正的守卫节点——后者是 LangGraph 拓扑里 `style_guard` 的职责（见 §7.3）。

### 4.5 文风范本的实际可用量（实测）

| 指标 | 实测值 |
|---|---|
| 样本数 | 171 |
| 可检索 chunk（带 vec + text） | 213 |
| 每 chunk 截断上限 | **400 字符**（`style_corpus.py:786` `e["text"][:400]`） |
| **全部可检索字符（截断后合计）** | **74,808** |
| 每章实际注入 | 最多 2 段 = **≤800 字符**（`pipeline.py:6509` `snippets[:2]`） |
| **重复样本名** | **45 个**（171 条中） |
| `fp` 指纹字段非空数 | **0** |

**读法**：
1. **语料库总量 74,808 字符 ≈ 一部中篇**，而每章范本 ≤800 字符 vs 规则块 4,745 字符（story）→ **范本 : 规则 ≈ 1 : 6**。（`style_corpus.py:752` docstring 也写明"只注入 1~2 段笔触范本，压缩单次提示词，避免多段风格互相干扰遵循度"——**这是上下文预算逻辑与文风学习逻辑的直接冲突**。）
2. **45 个重复样本名 + `fp` 全为 `None`** → `style_corpus.py:553` 的 `if any(it.get("fp") == fp for it in existing)` 指纹去重逻辑**对这些样本永不生效**（`None == None` 不会触发，因为 `fp` 是空值分支）。即语料库有约 26% 的重复内容在稀释检索质量。
3. 检索判据是**剧情语义相似度**（`style_corpus.py:797`），不是文笔相似度；`_touch_score` 只是 `sims[i] + 0.03 * touch[i]` 的**轻微加权**（`style_corpus.py:800`），无法改变主排序。

### 4.6 Editor 自己确实抓到了"比喻"——但没有用

对全部 Editor issue 文本（`description` + `suggestion`，实测 **165,610 字符**）做关键词统计：

```
描写 466 | 重复 211 | 节奏 173 | 套话 167 | AI味 55 | 比喻 43
注水 29  | 生理反应 24 | 排比 13 | 堆砌 13 | 文艺腔 2
```

**`比喻` 在 Editor 的实际产出里出现 43 次**——LLM 语义审校**抓得到**比喻问题。真实 issue 原文（verbatim）：

> `存在轻微的直接情绪命名，如"心跳如鼓"；以及章末的比喻"像一道随时会断裂的墨痕"稍显刻意和文艺腔…`
> `存在三段式排比或类似堆砌，如"那根线，似乎正朝着她父亲沈庭安当年触碰过的方向延伸。"之前的几个"指向…指向…"结构…`
> `从'窗外…'到结尾的段落…信息密度较低，有注水感…`

**这彻底定位了失败的性质**：不是"检测不到"，而是**检测到了却不产生任何修正动作**（§4.4 的 `must_review` 死信号 + §5.3 的文风不拦章）。

**因此重写的正确方向不是"加检测器"，而是"给已有检测结果接上强制执行通道"。** 这与 §8.7「不要做」清单完全一致：`prose_style.py:51-68` 已用三次负结果证明字符级检测器抓不到注水，而 LLM 语义审校**已经抓到了**——缺的是执行。

### 4.7 反AI规则块"lean 反而更大"（与模块自述矛盾）

`writer_rules.py:36` 自述设计意图：`所以 lean 档**不再增加禁令条数**`。实测：

| 档位 | `anti_ai_block` 渲染字符 | writer system 合计（learn） |
|---|---|---|
| legacy | **1,833** | 4,520 |
| lean（当前生效） | **2,472** | **5,197** |
| | **+639（+35%）** | **+677** |

lean 的第 1 条结构禁令写得很好，但它自身含 **6 个子项**（`writer_rules.py:358-364`），加上新增的"二、场景指南"4 条（`:377-381`）——`writer_rules.py:35-37` 诊断的"17 条并列硬规则，模型一条也守不住"**以另一种形式复现了**。

### 4.8 同一诉求在 writer 侧被写了三遍

| # | 位置 | 层级 | 字符 |
|---|---|---|---|
| 1 | `writer_rules.py:358-364`（lean 第 1 条「不重复结构·本条最重要」） | system | ~640 |
| 2 | `writer_rules.py:417-419`（story 硬规则第 3 条，**同样写「不重复结构·本条最重要」**） | user | ~230 |
| 3 | `writer.py:413-417`（【防雷同提醒】） | user | 170 |

**三处都在讲"不要重复"，没有一处被测量。** 而唯一能测它的 `prose_style.cross_chapter_overlap`（模块自述"已证实是与用户抱怨最相关的字面信号"）**零调用**。

### 4.9 度量基线取自 AI 自身产出

`prose_style.py:19-29` 的全部 baseline（`cross_chapter_overlap` p50 9.8%、`intra_redundancy` p50 6.2%、三段式排比 60 处/33 章）**都取自书 23 自身**——即这条流水线生成物。用它当"是否变好"的判据，等于**用 AI 的输出定义 AI 的正常值**。模块诚实地标注了这一点（`prose_style.py:15` "已用书 23 的 47 章 baseline 反解"），但**没有任何人类写作的健康基线**。

**重写建议**：建立人类文本基线（可用现有 `style_corpus` 的 74,808 字符人类语料算同一批指标），让阈值有外部参照。

### 4.10 为什么"AI 味 / 僵硬描写 / 比喻太多"仍然发生

#### 失效原因 ①：**"比喻"没有任何代码实现——但 LLM 抓得到**

必须严格区分两件事（这是本条抱怨的**核心事实**）：

| 层面 | 事实 |
|---|---|
| **代码层（检测器）** | 全仓 `app/**/*.py` 扫描：`比喻` 仅 **1** 次命中，且是 `review_triage.py:38` 的自认免责声明："它**不能**抓：…纯风格计数（「比喻十余处」）"；`明喻`/`暗喻`/`修辞` 各 **0** 次 |
| **数据层（LLM 实际产出）** | 生产 DB 中 Editor 的 issue 文本里 `比喻` 出现 **43 次**（§4.6），并给出了具体改写建议 |

**→ 不存在"比喻检测器"，但存在"比喻审校结论"。** 而按 §4.4（`must_review` 死信号）与 §5.3（文风不拦章），这个结论**不产生任何修正动作**。

**重写含义**：不要新建"比喻检测器"（字符级不可靠，见 ③）；要把 Editor 已有的语义结论**接进执行通道**。

#### 失效原因 ②：**"多余描写太多"被作者实测判定为不可测，于是被主动放弃**

`prose_style.py:51-68` 记录了三次独立负结果：

| 尝试 | 方法 | 结果 |
|---|---|---|
| 1 | 段落级 Dice | 全 47 章最高 0.519，`>=0.60` 的章数 **0/47** |
| 2 | 句子级重复 | 第 13 章 177 句里只有 1 种重复句 ×2；第 2、31 章 **0** |
| 3 | 「无新增内容单元」占比 | p50 = **0.0%** —— 一本被评为"非常注水"的书得到"零注水"。**已废弃该指标** |
| 4 | 低阈值段级相似度探针 | threshold 0.30 → 全 47 章仅 12 对/9 章；0.40 → 1 对；0.50 → **0 对**。合成对照在 0.30 下命中 **0** |

> **根本原因**：同一拍会**换一批词**来写（「按小灯/弹簧接触不上」→「转个面再插/弹一下就没」），所以字符级相似度天然抓不到；而「有没有引入新专名/数字」也抓不到，因为注水段落**一直在引入新的道具与编号**。

`review_triage.py:42-48` 据此明确"**明确不做的两件事**"：
> `1. **不做「无推进重复 / 细节注水」的确定性闸门**。三条独立路线在书 23 上**全部失效**…假阴性机器比没有闸门更糟（仓库口径：缺省即不通过）。`

**→ 这个失败是"已知的、被文档化的、且被主动放弃的"。重写时必须换成 LLM 语义判官 + 强制的重写闭环。**

#### 失效原因 ③：**套话词表是唯一被执行的检测手段，而它已被自己的作者证伪**

`anti_ai_block` 实测 2,472 字符，其中**第 10 条**是一份 254 字符的套话词表（`style_metrics.CLICHES`，52 词）：

> `10. 禁用以下高频套话及同类表达（检测系统会自动标红）：@@CLICHES@@；——这张词表只是下限，真正要治的重复在第 1 条；`

而 `writer_rules.py:54-61` 已自行证伪：

> `实测书 23：「指腹」出现 90 次、「纸面」213 次、「半寸」48 次 —— 这些词一个都不在任何黑名单里，加黑名单只会让模型换一个同义词、继续用同一套动作结构（「用指腹摩挲纸面」→「指尖捻过纸页」）。重复的不是词，是结构`

**即：作者知道词表无效，但它仍是唯一被执行的检测器（M1 的 `cliche_hits`，实测每章仅 0~3 命中），而结构级指标（M4 `cross_chapter_overlap`）零调用。**

#### 失效原因 ④：**检测结果进了 Editor prompt，但 Editor 的输出没有强制力**

`style_notes` 的流转（`pipeline.py:7247`）：
```
style_metrics.describe(metrics) → style_notes → user += f"⚠️【文风检测警告——重点核查并修正】\n{style_notes}"
```
采纳判据是 `_must_fix = bool(style_notes) or (review.get("passed") is False) or bool(score and score < 7)`（`pipeline.py:7494`）——`style_notes` 非空只表示"**允许**采用 Editor 返回的修订稿"，**如果 Editor 返回 `revised_content: null`，正文一字不改入库**。

叠加 `hard_only_unmet=true` 后，`must_fix_unmet` 只由 hard issues 驱动（`pipeline.py:8199-8200`），文风走 `issue_triage.style`。

**→ 文风警报的最终产物是一个 `notifications.push()` 和一个日志行。**（这就是 §4.2 里 60/185 章"被点名却判 passed"的机制。）

#### 失效原因 ⑤：**"随机文风调味"的信息量近乎为零**

`writer._seasoning`（`writer.py:222-272`）每章给三个短串：

| 池 | 条数 | 每条字符 |
|---|---|---|
| `OPENING_STYLES` | 5 | 21, 20, 22, 24, 17 |
| `FOCUS_STYLES` | 4 | 20, 24, 14, 20 |
| `MOOD_STYLES` | 3 | 14, 16, 14 |

合计输出 **132 字符**，组合空间 5×4×3 = 60 种。例如：

```
【本章文风指令（固定种子，重试时保持一致）】
- 开场方式：以一个具体的感官细节（声音/气味/触感）开场
- 叙事重心：重点用环境细节烘托氛围（光线、声音、温度、气味）
- 基调：整体基调偏紧张压迫，张弛有度
```

`writer_rules.py:32-34` 自认 legacy 的问题：`用 sha1(volume-idx-title) 抽「基调/叙事重心」，与本章实际内容无关：一个「谈判对峙」章可能抽到「重点用环境细节烘托氛围」`。lean 改为按章节信号打分（`_CONFLICT_KW`/`_DIALOGUE_KW`/`_ENV_KW`/`_SOLO_KW`，`_SIGNAL_MIN=2`），**但信息量仍是 132 字符**。

**权重比例：132 字符的风格指令 : 2,472 字符的禁令块 + 24,236 字符的上下文 = 1 : 202。**

#### 失效原因 ⑥：**规则数量本身就超过可遵循上限**

`writer_rules.py:35-37` 的关键实测：

> `三段式排比在书 23 出现 **60 次、覆盖 33/47 章**，而它**早就是 legacy 规则 2 的禁令** —— 这正好证明「17 条并列硬规则，模型一条也守不住」`

实测并列规则数：`_WRITE_BASE` **14 条**（`writer.py:156-195`；第 3 条含 7 子项、第 4 条含 8 子项、第 5 条含 5 子项、第 6 条含 4 子项 → **展开 38 个子规则**）、`anti_ai_block` lean **12 条 + 4 条场景指南**、`story_hard_rules` **9 条**、`_detail_block` **4 条 + 2 条锚点**、`output_requirement` **1 条含 3 子项**、U3 防雷同 **1 条**、U6 防重演 **1 条含 4 子项**、U9 名词引入 **1 条含 3 子项**。

**合计约 45 条顶层规则、约 80 个子规则，全部是"违反即废稿"的等权重祈使句。** 没有优先级、没有冲突消解。

（唯一例外：lean 把"不重复结构"提到第 1 条并写明"本条最重要"（`writer_rules.py:358`）——**这是全库唯一有优先级的设计，值得在重写中放大。**）

### 4.11 人物关系单一（抱怨 #4）

**结论：规划期有"制造关系多样性"的意图，但闸门全部关闭；写作期没有任何机制。**

#### (a) 规划期：有意图、有代码、但闸门关闭

- `story_director.py:26` 硬性要求：ultra 档"至少 2 条主线级 core + **2 条人物关系/情感线** + 2 条对立/探索/局势线"。
- `story_director.py:345-350` `_composition()` 实际计算 `core`/`rel`/`opp` 三类计数（关键词匹配 `relationship/关系/情感/同盟/亲情/爱情`）。
- `story_director.py:354-355` 判定不足则触发"补线小 prompt"（`:376-394`）。

**但是**：
1. 构成闸门 `ultra_structure.validate`（`ultra_structure.py:125` 报错文案"关系/情感线仅 N 条，至少需要 2 条"）由 `ultra_structure_config.json` 控制，实测该文件为 `{"enabled": false, "shadow": true}` → `story_director.py:450` 走 shadow 分支，`:463` 只在 `enabled` 时才 raise。**永不阻断。**
2. 补线失败后 `story_director.py:417-424` 只 `log_problem(level="warn")` 并 **放行**（注释原文：`保留已有主线放行`）。
3. 非 ultra 档 `min_arcs = 2`（`story_director.py:352`）——**默认档位（standard）只要求 2 条线**。

#### (b) 关系内容维度：17 类端点 ≠ 关系多样性

`post_processor.py:54-72` / `schemas.py:272-293` 的"17 类端点组合"**是实体类型对的分类，不是人物关系的分类**：人物-势力、势力-物品、功法-功法、规则-规则、地名-地名……其中真正"人物-人物"的枚举（`post_processor.py:55`）只有 9 个词：

> `师徒、敌对、盟友、同门、结拜、血亲、道侣、仇敌、上下级`

而 `post_processor.py:74-76` 要求 **"出场实体全量重报"**：
> `- 本章实际出场/被提及的每个角色、势力、地点、物品，都必须出现在 relationship_updates 或 state_updates 中；`
> `- 已存在的关系本轮未变化也要显式输出一条 status="维持"（仅确认不重复建边）；`

**→ 成本最低的合规输出就是：把上一章的边原样重发一遍，`status="维持"`。** 模型没有动力写新关系。

#### (c) 写作期：注入给 writer 的关系块是"平铺列表"，且大范围关闭

- `memory.py:1204-1245` 是唯一活着的关系注入路径。但它有两重削减：
  1. `memory.py:1217`：`if participants and not ({ea, eb} & set(participants)): continue` —— **只注入本章在场人物的关系边**。
  2. 输出是分组后的 `- A ↔ B：师徒（描述，维持）【明】` 平铺 bullet（`memory.py:1223`），**没有任何"本章应该推进哪条关系"的指示**。
- `memory.py:1257` 的图谱注入 `graph_context` 由 `audit_config.json` 的 `"graph_inject_enabled": false` 关闭 → `writer.py:481` 的分支永不触发。**这是唯一能给出"关系网络全貌"的块，默认关闭。**
- `memory.py:1446` 附近有一句【人物关系校验】，但只针对**零关系**的角色，且只列 6 个词（`师徒/同门/敌对/旧识/委托/归属`）。

#### (d) 全库没有任何 Agent 或规则以"关系多样性"为目标

搜索 `relationship` / `关系` 的全部生产调用点，职责只有两类：**记录**（post_processor）与**核对不得违背**（editor 的"明暗关系"检查、verifier 的逻辑校验⑤）。

**不存在**："本卷必须新增 N 条人物关系"、"本章必须推进 1 条关系线"、"关系类型分布检查"、"同一关系类型连续出现 N 章即告警" 任何一条。

---

## 五、审校链

### 5.1 每章 LLM 调用序列（实测，`pipeline._commit_chapter`）

以一次正常（未被跳过审校）的章节为例：

| 序 | 调用 | 位置 | 触发条件 | system 字符 | 输入规模 |
|---|---|---|---|---|---|
| 0 | `WriterAgent.write` | `writer.py:330` | 恒有 | 4,785 | ~30,115（见 §3.3） |
| 0b | 长度续写循环 | `writer.py:384` | `len(content) < _FLOOR`，最多 `max_continuations` 次 | 4,785 | 尾 400 字 + 细纲 |
| 0c | 首稿不足整章重写 | `writer.py:361` | `< _HARD_MIN` 且未开 `allow_short_chapter` | 4,785 | 同 0 |
| — | `guard_against_duplicate` + `_local_rewrite_duplicates` | `pipeline.py:7312-7321` | 雷同命中 | — | 局部改写调用 |
| — | **并行启动** `ChapterPostProcessor` | `pipeline.py:7386-7388` | `_do_review` 时与审校并行 | 4,203 | 与 Editor 并行 |
| 1 | **Editor 一审** | `pipeline.py:7410` | `_do_review` | 5,859 | **~17,909**（system + 12,050 user） |
| 2 | **Editor 定向重写**（`score < 6`） | `pipeline.py:7522` | `score<6` 且有 issues，最多 1 次 | 5,859 | ~17,909 + mission |
| 3 | **Editor 硬伤升级重写** | `pipeline.py:7599-7630` | `hard_escalation` + 有 hard issues + `_must_fix` + 未跑过 2 | 5,859 | ~17,909 + mission |
| 4 | **Editor 二审评分**（`score_only`） | `pipeline.py:7721` | 采用了修订稿且 `score<7` | 5,859 | ~17,909 |
| 5 | **Editor 超长二审压缩**（`length_pass`） | `pipeline.py:7777` | `len(content) > cap` | 5,859 | ~17,909 |
| 6 | `EraCheckerAgent`（默认关） | `pipeline.py:7865` | `era_check_enabled=true` | 102 | 3,000 |
| 6b | `EraChecker.rewrite_line` ×≤5 | `pipeline.py:7882` | 有出戏项 | 102 | 单句 |
| 7 | `ChapterPostProcessor`（串行回退或复用并行结果） | `pipeline.py:7899` | 并行失败时 | 4,203 | ~12,518 |
| — | `_apply_style_dedupe`（确定性，shadow） | `pipeline.py:7834` | 三分诊出 `pacing`/`ai_style` | 0（纯代码） | — |

**每章实测 LLM 调用数：3–7 次。最坏情况（低分 + 硬伤 + 超长 + 二审）7 次，其中 4 次是 Editor 的完整 18k 输入调用。**

额外的周期性调用（`audit_config.json`）：`verify_interval=10`（每 10 章全书校验）、`verify_logic_interval=5`（每 5 章逻辑校验）、`entities_report_interval=20`。`VolumeAuditor` 每卷一次。

### 5.2 阈值与"死区"

| 阈值 | 值 | 位置 | 后果 |
|---|---|---|---|
| Editor 判 `passed` | `score >= 7` | `editor.py:111`、`editor.py:351` | — |
| `_must_fix` | `score < 7` 或 `passed is False` 或 `style_notes` 非空 | `pipeline.py:7494` | 允许采用修订稿 |
| 定向重写触发 | `score < 6` | `pipeline.py:7496` | 走 `_targeted_rewrite_once` |
| **死区** | **`score == 6`** | `pipeline.py:7592-7593` 注释 | `must_fix=True` 但重写不触发 → **正文原样入库** |

**死区的实测证据**（`review_triage.py:6-7`，verbatim）：

> `1. **死区**：`pipeline._must_fix` 的阈值是 `score < 7`，而定向重写只在 `score < 6` 触发。47 章里 **28 章恰好 6.0 分** —— 主编点了名、正文按原稿入库。`

代码里保留了这条死区的显式说明（`pipeline.py:8269-8271`，乱码为 GBK 显示问题，原文）：

> `一审判定需修正（score<7）但既无修订稿入账；正文按原稿入库。重写阈值为 score<6，故 score 恰为 6 时不会触发定向重写 —— 此为已知死区。`

**当前缓解**：`review_escalation_config.json` = `{"enabled": true, "shadow": false, "hard_escalation": true, "hard_only_unmet": true, "max_attempts_per_chapter": 1}` → `pipeline.py:7599-7602` 的硬伤升级通道**已开启**，能覆盖 `score==6` 但**带 hard issue** 的章节。**但纯文风问题的 6 分章仍然走死区。**

### 5.3 强制执行 vs 仅报告的最终清单

| 项 | 状态 |
|---|---|
| 章首衔接硬切（M2） | ✅ 强制 `must_review` |
| 套话密度 / 对话占比 / 句长 / 章末总结句 / 代码泄漏（M1） | ✅ 强制 `must_review` + 注入 Editor |
| 雷同（近邻章）（M3） | ⚠️ 局部改写成功即放行；失败也放行（`pipeline.py:7328-7329`） |
| 雷同（非近邻）（M3） | ✅ 局部改写失败 → 强制 `must_review` |
| 超长（`len > cap`） | ✅ 强制 `must_review` + 二审压缩 |
| 主角名缺失 | ✅ 强制 `must_review`（`pipeline.py:7299-7305`） |
| 硬伤（logic/consistency/foreshadow/ooc + 细纲偏离） | ✅ 强制 1 次定向重写（若 `enabled`） |
| **文风问题（ai_style/pacing）** | ❌ **仅记录**（`pipeline.py:8197` 注释原文："文风偏好照样记录在 `issue_triage.style` 里（**记录 + 报告，但不单独拦章**）"） |
| 细纲长度/连续性 lint（M9） | ❌ shadow，不阻断 |
| 确定性重复压缩（M5） | ❌ shadow，不改正文 |
| 细纲 LLM 审校（M8） | ❌ 死代码 |
| 题材本地校验（M10） | ❌ 仅 log |
| 时代一致性（M12） | ❌ 默认关 |

**⚠️ 关键限定**：上表"✅ 强制"一列中的大部分，其强制机制都是**写入 `style_notes` → 强制走一次审校 → 允许采纳 Editor 自愿返回的修订稿**。因为 `must_review` 是死信号（§4.4），**如果 Editor 返回 `revised_content: null`，任何"强制"都不会改变正文**。真正能改变正文的只有三条：M3 的 `_local_rewrite_duplicates`（确定性局部改写）、M12 的 `EraChecker.rewrite_line`（确定性单句替换）、以及 `reviser` 路径（Editor 主动返回修订稿）。

### 5.4 审校链的三个结构性问题

1. **Editor 同时是"审稿人"和"改稿人"**。`editor.py:111-113`：
   > `综合打分 score(0~10)。≥7 且无重大问题则 passed=true；若情节矛盾/伏笔缺失/AI味超标/超长注水导致 <7，输出修订后的完整正文 revised_content。`
   一个 5,859 字符的 prompt 同时要求它：做 20 类检查、输出结构化 issues、给出分数、并在失败时**重写一整章 3,500–6,000 字**。这是重写时最该拆分的 Agent。
2. **审校可以整章跳过，且跳过时伪造满分**。`pipeline.py:7361-7367`：
   ```python
   if not _do_review:
       _log_step(book, "editor", f"⏭ 第{ch.volume}-{ch.idx}章跳过审校（{_review_reason}），直接入账")
       score = 7  # 跳过审校给默认分
       ch.review_json = json.dumps({"score": score, "skipped": True, ...})
   ```
   `review_config.json` 的 `sample_rate` 虽为 1.0（全审），但 `review_config.py:26-35` 的 DEFAULTS 与磁盘文件不一致（`fast[1,1]` vs 磁盘 `fast[1,2]`，`standard[1,2]` vs `[5,7]`），且 `force_flags` 只在 `style_notes` 非空或 beat∈{turning,climax,boss} 时为真。**普通推进章的审校依赖 `sample_rate`。**
3. **`Editor` 的 20 条规则里有 6 条是"必改"，但没有优先级**。`editor.py:27`（AI味）、`:80`（章末收束）、`:86`（事件质量）、`:92`（情绪质量）、`:98`（修订稿禁造新悬疑）、`:104`（细纲履约）都用"必须改写"措辞。第 20 条则自相矛盾地写"细纲偏离只标记不整章重写"（`editor.py:108`）。

---

## 六、流派 / 场景差异化

### 6.1 三层结构与真实覆盖面

| 层 | 文件 | 消费点 | 是否进 prompt |
|---|---|---|---|
| L1 题材卡 `genre_brief()` | `story_genres.py:238-251` | `story_director.py:215-217` | ✅ 规划期 1 次，**305–371 字符** |
| L1' 流派卡 `format_story_flow()` | `story_flows.py` | `story_director.py:219`、`pipeline.py:316-322` → `writer.py:419-426` | ✅ 每章 1 次，**277–328 字符** |
| L2 画像旋钮 `get_profile()` | `genre_profiles.py:28-49`（18 题材）`+52-65`（12 flow） | `genres/builtin/default.py`、`worldbuilder.py:100-103`、`writer_rules.py:268`、`pipeline.py:7237` | ⚠️ 仅部分旋钮有消费点 |
| L3 插件 `genres/` | `default/rules/rebirth/system` | writer / editor / post_processor / pipeline | ✅ 已接线，覆盖窄 |

**⚠️ `genre_profiles.json` 不存在** → `genre_profiles.py:83-87` 走 else 分支，只用代码内 `GENRE_DEFAULTS`；docstring 里的"前端可调（预留）"是空头支票。

### 6.2 六个旋钮的实测注入量

| 旋钮 | 消费点 | 实际注入 | 字符 |
|---|---|---|---|
| `combat_level` | `genres/builtin/default.py:45-55 boss_block()` | boss 章专属提示 | **74**（one_shot）/ **85**（none/low）/ **184**（medium/high） |
| `win_core` | `default.py:62-68 editor_focus()` | Editor system 追加 | **92**（仅 `combat_level ∈ {none,low}` 时注入） |
| `one_shot_ok` | `default.py:48-49` 写端 + `:69-73` 审端 | boss 提示 + 审校提示 | **74** + **55** |
| `has_realm` | `worldbuilder.py:100-103` | 圣经 system 追加 `REALM_SYSTEM_EXTRA` | **169**（`BIBLE_SYSTEM_STORY` 781 → 950，**+21.6%**） |
| `pacing` | **无** | — | **0** |
| `dialogue_floor` | `pipeline.py:7234-7243` → `style_metrics.py:156` + `writer_rules.py:268-272` | 改变 `needs_rewrite` 判定阈值（0.12 或 0.05） | 控制流 |
| `extract_hints` | `flowmode/base.py:213-220` → `post_processor.py:223-225` | 记忆提取器提示 | `rules` **244** / `rebirth` **256** / `system` **304** / default 0 |

### 6.3 判定：**薄覆盖层，不是真差异化**

1. **章节 prompt 里题材的全部影响 = 0–184 字符**，占 writer 总量（30,115 字符）的 **< 0.6%**。
2. **非 boss 章（约 85% 章节）题材影响 = 0**：`default.py:40-43` `if beat != "boss": return []`。
3. **18 个题材里 13 个的 boss 文本完全一致**（`medium` 与 `high` 同文，`none` 与 `low` 同文）。
4. **题材卡长度极差仅 66 字符**：xianxia 371 / urban 359 / scifi 357 / boys_love 356 / suspense 345 … youth 305。**全部是六行同构模板填空**（世界观 / 成长轴 / 命名 / 经典元素 / 读者期待）。
5. **无题材专属校验**：`rules.py:41-77` / `rebirth.py:36-60` / `system.py:41-62` 的 `local_checks` 只在 `pipeline.py:7911-7925` 被 `_log_step` 打印，不阻断（对比 `style_metrics.needs_rewrite` 会置 `must_review=True`）。
6. **无题材专属 Agent**：`genres/base.py:8-19` docstring 宣称"每流派即独立 Agent 栈"，实际 4 个插件中 3 个是 `DefaultPlugin` 子类，只覆写 2–3 个数据方法，**无独立 prompt、无独立 LLM 调用、无独立验证**。

**唯一接近"真差异化"的三项**：`dialogue_floor`（唯一改控制流的旋钮）、`has_realm`（唯一改 prompt 结构小节的旋钮）、`extract_hints`（唯一改提取 schema 的旋钮）。

### 6.4 死代码清单

| # | 位置 | 状态 |
|---|---|---|
| D1 | `genre_profiles.pacing` 全字段（约 30 处） | 💀 **零读取点**，注入 0 字符 |
| D2 | `combat_level="medium"` | 💀 与 `high` 同文，等价死值 |
| D3 | `GenrePlugin.state_schema()` × 3 实现 | 💀 仅 `service.py:64-67 to_dict()` 与 API 展示 |
| D4 | `extra_entity_types` / `extra_relation_pairs` | 💀 同上 |
| D5 | `genres/manager.py:21-22` `book_kind` | 💀 注释自认"暂不改插件选择"，写 `plugin._book_kind` 后无人读 |
| D6 | `genre_profiles.reload()` + 热加载 | 💀 JSON 不存在且无写入口 |
| D7 | `genres/registry.py:41-42 _SCENE_INDEX` | 💀 无插件声明 `scene_ids` → 永远空 dict |
| D8 | `genres/base.py:44 local_checks` | ⚠️ 有消费点但只 log |
| D9 | `writer.py:147,150` boss fallback | 💀 与 `default.py:30-32` 三重复 |
| D10 | `templates/scenes.py` 只有 1 个 key `xianxia` | 💀 `get_template()` 对任何 scene 回落 xianxia |

### 6.5 ⚠️ 一处活体污染 bug

`templates/scenes.py:6-34` 只有 `xianxia` 一个模板，`get_template()`（`:37-38`）对任何 scene 都回落 xianxia：

- `writer.py:307` 仍调 `get_template(context["scene"])`，但 story 路径只用到 `tpl["style_samples"]`（`:428`），被 `story_flow.py:146-150` 对非修仙题材剔除 → 侥幸不污染；`mapping_rules` 被 `story_flow.py:143-144` 返回空串屏蔽 → 也不污染。
- **但 `worldbuilder.py:82,120` 的 `tpl['style_prompt']` 无任何 flow 屏蔽**：`modern_romance` 的圣经生成 prompt 里会写进 `"东方玄幻成长流：可用宗门、功法、器物、境界，但名称与阶梯必须按本书自创…"`（**92 字符**）+ `"风格：…"`。
- `architect.py:2851,2924,2945` 同样注入 `tpl['name']` / `tpl['style_prompt']`。

**→ 任何非修仙题材的书，其世界观圣经与大纲 prompt 里都带着修仙风格指令。**

---

## 七、新 Agent 拓扑建议

### 7.1 设计原则

1. **一个 LLM 调用只做一件事**（当前 `ArchitectAgent` 做 9 件事、`EditorAgent` 做 5 件事）。
2. **审稿与改稿分离**（当前 editor 既评分又重写整章）。
3. **规则只写一份**（当前同一规则在 writer/editor 各写一遍，实测 10.9% 8-gram 重叠）。
4. **每条规则必须有明确的"谁执行、执行不动怎么办"**（当前 45 条规则全部等权、无优先级）。
5. **确定性检查用代码，语义检查用 LLM，两者不许互相冒充**（`prose_style.py:51-68` 已经证明字符级指标测不出"注水"）。

### 7.2 Agent 拓扑（建议 22 个节点）

标注 **[K]** = 保留并重构，**[S]** = 拆分，**[M]** = 合并，**[N]** = 新增，**[D]** = 删除。

#### 规划层（5）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[K] `intent_parser`** | LLM (fast) | 用户一句话意图 → 结构化 brief（题材/主角/基调/结局倾向/必含必避） | 用户输入 | `Brief` pydantic |
| **[K] `story_director`** | LLM (balanced) | 七锚骨架（保留，`story_director.py:12-52` 是全库质量最高的 prompt） | Brief + 题材卡 + 流派卡 | `StoryDesign`（七锚 + volumes + arcs） |
| **[N] `structure_validator`** | **确定性** | 校验 arcs 构成（core≥2/rel≥2/opp≥2）、milestones 数量、beats 只铺首卷、无终局泄露 | `StoryDesign` | `ValidationResult`（**阻断**） |
| **[M] `arc_refiner`** | LLM (balanced) | 合并 `story_director` 的补线逻辑（`story_director.py:342-427`）+ `story_arc_planner`（卷导演） | 缺失的线类型 + 故事设计 | 补线 `arcs[]` |
| **[S] `volume_planner`** | LLM (balanced) | 从 `ArchitectAgent._plan_volume_segmented`（`architect.py:2523`）拆出 | 故事设计 + 上卷梗概 | `VolumePlan` |

#### 大纲层（4，从 `ArchitectAgent` 拆出）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[S] `skeleton_writer`** | LLM (balanced) | 骨架：标题 + 知识点 + beat + 参与者（`architect.py:1428` 那条路径） | 卷规划 + 档位规则 | `ChapterSkeleton[]` |
| **[S] `detail_writer`** | LLM (balanced) | plot + end_hook + plot_detail + entry/exit_state（`architect.py:1793` 那条路径） | 骨架 + 前序章节 | `ChapterDetail[]` |
| **[N] `detail_lint`** | **确定性** | 复活 `outline_lint` + `detail_review.deterministic_issues` 的 11 项检查，**并真正阻断** | 全书细纲 | `LintResult`（**阻断**） |
| **[N] `detail_critic`** | LLM (fast) | 因果与动机审校（复活 `detail_review._PROMPT_HEAD`，`detail_review.py:804`） | 细纲批 | `{verdict, issues[]}` |

#### 设定层（3）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[M] `worldbuilder`** | LLM (balanced) | 圣经 + 人物表**合并为一次调用**（当前是 2 次：`worldbuilder.py:138` + `:158`） | 故事设计 + 题材卡 | 圣经 + `Character[]` |
| **[K] `bible_reviewer`** | LLM (balanced) | 保留（`bible_reviewer.py:28-94`），阈值 `MIN_SCORE=7` | 圣经 | `{score, issues[], revised_bible}` |
| **[N] `canon_extractor`** | LLM (fast) | 从圣经抽取结构化 canon（力量体系/纪元/势力/规则），供确定性校验用 | 圣经 | `Canon` pydantic |

#### 写作层（4）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[S] `scene_planner`** | LLM (fast) | 章内节拍规划：3–5 场，每场目标/阻碍/结果/代价 | 细纲 + 状态 + 关系 | `ScenePlan[]`（**新增**：把"写"与"想"分离） |
| **[K] `writer`** | LLM (balanced) | 只写正文。**system 只放风格与格式，规则全部下移到 deterministic guard** | ScenePlan + 上下文 | 正文 |
| **[N] `style_critic`** | LLM (balanced) | **独立**语义文风审官：比喻密度、描写推进性、句法单调、AI 腔 | 正文 + 前 5 章 | `{verdict, spans[]}` |
| **[N] `continuity_critic`** | LLM (fast) | 单章一致性（伤势/持有物/位置/关系明暗），替代 editor 的第 3/7/8/9/12/14/17 条 | 正文 + 状态表 | `{issues[]}` |

#### 审校层（3）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[S] `review_judge`** | LLM (fast) | **只评分 + 列 issues**，绝不改稿（`editor.py` 的 `score_only=True` 路径，`pipeline.py:7721`） | 正文 + 全部 critic 报告 | `{score, issues[], pass}` |
| **[S] `reviser`** | LLM (balanced) | **只改稿**，输入是结构化的 issue 清单（`editor.py` 的 `mission=` 路径，`pipeline.py:7533`） | 正文 + issues | `{revised_content}` 或 `{local_fixes[]}` |
| **[N] `revision_verifier`** | LLM (fast) | 复核修订稿是否真的修掉了指名的问题 | 原稿 + 修订稿 + issues | `{resolved[], remaining[]}` |

#### 记忆层（3）

| Agent | 类型 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **[S] `memory_extractor`** | LLM (fast) | **只提记忆增量**（state/relationship/events/claims/hang/entities），去掉知识卡 | 正文 + 快照 | `MemoryDelta` |
| **[S] `knowledge_card_maker`** | LLM (fast) | **只做知识卡**（learn 专属，与记忆解耦） | 正文 + 知识点 | `Card[]` |
| **[N] `relationship_curator`** | LLM (fast) | **关系专员（新增）**：读关系表 → 指出本卷内的关系单调（同类型连续 N 章、无新边、无明暗变化）→ 产出"下一章应推进哪条关系"的硬指令 | 关系表 + 最近 N 章 | `RelationshipBrief` |

#### 守卫层（全确定性，0 LLM）

| Node | 检查 | 动作 |
|---|---|---|
`style_guard` | `style_metrics` + `prose_style`（**第一次真正接线** `cross_chapter_overlap`/`triple_parallelism`/`ending_pattern`） | 超阈 → 路由到 `style_critic` |
| `continuity_guard` | 名字/死亡/持有物/位置（代码可判的部分） | 命中 → 路由到 `reviser` |
| `length_guard` | 字数区间 | 超上/下限 → 续写或压缩 |
| `duplicate_guard` | `novelty.guard_against_duplicate` | 命中 → 局部改写 |
| `canon_guard` | `entities.format_for_writer` + 专名首现章 | 无依据专名 → 阻断 |

### 7.3 LangGraph 拓扑（有向图，含条件边）

```
intent_parser → story_director → structure_validator ─(fail)→ arc_refiner ─┐
                                                          └─(pass)────────┴→ volume_planner
                                                                                  ↓
                                                                            skeleton_writer
                                                                                  ↓
                                                                            detail_writer ⇄ detail_lint (fail → detail_writer)
                                                                                  ↓
                                                                            detail_critic ─(fail)→ detail_writer
                                                                                  ↓
                                                                    worldbuilder → bible_reviewer ─(fail)→ worldbuilder
                                                                                  ↓
                                                                            canon_extractor
                                                                                  ↓
        ┌─────────────────────────────── 每章循环 ───────────────────────────────┐
        │  scene_planner → writer → ┌ style_guard ─(hit)→ style_critic ─┐        │
        │                           ├ continuity_guard ─(hit)→ continuity_critic │
        │                           ├ length_guard ─(hit)→ writer(续写)  │        │
        │                           └ duplicate_guard ─(hit)→ local_rewrite      │
        │                                        ↓ (all clear)                   │
        │                                  review_judge ─(fail)→ reviser → revision_verifier
        │                                        ↓ (pass)                        │
        │                    ┌───────────────────┴───────────────────┐           │
        │              memory_extractor                    knowledge_card_maker   │
        │                    ↓                                                   │
        │            relationship_curator ────────────→ (下一章 scene_planner)    │
        └────────────────────────────────────────────────────────────────────────┘
```

**关键：所有 `_guard` 是纯代码节点，不进 LLM；所有 `*_critic` 是独立 LLM 节点，输出结构化 JSON；`reviser` 只接受结构化 issues，不接受散文 notes。** 这就解决了当前最大的结构缺陷——"检测到了但没人执行"。

### 7.4 现存 Agent 的处置表

| 现状 | 处置 | 理由 |
|---|---|---|
| `ArchitectAgent`（2,784 行） | **拆 4**：`skeleton_writer` / `detail_writer` / `volume_planner` / 保留编排 | 9 个 LLM 任务挤在一个 system prompt 里，靠尾部补丁纠偏 |
| `EditorAgent`（5,859 字符 system） | **拆 3**：`review_judge` / `reviser` / `continuity_critic` | 一个 prompt 同时做 20 类检查 + 评分 + 重写整章 |
| `ChapterPostProcessor`（5,960 字符 system） | **拆 2**：`memory_extractor` / `knowledge_card_maker` | 记忆与知识卡是正交职责，learn/story 已经用两套 system 硬分叉 |
| `WriterAgent` | **拆 2**：`scene_planner` / `writer` | writer 现在的 system 是 4,785 字符规则，user 是 25,330 字符上下文；没有"想"的环节 |
| `StoryDirectorAgent` | **保留**（+ 拆出补线为 `arc_refiner`） | prompt 质量最高，2497 字符承载七锚 |
| `StoryArcPlannerAgent` | **合并**进 `arc_refiner` | 与 director 的补线逻辑重复 |
| `researcher` 6 个 Agent | **纯 learn 保留，但 story 重写可整体丢弃** | story 路径不调用 |
| `GateAgent` | 删除或降为确定性规则 | 565 字符换来一个布尔判断 |
| `EraCheckerAgent` | 合并进 `continuity_guard` | 102 字符，默认关 |
| `VolumeAuditor` | 保留（改为确定性+LLM 混合） | 卷末体检有价值 |
| `VerifierAgent` | 保留，合并 3 合 1（专名/漂移/逻辑共用一次调用） | 三次调用共享同一份正文上下文 |
| `detail_review`（1,273 行） | **复活为 `detail_lint` + `detail_critic`** | 这是全库唯一针对"细纲因果"的检查，却是死代码 |
| `prose_style` | **接线为 `style_guard`** | 指标已实测校准过 baseline，只差接上 |
| `genres/` 插件系统 | **不要移植** | 4 插件 × 8 方法，生产只用 3 个；扩展性押在这里没有回报 |
| `outline_lint` | 复活为 `detail_lint` 的一部分并**开启阻断** | |

---

## 八、Prompt 设计原则

### 8.1 分层：把 prompt 拆成 4 个显式层

当前所有内容混在一个 `system` 字符串 + 一个 `user` 字符串里，无法缓存、无法版本化、无法 A/B。重写时必须显式分层：

| 层 | 内容 | 变化频率 | 大小目标 | 缓存键 |
|---|---|---|---|---|
| **L0 · 契约** | 输出格式 + 角色身份（1 段，≤300 字符） | 永不变 | 300 | 全局 |
| **L1 · 静态规则** | 该 Agent 的任务规则（≤1,200 字符） | 版本级 | 1,200 | `agent_id + rules_version` |
| **L2 · 书级前缀** | 世界观圣经 + canon + 术语表 + 风格基线 | 每书 | ≤8,000 | `book_id + volume` |
| **L3 · 章级尾部** | 本章细纲 + 状态 + 关系 + 前文 + 记忆 | 每章 | ≤12,000 | — |

**实测依据**：当前 writer prompt 30,115 字符中，稳定部分 10,998（36.5%）、易变 19,117（63.5%）。分层后 L0+L1+L2 ≈ 9,500 字符可命中缓存前缀（31.5%），且**顺序正确**（当前 U4 文风范本在 U1 圣经之后，把稳定区切成两半）。

### 8.2 规则预算：硬上限 1,200 字符 / Agent

实测现状：writer 侧规则文本 5,300 字符 / 约 45 条顶层规则 + 80 子规则；editor 5,191 字符。**`writer_rules.py:35-37` 已经用实测证明"17 条并列硬规则，模型一条也守不住"。**

建议：
- **每个 Agent 的规则块 ≤1,200 字符，顶层规则 ≤7 条**（认知负荷的实测边界，参考 `_LEAN_RULE_9` 单行替换的成功经验）。
- **规则必须有优先级**：第 1 条写"本条最重要"，并明确"与其余规则冲突时以本条为准"（lean 档的唯一正确设计，`writer_rules.py:358,375`）。
- **删除所有可数配额**（`writer_rules.py:70-74` 的论断是对的：`只要配额还在 prompt 里，任何数字都是错的`）。

### 8.3 规则单一来源：一条规则只写一次

当前 8-gram 重叠 10.9% 的直接修法：**规则进确定性守卫的配置，不进 prompt。** prompt 里只保留"这条守卫存在且会阻断"的一句话。

| 规则类型 | 归属 | prompt 里说什么 |
|---|---|---|
| 可确定性判定的（伤势/持有物/位置/名字/字数/专名） | `*_guard` 代码 | **什么都不说**，守卫直接拦 |
| 结构性文风的（重复拍/排比/章末形状） | `style_guard` + `style_critic` | 只说"重复的判据是结构不是词"（1 条） |
| 语义判断的（因果/动机/关系张力/比喻是否服务剧情） | `*_critic` | 只说"由谁判、判什么"（1 条） |
| 纯风格偏好（基调/重心/开场） | L1 静态（≤150 字符） | 保留 `_seasoning`，但**权重必须提高**：从"三个短串"改成"一段 200–400 字符的风格锚" |

### 8.4 显式优先级与冲突消解

**当前冲突的实例**：`writer.py:188` 硬规则 9「章末停在未收束拍」与 `writer_rules.py:374` lean 第 11 条「可以让本章本地事件收束」直接矛盾；`_LEAN_RULE_9`（`writer_rules.py:435-439`）用"按行首 `9. ` 结构化定位替换一整行"的方式打补丁解决——**这个补丁手法本身就是"prompt 没有分层"的症状**。

**建议**：规则用结构化数据定义，而非字符串模板：

```yaml
- id: ending_shape
  priority: 1          # 1 = 最高
  scope: [writer]
  text: "章末必须留下让人想翻下一页的下一步…"
  overrides: [generic_rule_9]     # 显式声明覆盖哪条
  enforced_by: style_guard        # 谁执行
  on_violation: route_to_critic   # 违反了怎么办
```

渲染时按 priority 排序、按 scope 过滤、按 overrides 移除被覆盖项。**这样"lean 替换第 9 行"就变成一个 `overrides` 声明，而不是一次字符串手术。**

### 8.5 缓存友好的拼装顺序

```
[system]
  L0 契约（300）
  L1 静态规则（1,200）            ← 以上按 agent_id+rules_version 缓存
[user]
  L2 书级前缀
    世界观圣经 / canon / 术语表（≤8,000）   ← 按 book_id+volume 缓存
  L3 章级尾部（严格按"变化频率从低到高"排序）
    骨架指引 → 人物状态 → 关系 → 时间线/事件 → RAG 前文 →
    上一章结尾 → 细纲/钩子/锚点 → 文风范本 → 长尾指令（输出要求 + 字数）
```

**关键约束**：
1. **L2 必须是 user 的第一块**（当前圣经是第一块 ✓，但后面紧跟着易变的文风范本 ✗）。
2. **长度指令与输出格式必须放在最末尾**（当前 `output_requirement` 已在尾部 ✓，这是为数不多的正确设计，`writer.py:309` 注释称之为"recency 优先"）。
3. **文风范本移到 L3 末尾**：它是每章不同的 800 字符，放在中段会把后面 15k 全部推出缓存窗口。

### 8.6 版本化与 A/B（当前完全缺失）

现状：唯一开关是 `writer_rules_config.json` 的全局单例 `rule_profile`（`writer_rules.py:85` 模块级 `_cache`），无 book/request 维度；`prose_style_config.json` 等三处的 `"experiment"` 字段**无任何代码读取**；`benchmark/long_memory_prompt_shadow.py:101-102` 自认 `"quality_comparison": "not_run_no_llm"`。

**建议**：
1. **Prompt 资产化**：每个 Agent 的 L1 层为一个带 `version` 的注册项，落库时可写入 `prompt_version`（当前 `trace_config.json` 的 `capture_prompt_hash` 只存 sha256，`models.py:445` 明确不存 prompt 正文 → **无法回放**）。
2. **per-book 变体路由**：`book_id → {agent_id: prompt_version}`，让"书 24 用 lean，书 25 用 legacy"变成数据而不是改全局文件。
3. **质量归因**：把 `review_score` / `style_metrics` / `prose_style.analyze` 的指标按 `prompt_version` 聚合。**当前这些指标已经在计算或极易计算，只是没有和 prompt 版本关联。**

### 8.7 明确不要做的事（有实测背书的负面结论）

| 不要做 | 证据 |
|---|---|
| **不要为"注水/无推进重复"建确定性闸门** | `review_triage.py:44-48`：三条独立路线在书 23 上全部失效；`prose_style.py:59-62`：段级 Dice 最高 0.519，0/47 章 ≥0.60；合成对照在 0.30 阈值下命中 0 |
| **不要靠删词黑名单治重复** | `writer_rules.py:54-61`：「指腹」90 次、「纸面」213 次、「半寸」48 次，**一个都不在任何黑名单里**；加黑名单只会让模型换同义词、继续用同一结构 |
| **不要把"总结式结尾/时间流逝收尾"当硬伤** | `review_triage.py:49-53`：书 23 实测 `summary 0 / time_skip 0`，这两类措辞一次都没出现，当硬伤只会制造假阳性 |
| **不要再增加禁令条数** | `writer_rules.py:35-37`：三段式排比出现 60 次覆盖 33/47 章，**而它早就是 legacy 规则 2 的禁令** |
| **不要把"反复写生理反应"当成情绪呈现方案** | `writer_rules.py:63-68`：这变成了「攥拳/咬舌尖/指腹摩挲」被反复重写的许可证 |
| **不要指望 genres/ 插件系统提供题材差异化** | 4 插件 × 8 方法，生产只用 3 个；`pacing` 零消费点、`combat_level` 3 态退化、18 题材 boss 文本 13 个完全相同 |
| **不要以为"缺检测器"——检测器不缺，缺的是执行** | Editor 的 LLM 语义审校在真实 DB 里抓到 `比喻` 43 次、`描写` 466 次、`AI味` 55 次（§4.6）。失败点是 `must_review` 死信号（§4.4）+ 文风不拦章（§5.3） |
| **不要把度量基线建立在自家产出上** | `prose_style.py:19-29` 的全部阈值（p50 9.8%/6.2%/60 处排比）取自书 23，即本流水线的生成物 → 用 AI 的输出定义 AI 的正常值（§4.9） |
| **不要写"仪式性赋值"的信号** | 全仓 25 个 `must_review` 命中中**零个读取点**（§4.4）。这类赋值会让人误以为已有闸门；重写时每个信号必须有明确的消费者，否则删掉 |

---

## 附录 A · 测量方法

所有字符数由以下只读脚本实测（脚本位于 `E:\AI_project\story_commandcode\doc\research\_tools\`，**未在 `story_llm` 下写入任何文件**）：

| 脚本 | 用途 |
|---|---|
| `prompt_scan.py` | 扫描 `backend/**/*.py` 的三引号字符串字面量，输出字符数与文件:行号；支持 `summary` / `perfile` / `show <pattern>` / `json <path>` |
| `measure_writer_prompt.py` | import 生产代码，实测 `writer_rules` 各函数的真实渲染字符数、风格池长度 |
| `measure_blocks.py` | 通过"逐块抽取后 diff 全文"的方式，实测 `WriterAgent._build_prompt` 每个块的字符数 |
| `measure_agents.py` | 实测全部 16 个 Agent 的 system prompt 字符数（含按 mode 渲染后的值） |
| `budget.py` | 生成 §3.3 的预算分解表 |

**生产数据库统计（§4.2 / §4.3 / §4.6）**：以 `sqlite3.connect('file:learnstory.db?mode=ro&immutable=1', uri=True)` **只读**打开 `backend/learnstory.db`，读取 `chapters.review_json`（1,441 条）与 `relationships`（966 条）。已实测验证本会话的脚本**不会**触碰该库：

```
BEFORE import: learnstory.db=18:37:52.555  learnstory.db-wal=18:57:13.894  learnstory.db-shm=19:36:40.593
AFTER  import: learnstory.db=18:37:52.555  learnstory.db-wal=18:57:13.894  learnstory.db-shm=19:36:40.593
```

（`app/config.py` 读取 `.env` 的 `DATABASE_URL`，但 `os.environ.setdefault` 在导入前已注入临时库路径并优先生效，实测确认 `effective DATABASE_URL -> ...\Temp\_pcheck.db`。所有测量脚本均使用 `%TEMP%` 下的临时 sqlite 文件。）

**token 估算口径**：无法安装 `tiktoken`（本会话不允许安装依赖），故采用区间估计：
- 中文/全角字符：下界 **0.60 tok/字**（DeepSeek / cl100k 类 BPE 的汉字压缩率），上界 **1.00 tok/字**（保守）。
- ASCII：**1 token / 3.5 字符**。
- §3.3 表格中部分块的 token 区间较宽，是因为该块的填充内容用 ASCII 占位符测量（字符级稳定，但 token 率与真实中文不同）；行内已标注 `stable/volatile` 供缓存设计使用，**字符数是精确值，token 是区间估计**。

## 附录 B · 关键文件索引

| 关注点 | 文件:行 |
|---|---|
| Writer prompt 拼装 | `app/agents/writer.py:274-566`（`write()` + `_build_prompt()`） |
| Writer system 三段 | `app/agents/writer.py:290,292,294,296` |
| 反AI规则唯一来源 | `app/writer_rules.py:355-390`（lean）、`:296-329`（legacy冻结） |
| lean/legacy 开关 | `app/writer_rules.py:83,154-161` ← `backend/writer_rules_config.json` |
| 上下文构建 | `app/memory.py:1143-1691`（`build_context`）、`:1711-1784`（`snapshot_for_extraction`） |
| 文风确定性度量（**未接线**） | `app/prose_style.py:869-931` |
| 文风启发式（**唯一活着的**） | `app/style_metrics.py:106-164` |
| 审校三分诊 | `app/review_triage.py:352-405` |
| 死区与升级 | `app/pipeline.py:7461,7494-7496,7592-7602,8190-8271` |
| 审校跳过伪满分 | `app/pipeline.py:7361-7367` |
| 每章调用序列 | `app/pipeline.py:7312,7410,7522,7599,7721,7777,7834,7865,7899` |
| 细纲 lint（shadow） | `app/outline_lint.py:147`、`app/pipeline.py:2578-2621` |
| 细纲 LLM 审校（死代码） | `app/detail_review.py:804-899,1163-1250` |
| 流派插件 | `app/genres/base.py`、`app/genres/manager.py`、`app/genres/builtin/*.py` |
| 流派画像旋钮 | `app/genre_profiles.py:28-68` |
| 场景模板（只有 1 个） | `app/templates/scenes.py:6-38` |
| Agent 注册表 | `app/agents/registry.py:65-138` |
| Agent 基类与 JSON 修复回灌 | `app/agents/base.py:33-194` |
| 结构化 schema | `app/agents/schemas.py:322-472` |
