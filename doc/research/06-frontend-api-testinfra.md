# 06 · 前端 / HTTP API / learn-story 边界 / 测试与评测基建

> 调研对象：`E:\AI_project\story_llm`（**只读**，本报告未修改其中任何文件）
> 调研范围：`frontend/`、`backend/app/main.py`、`backend/app/flowmode/`、`backend/app/templates|genres|genre_state|genre_profiles`、`backend/test_*.py`、`backend/benchmark/`、可观测性六件套、运维脚本与配置。
> 产出日期：快照 = 仓库当前工作区（`sweep_all_v1.json` 时间戳 `2026-09-13T07:48Z`）。
> 约定：本文所有数字均为**实测**（`pwsh`/Python 统计），非引用文档；与旧文档冲突处以本文实测为准并标注。
> **密钥纪律**：`backend/.env`、`backend/llm_providers.json` 只读取**键名与结构**，全文未复述任何 key/token/密码值。

---

## 0. 规模实测（先对齐口径）

| 区域 | 文件数 | 行数（实测） | 备注 |
| --- | --- | --- | --- |
| `frontend/src/**`（含 `style.css`） | 31 | **11,666** | 任务书给的 10695 偏小；含 css 后为 11,666，纯 `.vue`+`.js` 为 11,373 |
| `backend/app/**/*.py` | 122 | **50,256** | 其中 `pipeline.py` 8,606、`main.py` 4,629 |
| `backend/benchmark/**/*.py` | 52 | **12,757** | 不含 `results/` 产物与 `evaluations.db` |
| `backend/test_*.py`（仓库根） | 123 | **25,781** | 任务书说 ~106，实际 123 |
| `backend/_*.py`（一次性脚本） | 48 | **4,445** | `_dbg_flow_story.py` 946 行、`_dbg_flow_learn.py` 982 行是最大的两个 |
| `backend/` 全部 `.py`（排除 `_pylibs`/`__pycache__`） | 357 | 80,730 | — |
| `backend/` 根目录 JSON 配置 | 36 | — | 另有 11 个 `*.bak*` 备份、4 个 `*.db*` |

> 任务书给的「755 py / 312k 行」包含了 `_pylibs/`（397 个 py 模块、30.9 MB）与 `__pycache__`；**实际业务代码约 8 万行**，其中 `app/` 5 万行、测试 2.6 万行、benchmark 1.3 万行。

---

## 一、前端地图

### 1.1 技术栈（`frontend/package.json`、`vite.config.js`）

- 框架：**Vue 3 `^3.4.27`**，`<script setup>` 组合式 API。
- 路由：**`vue-router ^4.3.2`**，`createWebHistory()`。
- 构建：**Vite `^5.2.11`** + `@vitejs/plugin-vue ^5.0.4`；依赖树**总共只有 2 个 runtime 依赖 + 2 个 devDependencies**。
- **没有** UI 组件库、**没有** Pinia/Vuex、**没有** axios、**没有** ESLint/Prettier/Vitest/Playwright。
- 代理：`vite.config.js:8` `'/api' → http://127.0.0.1:10087`；开发端口 5173。
- 全局样式集中在 `src/style.css`（293 行，`:root` CSS 变量 + `.btn/.card/.tag` 全局类）。
- `package.json` scripts 只有 `dev / build / preview`——**无 lint、无 test**。
- `index.html` 10–12 行的 HTML 骨架，`main.js` 55 行手工注册 16 个路由。

结论：技术栈选型本身**对重构友好**（零框架锁定、零状态库、零 UI 库），不需要"换栈"，需要的是"补齐工程化"。

### 1.2 路由表（`src/main.js`）

顶层 11 条 + `/ops` 子路由 13 条：

| 路径 | 组件 | LOC | 做什么 |
| --- | --- | --- | --- |
| `/` | `Home.vue` | 643 | 创建页：learn/story 双模式切换，story 侧 7 个可🎲随机的字段，候选池分页弹窗，文风偏好 |
| `/book/:bookId` | `MapView.vue` | 1124 | 生成前确认主界面（按 phase 分支：planning/profile_ready/awaiting_bible/map_ready/failed/rejected） |
| `/shelf` | `Bookshelf.vue` | 416 | **双书架**（按 `book_kind` 物理分区）+ 健康体检区 |
| `/read/:bookId` | `Reader.vue` | **1928** | 全站最大：目录抽屉/正文/知识卡/RAG trace 面板/专名词表/审校问题/伏笔账本/重写/定点修订/导出/文风 |
| `/progress/:bookId` | `ProgressView.vue` | 472 | 生成进度总览：StepPipeline + 章节网格（>120 章折叠）+ Agent 日志 |
| `/continue/:bookId` | `ContinueRun.vue` | 581 | 续接为 ultra 两步向导 + 后台续接监控 + 大纲补写/断点恢复 |
| `/novelty/:bookId` | `NoveltyView.vue` | 1017 | 5 个 tab 的审校查询：雷同/凭空出现/漂移/回忆依据/全书校验 |
| `/ops/overview` | `ops/Overview.vue` | 264 | 运行快照（书籍运行态 10s 轮询 + Agent + LLM + 子系统健康 + 基础设施装配） |
| `/ops/models` | `Models.vue` | 419 | 模型管理：连接/拉模型/测试/激活/并发间隔/embedding/文风样本导入 |
| `/ops/corpus` | `StyleCorpus.vue` | 502 | 文风库：样本库 / 生效看板 / 目录批量导入（后台任务 + 1.5s 轮询） |
| `/ops/settings` | `Settings.vue` | 613 | 生成设置：章节字数/审校抽查/时代自检/大纲配置，全部热生效 |
| `/ops/problem-logs` | `ops/ProblemLogs.vue` | 144 | `problems.jsonl` 浏览/清空 |
| `/ops/graph` | `ops/GraphOps.vue` | **580** | 手写 SVG 力导向图（自实现库仑斥力+弹簧，`simulate()` O(N²)×180 迭代），非 d3 |
| `/ops/entity-lifecycle` | `ops/EntityLifecycle.vue` | 282 | 实体离场/降级候选 + 逐章出场时间线 |
| `/ops/foreshadows/:bookId` | `ops/ForeshadowBoard.vue` | 208 | 伏笔全生命周期看板 |
| `/ops/drift` | `ops/DriftView.vue` | 225 | 漂移热力图 + 主线节拍表 |
| `/ops/agents` | `ops/AgentMonitor.vue` | 109 | Agent 成功率/耗时（onMounted 拉一次，无轮询） |
| `/ops/model-evals` | `ops/ModelEvals.vue` | 272 | 人工盲评控制台（Draft/Final 盲评，强制先 pairwise 后正文） |
| `/ops/novelty/:bookId` | `NoveltyView.vue` | — | 与顶层复用同一组件 |

重定向兼容路径：`/models → /ops/models`、`/corpus → /ops/corpus`、`/settings → /ops/settings`。

### 1.3 共享组件与模块

| 文件 | LOC | 说明 |
| --- | --- | --- |
| `components/StepPipeline.vue` | 357 | 7 阶段流水线总览，被 ProgressView / ContinueRun 复用；含 role→emoji 映射与"心跳年龄反推状态"逻辑 |
| `components/NotificationCenter.vue` | 221 | 全局通知中心，**每 4 秒轮询**，右下角弹框队列（最多 3 个，5s 自动消失） |
| `components/TaskStatusStrip.vue` | 78 | 写书期控制条（暂停/继续/停止/续卷） |
| `components/ToastHost.vue` + `toast.js` | 41 + 20 | 模块级 `reactive([])` 全局 toast 总线（**全站唯一的"全局状态"之一**） |
| `components/StepTimeline.vue` | 40 | 可折叠 Agent 活动条 |
| `progressTracks.js` | 328 | **纯函数**双轨进度展示逻辑，明确区分 `number / null / undefined` 三种语义（0 是合法值，null 显示「—（尚无细纲）」），零 Vue 依赖 |
| `scalesMeta.js` | 42 | 四档位元数据单例（**唯一另一个模块级全局 ref**），`/api/scales` 覆盖 + 内置兜底 |
| `api.js` | 304 | 133 个 API 方法，全部走原生 `fetch`，统一 `formatDetail()` 解析 FastAPI 错误体 |

**状态管理现状**：没有 store，没有跨页缓存。每个视图自己持 `book` ref + `setInterval` 轮询（3s/4s/10s 三种节奏并存）。全局状态仅 2 处：`toast.js` 的 `toasts` 数组、`scalesMeta.js` 的 `remote` ref。**页面切换即丢状态，回退必重新拉全量**——这是重构要解决的第一个体验问题。

### 1.4 API 客户端覆盖度（实测）

`api.js` 导出 133 个成员，与 `frontend/src/**` 全量正则交叉比对：

- **从未被任何视图调用：3 个** —— `modelEvalSummary`（Run 汇总，后端已实现）、`setHarvestOptIn`（Omen Harvest 开关，后端 `main.py:3264` 存在）、以及正则误报的 `if`。
- 后端 148 个路由 vs 前端 133 个方法：差集主要是 `POST /api/llm/providers/{id}/activate` 之外的运维/评测/续接细节，以及**全部 trace 三端点**（见 §7）。

### 1.5 前端哪些必须活、哪些是债

**必须存活（重构一等公民）**：
1. `Home.vue` 创建流 —— 但必须**拆成两个独立创建流**（story 先做，learn 预留），而不是现在的 `createMode` 三元表达式满屏飞（`Home.vue` 有 12 处 `createMode === 'story'` 分支）。
2. `Bookshelf.vue` 书架 —— 双书架设计保留，但健康体检区应下沉为独立面板。
3. `Reader.vue` —— 功能密度最高、也最该拆。1928 行里至少混了 8 个独立职责（目录/正文/知识卡/专名/RAG trace/审校/伏笔/导出）。**必须拆组件**，但功能不能删。
4. `ProgressView.vue` + `StepPipeline.vue` + `progressTracks.js` —— 这套"三态语义"（number/null/undefined）的设计是**全仓最好的前端实践**，值得原样继承并推广。
5. `Models.vue` 模型管理 —— 保留，但并发/限速输入框已在 2026-09-13 改为只读（AGENTS.md 规约 19），前端需同步简化。
6. `ops/ModelEvals.vue` + 盲评流程 —— **盲评不变量（服务端隐藏 Draft/Final 映射、先 pairwise 后正文）是全仓质量基建里最有价值的资产**，必须原样保留。

**可降级/可合并**：
- `NoveltyView.vue`（1017 行，5 tab）建议拆成 5 个独立路由页，或直接合并进 Reader 的侧栏。
- `ops/GraphOps.vue` 的手写力导向布局（O(N²)×180）在 400 节点时是主线程杀手，建议换成 canvas/WebGL 或服务端预布局。
- `ops/AgentMonitor.vue`（109 行、无轮询）与 `ops/Overview.vue` 的 Agent 区块重复，可合并。
- `/models`、`/corpus`、`/settings` 三个重定向是历史包袱，重构时直接删。

**明确是债**：
- `frontend/logs/frontend_err.log` 显示 `ContinueRun.vue` 有 `<tr>` 直接挂在 `<table>` 下的 HTML 违规（会造成 hydration 错误）；`frontend/scripts/check_vue_imports.py`（62 行 Python）是"防白屏"的自制静态检查 —— **说明当前前端没有任何真正的静态检查工具**。
- 文件名路径出现 `D:/Users/username/Desktop/learn_strory/frontend/...`，说明前端曾被搬到别的机器上跑过，工程不可复现。

---

## 二、API 清单

### 2.1 总览（实测）

