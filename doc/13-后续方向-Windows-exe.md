# 13 · 后续方向：Windows 单文件 exe（重新梳理）

> **性质**：**路线梳理，未实施**。本文汇总已实测的阻塞点、给出分期与判据。
> **上游**：`doc/08`（终态方案）、`doc/10`（PG-0 实测）、`doc/11`（PG-1 计划，决策已冻结）。
> **编制日期**：2026-09-26 · 基线 HEAD `6fdef10`（86 commits）· **1107 passed / 15 skipped**
> **本轮前置**：已修完 6 个真实缺陷（见 §1），S0 真实取证已完成。

---

## 0. 三十秒结论

**目标（双击 exe 即用）技术上可行，且代码基础比预期干净得多。**

| 维度 | 结论 | 证据 |
|---|---|---|
| **PyInstaller 友好度** | ✅ **很好** | 全仓**无** `importlib` 动态导入、**无**插件式模块发现、**无** C 扩展自研；唯一 `__import__` 是 `__import__("json")`（stdlib，PyInstaller 本来就收） |
| **依赖树** | 🟡 中等 | 60 个包，重的是 langchain/langgraph/pydantic/sqlalchemy/openai/tiktoken。**无 numpy/torch** —— 不会出现 GB 级体积 |
| **真实阻塞** | 🔴 **仅 1 类：路径锚点** | 3 处硬编码（详见 §2），**与 `doc/08`~`doc/11` 的结论一致** |
| **GUI** | ⬜ 完全缺失 | `api/` 空文件、0 个前端资产、0 个 HTTP 依赖 |

★ **最重要的一句**：阻塞点**不在打包工具，而在路径**。
打包只是把现有代码装进 exe；而路径不对，装进去也读不到 `config/` 与 prompt。
**故顺序必须是：先修路径（PG-1）→ 再谈打包（PG-2）。**

---

## 1. 本轮已修的前置缺陷（exe 化的隐性前提）

这 6 个缺陷与打包**不直接相关**，但都会在 exe 里**原样存在**，且更难诊断
（用户机器上没有源码可查）：

| # | 缺陷 | 为什么对 exe 尤其危险 |
|---|---|---|
| 1 | 实体/关系入库 FK 根缺失 | 用户机器上**永远没有 `books` 行** → 抽取结果永不入库 |
| 2 | CREDITS 越线丢弃已付费正文 | 用户看到的症状是"花钱没结果"，无法自查 |
| 3 | 超时 < 输出上限（300s vs 需 2392s） | 长章**必然失败**，看起来像"软件坏了" |
| 4 | `settings.toml` 从未被读取 | 用户改配置文件**零效果**，且无从发现 |
| 5 | `llm_calls` 零写入 | 用户无法回答"我花了多少"，客服也无法 |
| 6 | checkpointer 未接线 + resume 语义错 | 崩溃后**重跑整章**（重复付费），恢复能力形同虚设 |

**含义**：这 6 项不修就打包，等于把 6 个"静默失效"封进 exe 分发给用户。

---

## 2. 路径阻塞点（唯一的真阻塞，与 `doc/11` 结论一致）

| # | 位置 | 问题 | 打包后后果 |
|---|---|---|---|
| **B-1** | `infra/prompts/library.py` | `PROMPTS_DIR = REPO_ROOT / "src" / ...` **模块级常量** | 算成 `<exe>/src/...` → `_load()` 抛 `FileNotFoundError` → **写作/规划全链路不可用** |
| **B-2** | `config.py:35` | `REPO_ROOT = Path(__file__).parents[2]` | exe 里 `__file__` 在临时解包目录 → `config`/`data` 双双错位 |
| **B-3** | `config.py` `dotenv_paths()` | 只返回仓库根 `.env` | 用户**无处安放 API key** |
| **B-6** | `pyproject.toml` | `packages = ["src/storyforge"]`，而 `config/` 在仓库根 | `config/` **64 个文件全丢**（PG-0 实测 wheel 里 0 个） |
| **B-7** | `doctor.py:48` | `ensure_dirs()` 有写副作用 | 往**程序安装目录**写 176KB 数据库；装到 `Program Files` 会 `PermissionError` 或静默进 VirtualStore |
| **B-8** | `cmd_flows` | 资源缺失时打印"目录为空"且**退出码 0** | 用户看到空清单，以为"就是没内容" |

