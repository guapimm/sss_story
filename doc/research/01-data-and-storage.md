# 01 · 数据模型与持久化 调研报告

> 调研对象：`E:\AI_project\story_llm`（LearnStory，FastAPI + Vue3 + SQLite，754 个 py 文件）
> 调研范围：`models.py` / `storage/` / `docstore.py` / `artifact_store.py` / `alembic/` / 各 store
> 调研方式：源码精读 + **生产库 `backend/learnstory.db` 实测查询**（8 本书 / 1441 章真实数据）
> 目标：为 LangChain + LangGraph 重写提供数据层依据

---

## 0. 概览（TL;DR）

| 维度 | 现状 |
|---|---|
| 表数量 | **27 张**（代码 24 个 SQLAlchemy 模型 + `alembic_version` + `chapter_chunks` 无模型表 + `research_cache` 已弃用表） |
| 生产库体积 | 8.58 MB（8 本书 / 1441 章 / 12 万+ 行） |
| 存储双轨 | DB 只存元数据+路径；正文/圣经/时间线/RAG 向量/专名表全部落 JSON/TXT 文件 |
| 表使用率 | 27 张表中 **8 张完全空表**（含 6 张 Phase 2 表），4 张表占 27 表全部行数的 78% |
| Phase 2 表 | 6 张 revision/attribution 表，**生产 0 行**，配置全部 `enabled:false, shadow:false`，但已产生 ~2000 行配套代码 |
| 外键 | **全库 0 个 FOREIGN KEY**，所有 `book_id`/`chapter_id` 均为裸 Integer |
| Index | **61 个具名索引，其中 37 个（61%）位于 0 行表上** |
| 列总数 | 27 张表共 **358 列**，其中可删除 **101 列（28%）** |
| 数据访问 | 无 Repository/DAO 层；**145 处 `db_session()` 直连**，散落在 main/pipeline/memory 等 20+ 模块 |
| 迁移 | Alembic 只有 **1 个 baseline revision**（2026-08-31），真实迁移靠 `models._auto_migrate()` 手写 `ALTER TABLE` |

**一句话结论**：这个数据层的问题是「**两套半成品架构叠加 + 三种类型的真相来源 + 没有访问边界**」。
Phase 1（JSON blob 塞进 Text 列）从未被 Phase 2（revision-owned 规范化）替换，
两者以 feature flag 形式并存；而真正的实体数据（专名表、RAG、圣经）在文件系统里，
DB 里的 `CharacterState.state` 只是 LLM 每次重新抽取的**有损投影**。

---

## 1. 表清单（完整 inventory）

### 1.1 核心业务表

| 模型 | 表名 | 行数（实测） | 关键列 | 关系/FK | 索引 |
|---|---|---|---|---|---|
| `Book` | `books` | 8 | 43 列；`topic/scene/scale/quality/phase/mode`、`outline`(JSON)、`modules_json`、`characters_json`、`bible`+`bible_path`、`story_so_far`+`timeline_path`、`book_kind`、`story_brief_json`、`char_schema_json`、`style_prefs_json`、`reuse_keys_json`、20+ 开关列 | 无 | 无（仅 PK） |
| `Module` | `modules` | 60 | `book_id, rank, name, points_json, kind(core/extend)` | `book_id`→books.id 裸引用 | `ix_book_id` |
| `Chapter` | `chapters` | 1441 | 27 列；`book_id, idx, volume, title, plot, beat, summary, points, participants, plant_fs, resolve_fs, content_path, cards, hang_json, review_score, review_json, writer_*, status, is_transition, active_revision_id` | `book_id`/`active_revision_id` 裸引用 | `ix_book_id`, `ix_active_revision_id` |
| `Foreshadowing` | `foreshadowings` | 539 | `book_id, fs_key, description, kind, weight, plant_ch, resolve_ch, resolved` | 裸引用 | `ix_book_id` |
| `KnowledgeLedger` | `knowledge_ledger` | 36 | `book_id, point_name, status, taught_ch, review_count, next_review_ch` | 裸引用 | `ix_book_id` |
| `GenreState` | `genre_states` | 1 | `book_id, genre_type(rules/rebirth/system/custom), data`(JSON), `updated_ch, source` | 裸引用 | `ix_book_id` |
| `CharacterState` | `character_states` | 77 | `book_id, name, state`(JSON), `alive`, `updated_ch` | 裸引用 | `ix_book_id`（**无 `(book_id,name)` 唯一约束**） |
| `Relationship` | `relationships` | 966 | `book_id, entity_a/entity_b, type_a/type_b, char_a/char_b(旧), relation_type, description, visibility, status, since_ch, updated_ch` | 裸引用 | `ix_book_id` |
| `StoryEvent` | `story_events` | 996 | `book_id, chapter_idx, volume, story_time, location, participants`(JSON), `description, consequence, source_chapter_id, source_revision_id, ledger_revision_id, effect_key, is_active` | 裸引用 | `ix_book_id` + 5 个 Phase2 索引 + 1 partial unique |
| `PlotNode` | `plot_nodes` | 28 | `book_id, node_key, node_type, description, target_ch, achieved_ch` | 裸引用 | `ix_book_id` |
| `CanonClaim` | `canon_claims` | 684 | `book_id, speaker, text, truth(true/false/unknown), kind(speech/deed/world), status, audience, chapter_idx, note` + 5 个 Phase2 列 | 裸引用 | `ix_book_id` + 5 个 Phase2 索引 + 1 partial unique |
| `DriftLog` | `drift_log` | 102 | `book_id, kind(align/beat/correct), level, chapter, node_key, msg, corrected` | 裸引用 | `ix_book_id`, `ix_kind` |
| `BookSummary` | `book_summaries` | **0** | `book_id, level(global/volume/community), key, content, chapter_idx, updated_at` | 裸引用 | `ix_book_id`, `ix_level` |
| `ResearchCache` | `research_cache` | **0** | `topic_key`(unique), `result_json`, `hit_count` | — | `ix_topic_key`(unique) |

### 1.2 Phase 2 revision / attribution 模型（全部生产 0 行）

| 模型 | 表名 | 行数 | 说明 |
|---|---|---|---|
| `ChapterRevision` | `chapter_revisions` | **0** | 章节不可变事实版本；`models.py:127` 自述「Phase 2A shadow 阶段只写 prepared，不参与正式读取」。25 列 / 6 索引 |
| `ChapterCommitManifest` | `chapter_commit_manifests` | **0** | 提交意图 + 阶段位图（`text_ready/extraction_ready/db_applied/active_switched/materialized`）。14 列 / 5 索引 |
| `CharacterStateDelta` | `character_state_deltas` | **0** | Revision-owned 字段 delta；`models.py:238` 「CharacterState remains the materialized projection」。14 列 / 7 索引 |
| `RelationshipAction` | `relationship_actions` | **0** | `models.py:289` 「Relationship remains the projection」。16 列 / 6 索引 |
| `ForeshadowAction` | `foreshadow_actions` | **0** | `models.py:331` 「Foreshadowing is projection」。12 列 / 7 索引 |
| （`StoryEvent`/`CanonClaim` 复用） | — | — | 老表被追加了 revision 列（`effect_key`/`is_active`/`source_revision_id`/`ledger_revision_id`） |

**实测**：`story_events.effect_key` / `source_revision_id` / `ledger_revision_id` 全部为 **NULL（0/996）**，
`is_active` 全部为 1；`chapters.active_revision_id` 全部为 NULL（0/1441）。
即：**Phase 2 的写路径在生产从未执行过一次。**

### 1.3 运维/可观测表

| 模型 | 表名 | 行数 | 备注 |
|---|---|---|---|
| `GenerationTrace` | `generation_traces` | 745 | 16 列，业务级 trace |
| `GenerationAttempt` | `generation_attempts` | 1003 | 31 列，物理模型调用（token/耗时/重试） |
| `GenerationSpan` | `generation_spans` | 98 | 17 列，阶段级 span |
| `Metric` | `metrics` | **4373** | 最大表；8 列，仅 role/model/elapsed/tokens |
| `JobQueue` | `job_queue` | **0** | 持久化任务队列，未启用 |
| `Notification` | `notifications` | 247 | 10 列 |
| （无模型） | `chapter_chunks` | 49 | ⚠️ **孤儿表**：`id, book_id, chapter_idx, text, vec_json` — 代码与 Alembic 中**均无对应模型**，是 `rag.json` 的前身 |

> ⚠️ **可观测层有一个未受控的增长风险**：`metrics`(4373) + `generation_attempts`(1003) +
> `generation_traces`(745) + `generation_spans`(98) = **6219 行**，占全库 11570 行的 **54%**。
> 且 `metrics` 与 `generation_attempts` 字段高度重叠（`role/model/elapsed/tokens/status` ⊂
> `agent/model/elapsed_ms/tokens/status`）—— 同一份调用数据被记了两遍。
> 代码中**没有任何 TTL / 清理任务**（`grep` 未发现 retention 逻辑），8 本书即 6200 行，
> 按线性外推 100 本书将达 7.7 万行纯遥测数据。

### 1.4 行数分布（实测）

```
metrics             4373  ████████████████████████  37.7%
chapters            1441  ████████                12.4%
generation_attempts 1003  ██████                   8.6%
story_events         996  ██████                   8.6%
relationships        966  ██████                   8.3%
canon_claims         684  ████                     5.9%
generation_traces    745  ████                     6.4%
foreshadowings       539  ███                      4.6%
notifications        247  █                        2.1%
drift_log            102                           0.9%
generation_spans      98                           0.8%
character_states      77                           0.7%
modules               60                           0.5%
chapter_chunks        49                           0.4%
knowledge_ledger      36                           0.3%
plot_nodes            28                           0.2%
books                  8
genre_states           1
────────────────────────────────────────────────
book_summaries         0  ← 空表
research_cache         0  ← 空表（已迁到 JSON）
job_queue              0  ← 空表
chapter_revisions      0  ← Phase2 空表
chapter_commit_manifests 0 ← Phase2 空表
character_state_deltas 0  ← Phase2 空表
relationship_actions   0  ← Phase2 空表
foreshadow_actions     0  ← Phase2 空表
```

> **8 张空表 = 30% 的表零使用**（`book_summaries` / `chapter_commit_manifests` / `chapter_revisions` /
> `character_state_deltas` / `foreshadow_actions` / `job_queue` / `relationship_actions` / `research_cache`）。
> 其中最反讽的是：`relationship_actions` / `character_state_deltas` / `foreshadow_actions` 三张
> 「精确追踪实体变更」的表是空的，而实体追踪正是用户抱怨最多的功能。

---

## 2. 存储双轨问题（DB vs 磁盘 JSON/TXT）

### 2.1 两侧各存什么

**DB 侧（只存元数据与路径）** —— `docstore.py:12` 开宗明义：
> 「数据库只保留元数据与路径。」

**磁盘侧（`backend/storage/`）** —— `docstore.py:1-14` 定义的树：

```
storage/learn/books/{book_id}/     知识小说（book_kind=learn）
  bible.txt  timeline.txt  rag.json  rag_traces.json
  entities.json  audit_cache.json  audit_words.json
  suspicious_pool.json  verify_results.json
  chapter_vecs.json  novelty_progress.json  _outline_partial.json
  chapters/v{卷}-{章}.txt
  revisions/…                       ← Phase 2 产物目录
storage/story/books/{book_id}/     纯小说（book_kind=story）+ story_arc.json
storage/books/{book_id}/           老结构（兼容只读，migration 未完成）
```

**实测磁盘分布**（8 本书）：

| 目录 | 章节文件 | `revisions/` 文件 | 书根文件 |
|---|---|---|---|
| `learn/books/3` | 8 | 5 | bible.txt, chapter_vecs.json, rag.json, rag_traces.json, timeline.txt |
| `learn/books/5` | 8 | 5 | + novelty_progress.json |
| `learn/books/7` | 20 | 3 | bible.txt, rag.json, rag_traces.json, timeline.txt |
| `story/books/8` | 160 | 7 | + entities.json, audit_cache.json, audit_words.json, suspicious_pool.json, verify_results.json |
| `story/books/11` | 10 | 0 | 同上 |
| `story/books/18` | 29 | 0 | 同上 |
| `story/books/23` | 47 | 0 | 同上 |
| `story/books/24` | 12 | 0 | + _outline_partial.json |

**`backend/` 根目录另有一层全局 JSON 存储**（不走 `LEARNSTORY_DATA_DIR` 隔离）：

| 文件 | 体积 | 内容 |
|---|---|---|
| `artifact_cache.json` | **8.1 MB** | 大纲/圣经产物缓存，LRU 60 条（`artifact_store.py:25`） |
| `research_cache.json` | **5.7 MB** | 研究缓存 + checkpoint 断点续跑（`research_store.py:25`） |
| `verify_cache.json` | 195 KB | 知识点核验结果（`research_store.py:26`） |
| `selection_cache.json` | 9.3 KB | 知识点勾选（`selection_store.py:22`） |
| 其余 ~30 个 `*_config.json` | — | 各模块热生效配置 |

### 2.2 具体不一致风险（真实存在，非理论）

**风险 A：`content_path` 大面积为空 → DB 无法定位正文。**
实测：1441 章中 **1147 章（80%）`content_path` 为空字符串**：

| book | 总章数 | 空 `content_path` | status |
|---|---|---|---|
| 3 | 8 | 0 | done |
| 5 | 8 | 0 | done |
| 7 | 20 | 0 | done |
| 8 | 286 | **126** | 160 done / 126 pending |
| 11 | 356 | **346** | 10 done / 346 pending |
| 18 | 350 | **321** | 29 done / 321 pending |
| 23 | 400 | **353** | 47 done / 353 pending |
| 24 | 13 | 1 | 12 done / 1 reviewing |

即：**pending 章的 `content_path` 一律为空，done 章靠写入时回填**。
这不是损坏，但意味着 `content_path` 是一个「已写入」的弱标记，任何按路径扫描的工具都会漏章。

**风险 B：`content_path` 有三种互不兼容的格式。**
实测 `content_path` 前缀分布：

```
story/books/8/chapters/v1-1.txt   160
story/books/23/chapters/v1-1.txt   47
story/books/18/chapters/v1-1.txt   29
learn/books/7/chapters/v1-2.txt    19
story/books/24/chapters/v1-1.txt   12
story/books/11/chapters/v1-1.txt   10
books/5/chapters/v1-1.txt           8   ← 老结构
books/3/chapters/v1-1.txt           8   ← 老结构
books/7/chapters/v1-1.txt           1   ← 同一本书内混用两种格式
```

`docstore.py:179-187` 的 `_chapter_rel()` 就是为抹平这三种格式而写的：
```python
def _chapter_rel(path: str) -> str:
    if "/chapters/" in path: return "chapters/" + path.split("/chapters/", 1)[-1]
    if path.startswith("chapters/"): return path
    return path
```
`docstore.py:199-212` 的 `read_chapter()` 还要在**三个根**里逐个兜底查找：
```python
for d in _all_roots(book_id):     # [LEARN_ROOT, STORY_ROOT, OLD_ROOT]
    q = d / rel
    if q.exists(): return q.read_text(encoding="utf-8")
return ""
```
⚠️ **注意最后一行**：读不到就静默返回 `""`。正文丢失不会报错，只会「这一章是空的」。

**风险 C：`book_kind` 是决定文件位置的唯一依据，但缓存在进程内存里。**
`docstore.py:38` `_kind_cache: dict[int, str] = {}` + `docstore.py:41-61` `_kind_of()`。
`docstore.py:99` 的注释直接承认 SQLite rowid 复用会导致路由错误：
```python
# BUG-16：清理 kind 缓存，防止 SQLite rowid 复用后新书仍按旧 book_kind 路由
```
`docstore.py:72-84` `book_dir()` 在缓存与实际不符时还要「落到真实存在处」兜底。

**风险 D：同一实体在两侧各存一份，且格式不同。**

| 数据 | DB | 磁盘 | 冲突点 |
|---|---|---|---|
| 人物状态 | `character_states.state` JSON | `entities.json`（type/importance/count/appearances） | 两边都记「谁存在」，但更新路径完全独立 |
| 关系 | `relationships` 表 | `entities.json` | 图谱 `graph.py:50-61` 用 entities.json 定节点类型，用 DB 定边 |
| 大纲 | `books.outline`(文本) | `_outline_partial.json` | 后者是断点快照，无一致性校验 |
| 正文 | `chapters.content_path` | `chapters/v{x}-{y}.txt` | 路径三格式 + 静默读空 |
| RAG | （`chapter_chunks` 废弃表） | `rag.json` + `chapter_vecs.json` | 两个文件都存向量，语义重叠 |
| 圣经 | `books.bible`（已废弃列，实测全为 0 长度） | `bible.txt` | `models.py:54` 自注「[已废弃-仅迁移用]」 |
| 时间线 | `books.story_so_far`（已废弃，实测全 0） | `timeline.txt` | `models.py:58` 同上 |

**风险 E：事务边界断裂。** 一章的落盘是「写 TXT → 写 rag.json → 写 entities.json → 写 audit_cache.json → 写 DB」，
没有任何跨介质事务。任一步失败即永久不一致。
`commit_recovery.py`(9.9KB) 与 `recovery_orchestrator.py`(912B) 两个模块的存在本身就是这个问题的证据。

