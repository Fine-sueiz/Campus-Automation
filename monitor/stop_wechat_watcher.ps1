$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $PSScriptRoot).Path
$PidFile = Join-Path $Root "data\wechat_watcher.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "微信监听器当前没有运行。"
    exit 0
}

$pidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
$watcherPid = 0
if ([int]::TryParse($pidText, [ref]$watcherPid)) {
    $process = Get-Process -Id $watcherPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $watcherPid -Force
        Write-Host "已停止微信监听器，PID: $watcherPid"
    } else {
        Write-Host "PID 文件存在，但监听进程已经结束。"
    }
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue

