from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, model_validator

from .settings import SHANGHAI_TZ, get_monitor_api_base, get_monitor_integration_key


TERMINAL_STATUSES = {"calendar_added", "joined", "later", "ignored"}
InboxAction = Literal["add_calendar", "join", "later", "ignore"]


class InboxItemIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    external_key: str = Field(min_length=1, max_length=240)
    source_item_id: str = Field(default="", max_length=240)
    source_api_base: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=4000)
    category: str = Field(default="其他", max_length=64)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool = False
    location: str = Field(default="", max_length=200)
    source_name: str = Field(default="", max_length=160)
    source_url: str = Field(default="", max_length=1000)
    action_url: str = Field(default="", max_length=1000)
    event_payload: dict[str, Any] | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_window(self) -> "InboxItemIn":
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class InboxDecisionRequest(BaseModel):
    action: InboxAction
    updates: dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def record_from_input(payload: InboxItemIn, existing=None) -> dict[str, Any]:
    timestamp = now_iso()
    raw_payload = dict(payload.raw_payload)
    if payload.event_payload:
        raw_payload["event_payload"] = payload.event_payload
    preserved_status = existing["status"] if existing and existing["status"] in TERMINAL_STATUSES else "pending"
    return {
        "id": existing["id"] if existing else str(uuid4()),
        "provider": payload.provider,
        "external_key": payload.external_key,
        "source_item_id": payload.source_item_id,
        "source_api_base": payload.source_api_base,
        "title": payload.title,
        "summary": payload.summary,
        "category": payload.category,
        "start_at": payload.start_at.isoformat() if payload.start_at else None,
        "end_at": payload.end_at.isoformat() if payload.end_at else None,
        "all_day": 1 if payload.all_day else 0,
        "location": payload.location,
        "source_name": payload.source_name,
        "source_url": payload.source_url,
        "action_url": payload.action_url,
        "status": preserved_status,
        "event_id": existing["event_id"] if existing else None,
        "raw_payload": json.dumps(raw_payload, ensure_ascii=False),
        "last_error": "" if not existing or preserved_status == "pending" else existing["last_error"],
        "created_at": existing["created_at"] if existing else timestamp,
        "updated_at": timestamp,
    }


def row_to_dict(row) -> dict[str, Any]:
    try:
        raw_payload = json.loads(row["raw_payload"] or "{}")
    except json.JSONDecodeError:
        raw_payload = {}
    return {
        "id": row["id"],
        "provider": row["provider"],
        "external_key": row["external_key"],
        "source_item_id": row["source_item_id"],
        "source_api_base": row["source_api_base"],
        "title": row["title"],
        "summary": row["summary"],
        "category": row["category"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "all_day": bool(row["all_day"]),
        "location": row["location"],
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "action_url": row["action_url"],
        "status": row["status"],
        "event_id": row["event_id"],
        "raw_payload": raw_payload,
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def event_payload_from_row(row, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    public = row_to_dict(row)
    raw_event = public["raw_payload"].get("event_payload")
    payload = dict(raw_event) if isinstance(raw_event, dict) else {}
    payload.setdefault("title", public["title"][:120])
    payload.setdefault("start_at", public["start_at"])
    payload.setdefault("end_at", public["end_at"])
    payload.setdefault("all_day", public["all_day"])
    payload.setdefault("category", public["category"])
    payload.setdefault("location", public["location"])
    payload.setdefault("notes", public["summary"][:3800])
    payload.setdefault("source", public["provider"])
    payload.setdefault("reminder_minutes", 60)
    payload.setdefault("recurrence", None)
    for key, value in (overrides or {}).items():
        if key in payload and value is not None:
            payload[key] = value
    return payload


def call_monitor_decision(row, action: InboxAction, event_id: str = "") -> dict[str, Any]:
    decision_map = {"join": "join", "later": "later", "ignore": "reject"}
    decision = decision_map.get(action)
    if not decision:
        return {"status": "skipped", "reason": "no_monitor_callback"}
    source_item_id = str(row["source_item_id"] or row["external_key"])
    api_base = str(row["source_api_base"] or get_monitor_api_base()).rstrip("/")
    try:
        response = httpx.post(
            f"{api_base}/api/integrations/schedule/opportunities/{source_item_id}/decision",
            headers={"X-Integration-Key": get_monitor_integration_key()},
            json={"decision": decision, "calendar_event_id": event_id},
            timeout=10,
        )
        if response.status_code >= 400:
            return {"status": "failed", "error": f"HTTP {response.status_code}: {response.text[:300]}"}
        result = response.json()
        return result if isinstance(result, dict) else {"status": "failed", "error": "invalid response"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)}
