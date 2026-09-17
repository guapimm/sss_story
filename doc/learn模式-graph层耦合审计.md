# learn 模式（知识小说）— `graph/` 层耦合审计

审计范围：`src/storyforge/graph/` 下 13 个文件。全部逐行读过，行号为 `read`/`grep` 工具口径。

---

## 0. 三个先看的事实（它们决定了整个改造的规模）

### 事实 1：★ 契约里承诺的 `modes/` 扩展层**根本不存在**

- `src/storyforge/domain/enums.py:27-31` —— `BookMode` 已定义，注释写着「一期只实现 STORY（决策 D-08），**LEARN 保留接口不实现**」。
- `src/storyforge/infra/storage/models.py:83` —— DB 列 `books.mode` 已落地。
- `tests/contract/test_architecture_invariants.py:133-154` —— 规则 3 的断言消息是「所有模式差异必须经 **`modes/` 层的 Protocol**（决策 D-08）」。
- `src/storyforge/modes/__init__.py` —— **0 行，空文件**。`grep 'Protocol|ABC|abstractmethod|Strategy'` 在**整个 `graph/` 目录零命中**。

**结论：预留机制是空的。** learn 模式今天能拿到的扩展点数量是 **0**。任何 story 假设都只能靠「改共享代码」或「加 `if`」来解决——而后者被规则 3 禁止。

### 事实 2：禁止模式分支的边界是 `domain/` + `graph/nodes/`，**不含 `graph/graphs/`**

规则 3 的循环是 `for sub in ("domain", "graph/nodes")`（`test_architecture_invariants.py:140`），且白名单判据是「import 了 `storyforge.modes` 就整文件跳过」（`:144-145`）。

→ **`graph/graphs/`（`books.py` / `chapter.py` / `chapter_nodes.py` / `executor.py`）今天仍可合法地按模式分支**，是一条现成但**未被使用**的逃生通道。这直接改变了 `chapter_nodes.py` 的处置方式（见 §2.12）。

### 事实 3：伤势机器**在生产路径上完全没接线**

`HealthScheduler.schedule()` 的全部调用点都在 `tests/` 里（`test_detail_planner.py`、`test_injury.py`）。生产调用点 **0 个**：

- `graphs/books.py:303-314` 调 `build_detail_batch(...)` 时 **没有传 `health_schedule`** → 默认 `None`（`detail_planner.py:602`）→ `detail_planner.py:672` 的 `if health_schedule is not None` 恒假 → **`health_beats_injected` 永远为 0**；
- `graphs/books.py:361` 仍在把这个永远为 0 的值写进 state。

**含义（对 learn 是好消息，对 story 是坏消息）：**
1. 作为**运行时**耦合，伤势机制目前是**惰性**的——learn 不用为它写适配器，因为它根本没跑。
2. 作为**编译期**耦合，它是硬的——`detail_planner.py` 顶层 import 了 `domain.schedulers.health_scheduler`（L57-60），只要 learn 复用这个文件就继承该依赖。
3. 反过来说，story 模式有一条**独立的 P0 缺陷**：`health_beats_injected` 这个 state 字段目前是**纯装饰**（规则 17「禁止只写不读的信号」的同类形态）。接线时必须在 `books.py` 的 `plan()` 里补 `HealthScheduler(...).schedule(...)`。

---

## 1. `state.py` — 263 行（任务书给的 167 我读不到；`read`、`grep` 两次独立确认 `BookState`@53、`__all__`@255，全文件 263 行）

> 注：本仓库部分文件行尾混用 LF/CRLF。子代理环境里 `read` 与 PowerShell 给出的行数不一致（如本文件 263 vs 215 vs 167），**本报告一律采用 `read`/`grep` 口径**，因为两者相互印证。

**职责**：定义 6 个 LangGraph `TypedDict` state（`BookState` / `BibleState` / `VolumePlanState` / `SkeletonState` / `DetailState` / `ChapterState`），只放 ref/hash/小标量。

**归属判断：`mixed`（偏 core）** —— 这是**全书唯一的模式耦合咽喉**。

**判断依据**：`BookState` 的 8 个字段里 6 个是纯编排元数据（`book_id` / `run_id` / `target_chapters` / `refs` / `done_units` / `current_chapter`）。真正的问题不在字段本身，而在**它是所有 stage state 的唯一基底类**：`BibleState(BookState)`、`VolumePlanState(BookState)`、`SkeletonState(BookState)`、`DetailState(BookState)`、`ChapterState(BookState)` —— 5 个子类全部继承同一套卷/章字段。learn 若采用「单元 → 单元」以外的层级形状，改这一个类就会**同时命中全部 5 个 stage 图**。

### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/state.py:63 — `#: 总卷数（由全卷台阶规划确定，**不硬编码**）` / `total_volumes: int` — 卷是 story 的层级单位。learn 若按「知识模块」组织，字段名虽中性但语义已叙事化；注释里「台阶规划」直接引用了升级台阶概念。
src/storyforge/graph/state.py:66 — `current_volume: int` — 三级流水线的卷轴。learn 的知识序列未必有「卷」这一层（可能是 章→节→知识点）。
src/storyforge/graph/state.py:97-108 — `class VolumePlanState` / `series_plan_ref` / `gate_ok` — 整个 stage 的存在理由就是「全卷升级台阶」（L93-95 注释原文：「全卷升级台阶阶段（对策②核心）」）。learn 没有「升级台阶」，这个 stage 在 learn 里是**空转的**。
src/storyforge/graph/state.py:152-153 — `#: 本批注入的伤势节拍数（供观测）` / `health_beats_injected: int` — 伤势是武侠/战斗叙事专属。learn 的「知识小说」没有角色伤势，此字段恒定 0（事实上今天在 story 里也恒 0，见事实 3）。
src/storyforge/graph/state.py:203-216 — `is_volume_end: bool` / `injuries_block: str` / `previous_tail: str` / `plan_detail: str` — `is_volume_end` 绑定卷结构；`injuries_block` 直接是伤势上下文块；`previous_tail`「上一章结尾原文」预设了「章与章连续叙事」。
src/storyforge/graph/state.py:215-216 — `known_entities: list[tuple[int, str, list[str]]]` — 实体归一账本。这是全文件**最接近 core** 的字段（知识小说也要追踪「概念/术语」），但它是为「人物/势力/地点」设计的。
```

### 扩展点评估：(b) 在 `BookState` 上加扩展点

- **具体位置**：`state.py:53` `class BookState(TypedDict, total=False)`。
- **做法**：把 5 个 stage state 的卷/章字段抽成 `HierarchyState`（`total_volumes` / `current_volume` / `current_chapter` / `is_volume_end`），让 story 的 `BookState` 继承它；learn 提供自己的层级混入。**不要**在 `BookState` 里加 `book_kind` 字段——那正是规则 3 要防的。
- **`health_beats_injected`（L153）**：应随伤势机制一起**下沉到 story 专属 state 子类**，不要留在共享基底里。
- **注意 `ChapterState`（L177-252）是最大的单块**：它有 40+ 字段，其中 `injuries_block`(L208)、`plan_detail`(L212)、`is_volume_end`(L202) 是 story 专属，而 `prose_ref`/`scorecard`/`review_*`/`extraction_*`/`committed` 是 core。**建议按「写作闭环 state」+「叙事上下文块」拆两段。**

---

## 2. `nodes/` 逐文件

### 2.1 `nodes/bible_architect.py` — 334 行

**职责**：生成全书世界观圣经（稳定前缀第一块），含零 LLM 结构校验 + 失败带问题清单重试。

**归属判断：`mixed`**

**判断依据**：算法骨架是 **domain-neutral** 的——「产出一份全书不变、逐字节稳定的设定文档，并用确定性规则校验其结构完整性」对知识小说**完全成立**（知识小说的「圣经」是知识框架/术语体系/前置约定）。但**校验器的每一条判据都是按小说设定的段落形状写的**。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/bible_architect.py:45 — `BIBLE_ARCHITECT_SYSTEM = """你是长篇小说的世界观架构师。...` — prompt 第一句就把角色钉死为「长篇小说」。learn 需要「知识架构师」。
src/storyforge/graph/nodes/bible_architect.py:47 — `圣经将在**全书每一章**被注入给写作模型` — 预设「章」为写作单元。
src/storyforge/graph/nodes/bible_architect.py:55-56 — `- \`power_system\`：力量/能力体系的**层级与边界**——...这是防止后期战力崩坏的关键。` — 词表枚举 + 战力崩坏是**武侠/升级流专属**。
src/storyforge/graph/nodes/bible_architect.py:75 — `"power_system": "力量/能力体系的层级与边界：层次、晋升、代价、做不到什么"` — JSON 骨架里把它列为**必填字段**。
src/storyforge/graph/nodes/bible_architect.py:112-115 — `_MIN_TOTAL_CHARS = 1200` / `_MIN_WORLDVIEW_CHARS = 60` / `_MIN_RULES = 6` / `_MIN_TERMS = 4` — **四个模块级常量，无任何注入点**。标定依据是「旧项目 5 本书的真实 bible.txt 实测 4231~7631 字」（L100-107）——**按小说圣经标定的阈值**。知识小说的知识框架可能是表格/公式密集、字数少但信息量大，1200 字下限会系统性误拒。
src/storyforge/graph/nodes/bible_architect.py:153-157 — `if not bible.power_system.strip(): issues.append("缺力量/能力体系 ...")` — **硬闸门**：不写力量体系直接判失败。
src/storyforge/graph/nodes/bible_architect.py:179 — `person_markers = ("勇敢", "善良", "聪明", "坚强", "主角", "为人")` — 用「性格形容词」判「这不像世界法则」。知识小说里「主角」不存在，但「勇敢/善良」这类词可能合法地出现在知识举例中。
src/storyforge/graph/nodes/bible_architect.py:180 — `lead_pronoun = re.compile(r"^\s*(他|她|它|他们|她们)\s*[的很是会能]")` — 同上，按**叙事人称**判据。
src/storyforge/graph/nodes/bible_architect.py:199-221 — `def bible_to_markdown(...)` — 输出章节标题硬编码为 `## 【世界观】` / `## 【纪元与年代】` / `## 【力量/规则体系】` / `## 【世界硬规则】` / `## 【术语与专名】`。learn 的知识框架渲染不出这五个标题。
```

#### 扩展点评估：(b) 加扩展点，两处

1. **`bible_to_markdown`（L199）+ `BIBLE_ARCHITECT_SYSTEM`（L45）+ `validate_bible`（L131）三件套应打包成一个「Bible schema/渲染/校验」策略对象**。这是整个文件里最清晰可注入的缝：它已经是三个独立的纯函数，只是被 `build_bible`（L242）硬连在一起。建议定义 `BibleSpec` Protocol（`system_prompt` / `render` / `validate` / `min_chars`），story 与 learn 各给一份实现。
2. **阈值（L112-115）必须可注入**，不能做模块级常量。

---

### 2.2 `nodes/volume_planner.py` — 433 行

**职责**：一次规划**全部卷**的升级台阶（`new_locations` / `new_factions` / `antagonist` / `escalation_from_prev` / 里程碑），过结构闸门，失败走增量补缺。

**归属判断：`story-only`（本组最纯的 story 节点之一）**

**判断依据**：这个文件的**存在理由本身**就是「防止长篇困在一个地方 + 对手阶梯 + 力量上限」。它不是「规划一个单元层级」这种中性算法，而是「规划**叙事升级曲线**」——节点 docstring 开篇即 `对策②，痛点 5`，L61 标题 `## 最重要的要求：横向扩张是必填项`。`VolumePlan` 的字段名（`power_ceiling` / `escalation_from_prev` / `antagonist` / `volume_end_hook` / `old_map_closure`）没有一个是领域中性的。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/volume_planner.py:59 — `VOLUME_PLANNER_SYSTEM = """你是长篇小说结构规划师。为给定的主题规划**全部卷**的升级台阶。` — 「升级台阶」在 system prompt 第一行。
src/storyforge/graph/nodes/volume_planner.py:63-65 — `长篇小说的最大失败模式是**全书困在一个地方**。真实案例：一本 400 章的规则怪谈小说...` — 直接举小说案例作为规划目标。
src/storyforge/graph/nodes/volume_planner.py:69-73 — `\`new_locations\`（≥2 个）...\`new_factions\`（≥2 个）...\`antagonist\` —— 本卷主要对手。**必须与既往卷不同**（长篇需要对手阶梯）。` — **对手阶梯 = power-escalation ladder**。learn 的知识模块之间**不需要**「更强的对手」。
src/storyforge/graph/nodes/volume_planner.py:74-76 — `\`escalation_from_prev\` —— 相对上卷「升级在哪」...禁止「推向X的下一个阶段」这类模板句。` — 升级语义。
src/storyforge/graph/nodes/volume_planner.py:116 — `"power_ceiling": "本卷力量/能力上限",` — JSON 骨架里的**力量上限**字段。
src/storyforge/graph/nodes/volume_planner.py:120 — `"volume_end_hook": "卷末钩子",` — 悬念/钩子机制。
src/storyforge/graph/nodes/volume_planner.py:119 — `"old_map_closure": "旧地图如何收尾",` — 地图（叙事空间）概念。
src/storyforge/graph/nodes/volume_planner.py:85-90 — `\`milestones\`：卷内里程碑，**数量必须 ≥ 卷章数 ÷ 25**...**且最大间隔不超过 25 章**（否则中间会出现情节真空）` — 「情节真空」是叙事判据。
src/storyforge/graph/nodes/volume_planner.py:189-206 — `VolumePlan(volume_no=..., power_ceiling=v.power_ceiling, escalation_from_prev=..., old_map_closure=..., volume_end_hook=...)` — **领域对象构造点，字段一个不少地硬写**。
src/storyforge/graph/nodes/volume_planner.py:266-275 — `cfg = gate_config or GateConfig(enabled=settings.structure_gate.enabled, new_locations_per_volume_min=..., new_factions_per_volume_min=..., milestone_max_gap=..., location_reuse_ratio_max=..., volume_share_min=..., volume_share_max=..., chapters_per_volume_floor=...)` — 闸门配置**全部从全局 settings 读**，该函数没有 `mode` 参数。
src/storyforge/graph/nodes/volume_planner.py:377-399 — `load_series_plan(...)` — 反序列化时**逐字段硬还原** `power_ceiling` / `antagonist` / `old_map_closure` 等（L385-389）。即使 learn 用了不同的 `VolumePlan` 子类，这个函数也读不出来。
```

#### 扩展点评估：(c) 需要并行实现 —— 但**上游定义**可以复用

- `gates 之外几乎无可复用**。`series_plan_from_out`（L149）里唯一中性的是**卷内序号 → 全书号换算**（L166-187，含 `Milestone` 的 `key` 由代码生成这一条经验）。建议抽 `assign_global_offsets(volumes) -> list[(start,end)]` 供两侧复用。
- `_repair_prompt`（L215）的「增量补缺、禁止全量重写」是**通用 LLM 工程经验**，值得保留成共享工具。
- **`plan_volumes`（L239）本身建议 learn 不用**；learn 应写自己的 `plan_modules`，复用 `structure_gate.evaluate_*` 的思路但配一套知识侧判据。

---

### 2.3 `nodes/skeleton_planner.py` — 536 行

**职责**：生成**单卷**故事线骨架（core/relationship/opposition/sub 四类线 + 段落目标 + 角色 + 势力 + 地点），零 LLM 校验 + 容错匹配落实度。

**归属判断：`story-only`（语义层）/ `core`（算法层）—— 这是本审计里分裂最严重的一个文件**

