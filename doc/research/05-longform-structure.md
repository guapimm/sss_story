# 05 · 长篇结构 / 大纲生成 / 规模处理 —— 重写前调研报告

> 调研对象（只读）：`E:\AI_project\story_llm`
> 覆盖：`app/agents/architect.py`(3037 行) · `story_director.py`(558) · `story_arc_planner.py`(163) ·
> `app/ultra_structure.py` · `ultra_structure_repair.py` · `outline_lint.py` · `outline_config.py` ·
> `rolling_story_pipeline.py` · `continue_story.py`(1605) · `scales.json` · `chapter_limits.py` ·
> `story_random.py` · `story_health.py` · `lang_tips/new_dp_plan/bibel.md`(2649 行) · `AGENTS.md`
> 实证数据：`backend/storage/story/books/24/_outline_partial.json`（真实 447 章大纲，1.03 MB）、
> `backend/learnstory.db`（8 本书的真实 trace/token 数据，只读连接）
>
> 对接用户抱怨 #5：**「长篇小说内容不够丰满，1500 章的小说有的只在一个地方全程展开，过于啰嗦和 AI」**
>
> **一句话结论：抱怨 #5 不是模型能力问题，是架构里根本没有「横向扩张」这个维度。**
> 系统对长篇只有一条硬约束 —— **纵向不许断**（章与章必须无缝承接）；横向（新地理/新势力/新反派/新支线）
> 既没有 schema、没有 prompt 要求、没有闸门、没有 lint，还在多处被显式**禁止**（见 §5）。
> 于是「1500 章只在一个地方展开」是当前 prompt 约束下的**必然收敛结果**，而非偶发质量事故。

---

## 一、规模档位

### 1.1 `backend/scales.json` 的四档规格（story 段，逐字）

| 档位 | label | chapters | volumes | per_vol | segment | typical | expandable | ending |
|---|---|---|---|---|---|---|---|---|
| `fast` | 超短文 | 3~10 | 1~1 | 3~10 | **0**（不分段） | 6 | true | divergent |
| `standard` | 短文 | 10~100 | 1~1 | 10~100 | **0** | 50 | true | divergent |
| `grand` | 长文 | 100~300 | 1~1 | 100~300 | **30** | 200 | true | divergent |
| `ultra` | 超长文 | **1000~1700** | **3~6** | **300~500** | **50** | 400 | **false** | **closed** |

`scales.json:131`（story.ultra.rule，逐字）：

```json
"rule": "篇幅档位=超长文：全书 1000~1700 章，每卷 300~500 章，total_volumes 3~6；volumes 数组至少给出 3 卷规划，本次只细化第 1 卷；每卷是一个相对完整的大故事弧并留下跨卷悬念；【终卷·强收束】最终卷必须兑现 ending_promise、伏笔全部回收，这是最终成品（不可续接扩写）"
```

**「本次只细化第 1 卷」是问题的第一现场。** 1500 章的规划粒度实际是 **1 卷 ≈ 300~500 章**，
其余 2~5 卷在整个生成期只有 4 个字段。

### 1.2 每档的差异是「量」不是「质」

把四档的 rule 全文并列（`architect.py:226-242` + `933-938`，运行期由 `_refresh_scale_rules()` 从 json 覆写）：

| 维度 | fast | standard | grand | ultra |
|---|---|---|---|---|
| 起承转合 | 「节奏紧凑」 | 「完整起承转合」 | 「完整起承转合+阶段性里程碑」 | 「每卷是一个相对完整的大故事弧」 |
| Boss 配额 | — | ≥1 | ≥3 | **≥3/每卷** |
| 爆点 climax | ≥1 | ≥1 | ≥3 | **≥3/每卷** |
| 情绪点 emotion | — | ≥2 | ≥5 | **≥5/每卷** |
| 回忆杀 recall | — | — | ≥2 | **≥2/每卷** |
| 长期伏笔 | — | — | ≥3 全书 | **≥3/每卷**，贯穿型 ≥1 指向终局 |
| 非终卷禁令 | — | — | — | ✅`STORY_NON_TERMINAL_RULE` |
| 群像放宽 | — | — | — | ✅`ULTRA_ENSEMBLE_RULE` |
| 全书章数硬钳 | — | — | — | ✅`ULTRA_TOTAL_CHAPTERS_RULE` |

**关键观察**：ultra 相对 grand 的差异**全部是"每卷重复同一份配额"**。
`PACING_RULES["ultra"]`（`architect.py:257-262`）以「（每卷）」为单位定义了 4 类节拍配额，
但没有定义**卷与卷之间必须不同**。8 卷 × 「≥3 Boss/≥3 爆点/≥5 情绪/≥2 回忆」＝ 一张可以完全复制 8 次的清单。
这是"同一个东西重复 N 次"在 prompt 层的第一处硬证据。

### 1.3 架构到底能不能支撑 1500 章？—— 不能，有三处硬阻断

**阻断 ①：卷蓝图没有 schema 承载「扩张」。**
`architect.py:2070-2078` `_BLUEPRINT_BLOCK`（ultra 多卷必做，逐字）：

```
【全书剧情蓝图·ultra 多卷连载必做】volumes 数组每卷（除 no/title/goal/knowledge_direction 外）必须给出剧情蓝图：
"arcs":[{"name":"该卷剧情线名","description":"≤60字","est_chapters":本卷内该线约章数,"beats":["关键节拍1句话"](6~12个)}],
"fs_plan":{"plant":["本卷新埋伏笔简述"],"resolve":["本卷回收伏笔描述"]},
"ending_hook":"卷末悬念（终卷写结局收束）","milestones":["本卷兑现的主线里程碑"]}。
```

字段全集 = `{no, title, goal, knowledge_direction, arcs, fs_plan, ending_hook, milestones}`。
**没有 location / region / geography / faction_spread / antagonist_tier 任何一项。**
一张 8 卷蓝图里，"故事发生在哪里"这个信息在结构上无处安放。

**阻断 ②：`arcs` 被显式限定为「第 1 卷」。**
`story_director.py:26-28`（逐字）：

```
- arcs 剧情线：普通档 3~6 条、ultra 档 6~10 条（ultra 至少 2 条主线级 core + 2 条人物关系/情感线 + 2 条对立/探索/局势线），
  每条普通档 3~6 个、ultra 档 8~20 个 beats；【首卷边界】arcs 是**第 1 卷**的剧情线，
  每条线 est_chapters 合计 ≈ 第 1 卷章数（ultra 300~500 / grand 100~300 / 其它 1 卷），beats 只能铺到首卷卷末（卷末用钩子/悬念收束）；
```

再加上 `_guard_vol1_end()`（`story_director.py:64-90`）会把 beats 尾部的终局事件**代码级截断**，
把 `最终决战/牺牲/继承/殉道/献祭/真相大白/推翻天道/改写规则` 等 18 个关键词（`story_director.py:57-61`）从首卷 beats 里切掉。
于是第 1 卷的结构信息被**双重压缩**到「首卷之内」。

**阻断 ③：跨卷继承在代码里被硬编码为不支持。**
`pipeline.py:3024` `_load_filled_chapters_before`（Rolling Detail 的跨段继承源）：

```python
        if _safe_int(c.get("volume"), 1) != 1:
            continue
```

**跨段剧情继承只对第 1 卷生效。** 第 2 卷起的细纲补写拿不到任何前文 plot。

### 1.4 实证：真实 ultra 书的卷蓝图长什么样

`book 24`（ultra，3 卷规划，447 章首卷骨架）实际落库的 volumes：

```json
[{"no":1,"title":"首次记忆剥夺","vol_goal":"为救苏灵月施展禁术，失去关于"道心之始"的关键记忆，被宗门通…",
  "goal":"…（同上，别名）","main_line":"","vol_end_hook":"推向「首次记忆剥夺」的下一个阶段","toward":"m1"},
 {"no":2,"title":"身份初次暴露与宗门大比","vol_goal":"在云岚宗外门大比中为救人被迫展露真实实力（冰山一角），引起宗…",
  "main_line":"","vol_end_hook":"终局悬念：卷末悬念：记忆迷宫入口（楚渊找到进入记忆迷宫的方法，但需牺牲一段关键记忆，他犹豫是否踏入）",
  "toward":"m2","blueprint":"missing"},
 {"no":3,"title":"秘境探秘与记忆碎片","vol_goal":"进入上古秘境，获得蕴含记忆碎片的"溯魂晶"，部分恢复关于上界…",
  "main_line":"","vol_end_hook":"推向「秘境探秘与记忆碎片」的下一个阶段","toward":"m3","blueprint":"missing"}]
```

观察：
- `main_line` **全卷为空**（模型没填，代码也不校验）；
- 卷 1 与卷 3 的 `vol_end_hook` 都是**模板生成**「推向「<卷名>」的下一个阶段」—— 来自 `story_director.py:154` 的兜底卷构造；
- 卷 2、3 标 `blueprint:"missing"` → 续卷时降级为 `StoryArcPlannerAgent` **临时重新设计**（§7）；
- 三卷的 `vol_goal` 全是「获得/暴露/恢复某个东西」，**没有任何一卷的目标是"到达一个新地方"或"对抗一个新层级的敌人"**。

`book 18` 是唯一给出较丰富蓝图的（4 卷，各带 5~6 条 arcs）：

```
vol1 《代价初显》 arcs=6 「利用前世记忆规避初期死劫，发现魂祭代价显化，筑基后被宗门察觉异常…」
vol2 《囚笼与叛逆》arcs=5 「在追杀中壮大势力，深入接触魂祭真相与鬼王宗阴谋，在各大势力间周旋求存…」
vol3 《因果反噬》 arcs=5 「闯入葬神渊秘境，历经考验，揭开魂祭法则的核心真相…」
vol4 《大道殉道》 arcs=5 「联合一切力量，最终对决裴烬与背后的扭曲规则，以身殉道…」
```

**这正是应有的形态**（卷 3「闯入葬神渊秘境」就是一次地图扩张），但：
① 它是模型**自发**给的，不是被要求的 —— `book 24` 就没给；
② 它只覆盖 4 卷 × 350 章 = 1400 章，而蓝图字段里**没有任何东西强制"vol3 必须换地图"**；
③ 生产上 `book 18` 的 `phase=interrupted`、350 章全写完但**从未进入第 2 卷**（`current_volume=1`）。

---

## 二、大纲流水线全链路

### 2.1 全链路（story 模式，ultra 档）

```
①  StoryBrief（用户意图 + 题材 + 流派）
       │  story_genres.py(18题材) / story_flows.py(18流派) / story_random.py(发散池)
       ↓
②  StoryDirectorAgent.run()            ← 七锚骨架（唯一的长篇结构设计点）
       │  产出：title/logline/theme/dramatic_question/tone/protagonist/antagonist/
       │        ending_promise/golden_opening/milestones/acts/character_arcs/
       │        ladder/factions/golden_finger/pace_curve/volumes/arcs
       │  + _clean_volumes() 补齐到 ≥3 卷（缺失卷用 milestones 名兜底）
       │  + _guard_vol1_end() 截断首卷 beats 里的终局事件
       │  + _clean_milestones() 截到 8 个 ← ⚠ 与 ultra 闸门要求 ≥9 矛盾（§4.6）
       ↓
③  WorldBuilderAgent → bible.txt       ← 世界观圣经（Rolling 模式下前置到 ②之后、③之前）
       ↓
④  ArchitectAgent.run(scale="ultra")
       │
       ├─ _gen_segmented()             ← ultra/grand 走这条（story seg_table: grand=30, ultra=50）
       │    │
       │    ├─【第①步】全局预规划（1 次 LLM，reasoning 8000）
       │    │     prompt: "【分段规划·第一步·全局预规划】chapters 输出空数组 []。
       │    │              照常输出 arc/total_volumes/volumes/foreshadowings/plot_nodes，
       │    │              并额外输出 segments：把第 1 卷切成 {n_min}~{n_max} 个剧情段落
       │    │              （每段约 {seg_size} 章），每段 {"title","goal","chapters"}"
       │    │     ultra: lo,hi,seg = 300,500,50 → n_min=6, n_max=10
       │    │     产出: pre = {arc, total_volumes, volumes, segments, foreshadowings, plot_nodes,
       │    │                 segment_plan_source}
       │    │     兜底: _synthesize_segments()（零 LLM 纯算术）+ _segment_goals_from_preplan()
       │    │     落盘: _outline_partial.json  stage="pre"
       │    │
       │    ├─【第②步】分段骨架（6~10 次 LLM，**恒串行**，reasoning 8000）
       │    │     prompt: "【分段规划·第二步·段 {si}/{n}】生成第 1 卷第 {n} 章的骨架。
       │    │              段落目标：{title} — {goal}{prev_tail}{pts_line}{fs_line}
       │    │              只输出 JSON：{"summary":"本段剧情梗概","chapters":[
       │    │                {"idx","volume","title","points","participants","beat","plant_fs","resolve_fs"}]}
       │    │              idx 从 1 连续编号到 {n}，严禁输出 plot 字段。"
       │    │     段间交接: build_segment_handoff(prev_chapters, tail_count=5, summary=段梗概)
       │    │     落盘: stage="segments" + seg_parts{段号:章节}  每段一次
       │    │     回调: on_segment_done(SkeletonSegmentReady)  →  Rolling Detail 队列
       │    │
       │    └─【第③步】细纲补写 _fill_plots（N 批 LLM，批大小由预算反算 ⇒ 当前 3 章/批）
       │          prompt: "为每章补写五个字段：plot(≤100)/end_hook(≤40)/
       │                   plot_detail(900~2000，分场景拍点表 4~8 行，每行「场景/人物：目标→阻碍→结果」)/
       │                   entry_state/exit_state（【地点·时间·在场人物·谁知道什么】）"
       │          group_size=3（组间串行、组内并行）；Rolling 下强制 1
       │          落盘: stage="plots"，每批一次
       │
       └─ 返回 pre + chapters
       ↓
⑤  _finalize_outline()  →  book.outline 整份写库，phase=awaiting_confirm
       ↓
⑥  _produce_first() → 圣经 + 首章（必审） → _produce_serial() 逐章串行
       ↓
⑦  全部章 done → _finalize_book() → 若 current_volume < target_vols → _next_volume_inline()  → §7
```

### 2.2 各阶段 LLM 调用次数（ultra，第 1 卷 300~500 章）

以 `book 24`（447 章 / 14 段 / plot_detail p50 788）的真实配置反推：

| 阶段 | 调用次数 | 说明 |
|---|---|---|
| StoryDirector（七锚） | 1（+0~1 补线） | `story_director.py:395` 剧情线不足时补线小 prompt |
| **卷蓝图预规划** | **1** | `_gen_segmented` 第①步 |
| **分段骨架** | **9** | `⌈400/50⌉ = 8` 段 + book24 实际 14 段（`_normalize_segments` 拆分后） |
| **细纲补写** | **≈150** | 447 章 ÷ 批大小 3 |
| 圣经 | 1~2 | WorldBuilder + BibleReviewer |
| 正文（每章） | ≥1 Writer + 抽样 Editor | 首 3~5 章与 boss/climax/turning 必审 |
| **第 1 卷大纲合计** | **≈161 次** | 不含正文 |

**全书 1500 章（4 卷）**：卷 2~4 走 §7 的续卷路径，每卷 ≈ 卷蓝图 1 + 骨架 8 + 细纲 150 ≈ 159 次，
即 **≈ 640 次大纲调用 + 1500 次正文调用**。

### 2.3 真实 token 成本（`learnstory.db` 实测，非估算）

`generation_attempts` join `generation_traces`，按 agent 汇总：

| book | 章数 | architect (n / total tokens) | writer | editor | verifier | memory | **全书合计** |
|---|---|---|---|---|---|---|---|
| 23 | 400（+47 正文） | **255 / 3,929,145** | 124 / 3,002,398 | 89 / 2,848,638 | 52 / 898,699 | 10 / 452,976 | **≈ 11.9 M** |
| 24 | 447 骨架（+12 正文） | 54 / 801,336 | 24 / 387,840 | 33 / 611,775 | 5 / 42,019 | 7 / 102,485 | **≈ 1.99 M** |
| 18 | 350（+29 正文） | — | 2 / 27,875 | 3 / 31,771 | — | — | ≈ 0.06 M |

**book 23 的大纲 token 占比：architect 3.93 M / 全书 11.9 M ≈ 33%**（且 reasoning 独占 2.49 M = 63%）。

