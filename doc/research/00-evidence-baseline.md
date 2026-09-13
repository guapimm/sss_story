# 00 · 证据基线（Evidence Baseline）

> **用途**：本文件是重构计划的**事实锚点**。所有根因结论都必须能在此找到原始证据（SQL 查询结果 / 代码位置 / 实测数字）。
> **来源**：对 `E:\AI_project\story_llm`（**只读**）的实测勘察，2026-09-13 之后的最新生产库快照。
> **原则**：本文件只记录**实测得到的事实**，不记录推测。推测一律标注「假设」并给出验证方式。
> **为什么需要它**：本项目历史上出现过「结论与自己的数据矛盾」（见 `docs/AGENTS.md` 中 `KEEP_OFF_DEFAULT_SCOPE_NARROWED` 被推导结果推翻一事）。重构计划必须建立在可复查的证据上。

---

## 0. 项目规模基线

> ⚠️ **数字修正记录（重要）**：本节最初给出的「755 py / 312,628 行」是**错误的** —— 它把
> `backend/_pylibs/`（vendored 的 sqlalchemy / alembic / mako / greenlet，397 文件 / 288,238 行）算成了项目代码，
> 且 PowerShell `Measure-Object -Line` 会漏计空行。
> 下列数字为**逐文件按换行符重新统计的权威值**（排除 `_pylibs` / `__pycache__` / `.git` / `node_modules`）。

| 范围 | 文件数 | 行数 |
|---|---|---|
| **实际业务代码**（全仓 `.py`，排除 `_pylibs`） | **358** | **95,471** |
| `backend/app/`（核心业务） | 122 | 50,358 |
| `backend/_pylibs/`（**vendored 第三方库，非项目代码**） | 397 | 288,238 |
| 测试 `backend/test_*.py` | 123 | 25,885 |
| `backend/benchmark/` | 52 | 12,809 |
| 一次性调试脚本 `backend/_*.py` | 48 | 5,186 |
| 前端 `frontend/src` | 30 | 10,608 |

**结论不变、依据更精确**：这是一个**约 9.5 万行业务代码**的项目（其中核心 `app/` 5 万行），
`pipeline.py` 单文件 **8,607 行**、`main.py` **4,630 行**。
与 LangChain/LangGraph **零关系**（`requirements.txt` 全文无此依赖）。重构是真正的重写，不是迁移。

> **教训（对本项目的要求）**：任何规模数字必须**说明统计范围与工具**。
> `_pylibs/` 的存在本身就是一个运维问题（30.9 MB vendored 依赖入库），
> 新项目**不得**再出现 vendored 依赖目录。

### 关键模块行数分布（实测）

```
backend/app/main.py              4630    ← HTTP 端点
backend/app/pipeline.py          8607    ← 编排核心（状态机/连载/重写/取消/记忆提交全在此）
backend/app/memory.py            3203    ← 五层记忆 + 账本合并
backend/app/agents/architect.py  3038    ← 大纲/骨架/细纲
backend/app/prose_style.py        932
backend/app/writer_rules.py       653
backend/app/agents/post_processor.py 277  ← 但 system prompt 单条 >10,000 字符
```

`backend/app/` 目录结构（122 文件 / 50,358 行）：
```
agents/      architect.py 3038 | writer 519 | story_director 513 | schemas 415
             researcher 390 | editor 349 | post_processor 277 | base 186
             verifier 164 | bible_reviewer 162 | worldbuilder 154
             story_arc_planner 149 | registry 115 | era_checker 88 | runner 73
             audit 55 | gate 27
flowmode/    base 192 | story_flow 156 | learn_flow 39 | __init__ 17   ← 设计良好
llm/         client 1366 | mock_data 288
infra/       base 64 | factory 159 | memory 194 | milvus_impl 97
             redis_impl 127 | sqlite_queue 25     ← 抽象层，零生产使用
genres/      base 34 | manager 22 | registry 36 | service 67
search/      provider 47
templates/   scenes.py 33                        ← learn 只有一个 xianxia 模板
storage/     (目录存在)
flows/       (空)
```
根目录另有 ~70 个功能模块文件（`pipeline.py` / `main.py` / `memory.py` / `entities.py` /
`graph.py` / `novelty.py` / `prose_style.py` / `writer_rules.py` / …）

---

## 1. 生产库实测数据

数据库：`backend/learnstory.db`（SQLite，8.5 MB + WAL 4.5 MB）。
**查询方式**：只读 URI `file:...?mode=ro`，未修改任何数据。

### 1.1 表清单（27 张）

```
alembic_version          book_summaries           books
canon_claims             chapter_chunks           chapter_commit_manifests
chapter_revisions        chapters                 character_state_deltas
character_states         drift_log                foreshadow_actions
foreshadowings           generation_attempts      generation_spans
generation_traces        genre_states             job_queue
knowledge_ledger         metrics                  modules
notifications            plot_nodes               relationship_actions
relationships            research_cache           story_events
```

### 1.2 行数分布（关键：大量死表）

| 表 | 行数 | 判读 |
|---|---|---|
| chapters | 1,441 | 有效 |
| generation_attempts | 1,003 | 有效（trace） |
| story_events | 996 | 有效 |
| relationships | 966 | **有效但语义污染（见 §3）** |
| generation_traces | 745 | 有效 |
| canon_claims | 684 | 有效 |
| foreshadowings | 539 | 有效但回收率极低（见 §5） |
| notifications | 247 | — |
| drift_log | 102 | — |
| character_states | 77 | 有效 |
| modules | 60 | — |
| chapter_chunks | 49 | **严重偏少（RAG 索引）** |
| knowledge_ledger | 36 | learn 模式 |
| plot_nodes | 28 | 有效 |
| metrics | 4,373 | — |
| genre_states | 1 | — |
| **character_state_deltas** | **0** | **死表（Phase 2D3 shadow，从未启用）** |
| **chapter_revisions** | **0** | **死表（Phase 2 revision，从未启用）** |
| **chapter_commit_manifests** | **0** | **死表（Phase 2E recovery，从未启用）** |
| **relationship_actions** | **0** | **死表** |
| **foreshadow_actions** | **0** | **死表** |
| **book_summaries** | **0** | **死表** |
| **job_queue** | **0** | **死表（基础设施抽象未用）** |

> **重要判读**：`AGENTS.md` 用了极长篇幅描述 Phase 2（Revision / CommitRecovery / CharacterStateDelta / action provenance）机制，并配套 5 张表 + 多个配置文件 + 多个测试文件。**实测这些表全部 0 行** —— 这套机制从未在生产产生任何数据。它代表了**纯粹的架构负担**：文档、配置、测试、代码全部为 0 行数据服务。

### 1.3 书籍清单（8 本）

| id | kind | scene | scale | phase | 卷/总卷 | 备注 |
|---|---|---|---|---|---|---|
| 3 | learn | xianxia | fast | completed | 1/1 | Python 教学小说 |
| 5 | learn | xianxia | fast | completed | 1/1 | AI 大模型 |
| 7 | learn | xianxia | standard | completed | 1/1 | 二次函数 |
| 8 | story | ancient_romance | standard | interrupted | 1/1 | 286 章 |
| 11 | story | youth | ultra | paused | 2/4 | 卷1=6章、卷2=350章（**严重失衡**） |
| 18 | story | xianxia | ultra | interrupted | 1/4 | 350 章 |
| 23 | story | suspense | ultra | interrupted | 1/5 | 400 章（冻结对照样本） |
| 24 | story | xianxia | ultra | interrupted | 1/3 | 13 章（Rolling 首跑） |

### 1.4 章节按卷分布 —— **痛点 5 的核心证据**

```
book  3: vol1=8
book  5: vol1=8
book  7: vol1=20
book  8: vol1=286
book 11: vol1=6,   vol2=350
book 18: vol1=350
book 23: vol1=400          ← 400 章全部在第 1 卷
book 24: vol1=13
```

**书 23 大纲实测**（`books.outline` JSON）：
```
total_volumes: 5
chapters: list[400]        ← 400 章
卷分配: {1: 400}           ← 全部卷 1
segments: list[8]          ← 8 段 × 50 章
segment_plan_source: "model"
```

**书 23 的 5 卷规划**（大纲本身质量很高）：
| 卷 | 标题 | vol_goal | blueprint |
|---|---|---|---|
| 1 | 值夜手册 | 陈砚上手值夜，读透三十七条，查明上一任怎么没的 | (完整) |
| 2 | 三十七条之外 | 走出长明楼，接入城市节点网，建起属于自己的值夜人班底 | **missing** |
| 3 | 租界 | 踏入另一侧的城市，摸清制度全貌 | **missing** |
| 4 | 空页 | 吞掉七夜社，收拢所有值夜人，成为能与租界谈判的立约人 | **missing**（`main_line` 为空） |
| 5 | 最后一条 | 动第0条，拆掉值夜制度 | **missing**（`main_line` 为空） |

**书 24 大纲实测**：
```
chapters: list[447]
卷分配: {1: 447}           ← 全部卷 1
volumes[0].main_line: ""   ← 空
volumes[1].main_line: ""   ← 空
volumes[2].main_line: ""   ← 空
volumes[1..2].blueprint: missing
vol_end_hook 卷2: "推向「首次记忆剥夺」的下一个阶段"   ← 模板化填充，无实质内容
```

**机制（已定位到代码与配置）**：
- `backend/scales.json` → `story.ultra.rule`：**「本次只细化第 1 卷」**；`per_vol_lo/hi = 300/500`。
- `agents/architect.py` `_normalize_blueprint_volumes()`：后续卷缺失时**补占位卷并标 `blueprint="missing"`**（L642-676）。
- `agents/architect.py` `_lock_volumes()`：`total_volumes` 取 max 值，**禁止被 1 覆盖**（L687-732）。

**因为「只细化第 1 卷」+「每卷 300~500 章」组合，模型把 400 章全部塞进第 1 卷**，而第 1 卷的 `vol_goal` 是「在长明楼读透三十七条」—— 于是一本规划了 5 卷升级（楼→城市网→租界→立约人→拆制度）的小说，**前 400 章全部发生在一栋楼里**。后续 4 卷只有一句占位目标，从未被真正规划。

### 1.5 地点碎片化 —— 「一个地方全程展开」的直接证据

书 23 的 `story_events.location` 分组统计（前若干项）：
```
 77  (空字符串！)
 24  长明楼三层值班室
 11  长明楼二楼值班室
  8  长明楼负一层配电间
  7  长明楼负一层册房
  4  旧档案馆五层食堂
  3  长明楼负一层泵房
  3  长明楼二楼值班室门口       ← 「门口」= 新地点
  3  长明楼三层走廊
  3  档案馆值夜室
  2  长明楼负一层走廊
  2  长明楼值班室              ← 缺楼层 = 新地点
  2  长明楼五层507
  2  长明楼三层值班室门外        ← 「门外」= 又一新地点
  ...
  1  长明楼三楼档案科资料室      ← 与「三层值班室」实为同层不同写法
```

**同一栋楼被拆成 40+ 个「地点字符串」**，且 **77 次 location 为空**。这是"地点字符串"而非"地点实体"的必然结果：没有实体归一，就没有地理结构，也就无法在规划层约束"这一卷要去哪些地方"。

### 1.6 `participants` 为空 —— 实体追踪断裂证据

```
story_events 书23:
  ch1 @档案馆外门与值夜室 [[]]     ← 空数组
  ch1 @值夜室 [[]]                ← 空数组
  ...
  ch2 @档案馆值夜室 [["陈砚"]]     ← 有值
```

第 1 章所有事件的 `participants` 为**空数组**，尽管 `description` 里明确写了"郑维向陈砚交班"。抽取器未填该字段，系统也未报警。

---

## 2. 关系表语义污染 —— 痛点 2、4 的核心证据

### 2.1 实测：关系表里大量条目不是「关系」

