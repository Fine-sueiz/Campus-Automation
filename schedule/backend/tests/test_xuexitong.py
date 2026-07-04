from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import xuexitong
from app.settings import SHANGHAI_TZ


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULE_DB_PATH", str(tmp_path / "schedule.db"))
    monkeypatch.setenv("SCHEDULE_API_KEY", "test-key")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, main


def test_parse_chinese_and_slash_deadlines():
    today = date(2026, 6, 19)
    samples = [
        ("截止时间：2026年6月25日 23:59", "2026-06-25T23:59:00+08:00", False),
        ("截至 6月25日 23:59 前提交", "2026-06-25T23:59:00+08:00", False),
        ("截止：6/25 23:59", "2026-06-25T23:59:00+08:00", False),
        ("截止日期：2026-06-25", "2026-06-25T00:00:00+08:00", True),
    ]
    for text, expected_start, expected_all_day in samples:
        parsed = xuexitong.parse_deadline(text, today=today)
        assert parsed is not None
        assert parsed.start_at.isoformat() == expected_start
        assert parsed.all_day is expected_all_day


def test_parse_items_classifies_assignment_and_exam():
    text = """
    课程：高等数学
    作业名称：第一章函数练习
    截止时间：2026年6月25日 23:59

    课程：大学英语
    测验：Unit 2 Quiz
    截止：6/28 20:00
    """
    items = xuexitong.parse_items_from_text(text, source_url="https://mooc1.chaoxing.com/work", today=date(2026, 6, 19))
    titles = {(item.item_type, item.course_name, item.task_name) for item in items}
    assert ("作业", "高等数学", "第一章函数练习") in titles
    assert ("考试", "大学英语", "Unit 2 Quiz") in titles


def test_no_deadline_is_skipped():
    text = "课程：高等数学\n作业名称：第一章函数练习\n请大家认真完成"
    assert xuexitong.parse_items_from_text(text, source_url="https://mooc1.chaoxing.com/work") == []


def test_course_open_time_and_empty_pages_are_not_deadlines():
    text = """
    C++程序设计
    开课时间：2026-03-02～2028-03-02
    作业
    筛选 全部 已完成 未完成 0/0
    暂无作业

    考试
    筛选 全部 已完成 未完成
    暂无考试
    """
    assert xuexitong.parse_items_from_text(text, source_url="https://mooc1.chaoxing.com/work") == []


def fake_item(*, deadline: datetime, external_key: str = "fixed-xuexitong-task") -> xuexitong.XuexitongItem:
    return xuexitong.XuexitongItem(
        external_key=external_key,
        item_type="作业",
        course_name="高等数学",
        task_name="第一章函数练习",
        start_at=deadline,
        end_at=deadline + timedelta(minutes=30),
        all_day=False,
        deadline_text="截止时间：2026年6月25日 23:59",
        source_url="https://mooc1.chaoxing.com/work",
        raw_text="课程：高等数学 作业名称：第一章函数练习 截止时间：2026年6月25日 23:59",
    )


def test_xuexitong_sync_requires_api_key(client, monkeypatch):
    client_app, main = client
    monkeypatch.setattr(
        main.xuexitong,
        "read_items_from_chrome",
        lambda: {"status": "ok", "needs_login": False, "items": [], "pages_scanned": 0, "error": ""},
    )
    response = client_app.post("/api/integrations/xuexitong/sync")
    assert response.status_code == 401


def test_xuexitong_sync_dedupes_existing_event(client, monkeypatch):
    client_app, main = client
    deadline = datetime(2026, 6, 25, 23, 59, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        main.xuexitong,
        "read_items_from_chrome",
        lambda: {
            "status": "ok",
            "needs_login": False,
            "items": [fake_item(deadline=deadline)],
            "pages_scanned": 1,
            "error": "",
        },
    )

    first = client_app.post("/api/integrations/xuexitong/sync", headers={"X-API-Key": "test-key"})
    assert first.status_code == 200
    assert first.json()["created"] == 1

    second = client_app.post("/api/integrations/xuexitong/sync", headers={"X-API-Key": "test-key"})
    assert second.status_code == 200
    assert second.json()["skipped"] == 1

    items = client_app.get("/api/events?from=2026-06-25&to=2026-06-25").json()
    assert len(items) == 1
    assert items[0]["title"] == "学习通｜高等数学｜第一章函数练习"


def test_xuexitong_sync_updates_changed_deadline(client, monkeypatch):
    client_app, main = client
    first_deadline = datetime(2026, 6, 25, 23, 59, tzinfo=SHANGHAI_TZ)
    second_deadline = datetime(2026, 6, 26, 22, 0, tzinfo=SHANGHAI_TZ)

    monkeypatch.setattr(
        main.xuexitong,
        "read_items_from_chrome",
        lambda: {
            "status": "ok",
            "needs_login": False,
            "items": [fake_item(deadline=first_deadline)],
            "pages_scanned": 1,
            "error": "",
        },
    )
    client_app.post("/api/integrations/xuexitong/sync", headers={"X-API-Key": "test-key"})

    monkeypatch.setattr(
        main.xuexitong,
        "read_items_from_chrome",
        lambda: {
            "status": "ok",
            "needs_login": False,
            "items": [fake_item(deadline=second_deadline)],
            "pages_scanned": 1,
            "error": "",
        },
    )
    response = client_app.post("/api/integrations/xuexitong/sync", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    old_day = client_app.get("/api/events?from=2026-06-25&to=2026-06-25").json()
    new_day = client_app.get("/api/events?from=2026-06-26&to=2026-06-26").json()
    assert old_day == []
    assert len(new_day) == 1
    assert new_day[0]["start_at"] == "2026-06-26T22:00:00+08:00"
