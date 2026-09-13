# 02 · 记忆 / 账本 / 知识图谱 / RAG 深度审计

> 对象：`E:\AI_project\story_llm\backend\`（**只读**，全程未创建/修改/删除任何文件）
> 范围：`memory.py`(3202) · `entities.py`(1213) · `graph.py`(325) · `graph_summary.py`(376) · `hybrid.py` · `vec_index.py` · `novelty.py`(697) · `local_embed.py` · `embedding_settings.py` · `bible_inject.py`(123) · `bible_rebuild.py`(199) · `drift.py`(190) · `character_delta.py`(270) · `chapter_revisions.py`(705) · `long_memory_prompt.py`(70) · `commit_recovery.py`(212) · `recovery_orchestrator.py`(20)；辅以 `pipeline.py` / `config.py` / `audit_config.py` / `models.py` / `agents/post_processor.py` / `agents/editor.py` / `agents/architect.py` / `flowmode/base.py` 调用点核查 + 生产库只读实测。
> 口径：每条判断带 `file:line`；**"代码存在但从未被调用"与"代码根本不存在"严格区分**。
> 只读声明：为统计账本规模执行了 SQLite 只读查询。该校为 WAL 库，任何连接都会触碰瞬时 `learnstory.db-shm`；已复核 `pragma integrity_check = ok`，主库 mtime 与字节数审计前后一致（18:37:52 / 8,581,120 B），数据无变更。

---

## 概览

### 0.1 小结

**L0–L4 命名是真的，但"层"是 prompt 装配的产物，不是架构分层。** `memory.py:1-6` 定义五层，`build_context` docstring 自称"组装 L0~L4"（`:1144`），返回字典 `bible/timeline/states/rag_snippets` 逐项对应。但五层全由**同一个同步函数** `build_context()`（`:1143-1691`）一次拼出：无独立层边界、无独立写入时机、无独立失效策略。真实存储只有三种：**文件（bible.txt / timeline.txt / rag.json / entities.json）+ SQLite 账本表 + 进程内缓存**。

两条投诉的根因都不是"层没分层"，而是具体机制错配：

| 投诉 | 根因 | 定位 |
|---|---|---|
| 伤势不恢复 | **规划层没有损伤槽位**：恢复须由当章治疗事件触发，而唯一能排节拍的 architect 拿不到损伤状态 | `memory.py:2114-2121`、`architect.py:1795-1808` |
| 实体/势力漂移 | `势力` 不是合法实体类型 → 无节点；graph 注入默认关；`last_chapter` 跨卷比较必假 | `entities.py:17`、`graph.py:36`、`audit_config.py:58`、`entities.py:596` |

### 0.2 生产开关实测（读磁盘真实值，非默认值）

```json
// backend/revision_config.json
{ "chapter_revision": { "enabled": false, "shadow": false } }
// backend/character_delta_config.json
{ "enabled": false, "shadow": false, "projection_mode": "legacy" }
// backend/audit_config.json（节选）
"graph_inject_enabled": false,      "graph_summary_enabled": false,
"drift_align_enabled": true,        "drift_beat_enabled": true,
"drift_enable_correction": false,   "bible_relevant_inject": true,
"graph_inject_max_nodes": 40,       "graph_inject_max_chars": 1500
```

**本次范围内约 40% 的"高级"记忆机制在生产中关闭。**

**影子表实测（生产库 8 本书）——全部 0 行：**

```
chapter_revisions          = 0      chapter_commit_manifests = 0
character_state_deltas     = 0      book_summaries           = 0
foreshadow_actions         = 0      relationship_actions     = 0
story_events WHERE is_active=0  →  0     （全书 active，is_active 过滤是死代码）
drift_log                  = 102 行，全部 kind=align / level=medium（beat / high / low 一条都没有）
foreshadowings             = 539 行，其中 resolve_ch>0 仅 125 行（77% 让 drift B2 短路）
```

### 0.3 数据规模实测

```
-- backend/learnstory.db (8.58 MB)
book  8: chapters=286  char_states=20  rels=64   events=507  fs=389
book 23: chapters=400  char_states=18  rels=739  events=224  fs=70
book 18: chapters=350  char_states=6   rels=103  events=98   fs=39
book 24: chapters=13   char_states=13  rels=38   events=40   fs=0

-- storage/story/books/{id}/rag.json
book  8: entries=1190  chars=667041  key_chunks=0    dims={1024:1190}  # 27.75 MB
book 23: entries=446   chars=252145  key_chunks=426  dims={1024:446}   # 10.41 MB
book 18: entries=309   chars=175514  key_chunks=180  dims={1024:309}   #  7.22 MB
book 24: entries=110   chars=63192   key_chunks=43   dims={1024:110}   #  2.57 MB
```

`key_chunks` 在 book 8 是 0、book 23 是 426——该字段是**写入时快照**（`memory.py:446`），随伏笔回收动态变化；老书写入时功能未上线故整本为 0。查询侧正确地忽略它改用 `_key_chapters()` 缓存（`:595`），字段本身白占体积。

---

## 五层记忆实测

### 1.1 L0 全局圣经

纯文本 `bible.txt`（`docstore.py:108-119`）；开书期 worldbuilder 写，`bible_rebuild` 按需；**每章**读（`memory.py:1616`）注入 writer `context["bible"]`（`writer.py:411`）；实测 **3108 字符**（高度稳定）。

`memory.py:1613-1616` 记录了一次**"L0 每章静默为空"的生产事故**：

```python
# 圣经权威存储在 bible.txt（bible_path）。book.bible 列在重开生成时被清空、
# 且《执道者》式新书只写 bible_path → 必须读文件，否则每章 L0 世界观圣经静默为空、
# 世界观/境界/纪元约束失效。文件缺失时回退列值，兼容旧数据/迁移。
bible = docstore.read_bible(book.id) or book.bible or ""
```

L0 之后被**三次字符串追加污染**，全写进同一个 `bible` 变量（`:1617-1620`、`:1665-1666`）：

```python
if vol_note:            bible += f"\n\n【L1 当前卷】{vol_note}"
if prev_vol_summaries:  bible += f"\n\n【L1 前卷摘要】\n{prev_vol_summaries}"
if genre_state:         bible += "\n\n" + genre_state
```

于是 `context["bible"]` **混装 L0+L1+流派状态**，下游只有一个键名。更糟的是顺序耦合：**先拼接、后裁剪**（`memory.py:1627`），追加段也参与 `_split_blocks` 与相关度打分——**被裁掉时 L1 卷上下文静默消失，无日志**。

**`bible_inject` 按需裁剪对一半的书完全失效**（实测 8 本书的 bible.txt）：

```
\learn\books\3\bible.txt    len= 2947  ##= 0   【】=  0    ← 既无 ## 也无【】
\learn\books\5\bible.txt    len= 1649  ##= 0   【】=  5
\learn\books\7\bible.txt    len= 3324  ##= 0   【】= 18
\story\books\23\bible.txt   len= 5824  ##= 0   【】= 14
\story\books\11\bible.txt   len= 6786  ##=14   【】= 13
\story\books\18\bible.txt   len= 7631  ##=10   【】= 13
\story\books\24\bible.txt   len= 6607  ##= 9   【】=  9
\story\books\8\bible.txt    len= 4231  ##= 8   【】=  8
```

`bible_inject.py:49` 只认 Markdown `## ` 行分块，而 worldbuilder 模板产出的是 `【世界观】…` 方括号小节（`agents/worldbuilder.py:18-24`）。**4/8 本书 `##=0` → `_split_blocks` 返回单块 → `bible_inject.py:96-97 if len(blocks) <= 1: return bible` 整本原样全量注入，按需裁剪完全无效。** book3 连 `【】` 都没有，锚点一个也识别不到。更反直觉的是 `bible_inject.py:121-123`：**一个相关块都没选中时反而返回全文**，与压缩目标相反。

另外还有**三套互不知情的预算**：`bible_inject` 的 `bible_inject_max_chars`（`audit_config.py:79` 默认 3500，而 `bible_inject.py:39` 的兜底默认写成 4200——**两处不一致，4200 是死值**）、editor 的 `context['bible'][:1800]` 硬截断（`editor.py:189`）、era_check 的 `_bible[:600]`（`pipeline.py:7868`）。

### 1.2 L1 卷级上下文

无独立存储，派生自 `book.outline` 的 `volumes[]` + `volume_constraints{}`（`:1595-1611`），**拼进 `bible`**。另有一块独立键 `skeleton_guide`（`:1631-1656`）：从 `conflict_curve` + `character_arcs` 取本章冲突阶段与 ±3 章人物弧光转折点，最多 4 条——**五层里分层最干净的一块**。`:1600-1601` 记录 schema 漂移史（`vol_goal` / `goal` 双 schema，"硬取 `['goal']` 曾 KeyError 卡死多卷书第2章起全部写作"）。

> ⚠️ **`graph_summary` 产出的 global/volume/community 三级摘要不是 L1。** 它写 `BookSummary` 表，而全仓 grep `BookSummary` 只命中 `graph_summary.py` 与 `models.py`——**没有任何 prompt 读它**，唯一消费者是 GET 端点（`main.py:4057-4065`）。且生产开关关、表 0 行。**`BookSummary` 是只写不读的孤岛。**

### 1.3 L2 时间线

`list[str]`，每行 `第{v}-{i}章《{title}》：{summary}`，存 `timeline.txt`（`docstore.py:125-146`）。每章 `update_after_chapter`（`:3039-3048`）**同章替换语义**防重复累积；实测注入 **945–1136 字符**。

**`_compact_timeline`（`:314-351`）是全系统唯一正确的分级压缩实现**：明细超 `TIMELINE_COMPACT_THRESHOLD=40`（`:308`）时旧行按卷聚合为 `【第X卷梗概】`，最近 `TIMELINE_RECENT_KEEP=10`（`:309`）章保持逐章明细。

