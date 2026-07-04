from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..database import (
    delete_event,
    delete_future_exceptions,
    fetch_all_events,
    fetch_exceptions_for_events,
    insert_event,
    update_event,
    upsert_exception,
)
from ..deps import (
    ensure_event,
    event_patch_update,
    now_iso,
    patch_to_storage,
    record_from_create,
    require_api_key,
)
from ..recurrence import (
    count_occurrences_before,
    event_from_row,
    expand_event,
    iso,
    normalize_rule,
    parse_date_window,
    parse_dt,
)
from ..schemas import EventCreate, EventPatch, OccurrenceDeleteRequest, OccurrenceModifyRequest

router = APIRouter()


def occurrence_key(value: datetime) -> str:
    return iso(value)


@router.get("/api/events")
def list_events(
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
) -> list[dict[str, Any]]:
    try:
        window_start, window_end = parse_date_window(from_date, to_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rows = fetch_all_events()
    exceptions = fetch_exceptions_for_events([row["id"] for row in rows])
    occurrences: list[dict[str, Any]] = []
    for row in rows:
        occurrences.extend(expand_event(row, exceptions.get(row["id"], []), window_start, window_end))
    return sorted(occurrences, key=lambda item: (item["start_at"], item["title"]))


@router.post("/api/events", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_api_key)])
def create_event(payload: EventCreate) -> dict[str, Any]:
    record = record_from_create(payload)
    insert_event(record)
    row = ensure_event(record["id"])
    return event_from_row(row)


@router.get("/api/events/{event_id}")
def get_event(event_id: str) -> dict[str, Any]:
    row = ensure_event(event_id)
    return event_from_row(row)


@router.patch("/api/events/{event_id}", dependencies=[Depends(require_api_key)])
def patch_event(event_id: str, payload: EventPatch) -> dict[str, Any]:
    row = ensure_event(event_id)
    current = event_from_row(row)
    update_data = event_patch_update(payload)
    merged_start = update_data.get("start_at", current["start_at"])
    merged_end = update_data.get("end_at", current["end_at"])
    if parse_dt(merged_end) <= parse_dt(merged_start):
        raise HTTPException(status_code=422, detail="end_at must be later than start_at")
    update_event(event_id, update_data)
    return event_from_row(ensure_event(event_id))


