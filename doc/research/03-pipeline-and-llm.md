# 03 · 编排 / 状态机 / LLM 客户端 / 成本 · 调研报告

> **范围**：`app/pipeline.py`、`app/main.py`、`app/llm/client.py`、`app/llm_providers.py`、`app/llm_stats.py`、
> `app/model_settings.py`、`app/offpeak.py`、`app/job_queue.py`、`app/runctx.py`、`app/abort.py`、
> `app/safe_guard.py`、`app/observability.py`、`app/trace_service.py`、`app/trace_config.py`、
> `app/rolling_story_pipeline.py`、`app/continue_story.py`、`app/recovery_orchestrator.py`、
> `app/commit_recovery.py`、`backend/llm_providers.json`、`backend/benchmark/**`。
> **方法**：AST 全量抽取 + ripgrep + 只读 SQLite 查询真实 trace 数据（`generation_attempts` 1003 行）。
> **`story_llm` 全程只读**，未创建/修改/删除任何文件。
>
> **行数口径修正（重要）**：任务书给的行数由 PowerShell `Measure-Object -Line` 得出，**它会丢弃空行**。
> 按实际行数（`Get-Content | .Count`）：`pipeline.py` **8606 行**（非 7930）、`main.py` **4629 行**（非 4072）、
> `llm/client.py` **1503 行**（非 1366）。本报告一律使用**真实行号**，可直接跳转。

---

## 1. pipeline.py 全图

### 1.1 规模与分组统计

| 指标 | 值 |
|---|---|
| 真实行数 | **8606** |
| 顶层 `def`/`class` | **156** |
| 含嵌套函数总数 | **178** |
| 最大单函数 | `_commit_chapter` **7122–8360，1239 行** |
| 次大 | `_outline_body` 1910–2434（525 行）、`_finalize_outline` 1495–1779（285 行）、`_prepare_chapter` 6334–6615（282 行）、`_produce_serial` 4353–4633（281 行） |

按职责分组的行数（函数体行数含嵌套，合计 8015 / 8606，其余为模块级常量、import、注释块）：

| 组 | 函数数 | 行数 | 占比 |
|---|---:|---:|---:|
| **C 规划 / 大纲生成** | 28 | **2033** | 23.6% |
| **G 记忆提交 / 入账** | 6 | **1483** | 17.2% |
| **F 章节生成主链** | 12 | **1270** | 14.8% |
| **I 校验 / 审计** | 13 | 702 | 8.2% |
| **J 重写 / 补卡 / 体检** | 10 | 475 | 5.5% |
| **E 预生成流水线 / 并发** | 11 | 442 | 5.1% |
| **H 审校 / 重写 / 风格** | 8 | 296 | 3.4% |
| **D 细纲就绪闸门** | 9 | 257 | 3.0% |
| **B 进度 / 步骤时间线** | 11 | 232 | 2.7% |
| **M 共享杂项 / 模型解析** | 14 | 209 | 2.4% |
| **K learn-only** | 9 | 188 | 2.2% |
| **L story-only** | 8 | 181 | 2.1% |
| **A 取消 / 生命周期 / 并发闸门** | 12 | 178 | 2.1% |
| **N 恢复 / 续接** | 3 | 69 | 0.8% |

**story / learn / shared 判定**：learn-only ≈ **188 行（2.2%）**、story-only ≈ **181 行（2.1%）**，
**其余 ~7600 行（95%）是共享主干**。也就是说 `pipeline.py` 名义上是「双模式流水线」，
实际上分流极小（`_flow(book)` 门面 + 少数 `_story_*`），
**重写时可以直接把 story 当作唯一主干，learn 降级为一个可选插件面**。

### 1.2 全量函数地图（行号 · 行数 · 一句话职责）

#### A 组 · 取消 / 生命周期 / 并发闸门（178 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 60 | 8 | `request_pause` | 置 `pause` 标志；**不**调 `abort.arm()`，让当前章自然写完 |
| 70 | 9 | `request_stop` | 置 `stop` 标志 + `abort.arm()`，token 流内可中断 |
| 82 | 4 | `cancel_state` | 读 pending/applied 标记 |
| 88 | 20 | `_check_cancel` | **边界检查**：消费标志并转 `_cancel_applied`，不改 phase |
| 110 | 46 | `_apply_cancel_phase` | **工作线程真正退出时**才把 pause/stop 落到 `book.phase` |
| 538 | 3 | `is_running` | 该书是否在跑 |
| 543 | 11 | `_spawn_once` | 原子认领 + 起唯一后台线程（杜绝双击双线程） |
| 556 | 23 | `_claimed_run` | 线程体包装：`runctx.book_id` 注入 + 异常兜底 + `_running.discard` |
| 581 | 4 | `active_regen` | 正在重写的章 id |
| 587 | 18 | `claim_regen` | 单章重写原子认领 |
| 607 | 6 | `release_regen` | 释放认领 |
| 615 | 26 | `recover_orphans` | 启动时把无存活线程的活跃书标 `interrupted`，`writing/reviewing` 回 `pending` |

模块级并发原语：`_running` / `_running_lock`（45–46）、`_regen_active`（49）、`_cancel_flags` / `_cancel_applied`（53–54）、
`_chapter_slots = Semaphore(MAX_TOTAL_WORKERS)`（**158**）、`_pregen_threads` / `_pregen_lock` / `_pregen_stop`（2438–2440）、
`_health_lock`（4686）。

#### B 组 · 进度 / 步骤时间线（232 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 890 | 3 | `_norm_step` | 步骤去数字后缀（`已生成N字`） |
| 905 | 14 | `_step_identity` | 去重身份；**两套口径**（裁剪用 `stage`，节流用 `text`） |
| 921 | 5 | `_step_sort_key` | 按 ts 排序 |
| 928 | 54 | `_trim_steps` | 裁剪 `steps_json`：最近 120 条 + 阶段里程碑跨窗口永久保留 |
| 984 | 32 | `_log_step` | 写步骤时间线（含节流） |
| 1018 | 7 | `_steps_payload` | 读原始时间线 |
| 1027 | 18 | `milestone_steps` | `(kind, stage) → 最近里程碑` |
| 1047 | 29 | `stage_milestone_ts` | 阶段里程碑时间戳 |
| 1204 | 17 | `_thread_log` | 线程安全日志（独立 session） |
| 1223 | 33 | `class StepReporter` | LLM 流式字数进度节流写库（≥5s 一条） |
| 4296 | 21 | `_serial_percent` | `done/total → [30,90]`，**分母必须同口径** |
| 4319 | 32 | `_book_chapter_percent` | 全书章级进度 |

进度权重常量 `WEIGHTS`（**876–883**）：`map_ready=5, awaiting_confirm=10, bible_done=18, first_chapter_done=30, serializing_end=90, completed=100`。

#### C 组 · 规划 / 大纲生成（2033 行 — 最大的一块）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 1262 | 3 | `start_planning` | 原子认领规划线程 |
| 1279 | 51 | `_seed_outline_skeleton` | 骨架章节播种进 `book.outline["chapters"]`（Rolling 专用，幂等） |
| 1332 | 19 | `_globalize_segment_chapters` | 段内章号 → 全卷章号 |
| 1353 | 21 | `_next_global_idx` | 该卷下一个可用全局章号 |
| 1376 | 44 | `_carry_forward_outline_plot_fields` | 增量覆盖时保留已持久化的 plot 字段 |
| 1422 | 57 | `_merge_outline_progress` | 细纲进度增量合并（**内含 commit**） |
| 1481 | 12 | `_detail_for_new_books` | 新书是否要细纲（默认 True） |
| 1495 | **285** | `_finalize_outline` | 大纲就绪统一落库收尾（正常 select + 快照外科恢复共用） |
| 1782 | 126 | `select_and_estimate` | 后台跑架构师 + 预估报告 → `phase=awaiting_confirm` |
| 1910 | **525** | `_outline_body` | **大纲任务体**：按卷生成 + 落库；内嵌 `_on_batch_done`/`_run_outline`/`_pause_outline`/`_make_detail_agent` 等 8 个闭包 |
| 3216 | 210 | `_fill_outline_details` | 存量书补细纲工作体 |
| 3428 | 6 | `start_fill_outline_details` | 补细纲入口 |
| 3436 | 3 | `start_select` | 启动架构+预估线程 |
| 3456 | 19 | `_outline_snapshot_planning_incomplete` | 大纲断点快照是否存在且规划未完 |
| 3477 | 29 | `_reuse_prev_outline` | 同书快捷复用完整大纲 |
| 3508 | 13 | `_outline_usable` | 大纲是否含章节列表 |
| 3590 | 12 | `_lookup_research_cache` | **永久缓存无 TTL**，命中累加计数 |
| 3604 | 4 | `_save_research_cache` | 写研究缓存 |
| 3610 | 35 | `_reuse_assets_for` | 复用键 → 已完成书的大纲/圣经（直接读 DB） |
| 3647 | 10 | `_safe_int` | 安全解析 int（LLM 可能填文本） |
| 3659 | 61 | `_plan_story` | 纯小说规划：跳守门/联网/知识地图，故事导演出剧情线 |
| 3722 | 164 | `_plan` | 学习型规划主链（研究→知识地图→画像） |
| 3888 | 6 | `_init_book_ledgers` | 账本初始化（幂等） |
| 3896 | 221 | `_ensure_bible` | 世界观圣经生成链（缓存→生成→审校→落盘） |
| 3172 | 3 | `confirm_bible` | 圣经确认闸门 |
| 3177 | 37 | `outline_detail_missing` | 细纲覆盖统计 |
| 643 | 50 | `migrate_story_total_volumes` | 存量 story 书卷数补齐（幂等） |
| 8422 | 7 | `_flat_points` | 扁平化知识点 |

#### D 组 · 细纲就绪闸门（257 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 2450 | 35 | `_book_wants_detail` | **本书是否要细纲**（写前闸门口径唯一真源） |
| 2487 | 30 | `_note_detail_bootstrap_empty` | 「声明要细纲却 0 细纲」一次性留痕 |
| 2519 | 15 | `_detail_ok` | 单章细纲就绪 |
| 2536 | 18 | `_neighbor_detail_ok` | N±1 就绪 |
| 2556 | 20 | `_readiness_gate` | **统一的「本章可写」判定入口** |
| 2578 | 44 | `_run_detail_lint` | 全书细纲确定性 lint |
| 2624 | 81 | `_serial_detail_gate` | 正文阶段细纲闸门 |
| 2707 | 7 | `_ready_block_note` | 读写「为何停在等待」 |
| 2716 | 7 | `readiness_block_reason` | 公开访问器 |

#### E 组 · 预生成流水线 / 并发（442 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 2725 | 3 | `_pregen_running` | 流水线是否在跑 |
| 2730 | 80 | `_pregen_pick_next` | 严格 `(volume, idx)` 取下一章 |
| 2816 | 28 | `_model_concurrency_for` | 本书模型最大并发；**无法判定返回 `None`** |
| 2846 | 19 | `_book_parallel_stages` | **纯标志读取**（刻意不含模型能力） |
| 2867 | 40 | `_pregen_parallel_allowed` | 开关 **AND** 模型能力 |
| 2909 | 30 | `_kick_pregen` | 幂等启动预生成流水线 |
| 2941 | 81 | `_rolling_detail_batch_done` | Rolling：先 merge+commit 再唤醒正文（顺序硬要求） |
| 3024 | 35 | `_load_filled_chapters_before` | Rolling 跨段继承源 |
| 3061 | 18 | `_stop_rolling_coord` | 安全停止 Rolling 协调器 |
| 3081 | 89 | `_pregen_pipeline` | 大纲期连续预生成流水线主体 |
| 4275 | 19 | `_effective_writer_batch_size` | 正文实际批大小 |

#### F 组 · 章节生成主链（1270 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 4119 | 150 | `_produce_first` | 阶段1：圣经 + 首个未完成章（串行必审）→ 自动转连载 |
| 4353 | 281 | `_produce_serial` | 阶段2：剩余章串行 → 末2章必审 → 账本核销 |
| 5894 | 118 | `_produce_volume` | 续写卷：卷首必审 → 串行 → 末2章必审 |
| 5670 | 202 | `_next_volume_inline` | 自动续卷核心（当前 session/thread 内执行） |
| 5874 | 18 | `_next_volume` | 手动续卷（统一走 inline） |
| 6282 | 26 | `_write_one` | 单章写入包装 |
| 8363 | 58 | `_write_one_inner` | 带 trace span 的单章写入 |
| 6222 | 58 | `_write_chapter_strict` | 章级重试链（attempts=3） |
| 6310 | 22 | `bridge_for_chapter` | 断点补写/过渡章双向桥接判定 |
| 6334 | **282** | `_prepare_chapter` | **写作前置**：状态/复习/五层记忆上下文 + 全员展开 + 桥接 |
| 6617 | 19 | `_generate_writer_content` | 整章 Writer 调用（经 Omen Harvest 闸门） |
| 6638 | 36 | `_draft_parallel` | 并行起草工作体 |

#### G 组 · 记忆提交 / 入账（1483 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 7122 | **1239** | `_commit_chapter` | **按序入账**：零成本检查 → 审校（不过则定向重写）→ 定稿后单次记忆提炼 → 落库导出 |
| 6774 | 23 | `_consume_parallel_extraction` | 取回审校并行的记忆提炼结果（正文被改写则丢弃） |
| 6676 | 96 | `_local_rewrite_duplicates` | 只改写雷同句，失败返回 None 由调用方降级 |
| 505 | 29 | `_ensure_fs_rows` | 落库时补登记缺失伏笔行 |
| 5602 | 66 | `_finalize_book` | 账本核销与终态判定 |
| 5456 | 30 | `_force_recover_fs` | 强制回收未回收伏笔并入末章 |

`_commit_chapter` 是最重的单点：**一个函数承担了「质量闸门 + 审校循环 + 定向重写 + 风格去重 + 记忆提炼 + 知识卡 + 专名扫描 + 导出 + 异步补卡」九件事**，
内部还嵌了 `_style_dialogue_floor` 等闭包。这是重写时**第一个必须拆**的函数。

#### H 组 · 审校 / 重写 / 风格（296 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 6810 | 10 | `classify_issues` | D1 审校问题三分诊 `{hard, style, ignored}` |
| 6822 | 10 | `_review_escalation_cfg` | 读 `review_escalation_config`（热生效，读失败全禁用） |
| 6834 | 12 | `_log_review_problem` | 审校失败必须落 `problems.jsonl` |
| 6848 | 39 | `_review_issue_contract` | D3：Editor 返回空 issues 而 notes 有结论 → 契约违规可见 + best-effort 派生 |
| 6889 | 60 | `_targeted_rewrite_once` | 用既有定向重写通道跑一次 |
| 6951 | 14 | `_optional_module` | 惰性导入可选模块（缺失返回 None，绝不抛） |
| 6967 | 28 | `_prose_style_flags` | `prose_style` 的 `(enabled, shadow)`；**模块/接口缺失一律 no-op** |
| 6997 | 123 | `_apply_style_dedupe` | D4 确定性重复压缩接线（shadow 优先） |

