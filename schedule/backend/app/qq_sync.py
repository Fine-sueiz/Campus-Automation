from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from dateutil.parser import isoparse
from pydantic import BaseModel, Field

from .settings import SHANGHAI_TZ, get_qq_sync_config_path


PROVIDER = "qq"
DEFAULT_AUTO_CREATE_MIN_CONFIDENCE = 0.82
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_create_min_confidence": DEFAULT_AUTO_CREATE_MIN_CONFIDENCE,
    "groups": [
        {
            "group_name": "示例课程群",
            "group_id": "",
            "course_name": "示例课程",
            "teacher_names": ["老师群名片或昵称"],
            "teacher_ids": [],
            "default_category": "课程",
            "reminder_minutes": 1440,
        }
    ],
}

SCHEDULE_KEYWORDS = (
    "作业",
    "习题",
    "提交",
    "截止",
    "截至",
    "考试",
    "测验",
    "测试",
    "补交",
    "上课",
    "调课",
    "开会",
    "会议",
    "签到",
    "报名",
    "讲座",
    "答辩",
    "论文",
    "报告",
)
VAGUE_TIME_MARKERS = ("下次课", "近期", "最近", "这几天", "尽快", "择日", "课上", "之后")
DATE_RE = re.compile(
    r"(?<!\d)"
    r"(?:(?P<year>20\d{2})\s*[年./-]\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*[月./-]\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*(?:日|号)?"
    r"(?!\d)"
)
TIME_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|晚上|晚)?\s*"
    r"(?P<hour>[01]?\d|2[0-3])\s*(?:[:：点时])\s*"
    r"(?P<minute>[0-5]\d)?\s*(?:分)?"
)
RELATIVE_DAY_RE = re.compile(r"(今天|明天|后天|大后天)")
WEEKDAY_RE = re.compile(r"(?P<prefix>下周|下星期|本周|这周|星期|周)(?P<day>[一二三四五六日天])")
WEEKDAY_INDEX = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}


class QQAttachment(BaseModel):
    filename: str = ""
    url: str = ""
    content_type: str = ""
    text: str = ""


class QQMessageIn(BaseModel):
    external_key: str | None = None
    message_id: str | None = None
    group_id: str = ""
    group_name: str
    sender_id: str = ""
    sender_name: str
    course_name: str = ""
    text: str
    message_time: datetime | None = None
    attachments: list[QQAttachment] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class QQCandidateConfirmRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ParsedSchedule:
    has_schedule: bool
    title: str
    start_at: datetime | None
    end_at: datetime | None
    all_day: bool
    category: str
    location: str
    notes: str
    reminder_minutes: int | None
    confidence: float
    missing_fields: list[str]
    parse_source: str
    raw_result: dict[str, Any]


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def normalize_text(value: str) -> str:
    value = value.replace("\u3000", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_text(value: str, limit: int = 500) -> str:
    return normalize_text(value).replace("\n", " ")[:limit].strip()


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def load_config() -> dict[str, Any]:
    path = get_qq_sync_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)

    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    data.setdefault("enabled", True)
    data.setdefault("auto_create_min_confidence", DEFAULT_AUTO_CREATE_MIN_CONFIDENCE)
    data.setdefault("groups", [])
    return data