读取侧再做三层预算（`:1523-1557`）：`SHORT_WINDOW=5` / `SHORT_MAX=2000` / `MID_MAX=2000` / `LONG_MAX=1000`。本卷最近 5 章→short；`vol >= cur_vol-1`→mid；**更早卷章级明细直接丢弃**（`:1542`）。

> ⚠️ **信息不可逆丢失 + 截断方向不一致**：`long_text = "\n".join(vol_summary_lines)[:LONG_MAX]`（`:1548`）对卷摘要取**头部** 1000 字符。摘要按卷号升序，故超长书里**最近几卷摘要被截掉**，只留最早卷。而 short/mid 用的是 `[-MAX:]`（取尾部）。方向相反。

### 1.4 L3 结构化状态表

覆盖 8 张表（`models.py`）：`character_states` / `relationships` / `story_events` / `canon_claims` / `foreshadowings` / `knowledge_ledger` / `plot_nodes` / `character_state_deltas`（影子，生产 0 行）。

`build_context` 拼成 `list[str] l3_parts`（`:1379-1506`），固定顺序：姓名规范 → 死亡名单 → 永久失去名单 → 可恢复名单 → 人物状态 → 其他已知角色 → 人物关系 → 完整性校验 → 伏笔账本 → 知识点 → 维度目录 → 世界事实 → 谎言。

**L3 是一个 3400+ 字符的意大利面字符串**，注入 `context["states"]` 单键。writer/editor 只能读自然语言；任何"按字段查询"的下游都必须重新查库。

损伤字段**没有独立表**，只是 `CharacterState.state`（`models.py:226-234`）这个 JSON blob 里的四个键：

```python
memory.py:1078-1082
# 状态损伤体系（v3）：四字段独立，各为 [{name,severity,status,since_ch,recov,permanent}]
"伤势": "impact", "后遗症": "impact", "代价": "impact", "诅咒": "impact",
```

`recov` 是唯一的"恢复条件"载体，`since_ch` 是唯一的"计时"载体。**没有任何 `heal_after_ch` / `duration` / `eta` 字段。**

### 1.5 L4 前情检索

`rag.json` 整文件 JSON（`docstore.py:296-316`），`CHUNK_SIZE=600` 定长无重叠切（`:357`），后台线程 embed（`:424-453`），注入上限 `RAG_MAX_CHARS=1800`。详见 §6。

### 1.6 实测 token 成本（来自 `generation_spans.metadata_json`）

| 块 | 样本1 | 样本2 | 样本3 |
|---|---:|---:|---:|
| `character_state` (L3) | 3441 | 3489 | 3391 |
| `bible` (L0+L1) | 3108 | 3108 | 3108 |
| `rag` (L4) | 1810 | 1723 | 1205 |
| `timeline` (L2) | 1136 | 1040 | 945 |
| `previous_chapter` | 1000 | 1000 | 1000 |
| `plot_detail`（细纲） | 713 | 583 | 587 |
| `style_reference` | 641 | 838 | 510 |
| `misc`（含 graph_context） | 313 | 313 | 313 |
| `chapter_outline` | 74 | 77 | 65 |
| **合计** | **12236** | **12171** | **11124** |

中文约 1 字符 ≈ 0.7–1.0 token → **记忆注入约 1.1–1.2 万字符 ≈ 8–12k tokens/章**。构成：**L3 28% · L0/L1 25% · L4 15% · L2 9%**，其余 23%。

> **关键反直觉发现**：`misc` 仅 313 字符——**这正是 `graph_context` 的容器，而它默认关闭（`audit_config.py:58`）**。图谱在这套系统里连 1% prompt 预算都没占上。

---

## 账本与事实抽取

### 2.1 抽取入口

`ChapterPostProcessor`（`agents/post_processor.py`，`role="memory"`，`tier="fast"`），**每章一次 LLM 调用**同时产出知识卡+梗概+记忆增量（`:1-6`）。两条 system prompt 并存：`system`（`:18-125`，learn 含知识卡）与 `STORY_SYSTEM`（`:131-212`，纯小说），由 `_flow.post_processor_system()` 选择（`:264`）。

### 2.2 提取 schema

`PostProcessOut`（`schemas.py:420-433`）：`cards / summary / state_updates / deaths / relationship_updates / events / plot_progress / claims / schema_delta / hang / entities` + 6 个流派专属字段。

> ⚠️ **结构性入口缺陷**：`state_updates` / `events` 是 **untyped `list`**。**只有 `relationship_updates` 有逐元素校验器**（`:452-460` → `normalize_relationship`，`:296-317`）。`state_updates` 形状漂移不会报错，只会静默丢失——"实体追踪不靠谱"的入口。

另有一个**存在但无消费者**的字段：`damage_delta` 只出现在 `chapter_revisions.py:38,42` 的语义键白名单里，`memory.apply_extraction` **从不读取它**（损伤实际走 `state_updates.伤势`）。

### 2.3 损伤与生命周期 prompt 契约

`post_processor.py:140-143`（STORY_SYSTEM）：

```
- 「伤势」「后遗症」「代价」「诅咒」：结构化损伤，值为对象数组 [{name,severity,status,recov,permanent}]；
  · name 损伤名；severity 轻|重|极重；status 新发|恶化|维持|好转|缓解|痊愈|解除|永久·可缓解|绑定——只报有变化条目，未变化绝不重复输出（同值不覆盖、不推进计时）；
  · 好转/痊愈/解除/缓解必须能在 events 找到该角色治疗/恢复/解咒/补偿事件，否则不得报恢复类状态；
```

> ⚠️ **prompt 与代码枚举不一致**：prompt 写 `轻|重|极重` 三档，而阈值表 `_IMPACT_BASE_TH`（`memory.py:853-859`）登记五档 `轻微/轻/中/重/极重`。`memory.py:846-851` 记录了由此产生的实测事故：
> ```
> # 原表只登记「轻/重」两档，而模型实际会写「中」「极重」「轻微」→ `_impact_threshold` 拿不到 base → 返回 None
> # → 这些损伤**永远不会**被催「本轮必须推进」。实测书 24：最重的「神魂受创（伤势·极重）」
> # 恰恰是唯一不催的那条，而轻伤催得最凶 —— 注意力被反向引导。
> ```

### 2.4 抽取器看到的快照

`snapshot_for_extraction`（`:1711-1784`）喂 7 块：`characters`（**无截断**）、`relationships`（**无截断**）、`recent_events`（limit 10）、`pending_nodes`、`schema`（24 项）、`claims`（前 30）、`world_rules`（1500 字符）。

> ⚠️ **认知口径分裂**：记录员看**全书**（book 23 有 739 条关系 → 每次抽取 prompt 塞 739 行）；作者只看出场者（`:1197-1202` 按 participants 过滤）。**两者认知不一致是实体漂移的放大器。**

### 2.5 冲突处理与一致性守卫（`apply_extraction`，`:2509-2973`）

**唯一写入口**，六类守卫：

| 守卫 | 定位 | 行为 |
|---|---|---|
| 不变量 | `:2638-2645` | `外貌/出身/天赋` 变更 → 拒收（唯一例外：永久伤残追加备注） |
| 境界单调 | `:1994-2000` | 无剧情跌落 → 拒收；**无阶梯时直接放行**（`if not ladder: return True`） |
| 死亡角色 | `:2577-2579` | `alive=False` → **整条拒收** |
| 装备幻觉 | `:2668-2694` | 新增物品须在本章正文出现（归一化匹配）否则拒收 |
| 移除证据 | `:2214-2232` | 从持有列表消失但正文仍提及 → 判定漏报，**保留** |
| 谎言/证伪 | `:1894-1937` | 见下 |

> ⚠️ `_CANON_FALSIFY_EVENT_KWS = ("证伪", "揭穿", "推翻")`（`:2041`）——**只有 3 个词**，正文几乎不会字面出现，"世界事实被证伪"路径实践上近乎不可达。

### 2.6 关系写入：唯一有完整生命周期语义的账本

匹配键是五元组 `(type_a, entity_a, type_b, entity_b, relation_type)`（`:2763-2781`）。关系类型变化时置旧边解除（`:2802-2817`），另有暗线转明（`:2788-2801`）。

> ⚠️ **`status` 词表三源不一致**：`models.py:274` 注释写 `现行/已结束/已转变`；prompt 要求 `新增|变化|解除`（`post_processor.py:148`）；代码直接 `row.status = status`（`:2785`）。**没有任何归一化** → 无法用 status 做活跃性过滤。

### 2.7 `is_active` 与 revision attribution：**生产全关，表 0 行**

**`is_active` 不是业务标志，而是 revision（修订世代）墓碑**。五表有此列（`models.py:253,307,327,345,380`），配合 `ledger_revision_id` / `effect_key` / `source_revision_id` 构成事件溯源机制。

写入侧（`:2543-2549`）在 `impact == "SEMANTIC"` 时把旧世代事件置 `is_active=False`；读取侧到处过滤（`:1362,1484,1728,1750,1893`；`graph.py:106`）。**但驱动这一切的开关关闭**：

```python
# chapter_revisions.py:283 —— revision 机制唯一入口
if not revision_config.shadow_enabled() and not revision_config.enabled(): return ...
# revision_config.py:15-20
DEFAULTS = { "chapter_revision": { "enabled": False, "shadow": False } }
# character_delta.py:205
if context is None or not character_delta_config.shadow_enabled() and not character_delta_config.enabled(): return ...
```

`revision_config.py:1-6` 自陈：`Production remains disabled by default.`

**后果链**（`pipeline.py:8016-8032`）：开关关 → `_ledger_context = None` → `apply_extraction` 走裸分支（`:2537-2542`）→ **直接 DELETE 本章旧 StoryEvent 再重放**：