- **148 个路由**，全部由 `@app.get/post/put/delete` 直接注册在 `app/main.py`（**全仓 0 个 `APIRouter`**，0 个 `include_router`）。
- `main.py` **4,629 行**；`FastAPI(title="LearnStory API", version="0.2.0")` 在 `main.py:51`。
- 12 个 Pydantic 请求体：`BookCreate / SelectBody / SettingsUpdate / EmbeddingConfigBody / LlmConnectBody / LlmTestBody / LlmCapacityBody / ProfileBody / StoryDirectivesBody / ContinueResearchBody / ContinueExecuteBody / CorpusImportBody`。
- 175 个模块级函数（含 40+ 个私有辅助 `_eta/_ledger_summary/_book_dict/_runtime_status/...`）。
- 启动用旧式 `@app.on_event("startup")`（`main.py:95`），非 lifespan；启动期做 5 类数据迁移 + 健康 worker + 向量自检。
- **CORS 全开**（`main.py:87-92`：`allow_origins=["*"]`、methods/headers 全 `*`），**无任何鉴权**。单人工具可接受，多用户前必须重做。

### 2.2 按域分组 + story/learn/ops 标注

以下 LOC 为"该端点 decorator 行到下一个 decorator 行"的实测跨度（`END` 收尾），用于判断"哪块代码真正占地方"。

| 域 | 端点数 | 实测 LOC | 占比 | 分类 | 代表端点 |
| --- | --- | --- | --- | --- | --- |
| **BOOK/CHAPTER** | 64 | **2,823** | **65.3%** | learn 主体 + story 分支混杂 | `/api/books`、`/api/books/{id}`、`/api/chapters/{id}`、`/select`、`/confirm`、`/resume`、`/pause`、`/stop`、`/entities*`、`/bible*`、`/progress`、`/export*` |
| CONTINUE | 9 | 396 | 9.2% | **按 book_kind 分流** | `/continue/plan|recommend|research|status|resume|stop|pause`、`POST /continue` |
| LLM/CFG | 13 | 167 | 3.9% | ops | `/llm/*`、`/embedding-config*`、`/settings` |
| STORY-IDEAS | 5 | 167 | 3.9% | **story-only** | `/story-genres`、`/story-premise/random`、`/story-random/{field}*` |
| OPS-eval | 12 | 161 | 3.7% | ops（评测） | `/ops/model-evals/import*`、`/runs*`、`/human-review`、`/pairwise`、`/decision` |
| CONFIG | 13 | 160 | 3.7% | ops（热配置） | `/audit-config`、`/guard-config`、`/review-config`、`/scales`、`/chapter-limits`、`/outline-config`、`/rolling-config` |
| CORPUS | 9 | 137 | 3.2% | ops（文风库） | `/corpus*` |
| OPS | 7 | 130 | 3.0% | ops | `/ops/dashboard|health|runtime|infra|traces|trace-summary` |
| OPS-misc | 10 | 76 | 1.8% | ops | `/problem-logs*`、`/notifications*`、`/books/health-*` |
| CATALOG | 3 | 58 | 1.3% | learn 为主 | `/templates`、`/flows`、`/library` |
| STATS | 3 | 48 | 1.1% | ops | `/llm/stats`、`/agents/stats`、`/genres/stacks` |
| 其余（含 `model-stats`、`delete_book` 等） | — | 余量 | — | — | — |

**关键判断**：`main.py` 的 65% 是 BOOK/CHAPTER 单域。这一域里最胖的是 `get_book`（`main.py:692-779`，87 行拼装）、`select`（844-933，89 行）、`get_book_progress`（2030-2218，**188 行**）、`continue_resume`（3048-3113，65 行）。**`get_book_progress` 188 行是单端点最大块**，说明"进度口径"逻辑本应在 service 层而非路由层。

### 2.3 调试/运维专属端点（重构可下沉或关闭）

- `/api/problem-logs`、`/api/problem-stats`、`DELETE /api/problem-logs` —— 纯运维读日志（`problems.jsonl`，实测 **395 行，394 warn / 1 error**，top module 是 `entities` 282 条）。
- `/api/ops/traces`、`/api/ops/traces/{trace_id}`、`/api/ops/books/{id}/trace-summary` —— **Trace 只读查询，前端 0 页面消费**（见 §7）。
- `/api/ops/model-evals/import*` 5 个 import 端点 —— 每次导入都是硬编码某个 benchmark artifact 文件名（`import_omen_q1_h`、`import_mimo_p1`、`import_mimo_long_memory_shadow`、`import_mimo_lm_focused_pairwise`、`import_cross_model_p1`），**是"为一次实验写一个端点"的典型**。重构应改为 1 个泛化 `POST /ops/eval-runs:import {artifact_path}`。
- `/api/model-stats`、`/api/llm/stats`、`/api/agents/stats`、`/api/genres/stacks` —— 4 个只读统计，全部可合并进 `/ops/dashboard` 一次返回。

### 2.4 重复 / 死 / 冗余

- **重复语义**：`/api/settings`（GET/PUT，模型三档）与 `/api/llm/providers` + `/api/llm/capacity` 三者都在描述"用哪个模型、并发多少"，前端 Models.vue 与 Settings.vue 各读一半。
- **重复暴露**：`bookAuditWords` GET/POST 与 `audit-config` 都在管审校词表/参数，`NoveltyView.vue` 里两套 UI 并存。
- **重定向残留**：`/models`、`/corpus`、`/settings` 三个旧路径在 `main.js` 里 redirect，是纯前端债（API 侧无对应死端点）。
- **实测 0 死端点**：148 个路由里没有任何一个缺少实现体（都有对应 def），但有 **3 个前端从未调用**的后端端点族：trace 三端点、`import-cross-model-p1`（单模型策略下已休眠）、`modelEvalSummary`（前端有方法无调用）。
- **`/api/books/{id}/cards/backfill`（`main.py:1053-1055`）只有 3 行**：直接 `return backfill_missing_cards(book_id)`，**未做 story 拒绝**（对比 `/chapters/{id}/cards/regenerate` 在 `main.py:1278` 明确抛 400「纯小说没有知识卡」）——这是一处**契约不一致**。

### 2.5 4072 行里有多少是必需的

按实测 4,629 行拆分：

| 类别 | 估算行数 | 占比 | 重构处置 |
| --- | --- | --- | --- |
| 业务端点实现（BOOK/CHAPTER + CONTINUE） | ~3,200 | 69% | **必需**，但应下沉到 service 层，路由只做参数校验 + 调 service |
| 运维/评测/配置端点（可合并、可泛化） | ~700 | 15% | 压缩到 ~1/3：合并统计端点、泛化 import、统一 config 读写 |
| 启动迁移 + 私有辅助函数（`_eta/_ledger_summary/_book_dict/_rebuild_modules_from_json`…） | ~500 | 11% | 迁到 startup 模块 + serializer 模块 |
| CORS/Pydantic body/装饰器噪声 | ~230 | 5% | 保留 |

**结论：真正必需的业务逻辑约占 70%，但"必须留在 `main.py` 里"的不到 15%。**重构目标是 `main.py` ≤ 200 行（只做 `create_app()` + router 注册），业务进 `services/`，运维进 `routers/ops.py`。

---

## 三、learn / story 边界与耦合点

### 3.1 设计意图（AGENTS.md 规约 15，`AGENTS.md:98`）

> learn/story 隔离是硬约束：①静态 system 必须整体切换，不点状打补丁；②新增模式差异拆到各自归属流程/模块，**禁止在共享 pipeline 加 `is_story` 分支**；③存量 `_is_story` 分支逐步迁出。双模式鉴别统一入口 `pipeline._is_story(book)`。

### 3.2 现状：门面已建成，但只完成了一半

**已建成的抽象层**（`backend/app/flowmode/`，4 文件 509 行）：

| 文件 | LOC | 角色 |
| --- | --- | --- |
| `flowmode/base.py` | 234 | `FlowMode` 抽象基类，**37 个方法**，默认实现 = learn 语义 |
| `flowmode/story_flow.py` | 201 | `StoryFlow(FlowMode)`，覆写差异点 + 14 个 `@staticmethod` 把 learn 专属入口置空 |
| `flowmode/learn_flow.py` | 49 | `LearnFlow(FlowMode)`，基类同构；8 个 `@staticmethod` **惰性派发**回 `pipeline._xxx` |
| `flowmode/__init__.py` | 25 | `get_flow(book)` 按 `book_kind` 取实例；`flow_for(story_mode: bool)` 供无 book 的 agent 用 |

**关键机制**：
- `pipeline.py:223-225`：`_flow(book)` = `get_flow(book)`，取代裸 `_is_story` 分派。
- `flowmode/story_flow.py:69` 起，story 专属业务**全部惰性 import 回 `pipeline._story_*`**，避免 `learn_flow → pipeline → flowmode` 循环依赖（`learn_flow.py:6-9` 注释）。
- 实测 `_flow(book).` 调用点：**48 处**，全部集中在 `pipeline.py`（46 处）+ `bible_rebuild.py`（3 处）。
- `flowmode/story_flow.py:171-201` 的 8 个 `@staticmethod` 是 learn 专属业务的 story 空实现：`fast_guard_allowed / verify_selected / maybe_expand_knowledge_bg / warn_untaught_points / dynamic_deepen_knowledge / load_untaught_points / pick_due_reviews / warn_consecutive_no_points`。

**这 8 个方法就是 learn 模式的"接口预留清单"雏形** —— 它们精确划出了 learn 独有的调度钩子。

### 3.3 剩余耦合点（全部实测 `file:line`）

#### A. 共享 pipeline 内的裸 `_is_story`（规约 15 明令禁止，仅剩 1 处）

| 位置 | 内容 |
| --- | --- |
| `app/pipeline.py:216-217` | `def _is_story(book): return (getattr(book, "book_kind", None) or "learn") == "story"` —— 定义处 |
| `app/main.py:1304` | `from .pipeline import _bm, _is_story` —— **路由层直接 import 私有函数** |
| `app/main.py:1351` | `context["story_mode"] = _is_story(book)` —— 路由层手工注入 story_mode |

> `main.py:1304/1351` 是**最脏的一处**：`revise-local` 端点绕过了 `_flow(book).context()` 门面，自己算 story_mode。

#### B. `agents/` 层经 `flow_for(story_mode)` 分派（已收敛，但仍是分支）

| 位置 | 内容 |
| --- | --- |
| `agents/writer.py:142` | `flow_for(context.get("story_mode")).plugin_mode()` |
| `agents/writer.py:289` | `_story = bool(context.get("story_mode"))` |
| `agents/writer.py:410` | `_flow = flow_for(context.get("story_mode"))` |
| `agents/editor.py:157` | `flow = flow_for(context.get("story_mode"))` |
| `agents/editor.py:210` | `flow_for(context.get("story_mode")).editor_cards_section(cards)` |
| `agents/post_processor.py:214` | `run(..., story_mode: bool = False, ...)` 形参 |
| `agents/post_processor.py:218` | `_flow = flow_for(story_mode)` |
| `agents/post_processor.py:255` | `_progress_text = "🧠 记忆提炼中…" if story_mode else "🧠 知识卡+记忆提炼中…"` |
| `agents/verifier.py:76` | `"entities": flow_for(context.get("story_mode")).verifier_entity_system()` |
| `agents/architect.py:2064-2065` | `_current_flow()`：`"当前模式门面…取代 _story_mode 布尔 + 二次分派"` |
| `agents/architect.py:2505` | `self._flow = _SegStoryFlow() if story_mode else _SegLearnFlow()` |
| `agents/architect.py:2895` | `simple = (self._flow.kind != "story") and len(all_points) < 5` ← **裸 kind 判断** |
| `agents/architect.py:2916` | `if self._flow.kind == "story":` ← **裸 kind 判断** |

> `architect.py:2895/2916` 是 agents 层仅存的两处"读 kind 值做业务分支"，`story_mode` 布尔本身已通过 `context` 单一来源（`flowmode/__init__.py:16-22` 注释声明）。

#### C. 跨层直读 `book.book_kind`（未走门面）