**判断依据**：文件内有两种截然不同的代码。
- **算法层（约 L142-279）是漂亮的中性文本匹配工具**：`_lcs_len`（最长公共**子串**）、`_name_and_gloss`（拆「主名（括注）」）、`_normalize_for_match`（去标点）、`_mentions`（三级由严到宽判定）。它的 docstring 明确说「只做『是否出现』的确定性判断，不做语义判断」（L223-224）——**这对 learn 的「知识点是否落实」是逐字适用的**。
- **语义层（L48-105 prompt + L108-115 指标 + L282-391 校验）完全是叙事结构**。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/skeleton_planner.py:48 — `SKELETON_PLANNER_SYSTEM = """你是长篇小说结构师。为**当前这一卷**规划故事线骨架。` — role framing。
src/storyforge/graph/nodes/skeleton_planner.py:54-58 — `\`kind\` 取值：· \`core\` —— 主线（**至少 2 条**）· \`relationship\` —— 关系线（**至少 2 条**，讲人与人的关系如何变化）· \`opposition\` —— 对立线（**至少 2 条**，讲与谁对抗、赌注是什么）· \`sub\` —— 支线` — **硬编码的 beat/arc 类型词表**：core/relationship/opposition/sub。关系线与对立线对知识小说基本无意义。
src/storyforge/graph/nodes/skeleton_planner.py:56-57 — `讲人与人的关系如何变化` / `讲与谁对抗、赌注是什么` — 直接定义叙事语义。
src/storyforge/graph/nodes/skeleton_planner.py:61-64 — `\`characters\`：本卷主要角色（3~6 个）。\`factions\`：本卷登场势力（4~8 股）...\`places\`：本卷用到的**地点**（3~8 个）` — 三张角色/势力/地点表。
src/storyforge/graph/nodes/skeleton_planner.py:74-77 — `2. **关系线不能只有"同门/师徒"**...3. **对立线要写出赌注**：输了会失去什么（不是"打不过"而是具体代价）。` — 武侠黑话（同门/师徒）+ 对抗语义。
src/storyforge/graph/nodes/skeleton_planner.py:109-115 — `MIN_ARCS = 6` / `MAX_ARCS = 12` / `MIN_CORE_ARCS = 2` / `MIN_RELATIONSHIP_ARCS = 2` / `MIN_OPPOSITION_ARCS = 2` / `MIN_CHARACTERS = 3` / `MIN_FACTIONS = 4` — **7 个模块级常量，零注入点**。
src/storyforge/graph/nodes/skeleton_planner.py:299-303 — `checks = (("core", MIN_CORE_ARCS, "主线"), ("relationship", MIN_RELATIONSHIP_ARCS, "关系线"), ("opposition", MIN_OPPOSITION_ARCS, "对立线"),)` — **类型 → 下限 的硬编码元组**。这是本文件最硬的 story 假设：learn 想校验「概念线/例题线/证明线」必须重写这张表。
src/storyforge/graph/nodes/skeleton_planner.py:276-278 — `k = re.sub(r"^(主线|支线|暗线|辅线)[·\.\-—:：]?", "", k)` / `k = re.sub(r"[（(](core|主线|支线|暗线|辅线|核心)[)）]$", "", k)` — `_arc_core_key` 的**去前缀正则里直接写了中文线名**。防的是模型「换前缀凑数」。
src/storyforge/graph/nodes/skeleton_planner.py:325 — `thin = [a.key for a in arcs if len((a.description or "").strip()) < 10]` — 描述 ≥10 字下限（这条尚算中性）。
src/storyforge/graph/nodes/skeleton_planner.py:336-338 — `f"势力只有 {len(facs)} 股（要求 ≥{MIN_FACTIONS}）—— 旧项目势力实体实测 0 个，长篇需要鲜活的势力格局"` — 势力格局是 story 判据。
src/storyforge/graph/nodes/skeleton_planner.py:463-470 — `if not volume.new_locations or not volume.new_factions: return SkeletonResult(..., error=f"第 {volume.volume_no} 卷的台阶缺少 new_locations / new_factions —— 骨架无法落实扩张要求")` — **硬前置条件**：没有新地图/新势力**直接拒绝生成**。learn 的模块规划不产出这两个字段 → 这个函数对 learn **开箱即崩**。
src/storyforge/graph/nodes/skeleton_planner.py:415, 425-429 — `f"本卷升级：{volume.escalation_from_prev}"` / `【本卷必须落实的横向扩张】` / `- 新地图（必须在故事线中用到）` — prompt 里把升级与扩张写成必给输入。
src/storyforge/graph/nodes/skeleton_planner.py:432 — `pts = "、".join(f"第{m.chapter_no}章 {m.key}" for m in volume.milestones[:12])` — 「第N章」框架。
```

#### 扩展点评估：(b) 内部一刀两断

1. **`_mentions` / `_lcs_len` / `_name_and_gloss` / `_normalize_for_match` / `_STRIP_CHARS` / `_MENTION_MIN_LCS`（L142-264）→ 抽到 `graph/textmatch.py`，learn 零改动复用。** `continuity.py:26` 的注释已明确说「复用既有匹配器，**刻意不新写第二个**」——这个决定是正确的，抽出来即可服务两侧。
2. **`validate_skeleton`（L282）的 checks 元组（L299-303）与 7 个常量（L109-115）→ 必须参数化**。最小改法：加一个 `SkeletonSpec` 参数（含 `kind_floor: Mapping[str,int]`、`min_arcs`、`min_characters`、`min_factions`、`required_volume_fields`）。
3. **`build_skeleton` 的硬前置（L463）→ 必须可关**。这是 learn 复用的**第一位阻塞点**。

---

### 2.4 `nodes/detail_planner.py` — 733 行（全组最大）

**职责**：分批生成逐章细纲（4~8 个场景拍点），注入伤势节拍，跑两道零 LLM 闸门（`ChapterPlan.lint` + `validate_beats_against_scenes`）。

**归属判断：`story-only`** —— 并且是**本组耦合最深、最难救**的文件。

**判断依据**：
- 伤势机器**完整地长在这个文件内部**：`attach_health_beats`（L252-303）、`lint_chapter`（L313-315）、`format_health_beats`（L576-585）、`build_detail_batch` 的 `health_schedule` 参数（L602）与注入点（L672-673），以及 prompt 里的伤势节拍段（L535-543）与 `_inject_chapter_numbers` 之后的 `attach` 调用。
- `ChapterPlan` 这个领域对象的结构决定了一切：`beat_type` / `plant_foreshadowings` / `resolve_foreshadowings` / `end_hook` / `health_beats` 全是叙事字段（`domain/outline.py:236-254`）。
- **这个文件满足契约规则 10 的 800 行上限（733），本身已接近容量极限。**

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/detail_planner.py:49 — `from storyforge.domain.enums import BeatType` — 顶层 import，learn 复用即继承。
src/storyforge/graph/nodes/detail_planner.py:57-60 — `from storyforge.domain.schedulers.health_scheduler import (HealthSchedule, HealthScheduler,)` — **顶层 import 伤势调度器**。这是编译期硬耦合。
src/storyforge/graph/nodes/detail_planner.py:69 — `DETAIL_PLANNER_SYSTEM = """你是长篇小说细纲规划师。为给定的章号区间写出**逐章细纲**。` — role framing。
src/storyforge/graph/nodes/detail_planner.py:74 — `（旧项目实测：29 个重复标题覆盖 65/300 章，全是「深夜的敲门声」这类同义反复）` — 举例是小说章标题。
src/storyforge/graph/nodes/detail_planner.py:76-77 — `- \`beat_type\`：节拍类型。取值 \`normal | opening | turning | climax | boss | emotion | recall | settle | heal | recovery_prep | transition\`。` — **★ 硬编码的 11 个 beat 类型**，其中 `climax` / `boss` / `emotion` / `recall` / `heal` / `recovery_prep` 全部是叙事专属。这是全审计里最重要的硬编码之一。
src/storyforge/graph/nodes/detail_planner.py:84 — `- \`scenes\`：**4~8 个**分场景拍点。每个拍点必须四项齐全：\`goal\`+\`obstacle\`+\`outcome\`+\`why_now\`` — 场景拍点范式（目标-阻碍-结果-为何是现在）。知识小说的一章可能是「定义→定理→证明→例题」，不是「目标→阻碍→结果」。
src/storyforge/graph/nodes/detail_planner.py:92 — `\`open_thread\`（章末**具体**未收束的事：谁在做什么、什么没解决）` — 悬念收束。
src/storyforge/graph/nodes/detail_planner.py:93-94 — `- \`end_hook\`：章末钩子 —— 本章现场内**悬而未决的事件本身**。**禁止**「莫名的波动」「心头一紧」这类无主暗示。` — **cliffhanger machinery**。
src/storyforge/graph/nodes/detail_planner.py:105 — `5. **标题两两不同**，且不要用「XX的XX」同构句式批量生产。` — 小说章标题风格约束。
src/storyforge/graph/nodes/detail_planner.py:157-159 — `MIN_SCENES = 4` / `MIN_PLOT_CHARS = 12` — 模块级常量。
src/storyforge/graph/nodes/detail_planner.py:252-303 — `def attach_health_beats(plan, schedule) -> int` — **伤势节拍注入器**：均匀把节拍摊到 `[0, scene_count-1]` 的真实场景上（L282-296）。
src/storyforge/graph/nodes/detail_planner.py:298-302 — `hint = slot.beat_type_hint; if hint and plan.beat_type in (BeatType.NORMAL, BeatType.SETTLE): plan.beat_type = BeatType(hint)` — **调度器直接改写章节目拍类型**（HEAL / RECOVERY_PREP）。这是「伤势日程 → 章节角色」的耦合点。
src/storyforge/graph/nodes/detail_planner.py:313-315 — `issues += HealthScheduler.validate_beats_against_scenes(list(plan.health_beats), len(plan.scenes))` — **第二道闸门绑定伤势**。learn 没有 health_beats → 这道闸门恒空过（无害但也无用）。
src/storyforge/graph/nodes/detail_planner.py:406-407 — `"plant_foreshadowings": list(plan.plant_foreshadowings), "resolve_foreshadowings": list(plan.resolve_foreshadowings),` — **伏笔台账**（埋/收）序列化。
src/storyforge/graph/nodes/detail_planner.py:473-474 — 反序列化侧同样硬还原伏笔字段。
src/storyforge/graph/nodes/detail_planner.py:535-543 — `"【伤势节拍·已由调度器排定，必须落到场景里】"` / `"（寻医=求诊场景；求药=取得药物；恶化=伤情发作；疗伤尝试=施治过程；好转=见效；痊愈=明确治愈且有代价）。"` — prompt 里逐条列出**伤势动作 → 场景**的映射表。
src/storyforge/graph/nodes/detail_planner.py:602 — `health_schedule: HealthSchedule | None = None,` — 参数默认 None，但**函数体内无条件依赖**（L621-625、L672-673）。
src/storyforge/graph/nodes/detail_planner.py:604 — `max_rounds: int = 2,` — 重试上限（中性）。
```

#### 扩展点评估：(c) **需要并行实现**，且是本组最贵的

- `build_detail_batch`（L593）的**重试循环骨架**（L632-701：调用 → 解析 → 注入 → `validate_batch` → 记录 `last_plans` → 带问题清单重试）是**中性的 LLM 工程模式**，值得抽成 `run_batch_with_lint(...)` 高阶函数，两侧共用。
- `_inject_chapter_numbers`（L559）、`validate_batch`（L319）的**缺章/重复标题/邻章连续性**检查（L335-365）→ 大部分可复用（「标题两两不同」对 learn 也成立）。
- **`DETAIL_PLANNER_SYSTEM` + `MIN_SCENES`/`MIN_PLOT_CHARS` + `ChapterPlan` 的 beat/foreshadow/health 字段** → learn 需要独立的 prompt 与独立的计划对象。
- **`attach_health_beats`（L252）与 `format_health_beats`（L576）应随伤势机制一起迁出**到 `modes/story/` 或 `domain/schedulers/`，不要留在共享节点文件里——否则 learn 被迫 import 一个永远不用的函数，而它顶层还拉着 `HealthScheduler`。

---

### 2.5 `nodes/chapter_writer.py` — 441 行

**职责**：写单章正文：组装三层 prompt（stable/semi/volatile）、长度闭环（不足续写）、拒答检测、落盘。

**归属判断：`mixed`（骨架 core，上下文装配 story）**

**判断依据**：这是全组**最接近 core** 的节点。
- **完全中性**：`REFUSAL_PATTERNS`（L56-63，中英道歉/AI 自称/要求澄清/元说明/代码块包裹）、`CONTINUE_INSTRUCTION`（L66-73）、`detect_refusal`（L211）、`count_chars`（L224）、`looks_like_restart`（L233）、长度闭环（L337-388）、`persist_prose`/`load_prose`（L406/L418）。**「写文本 → 检测它不是正文 → 不够长就续写 → 太长只记录不截断」对知识小说逐字成立。**
- **story 专属**：`render_plot_detail`（L101）、`render_health_beats`（L123）、`assemble_context`（L161）的字段集。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/chapter_writer.py:43 — `from storyforge.domain.enums import BeatType` — 顶层 import（仅为 `__all__` 重导出，L440）。
src/storyforge/graph/nodes/chapter_writer.py:101-120 — `def render_plot_detail(plan: ChapterPlan) -> str:` — 把 `plan.scenes` 渲染成「目标/阻碍/结果/为何是现在」四段硬约束文本（L111-114）。
src/storyforge/graph/nodes/chapter_writer.py:116-119 — `if plan.plant_foreshadowings: lines.append("本章需埋下伏笔：" + "、".join(plan.plant_foreshadowings))` / `if plan.resolve_foreshadowings: lines.append("本章需回收伏笔：" + "、".join(plan.resolve_foreshadowings))` — **伏笔台账进 prompt**。★ 注意：全仓库**没有任何地方填充这两个字段**（prompt 也不要求模型输出）——它们目前是**恒空**的死字段，与 `health_beats_injected` 同型。
src/storyforge/graph/nodes/chapter_writer.py:123-135 — `def render_health_beats(plan) -> str:` — 伤势指令渲染（`f"- 第{b.scene_seq + 1}个场景：{b.note}"`，L134）。
src/storyforge/graph/nodes/chapter_writer.py:181-203 — `return ChapterContext(global_no=..., volume_no=..., title=..., plot=..., plot_detail=render_plot_detail(plan), end_hook=plan.end_hook, ..., injuries=injuries, foreshadow_plant=list(plan.plant_foreshadowings), foreshadow_resolve=list(plan.resolve_foreshadowings), health_beats=render_health_beats(plan), beat_type=plan.beat_type.value, is_transition=plan.is_transition, ...)` — **`assemble_context` 是 20+ 个 story 字段的硬拼装点**。
src/storyforge/graph/nodes/chapter_writer.py:188-189 — `next_entry_state=render_state(next_plan.entry_state) if next_plan else ""` — 双锚点机制（上一章出口 + 下一章入口）。这**对知识小说也可用**（上一节结尾 → 下一节开头），但字段名 `entry_state/exit_state` 是叙事化的。
```

#### 扩展点评估：(b) 只换 `assemble_context` 一层即可

- **`render_plot_detail`（L101）与 `render_health_beats`（L123）是唯一需要 learn 替换的部分**。`write_chapter`（L255）已经接受 `states` / `relations` / `injuries` / `timeline` / `previous_tail` / `style_rules` 作为**字符串参数**——即 prompt 上下文已经是**注入式的**，只是 `assemble_context` 把它们拼进了固定的 `ChapterContext`。
- **最小改造**：把 `assemble_context` 换成按 spec 选的两个实现（story 用现有，learn 用「知识点/例题/公式」渲染），`write_chapter` 主体、长度闭环、拒答检测**零改动**。
- `ChapterContext` 定义在 `infra/prompts/library.py:139-140`（`health_beats` 字段）——**不在本次审计范围**，但它是 `assemble_context` 的返回类型，learn 若要换字段集必须一并处理。

---

### 2.6 `nodes/reviewer.py` — 328 行

**职责**：风格审校（调 `style_critic` prompt）+ 定向重写（按 issues 类别只强调命中的规则）+ **修订必须过闸门才算数**。

**归属判断：`mixed`**

**判断依据**：三条核心机制**全部是领域中性的**：
1. `issues` 非空 ⇒ `revised_content` 必填（schema 层强制，L16-18）；
2. `has_usable_revision` 由代码判定，**不采信模型自述**（L20-22）；
3. `apply_revision_if_better`（L259）——**修订稿重新打分，分数不升则拒绝采用**（L298-316）。这是「防止重写空转」的通用闭环，对知识小说同样必要。

但**类别词表与每条类别的规则文本全是小说专属**。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/reviewer.py:55-84 — `CATEGORY_RULES: dict[str, str] = {...}` — **★ 硬编码的 8 个审校类别 → 补救规则映射**。这是本文件最硬的耦合：它是「审校 issue.category」到「重写指令」的**唯一下游**（L188 `rules = [CATEGORY_RULES.get(c, CATEGORY_RULES["other"]) for c in categories]`）。
src/storyforge/graph/nodes/reviewer.py:56-61 — `"ai_style": ("- **AI 味**：删掉套话（仿佛/瞬间/不禁/嘴角勾起/眼中闪过…）；减少比喻，一章最多留 2~3 处；**不要直接命名情绪**...")` — 比喻配额、情绪命名禁令 = 小说文风判据。
src/storyforge/graph/nodes/reviewer.py:62-66 — `"pacing": (..., "对话占比过低时，把部分叙述改成对话呈现。")` — **对话占比**，知识小说里通常是 0。
src/storyforge/graph/nodes/reviewer.py:67-70 — `"consistency": ("- **一致性**：严格对照上方人物状态表（伤势/装备/境界/位置）改写冲突处；角色不得知道状态表里他不知道的事。")` — **伤势/装备/境界**三件套。
src/storyforge/graph/nodes/reviewer.py:75-77 — `"foreshadow": ("- **伏笔**：本章该埋的伏笔要有具体落点，该回收的要当场兑现。")` — 伏笔类别。
src/storyforge/graph/nodes/reviewer.py:78 — `"ooc": "- **人物**：回到该角色的口癖与立场，不得为剧情需要而失真。"` — OOC（人物崩坏）。
src/storyforge/graph/nodes/reviewer.py:79-82 — `"outline_deviation": ("- **细纲**：逐条核对细纲场景，把遗漏的关键转折补上。（若本章是换场章，换地图/换时间是**正常**的，不算偏离。）")` — 换场/换地图。
src/storyforge/graph/nodes/reviewer.py:49 — `STYLE_CRITIC_SYSTEM_FILE = "style_critic"` — system prompt 从**文件名硬编码**加载（L151 `_load(STYLE_CRITIC_SYSTEM_FILE)`，实际文件 `infra/prompts/style_critic.md`）。
src/storyforge/graph/nodes/reviewer.py:218 — `system = _load("writer_system")` — 重写复用 writer 的 system prompt（同样是小说 writer）。
src/storyforge/graph/nodes/reviewer.py:196-197 — `"请**只针对下列问题**修改，其余内容保持原样（不要重写整个故事线，不要改变情节走向，不要增删场景）。"` — prompt 里的故事线/情节。
src/storyforge/graph/nodes/reviewer.py:232-256 — `def _thresholds() -> Thresholds:` — 20 个阈值全部从 `get_settings().scorecard` 单例读，**函数无参数**。
src/storyforge/graph/nodes/reviewer.py:255 — `always_review_chapters=tuple(s.always_review_chapters),` — 配置驱动的「黄金三章无条件审」。
```

#### 扩展点评估：(b) 把 `CATEGORY_RULES` 与 prompt 文件路径参数化

- **`CATEGORY_RULES`（L55）是最干净的注入点**：改成 `category_rules: Mapping[str,str] | None = None` 参数，或从 spec 对象取。
- **`STYLE_CRITIC_SYSTEM_FILE`（L49）与 `_load("writer_system")`（L218）**：learn 需要自己的 critic prompt 文件（`_load` 接受任意文件名，**改造量极小**）。
- **`_thresholds()`（L232）**：应接受 `Thresholds` 参数而非只读全局。
- `apply_revision_if_better`（L259）的**决策逻辑本身零改动**——它只比较两个 `Scorecard` 的 `score`。**前提是 learn 有一张打分卡**（见 §3 风险）。

---

### 2.7 `nodes/extractors.py` — 376 行

**职责**：三个独立抽取节点（`scene_extractor` / `entity_resolver` / `relation_extractor`），各带自己的 schema + 零 LLM 一致性校验。

**归属判断：`mixed`（`entity_resolver` 是 core，另两个偏 story）**

**判断依据**：
- `ENTITY_RESOLVER_SYSTEM`（L88-109）**是领域中性的**：「把提及名对齐到既有实体 ID 或给出规范名；同一实体不得并存两个规范名；别名不要丢；不要臆造 ID」——**这正是知识小说需要的「术语/概念归一」**（「梯度下降」「最速下降法」「GD」应统一）。算法与 prompt 都无需改。
- `SCENE_EXTRACTOR_SYSTEM`（L56）与 `RELATION_EXTRACTOR_SYSTEM`（L112）含大量叙事假设。
- 但**架构决策本身（拆三个节点，每个只抽一类）是 core 级的经验**：docstring L14 的结论「一次抽太多的直接后果不是『抽得差』，而是『什么都不抽』」是通用 LLM 工程结论。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/extractors.py:56 — `SCENE_EXTRACTOR_SYSTEM = """你是小说记录员。根据本章正文与当前记忆账本，抽取结构化记忆增量。` — role framing。
src/storyforge/graph/nodes/extractors.py:64 — `2. **区分事实与说法**。角色说的话不一定是世界真相。` — 「角色」概念。
src/storyforge/graph/nodes/extractors.py:71 — `- \`events\`：2~5 条关键事件，每条含故事内时间、地点、参与者、描述、后果。` — 故事内时间 + 事件链。
src/storyforge/graph/nodes/extractors.py:72-75 — `- \`state_updates\`：人物**可变状态**的变化（位置/境界/目标/情绪/装备等）。...装备用 \`+物品名\`/\`-物品名\` 表示获得/失去...不变属性（外貌/出身/天赋）严禁出现在这里。` — **★ 状态词表直接含「境界」**；装备获得/失去是叙事语义。
src/storyforge/graph/nodes/extractors.py:76-78 — `- \`injury_updates\`：损伤变化。**必须**给出 severity 与 status。好转/痊愈/解除**必须**能在 \`events\` 里找到对应的治疗事件，否则不要报恢复类状态。` — **伤势抽取**（HEAL/RECOVERY 链的抽取侧）。
src/storyforge/graph/nodes/extractors.py:79 — `- \`deaths\`：本章死亡的角色名。**漏记会导致该角色复活的世界级 Bug。**` — **死亡台账**。
src/storyforge/graph/nodes/extractors.py:80-83 — `- \`entities\`：本章出现的实体（人物/势力/地点/物品/功法/规则/术语）。\`kind\` 必须用规定的取值。**「势力」与「组织」都是合法值**。\`intro_method\` 标注引入方式；若是凭空出现或无依据回忆，标 \`appeared_out_of_nowhere\` / \`baseless_recall\` / \`baseless_knowledge\`。` — **★ 硬编码 entity kinds**：人物/势力/地点/物品/功法/规则/术语（对齐 `domain/enums.py:77-95` 的 `EntityKind`，其中 `TECHNIQUE="功法"`、`FACTION="势力"` 是武侠词）。
src/storyforge/graph/nodes/extractors.py:112 — `RELATION_EXTRACTOR_SYSTEM = """你是社会关系抽取器。**只抽人物/势力之间的社会关系**。` — 社会关系抽取，知识小说基本不需要。
src/storyforge/graph/nodes/extractors.py:133-134 — `- \`surface_facing\`：**世人所知**的关系表现（如「师徒」）。\`true_facing\`：**真实立场**。与 surface 不同时即暗线张力。` — 暗线张力（叙事）。
src/storyforge/graph/nodes/extractors.py:273-278 — `_ACTION_WORDS: frozenset[str] = frozenset({"查看", "记录", "离开", "拾取", "值守", "翻找", "听见", "看到", "走进", "走出", "打开", "关上", "坐下", "站起", "拿起", "放下",})` — **16 个中文动作词的硬编码黑名单**。
src/storyforge/graph/nodes/extractors.py:306-362 — `def validate_scene(out, *, planned_beats=None, global_no=0, scene_count=0, injuries=None) -> list[str]:` — 校验器签名带 `planned_beats` / `injuries`（伤势节拍 + 损伤清单）。
src/storyforge/graph/nodes/extractors.py:340-361 — `if not injuries: return problems` … `check = validate_recovery_claim(injury, target_status=status, chapter_no=global_no, beats=beats, scene_count=scene_count)` — **恢复申报必须有结构化 HealthBeat 支撑**（L327 局部 import `validate_recovery_claim`）。这是「恢复率 0%」修复链的抽取侧闸门。
src/storyforge/graph/nodes/extractors.py:334-338 — `if out.deaths and not out.events: problems.append(f"报了 {len(out.deaths)} 个死亡但没有任何事件支撑 ...")` — 死亡必须有事件支撑。
```

#### 扩展点评估：(a)+(b) 混合 —— **`entity_resolver` 直接复用，另两个换 prompt/schema**

1. **`resolve_entities`（L212）+ `ENTITY_RESOLVER_SYSTEM`（L88）+ `render_known_entities`（L162）→ learn 零改动复用**（只需 learn 侧也维护一张 `[(id, 规范名, 别名)]`）。
2. **`extract_scene` / `extract_relations` → learn 需要新的 system prompt + 新的 Out schema**。当前签名（L186/L246）已只接受 `prose` / `known` 字符串，**形状是中性的**，换 prompt 即可。
3. **★ `_ACTION_WORDS`（L273）应迁出**到 story 侧——它是纯故事假设的黑名单，learn 复用时是死代码。
4. `render_known_entities` 里的 `"（本章是全书第一章，尚无既有实体）"`（L168）——「第一章」措辞需中性化。

---

### 2.8 `nodes/continuity.py` — 79 行

**职责**：相邻两章连续性检查——判「有没有过渡」，而不是「地点名是否相等」。

**归属判断：`mixed`（逻辑 core / 判据 story）**

**判断依据**：docstring 的**核心洞察完全是领域中性的**：L9-19 记录了一次真实事故——「原先只做 `exit_state != entry_state` 的**字符串比对**，假阳性 100%（同一栋楼换房间、纯缩写 `市公安局` vs `市局` 全被误判）→ **做对的工作被校验器扔掉了**」。这是「**校验器的假阳性会毁掉正确的工作**」这条通用工程结论。

但**三条判定规则里有两条绑死在叙事字段上**：

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/nodes/continuity.py:53 — `from storyforge.domain.enums import BeatType` — 顶层 import。
src/storyforge/graph/nodes/continuity.py:54 — `from storyforge.domain.outline import ChapterPlan` — 签名绑死叙事领域对象。
src/storyforge/graph/nodes/continuity.py:55 — `from storyforge.graph.nodes.skeleton_planner import _mentions` — **跨文件 import 私有函数**（下划线前缀）。抽公共模块时必须一并处理。
src/storyforge/graph/nodes/continuity.py:67 — `if nxt.is_transition or nxt.beat_type == BeatType.TRANSITION: return []` — **第 ② 条判据 = 换场信号**（`is_transition` 布尔旗 + `beat_type == TRANSITION` 双信号）。
src/storyforge/graph/nodes/continuity.py:61-62 — `a_loc = (prev.exit_state.location_name or "").strip()` / `b_loc = (nxt.entry_state.location_name or "").strip()` — 第 ①/③ 条判据全部围绕 `location_name`（地点）。
src/storyforge/graph/nodes/continuity.py:76 — `return prev.exit_state.continuity_problems(nxt.entry_state)` — 委托给领域对象（`domain/outline.py:114`）。
src/storyforge/graph/nodes/continuity.py:33-36 — 举例全部是「铁门关驿丞署（驿站）」「秦牧的柴房（杂役坊）」「仁华医院病房」——古代/现代小说的空间。
```

#### 扩展点评估：(b) 参数化匹配器，或 (a) 部分复用

- **可零改动复用的只有「三级由严到宽的判定顺序」这个模式**（L21-26：先看是不是真的没变 → 再看有没有显式声明 → 最后做容错匹配）。
- **具体实现绑死 `location_name`**。learn 的「连续性」是**知识不矛盾 / 术语一致 / 前置知识已讲过**，不是地点。→ 需要 learn 自己的 `knowledge_continuity_issues(prev, nxt)`。
- **`_mentions` 的复用应当通过抽到 `graph/textmatch.py` 完成**，而不是继续 import 私有下划线函数——现在的写法会让 learn 与 story 一起耦合在 `skeleton_planner` 上。

---

### 2.9 `graphs/executor.py` — 98 行

**职责**：把 LangGraph 图接到 orchestrator 上——`WorkItem` ↔ 图 state / 输出 ↔ `ConfirmPoint` 的**适配器**。

**归属判断：`core` —— ★ 纯机械，learn 零改动复用**

**判断依据**：这个文件里**没有任何领域语义**。它做三件事，全部是编排水管：
1. `GraphUnitExecutor._state_for`（L40）：`state = dict(item.payload)` 再机械合并 `book_id` / `run_id` / `current_volume` / `batch_no`（L49-57）——注释原文「本方法只做**机械合并**，其余透传」。
2. `_stage_key`（L61）：`PipelineStage` → 图名字符串的**静态映射表**（L63-68）。
3. `_confirm_point_of`（L76）：从图输出读 `awaiting_confirm`，`ConfirmPoint(raw)` 转换失败**只 warning 不静默当已确认**（L89-91）。