```python
if ledger_context is None:
    session.query(StoryEvent).filter(
        StoryEvent.book_id == book.id, StoryEvent.volume == volume,
        StoryEvent.chapter_idx == ch_idx,
    ).delete(synchronize_session=False)
```

**实测印证**：`story_events WHERE is_active=0` = **0 行**（全书 active），故 `graph.py:106` 的过滤是纯死代码——`graph.py:103` 注释自己也承认"实测生产库 742/742 行为 active，故本过滤是**已验证的 no-op**"。`RelationshipAction` / `ForeshadowAction` / `CharacterStateDelta` / `ChapterRevision` / `ChapterCommitManifest` **五张表实测全 0 行**。

**结论：`is_active` + revision attribution 是完整的影子实现，生产 0% 生效。**

> ⚠️ **更重要的一条**：`chapter_revisions.py:543` 的函数 docstring 明文写着 `"""CAS activate one prepared revision; ledger/RAG/Graph are untouched."""`——**回滚一章正文不会回滚人物状态、死亡、关系**。`memory.py:2536` 注释亦自认："人物状态/关系/claims 为「最新态」累计…不在此删除"。生产语义是：**修订一章 = 删掉该章 StoryEvent 再重放，CharacterState 是"最新态覆盖"永不回退**。

同类影子：`long_memory_prompt.py` 默认 `{"enabled": False, "mode": "off"}`（`:16`），`prompt_metadata()`（`:61`）**生产零调用**，且 `variant_block` 只返回一段**防章节号泄漏到正文**的提示（`:51-58`）——**与"长期记忆"毫无关系，纯命名误导**。`recovery_orchestrator.recover_service_state()`（`:7`）**生产零调用**（docstring `:12` 自陈 "opt-in and does not run during app import/startup"）。

---

## 实体与势力漂移根因

### 4.1 【致命】`势力` 不是合法实体类型

```python
entities.py:17   ENTITY_TYPES = ("人物", "地名", "功法", "物品", "组织", "术语", "规则", "其他")   # 无 势力
graph.py:36      _NODE_TYPES  = ("人物", "地名", "功法", "物品", "组织", "规则", "其他")           # 无 势力
```

抽取 prompt 却反复要求输出 `type_b="势力"`：`post_processor.py:53`、`:56`、`:119` 的 JSON 示例 `{"entity_b":"青云宗","type_b":"势力","relation_type":"归属"}`。`models.py:267` 的注释也明写 `# 人物/地名/物品/功法/势力/组织/规则/其他`。

`entities.py:146` 会**静默降级**：
```python
_type = str(e.get("type", "其他")) if e.get("type") in ENTITY_TYPES else "其他"
```

**后果链**：模型报 `青云宗` type=`势力` → `_norm` 降级 `其他` → `format_for_writer` 按 `ENTITY_TYPES` 分组（`:633`）落入"其他"桶 → `graph.py:76-80` 读 entities.json 得 `其他`。**势力仍有节点**（`Relationship` 建节点 `graph.py:85-89`），但**全部标成"其他"，与真正杂项混在一起**。实测 book23 的 entities.json 有 228 个实体，含「组织」**无「势力」**。

**势力根本没有实体表**。`models.py` 全部 24 个类中无 Faction 模型。势力以三种字符串形态存在：
1. `story_brief_json` JSON blob（`models.py:74`），由 `story_director.py:48` 写入，仅作一次性 prompt 锚点（`story_arc_planner.py:95-96`、`architect.py:2794-2795`）
2. `Relationship.type_a/type_b` 字符串（`models.py:265-268`）——唯一"像表"的地方
3. `entities.json`——**但类型白名单不含它**

> **"势力归属很乱，做了图谱还是不靠谱"的机制解释**：势力从来没有一等公民身份（无表、无实体类型、无生命周期），只有 `Relationship` 两端字符串 + 一次性 JSON blob。图谱里「势力」这一维度实际是**空集**，剩下的只是人物↔某个字符串的点边。`schemas.py:273-293` 的 `REL_GROUP_LABELS` 是唯一完整支持 `势力`/`组织` 的地方——跨 4 文件、3 套枚举互不同步。

### 4.2 【致命】跨卷章号比较 bug：`last_chapter` 是卷内号

```python
entities.py:596    last = int(e.get("last_chapter", 0) or 0)
entities.py:597    if _cur and last and 0 <= (_cur - last) <= 10: filtered.append(e)
```

调用方（`memory.py:1672-1673`）传 `current_chapter=cur_idx`，而 `models.py:101` 明确 `idx = ...  # 卷内序号，从 1 开始`。写入侧（`entities.py:238-239`）也写卷内号：

```python
existing[e["name"]]["last_chapter"] = max(..., chapter_idx or 0)   # 卷内号
existing[e["name"]]["last_volume"]  = max(..., volume or 1)        # 卷号——被写入但从不参与比较
```

**`last_volume` 被正确写入却从未被读取。**

**后果**：第 2 卷第 5 章时 `_cur=5`；第 1 卷第 200 章出场过的角色 `last_chapter=200` → `5-200 = -195`，`0 <= -195` 为 False → **该角色被判定"非近期活跃"，从 writer 名词表静默剔除**。

**每次跨卷，上一卷所有 `last_chapter > 当前卷内序号` 的实体全部被踢出名词表**，连带影响 `departed` 离场检测（`:613-624`）与生命周期标签（`:652-656`）。

> **这是"实体追踪不靠谱"最直接的机制性根因：不是模型记不住，是注入层按错误坐标系过滤掉了。**

### 4.3 `_merge_collection` 为什么失败

`:2136-2233` 本身设计细致，两条路径。**失败模式 1：模型混用语义 → 走错分支且静默丢数据**

```python
memory.py:2140
if key == "装备" and isinstance(new, list) and any(str(x).startswith(("+", "-")) for x in new if str(x)):
```

判定是 `any(...)`——**只要一项带 `+`/`-`，整批按增量处理**。若模型输出 `["+新剑", "[毁·可修]旧剑"]`（第二项漏了 `-`）：走路径 A → `"+新剑"` 正常 → `"[毁·可修]旧剑"` 以 `[` 开头，`sign="["`，既非 `+` 也非 `-` → **静默丢弃**（`:2162-2176` 的 if/elif 无 else）。**无 warning、无日志**，整套生命周期机制在此被绕过。

**失败模式 2：两条路径比对口径不一致**

```python
memory.py:2214   gone = [x for x in old_l if x not in new_l]
```

路径 B 用**原始字符串**比较（`:2137`/`:2200` 只做 `split("|")[0].strip()`），而路径 A 用 `_normalize_item_name()` 归一化（`:2145`）。模型把 `"凝露草×3"` 重报成 `"凝露草"`（去份额）→ 路径 B 判定已 gone → **误触发移除**。

**失败模式 3：MONO_LIST 的 dict 推导会静默解除状态**

```python
memory.py:2203-2209
old_bare = {_strip_life_prefix(x): x for x in old_l}
new_bare = {_strip_life_prefix(x): x for x in new_l}
merged_map = dict(old_bare)
for k, v in new_bare.items():
    if k: merged_map[k] = v
```

若同名字段在模型输出里出现两次（`["[封]焚天诀", "焚天诀"]`），dict 推导**后者覆盖前者** → 最终 `"焚天诀"`（无前缀）胜出 → **封印状态被静默解除**。这与 `:2203-2204` 注释想防的事恰好相反。

**失败模式 4：同一批同时给出 `-X` 与 `+[态]X` 时状态随机丢失。** prompt `post_processor.py:38` 恰恰教模型这么写（`修复 → -[毁·可修]剑 +剑`）。`_locate` 用归一化名（剥离前缀）定位，两条都能命中同一 `idx`：若 `+[态]X` 先跑且旧值为裸名，落到 `# 纯名 + 且已有：视为确认持有（无需变化）`（`:2175`）被**静默忽略**，`[毁·可修]` 状态丢失。现有测试只覆盖了不带 `+` 的写法（`test_guards.py:284`），**未钉住该路径**。

**失败模式 5：`_locate` 只找第一个匹配**（`:2143-2147`）。若同角色同时持有 `"剑"` 与 `"[毁·可修]剑"`（历史脏数据），`_normalize_item_name` 对二者都返回 `"剑"` → **永远只操作第一个**，第二个成为僵尸条目。

### 4.4 别名：一半覆盖，一半空白

**entities.json 侧有别名**（`entities.py:155,188-190,248-251`）：参与 BM25 最长匹配（`memory.py:520`）与 `key_entities` 提取（`:536-540`）。实测 book23 有 57/228 个实体带 aliases。

**账本侧完全没有别名概念**：`CharacterState.name` 精确匹配（`:2562-2567`）、`Relationship.entity_a/entity_b` 精确字符串（`:2772-2779`）、`_relation_nodes` 收集原始字符串（`:1093-1097`）。

于是 `萧令衍` 与 `令衍` 在 entities.json 是**同一实体**，在 `character_states` 是**两行**，在 `relationships` 是**两组边**，在**图谱里是两个节点**（`graph.py` 完全没有实体消解）。

唯一桥 `_find_similar_character`（`:2282-2311`）有三个问题：
1. **阈值 2/3 且依赖 `外貌`/`出身`/`身份`**——三者都是 `immutable`（`:1074`），`身份` 甚至不在 `DEFAULT_SCHEMA`
2. `_sig` 取前 18 字符（`:2292`），中文 18 字外貌描述几乎不可能跨章一致
3. 命中后**只合并状态不合并关系**（`:2569-2576` 只改 `row`）→ 关系表旧名边永久滞留

### 4.5 合并/分裂整体图景