| 位置 | 内容 |
| --- | --- |
| `app/docstore.py:59` | `kind = "story" if kind == "story" else "learn"`（双根路由归一） |
| `app/docstore.py:65` | `return STORY_ROOT if kind == "story" else LEARN_ROOT` |
| `app/pipeline.py:646` | `books = session.query(Book).filter(Book.book_kind == "story").all()` |
| `app/pipeline.py:1682` | `rescale_to_chapters(..., story=(book.book_kind == "story"))` |
| `app/drift.py:54` | `def check_chapter_alignment(content, chapter, is_story: bool = False)` |
| `app/drift.py:66` | `if not is_story:` ← **drift 检查内部的模式分支** |
| `app/drift.py:162` | `def record_align(session, book, content, chapter, is_story: bool)` |
| `app/drift.py:166` | `check_chapter_alignment(content, chapter, is_story=is_story)` |
| `app/continue_story.py:431` | `if book.book_kind == "story":` |
| `app/continue_story.py:871` | `if not direction and book.book_kind == "learn":` |
| `app/continue_story.py:915/926/932` | `user_direction=direction if book.book_kind == "story" else ""` 等 3 处 |
| `app/continue_story.py:1582` | `if book.book_kind == "story":` |

#### D. 路由层 `kind == "story"` 分支（`main.py` 七处）

| 位置 | 内容 |
| --- | --- |
| `main.py:535` | `if kind == "story":` — premise 校验（必填、≤2000 字、去重字符） |
| `main.py:552` | `if kind == "story":` — 频道/题材校验（走 `story_genres.GENRES_BY_ID`） |
| `main.py:572` | `if kind == "story":` — 流派解析（走 `story_flows` 而非 `flows`） |
| `main.py:593` | `if kind == "story":` — 打包 `story_brief` dict |
| `main.py:2796` | `if kind == "story": return recommend_routes(...)` |
| `main.py:2814` | `if kind == "story": raise HTTPException(400, "纯小说不支持知识点研究")` |
| `main.py:3375` | `if kind == "story": return STORY_PHASE_TEXT.get(...)` |

#### E. 端点级的模式互斥（双向）

| 位置 | 拒谁 | 文案 |
| --- | --- | --- |
| `main.py:1278` | story 调 `/chapters/{id}/cards/regenerate` | 400「纯小说没有知识卡」 |
| `main.py:1564` | learn 调 GET `/story-directives` | （返回非 story 提示） |
| `main.py:1611` | learn 调 PUT `/story-directives` | （同上） |
| `main.py:2815` | story 调 `/continue/research` | 400「纯小说不支持知识点研究」 |
| `main.py:1053-1055` | **无拒绝** | `/cards/backfill` 未做 story 守卫 ← **缺口** |
| `flowmode/story_flow.py:132-133` | story 调 `backfill_gate` | 返回「纯小说没有知识卡」 |
| `flowmode/story_flow.py:172-173` | `fast_guard_allowed(scale)` | 恒 `False` |

#### F. 物理隔离已达成的部分（正资产）

- `docstore.py` 双根：`storage/learn/books/{id}/` 与 `storage/story/books/{id}/`。实测 `storage/learn/books` 有 3 本书（id 3/5/7），`storage/story/books` 有 5 本（id 8/11/18/23/24）。
- `test_flow_isolation.py`（255 行）用**同库交叉污染 e2e** 断言"story 不产生任何知识点制品（points / 知识卡 / 知识账本）"，并逐方法对拍门面语义（约 25 条 check）。

### 3.4 story 专属模块清单（实测 LOC）

| 文件 | LOC | 职责 |
| --- | --- | --- |
| `app/continue_story.py` | 1,419 | 续接：learn 走补充主题研究 / story 走发展路线 |
| `app/agents/story_director.py` | 513 | 七锚故事设计 |
| `app/ultra_structure_repair.py` | 514 | ultra 结构修复 |
| `app/story_random.py` | 475 | 灵感候选池（落盘 `story_random_pool.json`） |
| `app/book23_ultra_repair.py` | 291 | **以书名编号命名的修复脚本**（债） |
| `app/rolling_story_pipeline.py` | 277 | Skeleton/Detail/Chapter 三级流水线开关（默认关） |
| `app/book25_ultra_repair.py` | 241 | 同上（债） |
| `app/story_genres.py` | 234 | 18 个题材（男频 10 / 女频 8） |
| `app/story_flows.py` | 157 | 16 个流派 |
| `app/agents/story_arc_planner.py` | 149 | 分卷骨架 |
| `app/ultra_structure.py` | 138 | ultra 结构 |
| `app/story_health.py` | 136 | 故事体检 |
| **合计** | **4,544** | 占 `app/` 的 9% |

> `book23_ultra_repair.py` / `book25_ultra_repair.py` 是**以具体书号命名的生产模块**，属明确的历史债，重构必删（其能力应吸收进通用的 `ultra_structure_repair`）。

---

## 四、learn 模式功能清单（预留接口依据）

用户要求"先做 story，learn 后续补充但预留接口"。下面是 learn 相对 story **多出来的全部能力**，逐条给出当前实现位置与建议预留的接口面。

### 4.1 知识账本（Teaching Ledger）

- 表：`models.py:195` `KnowledgeLedger`：`book_id / point_name / status(未讲授|已讲授|已复用) / taught_ch / review_count / next_review_ch`。
- 复习调度：艾宾浩斯，`next_review_ch` + `REVIEW_INTERVALS="3,7,15,30"`；查询点在 `memory.py:3091-3096`。
- 使用点 30+ 处：`memory.py:1352/2986/3054/3091/3113/3132`、`main.py:500/892/3513`、`continue_story.py:433/438/782/1585/1588`。
- **story 侧**：`flowmode/story_flow.py:79-80` `chapter_points()` 恒返回 `[]`，`base.py:51-53` learn 版返回 `plan.get("points", [])`。

✅ **预留接口**：`TeachingLedger` 抽象 —— `record_taught(book, chapter, points) / due_reviews(book, ch) / untaught(book) / stats(book)`。story 实现为 NoOp。

### 4.2 知识点与模块（Module / points / 学习地图）

- 表：`models.py` `Module`：`book_id / rank / name / description / difficulty / points_json[{name,summary,difficulty,dependencies}] / est_chapters / selected / kind(core|extend)`。
- `Book.modules_json`（学习地图 Top10）、`Book.selected_points_json`（`{模块名: [点名]}`）、`Book.gap_fill_max`（缺口补写）、`Book.volume_domain_max`（续卷新知识域上限）。
- `Book.profile_options / profile_selected`（宽泛主题的学习画像）。
- 选择端点：`main.py:844` `select`（89 行，含超容量/偏薄/拓展域 3 重软拦截）。
- **story 侧**：无 Module 表数据；`MapView.vue:835` 用 `story_brief.arcs` 合成负 id 的伪模块来避免"无地图"假象。

✅ **预留接口**：`LearningMap` 抽象 —— `build(book) -> [Module] / select(book, ids, points) / estimate(...)`。story 实现为 `StoryArcMap`（剧情线当模块）。

### 4.3 知识卡（cards / 章末知识卡）

- 存储：`Chapter.cards`（`models.py:113`，Text，默认 `"[]"`）。
- 生成：`ChapterPostProcessor`（`agents/post_processor.py:214`），learn 版 prompt 为「知识卡+记忆提炼」，story 版为「记忆记录员」。
- 端点：`POST /books/{id}/cards/backfill`（补全书）、`POST /chapters/{id}/cards/regenerate`（单章重提炼，**story 400**）。
- 门面：`flowmode/base.py:207-210` `editor_cards_section(cards)` learn 注入「【章末知识卡】」；`story_flow.py:21-22` 换「【纯小说审校】」。
- **story 侧**：`story_flow.py:132-133` `backfill_gate` 返回「纯小说没有知识卡」；`pipeline.py:6053` 用它拦截。

✅ **预留接口**：`KnowledgeCardProvider` —— `extract(chapter_text, points) -> [Card] / render_prompt_section(cards)`。

### 4.4 知识点事实核验（factual verification）

- 缓存：`research_store.py` 的 `VerifyCache`（`verify_cache.json`，实测 **190.4 KB**），键为 `sha1(point|summary)[:20]`，值 `{verdict, note(≤60), updated_at}`。
- 消费：`pipeline` 研究阶段 + 续接 `research_extra_topics`。
- 校验器：`agents/verifier.py`（164 行）—— **这是 learn/story 共用的 agent**，learn/story 仅切换 system 文本（`verifier_entity_system`）。
- **story 侧**：`story_flow.py:176-177` `verify_selected` 恒 `None`。

✅ **预留接口**：`FactVerifier` —— `verify(points) -> [Verdict] / cached(points) -> (hits, misses)`。

### 4.5 联网研究（Tavily）

- `app/search/provider.py`（47 行）：`SearchProvider` / `TavilyProvider`（`api.tavily.com/search`）/ `MockSearchProvider`（无 Key 时占位）。
- `get_search_provider()` 按 `settings.TAVILY_API_KEY` 是否为空选择。
- 缓存：`research_store.py` 的 `ResearchStore`（`research_cache.json`，实测 **5,576.8 KB**），含 `put_stage/get_partial/clear_checkpoint` 的**分阶段断点续跑**（`{queries/snippets/map/verdicts}`）。
- `estimator.py:30-31`：learn 每章 research 调用 = 1 次，story = 0 次（`breakdown[0]["calls"]`，由 `test_flow_isolation.py:112-114` 断言）。
- 端点：`POST /books/{id}/continue/research`（**story 400**，`main.py:2815`）。
- `.env` 键：`TAVILY_API_KEY`、`SEARCH_MAX_RESULTS`、`RESEARCH_CACHE_TTL_DAYS`。

✅ **预留接口**：`ResearchProvider` —— `search(query, max) -> [Snippet] / research(topic) -> Result / checkpoint(topic)`。story 实现为 NoOp（省一次 LLM 调用与全部网络依赖）。

### 4.6 预估器（estimator）

- `app/estimator.py`（181 行）：`OUT_TOKENS`（research 3500 / architect 4500 / worldbuilder 3000 / write 3200 / cards 700 / memory 600 / review 900）、`CALLS_PER_CHAPTER = 2.5`。
- `estimate(selected_modules, scale, quality, overflow, story: bool = False)`。
- **story 差异**：无知识点（不计 points_count）、skip 联网研究、不产知识卡。
- `_avg_speed(model)` 走历史 `Metric` 表（`completion_tokens>100`，近 20 条），**缺失时降级到档位默认**（fast 45 / balanced 22 / premium 35 tok/s），并用 `safe_call` + `log_problem` 保证失败不静默。
- 门面：`flowmode/base.py:69-71` learn 版 `estimate(..., story=False)`；`story_flow.py:89-91` story 版 `story=True`。

✅ **预留接口**：保持 `estimate(selected, scale, quality, overflow, mode)` 签名，`mode` 从 bool 改成枚举。

### 4.7 learn 专属调度钩子（已在门面中，story 全部空实现）

来自 `flowmode/story_flow.py:171-201`，这 8 个就是"learn 预留接口"的**最小集**：

| 钩子 | learn 行为 | story 行为 |
| --- | --- | --- |
| `fast_guard_allowed(scale)` | `pipeline._fast_guard_allowed`（fast 档走轻量闸门） | `False` |
| `verify_selected(session, book, selected, scale)` | `_verify_selected_safe` | `None` |
| `maybe_expand_knowledge_bg(book_id, scale)` | `_maybe_expand_knowledge_bg`（知识背景扩展） | `None` |
| `warn_untaught_points(check, book)` | `_warn_untaught_points` | `None` |
| `dynamic_deepen_knowledge(session, book)` | `_dynamic_deepen_knowledge`（续卷动态加深） | `None` |
| `load_untaught_points(session, book)` | `_load_untaught_points` | `[]` |
| `pick_due_reviews(session, book, ch, data)` | `_pick_due_reviews`（艾宾浩斯到期复习） | `[]` |
| `warn_consecutive_no_points(session, book, ch)` | `_warn_consecutive_no_points` | `None` |

对应 `pipeline.py` 的调用点：`1823`（fast_guard）、`1850`（verify）、`4417/5919`（expand_bg）、`5621`（warn_untaught）、`5653`（deepen）、`5715`（load_untaught）、`6446`（due_reviews）、`8334`（warn_consecutive）。

### 4.8 learn 专属端点（story 直接拒绝或语义为空）

