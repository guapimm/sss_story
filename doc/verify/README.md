# 多 Agent 流水线验收入口与证据指南

> 适用范围：本仓库（story_commandcode）的 Orca worktree / 多 Agent 流水线。
> 本文只描述**源码级预验收**；发布级验收的边界见 §5，不能用本文的 `quick`/`full` 结果代替。
>
> 事实来源：`scripts/acceptance.ps1`、`scripts/orca-setup.ps1`、`scripts/orca-archive.ps1`、
> `pyproject.toml`、`requirements/README.md` 的当前实现（2026-10-20 仓库状态）。脚本行为变化时，
> 先改脚本再同步本文，避免文档与实现漂移。

---

## 1. 验收入口

统一入口是 `scripts\acceptance.ps1`，模式由 `-Mode` 指定，默认 `quick`：

```powershell
# 快速：doctor + arch-check（默认模式）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Mode quick

# 执行器自证：只跑 doctor（沙箱安全，不跑 arch-check）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Mode worker

# 契约：只跑 tests/contract，不跑 doctor/arch-check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Mode contract

# 全量：quick + 全量 pytest
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance.ps1 -Mode full
```

| 模式 | 实际执行内容 | 耗时量级 | 典型用途 |
|---|---|---|---|
| `quick` | `doctor` + `arch-check` | 约 1 分钟 | 每次改动后、提交前；确认环境、配置、存储和架构契约没有基本回归 |
| `worker` | 只跑 `doctor` | 秒级 | 执行器在受限沙箱内自证；完整验收仍用 `quick`/`full` |
| `contract` | 只跑 `tests/contract`（契约/架构不变量） | 分钟级，通常明显短于 `full` | 只改契约相关逻辑时的快速回归；**不覆盖** `doctor` |
| `full` | `quick` 的全部内容 + 全量 `pytest` | 约 70 分钟（长套件） | 合并/交付前的完整源码级回归 |

补充说明：