#### I 组 · 校验 / 审计（702 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 4640 | 11 | `_load_verify_results` | 读校验结果 |
| 4653 | 17 | `_append_verify_result` | 环形保留最近 30 次 |
| 4726 | 24 | `is_bad_chapter` | 超短/占位符检测 |
| 4752 | 28 | `_verifier_states` | 构造校验上下文人物状态（死亡角色优先） |
| 4782 | 17 | `_entities_first_ch` | 专名首现章号映射 |
| 4801 | 25 | `_format_relationships` | 格式化关系表 |
| 4828 | 28 | `_maybe_update_summary` | 批边界增量更新分层摘要 |
| 4858 | **156** | `_maybe_run_verify` | 每 N 章全书级校验（专名/漂移 + 逻辑） |
| 5026 | 15 | `_should_logic_chapter_check` | 单章逻辑检测触发判定 |
| 5043 | 93 | `_maybe_logic_chapter_check` | 定稿后单章即时逻辑校验（只读不改文） |
| 5138 | 107 | `_run_manual_verify` | 手动全书校验 |
| 5247 | 81 | `_run_volume_audit` | 卷末扩展审计 |
| 5330 | 100 | `_mid_volume_outline_correction` | 卷中动态大纲修正 |

#### J 组 · 重写 / 补卡 / 体检（475 行）

| 行号 | 行数 | 函数 | 职责 |
|---|---:|---|---|
| 6014 | 29 | `_regen_cards` | 仅补知识卡（受全局闸门约束） |
| 6045 | 36 | `backfill_missing_cards` | 后台逐章补卡 |
| 6083 | 137 | `_regen_chapter` | 手动重写单章（桥接 + 完整守卫链 + 失败恢复） |
| 697 | 46 | `scan_health` | 健康扫描（超短/占位符） |
| 745 | 13 | `repair_health` | 加入健康修复队列 |
| 760 | 19 | `ensure_health_worker` | 崩溃恢复 `running→pending` |
| 781 | 75 | `_health_worker_loop` | 修复 worker：一次一书、书内按 idx 串行 |
| 858 | 17 | `health_repair_status` | 队列状态（以 SQLite 为准） |
| 8515 | 92 | `_verify_selected` | 后置核验：仅对勾选点并发核验 |
| 5496 | 11 | `_verify_selected_safe` | 勾选点并发核验（learn 用） |

#### K 组 · learn-only（188 行）

`_fast_guard_allowed`(5491) · `_maybe_expand_knowledge_bg`(5509) · `_warn_untaught_points`(5526) ·
`_dynamic_deepen_knowledge`(5532) · `_load_untaught_points`(5558) · `_pick_due_reviews`(5569) ·
`_warn_consecutive_no_points`(5582) · `_deepen_knowledge`(8431) · `_expand_knowledge_bg`(5432)

> 全部通过 `_flow(book)` 门面分派；story 模式下多数返回空/False（见各函数 docstring）。

#### L 组 · story-only（181 行）

`_story_brief`(306) · `_story_flow_card`(316) · `_story_clear_points`(325) · `_story_init_genre_state`(333) ·
`_story_reinit_genre_state`(347) · `_story_beat_boost`(356) · `_story_plan_volume`(380) · `_story_craft`(491)

#### M 组 · 共享杂项 / 模型解析（209 行）

`_bm`(161 解析本书专属模型) · `_role_model`(183 role 级 Harvest 模型) · `_protagonist_name`(205) ·
`_is_story`(216) · `_flow`(223 **learn/story 门面**) · `_styles_for_beat`(243) · `_style_for_beat`(280) ·
`_style_author`(294) · `_derive_protagonist`(1115) · `_trusted_names`(1127) · `_check_outline_protagonist`(1135) ·
`_sanitize_outline_names`(1176) · `_continue_info`(4697) · `_effective_scale_for`(4707)

#### N 组 · 恢复 / 续接（69 行）

`spawn_resume`(3451) · `_resume_worker`(3523) · `continue_volume`(3446)

---

## 2. 状态机与恢复

### 2.1 Book.phase 状态枚举

`Book.phase` 是**一个无约束的 TEXT 列**（`models.py:46`，default `"planning"`），
没有枚举、没有 CHECK 约束、没有集中定义。全仓 `\.phase\s*=` 赋值点共 **43 处**（pipeline 40 + main 3 + continue_story 2 + bible_rebuild 1）。

| phase | 含义 | 进入点（file:line） |
|---|---|---|
| `planning` | 规划/研究阶段 | `main.py:790,820`；`pipeline.py:3729,4153` |
| `profile_ready` | 宽泛主题待选画像 | `main.py:837`；`pipeline.py:3818` |
| `map_ready` | 学习地图就绪 | `pipeline.py:3714,3866` |
| `rejected` | 守门拒绝 | `pipeline.py:3754,3872` |
| `architecting` | 架构师出大纲中 | `pipeline.py:2009` |
| `awaiting_bible` | 待确认圣经 | `pipeline.py:1885`；`continue_story.py:821` |
| `awaiting_confirm` | 大纲+预估就绪待确认 | `pipeline.py:1763` |
| `writing_first` | 阶段1：写首章 | `pipeline.py:4158,5921` |
| `serializing` | 阶段2：连载中 | `pipeline.py:4238,4367,5933`；`continue_story.py:946` |
| `finalizing` | 末2章必审 + 账本核销 | `pipeline.py:4613,5990` |
| `volume_done` | 本卷完结等续卷 | `pipeline.py:5915` |
| `completed` | 全书完成 | `pipeline.py:5665` |
| `paused` | 用户暂停 | `pipeline.py:119,2312` |
| `interrupted` | 中断（服务重启/停止） | `pipeline.py:152,625,1753,1897,2393,2419,2425,4411,4574,5636,5864,5951` |
| `failed` | 失败 | `pipeline.py:1901,2428,3876,3881,4251,4627,5614,5791,5868,6007`；`bible_rebuild.py:78` |

**问题**：`"architecting"` 只在 `pipeline.py:2009` 赋值一次，但 `main.py:861` 用它做分支判断 —— 这是个**只写一次读多次**的状态。
`ACTIVE_PHASES`（`pipeline.py:535`）= `("planning","writing_first","serializing","finalizing")`，
**不包含 `architecting`** —— 也就是说**服务在架构师阶段重启时，`recover_orphans` 不会把它标为 `interrupted`**（这是一个真实的孤儿漏洞）。

### 2.2 Chapter.status 状态枚举

`models.py:122` 注释即定义：`pending/writing/reviewing/done/failed`（default `pending`）。

| 转移 | 触发点 |
|---|---|
| `* → pending`（回退） | `pipeline.py:636,821,3130,3138,3157,4198,4209,4218,4493,4554,4565,5925,6241,6254,6263,7161,7174,7371,7767` |
| `pending → writing` | `pipeline.py:6336`（`_prepare_chapter` 开头） |
| `writing → reviewing` | `pipeline.py:7178`（过最终质量闸门后）；`7404` |
| `reviewing → pending` | `pipeline.py:7767`（审校要求重写） |
| `reviewing → done` | `pipeline.py:8336` |
| `→ failed` | `pipeline.py:8097–8098`（revision/manifest 失败） |

**关键不变量**：`content_path` 与 `status` **必须同时回退**。代码里每一处 `status = "pending"` 几乎都紧跟 `content_path = ""`
（`631:637`、`7175`、`3131/3139/3158`、`4199/4210/4219`…）—— 这是一条**靠约定维持、没有封装**的不变量。
任何新增回退点漏掉 `content_path` 清空，就会让 `done` 判定读到旧正文/占位符。这是重写时必须用
**单一状态转移函数**（如 `chapter.transition_to_pending()`）封装的第一个点。

### 2.3 双轨状态机（这是复杂度的真正来源）

系统实际上是**两套并行状态机**：

```
轨 A（大纲轨）: planning → architecting → awaiting_bible → awaiting_confirm →（可继续补细纲）
轨 B（正文轨）: writing_first → serializing → finalizing → completed / volume_done
```

两轨**可以重叠**（`parallel_stages=True` 时）：大纲还在补细纲，正文已经在写就绪的章
（`_pregen_pipeline`，`pipeline.py:3081`）。这就是「Serial Inside, Parallel Across Stages」的落地形态：

```
        ┌── 轨 A: 骨架 → Detail(细纲) 批次 ──┐
        │        (architect 并行批)          │
        └────────────┬───────────────────────┘
                     │ _merge_outline_progress + commit（顺序硬要求）
                     ▼
              _kick_pregen ──► _pregen_pipeline（单线程，一次一章，严格 (volume,idx)）
                     │
                     ▼
        ┌── 轨 B: Writer → Editor → Memory → done ──┐
        │        （章内严格串行，账本零竞态）        │
        └────────────────────────────────────────────┘
```

### 2.4 进度持久化与恢复

**持久化介质**（4 处，互不同步）：

1. **`books.phase` + `chapters.status`（SQLite，权威）** —— 恢复的唯一真源；
2. **`books.steps_json`**（`models.py:61`）—— 步骤时间线，裁剪到 `_STEPS_TARGET = 132`（`pipeline.py:897`）；
3. **磁盘断点快照**（`docstore`）：`_outline_partial.json`、`_vol_partial_{vol}.json`、`_continue_outline_partial.json`；
4. **磁盘续推存档**（`storage/resume/{call_key}.json`，`client.py:21–45`）—— 按 `sha1(system+user)[:24]` 存思考存档。

**恢复入口**：`spawn_resume`(3451) → `_resume_worker`(3523)。路由规则（3523–3585）：

```python
# pipeline.py:3535   只接受这三种 phase
if book.phase not in ("interrupted", "failed", "paused"): return
# pipeline.py:3566   ① 卷规划断点快照优先
cand = f"_vol_partial_{vol}.json"  →  _next_volume_inline(...)
# pipeline.py:3580   ② 否则按大纲/圣经状态路由
if not outline_ok:      _plan(book_id)
elif not has_bible or undone == 0:  _produce_first(book_id)
else:                   _produce_serial(book_id)
```

### 2.5 已知脆弱点（引用 file:line）

| # | 脆弱点 | 位置 | 说明 |
|---|---|---|---|
| 1 | **`architecting` 不在 `ACTIVE_PHASES`** | `pipeline.py:535` vs `2009` | 架构师阶段重启 → `recover_orphans` 不认它 → 书永远卡在 `architecting`，用户看不到「继续」按钮 |
| 2 | **孤儿检测依赖进程内 `_running` 集合** | `pipeline.py:45,538,624` | 多进程部署（uvicorn workers>1）时，A 进程的 `_running` 看不到 B 进程在跑的书 → 误标 `interrupted` |
| 3 | **cancel 标志是进程内的** | `pipeline.py:53–54` | 同上：换进程发的 pause 收不到 |
| 4 | **`_check_cancel` 与 `_apply_cancel_phase` 分离** | `pipeline.py:88` / `110` | 标志「已消费但未落 phase」的窗口期（`_cancel_applied`）里，`cancel_state()` 会返回非空、但 `book.phase` 还是 `serializing` → 前端显示的「状态」与真实不一致（`main.py:990–997` 就是为此打了补丁） |
| 5 | **`_running` 双用途** | `pipeline.py:602,612` | `claim_regen` 把「重写」也塞进 `_running`，于是 `is_running()` 无法区分「整书生成」与「单章重写」 |
| 6 | **恢复读 `book.bible` 列会清空** | `pipeline.py:3551` | `if not has_bible: book.bible = ""` —— 圣经在 `bible_path` 文件里，但恢复路径**主动清空列**，靠 `docstore.read_bible` 兜底（`memory.py:1616` 也有注释说明这个双源问题） |
| 7 | **`_vol_partial` 快照不校验内容** | `pipeline.py:3566–3578` | 只要文件存在就直接 `_next_volume_inline`，快照损坏 → 静默走错分支 |
| 8 | **`stage_milestone_ts` 无 meta 时回落文本解析** | `pipeline.py:1047` | 时间戳从 `action` 字符串里抠 —— 改文案即坏 |
| 9 | **`steps_json` 裁剪两套去重口径** | `pipeline.py:905–918` | 代码注释自己说「刻意用两套口径，别合并成一个，会误伤」—— 这是**已知的认知负担**，不是 bug |
| 10 | **`recover_orphans` 不清理 `job_queue`** | `pipeline.py:615` vs `job_queue.py:117` | 两条恢复路径（`recover_orphans` / `recover_running`）互不知情，`main.py` startup(96–217) 只调其中部分 |

---

## 3. 并发模型

### 3.1 线程而非 asyncio

**全项目零 `async def`**（`pipeline.py`、`main.py`、`llm/client.py` 均为纯同步），
并发全部靠 `threading.Thread` + `ThreadPoolExecutor` + `Semaphore`。FastAPI 端点本身是 `def`（同步），
由 Starlette 的 threadpool 承载。

**「Serial Inside, Parallel Across Stages」的确切含义**（从代码归纳，非文档原文）：

| 层 | 并发度 | 实现 |
|---|---|---|
| **章内** | **恒为 1** | `_prepare_chapter → Writer → Editor → Memory → done` 单线程串行；`_commit_chapter` 拥有 session，不跨线程 |
| **章间（同书正文）** | **恒为 1**（Rolling/关开关）或 **4**（quick_mode） | `_effective_writer_batch_size`（`pipeline.py:4275`） |
| **阶段间** | **2** | 大纲轨（architect 批）+ 正文轨（`_pregen_pipeline`）重叠 |
| **跨书** | **≤ `MAX_TOTAL_WORKERS`**（默认 4） | `_chapter_slots = Semaphore(...)`（`pipeline.py:158`） |

`_effective_writer_batch_size`（4275–4293）的判定顺序：

```python
if _flow(book).rolling_pipeline_enabled(book): return 1   # Rolling 强制 1
if not _book_parallel_stages(book):            return 1   # 关开关即 1
return _QUICK_BATCH_SIZE if quick else 1                  # _QUICK_BATCH_SIZE = 4 (pipeline.py:4272)
```

> 注意 `pipeline.py:4282` 的注释：**模型能力不在这里判** —— `client._conc_sem` 已按 `model_concurrency` 排队，
> 在这里再判一次会让测试/Mock 环境（无提供方 → 并发 1）莫名其妙全部变串行
> （对应 `test_outline_resume.py` 的两条用例）。这是**「两层判定必须分开」**的显式设计决策。

### 3.2 `parallel_stages` 三层闸门

```
① _book_parallel_stages(book)      纯标志读取，缺省 True        pipeline.py:2846
② _model_concurrency_for(book)     查模型能力，未知返回 None     pipeline.py:2816
③ _pregen_parallel_allowed(book)   ① AND (② is None or ②>1)    pipeline.py:2867
```

- ② 返回 `None`（无模型/MOCK/查询异常）→ **一律放行**，不降级、不提示（`2885`）；
  只有**查到且声明 ≤1** 才拦（`2882–2906`），并推送一次 `parallel_stages_degraded` 通知。
- ② 必须带 `base_url`：`model_capacity` 用 `"{host}:{model}"` 区分同模型多网关
  （官方 mimo 6 / 中转站 mimo 1）；只查纯键会看到默认 6 → **在只能串行的网关上误开并发**（`2823–2825` 记录了 2026-09-07 事故）。
- `_pregen_pipeline` **每章重读** `_pregen_parallel_allowed`（`pipeline.py:3110–3117`）——
  这是「边跑边关必须安全」的实现，注释明确要求**不得改成启动时一次性判定**。

### 3.3 `_spawn_once` / `_running_lock`

```python
# pipeline.py:543–553
def _spawn_once(book_id, target, *args) -> bool:
    with _running_lock:
        if book_id in _running: return False     # 锁内检查
        _running.add(book_id)                    # 锁内登记（原子认领）
    threading.Thread(target=_claimed_run, args=(book_id, target, args),
                     daemon=True).start()
    return True
```