**1500 章外推**（按 book 23 的 400 章大纲 3.93 M 线性放大，正文按 1500 章 × 约 30 k/章）：
- 大纲：3.93 M × (1500/400) ≈ **14.7 M tokens**
- 正文+审校：book 23 的 47 章正文链消耗 ≈ 7.9 M → 1500 章 ≈ **250 M tokens**
- **全书 ≈ 2.6 亿 tokens**，其中**大纲只占 6%**。

**这个比例本身就是结论**：预算压在大纲上的钱少得可怜，
而抱怨 #5 恰恰是大纲层（结构）的问题。重写时在大纲层多花 3~5 倍 token 换取结构密度，
相对正文预算是**数量级上的廉价**。

---

## 三、大纲数据结构（真实落库形态）

### 3.1 顶层：`book.outline`（`book 23`，400 章，真实 keys）

```json
{
  "title": "...", "logline": "...", "theme": "...", "tone": "...",
  "arc": "三幕式剧情弧线(≤150字)",
  "total_volumes": 5,
  "volumes": [ /* 见 §1.4 */ ],
  "segments": [ /* 见 3.3 */ ],
  "segment_plan_source": "model",
  "foreshadowings": [ /* 20 条，见 3.4 */ ],
  "plot_nodes": [ /* 2~4 个，见 3.5 */ ],
  "chapters": [ /* 400 条，见 3.2 */ ],
  "chapter_count": 400,
  "mode": "series"
}
```

`book 24` 的 `_outline_partial.json` 顶层（断点快照 v2，`architect.py:589-627`）：

```json
{"stage":"plots","vol":1,
 "pre":{"title","arc","total_volumes","volumes","segments","foreshadowings","plot_nodes","segment_plan_source"},
 "chapters":[...447...],
 "seg_parts":{"1":[...50章...],"2":[...],...,"14":[...]}}
```

### 3.2 章级：`chapters[]` 的完整字段（`book 24` 实测，447 章）

key 全集（union of all chapters）：

```
['beat','end_hook','entry_state','exit_state','idx','participants',
 'plant_fs','plot','plot_detail','points','resolve_fs','title','volume']
```

字段填充率（**book 24 是 ultra，447 章骨架，仅前 208 章完成细纲**）：

| 字段 | 出现 | 占比 | 写入者 |
|---|---|---|---|
| `idx` / `volume` / `title` / `beat` / `participants` | 447 | 100% | Skeleton（`_gen_seg`） |
| `plot` | 208 | 46.5% | Detail（`_fill_plots`） |
| `plot_detail` | 208 | 46.5% | Detail |
| `end_hook` | 206 | 46.1% | Detail |
| **`entry_state` / `exit_state`** | **58** | **13.0%** | Detail（模型常省略） |
| `plant_fs` / `resolve_fs` | 125 / 87 | 28% / 19% | Skeleton |
| `points`（知识点，story 恒空） | 10 | 2.2%（**违规残留**） | Skeleton |
| **`milestone_ids`** | **0** | **0%** | **prompt 要求但从未落地** |

> ⚠ `STORY_SYSTEM_TEXT:919` 明文要求「每章可在 `milestone_ids` 标注本章推进的里程碑（无推进的章不得连续超过 3 章）」，
> `book 23`/`book 18` 有该字段，但**代码里没有任何地方校验、消费或统计 `milestone_ids`**。
> grep 全 `backend/app` → 仅 `schemas.py` 的 normalize 透传，零校验。这是一条**写完即弃的 prompt 要求**。

### 3.3 段级：`segments[]`（`book 24` 真实 14 段，节选）

```json
{"title":"血契初启","goal":"楚渊为救师妹提前施展禁术，付出失去「道心之始」记忆的代价，被宗门通缉…",
 "chapters":50,
 "summary":"本段（第1-50章）是《初次记忆剥夺》的开篇：楚渊在宗门大比前夜为救被邪修重伤的师妹…",
 "summary_source":"model"}
```

字段：`{title, goal, chapters, summary, summary_source, goal_source}`。
- `summary` 目标 240 字（`SEGMENT_SUMMARY_TARGET_CHARS`）、硬上限 1200 字（`SEGMENT_SUMMARY_MAX_CHARS`）
- `summary_source` ∈ `{model, missing, non_string, plan_unavailable, plan_split_tail}`（诚实标记，`architect.py:1137-1193`）
- `goal_source` ∈ `{model, synthesized}`

**`book 24` 的 14 段出现了明显的「同一 goal 被拆成多段」**：

| 段 | 章范围 | 章数 | title | goal（截断） |
|---|---|---|---|---|
| 2 | 51-100 | 50 | 伪装潜入 | 楚渊为查清真相，伪装成低阶散修潜入云岚宗… |
| **3** | **101-107** | **7** | **伪装潜入** | **（与段 2 逐字相同）** |
| 4 | 108-157 | 50 | 锋芒初露 | 云岚宗大比开幕，楚渊为救人不得不展露… |
| **5** | **158-162** | **5** | **锋芒初露** | **（与段 4 逐字相同）** |
| 6 | 163-212 | 50 | 藏经阁秘辛 | … |
| **7** | **213-227** | **15** | **藏经阁秘辛** | **（与段 6 逐字相同）** |
| 8/9 | 228-282 | 50/5 | 血影教疑云 | （同） |
| 10/11 | 283-337 | 50/5 | 风暴前夜 | （同） |
| 12/13 | 338-397 | 50/10 | 记忆的齿轮 | （同） |
| 14 | 398-447 | 50 | 卷末·新旧交替 | … |

**成因**（`architect.py:845-890` `_normalize_segments`）：
模型给的原始段是 8×50=400 章，`_normalize_segments` 因总量不匹配上限做了**扩量再拆分**，
拆分时故意只让第一个子段继承 `summary`（其余标 `plan_split_tail`），
但 **`title`/`goal` 是按设计共享的**（`_child()` 注释：「过大的段拆成多个子段（共享 title/goal，保证衔接连贯）」）。

后果：段 2 与段 3、段 4 与段 5 …… **每对子段拿到完全相同的「段落目标」指令**，
而 `_gen_seg_inner` 把这句话原文注入 prompt（`architect.py:2342`）：

```python
f"段落目标：{seg['title']} — {seg['goal']}{prev_tail}{pts_line}{fs_line}\n"
```

**这正是 AGENTS.md 记载的「书 1005：6 段 goal 完全相同 → 29 组重复标题」的同型缺陷，
在 2026-09-11 的修复之后，以「拆分子段共享 goal」的新形态复现。**

### 3.4 伏笔登记表：`foreshadowings[]`（`book 24`，20 条）

```json
{"fs_key":"苏灵月隐藏的灵根","description":"苏灵月苏醒后，其灵根波动出现微弱异常，似乎对"溯魂"之力有亲和性，但她本人并不知情。",
 "kind":"贯穿","weight":9,"plant_ch":1,"resolve_ch":400}
```

配额由 `calc_foreshadow_quota()`（`architect.py:311-332`）按**典型章数**算：

```python
typical = {"fast": 6, "standard": 30, "grand": 175, "ultra": 450}
total = vol_chapters or typical.get(scale, 30)
return {
    "short":     max(2, math.ceil(total / 12)),   # ultra: 38
    "mid":       max(1, math.ceil(total / 40)),   # ultra: 12
    "long":      max(1, math.ceil(total / 100)),  # ultra: 5
    "spanning":  1,
    "total":     38 + 12 + 5 + 1,                 # = 56
}
```

**实测硬缺口**：`book 24` 的 `vol_chapters` 用的是 `typical_chapters=400`（估）而非实际 447，
配额算出 short=34/mid=10/long=4/spanning=1 = **49 条**，而模型只给了 **20 条**（40%）。
**没有任何闸门校验伏笔配额**——`outline_lint.py` 只查细纲，`ultra_structure.py` 只查 `milestones/factions/arcs`。

### 3.5 主线节点：`plot_nodes[]`（`book 24`，4 个）

```json
{"title":"禁术初施，记忆为祭",
 "summary":"楚渊为救苏灵月，于绝魂渊边缘施展禁术"溯魂逆生"，成功逆转其致命伤与丹田损毁，但自身永久失去关于"道心之始"的关键记忆，并立即遭到玄天宗通缉…",
 "chapters":[1,2,3]}
```

**schema 漂移**：`SYSTEM_TEXT:368` 要求的字段是 `{node_key, node_type, description, target_ch}`，
模型实际给的是 `{title, summary, chapters[]}` —— **四个字段名全部不符**，
而 `_segment_goals_from_preplan`（`architect.py:772-779`）读的是 `description`/`node_type`：

```python
for node in (pre.get("plot_nodes") or []):
    if isinstance(node, dict):
        desc = str(node.get("description") or node.get("node_type") or "").strip()
        if desc:
            cands.append(desc[:60])
```

→ `book 24` 的 `plot_nodes` 全部读不出 `description` → `cands` 为空 →
`_segment_goals_from_preplan` 落到最后一个兜底分支（`architect.py:794-797`）：

```python
else:
    pos = "开篇立局" if i == 0 else ("中段加压" if i < n - 1 else "收束并导向下一段")
    goals.append(f"第 {i + 1}/{n} 段（{pos}）：承接上一段结果推进主线，落实本段冲突与转折")
```

**这正是书 1005 那句被删除的「写死同一句」的等价物**，只是加了段号后缀让它「两两不同」。
`book 24` 的 `segment_plan_source="model"` 掩盖了这一点 —— 因为 `goal` 确实来自模型的 segments，
但**拆分出的子段共享 goal**（§3.3），效果与 synthesized 兜底等价。

### 3.6 节拍分布（`book 24` 实测，447 章）

```python
{'normal': 196, 'emotion': 91, 'turning': 53, 'climax': 45, 'boss': 41, 'recall': 21}
#  占比: 43.8%      20.4%        11.9%        10.1%       9.2%      4.7%
```

对照 `PACING_RULES["ultra"]`「每卷 ≥3 boss / ≥3 climax / ≥5 emotion / ≥2 recall」：
**配额是「每卷 ≥」的绝对下限，而模型产出的密度远远超过下限**，
说明配额约束**完全不起作用**（447 章里 boss 41 个，配额只要求 3 个）。
真正的问题是**分布**：`normal` 占 43.8%，且 `story_health.py:92` 只在「连续 ≥12 章无强节拍」时才报 issue。

---

## 四、结构缺陷根因（逐条带证据）

### 4.1 `_synthesize_segments` 写死同一句 goal —— ✅ 已修，但以新形态残留

**AGENTS.md:227-229 原文**：

> `* **分段规划不许再写死同一句（2026-09-11 修，A4/D1）**：`architect._synthesize_segments` 是模型没给`
> `  `segments` 时的确定性兜底，原先把 `goal` 写成同一句「沿主线推进本段剧情…」，而这句话**就是喂给模型的每段指令**`
> `  （`段落目标：{title} — {goal}`）。书 1005 的 6 段 goal 完全相同 → **29 个重复章节标题、覆盖 65/300 章**。`

**当前代码**（`architect.py:756-805`）已改为从 `plot_nodes`/`milestones` 派生 + 去重保险。
**但 §3.3 + §3.5 证明这条病在 2026-09-13 的 book 24 上以两种新形态复发**：
① `_normalize_segments` 拆分子段共享 `goal`（段 2/3、4/5、6/7 … 逐字相同）；
② `plot_nodes` schema 漂移导致派生源全空 → 落到带段号的兜底句。

**实测残留**：`book 24` 447 章里有 **16 组重复标题、覆盖 33 章**：

```
审判降临 ×3                 → idx [88, 207, 342]
旧伤与新痕 ×2               → [18, 416]
柳清尘的秘密 ×2             → [23, 64]
烬灭壁画 ×2                 → [67, 142]
赵雪的坦白 ×2               → [83, 233]
烬灭残响 ×2                 → [132, 172]
记忆的代价 ×2               → [133, 328]
…
壁画史诗 ×2                 → [302, 303]   ← 相邻两章同名
苏灵月的梦境 ×2             → [327, 412]
```

### 4.2 无条件 `segs = []` —— ✅ 已修（墓碑注释保留）

**AGENTS.md:233-237**：

> `  - ⚠️ **真正的根因是另一行（2026-09-11 删）**：`_gen_segmented` 的重试循环之后曾有一行`
> `    **无条件** `segs = []`（首卷；续卷那处在 `else:` 分支体内、合法，**不要一起删**），它紧跟在`
> `    `else: segs = []` 之后、位于 try 块之外，于是**每一次**预规划都被清空`

现场墓碑（`architect.py:2193-2202`）完整保留，含「删掉这一行后『模型分段规划』这条契约才真正生效」。
**验证**：`book 24` 的 `segment_plan_source="model"`，`segments` 有 14 段 → 契约已生效。

### 4.3 `plot_detail` 过薄（p50 179 vs 允许 650）—— ✅ 已修

**AGENTS.md:192-205** 记录了完整的预算算术：

```
client.py 对显式思考控制的调用设 max_tokens = max(4096, reasoning*4)，思考与正文共用这个上限；
补写批 reasoning_max_tokens=fill_reasoning_tokens。所以可用 JSON 预算 = 4x - x = 3x。
旧代码用 12000 // per_ch_tokens 反算批大小，把思考也算成 JSON 空间，高估 1/3
→ 批内写不下 → 模型自行把每章细纲压短。实测书 1005 的 300 章细纲 p50 仅 179.5 字（指令允许 650）。
```

修复代码（`architect.py:1294-1309`）：

```python
FILL_OUTPUT_MULTIPLIER = 4

def fill_batch_output_budget(r_tokens: int) -> int:
    """单批可用于**正文 JSON** 的输出 token（已扣掉思考预算）。"""
    total = max(4096, int(r_tokens) * FILL_OUTPUT_MULTIPLIER)
    return max(1, total - int(r_tokens))

def fill_batch_size_for(per_ch_tokens: int, r_tokens: int, requested: int) -> int:
    """按**真实可用**输出预算反算批大小。
    **故意不设下限地板**（旧代码是 `max(5, ...)`）…
    真正的最小值是 1（逐章补写）—— 单章写得厚，比一批写得全都薄更有价值。
    """
    per_ch = max(1, int(per_ch_tokens))
    budget = fill_batch_output_budget(r_tokens)
    return max(1, min(max(1, int(requested)), budget // per_ch))
```

调用点（`architect.py:1605-1606`）：`per_ch_tokens = 110 + int(pd_chars * 0.8)`（pd=2000 → 1710）

**实测效果（`book 24`，208 章有细纲）**：

| 指标 | min | p25 | **p50** | p75 | p90 | max | mean |
|---|---|---|---|---|---|---|---|
| `plot` (≤100 允许) | 38 | 72 | **85** | 108 | 130 | 177 | 91.1 |
| **`plot_detail`**(900~2000 允许) | 390 | 635 | **788** | 936 | 1123 | 1691 | 816.2 |
| `end_hook` (≤40 允许) | 18 | 35 | **43** | 52 | 65 | 89 | 44.7 |
| `entry_state` | 36 | 58 | **84** | 103 | 122 | 140 | 82.8 |
| `exit_state` | 45 | 66 | **104** | 121 | 148 | 168 | 99.0 |

**对比书 1005：p50 179.5 → book 24 p50 788，提升 4.4 倍。修复有效。**
但注意：**p50 788 仍低于配置下限 900，min 390 只有下限的 43%** ——
`plot_detail_min_chars=900` 是「判不合格 → 定向重补」的阈值，**但没有任何自动重补触发**（§8.3）。
另一个信号：**只有 159/208 章（76%）的细纲含 ≥1 行带 `→` 的场景行**，
`p50 = 4` 行（要求 4~8 行），**说明「分场景拍点表」格式只有约一半被遵守**。

### 4.4 批大小错误 —— ✅ 已修，但引入新瓶颈

配置 `backend/outline_config.json`：`{"fill_plots_batch_size":3, "plot_detail_chars":2000, "plot_detail_min_chars":900}`

代入公式：`per_ch = 110 + 1600 = 1710`；`budget = max(4096, 3000×4) − 3000 = 9000`；
`batch = min(requested=3, 9000 // 1710 = 5) = **3**`。

**AGENTS.md:200 的对照表说「pd=2000→5 章」，但实际是 3** ——
因为 `fill_plots_batch_size` 配置值本身是 3，成了**新的地板**（配置项取代了被删掉的 `max(5,…)`）。
这意味着**用户的 `plot_detail_chars` 调到 2000 时，批大小 3 是配置决定的，不是预算决定的**。
细纲想再厚到 3000（`per_ch=2510`，预算允许 3 章）时刚好打平，**再往上调就会重新越预算**。

### 4.5 段/批边界质量断崖（实证）

按 `book 24` 的 14 段边界统计每段的质量指标：

