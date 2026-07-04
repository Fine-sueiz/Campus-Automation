from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from sqlite3 import Row
from typing import Any

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, FR, MO, SA, SU, TH, TU, WE, rrule

from .settings import SHANGHAI_TZ

FREQ_MAP = {
    "daily": DAILY,
    "weekly": WEEKLY,
    "monthly": MONTHLY,
    "yearly": YEARLY,
}

WEEKDAY_MAP = {
    "MO": MO,
    "TU": TU,
    "WE": WE,
    "TH": TH,
    "FR": FR,
    "SA": SA,
    "SU": SU,
}


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(SHANGHAI_TZ).replace(microsecond=0)


def iso(dt: str | datetime) -> str:
    return parse_dt(dt).isoformat()


def parse_date_window(from_date: str, to_date: str) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(from_date), time.min, SHANGHAI_TZ)
    end = datetime.combine(date.fromisoformat(to_date), time.min, SHANGHAI_TZ) + timedelta(days=1)
    if end <= start:
        raise ValueError("to must be the same as or later than from")
    return start, end


def normalize_rule(rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rule:
        return None
    cleaned: dict[str, Any] = {
        "freq": rule["freq"],
        "interval": int(rule.get("interval") or 1),
    }
    if rule.get("until"):
        date.fromisoformat(str(rule["until"]))
        cleaned["until"] = str(rule["until"])
    if rule.get("count"):
        cleaned["count"] = int(rule["count"])
    weekdays = [day for day in rule.get("weekdays", []) if day in WEEKDAY_MAP]
    if weekdays:
        cleaned["weekdays"] = weekdays
    return cleaned


def event_from_row(row: Row) -> dict[str, Any]:
    recurrence = json.loads(row["recurrence"]) if row["recurrence"] else None
    return {
        "id": row["id"],
        "parent_event_id": row["parent_event_id"],
        "title": row["title"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "all_day": bool(row["all_day"]),
        "category": row["category"],
        "location": row["location"],
        "notes": row["notes"],
        "source": row["source"],
        "reminder_minutes": row["reminder_minutes"],
        "recurrence": recurrence,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def overlaps(start_at: datetime, end_at: datetime, window_start: datetime, window_end: datetime) -> bool:
    return start_at < window_end and end_at > window_start


def build_rrule(start_at: datetime, rule: dict[str, Any]):
    kwargs: dict[str, Any] = {
        "freq": FREQ_MAP[rule["freq"]],
        "dtstart": start_at,
        "interval": int(rule.get("interval") or 1),
    }
    if rule.get("count"):
        kwargs["count"] = int(rule["count"])
    if rule.get("until"):
        until_date = date.fromisoformat(rule["until"])
        kwargs["until"] = datetime.combine(until_date, time.max, SHANGHAI_TZ)
    if rule.get("freq") == "weekly" and rule.get("weekdays"):
        kwargs["byweekday"] = [WEEKDAY_MAP[day] for day in rule["weekdays"]]
    return rrule(**kwargs)


def _base_occurrence(
    event: dict[str, Any],
    occurrence_start: datetime,
    occurrence_end: datetime,
    is_recurring: bool,
) -> dict[str, Any]:
    occurrence_key = iso(occurrence_start)
    return {
        **event,
        "id": event["id"] if not is_recurring else f"{event['id']}::{occurrence_key}",
        "event_id": event["id"],
        "start_at": iso(occurrence_start),
        "end_at": iso(occurrence_end),
        "occurrence_start": occurrence_key,
        "is_recurring": is_recurring,
        "is_exception": False,
    }


def _apply_override(occurrence: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = {**occurrence}
    for key, value in overrides.items():
        if key in {"start_at", "end_at"} and value is not None:
            merged[key] = iso(value)
        else:
            merged[key] = value
    merged["id"] = f"{merged['event_id']}::{merged['occurrence_start']}"
    merged["is_exception"] = True
    return merged


def expand_event(
    event_row: Row,
    exception_rows: list[Row],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    event = event_from_row(event_row)
    start_at = parse_dt(event["start_at"])
    end_at = parse_dt(event["end_at"])
    duration = end_at - start_at
    exceptions = {
        iso(row["occurrence_start"]): {
            "action": row["action"],
            "overrides": json.loads(row["overrides"]) if row["overrides"] else {},
        }
        for row in exception_rows
    }

    if not event["recurrence"]:
        if not overlaps(start_at, end_at, window_start, window_end):
            return []
        return [_base_occurrence(event, start_at, end_at, False)]

    rule = event["recurrence"]
    recurrence = build_rrule(start_at, rule)
    search_start = window_start - duration - timedelta(days=1)
    occurrence_starts = list(recurrence.between(search_start, window_end, inc=True))
    seen: set[str] = set()
    occurrences: list[dict[str, Any]] = []

    for occurrence_start in occurrence_starts:
        occurrence_end = occurrence_start + duration
        occurrence_key = iso(occurrence_start)
        seen.add(occurrence_key)
        exception = exceptions.get(occurrence_key)
        if exception and exception["action"] == "cancel":
            continue
        occurrence = _base_occurrence(event, occurrence_start, occurrence_end, True)
        if exception and exception["action"] == "modify":
            occurrence = _apply_override(occurrence, exception["overrides"])
        if overlaps(parse_dt(occurrence["start_at"]), parse_dt(occurrence["end_at"]), window_start, window_end):
            occurrences.append(occurrence)

    for occurrence_key, exception in exceptions.items():
        if exception["action"] != "modify" or occurrence_key in seen:
            continue
        fallback_start = parse_dt(exception["overrides"].get("start_at") or occurrence_key)
        fallback_end = parse_dt(exception["overrides"].get("end_at") or (fallback_start + duration))
        occurrence = _base_occurrence(event, parse_dt(occurrence_key), parse_dt(occurrence_key) + duration, True)
        occurrence = _apply_override(
            occurrence,
            {
                **exception["overrides"],
                "start_at": iso(fallback_start),
                "end_at": iso(fallback_end),
            },
        )
        if overlaps(parse_dt(occurrence["start_at"]), parse_dt(occurrence["end_at"]), window_start, window_end):
            occurrences.append(occurrence)

    return occurrences


def count_occurrences_before(event: dict[str, Any], occurrence_start: datetime) -> int:
    if not event["recurrence"]:
        return 0
    recurrence = build_rrule(parse_dt(event["start_at"]), event["recurrence"])
    search_start = parse_dt(event["start_at"]) - timedelta(seconds=1)
    return len(list(recurrence.between(search_start, occurrence_start, inc=False)))