`_claimed_run`（556–578）：注入 `runctx.book_id` contextvar → 调 target → 捕获 `GenerationAborted`
落 `_apply_cancel_phase` → `finally` 里 `_running.discard`。
**注意 `except` 只处理 `GenerationAborted`，其他异常被静默吞掉**（没有任何 `log_problem`）——
target 内部的异常由 `_write_chapter_strict` 等各自处理，但**线程顶层异常无留痕**，是一个观测盲区。

### 3.4 并发点清单（全部 Thread/Executor 启动点）

| 位置 | 并发 | 约束 |
|---|---|---|
| `pipeline.py:552` | `_spawn_once` 线程 | 每书唯一 |
| `pipeline.py:778` | 健康修复 worker | 全局唯一（`_health_worker_started`） |
| `pipeline.py:2932` | `pregen-{book_id}` 线程 | 每书唯一（`_pregen_threads`+`_pregen_lock`） |
| `pipeline.py:4458` | `ThreadPoolExecutor(max_workers=len(prepared))` 并行起草 | 仅 `quick_mode`，批内 4 章 |
| `pipeline.py:5523` | `_deferred_expand` 后台知识发散 | learn-ultra |
| `pipeline.py:6079` | `_regen_cards` 后台补卡 | 受 `_chapter_slots` 约束 |
| `pipeline.py:7385` | `ThreadPoolExecutor(max_workers=1)` PostProcessor | 单 worker |
| `pipeline.py:8355` | 异步补知识卡线程 | 章完成后 |
| `pipeline.py:8552` | `ThreadPoolExecutor` 勾选点核验 | learn |
| `rolling_story_pipeline.py:162` | Rolling Detail Worker | 队列 + 单 worker，**并发硬门槛恒为 1** |

`_chapter_slots` 的 acquire/release 成对点：`4947/4974`（verify）、`5102/5111`（logic check）、
`5196/5211`（manual verify）、`6016/6042`（regen cards）、`6284/6307`（write strict）。

### 3.5 并发下会坏的地方

1. **起草并行时上下文是串行预构建的** —— `pipeline.py:4440–4446`：
   ```python
   # —— 起草阶段：上下文构建串行读库；模型调用可并行（不触碰数据库）——
   for ch in batch:
       cd, ctx = _prepare_chapter(session, book, ch, bridge=ch_bridge)
   ```
   4 章并行起草时，4 份上下文按**同一时刻的账本**构建 → **同批后写的章看不到前章的记忆增量**。
   代码用「入账仍严格按序」(`4485`) 补救账本一致性，但**写作时的上下文是陈旧的**。

2. **`_pregen_pipeline` 与 `_produce_serial` 抢同一批章** ——
   `_produce_first` 用 `_pregen_stop` + `join(timeout=1800)`（`pipeline.py:4134–4140`）做交接。
   **join 超时 1800s 后会继续往下走**，此时 pregen 线程可能还在写同一章 → 双写。

3. **`_merge_outline_progress` 与 pregen 读 outline 的竞态** ——
   Rolling 路径靠「先 merge+commit 再 kick」（`3001–3002`）保证顺序，但 `_pregen_pipeline`
   在 `3113` 用 `session.expire(book)` 重读 —— 依赖 SQLAlchemy 的 expire 语义，脆弱。

4. **`_book_used_snippets` / `_book_last_style` 是无锁 dict**（`pipeline.py:287,292`）——
   并行起草时多线程写同 key（`6514–6517`）。

5. **跨书 `Semaphore` 与模型级 `Semaphore` 叠加** ——
   `_chapter_slots`（跨书）+ `client._conc_sem`（同模型）两层排队，**没有统一的公平性**，
   慢书会长时间占用 slot。

6. **`model_config` 热改并发不会影响在飞请求**（`llm_providers.py:200–201` 自述）——
   已在飞行中的请求持旧容量信号量。

---

## 4. LLM 客户端细节（`app/llm/client.py`，1503 行）

### 4.1 结构

模块级单例 `llm = LLMClient()`（**1503**）。类 `LLMClient`（113–1289），
模块级 JSON 修复工具箱（`_scan_balance` 1292、`_close_at` 1318、`_salvage_truncated` 1335、
`policy_block_message` 1355、`_escape_controls_in_strings` 1368、`_json_err_detail` 1416、`parse_json` 1427）。

### 4.2 Mock 降级

`_compute_mock`（140–151）：`llm_providers.has_usable_model()` 为假 → `mock=True`，
所有生成返回内置占位数据并打印醒目警告（154–166）。
**注意**：`.env` 里的 Key 不再算「已配置」，唯一来源是模型页激活（`main.py:103`）。

### 4.3 重试与退避（`chat`，183–581）

主循环 `while True:`（299），每轮：

| 机制 | 实现 | 行号 |
|---|---|---|
| 候选源 | `llm_providers.rotation_candidates(model, locked_pid)` → `pool:` / `prov:` 前缀 | 229–255 |
| 源轮换 | `avail[attempt % len(avail)]`，跳过熔断源 | 305–309 |
| 熔断 | `cooldown_until[src]`；全熔断则清空重来 | 296–297, 306–308 |
| 基础退避 | `delay = min(60, 5 * 2**min(attempt,4))` | 458 |
| 日配额 429 | `cooldown_until = now + (reset_ts or 6h)`，`delay = min(delay,5)` | 467–470 |
| 连续 429 + reset_ts | gap≤120s → 按服务器指示等；否则熔断至 reset | 471–478 |
| `Retry-After` | `wait = min(120, max(delay, retry_after))` | 479–482 |
| **无指示 429** | **首次等 30s，之后 `min(600, 90*2^(n-2))`，同源续试不换模型** | 483–491 |
| 空输出快重试 | `not text and not err_s` → `delay = 1` | 495–496 |
| 429 不计预算 | `budget_extra += delay`（**唯一收口**，BUG P1-8 修复） | 561–562 |
| 长等待分段播报 | `delay>25 and 429` → 每 20s 报倒计时 | 565–576 |

**致命错误短路**（431–434）：`401/403/Insufficient balance/CreditsError/invalid_api_key` → 立即放弃不烧预算。
**重试上限**：`LLM_MAX_ATTEMPTS`（默认 **0 = 不启用**，`_llm_max_attempts` 100–110）——
注释给出证据：5 份 live artifact 的 123 条 attempt 里 `attempt_no` 分布 `{1:120, 2:3}`，**重试几乎从不发生**。
无 `deadline_s` 时：`attempt >= (8 if is_429 else 3)` 就 break（534–542）。

### 4.4 预算耗尽（504–517）

```python
if (deadline_s is not None
    and elapsed >= deadline_s + budget_extra
    and attempt >= settings.LLM_MIN_BUDGET_ATTEMPTS):   # 默认 2
    raise RuntimeError("⏱ 挂机重连预算耗尽…转入断点恢复: …")
```
`deadline_s` 是 book17 事故后刻意加的现场恢复语义（注释 520 明确「**不缩短 deadline**」）。

### 4.5 流式读取与 idle 看门狗（`_stream`，1064–1187）

| 机制 | 公式/常量 | 行号 |
|---|---|---|
| 总时长预算 | `budget = deadline_s or LLM_TIMEOUT(300) or 600` | 1082 |
| **静默 idle 上限** | `idle = min(60.0, max(20.0, budget/6))` | **1088** |
| 总时长硬截断 | 每 chunk 检查 `now - stream_start > budget` → `TimeoutError` | 1116–1117 |
| 静默截断 | `now - last_data > idle` → `TimeoutError`（空包不更新 `last_data`） | 1120–1121 |
| 取消检查 | 每 chunk 调 `abort.requested()` → `GenerationAborted` | 1100–1104 |
| reasoning 兼容 | `delta.reasoning` 或 `delta.reasoning_content` | 1126 |
| `reasoning_details` | 累积供 OpenRouter 续推 | 1135–1137 |
| usage 采集 | 每 chunk 覆盖 `self._last_usage` | 1106–1114 |
| 流中断有产出 | 保留部分产出 + `_last_stream_truncated = True` + 落 problems.jsonl | 1170–1186 |

> **关键设计**：**无 `on_progress` 的调用也走流式**（867–873）——否则单个非流式调用会阻塞在等完整响应上，
> httpx 只卡「收包间隔」、deadline 只在重试循环之间检查 → **无限挂死**。

### 4.6 断点续推（reasoning resume + 磁盘存档）

| 组件 | 说明 | 行号 |
|---|---|---|
| `call_key` | `sha1(system + "\x00" + user)[:24]` | 259 |
| 内存 stash | `_resume_stash`，**LRU 上限 5** | 131, 453–454 |
| 磁盘存档 | `storage/resume/{call_key}.json` | 13–45 |
| 触发条件 | `(details or len(rtext)>1500 or len(text)>200)` | 440–444 |
| 续推能力 | `_resume_capable`：**仅 OpenRouter 兼容通道**；`RESUME_REASONING=1`；`zen:` 前缀不支持 | 594–610 |
| 多轮组装 | system + user + assistant(思考+正文) + user("你上一次的响应被中断了…") | 757–770 |
| 合并 | `_merge_continued`：最长重叠 ≥40 字才合并，**否则返回 None 绝不拼接**（BUG P1-1） | 612–639 |
| 降级 | provider 不认 `reasoning_details` → 进程级 `_resume_enabled = False` 单轮重发 | 852–855 |

**注意**：续推**改变 messages 结构**（2 条 → 4 条），因此**续推轮次与首次调用的 prompt 前缀不同** —— 这直接打击缓存命中（见 §5）。

### 4.7 reasoning-token 处理（**`max_tokens = max(4096, reasoning*4)`**）

`_chat_openai_locked._create`（780–828）：

```python
# llm/client.py:802–816
if reasoning_max_tokens:
    extra["extra_body"] = {"reasoning": {"max_tokens": reasoning_max_tokens}}
    # 部分 OpenAI 兼容网关会忽略 extra_body.reasoning.max_tokens，且把思考与正文共用 max_tokens。
    # 对显式思考控制的调用补一个总输出上限…按 4x 留出正文空间。
    extra["max_tokens"] = max(4096, int(reasoning_max_tokens) * 4)
if eff:
    extra.setdefault("extra_body", {})["reasoning_effort"] = eff
```

**三条硬约束（注释 800–816 明确要求不得回退）**：

1. `max_tokens` 的 4x **只取决于调用方是否显式要思考，不得依赖 `eff`** ——
   `eff` 是「要不要给网关发 `reasoning_effort`」（对不认的网关会 400，必须查 `model_capacity`）；
   而「给思考留多少输出预算」是**本地算术**，与注册表无关。
   此前两者写在同一个 `if eff:` 块里 → **任何没登记 `reasoning_effort` 的思考模型都会静默丢掉这层保护**。
2. **实测代价**：`qwen3.8-flash`（生产 fast 档默认模型，未登记）reasoning 占 completion **91%（6139/6746）**，
   JSON 中途截断、解析失败。
3. **思考强度三层解析**（789–801）：agent 显式 > `model_capacity.reasoning_effort`（带 base_url）> 不传。

**思考占满输出的降级**（543–547）：`not text and _last_reasoning > 1500 and rmt_current and not resume_ctx`
→ `rmt_current = None`，下一轮取消思考上限。

**微产出判废**（408–412）：`len(text.strip()) < 20 and _last_reasoning > 3000` → 判废重试，
并计数 `_MICRO_OUTPUT_DISCARDS`（74–97）。注释（70–73）自陈：**「已烧掉的思考 token 照常计费」**。

### 4.8 模型容量 / 并发解析

| 函数 | 位置 | 说明 |
|---|---|---|
| `get_model_capacity(model, base_url)` | `llm_providers.py:116` | **合并语义：纯键打底，host 键覆盖**（host 是细化不是替换） |
| `outline_batch_for_model` | `llm_providers.py:149` | `batch = max(10, min(max_out*0.5/150, 150))` |
| `model_concurrency(model, base_url)` | `llm_providers.py:394` | 默认 **6** |
| `model_min_interval(model, base_url)` | `llm_providers.py:455` | 请求间距节流 |
| `set_model_capacity` | `llm_providers.py:191` | 写 JSON；`_invalidate_concurrency_cache`（171）清 client 信号量 |

`client._conc_sem`（949–965）：按 `"{base_url}|{model}"` 懒建 `Semaphore(max(1, min(64, concurrency)))`。
`_conc_acquire`（967–978）用 thread-local `held` 标志**防止续推降级递归时重复持有**。
`_pace`（924–947）：`min_interval_s` 节流，**每次调用都读盘**（热生效），并「占位推进」防并发线程同时通过。

### 4.9 `verified_models`

`llm_providers.py`：
- `provider_declares_model`（227–245）：**三源取并集** —— ① `models` 登记列表 ② `verified_models` ③ `_FREE_MODEL_PATCH` 免费补全清单。
- `mark_verified`（485–503）：`/api/llm/test` 测试成功 → 持久化模型粒度 verified 标记（`main.py:360`）。
- `runtime_status`（317–）：候选模型 = **测试连接成功过的模型**；激活方若一个都没测则回退展示全部。
- `rotation_candidates`（248–308）：**只换网关不换模型**；`locked_provider_id` 非空时锁定来源不轮换
  （官方 mimo 与中转站 mimo 不混）。**2026-09-07 修复（勿回退）**：激活提供方只有在其**确实声明了该模型**时才进候选（256–260）。

### 4.10 JSON 修复链（`parse_json`，1427–1500）

顺序**不可调换**（注释 1458 明确）：

1. `policy_block_message`（1430）—— 供应商安全策略拦截，抛友好文案；
2. 剥 markdown 围栏（含未闭合围栏，1434–1438）；
3. `_try`：裸解析 → 首尾大括号切片（1440–1452）；
4. **语法抢救①（必须在截断抢救之前）**：`_escape_controls_in_strings` —— 字符串内裸控制字符转义。
   **为什么顺序关键**：缺陷若落在靠前字段（如 `summary`），截断抢救的所有候选点都在缺陷之后 → 必然全失败，
   症状是「无论输出多长都报不合法」（1371–1378 有实测复现：注入 1 个裸换行 → 解析+抢救双失败；转义后 50 章全成功）。
   **不标 `_repaired`**（内容完整，只是序列化非法）；
5. **抢救②**：`_salvage_truncated` —— 最近 20 个闭合点从新到旧 + 末尾兜底，逐个截断补齐；成功标 `_repaired=True`；
6. 失败：`_json_err_detail` 给出**精确位置 + 上下文窗口**（1416–1424），并落 `problems.jsonl`。

`from ..safe_guard import log_problem` 的**两个点**是刻意强调的（1466–1470）：
写成 `from .safe_guard` 会 `ImportError` 被 `except Exception: pass` 吞掉 → 诊断日志静默消失
（2026-09-13 实际踩过：JSON 反复失败却一条日志都没有）。

### 4.11 `_record` 与 usage 契约（1235–1289）

```python
# client.py:1235–1252
def _native_completion_tokens(self) -> int | None:
    """绝不用「输出字数 × 0.8」之类估算值冒充 native
    （AGENTS.md trace usage contract：缺失保持 NULL、不得当作 0、
      Proxy Char 必须与 Native Token 分开）。"""
```
`_record`（1254–1289）写 `Metric` 表：`completion_tokens` **只写 native usage，缺失写 `sqlalchemy.null()`**
（1262–1265：`Metric.completion_tokens` 列默认值是 0，ORM 显式传 `None` 会被默认值覆盖成 0，**必须显式传 `null()`**）。
旧实现写 `int(len(text)*0.8)`，在同一列里与真值并存且无从区分（BUG P1-6）。

