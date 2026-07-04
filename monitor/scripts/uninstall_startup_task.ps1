$TaskName = "CampusOpportunityLocalMonitor"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已卸载开机自启任务：$TaskName"
} else {
    Write-Host "没有找到开机自启任务：$TaskName"
}