**风险 F：文件写入非原子。** 多数 store 直接 `p.write_text(...)`（如 `docstore.py:110` `save_bible`、
`docstore.py:291` `rag_save`、`artifact_store.py:112`）。
只有 `audit_store.py:47-49` 做了临时文件 + `replace()` 原子替换。进程被杀 → JSON 截断 → 后续 `_load()` 静默吞异常返回 `{}`（`artifact_store.py:72-73`、`research_store.py:40-41`）。

**风险 G：全局 JSON 未走测试隔离。**
`docstore.py:22-30` / `research_store.py:17-25` / `selection_store.py:14-22` **都**定义了
`_data_root()` 读 `LEARNSTORY_DATA_DIR`；但 `artifact_store.py:23` 也定义了同名函数却**漏了 `verify_cache`**，
且这些文件位于 `backend/` 而非 `storage/`，与 DB 的 `DATABASE_URL` 是两套独立配置。

---

## 3. 实体与势力建模（用户痛点：“实体追踪，势力归属也很乱，虽然做了图谱还是不靠谱”）

### 3.1 势力不是一等实体 —— 只是 `relationships` 里一个字符串

**没有任何 `Faction` / `Organization` 表。** 势力的存在形式只有三种字符串：

1. `relationships.entity_b` 的一个值（配 `type_b='势力'` 或 `'组织'`）
2. `entities.json` 里一条 `{name, type:'组织'}`（**注意：`entities.py:17` 的枚举没有 `'势力'`**）
3. 大纲 `story_brief_json.factions` 的一段 JSON（`story_director.py:48`）

### 3.2 证据一：三套类型词汇表互相冲突（这是“乱”的根源）

| 位置 | 定义的合法实体类型 |
|---|---|
| `entities.py:17` | `("人物", "地名", "功法", "物品", "组织", "术语", "规则", "其他")` — **8 种，无「势力」** |
| `models.py:267` | `人物/地名/物品/功法/势力/组织/规则/其他` — **8 种，有「势力」** |
| `graph.py:36` | `("人物", "地名", "功法", "物品", "组织", "规则", "其他")` — **7 种，既无「势力」也无「术语」** |

`entities.py:146` 做归一化时：
```python
_type = str(e.get("type", "其他")) if e.get("type") in ENTITY_TYPES else "其他"
```
→ LLM 按 `models.py` 的提示词吐出 `type="势力"`，`entities.py` 判定它 **不在枚举内 → 直接降级为「其他」**。

`graph.py:76-79` 再降一次：
```python
meta = entity_types.get(name, {})
ntype = meta.get("type") or "其他"
if ntype not in _NODE_TYPES:
    ntype = "其他"
```

**后果**：所有势力节点在图谱里都是 `type="其他"`。
`memory.py:1231-1240` 精心写的 `【势力-势力】/【势力-地点】/【势力-物品】/【势力-功法】/【势力-规则】`
分组标题**永远不会命中** —— 因为端点类型到不了「势力」，`schemas.REL_GROUP_LABELS`（`schemas.py:273-293`）
里 17 类端点组合中带「势力」的 10 条是死代码。

### 3.3 证据二：生产库里 `势力`/`组织` 被拆得四分五裂（实测）

`relationships` 表 `type_a/type_b` 实际分布（前 12）：

```
人物/物品: 286      人物/人物: 254      人物/地名: 141
物品/地名: 103      物品/物品:  32      人物/规则:  29
地名/地名:  18      人物/术语:  15      人物/组织:  14
人物/其他:  11      地名/组织:  10      人物/势力:   7
其他/人物:   7      术语/物品:   5      物品/术语:   5
人物/地点:   4      ← 注意：'地点' 与 '地名' 并存
```

同一个概念被写成 **`组织` / `势力` / `其他` / `地点` / `地名`** 五种 type。
「势力归属」的 966 条关系里只有 **7 条**用了 `type_b='势力'`，14 条用 `'组织'`，
其余全部落在 `'其他'`/`'术语'`（即被降级丢弃的类型信息）。

### 3.4 证据三：势力归属的更新路径 —— 五元组匹配 + 盲目「解除」

`memory.py:2763-2817` 是归属更新的全部逻辑：

```python
# memory.py:2763-2765（注释）
# 五元组去重：按 (type_a, entity_a, type_b, entity_b, relation_type) 匹配，
# 防「人物-人物」与「人物-地点约束」误合并，也防同端点不同关系（如「归属」vs「被通缉」）
# 被误并覆盖；老数据 entity_a 空则回退 char_a
...
# memory.py:2802-2817
else:
    # 关系类型变化（如 归属→叛变）：同一对端点+同类型、不同 relation_type 的旧边置「解除」，
    # 防冲突边并存（旧「归属」仍标新增，与新的「叛变」边语义打架）
    for _old in (session.query(Relationship)
        .filter(...).filter(Relationship.relation_type != rt)
        .filter(Relationship.status != "解除").all()):
        _old.status = "解除"
    session.add(Relationship(..., relation_type=u.get("relation_type", ""), ...))
```

三个致命缺陷：

1. **`type_b` 进匹配键** → LLM 本次写 `组织`、下次写 `势力`，**同一对端点匹配失败**，
   被当成新关系 `session.add()`，旧边因「不同类型」**不会被置解除** → 归属边永久累积。
   实测正是如此：`顾长生→执法殿` 同时存在 `归属(status=变化)` 和 `逃避/即将对峙(status=新增)`。

2. **盲目解除所有不同 `relation_type` 的旧边** → 「归属」和「任职」「被盘查」「协助整理」
   在语义上本可共存，代码却把前一条一律置 `解除`。
   实测样本（book 18）：
   ```
   顾长生 → 天衍宗   归属        维持
   顾长生 → 执法殿   归属        变化
   顾长生 → 古魂宗   怀疑/探索   维持
   顾长生 → 执法殿   逃避/即将对峙  新增
   王长老 → 执法殿   所属        维持
   ```
   「归属」与「所属」是同一个意思的两种写法，互为「不同类型的关系」→ 互相解除。

3. **`status` 语义重叠**：`新增/变化/维持/解除` 四态（实测 329/178/204/255），
   但 `update` 分支（`memory.py:2782-2786`）直接覆盖 `row.status = status`，
   没有状态机，「已解除」的边可被下一次「维持」复活。

### 3.5 证据四：`relation_type` 是自由文本，实测 40+ 种

```
存放 25 / 持有 24 / 包含 21 / 被念名 13 / 敌对 11 / 归属 11 / 血亲 9 / 藏匿地 7
获得 7 / 查看 7 / 受制 7 / 值守 7 / 问询者与被问者 6 / 持有/比对 6 / 上下级 6
载录 5 / 探索 5 / 受制存疑 5 / 藏匿 4 / 签名记录 4 / 离开 4 / 查阅 4
持有/带离 4 / 拾取持有 4 / 所在地 4 / 所在 4 / 张挂 4 / 师徒 4 / 存放解除 4
夹存 4 / 压印显形 4 / 使用 4 / 陪护 3 / 锁闭 3 / 进入/所在 3 / 进入 3 / 试探与被试探 3 …
```

`post_processor.py:56-66` 的提示词给了规范（「人物-势力：归属、效忠、叛变、脱离…」），
但**没有任何代码层校验**。结果：「归属」「所属」「值守/协助」「单次密约赴约」「被盘查」
全部落库，且用 `/` 拼接复合语义（`归属/知情抽档`、`逃避/即将对峙`、`受制存疑`）。
下游 `graph.py:270-272` 的冲突检测只能硬编码两个词表：
```python
conflict_pairs = {
    frozenset(["敌对", "仇敌", "势不两立"]): frozenset(["师徒", "盟友", "同盟", "同门", "挚友"]),
}
```
—— 只能查 3×5 = 15 对词，对 40+ 种自由文本的覆盖率约等于零。

### 3.6 证据五：图谱默认关闭，且从不参与写入

`audit_config.json` 实测：
```json
"graph_inject_enabled": false,
"graph_inject_max_nodes": 40,
"graph_inject_max_chars": 1500,
"graph_summary_enabled": false
```
`memory.py:1247-1256`：
```python
# 阶段4-B：图检索注入（相关实体子图 + 社区归属），默认关防上下文膨胀
try:
    from .audit_config import graph_inject_enabled as _g_enabled
    ...
except Exception:
    _g_enabled = lambda: False      # ← 导入失败时静默关闭
```
`graph.py:16` 自述设计原则：「**只读不写；懒构建 + book_id 缓存；异常降级为空图不影响生成**」。

**这是「做了图谱还是不靠谱」的直接答案**：图谱是一个**只读派生视图**，
数据源是已经被类型降级污染的 `relationships` + `entities.json`；
它既不能阻止错误写入，也默认不注入 prompt，只在前端可视化时才有存在感。

### 3.7 证据六：社区（community）≠ 势力

