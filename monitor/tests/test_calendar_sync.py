import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from wg_monitor.calendar_sync import (
    CalendarSyncSettings,
    build_calendar_payload,
    retry_calendar_syncs,
    sync_opportunity_to_calendar,
)
from wg_monitor.db import MonitorDB
from wg_monitor.feishu_handlers import handle_card_decision
from wg_monitor.server import create_app, db_path_for
from wg_monitor.team import password_hash
from wg_monitor.volunteer import confirmation_token, handle_volunteer_confirmation


def app_config(usernames=None):
    return {
        "calendar_sync": {
            "enabled": True,
            "api_base": "http://calendar.test",
            "api_key": "test-key",
            "usernames": usernames if usernames is not None else ["alice"],
            "reminder_minutes": 60,
            "timeout_seconds": 3,
        }
    }


def opportunity(**overrides):
    data = {
        "id": "opp-calendar-1",
        "article_item_id": "item1",
        "category": "volunteer",
        "title": "图书馆志愿服务招募",
        "source_name": "测试公众号",
        "article_url": "https://example.com/article",
        "signup_url": "https://v.wjx.cn/vm/a.aspx",
        "activity_time": "2026年6月20日 14:00-16:00",
        "deadline": "2026年6月18日",
        "location": "图书馆一楼",
        "schedule_status": "available",
        "free_time_text": "",
        "matched_time_text": "",
        "raw_text": "",
        "status": "pending_decision",
        "feishu_message_id": "",
    }
    data.update(overrides)
    return data


class CalendarSyncTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {
            name: os.environ.get(name)
            for name in [
                "CALENDAR_SYNC_ENABLED",
                "CALENDAR_SYNC_API_BASE",
                "CALENDAR_SYNC_API_KEY",
                "CALENDAR_SYNC_USERNAMES",
            ]
        }
        for name in self.old_env:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_build_payload_uses_explicit_activity_datetime(self):
        payload, mode = build_calendar_payload(
            opportunity(),
            CalendarSyncSettings.from_config(app_config()),
        )

        self.assertEqual(mode, "activity")
        self.assertEqual(payload["title"], "志愿｜图书馆志愿服务招募")
        self.assertEqual(payload["start_at"], "2026-06-20T14:00:00+08:00")
        self.assertEqual(payload["end_at"], "2026-06-20T16:00:00+08:00")
        self.assertFalse(payload["all_day"])
        self.assertEqual(payload["category"], "志愿活动")

    def test_build_payload_falls_back_to_deadline_for_weekday_only_time(self):
        payload, mode = build_calendar_payload(
            opportunity(activity_time="周二 14:00-16:00", deadline="2026年6月30日"),
            CalendarSyncSettings.from_config(app_config()),
        )

        self.assertEqual(mode, "deadline")
        self.assertEqual(payload["title"], "报名截止｜图书馆志愿服务招募")
        self.assertEqual(payload["start_at"], "2026-06-30T00:00:00+08:00")
        self.assertEqual(payload["end_at"], "2026-07-01T00:00:00+08:00")
        self.assertTrue(payload["all_day"])

    def test_time_range_without_date_does_not_break_deadline_fallback(self):
        payload, mode = build_calendar_payload(
            opportunity(activity_time="10:00-11:30", deadline="2026年6月24日"),
            CalendarSyncSettings.from_config(app_config()),
        )

        self.assertEqual(mode, "deadline")
        self.assertEqual(payload["start_at"], "2026-06-24T00:00:00+08:00")

    def test_sync_only_selected_user_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(db_path_for(Path(tmp)))
            alice = db.create_user("alice", password_hash("secret123"), "Alice")
            bob = db.create_user("bob", password_hash("secret123"), "Bob")

            response = Mock()
            response.status_code = 201
            response.json.return_value = {"id": "calendar-event-1"}
            with patch("wg_monitor.calendar_sync.requests.post", return_value=response) as post:
                skipped = sync_opportunity_to_calendar(db, app_config(["alice"]), opportunity(), user=bob)
                synced = sync_opportunity_to_calendar(db, app_config(["alice"]), opportunity(), user=alice)
                duplicate = sync_opportunity_to_calendar(db, app_config(["alice"]), opportunity(), user=alice)

            self.assertEqual(skipped["status"], "skipped")
            self.assertEqual(synced["status"], "synced")
            self.assertEqual(duplicate["status"], "already_synced")
            self.assertEqual(post.call_count, 1)
            row = db.get_calendar_sync("opp-calendar-1", alice["id"])
            self.assertEqual(row["status"], "synced")
            self.assertEqual(row["calendar_event_id"], "calendar-event-1")

    def test_sync_failure_is_recorded_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(db_path_for(Path(tmp)))
            alice = db.create_user("alice", password_hash("secret123"), "Alice")

            with patch("wg_monitor.calendar_sync.requests.post", side_effect=RuntimeError("calendar down")):
                result = sync_opportunity_to_calendar(db, app_config(["alice"]), opportunity(), user=alice)

            self.assertEqual(result["status"], "failed")
            row = db.get_calendar_sync("opp-calendar-1", alice["id"])
            self.assertEqual(row["status"], "failed")
            self.assertIn("calendar down", row["error"])

    def test_pwa_join_creates_task_and_syncs_for_selected_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "app.yml").write_text(
                """
monitor:
  feed_urls: []
calendar_sync:
  enabled: true
  api_base: "http://calendar.test"
  api_key: "test-key"
  usernames:
    - alice
  reminder_minutes: 60
""",
                encoding="utf-8",
            )
            (root / "config" / "schedule.yml").write_text("days: {}\n", encoding="utf-8")
            app = create_app(root, start_background=False)
            client = TestClient(app)
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
            user_id = register.json()["user"]["id"]
            db = MonitorDB(db_path_for(root))
            db.upsert_opportunity(opportunity())
            db.upsert_user_opportunity(
                {
                    "id": f"{user_id}:opp-calendar-1",
                    "user_id": user_id,
                    "opportunity_id": "opp-calendar-1",
                    "schedule_status": "available",
                    "free_time_text": "",
                    "matched_time_text": "",
                    "status": "pending_decision",
                }
            )

            response = Mock()
            response.status_code = 201
            response.json.return_value = {"id": "calendar-event-2"}
            with patch("wg_monitor.calendar_sync.requests.post", return_value=response):
                decision = client.post("/api/opportunities/opp-calendar-1/decision", json={"decision": "join"})

            self.assertEqual(decision.status_code, 200)
            self.assertEqual(decision.json()["status"], "approved")
            self.assertEqual(decision.json()["calendar_sync"]["status"], "synced")
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 1)
            row = db.get_calendar_sync("opp-calendar-1", user_id)
            self.assertEqual(row["calendar_event_id"], "calendar-event-2")

    def test_retry_does_not_sync_same_opportunity_as_user_and_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(db_path_for(Path(tmp)))
            alice = db.create_user("alice", password_hash("secret123"), "Alice")
            approved = opportunity(id="opp-calendar-approved", status="approved")
            db.upsert_opportunity(approved)
            db.upsert_user_opportunity(
                {
                    "id": f"{alice['id']}:opp-calendar-approved",
                    "user_id": alice["id"],
                    "opportunity_id": "opp-calendar-approved",
                    "schedule_status": "available",
                    "free_time_text": "",
                    "matched_time_text": "",
                    "status": "approved",
                }
            )

            response = Mock()
            response.status_code = 201
            response.json.return_value = {"id": "calendar-event-retry"}
            with patch("wg_monitor.calendar_sync.requests.post", return_value=response) as post:
                result = retry_calendar_syncs(db, app_config(["alice"]))

            self.assertEqual(result["counts"]["candidates"], 1)
            self.assertEqual(result["counts"]["synced"], 1)
            self.assertEqual(post.call_count, 1)
            self.assertIsNotNone(db.get_calendar_sync("opp-calendar-approved", alice["id"]))
            self.assertIsNone(db.get_calendar_sync("opp-calendar-approved", ""))

    def test_email_confirmation_syncs_calendar_without_user_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            approved = opportunity(id="opp-calendar-email")
            db.upsert_opportunity(approved)
            token = confirmation_token("opp-calendar-email")
            db.create_volunteer_confirmation(token, "opp-calendar-email", "9999-01-01T00:00:00+00:00")

            response = Mock()
            response.status_code = 201
            response.json.return_value = {"id": "calendar-event-email"}
            with patch("wg_monitor.calendar_sync.requests.post", return_value=response):
                status = handle_volunteer_confirmation(db, app_config([]), token, "join")

            self.assertEqual(status, "approved")
            row = db.get_calendar_sync("opp-calendar-email", "")
            self.assertEqual(row["status"], "synced")
            self.assertEqual(row["calendar_event_id"], "calendar-event-email")

    def test_feishu_card_decision_syncs_calendar_when_config_is_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MonitorDB(db_path_for(root))
            db.upsert_opportunity(opportunity(id="opp-calendar-feishu"))

            response = Mock()
            response.status_code = 201
            response.json.return_value = {"id": "calendar-event-feishu"}
            with patch("wg_monitor.calendar_sync.requests.post", return_value=response):
                result = handle_card_decision(
                    root,
                    db,
                    Mock(),
                    {"event": {"action": {"value": {"opportunity_id": "opp-calendar-feishu", "decision": "join"}}}},
                    app_config=app_config([]),
                )

            self.assertIn("报名任务已创建", result["toast"]["content"])
            row = db.get_calendar_sync("opp-calendar-feishu", "")
            self.assertEqual(row["status"], "synced")
            self.assertEqual(row["calendar_event_id"], "calendar-event-feishu")


if __name__ == "__main__":
    unittest.main()
