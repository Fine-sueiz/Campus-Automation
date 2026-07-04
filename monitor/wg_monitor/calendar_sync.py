from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import requests

from .db import MonitorDB
from .timeparse import (
    CALENDAR_TIME_RANGE_RE as TIME_RANGE_RE,
    DATE_RE,
    SHANGHAI_TZ,
    adjust_hour,
    infer_date,
    parse_explicit_activity_window,
    parse_first_date,
    parse_time_range,
)


@dataclass(frozen=True)
class CalendarSyncSettings:
    enabled: bool
    api_base: str
    api_key: str
    usernames: list[str]
    reminder_minutes: int
    timeout_seconds: int

    @classmethod
    def from_config(cls, app_config: dict[str, Any]) -> "CalendarSyncSettings":
        config = app_config.get("calendar_sync") or {}
        usernames = config.get("usernames") or []
        if isinstance(usernames, str):
            usernames = [item.strip() for item in usernames.split(",") if item.strip()]
        env_usernames = os.getenv("CALENDAR_SYNC_USERNAMES", "").strip()
        if env_usernames:
            usernames = [item.strip() for item in env_usernames.split(",") if item.strip()]
        enabled_raw = os.getenv("CALENDAR_SYNC_ENABLED")
        enabled = bool(config.get("enabled", False))
        if enabled_raw is not None and enabled_raw != "":
            enabled = enabled_raw.strip().lower() in {"1", "true", "yes", "y", "on"}
        return cls(
            enabled=enabled,
            api_base=os.getenv("CALENDAR_SYNC_API_BASE", str(config.get("api_base") or "http://127.0.0.1:8000")).rstrip("/"),
            api_key=os.getenv("CALENDAR_SYNC_API_KEY", str(config.get("api_key") or "dev-schedule-key")),
            usernames=[str(item).strip() for item in usernames if str(item).strip()],
            reminder_minutes=int(os.getenv("CALENDAR_SYNC_REMINDER_MINUTES", str(config.get("reminder_minutes") or 60))),
            timeout_seconds=int(os.getenv("CALENDAR_SYNC_TIMEOUT_SECONDS", str(config.get("timeout_seconds") or 8))),
        )


def should_sync_for_user(
    db: MonitorDB,
    settings: CalendarSyncSettings,
    user: dict[str, Any] | None,
) -> bool:
    if not settings.enabled:
        return False
    if user is None:
        return True
    if settings.usernames:
        candidates = {
            str(user.get("id") or ""),
            str(user.get("username") or ""),
            str(user.get("display_name") or ""),
        }
        return bool(candidates.intersection(settings.usernames))

    ordinary_users = [item for item in db.list_users() if item.get("role") != "admin"]
    return len(ordinary_users) == 1 and str(user.get("id") or "") == str(ordinary_users[0]["id"])


def build_notes(opportunity: dict[str, Any], mode_label: str) -> str:
    lines = [
        f"同步类型：{mode_label}",
        f"来源：{opportunity.get('source_name') or '未知'}",
        f"活动时间：{opportunity.get('activity_time') or '未识别'}",
        f"报名截止：{opportunity.get('deadline') or '未识别'}",
        f"原文链接：{opportunity.get('article_url') or '无'}",
        f"报名链接：{opportunity.get('signup_url') or '无'}",
        f"监测机会ID：{opportunity.get('id') or ''}",
    ]
    return "\n".join(lines)[:3800]


def build_calendar_payload(
    opportunity: dict[str, Any],
    settings: CalendarSyncSettings,
) -> tuple[dict[str, Any] | None, str]:
    title = str(opportunity.get("title") or "志愿活动").strip()
    location = str(opportunity.get("location") or "").strip()
    activity_text = "\n".join(
        str(opportunity.get(key) or "")
        for key in ("activity_time", "raw_text")
        if opportunity.get(key)
    )
    window = parse_explicit_activity_window(activity_text)
    if window:
        start_at, end_at = window
        return (
            {
                "title": f"志愿｜{title}"[:120],
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "all_day": False,
                "category": "志愿活动",
                "location": location,
                "notes": build_notes(opportunity, "正式活动"),
                "source": "campus-monitor",
                "reminder_minutes": settings.reminder_minutes,
                "recurrence": None,
            },
            "activity",
        )

    deadline_text = "\n".join(
        str(opportunity.get(key) or "")
        for key in ("deadline", "raw_text")
        if opportunity.get(key)
    )
    deadline = parse_first_date(deadline_text)
    if deadline:
        start_at = datetime.combine(deadline, time.min, SHANGHAI_TZ)
        end_at = start_at + timedelta(days=1)
        return (
            {
                "title": f"报名截止｜{title}"[:120],
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "all_day": True,
                "category": "志愿活动",
                "location": location,
                "notes": build_notes(opportunity, "报名截止提醒"),
                "source": "campus-monitor",
                "reminder_minutes": settings.reminder_minutes,
                "recurrence": None,
            },
            "deadline",
        )

    return None, "no_usable_date"


