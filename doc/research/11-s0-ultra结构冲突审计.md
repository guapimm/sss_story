# 11 · S0 ultra 结构冲突审计（只读取证，未改任何代码）

> **性质**：**只读审计**。本文不含任何代码改动；修复方案（§7）仅为草案，**未经负责人拍板前不实施**。
> **编制日期**：2026-09-26 · 基线 HEAD `098006b`（91 commits，文档清理后）。
> **已拍板背景**：修法方向已裁决为**选项 (c)「按 scale 声明放行」**（ultra-only，
> standard 档保留相对闸门、不得回退绝对硬闸门）。本文在该裁决下补齐它落地所需的
> 完整冲突矩阵、数据流证据与 standard 保护清单。
> **目标规格（唯一权威值）**：`story.ultra` ≈ **7–8 卷 × 150–250 章**（约 1500 章北极星）。

---

## 0. 一页摘要

三方对「ultra 该分几卷、每卷多少章」各执一词，且**数据流里根本没有 scale 这个概念**：

```
北极星/D-07        scales.toml story.ultra        structure_gate (share_min=0.15)
7~8 卷 × 150~250 ←─(冲突)─ 3~6 卷 × 300~500 ─(冲突)─ 每卷 ≥15% ⇒ 卷数 ≤6
```

数据流断点（逐跳核实，全部仍然成立）：

```
cmd_plan（无 --scale）→ BookState/VolumePlanState（无 scale 字段）
→ plan_volumes()（签名无 scale）→ prompt（只注入 target_chapters）
→ evaluate_series()（GateConfig 只来自全局 settings）
```

因此**只改 scales.toml 零效果** —— 这正是选项 (c) 的立项依据。

★ 本轮审计新发现（此前裁决未点名，均为逐行核实所得）：
1. `VOLUME_PLANNER_SYSTEM` 的 system prompt **明文教模型「不要写每卷 150~250 章式的
   绝对区间」「卷数不限」**（`volume_planner.py:81-84`）——它与 D-07 的目标**直接对立**，
   修复时该文案必须按 scale 分叉改写，否则即使闸门放行、数据流通了，
   模型仍会被这句话推向错误形态；
2. `scales.toml` story.ultra **自身**可行域矛盾（6 卷零可行解，§3.1）；
3. rule 文本中的「本次只细化第 1 卷」是旧项目病根原文、至今仍在线（§1.1）；
4. `_repair_prompt` 修复轮同样不含 scale 期望（§6-5）；
5. **share 检查逐卷生效、单卷不豁免**（§3.4 实测修正：全局检查的 `len>1` 豁免
   不适用于逐卷检查）——「400 章单卷被拦」是现状行为，修复不得改变。

---

## 1. 配置层清单（全档位 + 闸门双处默认）

### 1.1 `config/scales.toml`（加载器 `modes/scales.py`，缓存键含 mtime_ns）

| 档位 | chapters | vols | per_vol | segment | typical | 多卷? | rule 文本要点 |
|---|---|---|---|---|---|---|---|
| story.fast :31-45 | 3~10 | 1~1 | 3~10 | 0 | 6 | 否 | 单卷、结局发散 |
| story.standard :63-77 | 10~100 | 1~1 | 10~100 | 0 | 50 | 否 | 单卷、完整起承转合 |
| story.grand（共享 :13-27） | 100~300 | 1~1 | 100~300 | 30 | 200 | 否 | 单卷 |
| **story.ultra :95-109** | **1000~1700** | **3~6** | **300~500** | 50 | **400** | 是 | "**每卷 300~500 章，total_volumes 3~6**；…本次只细化第 1 卷"；终卷强收束 |
| learn.ultra :111-125 | 1000~1700 | 3~6 | 300~500 | 50 | 400 | 是 | "约 3~6 卷，卷数由知识拓展路线决定"（措辞与 story 不同） |

★ story.ultra 的 rule 文本含 **「本次只细化第 1 卷」** —— 这正是旧项目
「400 章挤第 1 卷」病根的原文（`volume_planner.py:5-8` 引用它作反面教材），
但该文本**至今仍是 scale 的权威声明**。修复时 rule 文本需整体重写（见 §7）。

### 1.2 结构闸门的三处定义（值一致，来源三份）

