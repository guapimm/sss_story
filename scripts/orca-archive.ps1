<#
.SYNOPSIS
  story_commandcode 的 Orca worktree archive 钩子。
.DESCRIPTION
  只清理可再生成的依赖/缓存；data/（书稿数据）与 .harness/evidence 永久保留。
#>
$ErrorActionPreference = 'Continue'

$wt = $env:ORCA_WORKTREE_PATH
if (-not $wt -or -not (Test-Path $wt)) { Write-Host '[orca-archive] 无 worktree 上下文，跳过'; exit 0 }
Set-Location $wt

# 1) 停掉本 worktree 的 serve（若记录过 pid）
$pidFile = Join-Path $wt '.harness\serve.pid'
if (Test-Path $pidFile) {
  $servePid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($servePid) { & taskkill /PID $servePid /T /F 2>&1 | Out-Null }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2) 清理可再生产物（保留 data/ 与 .harness/evidence/）
foreach ($p in @('.venv', 'node_modules', '.caches', 'task.env', '.pytest_cache')) {
  if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue; Write-Host "[orca-archive] removed $p" }
}

Write-Host '[orca-archive] done（data/ 与 .harness/evidence/ 已保留）'
