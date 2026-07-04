from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import Header, HTTPException, status

from . import xuexitong
from .database import fetch_event, fetch_integration_sync, upsert_integration_sync
from .recurrence import iso, normalize_rule, parse_dt
from .schemas import EventCreate, EventPatch
from .settings import SHANGHAI_TZ, get_api_key


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != get_api_key():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def patch_to_storage(patch: EventPatch) -> dict[str, Any]:
    raw = patch.model_dump(exclude_unset=True, mode="json")
    return normalize_payload_fields(raw)


def normalize_payload_fields(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    if "start_at" in data and data["start_at"] is not None:
        data["start_at"] = iso(data["start_at"])
    if "end_at" in data and data["end_at"] is not None:
        data["end_at"] = iso(data["end_at"])
    if "recurrence" in data:
        data["recurrence"] = normalize_rule(data["recurrence"])
    return data


def serialize_for_storage(data: dict[str, Any]) -> dict[str, Any]:
    storage = normalize_payload_fields(data)
    if storage.get("end_at") and storage.get("start_at"):
        if parse_dt(storage["end_at"]) <= parse_dt(storage["start_at"]):
            raise HTTPException(status_code=422, detail="end_at must be later than start_at")
    storage["all_day"] = 1 if storage.get("all_day") else 0
    storage["recurrence"] = (
        json.dumps(storage["recurrence"], ensure_ascii=False) if storage.get("recurrence") else None
    )
    return storage


def record_from_create(payload: EventCreate, parent_event_id: str | None = None) -> dict[str, Any]:
    timestamp = now_iso()
    data = payload.model_dump(mode="json")
    storage = serialize_for_storage(data)
    return {
        "id": str(uuid4()),
        "parent_event_id": parent_event_id,
        **storage,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def event_patch_update(patch: EventPatch) -> dict[str, Any]:
    data = patch_to_storage(patch)
    if "all_day" in data:
        data["all_day"] = 1 if data["all_day"] else 0
    if "recurrence" in data:
        data["recurrence"] = json.dumps(data["recurrence"], ensure_ascii=False) if data["recurrence"] else None
    if "start_at" in data or "end_at" in data:
        start_value = data.get("start_at")
        end_value = data.get("end_at")
        if start_value and end_value and parse_dt(end_value) <= parse_dt(start_value):
            raise HTTPException(status_code=422, detail="end_at must be later than start_at")
    if data:
        data["updated_at"] = now_iso()
    return data


def ensure_event(event_id: str):
    row = fetch_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return row


def sync_row_to_dict(row) -> dict[str, Any]:
    return {
        "provider": row["provider"],
        "external_key": row["external_key"],
        "event_id": row["event_id"],
        "content_hash": row["content_hash"],
        "title": row["title"],
        "source_url": row["source_url"],
        "status": row["status"],
        "last_error": row["last_error"],
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def record_integration_sync(
    *,
    provider: str = xuexitong.PROVIDER,
    external_key: str,
    event_id: str | None,
    content_hash: str,
    title: str,
    source_url: str,
    status_value: str,
    payload: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    timestamp = now_iso()
    existing = fetch_integration_sync(provider, external_key)
    upsert_integration_sync(
        {
            "id": existing["id"] if existing else str(uuid4()),
            "provider": provider,
            "external_key": external_key,
            "event_id": event_id,
            "content_hash": content_hash,
            "title": title[:160],
            "source_url": source_url[:500],
            "status": status_value,
            "last_error": error[:1000],
            "last_payload": json.dumps(payload or {}, ensure_ascii=False),
            "last_seen_at": timestamp,
            "created_at": existing["created_at"] if existing else timestamp,
            "updated_at": timestamp,
        }
    )
