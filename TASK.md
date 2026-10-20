# 任务：给 `scripts/acceptance.ps1` 增加 `contract` 模式

## 目标
在 `scripts/acceptance.ps1` 中新增 `contract` 模式：
- `-Mode` 的 ValidateSet 增加 `contract`；
- `contract` 模式只运行 `tests/contract`（用 `uv run --extra dev pytest tests/contract -p no:cacheprovider --basetemp=<数据盘缓存>`），不跑 doctor/arch-check（或按实现者判断保留 doctor，但必须只跑 contract 测试）；
- 与已有 `quick`/`full` 共用结果 JSON 与退出码语义；
- 更新脚本头部注释和 `.EXAMPLE` 说明。

## 约束
- **只允许修改** `scripts/acceptance.ps1`。
- 不改 `tests/`、`pyproject.toml`、`uv.lock`、`scripts/orca-*.ps1`、其他任何文件。
- 不发起真实业务/收费调用；不新增依赖、不联网下载包。
- 该脚本在 PowerShell 5.1 下运行：**必须保存为 UTF-8 带 BOM**；避免使用 `$Args` 这类 PowerShell 自动变量名。

## 验收（必须自己跑过并把结果写进最终消息）
1. `powershell -File scripts\acceptance.ps1 -Mode contract` → exit 0，且生成的 JSON 里 `"mode": "contract"`。
2. `powershell -File scripts\acceptance.ps1 -Mode quick` → exit 0（回归）。
3. `git diff --name-only` 只输出 `scripts/acceptance.ps1`。

## 交付
- 修改后的 `scripts/acceptance.ps1`；
- 最终消息里写明：改了哪些行/新增什么参数、两次验收命令的实际退出码、diff 文件清单。
