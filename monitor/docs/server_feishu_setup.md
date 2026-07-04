# 服务器版部署与飞书配置

## 1. 飞书开放平台

1. 创建企业自建应用。
2. 添加“机器人”能力，并发布版本。
3. 权限里开通“以应用身份发送消息”。
4. 事件与回调里配置：
   - 事件订阅 URL：`https://你的域名/feishu/events`
   - 卡片回调 URL：`https://你的域名/feishu/card-action`
   - Verification Token：填到 `.env` 的 `FEISHU_VERIFICATION_TOKEN`
   - Encrypt Key：如果启用，填到 `.env` 的 `FEISHU_ENCRYPT_KEY`
5. 把机器人拉进一个只有你自己的私人群聊。
6. 群里发送：`绑定`

绑定成功后，服务会保存这个群的 `chat_id`，后续所有校园机会都推送到这里。

## 2. Cloudflare Tunnel

在 Cloudflare Zero Trust 中创建 Tunnel，把公网域名转发到：

```text
http://app:8000
```

复制 Tunnel Token 到 `.env`：

```env
CLOUDFLARE_TUNNEL_TOKEN=...
```

## 3. 问卷助手

服务器版需要把现有问卷助手目录放到项目根目录：

```text
monitor\questionnaire_helper
```

也就是把这个目录复制过来：

```text
D:\爬虫\questionnaire_helper
```

Docker Compose 会把它挂载到：

```text
/app/questionnaire_helper
```

## 4. 启动

```bash
docker compose up -d --build
docker compose logs -f app
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

手动扫描一次：

```bash
curl -X POST http://127.0.0.1:8000/admin/scan-once
```

## 5. 安全边界

自动报名只处理普通公开表单。遇到验证码、滑块、人机验证、短信验证、登录验证、缺字段、提交按钮找不到时，会标记 `need_human` 并飞书提醒，不会绕过验证。
