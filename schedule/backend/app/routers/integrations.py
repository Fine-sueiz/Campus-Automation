from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import qq_sync, xuexitong
from ..database import (
    fetch_event,
    fetch_integration_sync,
    fetch_qq_candidate,
    fetch_qq_candidate_by_external_key,
    fetch_qq_message,
    insert_event,
    list_integration_syncs,
    list_qq_candidates,
    qq_candidate_counts,
    update_event,
    update_qq_candidate,
    upsert_qq_candidate,
    upsert_qq_message,
)
from ..deps import (
    ensure_event,
    event_patch_update,
    now_iso,
    record_from_create,
    record_integration_sync,
    require_api_key,
    sync_row_to_dict,
)
from ..recurrence import event_from_row
from ..schemas import EventCreate, EventPatch

router = APIRouter()


def sync_xuexitong_item(item: xuexitong.XuexitongItem) -> dict[str, Any]:
    payload = xuexitong.build_event_payload(item)
    content_hash = xuexitong.payload_hash(payload)
    existing = fetch_integration_sync(xuexitong.PROVIDER, item.external_key)
    event_id = existing["event_id"] if existing else None
    title = payload["title"]

    try:
        if existing and event_id and fetch_event(event_id):
            if existing["content_hash"] == content_hash:
                record_integration_sync(
                    external_key=item.external_key,
                    event_id=event_id,
                    content_hash=content_hash,
                    title=title,
                    source_url=item.source_url,
                    status_value="skipped",
                    payload=payload,
                )
                return {
                    "status": "skipped",
                    "event_id": event_id,
                    "title": title,
                    "external_key": item.external_key,
                }

            update_data = event_patch_update(EventPatch(**payload))
            update_event(event_id, update_data)
            record_integration_sync(
                external_key=item.external_key,
                event_id=event_id,
                content_hash=content_hash,
                title=title,
                source_url=item.source_url,
                status_value="updated",
                payload=payload,
            )
            return {
                "status": "updated",
                "event_id": event_id,
                "title": title,
                "external_key": item.external_key,
            }

        record = record_from_create(EventCreate(**payload))
        insert_event(record)
        record_integration_sync(
            external_key=item.external_key,
            event_id=record["id"],
            content_hash=content_hash,
            title=title,
            source_url=item.source_url,
            status_value="created",
            payload=payload,
        )
        return {
            "status": "created",
            "event_id": record["id"],
            "title": title,
            "external_key": item.external_key,
        }
    except Exception as exc:  # noqa: BLE001
        record_integration_sync(
            external_key=item.external_key,
            event_id=event_id,
            content_hash=content_hash,
            title=title,
            source_url=item.source_url,
            status_value="failed",
            payload=payload,
            error=str(exc),
        )
        return {
            "status": "failed",
            "event_id": event_id,
            "title": title,
            "external_key": item.external_key,
            "error": str(exc),
        }


def sync_xuexitong_items(items: list[xuexitong.XuexitongItem]) -> dict[str, Any]:
    result = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
    }
    for item in items:
        item_result = sync_xuexitong_item(item)
        status_value = item_result["status"]
        if status_value in result:
            result[status_value] += 1
        result["items"].append(item_result)
    return result


def should_auto_create_qq_candidate(parsed: qq_sync.ParsedSchedule, config: dict[str, Any]) -> bool:
    threshold = float(
        config.get("auto_create_min_confidence", qq_sync.DEFAULT_AUTO_CREATE_MIN_CONFIDENCE)
    )
    return (
        parsed.has_schedule
        and parsed.start_at is not None
        and parsed.end_at is not None
        and parsed.confidence >= threshold
        and not parsed.missing_fields
    )