**`doc/11` 已给出方案且决策冻结**（`force-include` / 仅安装态切 `%APPDATA%` /
CLI 层报错 / 只用 stdlib）—— 本节**不重复论证**，实施时直接照做。

### 2.1 ★ exe 相对 wheel 的**额外**问题（`doc/11` 未覆盖）

`doc/11` 面向 **wheel 安装**；exe（PyInstaller）多出两个特性：

| 差异 | 影响 | 应对 |
|---|---|---|
| **`sys.frozen`** | PyInstaller 下 `sys.frozen=True`、`sys._MEIPASS` 指向**临时解包目录** | 路径解析必须**新增第 4 个候选**：`sys._MEIPASS`。判据用 `getattr(sys, "frozen", False)` |
| **数据必须写用户目录** | exe 常在只读位置（`Program Files`），且**升级会覆盖**程序目录 | `doc/11` 决策 2 的"仅安装态切 `%APPDATA%`"**已覆盖此点** —— exe 也算安装态 |
| **无 `pytest`/`tests/`** | `arch-check` 依赖 `tests/contract` | 与 `doc/11` 的 B-4 同一处置：降级为可选检查并**明确报"跳过"** |

★ **因此路径解析函数要有 4 级候选**（`doc/11` §3.3 的 3 级 + exe 的第 4 级）：

```
1. STORYFORGE_CONFIG_DIR 环境变量        ← 最高（用户可覆盖）
2. sys._MEIPASS/config                    ← ★ exe 新增（PyInstaller 解包目录）
3. <包内> storyforge/_resources/config    ← wheel 安装态
4. REPO_ROOT/config                       ← 源码树（开发）
```

**判据必须用 `getattr(sys, "frozen", False)`**，不要靠"`sys._MEIPASS` 是否存在"
来判断 —— 后者不是公开 API，且在 `--onedir` 与 `--onefile` 下语义不同
（`--onedir` 时它指向 `exe所在目录/_internal`）。

**已核实（2026-09-26）**：全仓 `sys.frozen` / `_MEIPASS` / `sys.executable`
命中数为 **0**（唯一 `sys.executable` 命中是 `cmd_arch_check` 调 pytest，
属 B-4 范畴）—— 即**完全没有打包态处理**，与 `doc/08` 的结论一致。
⚠️ 检索时注意：`frozen` 在本仓有 25 处 `@dataclass(frozen=True)` 命中，
grep `frozen` 会误报，**必须带 `sys.` 前缀**。

---

## 3. 分期路线（每期可独立验收、独立回滚）

```
PG-1 路径解耦 ──→ PG-2 打包冒烟 ──→ PG-3 HTTP 底座 ──→ PG-4 桌面 GUI
  (wheel 可用)      (exe 能跑)        (D-05 修订)        (全流程可视化)
```

| 阶段 | 内容 | 验收判据（**必须实测，不接受"pytest 过了"**） |
|---|---|---|
| **PG-1A** 资源定位 | 4 级候选解析；`PROMPTS_DIR` 改 `__file__` 锚点；`pyproject` 加 `force-include` | 隔离安装后 `doctor` **10 项 PASS**；3 个 prompt 全部可读；流派 25 / 套路 16 / 题材 18 |
| **PG-1B** 数据目录 | 仅安装态切 `%APPDATA%\storyforge`；`doctor` 只读化 | 跑 `doctor` 后**安装目录文件增量为 0**；源码树 `data_dir` **不变** |
| **PG-1C** 消除静默降级 | `flows` 资源缺失报错；`doctor` 三项独立 try；B-5 不再假通过 | **变异测试 4 连**（本项目已栽过三次"恒真检查器"） |
| **PG-2** exe 冒烟 | PyInstaller `--onedir`；`--add-data` 带 `config/` + prompts；`sys._MEIPASS` 候选 | **干净 Windows（无 Python/uv）**双击可跑：`doctor` PASS、`flows` 有输出、能建书 |
| **PG-3** HTTP 底座 | 最小 API（建书/进度/读章/报告）；**先修订 D-05** | API 契约测试；**GUI 与 CLI 共用同一 Service 层** |
| **PG-4** 桌面 GUI | 题材/流派选择 → 联网研究逐轮可见 → L-1/L-2 逐条确认 → 正文阅读 | 对照 learn 目标流程 ①~⑥ **逐环节可操作**，无黑盒 |