| 载体 | 位置 | share_min | share_max | floor | locations/factions | milestone gap |
|---|---|---|---|---|---|---|
| `StructureGateSettings`（pydantic 默认） | `config.py:318-326` | 0.15 | 0.60 | 20 | ≥2 | 25 |
| `settings.toml [structure_gate]` | `:84-103` | 0.15 | 0.60 | 20 | ≥2 | 25 |
| `GateConfig`（闸门自己的 dataclass 默认） | `structure_gate.py:124-155` | 0.15 | 0.60 | 20 | ≥2 | 25 |

★ `settings.toml ↔ config.py` 的一致性被 `test_settings_toml.py:112` 锁定
（任一侧改默认必须同步另一侧）。`GateConfig.shadow` 刻意不暴露为配置项
（`volume_planner.py:256-265`：防「只报告不阻断」回归）。

---

## 2. 数据流追踪（scale 的每一跳，「到不了」的证据）

| # | 跳 | 位置 | 现状 | scale 到了吗 |
|---|---|---|---|---|
| 1 | CLI 入口 | `cli.py:931-947` | plan 参数：`--book-id/--topic/--genre/--book-chapters(默认 400)/--chapters/--volume`。**无 `--scale`** | ❌ 断点① |
| 2 | target_chapters 来源 | `cli.py:613` | `args.book_chapters`（硬编码默认 400；**不读 scales.toml 的 typical_chapters**） | ❌（连"档位决定章数"都不存在） |
| 3 | 图状态 | `graph/state.py:53-108` | `BookState`/`VolumePlanState` 有 `target_chapters`/`total_volumes`，**无 scale 字段**（全文 grep `scale` 零命中） | ❌ 断点② |
| 4 | 图装配 | `graph/graphs/books.py:293-305` | `plan_volumes(..., target_chapters=state[...])`；**不传 `gate_config`**（走全局 settings 默认） | ❌ |
| 5 | 规划器签名 | `volume_planner.py:239-250` | `plan_volumes(client, *, book_id, topic, genre, target_chapters, bible_text, registry, gate_config, idempotency_key)` —— **无 scale** | ❌ 断点③ |
| 6 | 规划器 user prompt | `volume_planner.py:277-283` | 只注入 `topic/genre/target_chapters/bible` —— **无任何卷数/每卷章数期望** | ❌ |
| 7 | 规划器 system prompt | `volume_planner.py:59-93` | **明文反对绝对区间**（:81-84「不要写"每卷 150~250 章"式的绝对区间…卷数不限」）；里程碑密度 ≤25 章有教 | ❌ 且**主动与 D-07 对立**（本轮新发现） |
| 8 | 闸门装配 | `volume_planner.py:266-275` | `GateConfig` 仅从 `settings.structure_gate` 构建 —— **全局一份，无按档位差异** | ❌ |
| 9 | 闸门判定 | `structure_gate.py:158-226, 336-362` | 五类检查（见 §3.3）；share 区间仅 `len(volumes)>1` 时逐卷检查（:184、:348-350） | ✅（这是唯一真正拦截卷数的地方） |
| 10 | Estimator 展示 | `modes/story.py:255-264` | `scale_spec()` 有被消费（`min_volumes`/`per_volume`/`total_chapters_range`），但只进 **estimate 展示输出**，不进规划链路 | ⚠️ 仅展示层 |

**结论**：scale 全链路断裂于第 1 跳；第 7 跳的 system prompt 甚至在**反向教学**。
闸门的 share 区间是「卷数上限」的唯一执行者，而它对 ultra 与 standard 一视同仁。

---

## 3. 数学核算

### 3.1 `scales.toml story.ultra` 自身矛盾（不用等目标值，它自己就不自洽）

可行域 = `[vols × per_vol_lo, vols × per_vol_hi] ∩ [chapters_lo, chapters_hi]`：

| 卷数 | 需要总章数 | 与 [1000, 1700] 的交集 | 结论 |
|---|---|---|---|
| 3 | [900, 1500] | [1000, 1500] | 可行 |
| 4 | [1200, 2000] | [1200, 1700] | 可行 |
| 5 | [1500, 2500] | [1500, 1700] | 可行 |
| **6** | **[1800, 3000]** | **∅** | **不可行**（6×300=1800 > 1700） |