| 端点 | learn 语义 | story |
| --- | --- | --- |
| `POST /books/{id}/profile` + `/profile/reset` | 学习画像选择 | 无画像（phase 不会停在 `profile_ready`） |
| `POST /books/{id}/select` | 勾选知识模块 + 知识点 | 勾选剧情线（走伪模块） |
| `GET /templates` | 修仙场景模板（`templates/scenes.py` **只有 1 个模板 xianxia**） | 走 `story_genres` |
| `GET /flows?scene=` | 修仙流派（`flows.py` 373 行） | 走 `story_flows.py` |
| `POST /books/{id}/continue/research` | 补充主题联网研究 | **400** |
| `POST /chapters/{id}/cards/regenerate` | 重提炼知识卡 | **400** |
| `GET /books/{id}/library?type=set` | 复用历史知识集/大纲 | 语义为空 |

> **注意 `templates/scenes.py` 只有 39 行、1 个 `xianxia` 模板**（`SCENE_TEMPLATES` 只有一个 key），但 `main.py:566` 却用它做 `if body.scene not in SCENE_TEMPLATES: raise 400`。也就是说 **learn 模式今天实际上只能创建"修仙玄幻"场景**——这是 learn 早已停止演进、story 才是主线的硬证据。

### 4.9 流派画像与流派的插件化分隔（learn/story 之外的第二条隔离轴）

- `app/genre_profiles.py`（132 行）：18 个题材默认画像 + 12 个 flow 覆盖，字段 `combat_level / has_realm / win_core / one_shot_ok / pacing / dialogue_floor`。
- `app/genres/`（插件包，5 文件 + 4 个 builtin，共 ~390 行）：`GenrePlugin` ABC + `GenreManager.get(scene, flow, plugin_mode)`。
- `GenreManager` 是**按 `plugin_mode`（= learn/story）取插件**的（`flowmode/base.py:213-220` `post_processor_hints`）。
- `app/genre_state.py`（159 行，实测）：`SECTION_PATTERNS` 三类题材特定小节（规则怪谈 `rules` / 重生流 `rebirth` / 系统流 `system`），从圣经里正则抽取结构化状态，独立 `genre_states` 表。

✅ **预留接口**：`GenrePlugin.extract_hints()` 已是模式感知的，重构时把 `plugin_mode` 参数换成 `Mode` 枚举即可，不用改结构。

---

## 五、测试基建评估

### 5.1 规模与分布（实测）

- **123 个 `test_*.py`，共 25,781 行**，全部堆在 `backend/` 根目录（无 `tests/` 包结构、无 conftest.py）。
- **121 / 123 个文件在模块级直接改 `os.environ`**（`DATABASE_URL` / `LEARNSTORY_DATA_DIR` / RAG 参数）。

按域分布（关键字归类，有交叠）：

| 域 | 文件数 | 代表 |
| --- | --- | --- |
| 流水线/大纲/细纲 | 15 | `test_rolling_story_pipeline.py`（**1,043 行**）、`test_detail_pipeline_contract.py`（493）、`test_outline_resume.py`（720） |
| story 双轨/多卷/流派 | 18 | `test_story_e2e.py`、`test_flow_isolation.py`、`test_story_volumes.py`、`test_volume_loop.py`、`test_rolling_acceptance_guard.py`（690） |
| Trace / Benchmark / 评测 | 23 | `test_trace_*.py`（5）、`test_benchmark_*.py`（6）、`test_model_evals.py`、`test_omen_*`、`test_mimo_*`、`test_long_memory_*`（4） |
| 审校/守卫/审计 | 17 | `test_guards.py`、`test_writer_rules.py`（630）、`test_review_escalation.py`（692）、`test_prose_style.py`（360） |
| 记忆/RAG/检索 | 12 | `test_rag_keypool.py`、`test_hybrid.py`、`test_vec_index.py`、`test_novelty*.py` |
| 隔离/契约/元测试 | 11 | `test_report_gate_contract.py`（**560 行，全仓最有价值的元测试**）、`test_test_isolation_scan.py`（314）、`test_benchmark_isolation.py` |
| 真实环境（默认不跑） | 5 | `test_live*.py` |
| 其他 | — | `test_json_repair.py`、`test_e2e.py`、`test_full_suite.py` |

### 5.2 模块级 `os.environ` 突变问题（这是本仓最大的测试工程缺陷）

**问题机理**（`benchmark/sweep_all.py:1-19` 有最精确的自我诊断）：

> pytest 会**先 import 全部测试模块、再执行用例**。本仓库 100 个 `test_*.py` 里有 **51 个**在模块级直接改 `os.environ`，import 是有序副作用，因此**所有用例最终都跑在「最后一个被 import 的模块」留下的环境上**。实测后果：单进程全量扫描 → 3 failed + 8 errors；逐个文件单跑 → 6/7 全部通过。

实测今天这个数字是 **121 / 123**（比文档记录的 51 个严重得多）。

**为什么 conftest.py 解决不了**（同文件 17-19 行）：
- 模块级赋值发生在 import 期，仍会覆盖 conftest 设的值；
- 若把那 51 处改成 `setdefault`，则 100 个文件共享同一个库，破坏"每个文件各自干净库"的既有假设，反而制造新失败。

> **唯一正确的解法是进程级隔离** —— 这是 `sweep_all.py` 存在的全部理由。

**唯一做对的样板**：`test_report_gate_contract.py:28-42` 用 `@pytest.fixture(autouse=True)` + `monkeypatch.setenv()`，并明确注释"本文件刻意不加入这个模式"。**所有新测试都应按这个写。**

### 5.3 `benchmark/sweep_all.py`（719 行）设计评析

**定位**：全量回归扫描器，**每个测试文件一个独立子进程**，产出可信的通过/失败信号。

**为什么不能直接用 `pytest backend/`**：见 §5.2。

**判定口径（五态，缺失值不当 0 记账）**：

| verdict | 含义 | 是否阻断 |
| --- | --- | --- |
| `pass` | **实测**通过（有具体证据来源与计数） | 否 |
| `fail_regression` | 实测失败且**未登记**在基线里 | **是**（退出码非零） |
| `fail_known` | 实测失败，但登记在 `known_failures.json`（既有债务） | 否，单列 |
| `excluded` | 真 LLM / 需真实网络的测试，**绝不计入通过** | 否 |
| `not_covered` | 既无 pytest 用例也无脚本 runner，或超时/无法收集 | 否，但**不计入通过** |

**第六态 `baseline_stale`（同样阻断）**：`known_failures.json` 里已修好/已删除的过期条目 —— 它会把本该按回归处理的失败认成"本来就在红"，**静默放宽闸门**。这是本仓最深刻的一处设计：**"让闸门变松"本身是可检测的缺陷**。

**模式选择（每文件只跑它真正权威的那一种）**：
- `_probe_collected()` 让 **pytest 自己回答**"这文件能收集几个用例"（唯一权威，手写 AST 规则处理不了 fixture / 类式用例 / parametrize）；
- `_plan_for()` 依据 (collected, runner 覆盖度, 模块级可执行代码) 决定 `pytest` / `script` / `both` / `none`；
- 4 种脚本输出风格都要认（`[PASS]`/`PASS `/`✓`/`汇总 N/M 通过`），并用 `SCRIPT_BAD_LINES` 识别 `✘✗❌×` —— **防"打印了失败但 exit 0"蒙混**；
- 证据分档：`measured`（有逐项标记）vs `exit_code_only`（只有退出码为 0），报告显式标注，**绝不混同**。

**隔离**：`_isolated_env()`（`sweep_all.py:261-270`）为每个子进程注入临时 `LEARNSTORY_DATA_DIR` + `DATABASE_URL` + `PYTHONIOENCODING=utf-8` + 清空 `LLM_API_KEY/TAVILY_API_KEY` + `pop LEARNSTORY_TRACE_ENABLED`。

**实测执行结果**（`benchmark/results/sweep_all_v1.json`）：

```json
{"files": 123, "pass": 116, "excluded": 6, "fail_regression": 1}
fail_regression: ["test_rolling_acceptance_guard.py"]
fail_known: []   known_failures_baseline: []   baseline_stale: []
excluded: [test_live.py, test_live_learn_full.py, test_live_resume_checks.py,
           test_live_smoke.py, test_live_story_full.py, test_opencode_provider.py]
started_at: 2026-09-13T07:37:37Z   elapsed_s: 641.54   all_pass: false
isolation: "per-file subprocess + temp LEARNSTORY_DATA_DIR/DATABASE_URL"
```

### 5.4 `known_failures.json`（22 行）

```json
{"report_version": "learnstory-known-failures-v1", "known_failures": {}}
```

设计要点（全文见文件 `_doc`）：
- 作用是**区分"本来就在红"与"本次改坏了"**，给既有债务留不阻断出口。
- **每条必须带 `reason`**（缺 reason 的条目被 `load_known_failures()` 直接忽略 —— 宁严勿松），并应带 `recorded` 与 `next_action`；"没有出路的债务条目就是免死金牌"。
- **修好某文件后必须从基线删除**，2026-09-11 起这条由机器强制（`baseline_stale` 阻断）。此前有 7 条基线在全修好后仍躺了数日。
- **当前为空是健康状态，不是"还没登记"**：基线为空时一切失败都按回归处理，闸门最严。
- 契约测试：`test_report_gate_contract.py` 的 baseline/sweep 系列（9 条用例专门守护它）。

### 5.5 测试策略总结与弱点

**强的部分（值得继承）**：
1. **"缺失值不得当 0 记账"** 贯穿全仓：sweep 证据分档、`quality.py` 的 `editor_intervention_rate` 不进 mean、benchmark 的 `not_evaluated_in_mock`、`usage_source` 三枚举、`mimo_long_memory_shadow_suite` 的 `unmeasured_cases` —— 这是**同一套认识论**，重构必须原样继承。
2. **元测试**：`test_report_gate_contract.py` 用 560 行防"报告里的 gate 退回字面量 `True`"，`test_test_isolation_scan.py`（314 行）扫全仓测试的隔离合规性。**用测试守护测试本身**，这是成熟度很高的做法。
3. **进程级隔离 + 五态判定 + 基线过期阻断** 的 sweep 设计，比绝大多数商业项目的回归闸门更严谨。

**弱的部分（重构要改）**：
1. **121/123 模块级 `os.environ` 突变** —— 根因是 `config.py` 在**类定义期**就冻结了 `settings`（`_dbg_flow_learn.py:29-30` 注释），测试不得不"import 前改环境"。重构必须改成**运行时可注入的 settings**。
2. **无 `tests/` 目录结构、无 conftest.py、无 pytest.ini/pyproject 配置** —— 全靠 CWD = `backend/` 的相对 import，换个目录就跑不了。
3. **脚本式与 pytest 式两套入口并存**（123 文件里两种都有），导致 sweep 必须写 `_plan_for()` 这种复杂的模式推断。
4. **测试文件与生产代码同目录**：123 个 `test_*.py` + 48 个 `_*.py` 一次性脚本全在 `backend/` 根，与 36 个 JSON 配置、11 个 `.bak`、4 个 `.db` 混在一起。
5. **`test_rolling_acceptance_guard.py` 当前是红的**（唯一 `fail_regression`）—— 基线为空意味着**这是当前真实回归**。
6. **无覆盖率工具**：没有任何 `.coveragerc` / coverage 依赖，无法回答"哪些代码没被测过"。
7. **无 CI 配置**：没有 `.github/workflows/`，sweep 全靠人手动跑。
8. **`test_live*.py` 有真实副作用红线**（默认 book id=17，AGENTS.md 明令不得动 Book 21），靠人工纪律而非工具约束。

### 5.6 重构应采用的测试策略

**金字塔与目录**：

```
tests/
  unit/          # 纯函数：estimator / flowmode / genre_profiles / progressTracks 口径
  contract/      # 端点契约：FastAPI TestClient + 冻结 JSON schema（含 story/learn 双向拒绝断言）
  integration/   # 单章 e2e：Mock LLM，走 writer→editor→post→commit
  golden/        # 黄金文件：prompt 快照 + 章节质量基线（见 §6）
  live/          # 真 LLM，默认 deselect，需显式 --live 且拒绝生产库
conftest.py      # 唯一的环境注入点（monkeypatch fixture，绝不在模块级改 env）
pyproject.toml   # pytest 配置 + coverage 阈值
```

