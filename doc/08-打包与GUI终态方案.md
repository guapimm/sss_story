# 08 · 打包与 GUI 终态方案（电脑直接安装）

> **定位**：回答「当前方向是否支持做成 Windows / Mac 直接安装的形式」，并给出阻塞点与分期。
> **性质**：**排查与规划文档，尚未实现**。本文记录的全是**实测证据**，不是设想。
> **本轮范围**：用户已拍板 **只写文档、暂不改代码**（2026-09-26）。
> **上游决策**：与 `00-决策记录.md` 的 **D-05** 存在冲突，见 §7 —— **PG-2 开工时必须先修订 D-05**。

---

## 0. 三十秒结论

**目标架构上兼容，但当前形态不支持。** 差距分三层，且**集中在路径解耦**上：

| 层 | 状态 | 说明 |
|---|---|---|
| **运行期**（解耦） | 🟡 **4 处路径硬编码** | 其中 2 处是纯代码改动，改动量小；今天暴露不出来，只因本机是 **editable 安装** |
| **分发期**（打包） | ⬜ **完全缺失** | 全仓 **0 个** PyInstaller/Nuitka spec、**0 个**启动脚本、**无** CI |
| **界面期**（GUI） | ⬜ **完全缺失** | `api/` 是**空文件**，全仓 **0 个** html/vue/jsx；**无**任何前端/HTTP 依赖 |

★ **最需要记住的一句**：`REPO_ROOT` 硬编码在本机**从未暴露**，因为项目是 **editable 安装**
（`.venv\Lib\site-packages\_editable_impl_storyforge.pth`）。
**换成正常安装（wheel / 打包）的那一刻，4 处硬编码会同时失效。**

**用户已拍板的目标形态**（2026-09-26）：

| 项 | 结论 |
|---|---|
| 最终形态 | **先让安装包跑通，再补桌面 GUI**（GUI 是紧接的下一阶段） |
| 平台 | **Windows 优先**，架构预留 Mac |
| 本轮动作 | **只在文档记录，暂不改代码** |

---

## 1. 实测证据（本轮核对，可复现）

| 检查项 | 实测值 | 取证方式 |
|---|---|---|
| 基线 HEAD | `c74e5a1`（修复变异脚本的两处真问题） | `git log -1 --format="%H %s"` |
| 提交数 | **81** | `git rev-list --count HEAD` |
| dirty | 仅 `doc/README.md`（一行，**非本轮改动**） | `git status --porcelain` |
| 测试 | **1048 passed / 15 skipped**（54.26s） | `uv run pytest` |
| 自检 | `doctor: PASS`（10 项） | `uv run storyforge doctor` |
| GUI 层 | `src/storyforge/api/__init__.py` **内容为空**（0 字节语义） | `Get-Content` |
| 界面资产 | **0 个** html / vue / jsx / tsx / css | 全仓文件名扫描 |
| 前端/HTTP 依赖 | **无** fastapi / uvicorn / flask / starlette / PySide / PyQt | `Select-String pyproject.toml,uv.lock` |
| 打包配置 | **0 个** spec / .ps1 / .cmd / .bat 启动脚本 | 全仓文件名扫描 |
| CI | **无** `.github` | 目录检查 |
| 安装方式 | **editable**（`_editable_impl_storyforge.pth`） | `.venv\Lib\site-packages` |
| `config/` | 3 个 toml + 18 题材 + 25 修仙流派 + 16 通用套路 + 2 映射 | 目录枚举 |
| `thresholds.toml` | **不存在**（`01-总体架构.md` 目录树里列了，实际未建） | `Test-Path` |

**结论**：`doctor` 全绿**不能**用来证明「可安装」—— 它检查的是数据目录可写、
表/外键/能力声明/分层纪律，**没有一项覆盖"装到用户机器上还能不能跑"**。

---

## 2. 阻塞点清单