- `worker` 只跑 `doctor`，供执行器在受限沙箱内自证；它不跑 `arch-check`/pytest，**不能替代** `quick`/`full` 的完整验收。
- 脚本根目录优先取 `$env:ORCA_WORKTREE_PATH`，未设置时取 `scripts\` 的父目录；所有检查都在该根目录下执行。
- 检查命令统一通过 `uv run --extra dev` 运行，因此依赖版本以 `uv.lock` 为准，且必须带 `dev` extra（见 §6.2）。
- `doctor` 是自检（配置 / 存储 / 外键 / 能力声明 / 分层纪律等）；`arch-check` 内部会再启动一次
  `pytest tests/contract`。所以 `quick` 已经包含契约检查，`contract` 只是把这一步单独拿出来跑。
- 超时上限：`doctor` 300 秒，`arch-check` 600 秒，`full`/`contract` 的 pytest 14400 秒（4 小时）。
  超时会被记为退出码 `124`，不会被当成通过。
- `arch-check` 执行期间，脚本会临时把 `PYTEST_ADDOPTS` 设为
  `-p no:cacheprovider --basetemp=<缓存根>\pytest\story-arch-<时间戳>`，并在结束后恢复原值；
  `full`/`contract` 也使用 `-p no:cacheprovider` 和显式 `--basetemp`，避免把测试临时文件写进仓库。
- 退出码：所有检查都为 0 时脚本 `exit 0`；任何一项非 0（含超时）时脚本 `exit 1`。
  即使失败，也会写出报告，便于定位。
- 可选的 `-TaskId <taskId>` 会原样写入报告，默认是 `manual`；流水线里应传任务合同中的 `taskId`。
- 验收脚本的缓存根由脚本按盘符派生，当前实现是 `<盘符>:\orca\caches`（例如 `E:\orca\caches`）。
  这与 §2 中 orca setup/archive 的 `.caches` 约定不是同一套路径；两者的已知偏差见 §6.4。

---

## 2. Orca worktree setup / archive 与缓存位置

### 2.1 `scripts\orca-setup.ps1`：worktree 初始化

这是 Orca 创建 worktree 后运行的 setup 钩子，必须在 Orca 注入 `ORCA_WORKTREE_PATH` 的环境里执行；
缺少该变量或 worktree 不存在时脚本直接报错退出。它做四件事：

1. **建立隔离缓存目录**：`$cacheRoot = $wt\.caches`，并设置
   - `UV_CACHE_DIR = $wt\.caches\uv`
   - `npm_config_cache = $wt\.caches\npm`
   - `CARGO_TARGET_DIR = $wt\.caches\cargo\story_commandcode`
   - `PYTHONDONTWRITEBYTECODE = 1`

   同时创建 `.caches`、`.harness\evidence`、`data`。缓存放在 **worktree 内**，不写 C 盘，也不依赖
   数据盘上的共享缓存目录；这样受限沙箱只需要 worktree 可写即可自验，归档时也能随 `.caches` 一起清理。

2. **安装 Python 依赖**：存在 `uv.lock` 时执行 `uv sync --frozen --extra dev`，失败即抛错。
3. **安装 Tauri CLI 依赖**：存在 `package-lock.json` 时执行 `npm ci --no-audit --no-fund`；
   不构建 Tauri，`CARGO_TARGET_DIR` 已指向 worktree 内缓存，构建按需进行。
4. **写 `task.env`**：记录 `WORKTREE`、`UV_CACHE_DIR`、`CARGO_TARGET_DIR`、
   `STORYFORGE_DATA_DIR=$wt\data`、`STORYFORGE_HTTP_PORT=4700 + hash`。
   `task.env` 不提交，`.gitignore` 已忽略；脚本**不写 `.env`**，密钥由负责人本机提供。

脚本明确不复制 `src-tauri/target`、`dist`，也不把缓存写到 C 盘或共享盘。

### 2.2 `scripts\orca-archive.ps1`：worktree 归档清理

这是 Orca 归档 worktree 时的清理钩子：

- 没有 `ORCA_WORKTREE_PATH` 或目录不存在时，打印跳过信息并 `exit 0`，不报错。
- 若存在 `.harness\serve.pid`，先按其中记录的 PID 停止本 worktree 的 `serve`，再删除 PID 文件。
- 只清理**可再生成**的依赖/缓存：`.venv`、`node_modules`、`.caches`、`task.env`、`.pytest_cache`。
- **永久保留** `data\`（书稿数据）与 `.harness\evidence\`（验收证据）。归档后再次 setup 会重新拉依赖，
  但书稿和证据不会丢。

### 2.3 缓存约定一句话

orca setup/archive 的执行器缓存统一在 **worktree 内 `.caches`**：`uv`、`npm`、`cargo` 各自有子目录；
不写 C 盘/共享盘缓存。验收脚本 `acceptance.ps1` 的缓存根是另一套实现，见 §6.4。

---

## 3. 报告与证据目录

### 3.1 `.harness\acceptance\`：验收脚本的机器报告

`acceptance.ps1` 每次运行都会在仓库根的 `.harness\acceptance\` 下生成：

- 报告：`acceptance-<yyyyMMdd-HHmmss>.json`
- 每个检查的日志：`<yyyyMMdd-HHmmss>-<check>.log`，其中 `<check>` 是 `doctor`、`arch-check`、
  `pytest` 或 `pytest-contract`

日志内容是子进程的 stdout，加上 `--- stderr ---` 分隔线和 stderr，使用 UTF-8（无 BOM）写入。
报告是**本机运行产物**，`.harness/acceptance/` 已被 `.gitignore` 忽略，不应提交；需要长期留存的结论
应另存到 §3.2 的证据目录。

报告 JSON 的字段（`schemaVersion` 当前为 `storyforge.acceptance/1`）：

| 字段 | 含义 |
|---|---|
| `schemaVersion` | 报告格式版本，当前 `storyforge.acceptance/1` |
| `mode` | `quick` / `worker` / `contract` / `full` |
| `taskId` | 调用方传入的任务 ID，默认 `manual` |
| `root` | 运行验收的仓库/worktree 根目录 |
| `head` | 报告生成时的 `git rev-parse HEAD` |
| `gitStatusCount` | 报告生成时 `git status --porcelain` 的行数；只作记录，不参与通过判定 |
| `passed` | 所有 `results[].exitCode` 都为 0 时为 `true` |
| `results` | 每个检查的数组，元素含 `check`、`exitCode`、`durationSec`、`log` |
| `generatedAt` | ISO 8601 时间戳（含时区） |

示例（字段值仅示意）：

```json
{
  "schemaVersion": "storyforge.acceptance/1",
  "mode": "quick",
  "taskId": "manual",
  "root": "E:\\orca\\workspaces\\story_commandcode\\verify-doc",
  "head": "<git HEAD>",
  "gitStatusCount": 1,
  "passed": true,
  "results": [
    {
      "check": "doctor",
      "exitCode": 0,
      "durationSec": 12.3,
      "log": "E:\\orca\\workspaces\\story_commandcode\\verify-doc\\.harness\\acceptance\\20261020-170350-doctor.log"
    }
  ],
  "generatedAt": "2026-10-20T17:03:50.7947596+08:00"
}
```

查看最近一次报告：

```powershell
Get-ChildItem .harness\acceptance\acceptance-*.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Raw
```

注意：`passed` 与脚本退出码一致；但请以**进程退出码**为最终判据（CI/验收方应独立复跑），
不要只看 JSON 里的字段或 worker 的转述。

### 3.2 `.harness\evidence\<REQ-ID>\<taskId>\`：人工/流水线证据

按 `requirements/README.md` 的约定，目录形态是：

```
.harness/evidence/<REQ>/<task>/
```

其中 `<REQ>` 即 `REQ-ID`，`<task>` 即任务合同里的 `taskId`。展开写就是：

```
.harness/evidence/<REQ-ID>/<taskId>/
```

- `REQ-ID` 格式为 `REQ-YYYYMMDD-slug`；`taskId` 取自任务合同（`task.json`）里的 `taskId`。
- 每个任务一个目录；证据**不要**放进 `requirements/` 合同目录。
- 建议至少包含：执行的命令、退出码、stdout/stderr 日志、`git status`/`git diff` 快照、必要的环境说明。
  例如现有目录中就有 `dsh-run/git-status.txt` 这类做法。
- 证据中不得包含密钥、token、小说正文或大体积测试临时目录；日志要能定位失败项，但不要复制无关内容。
- worker 自报不算通过：验收方应独立复跑命令，并把复跑日志（可与 worker 的日志分文件名）放进同一
  `<taskId>` 目录，便于对照。
- `orca-archive.ps1` 会保留整个 `.harness\evidence\`；`.harness/evidence/.gitkeep` 用于保留空目录。
- 与 §3.1 的分工：`.harness/acceptance/` 是脚本自动生成的原始报告；`.harness/evidence/` 是任务级
  可追溯材料。需要长期复核时，把关键报告/日志复制或摘要到证据目录，而不是依赖被忽略的运行目录。

---

## 4. Worker 角色边界

流水线 worker 的写入范围由任务合同的 `allowedPaths` 决定：

- **只允许写任务 `allowedPaths` 中列出的路径**；没列出的文件一律不改。
- 明确禁止改：`tests/`、`pyproject.toml`、`uv.lock`、`AGENTS.md`、`requirements/`，以及 CI 配置。
  这些由规划侧/负责人维护；测试、依赖、契约和需求合同不能由执行 worker 顺手修改。
- 共享文件（例如多个任务都会用到的脚本、配置、文档）如需改动，先回到主机器/规划侧确认，
  不要在各自 worktree 里各改一份。
- 如果发现必须越界才能完成，正确做法是停止并报告“需要修改 `<path>`”，而不是静默扩大 diff。
- 交付前用 `git diff --name-only`（以及 `git status --short` 看未跟踪文件）核对范围；只应出现
  `allowedPaths` 内的路径和任务要求的证据文件。
- worker 的“已完成/已测试”只是自报；验收方必须独立复跑 §1 的命令，并以退出码和证据为准。

---

## 5. 源码级预验收 ≠ 发布级验收

这是两条不同的验收线，不能互相替代：

| 维度 | 源码级预验收（本文 §1） | 发布级验收 |
|---|---|---|
| 对象 | 当前 worktree 的源码 + `.venv` | 最终安装产物（非 editable wheel / 安装包） |
| 环境 | 开发机，有 Python/uv，可复用缓存 | **干净 Windows**，不预装 Python/uv |
| 内容 | `doctor`、`arch-check`、`pytest` | 安装/启动/卸载/升级；离线 `doctor`/`flows`；用户数据目录隔离；GUI/桌面壳冒烟 |
| 网络/模型 | 默认离线，不调用真实云端/收费 API | 真实云端调用必须单独授权、单独记录，不混在自动验收里 |
| 能证明 | 配置、存储、外键、能力声明、分层/契约、回归测试在源码树中通过 | 最终产物在目标机器上可安装、可运行、资源随包、不写安装目录、不丢用户数据 |
| 不能证明 | 安装态资源完整、安装路径正确、无 Python 环境可用、真实云端可用 | 源码分支的日常回归速度（发布验收很慢，不适合每次提交） |

关键教训（详见 `doc/06-风险与开放问题.md` §8 与 `doc/08-打包与GUI终态方案.md`）：

- PG-0 曾出现**源码树 `doctor` 通过，但安装态缺 `config/`、prompt 路径锚错、`doctor` 假通过/缩水、
  `flows` 空结果仍退出 0**。所以“源码树全绿”不能证明“安装版可用”。
- 发布级验收必须在**干净 Windows、无 Python/uv** 的环境对最终安装产物做冒烟；CLI/源码树的通过
  不能代替安装态证据。
- 真实云端/收费调用是红线：必须由负责人当次明确授权，不能由自动验收脚本擅自发起；证据中不记录密钥。
- 因此在交接/汇报里请写清楚口径，例如“**源码级预验收通过（quick/full）**”，不要简写成
  “发布验收通过”。发布级验收至少记录：产物版本/哈希、操作系统、干净环境说明、命令、退出码和日志。

---

## 6. 常见坑

### 6.1 PowerShell 5.1 需要 UTF-8 BOM

本仓库的 `.ps1` 脚本含中文注释/字符串，必须以 **UTF-8 with BOM**（文件头 `EF BB BF`）保存。
Windows PowerShell 5.1 在没有 BOM 时可能按系统代码页（GBK）读取，导致中文乱码、字符串被截断甚至
解析错误；PowerShell 7 也会受编码设置影响。当前三个脚本都带 BOM，编辑时不要被编辑器/格式化工具
悄悄去掉。

检查 BOM：

```powershell
$b = [IO.File]::ReadAllBytes('scripts\acceptance.ps1')
'BOM=' + ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
```

显式写 BOM（避免依赖不同 PowerShell 版本的 `-Encoding UTF8` 语义）：

```powershell
$text = Get-Content -Raw -Encoding UTF8 scripts\foo.ps1
[IO.File]::WriteAllText(
  (Join-Path (Get-Location) 'scripts\foo.ps1'),
  $text,
  [Text.UTF8Encoding]::new($true)
)
```

在 Windows PowerShell 5.1 中 `Set-Content -Encoding UTF8` 会写 BOM；在 PowerShell 7 中请用
`-Encoding utf8BOM`。Markdown 文档本身用 UTF-8 无 BOM 即可，BOM 要求针对 `.ps1`。

### 6.2 pytest 必须带 `--extra dev`

`pytest` 和 `pytest-asyncio` 在 `pyproject.toml` 的 `[project.optional-dependencies].dev` 中，不在默认
运行依赖里。正确跑法：

```powershell
uv sync --frozen --extra dev
uv run --extra dev pytest
```

直接 `uv run pytest`（不带 `--extra dev`）可能报 `No module named pytest`，或用到未同步的开发依赖。
`acceptance.ps1` 的所有 pytest/doctor/arch-check 调用都已带 `--extra dev`；手工复跑时不要漏掉。

### 6.3 `.pytest_*` 遗留目录

本机 `%TEMP%\pytest-of-<user>` 存在权限问题（`PermissionError [WinError 5]`），`pyproject.toml` 的
`addopts` 已固化 `--basetemp=.pytest_tmp`。测试被中断或旧版本运行时，仓库根可能留下：

- `.pytest_tmp\`、`.pytest_<数字>\` 等 `--basetemp` 目录；
- `.pytest_cache\`（缓存插件目录）。

这些目录已被 `.gitignore` 的 `.pytest_*/` / `.pytest_cache/` 忽略，不是源码，也不是验收证据。
处理原则：

- 确认没有 pytest 正在运行后再删除；不要在测试进行中清理 basetemp。
- 不要提交、不要复制进 `.harness/evidence/`，也不要因为它们存在就判断当前测试失败。
- `acceptance.ps1` 已用 `-p no:cacheprovider` 和显式 `--basetemp` 把临时文件导向缓存根；但历史遗留目录
  仍可能存在于根目录，按上述方式清理即可。
- 若看到 `PYTEST_ADDOPTS` 被外部设置，先检查它是否覆盖了项目的 `addopts`/`basetemp`；`acceptance.ps1`
  只在 `arch-check` 期间临时覆盖并会恢复，`full`/`contract` 会继承调用者的环境。

### 6.4 缓存根与沙箱的已知偏差

`acceptance.ps1` 的注释写的是把 basetemp 放到 worktree 内 `.caches`，但当前实现仍按盘符派生：

```powershell
$drive = [IO.Path]::GetPathRoot($root)
$cacheRoot = Join-Path $drive 'orca\caches'   # 例如 E:\orca\caches
```

在 DSH/OpenCode 这类只保证 worktree 可写的沙箱里，该目录可能不可写，表现为 `uv` 初始化缓存失败
（`Failed to initialize cache at ... os error 5`），`quick` 的 `doctor`/`arch-check` 都以 `exit 2` 失败。
这不是代码测试失败，而是缓存路径不可用。处理方式：

- 在允许写该路径的环境运行验收；或由维护者把 `acceptance.ps1` 的缓存根也改到 worktree 内 `.caches`
  （该修改属于脚本维护范围，不是普通 worker 的 `allowedPaths`）。
- 在此之前，不要把“沙箱里 quick 因缓存失败”误报为业务回归；但也不能据此声称验收通过。
- orca setup/archive 的 `.caches` 约定（§2）本身是 worktree 内路径，不受这个偏差影响。

---

## 7. 一次验收的最小检查清单

- [ ] 改动只在任务 `allowedPaths` 内；`git diff --name-only` / `git status --short` 已核对。
- [ ] 跑过 `scripts\acceptance.ps1 -Mode quick` 且退出码为 0；必要时再跑 `contract`/`full`。
- [ ] 报告在 `.harness\acceptance\`，关键日志/退出码已归档到 `.harness\evidence\<REQ-ID>\<taskId>\`。
- [ ] 没有把 `requirements/`、`tests/`、`pyproject.toml`、`uv.lock`、`AGENTS.md`、CI 配置纳入改动。
- [ ] 汇报口径是“源码级预验收”，没有冒充发布级验收；真实云端调用未被擅自发起。
- [ ] 证据不含密钥、token、小说正文；`.pytest_*` 等临时目录未被提交。
