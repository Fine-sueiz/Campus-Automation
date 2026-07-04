from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from wg_monitor.db import MonitorDB
from wg_monitor.schedule_inbox import sync_opportunity_to_schedule_inbox
from wg_monitor.server import create_app, db_path_for
from wg_monitor.team import password_hash


def app_config() -> dict:
    return {
        "calendar_sync": {
            "enabled": True,
            "api_base": "http://127.0.0.1:8000",
            "api_key": "test-key",
            "usernames": ["admin"],
            "reminder_minutes": 60,
            "timeout_seconds": 2,
        },
        "schedule_inbox": {
            "enabled": True,
            "api_base": "http://127.0.0.1:8000",
            "api_key": "test-key",
            "monitor_api_base": "http://127.0.0.1:8011",
            "integration_key": "integration-key",
            "usernames": ["admin"],
            "timeout_seconds": 2,
        },
    }


def opportunity(**overrides) -> dict:
    payload = {
        "id": "opp-inbox-1",
        "article_item_id": "article-1",
        "category": "work_study",
        "title": "图书馆勤工助学",
        "source_name": "图书馆",
        "article_url": "https://example.com/article",
        "signup_url": "https://example.com/signup",
        "activity_time": "",
        "deadline": "2026年6月25日",
        "location": "图书馆",
        "schedule_status": "available",
        "free_time_text": "",
        "matched_time_text": "",
        "raw_text": "报名截止2026年6月25日",
        "status": "pending_decision",
        "feishu_message_id": "",
    }
    payload.update(overrides)
    return payload


def create_admin_opportunity(db: MonitorDB) -> dict:
    admin = db.create_user("admin", password_hash("secret123"), "Admin", role="admin")
    db.upsert_opportunity(opportunity())
    db.upsert_user_opportunity(
        {
            "id": f"{admin['id']}:opp-inbox-1",
            "user_id": admin["id"],
            "opportunity_id": "opp-inbox-1",
            "schedule_status": "available",
            "free_time_text": "",
            "matched_time_text": "",
            "status": "pending_decision",
        }
    )
    return admin


def test_pushes_selected_user_and_dedupes():
    with tempfile.TemporaryDirectory() as tmp:
        db = MonitorDB(Path(tmp) / "monitor.sqlite3")
        admin = create_admin_opportunity(db)
        response = Mock()
        response.status_code = 201
        response.json.return_value = {"id": "inbox-1"}
        with patch("wg_monitor.schedule_inbox.requests.post", return_value=response) as post:
            first = sync_opportunity_to_schedule_inbox(db, app_config(), opportunity(), user=admin)
            second = sync_opportunity_to_schedule_inbox(db, app_config(), opportunity(), user=admin)

        assert first == {"status": "synced", "inbox_item_id": "inbox-1"}
        assert second["status"] == "already_synced"
        assert post.call_count == 1
        sent = post.call_args.kwargs["json"]
        assert sent["provider"] == "wechat_monitor"
        assert sent["external_key"] == "opp-inbox-1"
        assert sent["event_payload"]["title"].startswith("勤工助学｜")


def test_schedule_service_decision_uses_key_and_reuses_event():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "config" / "app.yml").write_text(
            """
monitor:
  feed_urls: []
calendar_sync:
  enabled: true
  usernames: [admin]
schedule_inbox:
  enabled: true
  usernames: [admin]
  integration_key: integration-key
""",
            encoding="utf-8",
        )
        (root / "config" / "schedule.yml").write_text("days: {}\n", encoding="utf-8")
        app = create_app(root, start_background=False)
        client = TestClient(app)
        db = MonitorDB(db_path_for(root))
        admin = db.get_user_by_username("admin")
        assert admin is not None
        db.upsert_opportunity(opportunity())
        db.upsert_user_opportunity(
            {
                "id": f"{admin['id']}:opp-inbox-1",
                "user_id": admin["id"],
                "opportunity_id": "opp-inbox-1",
                "schedule_status": "available",
                "free_time_text": "",
                "matched_time_text": "",
                "status": "pending_decision",
            }
        )

        denied = client.post(
            "/api/integrations/schedule/opportunities/opp-inbox-1/decision",
            json={"decision": "join", "calendar_event_id": "event-1"},
        )
        assert denied.status_code == 401

        approved = client.post(
            "/api/integrations/schedule/opportunities/opp-inbox-1/decision",
            json={"decision": "join", "calendar_event_id": "event-1"},
            headers={"X-Integration-Key": "integration-key"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["calendar_sync"]["event_id"] == "event-1"
        assert len(db.due_form_tasks("9999-01-01T00:00:00+00:00")) == 1
        assert db.get_calendar_sync("opp-inbox-1", str(admin["id"]))["calendar_event_id"] == "event-1"