| # | 位置 | 问题 | 严重度 |
|---|---|---|---|
| **B-1** | `src/storyforge/infra/prompts/library.py:55` | `PROMPTS_DIR = REPO_ROOT / "src" / "storyforge" / "infra" / "prompts"` —— **模块级常量，不可注入**。非 editable 安装后该目录不存在 → `_load()` 抛 `FileNotFoundError`，**写作/规划链路整体不可用** | ★ 硬阻塞 |
| **B-2** | `src/storyforge/config.py:35` | `REPO_ROOT = Path(__file__).resolve().parents[2]`。wheel 安装后模块位于 `site-packages/storyforge/`，`parents[2]` 解析到 `<venv>\Lib` → `DEFAULT_CONFIG_DIR` / `DEFAULT_DATA_DIR` **双双指向错误位置**（见 §3 的实测要求） | ★ 硬阻塞 |
| **B-3** | `src/storyforge/config.py:425-426` | `dotenv_paths()` **只**返回 `REPO_ROOT / ".env"`。安装版没有仓库根 → 用户**无处安放 API key**，真实链路无法启动 | ★ 硬阻塞 |
| **B-4** | `src/storyforge/cli.py:64-76` | `cmd_arch_check` 以 `cwd=REPO_ROOT` 运行 `pytest tests/contract` —— 安装版**没有 `tests/`** | 中 |
| **B-5** | `src/storyforge/doctor.py:147` | 扫描 `REPO_ROOT / "src" / "storyforge"` 判定分层纪律 —— 安装版**没有源码树**，该项要么误报要么失效 | 中 |

### 2.1 ★ 附带发现：配置来源不一致（既有缺陷，PG-0 一并统一）

同一个 `config_dir`，**两条不同的读取路径**：

| 读法 | 使用处 |
|---|---|
| 模块常量 `DEFAULT_CONFIG_DIR` | `infra/llm/capability.py:321`（`providers.toml`） |
| `get_settings().config_dir` | `modes/scales.py:88`、`modes/genres.py:116`、`modes/mappings.py:68`、`modes/flows.py:150` |

**后果**：设 `STORYFORGE_CONFIG_DIR` 能改题材/档位/流派，
**却改不动 `providers.toml`** —— 模型路由配置搬不走。
这与 `.env` 被 `llm_providers.json` 静默覆盖（旧项目 L-06）是**同一类 "两个来源竞争同一字段"** 的病，
本项目在 `config.py` docstring 里明令禁止，此处的漏网之鱼。

### 2.2 可注入性核对（决定改动量）

| 目标 | 能否运行时注入 | 依据 |
|---|---|---|
| `data_dir` | ✅ **可以** | `Settings` 字段，`env_prefix="STORYFORGE_"` |
| `config_dir` | ✅ **可以**（但见 §2.1 漏网处） | 同上 |
| `PROMPTS_DIR` | ❌ **不可以** | 模块级常量 |
| `.env` 位置 | ❌ **不可以** | `dotenv_paths()` 硬编码 |

**故 5 个阻塞点里，只有 B-1 / B-3 需要真正的代码改动**；B-2 是"确认 + 统一用法"，
B-4 / B-5 可降级为"可选检查"（安装版跳过或改用打包清单校验）。

---

## 3. ★ PG-0 第一件事：实测 B-2（当前为**推断**，未验证）

B-2 的结论是**从代码推断**出来的，**尚未实测**。PG-0 开工时必须先证实或推翻：

```powershell
# 在临时目录建干净 venv，从本仓正常安装（非 editable），再跑 doctor
uv build                                  # 产出 dist/storyforge-0.1.0-py3-none-any.whl
python -m venv $env:TEMP\sf-probe
& $env:TEMP\sf-probe\Scripts\pip install (Get-ChildItem dist\*.whl).FullName
& $env:TEMP\sf-probe\Scripts\python -c "import storyforge.config as c; print(c.REPO_ROOT, c.DEFAULT_CONFIG_DIR, c.DEFAULT_DATA_DIR)"
& $env:TEMP\sf-probe\Scripts\storyforge doctor
```

