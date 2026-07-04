$ErrorActionPreference = "Stop"
$PidFile = Join-Path $PSScriptRoot "integrations\we-mp-rss\data\werss.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "WeRSS 当前未运行。"
    exit 0
}

$runningPid = 0
$pidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
if ([int]::TryParse($pidText, [ref]$runningPid)) {
    Stop-Process -Id $runningPid -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "WeRSS 已停止。"
