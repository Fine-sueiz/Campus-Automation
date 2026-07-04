# n8n 集成方案

n8n 与两个主服务一起部署在服务器上（127.0.0.1:5678，nginx `/n8n/` 反代）。
定位是 **A+B 混合**：

- **A 下游**：消费监测/日程系统的产出——AI 日报、断线告警、周报。
- **B 上游**：给系统喂新信源——n8n 抓取任意来源后调文章投喂接口，复用现有
  去重/评分/确认闭环。

**红线**：n8n 不做公众号抓取主线（那是 `pipeline.scan_cycle` 的职责），
不直接读写 SQLite 文件（一律走 HTTP API），不持有业务状态。

可导入的工作流 JSON 草稿在 [n8n/](./n8n/) 目录。

## 依赖的系统接口

| 接口 | 用途 | 鉴权 |
|---|---|---|
| `GET /health`（8011） | 告警流数据源：feed 健康度 + 上轮扫描摘要 | 无 |
| `GET /admin/opportunities?since=&min_score=&status=&limit=`（8011） | 日报流数据源（新增的只读查询接口；`/api/opportunities` 已被冻结的 team PWA 占用） | 无（只读、本机内环；nginx 层有 basic auth） |
| `POST /api/integrations/wechat/articles`（8011） | B 阶段信源投喂 | `X-Integration-Key` |
| `POST /api/integrations/xuexitong/sync`（8000） | 学习通登录态探测 | `X-API-Key` |

`GET /admin/opportunities` 返回 `{"items": [...], "count": N}`，每条含：
`id, title, category, category_label, score, score_reasons, deadline,
activity_time, location, signup_url, article_url, schedule_status, status,
source_name, created_at, updated_at`（不含 raw_text 等大字段）。
`since` 为 ISO 8601（支持 Z 后缀；无时区按 UTC 解释），`min_score` 整数
（score 为空的旧数据会被排除），`status` 精确匹配（如 `pending_decision`），
`limit` 默认 200、上限 1000。

## 工作流 1：每日 AI 情报日报（daily-digest）

```text
Schedule Trigger 每天 21:00
  → HTTP Request: GET http://127.0.0.1:8011/admin/opportunities?since={{24h前}}
  → IF items 为空 → 结束（不发"无事发生"邮件，避免噪音）
  → AI 节点（DeepSeek，OpenAI 兼容接口，key 与监测端 .env 共用一把）
      系统提示：按 score 降序、按 category 分组、每条一句话摘要、
      临近截止（<48h）加 ⚠ 前缀、末尾附 score_reasons 里的关键依据
  → Send Email（SMTP，发给本人）
```

设计要点：摘要对象是**已去重、已评分**的机会，AI 只负责可读性，
判定逻辑仍在 `scoring.py`（可测试、可回溯）。

## 工作流 2：feed 断线告警（feed-health-alert）

```text
Schedule Trigger 每小时
  → HTTP Request: GET http://127.0.0.1:8011/health
  → Code 节点：
      挑出 feeds 中 consecutive_failures >= 3 的源；
      用 workflow staticData 记录已告警的 feed 名 → 只在"新进入故障"和
      "从故障恢复"两个跳变点各发一次，避免每小时轰炸
  → Send Email（故障/恢复两种模板）
```

## 工作流 3：学习通登录态探测（xuexitong-probe）

```text
Schedule Trigger 每天 07:30
  → HTTP Request: POST http://127.0.0.1:8000/api/integrations/xuexitong/sync
      （X-API-Key）
  → IF 失败或返回登录态异常 → Send Email：“学习通登录态过期，
      按 server_migration.md §8 重新登录”
```

## B 阶段模板：新信源适配（source-adapter，每个信源复制一份）

```text
Schedule Trigger（按源频率，官网类建议 1-2 小时）
  → HTTP Request: GET <目标页面/接口>
  → HTML Extract / Code：解析出 [{title, url, text}]
  → Loop Over Items
  → HTTP Request: POST http://127.0.0.1:8011/api/integrations/wechat/articles
      Header: X-Integration-Key
      Body: {"items": [{"title": ..., "url": ..., "text": ...}]}
```

幂等性由监测端现有内容指纹保证，n8n 端不需要记状态；重复投喂只会被去重。
首选试点源：学校官网通知公告页（教程视频里的 loop/split 技巧用在这里）。

## 安全与运维约定

- n8n 只绑 127.0.0.1，经 nginx `/n8n/` + basic auth 访问；管理密码强随机。
- 密钥存 n8n Credentials，不写进 workflow JSON（`docs/n8n/` 里的 JSON 用
  占位符 `{{CREDENTIAL}}`）。
- n8n 调用本机服务走 127.0.0.1 内环，不出公网。
- 工作流改动后手动导出 JSON 回存 `docs/n8n/`，保持 git 里有最新副本。
