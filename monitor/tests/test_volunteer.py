import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wg_monitor.db import MonitorDB
from wg_monitor.feishu import FeishuClient
from wg_monitor.opportunity import OpportunityAnalysis
from wg_monitor.server import db_path_for, scan_once
from wg_monitor.volunteer import (
    confirmation_token,
    handle_volunteer_confirmation,
    maybe_send_volunteer_reminder,
    parse_confirmation_command,
    poll_mail_triggers,
    volunteer_source_accounts,
    volunteer_source_allowed,
)


def base_app_config(enabled: bool = True) -> dict:
    return {
        "opportunity": {
            "enabled_categories": ["work_study"],
            "required_any": ["勤工助学"],
            "extra_keywords": ["勤工助学"],
        },
        "volunteer": {
            "enabled": enabled,
            "confirm_by_email": True,
            "required_any": ["志愿服务", "志愿活动", "志愿者", "志愿时长"],
            "focus_any": ["报名", "招募", "参加"],
            "notify_email": "me@example.com",
            "token_expires_hours": 48,
            "allow_submit_when_schedule_conflict": False,
        },
        "email": {"auto_send_opportunities": True, "auto_send_categories": ["work_study"]},
        "user": {"name": "张三", "phone": "13800000000", "contact_email": "me@example.com"},
    }


def volunteer_analysis(opportunity_id: str = "volunteer-1", *, signup_url: str = "https://v.wjx.cn/vm/a.aspx") -> OpportunityAnalysis:
    return OpportunityAnalysis(
        id=opportunity_id,
        is_target=True,
        category="volunteer",
        category_label="志愿活动",
        title="图书馆志愿服务招募",
        source_name="测试源",
        article_item_id="article-1",
        article_url="https://example.com/a",
        signup_url=signup_url,
        activity_time="周五 12:00-14:00",
        deadline="6月30日",
        location="图书馆",
        schedule_status="available",
        free_time_text="周五：11:20-15:00",
        matched_time_text="周五：12:00-14:00",
        raw_text="图书馆志愿服务招募，报名链接 https://v.wjx.cn/vm/a.aspx。",
        keyword_hits=["志愿服务", "招募"],
        reasons=[],
    )


class VolunteerTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {
            name: os.environ.get(name)
            for name in [
                "EMAIL_DRY_RUN",
                "SMTP_HOST",
                "SMTP_USER",
                "SMTP_PASSWORD",
                "EMAIL_SENDER",
                "NOTIFY_EMAIL",
                "VOLUNTEER_MONITOR_ENABLED",
                "VOLUNTEER_CONFIRM_BY_EMAIL",
                "IMAP_HOST",
                "IMAP_USER",
                "IMAP_PASSWORD",
            ]
        }
        os.environ["EMAIL_DRY_RUN"] = "true"
        os.environ["SMTP_HOST"] = "smtp.qq.com"
        os.environ["SMTP_USER"] = "me@example.com"
        os.environ["SMTP_PASSWORD"] = ""
        os.environ["EMAIL_SENDER"] = "me@example.com"
        os.environ["NOTIFY_EMAIL"] = "me@example.com"
        os.environ.pop("VOLUNTEER_MONITOR_ENABLED", None)
        os.environ["VOLUNTEER_CONFIRM_BY_EMAIL"] = "true"
        os.environ["IMAP_HOST"] = "imap.qq.com"
        os.environ["IMAP_USER"] = "me@example.com"
        os.environ["IMAP_PASSWORD"] = "imap-auth-code"

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_parse_confirmation_command(self):
        self.assertEqual(parse_confirmation_command("报名 AB12CD34"), ("join", "AB12CD34"))
        self.assertEqual(parse_confirmation_command("我不报名 ab12cd34"), ("reject", "AB12CD34"))
        self.assertEqual(parse_confirmation_command("收到"), ("", ""))

    def test_volunteer_source_account_allowlist(self):
        config = {"volunteer": {"source_accounts": ["example-univ经贸青年", "示例大学义工联"]}}
        self.assertEqual(volunteer_source_accounts(config), ["example-univ经贸青年", "示例大学义工联"])
        self.assertTrue(volunteer_source_allowed(config, " example-univ经贸青年 "))
        self.assertTrue(volunteer_source_allowed(config, "示例大学义工联"))
        self.assertFalse(volunteer_source_allowed(config, "普通校园公众号"))
        self.assertTrue(volunteer_source_allowed({"volunteer": {}}, "任意来源"))

    def test_send_volunteer_reminder_creates_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(db_path_for(Path(tmp)))
            analysis = volunteer_analysis()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(maybe_send_volunteer_reminder(db, base_app_config(True), analysis), "sent")

            record = db.get_volunteer_confirmation_by_opportunity(analysis.id)
            self.assertIsNotNone(record)
            self.assertIn(f"[志愿确认:{record['token']}]", output.getvalue())
            self.assertEqual(maybe_send_volunteer_reminder(db, base_app_config(True), analysis), "skipped")

    def test_email_join_confirmation_creates_form_task_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            analysis = volunteer_analysis()
            db.upsert_opportunity(analysis.to_db_payload(status="pending_confirmation"))
            token = confirmation_token(analysis.id)
            db.create_volunteer_confirmation(token, analysis.id, "9999-01-01T00:00:00+00:00")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_volunteer_confirmation(db, base_app_config(True), token, "join"), "approved")
            self.assertEqual(handle_volunteer_confirmation(db, base_app_config(True), token, "join"), "duplicate")
            tasks = db.due_form_tasks("9999-01-01T00:00:00+00:00")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], f"task-volunteer-{token}")

    def test_email_reject_confirmation_marks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            analysis = volunteer_analysis("volunteer-reject")
            db.upsert_opportunity(analysis.to_db_payload(status="pending_confirmation"))
            token = confirmation_token(analysis.id)
            db.create_volunteer_confirmation(token, analysis.id, "9999-01-01T00:00:00+00:00")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_volunteer_confirmation(db, base_app_config(True), token, "reject"), "rejected")
            self.assertEqual(db.get_opportunity(analysis.id)["status"], "rejected")
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 0)

    def test_join_without_signup_url_marks_need_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            analysis = volunteer_analysis("volunteer-no-url", signup_url="")
            db.upsert_opportunity(analysis.to_db_payload(status="pending_confirmation"))
            token = confirmation_token(analysis.id)
            db.create_volunteer_confirmation(token, analysis.id, "9999-01-01T00:00:00+00:00")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_volunteer_confirmation(db, base_app_config(True), token, "join"), "need_human")
            self.assertEqual(db.get_opportunity(analysis.id)["status"], "need_human")
            self.assertEqual(db.get_volunteer_confirmation(token)["status"], "need_human")
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 0)

    def test_poll_mail_triggers_uses_mock_imap_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            analysis = volunteer_analysis("volunteer-poll")
            db.upsert_opportunity(analysis.to_db_payload(status="pending_confirmation"))
            token = confirmation_token(analysis.id)
            db.create_volunteer_confirmation(token, analysis.id, "9999-01-01T00:00:00+00:00")
            messages = [("1", "me@example.com", f"Re: [志愿确认:{token}]\n报名 {token}")]

            with patch("wg_monitor.volunteer.fetch_unseen_messages", return_value=messages):
                with contextlib.redirect_stdout(io.StringIO()):
                    summary = poll_mail_triggers(db, base_app_config(True))

            self.assertEqual(summary["approved"], 1)
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 1)

    def test_scan_once_respects_volunteer_enabled_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            feed = root / "volunteer.xml"
            feed.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><item>