反方向：1700 ÷ 3 ≈ 567 > 500（per_vol_hi）→ 3 卷在 chapters_hi 端也不可达。
即声明域 {3,4,5,6} 卷中 **6 卷纯占用声明、零可行解**；1500 章（北极星典型值）
只与 3/4/5 卷相容。

### 3.2 `volume_share_min=0.15` 的卷数封顶

每卷 share = `chapter_count / target_chapters`，约束 `share ≥ 0.15`（`structure_gate.py:350`）：

- N 卷均分时 share = 1/N ⇒ N ≤ 1/0.15 = **6.67 ⇒ 卷数 ≤ 6**；
- 1500 章实测口径（与裁决记录一致）：3 卷(500)/5 卷(300)/6 卷(250) 通过；
  **7 卷(214)/8 卷(187.5) 每卷 share 14.3%/12.5% < 15% → 每卷各报一条
  `CHAPTER_DISTRIBUTION`，全卷全灭**；
- 即使不均分，D-07 合法解 7 卷 × [150,250]（总和 1500）的 share 范围
  10%~16.7%，**多数卷 < 15%** → 依旧大面积被拦。**这不是均分巧合问题，是结构性封顶。**

### 3.3 1500 章 × 7~8 卷 × 150~250 对三个载体的逐项对照

| 约束载体 | 7 卷 × ~214 章 | 8 卷 × ~187 章 |
|---|---|---|
| D-07（150~250/卷） | ✅ | ✅ |
| scales.toml per_vol 300~500 | ❌（214 < 300） | ❌ |
| scales.toml vols 3~6 | ❌（7 > 6） | ❌ |
| gate share_min 0.15 | ❌（14.3% < 15%，逐卷 7 条 issue） | ❌（12.5%） |
| gate floor 20 / milestone ≤25 / Σ=target ±10% | ✅（floor 20 无碍；milestone 需每卷 ≥8 个——prompt 已教） | ✅ |

**结论**：7~8 卷目标被**三个载体同时禁止**，任何一个单独修都无用 —— 这就是
「冲突矩阵」要表达的最小事实，也是选项 (c) 必须**三处联动**的原因。

### 3.4 ⚠️ 修正一版草稿后的实测结论：share 检查**逐卷生效，单卷不豁免**

`len(volumes) > 1` 的豁免**只存在于全局 `max_share` 检查**（`structure_gate.py:184`）；
**逐卷 share 区间检查（`:348-350`）对单卷同样生效**。本轮用真实 `evaluate_series` 实测：

| 样本 | 结果 |
|---|---|
| 400 章 × 4 卷 × 100 | ✅ ok（share 25% each） |
| 400 章 × **1 卷**（100% share） | ❌ **被拦**：`[chapter_distribution] 占全书 100%，超出 [15%, 60%]` |
| 1500 章 3 卷(500)/5 卷(300)/6 卷(250) | ✅ ok |
| 1500 章 7 卷(214)/8 卷(187) | ❌ 逐卷 7/8 条 `CHAPTER_DISTRIBUTION` |

两个推论：
1. **现状下任何 target_chapters > 0 的单卷规划都会被 share_max=0.60 拦下**
   （这正是书 23 防线：400 章必须分卷）。S0 真实取证（--book-chapters 400）
   产出 5 卷 × ~80 章正是闸门塑造的结果。
2. **超短档可行域空洞（新发现，不影响 ultra 主线）**：fast 档 3~10 章、
   standard ≤40 章的书在当前闸门下**无解**——单卷 share=100% > 60% 被拦；
   拆两卷则每卷 < floor 20 也被拦。这些档位从未走通过 plan 全链（现网只跑过
   默认 400 章），该空洞此前无人触达。见 §8-6。

---

## 4. 冲突矩阵（核心交付）

行 = 约束载体；列 = 关键属性；`★` = 与 7~8 卷目标直接冲突；`—` = 不适用。