`graph.py:209-227` 的 `communities()` 用 `nx.connected_components` —— 这是**弱连通分量**，
不是社区发现算法。在一部小说里，主角与几乎所有角色都有路径相连，
所以整个故事通常塌缩成 **1 个巨大连通分量**。
`memory.py:1272-1279` 把它注入 prompt 时称为「社区·核心」，
但实际内容会是「全书所有实体」的列表（截断到 12 个成员 / 4 个核心）。
**这个注入对「势力归属」问题零信息量，反而占了 1500 字符预算。**

---

## 4. 伤势状态生命周期（用户痛点 #1：“伤势一直不恢复，恢复必须写在细纲里否则很突兀”）

### 4.1 存储位置：`character_states.state` 的四个 JSON 字段

**没有独立的 injury 表。** 伤势是 `CharacterState.state`（一个 Text 列装 JSON）里的键。
`memory.py:1080` 定义四字段与规则类型的映射：

```python
# memory.py:1078-1083
# 状态损伤体系（v3）：四字段独立，各为 [{name,severity,status,since_ch,recov,permanent}]
# status 状态机：新发→恶化→维持→好转→痊愈/解除；代价=永久·可缓解；诅咒=绑定
"伤势": "impact", "后遗症": "impact", "代价": "impact", "诅咒": "impact",
...
VALID_RULES = {"immutable", "event", "mono_list", "inventory", "realm", "injury", "impact", "scalar"}
```

单条结构（`memory.py:2097-2104`）：
```python
cur.append({
    "name": nm,
    "severity": str(it.get("severity") or "重")[:4],   # 轻|中|重|极重|轻微
    "status": "新发" if st in ("新发", "恶化", "维持") else st,
    "since_ch": int(since_global_ch or 0),             # 全局章号 vol*10000+idx
    "recov": str(it.get("recov") or "")[:60],
    "permanent": bool(it.get("permanent")),
})
```

### 4.2 写入侧：LLM 抽取 → `_merge_impacts` 状态机

`post_processor.py:39-42` 是抽取提示词（要求 LLM 输出 `damage_delta`）：
```
- 「伤势」「后遗症」「代价」「诅咒」：结构化损伤，值为对象数组 [{name,severity,status,recov,permanent}]；
  · severity 轻|重|极重（新发/变化时给）；
  · status 新发|恶化|维持|好转|缓解|痊愈|解除|永久·可缓解|绑定
    ——**只报有变化的条目，未变化绝不重复输出**（同值不覆盖、不推进计时，防慢性损伤无限续期）；
  · 好转/痊愈/解除/缓解**必须能在 events 找到该角色的治疗/恢复/解咒/补偿事件**，否则不得报恢复类状态
```

`memory.py:2716-2721` 分派：
```python
if rule == "impact":
    # 状态损伤体系（伤势/后遗症/代价/诅咒）：状态机流转 + 恢复需事件 + 同值静默 + 永久化
    final[k] = self._merge_impacts(k, old_state.get(k), v, events_blob, name, g_now, warnings)
    continue
```

### 4.3 恢复的唯一放行闸门（`memory.py:2114-2121`）

```python
# 恢复类状态（好转/痊愈/解除/缓解）：须 events 同时含该角色名 + 治疗/恢复语义
if any(r in st for r in MemoryManager._IMPACT_RECOVERY) and not (
    events_blob
    and name in events_blob
    and any(k in events_blob for k in MemoryManager._IMPACT_RECOVERY_EVENT_KWS)
):
    warnings.append(f"⛔ 一致性守卫：{name}.{key}[{nm}] 恢复（{prev_st}→{st}）缺治疗/恢复事件支撑，保留原状态")
    continue        # ← 恢复申报被拒，伤维持原状
```

关键词表（`memory.py:2035-2039`）：
```python
_IMPACT_RECOVERY_EVENT_KWS = (
    "治疗", "疗伤", "医治", "救治", "痊愈", "康复", "复原", "愈合",
    "好转", "缓解", "解除", "恢复", "服药", "用药", "敷药", "丹药", "疗愈",
    "解毒", "解咒", "祛除", "驱散", "拔除", "休养", "调养",
)
```

**这是「伤势一直不恢复」的第一因**：恢复必须同时满足
① LLM 在 `state_updates` 里**主动申报** `status=好转/痊愈`，
② LLM 在**同一章的** `events` 里写出带治疗语义的事件，
③ 事件文本里**字面包含角色名**。
三个条件都依赖同一次 LLM 抽取的自觉性。任一条不满足 → 恢复被拒 → 伤留在活跃列表里。

### 4.4 痊愈发起的第二条路径（旧格式兼容，`memory.py:2066-2077`）

```python
if not isinstance(new, list):
    # 兼容旧提取/测试的字符串值：痊愈申报（对旧条目批量尝试，守卫决定）或维持描述
    if isinstance(new, str):
        s = new.strip()
        if not s or s in ("痊愈", "康复"):
            if not (isinstance(old, list) and old):
                return old  # 无旧损伤，痊愈申报空转
            new = [{"name": _norm(x), "status": "痊愈"} for x in old if isinstance(x, dict)]
        else:
            new = [{"name": s[:40], "severity": "重", "status": "维持", "recov": ""}]
```
LLM 报一个字符串 `"伤势": "痊愈"` 会被展开为「所有旧条目 → 痊愈」，
但仍要过 `4.3` 的同一道 events 闸门。

### 4.5 永久化是「单向出口」—— 但 `痊愈` 也被算作终态（`memory.py:2108-2110`）

```python
# 永久态/痊愈态不再变化（单向出口）
if any(t in prev_st for t in MemoryManager._IMPACT_TERMINAL):
    continue
```
```python
_IMPACT_TERMINAL = ("痊愈", "解除", "永久", "绑定")     # memory.py:2030
_IMPACT_RECOVERY = ("好转", "痊愈", "解除", "缓解")     # memory.py:2031
```

⚠️ **这四行是本次调研中最值得重写的一组**：

1. **两个集合重叠**：`痊愈`/`解除` 同时是「终态」和「恢复态」。
   若某条目已是 `痊愈`（理论上会被 `memory.py:2133` 剔除，但旧数据/旧格式可能残留），
   它既不会被更新（`2109` 拦截），也不会被重新申报恢复。
2. **`memory.py:2127-2131` 的两个 `permanent` 写入语义冲突**：
   ```python
   if it.get("permanent"):
       prev["permanent"] = True                    # ① LLM 显式申报
   # 永久化也保留（代价/诅咒/经判定的永久后遗症）
   if any(t in st for t in MemoryManager._IMPACT_TERMINAL) and "永久" not in st:
       prev["permanent"] = True                    # ② status 含 痊愈/解除/绑定 即打永久
   ```
   分支 ② 的意图是「status 含『绑定』→ 视为永久」，但它用的是 `_IMPACT_TERMINAL` 全集，
   于是 `status="痊愈"` 也会命中（`"永久" not in "痊愈"` 为真）→ 给一个**即将被删除的条目**
   写入 `permanent=True`。紧接着 `memory.py:2132-2133` 把所有 `痊愈`/`解除` 条目
   **整体从活跃列表剔除**：
   ```python
   # 痊愈/解除从活跃列表移除（生命周期历史由 events/时间线承载）
   return [x for x in cur if not any(t in str(x.get("status") or "") for t in ("痊愈", "解除"))]
   ```
   → **分支 ② 对「痊愈」路径而言是死代码**（写了立刻被丢掉）；只对「绑定」生效。
3. 即便意图正确，`permanent`（布尔）与 `status`（字符串）是**同一事实的两份表示**，
   可相互漂移：报 `status="永久·可缓解"` 时 `"永久" in st` 使分支 ② 不触发，
   若 LLM 未同时给 `permanent=true`，就出现 `status` 说永久、`permanent` 说否。

**净效果**：损伤的终局语义散落在 2 个 Python 元组 + 1 个布尔字段 + 1 个中文字符串子串匹配上
（`"永久" in st`、`"绑定" in st`、`"痊愈" in st`）。这是「伤势行为不可预测」的结构性原因之一。

### 4.6 注入侧：为什么恢复「必须写在细纲里否则很突兀」

`memory.py:1001-1007`：
```python
if not only_changes and th and th < 999 and _cur > _since:
    el = max(0, _cur - _since)
    if el >= th:
        warn = " ⚠️已超恢复期，本轮必须推进（恢复剧情或明示永久化）"
    elif el >= th * 0.8:
        warn = " ⚠️接近恢复期，建议本轮推进"
lines.append(f"    - [{label}·{sev or '重'}{'·永久' if permanent else ''}·{status}] {nm}（{elapsed}{recov}）{warn}".rstrip())
```