| 场景 | 机制 | 结果 |
|---|---|---|
| 同名 | `name` 精确匹配 | ✅ 正确合并 |
| 改名（状态相似） | `_find_similar_character` 2/3 信号 | ⚠️ 概率命中，关系不迁移 |
| 改名（状态不相似） | 无 | ❌ 新建行 → **人格分裂** |
| 别名 | entities.json 有，账本/图谱无 | ❌ 双份实体 |
| 势力名变体 | 无任何归一化 | ❌ 每章可能新边 |
| 死亡复活 | `alive=False` 整条拒收（`:2577`） | ✅ 状态层防住；但 `deaths` 漏报则无救 |
| 生命周期前缀 | `_strip_life_prefix` 比对 | ⚠️ 见 §4.3 |
| 自环关系 | `graph.py:87 if not a or not b or a == b: continue` | ❌ 「一人成宗」类自指关系被丢弃 |

> "死亡复活"虽在状态层防住，但 prompt 自认这是"世界级 Bug"（`post_processor.py:147`）——**用"整条拒收"兜底"提取器可能漏报死亡"，等于把一致性押在 fast 档模型的召回率上**。

### 4.6 生命周期前缀：`[耗·一次性]` 完全失管，且无法自动清除

```python
memory.py:216-220
LIFE_PREFIX_RE = re.compile(r"^\[([^\[\]]+)\]")
_PERMANENT_STATES  = {"毁·不可逆", "失·不可寻", "灭"}
_RECOVERABLE_STATES = {"毁·可修", "失·可寻", "衰", "封", "转"}
```

> ⚠️ **`[耗·一次性]` 既不在永久集也不在可恢复集**——只在 prompt 里被描述（`post_processor.py:137`），代码里**无任何语义归属**。于是 `[耗·一次性]回气丹` 既不入"永久失去名单"（`:1410-1411`）也不入"可恢复名单"（`:1412-1413`）→ **消耗品用尽后既不禁止使用也不提示可恢复，完全失管**。

`_normalize_item_name`（`:245-258`）的剥离顺序有实战教训：
```python
# ± 必须先于生命周期前缀剥离：全量重报条目形如 "+[失·不可寻]家庭合影"，
# 若先剥前缀，"[失·不可寻]" 会逃过归一化残留下来（2026-09-06 书20 实证误杀）。
```

**是否会自动清除？——从不。** 全仓 grep `_is_permanent_loss|_is_recoverable|_life_state|_strip_life_prefix` 仅 28 命中，全部是**读取/剥离用于比对或展示**（`memory.py:216-256,1410-1413,2157-2160,2184,2203-2204,2682`；`entities.py:119-151`；`main.py:3743-3755,3911`）。**没有任何一处把无前缀名写回去"清态"**——唯一出口是模型自己发 `-[毁·可修]剑 +剑`。而这条路恰是 §4.3 失败模式 4 的静默丢失点。**"自动流转"这条通路根本不存在，需要新增，而不是修 bug。**

---

## 伤势不恢复根因

### 5.1 恢复的唯一触发路径：一道自锁守卫

`_merge_impacts`（`:2044-2133`）中恢复类状态的处理（`:2114-2121`）：

```python
# 恢复类状态（好转/痊愈/解除/缓解）：须 events 同时含该角色名 + 治疗/恢复语义
if any(r in st for r in MemoryManager._IMPACT_RECOVERY) and not (
    events_blob
    and name in events_blob
    and any(k in events_blob for k in MemoryManager._IMPACT_RECOVERY_EVENT_KWS)
):
    warnings.append(f"⛔ 一致性守卫：{name}.{key}[{nm}] 恢复（{prev_st}→{st}）缺治疗/恢复事件支撑，保留原状态")
    continue
```

`_IMPACT_RECOVERY_EVENT_KWS`（`:2035-2039`）是 25 词表（`治疗/疗伤/医治/救治/痊愈/康复/复原/愈合/好转/缓解/解除/恢复/服药/用药/敷药/丹药/疗愈/解毒/解咒/祛除/驱散/拔除/休养/调养`）。

**关键：`events_blob` 是 `json.dumps(extraction.get("events", []))`（`:2553`）——即同一个 post_processor 在同一次调用里输出的 `events` 数组。**

**双重合取的两道门都由同一次 LLM 输出提供**，而 prompt 又要求 `events` 只写"2~5 条关键事件"（`post_processor.py:178`）。若模型判定"本章是过渡章、无关键事件"，`events` 可能为空 → **即便它同时认为伤好了，恢复申报也被守卫拒绝**。

守卫初衷是对的（`:2032-2034`）：
```
# 只用「角色名出现在任意事件里」会把「押进城/走进大殿」这类与治疗无关的事件当成痊愈依据，
# 使伤势/代价被无依据清除（账本单向丢失，事后不可恢复）——2026-09-13 修。
```
但副作用是**把"记录一致性"变成了"叙事义务"**——要求 `events` 承担它 schema 上没承诺的职责。

### 5.2 代码侧**根本不存在**"按章数自动痊愈"

**这是"代码不存在"，不是"代码存在但没被调用"。** 证明链：

```python
memory.py:2132-2133 — 唯一移除处，且是被动响应 LLM 申报
# 痊愈/解除从活跃列表移除（生命周期历史由 events/时间线承载）
return [x for x in cur if not any(t in str(x.get("status") or "") for t in ("痊愈", "解除"))]
```

1. `_IMPACT_BASE_TH`（`:853-859`，唯一时间阈值表）**只有一个消费者** `_impact_threshold`（`:938`）；grep `_impact_threshold` → 仅 `:993` 与 `test_damage_sm.py`
2. `_fmt_impacts`（`:965`）**只产出字符串**，唯一副作用是拼一行 prompt 文本：
   ```python
   memory.py:1001-1006
   if not only_changes and th and th < 999 and _cur > _since:
       el = max(0, _cur - _since)
       if el >= th:
           warn = " ⚠️已超恢复期，本轮必须推进（恢复剧情或明示永久化）"
       elif el >= th * 0.8:
           warn = " ⚠️接近恢复期，建议本轮推进"
   ```
   调用点只有 `:1029`（`only_changes=True`）与 `:1049`。**阈值到期只产生一句提示词，不改任何数据。**
3. 另有两条**单向出口**把伤彻底钉死：`:2108-2110`（`_IMPACT_TERMINAL = ("痊愈","解除","永久","绑定")` 命中即 `continue`，永不变化）
4. grep `decay|expire|自然恢复|自动痊愈|自愈` → 仅命中 `memory.py:605-711` 的 **RAG 时间衰减**，与角色状态无关

**治愈 100% 依赖提取器（LLM）主动申报 `status="痊愈"/"解除"` + 正文 events 里恰好有治疗语义词。** 仓库自己的复盘文档已确认：

> `E:\AI_project\story_llm\.workbuddy\memory\2026-09-13.md:205-207`
> 「**另一半真相**：「每章都被催」不只是口径错……修完口径后催促变成**诚实**的，但伤势一直在的原因是**剧情里根本没有治疗节拍**。」

### 5.3 真正的缺口：细纲 schema 里没有损伤槽位

细纲不是表字段，而是 `book.outline`（`models.py:53`）里 `chapters[*]` 的 JSON 元素。字段由 architect 的 prompt 定义（`architect.py:1795-1812`）：

```python
请为每章补写三个字段：
plot（≤100 字）、end_hook（章末钩子 ≤40 字 …）、
plot_detail（**{pd_min}~{pd_chars} 字，下限是硬要求**：分场景拍点表，按正文顺序分 4~8 行…）
entry_state（本章开场状态：【地点·时间·在场人物·谁知道什么】四项齐全…）、
exit_state（本章结尾的同样四项，下一章将据此开场）。
```

**四项是「地点·时间·在场人物·谁知道什么」——没有伤势。** 三处独立确认：
1. `architect.py:1806-1808` prompt 定义
2. `outline_lint.py:121-125` 校验：`"第{plan.get('idx')}章缺 entry_state（开场地点·时间·在场人物·谁知道什么）"`
3. `detail_review.py:809` 审校准则 3：`状态一致：plot_detail 不得与本章 entry_state/exit_state 冲突（地点、在场人物、谁知道什么）`

解析侧 `_collect_plots`（`architect.py:75-113`）只认 `plot / end_hook / plot_detail / entry_state / exit_state` 五键——**多余键被静默丢弃**。且 `entry_state`/`exit_state` 是**自由文本字符串**（`architect.py:108-113`），无结构化字段，**不参与任何机械校验或状态计算**。

`beat` 是章节表上的独立枚举列（`models.py:105`），与细纲无关：
```python
agents/schemas.py:25   VALID_BEATS = {"normal", "boss", "climax", "emotion", "recall", "turning"}   # 无 heal
```

**能力矩阵（最终根因）**：

| 角色 | 能看到损伤 | 能规划节拍 |
|---|---|---|
| `architect`（细纲） | ❌ 无损伤字段、无账本快照 | ✅ 但不知道要规划什么 |
| `writer`（正文） | ✅ `context["states"]` | ❌ 受细纲硬约束 |
| `post_processor`（记录） | ✅ `snapshot.characters` | ❌ 只记录 |
| `editor`（审校） | ✅ `editor.py:63-72` | ❌ 只能打回重写 |

**没有任何 agent 同时具备"看到损伤"和"规划本章节拍"两个能力。**

唯一的"催促"只到达 writer（`_fmt_impacts` → `_fmt_char_line` → `build_context:1200` → `context["states"]`），而 writer 收到的是细纲硬约束（`flowmode/base.py:157-162`）：

```python
f"【本章细纲——场景骨架硬约束】\n{pd}\n"
"细纲执行规则（最高优先级，逐行核对）：\n"
"- 正文必须按细纲场景顺序展开，每个场景的「结果」与关键转折必须落实，不得遗漏、不得调换因果；\n"
"- 禁止新增细纲之外的重要人物、重大转折、新功法/新设定/新地图——它们属于剧情级加戏；\n"
```

**冲突确立**：损伤区说"本轮必须推进（恢复剧情）"；细纲区说"最高优先级…禁止新增细纲之外的重要转折"。实测证据（`character_state=3441` 字符确实进了 prompt 但恢复从未发生）表明 **writer 选择了服从细纲——这是正确的服从**，因为细纲标注"最高优先级"。

