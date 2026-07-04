from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .db import MonitorDB


@dataclass(frozen=True)
class PushResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0


def web_push_public_key() -> str:
    return os.getenv("WEB_PUSH_PUBLIC_KEY", "").strip()


def web_push_enabled() -> bool:
    return bool(web_push_public_key()) or os.getenv("WEB_PUSH_MODE", "").strip().lower() == "fake"


def send_push_to_user(db: MonitorDB, user_id: str, payload: dict[str, Any]) -> PushResult:
    mode = os.getenv("WEB_PUSH_MODE", "real").strip().lower()
    subscriptions = db.list_push_subscriptions(user_id)
    if not subscriptions:
        return PushResult(skipped=1)

    sent = skipped = failed = 0
    if mode == "fake":
        for sub in subscriptions:
            db.update_push_result(str(sub["id"]), True)
            sent += 1
        db.add_log("info", "fake web push sent", {"user_id": user_id, "payload": payload})
        return PushResult(sent=sent)

    public_key = web_push_public_key()
    private_key = os.getenv("WEB_PUSH_PRIVATE_KEY", "").strip()
    subject = os.getenv("WEB_PUSH_SUBJECT", "mailto:admin@example.com").strip()
    if not public_key or not private_key:
        return PushResult(skipped=len(subscriptions))

    try:
        from pywebpush import WebPushException, webpush
    except ModuleNotFoundError:
        db.add_log("warning", "pywebpush not installed; push skipped", {"user_id": user_id})
        return PushResult(skipped=len(subscriptions))

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub["subscription"],
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
            )
        except WebPushException as exc:
            failed += 1
            db.update_push_result(str(sub["id"]), False, str(exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            db.update_push_result(str(sub["id"]), False, str(exc))
        else:
            sent += 1
            db.update_push_result(str(sub["id"]), True)
    return PushResult(sent=sent, failed=failed)


def opportunity_push_payload(opportunity: dict[str, Any]) -> dict[str, Any]:
    status_label = {
        "available": "有空",
        "conflict": "课表冲突",
        "unknown_time": "时间不确定",
    }.get(str(opportunity.get("user_schedule_status") or opportunity.get("schedule_status")), "待判断")
    return {
        "title": f"发现校园机会：{opportunity.get('title', '新机会')}",
        "body": (
            f"{status_label}｜{opportunity.get('activity_time') or '时间待确认'}"
            f"｜{opportunity.get('location') or '地点待确认'}"
        ),
        "url": f"/#/opportunity/{opportunity.get('id')}",
        "opportunity_id": opportunity.get("id"),
    }
