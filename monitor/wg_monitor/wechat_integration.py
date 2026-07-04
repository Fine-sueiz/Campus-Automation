from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import get_bool_env
from .db import MonitorDB
from .feed import stable_id
from .schedule_inbox import sync_opportunity_to_schedule_inbox
from .team_server import materialize_opportunity_for_users, push_unpushed_opportunities
from .volunteer import (
    analyse_volunteer_opportunity,
    maybe_send_volunteer_reminder,
    volunteer_confirm_by_email,
    volunteer_enabled,
    volunteer_source_allowed,
)


WATCHER_STATUS_BINDING = "wechat_watcher_status"


@dataclass(frozen=True)
class WechatWatcherSettings:
    enabled: bool
    api_base: str
    integration_key: str
    poll_seconds: int
    process_name: str
    window_title: str
    baseline_on_first_run: bool
    max_seen_items: int

    @classmethod
    def from_config(cls, app_config: dict[str, Any]) -> "WechatWatcherSettings":
        config = app_config.get("wechat_watcher") or {}
        schedule_inbox = app_config.get("schedule_inbox") or {}
        return cls(
            enabled=get_bool_env("WECHAT_WATCHER_ENABLED", bool(config.get("enabled", False))),
            api_base=os.getenv(
                "WECHAT_WATCHER_API_BASE",
                str(config.get("api_base") or "http://127.0.0.1:8011"),
            ).rstrip("/"),
            integration_key=os.getenv(
                "WECHAT_WATCHER_INTEGRATION_KEY",
                str(config.get("integration_key") or schedule_inbox.get("integration_key") or "dev-schedule-key"),
            ),
            poll_seconds=max(5, int(os.getenv("WECHAT_WATCHER_POLL_SECONDS", config.get("poll_seconds") or 30))),
            process_name=str(config.get("process_name") or "Weixin.exe"),
            window_title=str(config.get("window_title") or "微信"),
            baseline_on_first_run=bool(config.get("baseline_on_first_run", True)),
            max_seen_items=max(100, int(config.get("max_seen_items") or 1000)),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_external_key(item: dict[str, Any]) -> str:
    supplied = str(item.get("external_key") or "").strip()
    if supplied:
        return supplied[:160]
    source = "".join(str(item.get("source_name") or "").split()).casefold()
    title = " ".join(str(item.get("title") or "").split()).casefold()
    article_url = str(item.get("article_url") or "").strip()
    return stable_id("wechat-visible", source, title, article_url)


def record_watcher_status(db: MonitorDB, payload: dict[str, Any]) -> None:
    status = {
        "status": str(payload.get("status") or "running"),
        "message": str(payload.get("message") or ""),
        "visible_items": int(payload.get("visible_items") or 0),
        "page_detected": bool(payload.get("page_detected", False)),
        "pid": int(payload.get("pid") or 0),
        "updated_at": utc_now(),
    }
    db.set_binding(WATCHER_STATUS_BINDING, json.dumps(status, ensure_ascii=False))


def process_wechat_items(
    db: MonitorDB,
    app_config: dict[str, Any],
    schedule_config: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    force: bool = False,
) -> dict[str, Any]:
    counts = {
        "received": len(items),
        "created": 0,
        "duplicates": 0,
        "not_allowed": 0,
        "not_target": 0,
        "failed": 0,
        "inbox_sent": 0,
        "inbox_failed": 0,
    }
    results: list[dict[str, Any]] = []

    for item in items[:100]:
        source_name = str(item.get("source_name") or "").strip()
        title = " ".join(str(item.get("title") or "").split()).strip()
        published_text = str(item.get("published_text") or "").strip()
        article_url = str(item.get("article_url") or "").strip()
        raw_text = str(item.get("raw_text") or item.get("summary") or title).strip()
        external_key = item_external_key(item)

        if not source_name or not title:
            counts["failed"] += 1
            results.append({"external_key": external_key, "status": "failed", "error": "source_name and title required"})
            continue

        existing = db.get_wechat_capture(external_key)
        if existing and not force:
            counts["duplicates"] += 1
            results.append({"external_key": external_key, "status": "duplicate"})
            continue

        if not volunteer_enabled(app_config):
            db.upsert_wechat_capture(
                external_key, source_name, title, published_text, article_url, raw_text, "disabled"
            )
            counts["not_target"] += 1
            results.append({"external_key": external_key, "status": "disabled"})
            continue

        if not volunteer_source_allowed(app_config, source_name):
            db.upsert_wechat_capture(
                external_key, source_name, title, published_text, article_url, raw_text, "not_allowed"
            )
            counts["not_allowed"] += 1
            results.append({"external_key": external_key, "status": "not_allowed"})
            continue

        article_item_id = stable_id("wechat-watcher", external_key)
        db.insert_article(article_item_id, source_name, title, article_url, published_text)
        analysis = analyse_volunteer_opportunity(
            title,
            raw_text,
            article_url,
            source_name,
            article_item_id,
            app_config,
            schedule_config,
        )
        if not analysis.is_target:
            db.upsert_wechat_capture(
                external_key, source_name, title, published_text, article_url, raw_text, "not_target"
            )
            counts["not_target"] += 1
            results.append({"external_key": external_key, "status": "not_target"})
            continue

        try:
            db.upsert_opportunity(analysis.to_db_payload(status="pending_confirmation"))
            materialize_opportunity_for_users(db, analysis)
            inbox_result = sync_opportunity_to_schedule_inbox(
                db,
                app_config,
                db.get_opportunity(analysis.id) or analysis.to_db_payload(status="pending_confirmation"),
            )
            inbox_status = str(inbox_result.get("status") or "failed")
            if inbox_status == "synced":
                counts["inbox_sent"] += 1
            elif inbox_status == "failed":
                counts["inbox_failed"] += 1
            reminder_status = "disabled"
            if volunteer_confirm_by_email(app_config):
                reminder_status = maybe_send_volunteer_reminder(db, app_config, analysis)
            db.upsert_wechat_capture(
                external_key,
                source_name,
                title,
                published_text,
                article_url,
                raw_text,
                "created",
                opportunity_id=analysis.id,
            )
            counts["created"] += 1
            results.append(
                {
                    "external_key": external_key,
                    "status": "created",
                    "opportunity_id": analysis.id,
                    "inbox_status": inbox_status,
                    "reminder_status": reminder_status,
                }
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            db.upsert_wechat_capture(
                external_key,
                source_name,
                title,
                published_text,
                article_url,
                raw_text,
                "failed",
                opportunity_id=analysis.id,
                error=error,
            )
            db.add_log("error", f"wechat article processing failed: {error}", {"external_key": external_key})
            counts["failed"] += 1
            results.append({"external_key": external_key, "status": "failed", "error": error})

    push_counts = push_unpushed_opportunities(db)
    return {"counts": counts, "push": push_counts, "items": results}


def wechat_watcher_status(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    settings = WechatWatcherSettings.from_config(app_config)
    raw_status = db.get_binding(WATCHER_STATUS_BINDING)
    try:
        watcher = json.loads(raw_status) if raw_status else {"status": "never_started"}
    except json.JSONDecodeError:
        watcher = {"status": "unknown", "message": raw_status}
    return {
        "enabled": settings.enabled,
        "api_base": settings.api_base,
        "poll_seconds": settings.poll_seconds,
        "watcher": watcher,
        "recent": db.list_wechat_captures(30),
    }
