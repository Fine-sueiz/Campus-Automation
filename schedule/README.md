# 本地日程安排程序

这是一个 Windows 本地运行的日程表 MVP：前端是中文月历，后端用 FastAPI + SQLite 保存日程，预留 `REST API + X-API-Key` 给 n8n、脚本、浏览器自动化或其他程序自动写入日程。

## 启动

```powershell
cd schedule
.\start.ps1
```

启动成功后打开：

```text
http://127.0.0.1:5173
```

默认 API Key：

```text
dev-schedule-key
```

如果端口被占用，可以换端口：

```powershell
.\start.ps1 -BackendPort 8010 -FrontendPort 5174
```

## 功能

- 月历查看日程，点击日期查看当天安排。
- 添加、编辑、删除单次日程。
- 支持全天、分类、地点、备注、来源、提醒提前分钟数字段。
- 支持每天、每周、每月、每年重复规则。
- 重复日程支持编辑/删除：本次、以后、全部。
- 支持从学习通 Chrome 页面同步作业/考试截止时间。
- 支持从已打开的 QQ 群窗口监听老师消息，解析成日程候选或自动写入。
- 支持接收公众号监测程序的机会待办，并在前端参加、加入日程、稍后或忽略。
- 数据默认保存到 `data\schedule.db`。

## 学习通同步

第一版通过本机 Chrome 调试窗口读取你已登录的学习通页面，不保存学习通密码，不绕过验证码、短信、人脸或滑块。新版 Chrome 不允许对默认用户目录开启远程调试，所以脚本会使用 `data\chrome-xuexitong-profile` 作为专用学习通 Chrome 配置目录；第一次需要在这个专用窗口登录一次，之后会复用这个专用登录状态。

1. 启动日程程序：

```powershell
cd schedule
.\start.ps1
```

2. 启动可同步的专用 Chrome：

```powershell
.\start_xuexitong_chrome.ps1
```

3. 在打开的专用 Chrome 里登录学习通。
4. 回到 `http://127.0.0.1:5173`，点击顶部“同步学习通”。

如果你想用 n8n、Windows 任务计划或脚本定时同步，可以调用：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/integrations/xuexitong/sync" `
  -Headers @{ "X-API-Key" = "dev-schedule-key" }
```

查看 Chrome 连通性和最近同步记录：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/integrations/xuexitong/status"
```

同步规则：

- 作业写入分类 `作业`，默认提前 1440 分钟提醒。
- 考试、测验写入分类 `考试`，默认提前 2880 分钟提醒。
- 有具体时间时创建 30 分钟日程；只有日期时创建全天日程。
- 同一学习通任务重复同步不会重复创建；截止时间变化会更新已有日程。
- 第一版不会自动删除学习通里消失的任务，避免误删你手动整理过的日程。

## API 示例

## QQ群消息同步

第一版不加 QQ 机器人，也不读取 QQ 加密聊天数据库。它通过本机 Windows UI 自动化读取你已经打开的课程群窗口，只处理配置白名单里的老师昵称/群名片。

1. 启动日程程序：

```powershell
cd schedule
.\start.ps1
```

2. 打开前端一次，或调用状态接口生成配置文件：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/integrations/qq/status"
```

3. 编辑配置文件：

```text
schedule\data\qq_sync_config.json
```

示例：

```json
{
  "enabled": true,
  "auto_create_min_confidence": 0.82,
  "groups": [
    {
      "group_name": "概率论A课程群",
      "group_id": "",
      "course_name": "概率论A",
      "teacher_names": ["张老师", "张三"],
      "teacher_ids": [],
      "default_category": "课程",
      "reminder_minutes": 1440
    }
  ]
}
```

4. 登录 QQ，并打开这些课程群窗口。

5. 启动监听器：

```powershell
.\start_qq_watcher.ps1
```

默认只监听启动后的新消息。如果要把当前窗口里已经可见的老师消息也导入：

```powershell
.\start_qq_watcher.ps1 -ImportVisible
```

云端大模型解析是可选的。未配置模型时，程序会用本地规则解析“6月25日 23:59”“下周三”“明天晚上8点”等常见表达。配置 OpenAI 兼容接口：

```powershell
$env:QQ_SYNC_LLM_API_KEY = "你的模型API Key"
$env:QQ_SYNC_LLM_API_BASE = "https://api.openai.com/v1"
$env:QQ_SYNC_LLM_MODEL = "gpt-4o-mini"
```

常用接口：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/integrations/qq/status"
Invoke-RestMethod "http://127.0.0.1:8000/api/integrations/qq/candidates"
```

手动模拟一条老师消息：

```powershell
$body = @{
  group_name = "概率论A课程群"
  sender_name = "张老师"
  course_name = "概率论A"
  text = "下周三交概率论作业，记得提交到学习通。"
  message_time = "2026-06-20T10:00:00+08:00"
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/integrations/qq/messages" `
  -Headers @{ "X-API-Key" = "dev-schedule-key" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

规则：

- 非白名单老师消息不会进模型，也不会写日程。
- 时间明确且置信度达标时自动写入月历。
- “下次课交”“近期提交”等模糊消息进入右侧“QQ群消息”候选区，编辑确认后再写入。
- 同一条 QQ 消息重复读取不会重复创建；你手动删除已创建日程后，监听器也不会自动重建。

## 公众号待办收件箱

启动日程程序和 `monitor` 的 8011 服务后，监测程序发现属于 `admin` 的目标机会会调用：

```text
POST http://127.0.0.1:8000/api/inbox/items
```

前端右侧“待办收件箱”支持参加并报名、编辑后加入日程、稍后、忽略和打开原文。QQ Mail 仍会照常发送；日程前端未打开时，待办保存在 `data\schedule.db`。

查看待办：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/inbox/items"
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

查询日程：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/events?from=2026-06-01&to=2026-06-30"
```

新增日程：

```powershell
$body = @{
  title = "数据库课小组会"
  start_at = "2026-06-20T14:00:00+08:00"
  end_at = "2026-06-20T15:00:00+08:00"
  all_day = $false
  category = "项目"
  location = "图书馆"
  notes = "讨论展示分工"
  source = "powershell"
  reminder_minutes = 30
  recurrence = $null
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/events" `
  -Headers @{ "X-API-Key" = "dev-schedule-key" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

新增每周重复日程：

```powershell
$body = @{
  title = "每周英语口语练习"
  start_at = "2026-06-22T19:00:00+08:00"
  end_at = "2026-06-22T20:00:00+08:00"
  all_day = $false
  category = "课程"
  location = "线上"
  notes = ""
  source = "n8n"
  reminder_minutes = 15
  recurrence = @{
    freq = "weekly"
    interval = 1
    count = 8
    weekdays = @("MO")
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/events" `
  -Headers @{ "X-API-Key" = "dev-schedule-key" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

## n8n 对接思路

- 触发器：Webhook、邮件、表格、问卷结果、学校通知网页监控。
- 处理节点：让 n8n 提取标题、时间、地点、来源。
- HTTP Request 节点：`POST http://127.0.0.1:8000/api/events`，Header 加 `X-API-Key`。
- 商业化方向：把“通知/群消息/网页信息自动变日程”的能力做成校园效率工具，比如自动收集讲座、比赛、报名截止日期、考试安排，然后给同学或社团提供订阅服务。