@router.delete("/api/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_api_key)])
def remove_event(event_id: str) -> Response:
    ensure_event(event_id)
    delete_event(event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/events/{event_id}/occurrences/modify", dependencies=[Depends(require_api_key)])
def modify_occurrence(event_id: str, payload: OccurrenceModifyRequest) -> dict[str, Any]:
    row = ensure_event(event_id)
    event = event_from_row(row)
    key = occurrence_key(payload.occurrence_start)

    if payload.scope == "all" or not event["recurrence"]:
        patch_event(event_id, payload.updates)
        return event_from_row(ensure_event(event_id))

    updates = patch_to_storage(payload.updates)
    if payload.scope == "this":
        if "start_at" in updates and "end_at" not in updates:
            base_duration = parse_dt(event["end_at"]) - parse_dt(event["start_at"])
            updates["end_at"] = iso(parse_dt(updates["start_at"]) + base_duration)
        if "end_at" in updates and "start_at" not in updates:
            base_start = parse_dt(key)
            updates["start_at"] = iso(base_start)
        if "start_at" in updates and "end_at" in updates:
            if parse_dt(updates["end_at"]) <= parse_dt(updates["start_at"]):
                raise HTTPException(status_code=422, detail="end_at must be later than start_at")
        timestamp = now_iso()
        upsert_exception(
            {
                "id": str(uuid4()),
                "event_id": event_id,
                "occurrence_start": key,
                "action": "modify",
                "overrides": json.dumps(updates, ensure_ascii=False),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return {"ok": True, "event_id": event_id, "occurrence_start": key, "scope": payload.scope}

    return split_future(event_id, event, parse_dt(key), updates)


@router.post("/api/events/{event_id}/occurrences/delete", dependencies=[Depends(require_api_key)])
def delete_occurrence(event_id: str, payload: OccurrenceDeleteRequest) -> dict[str, Any]:
    row = ensure_event(event_id)
    event = event_from_row(row)
    key = occurrence_key(payload.occurrence_start)

    if payload.scope == "all" or not event["recurrence"]:
        delete_event(event_id)
        return {"ok": True, "event_id": event_id, "scope": payload.scope}

    if payload.scope == "this":
        timestamp = now_iso()
        upsert_exception(
            {
                "id": str(uuid4()),
                "event_id": event_id,
                "occurrence_start": key,
                "action": "cancel",
                "overrides": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        return {"ok": True, "event_id": event_id, "occurrence_start": key, "scope": payload.scope}

    return shorten_future(event_id, event, parse_dt(key), delete_only=True)


def split_future(
    event_id: str,
    event: dict[str, Any],
    occurrence_start: datetime,
    updates: dict[str, Any],
) -> dict[str, Any]:
    result = shorten_future(event_id, event, occurrence_start, delete_only=False)
    new_event_id = result["new_event_id"]
    update_data = event_patch_update(EventPatch(**updates))
    if update_data:
        current = event_from_row(ensure_event(new_event_id))
        merged_start = update_data.get("start_at", current["start_at"])
        merged_end = update_data.get("end_at", current["end_at"])
        if parse_dt(merged_end) <= parse_dt(merged_start):
            raise HTTPException(status_code=422, detail="end_at must be later than start_at")
        update_event(new_event_id, update_data)
    return {"ok": True, "event_id": event_id, "new_event_id": new_event_id, "scope": "future"}


def shorten_future(
    event_id: str,
    event: dict[str, Any],
    occurrence_start: datetime,
    delete_only: bool,
) -> dict[str, Any]:
    start_at = parse_dt(event["start_at"])
    end_at = parse_dt(event["end_at"])
    duration = end_at - start_at

    if occurrence_start <= start_at:
        if delete_only:
            delete_event(event_id)
            return {"ok": True, "event_id": event_id, "scope": "future"}
        return {"ok": True, "event_id": event_id, "new_event_id": event_id, "scope": "future"}

    original_rule = dict(event["recurrence"])
    new_rule = dict(original_rule)

    if original_rule.get("count"):
        before_count = count_occurrences_before(event, occurrence_start)
        remaining_count = int(original_rule["count"]) - before_count
        if before_count <= 0:
            delete_event(event_id)
        else:
            original_rule["count"] = before_count
            original_rule.pop("until", None)
            update_event(
                event_id,
                {
                    "recurrence": json.dumps(normalize_rule(original_rule), ensure_ascii=False),
                    "updated_at": now_iso(),
                },
            )
        if remaining_count > 0:
            new_rule["count"] = remaining_count
    else:
        until_date = (occurrence_start - timedelta(days=1)).date().isoformat()
        original_rule["until"] = until_date
        update_event(
            event_id,
            {
                "recurrence": json.dumps(normalize_rule(original_rule), ensure_ascii=False),
                "updated_at": now_iso(),
            },
        )

    delete_future_exceptions(event_id, iso(occurrence_start))

    if delete_only:
        return {"ok": True, "event_id": event_id, "scope": "future"}

    timestamp = now_iso()
    new_record = {
        "id": str(uuid4()),
        "parent_event_id": event_id,
        "title": event["title"],
        "start_at": iso(occurrence_start),
        "end_at": iso(occurrence_start + duration),
        "all_day": 1 if event["all_day"] else 0,
        "category": event["category"],
        "location": event["location"],
        "notes": event["notes"],
        "source": event["source"],
        "reminder_minutes": event["reminder_minutes"],
        "recurrence": json.dumps(normalize_rule(new_rule), ensure_ascii=False),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    insert_event(new_record)
    return {"ok": True, "event_id": event_id, "new_event_id": new_record["id"], "scope": "future"}