阈值表（`memory.py:853-859`）：
```python
_IMPACT_BASE_TH = {
    ("伤势","轻微"): 1, ("伤势","轻"): 1, ("伤势","中"): 2, ("伤势","重"): 3, ("伤势","极重"): 5,
    ("后遗症","轻微"): 3, ("后遗症","轻"): 4, ("后遗症","中"): 6, ("后遗症","重"): 8, ("后遗症","极重"): 12,
    ("心魔","轻微"): 3, ("心魔","轻"): 4, ("心魔","中"): 6, ("心魔","重"): 8, ("心魔","极重"): 12,
    ("代价", ""): 12,   # 缓解/补偿剧情
    ("诅咒", ""): 999,  # 由解咒伏笔驱动，不按章催
}
```

弹性因子（`memory.py:938-962`）：core ×1.2；装备含 `药/丹/散/膏/参/灵芝/露/草/玉露/圣水/灵液` ×0.7，
否则 ×1.3；卷末 20% ×0.6；夹在 `[0.6, 1.4]`。

**于是产生了整个系统的核心张力**：
- **Writer** 收到 `⚠️已超恢复期，本轮必须推进（恢复剧情或明示永久化）`
- **Editor** (`editor.py:69`) 被要求：「状态表标注『⚠️已超恢复期，本轮必须推进』的损伤 → 本章必须安排推进（恢复剧情或明示永久化），无推进 → 判 pacing 必改」
- 但**恢复申报会被 `4.3` 的闸门拒绝**（除非同章有治疗事件）
- 而**永久化是单向出口**（`4.5`），且「报痊愈」会误打永久标记

→ Writer 若临时加一段「服下疗伤丹药」，Editor (`editor.py:66-67`) 又判：
```
· 恢复类（好转/痊愈/解除/缓解）必须能在本章找到治疗/恢复解咒剧情过程——
  **草率痊愈（无过程即"次日痊愈/伤好如初"）→ 判 consistency 必改**（补治疗过程或改为维持/永久化）
```
→ **这就是「恢复必须提前写进细纲」的成因**：
细纲（`chapters.plot`）是唯一能让「疗伤」显得有铺垫的地方；
正文里临时治伤会被 Editor 判 consistness 必改，不治又会被判 pacing 必改。

### 4.7 注入分层：配角伤完全不追踪（`memory.py:988-989`）

```python
if only_changes and status in ("维持", "绑定") and "永久" not in status:
    continue  # 配角慢性维持不追踪（仅剧情需要时体现）
```
`memory.py:968-970`：
```
only_changes=True 时（非主角/非 core 配角）：只注入有变化（新发/恶化/好转/痊愈/解除/缓解）的条目，
慢性维持（维持/绑定/永久）跳过——配角伤势只要剧情合理可一笔带过，不逐章追踪。
```
→ 配角伤一旦进入「维持」，**永远不再被注入，也永远不再被催促** → 永久僵尸化。

### 4.8 生产库实测：状态机的真实健康度

对 77 行 `character_states` 全量解析：

```
total impact entries = 36
  missing/zero since_ch = 13   （36% 无起始章号 → 不算时长、不催恢复）

status   分布: 新发 13 | 恶化 7 | 好转 3 | 旧字符串 13  ← 从未出现「痊愈」「解除」「永久」
severity 分布: 重 12 | 轻 9 | 极重 2 | 无(旧格式) 13
field    分布: 伤势 32 | 诅咒 4       ← 「后遗症」「代价」生产 0 条
```

**关键证据**：
1. **生产库里 `status` 从未取过 `痊愈` / `解除` / `永久`** —— 36 条损伤中 0 条走完生命周期。
   唯一接近的是 3 条「好转」（book 23 `手背墨痕`、book 24 `后腰符刃擦伤`、`神魂溃散危机`）。
2. **13/36（36%）是 v3 之前的旧字符串格式**（如 book 3 `沈砚`：`"丹田灵气部分恢复，仍处虚弱状态"`），
   没有 severity / status / since_ch，被 `_merge_impacts:2082-2084` 迁移为
   `{"severity":"重","status":"维持","since_ch":0}` —— 即**既不算时长、也不催恢复、也不注入（配角路径）**。
3. **`since_ch` 的 13 条缺失** 精确对应旧格式；且 book 23 `陈砚` 的 `since_ch` 是 **5/10/27/28/42**
   （**卷内号**），而 `updated_ch` 是 **10047**（全局号）。
   `memory.py:892-926` 的 `_impact_since_global()` 整个函数就是为修补这个口径错位而写，
   注释（`memory.py:895-902`）承认：
   ```
   为什么需要（2026-09-13 书 24 实测 P0）：since_ch 长期被写入侧存成卷内号，
   而消费侧 ctx["cur_ch"] 是全局号 → 已持续 = 10012 - 1 = 10011。
   后果不只是显示错：同一个 elapsed 还被拿去比恢复阈值，阈值仅 1~3，于是每章恒真
   → 每个损伤每章都挂「本轮必须推进」，一章推进不完 7 处
   → Editor 每章判「必推进的损伤未推进」→ 4~5 分 + 必改失败。
   这是 docs/TODO_20260913.md §7.1「Editor 连续低分」的上游根因。
   ```
4. **修复靠 `fallback` 而非收口**（`memory.py:860-884`）：未登记 severity 回落到「重」档并 `log_problem` 一次。
   `memory.py:848-851` 自述：
   ```
   ⚠️ 每个 field × severity 组合都必须有值（2026-09-13 修）：原表只登记「轻/重」两档，
   而模型实际会写「中」「极重」「轻微」→ _impact_threshold 拿不到 base → 返回 None
   → 这些损伤永远不会被催「本轮必须推进」。实测书 24：最重的「神魂受创（伤势·极重）」
   恰恰是唯一不催的那条，而轻伤催得最凶 —— 注意力被反向引导。
   ```
   —— 这段注释本身就是「一个 6×5 的枚举表靠人肉维护」这一设计缺陷的判决书。
5. **book 24 `楚渊` 是失效样本**：7 条损伤，`since_ch` 全为 1/2/3（卷内号），
   4 条状态为「恶化」，无一「好转」。这正是阈值错位后「每章恒真催促、但一章推不完 7 处」的现场。

### 4.9 细纲层完全没有伤势概念

`grep 伤势|损伤|伤残|恢复` 在 `agents/architect.py`（规划 Agent，生成 `chapters.plot` 细纲）
中 **0 命中**（17 处命中全是「断点恢复」「骨架恢复」等无关语义）。

→ **细纲规划阶段根本不规划伤势**。伤是写作期由 `post_processor` 从正文倒推抽取的，
恢复也只能在写作期临时处理。用户在需求里说的「恢复必须写在细纲里」，实际上是
**「系统没有在细纲里给恢复留位置，所以只能在正文里硬塞，然后被 Editor 判突兀」**。

---

## 5. Schema 变迁与 churn 证据

### 5.1 Alembic 只有 1 个 migration，且已过期

```
alembic/versions/5886c58b3729_baseline_all_tables.py   (2026-08-31, 312 行)
production alembic_version = '5886c58b3729'            ← 与 baseline 一致，之后无 revision
```

**baseline 只创建了 18 张表**，而生产库有 27 张。baseline **缺失**：
`book_summaries`、`chapter_chunks`、`chapter_commit_manifests`、`chapter_revisions`、
`character_state_deltas`、`foreshadow_actions`、`relationship_actions`、
`generation_attempts`、`generation_spans`、`generation_traces`。

→ **10 张表（37%）从未进入 Alembic**，仅靠 `Base.metadata.create_all()` 建表。
`alembic downgrade` 会直接 drop 掉 baseline 里的 18 张表却留下这 10 张 → 迁移不可逆。

### 5.2 真实迁移机制：`models._auto_migrate()` 手写 ALTER

`models.py:564-661`，在每次 `init_db()` 时执行（`models.py:559-561`）：
```python
def init_db():
    Base.metadata.create_all(engine)
    _auto_migrate()
```

这是一个 **dict 形式的硬编码迁移清单**，实测 14 张表 / 约 60 列：
```python
"books": [ ("bible_path","TEXT"), ("timeline_path","TEXT"), ... 22 列 ],
"chapters": [ ("content_path","TEXT"), ... 12 列 ],
"story_events": [ ("source_chapter_id","INTEGER"), ... 5 列 ],
"canon_claims": [ ... 5 列 ],
"chapter_revisions": [("ledger_revision_id","INTEGER")],
"foreshadowings": [("kind",...), ("weight",...)],
"modules": [("kind",...)],
"character_states": [("alive","BOOLEAN DEFAULT 1")],
"relationships": [("visibility",...), ("entity_a",...), ("entity_b",...), ("type_a",...), ("type_b",...)],
"knowledge_ledger": [("review_count",...), ("next_review_ch",...)],
"metrics": [("status",...), ("book_id",...), ("provider",...)],
"notifications": [("meta","TEXT DEFAULT '{}'")],
"generation_attempts": [("usage_source",...), ("usage_complete",...)],
```
外加两段**数据回填**（`models.py:650-661`）：
```python
# 关系泛化回填：老库 char_a/char_b 有值而 entity_a/entity_b 为空的，复制过去（type 保持人物）
UPDATE relationships SET entity_a = char_a, entity_b = char_b
WHERE (entity_a IS NULL OR entity_a = '') AND (char_a IS NOT NULL AND char_a != '')
```
和两个 partial unique index（`models.py:638-649`）。

