# n8n 工作流模板

本目录保存可导入 n8n 的 workflow JSON。它们配套
[`../n8n_integration.md`](../n8n_integration.md) 使用，n8n 只通过 HTTP API 调用
监测服务，不直接读写 SQLite。

## 文件

| 文件 | 用途 | 默认时间 |
|---|---|---|
| `daily-digest.json` | 拉取最近 24 小时机会，调用 OpenAI 兼容接口生成 AI 日报，再发邮件 | 每天 21:00 |
| `feed-health-alert.json` | 读取 `/health`，当 feed 连续失败 `>= 3` 或恢复时发一次邮件 | 每小时 |
| `xuexitong-probe.json` | 触发学习通同步，失败或登录态异常时发邮件 | 每天 07:30 |

## 导入后必须替换

导入 n8n 后先保持 inactive，逐个替换下面的占位项：

| 占位项 | 说明 |
|---|---|
| `REPLACE_WITH_SMTP_CREDENTIAL` / `REPLACE_WITH_SMTP_CREDENTIAL_ID` | n8n SMTP credential |
| `REPLACE_WITH_OPENAI_CREDENTIAL` / `REPLACE_WITH_OPENAI_CREDENTIAL_ID` | OpenAI 兼容接口的 Header Auth credential，Header 名通常为 `Authorization`，值为 `Bearer <key>` |
| `REPLACE_WITH_OPENAI_COMPATIBLE_BASE_URL` | 例如 `https://api.openai.com` 或兼容服务 base URL，不要带末尾 `/v1/chat/completions` |
| `REPLACE_WITH_OPENAI_MODEL` | 例如 `gpt-4o-mini` 或兼容服务模型名 |
| `REPLACE_WITH_XUEXITONG_API_KEY_CREDENTIAL` / `REPLACE_WITH_XUEXITONG_CREDENTIAL_ID` | Header Auth credential，Header 名为 `X-API-Key` |
| `REPLACE_WITH_FROM_EMAIL` / `REPLACE_WITH_TO_EMAIL` | 发件人与收件人 |

如果服务器 nginx 对 `/monitor/` 或 `/n8n/` 加了 basic auth，请在 HTTP Request 节点里再补
Basic Auth credential，或者把 URL 保持为同机内环 `127.0.0.1`。

## 手动验证顺序

1. 先手动执行 `feed-health-alert.json`，确认 `/health` 能返回 `ok`、`last_scan` 和 `feeds`。
2. 手动执行 `daily-digest.json`。如果最近 24 小时没有机会，IF 会走 false 分支，不发邮件。
3. 手动执行 `xuexitong-probe.json`。如果返回异常，会发“学习通登录态可能过期”邮件。
4. 三条都通过后再启用定时。

## 商业化提示

这三条 workflow 可以包装成“校园信息自动巡检包”：公众号/官网机会日报、信源断线告警、登录态巡检。
面向学生组织、社团、新媒体小组时，n8n 负责可视化配置，监测端负责去重、评分和闭环，比较容易做成一次性部署加月度维护的小服务。
