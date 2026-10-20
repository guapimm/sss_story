<#
.SYNOPSIS
  story_commandcode 验收统一入口（源码级）。
.DESCRIPTION
  - quick：doctor + arch-check（约 1 分钟）
  - full ：quick + 全量 pytest（长套件，约 70 分钟；basetemp 放数据盘，避免测试写仓库）
  - 结果写入 .harness/acceptance/<timestamp>.json；任何一步非 0 时脚本以非 0 退出
.EXAMPLE
  powershell -File scripts\acceptance.ps1 -Mode quick
  powershell -File scripts\acceptance.ps1 -Mode full
#>
[CmdletBinding()]
param(
  [ValidateSet('quick','full')]
  [string]$Mode = 'quick',
  [string]$TaskId = 'manual'
)

$ErrorActionPreference = 'Continue'
$root = if ($env:ORCA_WORKTREE_PATH) { $env:ORCA_WORKTREE_PATH } else { (Get-Location).Path }
Set-Location $root

$drive = [IO.Path]::GetPathRoot($root)
$cacheRoot = Join-Path $drive 'orca\caches'
$env:UV_CACHE_DIR = Join-Path $cacheRoot 'uv'
$env:PYTHONDONTWRITEBYTECODE = '1'
New-Item -ItemType Directory -Force -Path (Join-Path $cacheRoot 'pytest') | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $root '.harness\acceptance'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$results = New-Object System.Collections.Generic.List[object]

function Invoke-Check {
  param([string]$Name, [string]$Exe, [string[]]$ArgList, [int]$TimeoutSec = 600)
  $log = Join-Path $outDir ("{0}-{1}.log" -f $stamp, $Name)
  $started = Get-Date
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $Exe
  $quoted = @($ArgList | ForEach-Object { if ($_ -match '\s') { '"' + ($_ -replace '"','\"') + '"' } else { $_ } })
  $psi.Arguments = ($quoted -join ' ')
  $psi.WorkingDirectory = $root
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
  $psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi
  [void]$p.Start()
  $so = $p.StandardOutput.ReadToEndAsync(); $se = $p.StandardError.ReadToEndAsync()
  $finished = $p.WaitForExit($TimeoutSec * 1000)
  if (-not $finished) { & taskkill /PID $p.Id /T /F 2>&1 | Out-Null; $code = 124 } else { $code = $p.ExitCode }
  [IO.File]::WriteAllText($log, $so.Result + "`n--- stderr ---`n" + $se.Result, [Text.UTF8Encoding]::new($false))
  $p.Dispose()
  $r = [pscustomobject]@{ check = $Name; exitCode = $code; durationSec = [math]::Round(((Get-Date)-$started).TotalSeconds,1); log = $log }
  $results.Add($r)
  $color = if ($code -eq 0) { 'Green' } else { 'Red' }
  Write-Host ("[{0}] {1} exit={2} {3}s" -f $(if($code -eq 0){'PASS'}else{'FAIL'}), $Name, $code, $r.durationSec) -ForegroundColor $color
  return $code
}

"mode=$Mode task=$TaskId root=$root" | Write-Host
$codes = @()
$codes += Invoke-Check 'doctor'    'uv' @('run','--extra','dev','python','-m','storyforge.cli','doctor') 300
$codes += Invoke-Check 'arch-check' 'uv' @('run','--extra','dev','python','-m','storyforge.cli','arch-check') 600
if ($Mode -eq 'full') {
  $bt = Join-Path $cacheRoot ("pytest\story-{0}" -f $stamp)
  $codes += Invoke-Check 'pytest' 'uv' @('run','--extra','dev','pytest','-p','no:cacheprovider',"--basetemp=$bt") 14400
}

$allPass = (@($codes | Where-Object { $_ -ne 0 }).Count -eq 0)
$report = [pscustomobject]@{
  schemaVersion = 'storyforge.acceptance/1'
  mode = $Mode
  taskId = $TaskId
  root = $root
  head = (& git rev-parse HEAD 2>$null)
  gitStatusCount = @(& git status --porcelain 2>$null).Count
  passed = $allPass
  results = $results.ToArray()
  generatedAt = (Get-Date).ToString('o')
}
$jsonPath = Join-Path $outDir ("acceptance-{0}.json" -f $stamp)
[IO.File]::WriteAllText($jsonPath, (ConvertTo-Json -InputObject $report -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Host ("report=$jsonPath passed={0}" -f $allPass) -ForegroundColor $(if($allPass){'Green'}else{'Red'})
if (-not $allPass) { exit 1 }
exit 0