书 23 的 739 条关系样本（`relationships` 表，`book_id=23`）：
```
陈砚   <-值守->      档案馆            [解除/明]
郑维   <-归属->      档案馆            [维持/明]
值夜手册 <-存放->     值夜室            [维持/明]
黄铜条 <-嵌设->      值夜室            [维持/明]
值夜手册第九条 <-冲突-> 值夜手册第十四条  [变化/明]
第三十八条 <-作保->   第三十七条         [解除/明]
陆知白 <-否定->      第三十七条         [维持/暗]
老周   <-探视者与被探视者-> 周桂英       [变化/明]
郑维   <-遗留转赠->   录音笔            [维持/暗]
陈砚   <-持有->      录音笔            [维持/明]
值夜人名单 <-张挂->   档案馆            [新增/明]
牛皮纸信封 <-藏匿->   值夜室            [新增/明]
值夜牌 <-门禁通行绑定-> 值夜室           [维持/明]
深蓝色硬皮本 <-藏匿地-> 长明楼值班室      [解除/明]
```

全局 `relation_type` 分布（跨书）：
```
同门 25 | 师徒 24 | 盟友 21 | 上下级 13 | 敌对 11 | 亲属 11 | 血亲 9
查看 7 | 记录 5 | 离开 4 | 拾取物品 4 | 值班 7 | 归属 7 | 探视 5 ...
```

**「查看」「记录」「离开」「拾取物品」「值班」不是人际关系，是动作**。关系表被当成了事件流水账。

### 2.2 实测：人物-人物关系占比仅 13.3%

书 23 的端点类型配对统计：
```
(人物, 物品) 256
(人物, 地名) 127
(物品, 地名) 101
(人物, 人物)  98    ← 仅 13.3%
(物品, 物品)  32
(人物, 规则)  28
(地名, 地名)  18
(人物, 术语)  14
(地名, 组织)  10
(人物, 组织)   8
```
`type_a` 分布：`人物 539 | 物品 142 | 地名 29 | 规则 10 | 术语 9 | 其他 9 | 组织 1`

**「人物关系单一」（痛点 4）的直接解释**：关系表 86.7% 的容量被物品/地点/规则占用，真正的人际关系只占 13.3%。

### 2.3 实测：势力从未成为一等实体

查询书 23 中 `type_a='势力'` 或 `type_b='势力'` 的实体：**0 条结果**。
`组织` 类型仅：`(地名, 组织) 10` + `(人物, 组织) 8` = 18 条。

**「势力归属也很乱」（痛点 2）的直接解释**：势力在数据模型里**没有实体身份**，只以字符串形式出现在关系的 `entity_b` 里，且类型标记混乱（`组织`/`其他` 混用）。没有势力实体，就没有势力成员表、没有势力间关系、没有势力强弱演化。

### 2.4 根因定位：prompt 强制「全量重报」

`backend/app/agents/post_processor.py`（system prompt，L74-81）：

```
出场实体全量重报（防关系图空洞→下章无根出场）：
- 本章实际出场/被提及的每个角色、势力、地点、物品，都必须出现在
  relationship_updates 或 state_updates 中；
- 已存在的关系本轮未变化也要显式输出一条 status="维持"（仅确认不重复建边）；
```

配合 17 类端点组合 + 「可耗品份额」+ 「生命周期前缀」等指令，抽取器被迫把**每一次物品接触、每一次地点移动**都写成一条"关系"。这是**设计指令直接导致的数据污染**，不是模型能力问题。

### 2.5 实测：人物状态字段使用情况（书 23，18 个角色）

```
目标 15 | 位置 13 | 情绪 12 | 装备 10 | 境界 7 | 身份 6
立场 5 | 外貌 5 | 出身 5 | 天赋 5 | 性格 5 | 口癖 5 | 癖好 5
欲望 5 | 已学功法 5 | 机缘 5 | 装备来源 5 | 伤势 2 | 诅咒 2
际遇 1 | 生死 1
```

**400 章的小说，只有 18 个角色被追踪，其中只有 5 个有完整状态字段**。配角从未进入状态管理。

---

## 3. 伤势不恢复 —— 痛点 1 的完整根因链

### 3.1 机制全貌（`backend/app/memory.py`）

损伤模型：`{name, severity, status, since_ch, recov, permanent}`
- `severity ∈ {轻微, 轻, 中, 重, 极重}`（**注意 `post_processor.py` 的 prompt 只允许「轻|重|极重」三档** —— 与代码的四/五档不一致，见 §3.4）
- `status ∈ {新发, 恶化, 维持, 好转, 缓解, 痊愈, 解除, 永久·可缓解, 绑定}`

**弹性恢复阈值表**（`memory.py`，`_IMPACT_BASE_TH`）：
```python
("伤势", "轻微"): 1, ("伤势", "轻"): 1, ("伤势", "中"): 2, ("伤势", "重"): 3, ("伤势", "极重"): 5,
("后遗症", "轻微"): 3, ("后遗症", "轻"): 4, ("后遗症", "中"): 6, ("后遗症", "重"): 8, ("后遗症", "极重"): 12,
("心魔", "轻微"): 3, ("心魔", "轻"): 4, ("心魔", "中"): 6, ("心魔", "重"): 8, ("心魔", "极重"): 12,
("代价", ""): 12,
("诅咒", ""): 999,   # 由解咒伏笔驱动，不按章催
```

阈值修正因子（`_impact_threshold`，L938-962）：
```python
f = 1.0
if ctx.get("is_core"): f *= 1.2                    # 主角/核心角色给更长时间
has_heal = any(药/丹/散/膏/参/灵芝/露/草/玉露/圣水/灵液 in 装备)
f *= 0.7 if has_heal else 1.3                      # 有治疗资源提前，无则延后
if vol_total and ci/vol_total >= 0.8: f *= 0.6     # 卷末最后 20% 收紧
return max(1, int(base * max(0.6, min(1.4, f))))
```

注入逻辑（`_fmt_impacts`，L965-1012）：
```python
if el >= th:     warn = " ⚠️已超恢复期，本轮必须推进（恢复剧情或明示永久化）"
elif el >= th*0.8: warn = " ⚠️接近恢复期，建议本轮推进"
```
尾部提示：`（慢性损伤维持中：本章若无剧情需要可一笔带过，禁止每章刻意复述损伤细节；恢复须有治疗/剧情事件）`

### 3.2 根因一：恢复靠「每章 prompt 催促」，而非「大纲规划」

整个恢复机制**只在 writer prompt 里加一句警告**。没有任何机制：
- 在**大纲层**为伤势安排恢复节拍（哪一章、用什么方式痊愈）；
- 在**细纲层**把"疗伤"写成一个场景（带目标/阻碍/结果）；
- 校验"本章是否真的推进了伤势"（Editor 只是**读到**警告后批评，不触发修复）。

`post_processor.py` 的抽取规则（L42）：
```
· 好转/痊愈/解除/缓解必须能在 events 找到该角色的治疗/恢复/解咒/补偿事件，
  否则不得报恢复类状态；
```
**这是一条死锁**：抽取器**不允许**在没有治疗事件时报恢复 → 但**没有任何机制去生成治疗事件** → 伤势永久停留在「维持」，同时每章被标注「本轮必须推进」。Writer 看到"必须推进"，但没有细纲场景支持，只能一笔带过或忽略 → Editor 判"未推进" → 低分。

这正是项目自己记录的 P0 问题（`docs/TODO_20260913.md` §7.1）：
```
1-9  5/10 「多项硬性违规：楚渊多项标注『本轮必须推进』的损伤（左肩剑创、后腰擦伤、内腑…）未推进」
1-10 4/10 「严重AI味…及关键设定矛盾（人物状态表多项标注『本轮必须推进』的伤势…）」
```

### 3.3 根因二（已修复但值得记录）：章号口径混用

`memory.py` `_impact_since_global`（L892-926）的 docstring 记录了实测事故：
```
书 24 实测 P0：since_ch 长期被写入侧存成卷内号（_merge_impacts 收的是 ch_idx），
而消费侧 ctx["cur_ch"] 是全局号 → 已持续 = 10012 - 1 = 10011。
后果不只是显示错：同一个 elapsed 还被拿去比恢复阈值（阈值仅 1~3），
于是每章恒真 → 每个损伤每章都挂「本轮必须推进」，
一章推进不完 7 处 → Editor 每章判「必推进的损伤未推进」→ 4~5 分 + 必改失败。
```

**这个 bug 的教训对重构极其重要**：章号必须**只有一种表示法**。全局号 + 卷内号并存（`vol*10000+idx`）是这类 bug 的温床。

### 3.4 根因三：severity 档位三方不一致

| 位置 | 允许的档位 |
|---|---|
| `memory.py` 阈值表 | 轻微 / 轻 / 中 / 重 / 极重 |
| `post_processor.py` prompt（learn 版 L40） | 轻 / 重 / 极重 |
| `post_processor.py` prompt（story 版 L141） | 轻 / 重 / 极重 |
| `_IMPACT_SEVERITY_FALLBACK` | "重" |

代码有 5 档，prompt 只让模型出 3 档 → **「轻微」「中」永远不会被产生**，而阈值表里这两档（1 章、2 章）是最快催恢复的档位。同时未登记 severity 会走 fallback 并 `log_problem`（`_warn_unknown_severity`）。

### 3.5 实测现有伤势数据（19 个角色有伤势记录）

样本显示伤势**确实长期停留**，且 `since_ch` 与 `updated_ch` 不一致：
```
book3  角色A upd_ch=8:  ["……只能恢复本源，保持状态"]
book8  角色B upd_ch=160: ["……加剧（活动、湿气、……）需服药调养"]
```

---

## 4. Token / 缓存 / 空输出 —— 痛点 7 的量化基线

数据来源：`generation_attempts` 表（1,003 条真实调用记录）。
字段：`prompt_tokens / completion_tokens / reasoning_tokens / cached_tokens / total_tokens / usage_source / usage_complete`。

### 4.1 总体 token 与缓存命中

```
prompt_tokens      = 9,151,550
cached_tokens      = 4,023,724   → 缓存命中率 44.0%
completion_tokens  = 8,970,128
reasoning_tokens   = 6,876,206   → 占 completion 的 76.7%  ★
```

**reasoning 占输出的 76.7%** —— 绝大部分生成预算花在"思考"而非"产出"上。

### 4.2 按 agent 拆分（书 24 的 Rolling run，均值）

| agent | n | 平均输入字符 | 平均 prompt_tokens | 平均 cached | 平均 reasoning | 平均 completion | 平均耗时 ms |
|---|---|---|---|---|---|---|---|
| memory | 84 | 32,220 | 22,659 | 2,494 | 6,931 | 10,146 | 61,475 |
| writer | 132 | 27,139 | 19,725 | 10,231 | 4,376 | 6,853 | 54,039 |
| entity_classify | 4 | 480 | 17,225 | 2,720 | 5,789 | 9,110 | 64,554 |
| editor | 124 | 19,034 | 13,082 | 7,130 | 12,887 | 15,773 | 117,388 |
| verifier | 47 | 10,108 | 7,414 | 2,608 | 13,097 | 13,175 | 67,991 |
| architect | 464 | 8,725 | 5,493 | 3,380 | 7,151 | 9,363 | 71,789 |
| bible_reviewer | 7 | 7,749 | 5,313 | 2,325 | 14,117 | 17,617 | 101,048 |
| worldbuilder | 6 | 2,799 | 1,883 | 32 | 4,867 | 8,182 | 66,671 |
| base | 29 | 344 | 260 | 0 | 2,878 | 2,926 | 20,517 |

**缓存命中率按 agent**（跨全部数据）：
```
architect        60.3%
writer           49.1%
editor           53.2%
bible_reviewer   37.5%
verifier         32.2%
entity_classify  15.8%
memory           10.9%   ★ 最低（输入最大 32K 字符，缓存命中却最低）
worldbuilder      1.1%
```

**`memory` agent 是最大的优化机会**：平均输入 32,220 字符（最大），缓存命中仅 10.9%（最低）。它的 prompt 是「本章正文 + 账本快照」，账本快照在章间高度重复，但因为**拼在正文之后/混排**，无法命中前缀缓存。

### 4.3 reasoning 占 completion 比例（按 agent）

```
verifier         99.4%   ★
base             95.6%
editor           71.6%
bible_reviewer   69.1%
architect        65.8%
memory           61.7%
entity_classify  60.6%
writer           55.4%
worldbuilder     53.0%
```

