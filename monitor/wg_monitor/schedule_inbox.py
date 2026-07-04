from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from .calendar_sync import CalendarSyncSettings, build_calendar_payload
from .db import MonitorDB


@dataclass(frozen=True)
class ScheduleInboxSettings:
    enabled: bool
    api_base: str
    api_key: str
    usernames: list[str]
    monitor_api_base: str
    integration_key: str
    timeout_seconds: int

    @classmethod
    def from_config(cls, app_config: dict[str, Any]) -> "ScheduleInboxSettings":
        calendar_config = app_config.get("calendar_sync") or {}
        config = app_config.get("schedule_inbox") or {}
        usernames = config.get("usernames") or calendar_config.get("usernames") or []
        if isinstance(usernames, str):
            usernames = [item.strip() for item in usernames.split(",") if item.strip()]
        env_usernames = os.getenv("SCHEDULE_INBOX_USERNAMES", "").strip()
        if env_usernames:
            usernames = [item.strip() for item in env_usernames.split(",") if item.strip()]
        enabled_raw = os.getenv("SCHEDULE_INBOX_ENABLED")
        enabled = bool(config.get("enabled", calendar_config.get("enabled", False)))
        if enabled_raw is not None and enabled_raw != "":
            enabled = enabled_raw.strip().lower() in {"1", "true", "yes", "y", "on"}
        return cls(
            enabled=enabled,
            api_base=os.getenv(
                "SCHEDULE_INBOX_API_BASE",
                str(config.get("api_base") or calendar_config.get("api_base") or "http://127.0.0.1:8000"),
            ).rstrip("/"),
            api_key=os.getenv(
                "SCHEDULE_INBOX_API_KEY",
                str(config.get("api_key") or calendar_config.get("api_key") or "dev-schedule-key"),
            ),
            usernames=[str(item).strip() for item in usernames if str(item).strip()],
            monitor_api_base=os.getenv(
                "MONITOR_PUBLIC_API_BASE",
                str(config.get("monitor_api_base") or "http://127.0.0.1:8011"),
            ).rstrip("/"),
            integration_key=os.getenv(
                "MONITOR_INTEGRATION_KEY",
                str(config.get("integration_key") or "dev-schedule-key"),
            ),
            timeout_seconds=int(
                os.getenv(
                    "SCHEDULE_INBOX_TIMEOUT_SECONDS",
                    str(config.get("timeout_seconds") or calendar_config.get("timeout_seconds") or 8),
                )
            ),
        )


def selected_user(db: MonitorDB, settings: ScheduleInboxSettings) -> dict[str, Any] | None:
    for username in settings.usernames:
        user = db.get_user_by_username(username)
        if user:
            return user
    ordinary_users = [item for item in db.list_users() if item.get("role") != "admin"]
    if not settings.usernames and len(ordinary_users) == 1:
        return db.get_user(str(ordinary_users[0]["id"]))
    return None


def build_summary(opportunity: dict[str, Any]) -> str:
    lines = [
        f"来源：{opportunity.get('source_name') or '公众号监测'}",
        f"类别：{opportunity.get('category') or '其他'}",
        f"活动时间：{opportunity.get('activity_time') or '未识别'}",
        f"报名截止：{opportunity.get('deadline') or '未识别'}",
        f"地点：{opportunity.get('location') or '未识别'}",
        f"匹配情况：{opportunity.get('user_schedule_status') or opportunity.get('schedule_status') or '未知'}",
    ]
    raw_text = str(opportunity.get("raw_text") or "").strip()
    if raw_text:
        lines.extend(["", f"原文摘要：{raw_text[:2200]}"])
    return "\n".join(lines)[:3800]


