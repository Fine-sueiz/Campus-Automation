# Ubuntu 部署模板

目标服务器：Ubuntu 22.04+。

目标目录：

```text
/opt/campus/monitor   # 本仓库：monitor
/opt/campus/schedule  # 相邻仓库：schedule
```

本目录提供 3 个 systemd 服务、1 个 nginx 443 反代模板、1 个 bootstrap 脚本。

## 1. 上传代码

在服务器上准备目录：

```bash
sudo mkdir -p /opt/campus/monitor /opt/campus/schedule
sudo chown -R campus:campus /opt/campus
```

把 Windows 本机代码上传到：

```text
monitor     -> /opt/campus/monitor
schedule   -> /opt/campus/schedule
```

不要把 Windows 的 `logs/`、`.venv/`、`node_modules/`、`.pytest_cache/` 作为必须迁移项。

## 2. 运行 bootstrap

在本仓库根目录执行：

```bash
sudo bash deploy/bootstrap.sh
```

脚本会安装 `python3-venv`、`nginx`、`docker`、`chromium` 等依赖，创建 `campus` 用户和 `/opt/campus` 目录，安装 systemd/nginx 模板，并在代码已上传时安装 Python requirements。

如果先运行脚本、后上传代码，再补跑：

```bash
sudo -u campus /opt/campus/monitor/.venv/bin/python -m pip install -r /opt/campus/monitor/requirements.txt
sudo -u campus /opt/campus/schedule/.venv/bin/python -m pip install -r /opt/campus/schedule/backend/requirements.txt
```

## 3. 环境变量

监测服务读取：

```text
/opt/campus/monitor/.env
```

systemd 会强制使用这些运行值，对齐 Windows 的 `scripts/start_local_monitor.ps1`：

```text
APP_ROOT=/opt/campus/monitor
HOST=127.0.0.1
PORT=8011
RUN_BACKGROUND=true
CHECK_INTERVAL_SECONDS=180
FORM_RUNNER_MODE=fake
WEB_PUSH_MODE=fake
```

日程后端读取：

```text
/opt/campus/schedule/.env
```

至少确认：

```text
SCHEDULE_API_KEY=<强随机值>
SCHEDULE_DB_PATH=/opt/campus/schedule/data/schedule.db
MONITOR_API_BASE=http://127.0.0.1:8011
MONITOR_INTEGRATION_KEY=<与监测端一致>
```

监测端也要同步：

```text
SCHEDULE_INBOX_API_BASE=http://127.0.0.1:8000
SCHEDULE_INBOX_API_KEY=<与日程端 SCHEDULE_API_KEY 一致>
MONITOR_PUBLIC_API_BASE=https://<你的域名>/monitor
MONITOR_INTEGRATION_KEY=<与日程端一致>
```

## 4. 数据迁移

迁移前先停 Windows 本机的 8011/8000/wechat2rss，避免 SQLite 半写入。

建议迁移：

```text
monitor\data\campus_monitor.sqlite3
monitor\config\app.yml
monitor\config\schedule.yml
monitor\.env
schedule\data\schedule.db
schedule\data\qq_sync_config.json
schedule\data\chrome-xuexitong-profile\
schedule\.env
```

迁移后修正权限：

```bash
sudo chown -R campus:campus /opt/campus
```

## 5. systemd 服务

安装位置：

```text
/etc/systemd/system/wg-monitor.service
/etc/systemd/system/schedule-backend.service
/etc/systemd/system/chrome-xuexitong.service
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wg-monitor.service
sudo systemctl enable --now schedule-backend.service
sudo systemctl enable --now chrome-xuexitong.service
```

检查：

```bash
curl http://127.0.0.1:8011/health
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:9222/json/version
journalctl -u wg-monitor -n 80 --no-pager
journalctl -u schedule-backend -n 80 --no-pager
journalctl -u chrome-xuexitong -n 80 --no-pager
```

`chrome-xuexitong.service` 对应 Windows 的 `start_xuexitong_chrome.ps1`：使用 `--headless=new`、`--remote-debugging-port=9222`、`--remote-debugging-address=127.0.0.1` 和 `/opt/campus/schedule/data/chrome-xuexitong-profile`。

## 6. nginx 443

模板：

```text
deploy/nginx/campus.conf
```

它会反代：

```text
/monitor/  -> http://127.0.0.1:8011/
/schedule/ -> http://127.0.0.1:8000/
/n8n/      -> http://127.0.0.1:5678/
```

创建 basic auth：

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-campus campus
```

`bootstrap.sh` 会先生成 30 天临时自签证书，让 nginx 在 certbot 前也能启动。把模板里的 `campus.example.com` 改成你的域名。DNS 生效后：

```bash
sudo certbot --nginx -d <你的域名>
sudo nginx -t
sudo systemctl reload nginx
```

未备案或没有域名时，不建议直接裸开 8011/8000/5678。最少也要只开放 443，并保留 basic auth。

## 7. n8n

n8n 建议只监听本机：

```text
127.0.0.1:5678
```

如果用 Docker 跑 n8n，nginx 仍按 `/n8n/` 反代。n8n 工作流导入参考：

```text
docs/n8n/
docs/n8n_integration.md
```

n8n 不直接读写 SQLite，统一调用 8011/8000 的 HTTP API。

## 8. Windows 只保留 QQ 卫星

QQ 群监听依赖 Windows UI 自动化，不能搬到 Linux。服务器落地后，Windows 电脑只保留这个卫星进程：

```powershell
cd schedule
.\start_qq_watcher.ps1 -ApiBase "https://<你的域名>/schedule" -ApiKey "<SCHEDULE_API_KEY>"
```

这台电脑关机时，只影响 QQ 群消息同步；公众号监测、日程后端、学习通 CDP、n8n 仍在服务器上运行。

## 9. 商业化提醒

这套部署模板可以包装成小型交付包：给同学、社团或学院做“公众号/官网/群消息 -> 待办/日程/邮件提醒”的私有化部署。交付时重点卖的是稳定运行、信源配置、提醒规则和后续维护，不是单个脚本本身。