**代码自己的注释就是 churn 的控诉**（`models.py:613-614`）：
```python
# 逐表读列清单；读不到只跳过本表，且必须留痕（旧实现一张表一个 try，
# 任一列 ALTER 失败就跳出整表 → 同表剩余列被静默跳过，迁移半成品）。
```
（`models.py:566`）：
```python
# 注意：books 只能有一个键（dict 重复键会静默覆盖），新增列一律追加到这里
```

### 5.3 已废弃但仍占列的字段（实测确认）

| 表.列 | 声明 | 实测状态 |
|---|---|---|
| `books.bible` | `models.py:54` `# [已废弃-仅迁移用] 圣经改存文档` | 8/8 行长度 **0** |
| `books.story_so_far` | `models.py:58` `# [已废弃-仅迁移用] 时间线改存文档` | 8/8 行长度 **0** |
| `chapters.cards` | 无注释，无消费方 | 保留 |
| `research_cache`（整表） | 已被 `research_store.py` 取代 | **0 行**；`research_store.import_rows()`（`research_store.py:80-101`）是一次性迁移函数 |
| `chapter_chunks`（整表） | **无模型、无 Alembic** | 49 行孤儿数据 |
| `relationships.char_a/char_b` | `models.py:263-264` `# char_a/char_b 为老字段…仅当 entity_a 为空时回退读取` | **966/966 行仍被写入**（`memory.py:2823-2824` 新边同时写两套） |
| `foreshadowings.weight` | `models.py:189` 声明 `重要/普通` | 实测值：`普通/重要/关键/中/高/极高/1/2/3/4/5` — 自由文本 |
| `foreshadowings.kind` | `models.py:188` 声明 `短期/长期/贯穿` | 实测值：`短期/长期/贯穿/贯穿型/中期/人物/线索/世界观/主题/人物关系/悬念/角色背景/角色能力` — 13 种 |
| `CharacterState.state` 旧字符串伤势 | v3 迁移目标 | **13/36 条未迁移** |

### 5.4 复杂度死重估算

| 死重项 | 规模 | 判据 |
|---|---|---|
| Phase 2 六表 + 配置 + 代码 | 6 表 / 98 列 / 31 索引 / ~2000 行代码 | 生产 **0 行**；`revision_config.json` / `character_delta_config.json` 全 false |
| 空表 | 8 张（30%） | 实测 0 行 |
| 孤儿表 | 1 张（`chapter_chunks`） | 无模型无迁移，49 行 |
| 已废弃列 | `books.bible` / `books.story_so_far`（2 列） + `research_cache` 整表 6 列 | 实测全空 / 已迁移 |
| 双写列 | `relationships.char_a/char_b`（2 列 × 966 行） | 100% 冗余复制 `entity_a/entity_b` |
| 按行数计的索引 | **37 / 61 索引（61%）位于 0 行表上** | 8 张空表各带索引：`character_state_deltas` 7 / `foreshadow_actions` 7 / `chapter_revisions` 6 / `relationship_actions` 6 / `chapter_commit_manifests` 5 / `job_queue` 3 / `book_summaries` 2 / `research_cache` 1 |
| 迁移清单 | `_auto_migrate` 约 60 列手写 ALTER + 2 段回填 | 本该是 alembic revision |

**结论**：表数量 27 → 干净重写可压到 **12~14 张**（约 -50%）；
列数 358 → 可压掉 **101 列（28%）**，构成：
Phase 2 自有 81 列（25+14+14+16+12）+ 寄生在老表的 10 列（`StoryEvent`/`CanonClaim` 各 5）
+ 废弃列 10 列（`books.bible`/`books.story_so_far` 2 + `relationships.char_a/char_b` 2 +
`research_cache` 6）。

---

## 6. 数据访问边界（Repository / DAO）

### 6.1 结论：**没有任何抽象层**

`models.py:664-665` 是整个数据访问层的全部：
```python
def db_session() -> Session:
    return SessionLocal()
```
调用方拿到裸 `Session`，自己写 query、自己 `commit()`、自己 `close()`。

### 6.2 实测耦合度：145 处直连

```
main.py         55 处 db_session()
pipeline.py     28 处
memory.py        3 处（但内部持有的 session 由调用方传入）
notifications.py 6 处
continue_story.py 9 处
job_queue.py     6 处
trace_service.py 3 处
infra/memory.py  4 处
docstore.py      1 处（_kind_of 查 book_kind）
llm/client.py    1 处
style_corpus / graph_summary / estimator / exporter / observability / audit_words / bible_rebuild / book*_ultra_repair …
```

对比表规模：
- `pipeline.py` **7930 行**，`main.py` **4072 行**，`memory.py` **2836 行**，
  `continue_story.py` 1419 行，`entities.py` 1099 行 —— 5 个文件共 **17,356 行**，
  承载了几乎全部业务 + 数据 + HTTP 逻辑。

### 6.3 反模式清单（真实代码）

**(a) 事务边界由最外层随机决定。** `memory.py:114/138/168` 内部直接 `db_session()`：
```python
session = db_session()
```
而 `memory.py:2747` 起的大段写入（`_apply_relationship_actions` + Relationship 循环 +
StoryEvent 循环）用的是**调用方传入的 session**。同一个 MemoryManager 混用两种模式。

**(b) `commit()` 散布且时机不一。** 部分路径在 pipeline 层统一 commit，
部分模块自行 commit（如 `notifications.py:38/80/98/114/130/147` 六处）→ 无原子性。

**(c) 命名冲突：两个 `memory.py`。**
`app/memory.py`（158 KB，业务记忆）与 `app/infra/memory.py`（4 处 `db_session()`）。
后者是基础设施层，但两者职责重叠。

**(d) 模块级单例 store + 进程内缓存，与 DB 事务无关。**
```python
# graph.py:29-31
_cache_lock = threading.RLock()
_cache: dict[int, nx.Graph] = {}          # book_id -> Graph
_centrality_cache: dict[int, dict] = {}
# docstore.py:35 / :251
_lock = threading.Lock(); _rag_cache: dict = {}
# entities.py:15
_cache: dict[int, dict] = {}              # book_id -> {version, entities:[...]}
```
`graph.py:39-47` 提供 `invalidate(book_id)`，但**没有任何机制保证写入后一定调用它**。
`entities.py` / `docstore.py` 的缓存甚至没有失效 API（`docstore._kind_cache.pop` 只在 `delete_book` 里）。

**(e) 删书清理靠人工清单 + 反射测试兜底。**
`models.py:696-718` 的 `BOOK_SCOPED_MODEL_NAMES`（21 个模型名），
注释（`models.py:688-695`）自述：
```
# 为什么要有这份清单 + `book_scoped_models()`：
# 删书端点历史上手工维护一个模型元组，漏过 6 张表 —— 其中 GenerationTrace 是**生产已
# 启用**的表，实测生产库已经存在「书已删除、trace 行还在」的孤儿行
```
（实测当前 15 张表孤儿数均为 0 —— 说明 `test_delete_book_contract.py` 生效了，
但这是**用测试弥补缺失的 FK CASCADE**。）

**(f) `Query` 构造散落在 HTTP 层。** `main.py:3898` 等处直接在 FastAPI endpoint 里 query，
没有 service 层 → API 契约与 schema 强耦合。

---

## 7. 重构建议（供 LangGraph 重写落地）

### 7.1 数据层五大问题（按危害排序）

