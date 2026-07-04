from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "qq_sync_config.json"
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "auto_create_min_confidence": 0.82,
                "groups": [
                    {
                        "group_name": "概率论A课程群",
                        "group_id": "",
                        "course_name": "概率论A",
                        "teacher_names": ["张老师"],
                        "teacher_ids": [],
                        "default_category": "课程",
                        "reminder_minutes": 1440,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SCHEDULE_DB_PATH", str(tmp_path / "schedule.db"))
    monkeypatch.setenv("SCHEDULE_API_KEY", "test-key")
    monkeypatch.setenv("QQ_SYNC_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("QQ_SYNC_LLM_API_KEY", raising=False)
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client


def qq_message(**overrides):
    payload = {
        "group_name": "概率论A课程群",
        "sender_name": "张老师",
        "course_name": "概率论A",
        "text": "下周三交概率论作业，记得提交到学习通。",
        "message_time": "2026-06-20T10:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def test_qq_message_requires_api_key(client):
    response = client.post("/api/integrations/qq/messages", json=qq_message())
    assert response.status_code == 401


def test_non_teacher_message_is_ignored(client):
    response = client.post(
        "/api/integrations/qq/messages",
        json=qq_message(sender_name="李同学"),
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored_sender_not_allowed"

    events = client.get("/api/events?from=2026-06-01&to=2026-06-30").json()
    assert events == []
    candidates = client.get("/api/integrations/qq/candidates").json()
    assert candidates == []


def test_clear_teacher_message_auto_creates_event_and_dedupes(client):
    response = client.post(
        "/api/integrations/qq/messages",
        json=qq_message(external_key="qq-msg-1"),
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "created"

    duplicate = client.post(
        "/api/integrations/qq/messages",
        json=qq_message(external_key="qq-msg-1"),
        headers={"X-API-Key": "test-key"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "skipped"

    events = client.get("/api/events?from=2026-06-20&to=2026-06-30").json()
    assert len(events) == 1
    assert events[0]["title"].startswith("QQ群｜概率论A｜")
    assert events[0]["category"] == "作业"
    assert events[0]["all_day"] is True
    assert events[0]["start_at"].startswith("2026-06-24T00:00:00")


def test_vague_teacher_message_becomes_pending_candidate_then_confirmed(client):
    response = client.post(
        "/api/integrations/qq/messages",
        json=qq_message(external_key="qq-msg-2", text="下次课交概率论作业，大家提前准备。"),
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    candidate_id = body["candidate"]["id"]
    assert body["candidate"]["missing_fields"] == ["start_at"]

    confirm = client.post(
        f"/api/integrations/qq/candidates/{candidate_id}/confirm",
        json={
            "updates": {
                "title": "QQ群｜概率论A｜概率论作业",
                "start_at": "2026-06-24T09:00:00+08:00",
                "end_at": "2026-06-24T10:00:00+08:00",
                "all_day": False,
                "category": "作业",
                "location": "",
                "notes": "手动确认",
                "source": "qq",
                "reminder_minutes": 1440,
                "recurrence": None,
            }
        },
        headers={"X-API-Key": "test-key"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "created"

    events = client.get("/api/events?from=2026-06-24&to=2026-06-24").json()
    assert len(events) == 1
    assert events[0]["title"] == "QQ群｜概率论A｜概率论作业"
