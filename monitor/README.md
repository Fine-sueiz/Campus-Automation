# 单用户校园机会监测

单用户校园机会监测：Wechat2RSS 订阅公众号 → 8011 服务扫描 → 分类/评分/课表匹配 → 志愿活动邮件确认（默认不自动报名）→ 确认后自动报名（问卷助手）+ 写入日程安排程序（8000）。

这个项目是个人本机服务，目标是把公众号里的勤工助学、志愿活动、讲座、竞赛等机会变成可确认、可追踪、可写入日程的任务。当前主线是 Wechat2RSS feed + 本机 FastAPI 服务；飞书、多用户 PWA、微信桌面抓取代码保留，但不作为默认路线启用。

## 架构简图

`pipeline.scan_cycle` 是统一扫描流水线，CLI、GUI、服务端共用同一套处理逻辑：

```text
收集 feed
  → 拉取
  → SQLite 去重 + 内容指纹
  → 分析评分
  → 动作路由
```

动作路由按配置开关分别处理：

- 志愿活动：默认先给自己发确认邮件；确认后再进入问卷助手报名链路。
- 日程收件箱：把机会推送到 `schedule` 的 8000 后端。
- 勤工助学邮件：季节性默认关闭自动投递，避免误发。
- Web 推送 / 飞书 / 微信桌面监听：保留代码，不启用为主路径。

单条文章处理失败不会中断整轮扫描；去重状态以 SQLite 为准，旧的 `data/state.json` 路线已退役。

## 快速开始

安装依赖：

```powershell
cd monitor
python -m pip install -r requirements.txt
```

启动 8011 本机监测服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local_monitor.ps1 -Port 8011 -IntervalSeconds 180 -OpenBrowser
```

启动脚本会设置：

- `APP_ROOT=monitor`
- `RUN_BACKGROUND=true`
- `CHECK_INTERVAL_SECONDS=180`
- 未显式设置时，`FORM_RUNNER_MODE=fake`
- 未显式设置时，`WEB_PUSH_MODE=fake`

健康看板：

```powershell
Invoke-RestMethod http://127.0.0.1:8011/health
```

也可以打开：

```text
http://127.0.0.1:8011/
```

查看、停止、开机自启见 [docs/local_realtime_monitor.md](./docs/local_realtime_monitor.md)。

端到端冒烟（使用临时端口和临时数据目录，不触碰生产 8011/8000 进程）：

```powershell
python scripts/smoke_e2e.py
```

冒烟覆盖：公众号监测 → 日程收件箱 → 参加 → 监测端创建报名任务（fake）→ 写入日程事件。

## 关键配置

配置总入口见 [docs/configuration.md](./docs/configuration.md)。配置优先级：

```text
环境变量 > .env > config/app.yml / config/schedule.yml
```

重点看三个新配置段：

| 配置段 | 作用 |
|---|---|
| `dedup` | 控制 SQLite 去重和内容指纹窗口，避免同一活动换链接、换 guid、跨号转发后重复提醒。 |
| `scoring` / `shadow_mode` | 控制内容评分。`shadow_mode=true` 时只记录分数和新旧判定分歧，不改变现有动作；关闭后才按阈值拦截或降级。 |
| `logging` | 控制日志表保留天数和最大行数，避免本机 SQLite 长期运行后无限增长。 |

端口约定：

| 服务 | 地址 | 说明 |
|---|---|---|
| 本机监测服务 | `http://127.0.0.1:8011` | 本项目，`scripts/start_local_monitor.ps1` 启动 |
| 日程安排程序后端 | `http://127.0.0.1:8000` | 相邻项目 `schedule` |
| Wechat2RSS | `http://127.0.0.1:8001` | 第三方服务，独立启动 |

## 安全默认值

| 默认值 | 当前作用 |
|---|---|
| `EMAIL_DRY_RUN=true` | 邮件只记录不真发；真实发送必须显式改为 `false`。 |
| `FORM_RUNNER_MODE=fake` | 问卷助手只演练不真提交；真实报名必须显式切换模式。 |
| `email.auto_send_opportunities=false` | 勤工助学等机会邮件自动投递季节性关闭，避免暑期/低确定性误发。 |
| 志愿默认邮件确认 | 志愿活动先发确认邮件；确认后才进入报名任务和日程写入链路。 |

真实运行前，先保持这些默认值跑通 `/health` 和冒烟脚本，再逐项打开真实发送/真实报名。

## 冻结功能

这些功能代码仍在仓库中，但当前不作为默认路线启用：

| 功能 | 状态 |
|---|---|
| 飞书通知 / 飞书卡片 | 保留服务端和本地 MVP 代码，不作为当前主线。 |
| 多用户 PWA | 保留团队/多用户相关代码，不作为单用户本机监测默认入口。 |
| 微信桌面抓取 | 保留 watcher 代码，但路线冻结；当前主线是 Wechat2RSS feed。 |

## 测试

运行全量测试：

```powershell
python -m pytest tests/
```

常用本机检查：

```powershell
python -m wg_monitor validate
python -m wg_monitor monitor --once
```

`monitor --once` 会走统一扫描流水线；如果 Wechat2RSS 或外部 feed 暂时不可达，应记录错误并继续处理其他来源。

## 商业化延展

当前项目可以沉淀成“校园机会监测助手”低价定制包：给社团、学院、实验室或同学个人配置公众号源、筛选规则、课表匹配、邮件确认和日程同步。后续更适合用 n8n 做交付编排层，把“监测公众号 → 生成待办 → 人工确认 → 自动填表/提醒”包装成可复用工作流，而不是一开始就做重型多用户平台。