`verifier`（62.3% 空输出）和 `editor`（20.3% 空输出）都把绝大部分预算烧在思考上。

### 4.4 空输出率（`status='blank'`）—— 严重系统性缺陷

```
agent              n     blank    blank率
entity_classify    32     32     100.0%   ★ 完全失效
verifier           61     38      62.3%   ★
bible_reviewer      7      2      28.6%
editor            128     26      20.3%
architect         488     89      18.2%
writer            157     27      17.2%
base               36      6      16.7%
memory             88      3       3.4%
worldbuilder        6      0       0.0%
------------------------------------------------
合计             1003    223      22.2%
```

**总体 22.2% 的调用返回空**。`entity_classify` **100% 失效（32/32）**，`verifier` 62.3% 失效。

### 4.5 按模型拆分

| model | n | blank | blank率 | 缓存命中 |
|---|---|---|---|---|
| deepseek-v4.1-flash | 614 | 159 | 25.9% | 43.1% |
| mimo-v2.5 | 253 | 0 | **0.0%** | 45.6% |
| deepseek-flash | 69 | 10 | 14.5% | 53.6% |
| qwen3.8-flash | 58 | 53 | **91.4%** | 12.3% |
| omen-alpha | 8 | 1 | 12.5% | 70.5% |

**同一套 prompt，mimo-v2.5 空输出 0%，qwen3.8-flash 91.4%** —— 空输出高度依赖模型，说明当前 client 的"思考预算/输出预算"分配策略对不同模型**不具鲁棒性**。

### 4.6 失败原因分布

```
retry_reason: "Request timed out." 46 次 | "Connection error." 1 次
status: ok 772 | blank 223 | failed 8
finish_reason: 全部 1003 条为空（未采集！）
```

**`finish_reason` 100% 未采集** —— 无法区分"模型主动停"和"被 max_tokens 截断"。这是空输出诊断的最大障碍。

### 4.7 相关代码机制（`backend/app/llm/client.py`）

**思考预算与输出预算共用上限**（来自 `AGENTS.md` 与 `client.py`）：
```python
# client.py 对显式思考控制的调用设 max_tokens = max(4096, reasoning*4)
# 思考与正文共用这个上限 → 可用 JSON/正文预算 = 4x - x = 3x
```

**微产出判废**（`client.py` L82-92, L403-410）：
```python
if text and len(text.strip()) < 20 and getattr(self, "_last_reasoning", 0) > 3000:
    _note_micro_output_discard(len(text.strip()), getattr(self, "_last_reasoning", 0))
    last_err = RuntimeError(f"微产出判废（正文{len(text.strip())}字/思考{...}字）")
    text = ""
```
即：思考烧了 3000+ 字却只吐 <20 字 → 判废重试。**这是空输出的直接机制之一**：思考吃光预算，正文没空间。

**思考失控保护**（`AGENTS.md` 规约 19）：
> `client.py` 只在 `reasoning_effort` 非空时才给 `max_tokens` 留 4x 正文空间。不登记 = 该保护不生效。

### 4.8 prompt 结构对缓存的影响（已定位）

`writer.py` `_build_prompt` 的注入顺序（L409-566）：
```
【L0 世界观圣经】                    ← 稳定（全书不变）★ 应放最前
【世界观提醒】(flow)
【防雷同提醒】
【文风范本】(flow/tpl)
【术语映射规则】(tpl)
【本卷开篇允许换场景】/【现场必须承接】 ← 每章变化
【L2 前情时间线】                    ← 每章变化
【L5 上一章结尾原文】                 ← 每章变化
【L3 人物状态表】                    ← 每章变化
【图谱关系快照】                     ← 每章变化
【已注册名词表】                     ← 每章变化
【L3 关键事件记忆】                  ← 每章变化
【L4 相关前文片段】                  ← 每章变化
【骨架指引】
【伏笔任务-埋下/回收】                ← 每章变化
【复习候选】
【本章文风指令】(固定种子)
【beat 指令】
【终章指令】
【本章信息】(标题/梗概/细纲/钩子/前后锚点)  ← 每章变化
【字数目标】(尾部)
```
**system 部分**：`_WRITE_BASE`（2,315 字符）+ `writer_rules.anti_ai_block` + learn/story 差异 + 长度指令。
**问题**：稳定前缀（圣经、规则）与易变内容（时间线、状态表、前文）**交错混排**，且 `_WRITE_BASE` 里 `.format()` 注入了每章变化的 `MIN_CHARS/SOFT_MAX/CLIMAX_MAX` → **system prompt 逐章变化，破坏前缀缓存**。

---

## 5. 伏笔回收率 —— 「防烂尾」机制实测失效

| book | 总数 | 已回收 | 回收率 |
|---|---|---|---|
| 3 | 5 | 5 | 100% |
| 5 | 2 | 2 | 100% |
| 7 | 7 | 7 | 100% |
| **8** | **389** | **24** | **6%** |
| 11 | 27 | 5 | 19% |
| 18 | 39 | 4 | 10% |
| **23** | **70** | **16** | **23%** |

短篇（3/5/7，8~20 章）回收率 100%；**长篇（286~400 章）回收率 6%~23%**。

书 23 大纲里有 `foreshadowings: list[60]`，但库中 70 条、只回收 16 条。`ultra_structure_config.json` 要求"每卷新埋 ≥5 条、贯穿型至少 1 条指向终局"，但**没有机制强制回收**。

`plant_ch`/`resolve_ch` 使用**全局章号**（`vol*10000+idx`），已知存在跨卷解码 bug 历史（`OPTIMIZATION_POINTS.md` #6，已修）。

---

## 6. 文风控制实测状态 —— 痛点 3

### 6.1 `prose_style_config.json`（生产实时值）

```json
{
 "enabled": true,
 "shadow": true,          ★ 只观测，不阻断
 "duplicate_threshold": 0.72,
 "duplicate_min_chars": 40,
 "dialogue_floor": 0.12,   ★ 对话占比下限仅 12%
 "ngram": 4,
 "top_ngram_k": 3,
 "cross_window": 5,
 "experiment": "book24-lean-20260912"
}
```

**`shadow: true` = 文风控制模块只报告，不改变产物**。这是痛点 3「AI味重」未被拦截的直接原因。

`dialogue_floor = 0.12` 意味着**对话占比低到 12% 就算合格**。而 Editor 实测反复批评"对话占比极低"。

### 6.2 `writer_rules_config.json`

```json
{ "rule_profile": "lean" }
```
`writer_rules.py` 有 `legacy` / `lean` 两档。**lean 档是"反 AI 味"改造档**（`docs/TODO_20260913.md` 提到 commit `cfa0a6d`）。但 `writer_rules.py` 全文 23,611 字符，其中三引号 prompt 文本 9,262 字符。

### 6.3 prompt 体量实测

| 常量 | 字符数 |
|---|---|
| `writer._WRITE_BASE` | **2,315** |
| `writer.LEARN_SYSTEM_EXTRA` | 412 |
| `post_processor.ChapterPostProcessor.system` | **>10,000**（单条 system，11 类抽取任务） |
| `post_processor.STORY_SYSTEM` | ~4,000 |
| `writer_rules.py` 全部 prompt 文本 | 9,262 |
| `writer_rules.py` 文件总量 | 23,611 |

全仓三引号字符串（≈prompt 文本）体量 Top：
```
pipeline.py          25,032
agents/architect.py  20,849
main.py              14,121
agents/post_processor.py 10,391
detail_review.py      9,578
writer_rules.py       9,262
evaluation_store.py   7,743
prose_style.py        6,886
continue_story.py     6,656
agents/editor.py      6,397
memory.py             5,341
```
（仅列 >1,500 字符者，合计 172,822 字符）

### 6.4 文风相关模块

`prose_style.py` 722 行 | `style_corpus.py` 766 行 | `style_metrics.py` | `novelty.py` 602 行 | `writer_rules.py` 557 行 | `review_triage.py` 430 行 | `detail_review.py` 949 行

---

## 7. 审校链实测状态

### 7.1 `review_config.json`（生产实时值）

```json
{
  "global": {
    "sample_rate": 1.0,
    "head_tail_by_scale": {
      "fast":     [1, 2],
      "standard": [5, 7],
      "grand":    [10, 10],
      "ultra":    [20, 15]
    }
  },
  "per_book": {}
}
```

**standard 档只审头 5 章 + 尾 7 章**。100 章的书 → 12 章被审，**88 章完全不审**。
ultra 档头 20 尾 15 → 400 章的书 → 35 章被审，**365 章不审**。

### 7.2 阈值死区（已记录在 `AGENTS.md`）

```
_must_fix 阈值 = score < 7（闸门认为必须修）
定向重写触发 = score < 6
→ score == 6 是死区：闸门要求修，但没有修订稿，本章按原稿入库
```
已加 `review_json.must_fix_unmet` 使其可见（不改变阈值）。

### 7.3 审校空转证据（`docs/TODO_20260913.md` §7.1）

```
[editor] low_score_no_improve: 第 1-10 章「低分 4 分重写未提分」
[editor_review_escalation] hard_rewrite_not_adopted:
    第 1-6 章  no_revised_content
    第 1-11 章 too_short:3839<4600
    第 1-12 章 no_improve:6->6
[editor_hard_unmet] hard_issues_unfixed: 第 1-6 章「1 项硬伤未落实（原稿入库）」
[editor_issue_contract] issues_empty_but_notes_actionable: 第 1-13 章
    「Editor 返回空 issues，但 notes 含可执行结论（硬伤,矛盾），已派生 2 条」
```

**Editor 反复要求修改但从不产出可用的 `revised_content`** → 重写环节空转 → 原稿入库。

### 7.4 实测 Editor 分数分布

```
b3: 6.5 0.0 6.5 8.0 8.0 8.0 6.0 8.0
b5: 5.0 6.0 0.0 5.0 5.0 8.0 8.0 5.0
b7: 6.5 6.0 6.0 8.0 7.0 4.0 8.0 6.0 8.0 5.0 8.0 6.5 7.0 6.5 6.0 6.0 6.0 8.0 6.5 6.0
b8: 8.0 6.5 5.0 6.0 ...
```
**大量 0.0 分**（b3 1-2、b5 1-3）—— 0 分意味着审校本身失败或未运行，而不是"质量极差"。8.0 分也是**最高分封顶**，说明评分尺度在 4~8 之间粗糙量化。

---

## 8. 章节正文质量证据

### 8.1 `content_len` 与 `content` 字段

书 23 的 47 章 `status='done'`，但 **`content_len` 全部为 0，`content` 字段为空** —— 正文存储在**文件系统**（`content_path`），不在 DB。

**这印证了存储双轨**：DB 存元数据 + 账本，文件系统存正文（`docstore`）。两侧一致性靠代码保证，无事务。

### 8.2 书 23 章节标题（反映结构质量）

```
ch1  交接只留一句「三十七条背下来」；零点敲门，第二声喊他名字用的是老周的嗓子
ch2  他不出声，用门禁与监控时间戳做对照，靠互斥的第9条与第14条把走廊灯逼回原位
ch3  最后一页写着第37条，纸面自行多出一行第38条；页边浮出「别信第37条」，落款陆知白
...
ch21 507的琴声总在第37小节错一个音，循环到天亮
ch26 废弃二食堂摆着七副碗筷，每晚都有人吃饭的声音
ch40 值夜牌到了顶层不认——第三十八条空白页还没有显形
```

**标题质量很高**（信息密度大、具体、有悬念）。说明**大纲/细纲层能力是够的** —— 问题在规划范围（400 章挤一卷）与正文执行，不在创意。

### 8.3 书 23 段落目标（`segments`，8 段 × 50 章）

```
1. 午夜交接      落地黄金三章…陈砚用门禁与监控做对照实验…（m1）
2. 前二十条      逐条读透前二十条，替301老太太倒完二十年份的报纸…（发现-完成一个住户就掉一条规则）
3. 页边的字      与陆知白的批注一来一往…（m2）
4. 朱批第一笔    阮小满追着住户上了十三层被困；断电之夜…（第一次决定谁被留下）
5. 回信与代价    陆知白在批注里写下朱批用法与三条禁忌…（m3）
6. 投放          七夜社往楼里投放人工规则，手册四天内从三十七条涨到四十一条…
7. 十三层        断电失控里他登上十三层…长明楼不是值夜点，是租界租出去的一间房（m4）
8. 压回空白      他把第38条重新压回空白，代价是母亲彻底忘了他…卷末
```

