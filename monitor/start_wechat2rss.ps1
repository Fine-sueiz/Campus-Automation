param(
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"
$Root = Join-Path $PSScriptRoot "integrations\wechat2rss"
$EnvFile = Join-Path $Root ".env"
$ExampleFile = Join-Path $Root ".env.example"
$ComposeFile = Join-Path $Root "docker-compose.yml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未检测到 Docker。请先安装 Docker Desktop，并确认 WSL2/虚拟化已开启。"
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item -LiteralPath $ExampleFile -Destination $EnvFile
    Write-Host "已创建 $EnvFile"
    Write-Host "请先填写 LIC_EMAIL 和 LIC_CODE，然后重新运行本脚本。"
    exit 1
}

$envText = Get-Content -LiteralPath $EnvFile -Raw
if ($envText -match "your-email@example.com" -or $envText -match "your-license-code") {
    Write-Host "请先编辑 $EnvFile，填入 Wechat2RSS 的 LIC_EMAIL 和 LIC_CODE。"
    exit 1
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/list?k=dev-wechat2rss-token" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Write-Host "Wechat2RSS 已在运行：http://127.0.0.1:$Port"
            exit 0
        }
    } catch {
        throw "端口 $Port 已被进程 $($listener.OwningProcess) 占用，但不像 Wechat2RSS。"
    }
}

Push-Location $Root
try {
    docker compose --env-file $EnvFile -f $ComposeFile up -d
} finally {
    Pop-Location
}

Write-Host "Wechat2RSS 启动命令已发送。"
Write-Host "管理地址：http://127.0.0.1:$Port"
Write-Host "订阅列表：http://127.0.0.1:$Port/list?k=dev-wechat2rss-token"
Write-Host "监测程序状态：http://127.0.0.1:8011/admin/wechat2rss/status"
