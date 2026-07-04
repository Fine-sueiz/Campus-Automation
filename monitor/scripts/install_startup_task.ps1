param(
    [int]$Port = 8011,
    [int]$IntervalSeconds = 180
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartScript = Join-Path $Root "scripts\start_local_monitor.ps1"
$TaskName = "CampusOpportunityLocalMonitor"

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -Port $Port -IntervalSeconds $IntervalSeconds"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "本机校园机会监测服务，登录 Windows 后自动启动。" `
    -Force | Out-Null

Write-Host "已安装开机自启任务：$TaskName"
Write-Host "下次登录 Windows 后会自动启动本机监测。"
Write-Host "也可以现在手动启动：.\scripts\start_local_monitor.ps1 -Port $Port -IntervalSeconds $IntervalSeconds -OpenBrowser"