| # | 问题 | 证据 | 危害 |
|---|---|---|---|
| **1** | **实体类型三套词汇表冲突，势力/组织被降级为「其他」** | `entities.py:17` vs `models.py:267` vs `graph.py:36`；实测 `type_b` 出现 `组织/势力/其他/术语/地点/地名` 六种写法，`势力` 仅 7/966 | 势力归属永远建不成；图谱节点类型全错；`memory.py:1231-1240` 的势力分组标题是死代码 |
| **2** | **伤势恢复的唯一闸门是「同章 events 含治疗语义 + 角色名字面匹配」** | `memory.py:2114-2121` + `_IMPACT_RECOVERY_EVENT_KWS` (`memory.py:2035`)；实测 36 条损伤 **0 条走到「痊愈」** | 用户痛点 #1 的直接成因；细纲无伤势规划（`architect.py` 0 命中）→ 恢复只能临时硬塞 → 被 Editor 判突兀 |
| **3** | **`_auto_migrate` 手写 ALTER + Alembic baseline 过期（10 张表不在迁移内）** | `models.py:564-661`（14 表 ~60 列）；baseline 只建 18 表 vs 生产 27 表；`alembic downgrade` 不可逆 | 无法安全演进；`models.py:613-614` 自述历史 bug「迁移半成品」 |
| **4** | **DB/文件双轨无事务、无一致性校验，且读失败静默返回空** | `docstore.py:12`（DB 只存路径）；`content_path` 80% 为空 + 3 种格式；`read_chapter` 失败返回 `""` (`docstore.py:212`)；多数 store 非原子写 | 数据静默丢失；跨介质不一致无法发现 |
| **5** | **零 FK / 零 DAO / 145 处裸 session；`Relationship` 五元组匹配把 `type_b` 当键** | 全库 0 FOREIGN KEY；`models.py:664`；`main.py` 55 处 `db_session()`；`memory.py:2763-2817` | 归属边永久累积（`顾长生→执法殿` 归属+逃避并存）；删书靠人工清单 |

**附加（第 6 位，但影响体验）**：
Phase 2 六表 + 配置 + 代码（生产 0 行）在每次读代码时都构成认知负债；
`_impact_threshold` 的 6×5 枚举表靠 `fallback` + `log_problem` 兜错（`memory.py:848-884`）。

### 7.2 应该存活到新 schema 的表 / 字段

**✅ 保留并规范化（12 张核心）**

| 新表 | 来源 | 改造要点 |
|---|---|---|
| `books` | `books` | 删 `bible` / `story_so_far` / `bible_path` / `timeline_path`；`outline` / `modules_json` 等 JSON blob 拆成子表或移到对象存储；20+ 开关收敛为 `settings_json` |
| `chapters` | `chapters` | 删 `writer_provider/writer_route/writer_fallback_*`（4 列可合并为 `writer_json`）；`content_path` → 改为内容寻址（`content_hash`），删路径三格式问题 |
| `entities` **（新建）** | `entities.json` + `CharacterState` | **统一实体表**：`id, book_id, name, type, importance, aliases_json, first_ch, last_ch, count`。`type` 用**单一权威枚举**，`势力` 与 `组织` 合并为一类 |
| `character_states` | `character_states` | `state` JSON 拆出损伤为独立表（见下）；加 `UNIQUE(book_id, name)`（当前缺失） |
| `injuries` **（新建）** | `CharacterState.state["伤势"/"后遗症"/"代价"/"诅咒"]` | `id, book_id, character_id, kind, name, severity, status, since_ch, healed_ch, recov, permanent`。**独立成表**才能做状态机 + 查询 + 细纲联动 |
| `relationships` | `relationships` | 删 `char_a/char_b`（966 行 100% 冗余）；`type_a/type_b` → FK 到 `entities.type`；`relation_type` 改为**受控枚举 + 可空自由文本副字段**；加 `UNIQUE(book_id, entity_a_id, entity_b_id, relation_type)` 并**去掉 `type` 出匹配键** |
| `story_events` | `story_events` | 删 5 个 Phase2 列（`source_revision_id/ledger_revision_id/effect_key/is_active` 全 NULL/全 1）；`participants` JSON → 关联表 `event_participants` |
| `foreshadowings` | `foreshadowings` | `kind` / `weight` 收敛为枚举（实测 13 种 kind、9 种 weight 自由文本） |
| `plot_nodes` | `plot_nodes` | 保留 |
| `canon_claims` | `canon_claims` | 删 5 个 Phase2 列 |
| `knowledge_ledger` | `knowledge_ledger` | 保留（学习小说核心） |
| `drift_log` | `drift_log` | 保留但加 TTL/保留策略 |

**🔶 改造后保留**

| 表 | 处置 |
|---|---|
| `genre_states` | 保留（`GenreState` 的 `data` JSON 结构清晰，是学习小说题材特有资产） |
| `modules` | 保留（`points_json` 可拆 `knowledge_points` 子表） |
| `book_summaries` | 保留（`graph_summary.py` 需要，但当前 0 行 → 明确是否还需要） |
| `generation_traces` / `attempts` / `spans` | 合并为 1 张 `llm_calls`；**必须有保留策略**（现 `metrics` 4373 行无清理，`generation_attempts` 31 列） |
| `notifications` / `job_queue` | 保留（`job_queue` 当前 0 行 → 若 LangGraph 用 checkpoint 则删除） |
| `metrics` | **合并进 `llm_calls`**（`metrics` 的 8 列是 `generation_attempts` 的子集，4373 行冗余） |

**❌ 应删除（重写时不要移植）**

| 项 | 理由 |
|---|---|
| `chapter_revisions` / `chapter_commit_manifests` / `character_state_deltas` / `relationship_actions` / `foreshadow_actions` | **生产 0 行**，配置全 false。LangGraph 的 checkpoint + `Store` 原生提供不可变版本与 delta，无需自建 6 表 98 列 |
| `CharacterStateDelta` 的 `effect_key` 幂等设计 | 可用 LangGraph node 幂等（`thread_id` + `checkpoint_id`）替代 |
| `research_cache`（表） | 已被 `research_store.py` JSON 取代（0 行，`import_rows` 是一次性迁移） |
| `chapter_chunks` | 无模型无迁移的孤儿表，49 行；`rag.json` 已取代 |
| `books.bible` / `books.story_so_far` | 实测全 0 长度 |
| `relationships.char_a/char_b` | 966 行 100% 冗余双写 |
| `_auto_migrate()` | 换成真正的迁移链（重写期可直接 Aerich / Alembic autogenerate） |

### 7.3 针对两个用户痛点的具体重写建议

#### A. 势力/实体（「实体追踪，势力归属也很乱」）

1. **建 `entities` 一等表，`势力`/`组织` 合并为单一类型**，`type` 用 DB 级 CHECK 约束或枚举表。
   严禁三处各定义一套词汇表。
2. **关系端点用 `entity_id` FK，不用字符串 name**。当前按 name 字符串匹配
   （`memory.py:2772-2779`）意味着改名/别名/生命周期前缀（`entities.py:119-127` 的 `[毁·可修]X`）
   全部导致匹配失败。实测 `type_b` 的六种写法就是字符串匹配的直接后果。
3. **`relation_type` 收敛为受控枚举**（归属/效忠/师徒/敌对/盟友/血亲/任职/所在地…），
   自由文本降级为 `note` 字段。`post_processor.py:56-66` 已给出完整枚举，直接落地即可。
4. **归属采用「单一权威边」语义**：一个角色的「当前势力」应为 `characters.faction_id` 外键
   （或 `memberships` 表带 `start_ch`/`end_ch`），而不是 `relationships` 里可累积的边。
   历史变更走时间区间，不做「盲目解除」（`memory.py:2802-2817` 的 `_old.status = "解除"` 必须去掉）。
5. **图谱改为可写反馈回路**：`graph.py` 目前 `只读不写`（`graph.py:16`）且 `graph_inject_enabled=false`。
   重写时把图谱一致性检查接到**写入前的校验节点**上，而不是写完之后的只读视图。
6. `graph.py:209-227` 的 `connected_components` 改为真正的社区发现（Louvain/Leiden），
   否则「社区」= 全书，注入无信息量。

#### B. 伤势（「伤势一直不恢复 / 恢复必须写在细纲里」）

1. **损伤独立成表 `injuries`**，带显式状态机 + `heal_planned_ch` 字段。
2. **把「恢复计划」前置到细纲阶段**：`architect.py` 目前 0 处伤势逻辑 →
   细纲应产出 `injury_plan: [{character, injury, inflicted_ch, planned_heal_ch, heal_method}]`。
   这直接回应用户的「恢复必须写在细纲里」—— 把隐性要求变成**显式数据**。
3. **删掉「同章 events 含治疗语义 + 角色名匹配」这一闸门**（`memory.py:2114-2121`）。
   改为：恢复必须匹配 `injury_plan.planned_heal_ch`，或由单独的剧情事件显式触发。
   当前闸门的三重巧合依赖（LLM 申报 + 同章事件 + 字面名字）是 0% 恢复率的根因。