<title>图书馆志愿服务活动招募通知</title>
<link>https://example.com/a</link>
<guid>v1</guid>
<description><![CDATA[志愿服务招募，报名链接 https://v.wjx.cn/vm/a.aspx]]></description>
</item></channel></rss>
""",
                encoding="utf-8",
            )
            (root / "config" / "app.yml").write_text(
                f"""
monitor:
  feed_urls:
    - name: 志愿测试
      url: "{feed.as_posix()}"
  fetch_article_html: false
opportunity:
  enabled_categories:
    - work_study
  required_any:
    - 勤工助学
  extra_keywords:
    - 勤工助学
volunteer:
  enabled: false
  notify_email: me@example.com
email:
  auto_send_opportunities: true
  auto_send_categories:
    - work_study
user:
  name: 张三
  phone: "13800000000"
  contact_email: me@example.com
""",
                encoding="utf-8",
            )
            (root / "config" / "schedule.yml").write_text("day_start: '08:00'\nday_end: '22:00'\ndays: {}\n", encoding="utf-8")
            db = MonitorDB(db_path_for(root))
            summary = scan_once(root, db, FeishuClient())
            self.assertEqual(summary["opportunities"], 0)
            self.assertEqual(summary["volunteer_reminders_sent"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            feed = root / "volunteer.xml"
            feed.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><item>
<title>图书馆志愿服务活动招募通知</title>
<link>https://example.com/a</link>
<guid>v1</guid>
<description><![CDATA[志愿服务招募，报名链接 https://v.wjx.cn/vm/a.aspx]]></description>
</item></channel></rss>
""",
                encoding="utf-8",
            )
            (root / "config" / "app.yml").write_text(
                f"""
monitor:
  feed_urls:
    - name: 志愿测试
      url: "{feed.as_posix()}"
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
  notify_email: me@example.com
email:
  auto_send_opportunities: true
  auto_send_categories:
    - work_study
user:
  name: 张三
  phone: "13800000000"
  contact_email: me@example.com
""",
                encoding="utf-8",
            )
            (root / "config" / "schedule.yml").write_text("day_start: '08:00'\nday_end: '22:00'\ndays: {}\n", encoding="utf-8")
            db = MonitorDB(db_path_for(root))
            with contextlib.redirect_stdout(io.StringIO()):
                summary = scan_once(root, db, FeishuClient())
            self.assertEqual(summary["opportunities"], 1)
            self.assertEqual(summary["volunteer_reminders_sent"], 1)


if __name__ == "__main__":
    unittest.main()
