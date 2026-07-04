# 本地飞书手机通知 MVP

这版不需要 n8n，也不需要云服务器。你的电脑本地运行程序，主动和飞书开放平台建立长连接；手机飞书收到机会卡片后，你点按钮，本地程序就能收到选择。

```text
公众号 feed
-> 本地电脑循环扫描
-> 识别志愿活动 / 勤工助学 / 竞赛 / 讲座 / 奖助学金
-> 结合 config/schedule.yml 判断是否有空
-> 飞书私人群聊收到卡片
-> 你在手机点“参加并报名 / 不参加 / 需要人工看看”
-> 本地程序记录决定，点参加后创建报名任务并调用问卷助手
```

## 1. 安装依赖

```powershell
cd monitor
python -m pip install -r requirements.txt
```

## 2. 准备飞书应用

在飞书开放平台创建一个企业自建应用：

1. 开启机器人能力。
2. 记录应用凭证里的 `App ID` 和 `App Secret`。
3. 在权限管理里添加发送消息相关权限，例如 `im:message`。
4. 在事件订阅里选择长连接方式，订阅 `im.message.receive_v1`。
5. 在回调配置里选择长连接方式，添加 `card.action.trigger`。
6. 发布应用，并把机器人拉进你的私人群聊。

长连接方式不需要给飞书配置公网 URL，也不需要 Cloudflare Tunnel。电脑需要能正常访问互联网。

## 3. 填 .env

```powershell
notepad .env
```

至少填写：

```text
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FORM_RUNNER_MODE=fake
```

首次建议先用 `FORM_RUNNER_MODE=fake`，验证按钮链路正常后再改成 `real`。

## 4. 启动本地长连接

```powershell
python -m wg_monitor local-feishu
```

启动后，在飞书私人群聊里发送：

```text
绑定
```

程序会保存这个群聊的 `chat_id`。以后扫到机会，就会推送到这个群。

## 5. 发送测试卡片

绑定成功后，新开一个 PowerShell：

```powershell
cd monitor
python -m wg_monitor local-feishu --test-card
```

手机飞书会收到一张测试卡片。点：

- `参加并报名`：本地数据库会记录参加，并创建问卷报名任务。
- `不参加`：本地数据库会标记拒绝。
- `需要人工看看`：本地数据库会标记需要人工处理。

## 6. 常用命令

只扫描一次，不开长连接：

```powershell
python -m wg_monitor local-feishu --once
```

只监听飞书绑定和按钮，不扫描公众号：

```powershell
python -m wg_monitor local-feishu --bind-only
```

点参加后只创建报名任务，不立即调用问卷助手：

```powershell
python -m wg_monitor local-feishu --no-run-tasks
```

## 7. 注意事项

- 0 成本的代价是电脑必须开着、联网，并且不能睡眠。
- 公众号 feed 本质还是定时轮询，不是微信官方秒级推送。
- 遇到验证码、滑块、登录、短信验证时，问卷助手不会绕过，会标记需要人工处理。
- 如果后续要给别人做服务，建议再升级成服务器版或“本地客户端 + 服务器中转”的组合。