**死锁**：architect 没规划治疗 → writer 按细纲写 → `editor.py:66-69` 判「无治疗过程的草率痊愈 → consistency 必改」/「超期未推进 → pacing 必改」→ 修改稿仍须服从细纲（`editor.py:104-109` 细纲履约核查）→ **伤既不会好，也不许凭空好，每章扣分。**

### 5.4 次要放大器

1. **`only_changes=True` 过滤慢性损伤**（`:988-989`）：`if only_changes and status in ("维持","绑定") and "永久" not in status: continue` → 配角伤势进入"维持"后**连催促都不出现**。
2. **`equip` 参数传递不一致**：阈值计算读 `ctx["equip"]`（`:954-956`），但 `_ctx_dmg` 初始化无此键（`:1178-1186`），只有完整版 `_fmt_char_line` 在最后补上（`:1049`）；`limited=True` 路径（`:1029`）传 `dict(ctx or {})` **无 equip** → 配角永远按"无治疗资源"算，阈值 ×1.3 被推后。
3. **`("诅咒",""): 999`** 配合 `th < 999`（`:1001`）→ 诅咒永不催促；注释说"由解咒伏笔驱动"，但**没有任何代码把解咒伏笔与诅咒关联**。

### 5.5 结论

**"伤势不恢复"不是 bug，是架构缺失。** 恢复链条每一步都存在，但在"规划"环节断掉：记录 ✅ → 状态机 ✅ → 催促注入 ✅ → **规划 ❌** → 正文 ❌ → 记录（无变化）→ 循环。

修复不需要改 `_merge_impacts`，需要**在细纲 schema 里加损伤/治疗槽位，并把"恢复"变成可规划的一等节拍**。

---

## RAG 细节与成本

### 6.1 切片与索引

```python
memory.py:357   CHUNK_SIZE = 600
memory.py:434   chunks = [text[i : i + self.CHUNK_SIZE] for i in range(0, len(text), self.CHUNK_SIZE)]
```

**定长 600 字符硬切，无重叠、无句边界对齐。** 实测印证：book 24 有 110 entries / 63,192 字符 → 平均 574 字符/片。写入是后台线程（`:424-453`），异常静默吞掉（`:450-451`）；落盘是**整文件重写**（`docstore.py:289-291`）：

```python
def rag_save(book_id: int, entries: list[dict]):
    p = book_dir(book_id) / "rag.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
```

> 每章追加一个切片 = **重写整个 27.75 MB JSON**（book 8 实测）。

### 6.2 检索五路 + 融合

`query_with_trace`（`:462-761`）：

1. **时间过滤**（`:477-481`）：`(v,i) < (before_vol, before_idx)` 防未来泄漏
2. **窗口安全阀**（`:486-487`）：`RAG_WINDOW_CAP` 默认 **0 = 不限**（`config.py:67`）→ **全历史切片进入打分**
3. **向量路**（`:501-507`）：`llm.embed` 内部**优先本地 bge-m3**（1024 维，`local_embed.py:68` `normalize_embeddings=True`），在线兜底
4. **BM25 词法路**（`:517-544`）：`RAG_HYBRID` 默认 1（`config.py:56`）；分词是**纯字符二字组**（`hybrid.py:19-29`），有 entities 时先做专名最长匹配（`:31-57`）；k1=1.5 / b=0.75（`config.py:60-61`）。**双查询融合**（`:531-556`）：主查询 + 从专名表提取查询中出现的实体拼第二查询，RRF 融合
5. **伏笔锚定**（`:611-619`）：`_fs_anchors`（`:163-199`）把 query 向量与未回收伏笔描述向量比余弦，`>= RAG_FS_MATCH_MIN`（0.45，`config.py:91`）→ 取 top2 锚定埋设章，加成 `RAG_FS_MATCH_BONUS=1.5`（`:92`）

### 6.3 打分公式（`RAG_KEY_POOL=1`）

```python
memory.py:622-625
ordered = sorted(positions, key=lambda i: -(_bonus(i) + _fs_bonus(i) + score_map.get(i, 0.0) - _decay(i)))
```

| 分量 | 公式 | 定位 |
|---|---|---|
| `_bonus` | `RAG_KEY_BONUS(0.35) × tier_w` | `:601-603` |
| `_fs_bonus` | `RAG_FS_MATCH_BONUS(1.5)` 若命中锚定章 | `:618-619` |
| `_decay` | `RAG_TIME_DECAY(0.15) × min(1.0, gap / RAG_DECAY_HALF_LIFE(500))` | `:605-609` |
| `score_map` | RRF(k=60) / 向量分 / BM25 分 | `:569-591` |

**关键章权重**（`_build_key_chapters`，`:76-109`）：未回收伏笔埋设章 `_FS_W_MAP[weight] × _FS_K_MAP[kind]`（0.3~2.0）；伏笔回收章 0.4；有 `consequence` 的事件章 0.35；`beat in (recall,climax)` 0.3；卷首/卷末 0.25。映射表（`:52-53`）：`_FS_W_MAP={"极高":1.0,"高":0.7,"中":0.5,"普通":0.3}`、`_FS_K_MAP={"贯穿型":2.0,"贯穿":2.0,"长期":1.5,"中期":1.2,"短期":1.0}`。

> ⚠️ **权重映射与 prompt 枚举不匹配**：`_FS_W_MAP` 有键 `"极高"`，但 `ForeshadowOut`（`schemas.py:332`：`weight: str = "普通"`）与 post_processor 从未定义 `"极高"`；而 `_fs_priority`（`:1294`）只给 `"重要"` 加分 → **`"极高"` 在注入排序里得不到任何加权**。又一次词表不同步。

### 6.4 精排（rerank）

`RAG_RERANK_MODE` 默认 `"linear"`（`config.py:83`；`.env` 亦确认）。线性融合（`:638-647`）：

```python
mv = max([vec_scores.get(i, 0.0) for i in pool] or [0.0]) or 1.0
ml = max([lex_scores.get(i, 0.0) for i in pool] or [0.0]) or 1.0
mb = max([_bonus(i) for i in pool] or [0.0]) or 1.0
for i in pool:
    final_scores[i] = (W_VEC * (vec_scores.get(i,0.0)/mv)
                     + W_LEX * (lex_scores.get(i,0.0)/ml)
                     + W_KEY * (_bonus(i)/mb))
```

权重 0.55/0.30/0.15（`config.py:85-87`），候选池 `RAG_RERANK_N=16`（`:84`）。`bge-reranker-v2-m3` 只在 `mode=online` 时调用（`:634-637`），失败回退 linear（`:636`）。

> ⚠️ **归一化分母取自候选池内最大值**。若整池向量分都低（冷启动/embedding 降级），`mv` 会把噪声放大到 1.0——**"矮子里拔将军"**：一个 0.08 的余弦分可能被归一化到接近 1.0 并主导排序。

### 6.5 配额、去重与注入

三重约束（`:653-688`）：同章去重（每章只保 1 块，`:669-671`）；同卷配额 `RAG_SAME_VOL_CAP=2`（`config.py:72`）**仅在存在跨卷候选时生效**（`:662-663`，注释记录"书8 实测 4 段→2 段、关键章命中减半"）；全局关键章配额 `RAG_KEY_GLOBAL_CAP=2`（`:75`），伏笔锚定章与 prefer 卷豁免（`:675-679`）。

注入合并（`:719-752`）受 `RAG_MAX_CHARS=1800` 约束，**按排序顺序做字符级硬截断**：
```python
if len(t) > cap - used: t = t[: cap - used]
```
> ⚠️ **会在句子中间切断**，无"截到句号"兜底。

### 6.6 冷启动 / 懒建 / 预热

三级：① 写入侧预热，`RAG_PREWARM_MIN_CHUNKS=200` 片后触发后台预建（`:366`,`:370-422`）；② 查询侧懒建，`get_lazy` 首建异步返回 `(None, True)` → **静默降级**（`:527` 只记进 trace：`trace["lex_stale"] = lex_stale`）；③ 降级链 `hybrid_vec_bm25 → bm25_only → vector → bigram_fallback`（`:571-591`），`bigram_fallback` 是纯二字组重合度，质量极低但仍返回结果。

`memory.py:373-376` 记录了一个已修复的严重缺陷：`旧实现把标记写在成功之前 → 任一环节失败就让该书在本进程生命周期内永不预热（冷启动优化静默归零，且零痕迹）。`

### 6.7 已知坏点汇总

| # | 问题 | 定位 | 影响 |
|---|---|---|---|
| 1 | 整文件 JSON 重写 | `docstore.py:289-291` | 每章重写 27.75 MB |
| 2 | 无重叠定长切片 | `memory.py:434` | 跨切片语义断裂 |
| 3 | 字符级硬截断 | `:736-737` | 句子中间切断 |
| 4 | BM25 首建静默降级 | `hybrid.py:164-167` | 不落 problems 日志 |
| 5 | 矩阵失败返回全 0 | `vec_index.py:55-59` | 向量召回静默归零 |
| 6 | 归一化分母取池内 max | `memory.py:639-641` | 噪声放大 |
| 7 | 矩阵签名只比 (mtime,size,条目数) | `vec_index.py:145` | 重生成同章、条目数不变 → 脏矩阵 |
| 8 | `_FS_W_MAP["极高"]` 无人产出 | `:52` vs `schemas.py:332` | 死配置 |
| 9 | `key` 字段写了不用 | `:446` vs `:595` | 冗余体积 |
| 10 | `RAG_WINDOW_CAP=0` 全量打分 | `config.py:67` | 超长书 CPU 线性增长 |
| 11 | 本地模型 `_failed` 永久闩锁 | `local_embed.py:11,82-83` | 一次失败 → 进程级永久降级 |
| 12 | numpy 缺失静默降级 | `vec_index.py:19-24` | 性能 <5ms → 百 ms 级，无痕迹 |
| 13 | `invalidate()` 零调用 | `hybrid.py:169`、`vec_index.py:151` | 完全依赖 sig 比对 |
| 14 | `_iter_similarity_scores` O(n²) | `novelty.py:191-234` | docstring 自陈"暂不优化"（1500 章 28.85s）；**不在生成热路径** |
| 15 | `_cosine_matrix`/`_jaccard_matrix` | `novelty.py:146-177` | **死代码**，零调用 |
| 16 | `hybrid.tokenize` 专名匹配 O(文本×实体) | `hybrid.py:38-46` | 每文档每次调用 |
| 17 | `NOVELTY_GUARD` 死旋钮 | `config.py:63` | 全仓无读取点（真实开关在 `guard_config` → `pipeline.py:7310`） |

