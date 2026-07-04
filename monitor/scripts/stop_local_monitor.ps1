param(
    [int]$Port = 8011,
    [switch]$ForcePort
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $Root "data\local_monitor.pid"

$stopped = $false
if (Test-Path $PidFile) {
    $pidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $monitorPid = 0
    if ([int]::TryParse($pidText, [ref]$monitorPid)) {
        $process = Get-Process -Id $monitorPid -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $monitorPid -Force
            Write-Host "已停止本机监测，PID: $monitorPid"
            $stopped = $true
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing -and $ForcePort) {
        Stop-Process -Id $existing.OwningProcess -Force
        Write-Host "已停止占用端口 $Port 的进程，PID: $($existing.OwningProcess)"
        $stopped = $true
    } elseif ($existing) {
        Write-Host "没有找到 PID 文件，但端口 $Port 被进程 $($existing.OwningProcess) 占用。"
        Write-Host "确认这是监测服务后可运行：.\scripts\stop_local_monitor.ps1 -ForcePort"
        exit 2
    }
}

if (-not $stopped) {
    Write-Host "本机监测当前没有运行。"
}