def build_inbox_payload(
    opportunity: dict[str, Any],
    settings: ScheduleInboxSettings,
    app_config: dict[str, Any],
) -> dict[str, Any]:
    calendar_settings = CalendarSyncSettings.from_config(app_config)
    event_payload, _mode = build_calendar_payload(opportunity, calendar_settings)
    category = str(opportunity.get("category") or "其他")
    title = str(opportunity.get("title") or "公众号机会").strip()
    if event_payload and category == "work_study":
        event_payload["title"] = f"勤工助学｜{title}"[:120]
        event_payload["category"] = "项目"
    return {
        "provider": "wechat_monitor",
        "external_key": str(opportunity.get("id") or ""),
        "source_item_id": str(opportunity.get("id") or ""),
        "source_api_base": settings.monitor_api_base,
        "title": title[:160],
        "summary": build_summary(opportunity),
        "category": "志愿活动" if category == "volunteer" else "项目" if category == "work_study" else category,
        "start_at": event_payload.get("start_at") if event_payload else None,
        "end_at": event_payload.get("end_at") if event_payload else None,
        "all_day": bool(event_payload.get("all_day")) if event_payload else False,
        "location": str(opportunity.get("location") or "")[:200],
        "source_name": str(opportunity.get("source_name") or "公众号监测")[:160],
        "source_url": str(opportunity.get("article_url") or "")[:1000],
        "action_url": str(opportunity.get("signup_url") or "")[:1000],
        "event_payload": event_payload,
        "raw_payload": {
            "opportunity_id": opportunity.get("id"),
            "user_id": opportunity.get("user_id", ""),
            "user_status": opportunity.get("user_status", ""),
            "schedule_status": opportunity.get("user_schedule_status") or opportunity.get("schedule_status", ""),
            "activity_time": opportunity.get("activity_time", ""),
            "deadline": opportunity.get("deadline", ""),
        },
    }


def sync_opportunity_to_schedule_inbox(
    db: MonitorDB,
    app_config: dict[str, Any],
    opportunity: dict[str, Any],
    *,
    user: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    settings = ScheduleInboxSettings.from_config(app_config)
    if not settings.enabled:
        return {"status": "skipped", "reason": "disabled"}
    user = user or selected_user(db, settings)
    if not user:
        return {"status": "skipped", "reason": "selected_user_not_found"}
    opportunity_id = str(opportunity.get("id") or "")
    user_id = str(user.get("id") or "")
    if not opportunity_id:
        return {"status": "skipped", "reason": "missing_opportunity_id"}

    existing = db.get_schedule_inbox_sync(opportunity_id, user_id)
    if existing and existing.get("status") == "synced" and not force:
        return {"status": "already_synced", "inbox_item_id": existing.get("inbox_item_id", "")}

    user_item = db.get_user_opportunity(user_id, opportunity_id)
    source = user_item or opportunity
    payload = build_inbox_payload(source, settings, app_config)
    try:
        response = requests.post(
            f"{settings.api_base}/api/inbox/items",
            headers={"X-API-Key": settings.api_key},
            json=payload,
            timeout=settings.timeout_seconds,
        )
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}: {response.text[:300]}"
            db.upsert_schedule_inbox_sync(opportunity_id, user_id, "failed", error=error, payload=payload)
            return {"status": "failed", "error": error}
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        db.upsert_schedule_inbox_sync(opportunity_id, user_id, "failed", error=error, payload=payload)
        return {"status": "failed", "error": error}

    inbox_item_id = str(data.get("id") or "")
    db.upsert_schedule_inbox_sync(
        opportunity_id,
        user_id,
        "synced",
        inbox_item_id=inbox_item_id,
        payload=payload,
    )
    return {"status": "synced", "inbox_item_id": inbox_item_id}


def schedule_inbox_status(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    settings = ScheduleInboxSettings.from_config(app_config)
    health: dict[str, Any] = {"ok": False}
    if settings.enabled:
        try:
            response = requests.get(f"{settings.api_base}/api/health", timeout=settings.timeout_seconds)
            health = {"ok": response.ok, "status_code": response.status_code}
            if not response.ok:
                health["error"] = response.text[:300]
        except Exception as exc:  # noqa: BLE001
            health = {"ok": False, "error": str(exc)}
    return {
        "enabled": settings.enabled,
        "api_base": settings.api_base,
        "selected_usernames": settings.usernames,
        "health": health,
        "recent": db.list_schedule_inbox_syncs(30),
    }


def retry_schedule_inbox_syncs(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    settings = ScheduleInboxSettings.from_config(app_config)
    user = selected_user(db, settings)
    if not settings.enabled or not user:
        return {"counts": {"candidates": 0, "synced": 0, "failed": 0, "skipped": 0}}
    candidates = []
    for item in db.list_user_opportunities(str(user["id"])):
        existing = db.get_schedule_inbox_sync(str(item["id"]), str(user["id"]))
        if item.get("user_status") != "pending_decision":
            continue
        if existing and existing.get("status") == "synced":
            continue
        candidates.append(item)

    counts = {"candidates": len(candidates), "synced": 0, "failed": 0, "skipped": 0, "already_synced": 0}
    results = []
    for opportunity in candidates[:100]:
        result = sync_opportunity_to_schedule_inbox(db, app_config, opportunity, user=user, force=True)
        status = str(result.get("status") or "failed")
        counts[status] = counts.get(status, 0) + 1
        results.append({"opportunity_id": opportunity.get("id"), **result})
    return {"counts": counts, "results": results}
