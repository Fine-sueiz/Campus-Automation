from __future__ import annotations

import imaplib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import parseaddr
from typing import Any

from .calendar_sync import sync_opportunity_to_calendar
from .config import get_bool_env, get_int_env
from .db import MonitorDB, utc_now
from .emailer import EmailSettings, send_email
from .feed import stable_id
from .opportunity import OpportunityAnalysis, analyse_opportunity


DEFAULT_VOLUNTEER_REQUIRED = ["志愿服务", "志愿活动", "志愿者", "志愿时长"]
DEFAULT_VOLUNTEER_FOCUS = ["报名", "招募", "参加", "志愿时长"]
TOKEN_RE = re.compile(r"\b([A-Fa-f0-9]{8})\b")
REJECT_RE = re.compile(r"(不报名|不参加|拒绝|放弃|取消)\s*[:：#-]?\s*([A-Fa-f0-9]{8})")
JOIN_RE = re.compile(r"(报名|参加|确认)\s*[:：#-]?\s*([A-Fa-f0-9]{8})")


@dataclass(frozen=True)
class ImapSettings:
    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    poll_seconds: int

    @classmethod
    def from_env(cls) -> "ImapSettings":
        username = (os.getenv("IMAP_USER") or os.getenv("SMTP_USER", "")).strip()
        password = (os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD", "")).strip()
        return cls(
            host=os.getenv("IMAP_HOST", "imap.qq.com").strip(),
            port=get_int_env("IMAP_PORT", 993),
            use_ssl=get_bool_env("IMAP_USE_SSL", True),
            username=username,
            password=password,
            poll_seconds=max(15, get_int_env("MAIL_TRIGGER_POLL_SECONDS", 60)),
        )

    def validate(self) -> list[str]:
        missing: list[str] = []
        if not self.host:
            missing.append("IMAP_HOST")
        if not self.username:
            missing.append("IMAP_USER")
        if not self.password:
            missing.append("IMAP_PASSWORD")
        return missing


def volunteer_config(app_config: dict[str, Any]) -> dict[str, Any]:
    return app_config.get("volunteer") or {}


def volunteer_enabled(app_config: dict[str, Any]) -> bool:
    return get_bool_env("VOLUNTEER_MONITOR_ENABLED", bool(volunteer_config(app_config).get("enabled", False)))


def volunteer_source_accounts(app_config: dict[str, Any]) -> list[str]:
    values = volunteer_config(app_config).get("source_accounts") or []
    return [str(value).strip() for value in values if str(value).strip()]


def volunteer_source_allowed(app_config: dict[str, Any], source_name: str) -> bool:
    accounts = volunteer_source_accounts(app_config)
    if not accounts:
        return True

    normalized_source = "".join(source_name.split()).casefold()
    return normalized_source in {"".join(account.split()).casefold() for account in accounts}


def volunteer_confirm_by_email(app_config: dict[str, Any]) -> bool:
    return get_bool_env(
        "VOLUNTEER_CONFIRM_BY_EMAIL",
        bool(volunteer_config(app_config).get("confirm_by_email", True)),
    )


def volunteer_notify_email(app_config: dict[str, Any]) -> str:
    settings = EmailSettings.from_env()
    config = volunteer_config(app_config)
    user = app_config.get("user") or {}
    return str(config.get("notify_email") or settings.notify_email or user.get("contact_email") or settings.sender).strip()


def volunteer_token_expires_hours(app_config: dict[str, Any]) -> int:
    value = volunteer_config(app_config).get("token_expires_hours", 48)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 48


def allow_submit_when_schedule_conflict(app_config: dict[str, Any]) -> bool:
    return bool(volunteer_config(app_config).get("allow_submit_when_schedule_conflict", False))


def volunteer_detection_config(app_config: dict[str, Any]) -> dict[str, Any]:
    config = volunteer_config(app_config)
    required = [str(item) for item in (config.get("required_any") or DEFAULT_VOLUNTEER_REQUIRED) if str(item)]
    focus = [str(item) for item in (config.get("focus_any") or DEFAULT_VOLUNTEER_FOCUS) if str(item)]
    merged = dict(app_config)
    merged["opportunity"] = {
        "enabled_categories": ["volunteer"],
        "required_any": required,
        "extra_keywords": list(dict.fromkeys(required + focus)),
    }
    return merged


def analyse_volunteer_opportunity(
    title: str,
    text: str,
    article_url: str,
    source_name: str,
    article_item_id: str,
    app_config: dict[str, Any],
    schedule_config: dict[str, Any],
) -> OpportunityAnalysis:
    return analyse_opportunity(
        title,
        text,
        article_url,
        source_name,
        article_item_id,
        volunteer_detection_config(app_config),
        schedule_config,
    )


def confirmation_token(opportunity_id: str) -> str:
    return stable_id("volunteer-confirm", opportunity_id)[:8].upper()


def confirmation_expires_at(app_config: dict[str, Any]) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=volunteer_token_expires_hours(app_config))
    return expires_at.isoformat(timespec="seconds")


