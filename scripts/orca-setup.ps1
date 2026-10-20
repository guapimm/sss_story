<#
.SYNOPSIS
  story_commandcode 的 Orca worktree setup 钩子。
.DESCRIPTION
  - 依赖安装：uv sync --frozen（Python）；package-lock 存在时 npm ci（Tauri CLI）
  - 缓存放到数据盘：<盘符>:\orca\caches（避免 C 盘膨胀；不复制 src-tauri/target、dist）
  - 写 task.env 供执行器/验收脚本读取；不写 .env（密钥由负责人本机提供）
  - 不构建 Tauri（构建按需，CARGO_TARGET_DIR 指向数据盘缓存）
#>
$ErrorActionPreference = 'Stop'

$wt = $env:ORCA_WORKTREE_PATH
if (-not $wt) { throw 'ORCA_WORKTREE_PATH 未注入（请在 Orca 内运行）' }
if (-not (Test-Path $wt)) { throw "worktree 不存在: $wt" }
Set-Location $wt

# 按 worktree 所在盘派生缓存根：E:\... -> E:\orca\caches
# 缓存必须放 worktree 内部：executor(DSH/OpenCode) 的沙箱只保证可写 worktree；
# 写数据盘共享缓存会被拒绝（2026-10-20 实测：uv 缓存 E:\orca\caches 访问被拒）。
$cacheRoot = Join-Path $wt '.caches'
$env:UV_CACHE_DIR = Join-Path $cacheRoot 'uv'
$env:npm_config_cache = Join-Path $cacheRoot 'npm'
$env:CARGO_TARGET_DIR = Join-Path $cacheRoot 'cargo\story_commandcode'
$env:PYTHONDONTWRITEBYTECODE = '1'
New-Item -ItemType Directory -Force -Path $cacheRoot, $env:UV_CACHE_DIR, $env:CARGO_TARGET_DIR, '.harness\evidence', 'data' | Out-Null

Write-Host "[orca-setup] worktree=$wt"
Write-Host "[orca-setup] caches=$cacheRoot"

# Python 依赖（uv.lock 锁定）
if (Test-Path 'uv.lock') {
  uv sync --frozen --extra dev
  if ($LASTEXITCODE -ne 0) { throw "uv sync 失败（exit $LASTEXITCODE）" }
}

# Tauri CLI（只有构建/开发壳时需要）
if (Test-Path 'package-lock.json') {
  npm ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm ci 失败（exit $LASTEXITCODE）" }
}

# task.env：本 worktree 的隔离参数（不提交，.gitignore 已忽略）
$hash = [math]::Abs(($wt.ToLowerInvariant().GetHashCode())) % 200
@"
WORKTREE=$wt
UV_CACHE_DIR=$env:UV_CACHE_DIR
CARGO_TARGET_DIR=$env:CARGO_TARGET_DIR
STORYFORGE_DATA_DIR=$wt\data
STORYFORGE_HTTP_PORT=$((4700 + $hash))
"@ | Set-Content 'task.env' -Encoding UTF8

Write-Host '[orca-setup] done'
