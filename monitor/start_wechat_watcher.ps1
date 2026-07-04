param(
    [switch]$IncludeExisting,
    [int]$Port = 8011
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $PSScriptRoot).Path
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "wechat_watcher.pid"
$OutLog = Join-Path $DataDir "wechat_watcher.out.log"
$ErrLog = Join-Path $DataDir "wechat_watcher.err.log"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

if (-not (Get-Process Weixin -ErrorAction SilentlyContinue)) {
    Write-Host "没有检测到电脑版微信，请先登录微信并打开“公众号”消息页面。"
    exit 2
}

if (Test-Path $PidFile) {
    $oldPidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid) -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Host "微信监听器已经在运行，PID: $oldPid"
        Write-Host "状态：.\status_wechat_watcher.ps1"
        exit 0
    }
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    Write-Host "监测后端尚未在 $Port 端口运行，请先启动 monitor\scripts\start_local_monitor.ps1"
    exit 2
}

$pythonCandidates = @(
    @(
        $env:MONITOR_PYTHON,
        (Join-Path $Root ".venv\Scripts\python.exe"),
        "schedule\.venv\Scripts\python.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
)
$Python = if ($pythonCandidates.Count -gt 0) { $pythonCandidates[0] } else { "python" }

$arguments = @("-m", "wg_monitor.wechat_watcher", "--root", $Root)
if ($IncludeExisting) {
    $arguments += "--include-existing"
}

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $arguments `
    -WorkingDirectory $Root `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

Set-Content -Path $PidFile -Value ([string]$process.Id) -Encoding ASCII
Start-Sleep -Seconds 3

if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
    Write-Host "微信监听器启动失败。错误日志：$ErrLog"
    if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 12 }
    exit 1
}

Write-Host "微信订阅号监听器已启动。"
Write-Host "PID: $($process.Id)"
Write-Host "首次运行会将当前页面作为已读基线，只处理之后出现的新文章。"
Write-Host "状态：.\status_wechat_watcher.ps1"
Write-Host "日志：$OutLog"