**关键改动**：
1. **消灭模块级 env 突变**：`Settings` 改成 `pydantic-settings` + `lru_cache` 工厂函数，测试用 `dependency_overrides` 注入，`config` 不再在 import 期冻结。这一条解决 §5.2 的根因。
2. **保留 sweep 五态判定**，但新版应能在**单进程 pytest + `--forked`（pytest-forked）**下工作；若稳定性不足，直接沿用现有的进程级 sweep 并把它接入 CI。
3. **契约测试补 learn/story 双向矩阵**：今天只有 `cards/regenerate`（story 400）与 `continue/research`（story 400）有守卫，`cards/backfill` 漏了。重构应把"哪些端点对哪个 mode 应拒绝"变成**表驱动契约测试**。
4. **黄金文件回归**：把 `omen_q1` / `mimo_p1` 的 fixture 机制泛化 —— 冻结 `(bible, outline, plot_detail, context, prev_chapters)` 的 canonical hash，对 prompt 与产物做快照比对。今天这套机制只活在 benchmark 里，应上升为 general 能力。
5. **覆盖率门禁**：新仓从第一天起设 `--cov-fail-under`，避免重蹈"不知道哪里没测"。

---

## 六、Benchmark / 评测基建

### 6.1 规模与结构

- `backend/benchmark/`：**52 个 py、12,757 行**；`results/` 下 ~140 个 JSON+MD 双写产物；`evaluations.db` 776 KB（实测）。
- `fixtures/manifests/`：`phase0_core.json`（14 case）、`omen_q1_live.json`（5 case）、`mimo_lm_focused.json`、`mimo_p2_legacy_12.json`。
- **物理隔离机制** `environment.py`（78 行）：`BenchmarkEnvironment._assert_safe()` 要求根目录名含 `benchmark`、DB 名必须为 `benchmark.db`，否则抛 `BenchmarkSafetyError`。`activate()` 重写 13 组环境变量（含 `EMBEDDING_MODE=local` 禁在线向量），`restore()/cleanup()` 还原并删临时根。

### 6.2 脚本清单与用途（实测行数）

**核心 harness（可复用）**：

| 脚本 | LOC | 用途 |
| --- | --- | --- |
| `schema.py` | 73 | 稳定数据契约：`BenchmarkCase / BenchmarkExpectation / CaseMetrics / BenchmarkResult` |
| `adapter.py` | 19 | **薄边界**：`GenerationBenchmarkAdapter.commit_chapter()` 只调 `pipeline._commit_chapter` —— "流水线重构只需改这里"，**这是全仓最好的解耦设计** |
| `loader.py` | 27 | manifest / fixture 加载 |
| `runner.py` | 182 | `run_benchmark(mode=static/mock/live)` 分发 + `_metric_summary` + 双写 JSON/MD |
| `quality.py` | 93 | 确定性质量/Hard Gate：10 个 `QUALITY_FIELDS` + 14 个 static gate |
| `compare.py` | 88 | `DEFAULT_THRESHOLDS` = quality_mean −0.02 / character −0.01 / continuity −0.03 / token_mean +20%（相对） |
| `mutations.py` | 24 | **坏数据变异**：`resurrect_dead_character / remove_required_equipment / inject_unselected_knowledge / duplicate_commit` —— 证明 gate 有感 |
| `baseline.py` | 63 | `build_baseline()`，产物创建后**不可覆盖**（`FileExistsError`） |
| `mock.py` | 318 | Mock 全链路：`writer → editor → post_processor → commit`，全程 `trace_scope` |
| `live.py` | 131 | Live JSON canary（只测 provider 稳定性与 prompt/output 尺寸，自称"还不是完整章级质量 benchmark"） |
| `gate_runner.py` | 171 | gate 执行器：**benchmark 目录里唯一会执行 pytest 的脚本** |
| `ultra_scale_offline.py` | 250 | ultra 规模离线压测（100/300/600/1000/1500 章） |
| `cache_attribution.py` | 471 | **Prompt Cache 归因**（Phase 0，只读离线；响应 `lang_tips/money_token.md`；**不改 prompt、不调 LLM**） |
| `mimo_lm_cost_attribution.py` | 167 | 用既有 P1-LM trace 产物做成本归因（含夜间 0.8× 计费窗口折算，依赖 `attempt.started_at`） |
| `mimo_lm_factual_validator.py` | 166 | **确定性硬事实校验器**：检查 fixture 声明的事实与持久化完整性，"不把 Editor 评分或关键词代理当文学真值"，不调 LLM |
| `recovery_worker.py` | 250 | Phase 2E1.5 真实子进程崩溃 worker（`os._exit(17)` 作为人为崩溃约定） |

**Phase 报告脚本（产 `results/<x>.json|md`，返回码 = Hard Gate 是否通过）**：
`phase1_report.py`(308) / `phase2_shadow_report.py`(130) / `phase2c_activation_report.py`(94) / `phase2d_attribution_report.py`(126) / `phase2d3_shadow_report.py`(99) / `phase2d3b_delta_report.py`(95) / `phase2e1_recovery_report.py`(49) / `phase2e1_5_recovery_report.py`(183)。

**章级 Live（最有价值也最重）**：

| 脚本 | LOC | 用途 |
| --- | --- | --- |
| `omen_q1.py` | **1,141** | Q1/P1/Long-memory Shadow **共用**的 Live runner；含 `_resolve_model` 官方源锁定、`_build_fixture_manifest` canonical hash、`_seed_book/_seed_case` 离线造书、`tracked_call` 物理调用计数 |
| `omen_q1_judge.py` | 510 | Q1-J 独立 Judge：8 维 rubric，**把工程元数据（model/provider/trace/token/editor）全部 redact**，只吃文学上下文与候选正文 |
| `omen_q1_h.py` | 397 | Q1-H 污染溯源：`CONTAMINATION_MARKERS` + Draft→Revision→Canonical→Judge **body hash 链**定位首捕获污染层 |
| `mimo_p1.py` | 213 | P1 官方 Mimo Primary：**强制复用冻结 Omen Q1 manifest** 并校验 fixture identity，不一致直接 `RuntimeError`（"禁止把差异归因于模型"） |
| `mimo_p1_review.py` | 416 | P1-H 人工主审：从 `evaluation_store` 读，产出 go/no-go |
| `mimo_p2.py` | 359 | P2：isolated 12 章 Legacy 连续基线，**刻意绕过 Architect/StoryDirector**，用冻结 fixture outline 直走串行管线 |
| `rolling_acceptance.py` | **1,231** | Story Rolling Pipeline 验收脚手架（Skeleton/Detail/Chapter 三级） |
| `detail_acceptance_live.py` | 433 | 细纲/星标/衔接改造的"真实前 N 章"验收（真实 LLM、隔离 DB/storage） |
| `detail_acceptance_report.py` | 316 | 细纲/衔接验收报告（离线、零 LLM） |
| `editor_patch_ab.py` | 354 | Editor 改稿 A/B（"整章重写 vs 定点修订"）|
| `clean_novel_source.py` | 170 | 《神秘复苏》保守清洗 + manifest 生成（**主键 = `stable_id = v<卷>-<序:04d>`，绝不用原书打印章号**） |
| `long_memory_*`（5 个） | 约 600 | P1-LM 长记忆 meta/RAG 污染 detector + prompt 变体 Shadow |

### 6.3 `evaluation_store.py` / `/ops/model-evals`

- `evaluation_store.py`（**813 行**，实测比文档说的 699 行多）：**独立 SQLite**（`benchmark/evaluations.db`，可用 `LEARNSTORY_EVALUATIONS_DB` 隔离），只依赖标准库 `sqlite3/json`，**不依赖 SQLAlchemy、不写生产库、不触发 LLM**。
- 4 张表：`evaluation_runs / evaluation_cases / human_reviews / human_pairwise_reviews`，FK CASCADE。
- **盲评不变量（全仓质量基建最有价值的资产）**：
  - `version_a_is_final` 由服务端 `secrets.randbelow(2)` 生成，**永不回传前端**；
  - `get_case()` 在 `pair.status != "submitted"` 时**不返回 `final_text` 键**；
  - `save_review()` 前置校验 pair 必须已提交，否则 `ValueError` → API 层 409；
  - 已提交的 review/pairwise 不可改；
  - `set_decision()` 要求全部 case 的 review + pairwise 都 submitted。
- `_suggested_qualification()` 的阈值直接决定建议资格（>1 个 isolated contamination 才 FAIL，单次 major 不自动封杀），改动会影响结论口径。
- 导入函数：`import_omen_q1_h / import_mimo_p1 / import_mimo_long_memory_shadow / import_cross_model_p1`，共享 `_import_live_bundle()`。
- 对应前端 `ops/ModelEvals.vue`（272 行）。

### 6.4 哪些对重构真正可用

**可直接继承（高价值、低耦合）**：
1. `environment.py` 的**物理隔离 + `_assert_safe()`** —— 直接搬。
2. `adapter.py` 的**薄边界模式** —— 全仓唯一正确解耦点，重构后应从一个 adapter 扩成一层 `adapters/`。
3. `schema.py` 的**稳定数据契约** —— `CaseMetrics.gates` 是 dict[str,bool]，可无限扩展。
4. `quality.py` + `mutations.py` 的**"确定性 gate + 坏数据变异证明 gate 有感"** —— 这是自动化评测的正确姿势，比 LLM-as-judge 可靠得多。
5. `baseline.py` 的**不可变产物**约定（`FileExistsError`），以及 `compare.py` 的相对阈值。
6. `evaluation_store.py` 的**盲评不变量全过程**。
7. `_build_fixture_manifest()` 的 **canonical hash**（bible/outline/plot_detail/context 子集/prev chapters）—— 这是"可复现性"的地基。

**应丢弃或重写**：
1. 全部 **Phase 报告脚本（8 个）** —— 每个都是"为一次上线验收写的脚本"，`test_report_gate_contract.py:1-13` 明说"完整性审计发现 benchmark 里没有任何脚本会执行 pytest，gate 全是字面量或手抄，13 个报告脚本受影响，其中 Phase 2E1 的 `build_report()` **整个函数零数据依赖**"。
2. **5 个 `import-*` 端点** —— 应泛化为 1 个 `POST /ops/eval-runs:import {artifact_path}`。
3. `omen_q1.py`（1,141 行）巨型 runner —— 它同时承担 Q1/P1/Long-memory 三种用途，`_resolve_model` 的官方源锁定逻辑硬编码在生产路径里。重构应抽出 `LiveCaseRunner` + 独立 `ProviderLock`。
4. `rolling_acceptance.py`（1,231 行）+ `detail_acceptance_live.py`（433）+ `editor_patch_ab.py`（354）—— 这三个是围绕**当时正在改的功能**写的一次性验收，合计 2,000+ 行，其断言应转化为通用 fixture case。

### 6.5 对用户"模型测评 trace 后续再加，可以先预留"的映射

**已经存在、可以直接复用的地基**（这是好消息 —— 不需要从零建）：

| 用户要的能力 | 今天已有的 | 差距 |
| --- | --- | --- |
| 模型测评 | `evaluation_store` 4 表 + 盲评 API + `ModelEvals.vue` | 只支持"导入外部 artifact"，**不能从生产运行中直接起一个 eval run** |
| Trace | `observability` 三表 + `trace_service` 查询 + `/ops/traces` API | **无前端页面**；插桩点只有 8 处（见 §7） |
| 模型间对比 | `compare.py` 相对阈值 + `cross_model_pairwise`（已休眠） | 单模型策略下 `import_cross_model_p1` 禁止作 Go/No-Go |
| Cost 归因 | `cache_attribution.py`（471 行）+ `mimo_lm_cost_attribution.py` | 依赖 `attempt.started_at` 做夜间折算，而 `started_at` 是**后加的字段** |

**建议预留的接口面**（story 先做时就要定死，见 §7.4 与 §9.3）。

---

## 七、可观测性评估

### 7.1 模块清单（实测）

