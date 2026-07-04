param(
    [int]$DebugPort = 9222,
    [string]$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [string]$UserDataDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $UserDataDir) {
    $UserDataDir = Join-Path $Root "data\chrome-xuexitong-profile"
}

function Test-PortInUse {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-DebugPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 15
    )
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 300) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

if (-not (Test-Path $ChromePath)) {
    Write-Host "找不到 Chrome：$ChromePath" -ForegroundColor Red
    Write-Host "如果你的 Chrome 装在别的位置，请用 -ChromePath 指定。"
    exit 1
}

if (Test-PortInUse $DebugPort) {
    if (Wait-DebugPort -Port $DebugPort -TimeoutSeconds 3) {
        Write-Host "Chrome 调试端口已经可用：http://127.0.0.1:$DebugPort" -ForegroundColor Green
        exit 0
    }
    Write-Host "端口 $DebugPort 已被占用，但不是 Chrome 调试接口。" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null

$Arguments = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$DebugPort",
    "--user-data-dir=$UserDataDir",
    "--no-first-run",
    "--no-default-browser-check",
    "https://i.chaoxing.com/"
)

Start-Process -FilePath $ChromePath -ArgumentList $Arguments

if (-not (Wait-DebugPort -Port $DebugPort -TimeoutSeconds 15)) {
    Write-Host "Chrome 已启动，但调试端口暂时不可用。请稍后再试，或检查是否被安全软件拦截。" -ForegroundColor Red
    exit 1
}

Write-Host "Chrome 调试窗口已启动：http://127.0.0.1:$DebugPort" -ForegroundColor Green
Write-Host "专用 Chrome 数据目录：$UserDataDir"
Write-Host "请在这个专用 Chrome 窗口里登录学习通，然后回到日程网页点击“同步学习通”。"