**唯一需要注意的一点**：`_stage_key`（L63-68）的映射表**少了 `PipelineStage.CHAPTER`**——写 `learn` 的 stage 时若新增枚举值，这里会 `KeyError`。这不是 story 假设，是**完整性缺口**。

**★ 硬编码的 story 假设清单：无。**

`current_volume`（L54）字段名带「卷」，但它是纯透传默认值，learn 只要不用这个键就不受影响。

#### 扩展点评估：(a) 原样复用

learn 只要在 `books.py` 的 `build_all`（L383）里注册自己的图名并扩 `_stage_key` 映射即可。

---

### 2.10 `graphs/chapter.py` — 146 行

**职责**：单章闭环图的**接线**（readiness → write → score → review → revise → extract → commit）+ C-7 质量闸门判定。

**归属判断：`core`（接线层）**

**判断依据**：这个文件的全部内容是**路由函数 + 图装配**，共 6 个 5 行的纯路由（L93-108）和 7 个 `add_node`（L111-117）。路由条件全部读 state 里的小标量（`status` / `needs_review` / `review_ref`）——**没有一个条件判体裁**。

`needs_c7_confirm`（L63-79）是**纯函数**，逻辑是「重写次数达上限且仍未过闸门 → 暂停等人工」或「写作本身失败」——这是**成本止损**，对 learn 同样成立（docstring L70-71：「存在的意义是**止损**」）。