---

## 5. Token 预算与缓存命中（**重点**）

### 5.1 每次 LLM 调用的输入是怎么组装的

**Writer 调用**（唯一有完整上下文的一条链）：

```
pipeline._prepare_chapter(6334)
  ├── memory.build_context(session, book, chapter_data)   → memory.py:1143
  │     ├── bible            = docstore.read_bible() + L1卷 + L1前卷摘要   memory.py:1616–1620
  │     ├── bible            = bible_inject.select(...) 按本章剧情裁剪      memory.py:1624–1629
  │     ├── bible           += genre_state                               memory.py:1665–1666
  │     ├── timeline         = timeline[:6000]                            memory.py:1680
  │     ├── states           = "\n".join(l3_parts)                        memory.py:1681
  │     ├── events / rag_snippets / prev_prose / hang / volume_open
  │     ├── skeleton_guide / entity_registry / graph_context              memory.py:1631–1675
  │     └── return {...}                                                   memory.py:1677–1691
  ├── context["scene"] / ["flow"] / ["genre_profile"] / ["genre_blocks"]  pipeline.py:6452–6466
  ├── context["style_snippets"] = query_style(...)[:2]                    pipeline.py:6501–6518
  ├── context["flow_card"]                                                pipeline.py:6538
  └── context["bridge_prev"] / ["bridge_next"]                            pipeline.py:6573–6609

writer.WriterAgent._build_prompt(context, chapter, tpl)   → agents/writer.py:409
  按【L0 世界观圣经】【防雷同提醒】【文风范本】【本卷开篇】/【现场必须承接】
     【L2 前情时间线】【L5 上一章结尾原文】【L3 人物状态表】【图谱关系快照】
     【已注册名词表】【断点补写任务】【L3 关键事件记忆】【L4 相关前文片段】
     【骨架指引】【伏笔任务-埋下/回收】【复习候选】… 顺序字符串拼接
```

**顺序含义（对缓存至关重要）**：`writer.py:411` 第一行就是 `user = f"【L0 世界观圣经】\n{context['bible']}\n\n"` ——
**圣经是 user prompt 的最前面**。其后是**逐章变化的**防雷同提醒、文风范本、桥接、时间线、状态、RAG…

### 5.2 有没有按上下文来源的 token 拆分？—— **有，但只在两个地方、且不完整**

| 位置 | 内容 | 状态 |
|---|---|---|
| **`pipeline.py:8374–8388`** | `_prep_span.metadata["context_breakdown"]`，**13 个 key**：`system_instruction, bible, chapter_outline, plot_detail, character_state, relationship, foreshadow, timeline, rag, previous_chapter, style_reference, learn_context, misc` | **仅当 `trace_config.enabled()` 为真时写入**（`8364–8369`）；当前 `trace_config.json` = `enabled: true` |
| `benchmark/omen_q1.py:225–238` | `_context_breakdown()`，**11 个 key**（无 `system_instruction`/`style_reference`，多 `instruction`） | 仅 benchmark |
| `benchmark/mock.py:129–143` | 13 key，与 pipeline 同形 | 仅 benchmark |

**关键缺陷**：
1. `context_breakdown` **是字符数，不是 token 数**。转 token 只有两个粗糙做法：
   `benchmark/omen_q1.py:980` 用 `chars/4`；真实比例是 **1.448 chars/token**（本库实测，见 5.5），
   `chars/4` **低估约 2.76 倍**。
2. `system_instruction` 恒写 **0**（`8374`）—— system prompt 完全没计。
3. `relationship` 读的是 `context.get("relationships")`，但 `memory.build_context` 的返回 dict
   （`memory.py:1677–1691`）**没有 `relationships` 这个 key** → **恒为 0**。同理 `graph_context` 被并入 `misc`。
4. `_context_breakdown` 与 `context_breakdown` 是**两份手工维护的平行定义**，已经漂移。
5. `trace_service.summary`（104–116）读它、算 `context_coverage_proxy = sum(breakdown)/coverage_input_chars`，
   而 `phase1_trace_report.md:36` 实测该值 = **0.0076**（0.76%）—— 说明**覆盖率极低，当前归因基本不可用**。

### 5.3 Prompt caching：现有能力盘点

| 能力 | 是否存在 | 证据 |
|---|---|---|
| 读取 provider 的 `cached_tokens` | ✅ | `observability.py:100–102`：`pick("cached_tokens","cache_read_input_tokens")` + 嵌套 `prompt_tokens_details` |
| 落库 | ✅ | `generation_attempts.cached_tokens`（`observability.py:350`）；`models.py:430` |
| 聚合口径 | ✅ | `benchmark/cache_attribution.py:231`：**`sum(cached)/sum(prompt)`**，只统计 paired，缺失保持 NULL 不参与分母 |
| Credits 归因 | ✅ | `cache_attribution.py:58`：`CREDIT_WEIGHTS = {"hit":2,"miss":100,"output":200}`，缓存写入 0 |
| 夜间折算 | ✅ | `discount_for_attempt`（126）+ `offpeak.discount_factor` |
| **`cache_control`（显式打标）** | ❌ **完全没有** | 全仓 grep `cache_control` / `ephemeral` 零命中 |
| **`prompt_cache_key`** | ❌ | 零命中 |
| **稳定前缀排序** | ❌ | prompt 由 `writer.py:409+` 顺序拼接，**没有「稳定段在前、易变段在后」的设计** |
| 公共前缀诊断 | ⚠️ 有函数**无调用** | `cache_attribution.py:145/154/161`（`common_prefix_chars/ratio/analyze_prompt_pair`）**只被测试引用**（`test_cache_attribution.py`），**生产链路无集成、无 artifact 产物** |
| `cache_layout` 字段 | ⚠️ 恒为 `"legacy"` | `cache_attribution.py:341` 硬编码，**没有第二种布局存在** |
| 缓存写入记账 | ⚠️ 恒 0 | `CACHE_WRITE_CREDITS = 0`（59），注释说「限时免费，显式列出便于日后调整」 |

### 5.4 反复发送的上下文 / 可达缓存的位置

**每章都要重发的稳定内容**（按 `writer.py:411+` 的实际顺序）：

| 上下文块 | 稳定性 | 说明 |
|---|---|---|
| `bible`（L0 世界观圣经） | **全书稳定**（卷切换才变） | `memory.py:1616`，是 prompt **第一块**，天然最适合做缓存前缀 |
| `genre_state` / `genre_blocks` | 类型级稳定 | `memory.py:1665`、`pipeline.py:6460` |
| `entity_registry`（已注册名词表） | **单调增长**（只追加） | `memory.py:1672`，天然前缀友好 |
| `states`（L3 人物状态表） | **逐章小幅变化** | `memory.py:1681` |
| `timeline`（L2 前情，≤6000 字） | **滑动窗口**（追加+截断） | `memory.py:1680` |
| `rag_snippets`（L4） | **逐章完全不同** | 检索结果 |
| `prev_prose`（L5 上一章结尾） | 逐章不同 | |
| `style_snippets` | 逐章不同（且带近 32 章排除去重，`pipeline.py:291`） | |
| `hang` / `bridge_*` / 伏笔任务 | 逐章不同 | |

**结论：prompt 的实际排列是「稳定块（圣经/类型/名词表）在前 → 易变块（时间线/状态/RAG/上一章）在后」，
顺序本身是对的**，但有两个破坏点：

1. **`_prepare_chapter` 在 `bible` 前面没东西，可圣经内部被 `bible_inject.select(book.id, query, participants)` 按章裁剪**
   （`memory.py:1624–1629`）：`_bible_query = f"{plot} {title}"` —— **每章 query 都不同 → 选出的圣经块不同 → 前缀在第一章块就分叉**。
   这是**最大的缓存杀手**：为了省 bible 长度做的相关度裁剪，把全书最稳的缓存前缀变成了逐章可变。
2. **`prev_vol_summaries` / `vol_note` 被 append 到 `bible` 尾部**（`1617–1620`）——
   卷切换时改变，属可接受；但 `genre_state` 也 append 到 `bible`（`1666`），
   意味着 **bible 字符串 = 裁剪块 + 卷信息 + 类型状态**，三个来源混在一个串里，缓存粒度无法控制。

**其他可达缓存的调用**（重复度更高、更该做缓存，但目前完全没管）：

| 调用 | 重复模式 | 现状 |
|---|---|---|
| `architect` 大纲批生成 | **同一 system + 同一个逐章递增的 user**，批间高度相似 | 488 次调用（占 48.6%），hit 仅 **0.60**（见 5.5） |
| `memory` 记忆提炼 | 同一 system + 固定快照 + 本章正文 | 88 次，hit **0.11** —— **最差** |
| `editor` 审校 | 同一 system + 章节正文 | 128 次，hit **0.53** |
| `verifier` 逻辑校验 | 同一 system，**hit 0.94**（最高） | 说明「固定 system + 小 user」时 provider 缓存工作良好 |
| `writer` 正文 | 大 prompt，hit **0.49** | |

### 5.5 实测数字（来自 `backend/learnstory.db` 只读查询 + benchmark 产物）

#### A. 全库 native usage 汇总（`generation_attempts`，1003 行）

| agent | 调用 | prompt | cached | **hit%** | completion | reasoning | reasoning/completion |
|---|---:|---:|---:|---:|---:|---:|---:|
| architect | 488 | 2,548,905 | 1,538,103 | **60.3%** | 4,344,589 | 3,268,198 | 75.2% |
| writer | 157 | 2,603,727 | 1,278,848 | **49.1%** | 904,542 | 577,606 | 63.9% |
| editor | 128 | 1,622,145 | 862,714 | **53.2%** | 1,955,829 | 1,598,001 | 81.7% |
| memory | 88 | 1,903,393 | 206,976 | **10.9%** | 852,244 | 582,199 | 68.3% |
| verifier | 61 | 348,460 | 112,123 | **32.2%** | 619,221 | 615,565 | **99.4%** |
| base | 36 | 7,528 | 0 | **0.0%** | 84,852 | 83,459 | 98.4% |
| entity_classify | 32 | 68,901 | 10,880 | 15.8% | 36,440 | 23,157 | 63.5% |
| bible_reviewer | 7 | 37,191 | 13,952 | 37.5% | 123,318 | 98,817 | 80.1% |
| worldbuilder | 6 | 11,300 | 128 | 1.1% | 49,093 | 29,204 | 59.5% |
| **合计** | **1003** | **9,151,550** | **4,023,724** | **43.97%** | **8,970,128** | **6,876,206** | **76.7%** |

#### B. 每章成本（`operation='chapter_generation'` 的 trace，书24 共 12 章）

| 指标 | 值 |
|---|---|
| 平均 prompt / 章 | **35,062 token** |
| 平均 cached / 章 | **11,547 token** |
| **章级 hit 率** | **32.9%**（低于全库 44%） |
| 平均 completion / 章 | 13,142 token |
| 平均 reasoning / 章 | 4,380 token（33.3%） |

#### C. 字符 ↔ token 真实比例（本库实测，**用于替换 `chars/4` 的猜测**）

```
input_chars = 13,255,254   prompt_tokens = 9,151,550   →  1.448 chars/token  (0.690 tok/char)
output_chars = 3,473,402   completion_tokens = 8,970,128 →  0.387 chars/token (2.58 tok/char)
```
> 中文正文约 **2.58 token/字**（含 reasoning）；judge/estimate 用的「中文 ≈ 0.8 token/字」（`estimator.py:7`）
> **低估 3.2 倍**。**这一条同时解释了为什么离线预估与真实账单差得远。**

#### C2. **空产出（blank）烧掉的 token —— 14.36%（新增重大发现）**

| 指标 | 值 |
|---|---|
| attempt 状态分布 | `ok 772` / **`blank 223`** / `failed 8` |
| **blank 浪费 token** | **2,602,167 = 全部 token 的 14.36%** |
| blank 按模型 | deepseek-v4.1-flash **2,313,604**（159 次）/ deepseek-flash 160,888(10) / qwen3.8-flash 127,675(53) |
| `attempt_no` 分布 | `1:821, 2:159, 3:11, 4:3, 5:3, 6:3, 7:3` |

> **223 次「有请求、有 token 消耗、零有效产出」= 22% 的调用是空转。**
> 这与 `client.py:70–73` 自述的「思考烧了 3000+ 字却只吐出 <20 字正文」是同一现象的两种观测。
> 并且 `backend/logs/problems.jsonl` 里 **`思考 token 已计费` / `micro_output` 零命中** ——
> 说明 `_MICRO_OUTPUT_DISCARDS` 的观测埋点在**生产里从未触发过**，真正烧钱的是 `blank` 这条更宽的路径。

#### C3. 每章真实成本（单位已验证）

| 指标 | 值 | estimator 对照 |
|---|---|---|
| 每章 token（prompt+completion+reasoning） | **60,097** | 10,450(learn) / 9,750(story) → **低估 5.75× / 6.16×** |
| 每章 Credits | 5,625,964 | — |
| **每章 LLM 调用次数** | **4.08**（98 attempts / 24 chapter traces） | `CALLS_PER_CHAPTER = 2.5` → **低估 1.6×** |
| 每章 trace 覆盖 | **仅 24/745（3.2%）的 trace 有 `chapter_idx`** | 90% 的 trace 由 `agents/base.py:54–63` 隐式创建，只有 `book_id` |

> **per-chapter 成本在结构上只对 3% 的 trace 可推导** —— 因为 `GenerationAttempt` 没有 chapter 列
> （只有 `trace_id`），而 `GenerationTrace.chapter_idx` 在隐式 trace 上恒为 NULL/0。
> **这是「按章算钱」能力的根本障碍。**

#### D. Credits 归因（MiMo Token Plan：hit=2 / miss=100 / output=200）

| token 类型 | token 数 | Credits | 占比 |
|---|---:|---:|---:|
| hit（命中输入） | 4,023,724 | 8,047,448 | **0.35%** |
| miss（未命中输入） | 5,127,826 | 512,782,600 | **22.15%** |
| output（输出，**含 reasoning**） | 8,970,128 | 1,794,025,600 | **77.50%** |
| **合计** | | **2,314,855,648** | 100% |

按 agent 的 Credits 份额：

| agent | Credits | 份额 |
|---|---:|---:|
| **architect** | 973,074,206 | **42.0%** |
| editor | 468,834,328 | 20.3% |
| memory | 340,504,452 | 14.7% |
| writer | 315,953,996 | 13.6% |
| verifier | 147,702,146 | 6.4% |
| bible_reviewer / base / entity_classify / worldbuilder | 68,786,520 | 3.0% |

#### E. **最重要的结论：reasoning 占 59.4% 的总成本**

```
reasoning tokens = 6,876,206  = 76.7% 的全部 output token
reasoning Credits = 1,375,241,200 = 59.4% 的总 Credits
非 reasoning 输出  = 2,093,922  = 18.1% 的总 Credits
```

**反事实测算**：
- 若 reasoning 降到 0 → Credits 从 2.315G 降到 **0.940G，省 59.4%**；
- 若 cache hit 从 44% 提到 80% → 降到 **1.992G，省 13.96%**。

> **「token 预算分析优化缓存命中」的正确优先级是：先治 output（含 reasoning），再治缓存命中。**
> 缓存命中的天花板只有 14%；而 output 是 77.5% 的口子。