| 载体（文件:行） | 卷数声明 | 每卷章数声明 | share 下限 | 对 7~8 卷目标 | 修复阶段必须动吗 |
|---|---|---|---|---|---|
| `scales.toml` story.ultra rule 文本（:108） | ★「total_volumes 3~6」 | ★「每卷 300~500 章」 | — | ❌ 双重禁止 | ✅ 数值+文本整体重写 |
| `scales.toml` story.ultra 数值字段（:100-103） | ★ vols 3~6 | ★ per_vol 300~500 | — | ❌ | ✅ |
| `scales.toml` learn.ultra（:111-125） | 3~6 | 300~500 | — | 裁决范围外（仅 story.ultra） | ❌ 保持（见 §8-3） |
| `settings.toml [structure_gate]`（:100） | —（不声明卷数） | — | ★ 0.15（每卷下限，副作用封顶 6 卷） | ❌ | ✅ 或由 scale 覆盖（§7） |
| `config.py StructureGateSettings`（:323） | — | — | ★ 0.15 | ❌ 同上 | ✅ 与 toml 双侧同步 |
| `structure_gate.py GateConfig`（:152） | — | — | ★ 0.15 | ❌ 同上 | ✅ 接受按 scale 注入 |
| `structure_gate.py share 检查`（:184 全局豁免单卷；:348-350 逐卷不豁免） | 隐式封顶（每卷下限的副作用）+ **单卷强制分卷**（share_max 对 100% 生效，本轮实测） | — | ★ | ❌ | ✅ 语义保留、阈值按 scale |
| `volume_planner.py system prompt`（:81-84） | ★「卷数不限」 | ★「不要写 150~250 式绝对区间」 | — | ❌ **反向教学**（本轮新发现） | ✅ 按 scale 分叉 |
| `volume_planner.py user prompt`（:277-283） | 无卷数期望 | 无每卷期望 | — | ⚠️ 模型只能瞎猜 | ✅ 注入 scale 声明 |
| `volume_planner.py 签名`（:239） | — | — | — | 断点③ | ✅ 加 scale 参数 |
| `graph/state.py`（BookState/VolumePlanState） | — | — | — | 断点② | ✅ 加 scale 字段 |
| `cli.py plan`（:931-947） | — | — | — | 断点① | ✅ 加 `--scale` |
| `modes/scales.py 加载器`（:148-151） | 正确读取 | 正确读取 | — | 数据就绪、无人消费 | ⚠️ 视方案（§8-2） |
| `modes/story.py Estimator`（:255-264） | 展示 min_volumes/per_volume | 同 | — | 仅展示层 | ❌ 可不动 |
| `books.py plan_volumes 调用`（:297-305） | — | — | — | 不传 gate_config | ✅ 透传 scale/覆盖 |

---

## 5. standard 档行为保护清单（修复阶段的回归红线）

**现行为（必须零变化）**：
1. fast/standard/grand → `vols_lo=vols_hi=1`（scale 声明单卷），但注意：**闸门对
   单卷规划同样执行 share_max=0.60**（逐卷检查不豁免单卷，§3.4 实测）——
   400 章默认 plan 实际被塑造成 5 卷 × ~80 章（S0 取证实录）。修复不得改变
   「单卷 100% 被拦」这条书 23 防线；
2. 相对闸门语义保留（每卷下限是"卷不能太小"，**不是卷数上限**——裁决明确
   不得把它当卷数上限写错）；绝对区间 [150,250] **不得回归**为全局硬闸门；
3. `plan` 现有调用（不带新参数）行为与 prompt 文本零漂移。

**锁定该行为的测试（修复时全绿 = 通过底线）**：

| 测试 | 锁定内容 |
|---|---|
| `test_structure_gate.py`（healthy_plan=3 卷×600 target，:66-77） | evaluate_series 全部检查项的正反例；`:143` 是**书 23 拦截测试中的 metrics 断言**（`max_volume_share==1.0` 记录单卷 100% 的观测值，且整体预期 FAIL）——⚠️ 它**不是**"单卷合法"的锁定（本轮实测单卷 100% 被逐卷 share 检查拦截）；shadow 默认 False/enabled 默认 True（:426-427）；enabled=False 放行（:432） |
| `test_volume_planner.py:144-175` | `GateConfig()` 默认行为 + 单卷 100% 合法 + plan_volumes 修复循环（动签名时的直接回归面） |
| `test_graphs.py:211-224` | 图级 fixture：2 卷 × 400 章 SeriesPlan（卷数≠1 的既有形态） |
| `test_settings_toml.py:112` | `StructureGateSettings()` 与 settings.toml 逐键一致（改 config.py 默认必须同步 toml，反之亦然） |
| `test_mode_data.py:174-251` | 档位结构性质：四档齐全 / story·learn grand 共享 / ultra 两模式 rule 必须不同且 learn 含"知识" / `is_multi_volume` 仅 ultra 为 True / fast 3~10。★ **无数值锁死 3~6 卷或 300~500** —— 改 story.ultra 数值不破它，但 rule 改写须维持「与 learn 不同」 |
| `test_prompt_layers.py:181` | 仅 docstring 引用「ultra=3~6 卷」为历史注释，**非断言**（已核实） |

