param(
    [int]$BackendPort = 8000,
    [string]$ApiBase = "",
    [string]$ApiKey = "dev-schedule-key",
    [double]$IntervalSeconds = 5,
    [switch]$ImportVisible
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$LogsDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogsDir "qq-watcher-out.log"
$ErrLog = Join-Path $LogsDir "qq-watcher-err.log"
$ConfigPath = Join-Path $Root "data\qq_sync_config.json"
$StatusPath = Join-Path $Root "data\qq_watcher_status.json"

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 6
    )
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 600
        }
    }
    return $false
}

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$EffectiveApiBase = $ApiBase
if ([string]::IsNullOrWhiteSpace($EffectiveApiBase)) {
    $EffectiveApiBase = $env:SCHEDULE_API_BASE
}
if ([string]::IsNullOrWhiteSpace($EffectiveApiBase)) {
    $EffectiveApiBase = "http://127.0.0.1:$BackendPort"
}
$EffectiveApiBase = $EffectiveApiBase.TrimEnd("/")

if (-not (Test-Path $VenvPython)) {
    Write-Host "创建 Python 虚拟环境..."
    python -m venv (Join-Path $Root ".venv")
}

Write-Host "安装/检查 QQ 监听依赖..."
& $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt") | Out-Host

$HealthUrl = "$EffectiveApiBase/api/health"
if (-not (Wait-HttpOk -Url $HealthUrl)) {
    Write-Host "没有检测到日程后端，请先运行：.\start.ps1" -ForegroundColor Red
    Write-Host "健康检查地址：$HealthUrl"
    exit 1
}

$QQProcesses = Get-Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessName -match '^(QQ|TIM|NTQQ|QQGuild)$' -or
        ($_.Path -and $_.Path -match '\\Tencent\\.*QQ')
    }

if (-not $QQProcesses) {
    Write-Host "暂时没有检测到 QQ/TIM 进程。监听器仍会启动，你打开目标课程群窗口后它会继续扫描。" -ForegroundColor Yellow
}
else {
    Write-Host "检测到 QQ/TIM 相关进程：" -ForegroundColor Green
    $QQProcesses | Select-Object ProcessName, Id, Path | Format-Table -AutoSize | Out-Host
}

$env:SCHEDULE_API_KEY = $ApiKey
$env:SCHEDULE_API_BASE = $EffectiveApiBase
$Arguments = @(
    "-u",
    "-m",
    "app.qq_watcher",
    "--api-base",
    $EffectiveApiBase,
    "--api-key",
    $ApiKey,
    "--interval",
    "$IntervalSeconds"
)

if ($ImportVisible) {
    $Arguments += "--import-visible"
}

Start-Process -FilePath $VenvPython `
    -ArgumentList $Arguments `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden

Write-Host ""
Write-Host "QQ 群监听器已启动。" -ForegroundColor Green
Write-Host "配置文件：$ConfigPath"
Write-Host "状态文件：$StatusPath"
Write-Host "输出日志：$OutLog"
Write-Host "错误日志：$ErrLog"
Write-Host ""
Write-Host "使用方法："
Write-Host "1. 编辑配置文件，把课程群名和老师群名片/昵称填进去。"
Write-Host "2. 登录 QQ，并打开这些课程群窗口。"
Write-Host "3. 默认只监听启动后的新消息；如果要导入当前可见消息，用：.\start_qq_watcher.ps1 -ImportVisible"
