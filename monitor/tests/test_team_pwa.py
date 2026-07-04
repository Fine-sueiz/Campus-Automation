import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from wg_monitor.db import MonitorDB
from wg_monitor.server import create_app, db_path_for


def make_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "config").mkdir()
    (root / "config" / "app.yml").write_text(
        """
monitor:
  feed_urls:
    - name: 志愿测试
      url: "examples/fake_volunteer_feed.xml"
  check_interval_seconds: 600
  article_timeout_seconds: 15
  fetch_article_html: false
opportunity:
  extra_keywords:
    - 志愿活动
form_runner:
  helper_dir: questionnaire_helper
  auto_submit: true
  headless: true
""",
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text("day_start: '08:00'\nday_end: '22:00'\ndays: {}\n", encoding="utf-8")
    return root


class TeamPwaTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {name: os.environ.get(name) for name in ["FORM_RUNNER_MODE", "WEB_PUSH_MODE"]}
        os.environ["FORM_RUNNER_MODE"] = "fake"
        os.environ["WEB_PUSH_MODE"] = "fake"

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_register_profile_schedule_scan_and_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            app = create_app(root, start_background=False)
            client = TestClient(app)

            login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})
            self.assertEqual(login.status_code, 200)

            register = client.post(
                "/api/auth/register",
                json={
                    "invite_code": "TEAM2026",
                    "username": "alice",
                    "password": "secret123",
                    "display_name": "Alice",
                },
            )
            self.assertEqual(register.status_code, 200)

            profile = client.put(
                "/api/profile",
                json={
                    "name": "张三",
                    "phone": "13800000000",
                    "student_id": "2026001",
                    "college": "计算机学院",
                    "grade": "大二",
                    "answers": [{"label": "政治面貌", "keywords": ["政治面貌"], "type": "single", "value": "共青团员"}],
                },
            )
            self.assertEqual(profile.status_code, 200)

            schedule = client.put(
                "/api/schedule",
                json={"busy_text": "周三 08:00-09:40 高数", "day_start": "08:00", "day_end": "22:00"},
            )
            self.assertEqual(schedule.status_code, 200)

            scan = client.post("/admin/scan-once").json()
            self.assertEqual(scan["opportunities"], 1)
            self.assertGreaterEqual(scan["user_opportunities"], 1)

            opportunities = client.get("/api/opportunities").json()["items"]
            self.assertEqual(len(opportunities), 1)
            opportunity_id = opportunities[0]["id"]
            self.assertIn(opportunities[0]["user_schedule_status"], {"available", "conflict", "unknown_time"})

            decision = client.post(f"/api/opportunities/{opportunity_id}/decision", json={"decision": "join"})
            self.assertEqual(decision.status_code, 200)
            self.assertEqual(decision.json()["status"], "approved")

            db = MonitorDB(db_path_for(root))
            tasks = db.due_form_tasks("9999-01-01T00:00:00+00:00")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["user_id"], register.json()["user"]["id"])

    def test_push_subscription_fake_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            app = create_app(root, start_background=False)
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "admin", "password": "admin123456"})

            response = client.post(
                "/api/push/subscribe",
                json={"endpoint": "fake-endpoint", "keys": {"p256dh": "x", "auth": "y"}},
            )
            self.assertEqual(response.status_code, 200)
            test = client.post("/api/push/test").json()
            self.assertEqual(test["sent"], 1)


if __name__ == "__main__":
    unittest.main()
