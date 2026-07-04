# Campus Automation

这是三个校园自动化项目的脱敏公开版。仓库中的数据、密钥、个人信息、本地日志、构建产物和浏览器运行产物已移除；实际使用前需要按各子项目的 `.env.example` 自行配置本地 `.env`。

## 子项目

- `monitor/`：校园公众号机会监测服务。它从公众号/RSS 来源扫描文章，按关键词和模型评分识别勤工助学、志愿服务等机会，再通过邮件或接口发起确认流程。
- `schedule/`：个人日程与课程安排服务。它提供日程接口，并可接收外部系统写入的候选事件；公开版补充了 `qq_sync_config.example.json` 作为 QQ 群消息同步配置示例。
- `questionnaire-helper/`：问卷和表单自动填写助手。它负责按配置打开表单、填写字段和执行自动化提交流程；公开版不包含 MinGit 工具链、浏览器数据、日志或真实表单数据。

## 项目关系

典型链路是：`monitor` 扫描公众号文章 -> 评分和筛选机会 -> 邮件确认或人工确认 -> 调用 `schedule` 写入日程；`questionnaire-helper` 则作为独立的表单自动填写能力，也可被上层流程在需要填写报名表时调用。

## 配置说明

- `monitor/.env.example` 保留了源配置的键名和结构，真实邮箱、SMTP 授权码、API key、飞书和 Cloudflare 等密钥均已替换为占位符。
- `monitor/config/app.yml`、`monitor/config/schedule.yml`、`monitor/config/personal_availability.md` 已替换为通用示例。
- `schedule/qq_sync_config.example.json` 是公开示例，真实 `data/qq_sync_config.json` 不随仓库发布。
- 根目录和各子项目的 `.gitignore` 会继续忽略 `.env`、`data/`、`logs/`、数据库、虚拟环境和构建产物。

更多部署和集成说明见：

- `monitor/docs/server_migration.md`
- `monitor/docs/n8n_integration.md`