### 6.8 每章真实 token 成本

切片数 ~5（600 字定长）× `RAG_TOP_K=4`（`config.py:54`），同章去重后 ≤4 章；注入 **1205–1810 字符**（受 `RAG_MAX_CHARS=1800` 约束）→ **~1.2–1.8k tokens/章**；占 writer prompt 总量（1.1–1.2 万字符）的 **~15%**。检索耗时典型 50–300 ms（含 embedding 网络往返）。

---

## 图谱能力与缺口

### 7.1 `graph.py` 构建了什么

`build_graph`（`:64-131`）用 **NetworkX `MultiGraph`**（`:69`），三类源（**零 LLM**）：

**① 关系边**（`:82-93`）：
```python
for r in session.query(Relationship).filter(Relationship.book_id == book_id).all():
    a, b = r.entity_a_eff, r.entity_b_eff
    if not a or not b or a == b: continue
    _add_node(a); _add_node(b)
    G.add_edge(a, b, relation=r.relation_type or "关系", since_ch=r.since_ch or 0,
               visibility=r.visibility or "明", status=r.status or "现行")
```

**② 事件共现边**（`:95-119`）：解析 `StoryEvent.participants` **两两全连接**：
```python
for i in range(len(parts)):
    for j in range(i + 1, len(parts)):
        G.add_edge(parts[i], parts[j], relation="同场", event=ev.chapter_idx or 0)
```
> 10 个参与者 → 45 条边。**O(n²) 且事件表不删只增**（`memory.py:2538-2549`）。

**③ 孤立节点**（`:121-123`）：entities.json 实体即使无边也建节点。

**实测规模**：book8=583 边/152 节点、book23=882 边、book18=194 边、book24=113 边。`full_graph` 只对**输出**截断（`:144-146`，默认 400/1500，`main.py:4030`），**图对象本身永不裁剪**。

### 7.2 谁消费图谱

| 消费者 | 定位 | 生产状态 |
|---|---|---|
| `build_context` → `graph_context` | `memory.py:1247-1282` | ❌ **默认关**（`audit_config.py:58`，磁盘亦 false） |
| writer prompt | `writer.py:481-482` | ❌ 随上一条（**永不命中**） |
| 前端力导向图 API | `main.py:4016-4046` | ✅ 按需 |
| `graph_summary` | `graph_summary.py:121,319-320` | ⚠️ 摘要默认关，表 0 行 |
| `relation_conflict` | `graph.py:262-298` | ⚠️ 仅阅审，结果不落库 |
| 缓存失效钩子 | `memory.py:2902-2908`、`pipeline.py:7960-7964` | ✅ |

> **用户说"虽然做了图谱还是不靠谱"——图谱根本没参与写作。** 它只服务前端可视化和离线审校。`misc=313` 字符的实测值正是量化证据。**"阶段4 GraphRAG"在线上基本只体现为 `/graph/*` 只读端点，未进入生成语义。**

### 7.3 缺口清单

| 缺口 | 证据 |
|---|---|
| **无 `势力` 节点类型** | `graph.py:36` / `entities.py:17` |
| **无时间维度** | 边带 `since_ch`（`:90`）**但从不被读**（`:140/173/190` 投影只取 `relation` 与 `source/target`）；`event` 字段无排序/时间窗。**无"某时刻图快照"能力** |
| **无实体消解** | 节点名=原始字符串；book23 有 57/228 实体带 aliases，图谱里必然与主名**分裂成不同节点**；无大小写/空格归一；无生命周期前缀剥离；自环被丢弃（`:87`） |
| **社区检测名不副实** | `graph.py:212`：`comps = [list(c) for c in nx.connected_components(G)]`——连通分量 ≠ 社区；在"事件共现全连接"图上**几乎整本书连成一个巨型分量** |
| **图不持久化** | 进程内 `_cache: dict[int, nx.Graph]`（`:30`），**无 TTL、无上限、无淘汰**；重启即丢，每次重扫全表 |
| **`subgraph` 参数语义错** | `:193`：`"edges": edges[:max_nodes]`——用**节点上限**截断**边数** |
| **`full_graph` 截断破坏引用完整性** | `:150-157`：`nodes` 按 `keep` 过滤后再 `[:max_nodes]` → edges 里的 source/target 可能不在 nodes 中 |
| **`centrality` 的 "edge" 是 core 的复制** | `:253`：`"edge": sorted_deg[-top_n:]` 而 `sorted_deg` 升序 → 取尾 = **度数最高**，与 `core` 完全相同（docstring `:231` 却写"核心/边缘实体"）——错误语义 |
| **冲突检测只 1 组** | `:270-272`：只有 `{敌对,仇敌,势不两立} × {师徒,盟友,同盟,同门,挚友}`；且 `:277-283` 遍历全部 Relationship，**未按注释声称过滤 status** |
| **静默失败不可区分** | 5 处 `except Exception: pass`（`:59-60,92-93,118-119,296-297,319-320`）→ 表缺失/SQL 错误返回"空图"，与"这本书还没有关系数据"**不可区分**；`:128-131` 还把半成品空图**写进缓存** |

---

## 时间线与认知状态

### 8.1 有真实时间线模型吗

**没有。** 只有三个互不相通的"时间"表示：

| 表示 | 载体 | 精度 | 用途 |
|---|---|---|---|
| 章节序 | `Chapter.idx`（卷内）/ `global_ch()` | 章 | 调度、衰减、伏笔 |
| 故事内时间 | `StoryEvent.story_time`（**Text 列**，`models.py:318`） | 无结构 | 仅展示（`:1705`） |
| L2 文本 | `timeline.txt` 行 | 章 | prompt 注入 |

`story_time` 无解析、无排序、无比较；`_fmt_event`（`:1702-1709`）只做显示 `t = f"[{e.story_time}]" if e.story_time else f"[第{e.chapter_idx}章]"`。

**因此不存在"故事内时间轴"**：无法回答跨章乱序事件谁先发生、无法算"距上次治疗过了几天"、无法做时间一致性校验。**`story_time` 的价值等于零**——它是记录员写的一句散文。

### 8.2 事件排序：魔数 1000

```python
memory.py:1371-1377
def _event_relevance(e):
    overlap = len(set(_safe_participants(e.participants)) & set(participants)) if participants else 0
    ch_dist = abs((cur_vol * 1000 + cur_idx) - ((e.volume or 1) * 1000 + (e.chapter_idx or 0)))
    return overlap * 100 - ch_dist
event_lines = [self._fmt_event(e) for e in events_sorted[:10]]
```

> ⚠️ **`1000` 是"每卷 1000 章"的假设**；而 `global_ch` 用的是 **10000**（`models.py:673`）。同一个"全局章号"概念在同一仓库有 **1000 与 10000 两种编码**。先取 200 条事件再排序，最终只注入 **10 条**。

### 8.3 "每个角色在时刻 T 知道什么"（fog-of-war）

**这是最大的架构缺口。**

唯一接近"认知状态"的是 `Relationship.visibility`（`models.py:273`）：
```python
visibility = Column(Text, default="明")  # 明=世人所知的表面关系 / 暗=真实立场（仅作者视角）
```
注入时（`:1222-1225`）追加 `"——暗中关系：不知情角色的言行不得知晓此事，只能以伏笔/细节暗示"`。

> **`visibility` 是"世人知道 vs 只有作者知道"的二元叙事标记，不是"角色 X 知道"的认知标记。**

**没有的东西**：无 `CharacterKnowledge` 表；无"谁知道什么"的边或字段；`CanonClaim.audience`（`:1495-1496`）只是"听话人"字符串，**不参与任何推理**；`claims` 注入（`:1503-1506`）对全体角色无差别公开。

唯一替代品是细纲的 `entry_state`（`architect.py:1806`）：`【地点·时间·在场人物·谁知道什么】`。**"谁知道什么"被塞进一段自由文本字符串**：不结构化（无法查询"楚渊是否知道 X"）、不跨章维护、校验只是文本比较（`outline_lint.py:127-135` 的 `_state_key` 归一化比较——措辞不同即误报、措辞相同即漏报）。

**结论：本系统没有 fog-of-war 模型。** 信息不对称全靠 `visibility=暗` 一个布尔量 + 细纲里一句散文。任何需要"角色 A 不知道 B 已知 C"的情节（悬疑、误会、背叛铺垫）都无机制支撑。**这对重写是关键输入。**

---

## 写入放大

### 9.1 每章记忆写入清单