def compose_volunteer_reminder_email(
    app_config: dict[str, Any],
    analysis: OpportunityAnalysis,
    token: str,
    recipient: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = recipient
    msg["Subject"] = f"[志愿确认:{token}] {analysis.title}"
    schedule_label = {
        "available": "有空",
        "conflict": "课表冲突",
        "unknown_time": "时间不确定",
    }.get(analysis.schedule_status, analysis.schedule_status or "未知")
    body = [
        "检测到一条可能适合你的志愿服务信息。",
        "",
        f"标题：{analysis.title}",
        f"来源：{analysis.source_name}",
        f"时间：{analysis.activity_time or '未识别到明确时间'}",
        f"地点：{analysis.location or '未识别到明确地点'}",
        f"课表判断：{schedule_label}",
        f"报名链接：{analysis.signup_url or '未识别到'}",
        f"原文链接：{analysis.article_url}",
    ]
    if analysis.deadline:
        body.append(f"截止信息：{analysis.deadline}")
    if analysis.matched_time_text:
        body.extend(["", "可匹配时间：", analysis.matched_time_text])
    elif analysis.free_time_text:
        body.extend(["", "你的空闲时间：", analysis.free_time_text])
    body.extend(
        [
            "",
            "如果要报名，请直接回复：",
            f"报名 {token}",
            "",
            "如果不参加，请回复：",
            f"不报名 {token}",
            "",
            f"确认码有效期：{volunteer_token_expires_hours(app_config)} 小时。重复回复不会重复报名。",
        ]
    )
    msg.set_content("\n".join(body))
    return msg


def maybe_send_volunteer_reminder(
    db: MonitorDB,
    app_config: dict[str, Any],
    analysis: OpportunityAnalysis,
) -> str:
    if not volunteer_enabled(app_config):
        return "disabled"
    existing = db.get_volunteer_confirmation_by_opportunity(analysis.id)
    if existing and existing.get("status") in {"pending", "approved", "rejected", "need_human"}:
        return "skipped"

    recipient = volunteer_notify_email(app_config)
    if not recipient:
        db.add_log("error", "volunteer reminder failed: missing notify email", {"opportunity_id": analysis.id})
        return "failed"

    token = confirmation_token(analysis.id)
    record = db.create_volunteer_confirmation(token, analysis.id, confirmation_expires_at(app_config))
    token = str(record.get("token") or token).upper()
    settings = EmailSettings.from_env()
    missing = settings.validate(require_password=not settings.dry_run)
    if missing:
        db.update_volunteer_confirmation(token, "notify_failed")
        db.add_log("error", "volunteer reminder failed: missing email settings", {"missing": missing})
        return "failed"

    try:
        send_email(settings, compose_volunteer_reminder_email(app_config, analysis, token, recipient), recipient)
    except Exception as exc:  # noqa: BLE001
        db.update_volunteer_confirmation(token, "notify_failed")
        db.add_log("error", f"volunteer reminder failed: {exc}", {"opportunity_id": analysis.id})
        return "failed"
    db.add_log("info", "volunteer reminder sent", {"opportunity_id": analysis.id, "token": token})
    return "sent"


def parse_confirmation_command(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    reject = REJECT_RE.search(text)
    if reject:
        return "reject", reject.group(2).upper()
    join = JOIN_RE.search(text)
    if join:
        return "join", join.group(2).upper()
    token = TOKEN_RE.search(text)
    if token and "报名" in text and "不报名" not in text:
        return "join", token.group(1).upper()
    return "", ""


def handle_volunteer_confirmation(
    db: MonitorDB,
    app_config: dict[str, Any],
    token: str,
    decision: str,
    *,
    source_message_id: str = "",
) -> str:
    token = token.upper()
    record = db.get_volunteer_confirmation(token)
    if not record:
        db.add_log("info", "volunteer confirmation ignored: unknown token", {"token": token})
        return "unknown"
    if record.get("status") != "pending":
        return "duplicate"
    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(str(record["expires_at"]))
    except ValueError:
        expires_at = now
    if expires_at < now:
        db.update_volunteer_confirmation(token, "expired")
        return "expired"

    opportunity = db.get_opportunity(str(record["opportunity_id"]))
    if not opportunity:
        db.update_volunteer_confirmation(token, "need_human", used_at=utc_now(), source_message_id=source_message_id)
        return "need_human"

    if decision == "reject":
        db.update_volunteer_confirmation(token, "rejected", used_at=utc_now(), source_message_id=source_message_id)
        db.update_opportunity_status(str(opportunity["id"]), "rejected")
        db.record_decision(str(opportunity["id"]), "reject", "email")
        send_volunteer_status_notice(app_config, f"已记录不报名：{opportunity['title']}")
        return "rejected"

    if not opportunity.get("signup_url"):
        db.update_volunteer_confirmation(token, "need_human", used_at=utc_now(), source_message_id=source_message_id)
        db.update_opportunity_status(str(opportunity["id"]), "need_human")
        send_volunteer_status_notice(app_config, f"已收到报名确认，但未识别到报名链接：{opportunity['title']}")
        return "need_human"

    if opportunity.get("schedule_status") == "conflict" and not allow_submit_when_schedule_conflict(app_config):
        db.update_volunteer_confirmation(token, "need_human", used_at=utc_now(), source_message_id=source_message_id)
        db.update_opportunity_status(str(opportunity["id"]), "need_human")
        send_volunteer_status_notice(app_config, f"已收到报名确认，但课表判断冲突，需要人工处理：{opportunity['title']}")
        return "need_human"

    task_id = f"task-volunteer-{token}"
    db.create_form_task(task_id, str(opportunity["id"]), str(opportunity["signup_url"]), utc_now())
    db.update_volunteer_confirmation(token, "approved", used_at=utc_now(), source_message_id=source_message_id)
    db.update_opportunity_status(str(opportunity["id"]), "approved")
    db.record_decision(str(opportunity["id"]), "join", "email")
    try:
        sync_opportunity_to_calendar(db, app_config, opportunity)
    except Exception as exc:  # noqa: BLE001
        db.add_log("error", f"calendar sync after volunteer confirmation failed: {exc}", {"opportunity_id": opportunity["id"]})
    send_volunteer_status_notice(app_config, f"已收到报名确认，已创建报名任务：{opportunity['title']}")
    return "approved"


def send_volunteer_status_notice(app_config: dict[str, Any], text: str) -> None:
    recipient = volunteer_notify_email(app_config)
    if not recipient:
        return
    settings = EmailSettings.from_env()
    try:
        missing = settings.validate(require_password=not settings.dry_run)
        if missing:
            return
        msg = EmailMessage()
        msg["To"] = recipient
        msg["Subject"] = "志愿服务报名状态提醒"
        msg.set_content(text)
        send_email(settings, msg, recipient)
    except Exception:
        return


def poll_mail_triggers(db: MonitorDB, app_config: dict[str, Any]) -> dict[str, Any]:
    settings = ImapSettings.from_env()
    counts: dict[str, Any] = {"checked": 0, "approved": 0, "rejected": 0, "ignored": 0, "failed": 0, "interval": settings.poll_seconds}
    if not volunteer_enabled(app_config) or not volunteer_confirm_by_email(app_config):
        counts["status"] = "disabled"
        return counts
    missing = settings.validate()
    if missing:
        db.add_log("error", "mail trigger poll failed: missing imap settings", {"missing": missing})
        counts["failed"] += 1
        counts["status"] = "missing_settings"
        return counts

    try:
        messages = fetch_unseen_messages(settings)
    except Exception as exc:  # noqa: BLE001
        db.add_log("error", f"mail trigger poll failed: {exc}")
        counts["failed"] += 1
        return counts

    allowed_sender = settings.username.lower()
    notify_sender = volunteer_notify_email(app_config).lower()
    for message_id, sender, text in messages:
        counts["checked"] += 1
        sender_email = parseaddr(sender)[1].lower()
        if sender_email not in {allowed_sender, notify_sender}:
            counts["ignored"] += 1
            continue
        decision, token = parse_confirmation_command(text)
        if not decision or not token:
            counts["ignored"] += 1
            continue
        status = handle_volunteer_confirmation(db, app_config, token, decision, source_message_id=message_id)
        if status == "approved":
            counts["approved"] += 1
        elif status == "rejected":
            counts["rejected"] += 1
        elif status in {"need_human", "expired", "unknown", "duplicate"}:
            counts["ignored"] += 1
        else:
            counts["failed"] += 1
    return counts


def fetch_unseen_messages(settings: ImapSettings) -> list[tuple[str, str, str]]:
    imap_cls = imaplib.IMAP4_SSL if settings.use_ssl else imaplib.IMAP4
    with imap_cls(settings.host, settings.port) as client:
        client.login(settings.username, settings.password)
        client.select("INBOX")
        typ, data = client.search(None, "UNSEEN")
        if typ != "OK" or not data:
            return []
        messages: list[tuple[str, str, str]] = []
        for msg_id in data[0].split():
            typ, fetched = client.fetch(msg_id, "(RFC822)")
            if typ != "OK":
                continue
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), b"")
            if not raw:
                continue
            message = message_from_bytes(raw)
            subject = decode_mime_header(str(message.get("Subject", "")))
            sender = str(message.get("From", ""))
            body = message_text(message)
            messages.append((msg_id.decode("ascii", errors="ignore"), sender, subject + "\n" + body))
        return messages


def decode_mime_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def message_text(message: Message) -> str:
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            parts.append(decode_payload(part))
        return "\n".join(parts)
    return decode_payload(message)


def decode_payload(message: Message) -> str:
    payload = message.get_payload(decode=True)
    if payload is None:
        raw = message.get_payload()
        return str(raw or "")
    charset = message.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")