### 3.1 ★ 打包工具建议：**`--onedir` 而非 `--onefile`**

| | `--onefile` | **`--onedir`（推荐）** |
|---|---|---|
| 用户体验 | 单个 exe，最直观 | 一个文件夹 + exe |
| 启动速度 | **每次解包到临时目录**（本依赖树预计 5~15s） | 直接启动 |
| 杀软误报 | **高**（自解压行为像恶意软件） | 低 |
| 调试 | 极难（临时目录随机） | 直观 |
| 分发 | 需打成 zip 或安装器 | 直接压缩 |

**推荐 `--onedir` + 一个安装器**（Inno Setup 等）做成"安装包"形态 ——
用户感知仍是"双击安装"，但避开 onefile 的解包与误报问题。

⚠️ **体积预期**：无 numpy/torch，主要来自 langchain/langgraph/pydantic 的 .py 与
Python 运行时 —— 预计 **150~300 MB**（未实测，PG-2 必须量出来）。
`wangwenxiaoshuo/`（266 MB 语料）与 `.git`（177.5 MB）**必须显式排除**。

---

## 4. 需要用户拍板的决策（PG-2 开工前）

`doc/08` §6 列了 6 项，其中 3 项已被 `doc/11` 冻结（数据目录 / 零新依赖 /
`flows` 处置）。**剩余需拍板的**：

| # | 项 | 备选 | 影响 |
|---|---|---|---|
| 1 | 打包形态 | `--onedir` + 安装器（推荐）／ `--onefile` | 启动速度、杀软误报、分发方式 |
| 2 | GUI 技术栈 | 本地 Web UI（复用 D-05 的 API）／ 原生 PySide | 工期与维护成本；Web 可复用 CLI 已有能力 |
| 3 | HTTP 底座 | 标准库 `http.server`（零依赖，符合 D-05"最简技术"）／ FastAPI | 引入依赖 vs 手写 |
| 4 | 升级策略 | 覆盖安装（需数据迁移考量）／ 版本并存 | 程序在 `Program Files` 时**必须**数据在 `%APPDATA%` |

---

## 5. 风险（exe 特有）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **杀软误报** | PyInstaller 产物在 Windows 上常见误报 | `--onedir`；必要时代码签名 |
| **路径修好了但打包漏文件** | 源码树能跑、exe 读不到 —— **正是 B-1 的形状** | 验收**必须**在干净机器上做，不接受"本机 editable 通过" |
| **`%APPDATA%` 迁移** | 开发机 `data/` 含真实作品 | `doc/11` 决策 2 硬约束：**仅安装态**切换 |
| **体积膨胀** | 语料 266 MB / `.git` 177.5 MB 若误入 | 打包清单显式排除 + **产物体积上限检查** |
| **模型 API 与 key** | 用户需自备 key；`dotenv_paths()` 现只认仓库根（B-3） | PG-1A 一并修：新增用户目录候选 |
| **长任务与 GUI 卡死** | 单章可能跑 10 分钟（实测 13.7 tok/s） | GUI **必须**异步 + 进度可见；不得阻塞 UI 线程 |

---

## 6. 建议的下一步（最小可验收）

**先做 PG-1A**（资源定位统一）—— 它是后续一切的**共同前提**：
不修它，wheel 装不上、exe 也读不到资源，打包只是把坏掉的东西装进壳里。

理由：
1. `doc/11` 已完成**完整设计**且**决策已冻结**（4 项可行性验证已实测通过）；
2. 改动面小（`force-include` 源文件移动 **0 个**）；
3. 有**客观验收判据**（隔离安装后 doctor 10 项 PASS）。

PG-1A 之后**不并行铺开**，按 PG-1B → PG-1C → PG-2 顺序推进（`doc/07` §1）。

---

## 7. 明确不做（防范围蔓延）

| 不做 | 理由 |
|---|---|
| 本地推理（GPU/CPU 模型） | 独立议题，需单独选型；当前用远程 API |
| 自动更新服务 | 需服务端，超出单机单用户范围 |
| Mac 打包 | 需 Apple 开发者账号，独立排期（`doc/08` PG-4） |
| 多用户 / 权限 / 分布式 | `01-总体架构.md` §7 已明确不做 |
| 数据迁移（旧项目 → 新） | D-06：不迁移 |
| 改 `scripts/` 下 40+ 处 `parent.parent` | dev-only，不随包分发 |