**这 8 段的规划质量很高**，且**明确包含世界观升级**（段 7 揭示"长明楼是租界租出去的一间房"）。问题在于：**这 8 段全部发生在第 1 卷内、同一栋楼里**。升级被写成了"认知升级"，而非"地理/势力扩张"。

`plot_nodes`（里程碑，仅 4 个）：
```
{"node": "第38条空白行与页边批注",        "chapter": 3,   ...}
{"node": "批注回应成真，第一次朱批",       "chapter": 205, ...}
{"node": "登十三层·房客守则",             "chapter": 318, ...}
{"node": "第38条重新变空",               "chapter": 400, ...}
```
**400 章只有 4 个里程碑**，且 3→205 之间有 **202 章的里程碑真空**。`ultra_structure_config.json` 要求 `min_milestones: 9`，但该配置 `enabled: false, shadow: true` —— **校验器未启用**。

---

## 9. 结构校验器未启用

`backend/ultra_structure_config.json`（生产实时值）：
```json
{
  "enabled": false,     ★ 未启用
  "shadow": true,       ★ 只观测
  "repair_attempts": 5,
  "min_arcs": 6, "max_arcs": 10,
  "min_core_arcs": 2, "min_relationship_arcs": 2, "min_opposition_arcs": 2,
  "min_beats_per_arc": 8, "max_beats_per_arc": 20,
  "min_arc_capacity": 300,
  "min_milestones": 9,
  "min_factions": 4,
  "min_characters": 3,
  "min_volumes": 3
}
```

**校验器设计得很好**（要求 ≥6 条故事线、≥4 个势力、≥9 里程碑、≥2 关系线、≥2 对立线），**但没有启用**。书 23 实测只有 4 个里程碑、0 个势力实体 —— 若该闸门开启，这本书**根本不会被允许开始写作**。

`docs/AGENTS.md` 记录了修复策略：
> **修复必须走增量补缺，禁止回退成「全量重写整份故事 JSON」** —— 大输出必被模型截断（book23/25 两次 ROLLED_BACK 的根因）。补丁按 error_code 分类：`arcs`/`beats`/`capacity`/`milestones`/`factions`/`characters`/`volumes`，各自小 prompt。

---

## 10. 其他配置实测

### 10.1 `outline_config.json`
```json
{ "fill_plots_batch_size": 3, "plot_detail_chars": 2000, "plot_detail_min_chars": 900 }
```

### 10.2 `trace_config.json`
```json
{ "enabled": true, "persist_attempts": true, "persist_spans": true, "capture_prompt_hash": true }
```

### 10.3 `chapter_limits.json`
```json
fast:     min 3000 / hard_min 2500 / soft_max 6000  / climax_max 7200  / max_cont 3
standard: min 3500 / hard_min 3000 / soft_max 6000  / climax_max 7000  / max_cont 2
grand:    min 3500 / hard_min 3000 / soft_max 6000  / climax_max 10000 / max_cont 2
ultra:    min 4600 / hard_min 4000 / soft_max 8000  / climax_max 15000 / max_cont 2
          soft_floor_chars 3200 / allow_short_chapter true
```

### 10.4 `scales.json` 关键差异（story）
```
fast:     3~10 章    / 1 卷     / segment 0
standard: 10~100 章  / 1 卷     / segment 0
grand:    100~300 章 / 1 卷     / segment 30
ultra:    1000~1700 章 / 3~6 卷 / segment 50
          per_vol: 300~500 章 / typical 400
          "本次只细化第 1 卷"
          expandable: false / ending: closed
```

### 10.5 `.env` 变量名（仅名称，值已脱敏）
```
LLM_PROVIDER, ZEN_BASE_URL, ZEN_API_KEY
EMBEDDING_MODEL, EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODE
RAG_WINDOW_CAP, RAG_KEY_BONUS, RAG_TIME_DECAY, RAG_SAME_VOL_CAP, RAG_MAX_CHARS,
RAG_KEY_POOL, RAG_RERANK_MODE, RAG_RERANK_MODEL
TAVILY_API_KEY, SEARCH_MAX_RESULTS, RESEARCH_CACHE_TTL_DAYS
PARALLEL_WORKERS, REVIEW_SAMPLE_RATE, LLM_TIMEOUT
DATABASE_URL, HOST, PORT, MAX_TOTAL_WORKERS, ARCHITECT_PARALLEL, RESEARCH_WORKERS
```

### 10.6 backend 根目录 JSON 配置（~25 个）
```
artifact_dbg.json, artifact_test.json, audit_config.json, chapter_limits.json,
character_delta_config.json, commit_recovery_config.json, editor_cost_config.json,
free_pool.json (★ 含明文 API key，全仓零读者，待删), guard_config.json,
infrastructure.json, infrastructure.local.json, llm_providers.json (含密钥),
long_memory_meta_guard_config.json, long_memory_prompt_config.json,
long_memory_routing_config.json, model_settings.json, offpeak_config.json,
omen_harvest_config.json, outline_config.json, prose_style_config.json,
review_config.json, review_escalation_config.json, revision_config.json,
rolling_story_config.json, trace_config.json, ultra_structure_config.json,
writer_rules_config.json
```
**另有大量大体积缓存/数据文件**：`artifact_cache.json` 8.1MB、`research_cache.json` 5.7MB、`verify_cache.json` 195KB、`selection_cache.json` 9.3KB、`story_random_pool.json` 13KB、`llm_providers.json` 9.7KB + 6 个 `.bak` 变体。
**以及根目录散落的 100+ 个 `_dbg_*` / `_diag_*` / `_tmp_*` / `_mutate_*` / `_probe_*` / `_watch_*` 脚本**（未清理的开发残留）。

---

## 11. 已验证的架构优点（应当保留的设计）

重构不是全盘否定。以下是实测确认设计良好的部分：

| 设计 | 位置 | 为什么好 |
|---|---|---|
| **FlowMode 模式门面** | `flowmode/base.py` + `story_flow.py` | learn/story 差异**外置到门面**，共享代码不含 `is_story` 分支（`AGENTS.md` 规约 15 明确要求）。接口清晰、可扩展新题材。 |
| **状态机断点续跑** | `_outline_partial.json` 三级检查点（段/批/章） | 崩溃不重跑大纲；`recover_orphans` 收拾残局。设计正确。 |
| **串行入账 + 并行草稿** | `_spawn_once` / `_running_lock` | "生成并发 = 串行语义" 是正确的不变量。 |
| **失败不静默** | `safe_guard.log_problem` → `problems.jsonl` | 规约 7。 |
| **确定性校验优先** | `outline_lint.py`（零 LLM） | 细纲 lint：长度/场景行数/格式/exit_state→entry_state 连续性/标题近重复。**方向完全正确**。 |
| **结构校验器设计** | `ultra_structure.py` 的 12 项指标 | 指标选得对（线数/核心线/关系线/对立线/beat 数/容量/里程碑/势力/角色/卷数），只是没启用。 |
| **生命周期状态前缀** | `[毁·可修]` / `[失·可寻]` / `[耗·一次性]` / `[衰]` / `[封]` / `[转]` / `[灭]` | 用名字前缀表达实体状态，简单有效。 |
| **前后锚点机制** | `flowmode/base.py` `_detail_block` 的 `prev_exit_state` / `next_entry_state` | 让 writer "看到要去哪里"，解决章间跳变。思路正确。 |
| **细纲 lint 的缺省即不通过** | `outline_lint.py` | 无法评估时返回 `ok=False` + `not_covered`，不假装通过。 |
| **测试进程隔离扫描器** | `benchmark/sweep_all.py` | 识别出"单进程 pytest 因模块级 os.environ 赋值而产生假失败"，并给出正确解决方案。 |
| **Token 契约** | `generation_attempts` 表 + `UsageRecord` | `usage_source ∈ {native, partial, unavailable}`、缺失保持 NULL 不冒充 0、区分 Native Token 与 Proxy Char。**这个契约设计是正确的，应继承。** |

---

## 12. 证据索引（复查用）

### 关键代码位置
| 主题 | 文件 | 函数/行 |
|---|---|---|
| 五层记忆组装 | `app/memory.py` | `MemoryManager.build_context` (L1072+) |
| 损伤阈值 | `app/memory.py` | `_IMPACT_BASE_TH`, `_impact_threshold` (L938) |
| 损伤注入 | `app/memory.py` | `_fmt_impacts` (L965) |
| 章号归一 | `app/memory.py` | `_impact_since_global` (L892) |
| 人物行渲染 | `app/memory.py` | `_fmt_char_line` (L1015) |
| 骨架分段生成 | `app/agents/architect.py` | `_gen_segmented` |
| 段间交接 | `app/agents/architect.py` | `build_segment_handoff` (L1050) |
| 细纲批大小 | `app/agents/architect.py` | `fill_batch_size_for` (L1300) |
| 蓝图卷归一 | `app/agents/architect.py` | `_normalize_blueprint_volumes` (L642) |
| 卷锁定 | `app/agents/architect.py` | `_lock_volumes` (L687) |
| 分段目标合成 | `app/agents/architect.py` | `_synthesize_segments` (L808) |
| Writer prompt 组装 | `app/agents/writer.py` | `_build_prompt` (L409) |
| Writer system | `app/agents/writer.py` | `_WRITE_BASE` (L153), `write()` (L274) |
| 文风调味 | `app/agents/writer.py` | `_seasoning` (L222) |
| 记忆抽取 | `app/agents/post_processor.py` | `system` (L18), `run()` (L214) |
| 抽取 schema | `app/agents/schemas.py` | `PostProcessOut` (L420), `ChapterPlanOut` (L375) |
| 细纲注入块 | `app/flowmode/base.py` | `_detail_block` (L148) |
| 模式门面 | `app/flowmode/base.py` | `FlowMode` (L7) |
| 损伤/损伤阈值配置 | `app/memory.py` | `_IMPACT_SEVERITY_FALLBACK`, `_warn_unknown_severity` (L866) |
| 微产出判废 | `app/llm/client.py` | `_note_micro_output_discard` (L82) |
| 思考预算 | `app/agents/writer.py` | `compute_reasoning_budget` (L104) |
| 模型容量 | `app/llm_providers.py` | `get_model_capacity`, `model_concurrency` |

### 关键配置实测值（生产态）
```
writer_rules_config.json   rule_profile = "lean"
prose_style_config.json    enabled=true, shadow=true, dialogue_floor=0.12
review_config.json         standard 审 [5,7] / ultra 审 [20,15]
outline_config.json        plot_detail_chars=2000, plot_detail_min_chars=900
ultra_structure_config.json enabled=false, shadow=true, min_factions=4, min_milestones=9
trace_config.json          enabled=true
chapter_limits.json        ultra: min 4600 / soft_max 8000
scales.json                ultra: 1000~1700 章 / 每卷 300~500 / "只细化第 1 卷"
```

### 关键文档
```
AGENTS.md                        47KB  强制规约（北极星指标/分阶段上线协议/19 条工作规约）
docs/HANDOVER.md                 46KB  长期交接
docs/TODO_20260913.md            26KB  当日问题（含 §7.1 Editor 低分现场记录）
docs/OPTIMIZATION_POINTS.md      13KB  优化点清单（2026-09-11 复核口径）
docs/ARCHITECTURE_MAP.md         63KB  架构地图（自述 pipeline._commit_chapter 是 ~1240 行单函数）
docs/BUG_BACKLOG.md              50KB  跨日 backlog
docs/TEST_REPORT.md             123KB  测试流水账
docs/codebase_handover/          15 篇  完整交接文档集（00~14）
lang_tips/new_dp_plan/bibel.md   35KB  Rolling 三级流水线设计
```