**⚠️ 但「砍 reasoning」这个直觉已经被证伪（重要，避免重写时走弯路）**：`backend/test_reasoning_caps.py:5–16`
用 **74 条真实 attempt** 验证了配置的 reasoning 上限：
```
editor   实测峰值 3853   vs 配置上限 5000   → 从未撞到
verifier 实测峰值 3082   vs 配置上限 0(不限) → 不适用
memory   实测峰值 8004   → 唯一接近的一次
```
结论（该文件内自述）：**「上限不是成本杠杆…收益 ≈ 0」**。
也就是说 reasoning 占比高，但它是**模型自发产生的**，靠 `reasoning_max_tokens` 这个旋钮拧不动。

**真正的 reasoning 成本杠杆只有三个（按可行性排序）**：
1. **换模型 / 换档位** —— reasoning 量是模型属性。`verifier`(99.4%) / `base`(98.4%) / `editor`(81.7%)
   这些近乎「全思考零正文」的角色，应路由到**默认不思考的模型**（而非限制思考长度）。
2. **减少调用次数** —— `verifier` 6.4% + `memory` 14.7% 的 Credits 主要来自「调用多」，
   而不是「单次贵」。`memory` 的 hit 率只有 **10.9%**（最低），是重复 prompt 的重灾区。
3. **消灭 blank 空转** —— 见 C2，**223 次 blank = 14.36% 的 token 纯浪费**，这是**唯一确定可回收**的一块。

#### E2. **已实测的省钱 A/B：Editor 定向补丁省 61.6%（新增）**

`benchmark/results/editor_patch_ab_v1.md`（书8，3 章，`mimo-v2.5`）：
把「低分整章重写」改成「定向补丁」：

| 章 | legacy Credits | patch Credits | 节省 |
|---|---:|---:|---:|
| 1 | 1,974,388 | 589,780 | **70.1%** |
| 2 | 2,084,188 | 1,199,684 | **42.4%** |
| 3 | 1,925,088 | 509,704 | **73.5%** |
| **合计** | **5,983,664** | **2,299,168** | **61.6%** |

- adoption 3/3；**re-review delta −0.667**（质量略降，未阻断）；
- **但这个杠杆在生产里是关的**：`editor_cost_config.json` → `{"low_score_patch": false, "length_patch": false}`，
  只有 `low_score_patch` 接了线（`pipeline.py:7515–7543`），`length_patch` **无实现**（`editor_cost_config.py:12`）；
- `test_editor_cost.py:161–165` 断言「补丁路径必须 flag 门控、默认关」。

> **这是全项目 ROI 最高的单点**：Editor 占生产 **20.3%** 成本（离线 artifact 里占 59.3%），
> A/B 已证明能省 61.6%，代价是 −0.667 分。**重写时应当默认为开。**

#### F. 成本口径的四个**一致性缺陷**（必须在重写时修）

| # | 缺陷 | 证据 |
|---|---|---|
| 1 | **reasoning 被重复计入** | `reasoning ⊆ completion`（**896/897 行**验证 `prompt+completion == total`），但 `trace_service.py:117–120` 算 `completion + reasoning`，`:129–132` 还算 `prompt+completion+reasoning` → **任何基于这两处的成本函数会多收 77% 的输出费** |
| 2 | **两份互相矛盾的 Credits 公式** | `cache_attribution.py:65–74`（NULL-safe、含折扣）vs `editor_patch_ab.py:199–204`：`cached*2 + max(0,prompt-cached)*100 + out*200` —— 后者用 `usage.get(k) or 0`（`:201–203`）**把 NULL 变 0 → 把整段 prompt 当 miss 计费**。`test_editor_patch_ab.py:30–32` 还把这个行为**钉成正确** |
| 3 | **成本是 model-blind 的** | 只有一套 MiMo 权重，却用在 `deepseek-v4.1-flash`（**占生产 61% 的 attempt**）和 `qwen3.8-flash` 上（不同 provider、不同价） |
| 4 | **`native` 是弱保证** | `native` 只要 ≥3/5 字段（`observability.py:118`）→ 33 行 `native` 的 `cached_tokens` 为 NULL → `credits_for` 会**既不收 hit 也不收 miss**，**漏报**而非多报 |

#### G. 缓存归因的历史产物（`benchmark/results/`）

`prompt_cache_attribution_v1.md`（MiMo Token Plan 口径，权重 2/100/200）：

| artifact | attempts | paired | hit_ratio | Credits | 输出占比 |
|---|---:|---:|---:|---:|---:|
| `mimo_lm_focused_legacy_v1.json` | 26 | 24 | **0.370** | 38,403,404 | 66.5% |
| `mimo_lm_focused_focused_v1.json` | 25 | 22 | **0.519** | 27,949,964 | 75.3% |
| `mimo_primary_live_baseline_v1.json` | 11 | 8 | **0.428** | 13,352,348 | 72.9% |
| `omen_q1_live_baseline_v1.json` | 23 | 7 | **0.485** | 12,663,260 | 78.4% |

与官方账单对账（`cache_attribution.py:412`，**只比结构占比不比绝对值**）：

| 月份 | 账单 hit / miss / output | 报告 hit / miss / output | output 偏差 |
|---|---|---|---|
| 2026-09 | 0.6% / 33.1% / 66.3% | 0.4% / 27.9% / 71.7% | +5.4pp |
| 2026-08 | 0.4% / 54.0% / 45.6% | 0.4% / 27.9% / 71.7% | **+26.1pp** |

> 8 月偏差 26pp，说明**报告口径与真实账单形状严重不符**——最可能的原因是 trace 采样偏斜
> （`adoption` 在 `omen_q1` 里低至 0.0–0.5，见下表），报告的样本不代表全量。

#### H. **离线 artifact 的成本排名与生产不一致（重写时必须知道）**

| 排名 | 离线 artifact（`mimo_lm_focused_legacy_v1`） | 生产实测（本库 1003 attempts） |
|---|---|---|
| #1 | **editor 59.3%** | **architect 42.1%** |
| #2 | writer 23.2% | editor 20.3% |
| #3 | memory 14.0% | memory 14.8% |
| #4 | verifier 3.4% | writer 13.5% |

**原因**：离线套件严重欠采样 architect（生产 **401 条 architect trace** vs 离线只测了 26 条 attempt）。
**结论：不要用 `benchmark/results/` 里的成本排名做优化决策** —— 它会把你导向 editor，
而生产里真正的第一大头是 **architect（42.1%）**。

> **对一个 4 个 architect 调用的自检**：architect 的 hit 率其实**最高（60.3%）**，
> 但它的 output 有 75.2% 是 reasoning、绝对量大（2.55M prompt / 4.34M completion），
> 所以**即使命中率高，它仍然是第一成本项** —— 再次印证「输出侧才是主战场」。

`adoption`（有 paired usage 的 attempt 占比）在 artifact 间极不稳定：

| agent | legacy | focused | primary_live | omen_q1 |
|---|---:|---:|---:|---:|
| editor | 1.000 | 1.000 | 0.833 | **0.500** |
| memory | 1.000 | 1.000 | 1.000 | **0.000** |
| verifier | 0.333 | 0.800 | — | — |
| writer | 1.000 | 1.000 | **0.500** | **0.222** |

**首次 vs 重试的命中率差异**（focused artifact，verifier）：`first=0.934` / `retry=0.985`
→ **重试的缓存命中反而更高**（因为 prompt 完全相同，provider 已缓存）。

`phase1_trace_report.md:36`：`Classified chars: 533; attempt input chars: 69,699; coverage proxy: 0.0076`
—— **上下文归因覆盖率 0.76%，等于没有**。

### 5.6 缓存命中机会估算

**每章输入的稳定比例（保守估计）**：

| 上下文块 | 典型字符数 | 跨章稳定性 | 可缓存 |
|---|---:|---|---|
| `bible`（裁剪后） | ~2,000–6,000 | 全书稳定（但被按章裁剪破坏） | ✅ 若能去掉按章裁剪 |
| `genre_state` + `genre_blocks` | ~500–1,500 | 类型级稳定 | ✅ |
| `entity_registry` | 单调增长 | 前缀稳定 | ✅（前缀部分） |
| system prompt | 数十字 | 完全稳定 | ✅（当前根本没计） |
| `states` + `timeline` | ~2,000–6,000 | 滑动/渐变 | ⚠️ 部分 |
| `prev_prose` + `rag_snippets` + `style_snippets` | ~2,000–5,000 | 逐章全新 | ❌ |
| `hang` / `bridge` / 伏笔 / 复习 | ~500–1,500 | 逐章全新 | ❌ |

**估算**：`chapter_outline + plot_detail + character_state + foreshadow + timeline + rag + previous_chapter
+ style_reference + learn_context + misc` 这些**逐章可变块**合计约 **55%–70%**；
真正跨章稳定的是 `bible + genre + entity_registry`，约 **30%–45%**。

**这就是为什么现在的 hit 率卡在 33%–44%**：稳定段的**长度上限大约就是 40%**，
而现有实现连这 40% 都没拿到全部（章级只有 32.9%）。

**要达到 80% hit 需要做的（按收益排序）**：
1. **去掉 bible 的按章裁剪**（或改成「全量圣经进缓存前缀 + 按章选取的部分放到尾部」）——
   `memory.py:1624–1629` 是单点最大破坏；
2. **`entity_registry` / `genre_*` 移到 prompt 最前**，与 bible 合并成一个「全书不变前缀块」；
3. **`prev_prose` / `rag` / `style` / `hang` / `bridge` 全部移到 prompt 末尾**（当前已是后半，但要确保**无一块稳定内容夹在它们中间**）；
4. **修 `system_instruction: 0`**，让 system prompt 参与前缀；
5. 若 provider 支持（Anthropic 风格），对前缀块打 `cache_control: {type: "ephemeral"}`；
6. **去掉/收敛 `bible_inject.select`**，或改为「不改内容只改顺序」。
7. **续推轮次（4 条 messages）与首次（2 条）前缀不同** → 考虑对续推单独维护缓存键。

---

## 6. 成本追踪

### 6.1 三套互不相通的记账

| 系统 | 介质 | 粒度 | 覆盖 | 位置 |
|---|---|---|---|---|
| **`Metric` 表** | SQLite `metrics` | role / model / book_id / provider / elapsed_s / **仅 completion_tokens** | **4373 行** | `client.py:1254`、`models.py` |
| **`GenerationTrace/Attempt/Span`** | SQLite 三张表 | trace → attempt（**含 prompt/cached/reasoning/usage_source**） | **attempts 1003 行 / traces 745 行 / spans 98 行** | `observability.py:302` |
| **`LLMStats`** | **内存 deque(50)** | 成功率/耗时/429/suggested_parallel | 重启清零 | `llm_stats.py:12` |

**覆盖率缺口（实测）**：
```
metrics 4373 行  vs  generation_attempts 1003 行  →  只有 22.9% 的 LLM 调用有 native token usage
metrics 中 completion_tokens = 0 的：976 行（22.3%）—— 这些是 native 缺失被写成 0，不是真的 0
```
> `Metric` 只有 `completion_tokens` **没有 prompt_tokens / cached_tokens** →
> **成本分析必须走 trace 表，而 trace 表只有 23% 覆盖率**。

`Metric` 表缺失的另一个证据：`llm_stats.py` 的数据**从不落盘**（`llm_stats.py:4` 自述「统计数据仅存内存，重启后清零」），
所以 `/api/llm/stats`（`main.py:3920`）在重启后返回的是空窗口 —— 而 `main.py:3926` 的 `/api/agents/stats` 读的是 DB。
**两个"stats"端点一个是内存、一个是 DB，语义完全不同**。

### 6.2 `usage_source` 契约（`observability.py:44–120`）

```python
@dataclass
class UsageRecord:
    prompt_tokens / completion_tokens / reasoning_tokens / cached_tokens / total_tokens: int|None
    usage_source: str = "unavailable"     # native | partial | unavailable
    usage_complete: bool = False
```
判定（`110–119`）：
```python
present = sum(v is not None for v in (prompt, completion, reasoning, cached, total))
usage_source   = "native"      if present >= 3 else ("partial" if present else "unavailable")
usage_complete = all(v is not None for v in fields)     # 要求 5 项全非空
```

字段抽取兼容多种 provider 命名（`68–102`）：
- `prompt_tokens` / `input_tokens`
- `completion_tokens` / `output_tokens`
- `reasoning_tokens` / `reasoning`，以及 `completion_tokens_details.reasoning_tokens` / `output_token_details`
- **`cached_tokens` / `cache_read_input_tokens`**，以及 `prompt_tokens_details.cached_tokens` / `input_tokens_details`

**实测分布**：`native 896 / unavailable 107`。**注意：没有一条 `partial`** ——
因为 `native` 门槛只要 3 项，而 provider 通常一次给全 5 项。

**缺口**：
- `usage_complete` 要求 5 项**全部**非空 → 实测几乎恒为 False（`trace_service.summary` 的 `usage_complete_count` 可验证）；
- **`cached_tokens` 缺失时保持 NULL 是正确的**（`cache_attribution.py:185–186` 明确「严格保持 NULL，不退化成 0」），
  但 `Metric` 表没有对应列，无法做「按 role 的缓存命中」；
- **没有 `cache_write_tokens` 字段** —— Anthropic/部分网关会返回 `cache_creation_input_tokens`，当前**丢弃**。

### 6.3 `estimator.py` —— **离线预估，误差 3 倍以上**

| 常量 | 值 | 行号 |
|---|---|---|
| `OUT_TOKENS` | `research 3500 / architect 4500 / worldbuilder 3000 / write 3200 / cards 700 / memory 600 / review 900` | 8–16 |
| `CALLS_PER_CHAPTER` | **2.5** | 18 |
| `per_ch_in` | **5500**（"圣经+记忆+大纲注入"） | 107 |
| 默认速度 | `{fast:45, balanced:22, premium:35}` tok/s | 50 |

**与实测对比**：

| 项 | estimator 预估 | 本库实测 | 偏差 |
|---|---|---|---|
| 每章输入 token | **5,500** | **35,062** | **低估 6.4 倍** |
| 每章输出（非推理） | 3,200 + 600 + 450 = 4,250 | 13,142（含 reasoning） | 低估 3.1 倍 |
| 每章 LLM 调用数 | 2.5 | 章级 trace 实测 **4.0–7.0**（书24 平均约 4.3） | 低估 ~1.7 倍 |
| 中文 token/字 | 0.8（注释 7） | **2.58** | 低估 3.2 倍 |

> **结论：`estimator.py` 的 `total_tokens` 与真实成本差一个数量级。**
> 它现在的作用是「给用户一个心理预期」，不是成本控制 —— 但界面上是以 `estimate_json` 展示的，
> **用户看到的 token 数比真实低 5–6 倍**。
> `_avg_speed`（21–52）确实会读 `metrics` 表做校准，但只用于**耗时**，token 部分是纯常量。

### 6.4 `offpeak.py` —— 杠杆比想象小得多

| 事实 | 值 | 行号 |
|---|---|---|
| 非高峰窗口 | **北京时间 0:00–8:00**（UTC 16:00–24:00） | 4–5, 43–44 |
| 折扣系数 | **0.8×** | 46 |
| 默认开关 | **关闭**（`enabled: false`） | 42 |
| 最长等待 | 3600s，超时照常执行 | 45, 168–209 |

**模块 docstring 自己给出了清醒结论**（`offpeak.py:7–19`）：

