from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import inbox
from ..database import (
    fetch_event,
    fetch_inbox_item,
    fetch_inbox_item_by_key,
    inbox_item_counts,
    insert_event,
    list_inbox_items,
    update_inbox_item,
    upsert_inbox_item,
)
from ..deps import ensure_event, now_iso, record_from_create, require_api_key
from ..recurrence import event_from_row
from ..schemas import EventCreate

router = APIRouter()


@router.post("/api/inbox/items", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_key)])
def upsert_inbox(payload: inbox.InboxItemIn) -> dict[str, Any]:
    existing = fetch_inbox_item_by_key(payload.provider, payload.external_key)
    row = upsert_inbox_item(inbox.record_from_input(payload, existing=existing))
    return inbox.row_to_dict(row)


@router.get("/api/inbox/items")
def get_inbox_items(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return [inbox.row_to_dict(row) for row in list_inbox_items(status_filter, limit)]


@router.get("/api/inbox/status")
def get_inbox_status() -> dict[str, Any]:
    return {
        "counts": inbox_item_counts(),
        "recent": [inbox.row_to_dict(row) for row in list_inbox_items(limit=5)],
    }


@router.post("/api/inbox/items/{item_id}/decision", dependencies=[Depends(require_api_key)])
def decide_inbox_item(item_id: str, payload: inbox.InboxDecisionRequest) -> dict[str, Any]:
    row = fetch_inbox_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Inbox item not found")

    if payload.action == "add_calendar":
        existing_event_id = str(row["event_id"] or "")
        if existing_event_id and fetch_event(existing_event_id):
            return {
                "status": "calendar_added",
                "event": event_from_row(ensure_event(existing_event_id)),
                "item": inbox.row_to_dict(row),
            }
        event_payload = inbox.event_payload_from_row(row, payload.updates)
        if not event_payload.get("start_at") or not event_payload.get("end_at"):
            raise HTTPException(status_code=422, detail="待办缺少明确日期，请补全时间后再加入日程")
        record = record_from_create(EventCreate(**event_payload))
        insert_event(record)
        update_inbox_item(
            item_id,
            {
                "event_id": record["id"],
                "status": "calendar_added",
                "last_error": "",
                "updated_at": now_iso(),
            },
        )
        return {
            "status": "calendar_added",
            "event": event_from_row(ensure_event(record["id"])),
            "item": inbox.row_to_dict(fetch_inbox_item(item_id)),
        }

    event_id = str(row["event_id"] or "")
    if event_id and not fetch_event(event_id):
        event_id = ""
    callback = inbox.call_monitor_decision(row, payload.action, event_id=event_id)
    callback_status = str(callback.get("status") or "failed")
    error = str(callback.get("error") or callback.get("message") or "")

    if payload.action == "join":
        if callback_status in {"approved", "submitted", "joined"}:
            calendar_sync = callback.get("calendar_sync") if isinstance(callback.get("calendar_sync"), dict) else {}
            event_id = event_id or str(calendar_sync.get("event_id") or callback.get("event_id") or "")
            next_status = "joined"
            error = ""
        elif callback_status == "need_human":
            next_status = "needs_attention"
        else:
            next_status = "failed"
    elif payload.action == "later":
        next_status = "later"
    else:
        next_status = "ignored"

    update_inbox_item(
        item_id,
        {
            "event_id": event_id or row["event_id"],
            "status": next_status,
            "last_error": error[:1000] if callback_status == "failed" or next_status == "needs_attention" else "",
            "updated_at": now_iso(),
        },
    )
    return {
        "status": next_status,
        "callback": callback,
        "item": inbox.row_to_dict(fetch_inbox_item(item_id)),
    }
