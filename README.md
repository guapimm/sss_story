# storyforge

基于 **LangChain + LangGraph** 的长篇小说生成系统。

> **状态**：P0（地基）进行中。计划见 [`doc/`](doc/README.md)。

---

## 这是什么

从 `E:\AI_project\story_llm`（只读参考）重构而来。旧项目约 **9.5 万行业务代码**
（`pipeline.py` 单文件 8,607 行），迭代失控、难以修改。本项目完全重写。

**一期目标**（决策 D-04）：单章闭环端到端可跑，standard 档 20~30 章验证。

---

## 架构

```
L5 接口层    cli / api
L4 编排层    orchestrator（三级流水线协调器，纯 Python）
L3 图与节点  graph/（LangGraph 工作单元 + LLM 节点）
L2 领域层    domain/ scorecard/    ← 纯逻辑，零 LLM 零 SQL 零框架
L1 基础设施  infra/（LLM 客户端 / Prompt / 仓储 / 检索）
L0 存储      SQLite + 文件 + checkpoint DB
```

依赖严格单向。`domain/` 与 `scorecard/` 不得 import 框架或数据库
（由 `tests/contract/` 强制）。

### 三级流水线（决策 D-13）

```
圣经 ─[C-1]→ 骨架批1 ─[C-2]→ 骨架批2 → ...
                │              │
                └→ 细纲批1 ────┴→ 细纲批2 → ...      ← 与骨架并行
                       │
                 第1章细纲就绪 ─[C-3]→ 正文1 → 正文2 → ...  ← 与细纲并行
```

**阶段内严格串行、阶段间并行。** LangGraph 图是「工作单元」，由纯 Python
协调器调度（每单元一个 `thread_id`）。这是必需的 —— 单个大图里 `interrupt()`
会**冻结整个图**，确认首章细纲时骨架/细纲生产也会被冻住，并行就没了。

---

## 快速开始

```bash
uv sync --extra dev

# 架构契约检查（20 条，把旧项目的教训变成机器约束）
uv run pytest tests/contract -v

# 全部测试
uv run pytest

# 自检
uv run storyforge doctor
```

---

## 与旧项目的关键差异

| 方面 | 旧项目 | 本项目 |
|---|---|---|
| 编排 | `pipeline.py` 8,607 行过程式状态机 | 三级流水线 + 协调器 + LangGraph 工作单元 |
| 章号 | `vol*10000+idx` 与卷内号**双表示** → P0 事故「已持续 10011 章」 | **单一递增 `int`**，卷内号派生不存储（D-15） |
| 外键 | **0 个**，靠人工清单清理，漏过 6 张表 | 全部建立并**强制**（`PRAGMA foreign_keys=ON`） |
| 实体 | 势力**不是合法类型** → 降级「其他」→ 势力实体 0 个 | 统一 `EntityKind`，未知类型**抛错**不降级 |
| 关系 | 一张表混装，**86.7% 是非人际关系** | 关系/归属/状态/事件**四分离**；关系类型**枚举化** |
| 伤势 | 靠每章 prompt 催促，**恢复率实测 0%** | `HealthScheduler` **规划**恢复节拍进细纲 |
| 长篇 | 「只细化第 1 卷」→ **400 章挤第 1 卷** | **先规划全部卷的升级台阶**（必填字段 + 硬闸门） |
| 记忆预算 | 恒定 12,000 字 → 密度从 4.8% 衰减到 **0.16%** | **随篇幅增长**（对策⑨） |
| Prompt | 稳定块与易变块交错 → 缓存命中卡在 44% | **三层分离** + CI 断言稳定前缀逐字节相同 |
| 观测 | `finish_reason` **100% 未采集** → 22.2% 空输出无法诊断 | 必须采集；BLANK 状态强制要求诊断字段 |
| 配置 | 26 个 JSON + 81 个 env + 7 个模块各自缓存 | 单一入口 + 运行时注入 |
| 测试 | **121/123** 个文件在模块级改 `os.environ` → 假失败 | 运行时注入；契约测试禁止改环境变量 |

---

## 目录

```
config/             settings.toml / providers.toml / genres/
src/storyforge/
  domain/           实体 / 关系 / 伤势 / 大纲 / 校验器 / 调度器
  scorecard/        确定性质量打分卡（零 LLM）
  infra/            llm / prompts / storage / retrieval
  graph/            state / graphs（工作单元）/ nodes
  modes/            story（一期）+ learn（接口占位）
  observability/    计量 / 成本 / 报告
  orchestrator.py   三级流水线协调器
tests/
  contract/         架构不变量（AST 检查）
  unit/             纯逻辑
  integration/      图级（Mock LLM）
  golden/           质量回归
doc/                计划与调研（约 1 万行）
```

---

## 开发约定

1. **改架构前先读** `doc/00-决策记录.md`（15 条已确认决策）。
2. **新增 prompt 要求必须同时写校验器** —— 否则 `tests/contract` 会失败
   （旧项目 `entry_state` 13% 落地率、`milestone_ids` 0% 的教训）。
3. **不得在测试里改 `os.environ`** —— 用 `config.configure()`。
4. **不得硬编码模型名** —— 走 `config/providers.toml` 能力声明（决策 D-09）。
5. **每个写出的信号必须有读取点** —— 旧项目 `must_review` 25 处写入 0 处读取，
   导致整条文风控制链失效。
