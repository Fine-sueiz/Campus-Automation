from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULE_DB_PATH", str(tmp_path / "schedule.db"))
    monkeypatch.setenv("SCHEDULE_API_KEY", "test-key")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client


def event_payload(**overrides):
    payload = {
        "title": "高数复习",
        "start_at": "2026-06-18T09:00:00+08:00",
        "end_at": "2026-06-18T10:00:00+08:00",
        "all_day": False,
        "category": "课程",
        "location": "图书馆",
        "notes": "刷题",
        "source": "manual",
        "reminder_minutes": 30,
        "recurrence": None,
    }
    payload.update(overrides)
    return payload


def test_api_key_required_for_writes(client):
    response = client.post("/api/events", json=event_payload())
    assert response.status_code == 401


def test_create_and_list_single_event(client):
    created = client.post(
        "/api/events",
        json=event_payload(),
        headers={"X-API-Key": "test-key"},
    )
    assert created.status_code == 201
    response = client.get("/api/events?from=2026-06-01&to=2026-06-30")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "高数复习"
    assert items[0]["is_recurring"] is False


def test_daily_weekly_monthly_yearly_recurrence(client):
    rules = [
        ("daily", "2026-06-18T08:00:00+08:00", "2026-06-18T09:00:00+08:00", 3),
        (
            "weekly",
            "2026-06-18T10:00:00+08:00",
            "2026-06-18T11:00:00+08:00",
            2,
        ),
        (
            "monthly",
            "2026-06-18T12:00:00+08:00",
            "2026-06-18T13:00:00+08:00",
            2,
        ),
        (
            "yearly",
            "2026-06-18T14:00:00+08:00",
            "2026-06-18T15:00:00+08:00",
            1,
        ),
    ]
    for freq, start_at, end_at, count in rules:
        response = client.post(
            "/api/events",
            json=event_payload(
                title=f"{freq} event",
                start_at=start_at,
                end_at=end_at,
                recurrence={"freq": freq, "interval": 1, "count": count},
            ),
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 201

    response = client.get("/api/events?from=2026-06-01&to=2026-08-31")
    titles = [item["title"] for item in response.json()]
    assert titles.count("daily event") == 3
    assert titles.count("weekly event") == 2
    assert titles.count("monthly event") == 2
    assert titles.count("yearly event") == 1


def test_modify_and_delete_single_occurrence(client):
    created = client.post(
        "/api/events",
        json=event_payload(
            recurrence={"freq": "daily", "interval": 1, "count": 3},
        ),
        headers={"X-API-Key": "test-key"},
    ).json()
    event_id = created["id"]

    modify = client.post(
        f"/api/events/{event_id}/occurrences/modify",
        json={
            "occurrence_start": "2026-06-19T09:00:00+08:00",
            "scope": "this",
            "updates": {"title": "改成英语复习"},
        },
        headers={"X-API-Key": "test-key"},
    )
    assert modify.status_code == 200

    delete = client.post(
        f"/api/events/{event_id}/occurrences/delete",
        json={"occurrence_start": "2026-06-20T09:00:00+08:00", "scope": "this"},
        headers={"X-API-Key": "test-key"},
    )
    assert delete.status_code == 200

    items = client.get("/api/events?from=2026-06-18&to=2026-06-21").json()
    titles = [item["title"] for item in items]
    assert titles == ["高数复习", "改成英语复习"]


def test_future_scope_splits_series(client):
    created = client.post(
        "/api/events",
        json=event_payload(recurrence={"freq": "daily", "interval": 1, "count": 4}),
        headers={"X-API-Key": "test-key"},
    ).json()
    event_id = created["id"]

    response = client.post(
        f"/api/events/{event_id}/occurrences/modify",
        json={
            "occurrence_start": "2026-06-20T09:00:00+08:00",
            "scope": "future",
            "updates": {"title": "后续改成项目开发"},
        },
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200

    items = client.get("/api/events?from=2026-06-18&to=2026-06-22").json()
    titles = [item["title"] for item in items]
    assert titles == ["高数复习", "高数复习", "后续改成项目开发", "后续改成项目开发"]