**预期（待证实）**：`REPO_ROOT` 落在 venv 内 → `config` / `prompts` 找不到 → `doctor` FAIL。
**若实测与推断不符，先修正本文，再动代码。**

---

## 4. 目标形态

```
用户拿到安装包 → 双击安装 → 出现可启动的程序
  ├─ 首次运行：引导填 API key（写入用户目录，不进程序目录）
  ├─ 数据落在用户目录（不在程序安装目录）
  ├─ 程序自带 config/ 与 prompts/（随包分发，不依赖源码树）
  └─ 桌面 GUI：选题材/流派 → 联网研究逐轮可见 → 逐条确认知识点与平替修辞
                → 世界观 → 写作 → 阅读正文
```

**必须随包分发的运行时资产**（当前都在源码树里）：

| 资产 | 现状路径 | 规模 |
|---|---|---|
| Prompt 模板 | `src/storyforge/infra/prompts/*.md` | 4 个 md + `library.py` |
| 配置 | `config/*.toml` | 3 个 toml |
| 题材 | `config/genres/*.toml` | 18 个 |
| 流派 | `config/flows/xianxia/*.toml` | 25 个 |
| 通用套路 | `config/flows/tropes/*.toml` | 16 个 |
| 种子映射 | `config/mappings/*` | 2 个 |

**数据目录策略**：安装版数据必须移出程序目录 →
Windows 建议 `%APPDATA%\storyforge`（`data_dir` 已可由 `STORYFORGE_DATA_DIR` 注入，**今天就能做**）。
注意 `data/` 当前含真实运行数据（`storyforge.db` / `research_cache.json`），且已被 `.gitignore` 忽略。

**不随包分发**：`tests/`（B-4/B-5 依赖它，故需降级）、`wangwenxiaoshuo/`（**266 MB** 参考语料，只读、零代码引用）、`.git`（**177.5 MB**）。

---

## 5. 分期计划（每阶段可独立验收）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **PG-0** 路径解耦（前置，与主线不冲突） | 实测 B-2；B-1 改为可注入（与 `get_settings().config_dir` 同源）；B-3 增加用户目录候选；统一 §2.1 的配置来源；B-4/B-5 降级为可选检查 | 非 editable 安装到临时 venv 后 `doctor` **PASS** 且 `_load()` 能取到 prompt；`pytest` 仍 1048 passed；`baseline` 口径不变 |
| **PG-1** Windows 打包冒烟 | 打包产物 + 启动器；`config/` 与 `prompts/` 随包分发；数据落 `%APPDATA%` | 干净 Windows 上（**无 Python/uv**）双击可跑：`doctor` PASS、`flows` 有输出；全程不依赖源码树 |
| **PG-2** GUI 底座（HTTP API） | 落地 D-05 承诺过的最小 API：建书 / 查进度 / 读章节 / 看报告；`api/` 目前是**空文件** | API 契约测试；**GUI 与 CLI 共用同一 Service 层**，不各自实现编排（否则重演旧项目两套逻辑分叉） |
| **PG-3** 桌面 GUI | 题材/流派选择、联网研究**逐轮可视化**、L-1/L-2 **逐条确认 + 可编辑可回退**、正文阅读 | 对照 learn 目标流程 ①~⑥ **逐环节可操作**；无"黑盒"环节 |
| **PG-4** Mac 打包（可选） | `.app` 打包 | 需 **Apple 开发者账号**做签名与公证，成本与周期独立评估 |

**PG-0 的具体验收命令**（不要只跑 `pytest` 就算过）：

```powershell
$env:PYTHONIOENCODING="utf-8"
uv run pytest                     # 应仍 1048 passed / 15 skipped
uv run storyforge doctor          # 应仍 PASS
# 加上 §3 的临时 venv 非 editable 安装 + doctor 复验（这是 PG-0 的真正判据）
```

