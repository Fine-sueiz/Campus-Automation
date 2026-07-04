# 服务器迁移总方案（目标：9 月完全落地）

把"监测公众号 + 日程安排"双服务从个人 Windows 电脑迁到国内轻量云服务器 7×24 运行。
本文档是落地日的唯一操作依据；配套的部署模板在 [../deploy/](../deploy/)，n8n 方案见
[n8n_integration.md](./n8n_integration.md)。

## 1. 目标架构

```text
┌────────────────── 国内轻量云服务器（Ubuntu 22.04+，2C2G） ──────────────────┐
│                                                                              │
│  nginx :443（HTTPS + basic auth）                                            │
│    ├── /monitor/  → 127.0.0.1:8011  wg-monitor.service（systemd）            │
│    ├── /schedule/ → 127.0.0.1:8000  schedule-backend.service（systemd）      │
│    │                    （前端 npm run build 出静态文件，由 nginx 托管）     │
│    └── /n8n/      → 127.0.0.1:5678  n8n（日报/告警/信源适配）                │
│                                                                              │
│  Docker: wechat2rss 容器 → 127.0.0.1:8001（License 版，Linux 镜像）          │
│  chrome-xuexitong.service：headless Chromium，CDP 127.0.0.1:9222             │
│  SQLite：campus_monitor.sqlite3 / schedule.db（每日 cron 备份）              │
└──────────────────────────────────────────────────────────────────────────────┘
                    ▲ HTTPS + X-API-Key
┌─────────── 个人电脑（Windows，卫星角色，非必需在线） ───────────┐
│  QQ 群监听（pywinauto，无法上 Linux）→ POST 服务器 /schedule/api/ │
│  开机时有 QQ 消息解析，关机不影响其他任何功能                     │
└────────────────────────────────────────────────────────────────────┘
```

要点：
- 公众号主线（wechat2rss → 扫描 → 评分 → 邮件确认 → 日程）**完全脱离电脑**。
- 学习通同步上服务器：headless Chromium 加载迁移过去的登录态 profile。
- 电脑上只剩 QQ 监听一个"卫星"进程，目标地址从 127.0.0.1 改成服务器。

## 2. 组件可移植性结论（2026-07-03 代码盘点）

| 组件 | 结论 | 说明 |
|---|---|---|
| wechat2rss 容器 | ✅ 直接搬 | 本来就是 Linux Docker 镜像；整个 `integrations/wechat2rss/` 目录拷走即可 |
| 监测服务 8011 | ✅ 直接搬 | 纯 FastAPI+SQLite；Windows 依赖已带 `platform_system == "Windows"` 标记 |
| 日程后端 8000 | ✅ 直接搬 | 同上（pywinauto 平台标记见 PORTABILITY_NOTES） |
| 日程前端 | ✅ 改托管方式 | 5173 是开发服务器；上线用 `npm run build` 产物交 nginx |
| 问卷填写助手 | ✅ 直接搬 | 纯 Playwright，headless 可用；验证码仍走 need_human 邮件 |
| 邮件 SMTP/IMAP | ✅ 直接搬 | 标准库，无平台绑定 |
| 学习通同步 | ⚠️ 搬+改 | CDP 地址已参数化（`XUEXITONG_CDP_URL`）；需迁 profile 目录 + headless 启动 |
| QQ 群监听 | ❌ 留电脑 | pywinauto/UIA + QQ 桌面客户端，物理无法上 Linux；做卫星 |
| 微信桌面监听 | ❌ 维持冻结 | 本来就不是主线 |
| GUI（tkinter） | ➖ 不迁 | 服务器只跑 FastAPI，GUI 保留给本机调试 |

## 3. 前置采购与准备（现在能做）

1. **服务器**：腾讯/阿里轻量应用服务器，2C2G/40GB 起，Ubuntu 22.04/24.04。
   优先国内区域（抓 mp.weixin.qq.com 快、风控风险低）。
2. **域名（可选）**：国内域名需备案才能走 80/443；备案前用 `IP:443` + 自签证书过渡。
3. **wechat2rss License 换机**：License 绑定邮箱（`LIC_EMAIL`/`LIC_CODE` 在
   `integrations/wechat2rss/.env`）。落地前在作者文档确认换机/多机策略，必要时联系作者
   解绑旧机器。数据目录 `integrations/wechat2rss/data/` 一并迁移可保留全部订阅。
4. **密钥轮换**：迁移前把所有默认密钥换成强随机值（见 §5），本机先换并验证。

## 4. 数据迁移清单（落地日拷贝）