| 段 | 章范围 | 章数 | 有 plot | pd p50 | pd min | **有 entry_state** | 有 end_hook |
|---|---|---|---|---|---|---|---|
| 1 | 1-50 | 50 | 50 | 636 | 390 | **50** | 50 |
| 2 | 51-100 | 50 | 50 | 792 | 466 | **6** | 48 |
| 3 | 101-107 | 7 | 7 | 910 | 812 | **0** | 7 |
| 4 | 108-157 | 50 | 50 | 830 | 433 | **0** | 50 |
| 5 | 158-162 | 5 | 5 | 888 | 749 | **0** | 5 |
| 6 | 163-212 | 50 | 46 | 864 | 459 | **2** | 46 |
| 7-14 | 213-447 | 235 | **0** | — | — | 0 | 0 |

**`entry_state` 的填充率从段 1 的 100% 断崖式跌到段 2 的 12%、段 3 起归零。**
这不是模型随机波动 —— 段 1 是「首批」（`prompt` 里 `_req_chapter` 首次下发 `entry_state` 要求），
后续批次模型逐渐省略该字段，而代码「模型给了就落，缺了不判失败」（`architect.py:1858-1861`）：

```python
# 连续性状态字段：模型给了就落，缺了不判失败（lint 会单独报）
for _k in ("entry_state", "exit_state"):
    if got.get(_k):
        c[_k] = got[_k]
```

而 `lint` 默认 **shadow 不阻断**（`outline_config.py:57` `detail_lint_enabled: False`）。
→ **「章间状态连续性」这条最重要的长篇不变量，在 447 章里只有 58 章（13%）真的被记录，
且完全无人拦截。** 这直接对应抱怨里的「过于啰嗦和 AI」——没有状态锚，写作只能靠惯性复述。

**段 6 的 `46/50`**：有 4 章连 plot 都没补上（idx 197 与 199 之间有一个缺口，即 idx 198）。
`_fill_plots` 的 3 轮重试跑完仍未补齐，只报了「⚠️ 仍有 N 章 plot 为空」。

### 4.6 `min_milestones ≥ 9` 与 `_clean_milestones` 截到 8 —— ⚠ 活着的逻辑矛盾

| 位置 | 值 |
|---|---|
| `ultra_structure.py:29` `DEFAULTS["min_milestones"]` | **9** |
| `backend/ultra_structure_config.json` `"min_milestones"` | **9** |
| `AGENTS.md:161`「≥9 里程碑」 | **9** |
| `story_director.py:494` `for i, m in enumerate(ms[:8], 1)` | **8** ← 先截断 |
| `story_director.py:16` prompt「ultra 档 9~12 个」 | 9~12 |

`StoryDirectorAgent.run()` 的清洗顺序（`story_director.py:285-290`）：

```python
result["golden_opening"] = self._clean_golden(result.get("golden_opening"))
result["milestones"]    = self._clean_milestones(result.get("milestones"))   # ← 截到 8
result["acts"]          = self._clean_acts(result.get("acts"))               # ← 截到 4
result["character_arcs"]= self._clean_char_arcs(result.get("character_arcs"))# ← 截到 6
result["ladder"]        = self._clean_ladder(result.get("ladder"))           # ← 截到 10
result["factions"]      = self._clean_factions(result.get("factions"))       # ← 截到 8
```

然后 `story_director.py:445-461` 才调 `enforce_ultra_structure` 校验。

**结论：在 `enabled=true` 的配置下，`milestones` 门槛 `≥9` 在结构上永远无法满足**（上限 8）。
`ultra_structure_repair.py:264-272` 会去打补丁：

```python
if kind == "milestones":
    need = max(0, _as_int(cfg.get("min_milestones"), 9) - _as_int(metrics.get("milestone_count"), 0))
```

补出来的 milestone 会追加到 `result["milestones"]`（绕过 `_clean_milestones`），
既绕过截断也无从复现 —— 而生产 `enabled=false, shadow=true`（`ultra_structure_config.json`），
**所以这条矛盾被 shadow 模式掩盖，从未在生产暴露。** 重写时若不注意，开启闸门即炸。

### 4.7 `acts` / `conflict_curve` / `character_arcs` —— 要求了但从不校验

`SYSTEM_TEXT:413` 要求输出 `conflict_curve`：

```json
"conflict_curve": [{"volume":1,"act":"开端","start_ch":1,"end_ch":10,"description":"..."}]
```

`SYSTEM_TEXT:389-391` 要求：

```
13.【冲突曲线 conflict_curve】每卷标注起承转合节点：act 取 开端/发展/高潮/结局，
   start_ch/end_ch 为该阶段的章节范围（卷内序号），description 简述该阶段核心冲突与转折。
   每卷至少 3 个阶段（开端/发展/高潮），超长篇每卷 4 个阶段（含结局）。
```

grep 全仓 `conflict_curve`：

```
architect.py:413   （SYSTEM_TEXT 的输出格式示例）
```

**仅此一处。** `book 23`、`book 24` 的 outline 里都没有 `conflict_curve` 键。
同理 `character_arcs`（`SYSTEM_TEXT:392-395`，要求主角 ≥2 个转变点）：
`architect.py:414` 是唯一出现处，`book 24` 的 outline 里没有该键。

**这两个字段（三幕节拍 + 角色弧光）是长篇结构的骨架，prompt 里写了、输出格式给了，
但代码既不校验、也不落库、更不注入下游。** 这是 §4「结构缺陷」中最贵的一条：
**模型被要求做三幕式设计，但做与不做对系统没有任何区别。**

### 4.8 `_guard_vol1_end` 的反作用

`story_director.py:57-61` 的终局关键词表：

```python
_VOL_END_KEYWORDS = (
    "最终决战", "终极决战", "最终对决", "终极对决", "最终仪式", "终极仪式",
    "牺牲", "继承", "殉道", "献祭", "归于", "永困", "归墟之门永闭",
    "真相大白", "天道崩", "推翻天道", "动摇天道", "救世之局", "改写规则", "成为守门人",
)
```

`_guard_vol1_end`（`story_director.py:64-90`）从 beats **尾部连续命中**处截断。
这个设计的初衷正确（防首卷写完结局），但**它把截下来的终局语义塞进 `vols[1].vol_end_hook`**：

```python
_tgt = by_no.get(2) or by_no.get(1) or (vols[0] if vols else None)
if _tgt is not None:
    _tgt["vol_end_hook"] = f"终局悬念：{tail_text}"
```

`book 24` 卷 2 的 `vol_end_hook` 实际值：

```
"终局悬念：卷末悬念：记忆迷宫入口（楚渊找到进入记忆迷宫的方法，但需牺牲一段关键记忆，他犹豫是否踏入）"
```

**两个问题**：
① 前缀嵌套错误（"终局悬念：卷末悬念：" 双层）；
② **`vol_end_hook` 是 ≤30 字的字段（`story_director.py:127` `_clip(v.get("vol_end_hook"), 30)`），
   而这里写入的文本长达 55 字，绕过了 `_clip`** —— 因为它是在 `_clean_volumes` **之后**才被覆写的
   （`_clean_volumes` 在 `story_director.py:296`，`_guard_vol1_end` 在 `:441`）。
③ 更关键：**终局信息被写进第 2 卷的钩子，而第 2 卷又标 `blueprint="missing"`** →
   续卷时这个钩子会不会被用上取决于降级路径，实测 `book 24` 从未续卷（`interrupted`）。

---

## 五、长篇丰满度缺口分析（抱怨 #5 的根因）

### 5.1 结论先行

**系统对长篇只有「纵向连续性」这一条硬约束，横向扩张维度完全缺失，且在多处被显式禁止。**

### 5.2 证据链 A：那些**禁止**横向扩张的代码