---

## 6. 修复草案（**选项 (c) 落地路径**——未经拍板不实施）

按裁决「按 scale 声明放行、ultra-only、standard 保留相对闸门」，最小闭环：

1. **CLI**：`cmd_plan` 加 `--scale`（默认 `standard`，保持现行为）；`--book-chapters`
   仍是章数权威，未显式给时是否取 `scale_spec.typical_chapters` —— **待拍板（§8-1）**。
2. **State**：`BookState`（或 `VolumePlanState`）加 `scale: str` 字段（轻量标量，
   不违反规则 5）。
3. **规划器**：`plan_volumes(..., scale: str = "standard")`；
   按 `scale_spec` 把**卷数/每卷期望注入 user prompt**；
   `GateConfig` 构造时按 scale 覆盖 —— **仅 `story.ultra`**：
   share_min 降至约 0.10（7 卷×214 章 → 14.3% ≥ 10% ✅；同时保持对「400 章挤 1 卷」
   的 max 60% 拦截不变）。覆盖值的承载方式（代码内 per-scale 表 vs scales.toml 声明
   gate 字段）**待拍板（§8-2）**。
4. **system prompt 文案按 scale 分叉**（本轮新发现的必改点）：非 ultra 保留
   「不要绝对区间」教学；ultra 改为「本档位目标 7~8 卷、每卷 150~250 章」的显式声明。
   ⚠️ 改 system prompt 会改变 stable 前缀 hash —— 仅 ultra 路径受影响，standard
   路径文本必须逐字节不变（保护清单 §5-3）。
5. **scales.toml**：`story.ultra` 数值 → 7~8 卷 × 150~250；rule 文本整体重写
   （**删除「本次只细化第 1 卷」** —— 那是旧项目病根原文）；修复循环的
   `_repair_prompt`（:217-236）同步注入 scale 期望，防模型在修复轮被拉回旧形态。
6. **D-07 对齐**：`doc/00-决策记录.md` 追加补记（1500 章 ÷ 7~8 卷的现状对照）。
7. **变异验证计划**（修复阶段的验收）：
   - 1500 章 7 卷 × ~214 → 过闸门（修复前必失败，本轮已实测复现 7 条 issue）；
   - 1500 章 3/5/6 卷 → 仍过（不过度放宽：share_max 60% 不变）；
   - 400 章 4 卷 × 100（standard 量级）→ 仍过；**400 章单卷 → 仍被拦**
     （书 23 防线，与现状一致，本轮已实测）；
   - 8 卷 × 187.5 → 过；
   - `test_structure_gate.py`/`test_volume_planner.py`/`test_graphs.py`/
     `test_mode_data.py`/`test_settings_toml.py` 全绿（standard 零回归）；
   - **反向变异**：把 per-scale 覆盖删掉 → 7 卷样本必须重新被拦（证明放行真的来自
     scale 声明，不是闸门被改松）。

---

## 7. 与已裁决记录的差异声明

本审计与裁决记录（REF `mm-ed161439593c4f07a7a7d572954ab9e3`）完全一致的部分：
断点三连、share_min 封顶数学、ultra-only 边界、不得回退绝对闸门。
**本文新增**（裁决未点名，均为本轮逐行核实所得）：
- system prompt `volume_planner.py:81-84` 的「反绝对区间教学」与 D-07 直接对立
  （修复必改点，且涉及 stable 前缀 hash 的边界）；