| # | 操作 | 定位 | 成本 |
|---|---|---|---|
| 1 | `snapshot_for_extraction`（读） | `pipeline.py:7898` | 全表 CharacterState + Relationship |
| 2 | `add_entities` → 写 entities.json | `pipeline.py:7959` | 整文件重写 |
| 3 | `graph.invalidate` | `pipeline.py:7962` | 清缓存（下次全量重扫） |
| 4 | `memory.apply_extraction` | `pipeline.py:8085` | 见 §9.2 |
| 5 | `memory.update_after_chapter` | `pipeline.py:8300` | 写 timeline.txt + **整文件重写 rag.json** |
| 6 | `rag_index.add`（后台线程） | `memory.py:3067`→`:424` | LLM embed + 整文件重写 |
| 7 | `resolve_foreshadowings` | `pipeline.py:8324` | 伏笔 UPDATE |
| 8 | `record_foreshadow_actions` | `pipeline.py:8316` | **生产 no-op**（`ledger_context=None`） |
| 9 | `advance_reviews`/`defer_reviews` | `pipeline.py:8310-8312` | KnowledgeLedger UPDATE |
| 10-11 | `drift.record_align`/`record_beat` | `pipeline.py:7932,7937` | 写 `drift_log` |
| 12 | `save_rag_trace` | `memory.py:1562` | 读+写 `rag_traces.json`（环形 300） |
| 13 | `session.commit()` | `pipeline.py:8339` | — |

### 9.2 `apply_extraction` 内部的写

1. `StoryEvent` **DELETE**（`:2538-2542`，`ledger_context is None` 路径）
2. `apply_schema_delta` → 可能改 `book.char_schema_json`（`:2550`）
3. 每个 `state_updates` 元素 → 1 次 `CharacterState` SELECT（`:2562-2567`）+ 可能 1 次 `_find_similar_character` **全表扫描**（`:2294`）
4. `_apply_relationship_actions`（`:2747`）——生产 no-op（`:2381-2382`）
5. **每个 `relationship_updates` 元素 → 最多 3 次 SELECT**：五元组精确查（`:2768-2781`）、暗线转明扫描（`:2790-2799`）、旧关系置解除扫描（`:2805-2815`）
6. 每个 `events` 元素 → 1 次 INSERT（`:2853-2869`）
7. 每个 `deaths` 元素 → SELECT + UPDATE（`:2875-2900`）
8. `plot_progress` → 每 key 1 次 SELECT（`:2911-2918`）
9. 关系完整性复查（`:2922-2963`）：`_relation_nodes` **全表扫 Relationship**（`:1093`）+ **全表扫 CharacterState**（`:2925`）+ `entities.load`
10. `apply_claims` → 每 claim 最多 3 次 SELECT（`:1889`,`:1894-1899`,`:1927-1932`）+ INSERT

### 9.3 复杂度

**O(n) 随书增长热点**：`_find_similar_character` 全表扫（`:2294`）；关系完整性复查双全表扫（`:1093`+`:2925`）；`snapshot_for_extraction` 无截断（`:1713-1723`）**且进 prompt**；`build_context` 全量 `states`/`rels`/`fs`（`:1154,1204,1284`）；`_key_chapters` 全量重扫（`:87-108`，有签名缓存）；`rag.json` 整文件重写（`docstore.py:291`）；`entities.json` 整文件重写（`entities.py:173`）；`graph_summary` 对 `Relationship` 全表扫 + Python 过滤（`graph_summary.py:333-337`）。

**O(n²) 已确认**：`novelty._iter_similarity_scores`（`novelty.py:191-234`，docstring 附实测表 100 章 0.20s → 1500 章 28.85s，**不在生成热路径**）；`novelty._align_rows`（O(句数²)，**在生成热路径上**，`pipeline.py:7313`）；`graph` 事件共现两两建边（`graph.py:114-117`）；`hybrid.tokenize` 专名匹配（`hybrid.py:38-46`）。

### 9.4 重复抽取

**每章 LLM 抽取调用**：`ChapterPostProcessor` **1 次/章**（主抽取）；`EditorAgent` 可能多次（初评+复评）；`SummaryAgent`（graph_summary）默认关；`novelty` 句级比对是本地非 LLM 但含 O(句数²)。

**在 delta 模式下变成 2 次**（`pipeline.py:8049-8079`）——刻意双跑（注释："its DB side effects are rolled back before the real Delta projector"），但**生产 `projection_mode="legacy"`（磁盘实测）→ 走 else 分支（`:8084-8089`），只跑 1 次**。

### 9.5 一致性成本

**`_tier_meta_sig`（`:64-73`）每章被调用多次**：`_key_chapters`（`:116`）、`_fs_records`（`:140`）、`_fs_anchors`（`:170`）各一次。每次执行 **3 个全表聚合**：

```python
fs = session.query(Foreshadowing.id, Foreshadowing.resolved).filter(...).all()
ev = session.query(StoryEvent.id).filter(...).all()
ch_n = session.query(Chapter.id).filter(...).count()
return (len(fs), (fs[-1][0] if fs else 0), unr, len(ev), (ev[-1][0] if ev else 0), ch_n)
```

> ⚠️ `fs[-1][0]` / `ev[-1][0]` 依赖 `.all()` 的返回顺序取"最后一个 id"，**没有 `ORDER BY`**——SQLite 通常按 rowid 返回，但**这不是契约**。优化器一旦改变顺序，缓存签名会错乱。

**每章写入放大估算**（book 8，286 章）：`rag.json` 重写 286 次 × 平均 ~10 MB ≈ **2.9 GB 磁盘写**；另 `entities.json` / `timeline.txt` / `rag_traces.json` 各 286 次。

### 9.6 drift 模块：跑着，但没有闭环

`drift_align_enabled` / `drift_beat_enabled` 默认 **True**（`audit_config.py:19-20`），确实在生产流水线里跑（`pipeline.py:7931-7937`）。但：

- **只落库与日志，绝不改正文**（`drift.py:9` 自述"只落日志不动正文"）
- **B3「校正回写」从未实现**：`models.py:508` `corrected = Column(Boolean, default=False)  # 是否已校正回写`——全仓 grep `.corrected` 只有 `main.py:3996` 的**读取**，**从未被置 True**（实测 `drift_log WHERE corrected=1` = **0 行**）；配置键 `drift_enable_correction` 在**任何 `.py` 中都不存在**（grep 0 命中）→ **死配置键**。`drift.py:2` docstring 宣称的 B3 从未存在
- **实测只产出一种结果**：`drift_log` 102 行全部 `kind=align / level=medium`，**beat / high / low 一条都没有**。B2 的伏笔超期分支因 77% 的 `resolve_ch=0` 而短路（`drift.py:133`）
- **口径冲突**：`main.py:3988-3990` 传 `count(done)`（已写章数）给 `beat_schedule`，而 `pipeline.py:7937` 传 `global_ch(...)`（10000 进制全局号）→ **看板与流水线给出不同滞后结论**
- 信噪比问题：`drift.py:36` 中文无分词、只在标点处切 → `hit/len` 天然偏低，实测 book23 的 align/medium ≈ 47 章 47 条，**接近噪声**
- 失败静默：`drift.py:188-189` `record_beat` 失败返回 `{}` → `pipeline.py:7938 sched.get("overdue_important", 0)` 恒 0，**失败与"无超期"不可区分**

---

## 重构建议

### 10.1 保留与删除

**保留（设计正确，只需重封装）**：损伤状态机 `_merge_impacts`（`:2044-2133`，语义完备，**只缺规划侧对接**）；生命周期前缀协议（`:213-258`，简洁且已被 prompt 教会模型）；一致性守卫族（`:2638-2733`，逻辑扎实）；伏笔账本 + 关键章池（`:76-199`，结构性召回设计优秀）；二字组 BM25 + 专名最长匹配（`hybrid.py`，零依赖且对中文有效）；装备溯源 `_merge_provenance`（`:2236-2279`）；`_compact_timeline`（`:314-351`，唯一正确的分级压缩）；`commit_recovery` 的幂等设计（四道闸门，见 §2.7）。

**删除（生产零生效 / 死代码 / 净负债，合计约 1150 行 + 5 表）**：

| 资产 | 规模 | 理由 |
|---|---|---|
| `chapter_revisions.py` + `revision_config.py` + 2 表 | 705 行 | 生产全关，表 0 行 |
| `character_delta.py` + config + `CharacterStateDelta` | 270 行 + 1 表 | 生产 `legacy`，表 0 行 |
| `commit_recovery.py` + `recovery_orchestrator.py` + config | 232 行 | `enabled=false, startup_scan=false`；`recover_service_state` 生产零调用 |
| `long_memory_prompt.py` | 70 行 | `mode="off"`；`prompt_metadata` 零调用；与"长期记忆"无关 |
| `graph_summary.py` + `BookSummary` 表 | 376 行 + 1 表 | 输出从不进 prompt，表 0 行 |
| `ForeshadowAction` / `RelationshipAction` 表 | 2 表 | 只随 revision 写入，0 行 |
| `novelty._cosine_matrix` / `_jaccard_matrix` | 32 行 | 零调用死代码 |
| `bible_rebuild.py` 的死导入/死参数 | — | `:28` 导入未用、`:103` `skip_generate` 从未被读 |
| `rag.json` 的 `k` 字段 | 每片 1 int | 写了不用（`:446` vs `:595`） |
| `_FS_W_MAP["极高"]` / `config.NOVELTY_GUARD` / `drift_enable_correction` | — | 三个死配置 |
| `damage_delta` 提取字段 | — | 只在 `chapter_revisions.py:38,42` 白名单，无消费者 |
| `story_time` 自由文本时间 | `models.py:318` | 无结构即无价值 |

### 10.2 目标架构：LangGraph State Schema

核心原则：**把"层"从 prompt 拼装变成真正的存储边界**，且**每个写入只有一个所有者（single writer）**。

```python
class ChapterState(TypedDict):
    book_id: int; vol: int; idx: int
    chapter_plan: ChapterPlan

    # 只读快照（由 LoadContext 节点填充）
    canon: CanonSnapshot            # L0+L1：世界观 + 卷目标（★ 不再混装）
    recent: RecentSnapshot          # L2：短/中/长三层
    entities_view: EntityView       # L3-实体
    injury_view: InjuryView         # L3-损伤 ★ 一等结构
    knowledge_view: KnowledgeView   # L3-认知 ★ 新增 fog-of-war
    relations_view: RelationView    # L3-关系
    graph_view: SubGraph            # ★ 与 relations_view 合并
    recall: list[RecallChunk]       # L4 + 来源标注

    draft: str; review: ReviewResult; final: str
    extraction: Extraction          # ★ 强 schema
    ledger_patch: LedgerPatch       # 唯一写入点
```