| 模块 | LOC | 职责 | 现状 |
| --- | --- | --- | --- |
| `app/observability.py` | 353 | Fail-open Trace/Span/Attempt 原语，写 `generation_traces/attempts/spans` | **可用** |
| `app/trace_config.py` | 63 | 开关（`enabled/persist_attempts/persist_spans/capture_prompt_hash`） | 磁盘实测 `{"enabled": true, ...}` —— **生产已开启** |
| `app/trace_service.py` | 170 | 只读聚合（`list_traces/detail/summary`） | **可用** |
| `app/evaluation_store.py` | 813 | 独立评测库 | **可用** |
| `app/llm_stats.py` | 79 | LLM 调用成功率/429/建议并行数 | ⚠️ **纯内存 `deque(maxlen=50)`，重启清零** |
| `app/notifications.py` | 143 | 站内通知（SQLite `notifications` 表，MAX_KEEP=2000 自动清理） | **可用**；warn/error 级会同步落 `problems.jsonl` |
| `app/safe_guard.py` | 214 | `safe_call/safe_index/safe_get/log_problem`，落 `logs/problems.jsonl` | **可用**；日志目录跟随 `LEARNSTORY_DATA_DIR` 隔离 |
| `app/audit.py` | 421 | 凭空出现 / 大纲漂移 / 回忆依据扫描（程序化，无 LLM） | **可用** |
| `backend/logs/` | — | `problems.jsonl`（0.27 MB，395 行）、`backend.log`(0.02 MB)、`backend_err.log`(空)、3 个 `_inj*.py` 残留 | 混杂 |

### 7.2 Trace 契约（用户明确关心的部分）

**`UsageRecord`（`observability.py:44-56`）**：

```python
prompt_tokens / completion_tokens / reasoning_tokens / cached_tokens / total_tokens : int | None = None
usage_source: str = "unavailable"     # native | partial | unavailable
usage_complete: bool = False
```

**判定逻辑（实测 `observability.py:117-119`，这是全仓唯一权威出处）**：

```python
present = sum(value is not None for value in fields)
record.usage_source = "native" if present >= 3 else ("partial" if present else "unavailable")
record.usage_complete = all(value is not None for value in fields)
```

推论表：

| 场景 | `usage_source` | `usage_complete` |
| --- | --- | --- |
| 5 字段齐全 | native | True |
| 只有 prompt+completion | partial | False |
| 一个都没有 | unavailable | False |
| 有 4 个但缺 total | **native** | **False** ← source 与 complete 是两个独立维度 |

**关键约束（必须继承）**：
- 缺失值落 **NULL**，绝不用本地 tokenizer 估算填 0；proxy 用 `chars/4` 且报告必须区分 Native 与 Proxy。
- `usage_source` 只允许三个枚举值。
- `from_any()` 兼容 6 组别名：`prompt_tokens|input_tokens`、`completion_tokens|output_tokens`、`completion_tokens_details|output_token_details`、`prompt_tokens_details|input_tokens_details`、`cached_tokens|cache_read_input_tokens`（OpenAI / Anthropic 双风格）。
- **fail-open**：`_persist()` 的 `except` 全吞；trace 关闭时 `attempt()/span()` 返回 `None`，调用方必须容忍。

**三张表（`models.py`）**：
- `generation_traces`：`trace_id / book_id / book_kind / operation / volume / chapter_id / chapter_idx / status / elapsed_ms / root_agent / error_* / metadata_json`
- `generation_attempts`：上述 + `agent / scene / attempt_no / model / provider / request_mode / streaming / input_chars / output_chars / 5 个 token 字段 / usage_source / usage_complete / retry_reason / http_status / finish_reason / prompt_hash / system_prompt_hash`
- `generation_spans`：`span_id / parent_span_id / name / status / input_chars / output_chars / 3 个 token 字段 / metadata_json`（**spans 表缺 `cached_tokens` 与 `total_tokens` 列**）

**`trace_service.summary()` 已实现的聚合**：
- attempts / success_rate / p50_ms / p95_ms / retry_attempts / blank_outputs
- token 总量 + `unknown_usage_count`（`total_tokens is None`）
- `usage_native_count / partial / unavailable / complete_count`
- `by_agent: {agent: {calls, prompt_tokens, completion_tokens, p50_ms, p95_ms}}`
- `retry_waste_tokens / retry_waste_rate`
- `token_amplification_proxy = total_attempt_tokens / final_output_chars`
- `char_amplification_proxy`
- `known_usage_rate`
- `accepted_chapters`（含 `chapter_commit` ok span 的 trace 数）
- **`context_breakdown_chars / context_input_chars / context_coverage_proxy`** ← 从 span `metadata_json` 的 `context_breakdown` 字典累加

### 7.3 实现完成度：插桩严重不足

实测全仓插桩点：

| 原语 | 调用点 |
| --- | --- |
| `trace_scope(` | **仅 1 处真实调用**：`pipeline.py:6293`（+ `observability.py:266` 定义处） |
| `span_scope(` | **6 处**：`chapter_revisions.py:143`、`pipeline.py:6303/8371/8390/8400`（+ 定义处） |
| `.attempt(` | **2 处** |

**含义**：整条生成链只有**一个顶层 trace**，attempt 记录只覆盖极少数物理调用。要做到"每节点 span / 每调用 token 归因"，今天的数据量**根本不够**。

**其他缺口**：
- `llm_stats.py` 是**纯内存** deque(maxlen=50)，重启清零 —— 与"观测应可持久"矛盾。
- **前端 0 个 trace 页面**（`/ops/traces` 三端点无 UI 消费）—— 用户看不到任何 trace 数据。
- `generation_spans` 表**没有 `cached_tokens`/`total_tokens` 列** —— 即使 span 级记了 cache 命中也会丢。
- `trace_config` 用模块级 `_cache` + 手工 `reload()`，而 AGENTS.md 规约 18 明说"uvicorn --reload 不可靠" → **改配置不生效是常态**。
- `problems.jsonl` 实测 **394 warn / 1 error**，且 top module `entities` 占 282 条 —— 说明 warn 级噪音淹没了唯一的真 error，运维视图实际不可用。

### 7.4 重构应设计的可观测性接口

**保留的契约（不变）**：
```
usage_source ∈ {native, partial, unavailable}
usage_complete ⇔ 5 字段全非 None
缺失 → NULL，永不填 0；proxy 用 chars/4 且显式标注
fail-open：观测失败绝不影响生成
```

**必须新增的能力**：

1. **每节点 span**：把 pipeline 的每个阶段（research / architect / worldbuilder / write / editor / post / commit / memory）包成一个 span，`parent_span_id` 成树。今天只有 6 处 span，目标应是"每个 pipeline 阶段一个"。
2. **每调用 token 按上下文来源归因**：现在 `context_breakdown` 只在 `_prepare_chapter(bridge=...)` 一处写入（Q1 runner 里）。应改为 **token 级的归因**：
   ```
   Attempt.context_sources = [
     {source: "bible"|"outline"|"plot_detail"|"char_state"|"rag"|"prev_tail"|"style"|"instruction",
      chars: N, tokens_proxy: N/4, tokens_native: int|None,  # native 只有 provider 支持 prompt caching 分段时才有
      cache_hit: bool|None}
   ]
   ```
   判定逻辑复用 `usage_source` 的三态语义：拿不到 provider 分段用量就填 NULL，**绝不按比例摊派**。
3. **成本：每章 / 每卷 / 全书**
   ```
   cost(chapter_id) -> {native_tokens {prompt, completion, reasoning, cached}, 
                        proxy_chars, attempts, retries, retry_waste_tokens,
                        amplification {token, char}, 
                        billable_window: "peak"|"offpeak"|"unknown",  # 依赖 attempt.started_at
                        currency_cost: Decimal|None}
   ```
   夜间 0.8× 折算逻辑已有（`benchmark/cache_attribution.py`），应上升为 core。
4. **质量指标**：把 benchmark 已有的 `QUALITY_FIELDS` 与 `gates` 变成**每章落库的一等数据**，而不是只活在离线报告里：
   ```
   chapter_quality(chapter_id) -> {outline_adherence, character_consistency, canon_consistency,
                                   foreshadow_completion, continuity_score, novelty_score,
                                   editor_intervention_rate, gate_results: dict[str,bool],
                                   measured: dict[str,bool]}   # measured=false 表示该维度未测，不是 0
   ```
   `measured` 位是必须的 —— 这是"缺失不得当 0"在质量维度的落地。
5. **trace 的 UI**：`ops/TraceView.vue` 或直接把 trace 嵌进 Reader 的章节详情（今天 Reader 已有 RAG trace 面板，扩展成完整 trace 树成本很低）。

---

## 八、运维复杂度

### 8.1 启动链

- `start.bat`（52 行）：查 `.env` → 查 `python`/`npm` → 装依赖（靠 `requirements_installed.flag` 一次性标记）→ `start "LearnStory-Backend" cmd /k "python -m uvicorn app.main:app --port 10087"` → 硬等 4 秒 → `start "LearnStory-Frontend" cmd /k "npm run dev"` → 硬等 6 秒 → 开浏览器。
- `stop.bat`（12 行）：先按窗口标题 `taskkill`，**再兜底按端口 `netstat -ano | findstr :10087` 杀 PID**。
- **没有优雅停机**：`taskkill /f` 直接杀进程，正在写的章节会留在 `writing` 状态，靠 startup 的 `recover_orphans()` 兜底。
- **硬编码 4s/6s 等待**：机器慢就白屏。
- **端口 10087**（非默认 8000），前后端 + 代理 + 文档四处引用（AGENTS.md 规约 10）。

### 8.2 `docker-compose.yml`（5 个服务，全部冷门端口）

| 服务 | 端口映射 | 用途 |
| --- | --- | --- |
| redis | **6397**:6379 | 基础设施（`infra/redis_impl.py` 127 行） |
| etcd | — | Milvus 依赖 |
| minio | — | Milvus 依赖（凭据硬编码 `minioadmin/minioadmin`） |
| milvus | **19531**:19530 | 向量库（`infra/milvus_impl.py` 97 行） |
| postgres | **55433**:5432 | 备选存储 |

**问题**：
- **这套基础设施在生产里可能根本没跑** —— `infrastructure.local.json`（0.3 KB）的存在说明本机走的是 local 降级路径；`infra/factory.py`（159 行）的能力是"真机可用走真机、不可用静默落本地、绝不抛"（由 `test_infra_switcheroo.py` 保障）。
- 即"**同时维护两套基础设施实现（真机 + 本地），而真机那套没有实际验证**"。这是纯负债。
- minio 凭据明文硬编码在 compose 里。
- Redis/Milvus/Postgres 三个服务对"单人本地小说生成器"是**过度设计**：SQLite + numpy 向量 + 文件队列（`infra/sqlite_queue.py` 25 行）已经跑完全部业务。

### 8.3 配置地狱（实测）

`backend/` 根目录：

| 类别 | 数量 | 明细 |
| --- | --- | --- |
| 运行时 JSON 配置 | **26 个** | `audit_config / chapter_limits / character_delta_config / commit_recovery_config / editor_cost_config / free_pool / guard_config / infrastructure(+.local) / long_memory_meta_guard_config / long_memory_prompt_config / long_memory_routing_config / model_settings / offpeak_config / omen_harvest_config / outline_config / prose_style_config / review_config / review_escalation_config / revision_config / rolling_story_config / scales / story_random_pool / trace_config / ultra_structure_config / writer_rules_config` |
| 大缓存 JSON | 3 个 | `artifact_cache.json` **7.9 MB**、`research_cache.json` **5.6 MB**、`verify_cache.json` **190 KB** |
| 其他 JSON | 7 个 | `artifact_dbg/test.json`、`selection_cache.json`、`llm_providers.json`、`_rag_ab_result.json` 等 |
| **合计** | **36 个 `.json`** | — |
| `*.bak*` 备份 | **11 个** | 含 6 个 `llm_providers.json.bak_*`（**.gitignore 注释自承："历史上有 13 个 .bak_* 因此裸奔：一次 `git add -A` 就会把密钥写进历史"**） |
| `*.db*` | 4 个 | `learnstory.db`(8.4 MB) + `-shm` + `-wal`(4.3 MB) + `.bak-before-del53-094142`(2.4 MB) |