#### ★ 硬编码的 story 假设清单：实质为「无」

```
src/storyforge/graph/graphs/chapter.py:23 — `无条件审：黄金三章（1/2/3）/ 卷末 / 高潮·Boss 章（D-14 C-5）` — 仅出现在 **docstring** 里；实际判定在 scorecard 的 `forced_review`（见 2.12），本文件只是描述。
src/storyforge/graph/graphs/chapter.py:42-43 — `from storyforge.config import get_settings` / `from storyforge.domain.enums import ConfirmPoint` — 中性依赖。
src/storyforge/graph/graphs/chapter.py:73 — `limit = max_rewrites if max_rewrites is not None else get_settings().scorecard.max_rewrites` — 读全局配置，但**已有 `max_rewrites` 覆盖参数**，注入点已存在。
```

#### 扩展点评估：(a) 原样复用

- 唯一的模式差异是**「哪些章必须无条件送审」**——而这**已经不在这个文件里**：`chapter_nodes.py:222` 调 `should_review(card)`，最终由 `scorecard` 的 `forced_review` 决定。learn 只需换打分卡的阈值/强制规则，**本文件零改动**。
- `build_chapter_graph(deps)` 接受 `ChapterDeps`，已经是**依赖注入**形态。

---

### 2.11 `graphs/chapter_nodes.py` — 450 行

