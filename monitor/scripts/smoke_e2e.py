# -*- coding: utf-8 -*-
"""端到端冒烟脚本：公众号监测 → 日程收件箱 → 参加 → 日程事件。

用临时端口 + 临时数据目录起两个真实服务，完全不触碰生产 8011/8000 进程：

    1. 起日程后端（18000）与监测服务（18011），各用独立临时数据目录
    2. 监测端扫描 fake feed，检出志愿机会（EMAIL_DRY_RUN，不真发邮件）
    3. 机会同步进日程收件箱
    4. 对收件箱条目决策“join” → 日程端回调监测端 → 创建报名任务(fake) + 写日程
    5. 断言日程表里出现了对应事件；到期报名任务以 fake 模式执行成功

用法（在 monitor 下）：
    python scripts/smoke_e2e.py

退出码 0 = 全链路通过。任何一步失败打印诊断并退出 1。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

MONITOR_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_ROOT = Path("schedule")
SCHEDULE_PYTHON = SCHEDULE_ROOT / ".venv" / "Scripts" / "python.exe"

MONITOR_PORT = 18011
SCHEDULE_PORT = 18000
API_KEY = "smoke-test-key"

MONITOR_BASE = f"http://127.0.0.1:{MONITOR_PORT}"
SCHEDULE_BASE = f"http://127.0.0.1:{SCHEDULE_PORT}"


def http(method: str, url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 10) -> dict | list:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_health(url: str, name: str, timeout_seconds: int = 40) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            body = http("GET", url)
            if isinstance(body, dict) and body.get("ok"):
                print(f"[OK] {name} 已就绪：{url}")
                return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"{name} 健康检查超时：{url}（{last_error}）")


def write_monitor_project(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    activity_date = date.today() + timedelta(days=7)
    feed_path = root / "volunteer_feed.xml"
    feed_path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>冒烟志愿源</title>
    <item>
      <title>图书馆志愿服务活动招募通知（冒烟测试）</title>
      <link>https://example.com/smoke-volunteer</link>
      <guid>smoke-volunteer-001</guid>
      <pubDate>Sun, 07 Jun 2026 10:00:00 +0800</pubDate>
      <description><![CDATA[志愿活动招募，{activity_date.month}月{activity_date.day}日 14:00-16:00，地点图书馆一楼，报名链接 https://v.wjx.cn/vm/smokeVolunteer.aspx]]></description>
    </item>
  </channel>
</rss>
""",
        encoding="utf-8",
    )
    (root / "config" / "app.yml").write_text(
        f"""monitor:
  feed_urls:
    - name: 冒烟志愿源
      url: "{feed_path.as_posix()}"
  article_timeout_seconds: 5
  fetch_article_html: false

volunteer:
  enabled: true
  confirm_by_email: false
  notify_email: "smoke@example.com"

opportunity:
  enabled_categories:
    - work_study

scoring:
  enabled: true
  shadow_mode: true

safety:
  auto_send: false

email:
  auto_send_opportunities: false

calendar_sync:
  enabled: true
  api_base: "{SCHEDULE_BASE}"
  api_key: "{API_KEY}"
  usernames:
    - admin
  reminder_minutes: 60
  timeout_seconds: 8

schedule_inbox:
  enabled: true
  api_base: "{SCHEDULE_BASE}"
  api_key: "{API_KEY}"
  monitor_api_base: "{MONITOR_BASE}"
  integration_key: "{API_KEY}"
  usernames:
    - admin
  timeout_seconds: 8

user:
  name: 冒烟测试
  phone: "13800000000"
  contact_email: "smoke@example.com"
""",
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text(
        """timezone: Asia/Shanghai
day_start: "08:00"
day_end: "22:00"
days:
  tuesday:
    label: 周二
    busy: []
""",
        encoding="utf-8",
    )


def start_process(args: list[str], cwd: Path, env: dict, log_path: Path) -> subprocess.Popen:
    log_file = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def tail(path: Path, lines: int = 25) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return "(无日志)"


def main() -> int:
    if not SCHEDULE_PYTHON.exists():
        print(f"[FAIL] 找不到日程后端 venv：{SCHEDULE_PYTHON}")
        return 1

    temp_dir = Path(tempfile.mkdtemp(prefix="smoke-e2e-"))
    monitor_root = temp_dir / "monitor"
    schedule_db = temp_dir / "schedule.db"
    monitor_log = temp_dir / "monitor.log"
    schedule_log = temp_dir / "schedule.log"
    processes: list[subprocess.Popen] = []
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"[OK] {name}")
        else:
            print(f"[FAIL] {name} {detail}")
            failures.append(name)

    try:
        write_monitor_project(monitor_root)

        schedule_env = {
            **os.environ,
            "SCHEDULE_DB_PATH": str(schedule_db),
            "SCHEDULE_API_KEY": API_KEY,
            "MONITOR_API_BASE": MONITOR_BASE,
            "MONITOR_INTEGRATION_KEY": API_KEY,
        }
        processes.append(
            start_process(
                [str(SCHEDULE_PYTHON), "-u", "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(SCHEDULE_PORT)],
                cwd=SCHEDULE_ROOT / "backend",
                env=schedule_env,
                log_path=schedule_log,
            )
        )

        monitor_env = {
            **os.environ,
            "APP_ROOT": str(monitor_root),
            "HOST": "127.0.0.1",
            "PORT": str(MONITOR_PORT),
            "RUN_BACKGROUND": "false",
            "EMAIL_DRY_RUN": "true",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USER": "smoke@example.com",
            "EMAIL_SENDER": "smoke@example.com",
            "EMAIL_BODY_MODE": "template",
            "FORM_RUNNER_MODE": "fake",
        }
        monitor_env.pop("FEISHU_APP_ID", None)
        monitor_env.pop("WEB_PUSH_MODE", None)
        processes.append(
            start_process(
                [sys.executable, "-u", "run_server.py"],
                cwd=MONITOR_ROOT,
                env=monitor_env,
                log_path=monitor_log,
            )
        )

        wait_health(f"{SCHEDULE_BASE}/api/health", "日程后端")
        wait_health(f"{MONITOR_BASE}/health", "监测服务")

        # 1. 扫描：检出志愿机会并同步收件箱
        counts = http("POST", f"{MONITOR_BASE}/admin/scan-once", payload={}, timeout=30)
        check("扫描检出机会", counts.get("opportunities", 0) >= 1, f"counts={counts}")
        check("志愿确认邮件已触发(dry-run)", counts.get("volunteer_reminders_sent", 0) >= 1, f"counts={counts}")
        check("机会已同步日程收件箱", counts.get("inbox_sent", 0) >= 1, f"counts={counts}")

        # 2. 收件箱可见
        items = http("GET", f"{SCHEDULE_BASE}/api/inbox/items")
        check("收件箱有待办", isinstance(items, list) and len(items) >= 1, f"items={items}")
        item = items[0]
        check("待办带活动开始时间", bool(item.get("start_at")), f"item={item}")

        # 3. 决策 join：回调监测端 → 报名任务 + 写日程
        decision = http(
            "POST",
            f"{SCHEDULE_BASE}/api/inbox/items/{item['id']}/decision",
            payload={"action": "join"},
            headers={"X-API-Key": API_KEY},
            timeout=30,
        )
        check("join 决策成功", decision.get("status") == "joined", f"decision={decision}")
        event_id = str((decision.get("item") or {}).get("event_id") or "")
        check("决策后关联了日程事件", bool(event_id), f"decision={decision}")

        # 4. 日程表里能查到事件
        activity_date = date.today() + timedelta(days=7)
        window_from = (activity_date - timedelta(days=3)).isoformat()
        window_to = (activity_date + timedelta(days=3)).isoformat()
        events = http("GET", f"{SCHEDULE_BASE}/api/events?from={window_from}&to={window_to}")
        matched = [event for event in events if "志愿" in str(event.get("title"))]
        check("日程表出现志愿活动事件", len(matched) >= 1, f"events={events}")

        # 5. 到期报名任务以 fake 模式执行
        tasks = http("POST", f"{MONITOR_BASE}/admin/run-due-tasks", payload={}, timeout=60)
        check("报名任务(fake)执行成功", tasks.get("submitted", 0) >= 1, f"tasks={tasks}")

        # 6. 健康页反映本轮扫描
        health = http("GET", f"{MONITOR_BASE}/health")
        check(
            "健康页含扫描摘要与 feed 状态",
            bool(health.get("last_scan", {}).get("at")) and len(health.get("feeds", [])) >= 1,
            f"health={health}",
        )

        if failures:
            print("\n===== 监测服务日志（尾部）=====")
            print(tail(monitor_log))
            print("\n===== 日程后端日志（尾部）=====")
            print(tail(schedule_log))
            print(f"\n冒烟结果：{len(failures)} 项失败：{failures}")
            return 1
        print("\n冒烟结果：全链路通过 ✔（扫描→收件箱→参加→报名任务→日程事件）")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟脚本异常：{exc}")
        print("\n===== 监测服务日志（尾部）=====")
        print(tail(monitor_log))
        print("\n===== 日程后端日志（尾部）=====")
        print(tail(schedule_log))
        return 1
    finally:
        for process in processes:
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:  # noqa: BLE001
                process.kill()
        time.sleep(0.5)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
