# 配置参考

监测服务的配置来自三层，优先级从高到低：

1. **环境变量**（启动脚本或系统设置的，优先级最高）
2. **`.env` 文件**（项目根目录，只对尚未设置的变量生效）
3. **`config/app.yml` / `config/schedule.yml`**（业务配置）

服务端通过 `wg_monitor/settings.py` 的 `load_project_cached` 读取配置：
`.env`、`app.yml`、`schedule.yml` 任一文件修改后**下一次请求/扫描自动生效**，
不需要重启服务（环境变量例外——`.env` 里已被进程读取过的变量改动需重启）。

## 端口与服务约定

| 服务 | 地址 | 说明 |
|------|------|------|
| 本机监测服务 | `127.0.0.1:8011` | 本项目，`scripts/start_local_monitor.ps1` 启动 |
| 日程安排程序后端 | `127.0.0.1:8000` | `schedule\start.ps1` 启动 |
| 日程前端 | `127.0.0.1:5173` | 同上 |
| Wechat2RSS | `127.0.0.1:8001` | 第三方服务，独立启动，本仓库不管理 |

`run_server.py` 默认 `HOST=127.0.0.1`、`PORT=8011`。需要手机在局域网访问 PWA 时，
显式设置 `HOST=0.0.0.0` 再启动（此时务必先换掉默认 API 密钥，见下文）。

## 环境变量清单（.env）

### 邮件发送（SMTP）
| 变量 | 默认 | 说明 |
|------|------|------|
| `EMAIL_DRY_RUN` | `true` | **安全默认**：true 时只记录不真发。真发必须显式设为 false |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` | 无 / 465 / true | QQ 邮箱用 smtp.qq.com |
| `SMTP_USER` / `SMTP_PASSWORD` | 无 | 密码是邮箱授权码，只放 .env，不进 git |
| `EMAIL_SENDER` | 同 SMTP_USER | 发件人 |
| `NOTIFY_EMAIL` | 无 | 不确定机会/志愿提醒的收件人 |

### 邮件正文 LLM 生成（可选）
| 变量 | 默认 | 说明 |
|------|------|------|
| `EMAIL_BODY_MODE` | app.yml `email.body_mode` | `llm`/`template` |
| `EMAIL_LLM_PROVIDER` | `deepseek` | `deepseek` 或 `openai` |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | — | |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | — | |
| `EMAIL_LLM_TIMEOUT_SECONDS` | 30 | |
| `EMAIL_CONTEXT_MARKDOWN` | app.yml `email.context_markdown` | 个人情况上下文文件 |

### 志愿者邮件确认（IMAP 轮询）
| 变量 | 默认 | 说明 |
|------|------|------|
| `VOLUNTEER_MONITOR_ENABLED` | app.yml `volunteer.enabled` | |
| `VOLUNTEER_CONFIRM_BY_EMAIL` | app.yml `volunteer.confirm_by_email` | |
| `IMAP_HOST` / `IMAP_PORT` / `IMAP_USE_SSL` | imap.qq.com / 993 / true | |
| `IMAP_USER` / `IMAP_PASSWORD` | 回落到 SMTP_USER/PASSWORD | |
| `MAIL_TRIGGER_POLL_SECONDS` | 60 | 最小 15 |

### 服务运行
| 变量 | 默认 | 说明 |
|------|------|------|
| `APP_ROOT` | `.` | 项目根目录 |
| `HOST` / `PORT` | `127.0.0.1` / `8011` | |
| `RUN_BACKGROUND` | `true` | false 时只提供 API 不后台扫描 |
| `CHECK_INTERVAL_SECONDS` | app.yml `monitor.check_interval_seconds` | 扫描间隔 |
| `FORM_RUNNER_MODE` | 启动脚本设 `fake` | **fake=演练不真报名**；真报名需显式改 |
| `QUESTIONNAIRE_HELPER_DIR` | app.yml `form_runner.helper_dir` | 问卷填写助手目录 |
| `AUTO_SEND_OPPORTUNITY_EMAIL` | app.yml `email.auto_send_opportunities` | 勤工助学自动投递总开关 |

### 与日程安排程序集成
| 变量 | 默认 | 说明 |
|------|------|------|
| `CALENDAR_SYNC_ENABLED` / `SCHEDULE_INBOX_ENABLED` | app.yml 对应项 | |
| `CALENDAR_SYNC_API_BASE` / `SCHEDULE_INBOX_API_BASE` | `http://127.0.0.1:8000` | 日程后端 |
| `CALENDAR_SYNC_API_KEY` / `SCHEDULE_INBOX_API_KEY` | `dev-schedule-key` | 见“密钥轮换” |
| `MONITOR_PUBLIC_API_BASE` | `http://127.0.0.1:8011` | 日程端回调本服务的地址 |
| `MONITOR_INTEGRATION_KEY` | `dev-schedule-key` | 日程端回调鉴权 |
| `CALENDAR_SYNC_USERNAMES` / `SCHEDULE_INBOX_USERNAMES` | app.yml | 单用户模式填 admin |
| `CALENDAR_SYNC_REMINDER_MINUTES` | 60 | |
| `CALENDAR_SYNC_TIMEOUT_SECONDS` / `SCHEDULE_INBOX_TIMEOUT_SECONDS` | 8 | |

### 冻结功能（保留代码但不启用）
飞书：`FEISHU_APP_ID` `FEISHU_APP_SECRET` `FEISHU_VERIFICATION_TOKEN` `FEISHU_ENCRYPT_KEY` `FEISHU_DEFAULT_CHAT_ID`
Web 推送：`WEB_PUSH_MODE`（启动脚本设 fake）`WEB_PUSH_PUBLIC_KEY` `WEB_PUSH_PRIVATE_KEY` `WEB_PUSH_SUBJECT`
多用户：`TEAM_ADMIN_USERNAME` `TEAM_ADMIN_PASSWORD` `TEAM_ADMIN_DISPLAY_NAME` `SESSION_COOKIE_SECURE`
微信桌面抓取：`WECHAT_WATCHER_POLL_SECONDS`（按约束不启用此路线）

## 密钥轮换步骤（dev-schedule-key → 私有密钥）

当前两侧服务只监听 127.0.0.1，默认密钥风险可控。要轮换时**必须两侧一起改、一起重启**：

1. 生成新密钥：`python -c "import secrets; print(secrets.token_urlsafe(24))"`
2. 监测端 `.env`：设 `SCHEDULE_INBOX_API_KEY`、`CALENDAR_SYNC_API_KEY`、`MONITOR_INTEGRATION_KEY` 为新值
   （或改 `config/app.yml` 里 `calendar_sync.api_key`、`schedule_inbox.api_key`、`schedule_inbox.integration_key`）。
3. 日程端启动改为 `.\start.ps1 -ApiKey <新值>`，并在环境里设 `MONITOR_INTEGRATION_KEY=<新值>`。
4. 先重启日程端，再重启监测端，然后跑一次 `/admin/scan-once` 验证 inbox 同步成功。

## 安全默认值（不要随意翻转）

- `EMAIL_DRY_RUN=true`：邮件演练模式
- `FORM_RUNNER_MODE=fake`：报名演练模式
- `volunteer.allow_submit_when_schedule_conflict: false`：课表冲突不自动报名
- `email.auto_send_opportunities: false`：勤工助学自动投递已暂停（2026-07 起，季节性关闭）
- `wechat_watcher.enabled: false`：微信桌面窗口抓取路线冻结