### `docs/ARCHITECTURE_MAP.md` 自述的结构债（项目自己承认）
> `pipeline._commit_chapter` 是单函数 **~1240 行**、**11 个配置模块**重复 `_raw/reload/save`、**7 个模块缓存**导致「手改 JSON 需重启」（`AGENTS.md` 规约 11 的说法过强）——结构债

---

## 12bis. 数据层深度勘察补充（来自 `research/01-data-and-storage.md`，直接查询生产库）

> 本节是对 §1、§3 的加强证据，由独立子代理通读源码 + 直接查询生产库得到。已交叉验证。

### 12bis.1 数据库结构债量化

| 项 | 实测 |
|---|---|
| 表数 / 列数 | 27 张 / 358 列 |
| 空表 | 8 张（30%） |
| 索引总数 | 61 个，其中 **37 个（61%）建在 0 行表上** |
| 可删列 | **101 列（28%）** |
| 表可精简至 | 12~14 张 |
| 遥测 4 表行数 | 6,219 行 = 全库 **54%**，且**无 TTL**；`metrics` 与 `generation_attempts` 字段重叠（同一次调用记两遍） |

**Alembic 状态**：
- 只有 **1 个 baseline 迁移**（2026-08-31），且已过期
- baseline 只建 **18 张表**，生产有 **27 张** → **10 张表（37%）从未进入迁移**
- `alembic downgrade` 不可逆
- 真实迁移是 `models.py:564-661` 手写的 ~60 列 `ALTER TABLE` dict

### 12bis.2 零 DAO / 零外键

| 项 | 实测 |
|---|---|
| DAO 层 | **不存在**。`models.py:664-665` 是整个访问层（返回裸 `Session`） |
| 直连处 | **145 处** `db_session()` 散落在 `main.py`(55) / `pipeline.py`(28) 等 |
| FOREIGN KEY | **全库 0 个** |
| 删书清理 | 靠人工清单 `BOOK_SCOPED_MODEL_NAMES`（`models.py:696-718`）+ 反射测试兜底；注释自承**历史上漏过 6 张表**、生产已产生 trace 孤儿行 |

### 12bis.3 存储双轨已成实质数据风险

| 项 | 实测 |
|---|---|
| DB 存什么 | 只存路径（`docstore.py:12`） |
| `content_path` 为空 | **1,147 / 1,441 章 = 80%** |
| 路径格式 | **3 种互不兼容**：`story/books/`、`learn/books/`、`books/`，**甚至同一本书内混用** |
| 读失败行为 | `read_chapter` 读不到时**静默返回 `""`**（`docstore.py:212`）→ **正文丢失不报错** |

### 12bis.4 「势力归属乱」的精确机制：三套类型词汇表冲突

| 位置 | 词汇表 |
|---|---|
| `entities.py:17` | 有「组织」，**无「势力」** |
| `models.py:267` | 有「势力」 |
| `graph.py:36` | **两者都无** |

**失效链**：
```
LLM 按提示词吐出 type="势力"
  → entities.py:146 判定不在枚举内
  → 降级为「其他」
  → graph.py:76-79 再降一次
```

**实测后果**：`relationships` 966 行中 `type_b='势力'` 仅 **7 行**、`'组织'` **14 行**，其余散落「其他/术语/地点/地名」（且**「地点」与「地名」并存**）。`memory.py:1231-1240` 的 `【势力-势力】` 等分组标题是**死代码**。

**归属更新用「五元组匹配」且把 `type_b` 当匹配键**（`memory.py:2763-2779`）→ LLM 本次写「组织」下次写「势力」即匹配失败 → 旧边**不被解除**、新边**继续累积**。实测 `顾长生→执法殿` 同时存在「归属」与「逃避/即将对峙」两条并存。

**`memory.py:2802-2817` 盲目把「不同 relation_type 的旧边」全部置「解除」** → 「归属」vs「所属」（同义异写）互相删除。而 `relation_type` 是**自由文本**，实测 **40+ 种**（含 `归属/知情抽档` 这类斜杠复合）。

### 12bis.5 图谱「不靠谱」的原因

| 项 | 实测 |
|---|---|
| `graph.py:16` | 自述「只读不写」 |
| `graph_inject_enabled` | **`false`**（默认关，`memory.py:1247-1256`） |
| 社区检测 | 用 `connected_components`（`graph.py:209`）→ **在小说里全书塌缩成 1 个连通分量**，「社区」注入 = 全书实体列表，**零信息量** |

### 12bis.6 伤势：不是表，且恢复率实测 0%

| 项 | 实测 |
|---|---|
| 伤势存储 | **不是独立表**，是 `CharacterState.state` JSON 里的 4 个键（伤势/后遗症/代价/诅咒） |
| `character_states` 行数 | 77 |
| 损伤条目 | 36 条 |
| **`status` 曾出现「痊愈」「解除」** | **从未出现（恢复率 0%）** |
| 格式遗留 | 13/36 仍是 v3 前**旧字符串格式**；13 条**缺 `since_ch`** |

**恢复的唯一闸门要求三重巧合**（`memory.py:2114-2121`）：
1. LLM 主动申报恢复
2. 同章 `events` 含治疗语义
3. 事件文本**字面**含角色名

任一条不满足 → 恢复被拒。

**「恢复必须写在细纲里」的结构性原因**（新证据，直接回答用户痛点 1）：
> `agents/architect.py`（细纲规划）中 **`伤势|损伤` 0 命中** —— 细纲层**根本不规划伤**。
> Writer 被注入「⚠️已超恢复期，本轮必须推进」，但临时治伤又被 Editor 判「草率痊愈 → consistency 必改」（`editor.py:66-67`）→ **只能提前写进细纲**。
> **这是设计缺陷，不是模型问题。**

**另有语义重叠缺陷**：`memory.py:2108-2133` 中「痊愈」与「永久」语义重叠。

### 12bis.7 建议的存活/新增/删除清单（数据层）

**建议存活（瘦身）**：`books`、`chapters`（用 `content_hash` 取代 `path`）、`foreshadowings`（枚举化）、`plot_nodes`、`canon_claims`、`knowledge_ledger`、`drift_log`、`modules`、`genre_states`

**建议新增**：
- `entities`（一等实体表，统一类型枚举，**势力/组织合并**）
- `injuries`（独立表 + `heal_planned_ch`）
- `event_participants`（取代 `participants` JSON）
- `memberships`（带时间区间的归属表，**禁止可累积的边**）

**建议删除**：Phase 2 六表、`research_cache`、`chapter_chunks`（无模型的孤儿表）、`char_a/char_b`、`books.bible`/`story_so_far`、`_auto_migrate`

---

## 12ter. 记忆 / 账本 / 图谱 / RAG 审计补充（来自 `research/02-memory-graph-rag.md`）

> 由独立子代理完成，`memory.py`(3202 行) / `entities.py`(1213 行) 等逐行核查，全部结论带 `file:line`。

### 12ter.1 「五层记忆」是 prompt 装配的产物，不是架构分层

`memory.py:1-6` 的 docstring 与 `build_context`（`memory.py:1143-1691`）都自称 L0~L4。但：
- 五层**全部由同一个 `build_context()` 同步函数在一次调用中拼出**
- 没有独立的层边界、没有独立的写入时机、没有独立的失效策略
- 真正独立的存储介质只有三种：**文件**（`bible.txt` / `timeline.txt` / `rag.json` / `entities.json`）+ **SQLite 账本表** + **进程内缓存**

**「L0-L4」是命名，不是架构。**

### 12ter.2 生产开关实测（读自磁盘真实值，非默认）

```json
// backend/revision_config.json
{ "chapter_revision": { "enabled": false, "shadow": false } }
// backend/character_delta_config.json
{ "enabled": false, "shadow": false, "projection_mode": "legacy" }
// backend/audit_config.json（关键项）
"graph_inject_enabled": false,      "graph_summary_enabled": false,
"drift_align_enabled": true,        "drift_beat_enabled": true,
"drift_enable_correction": false,   "departure_inject_enabled": true,
"foreshadow_inject_max_chars": 6000,
"graph_inject_max_nodes": 40,       "graph_inject_max_chars": 1500
```

**本次审计范围内约 40% 的「高级」记忆机制在生产中是关闭的。**

### 12ter.3 L0 圣经的一个静默失效风险

`book.bible` 列在重开生成时被清空，且新书只写 `bible_path`：
```
圣经权威存储在 bible.txt（bible_path）。book.bible 列在重开生成时被清空、
且《执道者》式新书只写 bible_path → 必须读文件，否则每章 L0 世界观圣经静默为空、
世界观/境界/纪元约束失效。文件缺失时回退列值，兼容旧数据/迁移。
```

### 12ter.4 RAG 数据规模实测

```
book  8: entries=1190  chars=667041  key_chunks=0    dims={1024}  # 27.75 MB
book 23: entries= 446  chars=252145  key_chunks=426  dims={1024}  # 10.41 MB
book 18: entries= 309  chars=175514  key_chunks=180  dims={1024}  #  7.22 MB
book 24: entries= 110  chars= 63192  key_chunks=43   dims={1024}  #  2.57 MB
```
**`key_chunks` 在同一字段上语义完全不同**（book 8 = 0，book 23 = 426）：`key` 标记在**写入时**由 `_key_chapters()` 快照决定（`memory.py:446`），而关键章表随伏笔回收动态变化；早期书该功能未上线 → 老书整本 `k=0`。查询侧正确忽略它改用缓存表（`memory.py:595`），但**这个「写了不用」的字段白占 27 MB 文件空间**。

### 12ter.5 伤势不恢复：完整因果链（最终根因 = 架构缺失）

**恢复的唯一触发路径**（`memory.py:2114-2121`）是一个**双重合取守卫，两道门都由同一个 LLM 输出的同一个字段提供**：
```python
# 恢复类状态（好转/痊愈/解除/缓解）：须 events 同时含该角色名 + 治疗/恢复语义
if any(r in st for r in MemoryManager._IMPACT_RECOVERY) and not (
    events_blob and name in events_blob
    and any(k in events_blob for k in MemoryManager._IMPACT_RECOVERY_EVENT_KWS)
):
    warnings.append(f"⛔ 一致性守卫：{name}.{key}[{nm}] 恢复（{prev_st}→{st}）缺治疗/恢复事件支撑，保留原状态")
    continue
```
`events_blob` = 同一个 post_processor 在同一次调用里输出的 `events` 数组（`memory.py:2553`）。

**自锁**：prompt 要求 `events` 只写「2~5 条关键事件」（`post_processor.py:178`）。模型若判定"本章是过渡章、没有关键事件" → `events` 为空或不含治疗词 → **即便它同时认为伤好了，恢复申报也被守卫拒绝**。

守卫的设计初衷是对的（`memory.py:2032-2034`）：
```
# 只用「角色名出现在任意事件里」会把「押进城/走进大殿」这类与治疗无关的事件当成痊愈依据，
# 使伤势/代价被无依据清除（账本单向丢失，事后不可恢复）——2026-09-13 修。
```
但副作用是**把「记录一致性」变成了「叙事义务」** —— 要求 `events` 承担它 schema 上没承诺的职责。

**催促机制空转**：唯一的催促是 `_fmt_impacts` 的 `warn`（`memory.py:1001-1006`）。调用链：
```
_fmt_impacts → _fmt_char_line (memory.py:1029/1049)
             → build_context ONLY (memory.py:1200)
             → context["states"]
             → writer prompt
```
**`⚠️已超恢复期，本轮必须推进` 只到达 writer，不到达任何 planner。**

而 writer 的指令是"按细纲写"（`flowmode/base.py:157-162`）：
```
【本章细纲——场景骨架硬约束】
细纲执行规则（最高优先级，逐行核对）：
- 正文必须按细纲场景顺序展开…不得遗漏、不得调换因果；
- 禁止新增细纲之外的重要人物、重大转折、新功法/新设定/新地图——它们属于剧情级加戏；
```
**冲突确立**：
- 损伤区说「本轮必须推进（恢复剧情）」
- 细纲区说「最高优先级，逐行核对，禁止新增细纲之外的重要转折」

若细纲无治疗拍点，writer 面对两条互相矛盾的硬指令。**实测**：损伤区确实进了 prompt（`character_state=3441` token），但恢复从未发生 → **writer 选择了服从细纲**。这是**正确的服从**（细纲是最高优先级）。

