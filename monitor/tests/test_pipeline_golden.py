"""扫描流水线黄金用例。

先于流水线重构编写：用 TestClient 走 /admin/scan-once，
锁定“fake feed → 机会检出 → 志愿提醒 → 去重”的现有行为。
重构（monitor.run_once 与 server.scan_once 合并为 pipeline）前后，
本文件必须原样通过。
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from wg_monitor.db import MonitorDB
from wg_monitor.server import create_app, db_path_for
from wg_monitor.settings import invalidate_project_cache


VOLUNTEER_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>志愿测试源</title>
    <item>
      <title>图书馆志愿服务活动招募通知</title>
      <link>https://example.com/volunteer-article</link>
      <guid>golden-volunteer-001</guid>
      <pubDate>Sun, 07 Jun 2026 10:00:00 +0800</pubDate>
      <description><![CDATA[志愿活动招募，周二 14:00-16:00，地点图书馆一楼，报名链接 https://v.wjx.cn/vm/testVolunteer.aspx]]></description>
    </item>
  </channel>
</rss>
"""

WORK_STUDY_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>勤工测试源</title>
    <item>
      <title>下学期勤工助学岗位招聘通知</title>
      <link>https://example.com/work-article</link>
      <guid>golden-work-001</guid>
      <pubDate>Sat, 06 Jun 2026 10:00:00 +0800</pubDate>
      <description><![CDATA[勤工助学岗位报名，请发送邮件到 job@example.edu.cn。]]></description>
    </item>
  </channel>
</rss>
"""

APP_YML_TEMPLATE = """monitor:
  feed_urls:
    - name: 志愿测试源
      url: "{volunteer_feed}"
    - name: 勤工测试源
      url: "{work_feed}"
  check_interval_seconds: 300
  article_timeout_seconds: 5
  fetch_article_html: false

opportunity:
  enabled_categories:
    - work_study
  required_any:
    - 勤工助学
  extra_keywords:
    - 勤工助学

volunteer:
  enabled: true
  source_accounts:
    - 志愿测试源
  confirm_by_email: true
  notify_email: "me@example.com"
  token_expires_hours: 48
  allow_submit_when_schedule_conflict: false

safety:
  auto_send: false

email:
  auto_send_opportunities: false

user:
  name: 测试同学
  phone: "13800000000"
  contact_email: "me@example.com"
"""

SCHEDULE_YML = """timezone: Asia/Shanghai
day_start: "08:00"
day_end: "22:00"
days:
  tuesday:
    label: 周二
    busy:
      - name: 课程
        start: "08:00"
        end: "12:05"
"""

ENV_KEYS = [
    "EMAIL_DRY_RUN",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "EMAIL_SENDER",
    "EMAIL_BODY_MODE",
    "NOTIFY_EMAIL",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_DEFAULT_CHAT_ID",
    "WEB_PUSH_MODE",
    "WEB_PUSH_PUBLIC_KEY",
    "CHECK_INTERVAL_SECONDS",
    "SCHEDULE_INBOX_ENABLED",
    "CALENDAR_SYNC_ENABLED",
    "VOLUNTEER_MONITOR_ENABLED",
    "AUTO_SEND_OPPORTUNITY_EMAIL",
]


def write_project(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    volunteer_feed = root / "volunteer_feed.xml"
    work_feed = root / "work_feed.xml"
    volunteer_feed.write_text(VOLUNTEER_FEED, encoding="utf-8")
    work_feed.write_text(WORK_STUDY_FEED, encoding="utf-8")
    (root / "config" / "app.yml").write_text(
        APP_YML_TEMPLATE.format(
            volunteer_feed=volunteer_feed.as_posix(),
            work_feed=work_feed.as_posix(),
        ),
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text(SCHEDULE_YML, encoding="utf-8")


class PipelineGoldenTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {name: os.environ.get(name) for name in ENV_KEYS}
        for name in ENV_KEYS:
            os.environ.pop(name, None)
        os.environ["EMAIL_DRY_RUN"] = "true"
        os.environ["SMTP_HOST"] = "smtp.example.com"
        os.environ["SMTP_USER"] = "me@example.com"
        os.environ["EMAIL_SENDER"] = "me@example.com"
        os.environ["EMAIL_BODY_MODE"] = "template"
        invalidate_project_cache()

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        invalidate_project_cache()

    def scan(self, client: TestClient) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            response = client.post("/admin/scan-once")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_scan_detects_opportunities_sends_volunteer_reminder_and_dedups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root)
            app = create_app(root, start_background=False)
            client = TestClient(app)
            db = MonitorDB(db_path_for(root))

            counts = self.scan(client)

            # --- 第一轮扫描：两条 feed 各检出一条机会 ---
            self.assertEqual(counts["feeds"], 2)
            self.assertEqual(counts["items"], 2)
            self.assertEqual(counts["new_articles"], 2)
            self.assertEqual(counts["opportunities"], 2)
            # bootstrap 只有 admin 一个用户，每个机会物化一条
            self.assertEqual(counts["user_opportunities"], 2)
            # 飞书未配置、自动投递关闭、收件箱未启用
            self.assertEqual(counts["sent_cards"], 0)
            self.assertEqual(counts["emails_sent"], 0)
            self.assertEqual(counts["emails_failed"], 0)
            self.assertEqual(counts["inbox_sent"], 0)
            self.assertEqual(counts["inbox_failed"], 0)
            self.assertEqual(counts["inbox_skipped"], 2)
            # 志愿条目触发一封确认提醒（dry-run 也计 sent）
            self.assertEqual(counts["volunteer_reminders_sent"], 1)
            self.assertEqual(counts["volunteer_reminders_failed"], 0)

            opportunities = {row["category"]: row for row in db.list_opportunities()}
            self.assertEqual(set(opportunities), {"volunteer", "work_study"})

            volunteer = opportunities["volunteer"]
            self.assertEqual(volunteer["status"], "pending_confirmation")
            self.assertEqual(volunteer["signup_url"], "https://v.wjx.cn/vm/testVolunteer.aspx")
            # 周二 14:00-16:00 落在课后空闲 → available
            self.assertEqual(volunteer["schedule_status"], "available")

            work = opportunities["work_study"]
            self.assertEqual(work["status"], "new_no_chat")

            with db.connect() as conn:
                confirmations = conn.execute("SELECT * FROM volunteer_confirmations").fetchall()
                email_sends = conn.execute("SELECT * FROM email_sends").fetchall()
            self.assertEqual(len(confirmations), 1)
            self.assertEqual(confirmations[0]["status"], "pending")
            self.assertEqual(confirmations[0]["opportunity_id"], volunteer["id"])
            self.assertEqual(len(email_sends), 0)

            # --- 第二轮扫描：全部去重，无新增、无重复提醒 ---
            counts2 = self.scan(client)
            self.assertEqual(counts2["items"], 2)
            self.assertEqual(counts2["new_articles"], 0)
            self.assertEqual(counts2["opportunities"], 0)
            self.assertEqual(counts2["volunteer_reminders_sent"], 0)
            with db.connect() as conn:
                confirmations = conn.execute("SELECT * FROM volunteer_confirmations").fetchall()
            self.assertEqual(len(confirmations), 1)

    def test_feed_failure_does_not_break_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root)
            app_yml = root / "config" / "app.yml"
            content = app_yml.read_text(encoding="utf-8")
            content = content.replace(
                "  feed_urls:\n",
                '  feed_urls:\n    - name: 坏源\n      url: "'
                + (root / "missing.xml").as_posix()
                + '"\n',
                1,
            )
            app_yml.write_text(content, encoding="utf-8")
            invalidate_project_cache()

            app = create_app(root, start_background=False)
            client = TestClient(app)
            counts = self.scan(client)

            self.assertEqual(counts["feeds"], 3)
            self.assertEqual(counts.get("feed_failed", 0), 1)
            # 坏源不影响其余两条 feed 正常检出
            self.assertEqual(counts["new_articles"], 2)
            self.assertEqual(counts["opportunities"], 2)


if __name__ == "__main__":
    unittest.main()
