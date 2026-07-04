from __future__ import annotations

from pathlib import Path
from typing import Any

from .calendar_sync import sync_opportunity_to_calendar
from .settings import load_project_cached
from .db import MonitorDB, utc_now


def apply_user_opportunity_decision(
    root: Path,
    db: MonitorDB,
    opportunity_id: str,
    user: dict[str, Any],
    decision: str,
    *,
    calendar_event_id: str = "",
) -> dict[str, Any]:
    user_id = str(user.get("id") or "")
    item = db.get_user_opportunity(user_id, opportunity_id)
    if not item:
        return {"status": "not_found", "message": "未找到本人机会记录"}

    if decision == "join":
        if not item.get("signup_url"):
            db.update_user_opportunity_status(user_id, opportunity_id, "need_human")
            return {"status": "need_human", "message": "未识别到报名链接"}
        task_id = f"task-{user_id}-{opportunity_id}"
        db.create_form_task(task_id, opportunity_id, str(item["signup_url"]), utc_now(), user_id=user_id)
        db.update_user_opportunity_status(user_id, opportunity_id, "approved")
        _paths, app_config, _schedule_config = load_project_cached(root)
        if calendar_event_id:
            db.upsert_calendar_sync(
                opportunity_id,
                user_id,
                "synced",
                calendar_event_id=calendar_event_id,
                payload={"source": "schedule_inbox", "existing_event_id": calendar_event_id},
            )
            sync_result = {"status": "already_synced", "event_id": calendar_event_id}
        else:
            sync_result = sync_opportunity_to_calendar(db, app_config, item, user=user)
        return {"status": "approved", "task_id": task_id, "calendar_sync": sync_result}

    if decision == "reject":
        db.update_user_opportunity_status(user_id, opportunity_id, "rejected")
        return {"status": "rejected"}
    if decision == "later":
        db.update_user_opportunity_status(user_id, opportunity_id, "later")
        return {"status": "later"}

    db.update_user_opportunity_status(user_id, opportunity_id, "need_human")
    return {"status": "need_human", "message": "未知操作"}
