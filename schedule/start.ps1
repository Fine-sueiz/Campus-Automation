param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$ApiKey = "dev-schedule-key"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$WebDir = Join-Path $Root "web"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$LogsDir = Join-Path $Root "logs"
$BackendOut = Join-Path $LogsDir "backend-out.log"
$BackendErr = Join-Path $LogsDir "backend-err.log"
$WebOut = Join-Path $LogsDir "web-out.log"
$WebErr = Join-Path $LogsDir "web-err.log"

function Test-PortInUse {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45
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
            Start-Sleep -Milliseconds 700
        }
    }
    return $false
}

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

if ((Test-PortInUse $BackendPort)) {
    $HealthUrl = "http://127.0.0.1:$BackendPort/api/health"
    if (-not (Wait-HttpOk -Url $HealthUrl -TimeoutSeconds 3)) {
        Write-Host "后端端口 $BackendPort 已被占用，且不是本程序健康接口。" -ForegroundColor Red
        exit 1
    }
    Write-Host "检测到后端已经运行：$HealthUrl" -ForegroundColor Yellow
}
else {
    if (-not (Test-Path $VenvPython)) {
        Write-Host "创建 Python 虚拟环境..."
        python -m venv (Join-Path $Root ".venv")
    }
    Write-Host "安装后端依赖..."
    & $VenvPython -m pip install --upgrade pip | Out-Host
    & $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt") | Out-Host

    $env:SCHEDULE_API_KEY = $ApiKey
    Start-Process -FilePath $VenvPython `
        -ArgumentList @("-u", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $BackendOut `
        -RedirectStandardError $BackendErr `
        -WindowStyle Hidden

    $HealthUrl = "http://127.0.0.1:$BackendPort/api/health"
    if (-not (Wait-HttpOk -Url $HealthUrl -TimeoutSeconds 45)) {
        Write-Host "后端启动超时，请查看日志：" -ForegroundColor Red
        Write-Host $BackendOut
        Write-Host $BackendErr
        exit 1
    }
    Write-Host "后端已启动：$HealthUrl" -ForegroundColor Green
}

if ((Test-PortInUse $FrontendPort)) {
    Write-Host "前端端口 $FrontendPort 已被占用，请打开 http://127.0.0.1:$FrontendPort 或换端口启动。" -ForegroundColor Yellow
}
else {
    if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
        Write-Host "安装前端依赖..."
        Push-Location $WebDir
        npm install | Out-Host
        Pop-Location
    }

    $env:VITE_API_BASE = "http://127.0.0.1:$BackendPort"
    $env:VITE_SCHEDULE_API_KEY = $ApiKey
    Start-Process -FilePath "npm.cmd" `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort") `
        -WorkingDirectory $WebDir `
        -RedirectStandardOutput $WebOut `
        -RedirectStandardError $WebErr `
        -WindowStyle Hidden

    $FrontendUrl = "http://127.0.0.1:$FrontendPort"
    if (-not (Wait-HttpOk -Url $FrontendUrl -TimeoutSeconds 45)) {
        Write-Host "前端启动超时，请查看日志：" -ForegroundColor Red
        Write-Host $WebOut
        Write-Host $WebErr
        exit 1
    }
    Write-Host "前端已启动：$FrontendUrl" -ForegroundColor Green
}

Write-Host ""
Write-Host "访问地址：http://127.0.0.1:$FrontendPort" -ForegroundColor Cyan
Write-Host "API Key：$ApiKey"
Write-Host "后端日志：$BackendOut"
Write-Host "前端日志：$WebOut"
Write-Host ""
Write-Host "停止服务：在任务管理器结束对应 python.exe / node.exe，或用端口查 PID 后 Stop-Process。"
