# 小米 MiMo 接入实测（P5 前置调研）

> 生成日期：2026-09-13
> 方法：`scripts/ab_thinking.py`，用**本项目已标定的打分卡**当裁判
> 原始数据：`ab-thinking.json`（default/low 各 2）、`ab-thinking-2.json`（各 3）、
> `ab-thinking-high.json`（high 4），共 14 次真实调用

---

## 1. 结论先行（回答「思考强度开 low 的质量如何」）

**质量没有下降，且 low 是三者中表现最好的一档。**

| effort | n | 打分中位 | 字数中位 | 对话率 | 章内冗余 | 句长CV | 细纲落实 | reasoning% | 需审 |
|---|---|---|---|---|---|---|---|---|---|
| default | 5 | **0.739** | 3518 | 0.1845 | 0.0466 | 0.660 | 1.000 | 20% | 0/5 |
| **low** | 5 | **0.739** | **4523** | 0.1657 | 0.0438 | **0.678** | 1.000 | 19% | 2/5 |
| high | 4 | 0.732 | 3918 | **0.2187** | 0.0538 | **0.694** | 1.000 | 21% | 1/4 |

- **打分完全打平**（0.739 vs 0.739 vs 0.732）—— 0.007 的差异在 n=5 下是噪声。
- **low 的篇幅最长**（4523 vs 3518 vs 3918 字）。
- `high` 略低不是因为想得多，而是**两次样本偏短**（2178 / 3458 字）拖低了中位。
- 逐篇读过 4 个样本：`low` 的样本（4523 字）对话更利落、感官细节更具体
  （「能嗅到一丝未干的松烟味」「灰扑扑的布鞋鞋尖」），并不输给 default。

**所以：`low` 可以放心用，它是当前默认值，建议保持不变。**

---

## 2. 更重要的发现：**effort 这个旋钮在 MiMo 上几乎是惰性的**

这是本次实测最有价值的一条，与"省 token"无关，而是**预期管理**：

| 任务类型 | default | low | high |
|---|---|---|---|
| 正文写作 reasoning 占比 | 20% | 19% | 21% |
| **抽取任务 reasoning 占比** | **91%** | **92%** | **89%** |

- 正文写作：无论哪档，reasoning 都稳定在 **19~21%**。
- **抽取任务：无论哪档，reasoning 都吃掉 completion 的 89~92%。**

→ **调 effort 不会让抽取任务少思考。** 三种设置下都是"想掉九成预算"。

这意味着旧项目 `entity_classify` 空输出 **100%** 的根因**在本模型上无法靠参数解决**，
只能靠**别的手段**（见 §4）。

---

## 3. 实测确认的 MiMo 行为特征

### 3.1 **无法真正关闭思考**（与文档预期不符，必须记录）

逐项实测（同一 prompt，`{"a":1}` 这种极简任务）：

| 配置 | 结果 |
|---|---|
| `enable_thinking: False`（extra_body） | ❌ 被忽略，reasoning 仍 15 |
| `thinking: False`（extra_body） | ❌ 被忽略，reasoning 仍 15 |
| `reasoning_effort: "minimal"` | ❌ **400 错误**（非法值） |
| `reasoning_effort: "low"` | ✅ 接受 |
| `reasoning_effort: "high"` | ✅ 接受 |

→ 最低合法档是 **`low`**；`forbidden` 语义在本模型上**不可达成**。

**这直接改变了 `providers.toml` 的设计**：抽取节点原先声明 `thinking = "forbidden"`
（要求模型能真正不思考），在只有 MiMo 一个 provider 可用时会导致
**路由抛 `NoCapableModelError`**。因此新增了 `thinking = "minimize"` 语义：

> `minimize` = 取该模型可用的最低思考档（不保证"不想"）

这是诚实建模：假装关掉了而服务端忽略它，只会让账单上的 reasoning 打脸。

### 3.2 结构化输出

- 能按 schema 输出合法 JSON（抽取任务实测正常）。
- ⚠️ **有时会用 ```json 代码块包裹**（default 档实测出现过）。
  `LLMClient._extract_json` 已处理这种情况，但仍值得记录。

### 3.3 usage 字段

返回 **标准** `usage_metadata`，含 `reasoning_tokens`：

```
prompt_tokens / completion_tokens / total_tokens
reasoning_tokens  ← 非标但可读
```

⚠️ **未观察到缓存字段**（`cached_tokens` 全为 None）。
→ `usage_source` 会是 `partial`，`ModelPricing.cost_of` 会**保守按全 miss 计费**
并标注不确定性。这是正确行为（旧项目把 NULL 当 0 → 漏报）。

---

## 4. 对管线设计的实际影响

### 4.1 抽取节点的真实风险不是成本，是**截断**

reasoning 占 completion 的 91% ⇒ 若 `max_tokens` 设得不够，
**思考会把输出预算吃光 → 正文/JSON 被截断**（`finish_reason: length`）。

这条正好印证旧项目文档里的一句话：

> `max_tokens = max(4096, reasoning * 4)` —— 思考与输出共用上限
> → 可用输出预算 = 4x - x = 3x

**对策（已落地）**：
- `settings.llm.min_output_tokens = 1024` 保底输出预算
- 强制采集 `finish_reason`，`LLMCallRecord.truncated` 可直接统计
- 抽取节点用 `minimize` 取最低档，把 reasoning 压到该模型的下限

### 4.2 成本口径必须区分币种

Token Plan 用**点数（Credits）**计费（权重 hit=2 / miss=100 / output=200），
而不是美元。旧项目把同一套点数权重用在占 61% 流量的 deepseek 上
→ **跨单位混算**。

**对策（已落地）**：`ModelCapability.pricing_currency` + `ModelPricing.currency`，
`storyforge report` 显式打印币种。

### 4.3 质量杠杆在**管线**，不在 effort

本次 A/B 的分差（0.732~0.739）远小于旧项目基线与本项目目标之间的差距。
**真正决定质量的是 P3/P4 建的那些机制**：

| 机制 | 针对的旧项目实测问题 |
|---|---|
| 卷级台阶（全部卷一次定稿） | 400 章挤第 1 卷 |
| 骨架必须落实台阶扩张 | 后半本新增地点 0~1 个 |
| 细纲注入伤势节拍 | 恢复率 **0%** |
| `issues` 非空 ⇒ 必须有修订稿 | 88.1% 章被点名文风问题、57 章仍判 passed |
| 修订必须过闸门 | `no_improve:6->6` 空转 |
| 抽取拆三节点 | `entity_classify` 空输出 **100%**（一条 prompt 抽 11 类） |

---

## 5. 局限（诚实标注）

1. **样本量小**：每档 n=4~5。打分差异 0.007 不构成统计显著。
   结论"low 不差于 default"是**够用的**，但"low 更好"证据不足。
2. **打分卡测的是表层指标**（对话率、冗余、比喻数、句长变异），
   **不是文学质量**。§1 的补充判断来自我逐篇阅读，属主观。
3. **只测了一个细纲**（第 7 章）。换题材/换卷可能有不同表现。
4. **未做人工盲评**。若要更硬的结论，应把 14 篇样本打乱做人工 2 选 1。
   样本已存于 `_ab_prose_sample.txt`。

---

## 6. 复现方式

```powershell
$env:MIMO_API_KEY = "tp-..."
uv run python scripts/ab_thinking.py --samples 3 --efforts default low high
```

产出：`doc/research/ab-thinking*.json`（含每篇全文与全部指标）。
