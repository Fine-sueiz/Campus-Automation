# 本机实时监测运行方式

这里的“实时”不是微信推送式秒级实时，而是本机常驻服务按固定间隔轮询公众号 feed。建议个人本机使用 `180-300` 秒一次，别设得太频繁。

## 1. 启动本机监测

```powershell
cd monitor
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local_monitor.ps1 -Port 8011 -IntervalSeconds 180 -OpenBrowser
```

启动后打开：

```text
http://127.0.0.1:8011/
```

后台会自动执行：

```text
扫描 feed -> 识别机会 -> 按每个人课表生成个人机会 -> 处理到期 fake/真实报名任务
```

## 2. 查看状态

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_local_monitor.ps1 -Port 8011
```

主要看：

- 端口是否监听。
- 健康检查是否 OK。
- PID 文件是否存在。
- `data/local_monitor.err.log` 是否有错误。

## 3. 停止监测

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local_monitor.ps1 -Port 8011
```

如果 PID 文件丢了，但你确认 8011 上就是监测服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local_monitor.ps1 -Port 8011 -ForcePort
```

## 4. 设置开机自启

```powershell
cd monitor
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_startup_task.ps1 -Port 8011 -IntervalSeconds 180
```

这会创建 Windows 任务计划：

```text
CampusOpportunityLocalMonitor
```

以后你登录 Windows 后会自动启动本机监测。电脑关机、睡眠、断网时监测会停止；恢复联网后会继续。

## 5. 取消开机自启

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_startup_task.ps1
```

## 6. 推荐设置

- 本机个人用：`IntervalSeconds=180`，3 分钟一次。
- Feed 来源不稳定：`IntervalSeconds=300`，5 分钟一次。
- 真实公众号多、文章多：不要低于 `120` 秒，避免被第三方 feed 限流。
- 默认 `FORM_RUNNER_MODE=fake`，确认流程没问题后再改真实报名。

## 7. 相关脚本

- `scripts/start_local_monitor.ps1`：后台启动服务。
- `scripts/status_local_monitor.ps1`：查看运行状态。
- `scripts/stop_local_monitor.ps1`：停止服务。
- `scripts/install_startup_task.ps1`：安装开机自启。
- `scripts/uninstall_startup_task.ps1`：卸载开机自启。
