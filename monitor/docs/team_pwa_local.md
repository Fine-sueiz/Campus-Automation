# 七人共用 PWA 本地 MVP

这版先在你的电脑上跑通，不买云服务器、不配域名、不做 HTTPS 部署。下学期买服务器后，把同一套代码和数据库迁过去即可。

## 1. 本地启动

```powershell
cd monitor
python -m pip install -r requirements.txt
python run_server.py
```

打开：

```text
http://127.0.0.1:8000/
```

默认管理员：

```text
账号：admin
密码：admin123456
```

默认七人邀请码：

```text
TEAM2026
```

## 2. 本地能做什么

- 7 个同学注册/登录。
- 每个人填写个人资料、常用问卷答案和课表。
- 管理员添加公众号 feed。
- 管理员点“本地扫描一次”，系统识别校园机会。
- 同一个机会会按每个人课表生成个人判断。
- 每个人在自己的机会列表里点“参加并报名 / 不参加 / 稍后看 / 人工处理”。
- 点参加后创建该用户自己的报名任务。
- `FORM_RUNNER_MODE=fake` 下可以安全测试，不会真实提交问卷。
- 如果 `config/app.yml` 里的 `calendar_sync.enabled` 为 `true`，且当前用户名在 `calendar_sync.usernames` 中，点“参加并报名”后会同步到本地日程程序。

## 3. 手机通知现阶段怎么测

本地开发默认：

```text
WEB_PUSH_MODE=fake
```

这会保存订阅并记录 fake 推送日志，但不会真的把通知推到外网手机。真实 PWA 推送需要：

- HTTPS 域名
- VAPID public/private key
- 手机浏览器允许通知

这些等下学期买云服务器后再配置。

## 4. 管理员操作

登录管理员后进入“管理”页：

1. 添加 feed：可以先用 `examples/fake_volunteer_feed.xml`。
2. 点“本地扫描一次”。
3. 回到“机会”页查看个人机会。
4. 查看“用户”和“日志”确认状态。

也可以用命令行扫描：

```powershell
curl -X POST http://127.0.0.1:8000/admin/scan-once
```

## 5. 资料和课表格式

课表每行一个忙碌时间：

```text
周一 08:00-09:40 高数
周三 14:00-16:00 实验
周五 19:00-21:00 社团
```

常用问卷答案 JSON 示例：

```json
[
  {
    "label": "政治面貌",
    "keywords": ["政治面貌"],
    "type": "single",
    "value": "共青团员"
  }
]
```

## 6. 下学期上云时再做

- 买 `2核4G` 云服务器。
- 配域名和 HTTPS。
- 把 `monitor` 上传到服务器。
- 把 `data/campus_monitor.sqlite3` 一起迁移。
- `WEB_PUSH_MODE=real` 并填写 VAPID key。
- `FORM_RUNNER_MODE=real` 后接真实问卷助手。

## 7. 本地日程同步

日程同步默认推送到：

```text
http://127.0.0.1:8000/api/events
```

需要先启动 `schedule`：

```powershell
cd schedule
.\start.ps1
```

如果你不用默认 `admin` 账号参加活动，把 `config/app.yml` 里的 `calendar_sync.usernames` 改成你的 PWA 用户名。

## 8. 安全提醒

- 管理员默认密码只是本地测试用，上云前必须改。
- 自动报名只在本人点击“参加并报名”后创建任务。
- 验证码、滑块、登录、短信验证不绕过，只标记人工处理。
