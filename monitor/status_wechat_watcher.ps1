param(
    [string]$ApiBase = "http://127.0.0.1:8011",
    [string]$IntegrationKey = "dev-schedule-key"
)

$Root = (Resolve-Path $PSScriptRoot).Path
$PidFile = Join-Path $Root "data\wechat_watcher.pid"
$OutLog = Join-Path $Root "data\wechat_watcher.out.log"
$ErrLog = Join-Path $Root "data\wechat_watcher.err.log"

if (Test-Path $PidFile) {
    $pidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $watcherPid = 0
    if ([int]::TryParse($pidText, [ref]$watcherPid) -and (Get-Process -Id $watcherPid -ErrorAction SilentlyContinue)) {
        Write-Host "进程状态：正在运行，PID: $watcherPid"
    } else {
        Write-Host "进程状态：PID 文件已失效"
    }
} else {
    Write-Host "进程状态：未启动"
}

try {
    $status = Invoke-RestMethod `
        -Uri "$ApiBase/api/integrations/wechat/status" `
        -Headers @{ "X-Integration-Key" = $IntegrationKey } `
        -TimeoutSec 5
    Write-Host "后端状态：$($status.watcher.status)"
    if ($status.watcher.message) { Write-Host "说明：$($status.watcher.message)" }
    Write-Host "当前识别：$($status.watcher.visible_items) 条"
    Write-Host "最近记录：$($status.recent.Count) 条"
} catch {
    Write-Host "后端状态：无法读取（请确认 8011 服务正在运行）"
}

Write-Host "输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
if (Test-Path $ErrLog) {
    $recentErrors = Get-Content $ErrLog -Tail 8
    if ($recentErrors) {
        Write-Host ""
        Write-Host "最近错误："
        $recentErrors
    }
}