> ⚠️ 但这个杠杆的适用面比方案原稿估计的**小得多**。实测 story 流水线的 LLM 调用
> **全部是内联同步**的——`writer`/`editor`/`memory`/`verifier` 都在用户等待的章节链路里跑，
> 把它们 sleep 到夜间等于把生成卡死。
> **真正的省钱动作在操作层面**：把整本书／连载批跑安排在 0:00–8:00 执行，不需要改代码。
> **代码能自动化的部分约占成本的 1%。**

唯一调用点：`pipeline.py:5517`（`_maybe_expand_knowledge_bg` 的后台发散）。
`discount_factor()` 被 `benchmark/cache_attribution.py:126` 用于成本核算口径（**这是它当前最大的实际价值**）。

### 6.5 成本归因维度的覆盖情况

| 维度 | 有？ | 实现 |
|---|---|---|
| **book** | ✅ | `GenerationTrace.book_id`、`Metric.book_id` |
| **chapter** | ✅ | `GenerationTrace.chapter_id/chapter_idx/volume` |
| **agent role** | ✅ | `GenerationAttempt.agent` ← `runctx.agent_role` contextvar |
| **model** | ✅ | `GenerationAttempt.model` |
| **provider** | ✅ | `GenerationAttempt.provider` |
| **trace 级上下文来源（字符）** | ⚠️ 部分 | `context_breakdown`，覆盖率 0.76% |
| **pipeline stage** | ❌ | 只有 span name（`prepare_context`/`writer`/`chapter_commit`），**没有 span 级 token 归因** |
| **retry waste** | ✅ | `trace_service.summary:163` `retry_waste_tokens` / `retry_waste_rate` |
| **token 放大代理** | ✅ | `trace_service.summary:168` `token_amplification_proxy`、`:170` `char_amplification_proxy` |
| **小时级/夜间折扣折算** | ✅ | `cache_attribution.discount_for_attempt` |
| **成本上限/熔断** | ❌ **完全没有** | grep 无任何 budget ceiling / cost cap；`_llm_max_attempts()` 默认 0 = 禁用 |
| **货币 / 实际花费** | ❌ | 只有相对 Credits，没有任何 ¥/$ 总额；`reconcile_with_bill` 明确「只比结构占比不比绝对值」 |
| **实时成本看板** | ⚠️ | `/api/ops/dashboard`（`main.py:4203`）聚合 trace/延迟/agent 计数，**但没有任何 Credits 端点**；唯一的成本看板是 `docs/COST_OPTIMIZATION_STATUS.md` 这个 markdown |
| **per-stage 预算** | ❌ | 只有 per-call `deadline_s`（时间预算，不是 token 预算） |
| **retention / rotation** | ❌ | `generation_traces/attempts/spans` 无界增长；`GenerationAttempt`/`GenerationSpan` **不在 `BOOK_SCOPED_MODEL_NAMES`**（`models.py:696–718`，它们没有 `book_id` 列）→ 删书留孤儿（生产已存在 **297 条 `book_id = NULL` 的 attempt**） |

**命名陷阱（避免重写时被文件名误导）**：
- `backend/test_ledger_attribution.py` **不是成本测试** —— 它测知识账本的 revision 语义（TEXT_ONLY/SEMANTIC）；
- `backend/test_mutable_attribution.py` 测关系/伏笔的动作溯源；
- `_rag_ab_result.json` 里的 `_cost` 字段是**墙钟秒数**（`_tmp_rag_ab.py:137`：`round(time.time()-t0,1)`），不是钱；
- `story_arc_planner.py:39` 的 `cost_moment` 是**剧情节拍**（代价时刻），不是成本。

---

## 7. API 清单（`main.py`，**4629 行**）

**总数：148 个路由**，全部是扁平 `@app.*` 装饰器。
**零 `APIRouter` / 零 `include_router` / 零 `app.mount` / 零 websocket / 零 SSE** ——
全仓唯一的 `FastAPI(` 实例化在 `main.py:51`。
**148 个路由全部无鉴权**，而 CORS 是 `allow_origins=["*"]`（`main.py:87–92`）。

| 方法 | 数量 |
|---|---:|
| GET | 78 |
| POST | 54 |
| PUT | 8 |
| DELETE | 8 |
| PATCH / WS / HEAD / OPTIONS | 0 |
| **合计** | **148** |

分类：**story 87 / learn 3 / ops 29 / infra 29**。

> ⚠️ **learn-only 只有 3 个路由**（`/api/flows` 477、`/api/library` 484、`/api/books/{id}/continue/research` 2801）。
> **系统已经事实性地从「知识小说」漂移成「纯小说生成器」**，但 `pipeline.py` 里还留着 188 行 learn-only 代码。

### 7.1 前缀聚类 = 臃肿地图

| 前缀 | 路由数 | 占比 |
|---|---:|---:|
| `/api/books/{book_id}/*` | **66** | **44.6%** |
| `/api/ops/*` | 19 | 12.8% |
| `/api/corpus/*` | 9 | 6.1% |
| `/api/llm/*` | 8 | 5.4% |
| `/api/chapters/*` | 5 | 3.4% |
| `/api/books`（裸） | 5 | 3.4% |
| `/api/notifications` | 4 | 2.7% |
| `/api/story-random` | 3 | 2.0% |
| `/api/embedding-config` | 3 | 2.0% |
| 8 组「配置读写对」 | 16 | 10.8% |
| 单例 | 10 | 6.8% |

### 7.2 story 路由（87）

**生命周期控制（10）**：`POST /api/books`(527) · `POST /{id}/retry-planning`(779) · `POST /{id}/profile`(802) ·
`POST /{id}/profile/reset`(829) · `POST /{id}/select`(844) · `POST /{id}/confirm`(933) · `POST /{id}/resume`(952) ·
`POST /{id}/pause`(980) · `POST /{id}/stop`(1002) · `DELETE /api/books/{id}`(3335)

**圣经（6）**：`GET /{id}/bible-review`(1653) · `GET /{id}/bible`(1680) · `PUT /{id}/bible`(1699) ·
`POST /{id}/bible/confirm`(1739) · `POST /{id}/bible/rebuild`(2218)

**大纲 / 细纲（5）**：`POST /{id}/outline/fill-detail`(1758) · `GET /{id}/outline/detail-status`(1786) ·
`GET /api/outline-config`(3166) · `POST /api/outline-config`(3173) ·
`GET /api/rolling-config`(3182) · `POST /api/rolling-config`(3189)

**章节读写（7）**：`GET /api/chapters/{id}`(1082) · `GET /{id}/reading-context`(1099) ·
`GET /api/chapters/{id}/rewrite-compare`(1168) · `POST /api/chapters/{id}/regenerate`(1252) ·
`POST /api/chapters/{id}/cards/regenerate`(1270) · `POST /api/chapters/{id}/revise-local`(1295) ·
`GET /{id}/progress`(2030)

**实体 / 关系（7）**：`GET|POST /{id}/entities`(1205,1212) · `PUT|DELETE /{id}/entities/{name}`(1221,1228) ·
`POST /{id}/entities/{name}/suspect/restore`(1235) · `DELETE /{id}/entities/{name}/suspect`(1245) ·
`GET /{id}/entity-lifecycle`(4107)

**伏笔 / 图谱（5）**：`GET /{id}/foreshadowings`(1407) · `GET /{id}/graph/overview`(4005) ·
`GET /{id}/graph/full`(4029) · `GET /{id}/graph/{entity}`(4042) · `GET /{id}/drift`(3969)

**审计 / 校验（13）**：`GET /{id}/audit/entities`(2410) · `GET /{id}/audit/drift`(2427) ·
`GET /{id}/audit/recall`(2444) · `GET|POST /{id}/audit-words`(2461,2468) ·
`GET /{id}/verify-results`(2475) · `POST /{id}/verify`(2482) · `GET|POST /api/audit-config`(2391,2398) ·
`GET|POST /api/guard-config`(2499,2513) · `GET|POST /api/review-config`(2526,2539)

**续接（8）**：`GET /{id}/continue/plan`(2773) · `POST /{id}/continue/recommend`(2780) ·
`POST /{id}/continue`(2819) · `GET /{id}/continue/status`(2888) · `POST /{id}/continue/resume`(3048) ·
`POST /{id}/continue/stop`(3113) · `POST /{id}/continue/pause`(3125) · `POST /{id}/continue-volume`(1058)

**摘要 / 文风 / 新颖度（10）**：`GET|PUT /{id}/style-prefs`(2237,2255) · `GET /{id}/novelty`(2339) ·
`GET /{id}/novelty/progress`(2359) · `GET /{id}/novelty/pair`(2372) · `GET|PUT /{id}/story-directives`(1556,1603) ·
`GET /{id}/summaries`(4055) · `POST /{id}/summaries/generate`(4070) · `PUT /{id}/title`(2299) ·
`GET /{id}/story-health`(2322) · `GET /{id}/preview-director-prompt`(1630)

**配置 / 开关（12）**：`GET /api/scales`(2551) · `GET|POST /api/chapter-limits`(2579,2589) ·
`GET /api/story-genres`(2606) · `POST /{id}/parallel-stages`(3226) · `POST /{id}/quick-mode`(3250) ·
`PUT /{id}/harvest-opt-in`(3264) · `POST /{id}/cards/backfill`(1053) ·
`GET /api/story-premise/random`(2619) · `GET|DELETE /api/story-random/{field}`(2736,2747,2754) ·
`GET /{id}/search`(1451)

### 7.3 ops 路由（29）

`GET /api/books/health-scan`(663) · `POST /api/books/health-repair`(670) ·
`GET /api/books/health-repair/status`(685) · `GET|DELETE /api/problem-logs`(3145,3159) ·
`GET /api/problem-stats`(3152) · `GET /api/model-stats`(3330) · `GET /api/llm/stats`(3920) ·
`GET /api/agents/stats`(3926) · `GET /api/genres/stacks`(3946) · `GET /api/ops/dashboard`(4203) ·
`GET /api/ops/health`(4233) · `GET /api/ops/runtime`(4252) · `GET /api/ops/infra`(4287) ·
`GET /api/ops/traces`(4299) · `GET /api/ops/traces/{id}`(4309) · `GET /api/ops/books/{id}/trace-summary`(4323) ·
**model-evals 13 个**：`POST import`(4333) / `import-mimo-p1`(4344) / `import-mimo-long-memory-shadow`(4356) /
`import-mimo-lm-focused-pairwise`(4368) / `import-cross-model-p1`(4382) · `GET runs`(4396) /
`runs/{id}`(4409) / `runs/{id}/summary`(4418) / `runs/{id}/cases/{cid}`(4434) ·
`PUT .../human-review`(4443) · `POST .../pairwise`(4454) · `POST runs/{id}/decision`(4465)

### 7.4 infra 路由（29）

`GET|POST /api/llm/providers`(308) / `connect`(319) / `test`(347) · `GET|POST /api/llm/capacity`(367,372) ·
`POST /api/llm/providers/{id}/activate`(385) · `DELETE /api/llm/providers/{id}`(392) ·
`GET|POST /api/embedding-config`(400,416) / `test`(423) · `GET|PUT /api/settings`(449,462) ·
`GET /api/templates`(469) · `GET /api/books`(628) · `GET /{id}/export/meta`(1015) · `GET /{id}/export`(1025) ·
`GET|POST|DELETE /api/notifications`(3200,3207,3213,3219) · **corpus 9 个**：`GET /api/corpus`(4494) /
`import`(4501) / `import-dir`(4511) / `import-status`(4524) / `stats`(4531) / `usage`(4538) / `hits`(4558) /
`book-style/{id}`(4577) / `DELETE {cid}`(4623)

### 7.5 中间件 / 生命周期

| 项 | 位置 | 说明 |
|---|---|---|
| CORS | `main.py:87–92` | `allow_origins=["*"]`、`allow_methods=["*"]`、`allow_headers=["*"]` |
| startup | `main.py:95–217` | `init_db()` → `model_settings.apply_to_settings` → `llm_providers.apply_to_settings` → DB→storage 迁移（`PRAGMA table_info`） |
| shutdown | **无** | |
| HTTP middleware | **无** | |
| exception handler | **无** | 只有 handler 内 `raise HTTPException` |
| 鉴权 | **无** | 零 `Depends` / `Security` / `HTTPBearer` |

### 7.6 臃肿观察（可直接作为重构输入）

1. **硬编码的单本书业务规则进生产 API**：`main.py:3272` 有 `if book_id == 21:`；
   配套 `app/book23_ultra_repair.py` / `app/book25_ultra_repair.py` 是**单本书修复模块住在 app 包里**。
2. **5 个近似重复的 eval-import 端点**（4333/4344/4356/4368/4382）只差调哪个 `evaluation_store.import_*`；
   更糟的是 `main.py:4474` 在产品 handler 里 `import benchmark.omen_q1_h` —— **benchmark 泄漏进生产 API**。
3. **8 组「GET + 写」配置对（16 个路由）**，每组各有自己的 `load/save/reload/_raw` 实现，**没有共享配置层**；
   `/api/settings` 用 `PUT`（462），其余 6 个兄弟全用 `POST`。
4. **同一种操作的 HTTP 动词漂移**：`/parallel-stages`(3226) 和 `/quick-mode`(3250) 是 `POST`，
   而 `/harvest-opt-in`(3264) 和 `/title`(2299) 是 `PUT` —— 4 种方式设 1 个字段，2 种动词。
5. **路径命名两套并存**：连字符扁平段（`audit-words` 2461、`story-directives` 1556、`style-prefs` 2237、
   `reading-context` 1099、`retry-planning` 779、`health-scan` 663…共 16 个）vs 斜杠嵌套
   （`audit/entities` 2410、`graph/overview` 4005、`outline/detail-status` 1786）。
   **同一个功能内部就冲突**：`/audit-words`(2461) 紧挨 `/audit/entities`(2410)。
6. **重叠读端点**：`/api/ops/dashboard`(4203) 重新聚合 `/api/agents/stats`(3926) + `/api/llm/stats`(3920) +
   `/api/problem-stats`(3152)；`/api/model-stats`(3330) vs `/api/llm/stats`(3920)；
   `/api/books/{id}/audit/drift`(2427) vs `/api/books/{id}/drift`(3969)。
7. **静默失败的调试端点**：`/api/ops/traces`(4299)、`/traces/{id}`(4310)、`/trace-summary`(4323)
   全部 `except Exception` 返回空/零 payload —— `/trace-summary` 在失败时返回 `success_rate: 1.0`，
   **这是主动误导**。
8. **脆弱的声明顺序依赖**：`GET /api/books/health-scan`(663) 与 `/health-repair/status`(685)
   **必须**排在 `GET /api/books/{book_id}`(692) 之前；`graph/overview`(4005)/`graph/full`(4029)
   必须排在 `graph/{entity}`(4042) 之前 —— 代码 docstring（4006、4030）自己警告了这一点。
   **按字母排序即静默破坏**。
9. **无鉴权 + 全开 CORS**；18 个 handler 收裸 `payload: dict`，浪费了 12 个已声明的 `BaseModel`。
10. **单体结构**：45% 的路由挂在 `/api/books/{book_id}/` 下；整个 API 在一个 4629 行扁平模块里，
    中间被一段约 580 行的私有 helper 块（`main.py:3373–3917`：`_phase_text`/`_serial_start_ts`/
    `_serial_model_time_per_chapter`/`_eta`/`_ledger_summary`/`_rebuild_modules_from_json`/`_si`/
    `_modules_dict`/`_runtime_status`/`_steps`/`_book_dict`/`_strip_life_prefix_list`/`_char_damages`/
    `_chapter_dict`）横穿 —— **这段就是天然的第一次抽取边界**。

---

## 8. Feature Flag 清单

### 8.1 总量

| 类别 | 数量 |
|---|---:|
| `backend/` 根 JSON 文件 | **33** |
| `os.getenv` 类环境变量（字面名） | **81**（+3 动态模式） |
| JSON 可配键（估） | **~190** |
| **合计可独立设置的旋钮** | **~290** |

### 8.2 JSON 配置文件（33 个）

**A. 运行时特性开关（19，带热重载 + env 双胞胎）**

| 文件 | 字节 | 关键键 | 读取模块:行 |
|---|---:|---|---|
| `audit_config.json` | 1511 | 42 键：`drift_cover_threshold` 0.5 / `verify_interval` 10 / `verify_logic_interval` 5 / `logic_llm_batch` 3 / `editor_reasoning_max_tokens` 5000 / `pp_reasoning_max_tokens` 5000 / `foreshadow_inject_max_chars` 6000 / `graph_inject_enabled` false / `recall_check_enabled` true / `bible_len_by_scale{fast:[1500,2200]…ultra:[4000,6000]}` | `audit_config.py:10` + 30 个消费方 |
| `chapter_limits.json` | 852 | 4 档 × 7 键（`min_chars/hard_min_chars/soft_max_chars/climax_max_chars/max_continuations/soft_floor_chars/allow_short_chapter`） | `chapter_limits.py:33`；`writer.py:59,275,558`；`pipeline.py:4743,7268` |
| `character_delta_config.json` | 73 | `enabled` false / `shadow` false / `projection_mode` legacy | `character_delta_config.py:9` |
| `commit_recovery_config.json` | 90 | `enabled` false / `startup_scan` false / `batch_size` 100 / `fail_open` true | `commit_recovery_config.py:8` |
| `editor_cost_config.json` | 60 | `low_score_patch` false / `length_patch` false（**未接线**） | `editor_cost_config.py:21`；`pipeline.py:7517,7612` |
| `guard_config.json` | 244 | `global{near_window:4, near_matched:3, topk:3, recent:12, local_rewrite_max:8}` + `per_book{}` | `guard_config.py:8`；`pipeline.py:6682,7309` |
| `long_memory_prompt_config.json` | 61 | `enabled` false / `mode` off / `max_chars` 1200 | `long_memory_prompt.py:15`；`writer.py:301` |
| `model_settings.json` | 131 | 三档模型（**premium 与 balanced 都是 `deepseek-flash` → 档位退化**） | `model_settings.py:8`；`main.py:98–103` |
| `offpeak_config.json` | 139 | `enabled` false / `start_hour` 0 / `end_hour` 8 / `night_discount` 0.8 | `offpeak.py:33`；`pipeline.py:5517` |
| `omen_harvest_config.json` | 489 | `enabled` false / `omen_model` omen-alpha / `fallback_model` mimo-v2.5 / `allowed_roles[writer]` / `high_risk_beats[turning,climax,boss,recall]` | `omen_harvest.py:25`；`pipeline.py:172,193,6619` |
| `outline_config.json` | 94 | **只有 3 键**（`fill_plots_batch_size` 3 / `plot_detail_chars` 2000 / `plot_detail_min_chars` 900）；**模块 DEFAULTS 有 10 键，7 键缺失走默认** | `outline_config.py:34` |
| `prose_style_config.json` | 223 | `enabled` **true**（模块默认 False）/ `shadow` true / `duplicate_threshold` 0.72 / `experiment` book24-lean | `prose_style.py:97`；`pipeline.py:6971,6986` |
| `review_config.json` | 313 | `sample_rate` 1.0 / `head_tail_by_scale{fast:[1,2]…ultra:[20,15]}`（**模块默认 [1,1]/[1,2]/[2,3]/[3,5]**） | `review_config.py:22`；`pipeline.py:7343` |
| `review_escalation_config.json` | 280 | `enabled` **true**（模块默认 False）/ `hard_escalation` true / `style_dedupe` true / `max_attempts_per_chapter` 1 | `review_triage.py:70` |
| `revision_config.json` | 74 | `chapter_revision{enabled:false, shadow:false}`；**env `LEARNSTORY_REVISION_ENABLED` 未在 `.env.example` 声明** | `revision_config.py:14` |
| `rolling_story_config.json` | 409 | `enabled` **true**（书24 实验）；`ROLLING_SKELETON/WRITER_PARALLELISM = 1` **硬编码不可配** | `rolling_story_config.py:23` |
| `trace_config.json` | 106 | `enabled` true / `persist_attempts` true / `persist_spans` true / `capture_prompt_hash` true | `trace_config.py:13` |
| `ultra_structure_config.json` | 362 | `enabled` false / `shadow` true / `repair_attempts` 5 / `min_arcs` 6 / `max_arcs` 10 / `min_milestones` 9 | `ultra_structure.py:16` |
| `writer_rules_config.json` | 31 | `rule_profile` = `lean` | `writer_rules.py:83`；`writer.py:12` |

**B. 基础设施装配（2）**：`infrastructure.json`(823) —— `cache/queue/lock/vector/graph` 后端选择 + redis/milvus 连接；
`infrastructure.local.json`(296) —— **仅测试用**（25 个根测试文件通过 `LEARNSTORY_INFRA_CFG` 引用）。

**C. 含密钥文件（2）** —— ⚠️ **见 8.5 安全发现**：
`llm_providers.json`(9703)、`free_pool.json`(268)。

**D. 数据表（3）**：`scales.json`(8644) —— **learn 与 story 两块数值完全相同**（8 档 × 14 字段）；
`story_random_pool.json`(13132) —— 题材桶；`selection_cache.json`(9280) —— **68 键里约 66 个是测试夹具残留**。

**E. 缓存（4）**：`artifact_cache.json`(**8,142,988 B**，`MAX_ENTRIES=60`)、`research_cache.json`(5,710,692)、
`verify_cache.json`(194,954)、`artifact_dbg.json`/`artifact_test.json`/`test_results.json`（**测试垃圾**）。

**F. 死/孤儿（2）**：`long_memory_meta_guard_config.json`(89)、`long_memory_routing_config.json`(306)
—— **仅 benchmark 读取，app 零引用**。

### 8.3 环境变量（81 个，节选关键项）

| ENV | 读取点 | 默认 | 门控 |
|---|---|---|---|
| `LEARNSTORY_DATA_DIR` | 12 处（`artifact_store.py:20`、`docstore.py:26`、`safe_guard.py:36`、`client.py:14`…） | 无 | **测试/基准隔离根**；同时关闭「无可用模型」硬停（`main.py:530`） |
| `MAX_TOTAL_WORKERS` | `config.py:123` | 4 | 跨书总并发闸门 |
| `PARALLEL_WORKERS` | `config.py:111` | 4 | quick 模式并行起草通道 |
| `ARCHITECT_PARALLEL` | `config.py:112` | 2 | 架构段并行 |
| `RESEARCH_WORKERS` | `config.py:108` | 4 | 研究阶段并发 |
| `LLM_TIMEOUT` | `config.py:97` | 300 | 单次 LLM 调用超时 |
| `LLM_MIN_BUDGET_ATTEMPTS` | `config.py:102` | 2 | 预算耗尽前最少尝试次数 |
| `LLM_MAX_ATTEMPTS` | `client.py:108` | **0（不启用）** | 重试次数保险丝 |
| `CHAPTER_DEADLINE` | `config.py:105` | 1800 | 单章挂机重连预算 |
| `RESUME_REASONING` | `client.py:599` | 1 | 跨轮续推思考 |
| `WRITER_REASONING_{BASE,MAX,HARD_MAX,RATIO,BOOST}` | `config.py:130–134` | 1500/8000/16000/1.2/1.5 | **Writer 思考预算**（唯一与 reasoning 成本相关的旋钮） |
| `FREQUENCY_PENALTY` / `PRESENCE_PENALTY` | `config.py:117/116` | 0.2 / 0.4 | 采样 |
| `RAG_*`（20 个） | `config.py:54–92` | — | 检索窗口/融合/重排/衰减 |
| `LEARNSTORY_TRACE_ENABLED` | `trace_config.py:38` | JSON | 观测总开关 |
| `LEARNSTORY_OFFPEAK_SCHEDULE` | `offpeak.py:70` | JSON | 错峰闸门 |
| `TASK_TIER_{ROLE}` | `config.py:42` | 角色表 `:30–37` | **按角色选模型档位** |
| `LEARNSTORY_{CHARACTER_DELTA,COMMIT_RECOVERY,REVISION,ROLLING_STORY,OMEN_HARVEST,ULTRA_STRUCTURE}_*` | 各 config 模块 | JSON | **每个 JSON 开关的 env 双胞胎** |

### 8.4 Flag 泛滥评估

**同一个行为被多处门控**：

- **prose-style 去重被三处门控**：`prose_style_config.enabled`（`prose_style.py:164`）
  **AND** `review_escalation_config.style_dedupe`（`review_triage.py:163`）
  **AND** `pipeline._prose_style_flags()`（`pipeline.py:6967`）。
  后者依次尝试 模块 → 裸 JSON，**吞掉全部异常**（`6973,6986`）→ 文件坏掉时**静默降级成 no-op**。
- **章节字数下限散落三处**：`chapter_limits.json` + `scales.json`（`rule`/`pacing` 里嵌了数字）+ env。
  `chapter_limits.py:26` 自己把这种重叠记为风险。
- **`legacy`/`shadow` 灰度键散落 6 个独立文件**：`character_delta_config`、`long_memory_prompt_config`、
  `long_memory_meta_guard_config`、`long_memory_routing_config`、`revision_config`、`ultra_structure_config`，
  外加 `review_escalation_config.shadow`、`prose_style_config.shadow` —— **没有单一的「灰度状态」真源**。
- **temperature/effort 三处**：`audit_config.reasoning_effort{}`、`llm_providers.model_capacity[].reasoning_effort`、
  `WRITER_REASONING_*` env。

**JSON 实际值与模块默认值矛盾**（实测）：

| 文件 | JSON 值 | 模块默认 | 影响 |
|---|---|---|---|
| `prose_style_config.json` | `enabled: true` | `False`（`prose_style.py:103`） | 生产开着，代码兜底是关 |
| `review_escalation_config.json` | `enabled: true` | `False`（`review_triage.py:76`） | 同上 |
| `review_config.json` | `ultra:[20,15]` | `[3,5]`（`review_config.py:29–34`） | **强制审校量差 2–7 倍** |
| `audit_config.json` | `auto_absorb_threshold: 0` / `entity_pool_threshold: 15` / `beat_exhaustion_normal_ratio: 0.3` | `20` / `30` / `0.7` | 三处口径不一致 |
| `chapter_limits.json` | ultra `soft_max 8000` **<** `climax_max 15000` | — | soft_max 对高潮章不构成有效上界 |
| `.env.example:52` | `PORT=8000` | `config.py:153` = **10087** | 文档与代码矛盾 |
| `model_settings.json` | premium = balanced = `deepseek-flash` | — | **三档抽象已退化** |

**完全死掉的配置**：`free_pool.json`（**且含活密钥**）、`infrastructure.local.json`（仅测试）、
`long_memory_routing_config.json`、`long_memory_meta_guard_config.json`、`artifact_dbg.json`、
`artifact_test.json`、`test_results.json`、`_rag_ab_result.json`、`_rag_ab2_result.json`。

**活文件里的死键**：`audit_config.json` 的 `recall_match_min`、`recall_sentence_ctx`、`departure_downgrade_exit`、
`drift_enable_correction`（app 内零引用）；`outline_config.json` 缺模块声明的 7 键；
`audit_config` 的 `logic_chapter_check_enabled` / `logic_check_max_tokens` / `bible_relevant_inject` /
`bible_inject_max_chars` **代码要读但文件里没有** → 永久跑默认值、无 UI 可调。

### 8.5 安全发现（**最高优先级**）

1. **`backend/free_pool.json` 含 2 个真实 API Key，但没有任何代码读它。**
   `members.zhipu.api_key` / `members.siliconflow.api_key`；`app/free_pool.py` 源文件已删除
   （只剩 `__pycache__/free_pool.cpython-312.pyc` 与 `-313.pyc`）；
   `docs/03-系统全景图.md:93,106` 仍在描述它。
   → **模块删了，带密钥的数据文件留下了：明文凭证静置、未轮换。**
2. **`llm_providers.json` 含 6 个 provider 的 `api_key` + `base_url`，另有 6 份 `.bak_*` 时间戳快照同样含密钥**
   （`.bak_171451`、`.bak_adddsflash_102538`、`.bak_addmimo_20260911_170952`、`.bak_conc_184433`、
   `.bak_dsh_reasoning_20260911`、`.bak_t0_175351`）—— **未轮换的凭证副本**。
3. `backend/.env` 含 `ZEN_API_KEY` / `EMBEDDING_API_KEY` / `TAVILY_API_KEY` / `LLM_API_KEY`。
4. `.env.example` 里 **14 个变量零代码读取**（整个 free-pool 段失效）；`.env` 里 **19 个变量未写进 `.env.example`**。
5. `LEARNSTORY_DATA_DIR` 只在显式设置时才重定向存储 → **未隔离的测试会写生产状态**
   （`selection_cache.json` 里约 66 个测试夹具键就是证据）。

---

## 9. 重构建议

### 9.1 应该成为 LangGraph Node 的

| 现函数（行号） | 建议 Node | 理由 |
|---|---|---|
| `_plan`(3722) / `_plan_story`(3659) | `plan_research` | 单入单出、有清晰产物（modules/outline 雏形） |
| `_ensure_bible`(3896) | `build_bible` | 幂等、有 `has_bible` 短路 → 天然条件边 |
| `_outline_body`(1910) | `generate_outline`（**必须拆**） | 525 行含 8 个闭包；应拆成 `skeleton → segments(fan-out) → merge → finalize` |
| `_finalize_outline`(1495) | `finalize_outline` | 纯落库收尾 |
| `select_and_estimate`(1782) | `estimate` | 无副作用（只算+写 estimate_json） |
| `_fill_outline_details`(3216) | `fill_details` | 独立可重入 |
| `_readiness_gate`(2556) + `_serial_detail_gate`(2624) | `await_details` **条件边** | 这是「等待」语义，LangGraph 的 interrupt/conditional edge 天然表达 |
| `_prepare_chapter`(6334) | `prepare_chapter` | 纯读+组装上下文（**缓存前缀的落点**） |
| `_generate_writer_content`(6617) | `write_chapter` | 单次 LLM 调用 |
| `_commit_chapter`(7122) | **拆成 6 个 Node**（见 9.3） | 1239 行是最大单点 |
| `_maybe_run_verify`(4858) / `_maybe_logic_chapter_check`(5043) | `verify_chapter`（条件触发） | 「每 N 章」= 条件边 |
| `_next_volume_inline`(5670) | `plan_next_volume` | 天然的子图入口 = **子图递归** |
| `_finalize_book`(5602) | `finalize_book` | 终态 |
| `_run_volume_audit`(5247) / `_mid_volume_outline_correction`(5330) | `volume_audit` / `mid_volume_fix` | 独立 |

### 9.2 应该成为 LangGraph Subgraph 的

1. **`chapter_pipeline` 子图**（核心，可复用）：
   `prepare → write → postprocess → review → [rewrite]* → commit → export`
   现对应：`_prepare_chapter`(6334) → `_generate_writer_content`(6617) →
   `_commit_chapter`(7122，内部含审校循环)。
   **审校重写循环是 LangGraph 条件边的教科书场景**（当前是 `_commit_chapter` 里的嵌套 while）。

