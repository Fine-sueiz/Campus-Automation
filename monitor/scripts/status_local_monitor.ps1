param(
    [int]$Port = 8011,
    [string]$HostAddress = "127.0.0.1"
)

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $Root "data\local_monitor.pid"
$OutLog = Join-Path $Root "data\local_monitor.out.log"
$ErrLog = Join-Path $Root "data\local_monitor.err.log"

Write-Host "项目目录：$Root"
if (Test-Path $PidFile) {
    $pidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    Write-Host "PID 文件：$pidText"
} else {
    Write-Host "PID 文件：不存在"
}

$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "端口状态：$Port 正在监听，进程 $($conn.OwningProcess)"
    try {
        $health = Invoke-RestMethod -Uri "http://$HostAddress`:$Port/health" -TimeoutSec 5
        Write-Host "健康检查：OK"
        Write-Host "访问地址：http://$HostAddress`:$Port/"
    } catch {
        Write-Host "健康检查：失败"
    }
} else {
    Write-Host "端口状态：$Port 未监听"
}

Write-Host "输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
if (Test-Path $ErrLog) {
    Write-Host ""
    Write-Host "最近错误日志："
    Get-Content $ErrLog -Tail 12
}