**能力矩阵（最终根因）**：

| 角色 | 能看到损伤 | 能规划治疗 | 能落实治疗 |
|---|---|---|---|
| `architect`（细纲规划） | ❌ **无损伤字段、无账本快照** | ❌ | — |
| `writer`（正文） | ✅ `context["states"]` 有损伤区 | ❌ 受细纲硬约束 | ⚠️ 只能照细纲写 |
| `post_processor`（记录） | ✅ `snapshot_for_extraction.characters` | ❌ 只记录 | ❌ 只记录 |
| `editor`（审校） | ✅ `editor.py:63-72` | ❌ | ⚠️ 只能打回重写 |

> **没有任何一个 agent 同时具备「看到损伤」和「规划本章节拍」两个能力。**

`editor.py:69` 甚至明确要求：
```
· 状态表标注「⚠️已超恢复期，本轮必须推进」的损伤 → 本章必须安排推进（恢复剧情或明示永久化），无推进 → 判 pacing 必改；
```
**这把「规划失败」的成本转嫁给了「审校惩罚」**：architect 没规划治疗 → writer 按细纲写 → editor 判未推进 → 判必改 → 修改稿依然要服从细纲（`editor.py:104-109` 的细纲履约核查）→ **死循环，每章扣分，恢复永不发生**。

**三处独立确认「伤势不在细纲字段里」**：
1. `architect.py:1806-1808` prompt 定义：`entry_state` / `exit_state` 的四项是「**地点·时间·在场人物·谁知道什么**」—— 伤势不在其中
2. `outline_lint.py:121-125` 校验：`entry_state_missing` 的 detail 文本同上，无伤势
3. `detail_review.py:809` 三条审校准则之一：「状态一致：plot_detail 不得与本章 entry_state/exit_state 冲突（地点、在场人物、谁知道什么）」

`entry_state` / `exit_state` 是**自由文本字符串**（`architect.py:108-113`），即使细纲作者好心写了"楚渊肋下旧伤未愈"，它**不参与任何机械校验、不参与任何状态计算**。

**`VALID_BEATS = {"normal","boss","climax","emotion","recall","turning"}`（`schemas.py:25`）没有 `heal`/`recovery`** —— 恢复不是合法节拍类型。

**次要放大器（三个）**：

1. **`only_changes=True` 过滤掉慢性损伤**（`memory.py:988-989`）：
   ```python
   if only_changes and status in ("维持", "绑定") and "永久" not in status:
       continue  # 配角慢性维持不追踪
   ```
   配角伤势一旦进入"维持"，**连催促都不会出现**。

2. **`equip` 参数传递不一致**：`_fmt_impacts` 读 `ctx["equip"]`（`memory.py:954-956`）算治疗资源，但 `_ctx_dmg` 初始化时**没有 `equip` 键**（`memory.py:1178-1186`）；`limited=True` 路径传 `dict(ctx or {})`（`memory.py:1029`）**也没有 equip** → **配角永远按「无治疗资源」算，阈值 ×1.3 被推后**。

3. **`("诅咒",""): 999` 配合 `th < 999` 判定**（`memory.py:1001`）→ 诅咒永不催促。注释说"由解咒伏笔驱动"，但**没有任何代码把解咒伏笔和诅咒关联起来**。

**结论**：
> **「伤势不恢复」不是 bug，是架构缺失。**
> 需要触发恢复的代码路径每一步都存在，但链条在「**规划**」环节断掉：
> 记录 ✅ → 状态机 ✅ → 催促注入 ✅ → **规划 ❌** → 正文 ❌ → 记录（无变化）→ 循环
>
> 修复不需要改 `_merge_impacts`，需要**让细纲规划者拿到损伤账本，并把「恢复」变成可规划的一等节拍（beat）**。

### 12ter.6 阈值反向引导（新发现，严重）

`memory.py:291-294` 的注释自述：
```
⚠️ 每个 field × severity 组合都必须有值（2026-09-13 修）：原表只登记「轻/重」两档，
而模型实际会写「中」「极重」「轻微」→ _impact_threshold 拿不到 base → 返回 None
→ 这些损伤永远不会被催「本轮必须推进」。实测书 24：最重的「神魂受创（伤势·极重）」
恰恰是唯一不催的那条，而轻伤催得最凶 —— 注意力被反向引导。
```

**即：系统曾经把注意力反向引导 —— 最重的伤不催，轻伤催得最凶。** 根因是**枚举多源**（阈值表 / prompt / 模型实际输出三方不一致）。这直接支撑 `02-根因与架构对策` 对策④的「枚举单一来源」要求。

### 12ter.7 势力漂移的两个致命点（补充 §12bis.4）

**致命点 1**：`势力` 不在 `ENTITY_TYPES` 里（`entities.py:17`）→ **所有势力无节点**。
**致命点 2**：边界章号比较跨卷**必假**（`entities.py:596`，`last_chapter` 是卷内号）。

**`_merge_collection` 失败机制**（`memory.py`）：见报告 §4.3。
**名称别名**：一半覆盖、一半空白（报告 §4.4）。
**生命周期前缀执行有洞**（`memory.py:668-669` 注释）：
```
# ± 必须先于生命周期前缀剥离：全量重报条目形如 "+[失·不可寻]家庭合影"，
# 若先剥前缀，"[失·不可寻]" 会逃过归一化残留下来（2026-09-06 书20 实证误杀）。
```

### 12ter.8 图谱缺口（补充 §12bis.5）

- `graph_inject_enabled: false`、`graph_summary_enabled: false`（生产关闭）
- 社区检测用 `connected_components` → 全书塌缩为 1 个连通分量
- 详细缺口清单见报告 §7.4

### 12ter.9 时间线与认知状态（fog-of-war）

报告 §8.1-8.3 结论：
- **没有真实时间线模型**，`timeline.txt` 是文本累积
- 事件排序依赖章号
- **「每个角色在时刻 T 知道什么」无独立追踪**（§8.3）

**这对重构是重要输入**：`WorldStateSnapshot` 里的 `known_facts`（谁知道什么）在旧项目**没有对应实现**，属于**新增能力**。

### 12ter.10 写入放大与重复抽取

报告 §9：每章的记忆写入清单（§9.1）、`apply_extraction` 内部写（§9.2）、复杂度分析（§9.3）、**重复抽取分析（§9.4）**、一致性成本（§9.5）。

---

### 12quater.1 【决定性】痛点 5 的根因是「多层代码显式禁止横向扩张」

> 来自 `research/05-longform-structure.md` §5。**这不是"规划能力不足"，是设计上禁止扩张。**

**证据链 A：禁止横向扩张的代码（逐字原文）**

| # | 位置 | 原文 | 效果 |
|---|---|---|---|
| A1 | `architect.py:397` | 「每章 plot 必须是上一章情节的直接延续（同一事件/对话/调查/冲突的下一拍），**禁止每章自起自收（新场景开场+圆满收束）**」 | 禁新场景 |
| A2 | `architect.py:401` | 「相邻章时间线连续，**地点延续或自然过渡，禁止无交代的场景硬切或时间跳跃**」 | 禁地点跳变 |
| A3 | `architect.py:925` | （STORY_SYSTEM_TEXT 同款条款，story 模式生效） | 同上 |
| A4 | `architect.py:372` | 「ultra 档同一卷相邻章**必须场面连续**；**卷末可为下一卷换场留一句理由**；卷首允许新场景」 | **换场只允许在卷末/卷首** |
| A5 | `writer.py:258` | 「禁止另起炉灶、禁止直接休息疗伤、**禁止无过渡换地图**」 | 写作层禁换图 |
| A6 | `editor.py:243` | 「时间/场景硬切词与**无过渡换地图仍须改写**」 | 审校层把换图判为缺陷 |
| A7 | `writer_rules.py:374` | 「**禁止新增细纲之外的重要人物、重大转折、新功法/新设定/新地图**」 | **细纲没有的新地图 = 违规** |
| A8 | `editor.py:106` | 「无中生有的…**新地图**（剧情级加戏）→ 记「细纲偏离」」 | 换图被记为偏离 |

> **A7+A8 是致命组合**：写作层禁止写细纲外的新地图，审校层把新地图判为「偏离」。
> **而细纲（`plot_detail`）本身在结构上无法承载地图扩张** —— 它被要求写成「场景/人物：目标→阻碍→结果」的
> **当章事件表**，且 prompt 明令「末行停在当前事件未收束的拍上」（`architect.py:1795-1814`）。

**证据链 B：唯一允许换图的地方都是「卷级例外」**

grep 全 `backend/app` 的 `换地图|新场景|新地图|地理|势力扩张`（37 命中，去重）显示 7 处「卷首/卷末例外」条款，
而所有「扩张」字样的其他命中（`story_genres.py:73`「威胁等级随主角扩张持续升级」、
`story_flows.py:87,101,115`「势力版图扩张」）**全是描述性文字，无代码消费**。

**算术**：
```
ultra = 3~6 卷 → 整本 1000~1700 章，允许换地图的机会 = 2~5 次（仅卷首）
book 23 = 400 章 / 5 卷 → 每 80 章才有一次换场许可
```

**证据链 C：规划层没有任何「扩张」schema**

对 `architect.py` 所有 prompt 常量做 key 扫描（`SYSTEM_TEXT` / `STORY_SYSTEM_TEXT` / `SCALE_RULES` /
`PACING_RULES` / `STORY_SCALE_RULES` / `STORY_NON_TERMINAL_RULE` / `ULTRA_ENSEMBLE_RULE` /
`ULTRA_TOTAL_CHAPTERS_RULE` / `_BLUEPRINT_BLOCK`）：**零个地理/势力/反派阶梯字段**。

`scales.json` 四档 rule + pacing 全文扫描 → **零个地理/势力/反派阶梯字段**。
`story_director.py` 的七锚 → `factions`（3~8 股）是**静态关系网**，没有层级、没有登场时点。

**证据链 D：真实大纲的「地理熵」实测**

方法：从章 `plot` + `title` 中正则抽取「2~4 汉字 + 地点后缀」token，比较前半本 vs 后半本的新增地点数。

| book | 章数 | 后半本独有的新地点 token（≥3 次） | 数量 |
|---|---|---|---|
| **24**（修仙） | 447 | **（无）** | **0** |
| **23**（规则怪谈） | 400 | `裴照隔门`(9) ← 非地名 | **1** |
| **18**（修仙） | 350 | `与天衍宗`(6)、`烬与鬼王宗`(5)、`危急关`(4)、`前往葬神渊`(4)、`风纪殿`(3) | **5（全是已登场势力的变体）** |
| **8**（宫廷） | 286 | `帝师府`(3) | **1** |

**书 23 是最刺眼的样本**（400 章 ultra）。高频"地点"token 实测：
```
周德海(11) 裴照隔门(9) 电梯在四楼(5) 长明楼(5) 陈砚下楼(5) 他上楼(4) 电梯门(4)
老周隔门(4) 他推门(3) 全在长明楼(3) 陈砚开门(3) 陈砚隔门(3) 扇朝下的门(3)
不开门(2) 天亮门(2) 梯上五楼(2) 废弃二食堂(2) 负一层铁门(2) ...
```
> **400 章的故事，场景 token 的绝对主体是「门 / 电梯 / 楼层 / 走廊」。**
> `长明楼` 是全书唯一地标，仅出现 **5 次**。

**因果链闭环**：
```
A1-A4（章章必须无缝承接，禁新场景/禁地点跳变）
+ A5-A8（写作与审校把换图判为缺陷）
+ 规划层无扩张 schema
+ 换图只允许在卷首（全书 2~5 次机会）
→ 模型只能在同一地点内反复推进
→ 用「更细的描写 / 更多的心理独白 / 更多的重复场景」填章数
→ 这正是「过于啰嗦和 AI」的机械成因
```

**用户抱怨的三个症状 → 三个代码根因**：

| 症状 | 根因 |
|---|---|
| 「只在一个地方全程展开」 | 无横向扩张 schema + 换图禁令（A1-A8） |
| 「过于啰嗦」 | 细纲厚度不足（p50 788 < 900 下限）+ `entry_state` 仅 13% 落地 + 章节必须在原地打转 |
| 「AI 味」 | 16 组重复标题 + 段级 goal 逐字复制 + 模板卷钩子 |

**对重构的硬性要求（强化 `02-根因与架构对策` 对策②）**：
> 仅仅「规划全部卷的升级台阶」**不够**。必须同时**解除规划/写作/审校三层的换图禁令**，
> 并新增「卷内允许换地图」的正向机制。否则即使台阶规划出来了，写作层仍会因 A5/A7 而拒绝执行，
> 审校层仍会因 A6/A8 而判为偏离。

---

### 12quin.1 【最底层成因】信息预算恒定、篇幅线性增长

**这是「啰嗦 + AI 味」的最底层机械成因**（比"换图禁令"更根本）。

实测各记忆块的字数上限：

| 记忆块 | 上限 | 来源 |
|---|---|---|
| 前情时间线 | 6,000 字 | `memory.py:1680` |
| 世界观圣经 | 4,200 字 | `bible_inject.py:37` |
| RAG 前文片段 | 1,800 字 | — |
| 前卷摘要 | 200 字/卷 | — |

**这些上限是恒定的，不随篇幅增长。**

```
→ 越到后期，「记忆密度」（每章可用前情 / 已写篇幅）越低：
   第   50 章：可用前情 12,000 字 / 已写  25 万字 = 4.8%
   第 1500 章：可用前情 12,000 字 / 已写 750 万字 = 0.16%
```

> **第 1 章与第 1500 章拿到的世界记忆一样多。**
> 到后期只有 **0.16%** 的可用带宽 —— 模型唯一能做的，
> 就是**把当下这一小片场景反复描摹到字数达标**。
> **这才是「过于啰嗦和 AI」的机械根因**（换图禁令是它的放大器）。

**对重构的硬性要求**：记忆预算必须**随篇幅增长**（分层摘要 + 压缩 + 检索），
不能是恒定常量。这是 P1/P3 必须解决的结构问题。

### 12quin.2 另两个「同型 bug 只修了一处」（活 bug）

**bug 1：续卷目标逐字相同**（`continue_story.py:1251-1259`）

与 `_synthesize_segments` **完全同型**的活 bug：Python 列表推导给每个续接卷写入**逐字相同**的
goal「延续破局点开启的新剧情线，逐步推进至终局」+ 标题「第{n}卷·新篇」。

> 2026-09-11 修了**分段**的同句问题（`_synthesize_segments`），
> **没人检查续卷的同一处**。book 11 实测三卷逐字相同。

**bug 2：续卷章结构性最差**（`continue_story.py:1216-1227` 只写 9 个字段）

| 缺失 | 后果 |
|---|---|
| 无 `plot_detail` / `end_hook` / `entry_state` / `exit_state` | 续卷章无细纲 |
| `resolve_fs` 全文件**零赋值** | 续卷不回收伏笔 |
| **不写进 `book.outline["chapters"]`** | → `_serial_detail_gate` 的 `pos is None → return _pass()`（`pipeline.py:2679`）**静默放行**<br>→ Writer 的【本章细纲】块**整块不注入**<br>→ 「补齐细纲」也修不了（`plan_by_key` 查不到，`pipeline.py:3266`） |

### 12quin.3 长线伏笔逐卷被清空

卷末**无条件** `_force_recover_fs`（`pipeline.py:5994 → 5456`）把贯穿/长期伏笔
**并入末章核销**，且 ultra 要求 0 未回收 → **第 2 卷末就清空，后续无长线可收**。

> 这解释了为什么 `ultra_structure_config` 要求「贯穿型至少 1 条指向终局」，
> 而实测书 23 的 70 条伏笔只回收 16 条 —— **不是没回收，是被提前强制核销了**。

### 12quin.4 要求了但从不校验（公理：要求 ≠ 执行）

| 字段 | 要求位置 | 校验/落库 |
|---|---|---|
| `conflict_curve` | `architect.py:413-414`（唯一出现处） | **从不校验、不落库** |
| `character_arcs` | 同上 | **从不校验、不落库** |
| `milestone_ids` | 大纲 schema | 实测落地率 **0%** |
| `entry_state` | `architect.py:1806-1808` prompt 要求 | 实测填充率**仅 13%**（段1=100% → 段2=12% → 段3起**归零**） |
| `plot_detail` 下限 900 | `outline_config.json` | 实测 p50 **788（未达标）** |

### 12quin.5 活着的逻辑矛盾（被 shadow 掩盖）

`min_milestones ≥ 9`（`ultra_structure_config.json`）vs `_clean_milestones` 的 `ms[:8]`
（`story_director.py:494`）—— **要求 9 个但代码截断到 8 个**。
因为 `enabled: false, shadow: true`，这个矛盾从未暴露。

### 12quin.6 死代码

| 模块 | 状态 |
|---|---|
| `detail_review.py`（949 行） | **整个模块零调用方** |
| `novelty._cosine` / `_jaccard_matrix` | 死 |
| `config.py:63 NOVELTY_GUARD` | 死开关 |

### 12quin.7 实测：book 24 真实大纲的落地率

| 指标 | 实测 |
|---|---|
| `plot_detail` p50 | **788**（下限 900 **未达标**） |
| `entry_state` 填充率 | **仅 13%**（段1 100% → 段2 12% → 段3起归零） |
| `milestone_ids` 落地率 | **0%** |
| 重复标题 | **16 组覆盖 33 章** |
| 段级 goal | **因 `_normalize_segments` 拆分子段共享而逐字重复**（段 2/3、4/5、6/7…） |

### 12quin.8 token 实测：大纲层是最廉价的投资

| 项 | 数值 |
|---|---|
| book 23 大纲 token | 3.93M |
| book 23 全书 token | 11.9M |
| 大纲占比 | **33%** |
| **1500 章外推大纲占比** | **仅 6%** |

> **在大纲层多花 5 倍 token 是数量级上的廉价投资。**
> 这为 `02-根因与架构对策` 对策②（全卷台阶前置 + 闸门）提供了成本依据。

---

### 12sex.1 【根因】`must_review` 是只写不读的死信号

**全仓 25 个 `must_review` 命中，零个读取点。**

唯一近似读取是 `pipeline.py:6301` 把它写进 trace metadata。
`_commit_chapter(..., must_review, ...)`（`pipeline.py:7122`）**收进形参后再未引用**。

真正闸门是另一条链：`_force_flags = bool(style_notes) or _is_turning`（`pipeline.py:7354`）
—— 读的是 `style_notes`，**不是 `must_review`**。

> **后果**：`style_metrics.needs_rewrite` 的全部"阻断"语义，
> 实际强度 = 强制调用 Editor 一次 + 加一段警告。
> Editor 返回 `revised_content: null`（`editor.py:319` 明文允许）则**正文一字不改入库**。

**→ 重写时每个信号必须有明确消费者，否则删掉。**

### 12sex.2 【最强证据】检测器不缺，执行通道缺（DB 实测）

`learnstory.db` 1,441 章 `review_json`（本次独立复核确认）：

```
有 Editor 评分的章 210，被点名 ai_style/pacing 185 (88.1%)
  其中仍判 passed (score>=7) 57~60 (30.8%~32.4%)   ← 点名了但放行
issue 类型分布: ai_style 228 > consistency 219 > pacing 204
              > other 114 > logic 100 > foreshadow 77 > ooc 7
ai_style + pacing = 432 = 全部 issue 的 45.5%
  —— 恰好是唯一不拦章的一类
```

Editor 的 issue 文本（165,610 字符）关键词：
`描写 466 / 重复 211 / 节奏 173 / 套话 167 / AI味 55 / 比喻 43`

> **「比喻」在代码层零实现**（全仓仅 `review_triage.py:38` 一句自认抓不到；
> `明喻/暗喻/修辞` 各 0 命中），**但 LLM 语义审校抓到了 43 次并给了改写建议**。
>
> **所以正确方向不是"加检测器"，是"给已有结论接上执行"。**
> 这与 `prose_style.py:51-68` 的三次负结果（字符级测不出注水）完全一致。

### 12sex.3 【数据级根因】人物关系单一

本次独立复核（`mode=ro&immutable=1`）：

```
966 条关系边：
  人物-物品 286 (29.6%)   ← 是库存登记，不是关系
  人物-人物 254
  人物-地名 141 ...
  ...

254 条人物-人物边用了 191 种不同的 relation_type
  top: 敌对10 / 血亲9 / 上下级6 / 问询者与被问者6 / 师徒4
       / 试探与被试探3 / 监视3 / 对手2 / 救援者与被救者2 ...
```

**`post_processor` 自带的 9 词受控词表**（师徒/敌对/盟友/同门/结拜/血亲/道侣/仇敌/上下级）
**只覆盖 31/254 = 12.2%**。

> **87.8% 是模型每次即兴的自由文本标签**，平均每种只用 1.33 次
> → 系统**无法聚合/比较/去重/要求"关系多样性"**
> （例：敌对关系到底几条？`敌对/对手/仇视/对立` 无法归一）。

**且规则写了没人守**：`post_processor.py:60` 自己写了"持有走 `state_updates.装备`、不逐件记关系"，
实测**模型没守、且无校验**。

**相关的三处闸门失效**：
- `ultra_structure` 的关系线闸门 = `{"enabled":false,"shadow":true}` → **永不阻断**
- 补线失败仅 `log_problem(warn)` 后放行（`story_director.py:417-424`）
- `graph_inject_enabled=false` → writer 拿不到关系网（`memory.py:1257` → `writer.py:481` 永不触发）

### 12sex.4 Writer prompt 预算（已并入 `04-agents-and-prompts.md` §3.3）

story/lean/standard，第 2 卷第 12 章：
**SYSTEM 4,785 + USER 25,330 = 30,115 字符 ≈ 10.7k–13.3k tokens**

- SYSTEM：`_WRITE_BASE` 2,313 + `anti_ai_block` 2,472
- USER 最大块：**L0 圣经 6,000**（卷内不变）> L4 RAG 4,014 > L2 时间线+防重演 3,239
  > chapter_info+硬规则+细纲 3,187 > 状态表 2,221 > 名词表 1,693 > 事件 1,414 > 范本 1,244
  > 上章结尾 859 > 骨架 736
- **stable ≈ 11,998 (36.5%) / volatile ≈ 19,117 (63.5%)**
- **拼装顺序对缓存不友好**：每章变的文风范本（U4，~800 字符）插在圣经（U1）之后，
  **把后面 19k 全部推出缓存前缀**
- ✅ **`output_requirement` 已在 prompt 最尾（recency 优先）—— 这是为数不多的正确设计，保留**

### 12sex.5 题材差异化是薄覆盖层 + 一处活体污染 bug

**非 boss 章（约 85% 章节）题材影响 = 0 字符**（`default.py:40-43`：`if beat != "boss": return []`）。

18 题材里 **13 个的 boss 文本完全相同**（`combat_level` medium≡high、none≡low，4 值仅 3 段文本）。
`genre_brief` 长度极差仅 66 字符（305–371），全部六行同构模板。

唯一改**控制流**的旋钮只有 `dialogue_floor`（`style_metrics.py:156` 判 `needs_rewrite`）；
**`pacing` 零消费点**；`state_schema` / `extra_entity_types` / `extra_relation_pairs` / `book_kind`
**全无生产消费点**。

**⚠️ 活体污染 bug**：
- `templates/scenes.py` **只有 `xianxia` 一个 key**，`get_template()` 对任何 scene 回落
- `worldbuilder.py:82,120` 与 `architect.py:2851,2924,2945` 注入 `tpl['style_prompt']` **无 flow 屏蔽**
- → **任何非修仙题材的圣经/大纲 prompt 里都写着「东方玄幻成长流：可用宗门、功法、器物、境界…」**

### 12sex.6 Prompt 存量与重复

| 项 | 实测 |
|---|---|
| app/ 内 `>=200` 字符块 | **292 个 / 152,057 字符** |
| 含指令关键词的块 | **116 块 / 89,446 字符** |
| 碎片最严重 | `architect.py` 17,372 / 32 块；`pipeline.py` 16,904 / 44 块 |
| 设计文档写在 docstring 里 | **6 个模块，2.4k–3.4k 字符，合计约 13.5k** —— 与 prompt 文本同层级，是定位困难的一大来源 |

