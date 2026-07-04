import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from wg_monitor.db import MonitorDB
from wg_monitor.opportunity import OpportunityAnalysis
from wg_monitor.server import create_app, db_path_for, maybe_auto_send_opportunity_email


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.old_token = os.environ.get("FEISHU_VERIFICATION_TOKEN")
        os.environ["FEISHU_VERIFICATION_TOKEN"] = "token"
        self.old_email_env = {
            name: os.environ.get(name)
            for name in [
                "EMAIL_DRY_RUN",
                "SMTP_HOST",
                "SMTP_USER",
                "SMTP_PASSWORD",
                "EMAIL_SENDER",
                "EMAIL_BODY_MODE",
            ]
        }

    def tearDown(self):
        if self.old_token is None:
            os.environ.pop("FEISHU_VERIFICATION_TOKEN", None)
        else:
            os.environ["FEISHU_VERIFICATION_TOKEN"] = self.old_token
        for name, value in self.old_email_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_feishu_url_verification_and_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), start_background=False)
            client = TestClient(app)

            self.assertEqual(client.post("/feishu/events", json={"type": "url_verification", "challenge": "abc"}).json(), {"challenge": "abc"})

            response = client.post(
                "/feishu/events",
                json={
                    "header": {"token": "token", "event_type": "im.message.receive_v1"},
                    "event": {
                        "message": {"chat_id": "oc_x", "message_type": "text", "content": "{\"text\":\"绑定\"}"},
                        "sender": {"sender_id": {"open_id": "ou_x"}},
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(MonitorDB(db_path_for(Path(tmp))).get_binding("default_chat_id"), "oc_x")

    def test_card_action_creates_form_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "app.yml").write_text("monitor:\n  feed_urls: []\n", encoding="utf-8")
            (root / "config" / "schedule.yml").write_text("days: {}\n", encoding="utf-8")
            app = create_app(root, start_background=False)
            db = MonitorDB(db_path_for(root))
            db.upsert_opportunity(
                {
                    "id": "opp1",
                    "article_item_id": "item1",
                    "category": "volunteer",
                    "title": "活动",
                    "source_name": "源",
                    "article_url": "https://example.com",
                    "signup_url": "https://v.wjx.cn/vm/a.aspx",
                    "activity_time": "",
                    "deadline": "",
                    "location": "",
                    "schedule_status": "available",
                    "free_time_text": "",
                    "matched_time_text": "",
                    "raw_text": "",
                    "status": "pending_decision",
                    "feishu_message_id": "",
                }
            )
            client = TestClient(app)
            response = client.post(
                "/feishu/card-action",
                json={
                    "header": {"token": "token"},
                    "event": {
                        "action": {"value": {"opportunity_id": "opp1", "decision": "join"}},
                        "operator": {"open_id": "ou_x"},
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("报名任务已创建", response.json()["toast"]["content"])
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 1)

    def test_admin_poll_mail_triggers_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "app.yml").write_text(
                "monitor:\n  feed_urls: []\nvolunteer:\n  enabled: false\n",
                encoding="utf-8",
            )
            (root / "config" / "schedule.yml").write_text("days: {}\n", encoding="utf-8")
            app = create_app(root, start_background=False)
            client = TestClient(app)
            response = client.post("/admin/poll-mail-triggers")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "disabled")

    def test_auto_opportunity_email_dry_run_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(db_path_for(Path(tmp)))
            os.environ["EMAIL_DRY_RUN"] = "true"
            os.environ["SMTP_HOST"] = "smtp.qq.com"
            os.environ["SMTP_USER"] = "me@example.com"
            os.environ["SMTP_PASSWORD"] = ""
            os.environ["EMAIL_SENDER"] = "me@example.com"
            os.environ["EMAIL_BODY_MODE"] = "template"
            app_config = {
                "email": {
                    "auto_send_opportunities": True,
                    "auto_send_categories": ["volunteer"],
                    "opportunity_subject_template": "校园机会申请 - {name} - {title}",
                },
                "user": {"name": "张三", "phone": "13800000000", "contact_email": "me@example.com"},
            }
            analysis = OpportunityAnalysis(
                id="opp-email-1",
                is_target=True,
                category="volunteer",
                category_label="志愿活动",
                title="图书馆志愿服务招募",
                source_name="测试源",
                article_item_id="article-1",
                article_url="https://example.com/a",
                signup_url="https://example.com/signup",
                activity_time="周五 12:00-14:00",
                deadline="6月30日",
                location="图书馆",
                schedule_status="available",
                free_time_text="周五：11:20-15:00",
                matched_time_text="周五：12:00-14:00",
                raw_text="图书馆志愿服务招募，请发送邮件到 volunteer@example.edu.cn。",
                keyword_hits=["志愿服务", "招募"],
                reasons=[],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(maybe_auto_send_opportunity_email(db, app_config, analysis), "dry_run_sent")
            self.assertEqual(maybe_auto_send_opportunity_email(db, app_config, analysis), "skipped")
            with db.connect() as conn:
                rows = conn.execute("SELECT * FROM email_sends WHERE opportunity_id = ?", ("opp-email-1",)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["recipient"], "volunteer@example.edu.cn")


if __name__ == "__main__":
    unittest.main()
