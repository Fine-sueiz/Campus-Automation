# 单人志愿服务提醒与邮件回复确认

这条链路默认关闭，不影响当前勤工助学自动投递。

## 启用方式

打开 `.env`：

```powershell
notepad .env
```

把下面这一行改成 `true`：

```env
VOLUNTEER_MONITOR_ENABLED=true
```

如果 `IMAP_PASSWORD` 留空，程序会自动复用 `SMTP_PASSWORD`。QQ 邮箱一般使用同一个邮箱授权码。

## 工作流程

```text
本机后台扫描 feed
-> 识别志愿服务 / 志愿活动 / 志愿者 / 志愿时长
-> 给 NOTIFY_EMAIL 发送提醒邮件
-> 你在手机邮箱回复：报名 ABCD1234
-> 本机后台读取 IMAP 收件箱
-> 创建问卷报名任务
-> 问卷助手执行填写
```

不参加时回复：

```text
不报名 ABCD1234
```

确认码默认 48 小时有效，重复回复不会重复报名。

## 安全建议

首次启用志愿服务时，建议先保持：

```env
FORM_RUNNER_MODE=fake
```

这样会验证完整链路，但不会真正提交问卷。确认没问题后再改：

```env
FORM_RUNNER_MODE=real
```

如果没有报名链接、课表冲突、验证码、登录或短信验证，程序会标记为 `need_human`，并发邮件提醒你人工处理。