**① 损伤升级为一等账本，且参与规划（修复投诉 #1）**

```python
class Injury(BaseModel):
    id: str; character_id: str          # ★ 稳定 id + 外键，不与 name 耦合
    kind: Literal["伤势","后遗症","代价","诅咒"]
    name: str
    severity: Literal["轻微","轻","中","重","极重"]   # ★ 与阈值表统一
    status: Literal["新发","恶化","维持","好转","缓解","痊愈","解除","绑定"]
    permanent: bool = False
    since_ch: int                       # 全局章号，单一编码
    recov: str = ""
    heal_deadline_ch: int | None        # ★ ledger 计算，非注入时才算
    heal_required_beat: bool            # ★ 到期损伤强制占一个节拍

class InjuryTransition(BaseModel):      # ★ 解除 events 耦合
    injury_id: str; to_status: str
    evidence: str                       # ★ 显式字段，替代 events_blob 字符串匹配
```

**根因修复**：在 **DetailPlanner 之前**插入 `InjuryPlanner` 节点，读 `injury_view`，对 `heal_required_beat=True` 的损伤**强制分配治疗节拍**写入 `ChapterPlan.beats`，并新增 `VALID_BEATS |= {"heal"}`——直接消除 §5.3 的能力矩阵缺口，**让"看到损伤"与"规划节拍"发生在同一节点**。守卫改为 `to_status in RECOVERY and not evidence` → 拒绝；语义相同，但不再要求模型"在 events 里碰巧写出治疗词"。

**② 实体：稳定 id + 别名图 + 势力一等公民（修复投诉 #2）**

```python
class Entity(BaseModel):
    id: str                             # ★ ULID，永不变
    canonical_name: str
    aliases: list[str]                  # ★ 下游按 id 关联，名称只是显示
    kind: Literal["人物","势力","组织","地点","物品","功法","规则","术语","其他"]  # ★ 势力入枚举
    lifecycle: LifecycleState           # ★ 结构化，废除名称前缀
    first_ch: int; last_ch: int

class LifecycleState(BaseModel):
    state: Literal["常","毁·可修","毁·不可逆","失·可寻","失·不可寻",
                   "耗·一次性","衰","封","转","灭"]
    since_ch: int
    recoverable: bool                   # ★ 由 state 派生，不重复维护
```

根因修复：**`势力` 进入所有枚举**（`entities.py:17` + `graph.py:36`）；**账本全部改用 `entity_id` 外键**；**生命周期前缀从名称中彻底移除**（字符串前缀是漂移之源，§4.3/§4.6）；**别名合并成为幂等操作** `register_entity(name, aliases)`；**章号统一为 `vol*10000+idx`，禁止 1000**。

**③ 认知状态成为一等账本（填补 §8.3 空白）**

```python
class KnowledgeFact(BaseModel):
    id: str; fact: str
    truth: Literal["true","false","unknown"]
    known_by: list[str]                 # ★ entity_id：谁确切知道
    suspected_by: list[str]             # ★ 谁怀疑但不确定
    source_event_id: str                # ★ 获知途径（亲历/听闻/推断）
    since_ch: int

def known_at(entity_id: str, ch: int) -> list[KnowledgeFact]
```

悬疑/误会/背叛铺垫第一次有机械支撑。`visibility=暗` 降级为"`known_by` 为空的渲染提示"。

**④ 时间：单一编码 + 结构化故事时间**

```python
def ch_key(vol: int, idx: int) -> int:  return vol * 10_000 + idx   # ★ 全局唯一

class StoryTime(BaseModel):
    day: int                            # ★ 相对天数，可比较可排序
    phase: Literal["晨","午","暮","夜"]
    label: str = ""                     # 原文"第三日夜"，仅显示
```

**⑤ 存储：单一 writer + 事件溯源**

```
entities(id, canonical_name, kind, lifecycle, first_ch, last_ch)   entity_aliases(alias, entity_id)
character_states(entity_id, field, value_json, since_ch)          injuries(id, character_id, …) ★新
relationships(id, a_id, b_id, type, status, since_ch, until_ch)   relationship_events(id, rel_id, action, at_ch) ★新
knowledge_facts(id, fact, truth, source_event_id, since_ch)       knowledge_holders(fact_id, entity_id, mode) ★新
story_events(id, ch_key, story_day, location_id, participants)    foreshadowings(id, plant_ch, resolve_ch, resolved)
canon_claims(id, speaker_id, text, truth, kind)
向量库（独立服务）: chunks(chapter_key, text, embedding, content_hash)   ★ 增量 upsert
```

**关键改进**：**废弃 `rag.json` 整文件 JSON** → 向量库增量 upsert（消除 27.75 MB/章重写）；**`entities.json` 并入 DB**；**`timeline.txt` 降级为派生产物**；**bible 结构化为 `## ` 分节并统一三套预算**（修 §1.1 的 4/8 失效）；**`is_active` 语义修正**——若保留事件溯源则明确为"当前世代有效"，否则删除改用 `until_ch`（关系版本用区间表达，天然支持时间旅行查询）。

### 10.3 LangGraph 节点拓扑

```
PlanChapter(含 InjuryPlanner) → LoadContext → Recall(RAG) → Write
   → Review ─┬─ pass → Extract → ApplyLedger(单事务幂等) → Index(异步)
             └─ revise → Write
```

**写入点从 13 个收敛到 1 个**（对照 §9.1）。`Index` 是唯一事后异步步骤，不阻塞章节完成。

**Checkpoint 策略**：LangGraph checkpointer 天然替代 `commit_recovery`——`ApplyLedger` 是幂等事务，崩溃后重放安全。**这就是可以删除 `commit_recovery.py` 的原因。**

### 10.4 Token 预算重分配

| 块 | 现占比 | 建议 | 理由 |
|---|---|---|---|
| `character_state` (L3) | 28% | **→ 15%** | 结构化后只注入本章相关实体，不再拼 3400 字散文 |
| `bible` (L0+L1) | 25% | **→ 20%** | 拆分：L0 常驻锚点 + L1 按需，不再混装；修 4/8 失效 |
| `rag` (L4) | 15% | **→ 15%** | 保持；改精确片段（句边界） |
| `timeline` (L2) | 9% | **→ 8%** | 修掉 `[:LONG_MAX]` 截断方向 bug |
| **`injury`（新）** | 0% | **→ 8%** | 独立块，含到期治疗要求 |
| **`knowledge`（新）** | 0% | **→ 6%** | 认知状态 |
| **`graph`** | ~2.5%（关闭） | **→ 5%** | 开启，含势力节点 + 时间约束 |

净效果：同等预算下，损伤与认知从"埋在一坨散文里"变成显式块，**规划侧能看到**。

### 10.5 迁移顺序（按风险/收益）

| 阶段 | 内容 | 收益 | 风险 |
|---|---|---|---|
| **P0** | 删 `vol*1000+idx` 统一 `global_ch`；修 `entities.py:596` 跨卷 bug | 立即修复"实体被静默过滤" | 低 |
| **P0** | `势力` 加入 `ENTITY_TYPES` + `_NODE_TYPES` | 立即修复势力归类 | 低 |
| **P0** | `_merge_collection` 补 else 分支 + 落库去重 | 消除静默丢数据 | 低 |
| **P1** | 损伤表结构化 + `InjuryPlanner` 节点（细纲前） | **修复投诉 #1** | 中 |
| **P1** | bible 统一为 `## ` 分节 + 单预算 | 修复 4/8 书的按需注入失效 | 中（需重生成） |
| **P2** | 实体 id 化 + 别名表；关系改用 id | 修复 §4.4 别名分裂 | 中高（全表迁移） |
| **P2** | 删除 revision/delta/recovery/long_memory/graph_summary | −1650 行，−6 表 | 低（本就没生效） |
| **P3** | `KnowledgeFact` + `knowledge_holders` + fog-of-war | 新增能力 | 高（新模型） |
| **P3** | 向量库替换 `rag.json`；增量 upsert | 消除 GB 级重写 | 中 |
| **P4** | 图谱重建（势力节点、时间快照、真社区检测、实体消解） | 修复"图谱不靠谱" | 中 |

### 10.6 最小可用验证

1. **伤势恢复**：构造"重伤 + 无治疗章节序列"，断言细纲**必现** `beat=heal`，且 N 章后 `Injury.status` 变为 `痊愈`
2. **实体不漂移**：构造"改名 + 别名混用 + 跨卷回归"序列，断言 `entity_id` 恒定、`relationships` 不新增重复边
3. **势力归属**：断言 `kind="势力"` 实体存在，且 `人物→势力` 边在 `relations_view` 可见
4. **认知状态**：构造"A 目睹 B 死"，断言 `known_at(A, ch)` 含该事实、`known_at(C, ch)` 不含
5. **写入放大**：断言每章账本写入事务数 = 1，向量存储增量字节数 = O(本章字数)
6. **bible 裁剪**：断言任何 book 的 bible 都能切出 >1 块，且注入量 ≤ 预算

---

## 附录：本次未逐行审计的文件

`bible_rebuild.py` 的审校链细节、`chapter_revisions.py` 的 revision 激活链、`graph_summary.py` 的 LLM 摘要生成、`novelty.py` 句级对齐细节、`style_corpus.py`。**结论不受影响**：这些要么生产关闭（且表 0 行），要么不在两条投诉主线上。

---

*报告完成。所有 `file:line` 基于本次只读审计时的文件内容；所有计数均为生产库实测。*
