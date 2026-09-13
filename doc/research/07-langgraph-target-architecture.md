# 07 · LangChain + LangGraph 目标架构研究报告

> **定位**：为长篇小说 AI 生成系统的重写提供外部技术选型依据。
> **对照对象**：现有 `E:\AI_project\story_llm` 自研体系（backend 754 个 `.py`、**312,566 行 Python**、76 个顶层 `app/*.py`、`pipeline.py` 单文件即承载 plan → architect → write → commit 全链路）。
> **研究时间**：2026-09（本机系统时间 2026-09-13）。
> **方法**：PyPI JSON API 实测版本与发布日期、`docs.langchain.com` 官方 `.md` 原文抓取、GitHub REST/Issues 检索、HN Algolia API、官方厂商文档（DeepSeek / Temporal / Anthropic）。
> **诚实性约定**：凡未能一手验证的结论均显式标注「⚠️ 未验证」；凡厂商立场均标注商业动机；矛盾之处并列而非取一。

---

## 目录

1. [版本与依赖基线](#1-版本与依赖基线)
2. [LangGraph 核心机制](#2-langgraph-核心机制)
3. [持久化与恢复](#3-持久化与恢复)
4. [结构化输出与容错](#4-结构化输出与容错)
5. [Prompt 缓存](#5-prompt-缓存)
6. [Token 计量](#6-token-计量)
7. [多 Agent 模式选型](#7-多-agent-模式选型)
8. [长期记忆与 Store](#8-长期记忆与-store)
9. [知识图谱方案](#9-知识图谱方案)
10. [评测与 Trace 预留](#10-评测与-trace-预留)
11. [生产坑与批评](#11-生产坑与批评)
12. [版本风险](#12-版本风险)
13. [结论与缓解措施](#13-结论与缓解措施)
14. [来源](#14-来源)

---

## 0. 执行摘要（决策者先读这 10 条）

| # | 结论 | 影响 |
|---|---|---|
| 1 | 2026-09 版本基线：**langgraph 1.2.11**、**langchain-core 1.6.3**、**langchain 1.4.0**、langgraph-checkpoint **4.2.0**、-sqlite **3.1.1**、-postgres **3.1.2**、langsmith **0.12.4** | 见 §1 |
| 2 | 三者依赖关系已被上游收紧且**自洽**：`langchain 1.4.0` → `langgraph>=1.2.11,<1.3.0` → `langchain-core>=1.4.7,<2` | 可安全锁版本 |
| 3 | **不要 pin `langgraph` 1.0.x / 1.1.x**：它们对 `langchain-core` 无上界约束，且 `1.0.x` 声明 `langgraph-checkpoint<3.0.0`，与 checkpoint 4.x **互斥** | 见 §1.4 |
| 4 | **LangGraph checkpointer ≠ 持久执行**。官方与 Temporal 均承认：checkpoint 保存数据，不保存执行；进程死亡后**需要你自己检测失败并重新入图** | 见 §3.1 |
| 5 | **checkpoint 存储默认 O(N²)** —— LangChain 自己博客承认 200 轮编码 agent 序列化 **5.3 GB**；社区实测 32 线程 **1.24 GB** | 见 §3.4 |
| 6 | **小说正文绝不能进 graph state**。state 只放 id/元数据，正文放文件/DB。这一条同时解决 LangGraph 与 Temporal 的问题，**缓解层可移植** | 见 §3.5 |
| 7 | **DeepSeek 缓存命中价 = miss 价的 30–50 倍**（不是 10 倍 —— 那是 2024 年的官方措辞）。但缓存按**磁盘前缀单元**匹配，**前缀只要尾部一变就全部失效** | 见 §5 |
| 8 | **`deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 停止服务**，必须换 `deepseek-flash` / `deepseek-v4-pro`；且**必须用 `langchain-deepseek`**（`ChatOpenAI` 会丢 `reasoning_content` 与缓存字段） | 见 §4.4 / §6.1 |
| 9 | **结构化输出首选 `ProviderStrategy`（原生 JSON Schema）**，退化到 `ToolStrategy`；`json_mode` 对中文长文本风险最高（DeepSeek 官方承认"偶尔返回空内容"） | 见 §4 |
| 10 | **小说生成应当用「确定性图 + 少量 LLM 判定」而非 supervisor 自由路由**。官方 subagents 模式一次请求 4 次模型调用，router 只需 3 次；**Anthropic 明确把"所有 agent 共享同一上下文"列为多智能体的不适配场景** | 见 §7 |
| 11 | **官方 release policy 承诺「breaking 只在大版本」，但 patch 级破坏有 3 个一手反例**（langchain 1.0.6、langgraph-prebuilt 1.0.2、checkpoint-postgres 2.0.22） | 见 §12 |
| 12 | **官方给出的按节点归因最优路径是 `stream_mode=["updates"]`**（节点名就是 dict key），而 `UsageMetadataCallbackHandler` **只按模型名归因、不按节点** | 见 §6.3b |

**一句话建议**：**用 LangGraph，但把它当"可丢弃的加速器"而不是"真相来源"**——业务真相放在你自己的 DB（小说正文、章节索引、追溯日志）；LangGraph 只负责编排、断点、人工介入与流式。核心业务逻辑保持零框架依赖，验收标准是 `grep -r "langgraph" core/` 返回空。

---

## 1. 版本与依赖基线

### 1.1 实测版本（PyPI JSON API，2026-09-13 采集）

| 包 | 最新版本 | 上传时间 (UTC) | requires_python |
|---|---|---|---|
| `langgraph` | **1.2.11** | 2026-08-11T14:00:35Z | `>=3.10` |
| `langchain-core` | **1.6.3** | 2026-09-11T17:37:43Z | `>=3.10.0,<4.0.0` |
| `langchain` | **1.4.0** | 2026-09-03T16:59:31Z | `>=3.10.0,<4.0.0` |
| `langgraph-checkpoint` | **4.2.0** | 2026-08-07T20:05:02Z | `>=3.10` |
| `langgraph-checkpoint-sqlite` | **3.1.1** | 2026-07-30T19:19:36Z | `>=3.10` |
| `langgraph-checkpoint-postgres` | **3.1.2** | 2026-08-07T20:39:58Z | `>=3.10` |
| `langgraph-prebuilt` | **1.1.0** | 2026-05-12T03:37:48Z | `>=3.10` |
| `langgraph-sdk` | **0.4.4** | 2026-08-27T21:25:21Z | `>=3.10` |
| `langsmith` | **0.12.4** | 2026-09-09T21:19:55Z | `>=3.10` |
| `langchain-openai` | **1.6.2** | 2026-09-09T21:21:39Z | `>=3.10.0,<4.0.0` |
| `langchain-anthropic` | **1.7.2** | 2026-09-10T19:12:53Z | `>=3.10.0,<4.0.0` |
| `langchain-deepseek` | **1.1.0** | 2026-06-03T19:07:11Z | `>=3.10.0,<4.0.0` |
| `langchain-community` | **0.4.2** | 2026-05-22T19:42:57Z | `>=3.10.0,<4.0.0` |

来源：[pypi.org/pypi/langgraph/json](https://pypi.org/pypi/langgraph/json)（其余包同构 URL）。

### 1.2 关键版本时间线（实测）

**langgraph**

| 版本 | 日期 | 里程碑 |
|---|---|---|
| 0.4.0 | 2025-04-29 | |
| 0.5.0 | 2025-06-26 | |
| 0.6.0 | 2025-07-28 | |
| 1.0.0a1 | 2025-08-28 | 1.x 首个 alpha |
| **1.0.0 GA** | **2025-10-17** | LTS 基线 |
| 1.1.0 | 2026-03-10 | `version="v2"` streaming / `GraphOutput` |
| **1.2.0** | **2026-05-12** | `DeltaChannel`(beta)、节点 timeout/error_handler、`RunControl` 优雅停机、event streaming v3 |
| 1.2.11 | 2026-08-11 | 当前最新 |

**langchain / langchain-core**

| 包 | 1.0.0 GA | 后续 minor | 最新 |
|---|---|---|---|
| `langchain` | 2025-10-17 | 1.1.0 (2025-11-24)、1.2.0 (2025-12-15)、1.3.0 (2026-05-12)、**1.4.0 (2026-09-03)** | 1.4.0 |
| `langchain-core` | 2025-10-17 | 1.1.0 (2025-11-21)、1.2.0 (2025-12-12)、1.3.0 (2026-04-17)、1.4.0 (2026-05-11)、1.5.0 (2026-07-21)、1.6.0 (2026-08-19) | 1.6.3 |

来源：[LangChain Python changelog](https://docs.langchain.com/oss/python/releases/changelog)。

### 1.3 依赖约束实测（从 `requires_dist` 提取，机器可验证）

```
langgraph==1.2.11
  langchain-core<2,>=1.4.7
  langgraph-checkpoint<5.0.0,>=4.1.0
  langgraph-prebuilt<1.2.0,>=1.1.0
  langgraph-sdk<0.5.0,>=0.4.2

langchain==1.4.0
  langchain-core<2.0.0,>=1.6.0
  langgraph<1.3.0,>=1.2.11        ← 上游已经把 langgraph 收紧到 1.2.x
  pydantic<3.0.0,>=2.7.4

langchain-core==1.6.3
  langsmith<1.0.0,>=0.3.45        ← 注意：langsmith 无有效上界
  pydantic<3.0.0,>=2.7.4
  jsonpatch<2.0.0,>=1.33.0
  tenacity!=8.4.0,<10.0.0,>=8.1.0

langgraph-checkpoint==4.2.0          langchain-core>=0.2.38  ormsgpack>=1.12.0
langgraph-checkpoint-sqlite==3.1.1   langgraph-checkpoint<5.0.0,>=4.1.0  aiosqlite>=0.20  sqlite-vec>=0.1.6
langgraph-checkpoint-postgres==3.1.2 langgraph-checkpoint<5.0.0,>=4.1.0  orjson>=3.11.5  psycopg>=3.2.0
```

**结论**：`langgraph 1.2.11` + `langchain 1.4.0` + `langchain-core 1.6.3` + checkpoint 4.2.0/3.1.x **完全自洽**。这个组合是当前唯一被上游互相背书的稳定集。

### 1.4 三条从约束推出的硬结论

1. **⚠️ 不要 pin `langgraph` 1.0.x / 1.1.x**：只有 1.2.11 才把 `langchain-core` 收紧为 `<2,>=1.4.7`；更早版本约束松散，会让 `pip` 解析出意外组合。
2. **⚠️ `langgraph 1.0.x` 与 checkpoint 4.x 互斥**：`langgraph==1.0.0` 声明 `langgraph-checkpoint<3.0.0`。pin `langgraph<1.1` = 永远拿不到 checkpoint 4.x。
3. **checkpoint 三件套必须显式 pin**：它们是分叉轴（sqlite / postgres 决定存储表结构），且版本线与 langgraph 独立（4.x vs 3.1.x）。

### 1.5 发布节奏量化（2026-03-17 → 2026-09-11，仅 stable）

| 包 | release 数 | 中位间隔(天) | 2026 年新 minor |
|---|---|---|---|
| `langchain-core` | 40 | **3.04** | 1.2 / 1.3 / 1.4 / 1.5 / 1.6（5 个） |
| `langchain` | 28 | 4.14 | 1.2 / 1.3 / 1.4 |
| `langgraph` | 20 | **6.15** | 1.0 / 1.1 / 1.2 |
| `langsmith` | 74 | **1.85** | 0.6 → 0.12（**7 个**） |
| `langgraph-checkpoint-sqlite` | 2 | 79.66 | 3.0 / 3.1 |
| `langchain-classic` | 5 | 17.02 | 1.0（最后发版 2026-06-10，落后主包 2 个月） |

`langgraph` 1.2.x 系列在 **91.4 天**内发了 **19 个 patch**（1.2.0 → 1.2.11）。

**解读**：
- `langchain-core` 与 `langsmith` 是整条链上最吵的两个轴。`langsmith` 中位 **1.85 天**一次发版，却是 `langchain-core` 的**强制依赖**——几乎所有 pin 讨论都漏了它。
- `langgraph` 本身（6.15 天）反而是相对安静的。项目风险并不均匀分布。
- `langchain-classic` 明显被降速维护：若重写需要 legacy chains/retrievers，**这是一个已经半冻结的依赖**。

### 1.6 推荐 pin（两档）

```toml
# 【A】保守-可复现（推荐：长跑小说系统）
langgraph==1.2.11
langgraph-prebuilt==1.1.0             # 必须显式 pin：patch 级破坏有先例（见 §12.3）
langgraph-checkpoint==4.2.0
langgraph-checkpoint-sqlite==3.1.1    # 或 -postgres==3.1.2，二选一
langchain==1.4.0
langchain-core==1.6.3
langsmith==0.12.4                     # 中位 1.85 天/release，必须 pin
pydantic>=2.7.4,<3.0.0                # 上游已声明 <3.0.0，写死以免 pydantic 3 出现时被动
# 不引入：langchain-classic / langchain-community / langgraph-supervisor（已停止维护）
```

```toml
# 【B】允许 patch 内浮动（若需要安全补丁）
langgraph>=1.2.11,<1.3.0
langgraph-prebuilt>=1.1.0,<1.2.0
langgraph-checkpoint>=4.1.0,<5.0.0
langchain>=1.4.0,<1.5.0
langchain-core>=1.6.0,<2.0.0
langsmith>=0.12.0,<0.13.0
```

**为什么不用 `~=1.2.11`**：它等价于 `>=1.2.11,==1.2.*`，会放进 `1.2.12` 这类 patch——而 patch 级破坏已有三个一手先例。

**⚠️ 未验证项**：
- **不存在官方兼容矩阵页面**（已搜索确认）。§1.3 的约束是本次从 PyPI `requires_dist` 亲自提取的。
- **无可靠数据说明社区实际使用的约束风格**。GitHub 在 langgraph 仓库对 `"pinned" in:title` 检索 `total_count=1` 且为无关 docs PR——此项应视为空白，而非"大家都用 X"。
- 唯一官方谈 pin 的表述在 `langchain-mcp-adapters` 迁移指南："Prefer pinning a current stable version when the project already pins versions." —— **只说"要 pin"，没给区间风格**。

---

## 2. LangGraph 核心机制

来源：[Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)、[Graph API 使用指南](https://docs.langchain.com/oss/python/langgraph/use-graph-api)、[LangGraph runtime (Pregel)](https://docs.langchain.com/oss/python/langgraph/pregel)、[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)。

### 2.1 执行模型：Pregel 超步（super-step）

LangGraph 底层是 Google **Pregel** 的 BSP（Bulk Synchronous Parallel）模型。每次执行分三阶段：

- **Plan**：决定本轮要跑哪些 actor
- **Execution**：并行执行全部被选中的 actor；**此阶段 channel 更新对 actor 不可见**
- **Update**：把本轮写入合并进 channel

同一超步内并行的节点构成一个 checkpoint 单元；**顺序执行的节点属于不同超步**。这条直接决定了 checkpoint 数量与存储增长（§3.4）。

### 2.2 State schema：TypedDict vs Pydantic

官方原文：

> The main documented way to specify the schema of a graph is by using a **TypedDict**. If you want to provide default values in your state, use a **dataclass**. We also support using a **Pydantic BaseModel** as your graph state if you want recursive data validation (though note that **Pydantic is less performant** than a TypedDict or dataclass).

**关键补充**：`langchain` 的 `create_agent` **不支持 Pydantic state schema**；`langchain 1.x` 迁移指南明确 "no more pydantic state"。

**对本项目的建议**：**用 `TypedDict`**。理由有三——
1. 性能（官方点名 Pydantic 更慢）；
2. checkpoint serde 对 Pydantic 长期不稳定（§12.5）；
3. 上游自身在 `create_agent` 里已经放弃了 Pydantic state。

### 2.3 Reducer

Reducer 是**二元函数**，签名固定为 `reducer(left=state 现值, right=节点更新)`。

| 类型 | 行为 |
|---|---|
| 默认（无 `Annotated`） | **丢弃 left，直接用 right 覆盖** |
| `Annotated[list[str], operator.add]` | 列表拼接 |
| `Annotated[list[AnyMessage], add_messages]` | 按 message **id** 追加/更新，并反序列化为 `Message` 对象 |

**⚠️ 两个官方明确警告的坑**：

**(a) 合并型 reducer 无法用空值清空字段。** 官方原文示例：`errors: Annotated[list[str], add]`，节点返回 `{"errors": []}` 后 **`state["errors"]` 仍然是 `["bad sql"]`**。想清空必须用 `Overwrite`：

```python
from langgraph.types import Overwrite

def clear_errors(state: State):
    return {"errors": Overwrite([])}   # 绕过合并 reducer
```

**这条对「重试计数器 / 错误缓冲」类字段是刚需** —— 否则重试计数永远清零不掉。

**(b) 不要混用静态边与动态路由。** 官方原文：

> For each node, choose **one** routing mechanism: use normal edges for static routing, or use conditional edges / `Command` for dynamic routing. **Do not mix** normal edges and dynamic routing from the same node, because **both paths can execute** and make graph behavior harder to reason about.

**对小说系统的含义**：章节流水线（大纲 → 草稿 → 一致性检查 → 修订 → 提交）**必须用静态边**，只有"是否重写"、"是否升级到强模型"这类判定才用条件边。混用会导致章节被写两遍。

### 2.4 其他 state 工具

| 工具 | 用途 | 官方要点 |
|---|---|---|
| `UntrackedValue` | **存在但不 checkpoint** 的字段 | 适合放 DB 连接、临时缓存、不想持久化的大对象；resume 时重置。**不要用它放需要跨 interrupt / time travel 的数据** |
| `Overwrite` | 绕过 reducer 直接覆盖 | 见上 |
| `MessagesState` | 预置 `messages: Annotated[list[AnyMessage], add_messages]` | 通常 sub-class 后加字段 |
| `DeltaChannel` | 只存增量 delta（**需 langgraph>=1.2，beta**） | 见 §3.4 |
| `context_schema` | 运行期上下文（**不进 state**） | 放 model 名、DB 句柄、story_id 等 |

**设计原则（官方 `thinking-in-langgraph` 明确给出的）**：

> **Keep state raw, format prompts on-demand.** Your state should store **raw data, not formatted text**. Format prompts **inside nodes** when you need them.

三条收益：不同节点可按需格式化同一份数据；改 prompt 模板不用动 state schema；调试时能看清每个节点实际收到什么。

**这对本项目的直接含义**：**故事圣经（story bible）在 state 里应该是结构化数据（dict/list），不是拼好的 prompt 字符串**。每个写作节点自己按需渲染。这也顺带解决了 prompt 缓存的前缀稳定性（§5）——**渲染发生在节点内，前缀才可控**。

### 2.5 节点（Node）

节点签名支持三种参数（按名称/类型注入）：

```python
def node(state: State): ...
def node(state: State, config: RunnableConfig): ...          # thread_id、tags
def node(state: State, runtime: Runtime[Context]): ...        # store、stream_writer、execution_info、heartbeat、control
```

`Runtime` 暴露的字段（**对小说系统很关键**）：

| 属性 | 用途 |
|---|---|
| `runtime.store` | 长期记忆（§8） |
| `runtime.stream_writer` | 向 `custom` 流写进度（**章节生成进度条**） |
| `runtime.context` | `context_schema` 注入的 story_id / 模型配置 |
| `runtime.execution_info.node_attempt` | **当前重试次数（1-indexed）** —— 用于"第 2 次尝试换 fallback 模型" |
| `runtime.execution_info.thread_id` / `run_id` / `checkpoint_id` / `task_id` | **追溯日志的外键**（§10.4） |
| `runtime.heartbeat()` | 手动重置 idle 超时计时 |
| `runtime.drain_requested` | 优雅停机信号（§2.8） |

**`node_attempt` 是官方文档中直接给出的 fallback 模式**：

```python
def my_node(state: State, runtime: Runtime) -> State:
    if runtime.execution_info.node_attempt > 1:
        return {"result": call_fallback_api()}   # 第 2 次起换模型
    return {"result": call_primary_api()}
```

### 2.6 `Command`：状态更新 + 路由合一

```python
def my_node(state: State) -> Command[Literal["my_other_node"]]:
    return Command(update={"foo": "bar"}, goto="my_other_node")
```

四个参数：`update`（状态更新）、`goto`（跳转）、`graph`（跳父图，`Command.PARENT`）、`resume`（interrupt 恢复值）。

**必须注意的三条**：

1. **返回类型注解是强制的**：`Command[Literal["target"]]` —— 缺了图渲染和边推断都不对。
2. **`Command` 只"增加"动态边**：`add_edge("node_a","node_b")` 依然会执行。**这就是 §2.3(b) 警告的机制来源。**
3. **`Command(resume=...)` 是唯一可用于 `invoke()`/`stream()` 输入的 Command 形态**。官方明确警告：**不要**用 `Command(update=...)` 作为输入来继续多轮会话——因为它会从**最新 checkpoint** 恢复（即最后跑完的那步，而非 `__start__`），图会「卡住」：

```python
# ❌ 错误：会从最新 checkpoint 恢复，看起来卡住
graph.invoke(Command(update={"messages": [...]}), config)

# ✅ 正确：普通 dict 会从 __start__ 重新开始
graph.invoke({"messages": [...]}, config)
```

### 2.7 `Send`：动态 fan-out（map-reduce）与 join 屏障

`Send` 从**条件边**返回，实现运行期才确定的分支数：

```python
from langgraph.types import Send

def assign_workers(state: State):
    return [Send("write_chapter", {"chapter_idx": i}) for i in state["chapter_indices"]]

builder.add_conditional_edges("planner", assign_workers, ["write_chapter"])
```

**机制要点**：
- `Send` 的第二个参数是**该 worker 的独立输入 state**（不是共享全量 state）。官方 map-reduce 示例中 `WorkerState` 只含 `section` + 一个 reducer 键。
- 所有 worker 输出经 **reducer 汇聚到共享 key**（示例用 `Annotated[list, operator.add]`）。
- **Join 屏障天然成立**：所有 worker 属于同一超步，全部完成后下游 synthesizer 才执行。
- **`Send` 可以覆盖目标的静态 timeout**：`Send("process_item", {...}, timeout=TimeoutPolicy(idle_timeout=15))`。

**⚠️ `Send` 的已知存储反模式（来自真实 GitHub issue）**：[pizza-bot-app#58](https://github.com/pizza-bot-app/pizza-bot/issues/58) 报告 `__pregel_tasks` 占 checkpoint 总量的 **19%（123 MB）**，因为 **"Pending `Send`s carry the whole message list in `args.messages`"**——单个最大 checkpoint 2.0 MB 中有 1.65 MB 是**四份逐字节相同的 413 KB 历史**。

**→ 铁律：`Send` 的 payload 只放 id 和小标量，绝不放上下文/正文。**

**对小说系统的含义**：**卷/批级的并行章节生成非常适合 `Send`**——但 payload 必须是 `{"chapter_idx": 12, "book_id": "b24"}` 这种，上下文由 worker 节点自己从 DB 加载。这正是"状态外置"（§3.5）在多分支场景下的具体落地。

### 2.8 条件边、子图与重执行语义

**条件边**：

```python
graph.add_conditional_edges("node_a", routing_function, {True: "node_b", False: "node_c"})
```
- 返回**节点名列表**时，这些节点在下一超步**并行执行**。
- 路由函数返回的字符串默认直接当节点名；建议总是显式给映射表，便于图渲染和重构。

**子图**（官方 [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)）——两种接入方式：

| 方式 | 适用 | 状态耦合 |
|---|---|---|
| **在节点函数内调用** `subgraph.invoke(...)` | 父子 state schema **不共享键**，或需要转换 | 手动映射 |
| **直接作为节点** `add_node("x", subgraph)` | 父子**共享 state 键** | 自动读写同一批 channel |

**子图 persistence 三档**（`compile(checkpointer=...)`）：

| 模式 | 参数 | interrupt | 多轮记忆 | 同一子图并行调用 |
|---|---|---|---|---|
| **per-invocation（默认）** | `None` | ✅ | ❌ | ✅ |
| **per-thread** | `True` | ✅ | ✅ | **❌ 会 checkpoint 冲突** |
| **stateless** | `False` | ❌ | ❌ | ✅ |

**⚠️ per-thread 子图的官方红线**：并行调用会写同一 namespace → **必须**用 `ToolCallLimitMiddleware(tool_name=..., run_limit=1)` 或禁止并行工具调用。

**⚠️ 命名空间按调用顺序分配**：官方原文 *"**reordering your calls can mix up which subgraph loads which state**"*。解法：给每个 subagent 包一层**带唯一 node 名**的 `StateGraph`。

**⚠️ `checkpointer=False` 语义**：*"no durable execution... 进程中途崩溃无法恢复，必须从头重跑"*。

**⚠️ 动态 subgraph 无法查看状态**：`get_state(config, subgraphs=True)` 要求 LangGraph 能**静态发现**该子图。**当子图在 tool 函数或其他间接层里被调用时就不行**——这正是 subagents-as-tools 模式。

### 2.9 节点重执行与幂等性（**最重要的机制约束之一**）

官方原文：

> LangGraph saves checkpoints at **super-step boundaries**, not mid-function inside a node. If execution stops and later resumes (for example after an interrupt or a retry), **the affected node runs again from the start of its function**. Code and side effects before the pause **run again**.
>
> **Idempotency. Design node logic so re-execution does not corrupt state.** If a node inserts a database row, running it twice should not create duplicate rows... Use idempotency keys, upserts, or read-before-write checks.

**这条 + `interrupt()` 的规则组合，是小说系统最需要注意的地方**：

- 节点里 `interrupt()` 之前的代码**每次 resume 都会重跑**。
- 官方**明确禁止** `while True` + `interrupt()` 循环：*"the first resume replays 1 iteration, the second replays 2, and so on. The result is **exponential re-execution** of any code inside the loop body."* 正确做法：**一个节点一次 `interrupt()` + 条件边回环**。
- **禁止**把 `interrupt()` 包进裸 `try/except`（会吞掉中断异常）。
- **禁止**在节点内非确定性地重排/条件跳过 `interrupt()`（匹配是**按下标**的）。

**辅助手段 `@task`**：把节点内的多个操作拆成 `@task`，**task 结果会被 checkpoint**，resume 时可跳过已完成部分——这是"节点内细粒度断点"的正解。

**对本项目的直接设计约束**：写章节节点的结构应当是：

```
write_chapter(state, runtime):
    # 1. 从 DB 读上下文（幂等，可重跑）
    # 2. interrupt() 恰好一次（如果需要作者审核大纲）
    # 3. 调 LLM 生成正文 → 写文件（带幂等键，文件已存在则跳过）
    # 4. 返回 {"draft_ref": "..."}   ← 只返回引用
```

**绝不要**在节点里 `state["draft"] += new_text` 或用 `while` 循环反复 `interrupt()` 追问作者。

### 2.10 图迁移（Graph migrations）

官方明确的支持矩阵：

| 场景 | 支持情况 |
|---|---|
| 线程已结束 | ✅ 可任意改拓扑（增删改节点/边） |
| 线程**中断中** | ✅ 除**重命名/删除节点**外的拓扑变更 |
| 增删 state key | ✅ 完全向前向后兼容 |
| **重命名 state key** | ❌ 老线程的该 key 状态**丢失** |
| **改变 key 类型（不兼容）** | ⚠️ 可能出问题 |

**边拓扑本身不持久化**——增删改边只要节点还在就安全。**唯一会破坏中断线程的拓扑操作是重命名/删除节点。**

**→ 命名纪律**：节点名一旦上线就是**持久化契约**，等同数据库列名。建议在代码里用常量集中定义节点名，禁止散落字符串字面量。

---

## 3. 持久化与恢复

来源：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)、[Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)、[Backward compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility)、[LangGraph runtime](https://docs.langchain.com/oss/python/langgraph/pregel)。

### 3.1 ⚠️ 核心认知纠正：checkpointer ≠ 持久执行

这是本报告**最重要的一条**，且被两大厂商独立承认。

**Temporal 官方博客**（[链接](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)，**注意：Temporal 是竞品，有商业动机**）：

> "**Recovery is manual.** LangGraph **checkpoints state, but checkpoints are not durable execution**. A LangGraph run lives in a **single process**, so if that process dies then the run dies with it. The checkpoint **preserves your data, not your execution**: something has to detect the failure, decide where to re-enter the graph, and restart it."

**Diagrid**（同为竞品）：

> Frameworks like LangGraph... typically **don't provide durable execution—if the process crashes, the agent will restart from scratch**. Some have very basic checkpoint systems that **leave failure detection and resumption to the user, which is essentially the hard problem workflow engines solve**.

**LangChain 官方文档的表述其实是一致的**（只是不那么刺眼）——`durability` 模式的说明直接承认了中间状态的丢失风险（见 §3.3）。

**→ 对本项目的含义**：**必须有外部"看门狗"**：一个 job 表 + 定期扫描"running 但进程已死"的记录 + 用相同 `thread_id` 重新 `invoke(None, config)`。这不是 LangGraph 的功能，是**你必须自己写的那部分**。

**注意现有项目的有利条件**：`story_llm` 已经有 `job_queue.py`（SQLite 持久化）与 `recover_orphans` 启动钩子（`main.py:160`）—— 这套外部监督机制在重写时**应当保留并强化**，而不是指望 LangGraph 替你做。

### 3.2 Checkpointer 接口与三件套

`BaseCheckpointSaver` 五个**必须**实现的方法：

| 方法 | 作用 |
|---|---|
| `put` / `aput` | 存一个 checkpoint 行（一个超步一行） |
| `put_writes` / `aput_writes` | 存该超步内**各节点**的输出（`task_id, channel, value`） |
| `get_tuple` / `aget_tuple` | 读取（支持"最新"与"指定 checkpoint_id"两条路径） |
| `list` / `alist` | 列历史（供 `get_state_history`） |
| `delete_thread` / `adelete_thread` | 删除线程 |

**存储结构（两表）**：

- **checkpoints 表**：一行一个**超步**，存**序列化后的完整 graph state**（`channel_values`、`channel_versions`、`versions_seen`）、父 checkpoint 链接、metadata。
- **writes 表**：一行一个**节点输出**（`task_id, channel, value`），关联到所属 checkpoint。**这就是"pending writes 恢复"的来源**：同超步内某节点失败时，已成功节点的 writes 已落盘，resume 时不需重跑。

**官方三件套定位**：

| 包 | 定位 |
|---|---|
| `langgraph-checkpoint` | 基础接口 + serde + `InMemorySaver`（langgraph 自带的依赖） |
| `langgraph-checkpoint-sqlite` | `SqliteSaver` / `AsyncSqliteSaver`，**官方定位"local workflows"** |
| `langgraph-checkpoint-postgres` | `PostgresSaver` / `AsyncPostgresSaver`，**官方定位"production"** |

另有 MongoDB / Redis / Oracle / Azure CosmosDB 实现。

**⚠️ 单用户本地应用的取舍（本项目的实际场景）**：
- `SqliteSaver` 官方定位是 local workflows，**与"单用户本地小说系统"的定位天然契合**，且项目已经在用 SQLite（`learnstory.db`）。
- 但要接受它的已知规模上限（§3.4）与「中位 79.66 天才发一次版」（§1.5）—— 这既是"稳定"也是"修 bug 慢"。
- **建议：默认 SQLite，但通过 `BaseCheckpointSaver` 抽象隔离**，一旦需要切 Postgres 不改业务代码。项目已有的 `migrate_storage.py` 与 `DATABASE_URL` 切换机制（`requirements.txt` 中标注 Postgres 55433 端口）正好复用。

### 3.3 durability 模式（**官方已核实**）

三种模式，通过 `graph.stream({...}, durability="sync")` 指定：

| 模式 | 语义 | 崩溃后果（官方原文） |
|---|---|---|
| `"sync"` | 下一步开始前**同步落盘** | 最高持久性，有性能开销 |
| `"async"` | **默认**，异步落盘 | "存在**小概率不写 checkpoint**" |
| `"exit"` | **只在 graph 退出时持久化**（成功 / 报错 / HITL interrupt） | "对长跑 graph 性能最好，但中间状态不保存，**因此无法从进程崩溃中恢复**" |

支持文档补充：*"Note that `durability="exit"` corresponds to `checkpoint_during=False` in langgraph versions < 0.6"*。

**⚠️ 关键取舍**：`exit` 是**用崩溃可恢复性换性能**。**明确要求 resume-after-crash 的系统不应全局用 `exit`。**

**对小说系统的建议**：
- **昂贵的 LLM 生成节点**（章节正文、大纲、评审）→ 保持 `sync`。这些节点重跑一次就是真金白银加几十分钟。
- **可廉价重跑的批处理节点**（统计字数、重建索引、格式校验）→ 可用 `exit`。

### 3.4 ⚠️ Checkpoint 存储增长：官方承认的 O(N²)

**这是最硬的一条证据，因为它来自 LangChain 自己的博客**（[Delta Channels: Evolving the Agent Runtime](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)，2026-05-12）：

> "**Checkpoint storage under the default full-snapshot model grows at O(N²)** — for agents with long message histories and filesystem-backed context, this becomes a real operational cost fast."
>
> "For a coding agent running **200 turns**, current checkpointing methods serialize **5.3 GB** to the checkpointer. **Delta channels bring it to 129 MB**, over a **40x reduction**."

**社区实测（一手数据，含逐 channel 拆解）** —— [pizza-bot-app/pizza-bot#58](https://github.com/pizza-bot-app/pizza-bot/issues/58)（2026-09-03，open）：

> `checkpoints.sqlite` 在正常使用下增长到 **1.24 GB，仅 32 个线程**（3,609 个 checkpoint 行，**平均 183 KB/行**）。

| 构成 | 大小 |
|---|---|
| `checkpoints` 表 | 660 MB（3,609 行） |
| `writes` 表 | 259 MB（12,041 行） |
| **free pages（可回收未回收）** | **262 MB**（67,037/302,884 页） |

按 channel 拆分 checkpoint 字节：

| channel | 总量 | 占比 | 根因 |
|---|---|---|---|
| `files` | **332 MB**（`/skills/` 238 MB + `/large_tool_results/` 91 MB） | **51%** | **40 KB 的磁盘文本被重复序列化 3,200–3,500 次** |
| `messages` | 194 MB | 30% | |
| `__pregel_tasks` | 123 MB | 19% | **`Send` payload 携带完整历史（4 份逐字节相同的 413 KB）** |
| `__start__` | 9 MB | 1% | |

**四条根因（按贡献排序）**：
1. **静态大 payload 放进 state**：40 KB 的技能目录 → 238 MB 数据库（**~6000 倍放大**）。
2. **大工具结果进 state**：一个被卸载的结果在 **339 个连续 checkpoint** 中被重序列化，单独占 54 MB。
3. **`Send` payload 带全量上下文**：见 §2.7。
4. **无保留策略 + 无 VACUUM**：`deleteThread` 只把页移到 freelist，**文件永不缩小**（262 MB 死空间）。

**同类证据** —— [bytedance/deer-flow#4189](https://github.com/bytedance/deer-flow/issues/4189)（2026-07-15，open，12 评论）：

> "LangGraph's `AsyncSqliteSaver` / `AsyncPostgresSaver` **have no built-in retention API**. Old checkpoints accumulate indefinitely. Even with the image fix, long conversations with many tool calls still produce **O(steps × message_size) storage growth**."
>
> "A single **50KB tool output in a 20-step conversation produces ~1MB of checkpoint data**."

**运维阈值参考**（开源项目 runbook）—— [LIA-Assistant `state-size-critical.md`](https://github.com/jgouviergmail/LIA-Assistant/blob/main/docs/runbooks/langgraph/state-size-critical.md)：

> **Threshold**: P95 > 1MB (firing: 10m) | **Target**: P95 < 500KB
> "**LangGraph checkpoint performance degrades >1MB**: Slow Redis save/restore, Memory pressure, Serialization overhead"
> "### 1. Messages List Growth (**90% des cas**)"

**官方缓解手段：`DeltaChannel`（langgraph>=1.2，beta）**

```python
from langgraph.channels import DeltaChannel

class State(TypedDict):
    messages: Annotated[list[str], DeltaChannel(my_reducer, snapshot_frequency=5)]
```

- 只存**每步增量**，不重序列化全量累积值。
- `snapshot_frequency=K`：每 K 个 pregel 步写一次全量快照，把读取深度限制在 K 步内。
- 全局 `DELTA_MAX_SUPERSTEPS_SINCE_SNAPSHOT`（默认 **5000**）限制重放深度。
- **reducer 必须满足 batching-invariant（结合律）**：`reducer(reducer(state, xs), ys) == reducer(state, xs + ys)`。
- **reducer 在「重建时」运行，不在「写入时」运行** → **必须是纯函数**：`uuid.uuid4()` / `datetime.now()` 会在每次重建时产生不同结果，且**不会被持久化**。身份/稳定元数据必须在**写入上游**就分配好。

**⚠️⚠️ 不可回滚的红线**（官方原文）：

> "**Rolling back to a version without `DeltaChannel` support is not supported.** `langgraph>=1.2` writes delta channel checkpoints in a new format that earlier versions cannot read. Once a thread has used `DeltaChannel`, downgrading LangGraph leaves those checkpoints **unreadable**."
>
> "**Keep channel definitions stable for the lifetime of a thread.**"

**⚠️ 官方同时承认**："The underlying growth is **still quadratic** (because snapshots happen every K steps), but the coefficient is ~1/K of the baseline."

**→ 决策点**：**是否启用 `DeltaChannel` 必须在系统首次上线前决定。** 事后回滚会毁掉已有 checkpoint。建议：**第一期不启用**（保持简单），但在 state 设计上预留（即正文类大字段一开始就不进 state），等存储真的成为问题再切——**前提是那时没有生产中的线程依赖旧格式**。

### 3.5 ★ State 瘦身最佳实践（本项目最重要的架构约束）

**核心原则：state 里放 id，不放内容。**

```python
class NovelState(TypedDict):
    # ✅ 只放引用与元数据
    book_id: str
    volume_idx: int
    chapter_idx: int
    draft_ref: str          # file://data/books/{book_id}/ch{idx}/draft.md
    outline_ref: str
    word_count: int
    consistency_report_ref: str
    # ❌ 绝不放：
    #   chapter_text / full_outline / retrieved_docs / full messages
    #   story bible 全文（放 Store 或 DB，节点内按需读）
```

**六条具体规则**：

| # | 规则 | 依据 |
|---|---|---|
| 1 | **正文、大纲、检索结果一律外置**，state 只留路径/ID/哈希 | pizza-bot#58（40 KB → 238 MB）；deer-flow#4189（50 KB → 1 MB） |
| 2 | **`Send` payload 只放 id 和小标量** | pizza-bot#58：`__pregel_tasks` 占 19%，单点 4 份重复历史 |
| 3 | **不要往 state 放"静态大常量"**（风格库、写作规则、词典） | 同上，静态内容会被每个超步重序列化 |
| 4 | **大对象用内容寻址引用**（`sha256:...` + 桶键） | deer-flow#4189 的 `actual_path` 多实例失效问题 |
| 5 | **建保留策略 + VACUUM**（SQLite）/ `delete` + 定期 job（Postgres） | pizza-bot#58：262 MB 死空间 |
| 6 | **监控 state 大小 P95** | LIA-Assistant：告警 1 MB / 目标 500 KB |

**这条缓解措施的最大价值：它在 LangGraph 与 Temporal 之间是完全可移植的。** Temporal 的 2 MB payload 硬上限会强迫你做同样的事。**先做这个，选错引擎的代价就从"重写"降级为"换 adapter"。**

### 3.6 resume / time travel / replay

**基本 API**：

```python
config = {"configurable": {"thread_id": "book-24"}}

graph.get_state(config)                    # 最新 StateSnapshot
graph.get_state(config, subgraphs=True)    # 含子图状态
list(graph.get_state_history(config))      # 全部历史，**倒序（最新在前）**
graph.update_state(config, values, as_node="generate_topic")   # fork
graph.invoke(None, config)                 # 从当前 checkpoint 继续
graph.invoke(None, before_joke.config)     # 从历史 checkpoint 重放
```

**`StateSnapshot` 关键字段**：

| 字段 | 含义 |
|---|---|
| `values` | 该 checkpoint 的 state |
| `next` | **下一个要执行的节点名元组**；`()` 表示图已结束 |
| `config` | `thread_id` / `checkpoint_ns` / `checkpoint_id` |
| `metadata` | `source`（`"input"` / `"loop"` / `"update"`）、`writes`、**`step`（超步计数）** |
| `parent_config` | 上一个 checkpoint |
| `tasks` | 待执行任务，含 `error` / `interrupts` |

**"从第 N 章 resume"的正确实现**：不要自己解析章节号再拼 config——**用 `next` 字段定位**：

```python
history = list(graph.get_state_history(config))
# 找到「下一个要跑 write_chapter 且 chapter_idx == N」的那个 checkpoint
target = next(s for s in history
              if s.next == ("write_chapter",) and s.values.get("chapter_idx") == N)
graph.invoke(None, target.config)     # 从这里继续
```

**Replay vs Fork**：
- **Replay**：用历史 checkpoint 的 config 直接 invoke。**节点会真正重新执行**——LLM 调用、API 请求、`interrupt()` 全部**再次触发**，结果可能不同。**Replay 不是读缓存。**
- **Fork**：`update_state(prior_config, values)` 创建新分支，原历史不受影响。`update_state` **不会回滚线程**，它**新建**一个 checkpoint。

**⚠️ `as_node` 何时必须显式指定**（官方三条）：
1. **并行分支**在同一超步更新了状态，LangGraph 无法判断谁是最后（会抛 `InvalidUpdateError`）；
2. **无执行历史**（新线程上设置初始 state，测试常用）；
3. **跳过节点**（把 `as_node` 设成更靠后的节点，让图以为它已经跑过）。

**⚠️ 子图 time travel 的粒度陷阱**：
- **默认（子图继承父 checkpointer）**：父图把**整个子图当做一个超步** → **只能从子图之前 fork，无法在子图内部两个节点之间 fork**。
- **`compile(checkpointer=True)`**：子图有独立 checkpoint 历史 → 可从子图内部 fork。**但代价是不能被并行调用**（§2.8）。

**对小说系统的含义**：如果需要"从第 3 卷某一章的中间状态重跑，而不重跑前 2 卷"，**章节循环不要放在子图里，或者给子图独立 checkpointer**。

### 3.7 向后兼容与"行为版本"

官方 [Backward compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility) 给出了一个对长跑系统极其重要的判断：

> Because LangGraph runs **the latest deployed graph** against state that has been persisted for existing threads, **every change you ship is effectively a backward-compatible API change with respect to your existing checkpoints**.
>
> Unlike workflow engines that pin a run to the version of code it started with, **LangGraph applies the latest graph immediately to every thread**, both new threads and threads that resume from a checkpoint. This is convenient... It also means **you must reason about how each change interacts with runs that started under the previous version of the code**.

**三类兼容性问题**：

**(1) 技术兼容**（最常见）——破坏项：
- **重命名/删除节点**（线程停在该节点时，resume 找不到节点 → 运行失败）
- **重命名/删除 state key**（老 checkpoint 仍含它，下游仍读它）
- **收紧 state 字段**（`Optional` → 必填、缩小类型、新增无默认值的必填字段）

**推荐模式**：
- 新增字段用 `NotRequired` / `Optional[...] = None`，让老 checkpoint 仍能通过校验。
- **删除视同弃用**：保留字段定义**至少一个 drain 周期**。
- **改名走 add-then-remove**：新旧并存、双写、确认无在飞线程后再删。
- **节点函数要容忍未知 key**（`TypedDict` 运行时忽略多余 key）。
- rollout 前用 `get_state` / time travel 在 staging 上抽样验证老线程。

**(2) 业务兼容** —— 技术上都合法，但**语义变了**。官方给出的模式是**在 state 里记录"行为版本"**：

```python
def intake(state: State) -> dict:
    # 新线程盖上 flow_version=2；老线程 resume 越过 intake 的保持原值
    return {"flow_version": state.get("flow_version", 2)}

def after_triage(state: State) -> str:
    return "policy_check" if state.get("flow_version", 1) >= 2 else "respond"
```

**⚠️ 关键限定**：*"This pattern only works if you set the version **at thread start**, before any branch that needs to be versioned."*

**(3) 非确定性** —— 官方明确：**只适用于 Functional API 的 entrypoint，以及 Graph API 节点内的 `tasks` / `interrupt` 调用**。普通 Graph API 节点是"从头重跑整个函数"，**不需要保持 task 调用顺序**（但需要幂等）。

**→ 对小说系统的建议**：
- **在 state 里放 `pipeline_version: int`**，在第一卷开始时盖戳。改写作流水线时用它做分支，避免"第 1 卷用旧逻辑、第 5 卷用新逻辑"造成的风格断裂。
- **`book_id` + `pipeline_version` 应作为 trace 日志的固定列**（§10.4）。

### 3.8 序列化（serde）

| 组件 | 名称 | 位置 |
|---|---|---|
| 基类 | `BaseCheckpointSaver` | `langgraph.checkpoint.base` |
| 序列化接口 | **`SerializerProtocol`** | `langgraph.checkpoint.serde.base`（`serde` 是模块名，**不存在名为 `Serde` 的公开类**） |
| 默认实现 | **`JsonPlusSerializer`** | 内部用 **ormsgpack + JSON** |
| pickle 回退 | `JsonPlusSerializer(pickle_fallback=True)` | 用于 msgpack 不支持的对象（如 Pandas DataFrame） |
| 加密 | `EncryptedSerializer.from_pycryptodome_aes()` | 读 `LANGGRAPH_AES_KEY`，可传给任意 saver 的 `serde` 参数 |

**⚠️ 自定义 checkpointer 的两条官方要求**：
1. **metadata 必须整体存储，不要剥离未知 key**：LangGraph 会在 minor 版本里新增 metadata 字段（如 delta channel 的 `counters_since_delta_snapshot`），丢弃会导致功能静默失效。
2. **`get_tuple` 必须同时支持"最新"与"指定 checkpoint_id"两条路径**：指定 id 的路径用于 time travel，**且每次图调用都要用它重建 delta channel 状态**——"**A broken specific-id lookup silently corrupts delta channel state.**"

**→ 对小说系统的含义**：如果用 SQLite，**不要自己写 checkpointer**（官方明说定制 checkpointer 需要跑 conformance 测试套件）。除非有强需求（例如加密），否则用官方实现。

### 3.9 故障容错（langgraph>=1.2 新增，**对小说系统价值很高**）

来源：[Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)。

**三种可组合机制，作用顺序固定**：`node attempt → retry_policy → error_handler`

#### (1) RetryPolicy

```python
from langgraph.types import RetryPolicy

builder.add_node("call_api", call_api,
                 retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0))
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_attempts` | 3 | **含首次** |
| `initial_interval` | 0.5 | 首次重试前秒数 |
| `backoff_factor` | 2.0 | 每轮倍数 |
| `max_interval` | 128.0 | 上限 |
| `jitter` | True | 随机抖动 |
| `retry_on` | `default_retry_on` | 异常类型 / 序列 / 判定函数 |

**`default_retry_on` 的行为（重要）**：**默认重试任何异常**，但**排除**：`ValueError`、`TypeError`、`ArithmeticError`、`ImportError`、`LookupError`、`NameError`、`SyntaxError`、`RuntimeError`、`ReferenceError`、`StopIteration`、`StopAsyncIteration`、`OSError`。对 `requests` / `httpx` 只重试 **5xx**。`NodeTimeoutError` **默认可重试**。

**⚠️ 对本项目的坑**：`OutputParserException`（JSON 解析失败）**不在排除列表里** → **默认会被重试**。但重试同一个 prompt 通常是浪费——**JSON 修复应当走应用层逻辑（§4），而不是靠 RetryPolicy 撞运气**。建议对该节点显式 `retry_on` 收窄。

**自定义重试逻辑**：

```python
from langgraph.types import RetryPolicy, default_retry_on

def custom_retry_on(exc: BaseException) -> bool:
    if isinstance(exc, MyNonRetryableError):
        return False
    return default_retry_on(exc)
```

**检查当前尝试次数**：`runtime.execution_info.node_attempt`（1-indexed）—— 官方示例直接用它做 **fallback 模型切换**（§2.5）。

#### (2) TimeoutPolicy（**仅 async 节点；sync 节点带 timeout 会在 compile 时报错**）

```python
from langgraph.types import TimeoutPolicy

builder.add_node("call_model", call_model,
                 timeout=TimeoutPolicy(run_timeout=120, idle_timeout=30))
```

| 字段 | 语义 |
|---|---|
| `run_timeout` | **硬墙钟上限，永不刷新** |
| `idle_timeout` | **无进展才触发**；有进展信号就重置 |
| `refresh_on` | `"auto"`（默认，见下）/ `"heartbeat"`（只认 `runtime.heartbeat()`） |

`refresh_on="auto"` 下重置 idle 时钟的信号包括：state 写入、流式输出、子任务调度、stream-writer 调用、**来自该节点或其子孙的任何 LangChain callback 事件（含 LLM token）**。

超时抛 `NodeTimeoutError`（含 `node` / `elapsed` / `kind: "idle"|"run"` / `idle_timeout` / `run_timeout`），**失败尝试的 writes 会被清除**，然后交给 retry policy。

**手动心跳**：`runtime.heartbeat()` —— **在 idle-timed 尝试之外是 no-op，可以无条件调用**。

**⚠️ sync 节点的处理**：*"Node timeouts only apply to **async** nodes. Sync nodes with a `timeout` are rejected at compile time. To wrap blocking I/O, use `asyncio.to_thread` inside an async node."*

**→ 对小说系统的含义**：**所有 LLM 调用节点应当是 async**。章节生成最需要 `idle_timeout`——一个卡住的流式响应（思考 token 在跑但正文不出来）应该被杀掉重试。这与现有项目里已有的 `deadline_s` 参数、`_last_reasoning` 微产出判废逻辑（`llm/client.py:409`）是同一类需求，**LangGraph 已原生提供，不需要自己写**。

#### (3) error_handler（Saga / 补偿模式）

```python
from langgraph.errors import NodeError
from langgraph.types import Command

def payment_error_handler(state: State, error: NodeError) -> Command:
    return Command(update={"status": f"compensated: {error.error}"}, goto="finalize")

builder.add_node("charge_payment", charge_payment,
                 retry_policy=RetryPolicy(max_attempts=3, retry_on=ConnectionError),
                 error_handler=payment_error_handler)
```

- **只在重试耗尽后触发**（无 retry policy 则立即触发）。
- `NodeError` 是 frozen dataclass：`node: str`、`error: BaseException`。
- 可返回 `Command` 做**补偿 + 路由**。
- **失败来源会被 checkpoint**：若进程在节点失败后、handler 完成前崩溃，resume 时 handler 看到**同一个 `NodeError` 上下文**。
- **⚠️ `interrupt()` 抛出的异常不会进 error_handler**（它走 `GraphBubbleUp` 机制，绕过 retry policy 与 error handler）。
- **⚠️ 每个节点最多一个 handler**；**handler 自身抛错会向上冒泡**（等同于没有 handler）。

#### (4) 图级默认值：`set_node_defaults`（**强烈建议使用**）

```python
graph = (
    StateGraph(State)
    .set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=3),
        error_handler=default_error_handler,
        timeout=TimeoutPolicy(run_timeout=30),
    )
    .add_node("step_a", step_a)
    .add_node("step_b", step_b)
    .compile()
)
```

**适用性矩阵（官方）**：

| `set_node_defaults` 参数 | 普通节点 | **error-handler 节点** | 原因 |
|---|---|---|---|
| `retry_policy` | ✅ | ✅ | handler 也应有瞬时故障重试 |
| `timeout` | ✅ | ✅ | 卡住的 handler 也该被取消 |
| `error_handler` | ✅ | **❌** | **handler 绝不能捕获自己** |
| `cache_policy` | ✅ | **❌** | 缓存 handler 结果不安全 |

**⚠️ 作用域**：*"Defaults set on a parent graph are **not inherited by subgraphs**. Each graph maintains its own defaults."*
**⚠️ 优先级**：`add_node` 上的显式值 **总是**覆盖默认值；默认值在 `compile()` 时解析，所以 `set_node_defaults` 与 `add_node` 的调用顺序无关。

### 3.10 优雅停机（Graceful shutdown，langgraph>=1.2）

```python
from langgraph.runtime import RunControl
from langgraph.errors import GraphDrained

control = RunControl()
signal.signal(signal.SIGTERM, lambda *_: control.request_drain("sigterm"))

try:
    result = graph.invoke(inputs, config, control=control)
except GraphDrained as e:
    log.info("graph drained: %s", e.reason)
    # 已保存 checkpoint，下次启动用同样 config resume
```

**语义（官方表格）**：

| 场景 | 行为 |
|---|---|
| 节点执行中 | **跑完**。drain 在下一超步生效 |
| 节点正在重试 | **重试循环跑到底或成功**。drain 之后生效 |
| 图恰好在同一 tick 自然结束 | **正常返回**。用 `control.drain_requested` 区分 |
| 还有超步 | 抛 `GraphDrained(reason)`。**checkpoint 已保存，可 resume** |
| 子图请求 drain | `GraphDrained` 冒泡到父图，在父图自己的超步边界停止 |

**节点内读取 drain 状态**：

```python
async def my_node(state: State, runtime: Runtime) -> State:
    if runtime.drain_requested:
        return {"status": "skipped", "reason": runtime.drain_reason}   # 跳过昂贵工作
    return {"status": await do_work()}
```

**恢复**：`graph.invoke(None, config)`，同一 `thread_id`。

**⚠️ 官方限定**：*"`request_drain()` does **not** cancel running asyncio tasks or kill threads. For a hard upper bound, pair drain with a graceful timeout and task cancellation."*

**→ 对小说系统的含义**：这是**长跑章节生成在用户点"停止"时的正确实现**。现有项目有 `test_export_abort.py`、`pipeline._running` 集合手工管理取消——`RunControl` 提供了更干净的机制，且**保证 checkpoint 可恢复**，不会留下半写状态。

### 3.11 其他官方排查项（可直接当 checklist）

来源：[Persistence 排错章节](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)。

| 问题 | 官方诊断 | 修复 |
|---|---|---|
| `PostgresSaver` `thread_id` 过长 | `thread_id` 存在**长度受限的列**中 | **保持在 255 字符以内**；用 UUID 或 hash |
| `MemorySaver` 重启即丢 | 存 RAM | 用持久化 checkpointer（`PostgresSaver` / `SqliteSaver`） |
| checkpoint 无界增长 | 长会话累积 | **定期剪枝 / 设保留策略 / cron 删除 N 天前的 checkpoint** |
| 父图看不到子图状态更新 | **每个子图管理自己的 checkpoint namespace** | 跨图边界的数据走 **Store**，或配置子图写父 checkpoint |
| **Postgres 连接超时**（**官方点名，长跑工作流高危**） | 直接用 `PostgresSaver`（非 LangGraph Platform）时**默认整段 run 持有同一个数据库连接** | 改用 **`ConnectionPool`**（官方示例 `max_size=10, max_idle=300.0`） |
| MongoDB `DocumentTooLarge` | **单文档 16 MB** 上限（Postgres 单字段 1 GB） | 切 Postgres / 减小 checkpoint 体积 / **避免在 subgraph 里用 checkpointer** |
| TTL 不生效 | *"**TTL only applies to checkpoints created after it is enabled. Older checkpoints must be cleaned up separately.**"* | 单独清理历史 + **文档层面没有统一的 `set_ttl()` API 示例**，需自行实现 |

**DB 膨胀成因清单（官方，可直接作 checklist）**：多次 run / checkpoint 含大 state / **未配置 TTL** / **subgraph 自带 checkpointer 造成重复存储** / **大对象以 base64 或二进制直接进 state**。

**⚠️ Postgres 连接池这一条对多小时的章节生成任务极可能是隐性生产故障源**，务必在重写时直接采用 `ConnectionPool`。

---

## 4. 结构化输出与容错

来源：[Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)、[Models](https://docs.langchain.com/oss/python/langchain/models)、[OUTPUT_PARSING_FAILURE](https://docs.langchain.com/oss/python/langchain/errors/OUTPUT_PARSING_FAILURE)、[with_structured_output 参考](https://reference.langchain.com/python/langchain-core/language_models/chat_models/BaseChatModel/with_structured_output)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode)。

### 4.1 三种方法的真实差异

`with_structured_output` 的 `method` 参数（部分 provider 支持）：

| method | 机制 | 可靠性 | 何时用 |
|---|---|---|---|
| `'json_schema'` | **provider 原生结构化输出**，服务端强制 schema | **最高** | OpenAI / Anthropic / xAI / Gemini 等支持时**优先** |
| `'function_calling'` | 强制一次**工具调用**，参数即 schema | 高（受模型工具调用能力约束） | 不支持原生结构化输出的模型 |
| `'json_mode'` | 只保证"输出是合法 JSON"，**schema 必须写在 prompt 里** | **最低** | 最后的退路 |

`create_agent` 层面对应 `ProviderStrategy` / `ToolStrategy`（[结构化输出文档](https://docs.langchain.com/oss/python/langchain/structured-output)）：

- `ProviderStrategy(schema, strict=None)`：原生结构化输出。`strict` 参数 **需要 `langchain>=1.2`**（OpenAI / xAI 支持 strict schema）。
- `ToolStrategy(schema, tool_message_content=None, handle_errors=True)`：工具调用路径。
- 直接传 schema 类型时，LangChain 会**自动选择**：模型支持原生就用 `ProviderStrategy`，否则 `ToolStrategy`。**这个判断依赖模型 profile 数据（`langchain>=1.1`）**。

**⚠️ 对本项目的关键决策**：

现有项目用的是 OpenAI 兼容网关 + DeepSeek（`llm_providers.json` 里多个 provider）。**DeepSeek 走的是 `json_mode` 路线**（见 §4.4），可靠性最低的一档。

**建议的三层降级链**：

```
1. 若 provider 支持原生 json_schema → 用 ProviderStrategy / method="json_schema"
2. 否则 → ToolStrategy / method="function_calling"（强制工具调用，schema 进 tool 定义）
3. 最后才是 json_mode + 应用层修复
```

**明确反对的做法**：继续沿用"prompt 里写 JSON 示例 + 正则抠 JSON + 手写修复"的路线。现有项目 `backend/app/llm/client.py` 里有 `_salvage_truncated`、`_escape_controls_in_strings`、`_json_err_detail`、`parse_json` 一整套自研修复设施（第 1292–1450 行区间）——这套代码的存在本身就是"没有用对接口"的证据。

### 4.2 `OutputParserException` 与传统解析器

官方错误页对 `OUTPUT_PARSING_FAILURE` 的排查建议只有三条（原文）：

> * Consider using **tool calling or other structured output techniques** if possible without an output parser to reliably output parseable values.
> * Add more precise formatting instructions to your prompt.
> * If you are using a smaller or less capable model, try using a **more capable one**.

官方同时提示：*"Some prebuilt constructs like legacy LangChain agents and chains may use output parsers internally, so you may see this error **even if you're not visibly instantiating and using an output parser**."*

**→ 官方立场很清楚：能用结构化输出接口就不要用 output parser。** 传统 parser（`PydanticOutputParser` + `OutputFixingParser`）已是遗留路径。

### 4.3 错误处理与重试（`handle_errors`）

`ToolStrategy` 内置**自动重试**机制，默认 `handle_errors=True`。两类错误有专门的异常类型：

| 异常 | 触发场景 | 默认反馈给模型的内容 |
|---|---|---|
| `MultipleStructuredOutputsError` | 模型**一次调了多个**结构化输出工具（`Union` schema 时常见） | "Error: Model incorrectly returned multiple structured responses (A, B) when only one is expected. Please fix your mistakes." |
| `StructuredOutputValidationError` | Pydantic 校验失败 | 具体的校验错误（含字段名、约束、实际值） |

**`handle_errors` 的六种取值**（官方完整列表）：

```python
ToolStrategy(schema=S, handle_errors=True)              # 默认：捕获所有错误，默认模板
ToolStrategy(schema=S, handle_errors="自定义错误消息")   # 固定消息
ToolStrategy(schema=S, handle_errors=ValueError)         # 只重试该异常
ToolStrategy(schema=S, handle_errors=(ValueError, TypeError))
ToolStrategy(schema=S, handle_errors=my_handler)         # Callable[[Exception], str]
ToolStrategy(schema=S, handle_errors=False)              # 不重试，直接抛出
```

**机制本质**：错误被写进 `ToolMessage` 反馈给模型，模型看到具体错误后重试——**这是"LLM 可恢复错误"的标准实现**（与官方 `thinking-in-langgraph` 的错误分类表一致）。

**⚠️ 重要的成本含义**：每次校验失败重试 = **一次完整的 LLM 调用**。对长文本（大纲、章节 JSON）来说，一次重试可能就是几千 token。**所以 schema 设计应当尽量"宽容"而不是"严格"** —— 例如用 `str` 而不是 `Literal[...]`（除非真的需要枚举），用 `int | None` 而不是 `int`，避免让模型的轻微措辞差异触发整轮重试。

**官方原文的对应建议**：*"Do not make any field or value up"* 这类 system prompt 能显著减少校验失败。

**⚠️ 与 LangGraph `RetryPolicy` 的关系**：`OutputParserException` **不在 `default_retry_on` 的排除列表里**（§3.9），所以节点级 `RetryPolicy` 会重试它。**但重试同一个 prompt 通常是浪费**——JSON 修复必须在**应用层/结构化输出层**解决（`handle_errors`），不要靠节点的 RetryPolicy 撞运气。**建议对该类节点显式收窄 `retry_on`。**

### 4.4 中文场景的实测风险：DeepSeek JSON Output

**DeepSeek 官方文档明确列出三条要求 + 一条已知缺陷**（[JSON Output](https://api-docs.deepseek.com/guides/json_mode)）：

> To enable JSON Output, users should:
> 1. Set the `response_format` parameter to `{'type': 'json_object'}`
> 2. **Include the word "json" in the system or user prompt, and provide an example of the desired JSON format** to guide the model
> 3. **Set the `max_tokens` parameter reasonably to prevent the JSON string from being truncated midway**
>
> **⚠️ When using the JSON Output feature, the API may occasionally return empty content. We are actively working on optimizing this issue. You can try modifying the prompt to mitigate such problems.**

**⚠️⚠️ 这是本报告对现有系统最直接的一条风险提示**：**"偶尔返回空内容"是官方承认的、尚未修复的缺陷**。现有项目 `test_json_repair.py`、`_salvage_truncated`、`_note_micro_output_discard`（`llm/client.py:82`，专门处理"思考 token 已计费但正文被丢弃"）这些机制，很可能正是在对抗这类问题。

**⚠️⚠️ 另一条对现有系统的直接风险：模型名已过期（并行研究任务核实）**

| 模型名 | 状态 |
|---|---|
| `deepseek-chat` / `deepseek-reasoner` | **已于 2026-07-24 停止服务** |
| `deepseek-v4-flash` | 已退役（仍被接受，路由到 V4.1-Flash） |
| **`deepseek-flash`**（= DeepSeek-V4.1-Flash） | **当前应使用**（2026-09-10 发布） |
| **`deepseek-v4-pro`** | **当前应使用**（2026-08-13 GA） |

**规格**：1M 上下文、**max output 384K**、同时提供 OpenAI 格式（`https://api.deepseek.com`）与 **Anthropic 格式**（`https://api.deepseek.com/anthropic`）base URL。**`deepseek-flash` 支持 Vision，`deepseek-v4-pro` 不支持。**
**思考控制**：两者**同时支持 non-thinking 与 thinking，默认 thinking**；`reasoning_effort` 三档 **`low` / `high` / `max`**（2026-08-13 起）。
**⚠️ 若现有项目的 `llm_providers.json` 里仍配置 `deepseek-chat` / `deepseek-reasoner`，重写时必须替换。**

**中文 + JSON 的四个具体风险点**：

| 风险 | 机制 | 缓解 |
|---|---|---|
| **`max_tokens` 截断** | 中文 token 密度高，同样字数需要更多 token；思考模型会把 `max_tokens` 大量消耗在 reasoning 上 | **`max_tokens` 要显著放大**；现有代码里 `extra["max_tokens"] = max(4096, int(reasoning_max_tokens) * 4)` 这个 4 倍系数是合理的经验值 |
| **思考 token 挤占正文** | 现有代码注释记录："reasoning 占 completion **91%**（6139/6746），JSON 在中途被截断" | 优先级：**要么用非思考模型做结构化抽取，要么给足 `max_tokens`** |
| **空内容** | DeepSeek 官方承认 | 必须有**检测 + 重试**逻辑（不是解析失败重试，是"空响应"重试） |
| **prompt 里必须有 "json" 字样 + 示例** | DeepSeek 硬性要求 | 系统提示词中固定包含，**且应放在前缀稳定区**（§5） |

**schema 设计的中文特定建议**：

1. **字段名用英文**（`chapter_idx`、`summary`），**值用中文**。字段名用中文会增加 token 且提高 JSON 键出错概率。
2. **避免超长单字段**。如果一章正文 3000 字都要走 JSON，截断风险极高 —— **长文本用纯文本输出 + 单独的结构化元数据调用**，不要塞进同一个 JSON。
3. **`description` 写清楚**（Pydantic `Field(description=...)`），这是给模型的唯一 schema 说明。
4. **`Literal[...]` 枚举要克制**：中文同义词（"高潮"/"高潮段"/"climax"）会让枚举校验频繁失败。
5. **`include_raw=True` 便于诊断**（见 §4.5）。

### 4.5 `include_raw` 与自建修复层

```python
structured = model.with_structured_output(Movie, include_raw=True)
resp = structured.invoke("...")
# {"raw": AIMessage(...), "parsed": Movie(...) | None, "parsing_error": Exception | None}
```

官方说明：*"If **True** then both the raw model response (a `BaseMessage`) and the parsed model response will be returned. **If an error occurs during output parsing it will be caught and returned as well.**"*

**→ 这是自建修复层的正确接入点**：拿到 `raw` + `parsing_error`，而不是抛出异常。

**⚠️ 但不要重复造轮子**：如果 `ToolStrategy.handle_errors` 已经能覆盖（错误反馈给模型 + 自动重试），自建修复层只应处理**它覆盖不到的情况**——主要是：

| 场景 | `handle_errors` 能否处理 | 自建层是否必要 |
|---|---|---|
| Pydantic 校验失败 | ✅ | ❌ |
| 多个结构化输出 | ✅ | ❌ |
| **空内容**（DeepSeek 缺陷） | ❌（不是解析错误） | ✅ **需要** |
| **截断**（`max_tokens` 不够） | ❌（JSON 语法不完整） | ✅ **需要**（但优先修根因：加 `max_tokens`） |
| 缺字段但有默认值 | Pydantic 默认值可处理 | ❌ |

**建议的落地形态**：一个薄封装 —— **`call_structured(prompt, schema, model) -> (parsed, raw, diagnostics)`**，内部只做三件事：调结构化输出、检测空/截断、必要时重试一次并换模型。**不要重建现有 2000 行级别的修复设施。**

### 4.6 中文 JSON 生成的最终建议（决策清单）

| 决策 | 选择 | 理由 |
|---|---|---|
| schema 载体 | **Pydantic `BaseModel`**（结构化输出层） | 自动校验；`include_raw` 可用 |
| **state 载体** | **`TypedDict`** | 见 §2.2 / §3.5 |
| 方法 | `json_schema` > `function_calling` > `json_mode` | §4.1 |
| 失败处理 | `ToolStrategy(handle_errors=True)`，必要时自定义 handler | §4.3 |
| 截断防护 | `max_tokens` 按 reasoning 预算的 4–8 倍放大 | §4.4 |
| 空响应防护 | **自建检测 + 重试**（官方缺陷，无法靠框架解决） | §4.4 |
| 长正文 | **不进 JSON**，纯文本流式输出 + 元数据单独调用 | §4.4 / §3.5 |
| 修复设施规模 | **薄封装 ≤200 行**，不做通用 JSON 修复器 | 对照现有 312k 行 |

---

## 5. Prompt 缓存

来源：[DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache)、[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode)、[LangChain Messages](https://docs.langchain.com/oss/python/langchain/messages)、[LangChain changelog](https://docs.langchain.com/oss/python/releases/changelog)。

> **说明**：本节对 **DeepSeek**（本项目实际使用的 provider）给出**一手核实**的机制与价格；对 Anthropic / OpenAI / Qwen 的具体参数（`cache_control`、最小 token 数、TTL、`prompt_cache_key` 等）**本次未独立抓取官方页面成功**，仅给出结构性结论并在 §5.6 标注为待核实。

### 5.1 DeepSeek 上下文缓存：机制（一手核实）

**关键事实**：

> The DeepSeek API **Context Caching on Disk** Technology is **enabled by default for all users**, allowing them to benefit **without needing to modify their code**.

**⚠️ 这一点非常重要**：**DeepSeek 的缓存不需要你做任何 API 层面的改动**——但**它仍然对前缀结构极度敏感**，所以 prompt 组织方式依然决定命中率。

**缓存单元（cache prefix unit）的三种持久化时机**：

| 时机 | 说明 |
|---|---|
| **请求边界** | 每个 request 在**用户输入结束位置**和**模型输出结束位置**各产生一个 cache prefix unit |
| **公共前缀检测** | 系统检测到多个 request 的公共前缀时，把该前缀**持久化为独立单元** |
| **固定 token 间隔** | 对长输入/长输出，在固定 token 间隔切出单元，避免长前缀永远无法命中 |

**命中规则（官方两个示例，直接决定 prompt 设计）**：

| 示例 | 第一轮 | 第二轮 | 结果 |
|---|---|---|---|
| **多轮对话** | `A + B` | `A + B + C` | ✅ **命中**（完全匹配单元 `A+B`） |
| **长文问答** | `A + B` | `A + C` | ❌ **不命中**（`A+C` 不匹配 `A+B`）；但系统检测到公共前缀 `A` 并持久化 |
| （续上） | — | 第三轮 `A + D` | ✅ **命中 `A`** |

**⚠️⚠️ 对本项目最关键的推论**：

> 缓存以**「完全匹配整个 cache prefix unit」**为前提。**只要前缀单元的尾部一变，整个单元就失效。**

**具体到小说生成**：如果 prompt 结构是

```
[系统提示词(稳定)] [故事圣经(稳定)] [前情提要(每章变化)] [本章大纲(每章变化)] [写作指令(稳定)]
```

那么**每章都会因为"前情提要"变化而**——**等一下**：按 DeepSeek 的"公共前缀检测"机制，`[系统提示词 + 故事圣经]` 这一段**会在前两次请求后被识别并持久化为独立单元**，**第三轮起可以命中**。**这是本报告对成本优化的最重要发现。**

**→ 所以正确的 prompt 组织是**：

```
第 1 段（绝对稳定，跨全书不变）：系统提示词 + 写作规则 + 风格约束
第 2 段（稳定，跨多章不变）：故事圣经 / 人物设定 / 世界观
第 3 段（次稳定，同一卷内不变）：本卷大纲 / 分卷目标
第 4 段（每章变化）：前情提要、上一章结尾、本章细纲
第 5 段（每章变化，放最后）：本次生成任务指令
```

**关键点**：**变化的部分必须放在最后**，稳定的部分必须**逐字一致**地放在最前。这样 `第1+2段` 和 `第1+2+3段` 都能成为可命中的单元。

**⚠️ 现有项目的具体问题**：`story_llm` 有大量 per-book 配置文件（`prose_style_config.json`、`writer_rules_config.json`、`outline_config.json`、`long_memory_prompt_config.json`…），如果这些内容是**在 prompt 中间拼接**，或者**同一条 prompt 里混入了时间戳/书名/随机 ID**，就会破坏前缀稳定性。**重写时必须把"稳定区"变成显式的、可测量的一段。**

**其他官方限定**：

- *"The cache system works on a **best-effort** basis and does not guarantee a 100% cache hit rate."*
- *"**Cache construction takes seconds.**"*（**首次请求不会命中，第二次才会**）
- *"Once the cache is no longer in use, it will be **automatically cleared, usually within a few hours to a few days**."*
- *"The hard disk cache only matches the **prefix part of the user's input**. The output is still generated through computation and inference, and it is influenced by parameters such as `temperature`, introducing randomness."* —— **即：缓存只省 input，不省 output，也不保证输出确定性。**

**★ 官方对"10× 更便宜"的出处（并行研究任务核实）** —— DeepSeek 官方 changelog（2024-08-02）原文：

> "The DeepSeek API has innovatively adopted hard disk caching, **reducing prices by another order of magnitude**."

"order of magnitude" = 一个数量级 = **10×**。**所以"10x 更便宜"是 DeepSeek 自己的官方措辞，属实** —— 但它是 **2024 年的数字**，今天的实际倍率是 **30×–50×**（见 §5.3）。

**⚠️ 倍率随代际剧烈变化**：DeepSeek-R1 发布时（2025-01-20）官方价为 cache hit **\$0.14/M**、miss **\$0.55/M**，**hit/miss ≈ 3.9×，远非 10×**。**→ 不能对不同代际模型一概而论。**

### 5.2 用量观测字段（一手核实）

DeepSeek 在 `usage` 中提供两个字段（注意**不是** LangChain 的 `usage_metadata` 命名）：

| 字段 | 含义 |
|---|---|
| `prompt_cache_hit_tokens` | 本次输入中**命中缓存**的 token 数 |
| `prompt_cache_miss_tokens` | 本次输入中**未命中**的 token 数 |

**→ 这两个数应当成为本项目的核心成本指标**（§6.4）。**没有这两个数，你无法判断 prompt 改动是变便宜了还是变贵了。**

### 5.3 实测价格与"10 倍更便宜"说法的核验

**DeepSeek 官方定价页（2026-09 抓取）**，单位：**每 1M token 的美元价**。

| 模型 | 输入（缓存命中） | 输入（缓存未命中） | 输出 |
|---|---|---|---|
| **deepseek-flash** | **\$0.006**（峰值）/ \$0.003（低谷） | **\$0.30**（峰值）/ \$0.15（低谷） | \$1.20 / \$0.60 |
| **deepseek-v4-pro** | **\$0.044** / \$0.022 | **\$1.32** / \$0.66 | \$3.96 / \$1.98 |

> 注：峰值时段为 **UTC 周一至周五 01:00–04:00 与 06:00–10:00**，其余为低谷；低谷价为峰值价的一半。

**⚠️ 核验结论：题目中"DeepSeek cache hit is ~10x cheaper"的说法在 2026-09 已经过时——真实倍数是 30–50 倍。**

| 模型 | 命中/未命中价格比 | 倍数 |
|---|---|---|
| deepseek-flash | 0.006 / 0.30 | **50×** |
| deepseek-v4-pro | 0.044 / 1.32 | **30×** |

**→ 这条比"10 倍"更有决策价值**：**缓存命中的输入几乎免费**。对一个需要反复注入故事圣经（可能上万 token）的小说系统，**前缀缓存的收益是数量级的，不是百分之几十的**。

**⚠️ 但也意味着相反的风险**：**缓存未命中的成本是命中价的 30–50 倍**，所以**一次不小心的前缀破坏（例如在系统提示词里插入当前时间戳）会让每章多付 30–50 倍的输入成本**。这是本报告认为**最容易被忽视、也最容易修复的成本项**。

**离线时段优势**：低谷价是峰值价的一半。**批量生成应当尽量排在低谷时段**（现有项目已有 `offpeak_config.json` —— 这个方向是对的，重写时应保留）。

### 5.4 对 `ChatPromptTemplate` / `MessagesPlaceholder` 的排序约束

LangChain 的 prompt 模板渲染成**消息列表**（`SystemMessage` / `HumanMessage` / `MessagesPlaceholder` 展开的历史）。**缓存作用在这条消息序列的序列化前缀上**，因此：

| 规则 | 说明 |
|---|---|
| **`SystemMessage` 放最前，且内容逐字稳定** | 系统提示词是整个前缀的头部，**改动它等于作废整个缓存** |
| **`MessagesPlaceholder` 放中间，历史消息只在尾部增长** | 多轮时 `A+B` → `A+B+C` **能命中**（§5.1 示例 1） |
| **不要用 `MessagesPlaceholder` 在中间插入每轮变化的内容** | 会把后续所有内容推出缓存单元 |
| **`partial_variables` 只在变量来自稳定数据源时使用** | 若 partial 值每轮变（如时间戳），等于每轮破坏前缀 |
| **模板中禁止出现**：时间戳、UUID、随机数、当前书籍状态统计（字数/进度）、pid | 这些是最常见的"前缀杀手" |
| **每次请求的完整消息序列应当可序列化并哈希** | 便于事后验证"前缀是否稳定"（§5.5） |

**★ 渲染机制的三条官方事实**（并行研究任务核实）：

1. **声明顺序 = 渲染顺序 = 发送顺序 = 缓存前缀顺序。** `MessagesPlaceholder` **原地展开**为一个消息列表。
2. **`partial_variables` 在模板对象上预填充，渲染时不再变化** —— 等价于把内容"固化"进前缀，**适合放静态 system 内容、长文档、few-shot 示例**。⚠️ **但绝不要把动态值（时间戳、user_id）做成 partial** —— 那会让**同一个模板对象在不同时间产出不同前缀**，反而更难排查（因为看起来"模板没变"）。
3. **`MessagesPlaceholder` 是"变量"而非"静态块"** —— 它展开的内容由**调用方**决定，**模板本身不保证前缀稳定性**。若传入的历史消息第一条带时间戳或含 per-user 数据，**缓存照样失效**。
   **→ 结论：稳定性责任在调用方，不在模板。**

**★ 一个决定缓存命运的模板顺序对比**：

```python
# ✅ 正确：历史在前缀中段，追加式（append-only）增长 —— 缓存友好
[("system", STATIC_RULES), MessagesPlaceholder("history"), ("human", "{question}")]

# ❌ 灾难：问句在前，每轮前缀第一段就变 —— 缓存命中率归零
[("system", STATIC_RULES), ("human", "{question}"), MessagesPlaceholder("history")]
```

**官方的相关能力**：`langchain 1.1.0` 起支持把 `SystemMessage` 实例直接传给 `create_agent` 的 `system_prompt`，官方说明是 *"enabling advanced features like **cache control** and structured content blocks"* —— **即：需要供应商级缓存控制（如 Anthropic 的 `cache_control` 内容块）时，必须用 `SystemMessage` 而不是字符串。**

### 5.5 落地：缓存健康度可观测（本项目必须做）

**这是把上面所有规则变成可执行约束的唯一办法**：

```
每次 LLM 调用落库（与 §10.4 的 trace 表共用）：
  prompt_prefix_hash      -- 稳定区前 N 字符的 sha256
  prompt_full_hash        -- 完整消息序列的 sha256
  cache_hit_tokens        -- DeepSeek: prompt_cache_hit_tokens
  cache_miss_tokens       -- DeepSeek: prompt_cache_miss_tokens
  cache_hit_ratio         -- hit / (hit + miss)
```

**三条可执行的告警规则**：

1. **`cache_hit_ratio` 在连续 N 次调用中低于阈值（例如 0.5）** → 前缀被破坏，或流程刚启动（前两次必然不命中）。
2. **`prompt_prefix_hash` 在同一 pipeline 版本内发生变化** → **这是一个 bug**，不是配置变化。前缀必须逐字稳定。
3. **`cache_miss_tokens` 单次超过阈值** → 检查是否有大段动态内容被插到了前缀区。

**⚠️ 现有项目的对照**：`backend/app/observability.py` 已经有 `UsageRecord` 与 `prompt_hash()`（含 `capture_prompt_hash` 开关），`benchmark/cache_attribution.py` 也已存在。**说明"缓存归因"这个需求已经被识别过——重写时应当把它从 benchmark 提升为运行时一等公民。**

### 5.6 其他 Provider 的缓存规则（**已核实，替代初稿的"未验证"清单**）

> **⚠️ 方法声明**：`platform.claude.com` / `platform.openai.com` / `ai.google.dev` 在本机网络**被区域封锁 / Cloudflare 拦截**，无法直接抓取。以下 Anthropic / OpenAI 数字来自**官方镜像与官方公告**（AWS Bedrock 官方文档、Microsoft Learn Azure OpenAI 文档、OpenAI 官方博客公告、OpenRouter 官方文档），互相印证。**Gemini 部分为博客级证据，已标注。**

#### Anthropic

**标记语法**：把 `cache_control` 放在**要缓存内容的最后一个 content block** 上，`content` 必须是 block 数组：`system=[{"type":"text","text":LONG,"cache_control":{"type":"ephemeral"}}]`。

| 项 | 值 |
|---|---|
| **Cache write（5 分钟 TTL）** | **1.25×** 基础输入价 |
| **Cache write（1 小时 TTL）** | **2.0×** |
| **Cache read（命中）** | **0.10×**（省 90%） |
| 回本点 | **5 分钟 TTL 第 2 次命中即回本**；**1 小时 TTL 需第 3 次** |
| TTL | **滑动窗口**，每次命中重置 |
| **单请求最多 4 个 cache breakpoint** | 超出则**最后 4 个生效** |
| **前缀匹配回溯窗口 ≈ 20 个 content block** | **多轮长对话只靠"最后一轮打标记"兜不住全部历史** |
| **处理顺序** | **`tools` → `system` → `messages`**，最小 token 按**累计**判定；**改 `tools` 会废掉 `system` 和 `messages` 的缓存** |
| **cache hit 不计入 rate limit** | ✅ |
| **约束** | **长 TTL 的条目必须排在短 TTL 之前**（1h 在 5m 之前） |

**最小可缓存 token（逐模型，⚠️ 反直觉）**：

| 最小 tokens | 模型 |
|---|---|
| **512** | Claude Opus 5、Fable 5 系列、Mythos 5 |
| **1024** | Claude Sonnet 5 / 4.6 / 4.5、Claude 3.7 / 3.5 Sonnet v2、Opus 4.8 |
| **2048** | Claude Opus 4.7、Haiku 3.5 |
| **4096** | Claude Opus 4.6 / 4.5、**Haiku 4.5**、Mythos Preview |

**⚠️⚠️ 阈值不随版本单调下降**：Opus 5 只要 512，而更新的 Opus 4.6/4.5 要 4096（**8 倍差**）；Haiku 4.5（4096）比更老的 Haiku 3.5（2048）**更高**。**"新模型门槛更低""小模型门槛更低"两个推断都不成立。** **低于阈值不报错，静默忽略。**

**usage 字段**：`input_tokens` / `cache_creation_input_tokens`（1.25× 或 2×）/ `cache_read_input_tokens`（0.1×）。
**⚠️ Anthropic 的 `input_tokens` 不含缓存部分，与 OpenAI 语义相反** —— 跨 provider 求和会错。

**LangChain 集成（已核实）**：
```python
# 推荐：automatic caching，需 langchain-anthropic>=1.4.0
response = model.invoke(messages, cache_control={"type":"ephemeral"})
response = model.invoke(messages, cache_control={"type":"ephemeral","ttl":"1h"})
usage = response.usage_metadata["input_token_details"]  # cache_read / cache_creation
```
也可在 content block 上写显式断点，或用 tool 的 `extras={"cache_control": {...}}`。
**⚠️ 只在 Anthropic 原生 `/v1/messages` 生效**；走 OpenAI 兼容的 `/v1/chat/completions` **无论如何传都拿不到缓存计费**。
**⚠️ 未能核实**：`AnthropicPromptCachingMiddleware` 的导入路径（`reference.langchain.com` 对该符号返回 `Symbol not found`）。

#### OpenAI（自动前缀缓存）

| 项 | 值 |
|---|---|
| 最小前缀 | **1024 tokens** |
| 粒度 | 1024 起**按 128-token 递增**（GPT-5.5 及更早）；**GPT-5.6+ 取消取整** |
| 命中判定 | **前 1024 tokens 必须逐字节相同**；差一个字符 → `cached_tokens = 0` |
| TTL | 通常 **5–10 分钟无活动后清除**，最迟最后一次使用后 **1 小时** |
| 写入费用 | **GPT-5.6 之前：写缓存免费**；**GPT-5.6+：写按 1.25×** |
| 读取折扣 | 上线时 50%（2024-10）；**现为 0.1×（省 90%）** |
| 隔离 | **组织之间不共享**；**按模型隔离** |

**⚠️ 历史分层提醒**：`50% / 75% / 90%` 都曾是正确值 —— 2024-10-01 上线是 **50%**，后续提到 **75%**，当前为 **0.1×（90%）**。**旧教程写 50% 不是错误，而是过期。**

**`prompt_cache_key`（GPT-5.6+）**：对共享长公共前缀的请求复用同一 key。**同一 (prefix, key) 超过约 15 req/min 时部分请求会 miss** → 高并发需**分片多 key**。

**可缓存内容**：messages、图片（`detail` 须一致）、**工具定义**、**structured output schema**。
**什么**不会**失效**：尾部变动的 user message、`temperature` / `top_p` / `max_tokens`、request id / metadata / header。
**什么会失效**：前缀任意改动、**换模型**、**变量内容前置**、TTL 过期。

**读取**：Chat Completions → `usage.prompt_tokens_details.cached_tokens`；Responses → `usage.input_tokens_details.cached_tokens`。

**⚠️ LangChain 侧一个隐蔽陷阱（官方明写）**：`ChatOpenAI` 的 `stream_usage` **默认值随 `OPENAI_BASE_URL` 反转** —— *"LangChain also inspects `OPENAI_BASE_URL` to decide whether to default-enable `stream_usage`; when set, the default is left off because many non-OpenAI endpoints don't support streaming token usage."* **一旦用自建网关/中转，必须显式设 `stream_usage=True`。**

#### 阿里云百炼 / Qwen（**两种模式都有，且互斥**）

| 项目 | 显式缓存 | 隐式缓存 |
|---|---|---|
| 开关 | 必须显式加 `cache_control` 标记 | **自动开启，无法关闭** |
| 创建计费 | **输入单价 125%** | 100% |
| 命中计费 | **10%** | **20%**（阿里云百炼部署的模型默认） |
| 最小 token | **1024** | **1024** |
| 有效期 | **5 分钟（命中后重置）** | 不确定，系统定期清理 |

**⚠️ 单请求只能用其中一种。** 从标记处**向前回溯最多 20 个 content block**；**最多 4 个标记**，**超过则最后 4 个生效**。
**⚠️ 工具定义不支持独立缓存**：有 `tools` 参数时工具定义会作为 system message 的一部分参与计算，**在工具定义里加 `cache_control` 会被忽略**。

**提高 Function Calling 命中率（官方明写）**：`tools` 数组顺序一致；同一 tool 的 **JSON 字段顺序一致**；**不增不减字段**（即使为空或可选）。

**⚠️ 并行工具调用的坑与官方解法**：并行工具结果若各自作为独立 `tool` 消息回传，连续同角色消息**各占一个 content block 位置**，超过 20 个后缓存无法命中。**官方建议：把连续同角色的 tool 消息预合并为「一条消息 + 多个 content 块」**，并在最后一个块加 `cache_control`。

**⚠️ 按账号隔离、按模型隔离**，不同账号/模型之间不共享。

#### Google Gemini（**证据等级较低**）

- **隐式缓存**：Gemini 2.5 系列及更新模型**自动开启**（Google 官方博客 2025-05-09 宣布）。
- **最小 token**：Gemini 2.5 Flash **1024**、2.5 Pro **2048**（中文科技媒体转述 Google 建议）。
- **折扣**：官方宣称**最高降低 75%**。
- **usage 字段**：`cached_content_token_count`；TTL 约 **3–5 分钟且会波动**。
- **显式缓存**：2024-05 推出，**TTL 默认 1 小时**，可自定义。

> **⚠️ 诚实标注**：`ai.google.dev/gemini-api/docs/caching` **两次抓取失败**。以上为 Google 官方博客 + 中文媒体 + 网关文档的交叉转述，**属"博客级"而非"一手文档级"证据**。

### 5.7 缓存规则速查表（可直接作为 code review checklist）

| # | 规则 | 违反的代价 |
|---|---|---|
| 1 | **稳定内容放最前，变化内容放最后** | 全价输入（30–50× 成本） |
| 2 | **系统提示词逐字稳定**，禁止时间戳/UUID/随机数/进度统计 | 全价输入 |
| 3 | **故事圣经/风格规则作为独立稳定段**，不要与其他内容混拼 | 公共前缀检测失效，永远不命中 |
| 4 | **变化内容（前情提要、细纲）必须放在稳定段之后** | 后续所有内容退出缓存单元 |
| 5 | **同一逻辑前缀的序列化结果必须逐字一致**（键顺序、空格、换行） | 静默不命中 |
| 6 | **落库 `prompt_prefix_hash` + `cache_hit_tokens`/`cache_miss_tokens`** | 无法发现问题（最危险的失败模式） |
| 7 | **前两次调用必然不命中**，不要误判为 bug | 无谓排查 |
| 8 | **批量生成排在低谷时段**（DeepSeek 低谷价 = 峰值价一半） | 2× 成本 |
| 9 | **不要为了缓存而把变化内容删掉** | 质量下降（缓存是省钱手段，不是 prompt 设计目标） |
| 10 | **接入新 provider 前重新核实其缓存规则** | 照搬结论可能完全失效 |

**★ 补充规则（来自并行研究任务的多来源交叉印证）**：

| # | 规则 | 依据 |
|---|---|---|
| 11 | **`tools` 的 JSON 字段顺序必须一致，不增不减字段**（即使为空或可选） | DashScope 官方明写；**工具定义序列化为字符串参与缓存计算** |
| 12 | **改 `tools` 会废掉 `system` 和 `messages` 的缓存** | Anthropic 处理顺序为 `tools` → `system` → `messages` |
| 13 | **善用多断点分层**：工具定义 / system / 长文档 / 最近一轮对话各一个断点，**正好用满 4 个槽位** | Anthropic / DashScope 均支持 4 个 |
| 14 | **并行工具结果合并回传**（一条 tool 消息 + 多个 content 块） | DashScope 官方解法；否则连续同角色消息各占一个 block 位置，超过 20 个就失效 |
| 15 | **换模型 = 换缓存键**（四家**全部**按模型隔离）；灰度切模型、A/B、**内容安全回退**都会打断缓存 | 四家官方 |
| 16 | **多轮对话只在"最后一条 message 的最后一个 content block"打标记**，但注意 **20 content block 回溯窗口**的天花板 | Anthropic / DashScope |
| 17 | **保持稳定的请求流**（TTL 是滑动窗口），低频访问会让缓存自然过期 | OpenAI / Anthropic |

**⚠️⚠️ 两条最容易在日常开发中被忽视的"缓存杀手"（并行研究任务核实）**：

**(1) `SummarizationMiddleware` / `trim_messages` / `RemoveMessage` 会永久改写 messages → 前缀缓存全部失效。**
官方 short-term-memory 文档明确：*"The summarized conversation history is **permanently updated** - future turns will see the summary instead of the original messages."*
**→ 这是"省钱（缓存）"与"省 token（裁剪）"之间的直接冲突，必须显式权衡。** 建议把 `SummarizationMiddleware` 的触发阈值设得**尽量晚**。

**(2) `@dynamic_prompt` middleware 是缓存杀手。**
官方 context-engineering 文档的示例依据 `message_count` / `user_role` / `store` 内容**动态拼接 system prompt** → 每次不同 → 前缀不同 → 缓存失效。
**→ 若必须动态，静态部分在前、动态部分在后。**
**★ 官方在 `wrap_model_call` 注入策略上的注释恰好印证**：*"**Append at end** - models pay more attention to final messages"* —— **这个位置既符合注意力规律，也是缓存友好的。反之，把注入内容插到前面会破坏整个前缀。**

**★ "插入一个变量在前段"的代价是全有或全无，不是线性衰减**：官方原文 *"A single character difference in the first 1,024 tokens results in a cache miss."*（OpenAI）；*"Changes to a prompt prefix in subsequent requests result in cache misses."*（Anthropic）。
**数值化**：10,000 token 稳定前缀被破坏 → 正常命中是 1,000 等效 token，破坏后是 10,000 + 一次 cache write（10,000 × 1.25）→ **单次代价约 11–22 倍**。

---

## 6. Token 计量

来源：[LangChain Messages](https://docs.langchain.com/oss/python/langchain/messages)、[Models](https://docs.langchain.com/oss/python/langchain/models)、[LangGraph Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)、[LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[DeepSeek Context Caching](https://api-docs.deepseek.com/guides/kv_cache)。

### 6.1 `usage_metadata` 的确切形状

`AIMessage.usage_metadata` 是一个 `dict | None`。**官方文档给出的完整示例**（[Messages](https://docs.langchain.com/oss/python/langchain/messages)）：

```python
response = model.invoke("Hello!")
response.usage_metadata
# {'input_tokens': 8,
#  'output_tokens': 304,
#  'total_tokens': 312,
#  'input_token_details': {'audio': 0, 'cache_read': 0},
#  'output_token_details': {'audio': 0, 'reasoning': 256}}
```

**字段清单**：

| 字段 | 含义 |
|---|---|
| `input_tokens` | 输入 token 总数，**是所有输入类型之和** |
| `output_tokens` | 输出 token 总数，**是所有输出类型之和** |
| `total_tokens` | 合计 |
| `input_token_details.cache_read` | **命中缓存、从缓存读取**的输入 token |
| `input_token_details.cache_creation` | **命中失败、因而被写入缓存**的输入 token |
| `input_token_details.audio` | 音频输入 |
| `output_token_details.reasoning` | **推理 token**（示例中 304 输出里有 **256 是 reasoning**，占 **84%**） |
| `output_token_details.audio` | 音频输出 |

**⚠️ 两个官方明确的语义**：
1. **details 不必求和等于总量，也不必包含所有键** —— 官方原文。**必须防御式取值。**
2. **`input_token_details` 可含供应商私有键**（*"May also hold extra provider-specific keys."*）。

**版本分界**：`input_token_details` / `output_token_details` 在 **`langchain-core` 0.3.9** 加入。

**★ 官方提供的聚合工具 `add_usage(left, right)`**：**递归相加**两个 `UsageMetadata`（含嵌套 details），**这是自定义聚合的正确工具**，不要手写字典相加（会丢 details 合并语义）。

**⚠️ 各 provider 填充差异（对本项目直接相关）**：

| Provider | 情况 |
|---|---|
| **OpenAI** | `input_token_details: {'audio':0, 'cache_read':0}` + `output_token_details: {'audio':0, 'reasoning':N}` 是常规字段 |
| **Anthropic** | **启用缓存后**才出现 `cache_read` / `cache_creation`；**`input_tokens` 语义与 OpenAI 相反（不含缓存部分）** |
| **DeepSeek** | ⚠️ **必须用 `langchain-deepseek`，不要用 `ChatOpenAI` 硬接** |

**⚠️⚠️ DeepSeek 的官方警告（对本项目是直接风险）** —— LangChain `ChatOpenAI` 文档原文：

> "**Non-standard response fields from third-party providers (e.g., `reasoning_content`, `reasoning`, `reasoning_details`) are not extracted or preserved.** If you are using a provider that extends the Chat Completions or Responses formats, such as OpenRouter, LiteLLM, vLLM, or **DeepSeek**, **use a provider-specific package instead**."

**→ 含义**：若用 `ChatOpenAI` + 自定义 `base_url` 接 DeepSeek，**`reasoning_content` 与缓存 token 字段会丢失**。这与现有项目 `llm/client.py` 大量手工处理 `reasoning_details` / `_last_reasoning` 的现状一致 —— **重写时应当用 `langchain-deepseek` 而不是自建解析**。

**⚠️ 对本项目的直接含义**：示例里 reasoning 占输出 token 的 **84%**。现有项目注释记录过更极端的 **91%**（`llm/client.py:815`：`reasoning 占 completion 91%（6139/6746）`）。**这意味着按"输出 token"估算成本会严重低估思考模型的真实开销**，必须**分开统计 reasoning**。

**⚠️ 注意 `input_token_details` 里没有 `cache_creation`**：官方示例只展示了 `audio` 与 `cache_read`。Anthropic 的缓存**写入** token 是否映射到某个 key，**本次未核实**（见 §5.6）。**不要假设存在 `cache_creation` 字段**——应写成 `usage.get("input_token_details", {}).get("cache_creation", 0)` 这种防御式取值。

### 6.2 从流式响应获取 usage

**LangChain 的 `AIMessageChunk` 可以相加聚合**（官方原文：*"each chunk in a stream is designed to be gathered into a full message via summation"*）：

```python
full = None
for chunk in model.stream("..."):
    full = chunk if full is None else full + chunk
# full 是 AIMessageChunk，可当 AIMessage 用
```

**⚠️ usage 在流式下的行为**：
- usage 通常**只在最后一个 chunk**（或某个携带 `message-finish` 的 chunk）上出现。
- 部分 provider 需要显式开启（OpenAI 的 `stream_options={"include_usage": True}`，LangChain 侧对应 `stream_usage=True`）。**⚠️ 本次未从官方文档核实 `stream_usage` 参数的确切名称与默认值**，重写时需实测。
- **未携带 usage 的 chunk，其 `usage_metadata` 是 `None`** —— 聚合时要注意 `None + dict` 的行为。

**LangGraph 的 event streaming 提供了更干净的方式**（v3，langgraph>=1.2）：

```python
stream = graph.stream_events(input, version="v3")

for message in stream.messages:
    text = str(message.text)
    usage = message.output.usage_metadata      # ← 每次 LLM 调用的 usage
    print(text, usage)
```

官方说明：`stream.messages` 的 `message` 对象暴露 `text`（可迭代 token）、**`reasoning`（推理增量）**、**`tool_calls`**、以及 **`message.output.usage_metadata`**。

**⚠️ `message.node`**：`stream.messages` 的每个 `message` 带有 **`.node` 字段** —— **这就是"按节点归因 token"的官方入口**（见 §6.4）。

**Raw 通道的 `message-finish`**：官方原文 *"`message-finish` **may include token usage**; unrecoverable model-call failures arrive as **message error events**."* —— 注意 "may"，**不是所有 provider/所有调用都会带**。

### 6.3 `UsageMetadataCallbackHandler`（**已核实，替代初稿的"未核实"标注**）

**导入路径**：`from langchain_core.callbacks import UsageMetadataCallbackHandler`（官方模块页 `langchain_core.callbacks.usage`）。
**版本**：`Added in langchain-core 0.3.49`。
**属性**：`usage_metadata: dict[str, UsageMetadata]`，**键是 `response_metadata["model_name"]`**。

**⚠️⚠️ 四条关键限制（已读源码 `libs/core/langchain_core/callbacks/usage.py`，124 行）**：

| # | 限制 | 后果 |
|---|---|---|
| 1 | **不按 node 归因** —— 只用 `model_name` 做 key | **同一模型在多个节点被调用会被合并成一条**，无法区分节点 |
| 2 | **只取 `generations[0][0]`** | 一次调用返回多个 generation（`n>1`）时**只统计第一个** |
| 3 | `if usage_metadata and model_name:` | **provider 不填 `model_name` 或 `usage_metadata` 则静默跳过**，不报错 |
| 4 | `_lock` 只保护写入，**不保护读取** | 多线程读取 `usage_metadata` 需自行加锁 |

**上下文管理器版本**：`get_usage_metadata_callback(name="usage_metadata_callback")`，内部用 `ContextVar` + `register_configure_hook(..., inheritable=True)`。源码注释记录了一个**已修复的真实坑**：*"Without finally, post-block model calls keep accumulating into cb (see #38989)"* —— **旧版本在 `with` 块外继续调用模型会继续累加到同一个 cb**。

**→ 对本项目的结论**：`UsageMetadataCallbackHandler` **不能用于按节点归因**。它是"按模型汇总"的工具，**不是"按节点汇总"的工具**。

### 6.3b ★ 按节点归因的官方最优路径（**比自建 callback 更简单**）

**并行研究任务核实出一条本报告初稿遗漏的路径**：

> **`stream_mode=["updates"]` 的每条 update，其 dict key 就是节点名**，而 value 里带完整的 `AIMessage.usage_metadata`。

官方 streaming 文档的 "Streaming multiple modes" 示例直接打印出 `usage_metadata={...}` 带 `input_token_details: {'audio': 0, 'cache_read': 0}`。

**三条官方路径对比**：

| 路径 | 节点名来源 | 复杂度 | 推荐度 |
|---|---|---|---|
| **`stream_mode=["updates"]`** | **update 的 dict key 就是节点名** | **最低** | **⭐⭐⭐⭐⭐ 首选** |
| `stream_mode="messages"` | `metadata["langgraph_node"]`（官方明确） | 低 | ⭐⭐⭐⭐ |
| `stream_events(version="v3")` | `message.node`（官方示例） | 低 | ⭐⭐⭐⭐（新项目） |
| **自建 `BaseCallbackHandler`** | `on_llm_end(**kwargs)` 里的 metadata（**推断**） | 中 | ⭐⭐ 仅在需要细粒度时 |

**→ 建议修正**：**优先用 `stream_mode=["updates"]` 从 state 的 `AIMessage.usage_metadata` 读**，节点名就是 dict key。**自建 callback 只在需要"每次调用的完整 prompt/response 存档"时才引入**（§10.4 接缝 2 的用途）。

**⚠️ 若仍要自建 callback，一条必须先实测的假设**：官方 `on_llm_end` 签名是 `(self, response, *, run_id, parent_run_id=None, tags=None, **kwargs)`。**`metadata` 只能通过 `**kwargs` 到达**，而**未找到官方文档明写 `on_llm_end(**kwargs)` 中一定包含 `metadata` 或 `langgraph_node`**。**落地前请先跑三行代码实测 `print(sorted(kwargs.keys()))`。**

**⚠️ 流式 + callback 双计风险**：若同时（a）用 `stream_mode` 聚合 chunk 的 usage，且（b）用 callback 记录，**同一 token 会被两处各记一次**。⚠️ **官方文档未明确描述"双重计数"**，但两条路径确实各自独立地从同一个 `on_llm_end` / chunk 取数，**机制上会重复** —— 属**推断，需自测**。**业务侧不要叠加两套数字。**

**⚠️ 一条官方论坛报告的行为差异（非官方文档）**：*"`stream_mode=["updates"]` 时最终 update 里带完整 `usage`；一旦同时加 `"messages"`，chunks 与最终 update 都拿不到 usage。"*（langgraph 0.5.3/0.6.4）**→ 结论：`stream_mode` 组合会影响 usage 可见性，需实测。** 同类帖在官方论坛有多个，**说明这是高频痛点**。

### 6.4 ★ 自建"按节点归因"的 token 计量（推荐方案）

**这是本报告对本项目最有直接价值的一节**，因为现有项目已有 `observability.py` 的 `UsageRecord`、`llm_stats.py`、`trace_service.py`，重写时需要把它们统一到一个真正按节点归因的机制上。

**方案：`BaseCallbackHandler` + LangGraph 的 node metadata**

LangChain 的回调机制提供两个关键钩子：

| 钩子 | 时机 | 可获取 |
|---|---|---|
| `on_chat_model_start` | LLM 调用开始 | `run_id`、`parent_run_id`、`serialized`、`messages`、`metadata`、`tags` |
| `on_llm_end` | LLM 调用结束 | `run_id`、`response`（含 `generations[].message.usage_metadata`） |

**节点归因的两个可用来源（二者应交叉验证）**：

**(a) 从回调 metadata 拿**：LangGraph 会给节点内发起的调用附加 `metadata["langgraph_node"]`。官方在 **streaming 文档**里明确使用这个字段做过滤：

> To stream tokens only from specific nodes, use `stream_mode="messages"` and filter the outputs by the **`langgraph_node`** field in the streamed metadata.

→ 同一个字段在 callback metadata 里也可用。

**(b) 从 `run_id` 树反推**：`on_chat_model_start` 的 `parent_run_id` 指向所属的 graph 节点 run；维护 `run_id → node_name` 映射即可归因。**这是更稳的方式**，因为不依赖 metadata 的注入时机。

**建议的实现骨架**（约 100–150 行，符合"薄层"原则）：

```python
class NodeUsageTracker(BaseCallbackHandler):
    """按 (thread_id, node_name, model) 归因 token 用量，落库到自己的表。"""

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            metadata=None, tags=None, **kw):
        node = (metadata or {}).get("langgraph_node") or self._resolve(parent_run_id)
        self._pending[run_id] = {
            "node": node,
            "model": (serialized or {}).get("name") or (metadata or {}).get("ls_model_name"),
            "thread_id": (metadata or {}).get("thread_id"),
            "prompt_prefix_hash": prefix_hash(messages),   # §5.5
            "prompt_full_hash": full_hash(messages),
        }

    def on_llm_end(self, response, *, run_id, **kw):
        ctx = self._pending.pop(run_id, None)
        if not ctx:
            return
        usage = extract_usage(response)     # 见下
        self._store({**ctx, **usage})       # 落自己的 DB

    def on_llm_error(self, error, *, run_id, **kw):
        ctx = self._pending.pop(run_id, None)
        self._store({**ctx, "status": "error", "error": str(error)})
```

**`extract_usage` 的防御式实现**（必须容忍缺失）：

```python
def extract_usage(response) -> dict:
    try:
        msg = response.generations[0][0].message
    except (AttributeError, IndexError):
        return {"input_tokens": None, "output_tokens": None}
    u = getattr(msg, "usage_metadata", None) or {}
    d_in  = u.get("input_token_details")  or {}
    d_out = u.get("output_token_details") or {}
    return {
        "input_tokens":      u.get("input_tokens"),
        "output_tokens":     u.get("output_tokens"),
        "total_tokens":      u.get("total_tokens"),
        "cache_read":        d_in.get("cache_read"),
        "cache_creation":    d_in.get("cache_creation"),   # 防御式：可能不存在
        "reasoning_tokens":  d_out.get("reasoning"),
    }
```

**⚠️ 必须处理的四个陷阱**：

| 陷阱 | 表现 | 处理 |
|---|---|---|
| **provider 不返回 usage** | `usage_metadata is None` | 落 `NULL` 并标记 `usage_source="missing"`，**不要填 0**（0 会污染成本统计） |
| **流式下 usage 只在最后 chunk** | 中间 chunk 全 `None` | 用 `AIMessageChunk` **聚合后再取**（§6.2） |
| **回调与显式取值双重计数** | 同一调用被记两次 | **只选一条路径**：要么全走 callback，要么全走节点内显式取值。**不要混用。** |
| **`Send` 并行 fan-out** | 多分支同时调 LLM | callback 用 `run_id` 区分，**天然支持并行**；落库时用 `(thread_id, node, task_id)` 做粒度 |

**⚠️ 第五条（架构级）**：**usage 不应当写进 graph state。**
- 理由 1：会增加每个超步的 checkpoint 体积（§3.4）。
- 理由 2：并行分支写同一个 key 会冲突（§2.3 / §11.4）。
- **正确做法**：usage **只落你自己的 DB**，state 里最多留一个累计值（且必须用 reducer）。

**现有项目的对照**：`observability.py` 已有 `TraceHandle` / `SpanHandle` / `UsageRecord.from_any(...)`，`llm/client.py:401-405` 已经在构造 `UsageRecord`。**这说明归因机制已经存在，缺的是"按 LangGraph 节点"这一维度。** 重写时应把 `node_name` 提升为 trace 表的一等列。

### 6.5 成本核算公式（建议内置）

```python
def cost_usd(usage, price):   # price: 每 1M token 的美元价，来自配置文件
    hit   = usage.get("cache_read")   or 0
    miss  = (usage.get("input_tokens") or 0) - hit
    out   = usage.get("output_tokens")  or 0
    reas  = usage.get("reasoning_tokens") or 0     # 已在 out 内，仅用于报表拆分
    return (hit  / 1e6) * price["input_cache_hit"]
         + (miss / 1e6) * price["input_cache_miss"]
         + (out  / 1e6) * price["output"]
```

**⚠️ 关键**：**`reasoning_tokens` 已包含在 `output_tokens` 里**（官方示例：`output_tokens: 304` 且 `reasoning: 256`），**不能重复计价**。它应当只用于**报表拆分**（"本次花了多少钱在思考上"）。

**单价配置建议从配置文件读取而非硬编码**——DeepSeek 的价格**会随时间和时段变化**（峰值/低谷、模型下线）。**注意官方公告**：`deepseek-v4-pro` 原计划 2026-09-14 停止 API 服务，后决定继续提供；这类变更必须靠配置而非代码。

---

## 7. 多 Agent 模式选型

来源：[Multi-agent 概览](https://docs.langchain.com/oss/python/langchain/multi-agent/index)、[Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)、[Router](https://docs.langchain.com/oss/python/langchain/multi-agent/router)、[Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)。

> **⚠️ 重要更正（并行研究任务一手核实）**：**旧的 LangGraph "multi-agent" 概念页已不存在** —— `docs.langchain.com/oss/python/langgraph/multi-agent` **返回 404**。**多 agent 的权威文档现在在 LangChain 侧而非 LangGraph 侧**。
> **"Network" 与 "supervisor" 作为 LangGraph 命名模式已没有专门的官方页面。** 不要按旧教程的命名去找。

### 7.1 官方五种模式的性能对照

官方多 agent 文档给出的 **one-shot 请求（"Buy coffee"）模型调用次数**：

| 模式 | 模型调用次数 | 官方标记最佳 |
|---|---|---|
| **Subagents**（主 agent 把 subagent 当工具） | **4** | |
| **Handoffs**（工具调用切换 agent） | **3** | ✅ |
| **Skills**（单 agent 按需加载知识） | **3** | ✅ |
| **Router**（先分类再分发） | **3** | ✅ |
| **Custom workflow**（LangGraph 显式编排） | 官方未给单值 | — |

**⚠️ 官方另有更完整的成本数据（可直接引用于成本可预测性论证）** —— [multi-agent index](https://docs.langchain.com/oss/python/langchain/multi-agent/index) 给出三个场景：

| 场景 | Subagents | Handoffs | Skills | Router |
|---|---|---|---|---|
| **One-shot**（模型调用） | **4** | 3 ✅ | 3 ✅ | 3 ✅ |
| **Repeat**（第 2 轮调用 / 累计） | 4 / **8** | **2 / 5** ✅ | **2 / 5** ✅ | 3 / 6 |
| **Multi-domain**（调用 / token） | 5 / **~9K** ✅ | 7+ / ~14K+ | 3 / **~15K** | 5 / **~9K** ✅ |

**官方三条解读**：
- *"Subagents adds one extra call because results flow back through the main agent—**this overhead provides centralized control**."*
- *"Stateful patterns (Handoffs, Skills) **save 40-50% of calls on repeat requests**. Subagents maintain **consistent cost per request**—this stateless design provides strong context isolation but at the cost of repeated model calls."*
- Skills 的 multi-domain 是 3 calls 但 **~15K tokens**：*"After loading, **every subsequent call processes all 6K tokens of skill documentation**."* → *"Subagents processes **67% fewer tokens** overall due to context isolation."*

**⚠️ 诚实标注**：这是官方在**三个特定玩具场景**下测得的数字，**不是通用 token 成本模型**。官方未公布测量方法、模型版本或 tokenizer 细节。**用于相对比较可以，用于绝对预算估算需自行校准。**

**★ 行业成本上界警告（Anthropic 一手工程博客）**：[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

> "In our data, agents typically use about **4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats**. For economic viability, multi-agent systems require tasks where the value of the task is high enough to pay for the increased performance."

**⚠️ 同文对"共享上下文 + 高依赖"场景的明确排除（对本项目直接相关）**：

> "some domains that **require all agents to share the same context** or involve **many dependencies between agents** are **not a good fit for multi-agent systems today**."

**及生产可靠性教训**：*"**Agents are stateful and errors compound.** ... When errors occur, we can't just restart from the beginning... we built systems that can **resume from where the agent was** when the errors occurred."* 与 *"**Synchronous execution creates bottlenecks.**"*

**同文的失控案例**：*"Early agents made errors like **spawning 50 subagents for simple queries**"* —— 其对策是在 prompt 里硬编码预算规则。**⚠️ 这本身就是"用 prompt 模拟确定性调度"的补丁，恰好印证 §7.4 的结论。**

**官方能力矩阵**：

| 模式 | 分布式开发 | 并行化 | Multi-hop | 直接与用户交互 |
|---|---|---|---|---|
| Subagents | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ |
| Handoffs | — | — | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Skills | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Router | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | — | ⭐⭐⭐ |

**关键结论**：**subagents 模式最贵（4 次 vs 3 次调用）**，且**无法查看子图状态**（§2.8）。它对"分布式开发/并行化"最强，但对**成本敏感的长文本生成并不是默认最优**。

### 7.2 ⚠️ `langgraph-supervisor` 已停止维护

**这是一条对选型有直接影响的时效性事实**（官方[迁移文档](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)）：

> "The `langgraph-supervisor` package is **no longer actively maintained**."

官方要求迁移到 **subagents-as-tools** 模式。

**→ 含义**：如果你想用"supervisor"这个官方曾经推荐的模式，**不要装 `langgraph-supervisor`**。要么自己用 `StateGraph` + 条件边实现 supervisor，要么用 subagents 模式。

### 7.3 官方 workflow 模式（对小说生成最相关的三个）

LangGraph 官方 [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) 明确区分：

> **Workflows have predetermined code paths** and are designed to operate in a certain order.
> **Agents are dynamic** and define their own processes and tool usage.

官方列出五个 workflow 模式：

| 模式 | 机制 | 小说生成中的对应 |
|---|---|---|
| **Prompt chaining** | 每步处理上一步输出 + **可插门控**（gate） | 大纲 → 细纲 → 草稿 → 润色；门控 = 质量检查 |
| **Parallelization** | 多节点并行 + 聚合 | 同一章的多视角一致性检查（人物/时间线/设定） |
| **Routing** | **结构化输出分类 → 确定性分发** | 按章节类型（战斗/日常/转折）选择不同写作指令 |
| **Orchestrator-worker** | `Send` 动态 fan-out + reducer 汇聚 + synthesizer | **整卷并行生成 N 章** ← 最匹配 |
| **Evaluator-optimizer** | 生成 → 评估 → 反馈 → 重新生成（循环） | **改稿循环** ← 最匹配 |

**两个模式的官方代码骨架（对本项目可直接套用）**：

**(a) Evaluator-optimizer —— 官方推荐的"生成-评估-修订"闭环**：

```python
class Feedback(BaseModel):
    grade: Literal["funny", "not funny"]
    feedback: str

evaluator = llm.with_structured_output(Feedback)

def llm_call_generator(state: State):
    if state.get("feedback"):
        msg = llm.invoke(f"Write ... but take into account the feedback: {state['feedback']}")
    else:
        msg = llm.invoke("Write ...")
    return {"joke": msg.content}

def llm_call_evaluator(state: State):
    grade = evaluator.invoke(f"Grade the joke {state['joke']}")
    return {"funny_or_not": grade.grade, "feedback": grade.feedback}

def route_joke(state: State):
    return "Accepted" if state["funny_or_not"] == "funny" else "Rejected + Feedback"

builder.add_conditional_edges("llm_call_evaluator", route_joke,
    {"Accepted": END, "Rejected + Feedback": "llm_call_generator"})
```

**⚠️ 落地时必须加的三样东西**（官方示例没有，但生产必需）：
1. **最大迭代次数**（state 里 `revision_rounds` + 条件边判断）—— 否则可能无限循环。
2. **`recursion_limit`**（默认 25，超限抛 `GraphRecursionError`，未捕获则用户看到 500）。
3. **模型降级**（用 `runtime.execution_info.node_attempt`，§2.5）。

**(b) Orchestrator-worker with `Send`**：

```python
def assign_workers(state: State):
    return [Send("llm_call", {"section": s}) for s in state["sections"]]
```

**⚠️ 关键约束（§2.7）**：`Send` 的 payload **只放 id/小标量**，上下文由 worker 自己从 DB 加载。

### 7.4 ★ 对"多个专业写作 agent + 共享故事圣经"的选型结论

**问题**：架构师、情节师、写手、批评者、编辑、连贯性检查者 —— 6 类专职 agent 共享一本故事圣经。用哪种模式？

**结论：用「确定性状态机 + 少量 LLM 判定」的 Custom workflow，而不是 supervisor/network 自由路由。**

**五条理由**：

**(1) 阶段顺序本质上是已知的。** 大纲 → 分卷 → 细纲 → 草稿 → 一致性检查 → 修订 → 提交。官方对 workflow 的定义就是 *"predetermined code paths"* —— 这正是一本小说生产的形态。**让 LLM 决定"下一步去哪个 agent"是在为不存在的灵活性付费。**

**(2) 成本差是实打实的。** 官方数据：subagents 4 次调用 vs router/handoffs 3 次。**在小说系统里，"第 4 次调用"往往是携带完整故事圣经的那一次** —— 也就是最贵的那一次（§5.3：未命中缓存时输入贵 30–50 倍）。

**(3) 确定性边可缓存，LLM 路由不可预测。** 前缀缓存要求 prompt 结构稳定（§5）。**LLM 路由会让每次请求的 prompt 结构随决策路径变化**，直接破坏缓存命中率。

**(4) LLM 路由 + 长节点 = 官方社区实测的最常见失败源。** HN 用户 `prohobo` 的经验（§11.6）：

> "I spent weeks trying to fine-tune a prompt... I re-wrote it like 100 times, and every time I ran it it would screw up in a new way... My solution was to **decompose the node into 4 separate ones that each handle a single task**"

中文实测的经验（§11.6）：

> "**不要让 LLM 自由决定「下一步去哪」。**"

**(5) 错误率会复合。** 5 个 90% 准确率的 agent 串行 → 0.9⁵ = **59%**。**每一层 LLM 判定都是一次准确率折损。**

**推荐的具体形态**：

```
START
  → load_context          (纯函数：从 DB 读故事圣经/前情/细纲，写引用到 state)
  → plan_chapters         (LLM：产出章节列表 → 写入 DB，state 只留 chapter_ids)
  → [Send 并行] write_chapter × N
        ├─ write_draft      (LLM：正文 → 写文件，state 只留 draft_ref)
        ├─ check_continuity (LLM + 规则：人物/时间线/设定冲突)
        └─ revise           (LLM：按报告修订 → 新文件)
  → [join] aggregate      (纯函数：汇总报告)
  → consistency_gate      (确定性判定 + 阈值，不是 LLM 路由)
        ├─ pass  → commit
        └─ fail  → 回到 revise（有迭代上限）
  → commit                (纯函数：写 DB、更新索引、写记忆)
END
```

**其中只有 3 个节点调 LLM**（plan / write / check），**路由全部是确定性边**。这直接对应官方 workflow 与 agent 的分界线。

**何时才该引入 LLM 路由**：**只有当"下一步做什么"真的无法预先编码时**。对小说生成，这种情况很少（典型例子：读者反馈驱动的剧情调整 —— 这时可以用 router 模式选一个分支，但**分支本身仍是确定性的**）。

### 7.5 故事圣经的共享方式（关键设计）

**官方明确的原则**（§2.4）：

> **Keep state raw, format prompts on-demand.** Your state should store **raw data, not formatted text**.

**⚠️ 但故事圣经"原始数据"本身可能很大（几万 token），不能进 state**（§3.5）。

**推荐的三层结构**：

| 层 | 存放 | 访问方式 |
|---|---|---|
| **故事圣经全文** | **DB / 文件**（不是 state，不是 Store 的 blob） | 节点内按 `book_id` 读 |
| **state 中的引用** | `bible_version: int`、`bible_ref: str` | 判断是否需要重载 |
| **Store 中的切片** | 人物/地点/事件的分条记忆（可语义检索） | `runtime.store.asearch(...)`（§8） |

**为什么不用 Store 存全文**：
- Store 是 **KV + 语义检索**，适合"按需取相关片段"，不适合"每次都要全文"。
- 全文每次都注入的话，它应当是**prompt 的稳定前缀区**（§5.1），而 prefix 稳定性要求它**逐字一致** —— 从 DB 读固定版本比从 Store 检索更可控。

**⚠️ 一个容易踩的坑**：如果故事圣经在生成过程中被 LLM **修改**（例如"新增了一个人物"），那么**前缀会变化，缓存全废**。**建议**：故事圣经采用**版本化**策略 —— 一卷之内冻结为一个 `bible_version`，卷与卷之间才更新。这样前缀在同一卷内完全稳定。

**→ 这条与 §3.7 的"行为版本"（`pipeline_version`）是同一类设计**，可以合并到 state 里。

---

## 8. 长期记忆与 Store

来源：[Stores](https://docs.langchain.com/oss/python/langgraph/stores)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Long-term memory](https://docs.langchain.com/oss/python/langchain/long-term-memory)、[Memory 概念](https://docs.langchain.com/oss/python/concepts/memory)、[Add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)。

### 8.1 Checkpointer vs Store：职责边界（官方对照表）

| | Checkpointer | Store |
|---|---|---|
| 持久化对象 | **graph state 快照** | **应用定义的 KV 数据** |
| 作用域 | **单个 thread** | **跨 thread** |
| 记忆类型 | 短期、thread 级 | 长期、跨 thread |
| 用途 | 会话连续性、HITL、time travel、容错 | 用户偏好、事实、共享知识 |
| 访问方式 | config 里的 `thread_id` | 节点或应用代码读写 |

**→ 对小说系统的映射**：

| 数据 | 归属 | 理由 |
|---|---|---|
| 本次生成流程的进度、当前章号、重试计数 | **Checkpointer**（thread 级） | 就是"这一次任务跑了多远" |
| **故事圣经、人物卡、世界观、已发生事件** | **Store**（跨 thread） | 跨章节、跨会话都要用 |
| **章节正文** | **都不是**（文件/DB） | 太大（§3.5） |
| 追溯日志、成本 | **都不是**（自己的表） | §6.4 |

### 8.2 Store API（官方完整契约）

**五个异步方法（必需）+ 五个同步方法（可选但推荐）**：

| 方法 | 说明 |
|---|---|
| `aput(namespace, key, value, index=None)` | 存/覆盖单条 |
| `aget(namespace, key)` | 按键取单条；缺失返回 `None` |
| `adelete(namespace, key)` | 删单条 |
| `asearch(namespace_prefix, *, query=None, filter=None, limit=10, offset=0)` | 前缀检索；`query` 给定时按语义排序 |
| `alist_namespaces(*, prefix=None, suffix=None, max_depth=None, limit=100, offset=0)` | 列出命名空间 |

**`Item` 的属性**：`value`（dict）、`key`、`namespace`（tuple）、`created_at`、`updated_at`、以及 **`score`（仅 `query` 给定时）**。

**⚠️ 官方列出的三个命名空间陷阱**：

1. **前缀匹配，不是精确匹配**：`asearch(("alice",))` 也会返回 `("alice","memories")`、`("alice","preferences")` 下的项。要限定单层必须传完整 namespace 或客户端按 `item.namespace` 过滤。
2. **超出 `limit` 会被静默截断，没有溢出信号**。要么把 `limit` 设得比预期最大值大，要么用 `offset` 分页。
3. **默认排序因后端而异**：`PostgresStore` / `AsyncPostgresStore` 按 `updated_at` **降序**；`InMemoryStore` 按**插入顺序**（最近的**最后**）。**不要依赖跨实现的顺序**，需要顺序时客户端按 `item.updated_at` 排。

**→ 这三条对小说系统都是实际风险**：故事圣经查询如果依赖默认顺序，换后端（SQLite → Postgres）会静默改变行为。

### 8.3 语义检索与索引配置

```python
from langchain.embeddings import init_embeddings

store = InMemoryStore(
    index={
        "embed": init_embeddings("openai:text-embedding-3-small"),
        "dims": 1536,
        "fields": ["food_preference", "$"],     # "$" = 整个 value
    }
)

store.put(ns, key, {"food_preference": "I like pizza"}, index=["food_preference"])  # 只嵌该字段
store.put(ns, key, {"system_info": "..."}, index=False)                            # 完全不嵌
memories = store.search(ns, query="What does the user like to eat?", limit=3)
```

**要点**：
- **`fields`（store 级）与 `index`（item 级）配合**：前者定义默认嵌哪些字段，后者覆盖单条。
- **`index=False` 的项仍可 `get`/`search`（无 query），但语义检索不到**。
- **`Score` 只在 `query` 给定时出现在 `Item` 上。**
- **自定义后端若不支持向量检索，应在 `query` 非 None 时抛 `NotImplementedError`**（官方明确要求）。

**⚠️ 对本地单用户应用的现实问题**：语义检索**需要一个 embedding 模型**。

| 方案 | 优点 | 缺点 |
|---|---|---|
| 远端 embedding（OpenAI 等） | 零本地依赖 | **需要额外 API 调用与成本**；离线不可用 |
| **本地 bge-m3（现有项目已用）** | 免费、离线、中文好 | **需要 torch + 模型文件**，启动慢、占内存 |

**→ 现有项目已经部署了本地 bge-m3**（`requirements.txt` 注释 + `model/bge-m3` 目录 + 预热逻辑）。**重写时应当保留这个能力**，并把它作为 `Store` 的 `embed` 函数——**这样 Store 的语义检索可以完全离线运行**。

**⚠️ 但要注意 `SqliteStore` 的语义检索支持情况本次未核实**（见 §8.6）。

### 8.4 ★ 热路径 vs 后台写入（官方明确的两条路线）

官方 [Memory 概念](https://docs.langchain.com/oss/python/concepts/memory) 给出了这个对照，**这是本节最有价值的部分**：

**In the hot path（在生成循环内写记忆）**：

| 优点 | 缺点 |
|---|---|
| 实时更新，新记忆立即可用于后续交互 | 可能需要**新增一个工具**让 agent 决定写什么，增加复杂度 |
| 透明——用户可以被告知"记住了什么" | **"the process of reasoning about what to save to memory can impact agent latency"** |
| | **"the agent must multitask between memory creation and its other responsibilities, potentially affecting the quantity and quality of memories created"** |

参考实现：[ChatGPT 的 `save_memories` 工具](https://openai.com/index/memory-and-new-controls-for-chatgpt/)、官方 [memory-agent 模板](https://github.com/langchain-ai/memory-agent)。

**In the background（作为独立后台任务写记忆）**：

| 优点 | 缺点 |
|---|---|
| **"eliminates latency in the primary application"** | **"Determining the frequency of memory writing becomes crucial, as infrequent updates may leave other threads without new context"** |
| **"separates application logic from memory management"** | **"Deciding when to trigger memory formation is also important"** |
| **"allows for more focused task completion by the agent"** | |
| **"provides flexibility in timing memory creation to avoid redundant work"** | |

官方给出的触发策略：**按固定时间周期调度（有新事件则重排）/ cron / 用户或应用逻辑手动触发**。参考实现：[memory-service 模板](https://github.com/langchain-ai/memory-template)。

**★ 对小说系统的结论：必须用「后台写入」。**

**五条理由**：

1. **章节生成已经很慢**。再加一次"分析本章该记什么"的 LLM 调用，**用户等待时间直接翻倍**。
2. **记忆写入天然是"章节提交后"的动作**。现有项目已有 `_commit_chapter`（EditorAgent → PostProcessor → memory → docstore）阶段 —— **记忆写入应当挂在这里，而不是挂在写作节点里**。
3. **记忆写入失败不应影响章节产出**。后台任务失败可以重试；热路径失败会中断整章生成。
4. **前缀缓存要求 prompt 稳定**（§5）。**记忆在生成过程中被修改 → 圣经前缀变化 → 缓存全废**。
5. **"何时触发"在小说场景有天然答案**：**一章提交时**。不需要 cron，也不需要 LLM 判断。

**★ 落地形态（结合官方 async 模式）**：官方在 [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) 页明确区分了 *"Not to be confused with Python's `async`/`await`. Here, 'async' means the main agent kicks off a **background job (typically in a separate process or service)** and continues without blocking."*，并给出 **three-tool pattern**（Start job → Check status → Get result），但同时坦承代价是 *"**more complex**"*。

**→ 对本项目的务实选择：不需要引入 job queue。**

```
commit_chapter 节点（纯函数）:
    1. 写正文到文件/DB
    2. 更新章节索引
    3. 返回 {"committed": True, "chapter_idx": N}

图末尾挂一个 extract_facts 节点（独立节点，非热路径）:
    - 用一次结构化输出调用，批量抽取"本章新增/变更的设定事实"
    - aput 写回（1 次写入，不是 N 次）
```

**这把 N 次写入压缩成 1 次，天然落在确定性主干上** —— 既拿到 background 模式的延迟收益，又不需要引入进程外 job queue。**若确实需要跨进程异步（例如记忆抽取很慢或要换更强的模型），再复用现有 `job_queue`。**

**⚠️ 直接写 DB 还是写 Store？**

| 方案 | 何时用 |
|---|---|
| **直接写自己的 DB 表** | 结构化的故事圣经（人物、地点、事件、伏笔）—— **推荐**。查询模式是确定性的（"这个人物在第几章出现"），SQL 比语义检索更准 |
| **写 Store** | 非结构化的、需要"按语义找相关片段"的记忆（例如"之前写过的相似桥段"，用于去重） |

**→ 建议：故事圣经用 DB（结构化），Store 用于"风格样本/桥段检索"这类模糊匹配。** 这与 §9 的结论一致。

### 8.5 官方给出的两种记忆组织方式（含取舍）

官方 [Memory 概念](https://docs.langchain.com/oss/python/concepts/memory) 对比了两种语义记忆形态：

**Profile（单一不断更新的档案）**：
- 本质是"一个 JSON 文档 + 若干 KV"
- 更新时需要**把旧 profile 传给模型**，让它生成新 profile（或 [JSON patch](https://github.com/hinthornw/trustcall)）
- **官方警告**：*"This can become error-prone **as the profile gets larger**, and may benefit from **splitting a profile into multiple documents** or **strict decoding** when generating documents to ensure the memory schemas remains valid."*

**Collection（不断追加/更新的文档集合）**：
- 每条记忆范围更窄、更易生成，**"less likely to lose information over time"**
- 官方原文：*"It's easier for an LLM to generate **new** objects for new information than **reconcile** new information with an existing profile. As a result, a document collection tends to lead to **higher recall downstream**."*
- **代价**：模型必须**删除或更新**已有条目，*"which can be tricky. In addition, **some models may default to over-inserting and others may default to over-updating**."*
- 官方推荐参考 [Trustcall](https://github.com/hinthornw/trustcall)
- **更大的代价**：*"using a collection of memories can make it **challenging to provide comprehensive context** to the model... the model may **lack important contextual information** that would be more readily available in a unified profile approach."*

**★ 对小说系统的结论：混合 —— 人物卡用 Profile，事件/伏笔用 Collection。**

| 数据 | 形态 | 理由 |
|---|---|---|
| **人物卡**（姓名/身份/能力/关系/当前状态） | **Profile**（每人物一个 JSON 文档） | 需要"全貌"；人物数量有限；更新频率可控 |
| **事件时间线、伏笔清单** | **Collection**（追加为主） | 天然是 append-only；数量会增长；需要按需检索 |
| **世界观设定** | **Profile**（分模块的多个文档） | 官方警告"profile 越大越易错" → **按模块拆分** |

**⚠️ 官方警告"模型可能过度插入或过度更新"** —— 这正是现有项目里 `test_character_delta.py`、`test_entity_lifecycle.py`、`character_delta_config.json`、`test_dynamic_knowledge.py` 试图解决的问题。**重写时应把这些规则显式化为 schema 约束 + 校验**，而不是依赖 prompt 措辞。

**官方对这种情况的建议**：*"consider **evaluation** (e.g., with a tool like LangSmith) to help you **tune the behavior**"* —— 即：**记忆写入质量必须被评测**（§10）。

### 8.6 Store 的已知限制与未核实项

**已核实**：

| 限制 | 说明 |
|---|---|
| **无事务语义** | 官方文档未提及多键事务。**不要假设 `aput` 批量原子性。** |
| **超出 `limit` 静默截断** | §8.2 |
| **排序因后端而异** | §8.2 |
| **自定义 Store 没有 conformance 测试套件** | 官方原文：*"There is **currently no conformance suite** for custom stores."* **⚠️ 与 checkpointer 相反——checkpointer 官方明确要求跑 conformance 测试套件**（§3.8） |
| **Store 值必须是 JSON 可序列化的 plain dict** | *"Do not store raw Python objects that are not JSON-serializable."* |
| **TTL** | 官方 Stores 页未详述 TTL API。⚠️ 未核实 |

**✅ SQLite Store 确实存在（已核实，纠正初稿）**：

- 类：**`langgraph.store.sqlite.SqliteStore`** / **`AsyncSqliteStore`**
- 文档位置：[docs.langchain.com/langsmith/custom-store](https://docs.langchain.com/langsmith/custom-store)（作为**自定义 Store 的示例**出现），**但未列在 store-integrations 页面上**
- **⚠️ 官方对其定位有明确保留**：*"SQLite is **not recommended for use in production deployments**."*

**✅ Store 的访问方式（已核实）**：节点内通过 **`runtime.store`**（`Runtime` 对象）访问。**旧的 `config["store"]` / `store` 关键字参数形式只出现在较老的 Oracle 示例中**，不要照抄。

**★ 对本项目的含义**：**SQLite Store 可用，但官方不推荐用于生产。** 这与本项目"单用户本地"的定位存在张力 —— 但**"生产"在这里指的是多人/多副本服务器场景**，单用户桌面应用的风险面不同。

| 路径 | 做法 | 取舍 |
|---|---|---|
| **A. `SqliteStore`** | 官方提供，`AsyncSqliteStore` 支持异步 | **最省事**；但官方标注"不推荐生产"，且需自行确认语义检索支持 |
| **B. `InMemoryStore` + 自己持久化** | 启动时从自己的 DB 重建 | 简单；但**进程重启后语义索引要重建**（大书可能慢） |
| **C. 绕过 Store，自己写检索层** | 直接用现有 SQLite + bge-m3，**不用 LangGraph Store** | **最简单**；代价是失去 `runtime.store` 的便利 —— 但可用 `context_schema` 注入自己的 retriever |

**★ 建议：先走 C。** 理由：

- 故事圣经的检索模式**主要是确定性查询**（按 book_id / 人物 id / 章节范围），**语义检索是补充**。
- 现有项目已有 `test_vec_index.py`、`test_rag_keypool.py`、`test_hang_rag.py` —— **一套 RAG 检索层已经存在**。
- **Store 的最大价值是"免去自己写 KV + 向量检索"**，但**这个价值在本项目已经被现有代码覆盖了**。
- 若未来确实需要 Store，用 `context_schema` 注入的改变很小 —— **且 `SqliteStore` 始终是一个现成的退路**。

**验收标准**：`grep -r "langgraph.store" core/` 返回空 —— 与 §13 的 thin adapter 原则一致。

### 8.7 在节点中访问 Store（备用，若采用 Store）

```python
from langgraph.runtime import Runtime

async def update_memory(state: MessagesState, runtime: Runtime[Context]):
    user_id = runtime.context.user_id
    namespace = (user_id, "memories")
    await runtime.store.aput(namespace, str(uuid.uuid4()), {"memory": memory})
```

**⚠️ 两个要点**：
1. **Store 通过 `compile(store=store)` 传入，节点内从 `runtime.store` 取**（也可用 `config["store"]`）。
2. **`Store` 在 Temporal 的 Activity 节点内不可用**（`runtime.store` 为 `None`，只 warning）——**若未来引入 Temporal，这一条会破坏记忆读写**。这是"可移植性"的一个具体反例，选型时需注意。

### 8.8 命名空间设计（若采用 Store）

官方建议（§3.8 同源）：命名空间是**字符串元组**，需支持前缀匹配与大致的 O(1) 精确查键。SQL 后端的典型 schema：

```sql
CREATE TABLE store_items (
    namespace   TEXT[] NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
CREATE INDEX ON store_items USING gin(namespace);
```

**★ 对小说系统的命名空间建议**：

```python
(book_id, "bible", "characters")        # 人物卡
(book_id, "bible", "worldbuilding")     # 世界观
(book_id, "timeline", volume_idx)       # 事件时间线（按卷分区）
(book_id, "foreshadowing")              # 伏笔清单
(book_id, "style_samples")              # 风格样本（语义检索用）
("global", "writing_rules")             # 跨书共享的写作规则
```

**⚠️ 注意前缀匹配语义**：`asearch((book_id,))` 会返回该书的**全部**记忆。**要按类型查必须传完整前缀**（§8.2 陷阱 1）。

---

## 9. 知识图谱方案

> **说明**：本节的具体 API 细节（`LLMGraphTransformer` 的当前归属、Neo4j 集成现状）**由并行研究任务核实中**；以下给出**架构层面的判断依据**，均基于本报告已核实的事实（§3.4 / §3.5 / §8.5）。

### 9.1 需求澄清：小说连续性到底需要什么图能力

先把"知识图谱"这个词拆开，因为**小说连续性有四种完全不同的查询模式**：

| 查询类型 | 例子 | 需要的结构 |
|---|---|---|
| **① 属性查找** | "林雪的当前境界是什么？" | KV / 关系表 |
| **② 邻接查找** | "和林雪有关系的人物有哪些？" | 邻接表（一跳） |
| **③ 多跳路径** | "A 和 C 之间通过谁产生了关联？" | 图遍历（2–4 跳） |
| **④ 时序查询** | "在第 30 章时，林雪知道哪些事？" | **带时间维度的边**（bitemporal） |

**⚠️ 关键判断**：**④ 才是长篇小说真正难的部分**，而 **④ 恰恰是标准属性图数据库最不擅长的**。

"谁知道什么、在什么时候知道"是一个**双时间维度**问题（故事时间 + 叙述时间），Neo4j 的边只能挂属性，**没有原生的时间维度语义**。用 Neo4j 做这个，你仍然要自己在边属性上编码时间区间并在 Cypher 里手写区间查询 —— **图数据库帮不上忙**。

**而 ①②③ 在一个有索引的 SQLite 表上完全够用**：

```sql
CREATE TABLE entity (id, book_id, type, name, attrs_json, first_chapter, last_chapter);
CREATE TABLE relation (src_id, dst_id, kind, since_chapter, until_chapter, attrs_json);
CREATE INDEX ON relation(src_id);
CREATE INDEX ON relation(dst_id);
```

三跳查询用**三次索引查找**（Python 里就是三次 `SELECT`）即可，对单本书（几百个实体、几千条关系）**性能完全不是问题**。

### 9.2 四种方案的取舍（针对单用户本地应用）

| 方案 | 安装负担 | 查询能力 | 时序支持 | 对单用户本地的适配度 |
|---|---|---|---|---|
| **SQLite + 邻接表** | **零**（已有） | ①②③ 够用 | **需要自己编码，但可控** | ⭐⭐⭐⭐⭐ |
| **NetworkX（内存图 + pickle/JSON）** | 极低（纯 Python） | ①②③ 好（内置路径算法） | 弱（属性可挂，查询要自己写） | ⭐⭐⭐⭐ |
| **Kùzu / DuckDB 图扩展** | 低（嵌入式） | ③ 好（Cypher 子集） | 中 | ⭐⭐⭐ |
| **Neo4j（Docker）** | **高**（JVM + 容器 + 备份） | ①②③④ 最强 | 中 | ⭐⭐ |

**★ 结论：对单用户本地小说系统，Neo4j 是过度设计。**

**五条理由**：

1. **数据规模根本不需要**。一本 500 章的长篇，实体量级是 **10³**，关系量级是 **10⁴**。**这个规模下 SQLite 的索引查找是微秒级**，图数据库的遍历优势完全体现不出来。
2. **运维负担不成比例**。Docker + Neo4j 意味着：额外的容器、额外的备份策略、额外的版本升级、额外的内存占用（JVM 基线就是几百 MB）。**对单用户桌面应用，这是纯粹的负担。**

   **⚠️ 现有项目的对照**：`docker-compose.yml` 已经定义了 Redis / Milvus / Postgres 三个可选服务，`requirements.txt` 明确写着 *"可选基础设施（默认关闭；未安装时自动降级为 Memory/本地实现，不影响运行）"* —— **这个"默认零依赖、可选增强"的设计原则是正确的，重写时应当保留。引入 Neo4j 会破坏它。**

3. **④ 时序查询图数据库帮不上忙**（§9.1），而 ①②③ SQLite 够用。**即：花大代价换来的能力，正好不是你需要的那部分。**

4. **LLM 抽取才是真正的成本项，不是存储**。用 LLM 从章节正文抽取实体与关系，是**每章一次额外的 LLM 调用**。这个成本与"存在哪个数据库"无关。**先把抽取质量做好，比先选图数据库重要得多。**

5. **与 §8.5 的结论一致**：官方建议"人物卡用 Profile、事件用 Collection"，**两者都可以落在 SQLite 表上**。

### 9.3 推荐方案：SQLite 邻接 + 显式时序字段

**核心设计**：

```sql
-- 实体表
CREATE TABLE kg_entity (
    id            TEXT PRIMARY KEY,          -- 稳定 ID，跨章节不变
    book_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- character / location / item / faction / concept
    name          TEXT NOT NULL,
    aliases_json  TEXT,                      -- 别名（中文小说同名/改名很常见）
    attrs_json    TEXT,                      -- 结构化属性
    created_chapter INTEGER,                 -- 首次出现
    UNIQUE(book_id, kind, name)
);

-- 关系表（带时间维度）
CREATE TABLE kg_relation (
    id            INTEGER PRIMARY KEY,
    book_id       TEXT NOT NULL,
    src_id        TEXT NOT NULL,
    dst_id        TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- knows / allies / enemy / located_in / owns
    since_chapter INTEGER,                   -- 关系何时开始
    until_chapter INTEGER,                   -- 何时结束（NULL = 仍有效）
    attrs_json    TEXT,
    FOREIGN KEY(src_id) REFERENCES kg_entity(id),
    FOREIGN KEY(dst_id) REFERENCES kg_entity(id)
);
CREATE INDEX idx_rel_src ON kg_relation(book_id, src_id, since_chapter);
CREATE INDEX idx_rel_dst ON kg_relation(book_id, dst_id, since_chapter);

-- 认知表（★ 解决 ④：谁知道什么、何时知道）
CREATE TABLE kg_knowledge (
    id            INTEGER PRIMARY KEY,
    book_id       TEXT NOT NULL,
    knower_id     TEXT NOT NULL,             -- 谁知道
    fact_key      TEXT NOT NULL,             -- 知道什么（指向实体/关系/自由文本）
    learned_chapter INTEGER NOT NULL,        -- 何时知道
    confidence    REAL DEFAULT 1.0,
    source_chapter INTEGER                   -- 从哪一章推断出来的
);
CREATE INDEX idx_kn ON kg_knowledge(book_id, knower_id, learned_chapter);
```

**`kg_knowledge` 是这张设计里最重要的表** —— 它直接回答了长篇小说最容易出错的问题：**"这个角色在第 N 章时应该知道这件事吗？"**

**一次一致性检查的实现**（纯 SQL + Python，无图数据库）：

```python
def check_knowledge_leak(book_id, chapter_idx, text, entities_in_text):
    """检查本章是否使用了角色此时还不该知道的信息。"""
    for e in entities_in_text:
        # 三跳查询：该角色在本章前是否可能知道关于 e 的事实
        known = query("""
            SELECT fact_key FROM kg_knowledge
            WHERE book_id=? AND knower_id=? AND learned_chapter <= ?
        """, (book_id, e.id, chapter_idx))
        # ... 与 text 中的断言比对
```

**如果将来真的需要多跳路径查询**：把 `kg_entity` / `kg_relation` 加载进 **NetworkX**（几百个节点是毫秒级），用 `nx.shortest_path` / `nx.descendants` 做分析，**查完即弃，不持久化图结构**。

**→ 这是"SQLite 作为真相来源 + NetworkX 作为分析工具"的组合，零新增运维负担。**

### 9.4 如果确实需要图数据库：什么时候、选什么

**触发条件（满足任一才考虑）**：

1. 需要**跨书**共享世界观（多本书共用一个宇宙）→ 关系量级上升一个数量级；
2. 需要**多跳路径作为一等查询**（例如"找出所有能通过 3 跳关联到主角的隐藏势力"）；
3. 需要**图算法**（社区发现、中心度）用于自动发现剧情结构问题。

**选型优先级（单用户本地）**：

| 优先 | 方案 | 理由 |
|---|---|---|
| 1 | **DuckDB + 递归 CTE** | 已有 DuckDB/SQLite 生态；递归 CTE 可做变长路径 |
| 2 | **NetworkX + 自定义持久化** | 纯 Python，零依赖；但**无查询语言，全在内存** |
| 3 | **Neo4j（Docker）** | 能力最强，**但对单用户本地的运维负担最重** |
| ❌ | ~~Kùzu~~ | **⚠️ 已 ARCHIVED** —— 维护者声明 *"We will no longer be actively supporting KuzuDB."* **不要为新项目推荐**（本条为并行研究任务一手核实，纠正了本节初稿的建议） |

**⚠️ 无论选哪个，`kg_knowledge` 的时序语义仍需自己实现**（§9.1）。

### 9.5 LangChain 图谱集成（**已核实并纠正**）

> **⚠️ 本节已根据并行研究任务的一手核实结果修正**：

| 组件 | 核实结果 |
|---|---|
| **`langchain-experimental`** | **已正式 SUNSET**（issue #87，"takes effect immediately"，**最后一次发版 2025-12**）。**不要在新项目中使用。** |
| **`LLMGraphTransformer`** | **已迁移到 `langchain-neo4j`**：`from langchain_neo4j import LLMGraphTransformer`（包版本 **0.10.0**，MIT）。**不在 `langchain-experimental` 里了。** |
| `GraphDocument` / `Node` / `Relationship` | 随 `LLMGraphTransformer` 一同迁移 |
| `Neo4jGraph` / `GraphCypherQAChain` | 同属 `langchain-neo4j` 生态 |

**⚠️ `langchain-experimental` 的 SUNSET 对本项目是一条重要的依赖警示**：它意味着**任何仍从 `langchain_experimental` 导入的代码都是死路**。重写时必须检查并替换。

**★ 架构结论不变**：

**即便 `LLMGraphTransformer` 可用（现在在 `langchain-neo4j` 中），也不应把"抽取"和"存储"绑定到它。**

理由（基于已核实的事实）：
- **抽取是 LLM 调用** → 它是**成本项**，应当走 §4 的结构化输出路径，并纳入 §6 的 token 计量。
- **`LLMGraphTransformer` 的输出 schema 是固定的**（Node/Relationship），**而小说需要的 schema 带时间维度**（`since_chapter` / `learned_chapter`）—— 通用抽取器不会产出这些字段。
- **抽取质量需要评测**（§10）—— 绑定到框架 API 会让 A/B 测不同抽取策略变难。
- **引入 `langchain-neo4j` 会把 Neo4j 的依赖拉进来**，与 §9.2 的决策矛盾。

**→ 建议：抽取层自己写（结构化输出 + Pydantic schema），存储层用 §9.3 的 SQLite 表。** `LLMGraphTransformer` 可作为**参考实现**阅读，但**不进入生产关键路径**。

### 9.6 小结

| 决策 | 选择 | 依据 |
|---|---|---|
| 图数据库 | **不引入** | 数据规模 10³–10⁴；④ 时序查询图数据库帮不上忙；破坏"默认零依赖"原则 |
| 存储 | **SQLite 邻接表 + 显式时序字段** | 已有基础设施；查询模式确定性 |
| 时序/认知 | **`kg_knowledge` 表**（谁在第几章知道什么） | 长篇小说的核心正确性问题 |
| 多跳分析 | **NetworkX 临时加载**（不持久化） | 零依赖；用完即弃 |
| 抽取 | **自写结构化输出**（不绑框架） | 成本可控、可评测、schema 可控 |
| 升级触发条件 | 跨书共享 / 多跳一等查询 / 图算法 | §9.4 |

---

## 10. 评测与 Trace 预留

来源：[LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)、[Evaluation quickstart](https://docs.langchain.com/langsmith/evaluation-quickstart)、[Agent Evals](https://docs.langchain.com/oss/python/langchain/test/evals)、[LangSmith Observability](https://docs.langchain.com/oss/python/langchain/observability)、[Set up LangSmith](https://docs.langchain.com/langsmith/platform-setup)、[LangGraph Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)。

### 10.1 LangSmith 的三个关键事实

**(1) 免费层可用，但自托管是 Enterprise 专属。**

官方 [Set up LangSmith](https://docs.langchain.com/langsmith/platform-setup) 明确：

> Self-hosted and BYOC are available on the **[Enterprise plan](https://docs.langchain.com/langsmith/pricing-plans)**.

三种部署形态：

| 形态 | 谁运行基础设施 | 敏感数据位置 | 适用 |
|---|---|---|---|
| **Cloud** | LangChain | LangChain 云 | 最快上手 |
| **BYOC** | LangChain 管控制面，你跑数据面 | 你的 VPC | 数据要留在自己 VPC 但要托管 |
| **Self-hosted** | 你 | 你的 VPC | **需要 Enterprise 计划**（Kubernetes + ClickHouse + Postgres + Redis + blob 存储） |

**免费层与付费层（并行研究任务核实）**：**Free = 5k base traces/月、1 seat、14 天保留**；**Plus = \$39/seat、10k traces**。

**→ 结论**：**"不想要 SaaS 依赖"这个需求，用 LangSmith 自托管无法免费满足。** 必须走替代方案（§10.3）。

**★ 但有一条关键的"防锁定"路径（已核实）**：

**OpenTelemetry 是 LangSmith 的一等接入方式** —— 设 **`LANGSMITH_OTEL_ENABLED=true`** 即可，且**支持通过 OTel Collector 做 fanout**（同时导出到 LangSmith + 自建后端）。

**→ 这是"预留接口"需求的最优解**：**只要走 OTel，将来换任何后端都不用改业务代码。** 这比"自建回调"更标准化。

**(2) 评测的核心三件套（离线）**：

官方定义的三个组件：
- **Dataset**：一组测试输入（+ 可选参考输出）
- **Target function**：被测的那部分应用（单次 LLM 调用 / 一个模块 / 整个工作流）
- **Evaluators**：给输出打分的函数

**四种 evaluator 类型**：Human（人工）/ Code（规则）/ **LLM-as-judge** / **Pairwise（成对比较）**。

**离线评测工作流的四步**：建数据集 → 定义 evaluators → 跑 experiment → 分析结果（用于 benchmarking / unit test / regression test / backtesting）。

**(3) 在线评测（Online Evaluation）**：

> Evaluators run automatically on **runs** or **threads**, providing real-time monitoring, anomaly detection, and alerting.

**支持"采样率"** 控制成本（官方：*"Apply filters and sampling rates to control costs"*）—— **对长跑小说系统，这个"采样而不是全量评测"的能力很关键**（每章都跑 LLM 评测会显著推高成本）。

### 10.2 离线长文本评测的可行路径

**⚠️ 首先明确一个困难**：**长篇小说没有参考答案**。"golden chapter" 这种东西对创作类任务不存在，所以**基于参考输出的评测（reference-based）基本不可用**。

**官方 Agent Evals 提供的两类方法**（[文档](https://docs.langchain.com/oss/python/langchain/test/evals)）：

| 方法 | 适用 | 对小说系统 |
|---|---|---|
| **Trajectory match**（轨迹匹配，确定性） | *"You know the expected tool calls and want fast, deterministic, cost-free checks"* | **部分可用**：可用于验证**流程正确性**（"这一章是否先做了连贯性检查再提交"），**不是验证文笔** |
| **LLM-as-judge**（LLM 裁判） | *"You want to assess overall quality and reasoning without strict expectations"* | **主力方法** |

**Trajectory match 的四种模式**（`create_trajectory_match_evaluator`）：

| 模式 | 语义 | 小说场景 |
|---|---|---|
| `strict` | 消息结构与工具调用**顺序完全一致** | 验证流水线阶段顺序（例如"必须先加载圣经再写作"） |
| `unordered` | 工具调用集合一致，**顺序可变** | 验证"多种检查都做了" |
| `subset` | **只允许**参考中的工具（不能多） | **成本控制**：验证没有调用计划外的昂贵模型 |
| `superset` | **至少**包含参考工具（可多） | 验证关键步骤没被跳过 |

**LLM-as-judge 的官方入口**：`create_trajectory_llm_as_judge(model=..., prompt=...)`，支持 `TRAJECTORY_ACCURACY_PROMPT` 与 `TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE`。**全部 evaluators 都有 async 版本**（`create_async_*`）。

**★ 针对长篇小说的评测设计建议**：

| 评测维度 | 方法 | 是否需要参考 |
|---|---|---|
| **流程正确性** | Trajectory match（`strict` / `superset`） | 需要（可手写） |
| **连贯性**（人物/时间线/设定冲突） | **规则 + 结构化抽取比对**（§9.3 的 `kg_knowledge`） | **不需要** —— 这是可确定性验证的 |
| **文风一致性** | 与风格基线的**embedding 距离** / 风格特征统计 | 不需要 |
| **质量/吸引力** | **LLM-as-judge + rubric** | 不需要参考，但需要 rubric |
| **版本对比**（A/B） | **Pairwise 比较** | 不需要 |
| **成本** | §6.4 的 token 计量表 | 不需要 |

**⚠️ 评测可靠性的已知问题（本次未独立核实，仅作为设计提醒）**：LLM-as-judge 存在**位置偏置**（pairwise 时偏好先出现的）、**冗长偏置**（偏好更长的答案）、**自我偏好**（偏好同族模型输出）。**缓解手段**：交换位置重复评测、控制长度变量、用不同族的模型做裁判。**这些是通用实践，重写时应当内置到 judge 封装里，而不是每次手写。**

**⚠️ 一条重要的成本提醒**：LLM-as-judge 对长文本（一章 3000 字）意味着**又一次长输入 LLM 调用**。**评测成本可能达到生成成本的 20–50%**。**必须采样，不能全量。**

### 10.3 替代方案：不依赖 SaaS 的评测与 Trace

**⚠️ 本节的具体对比（Langfuse / Phoenix / Opik / OpenTelemetry 的版本、License、LangChain 集成方式）由并行研究任务核实中**；以下给出**基于已核实事实的架构判断**。

**已核实的关键约束**：

| 事实 | 影响 |
|---|---|
| LangSmith **自托管需 Enterprise** | 免费路径必须自建或用第三方 |
| LangSmith 通过**环境变量**接入：`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`（[官方](https://docs.langchain.com/oss/python/langchain/observability)） | **这是唯一的"开关"** —— 只要不设这两个变量，LangChain 就不会上报 |
| 支持 `tracing_context(enabled=True/False)` **按调用粒度开关** | **可以做到"默认关闭、需要时开启"** |
| 支持 **`tags` 与 `metadata`** 注入（`config={"tags": [...], "metadata": {...}}`） | **这些 metadata 是自建 trace 的天然载体** |
| LangGraph 提供 `checkpoints` / `tasks` / `debug` 三种 stream mode | **可以在不接 LangSmith 的情况下拿到执行细节**（见 §10.4） |

**★ 最重要的一条架构结论**：

> **LangChain 的 tracing 是"环境变量驱动 + 可选"的，因此"不接 LangSmith 也能跑"是默认状态。**
> **但"没有 tracing 就没法调试"是真实风险**（§11.7 的社区证词：*"at some point I had more debugging code than actual agent code"*）。

**→ 所以正确的做法不是"以后再说"，而是现在就把自建 trace 的表结构定下来。**

**★ 官方给出的三条集成通道（这是"预留接缝"的机制基础）**：

| 通道 | 用法 | 防锁定程度 |
|---|---|---|
| **`config={"callbacks": [...]}`** | 显式传 handler（Langfuse、`LangChainTracer` 都用这个） | 中 —— 换厂商改一处 |
| **环境变量自动启用** | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`；或 `LANGSMITH_OTEL_ENABLED=true` | 高 |
| **全局 OTel `TracerProvider`** | *"Set a global trace provider **before initializing LangChain components**, which **LangSmith will detect and use instead of creating its own**"* | **最高 —— 零锁定** |

**⚠️ 两个已核实的 OTel 陷阱**（[Trace with OpenTelemetry](https://docs.langchain.com/langsmith/trace-with-opentelemetry)）：

1. **`OTEL_EXPORTER_OTLP_ENDPOINT` 是 base URL** —— *"OTLP/HTTP exporters append the signal path (`/v1/traces`) themselves, so **do not include it**: setting the base endpoint to `https://api.smith.langchain.com/otel/v1/traces` sends traces to `/otel/v1/traces/v1/traces`, which returns **404**."*
2. **孤儿子 span 会被静默丢弃** —— *"if the parent span is **never exported** to LangSmith, the buffered child **expires and never appears as a run**. LangSmith will **not return an error**, because the `200` was sent before processing."*（自托管窗口由 `REDIS_RUNS_EXPIRY_SECONDS` 控制，默认 12 小时）

**★ OTel fanout 是"防锁定"的最优解**（一份 span 同时送多个后端，代码零改动）：

```yaml
exporters:
  otlphttp/langsmith:  { traces_endpoint: https://api.smith.langchain.com/otel/v1/traces,
                         headers: { x-api-key: ${env:LANGSMITH_API_KEY}, Langsmith-Project: my_project } }
  otlphttp/local:      { traces_endpoint: http://localhost:4318/v1/traces }
service:
  pipelines:
    traces: { receivers: [otlp], processors: [batch], exporters: [otlphttp/langsmith, otlphttp/local] }
```

官方对 fanout 的定位：*"**Add or remove destinations without changing application code**"*。只送 OTel 不送 LangSmith 用 `LANGSMITH_OTEL_ONLY=true`。

**⚠️ 一条部署陷阱（对本地服务型应用相关）**：Langfuse 官方文档指出 —— *"Since Langchain version > 0.3.0, the callbacks on which Langfuse relies have been **backgrounded**. This means that execution will **not wait** for the callback to either return before continuing."* 在 serverless / 短生命周期进程里会丢 trace，需要设 **`LANGCHAIN_CALLBACKS_BACKGROUND=false`**。**本地长跑进程不受影响，但若做 CLI 一次性命令需注意。**

### 10.4 ★ 应当预留的接缝（本节的落地清单）

**这是本项目"预留接口"需求的具体答案。** 五类接缝，全部**不依赖任何 SaaS**：

#### 接缝 1：Callback Handler 协议（框架层）

**做法**：实现一个 `BaseCallbackHandler` 子类（§6.4 的 `NodeUsageTracker`），**它同时承担 token 计量 + trace 落库**。

**为什么这是正确的接缝**：官方提供的 callback 机制是**框架无关的观察点**——它不依赖 LangSmith。**只要把逻辑写在这里，将来接不接 LangSmith 都不用改业务代码。**

**预留的开关**：

```python
LS_TRACE_ENABLED=false          # 默认关闭（对应现有 trace_config.py 的 DEFAULTS）
LS_TRACE_MODEL_IO=false         # 是否记录原始 prompt/response（隐私 + 体积）
LS_TRACE_PROMPT_HASH=true       # 只记录哈希
```

**现有项目的对照**：`trace_config.py` 已有 `{"enabled": False, "persist_attempts": True, "persist_spans": True, "capture_prompt_hash": True}` —— **这个默认值设计（默认关闭 + 只存哈希）是正确的，重写时应保留。**

#### 接缝 2：一次 LLM 调用一行的日志表（数据层）★ 最重要

```sql
CREATE TABLE llm_call (
    id                INTEGER PRIMARY KEY,
    trace_id          TEXT NOT NULL,          -- 一次 graph run
    thread_id         TEXT,                   -- LangGraph thread
    checkpoint_id     TEXT,                   -- LangGraph checkpoint（time travel 用）
    task_id           TEXT,                   -- Send 分支标识
    node_name         TEXT NOT NULL,          -- ★ 按节点归因
    step_index        INTEGER,                -- 超步序号
    attempt           INTEGER,                -- runtime.execution_info.node_attempt

    book_id           TEXT,
    pipeline_version  INTEGER,                -- ★ §3.7 行为版本
    chapter_idx       INTEGER,

    model             TEXT NOT NULL,
    ts_start          TEXT NOT NULL,
    ts_end            TEXT,
    latency_ms        INTEGER,

    -- token / 成本（§6.4）
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    reasoning_tokens  INTEGER,
    cache_read_tokens INTEGER,                -- ★ §5.5
    cache_miss_tokens INTEGER,                -- ★
    cost_usd          REAL,

    -- 缓存健康度（§5.5）
    prompt_prefix_hash TEXT,
    prompt_full_hash   TEXT,

    -- 诊断（默认只存哈希/引用，不存正文）
    input_ref         TEXT,                   -- 原文的存储引用（可为 NULL）
    output_ref        TEXT,
    input_digest      TEXT,
    output_digest     TEXT,

    status            TEXT NOT NULL,          -- ok / error / empty / truncated
    error_type        TEXT,
    error_message     TEXT
);
CREATE INDEX idx_llm_call_trace ON llm_call(trace_id, step_index);
CREATE INDEX idx_llm_call_book  ON llm_call(book_id, chapter_idx);
CREATE INDEX idx_llm_call_node  ON llm_call(node_name, ts_start);
```

**这张表一次性解决六件事**：
1. **按节点归因 token 成本**（`node_name`）
2. **prompt 缓存健康度监控**（`cache_read_tokens` / `prefix_hash`，§5.5）
3. **崩溃后的事后取证**（不依赖 LangSmith）
4. **A/B 测试成本对比**（`pipeline_version` 分组）
5. **评测的成本核算**（judge 调用也走这张表）
6. **`status="empty"` 的专项统计**（DeepSeek 空响应缺陷，§4.4）

**★ 这张表是"评测与 Trace 预留"的核心答案。** 有了它，接 LangSmith 是"多一路数据源"，不接也无所谓。

#### 接缝 3：评测数据集表（评测层）

```sql
CREATE TABLE eval_dataset (id, name, created_at, notes);
CREATE TABLE eval_case (
    id, dataset_id, input_json, reference_json,     -- reference 可为 NULL（创作类）
    source_chapter_idx, tags_json
);
CREATE TABLE eval_run (
    id, dataset_id, target_version,                 -- ★ 对应 pipeline_version / git sha
    model_config_json, judge_model, started_at, finished_at
);
CREATE TABLE eval_score (
    id, run_id, case_id, evaluator_name,             -- coherence / style / judge_overall
    score REAL, comment TEXT, raw_ref TEXT
);
```

**现有项目的对照**：`backend/app/evaluation_store.py` 已有 `evaluations.db`（"deliberately separate from LearnStory's production SQLite DB"）+ `benchmark/evaluations.db`。**这个"评测库与生产库分离"的设计是正确的**，重写时应保留并把上面的表结构补齐（尤其是 `target_version` 字段 —— 没有它就无法做版本对比）。

**关键预留**：
- **`target_version` 必须存在**，且与 `pipeline_version` / git sha 关联 —— 否则评测结果无法归因到代码版本。
- **`judge_model` 必须记录** —— 换裁判模型会让历史分数不可比。
- **reference 允许 NULL** —— 创作类任务没有标准答案。

#### 接缝 4：Judge 封装（模型层）

**不要在每个测试里手写 judge prompt。** 封装一个：

```python
def judge(task: str, content: str, rubric: Rubric, model: str) -> JudgeResult:
    """
    统一的 LLM 裁判封装。内置：
      - rubric 模板
      - 结构化输出（§4）
      - 位置偏置缓解（pairwise 时双向评测）
      - usage 落库（走接缝 2，evaluator_name='judge'）
    """
```

**预留的意义**：换裁判模型、改 rubric、加去偏策略，**都只改这一个函数**。

#### 接缝 5：LangGraph 原生事件的消费（可选增强层）

**不接 LangSmith 也能拿到的执行细节**（全部是官方能力）：

| 来源 | 内容 |
|---|---|
| `stream_mode="checkpoints"` | 每个 checkpoint 事件，格式同 `get_state()` |
| `stream_mode="tasks"` | 任务开始/结束事件，**含结果与错误** |
| `stream_mode="debug"` | `checkpoints` + `tasks` + 额外 metadata |
| `graph.get_state_history(config)` | **完整历史**（`values` / `next` / `metadata.writes` / `step` / `parent_config`） |
| `checkpointer.list(...)` / `get_tuple(...)` | 底层 checkpoint 记录 |

**⚠️ 一个已核实的坑**（[langgraph#8704](https://github.com/langchain-ai/langgraph/issues/8704)）：**事件流的 `values` payload 会丢弃每条消息的 `additional_kwargs`**，而 `GET /threads/{id}/state` 返回完整记录 —— *"same message has **two different shapes** depending on which endpoint you ask"*。

**→ 教训：不要相信任何单一数据源。** 接缝 2 的自建表是权威记录，LangGraph 的事件流只作辅助。

### 10.5 预留接口的验收标准

重写完成后，以下命令应当全部通过：

```bash
# 1. 业务逻辑不依赖框架
grep -r "langgraph" core/ && echo "FAIL" || echo "PASS"

# 2. 未配置 LangSmith 也能完整运行
env | grep -c LANGSMITH   # 期望 0；且系统功能完整

# 3. 每次 LLM 调用都有记录
python -c "assert count_rows('llm_call') >= count_llm_calls(); print('PASS')"

# 4. 能按节点报告成本
python -c "print(cost_by_node(book_id='b24'))"   # 非空

# 5. 能按 pipeline_version 对比评测结果
python -c "print(eval_compare(dataset='quality_v1', v1=1, v2=2))"
```

**★ 第 2 条是最重要的一条**：**"不设 `LANGSMITH_*` 环境变量时系统完全可用"** —— 这是"无 SaaS 依赖"的可测定义。

---

## 11. 生产坑与批评

> **本节为独立取证**（50+ 次检索：HN Algolia API、GitHub REST API、官方迁移文档、中英文技术社区）。**issue 编号与 URL 均为实际检索所得。**

### 11.1 过度抽象与"黑盒"——最持久、最一致的批评

**最权威的一手证据来自 Anthropic**（模型厂商，有动机说反话）：

> "frameworks... often create extra layers of abstraction that can **obscure the underlying prompts and responses, making them harder to debug**... **We suggest that developers start by using LLM APIs directly**."
> — [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

**经典 postmortem**（HN 480 分 / 297 评论）：[Octomind: "Why we no longer use LangChain"](https://www.octomind.dev/blog/why-we-no-longer-use-langchain-for-building-our-ai-agents)（[HN 讨论](https://news.ycombinator.com/item?id=40739982)）

| 用户 | 原话（节选） |
|---|---|
| `huevosabio` | 做过 **content generation（authoring / researcher / editor agent）**，结论是 **"easier to just write it as 3 sequential prompts with an explicit control loop"** |
| `empiko` | 简单场景 **too complex**，复杂场景 **too difficult to adapt**，找不到 sweet spot |
| `autokad` | prompt engineering 需要看到每一步发生了什么，而 **"langchain makes that harder if not impossible"** |
| `spullara` | **"every good developer i know that has started using langchain stopped"** |
| `Kydlaw` | 原型阶段有用，**"aside from that, I don't think I would run it in production"** |

**⚠️ `huevosabio` 那条对本项目直接相关**：他做的正是 authoring/researcher/editor 三类 agent 的内容生成，结论是"顺序 prompt + 显式控制循环"更容易调试和控制。

**维护者自己承认了** —— Harrison Chase（LangChain CEO）在同一帖回复：*"The initial version of LangChain was **pretty high level and absolutely abstracted away too much**. We're moving more and more to low level abstractions."*

**[Minimaxir 的案例研究](https://minimaxir.com/2023/07/langchain-problem/)**：*"I **wasted a month** learning and testing LangChain... one of the few pieces of software that **increases overhead** in most of its popular use cases."* 他记录的失败链条是：agent 的工具选择依赖"让模型输出合法 JSON 然后祈祷" —— *"**just hoping everything goes well**"*。

**⚠️ 这直接印证了 §4.1**：**输出格式必须由接口保证（`json_schema` / tool calling），不能靠 prompt + 祈祷。** 现有项目的 JSON 修复设施正是这种"祈祷"路线的产物。

### 11.2 LangGraph 专属：学习曲线与心智负担

**[AgentRank 实测 review](https://www.agentrank.tech/blog/langgraph-honest-take-state-machine-agents)**：*"manages to make 'call an LLM and decide what to do next' feel like **you're writing a compiler**"*；*"**The Learning Curve Is Brutal.** ... The official docs have this weird habit of showing you **advanced patterns before you've understood the basics**."*

**[中文一手生产实践（CSDN，2026-06-13）](https://mcp.csdn.net/6a3a56e510ee7a33f281455c.html)**：

> "**LangGraph**：需要 **10-14 个工程日**才能熟练；**State schema 一旦上线就很难改动，早期设计错误代价极高**。"
> "**自研调度器**：灵活性最高，但 **observability 几乎为零，调试成本是其他方案的 3-5 倍**；没有 30+ 工程日的投入不要轻易尝试。"

**⚠️ 对照刺眼**：现有系统就是"自研调度器"路线（312k 行），**"调试成本 3-5 倍"与"30+ 工程日"的描述与 312k 行的规模高度吻合**。

其实测对比（分析 50 条反馈，100 次测试）与选型矩阵：

| 框架 | 总 token | 时间 | 失败率 | Observability | 初始投入 |
|---|---|---|---|---|---|
| AutoGen 0.x 默认 | ~85,000 | 4.2 min | 12% | ★★★ | 2-5 天 |
| **LangGraph** | **~18,000** | 1.6 min | **1%** | ★★★★★ | **10-14 天** |
| 自研调度器 | ~16,000 | 1.8 min | 2% | ★★（需自建） | **30+ 天** |

**⚠️ 这是反方证据**：**LangGraph 的 token 效率接近自研**（18k vs 16k），失败率最低。机制是 *"节点之间传递的是**状态 delta**，不是完整对话历史... **token 消耗是线性的**"*。

**[另一篇中文生产复盘（技术栈，2026-08-21）](https://jishuzhan.net/article/2090632413641588738)** 的四个事故：

> **事故 1**：两个子 Agent 并发写同一个 State 字段，后写的把先写的覆盖了，用户问「我的订单到哪了」，系统回答「您的退换货申请已提交」。
> **事故 2**：一个 Agent 在工具调用循环里出不来，连续调了 **14 次**天气查询 API，直到 token 配额耗尽才被强制终止。
> **事故 3**：线上反馈某个意图识别经常出错，但我们翻遍日志也拼不出完整的决策链路——只知道输入和输出，中间经过了哪些节点、哪一步判断错了，**完全黑盒**。
> **事故 4**：Supervisor 把订单查询任务分发给子 Agent 时，子 Agent 竟然**读到了另一个用户的会话历史**。

两个容易漏的技术细节：

- **未声明字段被静默接受**："`intent_recognition` 返回了一个 `intent` 字段，但 `AgentState` 里根本没有定义它。LangGraph 默认会**静默接受**这个未声明字段——这在 Demo 里无所谓，但在生产环境就是**定时炸弹**。"
  **→ 这正是 §13 M5 建议 `extra="forbid"` 的原因。**
- **`recursion_limit` 的真实语义**："这个参数是「**全局步数上限**」，不是「循环次数上限」，而且它抛出的异常是 `GraphRecursionError`，如果你没捕获，用户看到的就是 **500 错误**。"默认值 **25**。

其结论：

> "LangGraph 的图编排抽象是当前 Agent 框架里最接近「可控」的范式，但它的**工程化成熟度远未达到「拿来即用」的水平**。"
> "**框架本身不提供「开箱即用的生产级部署方案」**，这需要你自己补齐。**Checkpoint + 无状态化 + 健康检查**三件套是底线，缺一个都会在生产环境出问题。"

### 11.3 状态膨胀——**官方承认的设计缺陷**（数据见 §3.4）

**官方硬容量边界**（[支持文档](https://support.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl)）：**MongoDB 单文档 16 MB**；**PostgreSQL 单字段 1 GB**。MongoDB 报 `DocumentTooLarge` 时官方建议"切 Postgres 或减小 checkpoint 体积；**避免在 subgraph 里用 checkpointer**"。

**⚠️ 生产连接坑（官方点名）**：直接用 `PostgresSaver`（非 LangGraph Platform 部署）时，**默认整段 run 期间持有同一个数据库连接**，"对长跑工作流会导致**连接超时**"，必须改用 `ConnectionPool`（官方示例 `max_size=10, max_idle=300.0`）。

**⚠️ 一条安全配置（并行研究任务核实）**：`langgraph-checkpoint-sqlite` / `-postgres` 的 README 均给出 **`LANGGRAPH_STRICT_MSGPACK=true`** —— *"This restricts checkpoint deserialization to known-safe types, **preventing code execution if the database is compromised**."* **本地应用也应开启**。

### 11.4 ★ 沉默的数据损坏——**最危险的一类 bug**

**全部 open、无异常抛出、返回错误答案而非报错。对长跑小说系统是致命的。**

| Issue | 症状 |
|---|---|
| [langgraph#8314](https://github.com/langchain-ai/langgraph/issues/8314) | **状态通道端到端引用透传**。`LastValue` / `BinaryOperatorAggregate` / `AnyValue` / `EphemeralValue` 的 `copy()` 是 `empty.value = self.value`（**引用赋值**）。router 或**普通节点**原地修改 state 会污染**调用方传入的原始对象**。复现输出：`original list after invoke: ['x','y','MUTATED_BY_ROUTER']` 且 `id(original) == id(result['items'])` 为 `True` |
| [langgraph#8115](https://github.com/langchain-ai/langgraph/issues/8115) | `PregelLoop.put_writes()` 竞态：`self.checkpoint_pending_writes = [w for w in ...]` + `.extend(...)` **非原子** → **并行执行时 checkpoint 静默丢数据**。**30/30 次确定性复现** |
| [langgraph#8753](https://github.com/langchain-ai/langgraph/issues/8753) | 缓存命名空间塌缩为字面量 `"__dynamic__"`（`identifier()` 对 callable object / `functools.partial` / lambda 返回 `None`）→ **两个不同图共享缓存时，第二个图直接返回第一个图的结果，自己的节点从不执行** |
| [langgraph#8826](https://github.com/langchain-ai/langgraph/issues/8826) | `datetime` 经 checkpoint round-trip **静默丢失 `ZoneInfo`**（退化为固定偏移），DST 跨界算术差 1 小时，`fold` 也丢失。"**silently off by an hour**... with no error anywhere" |
| [langgraph#8447](https://github.com/langchain-ai/langgraph/issues/8447) | `DeltaChannel` + PostgresSaver：分页快路径中 `parent_of.get(target_id)` 返回 `None` 被当成"根节点" → **超过 1024 个 checkpoint 的老线程，`get_state` 返回 `messages` 为空列表**。"**silent, incorrect state with no error raised**" |
| [langgraph#8704](https://github.com/langchain-ai/langgraph/issues/8704) | 事件流 `values` payload **丢弃 `additional_kwargs`**，而 `/state` 端点返回完整 → "same message has **two different shapes** depending on which endpoint you ask" |

**#8314 作者点出的结构性问题**：

> "These channels are **reference-transparent end to end, on both read and write**... a naive fix (deep-copy in `copy()`) doesn't actually resolve the underlying issue, and has real costs of its own: it's **too slow to run on every read for real-sized state**, and fails outright (`TypeError: cannot pickle ...`) on any state holding something not deep-copyable — **a DB session, an LLM client, a lock**."
> "#7993 and #8180... only patch `topic.py` and `named_barrier_value.py`. Neither touches `last_value.py`, `binop.py`, `any_value.py`, or `ephemeral_value.py`... If either PR is merged as-is, the #7992 bug class **remains fully exploitable via `LastValue`** — **the default channel for any un-annotated state field**."

**→ 不要依赖框架帮你做状态隔离。用不可变数据结构 + 显式返回新对象。**（§13 M3）

**⚠️ `#8826` 对本项目的具体风险**：小说系统必然有大量时间/日期（章节内的故事时间、事件时间戳）。**checkpoint 往返后时区静默丢失**意味着"第 30 章发生在冬季"这类判断在 resume 后可能出错。**建议：state 里的时间用 `int`（章节序号）或 ISO 字符串，不用带时区的 `datetime` 对象。**

### 11.5 性能、并发与 async 陷阱

| Issue | 发现 | 数据 |
|---|---|---|
| [langgraph#8859](https://github.com/langchain-ai/langgraph/issues/8859) | `FuturesDict.on_done` 每次完成都重扫累积集合 → **O(tasks²)** | 2,400 tasks：**4,153 ms → 3.39 ms（1225×）**；150 并行分支 2.212s → 1.260s |
| [langgraph#7417](https://github.com/langchain-ai/langgraph/issues/7417) | **>180s 的工具调用在 LangGraph Cloud 被 checkpoint 静默重放**，产生 **2-3× 冗余工作与成本**。`BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS=900` / `BG_JOB_ISOLATED_LOOPS=true` 均无效，`BG_JOB_HEARTBEAT=120` 硬编码不可配 | open，**53 评论**，2026-09-12 仍活跃；1.1.3–1.1.6 均复现 |
| [langgraph#8896](https://github.com/langchain-ai/langgraph/issues/8896) | `SyncStreamController._reconnect_shared_stream` **同一 class body 定义两次**，第二次覆盖第一次 → 加固 PR #7829 的锁**从未生效**，shutdown 时连接泄漏 | open |
| [langgraph#8861](https://github.com/langchain-ai/langgraph/issues/8861) | 模块级全局 `SKIP_RERAISE_SET`（`weakref.WeakSet`）跨 tick 泄漏 → **order-dependent 测试 flake** | open |

**⚠️⚠️ `#7417` 对 AI 小说系统是最直接的风险**：**章节生成极易超过 180s**（几千字 + 思考模型）。后果是 **2-3 倍的重复扣费 + 状态污染**。

**→ 所有节点必须幂等（§13 M2）。** 这是本报告认为**第二重要**的缓解措施（第一是状态外置）。

**async / event loop 事故**：
- **bytedance/deer-flow#1917**：`load_skills()` 在 LangGraph async event loop 里用阻塞 `os.walk` → 在 `langgraph dev` 下破坏 tool calls
- **langgraph#7591**：`ToolNode._arun_one` 在使用同步 `wrap_tool_call` 时阻塞 event loop
- 中文实测："**当工作流出错时，你不能简单 print 每一步输出，因为节点是并发执行的，日志会乱序。**"

**⚠️ 对本项目的直接含义**：现有系统是**线程 + 同步**模型（`pipeline._spawn_once` / `_running_lock` / `threading.Semaphore`）。**迁移到 async 不是免费的**——所有阻塞 I/O（文件读写、SQLite 访问、bge-m3 推理）都必须改成 `asyncio.to_thread` 或异步驱动，否则会重演 deer-flow#1917 的问题。

### 11.6 路由与"god node"：最实用的失败模式

`prohobo`（HN，AI 电商分析 agent 的一手经验）：

> "**I don't use parallel tool calling for most of my agents because it adds too much instability to graph execution.**"
> "Keep the graph small, the prompts concise, the nodes and tools **atomic in function**."
> "I spent weeks trying to fine-tune a prompt... I **re-wrote it like 100 times**, and every time I ran it it would screw up in a new way... **My solution was to decompose the node into 4 separate ones that each handle a single task**"
> "**People complain a lot about LangChain, but the general vibe around LangGraph is that it's a maturely designed framework.**"
> "This, honestly, is **the main problem with modern AI** — the model did exactly what you told it to given what was in context. The problem is that by step 4 **the context has drifted** from what you expected."

**★ 这一条对小说系统极有价值**：**失败往往不是框架 bug，而是 context drift**。

**→ 含义**：**"上帝节点"（一个节点同时做多件事）是最大的反模式**。现有项目 `pipeline.py` 单个文件承载 plan/architect/write/commit 全链路，是典型的**超大节点**结构。**重写时的拆解粒度应当以"单一职责"为准，而不是以"一次函数调用"为准。**

**中文实测的核心经验**：

> "**Planning 粒度通过图节点拆分控制**... **不要让 LLM 自由决定「下一步去哪」。**"
> 三分法："**LLM 必须的** / **可以是普通函数的** / **可以是工具调用的**"——"把第二、三类从 Agent 链路里拿出来，变成普通函数或工具，**既降低错误率，又节省大量 token 成本**"
> "**如果一个工具调用能解决的，就不要用 Agent。**"

**→ 这三条与 §7.4 的选型结论完全一致。**

### 11.7 可观测性：最真实的生产痛点

`redhanuman`（HN 生产事故）：

> "It failed on step 4 of 6 and by the time I opened the logs, the state was gone... **at some point I had more debugging code than actual agent code**"

中文实测：

> "线上反馈某个意图识别经常出错，但我们翻遍日志也拼不出完整的决策链路——只知道输入和输出，**完全黑盒**"（§11.2 事故 3）

**官方先例（值得直接抄）**：Airflow 的 common-ai provider：

> "Every LLM task logs token usage and tool calls to Airflow's metadata DB. **The full conversation history is stored too, so you can audit what the agent did after the fact.**"

**→ 这是"自建 LLM I/O 落库"的官方先例**，也正是 §10.4 接缝 2 的设计依据。

**⚠️ 一条诚实标注**：**未找到"如何专门绕开 LangSmith 锁定的官方文档"** —— LangChain 官方文档通篇以 LangSmith 为默认观测后端。"自建 LLM I/O 落库以避免 LangSmith 锁定"是**推断 + Airflow 先例的类比**，不是被引用的既有实践。

### 11.8 文档质量与生态锁定

**[Ask HN: How's LangGraph for Prod?](https://news.ycombinator.com/item?id=41995104)**：

> `karpatic`: "**The langchain docs are God awful, impossible to navigate with every page half baked.** It was made even worse by their trying to push lg to the point you can barely find their lc docs. And It's like they just **shove jsdoc output into docusaurus pages** for the api and call it a day."

**锁定批评**（HN `laborcontract`，2024-06）：

> "They put everyone who uses it in a position of **massive technical debt**, create more abstractions like langgraph to **lock people into their tools** and then and then create **paid tools on top of it to solve the problems that they created (langsmith)**."

**LangSmith 耦合的实证**（HN，Langfuse launch 帖）：

> "Langsmith appeared popular, but we had encountered challenges with Langchain from the same company, finding it **overly complex**... We **rewrote our systems to remove dependencies on Langchain** and chose not to proceed with Langsmith as it seemed **strongly coupled** with Langchain."

**Minimaxir 的对应论述**：

> "The real issue with all these integrations is that it creates an **inherent lock-in** to only use LangChain-based code, and if you look at the code for the integrations **they are not very robust**."

### 11.9 "图抽象本身是错的"——竞品创始人的技术批评

HN `skull8888888`（Laminar 创始人）：

> "what I meant when I was saying that **graph is a wrong abstraction**, is that predefined edges is not the best of writing code when dealing with dynamic systems such as AI Agents... **how would you define cycles or spawning of the same task multiple times as a node-edge relation?** It becomes extremely inconvenient and just gets in your way... it['s] impossible to do MapReduce like operations on node-edge **without inventing some special node**."

**⚠️ 注意他说的"some special node"就是 `Send`** —— 这实际上是对 LangGraph 的评价，但**对本项目而言，`Send` 恰好是需要的**（整卷并行生成章节）。**所以这条批评对本项目的适用性有限**，但它准确描述了一个事实：**当你的流程真的是动态的时候，图抽象会变成负担**。

**反方**（HN `ossa-ma`）：

> "LangGraph is for multi-agent orchestration as state graphs. This isn't useful for Claude Code as there is no multi-agent chaining. It uses a single coordinator agent that spawns subagents on demand. **Basically too dynamic to constrain to state graphs.**"

**→ 对本项目的判断**：**小说生成流程是"大部分确定 + 少量动态"，属于图抽象的最佳适用区间**（§7.4）。

### 11.10 中文社区的特有批评

**[CSDN 自研轻量级编排引擎](https://adg.csdn.net/6a5a1f10662f9a54cb905db5.html)**（每日 10 万+ 次调用生产验证）：

> "通用 Agent 框架在 **Demo 阶段表现优秀，但在未经二次工程封装的前提下，直接用于企业级复杂业务，存在明显挑战**。"

三个具体挑战：

1. **多工具协同不可控**："LangGraph 提供了灵活的图结构，但**原生框架对工具调用的超时、熔断、降级缺乏工程化兜底机制**。"并举例："某金融科技团队曾尝试直接使用开源框架开发多智能体风控系统，因消息队列设计缺陷导致状态不同步，最终**项目重构成本超预算 200%**。"
   **⚠️ 部分过时**：`langgraph>=1.2` 已经补齐了 **timeout / retry / error_handler**（§3.9），这条批评在 2026-09 已大幅缓解。
2. **高并发状态追踪**："`StateGraph` 采用**全局共享状态设计**，这虽然简化了开发，但在高并发场景下，**状态隔离和追踪变得异常困难**。**Thoughtworks 的技术雷达也指出**，这种「全局共享状态」的架构在某些场景下并非最优解。"
   **⚠️ 未验证**：**Thoughtworks Technology Radar 对 LangGraph 的正式评级未能取到**（3 次抓取均被导航栏淹没）。**这条转述属二手，不要引用。**
3. **异常处理依赖 Prompt 约定**："原生 LangGraph 中，节点的异常处理主要依赖 `try/except` 或 `retry_policy`，但**缺乏类似 SAGA 模式的补偿事务机制**。"
   **⚠️ 部分过时**：`langgraph>=1.2` 的 `error_handler` 已经支持 `Command(goto=...)` 补偿路由（§3.9），**且失败来源会被 checkpoint，resume 后 handler 看到同一个 `NodeError`**。官方文档甚至直接用了 "**Saga / compensation patterns**" 这个词。

其结论：

> "**Agent 的稳定性不取决于 Prompt，而取决于工程约束。**"
> "**不要让 LLM 自由决定「下一步去哪」。**"
> "多副本部署必须使用 Redis/PostgreSQL 作为 checkpoint 存储，否则用户会话无法跨 Pod 恢复。"

**⚠️ 一份重要的时效性提醒**：这三条中文批评**成文时间早于 langgraph 1.2（2026-05-12）**。第 1、3 条已被 1.2 的部分能力缓解。**引用时须注明时间。**

### 11.11 ★ 反方观点（必须平衡呈现）

**LangGraph 有真实的、不可替代的价值，且这些价值恰好匹配长跑 AI 小说生成的需求。**

**(1) 有真实生产用户在跑。** GitHub README 称 Klarna、Replit、Elastic 在用。LangChain 1.0 GA 时引用 90M 月下载量，客户含 Rippling、Uber、LinkedIn、Klarna、JP Morgan、Blackrock、Cisco。

**(2) 最有力的独立证据：GitLab 的正式架构决策。**
[GitLab ADR](https://gitlab.com/gitlab-org/gitlab/-/work_items/596718)：

> "**LangGraph is currently the only framework that satisfies all four hard requirements simultaneously**"

| 需求 | LangGraph | Temporal | Prefect | Claude Agent SDK | Haystack |
|---|---|---|---|---|---|
| Graph-based conditional routing | ✓ | △ (code-only) | ✓ | △ (sessions) | △ (pipeline) |
| Custom checkpoint backends | ✓ | N/A | N/A | △ | △ |
| **Native LLM token streaming** | ✓ | **✗** | **✗** | ✓ | ✓ |
| Human-in-the-loop interrupts | ✓ | ✓ | ✓ | △ | △ |
| SM data residency compatible | ✓ | ✗ | ✗ | △ | △ |

> "**Temporal**: Best durability model (event sourcing / replay), but has **no native streaming primitive**... Replicating this with Temporal requires a **side-channel (Kafka, Redis Streams) — significant extra infrastructure**."
> "The `GitLabWorkflow` checkpointer is **~930 lines of code** with deep integration into Rails APIs, billing events, internal tracking, and state machines. **LangGraph's `BaseCheckpointSaver` interface is what makes this possible.**"

结论：*"Keep LangGraph. Invest in improving how the checkpoint interface is used (incremental writes, async writes, tracing decoupling) rather than migrating the framework."*

**⚠️ 对本项目的直接含义**：**"原生 token streaming" 对小说系统同样是硬需求**（用户要看章节逐字生成）。**这一条是 LangGraph 相对 Temporal 的决定性优势。**

**(3) 图抽象对很多人是有效的心智模型。**

HN `four_fifths`: *"fully agree that **LangChain is a meaningless abstraction** but I've found that **the graph abstraction that LangGraph uses is a very useful mental model**"*

HN `sbrother`: *"IMO it is **a lot better than langchain; its abstractions are lower level and more appropriate**... That being said, **it's not doing that much.**"*

HN `TimPC` 给出了最实用的辩护 —— **节点可以是任意状态操作函数，不只是 agent**：

> "A node **doesn't have to be an agent** it can be any State manipulation function. So you can create nodes that exclusively do preprocessing or postprocessing and get your run loop as close as possible to just ainvoke on your graph repeatedly."
> "I do highly recommend **abstracting your graph into Node and Edge classes**... Getting as much code reuse as possible **dramatically simplifies debugging graph issues**."

HN `wfhpw` 澄清了一个常见混淆：

> "Almost none of those things are part of the **LangGraph** framework? LangGraph does the scheduling, checkpointing, state management, etc. All of those things are **LangChain abstractions** that you can use when building a graph."

**→ 这条很重要**：**很多对"LangChain"的批评不适用于 LangGraph。** 两者可以分开评估。

**(4) 真实生产成功案例（含具体收益）。**

中文实测：*"一个分析任务跑到第 8 个 Agent 时 LLM 超时，AutoGen 时代需要从头重跑，LangGraph 时代**直接从第 8 个 checkpoint 恢复，节省了 80% 的重试成本**。"*

`7777777phil`（研究自动化）：*"**We always put a human in the loop checkpoint after each critical step** — might be annoying now but I think it will save us long-term."*

**(5) 官方对"我该不该用"的诚实界定。**
[LangChain alternatives 页](https://www.langchain.com/resources/langchain-alternatives)：

> "And if your agent is **a simple prompt wrapper with no tool use or memory, none of us are the right fit. A basic logging library is a stronger match for the job.**"

**这条比任何批评都更值得引用：厂商自己说了什么时候不该用它。**

**(6) 适用性判据。[kalviumlabs](https://www.kalviumlabs.ai/blog/langgraph-for-founders-agent-framework-pays-back/)** 基于 12 个 agentic 项目：

> "LangGraph pays back when **at least one of four conditions** is true: **multi-session state, branching with more than 3 routes, human-in-the-loop approvals, or multi-agent handoffs.**"

**成本量化**：

> "**State schema design: 3-5 days**... The bug this creates (a **parallel branch silently overwriting another branch's output**) doesn't appear until you run a multi-branch flow in production."
> "**Team learning curve: 1-2 weeks**... the first LangGraph agent from any team costs **30-50% more in developer time** than an equivalent state machine built without a framework."
> "**Graph topology changes: harder than they sound.** ... On a plain Python agent, this is a function and an if statement."
> "**On a 4-week prototype, this front-loaded cost is real money. On an 8-week production build with defined requirements, it typically pays back from sprint 2 onward.**"

**★ 对小说系统的判断**：**同时命中全部 4 条**（多会话状态 / >3 条分支 / 人工审核 / 多 agent 交接）。

**→ 所以"不用 LangGraph"不是正确答案。正确答案是：用，但严格约束使用方式。**

**★ 而 Anthropic 的排除条款恰好也命中**（§7.1）：写作流水线**所有 agent 都要读同一份设定集，连续性检查依赖前文** —— 这正是"require all agents to share the same context or involve many dependencies between agents"。

**→ 两条证据合起来指向同一个结论**：**用 LangGraph（需要它的 checkpoint/HITL/streaming），但用「确定性 custom workflow」形态而不是「多 agent 自由协作」形态。** 这不是矛盾，而是**用对了工具的那一部分**。

### 11.12 CN 社区的生产验证方案（混合架构）

CSDN 实测的最终方案：

> "**核心生产 pipeline 用 LangGraph，超长异步任务用自研队列**"

**★ 这个混合架构对本项目是重要参考**：现有项目已经有 `job_queue.py`（SQLite 持久化 job）+ `_spawn_once` 线程模型。**合理的迁移路径不是"全部换成 LangGraph"，而是**：

```
LangGraph 负责：编排、断点、HITL、流式、并行 fan-out
自研 job_queue 负责：跨进程的任务调度、超长任务、失败重试的看门狗
自己的 DB 负责：真相（正文、圣经、追溯、成本）
```

---

## 12. 版本风险

### 12.1 官方政策的原文（无歧义）

来源：[Release policy](https://docs.langchain.com/oss/python/release-policy)。

> "**Breaking changes are only introduced in major versions**"，且列举包括 "**Changes to default behavior that affect existing applications**"
> "**Both LangChain and LangGraph 1.0 are designated as LTS releases**"
> "Legacy versions (LangChain 0.3 and LangGraph 0.4) are in MAINTENANCE mode until **December 2026**"
> LangGraph: "We expect to **space out major releases by at least 6-12 months**" / "Minor releases are typically released **every 1-2 months**" / "**Grace Period**: Deprecated features remain functional for **at least one minor version**"
> LangChain: "**patch versions are released frequently, up to a few times per week**" / "deprecated features will continue to work throughout the entire 1.x release series"
> Pre-1.0 例外：`deepagents` "*is a pre-1.0 package*... the API may change between minor versions"

### 12.2 ⚠️ 政策 vs 现实：三个 patch 级破坏的一手反例

| Issue | 内容 | 反证了什么 |
|---|---|---|
| [langchain#34169](https://github.com/langchain-ai/langchain/issues/34169) | **langchain 1.0.5 可用，≥1.0.6 抛 `ValueError`**（`InjectedToolCallId` 调用契约变更，PR #33999）。作者原话：*"**As this was a breaking change, it would be great to keep it in a major version.**"* | "patch 只修 bug" |
| [langgraph#6363](https://github.com/langchain-ai/langgraph/issues/6363) | `langgraph-prebuilt==1.0.2`（2025-10-29）给 `ToolNode.afunc` 加**必填** `runtime` 参数 → `TypeError`。根因是 `langgraph==1.0.1` **未约束** prebuilt 的 patch 上限。*"This caused **production failures** for teams that... Rebuilt their environments after Oct 29, 2025... Found their code **suddenly broken without any changes on their end**."* | **"breaking 只在大版本"** + **"pin langgraph 就够了"** |
| [langgraph#5862](https://github.com/langchain-ai/langgraph/issues/5862) | `langgraph-checkpoint-postgres` **2.0.21 → 2.0.22（同一 patch 号段内）** metadata 序列化从 `JsonPlus` 改为 `JsonB` → `"Object of type HumanMessage is not JSON serializable"` | 同 patch 号内行为破坏 |

**★ 结论：政策文本不模糊，含糊的是执行。** `#6363` 特别重要 —— 它证明**只 pin `langgraph` 是不够的**，因为它对 `langgraph-prebuilt` 的约束是**区间**（`>=1.1.0,<1.2.0`），patch 会在区间内浮动。

**⚠️ 两个数字的引用陷阱**：官方说"minor 每 1-2 个月"，本次实测 `langgraph` 全部 release 的中位间隔是 **6.15 天**。**两者都对** —— 6.15 天是**全部 release**（含 patch）的中位，1-2 个月只是 minor。**单独引用任一个都会误导。**

### 12.3 官方迁移指南：具体坏了什么

**(a) LangGraph v1 —— 官方定性"基本向后兼容"**

> "**LangGraph v1 is largely backwards compatible with previous versions. The main change is the deprecation of `create_react_agent`.**"
> "custom `StateGraph`-based graphs, checkpointers, `interrupt()`, `Command`, `Send`, and `MemorySaver` **all continue to work unchanged**."

**Deprecation 全表（官方逐条）**：

| 被弃用 | 替代 |
|---|---|
| `create_react_agent` | `langchain.agents.create_agent`；**`prompt=` 改名 `system_prompt=`** |
| `AgentState` / `AgentStateWithStructuredResponse` | `langchain.agents.AgentState` |
| `AgentStatePydantic` / `...Pydantic` | 同上（**"no more pydantic state"**） |
| `HumanInterruptConfig` / `ActionRequest` | `...human_in_the_loop.InterruptOnConfig` |
| `HumanInterrupt` | `HITLRequest` |
| `ValidationNode` | 删除（`create_agent` 自动校验） |
| `MessageGraph` | `StateGraph` + `messages` key |

**唯一 Breaking changes 章节只有一条**：`Dropped Python 3.9 support` → 要求 **Python 3.10+**。

**→ 对本项目的好消息**：**`StateGraph` / `interrupt` / `Command` / `Send` / checkpointer 全部向后兼容**。这意味着**如果只用核心 API，v1 迁移成本很低**。

**(b) LangChain v1 —— 破坏面大得多**

- **namespace 收缩**：`langchain.chains` / `.retrievers` / `.indexes` / `hub` / `CacheBackedEmbeddings` / `langchain-community` re-export **全部移出** → 需要 `pip install langchain-classic` + `from langchain_classic.*`。**仅剩 5 个模块**：`agents` / `messages` / `tools` / `chat_models` / `embeddings`。
- **hooks → middleware**：`pre_model_hook=` / `post_model_hook=` / `state_modifier=` 全部替换。
- **state 类型强约束**：*"`create_agent` only supports `TypedDict` for state schemas. **Pydantic models and dataclasses are no longer supported.**"*
- **pre-bound model 不支持**：`create_agent(model.bind_tools([...]))` 报错。
- **`ToolNode` 不再被接受**：`tools=ToolNode([...])` 必须改 `tools=[...]`。
- **structured output 重写**：prompted output 形态 `response_format=("please generate ...", Schema)` **删除**。
- **流式节点改名 `"agent"` → `"model"`** —— ⚠️ **会静默破坏按 node name 过滤 stream 事件的代码**（无异常、无告警）。
- **`config["configurable"]` → `context=`**（旧的仍兼容但不推荐）。
- chat model 返回签名 `BaseMessage` → `AIMessage`；`.text()` → `.text`（**v2 移除**）；`AIMessage.example` 删除；`langchain-anthropic` 的 `max_tokens` 默认值不再是 1024。
- *"Methods, functions, and other objects that were already deprecated and slated for removal in 1.0 **have been deleted**."*

**(c) 影响 2026-09 新项目的额外发现**

1. **`langchain 1.4.0`（2026-09-03）又强制一次迁移**：`langchain.mcp` namespace 内置（**beta**），**取代 `langchain-mcp-adapters`**。`MultiServerMCPClient` → `MCPAdapter`（async context manager）；`convert_mcp_tool_to_langchain_tool` → `as_langchain_tool`（**且变异步**）；MCP prompts/resources **无等价 API**；sampling/roots 直接 `NotImplementedError`。
2. **`langgraph-supervisor` 已停止维护**：*"The `langgraph-supervisor` package is **no longer actively maintained**."* 官方要求迁移到 subagents-as-tools 模式。
3. **`DeltaChannel` 不可回滚**（最高风险）：*"**Rolling back to a version without `DeltaChannel` support is not supported.**"* + *"Keep channel definitions stable for the lifetime of a thread."*
4. **⚠️ `langchain 1.4.0` 依赖 `langgraph>=1.2.11,<1.3.0`** —— 即 **`langchain` 会对 `langgraph` 的 minor 版本施加约束**。**升级 langchain 可能强制升级 langgraph。**

### 12.4 checkpoint serde 层对 Pydantic 的长期不稳定

**跨年度、多 PR 未收敛的信号**：

| Issue | 内容 | 状态 |
|---|---|---|
| [langgraph#6718](https://github.com/langchain-ai/langgraph/issues/6718) | 嵌套 `StrEnum` 经 checkpointer 反序列化后变 `None`，破坏 Pydantic 校验 | **open**，2026-01-24 |
| langgraph#6102 | pydantic v2 泛型模型序列化后**静默退化为 plain dict** | — |
| [langgraph#5225](https://github.com/langchain-ai/langgraph/issues/5225) | Pydantic `Field(default_factory=...)` + `Annotated` reducer **被忽略** | **至少 4 个独立 PR 尝试修复**（#6886、#8361、#8662、#8675），横跨 2026-02 → 2026-08 **仍未见收敛** |

修复 PR 反复提交仍未合并：#6905（2026-02-22）、#6901（open）、#8892（2026-09-11）。

**→ 长跑系统的 state schema 应优先用 `TypedDict` + 基础类型，避免嵌套 Enum、泛型模型、`default_factory` + reducer 组合。**（§13 M5）

### 12.5 生态级 pin 冲突已经发生

[langgraph#8352](https://github.com/langchain-ai/langgraph/issues/8352)（open，2026-07-15）：`langgraph-api` 对 `opentelemetry-exporter-prometheus<0.59` 的 pin，使 `langgraph-api>=0.11.0` 与 `pydantic-ai 2.x / logfire>=4.16` **无法共存**（依赖解析直接失败）。

**→ 含义**：LangGraph 的**部署层包**（`langgraph-api`）会将自己的依赖约束传导到整个环境。**如果只用 OSS 库（不部署 LangGraph Platform），可以规避这类冲突。**

### 12.6 升级节奏建议

| 动作 | 频率 | 依据 |
|---|---|---|
| 依赖安全补丁 | 每月一次，只吃 patch | `langchain-core` 中位 3 天/release |
| 评估 minor | **每季度一次**，不是每月 | `langgraph` 中位 6.15 天；1.1→1.2 恰好 2 个月 |
| 评估 major | 1-2 年一次 | 官方承诺 6-12 个月；1.0 GA (2025-10) 至今未出 2.0 |
| 重跑全量 eval + **checkpoint 兼容性测试** | **每次升级必做** | #5862 证明同 patch 号内可破坏序列化 |

**必须建立的自动化**：

1. `pip-compile` / `uv lock` **全量哈希锁定**。
2. 升级前对**已有 checkpoint 做反序列化回归测试**（历史 thread 能否 `get_state`）—— **这是本项目最容易忽略、后果最严重的一项**：升级后老书无法 resume 意味着用户的书打不开了。
3. 监控 `langgraph_state_size_bytes` P95（阈值参考 LIA-Assistant：告警 1 MB / 目标 500 KB）。
4. 监控 `cache_hit_ratio`（§5.5）。

### 12.7 ⚠️ 未验证项（诚实标注）

1. ❌ **不存在官方兼容矩阵页面**（已搜索确认）。§1.3 的约束是本次从 PyPI `requires_dist` 亲自提取的。
2. ⚠️ **"人们实际用什么约束风格"无可靠数据**。GitHub 在 langgraph 仓库对 `"pinned" in:title` 检索 `total_count=1` 且为无关 docs PR。**此项应视为空白。** 唯一官方谈 pin 的表述在 `langchain-mcp-adapters` 迁移指南：*"Prefer pinning a current stable version when the project already pins versions."* —— **只说"要 pin"，没给区间风格**。
3. ⚠️ **GitHub `total_count` 是关键词命中数**（词法匹配），**不等于真实破坏性变更总数**。应作为**相对 churn 信号**而非绝对计数。

---

## 13. 结论与缓解措施

### 13.1 核心判断（三句话）

1. **LangGraph 是当前最合适的选择** —— 尤其因为 **GitLab 的独立评估证实：它是唯一同时满足「图路由 + 自定义 checkpoint 后端 + 原生 LLM token streaming + HITL 中断」的框架**，而 **token streaming 与 HITL 对小说系统都是硬需求**。
2. **但"LangGraph checkpointer = 持久化执行"是错的。** 它保存**数据**，不保存**执行**。崩溃后的失败检测与重新入图**必须你自己写**。
3. **312k 行自研代码库的教训不是"不要框架"，而是"要控制抽象层的所有权边界"。** 真正的行动项是 **thin adapter + 外部化状态 + 幂等节点 + 自建 LLM I/O 日志** —— 这四条**在 LangGraph 与 Temporal 之间是可移植的**。

### 13.2 分层架构建议（目标形态）

```
┌─────────────────────────────────────────────────────────────┐
│  core/          纯 Python，零框架依赖，可直接单测            │
│    chapter_gen.py    consistency.py    style.py             │
│    kg.py             memory.py         eval_judge.py        │
│  ★ 验收：grep -r "langgraph" core/ → 空                      │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ 只传 dataclass / dict，不传框架对象
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  adapters/langgraph/    编排层（薄）                         │
│    state.py      TypedDict schema（无 Pydantic / 无 Enum）   │
│    graph.py      StateGraph 定义 + 确定性边                  │
│    nodes.py      节点 = 读 state → 调 core → 返回引用        │
│    callbacks.py  NodeUsageTracker（token 归因 + trace 落库）  │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  自己的 DB（真相来源）                                        │
│    novel.db       章节索引、故事圣经、kg_entity/kg_relation   │
│    llm_call       每次 LLM 调用一行（§10.4 接缝 2）★         │
│    eval.db        评测数据集与结果（§10.4 接缝 3）            │
│    checkpoints.sqlite  ← LangGraph 的，但视为「可丢弃的加速器」│
└─────────────────────────────────────────────────────────────┘
                            ▲
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  job_queue（跨进程看门狗，保留现有设计）                      │
│    检测「running 但进程已死」→ 用同 thread_id 重新 invoke     │
└─────────────────────────────────────────────────────────────┘
```

### 13.3 缓解措施清单（按优先级）

#### P0 —— 必须做，否则会出生产事故

| # | 措施 | 依据 |
|---|---|---|
| **M1** | **状态外部化：state 只放 id/元数据，正文/大纲/圣经全文一律外置** | LangChain 官方 O(N²)（200 轮 = 5.3 GB）；pizza-bot#58（40 KB → 238 MB）；LIA runbook（P95 > 1 MB 告警） |
| **M2** | **所有节点必须幂等**（幂等键 / upsert / 读后写检查） | langgraph#7417（**>180s 调用被静默重放，2-3× 冗余成本，53 评论仍 open**）；§2.9 官方明确要求 |
| **M3** | **不原地修改 state；返回新对象** | langgraph#8314（`LastValue` 等通道**端到端引用透传**，污染调用方输入） |
| **M4** | **限制并行写同一字段**（字段所有权 + 显式 reducer + 必要时关并行） | langgraph#8115（并行写入竞态 → **静默丢 checkpoint**，30/30 复现）；官方 subgraph namespace 冲突警告 |
| **M5** | **state schema 用 `TypedDict` + 基础类型**；避免嵌套 Enum / 泛型 Pydantic / `default_factory` + reducer | #6718 / #6102 / #5225（**4 个 PR 跨 6 个月未收敛**）；§2.2 官方"Pydantic 更慢" |

**★ M1 是最高优先级**：它在 **LangGraph / Temporal / DBOS 上完全同构**，做完之后切换引擎的成本从"重写"降为"换 adapter"。

#### P1 —— 强烈建议

| # | 措施 | 依据 |
|---|---|---|
| **M6** | **分级 durability**：昂贵 LLM 节点用 `sync`，廉价批处理节点可用 `exit` | §3.3 官方语义；`exit` 无法从进程崩溃恢复 |
| **M7** | **自建 LLM I/O 日志**（§10.4 接缝 2 的表结构） | HN `redhanuman`（"more debugging code than actual agent code"）；中文实测（"完全黑盒"）；**Airflow 官方先例** |
| **M8** | **Thin adapter**：`core/` 零框架依赖 | beri.net（"abstraction complaint is a complaint about **ownership**"）；Manetu 案例（`agent.py` 零 Temporal import） |
| **M9** | **子图用于状态隔离，不用于组织代码**；嵌套 ≤2 层；每个 subagent 包唯一 node 名 | §2.8 官方四条风险；多 agent 错误率复合（0.9⁵ = 59%） |
| **M10** | **确定性边优先于 LLM 路由** | 中文实测（"不要让 LLM 自由决定下一步去哪"）；`prohobo` 的 100 次重写教训；§7.4 |

#### P1.5 —— 成本与缓存（对小说系统是数量级的钱）

| # | 措施 | 依据 |
|---|---|---|
| **M11** | **prompt 前缀结构化**：稳定区在前逐字固定，变化区在最后 | §5.1 DeepSeek 缓存单元机制；**命中价是 miss 价的 1/30–1/50** |
| **M12** | **落库 `cache_hit_tokens` / `cache_miss_tokens` / `prompt_prefix_hash`** | §5.5；**没有这个数就无法判断 prompt 改动是否变贵了** |
| **M13** | **批量生成排在低谷时段** | §5.3 DeepSeek 低谷价 = 峰值价一半 |
| **M14** | **故事圣经版本化**（一卷内冻结 `bible_version`） | §7.5；否则圣经修改会破坏前缀、缓存全废 |

#### P2 —— 运维与长期治理

| # | 措施 | 依据 |
|---|---|---|
| **M15** | **checkpoint 保留策略 + VACUUM**（SQLite）/ 定期 delete（Postgres） | pizza-bot#58（262 MB 死空间）；deer-flow#4189（无内置 retention API）；官方 TTL 陷阱 |
| **M16** | **用 `ConnectionPool` 而非直接 `PostgresSaver`** | 官方点名：长跑工作流会**连接超时** |
| **M17** | **节点超时（`TimeoutPolicy`）+ 循环硬上限（`recursion_limit` 默认 25）+ 逃生舱节点** | 中文实测（14 次 API 调用耗尽配额；未捕获 `GraphRecursionError` → 500） |
| **M18** | **`set_node_defaults` 统一配置 retry / timeout / error_handler** | §3.9 官方能力，避免每个 `add_node` 重复 |
| **M19** | **失败来源会被 checkpoint**：resume 后 handler 看到同一 `NodeError` → 可用于断点续跑的补偿 | §3.9 官方明确 |
| **M20** | **`RunControl` 优雅停机**替代手工取消 | §3.10；保证 checkpoint 可恢复，不留半写状态 |
| **M21** | **升级前对已有 checkpoint 做反序列化回归测试** | #5862（同 patch 号内破坏序列化）→ 升级可能导致**用户的书打不开** |
| **M22** | **监控 `state_size_bytes` P95 + `cache_hit_ratio`** | LIA runbook（告警 1 MB / 目标 500 KB）；§5.5 |

#### P3 —— 架构决策（有不可逆性）

| # | 决策 | 依据 |
|---|---|---|
| **M23** | **`DeltaChannel` 是否启用必须在首次上线前决定** | §3.4 官方："**Rolling back... is not supported**" |
| **M24** | **不用 `langchain-classic` / `langchain-community` / `langgraph-supervisor`** | classic 半冻结（最后发版 2026-06-10）；supervisor **已停止维护** |
| **M25** | **不引入 Neo4j**；用 SQLite 邻接 + 时序字段（§9.3） | §9.2 五条理由；保持"默认零依赖"原则 |
| **M26** | **不依赖 `langgraph.store`**；用 `context_schema` 注入自研检索层 | §8.6；现有 RAG 层已存在；Store 的自定义实现**无 conformance 套件** |
| **M27** | **`kg_knowledge` 表（谁在第几章知道什么）** | §9.3；长篇小说最核心的正确性问题 |

### 13.4 两条硬性红线

> **红线 ①：绝不让小说正文进 checkpoint / workflow state。**
> LangGraph 侧会撞 16 MB–1 GB 字段上限与存储 O(N²)；Temporal 侧会撞 2 MB payload 上限。

> **红线 ②：绝不用 LLM 做关键路由决策而不加确定性兜底。**
> 小说生成阶段顺序是已知的；LLM 路由会破坏 prompt 缓存、复合错误率、并使成本不可预测。

### 13.5 反直觉的最终建议：**先修缓解层，再评估引擎**

来自 [beri.net 的分析](https://www.beri.net/article/langchain-alternatives-durable-execution-migration-2026)（2026-08-16）：

> "**Before you rewrite anything, answer one question: when your agent dies at step 6, does it resume at step 6 or at step 1?** If the answer is step 1, **you do not have a LangChain problem. You have a durable-state problem**, and every framework on your shortlist will fail at it the same way — because **none of them is a durable execution engine**. Teams rewrite, ship, get paged at 3am for the identical failure, and conclude that frameworks are cursed."
> "**The abstraction complaint is a complaint about ownership, and swapping vendors does not change who owns the loop.**"

**其决策四问（可直接用于本项目的技术评审）**：

1. 中断的运行是**从断点续还是从头**？（否则先加看门狗，**本周**）
2. 你有能在迁移中**抓到回归的 eval 套件**吗？（**本月**；没有就无法验证迁移）
3. 未来 12 个月用**几个 provider**？（1 个 → 可考虑无框架；3 个以上 → 保留薄抽象）
4. **凌晨 3 点谁 on-call**？（写它的人 → 自己拥有循环；继承的轮值 → 买框架 + 支持）

**对本项目的答案**：①需要看门狗（M1–M5 + job_queue）；②需要建 eval（§10.4 接缝 3）；③现有系统已接多个 provider（`llm_providers.json`）→ **保留薄抽象**；④单人项目 → **自己拥有循环，但也自己承担调试成本** → **M7 自建 trace 是必需品，不是可选项**。

### 13.6 决策清单（一页速查）

| 决策点 | 结论 | 章节 |
|---|---|---|
| 用不用 LangGraph | **用**（4 条适用判据全中 + 原生 streaming 是硬需求） | §7.4 / §11.11 |
| 版本 | **langgraph 1.2.11 / langchain 1.4.0 / core 1.6.3 / checkpoint-sqlite 3.1.1** | §1.6 |
| **DeepSeek 模型名** | **`deepseek-flash` / `deepseek-v4-pro`**（`deepseek-chat`/`deepseek-reasoner` **已停服**）；**必须用 `langchain-deepseek`** | §4.4 / §6.1 |
| state schema | **TypedDict + 基础类型** | §2.2 / §12.4 |
| 编排模式 | **确定性状态机 + 少量 LLM 判定**（不是 supervisor 自由路由） | §7.4 |
| 并行 | **`Send` fan-out，payload 只放 id** | §2.7 |
| 持久化 | **SqliteSaver 起步，抽象隔离；分级 durability** | §3.2 / §3.3 |
| DeltaChannel | **第一期不启用**（但 state 设计已预留） | §3.4 / M23 |
| 结构化输出 | **`json_schema` > `function_calling` > `json_mode`；`handle_errors` 处理校验失败** | §4 |
| Prompt 缓存 | **稳定区在前逐字固定；`tools` 字段顺序稳定；落库 hit/miss + prefix_hash** | §5 |
| **缓存杀手** | **`SummarizationMiddleware` 与 `@dynamic_prompt` 会破坏前缀 —— 必须把动态部分放末尾** | §5.7 |
| **Anthropic 最小 token** | **换模型必须查表**（Opus 4.6/4.5 与 Haiku 4.5 是 4096，极易不达标） | §5.6 |
| Token 计量 | **首选 `stream_mode=["updates"]`（节点名 = dict key）**；`UsageMetadataCallbackHandler` **不按节点归因** | §6.3b |
| 长期记忆 | **后台写入（章提交后），不走热路径** | §8.4 |
| Store | **不用**；用 `context_schema` 注入自研检索（`SqliteStore` 存在但官方不推荐生产） | §8.6 / M26 |
| 知识图谱 | **SQLite 邻接 + `kg_knowledge` 时序表；不引入 Neo4j**（**Kùzu 已归档**） | §9.3 / M25 |
| 评测 | **自建 eval 表 + judge 封装；LangSmith 可选接** | §10.4 |
| Trace | **自建 `llm_call` 表（接缝 2）+ 走 OTel 保留 fanout 能力** | §10.4 |
| 文件规模目标 | **`core/` 零框架依赖；JSON 修复层 ≤200 行** | §13.2 |

---

## 14. 来源

### 14.1 官方文档（LangChain / LangGraph）

1. https://docs.langchain.com/oss/python/langgraph/graph-api — StateGraph、State、Reducer、Send、Command、Recursion limit、Graph migrations
2. https://docs.langchain.com/oss/python/langgraph/use-graph-api
3. https://docs.langchain.com/oss/python/langgraph/persistence — Checkpointer vs Store、排错清单（TTL / 连接 / 子图）
4. https://docs.langchain.com/oss/python/langgraph/checkpointers — 三件套定位、durability 模式、serde、自定义 checkpointer、Pending writes
5. https://docs.langchain.com/oss/python/langgraph/fault-tolerance — RetryPolicy / TimeoutPolicy / error_handler / set_node_defaults / Graceful shutdown
6. https://docs.langchain.com/oss/python/langgraph/backward-compatibility — 技术/业务兼容、行为版本、非确定性
7. https://docs.langchain.com/oss/python/langgraph/interrupts — interrupt 规则、多中断、验证循环
8. https://docs.langchain.com/oss/python/langgraph/use-time-travel — replay / fork / as_node / 子图粒度
9. https://docs.langchain.com/oss/python/langgraph/streaming — stream_mode、v2 StreamPart
10. https://docs.langchain.com/oss/python/langgraph/event-streaming — v3 投影、`langgraph_node`、ProtocolEvent
11. https://docs.langchain.com/oss/python/langgraph/stores — Store API、namespace、语义检索、自定义 Store
12. https://docs.langchain.com/oss/python/langgraph/use-subgraphs — 三档 persistence、namespace 冲突
13. https://docs.langchain.com/oss/python/langgraph/workflows-agents — 五种 workflow 模式
14. https://docs.langchain.com/oss/python/langgraph/add-memory — checkpointer 生产用法、多后端
15. https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph — state 设计原则、错误分类表
16. https://docs.langchain.com/oss/python/langgraph/choosing-apis — Graph API vs Functional API
17. https://docs.langchain.com/oss/python/langgraph/pregel — Pregel 模型、LastValue/Topic/BinaryOperatorAggregate/**DeltaChannel**
18. https://docs.langchain.com/oss/python/langgraph/overview
19. https://docs.langchain.com/oss/python/langgraph/llms.txt
20. https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel
21. https://reference.langchain.com/python/langgraph/types/ — Send / Command / RetryPolicy / TimeoutPolicy / GraphOutput / StreamPart
22. https://reference.langchain.com/python/langgraph/runtime/RunControl
23. https://reference.langchain.com/python/langgraph/errors/NodeError
24. https://reference.langchain.com/python/langgraph/errors/NodeTimeoutError

### 14.2 官方文档（LangChain）

25. https://docs.langchain.com/oss/python/langchain/structured-output — ProviderStrategy / ToolStrategy / handle_errors
26. https://docs.langchain.com/oss/python/langchain/models — with_structured_output method、参数、model profiles
27. https://docs.langchain.com/oss/python/langchain/messages — `usage_metadata` 形状、content_blocks、streaming chunks
28. https://docs.langchain.com/oss/python/langchain/errors/OUTPUT_PARSING_FAILURE
29. https://docs.langchain.com/oss/python/langchain/multi-agent/index — 六种模式对比与性能数据
30. https://docs.langchain.com/oss/python/langchain/multi-agent/subagents
31. https://docs.langchain.com/oss/python/langchain/multi-agent/router
32. https://docs.langchain.com/oss/python/langchain/long-term-memory — store 接线、namespace
33. https://docs.langchain.com/oss/python/langchain/observability — LANGSMITH_* 环境变量、tracing_context
34. https://docs.langchain.com/oss/python/langchain/test/evals — AgentEvals、trajectory match、LLM-as-judge
35. https://docs.langchain.com/oss/python/concepts/memory — 语义/情景/程序记忆、**热路径 vs 后台写入**、Profile vs Collection
36. https://reference.langchain.com/python/langchain-core/language_models/chat_models/BaseChatModel/with_structured_output
37. https://docs.langchain.com/oss/python/langchain/llms.txt

### 14.3 官方文档（迁移 / 政策 / 评测）

38. https://docs.langchain.com/oss/python/migrate/langgraph-v1 — Deprecation 全表
39. https://docs.langchain.com/oss/python/migrate/langchain-v1 — namespace 收缩、middleware、state 约束
40. https://docs.langchain.com/oss/python/migrate/langgraph-supervisor — **supervisor 停止维护**
41. https://docs.langchain.com/oss/python/migrate/langchain-mcp-adapters
42. https://docs.langchain.com/oss/python/release-policy — 版本政策原文
43. https://docs.langchain.com/oss/python/releases/changelog — 各版本发布日期与变更
44. https://docs.langchain.com/langsmith/evaluation
45. https://docs.langchain.com/langsmith/evaluation-quickstart
46. https://docs.langchain.com/langsmith/platform-setup — **Cloud / BYOC / Self-hosted（Enterprise）**
47. https://support.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl — 16MB/1GB、TTL 陷阱、ConnectionPool、膨胀成因

### 14.4 Provider 官方文档

48. https://api-docs.deepseek.com/guides/kv_cache — **上下文缓存机制、`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`**
49. https://api-docs.deepseek.com/quick_start/pricing — **实测价格（缓存命中/未命中/输出，峰值/低谷）**
50. https://api-docs.deepseek.com/guides/json_mode — **JSON 三条要求 + "可能返回空内容"官方承认**
51. https://api-docs.deepseek.com/updates — **changelog：2024-08-02 "order of magnitude" 原文；`deepseek-chat`/`deepseek-reasoner` 下线时间线**
52. https://api-docs.deepseek.com/news/news250120 — R1 发布价（\$0.14 hit / \$0.55 miss）
53. https://api-docs.deepseek.com/guides/reasoning_model — thinking 模式、`reasoning_effort`、base URL
54. https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html — **Anthropic 缓存官方镜像：逐模型最小 token / 4 断点 / 5m·1h TTL / `tools`→`system`→`messages` 顺序 / 20 block 回溯窗口 / cache hit 不计 rate limit**
55. https://openrouter.ai/docs/features/prompt-caching — Anthropic 倍率 1.25×/2×/0.1×
56. https://openai.com/index/api-prompt-caching/ — **OpenAI 官方公告（2024-10-01）：1024 token / 128 递增 / 50% 折扣原始价目**
57. https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/prompt-caching — **GPT-5.6+ 的 `prompt_cache_key`、`prompt_cache_options`、4 写上限、50 断点读取上限、1.25× 写入、15 req/min 分片建议**
58. https://help.aliyun.com/zh/model-studio/context-cache — **阿里云百炼：显式 vs 隐式对照、125%/10%/20% 计费、工具定义不可独立缓存、并行 tool 合并建议、逐模型折扣表**
59. https://developers.googleblog.com/en/gemini-2-5-models-now-support-implicit-caching/ — Gemini 隐式缓存（⚠️ 仅标题确证）
60. https://docs.temporal.io/cloud/limits — 2 MB payload / 4 MB transaction / 51,200 events
61. https://docs.temporal.io/workflow-execution/limits
62. https://docs.temporal.io/develop/python/integrations/langgraph
63. https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching — **⚠️ 从本机 IP 返回 "App unavailable in region"，未取到正文**（数值改用 AWS Bedrock / OpenRouter 官方镜像）
64. https://platform.openai.com/docs/guides/prompt-caching — **⚠️ HTTP 403，未取到正文**（数值改用官方公告 + Azure 镜像）
65. https://ai.google.dev/gemini-api/docs/caching — **⚠️ 多次抓取失败**（Gemini 结论为博客级证据）

### 14.5 GitHub Issues / PRs（全部通过 REST API 或直接抓取确认）

57. https://github.com/langchain-ai/langgraph/issues/8314 — **状态通道引用透传（静默数据损坏）**
58. https://github.com/langchain-ai/langgraph/issues/8115 — **PregelLoop.put_writes() 竞态（静默丢 checkpoint）**
59. https://github.com/langchain-ai/langgraph/issues/8753 — 缓存命名空间塌缩为 `__dynamic__`
60. https://github.com/langchain-ai/langgraph/issues/8826 — datetime 丢失 ZoneInfo
61. https://github.com/langchain-ai/langgraph/issues/8447 — DeltaChannel Postgres 分页 >1024
62. https://github.com/langchain-ai/langgraph/issues/8704 — 事件流丢弃 additional_kwargs
63. https://github.com/langchain-ai/langgraph/issues/8859 — on_done O(n²)
64. https://github.com/langchain-ai/langgraph/issues/8861 — SKIP_RERAISE_SET 全局状态 flake
65. https://github.com/langchain-ai/langgraph/issues/8896 — 方法重复定义，锁从未生效
66. https://github.com/langchain-ai/langgraph/issues/7417 — **>180s 工具调用静默重放（53 评论，仍 open）**
67. https://github.com/langchain-ai/langgraph/issues/6363 — **langgraph-prebuilt 1.0.2 patch 级破坏**
68. https://github.com/langchain-ai/langgraph/issues/5862 — checkpoint-postgres patch 内破坏
69. https://github.com/langchain-ai/langgraph/issues/6718 — 嵌套 Enum 变 None
70. https://github.com/langchain-ai/langgraph/issues/5225 — default_factory + reducer 被忽略
71. https://github.com/langchain-ai/langgraph/issues/8352 — opentelemetry pin 冲突
72. https://github.com/langchain-ai/langchain/issues/34169 — **langchain 1.0.6 破坏 InjectedToolCallId**
73. https://github.com/langchain-ai/langgraphjs/issues/1138 — Postgres checkpointer 无界增长提问
74. https://github.com/langchain-ai/langgraph/issues/7591 — ToolNode._arun_one 阻塞 event loop
75. https://github.com/pizza-bot-app/pizza-bot/issues/58 — **checkpoints.sqlite 1.24 GB / 32 线程（逐 channel 拆解）**
76. https://github.com/bytedance/deer-flow/issues/4189 — **checkpoint 存储增长（无 retention API）**
77. https://github.com/bytedance/deer-flow/issues/1917 — blocking os.walk in async loop
78. https://github.com/jgouviergmail/LIA-Assistant/blob/main/docs/runbooks/langgraph/state-size-critical.md — **P95 > 1MB 告警阈值**
79. https://github.com/langchain-ai/agentevals
80. https://github.com/langchain-ai/memory-agent
81. https://github.com/langchain-ai/memory-template

### 14.6 Hacker News（通过 Algolia API 实际抓取）

82. https://news.ycombinator.com/item?id=40739982 — "Why we no longer use LangChain"（480 分 / 297 评论）
83. https://hn.algolia.com/api/v1/items/40739982 — 完整评论树（多条引文来源）
84. https://news.ycombinator.com/item?id=41995104 — "Ask HN: How's LangGraph for Prod?"
85. https://hn.algolia.com/api/v1/items/41995104
86. https://news.ycombinator.com/item?id=43468435 — "We chose LangGraph to build our coding agent"（反方证据）
87. https://news.ycombinator.com/item?id=41722243 — "Should I Use a Framework to Build an Agent?"
88. https://news.ycombinator.com/item?id=42441258 — Launch HN: Langfuse（LangSmith 耦合证词）
89. https://news.ycombinator.com/item?id=42521241 — Building AI Products Part I（Airflow 不适配证词）

### 14.7 博客 / 分析与社区

90. https://www.anthropic.com/engineering/building-effective-agents — **"start by using LLM APIs directly"**
91. https://www.anthropic.com/engineering/multi-agent-research-system — **行业成本上界：agents 4× chat tokens、multi-agent 15×；"require all agents to share the same context" 排除条款；"spawning 50 subagents" 失控案例**
92. https://www.octomind.dev/blog/why-we-no-longer-use-langchain-for-building-our-ai-agents — 经典 postmortem
93. https://minimaxir.com/2023/07/langchain-problem/ — Minimaxir 深度案例研究
94. https://www.agentrank.tech/blog/langgraph-honest-take-state-machine-agents — LangGraph 实测 review
95. https://www.langchain.com/blog/delta-channels-evolving-agent-runtime — **官方承认 O(N²)、5.3 GB / 129 MB**
96. https://www.langchain.com/resources/langchain-alternatives — 官方自述边界
97. https://temporal.io/blog/temporal-langgraph-plugin-durable-execution — **"checkpoints are not durable execution"**（⚠️ 竞品立场）
98. https://temporal.io/blog/manetu-the-thread-is-the-workflow — Manetu 一手生产经验
99. https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows — 四条缺口清单（⚠️ 竞品立场）
100. https://gitlab.com/gitlab-org/gitlab/-/work_items/596718 — **GitLab ADR：保留 LangGraph（最严谨的独立对比）**
101. https://www.beri.net/article/langchain-alternatives-durable-execution-migration-2026 — **"先修 durability，再考虑换框架"**
102. https://www.kalviumlabs.ai/blog/langgraph-for-founders-agent-framework-pays-back/ — 12 个项目的适用判据与成本量化
103. https://tinyagents.dev/compare/langgraph — 80 行对照
104. https://mcp.csdn.net/6a3a56e510ee7a33f281455c.html — **中文实测：10-14 工程日 / token 效率对照 / 选型矩阵**
105. https://jishuzhan.net/article/2090632413641588738 — **中文生产复盘：四个事故 + 两个易漏细节**
106. https://adg.csdn.net/6a5a1f10662f9a54cb905db5.html — 中文自研引擎的批评
107. https://hamel.dev/blog/posts/evals/ — 三层评估、precision/recall 警告、judge 校准
108. https://ssimplifi.com/blog/openai-prompt-caching-explained / https://ssimplifi.com/blog/anthropic-prompt-caching-explained — 博客级成本案例（⚠️ 作者自带 VERIFY 标注）
109. https://github.com/hinthornw/trustcall — 官方推荐的记忆更新工具
110. https://arxiv.org/pdf/2309.02427 — CoALA 论文（记忆类型映射）
111. https://arxiv.org/abs/2306.05685 — **LLM-as-judge 的 position / verbosity / self-enhancement bias 原始出处**
112. https://arxiv.org/abs/2307.03172 — **Lost in the Middle：长上下文中间信息召回衰减**
113. https://arxiv.org/abs/2412.15266 — On the Structural Memory of LLM Agents（chunks / triples / atomic facts / summaries 对比）

### 14.7b 本报告新增引用的官方页面

114. https://docs.langchain.com/oss/python/langchain/callbacks — **404**（该页已不存在）
115. https://docs.langchain.com/oss/python/langchain/runtime — `ToolRuntime`、`runtime.execution_info`
116. https://docs.langchain.com/oss/python/langchain/context-engineering — **`@dynamic_prompt` 与 `wrap_model_call` "Append at end" 策略**
117. https://docs.langchain.com/oss/python/langchain/middleware/built-in — `SummarizationMiddleware` 的 trigger/keep
118. https://docs.langchain.com/oss/python/langchain/short-term-memory — **`trim_messages` / `RemoveMessage` / 摘要永久替换历史**
119. https://docs.langchain.com/langsmith/custom-store — **`AsyncSqliteStore` 示例 + "not recommended for production"**
120. https://docs.langchain.com/langsmith/configure-ttl — store / checkpointer TTL 配置
121. https://docs.langchain.com/langsmith/trace-with-opentelemetry — **fanout 配置、`OTEL_EXPORTER_OTLP_ENDPOINT` 404 陷阱、孤儿子 span**
122. https://docs.langchain.com/oss/python/langchain/event-streaming — `message.output` / `message.reasoning` / `message.node` / `on_stream_event`
123. https://reference.langchain.com/python/langchain-core/messages/ai/UsageMetadata
124. https://reference.langchain.com/python/langchain-core/messages/ai/InputTokenDetails
125. https://reference.langchain.com/python/langchain-core/messages/ai/add_usage — **递归相加语义**
126. https://reference.langchain.com/python/langchain-core/callbacks/usage/UsageMetadataCallbackHandler
127. https://reference.langchain.com/python/langchain-core/callbacks/base/BaseCallbackHandler — 全部钩子与 `ignore_*` 开关
128. https://reference.langchain.com/python/langchain-core/prompts/chat/MessagesPlaceholder — `n_messages` / `optional` / 展开语义
129. https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/callbacks/usage.py — **官方源码 124 行（逐行已读）**
130. https://forum.langchain.com/t/token-usage-not-included-in-output-when-stream-mode-includes-messages/1031 — **`stream_mode` 组合导致 usage 丢失的官方论坛复现**
131. https://github.com/langchain-ai/langchain-experimental/issues/87 — **`langchain-experimental` sunset 公告**
132. https://github.com/langchain-ai/langchain-neo4j — **`LLMGraphTransformer` 新家**
133. https://github.com/kuzudb/kuzu — **Kùzu 归档公告**
134. https://langfuse.com/self-hosting — Langfuse 自托管架构
135. https://langfuse.com/integrations/frameworks/langchain — **`LANGCHAIN_CALLBACKS_BACKGROUND` 陷阱**
136. https://docs.langchain.com/langsmith/self-hosted — LangSmith 自托管服务栈

### 14.8 版本数据源

108. https://pypi.org/pypi/langgraph/json
109. https://pypi.org/pypi/langchain/json
110. https://pypi.org/pypi/langchain-core/json
111. https://pypi.org/pypi/langgraph-checkpoint/json
112. https://pypi.org/pypi/langgraph-checkpoint-sqlite/json
113. https://pypi.org/pypi/langgraph-checkpoint-postgres/json
114. https://pypi.org/pypi/langgraph-prebuilt/json
115. https://pypi.org/pypi/langgraph-sdk/json
116. https://pypi.org/pypi/langsmith/json
117. https://pypi.org/pypi/langchain-openai/json
118. https://pypi.org/pypi/langchain-anthropic/json
119. https://pypi.org/pypi/langchain-deepseek/json
120. https://api.github.com/repos/langchain-ai/langgraph/releases

### 14.9 ⚠️ 搜索中出现但未成功抓取 / 未采信

- https://zhuanlan.zhihu.com/p/2047758537632256246（403）
- https://www.thoughtworks.com/radar/languages-and-frameworks/langgraph（正文被导航栏淹没，**评级未取到，不要引用二手转述**）
- https://platform.openai.com/docs/guides/prompt-caching（403）
- https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching（地区限制占位页）
- https://docs.temporal.io/error-handling#blobsizelimiterror（404）
- https://docs.langchain.com/langsmith/ttl（返回 RSC 载荷，未取到正文）

---

## 附录 A · 本地对照项目的事实基线

> 来源：`E:\AI_project\story_llm`（只读参考）。以下为**实测**数据，用于支撑本报告中的对照分析。

| 项 | 实测值 |
|---|---|
| Python 总行数 | **312,566** |
| 递归 `.py` 文件数 | **754** |
| `backend/app` 顶层 `.py` 文件数 | **76** |
| 包管理 | `requirements.txt`（**无 lock 文件**，全部 `>=` 约束） |
| 核心依赖 | fastapi / sqlalchemy / pydantic / httpx / **openai SDK 直连** / numpy / alembic |
| **未使用 LangChain / LangGraph** | ✅ 确认（requirements.txt 无相关包） |
| LLM 层 | `backend/app/llm/client.py`（**74.5 KB 单文件**）+ `llm_providers.py`（26.6 KB） |
| 并发模型 | `threading` + `_running_lock` + `threading.Semaphore`（`_spawn_once` 单一入口） |
| 持久化 | SQLite（`learnstory.db`）+ Alembic 迁移；可选 Postgres/Redis/Milvus（**默认关闭自动降级**） |
| 可观测性 | `observability.py`（Trace/Span/Attempt，**fail-open**）+ `trace_config.py`（**默认 enabled=False**）+ `llm_stats.py`（内存，重启清零） |
| 评测 | `evaluation_store.py`（**独立 `evaluations.db`**，与生产库分离）+ `benchmark/` |
| 本地 embedding | bge-m3（`model/bge-m3`，可选，缺失时降级） |
| JSON 修复设施 | `_salvage_truncated` / `_escape_controls_in_strings` / `_json_err_detail` / `parse_json`（`client.py` 1292–1450 行区间） |
| 推理 token 处理 | `_note_micro_output_discard`（`client.py:82`）；注释记录 **reasoning 占 completion 91%** |
| 配置外置 | 约 25 个 `*_config.json`（风格、大纲、复审、升级、offpeak、追踪等） |

**已经做对、重写时应保留的五件事**：

1. **`observability.py` 的 fail-open 原则**（"deliberately has no dependency on generation correctness"）—— 与 §10.4 接缝 1 完全一致。
2. **`trace_config.py` 的默认关闭 + 只存哈希**（`capture_prompt_hash: True`）—— 与 §10.4 接缝 1 一致。
3. **评测库与生产库分离**（`evaluation_store.py` 的 `benchmark/evaluations.db`）—— 与 §10.4 接缝 3 一致。
4. **"可选基础设施默认关闭、自动降级"**（`requirements.txt` 注释）—— 与 §9.2 反对引入 Neo4j 的理由一致。
5. **`llm_providers.json` 的原子替换写入**（`os.replace`）—— 避免了"写了一半被读到"的窗口。

**重写时必须改掉的五件事**：

1. **312k 行的自研调度器**（§11.2：调试成本 3-5 倍）
2. **`pipeline.py` 单个"上帝文件"** 承载全链路（§11.6：`prohobo` 的 100 次重写教训）
3. **JSON 修复设施**（§4.1：应该用结构化输出接口而不是事后修复）
4. **`requirements.txt` 无 lock**（§1.6：不可复现）
5. **可观测性默认关闭且只存哈希**（§11.7：现有 `enabled: False` 会导致"翻遍日志也拼不出决策链路"；应改为**默认开启自建表**、只对"原始正文"做隐私开关）

---

**报告完**