4. **消掉 `status` 字符串与 `permanent` 布尔的双重表示**（`memory.py:2109-2131`）。三个缺陷叠加：
   - `_IMPACT_TERMINAL = ("痊愈","解除","永久","绑定")`（`memory.py:2030`）与
     `_IMPACT_RECOVERY = ("好转","痊愈","解除","缓解")`（`memory.py:2031`）**集合重叠**：`痊愈`/`解除`
     同时属于「终态」和「恢复态」两个语义类别。
   - `memory.py:2130-2131` 的 `if any(t in st for t in _IMPACT_TERMINAL) and "永久" not in st:
     prev["permanent"] = True` —— 当 LLM 报 `status="痊愈"` 时 `"永久" not in "痊愈"` 为真，
     于是给一个**即将被删除的条目**写入 `permanent=True`；紧接着 `memory.py:2133` 把
     `痊愈`/`解除` 条目整体从活跃列表剔除 → **这次写入是死代码**。
   - 反向缺陷同样存在：报 `status="永久·可缓解"` 时 `"永久" in st` 使该分支不触发，
     若 LLM 未同时给 `permanent=true`，则 `status` 说永久、`permanent` 说否 —— 两个字段可漂移。
   - 新设计应只保留**一个**表示（建议 `status` 枚举 + `healed_ch` 列），删掉 `permanent` 布尔。
5. **`_IMPACT_BASE_TH` 的 6×5 枚举表改为公式**（severity 数值 × field 系数），
   支持任意 severity 字符串，消灭 `_warn_unknown_severity` / `_IMPACT_SEVERITY_FALLBACK` 兜错机制。
6. **消除 `since_ch` 全局号 vs 卷内号的双口径** —— 根治方案是改用**单一全局章序**
   （不要 `vol*10000+idx` 的编码，用真的 `global_chapter_no` 单调递增列）。
   `models.py:668-681` 的 `global_ch` / `fmt_global_ch` + `memory.py:892-926` 的
   `_impact_since_global` 整个修补函数都可以删除。
7. **配角伤也要有终结态**：`memory.py:988-989` 让配角「维持」伤永不注入 → 永远僵尸化。
   新设计应让所有损伤都能通过 `planned_heal_ch` 到期或显式关闭。

### 7.4 双轨存储的处置建议

| 内容 | 建议 |
|---|---|
| 章节正文 | 保留文件存储（大文本），但改为 **content-addressed**（`sha256` 命名）+ DB 存 `content_hash`，消除 `content_path` 三格式与 80% 空值问题 |
| `bible.txt` / `timeline.txt` | 保留文件（长文本 + 人工可编辑），DB 存 hash + mtime |
| `entities.json` | **迁入 DB `entities` 表**（这是关系型数据，且在文件里导致图谱与 DB 脱节） |
| `rag.json` / `chapter_vecs.json` | 迁入向量库（LangChain `VectorStore`），删 `chapter_chunks` 与 `rag.json`。当前两个文件都存向量且语义重叠 |
| `audit_cache.json` / `suspicious_pool.json` / `verify_results.json` / `novelty_progress.json` | 迁入 DB（都是结构化小数据） |
| `_outline_partial.json` | 用 LangGraph checkpoint 替代（LangGraph 原生断点续跑，可删 `commit_recovery.py` + `recovery_orchestrator.py`） |
| `artifact_cache.json` (8.1 MB) / `research_cache.json` (5.7 MB) | 迁入 DB 或对象存储；加 TTL + 明确的失效策略（当前 LRU 60 条硬编码在 `artifact_store.py:25`） |
| ~30 个 `*_config.json` | 收敛为 1 张 `settings` 表或 1 个配置文件（当前每个模块一个文件 + 一个 accessor 模块，如 `revision_config.py` / `character_delta_config.py` / `review_config.py` / `audit_config.py` …） |

---

## 附录 A · 关键 file:line 证据索引

| 主题 | 位置 |
|---|---|
| DB 只存元数据与路径 | `docstore.py:12` |
| 存储目录树定义 | `docstore.py:1-14` |
| 三根兜底 + 静默读空 | `docstore.py:199-212` |
| `content_path` 三格式归一 | `docstore.py:179-187` |
| `book_kind` 缓存与 rowid 复用 BUG-16 | `docstore.py:38`, `docstore.py:99` |
| RAG 10~20MB JSON + mtime 缓存 | `docstore.py:248-293` |
| **三套类型词汇表** | `entities.py:17`, `models.py:267`, `graph.py:36` |
| 势力被降级为「其他」 | `entities.py:146`, `graph.py:76-79` |
| 势力分组标题（死代码） | `memory.py:1228-1240`, `schemas.py:273-293` |
| **五元组匹配 + 盲目解除** | `memory.py:2763-2817` |
| 图谱只读不写 | `graph.py:16`, `graph.py:39-47` |
| 图谱默认关闭 | `audit_config.json` (`graph_inject_enabled: false`), `memory.py:1247-1256` |
| 社区 = 连通分量 | `graph.py:209-227` |
| 关系冲突检测硬编码 15 对词 | `graph.py:270-272` |
| 实体生命周期前缀 | `entities.py:119-127`, `entities.py:148-151` |
| **损伤四字段定义** | `memory.py:1078-1083` |
| **恢复闸门（三重巧合）** | `memory.py:2114-2121` |
| 恢复语义关键词表 | `memory.py:2035-2039` |
| **永久化单向出口 + 痊愈/解除 语义重叠** | `memory.py:2108-2110`, `memory.py:2030-2031`, `memory.py:2127-2133` |
| 痊愈/解除移出活跃列表 | `memory.py:2132-2133` |
| 阈值表 6×5 | `memory.py:853-859` |
| 弹性因子 | `memory.py:938-962` |
| **since_ch 卷内号 vs 全局号 P0** | `memory.py:892-926`（注释 `895-902`） |
| severity 兜底 + 留痕 | `memory.py:860-884`（注释 `848-851`） |
| 催促注入 | `memory.py:1001-1007` |
| 配角伤不追踪 | `memory.py:988-989`, `memory.py:968-970` |
| Editor 伤势必改规则 | `editor.py:59-72`, `editor.py:307-308` |
| Writer 伤势约束 | `writer.py:159` |
| 抽取提示词 | `post_processor.py:27-42`, `post_processor.py:140-147` |
| **细纲 0 处伤势逻辑** | `architect.py` 中 `伤势|损伤|伤残` 仅 17 处「恢复」误命中 |
| Alembic baseline（仅 18 表） | `alembic/versions/5886c58b3729_baseline_all_tables.py:24-270` |
| **手写 ALTER 迁移** | `models.py:564-661` |
| 迁移半成品自述 | `models.py:613-614`, `models.py:566` |
| 关系泛化回填 | `models.py:650-661` |
| 废弃列自注 | `models.py:54`, `models.py:58` |
| 老字段双写 | `models.py:263-286`, `memory.py:2823-2824` |
| 删书清理清单 + 孤儿行教训 | `models.py:688-718` |
| 唯一 DAO | `models.py:664-665` |
| Phase 2 shadow 自述 | `models.py:127`, `models.py:160`, `models.py:238`, `models.py:289`, `models.py:331`, `models.py:127` |
| Phase 2 全 false 配置 | `revision_config.json`, `character_delta_config.json` |
| Phase 2 写路径调用点 | `pipeline.py:8018`, `pipeline.py:8071`, `pipeline.py:8105`, `pipeline.py:8151` |
| SQLite WAL/busy_timeout | `models.py:15-24` |
| 全局章号编码 | `models.py:668-681` |
| 全局 JSON 存储（无隔离） | `artifact_store.py:23`, `research_store.py:25-26`, `selection_store.py:22` |
| 非原子写 | `docstore.py:110`, `docstore.py:291`, `artifact_store.py:112`（对照 `audit_store.py:47-49` 是原子的） |

## 附录 B · 实测数据来源

所有「实测」数据来自对生产库 `E:\AI_project\story_llm\backend\learnstory.db`（8,581,120 bytes，WAL=0）
的只读 SQL 查询，以及 `backend/storage/` 与 `backend/*.json` 的文件枚举。
查询脚本仅执行 `SELECT` / `PRAGMA`，**未对 `story_llm` 做任何写入**。

关键实测数字：
- 27 张表 / 358 列；**8 张 0 行表**；`metrics` 4373 行（最大）
- **61 个具名索引，37 个（61%）位于 0 行表上**
- 遥测 4 表（`metrics`+`generation_attempts`+`generation_traces`+`generation_spans`）共 6219 行 = 全库 **54%**，且无 TTL
- 8 本书 / 1441 章 / 1147 章 `content_path` 为空（80%）
- `chapters.active_revision_id` NULL = 1441/1441
- `story_events.effect_key` NULL = 996/996；`is_active=1` = 996/996
- `relationships`：966 行，`type_b='势力'` 仅 7 行，`'组织'` 14 行，`'其他'` 11 行，`'地点'`/`'地名'` 并存
- `character_states`：77 行，36 条损伤，**0 条 `痊愈`/`解除`**，13 条旧字符串格式，13 条缺 `since_ch`
- 全库 **0 FOREIGN KEY**
- `docstore` 的 `read_chapter` 读不到文件时返回 `""`（`docstore.py:212`）
- Phase 2 六表 + 寄生列 = 91 列；废弃/双写列 = 10 列 → 合计 101 列（28%）可删