def config_public_summary(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    groups = config.get("groups") if isinstance(config.get("groups"), list) else []
    return {
        "enabled": bool(config.get("enabled", True)),
        "config_path": str(get_qq_sync_config_path()),
        "auto_create_min_confidence": float(
            config.get("auto_create_min_confidence", DEFAULT_AUTO_CREATE_MIN_CONFIDENCE)
        ),
        "groups": [
            {
                "group_name": str(group.get("group_name", "")),
                "course_name": str(group.get("course_name", "")),
                "teacher_count": len(group.get("teacher_names") or []) + len(group.get("teacher_ids") or []),
            }
            for group in groups
            if isinstance(group, dict)
        ],
    }


def message_external_key(payload: QQMessageIn) -> str:
    if payload.external_key:
        return payload.external_key.strip()
    if payload.message_id:
        return f"message:{payload.message_id.strip()}"
    raw = "|".join(
        [
            payload.group_id,
            payload.group_name,
            payload.sender_id,
            payload.sender_name,
            normalize_text(payload.text),
            (payload.message_time or datetime.now(SHANGHAI_TZ)).isoformat()[:16],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def group_matches(payload: QQMessageIn, group: dict[str, Any]) -> bool:
    configured_id = str(group.get("group_id") or "").strip()
    if configured_id and configured_id == payload.group_id:
        return True

    configured_name = normalize_name(str(group.get("group_name") or ""))
    incoming_name = normalize_name(payload.group_name)
    if not configured_name:
        return False
    return configured_name in incoming_name or incoming_name in configured_name


def find_group_config(payload: QQMessageIn, config: dict[str, Any]) -> dict[str, Any] | None:
    groups = config.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        if isinstance(group, dict) and group_matches(payload, group):
            return group
    return None


def sender_allowed(payload: QQMessageIn, group: dict[str, Any]) -> bool:
    sender_id = str(payload.sender_id or "").strip()
    teacher_ids = [str(item).strip() for item in group.get("teacher_ids") or [] if str(item).strip()]
    if sender_id and sender_id in teacher_ids:
        return True

    sender_name = normalize_name(payload.sender_name)
    for teacher_name in group.get("teacher_names") or []:
        normalized = normalize_name(str(teacher_name))
        if normalized and (normalized == sender_name or normalized in sender_name or sender_name in normalized):
            return True
    return False


def build_message_record(
    payload: QQMessageIn,
    external_key: str,
    status_value: str,
    existing_id: str | None,
    created_at: str | None,
) -> dict[str, Any]:
    timestamp = now_iso()
    raw_payload = payload.model_dump(mode="json")
    return {
        "id": existing_id or hashlib.sha256(f"qq-message:{external_key}".encode("utf-8")).hexdigest(),
        "external_key": external_key,
        "group_id": payload.group_id,
        "group_name": payload.group_name,
        "sender_id": payload.sender_id,
        "sender_name": payload.sender_name,
        "course_name": payload.course_name,
        "message_time": (payload.message_time or datetime.now(SHANGHAI_TZ)).isoformat(),
        "text": normalize_text(payload.text)[:4000],
        "raw_payload": json.dumps(raw_payload, ensure_ascii=False),
        "status": status_value,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }


def detect_category(text: str, default_category: str = "课程") -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("考试", "测验", "测试", "quiz", "exam")):
        return "考试"
    if any(keyword in lowered for keyword in ("作业", "习题", "提交", "报告", "论文", "homework", "assignment")):
        return "作业"
    if any(keyword in lowered for keyword in ("会议", "开会", "班会")):
        return "会议"
    if any(keyword in lowered for keyword in ("报名", "讲座")):
        return "项目"
    return default_category or "课程"


def infer_date(year_text: str | None, month_text: str, day_text: str, base_date: date) -> date | None:
    try:
        year = int(year_text) if year_text else base_date.year
        parsed = date(year, int(month_text), int(day_text))
    except ValueError:
        return None
    if not year_text and parsed < base_date - timedelta(days=30):
        parsed = date(base_date.year + 1, parsed.month, parsed.day)
    return parsed


def adjust_hour(period: str | None, hour: int) -> int:
    if period in {"下午", "晚上", "晚"} and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    return hour


def parse_time(text: str) -> time | None:
    match = TIME_RE.search(text)
    if not match:
        return None
    hour = adjust_hour(match.group("period"), int(match.group("hour")))
    minute = int(match.group("minute") or "00")
    return time(hour, minute)


def relative_date_from_text(text: str, base_date: date) -> date | None:
    relative = RELATIVE_DAY_RE.search(text)
    if relative:
        offsets = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
        return base_date + timedelta(days=offsets[relative.group(1)])

    weekday = WEEKDAY_RE.search(text)
    if not weekday:
        return None
    target = WEEKDAY_INDEX[weekday.group("day")]
    prefix = weekday.group("prefix")
    if prefix in {"下周", "下星期"}:
        days_to_next_monday = 7 - base_date.weekday()
        return base_date + timedelta(days=days_to_next_monday + target)

    delta = target - base_date.weekday()
    if delta < 0:
        delta += 7
    return base_date + timedelta(days=delta)


def explicit_date_from_text(text: str, base_date: date) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    return infer_date(match.group("year"), match.group("month"), match.group("day"), base_date)


def parse_datetime_window(text: str, base_date: date) -> tuple[datetime | None, datetime | None, bool, float]:
    parsed_date = explicit_date_from_text(text, base_date)
    relative_date = None if parsed_date else relative_date_from_text(text, base_date)
    target_date = parsed_date or relative_date
    if not target_date:
        return None, None, False, 0.0

    parsed_time = parse_time(text)
    if parsed_time:
        start_at = datetime.combine(target_date, parsed_time, SHANGHAI_TZ)
        return start_at, start_at + timedelta(minutes=30), False, 0.9 if parsed_date else 0.84

    start_at = datetime.combine(target_date, time.min, SHANGHAI_TZ)
    confidence = 0.84 if any(keyword in text for keyword in ("截止", "截至", "提交", "考试", "测验")) else 0.76
    return start_at, start_at + timedelta(days=1), True, confidence


def looks_schedule_relevant(text: str) -> bool:
    return any(keyword in text for keyword in SCHEDULE_KEYWORDS)


def clean_title(text: str, category: str, course_name: str) -> str:
    title = re.sub(r"https?://\S+", "", text)
    title = DATE_RE.sub("", title)
    title = TIME_RE.sub("", title)
    title = WEEKDAY_RE.sub("", title)
    title = RELATIVE_DAY_RE.sub("", title)
    title = re.sub(r"(请大家|大家|同学们|通知|提醒|截止|截至|时间|地点|要求|今天|明天)", " ", title)
    title = re.sub(r"[，,。；;：:\s]+", " ", title).strip(" -—｜|")
    if len(title) > 42:
        title = title[:42].strip()
    if title:
        return title
    if category == "考试":
        return f"{course_name}考试"
    if category == "作业":
        return f"{course_name}作业"
    return f"{course_name}安排"


def parse_with_rules(payload: QQMessageIn, group: dict[str, Any]) -> ParsedSchedule:
    text = normalize_text(payload.text)
    course_name = payload.course_name or str(group.get("course_name") or group.get("group_name") or payload.group_name)
    default_category = str(group.get("default_category") or "课程")
    category = detect_category(text, default_category)
    base_date = (payload.message_time or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ).date()

    if not looks_schedule_relevant(text):
        return ParsedSchedule(
            has_schedule=False,
            title="",
            start_at=None,
            end_at=None,
            all_day=False,
            category=category,
            location="",
            notes="",
            reminder_minutes=None,
            confidence=0,
            missing_fields=[],
            parse_source="rules",
            raw_result={"reason": "no_schedule_keyword"},
        )

    start_at, end_at, all_day, confidence = parse_datetime_window(text, base_date)
    missing_fields: list[str] = []
    if not start_at or not end_at:
        missing_fields.append("start_at")
        confidence = 0.38 if any(marker in text for marker in VAGUE_TIME_MARKERS) else 0.45

    reminder = group.get("reminder_minutes")
    if reminder is None:
        reminder = 2880 if category == "考试" else 1440 if category == "作业" else 60

    return ParsedSchedule(
        has_schedule=True,
        title=clean_title(text, category, course_name),
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        category=category,
        location="",
        notes="",
        reminder_minutes=int(reminder),
        confidence=confidence,
        missing_fields=missing_fields,
        parse_source="rules",
        raw_result={
            "rule": "date_keyword",
            "base_date": base_date.isoformat(),
            "text": compact_text(text),
        },
    )


def parse_model_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = isoparse(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def llm_parse(payload: QQMessageIn, group: dict[str, Any]) -> ParsedSchedule | None:
    api_key = os.getenv("QQ_SYNC_LLM_API_KEY", "").strip()
    if not api_key:
        return None

    api_base = os.getenv("QQ_SYNC_LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("QQ_SYNC_LLM_MODEL", "gpt-4o-mini")
    course_name = payload.course_name or str(group.get("course_name") or group.get("group_name") or payload.group_name)
    base_time = (payload.message_time or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    prompt = {
        "role": "user",
        "content": (
            "你是一个大学课程群消息日程解析器。只输出 JSON，不要 Markdown。"
            "如果消息不是作业、考试、会议、课程调整、报名截止等日程事项，返回 has_schedule=false。"
            "不要猜不存在的年月日；相对日期按 message_time 和 Asia/Shanghai 计算。"
            "字段：has_schedule,title,start_at,end_at,all_day,category,location,notes,"
            "reminder_minutes,confidence,missing_fields。"
            f"\nmessage_time={base_time.isoformat()}"
            f"\ncourse_name={course_name}"
            f"\ngroup_name={payload.group_name}"
            f"\nsender_name={payload.sender_name}"
            f"\nmessage={payload.text}"
        ),
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只返回可解析的 JSON 对象。"},
            prompt,
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    content = (
        response_body.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None

    if not data.get("has_schedule"):
        return ParsedSchedule(
            has_schedule=False,
            title="",
            start_at=None,
            end_at=None,
            all_day=False,
            category=str(data.get("category") or "其他"),
            location="",
            notes="",
            reminder_minutes=None,
            confidence=float(data.get("confidence") or 0),
            missing_fields=[],
            parse_source="llm",
            raw_result=data,
        )

    start_at = parse_model_datetime(data.get("start_at"))
    end_at = parse_model_datetime(data.get("end_at"))
    all_day = bool(data.get("all_day", False))
    if start_at and not end_at:
        end_at = start_at + (timedelta(days=1) if all_day else timedelta(minutes=30))

    missing_fields = data.get("missing_fields")
    if not isinstance(missing_fields, list):
        missing_fields = []
    if not start_at and "start_at" not in missing_fields:
        missing_fields.append("start_at")

    return ParsedSchedule(
        has_schedule=True,
        title=str(data.get("title") or clean_title(payload.text, str(data.get("category") or "课程"), course_name)),
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        category=str(data.get("category") or detect_category(payload.text, str(group.get("default_category") or "课程"))),
        location=str(data.get("location") or ""),
        notes=str(data.get("notes") or ""),
        reminder_minutes=int(data["reminder_minutes"]) if data.get("reminder_minutes") is not None else None,
        confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
        missing_fields=[str(item) for item in missing_fields],
        parse_source="llm",
        raw_result=data,
    )


def parse_schedule(payload: QQMessageIn, group: dict[str, Any]) -> ParsedSchedule:
    parsed = llm_parse(payload, group)
    if parsed is not None:
        return parsed
    return parse_with_rules(payload, group)


def build_candidate_record(
    payload: QQMessageIn,
    message_id: str,
    external_key: str,
    parsed: ParsedSchedule,
    group: dict[str, Any],
    existing_id: str | None,
    created_at: str | None,
    status_value: str,
    event_id: str | None = None,
    error: str = "",
) -> dict[str, Any]:
    timestamp = now_iso()
    course_name = payload.course_name or str(group.get("course_name") or group.get("group_name") or payload.group_name)
    title = f"QQ群｜{course_name}｜{parsed.title}"[:120]
    attachment_lines = [
        f"- {item.filename or item.content_type or '附件'} {item.url}".strip()
        for item in payload.attachments
    ]
    notes_parts = [
        "同步来源：QQ群消息",
        f"群：{payload.group_name}",
        f"课程：{course_name}",
        f"发送人：{payload.sender_name}",
        f"消息时间：{(payload.message_time or datetime.now(SHANGHAI_TZ)).isoformat()}",
        f"解析方式：{parsed.parse_source}",
        f"置信度：{parsed.confidence:.2f}",
    ]
    if payload.attachments:
        notes_parts.extend(["附件：", *attachment_lines])
    if parsed.notes:
        notes_parts.extend(["", f"模型备注：{parsed.notes}"])
    notes_parts.extend(["", f"原文：{normalize_text(payload.text)}", "", f"同步ID：{external_key}"])
    raw_result = parsed.raw_result | {
        "group_name": payload.group_name,
        "sender_name": payload.sender_name,
        "message_text": payload.text,
    }
    candidate_payload = {
        "title": title,
        "start_at": parsed.start_at.isoformat() if parsed.start_at else None,
        "end_at": parsed.end_at.isoformat() if parsed.end_at else None,
        "all_day": parsed.all_day,
        "category": parsed.category,
        "location": parsed.location,
        "notes": "\n".join(notes_parts)[:3800],
        "source": PROVIDER,
        "reminder_minutes": parsed.reminder_minutes,
        "recurrence": None,
    }
    return {
        "id": existing_id or hashlib.sha256(f"qq-candidate:{external_key}".encode("utf-8")).hexdigest(),
        "message_id": message_id,
        "external_key": external_key,
        "event_id": event_id,
        "content_hash": payload_hash(candidate_payload),
        "title": title,
        "start_at": candidate_payload["start_at"],
        "end_at": candidate_payload["end_at"],
        "all_day": 1 if parsed.all_day else 0,
        "category": parsed.category,
        "location": parsed.location,
        "notes": candidate_payload["notes"],
        "reminder_minutes": parsed.reminder_minutes,
        "confidence": parsed.confidence,
        "missing_fields": json.dumps(parsed.missing_fields, ensure_ascii=False),
        "parse_source": parsed.parse_source,
        "status": status_value,
        "last_error": error[:1000],
        "raw_result": json.dumps(raw_result, ensure_ascii=False),
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }


def candidate_row_to_dict(row) -> dict[str, Any]:
    missing_fields = []
    raw_result: dict[str, Any] = {}
    try:
        missing_fields = json.loads(row["missing_fields"] or "[]")
    except json.JSONDecodeError:
        missing_fields = []
    try:
        raw_result = json.loads(row["raw_result"] or "{}")
    except json.JSONDecodeError:
        raw_result = {}
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "external_key": row["external_key"],
        "event_id": row["event_id"],
        "title": row["title"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "all_day": bool(row["all_day"]),
        "category": row["category"],
        "location": row["location"],
        "notes": row["notes"],
        "reminder_minutes": row["reminder_minutes"],
        "confidence": float(row["confidence"]),
        "missing_fields": missing_fields,
        "parse_source": row["parse_source"],
        "status": row["status"],
        "last_error": row["last_error"],
        "raw_result": raw_result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def candidate_event_payload(row, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    payload = {
        "title": row["title"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "all_day": bool(row["all_day"]),
        "category": row["category"],
        "location": row["location"],
        "notes": row["notes"],
        "source": PROVIDER,
        "reminder_minutes": row["reminder_minutes"],
        "recurrence": None,
    }
    for key, value in overrides.items():
        if value is not None and key in payload:
            payload[key] = value
    return payload