**26 个 JSON 配置 = 26 套"`_raw()` + RLock + `_cache` + `reload()` + `save()`"的复制粘贴模板**（`docs/codebase_handover/10_配置与FeatureFlags.md:20` 官方承认"统一模式：几乎都是同一模板"）。每个模块都要独立处理：文件缺失、解析失败、热生效、缓存失效。**这是重构最该消灭的一块复杂度。**

**`.env`**（只列键名，27 个）：`LLM_PROVIDER / ZEN_BASE_URL / ZEN_API_KEY / EMBEDDING_MODEL / EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODE / RAG_WINDOW_CAP / RAG_KEY_BONUS / RAG_TIME_DECAY / RAG_SAME_VOL_CAP / RAG_MAX_CHARS / RAG_KEY_POOL / RAG_RERANK_MODE / RAG_RERANK_MODEL / TAVILY_API_KEY / SEARCH_MAX_RESULTS / RESEARCH_CACHE_TTL_DAYS / PARALLEL_WORKERS / REVIEW_SAMPLE_RATE / LLM_TIMEOUT / DATABASE_URL / HOST / PORT / MAX_TOTAL_WORKERS / ARCHITECT_PARALLEL / RESEARCH_WORKERS`。

> 注意：`llm_providers.json` 里的运行时模型会**覆盖** `.env` 的 `MODEL_*`（`config.py:181-197` 在 import 期应用覆盖）。也就是说 **`.env` 里写的模型配置是静默 no-op** —— `_dbg_flow_learn.py:30-31` 明确记录了这个陷阱："只改隔离根 model_settings.json 三档是**静默 no-op**"。这是极难排查的坑。

**`llm_providers.json`**（9.5 KB，**含 6 个明文 api_key**）：`providers(6) / active_id / active_model / model_capacity`。6 个提供方：硅基流动（101 个模型）、智谱 GLM、OpenCode Go（36 个模型）、AMD Radeon、BAI、**官方小米 Mimo（`token-plan-cn.xiaomimimo.com`）**。注释说明单模型策略锁定在官方 Mimo。

### 8.4 `_pylibs/`（30.9 MB，397 个 py 模块）

- `.gitignore` 注释："沙箱内 pip 降级安装目录（仅 Alembic 命令使用，不提交）"。
- 内容：`alembic` + `sqlalchemy` + `greenlet` + `mako` + `markupsafe` + `typing_extensions`。
- **它是"为了跑 alembic 而在项目内塞了一份 SQLAlchemy"** —— 而项目本身已经在用 SQLAlchemy 2.0.52。这是环境管理失败的化石。
- 它也是"755 py / 312k 行"这个统计数字的主要来源。

### 8.5 其他运维债

| 项 | 实测 |
| --- | --- |
| `backend/` 根 `_*.py` 一次性脚本 | **48 个，4,445 行**（`_dbg_flow_story.py` 946 / `_dbg_flow_learn.py` 982 / `_tmp_*` / `_diag_*` / `_probe_*` / `_watch_*` / `_mutate_*` / `_reset_*`） |
| `backend/logs/` 里的 `.py` 残留 | `_inj.py / _inj2.py / _bk19.py` + `__pycache__` —— **调试脚本写到了日志目录** |
| `backend/backups/` | 11 个 `book24_reset_*` 时间戳目录 |
| `backend/_tmp_test/`、`_dbg_learn_artifacts/`、`_diag_seg_raw_out/` | 3 个临时产物目录 |
| 生产模块以书号命名 | `book23_ultra_repair.py`(291) / `book25_ultra_repair.py`(241) |
| git 状态 | AGENTS.md 规约 1："git 停在「批次6」，工作区有 **38 个批次的未提交改动**"，并明令"绝不执行 `git checkout --` 或 `git clean`" |
| 前端路径泄漏 | `frontend_err.log` 里出现 `D:/Users/username/Desktop/learn_strory/frontend/...` |

### 8.6 重构应消除的运维复杂度（清单）

1. **26 个 JSON 配置 → 1 个 `config.yaml`（或 DB 表）**，用 `pydantic-settings` 统一 schema、统一热重载、统一校验。Feature flag 集中到一处并带默认值与生命周期标注。
2. **docker-compose 5 服务 → 0**：单人本地工具用 SQLite + 本地向量（bge-m3 已在用）+ 文件队列。真要分布式再引入 Redis/Milvus。
3. **`_pylibs/` 删除**，用 `uv` 或 `poetry` 管依赖 + `alembic` 作为正式 devDependency。
4. **`start.bat/stop.bat` → 单个 `uvicorn` 命令 + 前端 `vite` 或直接由后端托管 `dist/`**（今天后端**完全没有 StaticFiles 托管**，前端必须单跑一个 dev server，这是纯粹的部署复杂度）。生产模式应 `app.mount("/", StaticFiles(directory="dist", html=True))`，一个进程一个端口。
5. **48 个 `_*.py` → 0**：需要的进 `scripts/` 并纳入 git，不需要的删。禁止"以书号命名的生产模块"。
6. **11 个 `.bak` → 0**：靠 git 分支/tag，不靠文件系统复制。密钥类文件应从"裸奔"改为显式 `.gitignore` + 一次性轮换（AGENTS.md 已记录曾差点泄露）。
7. **3 个巨型 cache JSON（13.7 MB）→ SQLite 或 `.cache/` 目录**：`artifact_cache.json` 7.9 MB 且 LRU 只留 60 条，读写成本远高于收益。
8. **端口 10087 保留或改回 8000**（无所谓，但要只在一处定义）。
9. **`.env` 与 `llm_providers.json` 的模型配置二义性必须消除**：单一来源，另一处读取时必须显式报错而非静默覆盖。

---

## 九、重构建议

### 9.1 外部表面：HTTP API 设计

**结论：继续用 REST/JSON（FastAPI），不引入 GraphQL，不上 gRPC。**

理由：
- 客户端只有 1 个 Vue SPA，无第三方集成诉求 —— GraphQL 的"客户端驱动取数"价值为零，只会带来 N+1 与复杂度。
- 但**今天的 REST 有问题，问题不在协议而在组织**：
  1. 148 个端点平铺在 1 个文件、0 个 `APIRouter`；
  2. 808 行 `get_book_progress` 这类"HTTP 端点里写业务"；
  3. 端点粒度过细（148 个端点服务 19 个页面 ≈ 每页 8 个请求），导致前端每页 3–4 个轮询定时器。

**具体设计原则**：

| 原则 | 做法 |
| --- | --- |
| **资源化 + 分层** | `routers/{books,chapters,generation,learning,ops,evals}.py`，每个 ≤ 400 行；路由只做 Pydantic 校验 + 调 service |
| **写操作全部异步任务化** | 今天已有 `start_planning/start_select/spawn_resume/_continue_mark` 四套**互不相通的**并发防护。统一为 `POST /jobs {type, params}` → `{job_id}`，`GET /jobs/{id}` 查状态，`POST /jobs/{id}:cancel`。所有任务走统一 job 表 + 统一锁 |
| **状态查询聚合** | 把 `get_book + continue/status + progress + outline/detail-status` 4 个轮询合并成 **1 个 `GET /books/{id}/state?since=<etag>`**，返回 book + phase + 双轨进度 + waiting_reason + 可用的下一步动作。前端从 3–4 个定时器降到 1 个 |
| **增量/长连接** | 轮询改 **SSE**（`GET /books/{id}/events`）。今天是 3s 轮询 ×N 页面 ×M 定时器，SSE 一次连接全解决，且天然支持"章节写完推送" |
| **模式显式化** | 创建走 `POST /books {mode: "story"\|"learn", ...}`，**mode 是一等字段且不可变**；所有 mode 相关行为由 mode 决定的策略对象承担（见 §9.4） |
| **运维端点前缀隔离 + 可关闭** | `/ops/*` 保留前缀，但由配置 `ops.enabled` 控制是否注册（生产单用户也建议保留，因为这是自用工具） |
| **契约冻结** | 端点用 `response_model=` 显式声明（今天**一个都没有**）；生成 OpenAPI 并把它作为前端类型来源（`openapi-typescript` 生成 `.d.ts`，消灭 `api.js` 手工维护 133 个方法） |
| **鉴权** | 本地单人可继续无鉴权，但必须有 `AUTH_TOKEN` 开关；CORS 从 `["*"]` 改为显式白名单 |

**端点数量目标**：148 → **~70**（合并统计类、泛化 import、把 12 个热配置端点统一为 `GET/PUT /config/{key}`）。

### 9.2 前端技术栈决策

**结论：留在 Vue 3，但必须补齐四件事。不换 React/Svelte。**

理由：11.6k 行代码 + 0 框架锁定，迁移成本 > 收益；Vue 3 的 `<script setup>` 与本仓风格契合。

必补：

| 事项 | 具体 |
| --- | --- |
| **类型安全** | TypeScript（`.vue` + `<script setup lang="ts">`），API 类型从 OpenAPI 生成。今天 `api.js` 是 133 个手写方法的裸 `fetch`，**任何后端字段改名都只能在运行时发现** |
| **状态层** | Pinia：至少 `useBookStore`（书/章节/进度，跨页共享，消灭切页重拉）、`useJobsStore`（统一轮询/SSE）、`useConfigStore`、`useUiStore`（toast 迁移进来）。今天只有 2 个模块级 ref，切页即丢状态 |
| **轮询统一** | 所有 `setInterval` 收敛到 store 里的 1 个 SSE 连接 + 1 个兜底轮询器。今天是 3s/4s/10s 三种节奏散落在 8 个组件里 |
| **组件拆分** | `Reader.vue`(1928) → `ReaderLayout + TocDrawer + ChapterBody + KnowledgeCardPanel + EntityTable + RagTracePanel + ReviewPanel + ForeshadowPanel`（8 个，每个 ≤ 300 行）；`MapView.vue`(1124) → 按 phase 拆 6 个子组件；`NoveltyView.vue`(1017) → 5 个独立 tab 组件 |

可选但推荐：**Vitest + Vue Test Utils**（今天前端 0 测试）、**ESLint + Prettier**（替代 `scripts/check_vue_imports.py` 这个自制检查器）、**Storybook 或最小视觉回归**（`BookShelf`/`Reader` 是布局敏感页）。

`progressTracks.js` 的**三态语义**（0 合法 / null 显式无数据 / undefined 字段缺失）应上升为全前端的展示契约，并配 Vitest 单测。

### 9.3 配置策略

**结论：一个 `pydantic-settings` 的 `Settings` + 一个 DB 表的运行时覆盖层。**

设计：

```
# 1. 静态配置（启动期，不可热改）—— .env / 环境变量
class Settings(BaseSettings):
    database_url: str
    host: str = "127.0.0.1"
    port: int = 8000
    llm: LlmSettings          # providers 从 DB 读，.env 只放默认
    embedding: EmbeddingSettings
    ops: OpsSettings
    
# 2. 运行时配置（热改，DB 单表）—— 取代 26 个 JSON
class RuntimeConfig(Base):
    __tablename__ = "runtime_config"
    key: str = primary_key          # "chapter_limits.fast" / "review.sample_rate" / "flag.trace_enabled"
    value: str                      # JSON
    schema_version: int
    updated_at: datetime
    updated_by: str

# 3. 访问层（唯一入口，带类型 + 校验 + 缓存 + 变更事件）
class ConfigService:
    def get[T](self, key: str, model: type[T]) -> T
    def set(self, key: str, value) -> None      # 写 DB + 递增 epoch + 广播
    def watch(self, prefix: str) -> AsyncIterator  # 热生效
```

**收益**：
- 26 个 `_raw()/_cache/RLock/reload/save` 模板 → 1 个实现；
- Feature flag 集中，可一眼看到**所有开关的当前值与默认值**（今天散在 12 个模块的 `DEFAULTS` 常量里）；
- 配置变更可审计（`updated_at/updated_by`）；
- 消除"`.env` 写模型但被 `llm_providers.json` 静默覆盖"这类陷阱 —— **单一来源，另一处读取必须抛错**。

