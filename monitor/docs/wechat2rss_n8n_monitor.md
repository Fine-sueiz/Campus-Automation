# Wechat2RSS + n8n 公众号动态监测路线

这条路线对应“公众号动态 -> RSS/API -> n8n/本地监测程序 -> 日程待办箱”。

## 1. 结构

```text
Wechat2RSS
  -> 公众号文章 RSS
  -> 监测公众号程序 http://127.0.0.1:8011/admin/scan-once
  -> 志愿/勤工机会识别
  -> 日程程序待办收件箱 http://127.0.0.1:8000
```

旧的微信窗口监听已经在 `config/app.yml` 中关闭。第一版优先使用 Wechat2RSS，不读微信聊天数据库，不需要注册微信公众号后台账号。

## 2. 启动 Wechat2RSS

前置条件：

- Docker Desktop 可用。
- 已获得 Wechat2RSS 私有部署授权 `LIC_EMAIL` 和 `LIC_CODE`。

第一次运行：

```powershell
cd monitor
.\start_wechat2rss.ps1
```

脚本会创建：

```text
monitor\integrations\wechat2rss\.env
```

打开 `.env`，填写：

```text
LIC_EMAIL=你的授权邮箱
LIC_CODE=你的激活码
```

再运行：

```powershell
.\start_wechat2rss.ps1
```

默认地址：

- Wechat2RSS: `http://127.0.0.1:8001`
- 订阅列表：`http://127.0.0.1:8001/list?k=dev-wechat2rss-token`

## 3. 添加公众号

已内置一个确认过的账号：

```text
示例大学图书馆 -> 1234567890
```

如果你有某个公众号的一篇文章链接，可以让监测程序调用 Wechat2RSS 订阅：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8011/admin/wechat2rss/subscribe" `
  -ContentType "application/json" `
  -Body '{"article_urls":["https://mp.weixin.qq.com/s/EXAMPLEARTICLETOKEN"]}'
```

如果已经知道文章页 `biz` 解码出的数字 ID，也可以直接订阅：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8011/admin/wechat2rss/subscribe" `
  -ContentType "application/json" `
  -Body '{"account_ids":["1234567890"]}'
```

## 4. 手动触发扫描

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8011/admin/scan-once"
```

扫描结果会继续走原来的逻辑：

- 命中志愿/勤工条件，写入监测数据库。
- 推送到日程程序的待办收件箱。
- QQ Mail 通知继续保留。

## 5. n8n 用法

最省心的方式是让 n8n 定时触发本地监测程序扫描，而不是在 n8n 里重写公众号解析逻辑。

导入工作流：

```text
monitor\examples\n8n_wechat2rss_scan.workflow.json
```

这个工作流每 10 分钟调用：

```text
POST http://127.0.0.1:8011/admin/scan-once
```

如果以后要商业化，可以把 n8n 作为“可视化配置层”：每个客户配置自己的公众号文章链接、关键词、通知渠道，核心分析和日程写入仍由后端程序完成。

## 6. 注意

- Wechat2RSS 的私有部署授权是个人用途路线，不适合直接拿来做公网商业内容分发。
- 本机部署默认只监听 `127.0.0.1:8001`，不要暴露到公网。
- 公众号更新有延迟是正常的，Wechat2RSS 不是实时官方接口。
- 其它公众号需要提供至少一篇文章链接，程序才能通过 Wechat2RSS 建立订阅。
