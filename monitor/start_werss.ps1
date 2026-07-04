param(
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"
$Root = Join-Path $PSScriptRoot "integrations\we-mp-rss"
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "werss.pid"
$OutLog = Join-Path $DataDir "werss.out.log"
$ErrLog = Join-Path $DataDir "werss.err.log"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

if (Test-Path $PidFile) {
    $runningPid = 0
    $pidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ([int]::TryParse($pidText, [ref]$runningPid) -and (Get-Process -Id $runningPid -ErrorAction SilentlyContinue)) {
        $activeListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($activeListener) {
            Set-Content -LiteralPath $PidFile -Value ([string]$activeListener.OwningProcess) -Encoding ASCII
        }
        Write-Host "WeRSS 已运行：http://127.0.0.1:$Port"
        exit 0
    }
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $existingResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3
        if ($existingResponse.StatusCode -eq 200 -and $existingResponse.Content -match "WeRss|WeRSS") {
            Set-Content -LiteralPath $PidFile -Value ([string]$listener.OwningProcess) -Encoding ASCII
            Write-Host "WeRSS 已运行：http://127.0.0.1:$Port"
            exit 0
        }
    } catch {
        # The occupied port is handled by the error below.
    }
    throw "端口 $Port 已被进程 $($listener.OwningProcess) 占用。"
}

$env:HOST = "127.0.0.1"
$env:PORT = [string]$Port
$env:USERNAME = "admin"
$env:PASSWORD = "example-univ-rss-2026"
$env:TZ = "Asia/Shanghai"
$env:AUTO_RELOAD = "False"
$env:WERSS_AUTH_WEB = "True"
$env:REDIS_SERVER_HOST = "127.0.0.1"
$env:RSS_BASE_URL = "http://127.0.0.1:$Port/"
$env:RSS_LOCAL = "True"

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList "main.py", "-job", "True", "-init", "True" `
    -WorkingDirectory $Root `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog

Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -Encoding ASCII

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        if ($process.HasExited) { break }
    }
}

if (-not $ready) {
    Write-Host "WeRSS 未能正常启动，错误日志：$ErrLog"
    if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 30 }
    exit 1
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    Set-Content -LiteralPath $PidFile -Value ([string]$listener.OwningProcess) -Encoding ASCII
}

Write-Host "WeRSS 已启动：http://127.0.0.1:$Port"
Write-Host "登录账号：admin"
Write-Host "登录密码：example-univ-rss-2026"