def sync_opportunity_to_calendar(
    db: MonitorDB,
    app_config: dict[str, Any],
    opportunity: dict[str, Any],
    *,
    user: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    settings = CalendarSyncSettings.from_config(app_config)
    user_id = str(user.get("id") or "") if user else ""
    opportunity_id = str(opportunity.get("id") or "")
    if not opportunity_id:
        return {"status": "skipped", "reason": "missing_opportunity_id"}
    if not should_sync_for_user(db, settings, user):
        return {"status": "skipped", "reason": "user_not_selected"}

    existing = db.get_calendar_sync(opportunity_id, user_id)
    if existing and existing.get("status") == "synced" and not force:
        return {"status": "already_synced", "event_id": existing.get("calendar_event_id", "")}

    payload, mode = build_calendar_payload(opportunity, settings)
    if not payload:
        db.upsert_calendar_sync(opportunity_id, user_id, "skipped", error=mode)
        db.add_log("info", "calendar sync skipped", {"opportunity_id": opportunity_id, "reason": mode, "user_id": user_id})
        return {"status": "skipped", "reason": mode}

    try:
        response = requests.post(
            f"{settings.api_base}/api/events",
            headers={"X-API-Key": settings.api_key},
            json=payload,
            timeout=settings.timeout_seconds,
        )
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}: {response.text[:300]}"
            db.upsert_calendar_sync(opportunity_id, user_id, "failed", error=error, payload=payload)
            db.add_log("error", "calendar sync failed", {"opportunity_id": opportunity_id, "error": error, "user_id": user_id})
            return {"status": "failed", "error": error}
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        db.upsert_calendar_sync(opportunity_id, user_id, "failed", error=error, payload=payload)
        db.add_log("error", "calendar sync failed", {"opportunity_id": opportunity_id, "error": error, "user_id": user_id})
        return {"status": "failed", "error": error}

    event_id = str(data.get("id") or "")
    db.upsert_calendar_sync(opportunity_id, user_id, "synced", calendar_event_id=event_id, payload=payload)
    db.add_log("info", "calendar sync completed", {"opportunity_id": opportunity_id, "event_id": event_id, "mode": mode, "user_id": user_id})
    return {"status": "synced", "event_id": event_id, "mode": mode}


def calendar_sync_status(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    settings = CalendarSyncSettings.from_config(app_config)
    health: dict[str, Any] = {"ok": False}
    if settings.enabled:
        try:
            response = requests.get(f"{settings.api_base}/api/health", timeout=settings.timeout_seconds)
            health = {"ok": response.ok, "status_code": response.status_code}
            if response.ok:
                health["body"] = response.json()
            else:
                health["error"] = response.text[:300]
        except Exception as exc:  # noqa: BLE001
            health = {"ok": False, "error": str(exc)}
    return {
        "enabled": settings.enabled,
        "api_base": settings.api_base,
        "selected_usernames": settings.usernames,
        "health": health,
        "recent": db.list_calendar_syncs(30),
    }


def retry_calendar_syncs(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    seen: set[tuple[str, str]] = set()
    user_scoped_opportunities: set[str] = set()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT opportunities.*, calendar_syncs.user_id
            FROM calendar_syncs
            JOIN opportunities ON opportunities.id = calendar_syncs.opportunity_id
            WHERE calendar_syncs.status IN ('failed', 'skipped')
              AND NOT (
                  calendar_syncs.user_id = ''
                  AND EXISTS (
                      SELECT 1 FROM user_opportunities
                      WHERE user_opportunities.opportunity_id = calendar_syncs.opportunity_id
                        AND user_opportunities.status IN ('approved', 'submitted')
                  )
              )
            ORDER BY calendar_syncs.updated_at ASC
            LIMIT 100
            """
        ).fetchall()
        for row in rows:
            opportunity = dict(row)
            user = db.get_user(str(row["user_id"])) if row["user_id"] else None
            key = (str(opportunity["id"]), str(row["user_id"] or ""))
            seen.add(key)
            if row["user_id"]:
                user_scoped_opportunities.add(str(opportunity["id"]))
            candidates.append((opportunity, user))

        user_rows = conn.execute(
            """
            SELECT opportunities.*, user_opportunities.user_id
            FROM user_opportunities
            JOIN opportunities ON opportunities.id = user_opportunities.opportunity_id
            WHERE user_opportunities.status IN ('approved', 'submitted')
              AND NOT EXISTS (
                  SELECT 1 FROM calendar_syncs
                  WHERE calendar_syncs.opportunity_id = user_opportunities.opportunity_id
                    AND calendar_syncs.user_id = user_opportunities.user_id
                    AND calendar_syncs.status = 'synced'
              )
            LIMIT 100
            """
        ).fetchall()
        for row in user_rows:
            key = (str(row["id"]), str(row["user_id"]))
            if key in seen:
                continue
            seen.add(key)
            user_scoped_opportunities.add(str(row["id"]))
            candidates.append((dict(row), db.get_user(str(row["user_id"]))))

        single_rows = conn.execute(
            """
            SELECT opportunities.*
            FROM opportunities
            WHERE opportunities.status IN ('approved', 'submitted')
              AND NOT EXISTS (
                  SELECT 1 FROM calendar_syncs
                  WHERE calendar_syncs.opportunity_id = opportunities.id
                    AND calendar_syncs.user_id = ''
                    AND calendar_syncs.status = 'synced'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM user_opportunities
                  WHERE user_opportunities.opportunity_id = opportunities.id
                    AND user_opportunities.status IN ('approved', 'submitted')
              )
            LIMIT 100
            """
        ).fetchall()
        for row in single_rows:
            key = (str(row["id"]), "")
            if key in seen or str(row["id"]) in user_scoped_opportunities:
                continue
            seen.add(key)
            candidates.append((dict(row), None))

    counts = {"candidates": len(candidates), "synced": 0, "failed": 0, "skipped": 0, "already_synced": 0}
    results = []
    for opportunity, user in candidates:
        result = sync_opportunity_to_calendar(db, app_config, opportunity, user=user, force=True)
        status = str(result.get("status") or "failed")
        counts[status] = counts.get(status, 0) + 1
        results.append({"opportunity_id": opportunity.get("id"), "user_id": (user or {}).get("id", ""), **result})
    return {"counts": counts, "results": results}