def create_qq_event_from_candidate(candidate_row, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = qq_sync.candidate_event_payload(candidate_row, overrides=overrides)
    if not payload.get("start_at") or not payload.get("end_at"):
        raise HTTPException(status_code=422, detail="候选事项缺少明确时间，不能写入日程")
    record = record_from_create(EventCreate(**payload))
    insert_event(record)
    timestamp = now_iso()
    update_qq_candidate(
        candidate_row["id"],
        {
            "event_id": record["id"],
            "status": "created",
            "last_error": "",
            "updated_at": timestamp,
        },
    )
    record_integration_sync(
        provider=qq_sync.PROVIDER,
        external_key=candidate_row["external_key"],
        event_id=record["id"],
        content_hash=candidate_row["content_hash"],
        title=payload["title"],
        source_url="",
        status_value="created",
        payload=payload,
    )
    return event_from_row(ensure_event(record["id"]))


def ingest_qq_message(payload: qq_sync.QQMessageIn) -> dict[str, Any]:
    config = qq_sync.load_config()
    external_key = qq_sync.message_external_key(payload)
    message_hash = qq_sync.payload_hash(payload.model_dump(mode="json"))
    existing_sync = fetch_integration_sync(qq_sync.PROVIDER, external_key)
    existing_message = fetch_qq_message(external_key)

    if existing_sync and existing_sync["status"] != "failed":
        event_id = existing_sync["event_id"]
        if existing_sync["status"] == "created" and event_id and not fetch_event(event_id):
            record_integration_sync(
                provider=qq_sync.PROVIDER,
                external_key=external_key,
                event_id=event_id,
                content_hash=message_hash,
                title=existing_sync["title"],
                source_url="",
                status_value="skipped_deleted",
                payload=payload.model_dump(mode="json"),
            )
            return {"status": "skipped_deleted", "external_key": external_key, "event_id": event_id}
        return {
            "status": "skipped",
            "external_key": external_key,
            "event_id": event_id,
            "reason": existing_sync["status"],
        }

    group = qq_sync.find_group_config(payload, config)
    ignored_reason = ""
    if not config.get("enabled", True):
        ignored_reason = "disabled"
    elif group is None:
        ignored_reason = "group_not_configured"
    elif not qq_sync.sender_allowed(payload, group):
        ignored_reason = "sender_not_allowed"

    if ignored_reason:
        message_row = upsert_qq_message(
            qq_sync.build_message_record(
                payload,
                external_key=external_key,
                status_value=f"ignored_{ignored_reason}",
                existing_id=existing_message["id"] if existing_message else None,
                created_at=existing_message["created_at"] if existing_message else None,
            )
        )
        record_integration_sync(
            provider=qq_sync.PROVIDER,
            external_key=external_key,
            event_id=None,
            content_hash=message_hash,
            title=payload.text[:120],
            source_url="",
            status_value=f"ignored_{ignored_reason}",
            payload=payload.model_dump(mode="json"),
        )
        return {
            "status": f"ignored_{ignored_reason}",
            "external_key": external_key,
            "message_id": message_row["id"],
        }

    parsed = qq_sync.parse_schedule(payload, group)
    if not parsed.has_schedule:
        message_row = upsert_qq_message(
            qq_sync.build_message_record(
                payload,
                external_key=external_key,
                status_value="ignored_no_schedule",
                existing_id=existing_message["id"] if existing_message else None,
                created_at=existing_message["created_at"] if existing_message else None,
            )
        )
        record_integration_sync(
            provider=qq_sync.PROVIDER,
            external_key=external_key,
            event_id=None,
            content_hash=message_hash,
            title=payload.text[:120],
            source_url="",
            status_value="ignored_no_schedule",
            payload=payload.model_dump(mode="json"),
        )
        return {
            "status": "ignored_no_schedule",
            "external_key": external_key,
            "message_id": message_row["id"],
        }

    auto_create = should_auto_create_qq_candidate(parsed, config)
    candidate_status = "pending"
    if auto_create:
        candidate_status = "ready"

    message_row = upsert_qq_message(
        qq_sync.build_message_record(
            payload,
            external_key=external_key,
            status_value=candidate_status,
            existing_id=existing_message["id"] if existing_message else None,
            created_at=existing_message["created_at"] if existing_message else None,
        )
    )
    existing_candidate = fetch_qq_candidate_by_external_key(external_key)
    candidate_row = upsert_qq_candidate(
        qq_sync.build_candidate_record(
            payload,
            message_id=message_row["id"],
            external_key=external_key,
            parsed=parsed,
            group=group,
            existing_id=existing_candidate["id"] if existing_candidate else None,
            created_at=existing_candidate["created_at"] if existing_candidate else None,
            status_value=candidate_status,
            event_id=existing_candidate["event_id"] if existing_candidate else None,
        )
    )

    if auto_create:
        try:
            event = create_qq_event_from_candidate(candidate_row)
            return {
                "status": "created",
                "external_key": external_key,
                "candidate": qq_sync.candidate_row_to_dict(fetch_qq_candidate(candidate_row["id"])),
                "event": event,
            }
        except Exception as exc:  # noqa: BLE001
            timestamp = now_iso()
            update_qq_candidate(
                candidate_row["id"],
                {"status": "failed", "last_error": str(exc), "updated_at": timestamp},
            )
            record_integration_sync(
                provider=qq_sync.PROVIDER,
                external_key=external_key,
                event_id=None,
                content_hash=candidate_row["content_hash"],
                title=candidate_row["title"],
                source_url="",
                status_value="failed",
                payload=payload.model_dump(mode="json"),
                error=str(exc),
            )
            return {
                "status": "failed",
                "external_key": external_key,
                "candidate": qq_sync.candidate_row_to_dict(fetch_qq_candidate(candidate_row["id"])),
                "error": str(exc),
            }

    record_integration_sync(
        provider=qq_sync.PROVIDER,
        external_key=external_key,
        event_id=None,
        content_hash=candidate_row["content_hash"],
        title=candidate_row["title"],
        source_url="",
        status_value="pending",
        payload=qq_sync.candidate_row_to_dict(candidate_row),
    )
    return {
        "status": "pending",
        "external_key": external_key,
        "candidate": qq_sync.candidate_row_to_dict(candidate_row),
    }


@router.get("/api/integrations/xuexitong/status")
def xuexitong_status() -> dict[str, Any]:
    recent = [sync_row_to_dict(row) for row in list_integration_syncs(xuexitong.PROVIDER, limit=5)]
    return {
        "chrome": xuexitong.get_chrome_status(),
        "recent": recent,
    }


@router.post("/api/integrations/xuexitong/sync", dependencies=[Depends(require_api_key)])
def sync_xuexitong() -> dict[str, Any]:
    read_result = xuexitong.read_items_from_chrome()
    items = read_result.get("items") or []
    sync_result = sync_xuexitong_items(items)
    status_value = read_result.get("status") or "ok"
    if read_result.get("needs_login"):
        status_value = "needs_login"
    elif status_value == "ok" and sync_result["failed"]:
        status_value = "partial_failed"

    return {
        "status": status_value,
        "created": sync_result["created"],
        "updated": sync_result["updated"],
        "skipped": sync_result["skipped"],
        "failed": sync_result["failed"],
        "needs_login": bool(read_result.get("needs_login")),
        "pages_scanned": int(read_result.get("pages_scanned") or 0),
        "error": read_result.get("error") or "",
        "items": sync_result["items"],
    }


@router.get("/api/integrations/qq/status")
def qq_status() -> dict[str, Any]:
    recent = [sync_row_to_dict(row) for row in list_integration_syncs(qq_sync.PROVIDER, limit=8)]
    candidates = [qq_sync.candidate_row_to_dict(row) for row in list_qq_candidates(limit=5)]
    return {
        "config": qq_sync.config_public_summary(),
        "counts": qq_candidate_counts(),
        "recent": recent,
        "candidates": candidates,
    }


@router.post("/api/integrations/qq/messages", dependencies=[Depends(require_api_key)])
def post_qq_message(payload: qq_sync.QQMessageIn) -> dict[str, Any]:
    return ingest_qq_message(payload)


@router.get("/api/integrations/qq/candidates")
def get_qq_candidates(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    return [
        qq_sync.candidate_row_to_dict(row)
        for row in list_qq_candidates(status_value=status_filter, limit=limit)
    ]


@router.post("/api/integrations/qq/candidates/{candidate_id}/confirm", dependencies=[Depends(require_api_key)])
def confirm_qq_candidate(
    candidate_id: str,
    payload: qq_sync.QQCandidateConfirmRequest | None = None,
) -> dict[str, Any]:
    candidate_row = fetch_qq_candidate(candidate_id)
    if candidate_row is None:
        raise HTTPException(status_code=404, detail="QQ candidate not found")

    event_id = candidate_row["event_id"]
    if event_id and fetch_event(event_id):
        return {
            "status": "skipped",
            "reason": "already_created",
            "event": event_from_row(ensure_event(event_id)),
            "candidate": qq_sync.candidate_row_to_dict(candidate_row),
        }

    overrides = payload.updates if payload else {}
    event = create_qq_event_from_candidate(candidate_row, overrides=overrides)
    updated_candidate = fetch_qq_candidate(candidate_id)
    return {
        "status": "created",
        "event": event,
        "candidate": qq_sync.candidate_row_to_dict(updated_candidate),
    }