---

## 6. 待用户决策（不得自行拍板）

| # | 项 | 备选 | 影响 |
|---|---|---|---|
| 1 | 打包工具 | PyInstaller / Nuitka / 仅 wheel + 引导脚本 | 决定产物体积、启动速度、杀软误报率 |
| 2 | GUI 技术栈 | 本地 Web UI / 原生（PySide）/ 其他 | 决定 PG-2/PG-3 工期与维护成本 |
| 3 | HTTP 底座 | FastAPI / 标准库 `http.server` | D-05 说"最简技术"；引入依赖 vs 手写 |
| 4 | 数据目录 | `%APPDATA%` / 用户自选 | 决定升级、备份、卸载行为 |
| 5 | Mac 时机 | 与 Windows 同时 / 后续独立 | 需 Apple 账号，工期显著增加 |
| 6 | 升级/回滚 | 覆盖安装 / 版本并存 | 涉及用户数据的迁移策略 |

---

## 7. ★★ 与既有决策的冲突（**必须显式记录**）

**`00-决策记录.md` 的 D-05** 写的是：

> **一期不做前端**，只提供最小 HTTP API + 一个**只读**查看页（章节/大纲/打分卡/Token 报告）供人工审稿。

而用户 2026-09-26 的目标是**可安装的桌面 GUI 应用**（全流程可视化）。
**两者冲突。**

| 项 | 处置 |
|---|---|
| 本轮 | **只在本节标注，不改 `00-决策记录.md`** —— 避免在未实现时改动已生效决策 |
| **PG-2 开工时** | **必须先修订 D-05**，再写 API/GUI 代码 |
| 不得做的事 | ❌ 静默违反 D-05（本项目明令禁止"设计对了却没人执行"这类静默失效） |

**注意 D-05 里仍然有效的部分**：「后端不得为前端便利牺牲接口纯度」—— PG-2/PG-3 必须遵守，
GUI 只能经 Service 层取数据，不得穿透到 `domain/` 或直接读 SQLite。

---

## 8. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **改路径引入回归** | B-1/B-3 是全局单点，改错会让**所有** prompt 与密钥加载失败 | PG-0 必须先有非 editable 安装的验收证据；`baseline` 口径 + 1048 测试双保险 |
| **打包后杀软误报** | PyInstaller 单文件在 Windows 上常见 | 优先目录模式；必要时签名 |
| **GUI 与 CLI 逻辑分叉** | 若 GUI 自己实现编排，会重演旧项目多套逻辑 | GUI 只调 Service 层；PG-2 验收含"共用同一层" |
| **体积膨胀** | 语料 266 MB + `.git` 177.5 MB 若误入包 | 打包清单显式排除；产物做体积上限检查 |
| **Mac 签名成本** | 无 Apple 账号则无法分发 | PG-4 独立排期，不阻塞 Windows |
| **文档与实测漂移** | 数字写死会误导接手人（本项目已发生过） | 本文数字标注取证方式与日期；接手前重新实测 |

---

## 9. 明确不做的事（防范围蔓延）

| 不做 | 理由 |
|---|---|
| 本轮改任何代码 | 用户已拍板「只在文档记录」 |
| 本轮建 GUI / API / 打包配置 | 同上；且尚无技术选型 |
| 数据迁移 / 旧数据导入 | D-06：不迁移旧数据 |
| 自动更新服务 | 需要服务端，超出单机单用户范围 |
| 多用户 / 权限 / 分布式 | `01-总体架构.md` §7 已明确不做 |

---

## 10. 下一步

1. **等用户对 §6 的 6 项决策**（打包工具 / GUI 栈 / HTTP 底座 / 数据目录 / Mac 时机 / 升级策略）。
2. 决策落地后，**先修本文 §7 的 D-05 冲突**，再启动 PG-0。
3. PG-0 的**第一个动作是实测 §3 的 B-2**，不是改代码 —— 推断必须先变成证据。