| # | 位置 | 逐字原文 | 效果 |
|---|---|---|---|
| A1 | `architect.py:397` | 「每章 plot 必须是上一章情节的直接延续（同一事件/对话/调查/冲突的下一拍），**禁止每章自起自收（新场景开场+圆满收束）**」 | 禁新场景 |
| A2 | `architect.py:401` | 「相邻章时间线连续，**地点延续或自然过渡，禁止无交代的场景硬切或时间跳跃**」 | 禁地点跳变 |
| A3 | `architect.py:925` | （STORY_SYSTEM_TEXT 里的同款条款，story 模式生效） | 同上 |
| A4 | `architect.py:372` | 「ultra 档同一卷相邻章**必须场面连续**（不能上一章还在打、下一章无过渡已休息）；**卷末可为下一卷换场留一句理由**；卷首允许新场景」 | **换场只允许发生在卷末** |
| A5 | `writer.py:258` | 「禁止另起炉灶、禁止直接休息疗伤、**禁止无过渡换地图**」 | 写作层禁换图 |
| A6 | `editor.py:243` | 「时间/场景硬切词（次日/三日后/与此同时）与**无过渡换地图仍须改写**」 | 审校层把换图判为缺陷 |
| A7 | `writer_rules.py:374` | 「**禁止新增细纲之外的重要人物、重大转折、新功法/新设定/新地图**（细节笔触自由发挥不算加戏）」 | **细纲没有的新地图 = 违规** |
| A8 | `editor.py:106` | 「无中生有的重要人物、重大转折、新功法/新设定/**新地图**（剧情级加戏）→ 记「细纲偏离」」 | 换图被记为偏离 |

**A7+A8 是致命组合**：写作层禁止写细纲外的新地图，审校层把新地图判为「偏离」。
**而细纲（`plot_detail`）本身在结构上无法承载地图扩张** —— 它被要求写成
「场景/人物：目标→阻碍→结果」的**当章事件表**，且 prompt 明令「末行停在当前事件未收束的拍上」。

### 5.3 证据链 B：唯一允许换图的地方，都是**卷级**且是**例外条款**

grep 全 `backend/app` 的 `换地图|新场景|新地图|地理|势力扩张` 结果（37 命中，去重后）：

| 位置 | 原文 | 性质 |
|---|---|---|
| `writer.py:192` | 「若注入【**本卷开篇**允许换场景】，可用一两句交代间隔后换地图/时间」 | 卷首例外 |
| `writer.py:443` | 「可用一两句交代间隔后**换地图、换时间、开新冲突**」 | 卷首例外 |
| `editor.py:230` | 「【**本卷开篇**允许换场景】换地图/时间跳跃**不视为跳戏**」 | 卷首豁免 |
| `writer_rules.py:318` | 「**卷末章**的钩子指向下一卷的核心冲突或**新地图**（**此为唯一例外**）」 | 卷末例外 |
| `post_processor.py:95` | 「**卷末章**的 hook 可指向下一卷核心冲突或新地图」 | 卷末例外 |
| `architect.py:372` | 「卷末可为下一卷换场留一句理由」 | 卷末例外 |
| `story_genres.py:73` | 「威胁等级随主角扩张持续升级」 | **题材描述文字，无代码消费** |
| `flow.py:65` / `story_flows.py:87,101,115` | 「势力版图扩张」/「扩张版图」 | **流派结构描述文字，无代码消费** |
| `continue_story.py:659,1285,1328-1329` | 「新势力/新地图/新规则」 | **仅续接扩写路径**（§7） |

**「卷级例外」的算术**：ultra = 3~6 卷 → **整本 1000~1700 章，允许换地图的机会是 2~5 次**（卷首）。
`book 23` 是 400 章 / 5 卷，即**每 80 章才有一次换场许可**。
抱怨里的「1500 章只在一个地方展开」，在结构上就是这么被生产出来的。

### 5.4 证据链 C：全链路没有任何「扩张」schema

对 `architect.py` 的所有 prompt 常量做 key 扫描：

| 常量 | 行 | 与空间/势力扩张相关的字段 |
|---|---|---|
| `SYSTEM_TEXT`（learn） | 351-414 | 无 |
| `STORY_SYSTEM_TEXT`（story） | 892-931 | 无（只有 `milestone_ids`，且实测 0% 落地） |
| `SCALE_RULES` / `PACING_RULES` | 226-263 | 无 |
| `STORY_SCALE_RULES` | 933-938 | 无 |
| `STORY_NON_TERMINAL_RULE` | 942-946 | 无（只禁提前结局） |
| `ULTRA_ENSEMBLE_RULE` | 958-961 | 「至少 2~3 条支线各有配属小团体（3~5 人）」← **最接近"势力扩张"的一条，但是人物维度不是地理维度** |
| `ULTRA_TOTAL_CHAPTERS_RULE` | 965-969 | 无 |
| `_BLUEPRINT_BLOCK` | 2070-2078 | 无 |

`scales.json` 的四档 rule + pacing 全文扫描 → **零个地理/势力/反派阶梯字段**。
`story_director.py` 的七锚 → `factions`（3~8 股）是**静态关系网**，没有层级、没有登场时点。

### 5.5 实证：真实大纲的「地理熵」测量

**方法**：从 447 章（`book 24`）/ 400 章（`book 23`）/ 350 章（`book 18`）的 `plot` + `title` 中
正则抽取「2~4 汉字 + 地点后缀（宗门渊城谷殿阁塔山域界岛海林村镇国府宫院教会族堂楼坊塞关洞窟原漠洲盟派境）」
的 token，比较**前半本 vs 后半本**的新增地点数。

| book | 章数 | 后半本独有的新地点 token（出现 ≥3 次） | 数量 |
|---|---|---|---|
| **24**（万界烬灭录·修仙） | 447 | **（无）** | **0** |
| **23**（值夜手册·规则怪谈） | 400 | `裴照隔门`(9) ← 非地名 | **1** |
| **18**（修仙·魂祭） | 350 | `与天衍宗`(6)、`烬与鬼王宗`(5)、`危急关`(4)、`前往葬神渊`(4)、`风纪殿`(3) | **5（且全是已登场势力的变体）** |
| **8**（宫廷） | 286 | `帝师府`(3) | **1** |

**`book 23` 是最刺眼的样本**（400 章，ultra）。它的高频"地点"token 是：

```
周德海(11) 裴照隔门(9) 电梯在四楼(5) 长明楼(5) 陈砚下楼(5) 他上楼(4) 电梯门(4)
老周隔门(4) 他推门(3) 全在长明楼(3) 陈砚开门(3) 陈砚隔门(3) 扇朝下的门(3)
不开门(2) 天亮门(2) 梯上五楼(2) 废弃二食堂(2) 负一层铁门(2) ...
```

**400 章的故事，场景 token 的绝对主体是「门 / 电梯 / 楼层 / 走廊」。**
`长明楼` 是全书唯一的地标（5 次），`电梯在四楼`/`他上楼`/`梯上五楼` 全是同一栋楼的内部移动。
这就是用户说的「1500 章的小说有的只在一个地方全程展开」。

**因果链闭环**：
`A1-A4`（章章必须无缝承接，禁新场景/禁地点跳变）+ `A5-A8`（写作与审校把换图判为缺陷）
+ `§5.4`（规划层没有扩张 schema）+ `§5.3`（换图只允许在卷首，2~5 次机会/全书）
→ **模型只能在同一地点内反复推进，用「更细的描写 / 更多的心理独白 / 更多的重复场景」填章数**
→ 这正是「过于啰嗦和 AI」的机械成因。

### 5.6 用户抱怨的三个症状 → 三个代码根因

| 症状 | 根因 | 证据 |
|---|---|---|
| 「只在一个地方全程展开」 | 无横向扩张 schema + 换图禁令 | §5.2 A1-A8、§5.3、§5.4、§5.5 |
| 「过于啰嗦」 | 细纲厚度不足 + 状态锚缺失 + 章节必须在原地打转 | §4.3（p50 788 < 900 下限）、§4.5（entry_state 仅 13%）、§5.5 |
| 「AI 味」 | 16 组重复标题、段级 goal 逐字复制、模板卷钩子 | §3.3、§4.1、§4.8 |

---

## 六、卷 / 段交接

### 6.1 `build_segment_handoff` 实际传了什么

`architect.py:1050-1120`，**确定性构造、零 LLM**。实际注入文本（按代码顺序）：

```
【上一段实际规划结果】段落「<seg_title>」，本段自述剧情梗概：
【上一段剧情梗概】<summary，目标 240 字 / 硬上限 1200 字>
【上一段实际规划结果】章节范围 第 X~Y 章（共 N 章）。
最近章节（下一段必须承接，不得另起互不相关的剧情线）：
  - 第X章《<title[:30]>》，节拍=boss，参与=A、B、C
  - ...（共 tail_count=5 章）
上一段主要出场人物（须保持其目标/关系/位置连续）：<按频次 top 8>
上一段已埋设伏笔（后续段须推进或可回收）：<fs_key 列表>
上一段已回收伏笔：<fs_key 列表>
必须从上述状态继续：保持人物、势力、伏笔与因果的连续性。
```

**传递量的硬上限**：

| 项 | 上限 | 位置 |
|---|---|---|
| 段落梗概 | 1200 字（目标 240） | `SEGMENT_SUMMARY_MAX_CHARS` `architect.py:1134` |
| 最近章节 | **5 章** | `tail_count: int = 5` `architect.py:1053` |
| 主出场人物 | 8 人（按频次） | `_frequent_names(chs, limit=8)` `architect.py:1030` |
| 参与人/章 | 6 人 | `names[:6]` `architect.py:1100` |
| 标题/章 | 30 字 | `[:30]` `architect.py:1094` |

**关键缺口：骨架阶段没有 `plot`**（`architect.py:1063-1064` 自己承认）：

> 骨架阶段章节只有 title/participants/beat/plant_fs/resolve_fs（plot 由后续 `_fill_plots` 补写、此处尚不存在），
> 因此**结构行**只传递能确定性拿到的信息，不臆造剧情。

**即：段与段之间传递的「故事内容」= 一段 240 字的自述梗概（约 447 章中的 8 行）。
50 章（约 15 万字正文）的信息被压缩成 240 字。压缩比 ≈ 600:1。**

### 6.2 细纲层的跨段继承 —— 双重截断

`architect.py:1749-1762`（`_fill_plots` 内的剧情继承）：

```python
first_pos = chapters.index(empties[0])
_prev_chapters = [c for c in (preceding_chapters or []) if isinstance(c, dict)]
prior_filled = [c for c in (_prev_chapters + chapters[:first_pos])
                if isinstance(c, dict) and (c.get("plot") or "")]
inherit_block = ""
if prior_filled:
    inherit_block = (
        f"\n【此前章节已写剧情（必须承接推进，禁止重复这些桥段/场景/套路）】\n"
        + "\n".join(
            f"第{c.get('idx')}章《{c.get('title', '')}》：{(c.get('plot') or '')[:80]}"
            for c in prior_filled[-40:]
        )[-2500:]
    )
```

**三重截断叠加**：
1. `prior_filled[-40:]` —— 只取**最近 40 章**（447 章的 9%）
2. 每章 `plot[:80]` —— plot 本身 ≤100 字，截到 80
3. `[-2500:]` —— **最终字符串尾部 2500 字符硬截断**

`40 章 × (约 20 字标题 + 80 字 plot) ≈ 4000+ 字符` → 被 `[-2500:]` 砍掉约 40%。
**实际生效的继承窗口 ≈ 最近 25 章。**

标题级防重 `titles_before = (...)[-45:]`（`architect.py:1771`）—— 最近 45 章标题。

**在 1500 章规模下，40 章窗口只覆盖 2.7% 的前文。**
伏笔的 `plant_ch=1 / resolve_ch=400` 意味着**第 1 章埋的伏笔在第 400 章回收时，
中间没有任何机制保证细节不丢** —— 只能依赖 `plant_fs`/`resolve_fs` 的 key 字符串。

### 6.3 Rolling 跨段继承源 —— 硬编码只支持第 1 卷

`pipeline.py:3024-3058`：

```python
def _load_filled_chapters_before(book_id: int, segment) -> list:
    """Rolling Detail 的跨段继承源：本段起点之前、已细化（有 plot）的章节。
    ...
    最多回溯 40 章，与 `_fill_plots` 的 `prior_filled[-40:]` 同口径。
    """
    ...
        if _safe_int(c.get("volume"), 1) != 1:
            continue
```

**`!= 1 → continue`：第 2 卷起的 Detail 补写拿不到第 1 卷的任何已细化章节。**

### 6.4 卷边界发生了什么 —— 实证

`book 11`（ultra，2 卷已完成，第 1 卷 6 章 / 第 2 卷 4 章）的卷 1 末章与卷 2 首章对比：

**卷 1 第 6 章末尾**（`storage/story/books/11/chapters/v1-6.txt`）：

> 她写下标题：【反噬应对与"异常"路径探索计划（草案）】
> …「这是悬崖边找路。」苏念安合上笔帽，「没地图，只能边走边画。」
> 「一起画。」顾屿点了点计划本上的箭头。
> …「明天，去个地方。旧图书馆天台，可能有我们需要的东西。」
> …**顾屿带她绕到背面，推开一扇虚掩的铁门。** 灰尘落下。楼梯间昏暗…
> 天台不大，水泥开裂，杂草丛生…「这里有什么？」苏念安问。顾屿走向〔正文在此截断〕

**卷 2 第 1 章开头**（`v2-1.txt`）：

> 路灯在远处投来昏黄的光，刚好切断顾屿没说完的那半句话。苏念安站在宿舍楼门口的阴影里…

**现象**：卷 2 首章**回退到了卷 1 末章的更早时点**（宿舍楼门口是卷 1 末章中段的位置），
然后 `***` 分隔、跳到「两天后，周六清晨」。

**这不是「承接」，是「重播 + 时间跳跃」。** 成因：卷 2 首章的写作上下文来自
`_next_volume_inline` 重新规划的本卷骨架，而卷 1 的 `exit_state` **不存在**（`book 11` 是 `fast` 档原始书，
`chapters` 里没有 `entry_state`/`exit_state` 字段）。唯一的桥接是 `hang_json`（上一章末尾状态）。
→ **卷边界缺少结构化状态交接，只能靠自然语言钩子，于是模型从最近记得的画面重写一遍。**

对比 `book 11` 全书 6 章大纲与 356 章目标（§7.2）—— 卷 1 只有 6 章却要展开 356 章，
**「重播开头」是唯一可行的续写策略**。

---

## 七、续卷机制（`continue_story.py`）

### 7.1 两条续卷路径，质量天差地别

| | **路径 A：`continue_story.py`**（用户点「续接扩写」） | **路径 B：`pipeline._next_volume_inline`**（自动续卷） |
|---|---|---|
| 触发 | `POST /api/books/{id}/continue` | `_finalize_book`（`pipeline.py:5651`） |
| 前置 | `phase == "completed"` 且 `scale ∈ {fast,standard,grand}` | `current_volume < target_vols` |
| 规划者 | **`extend_outline`**（裸 prompt，非 ArchitectAgent） | `_story_plan_volume` → `StoryArcPlannerAgent` |
| 用 bible？ | ✅ `extend_bible`（追加 800~1500 字新设定） | ✅ 注入圣经摘要 `bible[:1500]` |
| 用七锚？ | ❌ **完全不用** | ✅ 注入 milestones/ladder/factions/golden_finger |
| 章数粒度 | `ULTRA_CHAPTERS_PER_VOLUME = 500` 一次生成 | 本卷 300~500 |
| 落库 | 直接插 `Chapter` 行（pending） | 走完整 `_fill_plots` |

### 7.2 路径 A 的降级链（`continue_story.py:1011-1182`）

```python
def extend_outline(book, ending_info, breakpoint_info, target_chapters, new_chapters,
                   session, log, learn_direction=""):
    """扩展大纲：基于破局点设计新剧情线，生成新章节规划。

    注意：此函数在 Step 1 中使用简化版（直接生成章节规划），
    完整的 ArchitectAgent 续接模式在后续优化中完善。
    """
```

**函数 docstring 自己承认是「简化版」。** 其 prompt（`continue_story.py:1101-1124`，逐字）：

```python
system = f"""你是网文大纲策划师。为一部{genre_name}小说的超长篇续作生成章节大纲。

原结局类型：{ending_info.get('ending_type_label', '')}
破局模式：{breakpoint_info.get('mode_label', '')}
破局描述：{breakpoint_info.get('description', '')}

当前已生成到第 {current_global + generated} 章，本批生成第 {current_global + generated + 1} ~ {current_global + generated + batch} 章。

要求：
1. 每章有标题（4-8字）和 plot（20-50字，描述本章核心事件）
2. 剧情要推进，不能水字数，每批至少有 1-2 个转折点
3. 注意埋设和回收伏笔（短期伏笔 5~10 章回收，中期 20~40 章，长期 50~100 章——与大纲期配速一致）
4. 节拍类型：normal（日常）/turning（转折）/climax（高潮）/boss（战斗）/emotion（情感）/recall（回忆）
5. 符合{genre_name}类型特征
6. 必须延续原文题材与文风，不得跳类型（言情不得续成诡异/恐怖/修仙等）
7. {_genre_style_block(book)}

输出格式（严格 JSON）：
{{"chapters": [{{"title": "章节标题", "plot": "本章核心事件", "beat": "normal/turning/climax/boss/emotion/recall"}}]}}
"""
```

**与主路径的差距**（对照 §2.1 的 ArchitectAgent prompt）：

| 维度 | 主路径 `architect._fill_plots` | 续卷路径 `extend_outline` |
|---|---|---|
| `plot` 上限 | ≤100 字 + 章末钩子约束 | **20~50 字** |
| `plot_detail` | 900~2000 字分场景拍点表 | **❌ 无** |
| `entry_state`/`exit_state` | ✅ 要求 | **❌ 无** |
| `end_hook` | ✅ ≤40 字，禁无主暗示 | **❌ 无** |
| `participants` | ✅ | **❌ 无** |
| `plant_fs`/`resolve_fs` | ✅ | **❌ 无** |
| 世界观圣经 | 注入 | **❌ 不注入** |
| 七锚/里程碑 | 注入 | **❌ 不注入** |
| 段/卷结构 | segments + 里程碑 | **❌ 无** |

**即：续卷产出的 `Chapter` 行只有 `{title, plot(20-50字), beat}` 三个字段。**
`plot_detail` 永远为 `None` → 写作时 `_readiness_gate` 若 `detail_convention=True` 会**卡住不写**（§8.4）。

**更致命的是失败兜底**（`continue_story.py:1161-1173`）：

```python
except Exception as e:
    ...
    log(f"  ⚠️ 批次生成失败（{str(e)[:50]}），用简化模板填充")
    for i in range(batch):
        ...
        new_chapters_data.append({
            "volume": vol, "idx": idx,
            "title": f"第{current_global + generated + i + 1}章",
            "plot": "剧情推进中…",       # ← 占位符
            "beat": "normal",
        })
        idx += 1
    generated += batch
```

**LLM 失败时用「剧情推进中…」占位符填满整批（`outline_batch_for_model()` 章，可达 100+ 章）。**
这些章会真的写作。

### 7.3 路径 A 的实证质量崩塌（`book 11`）

`book 11`「第九天，喜欢你」——**原始档位 `fast`（3~10 章）的校园言情**，
被续接成 **`ultra` / `target_chapters = 356` / 4 卷**。

`book.story_brief_json._continue_info`：

```json
{"original_scale":"fast","target_chapters":356,"total_volumes":4,"breakpoint_mode":"new_crisis"}
```

它的 4 卷蓝图（`book.outline.volumes`）：

```json
[{"no":2,"title":"第2卷·新篇","goal":"延续破局点开启的新剧情线，逐步推进至终局"},
 {"no":3,"title":"第3卷·新篇","goal":"延续破局点开启的新剧情线，逐步推进至终局"},
 {"no":4,"title":"第4卷·新篇","goal":"延续破局点开启的新剧情线，逐步推进至终局"}]
```

**三卷标题逐字相同（「第N卷·新篇」），三卷 goal 逐字相同。** 无 `arcs`、无 `vol_end_hook`、无 `milestones`。

**来源已定位 —— `continue_story.py:1246-1260`，一个 Python 列表推导里的字面量：**

```python
    # C-11：为 2~N 卷补 volumes 元数据，保证续卷大纲有卷目标指引
    orig_vols = outline_data.get("volumes") or []
    existing_nos = {v.get("no") for v in orig_vols if isinstance(v, dict)}
    missing = [n for n in range(2, total_volumes + 1) if n not in existing_nos]
    if missing:
        new_vols = [
            {
                "no": n,
                "title": f"第{n}卷·新篇",
                "goal": "延续破局点开启的新剧情线，逐步推进至终局",   # ← 硬编码字面量
                "knowledge_direction": learn_direction,
            }
            for n in missing
        ]
        outline_data["volumes"] = list(orig_vols) + new_vols
```

> **⚠ 这是与 `_synthesize_segments` **完全同型**的缺陷，而且它还活着。**
> 2026-09-11 修复了「分段 goal 写死同一句」（`architect.py:756-805`），
> 但**没有人去检查续卷路径的同一处**：`continue_story.py` 给每一个续接卷
> 写入**逐字相同**的 `goal` 与 `f"第{n}卷·新篇"` 标题。
> 而 `goal` 正是注入卷导演 prompt 的「本卷既定目标」（`story_arc_planner.py:119`）。
> **`book 11` 的卷 2/3/4 拿到的是同一句指令** —— 与书 1005 的 6 段拿到同一句 goal 是同一个 bug 的两个副本。

### 7.5 续卷章**永远没有细纲** —— 代码级确证

`continue_story.py:1216-1227`（新章落库的唯一构造点）：

```python
            chapter = Chapter(
                book_id=book.id,
                volume=ch_data["volume"],
                idx=ch_data["idx"],
                title=ch_data.get("title", ""),
                plot=ch_data.get("plot", ""),
                beat=ch_data.get("beat", "normal"),
                points="[]",
                participants=json.dumps(participants, ensure_ascii=False),
                status="pending",
                is_transition=is_transition,
            )
            session.add(chapter)
```

**构造参数只有 9 个：`book_id / volume / idx / title / plot / beat / points / participants / status / is_transition`。**

| 细纲字段 | 是否写入 | 后果 |
|---|---|---|
| `plot` | ✅（20~50 字） | 极短 |
| `plot_detail` | **❌ 从未写入** | `_readiness_gate` 在 `detail_convention=True` 时**拒绝开写** |
| `end_hook` | **❌** | 章末钩子只能靠 writer 自由发挥 |
| `entry_state` / `exit_state` | **❌** | **无状态锚 → 卷 2 首章重播卷 1 末章（§6.4 实证）** |
| `plant_fs` / `resolve_fs` | **❌** | 伏笔 key 不进章节，`_ensure_fs_rows` 对账缺输入 |
| `beat` | ✅ | 但续卷 prompt 未给节奏配额 |

**三重后果叠加**：
1. **续接卷章不写进 `book.outline["chapters"]`，细纲块整块为空**：
   `extend_outline` 只写 `Chapter` 表行，`:1236-1266` 往 outline 里塞的只有
   `_continued` / `_continue_info` / `volumes` 元数据，**`chapters` 数组一个都没加**。
   而正文侧取细纲是从 `book.outline.chapters` 按 `(volume, idx)` 查的
   （`pipeline.py:6351-6372`）→ **查不到** → `_serial_detail_gate` 的
   `pos is None → return _pass()`（`pipeline.py:2679-2680`）→ **闸门直接放行**。
   → Writer 拿到的 `【本章细纲——场景骨架硬约束】` **整块不注入**
   （`flowmode/base.py:153-163` 在 `plot_detail` 为假时整块跳过）
   → **只能凭 20~50 字的 `plot` 自由发挥**。
   > ⚠ 注意这不是「闸门卡死」，而是**闸门静默放行 + 约束块消失** —— 更隐蔽，
   > 因为日志上看不出任何异常，只是正文质量下滑。
   > **而且修不了**：唯一能给已有章节补细纲的 `_fill_outline_details`
   > 要求 `plan_by_key.get((vol, idx))` 存在（`pipeline.py:3266-3273`）
   > → **续接卷的章号不在 `plan_by_key` 里，「补齐细纲」按钮对它们也无效**；
   > `outline_detail_missing`（`pipeline.py:3177`）同理统计不到。
2. **`resolve_fs` 全文件零赋值**：grep `resolve_fs` 在 `continue_story.py` 只命中
   `:1561` 的 docstring。`plant_fs` 由 `_distribute_fs_to_chapters`（`:1521-1556`）补，
   但规则是**把伏笔全挤在开头**：

   ```python
   # continue_story.py:1551-1556
       # 长期/贯穿 → 过渡章优先，其次开篇 5 章
       _assign(chapters[:5], long_keys)
       # 中期 → 前 1/3
       _assign(chapters[: max(5, total // 3)], mid_keys)
       # 短期 → 前 60 章（回收可及）
       _assign(chapters[: min(60, total)], short_keys)
   ```

   → **第 60 章之后没有任何伏笔埋设指令，500 章里后 440 章的 `plant_fs` 恒为空。**
   叠加伏笔配额被砍（`:1437-1439`）：

   ```python
       target_short = min(fs_plan["short_term"], 15)  # 先生成15个短期伏笔
       target_mid = min(fs_plan["mid_term"], 10)
   ```

   `calculate_new_foreshadow_count(500)` 算出 75/40/15/4 = **134 条**，
   实际最多 **25 条**（15 短期 + 10 中期），长期/贯穿只来自破局点 `new_hooks`（prompt 要求 ≥3）
   → **长期伏笔 ≈ 1~2 条支撑 500 章**。
3. **长线伏笔在每卷末被就地核销**：`_produce_volume` 收尾无条件调
   `_force_recover_fs`（`pipeline.py:5994`），它把**所有**未回收伏笔并入最后一章 `resolve_fs`
   并标记 `resolved=True`（`pipeline.py:5456-5485`）；配合 `_finalize_book` 对 ultra
   要求「0 未回收」否则转 `interrupted`（`pipeline.py:5625-5642`）
   → **贯穿型/长期伏笔在第 2 卷末就被清空**，后续卷无长线可收，只能靠新开短伏笔填充。
   另一副作用（`continue_story.py:1503-1514`）：**所有**未回收伏笔（含刚建的普通短期）
   一律升为 `weight="重要"` → `weight` 分级被抹平，逾期必审失去区分度。
4. **无状态锚**：`entry_state`/`exit_state` 缺失 → 卷边界交接只能靠 `hang_json`，
   而 `hang` 在卷首章被**主动丢弃**（`memory.py:1569,1588`：`volume_open` 时
   `hang` 与 `prev_prose` 都不注入）→ §6.4 的「卷 2 首章重播卷 1 末章画面」。
   同时 `agents/writer.py:440-444` 对卷首章只说「**允许**换场景」，
   **没有一条指令要求「必须换」** —— 允许是可选，承接是默认，模型的默认解就是留在原地。
5. **`participants` 是全员广播**：`active_names = 活跃角色 top 30`（`continue_story.py:1186-1201`），
   非过渡章 `participants = list(active_names)`（**全部 30 人**，`:1213`）——
   与主路径「普通章 ≤6 人 / 大场面 ≤10 人」的约束（`architect.py:356-357`）直接冲突，
   且 `ULTRA_ENSEMBLE_RULE` 放宽后也只有 ≤9/≤16。
   **续卷章每章都标 30 个出场人物。**

**对比自动续卷路径（§7.4 路径 B）**：`pipeline._next_volume_inline` → `arch.plan_volume`
→ `_plan_volume_segmented` → `architect.py:2762 self._fill_plots(...)`，
**会产 `plot_detail`/`end_hook`/`entry_state`，并把 `resolve_fs`/`plant_fs` 转全局号落库**
（`pipeline.py:5819-5843` 的 `Chapter(...)` 构造含 `plant_fs`/`resolve_fs`）。

> **即：续接产生的那一卷是全生命周期里结构最差的一卷**
> （无细纲、无 `end_hook`、无状态锚、无 `resolve_fs`、不在 `book.outline.chapters` 里、
> 不参与细纲 lint、30 人广播、无法用「补齐细纲」修复），
> 而第 1 卷与后续自动续卷都是完整的。**它之后的卷反而都正常** ——
> 这解释了「续接点那一卷特别烂，之后又变好」的现象。

**另：`detail_review.py` 是死代码。** `detail_review.py:744` 有 `_check_scene_location_jump`
（用约 90 词的 `_LOC_GROUPS` 表判定室内↔室外跳变），但：① 它是 **warn 级**
（`detail_review.py:745` 自述「不参与 passed 判定」）；② 只检测**跳变**，从不检测**停滞**；
③ `review_detail_batch`（`detail_review.py:1163`）**在 `app/` 内零调用方**
（只有 `test_detail_review.py`）。**连这个 warn 级的地点启发式也从未运行。**

而第 1 卷（原始的 6 章）大纲是**完整、具体、有结构的**：

```json
{"chapter_id":1,"chapter_title":"第九天，同桌协议","beat":"normal","milestone_ids":["m1"],
 "plot":"转学第一天，苏念安在情感系统抽签中与同桌顾屿绑定9天期限。她冷静接受，迅速拟定《理性交往协议》…"}
...
{"chapter_id":6,"chapter_title":"无限期，进行时","beat":"climax","milestone_ids":["m4"],
 "plot":"期限终点日，顾屿根据线索带苏念安到旧图书馆天台。'如果规则是既定的，那我们就在终点之外，写下新的开始。'…"}
```

**质量断崖的机制**：
1. `fast` 档 6 章的故事，`estimated_chapters = 6`（`book.outline` 里确认），
   所有 `milestones` 在 6 章内全部兑现（`m1`~`m4`），第 6 章已是 `climax` + 结局；
2. 续接把 `scale` 改成 `ultra`、目标 356 章 —— **但 `story_brief` 的七锚、`milestones`、
   `ending_promise`、`volumes` 全部保持不变**（只有 `total_volumes` 被改成 4）；
3. `extend_outline` **不用七锚**，只用 `breakpoint_info`（一次 `new_crisis` 破局点设计）+
   「当前已生成到第 N 章」——**一个 4 字段的 prompt 去生成 356 章**；
4. 卷 2/3/4 的 `goal` 由 `_clean_volumes` 的兜底分支生成 ——
   但那是 `story_director.py` 的函数，**`continue_story.py` 根本不调用 `_clean_volumes`**，
   所以这三个卷的 goal 来自更上游的某次 `_lock_volumes` 合并（`architect.py:687-736`），
   而 brief 里的 `volumes` 本身就只有 `{no, title}` 两项，`goal` 是 `""` →
   `_lock_volumes` 的 `for k in ("arcs","fs_plan","milestones","ending_hook","blueprint",
   "knowledge_direction","goal","title")` 只在 `cur.get(k) not in (None,"",[],{})` 时才覆盖 →
   **brief 的空 goal 被保留，最终写库的就是「延续破局点开启的新剧情线，逐步推进至终局」这个模板串**。

**这是一条完整的「fast 小书 → 356 章 ultra」的质量崩塌链，且每一步都可复现。**

### 7.4 路径 B（自动续卷）的质量更高，但有硬缺陷
`pipeline.py:380` `_story_plan_volume` → `StoryArcPlannerAgent.run()`（`story_arc_planner.py:72-163`）。

它的 prompt 质量明显更高（逐字）：

```python
SYSTEM_TEXT = """你是网文系列的「卷导演」，负责为纯小说的单独一卷设计剧情线。
...
硬性要求：
1. 本卷 arcs 3~6 条，每条是一条剧情线（name≤30字、description≤120字、est_chapters 为该线约占章数、beats 8~20 个关键节拍，每个节拍一句话）；
2. 必须承接上一卷结局与人物状态，不得重置人物关系/实力/目标；
3. 主角本卷「想要」（外部目标）与「需要」（内心缺陷），卷末至少其一发生改变；
4. 至少 1 次关系质变、1 个代价时刻、1 次重大逆转；反派有自己的目标与逻辑；
5. 收到的未回收伏笔必须在 beats 中安排推进或回收；终卷时全部回收，且后 20% 不新埋长线伏笔；
6. 非终卷须在卷末留下指向下一卷的强悬念，但本卷自身的主冲突要有阶段性结果；
7. 严格遵守世界观圣经的时代/力量体系/境界进阶，不得出现设定崩坏。
```

**但它同样没有「扩张」维度**：`arcs` 的 6 个字段里没有地点、没有势力层级。
**并且 `age`/`acts`/`conflict_curve` 不在输出 schema 里。**

**硬缺陷 ①**：`story_arc_planner.py:105`：

```python
is_ultra = ("400" in (per_volume_range or "")) or (total_volumes or 0) >= 4
```

**用字符串包含 `"400"` 来判定是否 ultra。** 若 `per_volume_range = "300~500"` 且 `total_volumes=3`
（合法的 ultra 配置），`is_ultra = False` → 卷 2 拿到的是**非 ultra 的增强等级**（arcs 3~6 而非 6~10）。

**硬缺陷 ②**：`story_arc_planner.py:88`：

```python
is_terminal = total_volumes and volume >= total_volumes
```

终卷判定只看卷号。若 `total_volumes` 被 `_lock_volumes` 抬高（`pipeline.py:726-732` 取 max），
终卷判定漂移，可能导致**全书无处收束**（每一卷都被判为非终卷 → 每卷都留悬念 → 没有卷回收伏笔）。

**硬缺陷 ③**：`pipeline.py:5648-5651`（`_finalize_book`）：

```python
target_vols = book.total_volumes or 1
target_vols = _flow(book).min_volumes(target_vols, book.scale or "standard")
if target_vols > 1 and book.current_volume < target_vols:
```

`min_volumes()` 对 ultra 返回 `vols_lo = 3`。**只要 `total_volumes ≥ 3` 就自动续卷到 3 卷**——
`book 18` 规划 4 卷但 `current_volume=1`（`interrupted`），若跑到卷末会自动补 2 卷。

**硬缺陷 ④（最贵的一条）：`ending_promise` 在续接路径里 0 次出现。**
grep `ending_promise` 于 `continue_story.py` **无命中**。它只出现在
`story_director.py:42`（schema）、`story_arc_planner.py:121/128`、
`pipeline.py:417/476/499`。
→ **续接路径不知道全书结局承诺是什么，`breakpoint_info` 里也没有这个字段。
续接卷的 LLM 从未被告知「这本书最终要走向哪」。**
`extend_outline` 的整个 system prompt 只有三个输入字段
（结局类型标签 / 破局模式标签 / 破局描述，`continue_story.py:1101-1113`）。

**硬缺陷 ⑤：`total_volumes` 无上钳。**
`continue_story.py:1046 total_volumes = max(total_volumes, trans_volume + 1)` 只有下钳；
`:1267 book.total_volumes = total_volumes` **直接覆盖 DB 值（无 `max` 保护，若原书更大则被调小）**。
`scales.json` 的 `vols_hi=6` / `chapters_hi=1700` **在续接路径上没有任何强制点**
（对照 `ULTRA_TOTAL_CHAPTERS_RULE`，`architect.py:965-969`，只在 `plan_volume` 路径注入）。
→ **续接可以无界增长**，1000 章只是 `chapters_lo` 的下界语义。

**硬缺陷 ⑥：配置分层失效。** `ultra_volume_config()`（`continue_story.py:94`，读 `scales.json`）
**只被预览端点 `get_continue_plan` 调用一次**（`:397`）；
真正执行的 `extend_outline` 用的是模块级硬编码常量（`:1030-1032`）：

```python
    chapters_per_vol = ULTRA_CHAPTERS_PER_VOLUME      # = 500，硬编码
    total_volumes = max(ULTRA_MIN_VOLUMES, (target_chapters + chapters_per_vol - 1) // chapters_per_vol)
```

→ **「改 `scales.json` 热生效」的承诺对续接无效**，且预览与实际可能不一致。

### 7.6 「信息预算恒定、篇幅线性增长」——最底层的结构缺陷

这是比任何单点 bug 都更根本的一条。全书上下文预算**不随篇幅增长**：

| 注入项 | 上限 | 位置 |
|---|---|---|
| 时间线（前情） | **6000 字** | `memory.py:1680 timeline[:6000]` |
| 世界观圣经（按需注入） | **4200 字** | `bible_inject.py:37-41 _max_chars()` |
| RAG 检索 | **1800 字** | `config.py:79 RAG_MAX_CHARS=1800` |
| 前卷摘要 | **200 字/卷** | `memory.py:1595-1611` |
| 上一卷主线进展 | **200 字** | `agents/audit.py` `main_plot_summary ≤200` |
| 上一卷人物状态 | **300 字** | `agents/audit.py` `character_snapshot ≤300` |
| 上一卷备忘 | **200 字** | `agents/audit.py` `memo_for_next_volume ≤200` |
| 续接上下文（最后 5 章） | **2000~3000 字** | `continue_story.py:224/334` |
| 未回收伏笔 | **10 条 × 80 字** | `continue_story.py:282-285` |
| 细纲继承 | **最近 40 章 ∩ 2500 字符** | `architect.py:1760-1761` |

**1500 章的书，第 1 章与第 1500 章拿到的"世界记忆"总量完全一样。**
每章写作能看到的"前情"恒定在约 6000 + 4200 + 1800 字。
→ **越到后期，"记忆密度"（每章可用前情 / 已写篇幅）越低**：

```
第 50 章：可用前情 12000 字 / 已写 25 万字  = 4.8%
第 400 章：可用前情 12000 字 / 已写 200 万字 = 0.6%
第 1500 章：可用前情 12000 字 / 已写 750 万字 = 0.16%
```

**这才是「AI 味」与「啰嗦」的最底层成因**：模型在第 1500 章时，对前 1499 章的知识
只有 0.16% 的可用带宽。它唯一能做的，就是把当下这一小片场景反复描摹到字数达标 ——
**因为除此之外它没有任何东西可写。**

（`AGENTS.md:253-260` 的规模压测只测了**性能**（RAG 查询 2.36s、RSS 350MB、图谱 0.039s），
**从未测过"信息密度随篇幅衰减"**。这是重写时最该补的测量。）

---

## 七-b、续卷机制的完整调用链（速查）

```
用户点「续接扩写」
  └─ POST /api/books/{id}/continue            main.py:2819
       └─ 后台线程                              main.py:2884
            └─ execute_continue(book_id, ..., direction)      continue_story.py:846
                 ├─ check_eligibility              :122   （仅 completed + fast/standard/grand）
                 ├─ ← 提前返回：_outline_partial.json 存在 → _resume_outline_plots  :881-893
                 ├─ backup_book                    :133
                 ├─ detect_ending                  :177   （最后 5 章 × 200 字）
                 ├─ design_breakpoint              :251   （最后 3 章 × 200 字 + 10 条伏笔 × 80 字）
                 ├─ extend_outline                 :1011  ⚠ 简化版；裸 StoryDirectorAgent.ask_json
                 ├─ extend_bible                   :1282  （追加文本到 bible.txt，不产章节/角色/伏笔）
                 ├─ init_new_foreshadows           :1395  （配额 134 → 实际 ≤25；全部升 weight=重要）
                 ├─ extend_knowledge_ledger        :1577  （story 直接跳过 :1582-1583）
                 ├─ book.scale="ultra"; phase="serializing"  :944-946
                 └─ _spawn_once(_produce_serial)   :980-981
                      └─ 卷末 → _force_recover_fs → _finalize_book
                           └─ current_volume < total_volumes → _next_volume_inline  ← 路径 B（完整）
```

**两条路径的产出对比**：

| 产出 | 路径 A `extend_outline`（续接卷） | 路径 B `_next_volume_inline`（自动续卷） |
|---|---|---|
| `title` / `plot` / `beat` | ✅ | ✅ |
| `participants` | ⚠ 全员 30 人 | ✅ 按章给 |
| `points` | `"[]"` | ✅ |
| `plant_fs` | ⚠ 仅前 60 章 | ✅ |
| **`resolve_fs`** | **❌ 零赋值** | ✅ 转全局号落库 |
| **`plot_detail`** | **❌ 零出现** | ✅ `_fill_plots` |
| **`end_hook` / `entry_state` / `exit_state`** | **❌ 零出现** | ✅ |
| **进 `book.outline["chapters"]`** | **❌** | ✅ |
| 过 `outline_lint` | ❌ | ✅（shadow） |
| 可用「补齐细纲」修复 | **❌**（`plan_by_key` 查不到） | ✅ |
| 卷蓝图 `arcs` | ❌（故必然降级卷导演） | ✅ 优先取蓝图 |
| 卷目标 | ⚠ Python 字面量，逐字相同 | ✅ 卷导演生成 |

---

## 八、大纲遵循度与 lint

### 8.1 `outline_lint.py` 的完整检查清单

**零 LLM、纯函数、可单测**（`outline_lint.py:1-29`）。全部检查码：

| code | level | 判据 | 位置 |
|---|---|---|---|
| `no_chapters` | error | 大纲无章级计划 → `ok=False, not_covered=True` | `:159` |
| `no_detail_anywhere` | error | 全书无任何章带 `plot_detail` → 同上 | `:166` |
| `detail_missing` | error | 单章 `plot_detail` 为空 | `:93` |
| `detail_too_thin` | error | `len(detail) < min_chars` | `:98` |
| `scene_lines_few` | error | 带 `→` 的场景行 `< min_scene_lines`（默认 4） | `:102` |
| `scene_line_malformed` | error | 场景行缺 `：` 前缀 | `:109` |
| `entry_state_missing` | error | `require_states=True` 时缺 `entry_state` | `:121` |
| `exit_state_missing` | error | 同上，缺 `exit_state` | `:124` |
| **`state_break`** | error | **`exit_state[N] != entry_state[N+1]`**（状态连续性不变量） | `:129` |
| `state_break_next` | error | 同上，向后比对 | `:138` |
| `duplicate_titles` | error | 全书标题近重复组数 > `max_dup_titles` | `:190` |
| `segment_goals_identical` | error | 所有段 `goal` 完全相同（`len(set(goals)) == 1`） | `:201` |
| `segment_plan_synthesized` | error | `segment_plan_source == "synthesized"` | `:206` |

**「缺省即不通过」原则**（`outline_lint.py:24-26`）：

> 沿用仓库「缺省即不通过」的既有约定（`benchmark/gate_runner.gates_all_pass`、
> `benchmark/rolling_acceptance._tri`）：**没有数据 ≠ 通过**。

状态连续性归一化（`outline_lint.py:69-71`）：

```python
def _state_key(state: Any) -> str:
    """状态串归一化：只留字词，便于容忍标点/顿号/空格的差异。"""
    return _NON_WORD.sub("", str(state or "")).strip()
```

**注意 `state_break` 用的是精确字符串相等**（归一化后）。这意味着
「地点：古祭坛遗址（古碑已碎）·雨后 / 时间：黎明前 / 人物：A、B」与
「地点：古祭坛遗址（古碑已碎）·雨后 / 时间：清晨 / 人物：A、B」
会被判为 `state_break` —— **状态比较过于字面，实际几乎必然触发**。

### 8.1b 死代码与 shadow 闸门清单（重写时的「别被表面欺骗」）

| 模块 | 状态 | 证据 |
|---|---|---|
| `detail_review.py`（64 KB） | **死代码** | `review_detail_batch`（`:1163`）在 `app/` 内**零调用方**，仅 `test_detail_review.py` 调它 |
| ├ `_check_scene_location_jump`（`:744`） | 死代码 + warn 级 | 自述「**启发式，warn 级、不参与 passed 判定**」；且只检测**跳变**，不检测**停滞** |
| └ `_LOC_GROUPS`（`:185-203`） | 死代码 | 约 90 词的室内/室外/场所硬编码词表 |
| `novelty._cosine_matrix`（`:146`）/ `_jaccard_matrix`（`:164`） | 死代码 | 被 `_iter_similarity_scores` 取代，零调用方 |
| `config.py:63 NOVELTY_GUARD` | **死开关** | 全仓只有定义 + 文档表格；`.env` 里也没有。文档称其为"源头雷同防护开关"是**过时的** |
| `outline_config.detail_lint_enabled` | **shadow** | 默认 `False`；且 `backend/outline_config.json` **根本没这个键**；被 `test_detail_pipeline_contract.py:112` 钉成契约 |
| `outline_config.detail_review_enabled` | **shadow** | 同上 |
| `ultra_structure_config.enabled` | **false** | `shadow: true` → 结构闸门只观测不阻断 |
| `prose_style_config.shadow` | **true** | 「只计算 + 落 problems.jsonl + steps 日志，**绝不改正文**」（`pipeline.py:7001`） |
| `rolling_story_config.DEFAULTS` | `{"enabled": False}` | 但生产 json 是 `enabled: true`（刻意的非默认态，`AGENTS.md:163`） |

**续接路径（`continue_story.py`）把这些闸门全部绕过** ——
`ultra_structure.py` / `outline_lint.py` / `detail_review.py` / `rolling_story_pipeline.py` /
`agents/story_arc_planner.py` 在该文件中**零引用**（grep 无命中）。

### 8.2 lint 是 shadow，不阻断

`outline_config.py:51-57`（逐字）：

```python
    # 确定性细纲 lint（app/outline_lint.py）是否**阻断**。
    # **默认 False = shadow**：lint 照常运行、照常落日志与通知，但不拦写作。
    # 这是刻意遵守仓库强制上线协议「新闸门必须先 shadow 观测假失败率，才允许阻断」。
    # 实测若开成 True，书 1005 这类既有书会**立刻停止写作**（29 组重复标题、
    # 6 段 goal 全同、细纲 p50 仅 179 字全部命中 error）—— 那应当是一个显式决定，
    # 而不是升级代码后被动发生的事。确认要严出后再置 true。
    "detail_lint_enabled": False,
    # 细纲 LLM 一致性审校是否**阻断**。默认关：新闸门必须先 shadow 观测假失败率。
    "detail_review_enabled": False,
```

**实测 `backend/outline_config.json` 里这两个键甚至不存在**（只有 3 个键）
→ `_raw()` 用 `DEFAULTS` 补齐 → **`False`**。

**AGENTS.md:226 记录的 shadow 观测结果**：

> `  （实测 412 项 error：300 章 `detail_too_thin`、91 章 `scene_lines_few`、31 组重复标题、1 项 `segment_goals_identical`）。`

**即：lint 在书 1005 上 100% 命中，只能 shadow。**

### 8.3 细纲厚度与正文质量的证据

**文档证据** — `outline_lint.py:15-20`：

> 细纲只占正文的 ~3~6%（正文 3155~6774 字），writer 拿到的「场景骨架硬约束」约束不了
> 任何东西，只能自由发挥 —— Editor 因此反复标出「大段细纲外的内容」「无中生有」「剧情级加戏」，
> 以及最刺眼的那类**动机断裂**：第 2 章「拒绝给李巡长开门」，同一章后段就「在镇公所与李巡长同席」。

**注入侧不是瓶颈** — `AGENTS.md:193-194`：

> `  `flowmode/base.py` 把它**原文平铺**进 prompt（无 cap、无截断）—— 注入侧从不是瓶颈，`
> `  每 +1 中文字 ≈ +0.69 输入 token（650→4000 字仅 +1858 token）。**瓶颈在生成侧**：`

**修复后的实测（`book 24`）**：细纲正文占比 = `788 / (447 章 × 平均 5000 字) ≈ 0.35%`……
不，正确的算法是每章：`plot_detail 788 字 / 正文约 5000 字 ≈ 15.8%`
（book 24 的 12 章正文文件平均 16 KB ≈ 5300 汉字）。
对照书 1005 的 3~6% → 已提升到约 16%，**但配置目标是 900~2000 字 / 4600~8000 字正文 = 20~43%**。

**chapter_limits 的对照（`backend/chapter_limits.json`，ultra）**：

```json
"ultra": {"min_chars":4600, "hard_min_chars":4000, "soft_max_chars":8000,
          "climax_max_chars":15000, "max_continuations":2,
          "soft_floor_chars":3200, "allow_short_chapter":true}
```

`architect.py:15-28` 的 docstring 记录了**下限如何直接制造注水**：

> 书 23 实测：ultra 的 `min_chars=4600` 配上 writer 的「必须写满 4600 字以上」措辞，
> 在细纲只支撑 ~5 个 beat 时，写到 4600 字的唯一办法就是注水。**下限本身不是错，
> 错的是「低于下限 = 废稿」的语义。**

**当前配置**：`soft_floor_chars=3200` + `allow_short_chapter=true` + `writer_rules_config.json = {"rule_profile": "lean"}`
→ 注水语义已解除。**但 §4.3 显示：细纲 p50 788 字仍低于 `plot_detail_min_chars=900`，
即「细纲支撑不了 4600 字正文」这个根因仍在。**

### 8.4 细纲就绪闸门（`pipeline._readiness_gate`）

**AGENTS.md:213-219**：

> `* **细纲就绪闸门（2026-09-11 补，A3/E1）**：`_pregen_pick_next` 一直有闸门，但`
> `  **`_produce_serial`（点完「开始创作」后的正文阶段）完全没有** —— `undone` 只过滤 status，`
> `  空细纲也照写。现已统一走 `pipeline._readiness_gate`：`
> `  ①必须有 plot；②本书带细纲约定时必须有细纲（且达 `plot_detail_min_chars`）；`
> `  ③`outline_config.neighbor_ready_window>=1` 时还要求 **N-1 与 N+1 细纲均就绪**`

**这对续卷路径（§7.2）是致命的**：`extend_outline` 产出的章**没有 `plot_detail`**，
若 `detail_convention=True`（`detail_for_new_books` 默认 `True`），
→ **续卷后的所有章都会卡在闸门，正文永远不开始。**
这是「续卷从未真正跑通」的又一条机制性解释（`book 18` 规划 4 卷、`book 11` 规划 4 卷，都停在卷 1/卷 2）。

---

## 九、新颖度对抗

### 9.1 `novelty.py` 测什么（697 行）

| 函数 | 行 | 测什么 | 阈值来源 |
|---|---|---|---|
| `novelty_report(session, book, warn=0.8, high=0.9, ...)` | `:250` | **章节两两相似度**（下三角全配对） | 请求参数 |
| `_chapter_vectors()` | `:101` | 用本地 **bge-m3** 编码每章指纹文本 | `EMBED_BATCH=32` |
| `_fp_text(ch)` | `:66` | 指纹文本 = `summary` → `plot` → `title`（**不用正文**） | — |
| `_cosine_matrix` / `_jaccard_matrix` | `:146` / `:164` | 向量余弦 / 二字组 Jaccard（bge 不可用时降级 `mode=lexical`） | — |
| `guard_against_duplicate(...)` | `:594` | **初稿 vs 历史章的句级比** | `guard_config.json` |
| `_count_sent_match` / `_align_rows` | `:237` / `:426` | 句级 TF-IDF 加权对齐 | — |

**维度覆盖**：

| 维度 | 是否存在 | 位置 |
|---|---|---|
| 语义换皮（措辞不同剧情一样） | ✅ | `_chapter_vectors` + 余弦 |
| 字面句子重复 | ✅ | `guard_against_duplicate` |
| 伏笔呼应豁免 | ✅ | `novelty.py:10`「伏笔回收章与其埋设章标注为「伏笔呼应」而非雷同」 |
| 相邻承接章跳过 | ✅ | `skip_adjacent: true` |
| **场景 / 地点重复** | **❌ 不存在** | 全仓无 location 相似度 |
| **标题唯一性（生成时）** | **❌ 不存在** | 只在 `outline_lint`（advisory）+ `story_health`（阈值 ≥3）事后检测 |
| **剧情 beat 序列重复** | **❌ 不存在** | `novelty.py:7` 自己承认：「结构/节拍套路重复（beat 序列同模板）**不在本模块**，属另一条线」（那条线不存在） |
| **段落级 / 句式模板重复** | **❌ 不存在** | — |

### 9.2 在哪里被执行 —— 只有一处，且是"初稿后"

grep 全 `backend/app` 的 novelty 引用：

```
config.py:63   NOVELTY_GUARD  ← 【死开关】全仓只有定义 + 文档表格，生产代码零引用
main.py:2339/2359/2372   ← 三个只读端点（按需接口）
novelty.py     ← 本体
pipeline.py:6680   from .novelty import split_sentences   ← 只借切句工具
pipeline.py:7312   from .novelty import guard_against_duplicate
pipeline.py:7313   guard = guard_against_duplicate(session, book, (ch.plot or "")[:100], content, ch.volume or 1, ch.idx or 0)
safe_guard.py  ← 日志
```

**只有一个生成期调用点**：`pipeline.py:7306-7340`，`_commit_chapter` 里**初稿写完之后**。

**违规后的四条分支**（`pipeline.py:7322-7338`）：

| 分支 | 条件 | 后果 | 是否阻断 |
|---|---|---|---|
| ① | 近邻章 + 局部改写成功 | 替换正文，**不升级整章审校** | 否 |
| ② | 非近邻 + 局部改写成功 | 替换正文，**仍跳过整章审校** | 否 |
| ③ | **近邻章 + 局部改写失败** | **「放行并标记（不整章重写，避免破坏承接）」** | **否 —— 违规内容直接入库** |
| ④ | 非近邻 + 局部改写失败 | `must_review = True` + 注入 `dup_note` | 软性（升级 Editor 审校） |

分支 ③ 原文（`pipeline.py:7328-7329`）：

```python
                    if is_near:
                        _log_step(book, "editor", "⚠️ 近邻章局部改写失败，放行并标记（不整章重写，避免破坏承接）")
```

异常兜底是**静默放行**（`pipeline.py:7339-7340`）：

```python
        except Exception as e:
            _log_step(book, "editor", f"雷同防护检测跳过（{str(e)[:60]}），不阻断生成")
```

**三个放行缺口**：① 近邻章失败即放行（上表分支③）；② 任何异常静默放行；③ **改写后不再复检**
（全树只有 `pipeline.py:7314` 一处 `guard.get("hit")`，单次单遍，改完直接入账）。

**传入的指纹是 `(ch.plot or "")[:100]`** —— 此时 `summary` 尚未生成
（`pipeline.py:7308` 注释自认「此时记忆提炼未做」），
**所以向量召回用的是 plot 前 100 字，不是梗概。**

`guard_config.json` 的阈值印证这种宽容：

```json
{"global": {"enabled": true, "near_window": 4, "near_matched": 3, "near_strong": 1,
            "far_matched": 10, "far_strong": 4, "topk": 3, "recent": 12, "local_rewrite_max": 8}}
```

近邻章：**3 句相似 或 1 句强相似**才算命中；非近邻章：**10 句相似 或 4 句强相似**。
且 `topk=3` —— 非近邻只做**向量召回 top 3** 的比对，**不是全历史比对**。
`local_rewrite_max=8` —— 单次最多改写 8 句（按相似度降序取）。

**句级阈值全是硬编码模块常量**（`novelty.py:348-353`，**无一来自配置**）：

```python
SENT_MATCH = 0.40      # 判"相似句"的加权 Jaccard 阈值
SENT_STRONG = 0.62     # 判"强相似句"
MIN_COMMON = 3         # 至少 3 个实词重叠才算候选
MIN_SENT_TOKENS = 4    # 少于 4 token 的碎片不判（「锁魂兰。」本就该反复出现）
IDF_FLOOR = 1.0        # 只认真实词，人名/口头禅 IDF 低被排除
LEN_RATIO = 0.30       # 长短悬殊不判
```

且 **IDF 只在"这两章内部"计算**（`novelty.py:399-403`，`n = len(ta) + len(tb)`），
**不是全书 IDF** —— 一个在全书高频但在两章内低频的词会被误判为有区分度。

**测试还把「闸门关闭」钉成了契约**（`test_detail_pipeline_contract.py:112`）：

```python
assert oc.detail_review_enabled() is False, "新 LLM 闸门默认必须 shadow"
```

### 9.2b 缺失维度的逐条证据

| 缺失维度 | 证据 |
|---|---|
| **地点/场景重复** | `novelty.py` 全文**不读 `location`**。grep `location\|hang\|place\|scene` 仅 6 处命中，全是 docstring 与 `beat`/`participants` 的 reasons 字符串。跨章地点数据**存在但从未被消费**：`models.py:114 hang_json`、`models.py:319 StoryEvent.location`；唯一消费点是 `memory.py:1704 loc = f"@{e.location}" if e.location else ""` —— **只拼进 prompt，从不做集合去重或计数**。 |
| **生成时标题唯一性** | grep `seen_titles\|used_titles\|title_seen\|title_pool` 全 backend **0 命中**。三个写入点（`pipeline.py:1635` / `:2796` / `:5404`）全是裸赋值 `title=plan.get("title","")`。 |
| **beat 序列模板重复** | `novelty.py:7` docstring 自认：「结构/节拍套路重复（beat 序列同模板）**不在本模块，属另一条线**」—— 那条线不存在。`story_health.py:78-98` 只测「连续 N 章无强节拍」（节奏平淡），**看不到**「第 1/40/90/200 章都是受辱→奇遇→打脸」。 |
| **重复角色动作** | 无确定性检测。仅 Editor 的 LLM 提示词规则（`agents/editor.py:74-97`），如第 18 条「连续两处用同一行为动作表现同一情绪（如两次"攥紧拳头"）→ 判 ai_style 必改」—— **是提示词，不是检测器**。 |
| **描写/phrasing 重复** | `prose_style.py:51-68` 记录**四次独立验证的负结果**，结论原文：「本模块**不提供**「无推进重复」的判定，也不应该为它建确定性闸门（**那会是一台假阴性机器**）」。其中第 3 次尝试：「一本被评为「非常注水」的书得到「零注水」。**已废弃该指标**」。 |

**标题去重的唯一硬失败点在离线 benchmark**（`benchmark/detail_acceptance_report.py:250-254`）：

```python
        report["checks"]["no_duplicate_titles"] = _check(
            "no_duplicate_titles", len(dups) == 0, "measured",
            f"{len(dups)} 组重复标题 / 覆盖 {sum(len(v) for v in dups.values())} 章"
            + ("（书1005 基线：29 组 / 65 章）" if dups else ""),
        )
```

由 `test_detail_acceptance_report.py:133-141 test_duplicate_titles_fail_the_gate` 锁定。
**但这是手动跑 benchmark 时的 PASS/FAIL，与生成流水线无关。**
→ 「书 1005 基线：29 组 / 65 章」被写死进基准字符串，**说明作者知道这个数并把它当基线接受了**。

### 9.3 `novelty_report` 是 O(n²) 且只在按需接口

`novelty.py:13-33`（模块 docstring，逐字）：

> `_iter_similarity_scores` 是**下三角全配对**（n(n-1)/2 对），实测这是本仓库唯一确认的
> O(n²) 热点（`benchmark/ultra_scale_offline.py`，零 LLM）：
>
>     章数     100     300     600     1000    1500
>     耗时(s)  0.20    1.12    4.51    13.14   28.85     ← 章数 ×15，耗时 ×144
>     对数     4.9k    44.9k   179.7k  499.5k  1.12M
>
> **评估后决定暂不优化**，理由是它不在生成热路径上：
>
> - `novelty_report` 只由**按需接口** `GET /api/books/{id}/novelty`（前端「雷同检测/故事体检」）触发

**即：真正能发现"全书雷同"的能力，是一个用户手动点击的离线报告，1500 章要跑 29 秒。
它从不参与生成。**

### 9.4 结论

**系统对"重复"的防御是：**
1. prompt 里写「禁止重复这些桥段/场景/套路」（`architect.py:1757, 1775`）；
2. 生成后对**初稿**做一次句级比对，**近邻章重复失败即放行**（三分支放行）；
3. 提供一个人工触发的离线雷同报告（`GET /api/books/{id}/novelty`）。

**缺失的防御**：生成**前**的结构性防重（标题唯一性、地点多样性、beat 序列多样性、
每卷必须引入 N 个新地理/势力）。**这三层里唯一能把"1500 章一个地方"挡住的，恰恰是缺失的那层。**

**一句话**：这套子系统是一台**逐字抄袭探测器**。它没有地点维度、没有标题闸门
（只有 shadow 报告）、没有节拍模板探测器，生成热路径上唯一的守卫是**单次、非阻断、失败即放行**的。
**用户抱怨的「1500 章原地不动」，在现有代码里没有任何一行会报警。**

---

## 十、重构建议（长篇结构设计）

> 用户原则：**质量优先，可拓展性优先；前几章最重要。**
> 目标栈：LangChain + LangGraph。以下设计按「LangGraph 节点 + 结构化 State」给出。

### 10.0 设计公理

三条从本报告推出的公理，作为后续所有设计的判据：

- **公理 1（纵向已有、横向为零）**：现有系统 100% 的约束力都在「章与章不断」上，0% 在「卷与卷不同」上。
  任何新设计必须把**至少一半的结构约束预算投向横向**。
- **公理 2（要求 ≠ 执行）**：`conflict_curve` / `character_arcs` / `milestone_ids` / 伏笔配额
  四者都是「prompt 里写了、代码从不校验」→ 全部静默失效（§4.6/§4.7/§3.4）。
  **凡是长篇结构的关键字段，必须有代码级 validator + 阻断，否则等于没写。**
- **公理 3（预算是真瓶颈，但极其便宜）**：细纲厚度是预算算术问题（§4.3），不是模型能力问题；
  而大纲 token 只占全书 6%（§2.3）。**在大纲/结构层多花 5 倍 token 是数量级上的廉价投资。**

---

### 10.1 新的层级模型：Series → Volume → Arc → Chapter → Beat

现有系统的层级是 `卷 → 段(50章) → 章`，其中「段」是**编排单位**（用于防截断的批处理），
**不是叙事单位** —— 它没有主题、没有冲突目标、没有空间。这是必须换掉的第一件事。

**新的五层**（每层有独立的 State schema + validator + 生命周期）：

```
Series（全书 1000~1700 章）
  ├─ Premise / Theme / DramaticQuestion / EndingPromise     ← 不变（永恒层）
  ├─ WorldLadder        ← 【新增】地理/空间阶梯（见 10.2）
  ├─ FactionLadder      ← 【新增】势力阶梯
  ├─ AntagonistLadder   ← 【新增】反派阶梯
  ├─ PromiseLedger      ← 【新增】承诺账本（见 10.4）
  └─ Volumes[3..6]
       │
       Volume（300~500 章）—— 必须有：独立的时空坐标 + 独立的对手层级 + 独立的世界观增量
       │   ├─ locale          : 本卷主场（必填，且不得与上一卷相同）
       │   ├─ new_locales[]   : 本卷新开地理（≥2）
       │   ├─ new_factions[]  : 本卷新登场势力（≥2）
       │   ├─ antagonist_tier : 本卷对手层级（必须 > 上一卷）
       │   ├─ world_delta     : 本卷给世界观新增的规则/体系（≥1）
       │   ├─ VolGoal / VolEndHook / Acts[3..4] / Milestones[]
       │   └─ Arcs[4..8]
       │
       Arc（20~60 章）—— 叙事单位，取代现在的「段」
       │   ├─ arc_goal / arc_type(core|relationship|opposition|exploration)
       │   ├─ locale_scope    : 本 arc 的活动范围（继承或新开）
       │   ├─ entry_condition / exit_condition（结构化状态，取代自然语言）
       │   ├─ beats[8..20]
       │   └─ Chapters[]
       │
       Chapter
       │   ├─ plot / plot_detail / end_hook
       │   ├─ entry_state / exit_state（结构化四元组，见 10.5）
       │   ├─ beat_type / pov / cast
       │   └─ promises_touched[] / promises_opened[] / promises_closed[]
       │
       Beat（细纲内的一行）
           └─ {location_id, cast[], goal, obstacle, result, is_turn}
```

**关键变更**：
1. **`段` 升级为 `Arc`** —— 保留 50 章的分批能力（防截断），但每段必须有叙事身份
   （`arc_goal` + `locale_scope` + `entry/exit_condition`），不再是纯算术切分；
2. **`Volume` 增加 5 个必填的"增量"字段** —— 这是对抗「1500 章一个地方」的**最小充分集**；
3. **`Beat` 提升为一等公民** —— 现在 `plot_detail` 是一坨文本，`outline_lint` 只能正则数箭头行。
   结构化后可以精确校验地点分布。

### 10.2 地理扩张：`WorldLadder` + 强制换图节奏

**问题重述**：当前 prompt 明令「相邻章地点延续」「禁止无过渡换地图」，
换图只允许在**卷首**（2~5 次机会/全书，§5.3）。

**设计**：把「地图」变成**显式的一等数据**，并把换图从"例外"变成"配额"。

```python
class Locale(BaseModel):
    """一个可复用的空间单元。"""
    locale_id: str            # 稳定 ID，如 "L07_葬神渊"
    name: str
    tier: int                 # 空间层级：1=村/镇 2=城/宗门 3=域/国 4=界/秘境 5=上界
    parent_id: str | None     # 层级归属（构成地图树）
    introduced_at: ChapterRef # 首次登场章（代码分配，不由模型填）
    danger_level: int         # 1~5，必须随 tier 单调不减
    controlling_faction: str | None
    description: str
    status: Literal["planned", "introduced", "closed"]   # 代码维护

class WorldLadder(BaseModel):
    """全书空间阶梯。"""
    locales: list[Locale]
    # 硬约束（validator 强制）：
    #  ① len(locales) >= ceil(total_chapters / 60)   —— 每 60 章至少一个新空间
    #  ② 每卷的 locale 集合必须与上一卷有 >= 2 个新增
    #  ③ tier 序列必须整体上升：卷 N 的 max(tier) >= 卷 N-1 的 max(tier)
    #  ④ 相邻章的 locale 可以相同（承接），但连续同 locale 章数 <= 12
```

**新增硬规则（注入规划 prompt，且由 validator 阻断）**：

```
【空间扩张·硬性配额】
- 每 60 章内必须至少引入 1 个从未出现过的 locale（全书 ≥ ceil(总章数/60) 个）；
- 每卷至少 2 个新 locale，且其中至少 1 个 tier 高于上一卷的最高 tier；
- 同一 locale 连续出现不得超过 12 章（"承接"是允许的，"长住"不允许）；
- 换图必须由本卷/本 arc 的目标驱动（追杀/求援/寻物/逃亡），不得用"次日启程"式硬切；
- 卷首换图是**权利**（不是唯一例外）；arc 边界换图是**常态**。
```

**与现有禁令的关系（重要）**：`architect.py:397`「禁止每章自起自收」与 `writer.py:258`「禁止无过渡换地图」
**不是错的** —— 它们防的是"无动机的地点硬切"。新规则要用**同一套动机语言**表达扩张：
不是「允许乱换地图」，而是「**每个 arc 必须有一个换图理由，且理由必须能被前一个 arc 的结果逼出**」。
这样既保住了「纵向不许断」，又打开了横向。

**验证方式（可量化）**：用 §5.5 的方法做**地理熵仪表**：

```python
def geography_entropy(chapters, window=60) -> list[float]:
    """滑动窗口内的"新地点引入率"。健康值：每 60 章 1~3 个新 locale。
    < 0.5 → 停在原地（当前 book 23/24 的值是 0）；> 5 → 地图漂移。"""
```

**在现有数据上的基线**：`book 24`（447 章）= **0 个新地点/全书**；`book 23`（400 章）= **0 个**；
`book 18`（350 章）= **0 个真新地点**。**这就是要修的那个数。**

### 10.3 势力增殖与反派阶梯

**现状**：`factions` 是 `StoryDirector` 输出的一个**静态数组**（3~8 股，`story_director.py:20`），
字段 `{name, stance, goal, relation_shift}`。它：
- 只在首卷规划时生成一次；
- 没有层级（"鬼王宗"和"村口帮派"同权）；
- 没有登场时点（无法要求"第 3 卷才登场的势力"）；
- 续卷时不重新生成（`story_arc_planner.py:95-96` 只把它注入 prompt 当参考）。

**设计**：

```python
class FactionTier(BaseModel):
    tier: int                     # 1=地方 2=宗门/公司 3=域级 4=国家级 5=跨界/上界
    factions: list[Faction]

class Faction(BaseModel):
    faction_id: str
    name: str
    tier: int
    stance: Literal["敌", "友", "中", "变"]      # "变" = 立场会翻转
    goal: str
    enters_at_volume: int         # 【新增】登场卷（validator 检查分布）
    retire_at_volume: int | None  # 【新增】退场卷
    relation_to_protagonist: str
    relation_shift_volume: int    # 【新增】关系质变发生在第几卷

class Antagonist(BaseModel):
    antagonist_id: str
    name: str
    tier: int                     # 【核心】反派层级，必须随卷号单调不减
    enters_at_volume: int
    defeats_at_volume: int | None
    want: str                     # 反派自己的目标（现有 prompt 有要求，从不校验）
    logic: str                    # 行为逻辑
    escalates_to: str | None      # 【新增】被击败后指向的更上层反派
    is_final: bool
```

**硬约束（validator 阻断）**：
1. **反派阶梯单调**：`vol N 的 max(antagonist.tier) >= vol N-1 的 max(tier)`，且全书必须严格递增 ≥2 次；
2. **每卷至少 1 个新反派**登场，且不得是上一卷反派的"复活/兄弟/师父"（用名字相似度 + `escalates_to` 链校验）；
3. **势力登场均匀分布**：`factions` 按 `enters_at_volume` 分组，任一连续 2 卷新增势力数 ≥ 2；
4. **`is_final` 有且仅有一个**，且其 `enters_at_volume <= total_volumes - 1`（终局反派必须提前铺垫，不能最后一卷空降）。

> **这一条直接对着"幕后天梯"这个网文长篇的核心爽点机制。**
> 当前系统里，`book 24` 的反派链是 `邪修残魂 → 张昊 → 玄天宗执法队 → 玄冥`，
> 全书 447 章只走到 tier 3（玄冥是"上界审判团"），而规划 3 卷 = 1500 章。
> 后两卷没有任何新反派被规划出来。

### 10.4 承诺-兑现账本（Promise Ledger）：驱动"故事压力"

**问题**：现有 `foreshadowings` 只管"埋/收"，`plot_nodes` 只管 4 个主线节点。
**两者都不产生"必须往前推"的压力** —— 所以模型可以无限期在原地展开日常。
`scales.json` 的 pacing 配额（≥3 Boss/卷）是**计数下限**，不是**压力**（§3.6 实测：配额 3，实产 41，完全不起作用）。

**设计**：引入一个**单调增长的"故事压力"量**，它由三部分构成，且**代码可计算、可作为闸门**：

```python
class Promise(BaseModel):
    promise_id: str
    kind: Literal["mystery", "threat", "goal", "relationship", "world"]
    opened_at: ChapterRef
    due_by: ChapterRef            # 【核心】必须兑现的截止章
    escalate_every: int = 40      # 【核心】每 N 章必须"加压"一次
    last_escalated_at: ChapterRef
    intensity: int = 1            # 1~5，加压时 +1
    payoff: str | None
    status: Literal["open", "escalating", "due", "overdue", "paid"]

class StoryPressure(BaseModel):
    """全书故事压力：必须在 [lo, hi] 内单调不减地增长。"""
    open_promises: int
    overdue_promises: int          # 【闸门】> 0 时禁止继续开新承诺
    mean_intensity: float
    chapters_since_new_locale: int # 【闸门】> 12 强制换图
    chapters_since_new_faction: int
    chapters_since_antagonist_escalation: int

    @property
    def pressure(self) -> float:
        """压力 = 未兑现承诺的加权和。健康曲线：随章号近似线性上升。"""
        return sum(p.intensity for p in self.open_promises) + 3 * self.overdue_promises
```

**三条闸门规则**（在 `_readiness_gate` 里执行，**阻断式**）：

```
G1【换图闸门】chapters_since_new_locale > 12
   → 拒绝开写本章，回退给 Detail，要求本 arc 余下部分安排一次换图（并给出动机来源）
G2【势力闸门】chapters_since_new_faction > 80
   → 同上，要求引入一个新势力（或让现有势力升级 tier）
G3【压力闸门】pressure < expected_pressure(chapter_idx) × 0.7
   → 判定"在原地打转"，拒绝开写，要求 opens 一个新 promise 或 escalates 一个现有 promise

其中 expected_pressure(n) = base(3.0) + n / 50.0     # 每 50 章压力 +1
```

**为什么这能治"啰嗦"**：
当前的写作 prompt 只要求「本章承接上一章，停在悬而未决处」（纵向）。
**G3 施加的是横向压力：故事必须在"承诺空间"里前进，而不只是在"事件空间"里前进。**
一个只有日常、没有新承诺的章节会被闸门拒绝 —— 这正好排除"啰嗦"。

**为什么这比"每卷 3 个 Boss"有效**：Boss 配额是**输出约束**（可以靠堆战斗满足），
压力是**结构性约束**（必须改变承诺空间的形状）。

### 10.5 状态锚：把 `entry_state`/`exit_state` 从自然语言变成结构

**现状（§4.5 实测）**：447 章里只有 58 章（13%）有 `entry_state`；
`outline_lint.state_break` 用归一化字符串**精确相等**判定 —— 实际上永远会触发，所以只能 shadow。

**设计**：

```python
class SceneState(BaseModel):
    """章级状态锚——四元组，全部结构化。"""
    locale_id: str                      # 外键 → Locale（不是自由文本！）
    sub_location: str                   # 楼内具体位置（长明楼·四层配电间）
    time_offset_minutes: int            # 相对上一章的时间推进（分钟）
    present: list[str]                  # 在场人物 ID 列表
    knowledge: dict[str, list[str]]     # {character_id: [fact_id, ...]} 谁知道什么
    unresolved: list[str]               # 本章末未了结的事（promise_id 列表）
```

**校验规则（可精确执行，取代字符串比较）**：
1. `entry_state.locale_id == exit_state_prev.locale_id` **或** 换图理由已在 `plan` 中登记（`locale_change_reason`）；
2. `entry_state.time_offset >= 0`；
3. `entry_state.present ⊆ exit_state_prev.present ∪ newly_arrived`（新到者必须有 arrival 事件）；
4. `entry_state.knowledge >= exit_state_prev.knowledge`（知识只增不减，除非有"失忆/被杀"事件登记）；
5. `entry_state.unresolved ⊇ exit_state_prev.unresolved`（未了结的事不能凭空消失）。

**这五条是可判定的**，不像现在的字符串比较。落地后 `outline_lint` 可以从 shadow 切成阻断。

### 10.6 大纲遵循度：把 lint 从"事后观测"改成"事前约束"

**现状（§8.2）**：412 项 error 的 lint 只能 shadow，因为一开就停写。

**设计原则**：**不要在最后一道关卡拦，要在生成时就不给它犯错的机会。**

| 现有做法 | 新做法 |
|---|---|
| 生成 300 章 → lint 发现 300 章太薄 → shadow | **每批生成后立即 lint 本批**，不合格的批**当场重生成**（带失败原因），合格才 merge |
| `plot_detail` 只给上下限 → 模型取最小值 | **给它一个必须填满的结构模板**（见下） |
| `segment_goals_identical` 事后检测 | 生成 segments 时**代码分配 arc 主题**（从 `Volume.arcs` 取），模型只能填内容不能填主题 |
| 标题重复事后检测 | **为每章分配 `arc_goal` + `locale_id` + `beat_type` 三元组**，标题必须从这个三元组派生 → 天然不重复 |

**`plot_detail` 的结构化模板**（取代现在的一坨文本）：

```json
{
  "chapter_idx": 42,
  "locale_id": "L12_藏经阁",
  "beats": [
    {"n":1, "sub_location":"藏经阁·三层禁书区", "cast":["楚渊","柳清尘"],
     "goal":"找到《逆魂禁典》残页", "obstacle":"禁制会记录翻阅者", "result":"拿到残页，但触发留痕",
     "is_turn":false, "opens":[], "closes":[], "escalates":["P03_溯魂代价"]},
    {"n":2, "sub_location":"藏经阁·三层禁书区", "cast":["楚渊","玄清真人"],
     "goal":"在被发现前脱身", "obstacle":"玄清真人堵住唯一出口", "result":"被迫交出一半残页",
     "is_turn":true, "opens":["P07_玄清的真正目的"], "closes":[], "escalates":[]},
    ...
  ],
  "entry_state": {"locale_id":"L12_藏经阁", "sub_location":"一层入口", "time_offset_minutes":180,
                  "present":["楚渊"], "knowledge":{"楚渊":["F12"]}, "unresolved":["P03"]},
  "exit_state":  {"locale_id":"L12_藏经阁", "sub_location":"三层禁书区", "time_offset_minutes":45,
                  "present":["楚渊","玄清真人"], "knowledge":{"楚渊":["F12","F19"],"玄清真人":["F19"]},
                  "unresolved":["P03","P07"]},
  "end_hook": "玄清真人收走半页残卷，说『另一半在你师兄手里』——楚渊想起张昊已死。"
}
```

**好处**：
- `locale_id` 是外键 → **地理熵可精确统计**（§10.2）；
- `present` 是 ID → 人物一致性可校验；
- `knowledge` 是 dict → **"谁知道什么"第一次成为可计算量**（现在只是自然语言）；
- `opens`/`closes`/`escalates` → **Promise Ledger 自动更新**（§10.4）；
- `beats[]` 结构化 → `outline_lint` 的「场景行数/格式」检查变成 schema 校验（不需要正则）；
- **每个 beat 都有 `sub_location`** → 「同一地点连续 N 章」可直接测量。

**代价**：token 增加约 40%（同样的信息量，JSON 比散文冗长）。
但 §2.3 已证明大纲只占全书 token 的 6% —— **+40% 大纲 = 全书 +2.4%。完全可以接受。**

### 10.7 前三章优先：`golden_opening` 的工程化

**用户明确要求「前几章最重要」。** 现有系统的 `golden_opening`（`story_director.py:15`）：

```
1. 黄金三章：第1章绝境/变故落地+亮出核心设定，第2章第一次小冲突小爽点，第3章锁定全书长期主线并留长钩子；
```

**但它是 `_clip(g.get(k), 80)` 截断到 80 字的自然语言**（`story_director.py:488`），
且只在 `_story_anchors_block` 里作为一段 JSON dump 注入（`architect.py:2785`）——
**没有校验、没有逐章对齐、正文层看不到。**

**建议**：
1. **前三章单独走一条高质量路径**：不与其他章共享批处理；
2. **每章独立的验收标准**（可机器校验）：
   - Ch1：`locale_id` 已建立 + 主角 want/need 已显式 + 核心设定已亮 + 至少 1 个 promise opened；
   - Ch2：第一次小冲突**闭环**（有结果，不是悬念）+ 至少 1 次"爽点"（`beat_type ∈ {climax, turning}`）；
   - Ch3：长期主线锁定（`promise.kind == "goal"` 且 `due_by >= 总章数 × 0.5`）+ `long_hook` 落地；
3. **前三章生成 N 个候选，用 LLM judge + 人工可选**（成本极低：3 章 × 3 候选）；
4. **前三章一经确认即冻结为 `canon`**，后续所有章节的 `state` 校验都从 Ch3 的 `exit_state` 起算。

### 10.8 LangGraph 落地形态

```python
class StoryState(TypedDict):
    # —— 永恒层（全书只生成一次，之后只读）——
    series: SeriesBible              # premise/theme/ending_promise
    world_ladder: WorldLadder
    faction_tree: list[FactionTier]
    antagonist_ladder: list[Antagonist]
    milestone_ladder: list[Milestone]

    # —— 卷层（每卷生成一次）——
    current_volume: int
    volume_plan: VolumePlan          # locale/new_locales/new_factions/antagonist_tier/arcs

    # —— Arc 层（每 arc 生成一次，取代"段"）——
    current_arc: ArcPlan             # arc_goal/locale_scope/entry_condition/exit_condition/beats

    # —— 章层（每章）——
    chapter_plan: ChapterPlan        # plot/beats[]/entry_state/exit_state/end_hook

    # —— 全局账本（每步更新，是"故事压力"的载体）——
    promise_ledger: PromiseLedger
    story_pressure: StoryPressure
    locale_registry: dict[str, Locale]
    canon_states: dict[ChapterRef, SceneState]

graph = StateGraph(StoryState)

graph.add_node("series_architect",   make_series_architect)    # 1 次/书
graph.add_node("volume_architect",   make_volume_architect)    # 1 次/卷
graph.add_node("arc_architect",      make_arc_architect)       # 1 次/arc
graph.add_node("chapter_detailer",   make_chapter_detailer)    # 1 次/批
graph.add_node("structure_validator", validate_structure)      # 【新增】硬闸门
graph.add_node("pressure_gate",       check_story_pressure)    # 【新增】G1/G2/G3
graph.add_node("prose_writer",        write_chapter)
graph.add_node("ledger_updater",      update_ledgers)

# 关键边：validator 失败 → 回退到生成节点（带失败原因），不是"记录后放行"
graph.add_conditional_edges("structure_validator", route_on_validation,
    {"ok": "pressure_gate", "retry_detail": "chapter_detailer",
     "retry_arc": "arc_architect", "retry_volume": "volume_architect"})
graph.add_conditional_edges("pressure_gate", route_on_pressure,
    {"ok": "prose_writer", "need_new_locale": "arc_architect",
     "need_new_promise": "chapter_detailer"})
```

**与现有系统的关键差异**：
1. **`structure_validator` 是阻断节点，不是 shadow**（公理 2）；
2. **失败回退到正确的层级** —— 细纲不合格回 `chapter_detailer`，
   arc 缺换图回 `arc_architect`，卷缺新势力回 `volume_architect`。
   现有系统只有「重试同一批」一条退路（`_fill_plots` 的 3 轮）；
3. **`story_pressure` 是一等 State** —— 它必须随每章更新，且是所有"该不该继续原地展开"判定的依据。

### 10.9 迁移路径（遵守仓库既有的分阶段上线协议）

`AGENTS.md:39-61` 的 9 步协议（baseline → flag → 并存 → shadow → fixture → quality → token → 切默认 → rollback）
必须遵守。建议顺序：

| 阶段 | 内容 | 判据 |
|---|---|---|
| P0 | **只加仪表**：`geography_entropy` / `story_pressure` / `promise_ledger` 的**只读计算**，跑在现有 outline 上 | 在 book 23/24/18 上产出基线（预期：地理熵 ≈ 0） |
| P1 | **只加 schema**：`Locale` / `Promise` / `SceneState` 数据结构 + validator，**shadow 模式** | validator 在历史书上的假失败率 < 10% |
| P2 | **换图配额上线（唯一一条先行的硬规则）** | 因为它是抱怨 #5 的直接对因，且在 shadow 期已证明可行 |
| P3 | **Arc 取代段**（`arc_goal` + `locale_scope` + `entry/exit_condition`） | fixture：同一 prompt 下 arc 边界章节的 Editor Intervention Rate 对比 |
| P4 | **Promise Ledger + G3 压力闸门** | 质量对比：重复标题组数、地理熵、Editor consistency 问题数 |
| P5 | **反派/势力阶梯 validator** | 阻断式 |
| P6 | **前三章独立路径** | 人工评审 |

**每个阶段的 rollback 开关必须是独立的**（现有系统把 `rolling`、`detail_lint`、`detail_review`
三个不相关的开关塞在同一个设计里，导致「开 lint 就停写」这种耦合）。

### 10.10 立即可用的最小改动（不改架构，只补最缺的一块）

如果要先要一个**一周内见效**的改动，按性价比排序：

1. **在 `_BLUEPRINT_BLOCK` 里加 3 个字段 + 3 条硬规则**（`architect.py:2070`）：
   ```
   "new_locales":["本卷新开地点（≥2，须给出与上一卷的空间关系）"],
   "new_factions":["本卷新登场势力（≥2，须给层级）"],
   "antagonist_tier":本卷对手层级（须高于上一卷）,
   【扩张硬性】每卷至少 2 个新地点、2 个新势力；同一地点连续出现不得超过 12 章；
   反派层级必须逐卷递增；卷首换图是权利不是例外。
   ```
   **成本：30 行 prompt。** 直接命中抱怨 #5。

2. **把 `_normalize_segments` 的 `_child()` 改成不共享 goal**（`architect.py:867-872`）：
   ```python
   def _child(plan: dict, chapters: int, first: bool, idx: int, total: int) -> dict:
       if first:
           return {**plan, "chapters": chapters}
       return {**plan, "chapters": chapters,
               "goal": f"{plan.get('goal','')}（续：本子段推进至其第 {idx}/{total} 阶段，须换场或引入新信息量）",
               "summary": "", "summary_source": "plan_split_tail"}
   ```
   **成本：5 行。** 修掉 book 24 上正在复发的「子段 goal 逐字相同」。

3. **修 `_clean_milestones` 的 `ms[:8]`**（`story_director.py:494`）→ `ms[:12]`，
   使 `min_milestones=9` 可达。**成本：1 个字符。**

4. **`_guard_vol1_end` 之后重新 `_clip` `vol_end_hook`**（`story_director.py:89`）。
   **成本：1 行。**

5. **`_load_filled_chapters_before` 去掉 `volume != 1` 硬编码**（`pipeline.py:3052`）。
   **成本：1 行。** 打开跨卷继承。

6. **`outline_lint` 的 `state_break` 从"精确相等"改为"locale_id 相等 + knowledge 单调"**
   （`outline_lint.py:126-143`）。**成本：约 30 行。** 让状态连续性检查真正可用。

7. **【最高优先级】删掉 `continue_story.py:1255` 的硬编码卷目标**（§7.4），
   改为调用 `StoryArcPlannerAgent` 或至少按卷号分档生成不同目标。
   **成本：约 20 行。** 它正在给每一部续接书的每一个续接卷写入逐字相同的指令。

8. **续卷章补写细纲**：在 `extend_outline` 落库后追加一次 `_fill_plots`（复用现有路径），
   或至少在 `Chapter(...)` 构造里带上 `plot_detail` / `end_hook` / `entry_state` / `exit_state`。
   **成本：约 15 行。** 不做这一步，续卷路径在 `detail_convention=True`（默认）下**必然死锁**
   （§7.5），这解释了全部超长书都停在卷 1/卷 2。

9. **`continue_story.py:1213` 的 `participants = list(active_names)`（全员 30 人）**
   改为按本卷 arcs 的 `cast` 取，普通章 ≤6、大场面 ≤10，与主路径同口径。**成本：约 10 行。**

---

### 10.11 重写时的「先做/后做」一览

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0** | 续卷路径的 3 处硬伤（§10.10 的 7/8/9） | 当前**所有**超长书都死在卷 1/卷 2；不修则重写后同样跑不到 1500 章 |
| **P0** | `WorldLadder` + 换图配额（§10.2） | 抱怨 #5 的直接对因；实测地理熵 = 0 |
| **P1** | 结构化 `SceneState`（§10.5） | 让"纵向不许断"第一次可判定；解锁 lint 阻断 |
| **P1** | `Promise Ledger` + 压力闸门 G1/G2/G3（§10.4） | 把"配额"换成"压力"，治啰嗦与日常注水 |
| **P1** | Arc 取代段（§10.1） | 段是编排单位不是叙事单位；不换层就无处挂 `locale_scope` |
| **P2** | 反派/势力阶梯 validator（§10.3） | 幕后天梯是网文长篇的核心爽点，现完全缺失 |
| **P2** | 前三章独立路径（§10.7） | 用户明确要求；成本低（3 章 × N 候选） |
| **P3** | `conflict_curve` / `character_arcs` / `milestone_ids` 落地或删除（§4.6/§4.7） | 公理 2：要求 ≠ 执行；要么校验要么删，别留着 |

**必须保留的既有不变量**（重写时最容易丢，丢了会静默失效）：
`_seed_outline_skeleton`（骨架先播种进 outline）· `_carry_forward_outline_plot_fields`
（定稿时保住细纲）· `_notify_batch_done` 的返回值契约 · `on_segment_done` 必须在 checkpoint 之后 ·
merge+commit 必须先于 kick pregen · Detail fail-closed 不越缺口 · 实测并发峰值不得用配置常量冒充。

---

## 附：关键数字速查

| 指标 | 值 | 来源 |
|---|---|---|
| ultra 规格 | 1000~1700 章 / 3~6 卷 / 300~500 章每卷 / seg=50 | `scales.json:118-133` |
| 第 1 卷规划粒度 | ≈400 章 = 全书的 27% | `book 24` 实测 447 章 |
| 段间传递的故事内容 | 240 字梗概 + 5 章标题 | `architect.py:1050-1120` |
| 细纲继承窗口 | 最近 40 章 ∩ 2500 字符 ≈ 25 章 | `architect.py:1760-1761` |
| 1500 章下的继承覆盖率 | 25/1500 ≈ **1.7%** | 计算 |
| `plot_detail` p50（修前/修后） | **179.5 → 788** 字 | 书 1005 / `book 24` |
| `plot_detail` 下限 | 900 字（**p50 788 未达标**） | `outline_config.json` |
| `entry_state` 填充率 | **58/447 = 13%** | `book 24` 实测 |
| `milestone_ids` 填充率 | **0/447 = 0%** | `book 24` 实测 |
| 重复标题组（书 1005 / book 24） | **29 组/65 章 → 16 组/33 章** | AGENTS.md / 实测 |
| **后半本新地点数（book 23/24/18）** | **0 / 0 / 0** | 实测（§5.5） |
| 换图许可次数/全书 | 2~5 次（仅卷首） | `architect.py:372` 等 |
| 单批细纲大小 | 3 章（配置瓶颈，非预算） | `outline_config.json` + `fill_batch_size_for` |
| 大纲 LLM 调用（第 1 卷 400 章） | ≈161 次 | 计算 |
| 大纲 token（400 章） | 3.93 M（architect） | `book 23` 实测 |
| 大纲 token 占全书比 | **≈33%（book 23）/ ≈6%（1500 章外推）** | `learnstory.db` |
| 全书 1500 章预估 token | ≈**2.6 亿** | 外推 |
| `novelty_report` 1500 章耗时 | 28.85 s（O(n²)，仅按需触发） | `novelty.py:18-20` |
| 唯一生成期防重调用点 | `pipeline.py:7313`（近邻重复**放行**） | `guard_config.json` |
| 续卷章写入的字段数 | **9 个**（无 `plot_detail`/`end_hook`/`entry_state`/`exit_state`/`*_fs`） | `continue_story.py:1216-1227` |
| 续卷章 `participants` | **全员 30 人广播**（主路径约束 ≤6/≤10） | `continue_story.py:1213` |
| 续卷卷目标的来源 | **Python 字面量**「延续破局点开启的新剧情线，逐步推进至终局」 | `continue_story.py:1255` |
| 续卷失败兜底 | `plot="剧情推进中…"` 填满整批 | `continue_story.py:1170` |
| 生产超长书实际卷进度 | book 18: 4 卷规划 / 停在卷 1；book 23: 5 卷 / 卷 1；book 11: 4 卷 / 卷 2 | `learnstory.db` |

---

*报告完 · 全部结论均附 `file:line` 或实测数据；未写入 `story_llm` 任何文件。*