**Feature flag 生命周期**（今天有 `revision_config / character_delta_config / commit_recovery_config / long_memory_*_config / omen_harvest_config / rolling_story_config / ultra_structure_config` 七八套"shadow/enabled/rollback"三态旗标，全部默认关且从未 cutover）：
> 每个 flag 必须带 `owner / created_at / 计划删除日期 / rollback 命令`。**没有删除日期的 flag 不允许合并**。今天是"shadow 逻辑写完了、观测做完了、结论是 NO_CUTOVER，然后 flag 永远留在代码里"——这类死代码是新仓最大的熵源。

### 9.4 learn 模式接口预留方案（story 先行的具体做法）

**核心思路：把 `FlowMode` 门面从"37 个方法的上帝接口"改成"按能力组合的策略集合"。**

```python
@dataclass(frozen=True)
class ModeCapabilities:
    """一个模式声明它支持哪些能力；story 用 NoOp 实现，learn 用自己的实现。"""
    learning_map: LearningMap        # 学习地图 / 剧情线（story = StoryArcMap）
    ledger: TeachingLedger           # 知识账本（story = NullLedger）
    cards: KnowledgeCardProvider     # 知识卡（story = NullCards）
    research: ResearchProvider       # 联网研究（story = NullResearch）
    verifier: FactVerifier           # 事实核验（story = NullVerifier）
    estimator: Estimator             # 预估（story = StoryEstimator）
    genre: GenrePlugin               # 流派插件（已有，按 mode 取）
    prompt_pack: PromptPack          # 提示词整包（今天的 37 个方法收敛到这里）

MODES = {
    "story": ModeCapabilities(story_impl...),
    "learn": ModeCapabilities(learn_impl...),   # ← 第一期只放占位实现
}
```

**第一期（story）必须做的事**：
1. 定义上面 8 个 Protocol/ABC，**只写 story 实现**；
2. `learn` 注册为 `NotImplementedMode`，**任何取用它的调用点立刻 `raise NotImplementedError("learn mode not yet implemented")`** —— 比今天的"story 侧空实现"更安全（今天的 `story_flow.py:171-201` 是 story 空实现 learn 钩子，方向反了）；
3. **把 AGENTS.md 规约 15 变成机器可验证的约束**：写一个 AST 检查器（类似 `sweep_all.py` 的 `_module_test_functions`），断言"`services/` 与 `pipeline/` 下的模块不得出现 `book_kind ==` 或 `is_story` 字面量"。今天这条规矩靠人盯，而实际有 **30+ 处残留**（§3.3）。
4. **契约测试表驱动化**：建一张 `mode × endpoint → expected(200|400|404)` 矩阵，把 §3.3-E 的双向拒绝变成测试（补齐 `cards/backfill` 的缺口）。

**接口面清单（这就是"预留"的全部内容）**：

| Protocol | 方法 | story 实现 | learn 未来实现 |
| --- | --- | --- | --- |
| `LearningMap` | `build(book) -> list[Node]`, `select(book, ids, points)`, `estimate(...)` | StoryArcMap | Module/points 学习地图 |
| `TeachingLedger` | `record_taught / due_reviews / untaught / stats` | NullLedger | KnowledgeLedger + 艾宾浩斯 |
| `KnowledgeCardProvider` | `extract(text, points) -> list[Card]`, `render_section(cards)` | NullCards | ChapterPostProcessor 知识卡 |
| `ResearchProvider` | `search(q, n)`, `research(topic)`, `checkpoint(topic)` | NullResearch | Tavily + research_cache |
| `FactVerifier` | `verify(points)`, `cached(points)` | NullVerifier | verifier + verify_cache |
| `Estimator` | `estimate(selection, scale, quality, overflow)` | story 公式（无 points/research/cards） | learn 公式（含 points_count） |
| `PromptPack` | 今天 `FlowMode` 的 37 个方法 | StoryPrompts | LearnPrompts |
| `GenrePlugin` | `extract_hints()`, boss 三态，… | 已实现 | 已实现 |

**8 个 learn 专属调度钩子**（§4.7）在 story 侧应**整个不存在**（而不是返回 None），由 `NullXxx` 提供 no-op，pipeline 直接调不判空。

### 9.5 测试策略（新仓落地）

见 §5.6 的完整方案，关键三条：

1. **消灭模块级 env 突变**：`Settings` 运行时注入，`conftest.py` 是唯一注入点，用 `monkeypatch` fixture（照抄 `test_report_gate_contract.py:28-42` 的样板）。
2. **保留 sweep 的判定哲学**（五态 + `fail_known` 带 reason + `baseline_stale` 阻断 + 证据分档），但把它做成 **CI job** 而不是手动脚本。今天 `sweep_all` 跑一次 **641 秒**、123 个文件、116 pass / 1 regression —— 这个耗时完全可接受，应该每次 push 都跑。
3. **契约测试表驱动**：`mode × endpoint` 矩阵 + OpenAPI schema 冻结（`schemathesis` 或自写），避免"加了 story 忘了拒绝 learn"。

### 9.6 评测/trace 接缝（对应用户"后续再加，先预留"）

**第一期只做 3 件事，但把接口定死**：

```python
# 1. 观测原语（今天已有，几乎不用改）
trace_scope(operation, book_id, chapter_id, mode, metadata) -> Trace
span_scope(name, **meta) -> Span          # 每 pipeline 阶段一个
attempt(agent, model, provider, usage, context_sources) -> Attempt

# 2. 成本/质量读取接口（新增，第一期先返回 NULL 占位但要能跑通）
def chapter_cost(chapter_id) -> ChapterCost        # native_tokens + proxy_chars + billable_window + currency
def chapter_quality(chapter_id) -> ChapterQuality  # 10 个 QUALITY_FIELDS + gate_results + measured: dict[str,bool]
def book_cost(book_id) -> BookCost                 # 聚合到卷/全书

# 3. 评测 Run（复用 evaluation_store 的 schema，泛化导入）
POST /ops/eval-runs {name, artifact_path|job_ids, evaluation_type} -> run_id
GET  /ops/eval-runs/{run_id}                  # 复用今天的盲评不变量
POST /ops/eval-runs/{run_id}/cases/{cid}:pairwise
PUT  /ops/eval-runs/{run_id}/cases/{cid}/review
POST /ops/eval-runs/{run_id}:decide
```

**第一期必须做对的 4 条不变量（做错了后面补不回来）**：
1. `usage_source ∈ {native, partial, unavailable}`，缺失落 NULL，**永不填 0，永不用本地 tokenizer 冒充**；
2. `measured: dict[str,bool]` —— 每个质量维度显式标注"测了没有"，未测 ≠ 0；
3. Trace **fail-open**：观测失败绝不影响生成，也绝不吞掉生成异常；
4. **盲评映射只在服务端**，`version_a_is_final` 永不外泄，pairwise 未提交不返回 final。

**第一期明确不做**：真实 LLM 的章级 Judge、跨模型 pairwise、per-node 的 token 分段归因（依赖 provider 支持）、UI 里的 trace 树。这些都留到"模型测评 trace 后续再加"那一期。

### 9.7 优先级建议（按"投入产出比"排序）

| 优先级 | 动作 | 收益 |
| --- | --- | --- |
| P0 | 定死 `ModeCapabilities` 8 个 Protocol + story 实现，learn 全 `NotImplementedError` | 用户诉求的直接落地；后续补 learn 不用动 pipeline |
| P0 | `Settings` 运行时注入 + `conftest.py` 统一隔离 | 解决 121/123 模块级 env 突变，是新仓所有测试的地基 |
| P0 | `main.py` 从 4,629 行降到 ≤200 行，业务进 `services/` | 一切后续重构的前置条件 |
| P1 | 26 个 JSON → 1 个 `ConfigService` + DB 表 | 一次性消灭最大的重复代码块与热生效 bug 源 |
| P1 | API 端点 148 → ~70，写操作统一 job 化，前端轮询 → SSE | 前后端同时减复杂度 |
| P1 | `Reader.vue` / `MapView.vue` / `NoveltyView.vue` 拆分 + Pinia | 前端可维护性 |
| P2 | 删 `_pylibs` / docker-compose / 48 个 `_*.py` / 11 个 `.bak` / `book2X_ultra_repair.py` | 仓库瘦身，消除密钥泄露面 |
| P2 | 后端 StaticFiles 托管前端 dist，单进程单端口 | 消灭 start/stop.bat 与双进程 |
| P2 | `trace_scope/span_scope` 插桩从 8 处扩到每个 pipeline 阶段 | trace 才有数据可用 |
| P3 | 前端 TypeScript + OpenAPI 代码生成 | 消灭 `api.js` 133 个手写方法 |
| P3 | sweep_all 接入 CI + 覆盖率门禁 | 防回归 |

---

## 附录 A · 关键 `file:line` 索引（供重构时定位）

**learn/story 边界**
- `app/pipeline.py:216-217` `_is_story(book)` 定义
- `app/pipeline.py:223-225` `_flow(book)` 门面入口
- `app/flowmode/base.py:7-234` `FlowMode` 37 方法基类
- `app/flowmode/story_flow.py:171-201` learn 钩子的 story 空实现
- `app/flowmode/learn_flow.py:11-49` 8 个 learn 钩子惰性派发
- `app/flowmode/__init__.py:11-22` `get_flow(book)` / `flow_for(story_mode)`
- `app/main.py:1304,1351` 路由层绕过门面直取 `_is_story`（**最脏**）
- `app/drift.py:54,66,162,166` drift 内部 is_story 分支
- `app/agents/architect.py:2895,2916` 裸 `_flow.kind` 判断
- `app/continue_story.py:431,871,915,926,932,1582` 6 处 `book_kind` 直读
- `app/main.py:535,552,572,593,2796,2814,3375` 7 处路由层 `kind == "story"`

**端点契约缺口**
- `app/main.py:1053-1055` `/cards/backfill` 缺 story 守卫（对比 `main.py:1278` 有守卫）
- `app/main.py:1564,1611` `/story-directives` 拒绝 learn
- `app/main.py:2815` `/continue/research` 拒绝 story

**可观测性**
- `app/observability.py:117-119` `usage_source`/`usage_complete` 判定（唯一权威）
- `app/observability.py:44-56` `UsageRecord` 定义
- `app/trace_config.py:16-22` `DEFAULTS`（磁盘实测 `enabled: true`）
- `app/pipeline.py:6293` 全仓唯一 `trace_scope` 调用点
- `app/trace_service.py:25-90` `list_traces/detail/summary`

**测试与评测**
- `backend/benchmark/sweep_all.py:1-49` 模块级 env 突变的完整诊断（**重构时必读**）
- `backend/benchmark/sweep_all.py:261-270` `_isolated_env()`
- `backend/benchmark/known_failures.json` 空基线 = 健康状态
- `backend/test_report_gate_contract.py:28-42` **正确的隔离样板**
- `backend/benchmark/adapter.py` 薄边界（19 行，全仓最佳解耦点）
- `backend/benchmark/environment.py:49` `_assert_safe()`

**运维**
- `start.bat` / `stop.bat` / `docker-compose.yml`
- `frontend/vite.config.js:8` 代理到 10087
- `backend/app/config.py` 末尾（181-197）import 期应用 `llm_providers` 覆盖 —— **`.env` 模型配置静默 no-op 的根因**

---

## 附录 B · 未能核实项（诚实标注）

1. `frontend/dist/` 是否存在、生产如何部署（`main.py` 内无 `StaticFiles`/`mount`，前端只能 dev server）。
2. `docker-compose.yml` 的 5 个服务是否在真实使用中（`infrastructure.local.json` 的存在与 `test_infra_switcheroo.py` 的"静默落本地"语义暗示**可能从未启用**）。
3. `backend/storage/` 下 learn 3 本 / story 5 本均为真实生产书，未读取其正文内容（只读目录名）。
4. `llm_providers.json` 的 `model_capacity` 实际配置值未展开（只确认 key 存在）。
5. `test_rolling_acceptance_guard.py` 当前失败的具体原因未定位（sweep 只记录 verdict，未展开输出）。
