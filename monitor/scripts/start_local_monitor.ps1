param(
    [int]$Port = 8011,
    [string]$HostAddress = "127.0.0.1",
    [int]$IntervalSeconds = 180,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "local_monitor.pid"
$OutLog = Join-Path $DataDir "local_monitor.out.log"
$ErrLog = Join-Path $DataDir "local_monitor.err.log"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

function Test-AlivePid {
    param([int]$ProcessId)
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        return $null -ne $process
    } catch {
        return $false
    }
}

if (Test-Path $PidFile) {
    $oldPidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid) -and (Test-AlivePid -ProcessId $oldPid)) {
        Write-Host "本机监测已经在运行，PID: $oldPid"
        Write-Host "访问地址：http://$HostAddress`:$Port/"
        exit 0
    }
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Write-Host "端口 $Port 已被进程 $($existing.OwningProcess) 占用。可以换端口："
    Write-Host ".\scripts\start_local_monitor.ps1 -Port 8012"
    exit 2
}

$env:APP_ROOT = $Root
$env:HOST = $HostAddress
$env:PORT = [string]$Port
$env:RUN_BACKGROUND = "true"
$env:CHECK_INTERVAL_SECONDS = [string]$IntervalSeconds
if (-not $env:FORM_RUNNER_MODE) { $env:FORM_RUNNER_MODE = "fake" }
if (-not $env:WEB_PUSH_MODE) { $env:WEB_PUSH_MODE = "fake" }

$process = Start-Process `
    -FilePath "python" `
    -ArgumentList "run_server.py" `
    -WorkingDirectory $Root `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

Set-Content -Path $PidFile -Value ([string]$process.Id) -Encoding ASCII

$healthUrl = "http://$HostAddress`:$Port/health"
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.ok) {
            $ready = $true
            break
        }
    } catch {
        continue
    }
}

if (-not $ready) {
    Write-Host "服务已启动但健康检查还没通过，PID: $($process.Id)"
    Write-Host "错误日志：$ErrLog"
    exit 1
}

Write-Host "本机实时监测已启动。"
Write-Host "PID: $($process.Id)"
Write-Host "访问地址：http://$HostAddress`:$Port/"
Write-Host "轮询间隔：$IntervalSeconds 秒"
Write-Host "日志：$OutLog"

if ($OpenBrowser) {
    Start-Process "http://$HostAddress`:$Port/"
}