**职责**：单章闭环图 7 个节点的**工厂函数实现**（`make_readiness_node` / `make_write_node` / `make_score_node` / `make_review_node` / `make_revise_node` / `make_extract_node` / `make_commit_node`）。

**归属判断：`mixed`（就绪闸门/提交校验 core，打分与抽取 story）**

**判断依据**：逐节点看：
- **`check_readiness`（L62-98）完全中性**，而且是本组设计得最好的函数之一：区分「ready / not_ready（正常调度态）/ blocked（真问题）」，注释 L81-82 明确「`not_ready` 是**正常的调度状态**，不是失败 → 干净退出而不是抛异常」。**learn 逐字复用。**
- **`make_commit_node`（L401-432）完全中性**：校验「正文在 + 抽取产物在」才允许 `committed=True`，且**不假装已入库**（L429 `"DB 入库待 infra/storage/repositories 实现"`）。**learn 逐字复用。**
- `make_write_node`（L165）的**长度闭环/拒答链在 `chapter_writer` 里**，这里只是取 state 块（L182-188：`volume_block` / `segment_block` / `states_block` / `injuries_block` / `timeline_block`）→ **story 上下文字段名硬连**。
- `make_score_node`（L211）与 `make_extract_node`（L337）→ 绑死打分卡与三类抽取。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/graphs/chapter_nodes.py:131 — `is_volume_end=bool(state.get("is_volume_end", False)),` — 卷末概念进打分。
src/storyforge/graph/graphs/chapter_nodes.py:130 — `beat_type=plan.beat_type.value,` — 节拍类型进打分（`_score_now`，L124-133）。
src/storyforge/graph/graphs/chapter_nodes.py:182-188 — `volume_block_text=state.get("volume_block", ""), segment_block_text=state.get("segment_block", ""), states=state.get("states_block", ""), injuries=state.get("injuries_block", ""), timeline=state.get("timeline_block", ""),` — **5 个 story 上下文块的硬连接名**。
src/storyforge/graph/graphs/chapter_nodes.py:308 — `beat_type=plan.beat_type.value,` — 重写复检再次用节拍类型。
src/storyforge/graph/graphs/chapter_nodes.py:101-109 — `def should_review(card: Scorecard, *, is_volume_end: bool = False) -> tuple[bool, str]:` — 送审路由（读 `card.forced_review` / `card.needs_review`）。**本函数中性**，但 `card` 来自 story 打分卡。
src/storyforge/graph/graphs/chapter_nodes.py:355-380 — `scene, c1 = await extract_scene(...)` / `entities, c2 = await resolve_entities(...)` / `relations, c3 = await extract_relations(...)` — **三类抽取被硬编码在图里**（顺序、调用数、problem 汇总）。learn 若只要 2 类（概念 + 归一），必须改这里。
src/storyforge/graph/graphs/chapter_nodes.py:363 — `mentions = [e.name for e in (scene.entities if scene else [])]` — 抽取链的**数据依赖**：resolve 的输入来自 scene 的 entities。
src/storyforge/graph/graphs/chapter_nodes.py:382-388 — `ref = ArtifactStore.extraction_key(book_id, global_no); deps.store.write_json(ref, {"scene": ..., "entities": ..., "relations": ..., "problems": problems})` — 产物形状硬编码为三键。
src/storyforge/graph/graphs/chapter_nodes.py:101-102 — `if card.forced_review:` — 无条件审的来源在打分卡（`scorecard/aggregate.py`，不在本审计范围）。
```

#### 扩展点评估：(b) **这是最该用「逃生通道」的文件**

按**事实 2**，`graph/graphs/` **不在规则 3 的扫描范围内**。因此本文件可以**合法地**按模式分派，无需先建 `modes/` Protocol：

- **最小方案**：把 `make_write_node` 的上下文字段读取（L182-188）与 `make_extract_node` 的抽取链（L355-388）抽成两个可选工厂参数，`ChapterDeps` 增加 `extractors: list[...]` 或 `context_builder`。
- **`check_readiness`（L62）与 `make_commit_node`（L401）→ learn 零改动复用。**
- **建议**：learn 不必复用 `make_extract_node`，直接写 `make_knowledge_extract_node` 并让 `build_chapter_graph` 收一个节点映射表。

---

### 2.12 `graphs/books.py` — 403 行

**职责**：四个阶段图的组装（bible / volume_plan / skeleton / detail_batch）+ checkpoint 管理 + `GraphDeps` 依赖容器 + 注册表 `build_all`。

**归属判断：`mixed`（图 1 骨架可复用，图 2/3/4 是 story 流水线的形状）**

**判断依据**：
- **可复用**：`open_checkpointer`（L82，SQLite + WAL + 幂等键 thread_id）、`thread_config`（L94，`recursion_limit=30`）、`GraphDeps`（L110，显式注入，注释 L112-116 说「旧项目 `pipeline.py` 直接在模块里 `from ..db import ...`，导致**无法在测试中替换**（121/123 个测试文件被迫改 `os.environ`）」）。
- **story 专属**：四个图的**语义**（圣经 / 全卷台阶 / 单卷骨架 / 细纲批次）本身就是 story 流水线的三级重叠形状（L5-13 的 ASCII 图）。

#### ★ 硬编码的 story 假设清单

```
src/storyforge/graph/graphs/books.py:129-156 — `def build_bible_graph(deps)` — 单节点图：`build_bible(topic=..., genre=...)`。**topic + genre 两参数即全部输入**——learn 可能需要「知识领域 + 难度 + 目标读者」，形状不同。
src/storyforge/graph/graphs/books.py:138 — `idempotency_key=f"bible:{state['book_id']}",` — 幂等键命名（中性）。
src/storyforge/graph/graphs/books.py:164-206 — `def build_volume_plan_graph(deps)` — 图节点直接调 `plan_volumes(...)` 并把 `"awaiting_confirm": "C4_volume_ladder"` 写进 state（L199）。
src/storyforge/graph/graphs/books.py:214-276 — `def build_skeleton_graph(deps)` — 节点内 `series = load_series_plan(deps.store, book_id)` 再 `volume = next((v for v in series.volumes if v.volume_no == volume_no), None)`（L226-229）——**硬依赖 SeriesPlan/卷结构**。learn 若没有 `SeriesPlan`，此图**直接不可用**。
src/storyforge/graph/graphs/books.py:232 — `"violations": [f"全卷台阶里没有第 {volume_no} 卷（不可静默跳过）"]` — 卷轴措辞。
src/storyforge/graph/graphs/books.py:240 — `idempotency_key=f"skeleton:{book_id}:v{volume_no}",` — 键含卷号。
src/storyforge/graph/graphs/books.py:284-375 — `def build_detail_batch_graph(deps)` — docstring L285「生成 + **注入伤势节拍** + lint + 落盘」。
src/storyforge/graph/graphs/books.py:303-314 — `result = await build_detail_batch(deps.client, book_id=book_id, volume=volume, bible_text=bible_text, batch_start=..., batch_end=..., idempotency_key=...)` — **★ 这是事实 3 的证据点：没有传 `health_schedule`**，因此伤势注入在生产路径上不生效。
src/storyforge/graph/graphs/books.py:361 — `"health_beats_injected": result.health_beats_injected,` — 把这个恒 0 的值写进 state（只写不读的装饰信号）。
src/storyforge/graph/graphs/books.py:367-368 — `if int(state["batch_start"]) <= 1 <= int(state["batch_end"]): out["awaiting_confirm"] = "C3_first_detail"` — 首个细纲批的确认点。
src/storyforge/graph/graphs/books.py:383-390 — `def build_all(deps) -> dict[str, CompiledStateGraph]: return {"bible": ..., "volume_plan": ..., "skeleton": ..., "detail": ...}` — **注册表硬编码 4 个 story 图名**，与 `executor._stage_key`（`executor.py:63-68`）一一对应。这是**唯一需要改的注册点**。
```

#### 扩展点评估：(b) 复用 L82-127 的编排基础设施，(c) 另建 4 个图

- **`open_checkpointer`（L82）/ `thread_config`（L94）/ `GraphDeps`（L110）→ learn 零改动复用**，且 `GraphDeps` 只需扩一个字段（如 `spec`）即可携带模式差异。
- **`build_all`（L383）→ 改成可注册**：接收 `Mapping[str, Callable[[GraphDeps], CompiledStateGraph]]` 或让 learn 追加自己的键。
- **`build_skeleton_graph`（L214）与 `build_detail_batch_graph`（L284）→ learn 需要并行实现**（它们硬读 `SeriesPlan` / 卷）。
- **`build_bible_graph`（L129）→ 可复用形状**（单节点 + 落盘 + refs 回填），只是入参 shape 不同。

---

## 3. 汇总表

| 文件 | 行数 | 归属 | learn 的改造方式 |
|---|---|---|---|
| `state.py` | 263 | **mixed**（全书耦合咽喉） | (b) 拆 `HierarchyState`；`health_beats_injected` 下沉 |
| `nodes/bible_architect.py` | 334 | mixed | (b) `BibleSpec` 三件套（prompt/render/validate）+ 阈值注入 |
| `nodes/volume_planner.py` | 433 | **story-only** | (c) 并行实现；仅 `series_plan_from_out` 的编号换算可抽 |
| `nodes/skeleton_planner.py` | 536 | **story-only**（语义）/ core（算法） | (b) 拆分：`_mentions` 等抽 `graph/textmatch.py`；checks/常量参数化；L463 硬前置必须可关 |
| `nodes/detail_planner.py` | 733 | **story-only** | (c) 并行实现；抽 `run_batch_with_lint` 骨架；伤势函数迁出 |
| `nodes/chapter_writer.py` | 441 | mixed（骨架 core） | (b) 只换 `assemble_context` + `render_plot_detail`；核心零改动 |
| `nodes/reviewer.py` | 328 | mixed | (b) `CATEGORY_RULES` + prompt 文件名 + `Thresholds` 参数化；`apply_revision_if_better` 零改动 |
| `nodes/extractors.py` | 376 | mixed | (a) `entity_resolver` 零改动；(b) 另两个换 prompt/schema；`_ACTION_WORDS` 迁出 |
| `nodes/continuity.py` | 79 | mixed | (b) 参数化匹配器；(c) learn 需自己的知识连续性检查 |
| `graphs/executor.py` | 98 | **core** | ★ **(a) 零改动**（只需补 `_stage_key` 映射） |
| `graphs/chapter.py` | 146 | **core** | ★ **(a) 零改动** |
| `graphs/chapter_nodes.py` | 450 | mixed | (b) 抽 `make_write_node` 上下文构建 + `make_extract_node` 抽取链；`check_readiness`/`make_commit_node` 零改动 |
| `graphs/books.py` | 403 | mixed | (b) 复用 `open_checkpointer`/`thread_config`/`GraphDeps`；`build_all` 改可注册；(c) 图 3/4 并行实现 |

**统计**：零改动复用 2 个文件（`executor.py`、`chapter.py`），零改动复用 2 个函数块（`check_readiness`、`make_commit_node`），其余 11 个文件都需要改动。

---

## 4. ★ 纯机械（PURE MECHANICS）—— learn 零改动、逐字复用

**这一节请当作硬结论使用。**

### 4.1 两个整文件

| 文件 | 为什么零耦合 |
|---|---|
| **`graphs/executor.py`（98 行）** | 三件事全是水管：① `_state_for`（L49-57）`state = dict(item.payload)` + 机械合并 4 个 ID 类字段；② `_stage_key`（L63-68）静态映射表；③ `_confirm_point_of`（L76-91）枚举转换 + 转换失败只 warning。**无一处领域语义。** |
| **`graphs/chapter.py`（146 行）** | 6 个纯路由（L93-108）+ 7 个 `add_node`（L111-117）+ `needs_c7_confirm`（L63，纯函数，判重写次数上限）。路由条件全部读 state 小标量（`status`/`needs_review`/`review_ref`），**无条件判体裁**。 |

### 4.2 同一个函数块级（在混合文件里）

- **`check_readiness`（`chapter_nodes.py:62-98`）** —— 三分结论 ready/not_ready/blocked；增量领先上限判定。**逐字复用。**
- **`make_commit_node`（`chapter_nodes.py:401-432`）** —— 「正文在 + 抽取产物在」才 `committed=True`；缺任一显式失败；不假装入库。**逐字复用。**
- **`open_checkpointer` / `thread_config` / `GraphDeps`（`books.py:82` / `:94` / `:110`）** —— checkpoint 生命周期 + 幂等键 thread_id + 显式依赖注入。**逐字复用**（`GraphDeps` 可扩字段）。
- **拒答检测与长度闭环（`chapter_writer.py:56-73` / `:211` / `:224` / `:233` / `:337-388`）** —— `REFUSAL_PATTERNS` 6 条正则、`CONTINUE_INSTRUCTION`、`detect_refusal`、`count_chars`、`looks_like_restart`。**全部中英通用，逐字复用。**
- **`_mentions` 家族（`skeleton_planner.py:142-264`）** —— `_lcs_len`（最长公共**子串**，含「为何不用子序列」的说明）/ `_name_and_gloss`（主名 vs 括注）/ `_normalize_for_match` / `_STRIP_CHARS` / `_MENTION_MIN_LCS=4`。**这是「中文实体名容错匹配」的通用工具，两边都要用。**
- **`resolve_entities` + `ENTITY_RESOLVER_SYSTEM` + `render_known_entities`（`extractors.py:212` / `:88` / `:162`）** —— 实体归一。**learn 的术语归一直接复用。**

### 4.3 记录在案的**死字段**（learn 不必适配，因为 story 自己也没跑）

审计中发现三处「声明了、渲染了、但从无写入方」的字段。learn **不要**把它们当成必须兼容的契约：

1. **`ChapterPlan.plant_foreshadowings` / `resolve_foreshadowings`**（`domain/outline.py:248-250`）—— `chapter_writer.py:116-119` 会渲染进 prompt，`detail_planner.py:406-407/473-474` 会序列化，但**没有任何 prompt 要求模型输出它们**，也没有解析赋值。恒空。
2. **`health_beats_injected`**（`state.py:153`）—— 由 `books.py:303` 未传 `health_schedule` 导致恒 0（事实 3）。
3. **`known_entities`**（`state.py:216`）—— `chapter_nodes.py:369` 读 `state.get("known_entities", [])`，但**全书没有写入点**，恒空列表。

---

## 5. 给 learn 的落地顺序建议（按阻塞强度排序）

1. **先建 `modes/` 层**（当前 0 行）。规则 3 的白名单是「import 了 `storyforge.modes` 就整文件跳过」——所以**只要 learn 的实现放在 `modes/learn/` 下并 import 它，就自动获得分派自由**。
2. **`graph/textmatch.py`**：把 `skeleton_planner.py:142-264` 抽出来。这是唯一被两个模块同时需要的工具，且 `continuity.py:55` 已经在跨文件 import 私有函数。
3. **`graph/state.py`**：拆 `HierarchyState`，让卷/章字段不再绑死在 5 个 stage state 的公共基底上。
4. **`books.py:build_all`** 改可注册 + `executor.py:_stage_key` 补映射（同时修掉 `PipelineStage.CHAPTER` 缺失）。
5. **`chapter_nodes.py:make_write_node` / `make_extract_node`** 参数化（此处**不需要** `modes/` Protocol，因为 `graph/graphs/` 不在规则 3 范围内）。
6. **`detail_planner.py` 与 `volume_planner.py`** 留到最后并行实现——它们的重试骨架值得抽，但语义层无法复用。

**顺带建议的 story 侧修复**（不属于本审计范围但已取证）：`books.py:303` 补 `health_schedule`，否则伤势调度器、`attach_health_beats`、`BeatType.HEAL/RECOVERY_PREP`、`validate_recovery_claim` 这一整条精心构造的链在**生产环境从未执行过**。
