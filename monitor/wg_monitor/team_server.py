from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .calendar_sync import calendar_sync_status, retry_calendar_syncs
from .settings import load_project_cached
from .db import MonitorDB
from .extraction import extract_time_windows
from .opportunity import OpportunityAnalysis, schedule_status_for
from .opportunity_decision import apply_user_opportunity_decision
from .push import (
    opportunity_push_payload,
    send_push_to_user,
    web_push_enabled,
    web_push_public_key,
)
from .schedule_inbox import (
    ScheduleInboxSettings,
    retry_schedule_inbox_syncs,
    schedule_inbox_status,
    selected_user,
)
from .team import (
    DEFAULT_INVITE_CODE,
    clear_login_session,
    create_login_session,
    current_admin,
    current_user,
    ensure_team_bootstrap,
    parse_busy_text,
    password_hash,
    public_user,
    schedule_to_busy_text,
    verify_password,
)


def static_dir_for(root: Path) -> Path:
    return root / "web"


def configured_feeds(db: MonitorDB, app_config: dict[str, Any]) -> list[dict[str, Any]]:
    feeds = db.list_feed_configs(enabled_only=True)
    if feeds:
        return [{"name": item["name"], "url": item["url"]} for item in feeds]
    return list((app_config.get("monitor") or {}).get("feed_urls") or [])


def materialize_opportunity_for_users(db: MonitorDB, analysis: OpportunityAnalysis) -> int:
    time_windows = extract_time_windows(analysis.raw_text)
    count = 0
    for user in db.list_users():
        schedule_config = db.get_user_schedule(str(user["id"]))
        status, free_text, matched_text = schedule_status_for(schedule_config, time_windows)
        db.upsert_user_opportunity(
            {
                "id": f"{user['id']}:{analysis.id}",
                "user_id": user["id"],
                "opportunity_id": analysis.id,
                "schedule_status": status,
                "free_time_text": free_text,
                "matched_time_text": matched_text,
                "status": "pending_decision",
            }
        )
        count += 1
    return count


def materialize_existing_for_user(db: MonitorDB, user_id: str) -> int:
    count = 0
    schedule_config = db.get_user_schedule(user_id)
    for opportunity in db.list_opportunities():
        time_windows = extract_time_windows(str(opportunity.get("raw_text") or ""))
        status, free_text, matched_text = schedule_status_for(schedule_config, time_windows)
        db.upsert_user_opportunity(
            {
                "id": f"{user_id}:{opportunity['id']}",
                "user_id": user_id,
                "opportunity_id": opportunity["id"],
                "schedule_status": status,
                "free_time_text": free_text,
                "matched_time_text": matched_text,
                "status": "pending_decision",
            }
        )
        count += 1
    return count


def push_unpushed_opportunities(db: MonitorDB) -> dict[str, int]:
    counts = {"candidates": 0, "sent": 0, "skipped": 0, "failed": 0}
    if not web_push_enabled():
        return counts
    for item in db.list_unpushed_user_opportunities():
        counts["candidates"] += 1
        result = send_push_to_user(db, str(item["user_id"]), opportunity_push_payload(item))
        counts["sent"] += result.sent
        counts["skipped"] += result.skipped
        counts["failed"] += result.failed
        if result.sent or result.skipped:
            db.mark_user_opportunity_pushed(str(item["user_id"]), str(item["opportunity_id"]))
    return counts