**规则重复（8-gram 交集实测）**：writer 规则文本 vs `EditorAgent.system` 重叠 **10.9%**（538/4944），
命中**全是整条规则的逐字复制**（人物一致性七查、名词引入八查、事件冲击五条、情绪呈现四条…）。
**同一套规则写三遍**（writer system / writer user / editor）。

### 12sex.7 实质矛盾与死区

| 矛盾 | 内容 |
|---|---|
| **对话占比阈值差 3~7 倍** | `editor.py:31` 写 `④ 对话占比低于35%（与写作端阈值一致）`，而写作端 lean 档按流派 floor = **0.05~0.12**。且 `writer_rules.py:375` 明文覆盖"按比例的配额式要求一律以本篇为准" |
| **editor 是冻结的 legacy 副本** | `editor.py:34` 的"每段≤5行/形容词≤2次"是 legacy 配额，lean 已废除。**editor 从不 import writer_rules（0 命中）** |
| **lean 反而更大** | 与 `writer_rules.py:36` 自述"不再增加禁令条数"**矛盾**：legacy 1,833 → **lean 2,472（+639，+35%）**；writer system 4,520 → **5,197** |
| **规则全部等权** | 规则总量实测 **约 45 条顶层 + 约 80 个子规则**，全部等权"违反即废稿"，**无优先级** |
| **score=6 死区** | `review_triage.py:6-7` 记录"47 章里 **28 章恰好 6.0 分** —— 主编点了名、正文按原稿入库"。本次全库复核：**121/1441 = 8.4% 恰好 6.0 分** |
| **配置与自身 DEFAULTS 矛盾** | `review_config.json` 磁盘值 vs DEFAULTS：`fast[1,2]` vs `[1,1]`；`ultra[20,15]` vs `[3,5]` |
| **伪造通过分** | 跳过审校时 `pipeline.py:7363` 把 `score = 7` —— **伪造通过分**（本次复核：全库 **28 章恰好 7.0 分**） |

### 12sex.8 死 prompt 与基线失真

| 项 | 状态 |
|---|---|
| `detail_review.py` 全套 | **零生产调用者**（7,042 字符 prompt + 1,273 行确定性检查器） |
| `WRITE_SYSTEM` / `ANTI_AI_RULES` | **冻结 legacy** —— benchmark 基线测 legacy、生产跑 lean → **基线失真** |
| `researcher` 6 个 Agent + `Gate` / `QueryPlanner` | **纯 learn 专属**，story 重写可整体丢弃 |

### 12sex.9 Prompt 版本化是最大缺口

```
ab_test / canary / prompt_version   全仓 0 命中
唯一开关 rule_profile              是模块级全局单例（无 book/request 维度）
3 处 "experiment" 字段             无任何代码读取
long_memory_prompt_shadow.py:101   自认 "quality_comparison": "not_run_no_llm"
models.py:445                      注释自认不存 prompt 正文（只有 sha256）
```

> **能否在生产对比两个 prompt 变体：不能。**

### 12sex.10 明确不要做的 8 件事（全部有实测背书）

| # | 不要做 | 依据 |
|---|---|---|
| 1 | 不要为「注水」建确定性闸门 | `prose_style.py` 四次验证失败 |
| 2 | 不要靠删词黑名单 | — |
| 3 | 不要把「总结式结尾」当硬伤 | 书 23 实测 `summary 0 / time_skip 0` |
| 4 | 不要再增加禁令条数 | 规则已 45 条顶层 + 80 子规则，全等权 |
| 5 | 不要把「反复写生理反应」当情绪方案 | — |
| 6 | 不要指望 `genres/` 插件 | 非 boss 章题材影响 = 0 字符；`pacing` 零消费点 |
| 7 | **不要以为缺检测器** | Editor 抓到「比喻 43 次」而代码零实现 → **缺的是执行通道** |
| 8 | **不要把 baseline 建在自家产出上** | `prose_style.py:19-29` 全部阈值取自书 23，**而书 23 是本流水线生成物** |

### 12sex.11 style_corpus 实测缺陷

```
可检索字符仅 74,808（171 样本 / 213 chunk，每 chunk 截断 400）
45 个重复样本名
fp 字段全为 None  →  style_corpus.py:553 的指纹去重对这些永不生效
范本 : 规则 ≈ 1 : 6
```

---

## 13. 待验证假设（诚实标注）

以下是我**尚未实测确认**的推断，重构设计时不应作为既定事实：

1. **缓存命中 44% 中有多少是"可避免的未命中"**：需要按 prompt 结构分析才能定论。已知 `memory` 10.9% 明显偏低，但需确认是前缀不稳还是 provider 侧不支持。
2. **空输出 22.2% 中"思考吃光预算"占多少**：`finish_reason` 100% 未采集，无法区分 `length` 截断 vs 其他。**这是重构必须先补的观测字段。**
3. **正文实际质量**：书 23 的 47 章正文在文件系统，本次未读取。质量判断目前只能依据 Editor 分数（而 Editor 分数已被项目自己声明"不是质量真值"）。
4. **`entity_classify` 100% 空输出的具体原因**：仅 32 条样本、仅 4 条有 completion 数据。可能是该 agent 的调用方式（schema/超时）问题，需看代码。
5. **`novelty` 的 O(n²)**：项目已实测确认（100→1500 章：0.20s→28.85s）并决定不优化（不在热路径）。这是**已验证事实**，非假设。
6. **RAG 单次检索 3.3s~12.6s**：来自 `TODO_20260913.md` §7.4，未独立复测。

---

## 14. 本基线对重构的直接含义

| 实测事实 | 对重构的硬性要求 |
|---|---|
| **业务代码 95,471 行 / 358 文件**；`pipeline.py` 8,607 行、`main.py` 4,630 行 | 编排层必须重写为声明式图；禁止再出现千行级单函数 |
| API：**148 个路由全 `@app.xxx` 直注册在单文件**，0 个 APIRouter、0 个 `response_model`；BOOK/CHAPTER 64 端点占 65.3% LOC；单端点最大 `get_book_progress` 188 行 | 端点分模块（APIRouter）+ 强类型响应模型；目标 `main.py` ≤200 行 |
| `_pylibs/` **30.9 MB / 288,238 行 vendored 依赖入库** | 用 uv 锁文件；**禁止** vendored 目录 |
| 5 张 Phase 2 表全 0 行 + 大量配套文档/配置/测试 | **不要移植未产生数据的机制** |
| 400 章挤第 1 卷 / 后续卷 `blueprint=missing` | **卷级升级台阶必须在大纲阶段全部规划** |
| 人物-人物关系仅 13.3% / 势力实体 0 个（**三套类型词汇表冲突**：`entities.py` 有「组织」无「势力」、`models.py` 有「势力」、`graph.py` 都无） | 关系/归属/状态/事件**四分离**；势力是一等实体；**统一类型枚举** |
| 地点字符串碎片化 40+ 变体 + 77 次为空 | 地点必须是**归一实体**，有层级 |
| 伤势恢复率**实测 0%**（36 条损伤无一走到「痊愈」）；`architect.py` 看不到损伤；`VALID_BEATS` 无 heal | 恢复必须在**细纲层**规划为场景；`BeatType` 必须有 `HEAL`；调度器驱动 |
| 阈值反向引导（最重的伤不催、轻伤催得最凶） | 阈值表/枚举**单一来源**，由类型系统保证穷尽 |
| severity 三方不一致（代码 5 档 / prompt 3 档） | 枚举**单一来源**（Pydantic 生成 prompt 约束） |
| 章号双表示（`vol*10000+idx` + 卷内号）导致 P0 事故 | **单一章号表示法**（全书递增 int） |
| reasoning 占 completion 76.7% / verifier 99.4% | 思考预算与输出预算**分离**，按节点类型配置 |
| 空输出率 22.2%（entity_classify 100%） | **必须采集 `finish_reason`**；空输出率 SLO |
| 缓存命中 44%（memory 10.9%） | prompt **稳定前缀 + 易变后缀**分层；命中率纳入 CI |
| 伏笔回收率 6%~23%（长篇） | 伏笔必须有**回收排程**与未回收阻断 |
| `prose_style` shadow=true / `dialogue_floor=0.12` | 文风门禁必须可阻断；阈值需重新标定 |
| `ultra_structure` enabled=false（指标设计正确） | **启用并强化**结构校验器，作为大纲硬闸门 |
| `finish_reason` 100% 未采集 | 观测契约里补上 |
| **121/123 个测试文件在模块级改 `os.environ`**（`config.py` 类定义期冻结 settings） | `Settings` **运行时注入**（非 import 期冻结）；统一 conftest 隔离 |
| 26 个 JSON 配置 + 7 个模块各自的 `_raw/reload/save` 缓存；`.env` 写模型被 `llm_providers.json` **静默覆盖**（`config.py:181-197` import 期覆盖） | 配置集中化、类型化、**单一优先级**；禁止静默覆盖 |
| 48 个 `_*.py` 调试脚本 + 11 个 `.bak` + `book23/25_ultra_repair.py`（**以书号命名生产模块**） | 开发残留不入库；禁止以业务实例名命名生产模块 |
| `problems.jsonl` 394 warn / 1 error，entities 模块占 282 条 | warn 噪音淹没真 error；日志需分级与限流 |
| `trace_scope` 全仓仅 **1 处**调用；`generation_spans` 缺 `cached_tokens` 列；`llm_stats` 是内存 `deque(50)`，重启清零；`/ops/traces` 前端零消费 | 插桩覆盖每个节点；观测数据必须持久化 |
| CORS `allow_origins=["*"]` + 零鉴权；后端无 StaticFiles 托管 | 单进程单端口（后端托管静态）；鉴权可选但 CORS 不得全开 |
| `flowmode/` 门面思路正确，但 37 方法上帝接口 + **方向反了**（story 侧给 learn 钩子写空实现）；门面外仍有 30+ 处直读 `book_kind` | 改为 **8 个 Protocol 组合**；learn 用 `NotImplementedError` 占位；用 AST 检查器把规约变成机器可验证 |
| `evaluation_store` 盲评不变量 + `benchmark/adapter.py` 薄边界（19 行，全仓最佳解耦点） | **可继承的设计**（按 D-03 重写实现） |
| `sweep_all.py` 六态判定（含 `baseline_stale` 阻断）+ 每文件独立子进程 | **可继承的测试策略**；新项目测试必须默认隔离 |
| `learn` 停更硬证据：`templates/scenes.py` 仅 **39 行 / 1 个 xianxia 模板**，`main.py:566` 却用它做创建校验 | learn 一期不做（D-08）是正确判断 |

### 14.1 新增风险（重构前需处理）

| 风险 | 说明 |
|---|---|
| `test_rolling_acceptance_guard.py` **当前是红的** | 且 `known_failures.json` 为空 → 按该仓库自己的口径，这是**真实回归**而非既有债务。虽与新项目无直接关系，但说明旧项目状态不干净 |
| `.gitignore` 自承「历史上有 13 个 `llm_providers.json.bak_*` 裸奔」 | 一次 `git add -A` 就会把密钥写进历史。**建议确认 git 历史无泄露 key** |
| `backend/logs/` 里有 3 个 `.py` 调试残留 | 写在日志目录 |
| `AGENTS.md` 规约 1 记录「git 停在批次6，工作区有 38 个批次的未提交改动」 | 旧仓起点状态需人工确认（新仓独立，不受影响） |
| `backend/free_pool.json` 含**明文 API key** 且全仓零读者 | 确认旧项目是否已清理；新项目不得出现 |

### 14.2 统计口径规约（写给自己）

> 本次基线在规模数字上**犯过一次错**（把 `_pylibs/` 计入项目代码、且 PowerShell
> `Measure-Object -Line` 漏计空行，得出 312,628 行的错误数字）。
> **后续所有规模类结论必须注明：统计范围（排除哪些目录）+ 统计工具 + 计数方式（换行符 vs Measure-Object）**。
> 这条规约同样适用于新项目自身的度量报告。