2. **`volume_pipeline` 子图**：`plan_volume → generate_chapters(map over chapter_pipeline) → volume_audit`
   现对应：`_next_volume_inline`(5670) + `_produce_volume`(5894)。
   多卷 = 子图的**递归调用**，天然替掉 `continue_volume`/`_next_volume` 两套手动续卷路径。

3. **`book_pipeline` 主图**：`plan → bible → outline → [pregen ∥ serial] → finalize`

4. **`detail_fill` 子图**：`skeleton → segments(fan-out) → merge`（`_outline_body` + Rolling 协调器）
   —— Rolling 的 `RollingStoryCoordinator`(queue+单worker) 可以直接换成 LangGraph 的 `Send` API。

### 9.3 应该删除 / 合并的

**删除**（低风险、纯收益）：
- `_fast_guard_allowed`(5491)、`_maybe_expand_knowledge_bg`(5509)、`_warn_untaught_points`(5526)、
  `_dynamic_deepen_knowledge`(5532)、`_load_untaught_points`(5558)、`_pick_due_reviews`(5569)、
  `_warn_consecutive_no_points`(5582)、`_deepen_knowledge`(8431)、`_expand_knowledge_bg`(5432)
  → **188 行 learn-only**，且 learn 只剩 3 个 API 路由；（若要保留 learn，应做成插件而非主干分支）
- `app/book23_ultra_repair.py`、`app/book25_ultra_repair.py` → 单本书修复脚本，移出 app 包
- `backend/free_pool.json`（**先轮换密钥**）、6 份 `llm_providers.json.bak_*`、
  `artifact_dbg.json`、`artifact_test.json`、`test_results.json`、`_rag_ab*.json`、
  `infrastructure.local.json`、`long_memory_{meta_guard,routing}_config.json`
- `GATEWAY_ALIASES`（`client.py:59`，**空字典**，全部调用点都是 no-op）
- `codex_resume`/`_extras_disabled`（`client.py:126`，注释自陈「兼容旧状态」）

**合并**：
- 5 个 `model-evals/import*` 端点 → 1 个参数化端点
- `/api/ops/dashboard`(4203) / `/api/agents/stats`(3926) / `/api/llm/stats`(3920) / `/api/model-stats`(3330)
  / `/api/problem-stats`(3152) → **1 个 `/api/ops/metrics`**
- `/api/books/{id}/audit/drift`(2427) 与 `/api/books/{id}/drift`(3969) → 合并
- 8 组配置读写对 → **1 个 `/api/config/{key}`**（由统一 config registry 驱动）
- `_readiness_gate`(2556) 与 `_serial_detail_gate`(2624) 的两套口径 → 1 个（代码注释已承认「避免两处口径漂移」）
- `_context_breakdown`(omen_q1.py:225) 与 `context_breakdown`(pipeline.py:8374) → **1 个定义**
- `_verify_selected`(8515) 与 `_verify_selected_safe`(5496) → 1 个

**拆分**：
- `_commit_chapter`(7122, 1239 行) → `quality_gate` / `review_loop` / `targeted_rewrite` /
  `style_dedupe` / `memory_extract` / `cards` / `export` **7 个 Node**
- `_outline_body`(1910, 525 行) → 拆掉 8 个闭包
- `main.py:3373–3917`（约 580 行私有 helper 块）→ `presenters.py`
- `pipeline.py` 本身 → `orchestrator/`（状态机）+ `outline/` + `chapter/` + `commit/` + `verify/` + `progress/`

### 9.4 让 Token 记账与缓存命中成为一等公民

**A. 修好归因（当前覆盖率 0.76%，形同虚设）**

1. **把 `context_breakdown` 从「仅 trace 开启时」改成无条件计算**（`pipeline.py:8369` 的 `if _trace_on:`
   应去掉）—— 它只是 `len()` 调用，成本可忽略。
2. **补 `system_instruction`**（当前恒 0）与 **`relationships`**（key 根本不存在，
   `memory.py:1677–1691` 的返回 dict 没有此键）。
3. **用实测比例转 token，别用 `chars/4`**：
   本库实测 **input 1.448 chars/token、output 2.58 token/字**。
   更正确的做法是**让 provider 返回 `prompt_tokens` 后按字符比例回分摊**（`prompt_tokens × block_chars / total_chars`），
   这样归因之和恒等于真实 token。
4. **消灭 `_context_breakdown` 的第二份定义**，改为从 `memory.build_context` 的返回 dict 派生。
5. **`Metric` 表补列**：`prompt_tokens` / `cached_tokens` / `reasoning_tokens` / `usage_source`，
   或**直接废掉 `Metric` 表**（trace 表已经覆盖它，且有 23% vs 100% 的覆盖率优势）。

**B. 打开缓存命中（当前章级 32.9%，天花板见 §5.6）**

1. **`memory.py:1624–1629` 的 `bible_inject.select` 是最优先要改的一处**：
   现在 `_bible_query = f"{plot} {title}"` 逐章不同 → 选出的圣经块逐章不同 →
   **全书最稳的缓存前缀在第一个块就分叉**。
   建议：**全局锚点块（世界观/纪元/规则/主角）无条件前置，相关块放 prompt 尾部**。
2. **建立「稳定前缀块」概念**：`system + 全局圣经锚点 + genre_profile + entity_registry(前缀)`
   拼成一个**逐章字节级一致**的字符串，放在 messages 最前。
3. **确保易变块（timeline/states/rag/prev_prose/style/hang/bridge）集中在尾部且互不夹隔稳定内容**。
4. **对支持 `cache_control` 的 provider 显式打标**（`{"type":"ephemeral"}`），
   并新增 `cache_write_tokens` 记账（当前完全丢弃 `cache_creation_input_tokens`）。
5. **续推轮次（4 条 messages）单独维护缓存键** —— 它与首次调用（2 条）前缀不同。
6. **修 `memory` agent（hit 仅 10.9%）** —— 它是第 3 大成本项（14.7%）却几乎不命中。
7. **`verifier` 已证明「固定 system + 小 user」能拿到 94% 命中** ——
   说明 provider 缓存本身工作良好，**问题在 prompt 组装，不在 provider**。

**C. 治 output（比缓存重要 4–5 倍，但「砍 reasoning 长度」已证伪）**

> **前置结论（务必先读）**：`test_reasoning_caps.py:5–16` 已用 74 条真实 attempt 证明
> **`reasoning_max_tokens` 上限不是成本杠杆**（editor 峰值 3853 < 上限 5000；verifier 峰值 3082 而配置不限）
> —— 文件内自述「收益 ≈ 0」。**不要在新架构里花力气调 reasoning 上限。**

按 ROI 排序的真正杠杆：

1. **【最高 ROI】默认打开 Editor 定向补丁** —— A/B 实测省 **61.6%**（§5.5-E2），
   当前 `editor_cost_config.json` 是 `low_score_patch: false`。Editor 占生产 20.3% 成本。
2. **【确定可回收】消灭 blank 空转** —— **223 次 blank = 14.36% 的 token**（§5.5-C2）。
   blank 的定义是「有请求、有消耗、零有效产出」。现有 `_MICRO_OUTPUT_DISCARDS`（`client.py:74`）
   只覆盖「<20 字正文 + >3000 字思考」这个很窄的子集，且**在生产里从未触发过**
   （`problems.jsonl` 零命中）—— 真正宽的那条路径没有任何观测。**应当在 `_record` 里按 status 聚合告警。**
3. **【结构性】按 agent 换模型，而不是限制思考长度**：
   `verifier`（reasoning 99.4%）/ `base`（98.4%）/ `editor`（81.7%）这类近乎「全思考零正文」的角色，
   应路由到**默认不思考的模型**（`TASK_TIER_{ROLE}` 已有按角色选档的基础，`config.py:42`）。
4. **【结构性】减少调用次数**：`memory` 占 14.8% 成本，hit 率只有 **10.9%**（最低），
   是重复 prompt 的重灾区；`CALLS_PER_CHAPTER` 实测 **4.08** 而非预估的 2.5。
5. **引入 per-stage / per-book token 预算与熔断**（当前**完全没有**成本上限，
   `_llm_max_attempts()` 默认 0 = 禁用）。`deadline_s` 是时间预算，不是 token 预算。
6. **修 `estimator.py`**：`per_ch_in = 5500` 应为 ~**35,000**（实测 prompt/章）；
   每章总量应从 10,450 改成 **60,097**；`CALLS_PER_CHAPTER` 从 2.5 改成 **4.08**；
   「0.8 token/字」改成 **2.58**。**并且要引入 `actual_tokens` 字段做闭环**
   （当前 `est_tokens = total_tokens` 被赋值两次：`estimator.py:180`、`pipeline.py:1685`，
   **没有任何真实值回流**）。
7. **修成本口径的 4 个一致性缺陷**（§5.5-F）：去掉 reasoning 重复计入、合并两份 Credits 公式、
   按 provider 分价、把 `native` 门槛提高到「5 项齐全」或新增 `usage_complete` 门控。
8. **给 `generation_traces/attempts/spans` 加保留策略** —— 当前**零 retention**，
   且 `GenerationAttempt`/`GenerationSpan` **不在 `BOOK_SCOPED_MODEL_NAMES`**（`models.py:696–718`）
   → 删书留下孤儿（生产已存在 **297 条 `book_id = NULL` 的 attempt**）。
9. **废掉或修复 `metrics` 表** —— 4373 行里 **976 行 `completion_tokens = 0`**（历史 `int(len(text)*0.8)` 污染），
   与真实 0 无法区分，而 `main.py:3314` 的 `_model_stats()` 仍在用它喂 UI（`or 0`）。
   两套 token 存储（`metrics` vs `generation_attempts`）从未对账。
10. **补上 per-chapter 成本能力** —— 90% 的 trace 没有 `chapter_idx`
    （`agents/base.py:54–63` 隐式建 trace 只带 `book_id`）。
    **`GenerationAttempt` 应直接加 `chapter_id` 列**，否则「按章算钱」永远只能靠 3% 的样本。
11. **暴露一个 Credits 端点** —— 当前唯一的成本看板是 `docs/COST_OPTIMIZATION_STATUS.md` 这个 markdown；
    `/api/ops/*` 有 trace / 延迟 / agent 计数，**没有任何 Credits 端点**。
12. **让 `discount_factor()` 进生产** —— 它现在**零生产调用方**；
    而 **30.3% 的流量本来就落在 0.8× 窗口内**（`mimo-v2.5` 是 **0%**，一分钱折扣没吃到）。
    即使不做错峰调度，**成本口径也必须乘这个系数**，否则夜间账单低估 20%。

### 9.5 状态机与并发的重构要点

1. **`Book.phase` 变成显式枚举 + 转移表**，把 43 处散落赋值收敛成
   `book.transition_to(Phase.X)`，并加**断言**（非法转移直接抛）。
2. **`Chapter.status` + `content_path` 封装成单一转移函数**（当前靠 19 处手工成对赋值维持）。
3. **`ACTIVE_PHASES` 补 `architecting`**（`pipeline.py:535`）—— 一行修复一个孤儿漏洞。
4. **把进程内 `_running` / `_cancel_flags` 换成持久化状态**（DB 或 Redis）——
   当前多进程部署下孤儿检测与取消都会失效。
5. **收掉「Serial Inside, Parallel Across Stages」的两处手工顺序约定**：
   `_merge_outline_progress` 必须先 commit 再 `_kick_pregen`（`pipeline.py:3001–3002`）——
   用 LangGraph 的边依赖天然保证，而不是靠注释和 join。
6. **修 `_produce_first` 的 `join(timeout=1800)`**（`pipeline.py:4138`）——
   超时后继续执行会让 pregen 与 serial 双写同一章。
7. **`_claimed_run` 的顶层异常必须 `log_problem`**（`pipeline.py:560–574` 当前静默吞）。

---

## 附录 A · 关键常量速查

| 常量 | 值 | 位置 |
|---|---|---|
| `_QUICK_BATCH_SIZE` | 4 | `pipeline.py:4272` |
| `_STEPS_TAIL` / `_STEPS_TARGET` | 120 / 132 | `pipeline.py:895,897` |
| `WEIGHTS` | map_ready 5 / awaiting_confirm 10 / bible_done 18 / first_chapter 30 / serializing_end 90 / completed 100 | `pipeline.py:876` |
| `ACTIVE_PHASES` | `(planning, writing_first, serializing, finalizing)` | `pipeline.py:535` |
| `idle` (流式静默) | `min(60, max(20, budget/6))` | `client.py:1088` |
| 基础退避 | `min(60, 5*2^min(attempt,4))` | `client.py:458` |
| 无指示 429 退避 | 首次 30s，其后 `min(600, 90*2^(n-2))` | `client.py:489` |
| `max_tokens` | `max(4096, reasoning_max_tokens*4)` | `client.py:816` |
| 续推 LRU | 5 | `client.py:453` |
| 续推合并阈值 | ≥40 字重叠 | `client.py:624` |
| 微产出判废 | 正文 <20 字 且 思考 >3000 字 | `client.py:409` |
| JSON 抢救候选点 | 最近 20 个闭合点 | `client.py:1343` |
| `CREDIT_WEIGHTS` | hit 2 / miss 100 / output 200 | `cache_attribution.py:58` |
| 错峰窗口 | 北京 0:00–8:00，0.8× | `offpeak.py:4,46` |
| 默认模型并发 | 6 | `llm_providers.py:394` |
| `outline_batch_for_model` | `max(10, min(max_out*0.5/150, 150))` | `llm_providers.py:161–162` |
| 实测 chars/token (in) | **1.448** | 本库统计 |
| 实测 token/字 (out) | **2.58** | 本库统计 |

## 附录 B · 引用文件清单

```
E:\AI_project\story_llm\backend\app\pipeline.py                8606 行
E:\AI_project\story_llm\backend\app\main.py                    4629 行
E:\AI_project\story_llm\backend\app\llm\client.py              1503 行
E:\AI_project\story_llm\backend\app\memory.py                  3202 行
E:\AI_project\story_llm\backend\app\llm_providers.py            642 行
E:\AI_project\story_llm\backend\app\continue_story.py          1605 行
E:\AI_project\story_llm\backend\app\rolling_story_pipeline.py   355 行
E:\AI_project\story_llm\backend\app\observability.py            392 行
E:\AI_project\story_llm\backend\app\safe_guard.py               257 行
E:\AI_project\story_llm\backend\app\commit_recovery.py          212 行
E:\AI_project\story_llm\backend\app\offpeak.py                  209 行
E:\AI_project\story_llm\backend\app\estimator.py                197 行
E:\AI_project\story_llm\backend\app\trace_service.py            183 行
E:\AI_project\story_llm\backend\app\model_settings.py            53 行
E:\AI_project\story_llm\backend\app\trace_config.py              64 行
E:\AI_project\story_llm\backend\app\abort.py                     55 行
E:\AI_project\story_llm\backend\app\llm_stats.py                 89 行
E:\AI_project\story_llm\backend\app\job_queue.py                141 行
E:\AI_project\story_llm\backend\app\runctx.py                     7 行
E:\AI_project\story_llm\backend\app\recovery_orchestrator.py     20 行
E:\AI_project\story_llm\backend\agents\writer.py                566 行
E:\AI_project\story_llm\backend\benchmark\cache_attribution.py  587 行
E:\AI_project\story_llm\backend\learnstory.db  (只读查询)
E:\AI_project\story_llm\backend\benchmark\results\*.md / *.json
```