def register_team_routes(app: FastAPI, root: Path, db: MonitorDB) -> None:
    ensure_team_bootstrap(db)
    static_dir = static_dir_for(root)
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def pwa_index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest() -> FileResponse:
            return FileResponse(static_dir / "manifest.webmanifest")

        @app.get("/service-worker.js", include_in_schema=False)
        async def service_worker() -> FileResponse:
            return FileResponse(static_dir / "service-worker.js")

    @app.post("/api/auth/register")
    async def register(request: Request, response: Response) -> dict[str, Any]:
        payload = await request.json()
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        display_name = str(payload.get("display_name") or username).strip()
        invite_code = str(payload.get("invite_code") or "").strip()
        if not username or not password or not display_name:
            raise HTTPException(status_code=400, detail="username, password, display_name required")
        if len(password) < 6:
            raise HTTPException(status_code=400, detail="password too short")
        if db.get_user_by_username(username):
            raise HTTPException(status_code=409, detail="username exists")
        if not db.use_invite_code(invite_code):
            raise HTTPException(status_code=400, detail="invalid invite code")
        user = db.create_user(username, password_hash(password), display_name, role="user", invite_code=invite_code)
        materialize_existing_for_user(db, str(user["id"]))
        create_login_session(db, response, str(user["id"]))
        return {"user": public_user(user)}

    @app.post("/api/auth/login")
    async def login(request: Request, response: Response) -> dict[str, Any]:
        payload = await request.json()
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        user = db.get_user_by_username(username)
        if not user or not verify_password(password, str(user.get("password_hash") or "")):
            raise HTTPException(status_code=401, detail="bad credentials")
        create_login_session(db, response, str(user["id"]))
        return {"user": public_user(user)}

    @app.post("/api/auth/logout")
    async def logout(response: Response, request: Request) -> dict[str, Any]:
        clear_login_session(db, response, request.cookies.get("campus_session", ""))
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        profile = db.get_user_profile(str(user["id"]))
        schedule = db.get_user_schedule(str(user["id"]))
        return {
            "user": public_user(user),
            "profile": profile,
            "schedule": schedule,
            "busy_text": schedule_to_busy_text(schedule),
            "push_enabled": web_push_enabled(),
            "default_invite_code": DEFAULT_INVITE_CODE if user.get("role") == "admin" else "",
        }

    @app.get("/api/profile")
    async def get_profile(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return db.get_user_profile(str(user["id"]))

    @app.put("/api/profile")
    async def save_profile(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        payload = await request.json()
        db.save_user_profile(str(user["id"]), payload)
        return {"profile": db.get_user_profile(str(user["id"]))}

    @app.get("/api/schedule")
    async def get_schedule(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        schedule = db.get_user_schedule(str(user["id"]))
        return {"schedule": schedule, "busy_text": schedule_to_busy_text(schedule)}

    @app.put("/api/schedule")
    async def save_schedule(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        payload = await request.json()
        try:
            schedule = parse_busy_text(
                str(payload.get("busy_text") or ""),
                str(payload.get("day_start") or "08:00"),
                str(payload.get("day_end") or "22:00"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.save_user_schedule(str(user["id"]), schedule)
        materialize_existing_for_user(db, str(user["id"]))
        return {"schedule": schedule, "busy_text": schedule_to_busy_text(schedule)}

    @app.get("/api/opportunities")
    async def list_opportunities(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"items": db.list_user_opportunities(str(user["id"]))}

    @app.get("/api/opportunities/{opportunity_id}")
    async def get_opportunity(opportunity_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        item = db.get_user_opportunity(str(user["id"]), opportunity_id)
        if not item:
            raise HTTPException(status_code=404, detail="not found")
        return {"item": item}

    @app.post("/api/opportunities/{opportunity_id}/decision")
    async def decide_opportunity(
        opportunity_id: str,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        payload = await request.json()
        decision = str(payload.get("decision") or "").strip()
        item = db.get_user_opportunity(str(user["id"]), opportunity_id)
        if not item:
            raise HTTPException(status_code=404, detail="not found")
        return apply_user_opportunity_decision(root, db, opportunity_id, user, decision)

    @app.post("/api/integrations/schedule/opportunities/{opportunity_id}/decision")
    async def schedule_integration_decision(
        opportunity_id: str,
        request: Request,
        x_integration_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root)
        settings = ScheduleInboxSettings.from_config(app_config)
        if x_integration_key != settings.integration_key:
            raise HTTPException(status_code=401, detail="invalid integration key")
        user = selected_user(db, settings)
        if not user:
            raise HTTPException(status_code=404, detail="selected user not found")
        payload = await request.json()
        decision = str(payload.get("decision") or "").strip()
        calendar_event_id = str(payload.get("calendar_event_id") or "").strip()
        result = apply_user_opportunity_decision(
            root,
            db,
            opportunity_id,
            user,
            decision,
            calendar_event_id=calendar_event_id,
        )
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result.get("message") or "not found")
        return result

    @app.get("/api/push/public-key")
    async def push_public_key(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return {"public_key": web_push_public_key(), "mode": os.getenv("WEB_PUSH_MODE", "real")}

    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        payload = await request.json()
        subscription = payload.get("subscription") if "subscription" in payload else payload
        if not isinstance(subscription, dict):
            raise HTTPException(status_code=400, detail="subscription required")
        sub_id = db.save_push_subscription(
            str(user["id"]),
            subscription,
            request.headers.get("user-agent", ""),
        )
        return {"ok": True, "subscription_id": sub_id}

    @app.post("/api/push/test")
    async def push_test(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        result = send_push_to_user(
            db,
            str(user["id"]),
            {"title": "校园机会雷达测试", "body": "手机通知链路已连接", "url": "/"},
        )
        return {"sent": result.sent, "skipped": result.skipped, "failed": result.failed}

    @app.get("/api/admin/feeds")
    async def admin_feeds(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        return {"items": db.list_feed_configs(), "fallback": db.list_feed_configs() == []}

    @app.post("/api/admin/feeds")
    async def admin_add_feed(request: Request, user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        payload = await request.json()
        name = str(payload.get("name") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not name or not url:
            raise HTTPException(status_code=400, detail="name and url required")
        return {"item": db.add_feed_config(name, url, bool(payload.get("enabled", True)))}

    @app.put("/api/admin/feeds/{feed_id}")
    async def admin_update_feed(
        feed_id: str,
        request: Request,
        user: dict[str, Any] = Depends(current_admin),
    ) -> dict[str, Any]:
        payload = await request.json()
        db.update_feed_config(
            feed_id,
            str(payload.get("name") or "").strip(),
            str(payload.get("url") or "").strip(),
            bool(payload.get("enabled", True)),
        )
        return {"ok": True}

    @app.delete("/api/admin/feeds/{feed_id}")
    async def admin_delete_feed(feed_id: str, user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        db.delete_feed_config(feed_id)
        return {"ok": True}

    @app.get("/api/admin/users")
    async def admin_users(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        return {"items": db.list_users(), "invites": db.list_invite_codes()}

    @app.get("/api/admin/logs")
    async def admin_logs(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        return {"items": db.recent_logs(80)}

    @app.get("/api/admin/calendar-sync/status")
    async def admin_calendar_sync_status(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root)
        return calendar_sync_status(db, app_config)

    @app.post("/api/admin/calendar-sync/retry")
    async def admin_calendar_sync_retry(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root)
        return retry_calendar_syncs(db, app_config)

    @app.get("/api/admin/schedule-inbox/status")
    async def admin_schedule_inbox_status(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root)
        return schedule_inbox_status(db, app_config)

    @app.post("/api/admin/schedule-inbox/retry")
    async def admin_schedule_inbox_retry(user: dict[str, Any] = Depends(current_admin)) -> dict[str, Any]:
        _paths, app_config, _schedule_config = load_project_cached(root)
        return retry_schedule_inbox_syncs(db, app_config)
