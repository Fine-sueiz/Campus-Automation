from __future__ import annotations

import importlib
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULE_DB_PATH", str(tmp_path / "schedule.db"))
    monkeypatch.setenv("SCHEDULE_API_KEY", "test-key")
    monkeypatch.setenv("MONITOR_INTEGRATION_KEY", "monitor-key")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def inbox_payload(**overrides):
    payload = {
        "provider": "wechat_monitor",
        "external_key": "opp-1",
        "source_item_id": "opp-1",
        "source_api_base": "http://127.0.0.1:8011",
        "title": "图书馆勤工助学招募",
        "summary": "报名截止到6月25日",
        "category": "项目",
        "start_at": "2026-06-25T00:00:00+08:00",
        "end_at": "2026-06-26T00:00:00+08:00",
        "all_day": True,
        "location": "图书馆",
        "source_name": "学校图书馆",
        "source_url": "https://example.com/article",
        "action_url": "https://example.com/signup",
        "event_payload": {
            "title": "勤工助学｜图书馆招募",
            "start_at": "2026-06-25T00:00:00+08:00",
            "end_at": "2026-06-26T00:00:00+08:00",
            "all_day": True,
            "category": "项目",
            "location": "图书馆",
            "notes": "公众号监测",
            "source": "campus-monitor",
            "reminder_minutes": 60,
            "recurrence": None,
        },
    }
    payload.update(overrides)
    return payload


def test_inbox_requires_api_key_and_dedupes(client):
    denied = client.post("/api/inbox/items", json=inbox_payload())
    assert denied.status_code == 401

    created = client.post(
        "/api/inbox/items",
        json=inbox_payload(),
        headers={"X-API-Key": "test-key"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    updated = client.post(
        "/api/inbox/items",
        json=inbox_payload(title="图书馆岗位更新"),
        headers={"X-API-Key": "test-key"},
    )
    assert updated.status_code == 201
    assert updated.json()["id"] == item_id
    assert updated.json()["title"] == "图书馆岗位更新"
    assert len(client.get("/api/inbox/items").json()) == 1


def test_add_calendar_from_inbox(client):
    item = client.post(
        "/api/inbox/items",
        json=inbox_payload(),
        headers={"X-API-Key": "test-key"},
    ).json()
    decided = client.post(
        f"/api/inbox/items/{item['id']}/decision",
        json={"action": "add_calendar", "updates": {}},
        headers={"X-API-Key": "test-key"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "calendar_added"
    assert decided.json()["item"]["event_id"]

    events = client.get("/api/events?from=2026-06-25&to=2026-06-25").json()
    assert len(events) == 1
    assert events[0]["title"] == "勤工助学｜图书馆招募"


def test_join_reuses_existing_calendar_event(client):
    item = client.post(
        "/api/inbox/items",
        json=inbox_payload(),
        headers={"X-API-Key": "test-key"},
    ).json()
    added = client.post(
        f"/api/inbox/items/{item['id']}/decision",
        json={"action": "add_calendar", "updates": {}},
        headers={"X-API-Key": "test-key"},
    ).json()
    event_id = added["item"]["event_id"]

    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "status": "approved",
        "calendar_sync": {"status": "already_synced", "event_id": event_id},
    }
    with patch("app.inbox.httpx.post", return_value=response) as post:
        joined = client.post(
            f"/api/inbox/items/{item['id']}/decision",
            json={"action": "join", "updates": {}},
            headers={"X-API-Key": "test-key"},
        )

    assert joined.status_code == 200
    assert joined.json()["status"] == "joined"
    assert post.call_args.kwargs["json"]["calendar_event_id"] == event_id
    events = client.get("/api/events?from=2026-06-25&to=2026-06-25").json()
    assert len(events) == 1


def test_later_and_ignore_persist(client):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"status": "later"}
    item = client.post(
        "/api/inbox/items",
        json=inbox_payload(),
        headers={"X-API-Key": "test-key"},
    ).json()
    with patch("app.inbox.httpx.post", return_value=response):
        later = client.post(
            f"/api/inbox/items/{item['id']}/decision",
            json={"action": "later", "updates": {}},
            headers={"X-API-Key": "test-key"},
        )
    assert later.json()["status"] == "later"

    repeated = client.post(
        "/api/inbox/items",
        json=inbox_payload(title="再次扫描更新"),
        headers={"X-API-Key": "test-key"},
    )
    assert repeated.json()["status"] == "later"