| 文件/目录 | 说明 |
|---|---|
| `monitor\data\campus_monitor.sqlite3` | 监测数据库（迁移前先停服务再拷） |
| `monitor\config\app.yml`、`config\schedule.yml` | 监测配置与课表 |
| `monitor\.env` | SMTP/IMAP/密钥（迁移时同步轮换） |
| `monitor\integrations\wechat2rss\`（整目录） | License .env + docker-compose + data/ |
| `schedule\data\schedule.db` | 日程数据库 |
| `schedule\data\qq_sync_config.json` | QQ 群/老师白名单 |
| `schedule\data\chrome-xuexitong-profile\`（整目录） | 学习通 Chrome 登录态（关键！） |
| `schedule\backend\.env`（如存在） | 日程端密钥 |
| `questionnaire-helper\`（整目录） | form_runner 调用的 Playwright 助手 |

不迁：`qq_watcher_status.json`、`form_tasks/*.json`（运行时生成）、`backups/`（旧备份留本机）。

## 5. 安全基线（上公网前必须完成，缺一不上线）

现状是全部服务绑 127.0.0.1、默认密钥（`dev-schedule-key`、`dev-wechat2rss-token`）——
本机可以裸奔，公网一秒都不行。

1. **防火墙/安全组**：只放行 22（SSH，建议改端口+密钥登录+fail2ban）和 443。
   8011/8000/8001/5678/9222 一律只绑 127.0.0.1，不进安全组。
2. **nginx 反代 + HTTPS**：模板见 `deploy/nginx/campus.conf`；备案前自签证书或
   IP 证书，备案后 certbot。
3. **basic auth**：nginx 层对 /schedule/（前端）和 /n8n/ 加 htpasswd，作为
   应用层密钥之外的第二道门。
4. **密钥全轮换**（步骤见 [configuration.md](./configuration.md)）：
   - 监测端 ↔ 日程端互调的 `X-API-Key` / `X-Integration-Key`
   - wechat2rss 的 `RSS_TOKEN`
   - n8n 管理密码 + n8n 调用两服务的专用 key
5. **备份**：cron 每日 `sqlite3 .backup` 两个库到 `/opt/campus/backup/`，保留 14 天。

## 6. 落地日操作手册（按顺序执行）

```text
[准备] 服务器已购、能 SSH、deploy/bootstrap.sh 已上传
 1. 跑 deploy/bootstrap.sh：装依赖、建 campus 用户与 /opt/campus 目录、建 venv
 2. git clone / 上传两个仓库到 /opt/campus/{monitor,schedule}
 3. 停掉电脑上的 8011/8000/wechat2rss（保证 SQLite 一致性）
 4. 按 §4 清单拷数据（scp/WinSCP）
 5. 启 wechat2rss：cd integrations/wechat2rss && docker compose up -d
    验证：curl "http://127.0.0.1:8001/list?k=<RSS_TOKEN>"
 6. 改两端配置：app.yml 的 base_url 保持 127.0.0.1:8001；轮换 §5 全部密钥
 7. systemd 起三个服务（deploy/systemd/*.service）：
    systemctl enable --now wg-monitor schedule-backend chrome-xuexitong
 8. 前端 npm run build，产物指给 nginx；配 campus.conf + htpasswd + 证书，reload
 9. 装 n8n（npm i -g n8n + systemd），导入 docs/n8n/ 两条工作流，改成服务器地址
10. 电脑卫星切换：QQ 监听目标地址改为 https://<服务器>/schedule/...（见
    schedule\PORTABILITY_NOTES.md），带新 X-API-Key
11. 学习通登录态验证：触发一次 /api/integrations/xuexitong/sync；失败则说明
    profile 过期，见 §8
```

## 7. 验收清单（全过才算落地）

- [ ] `curl https://<服务器>/monitor/health`：`last_scan` 非空、feeds 全绿
- [ ] `python scripts/smoke_e2e.py` 在服务器上跑通（临时端口，不碰生产库）
- [ ] 真实扫描一轮：`POST /admin/scan-once` 计数正常，日志有 `scan cycle completed`
- [ ] 志愿确认闭环：测试邮件发出（EMAIL_DRY_RUN 先保持 true 验证日志，再切 false）
- [ ] 日程前端手机可访问（basic auth 生效）
- [ ] n8n 日报流手动触发一次成功；feed 告警流手动触发一次成功
- [ ] 电脑 QQ 卫星发一条测试消息 → 服务器收件箱出现
- [ ] 学习通 sync 成功拉到作业/考试
- [ ] 重启服务器：全部服务自动拉起（systemd enable + docker restart policy）

## 8. 已知风险与对策

| 风险 | 对策 |
|---|---|
| wechat2rss License 换机受限 | 落地前先向作者确认；最坏情况旧机停用后新机激活 |
| 学习通 Chrome 登录态过期 | n8n 每日探测 sync 接口，失败即邮件告警；重登方案：本机登录后重拷 profile，或服务器上 X11 转发/临时有头模式扫码 |
| 国内服务器抓微信文章被风控 | 保持 180s 扫描间隔不加密集；wechat2rss 自身有频控 |
| SQLite 并发（n8n 直读库文件） | 禁止：n8n 一律走 HTTP API，不碰 .sqlite3 文件 |
| 电脑关机 QQ 消息漏抓 | 接受的设计取舍；卫星启动时无补扫机制，重要群消息以公众号/学习通渠道兜底 |
| 迁移失败 | 电脑侧环境原样保留到验收全过；期间随时可切回本机运行 |

## 9. 迁移后本机清理（验收通过后再做）

- 停用本机三个服务的自启；保留代码仓库（开发用）
- QQ 监听卫星保留并指向服务器
- 本机 wechat2rss 容器停用（License 单机时必须停）