- scales.toml story.ultra **自身**的可行域矛盾（6 卷零可行解，§3.1）；
- rule 文本中的「本次只细化第 1 卷」是旧项目病根原文、至今仍在线（§1.1）；
- `_repair_prompt` 修复轮同样不含 scale 期望（§6-5）；
- share 检查的**单卷不豁免**（§3.4）：初版草稿曾误写「单卷 100% 合法」，
  经真实 `evaluate_series` 实测推翻并已在 §3.4/§5/§6 同步修正 ——
  修正依据：`400 章 × 1 卷` 返回 `[chapter_distribution] 占全书 100%，
  超出 [15%, 60%]`（本审计当日实测，复现命令形态见 §3.4 表格）。

---

## 8. 开放问题（需负责人拍板，修复阶段前）

1. **`--scale` 与 `--book-chapters` 的关系**：`--scale` 只作闸门/prompt 语境
   （章数仍由 `--book-chapters` 权威），还是 `--scale` 未显式给 `--book-chapters`
   时自动取 `typical_chapters`（ultra=400→改为新 typical？现值 400 与 1500 矛盾）？
   建议：`--book-chapters` 显式传则用之；否则取档位 `typical_chapters`（此时
   story.ultra 的 typical 需同步改为 ~1500）。
2. **per-scale 闸门覆盖的承载方式**：(i) `plan_volumes` 内按 scale 硬编码覆盖表
   （最小改动）；(ii) scales.toml 每档声明 gate 覆盖字段（数据化，加载器要扩
   `_KNOWN`；与"配置驱动"方向一致）。建议 (ii)，但 (i) 更符合"最小修复"。
3. **learn.ultra 是否跟随**：裁决为仅 story.ultra —— 维持不动；若未来 learn
   也要 7~8 卷，同型修复另开。
4. **`doc/research/09` 编号冲突**（corpus-inventory 与 hindsight-for-novel 重复占
   09，`doc/12` 建议的改名未执行）：本文跳号至 11 以避让；改名是否执行待定。
5. **milestone 密度**：150~250 章/卷 → 每卷 ≥6~10 个里程碑，prompt 已有教学
   （:85-90），预计无冲突；真实 ultra 规划时留意模型输出质量。
6. **超短档可行域空洞**（§3.4 新发现，与本审计主线无关但闸门设计者应知）：
   fast（3~10 章）与 standard ≤40 章的书在现闸门下无任何可行解
   （单卷 share=100%>60% 拦；两卷每卷 <floor 20 拦）。这些档位从未走通 plan 全链。
   修复 ultra 时**不要顺手放宽全局 share_max/floor**（会动书 23 防线）；
   空洞是否修、怎么修，另行提案。

---

## 9. 证据索引（关键行号速查）

| 事实 | 位置 |
|---|---|
| plan 参数（无 --scale） | `src/storyforge/cli.py:931-947` |
| target_chapters=--book-chapters | `src/storyforge/cli.py:608-617` |
| plan_volumes 调用（不传 gate_config） | `src/storyforge/graph/graphs/books.py:293-305` |
| plan_volumes 签名 | `src/storyforge/graph/nodes/volume_planner.py:239-250` |
| user prompt 只注入 target_chapters | `src/storyforge/graph/nodes/volume_planner.py:277-283` |
| system prompt「反绝对区间」教学 | `src/storyforge/graph/nodes/volume_planner.py:81-84` |
| GateConfig 全局构建 | `src/storyforge/graph/nodes/volume_planner.py:266-275` |
| share 区间检查（len>1 才查） | `src/storyforge/domain/validators/structure_gate.py:184, 336-362` |
| GateConfig 默认值与设计史 | `src/storyforge/domain/validators/structure_gate.py:124-155` |
| StructureGateSettings | `src/storyforge/config.py:318-326` |
| settings.toml 相对闸门 | `config/settings.toml:84-103` |
| scales.toml story.ultra | `config/scales.toml:95-109`（learn.ultra :111-125） |
| scale_spec 消费点（仅展示） | `src/storyforge/modes/story.py:255-264` |
| state 无 scale 字段 | `src/storyforge/graph/state.py:53-108` |
| scales 加载器 | `src/storyforge/modes/scales.py:37-75, 148-151` |
